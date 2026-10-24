#!/usr/bin/env python3
"""通道租借的 runner:Muninn 把一個對話串「借」給某個專案的 Claude Code session,這裡負責開、接續、轉送、收回。

只聽 127.0.0.1(預設 18797),每個請求要 `Authorization: Bearer <~/agent-hub/.secrets/runner-token>`。
API(契約 contracts/lease-runner.v1.schema.json):
  POST /v1/leases                     {v, project, mode: discuss, session_id, thread, resume?, max_turns?} → 201
  POST /v1/leases/<id>/messages       {v, text, client_msg_id} → 202(同一個 client_msg_id 只送一次;程序停了自動接回)
  GET  /v1/leases/<id>/events         SSE;Last-Event-ID 之後的補送
  POST /v1/leases/<id>/warned         Muninn 確認「還要繼續嗎」的推播真的送出去了(之後 10 分鐘沒回應才收回)
  POST /v1/leases/<id>/keepalive      回答「繼續」
  POST /v1/leases/<id>/terminal       L3:停掉程序,回 `~/agent-hub/bin/lease-resume <id>`
  POST /v1/leases/<id>/detach         {v, reason} → 202;交接在背景跑,結果走事件
  GET  /v1/leases、GET /v1/status、GET /v1/policy(不能 allow_session 的網域,hub.json lease.no_session_domains)
  POST /v1/internal/<id>/relay        權限轉接回報(稽核 + 等待中不算閒置)
每個租約一個長駐程序:`llm.py exec lease-session`(宣告在 contracts/llm-points.yaml)+ `--session-id`/`--resume`、
只有權限轉接的 MCP(--strict-mcp-config)、`--settings`(第二階段的 deny 清單,不管專案有沒有開 enforce)、
`--append-system-prompt`(租約說明)、`--max-turns`;cwd = 專案登錄的 workspace,環境有 HUBBUS_LEASE=1(信箱 hook
把信給它看但不標 delivered)。
閒置:程序 10 分鐘沒事就停(租約還在,下一則 --resume 接回);租約 30 分鐘沒事送 idle_warning,Muninn 回報
warned 之後 10 分鐘還沒回應才收回 + 交接;等權限/提問回答的時間不算閒置。
收回:停程序(終端機還開著就 busy)→ `llm.py run lease-handover -- --resume <id>`(不合格重試一次,再不行最小交接)
→ 匯流排 session.report + 專案信箱 + 給 owner 的 progress/task/question(固定 id,重送不重複)+ 筆記寫進 memory
→ 馬上跑一次 hubbus bridge。
"""
from __future__ import annotations

import datetime
import http.server
import json
import os
import pathlib
import queue
import re
import secrets as pysecrets
import shlex
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402

HUB = hubconf.ROOT
HOST, PORT = "127.0.0.1", int(os.environ.get("LEASE_RUNNER_PORT", "18797"))
DIR = hubconf.STATE / "lease-runner"
TOKEN_FILE = HUB / ".secrets" / "runner-token"
PY = "/opt/homebrew/bin/python3"
GLOBAL_CAP = 3
PROC_IDLE_S = int(os.environ.get("LEASE_PROC_IDLE_S", 600))     # 程序閒置多久就停
LEASE_IDLE_S = int(os.environ.get("LEASE_IDLE_S", 1800))        # 租約閒置多久送 idle_warning
WARN_GRACE_S = int(os.environ.get("LEASE_WARN_GRACE_S", 600))   # warned 之後多久沒回應就收回
IDLE_TICK_S = int(os.environ.get("LEASE_IDLE_TICK_S", 20))
DEFAULT_MAX_TURNS = 200
HANDOVER_PROMPT = ("租約要收回了。根據這整段對話寫交接,用繁體中文:summary(重點)、decisions(談定的決定與理由)、"
                   "open_questions(要 Knas 回答的問題)、tasks_for_owner(只有 Knas 本人能做的事,有期限就寫 due)、"
                   "notes_markdown(要記進這個專案 memory 的原始筆記:討論了什麼、決定、踩到的問題;保留「預計/可能」"
                   "這類不確定的語氣)。沒有的項目給空陣列。只回結構化輸出。")
PREAMBLE = """這是「通道租借」的深談:Knas 正在手機上透過他的助理 Muninn 跟你(這個專案的 session)對話。
- 這是唯讀的深談(discuss):你可以讀檔、搜尋、查資料,不能改檔或跑指令。需要改的東西寫進最後的交接,讓 Knas 決定。
- 要 Knas 在幾個選項裡選時,用提問工具(AskUserQuestion),他會在手機上回答;沒回答就用文字問。
- 要讀不在允許清單的網站時,系統會先問 Knas;被拒絕就不要重試同一個網址。
- 回覆簡短、適合在手機上讀。收回時系統會要你寫一份結構化交接(摘要、決定、未決問題、Knas 要做的事、memory 筆記)。"""

_lock = threading.RLock()
LEASES: dict[str, "Lease"] = {}


def now() -> datetime.datetime:
    return datetime.datetime.now().astimezone().replace(microsecond=0)


def iso(t: datetime.datetime | None = None) -> str:
    return (t or now()).isoformat()


def log(msg: str) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    with open(DIR / "runner.log", "a") as f:
        f.write(f"[{now():%Y-%m-%d %H:%M:%S}] {msg}\n")


class Lease:
    def __init__(self, d: dict):
        self.id = d["lease_id"]
        self.project, self.mode, self.thread = d["project"], d.get("mode", "discuss"), d.get("thread", "")
        self.cwd = d["cwd"]
        self.state = d.get("state", "attaching")
        self.started_once = d.get("started_once", False)
        self.max_turns = d.get("max_turns", DEFAULT_MAX_TURNS)
        self.last_activity = d.get("last_activity") or iso()
        self.idle_warned_at = d.get("idle_warned_at")
        self.warned_at = d.get("warned_at")
        self.seq = d.get("seq", 0)
        self.crashes = 0
        self.waiting = 0
        self.in_turn = False
        self.intent_stop = False
        self.proc: subprocess.Popen | None = None
        self.seen_msgs: list[str] = d.get("seen_msgs", [])[-200:]
        self.recent: list[dict] = []          # 最近的事件(含 text_delta),SSE 補送用
        self.cond = threading.Condition(_lock)

    def dump(self) -> dict:
        return {"lease_id": self.id, "project": self.project, "mode": self.mode, "thread": self.thread, "cwd": self.cwd,
                "state": self.state, "started_once": self.started_once, "max_turns": self.max_turns,
                "last_activity": self.last_activity, "idle_warned_at": self.idle_warned_at, "warned_at": self.warned_at,
                "seq": self.seq, "seen_msgs": self.seen_msgs[-200:]}

    def public(self) -> dict:
        return {"lease_id": self.id, "project": self.project, "mode": self.mode, "thread": self.thread,
                "state": self.state, "last_activity": self.last_activity, "waiting": self.waiting > 0}

    @property
    def dir(self) -> pathlib.Path:
        return DIR / self.id

    def emit(self, etype: str, **data) -> dict:
        with _lock:
            self.seq += 1
            ev = {"v": 1, "seq": self.seq, "lease_id": self.id, "ts": iso(), "type": etype, **data}
            self.recent.append(ev)
            del self.recent[:-3000]
            if etype != "text_delta":  # 逐字的不存檔(量大;斷線補送從完整的 assistant 事件看得到)
                self.dir.mkdir(parents=True, exist_ok=True)
                with open(self.dir / "events.jsonl", "a") as f:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            self.cond.notify_all()
            save()
        return ev

    def set_state(self, st: str, **extra) -> None:
        self.state = st
        self.emit("state", state=st, **extra)

    def touch(self) -> None:
        self.last_activity = iso()
        self.idle_warned_at = self.warned_at = None


def save() -> None:
    with _lock:
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = DIR / "leases.json.tmp"
        tmp.write_text(json.dumps({lid: l.dump() for lid, l in LEASES.items()}, ensure_ascii=False, indent=1))
        tmp.replace(DIR / "leases.json")


def load() -> None:
    f = DIR / "leases.json"
    if not f.exists():
        return
    for lid, d in json.loads(f.read_text()).items():
        lease = Lease(d)
        if lease.state in ("running", "resuming", "attaching", "crashed"):
            lease.state = "idle_stopped"  # runner 重啟:子程序都不在了,下一則訊息 --resume 接回
        LEASES[lid] = lease


# ---------- 設定:deny 清單、MCP、網域 ----------

def deny_settings(project: str) -> pathlib.Path:
    """跟 render-project-settings 算的同一份 deny(不管專案有沒有開 enforce)+ 使用者層級的敏感檔。"""
    import importlib.util
    import registry
    spec = importlib.util.spec_from_file_location("rps", HUB / "bin" / "render-project-settings.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    idx = registry.refresh()
    _, deny, _ = m.desired(project, idx["projects"][project], idx)
    deny += ["Read(~/.claude.json)", "Read(~/.claude/**)", "Read(~/.ssh/**)", "Read(~/.config/gh/**)", "Read(~/.aws/**)"]
    p = DIR / project / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"permissions": {"deny": deny}}, ensure_ascii=False))
    return p


def mcp_config(lease: Lease) -> pathlib.Path:
    p = lease.dir / "mcp.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"mcpServers": {"lease_relay": {
        "command": PY, "args": [str(HUB / "bin" / "lease-relay.py")],
        "env": {"LEASE_ID": lease.id, "LEASE_PROJECT": lease.project, "LEASE_RUNNER": f"http://{HOST}:{PORT}"}}}}))
    return p


def allow_rules(project: str) -> list[str]:
    doc = hubconf.projects().get(project) or {}
    return [f"WebFetch(domain:{d})" for d in (doc.get("network") or [])]


def env_for(lease: Lease) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_OAUTH_TOKEN"}  # 憑證由 llm.py exec 決定(headless token)
    env.update(HUBBUS_LEASE="1", LEASE_ALLOW=json.dumps(allow_rules(lease.project)),
               PATH=os.environ.get("LEASE_PATH_PREFIX", "") +  # 測試用:把假的 claude 放前面
                    f"{hubconf.LAUNCHD_PATH}:{pathlib.Path.home()}/.local/bin:/usr/sbin:/sbin")
    return env


# ---------- 程序 ----------

def start_proc(lease: Lease, resume: bool) -> None:
    flag = ["--resume", lease.id] if resume or lease.started_once else ["--session-id", lease.id]
    cmd = [PY, str(HUB / "bin" / "llm.py"), "exec", "lease-session", "--cwd", lease.cwd, "--",
           *flag, "--strict-mcp-config", "--mcp-config", str(mcp_config(lease)),
           "--permission-prompt-tool", "mcp__lease_relay__approve",
           "--settings", str(deny_settings(lease.project)), "--append-system-prompt", PREAMBLE,
           "--max-turns", str(lease.max_turns)]
    lease.dir.mkdir(parents=True, exist_ok=True)
    err = open(lease.dir / "stderr.log", "a")
    lease.proc = subprocess.Popen(cmd, cwd=lease.cwd, env=env_for(lease), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=err, text=True, bufsize=1)
    lease.started_once = True
    lease.intent_stop = False
    lease.set_state("running", pid=lease.proc.pid, resumed=bool(flag[0] == "--resume"))
    threading.Thread(target=reader, args=(lease, lease.proc), daemon=True).start()


def stop_proc(lease: Lease, new_state: str) -> None:
    p = lease.proc
    lease.intent_stop = True
    if p and p.poll() is None:
        try:
            p.stdin.close()
            p.wait(timeout=15)
        except Exception:
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    lease.proc = None
    lease.in_turn = False
    lease.set_state(new_state)


def summarize_tool(name: str, inp: dict) -> str:
    for k in ("file_path", "path", "pattern", "url", "query", "command"):
        if inp.get(k):
            return f"{name} {str(inp[k])[:160]}"
    if name == "AskUserQuestion":
        return "問 Knas:" + " / ".join(q.get("question", "") for q in inp.get("questions") or [])[:200]
    return name


def reader(lease: Lease, proc: subprocess.Popen) -> None:
    for line in proc.stdout:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = d.get("type")
        if t == "stream_event":
            ev = d.get("event") or {}
            delta = ev.get("delta") or {}
            if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                lease.emit("text_delta", text=delta.get("text", ""))
        elif t == "assistant":
            for block in (d.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text"):
                    lease.emit("assistant", text=block["text"])
                elif block.get("type") == "tool_use":
                    lease.emit("tool_use", name=block.get("name"), summary=summarize_tool(block.get("name", ""), block.get("input") or {}))
        elif t == "user":
            content = (d.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_result":
                    c = block.get("content")
                    text = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict))
                    lease.emit("tool_result", is_error=bool(block.get("is_error")), summary=text[:200], truncated=len(text) > 200)
        elif t == "rate_limit_event":
            info = d.get("rate_limit_info") or {}
            w = info.get("unifiedWindows") or {}
            rl = {"five_hour": (w.get("five_hour") or {}).get("utilization"),
                  "seven_day": (w.get("seven_day") or {}).get("utilization"),
                  "five_hour_resets_at": (w.get("five_hour") or {}).get("resetsAt"),   # epoch 秒
                  "seven_day_resets_at": (w.get("seven_day") or {}).get("resetsAt")}
            lease.emit("rate_limit", status=info.get("status"), **rl)
            STATUS["rate_limit"] = {**rl, "at": iso()}
        elif t == "system" and d.get("subtype") == "post_turn_summary":
            lease.emit("turn_end", summary=d.get("status_detail", ""))
        elif t == "result":
            with _lock:
                lease.in_turn = False
                lease.crashes = 0
                lease.touch()
            text = str(d.get("result") or "")
            if d.get("is_error") and re.search(r"rate.?limit|usage limit|額度", text, re.I):
                lease.emit("error", error="rate_limit", detail=text[:300])
            elif d.get("subtype") == "error_max_turns":
                lease.emit("error", error="bad_request", detail="回合上限到了,收回")
                threading.Thread(target=detach, args=(lease, "budget"), daemon=True).start()
            lease.emit("turn_end", cost=d.get("total_cost_usd"), num_turns=d.get("num_turns"),
                       duration_ms=d.get("duration_ms"), is_error=bool(d.get("is_error")))
    proc.wait()
    with _lock:
        if lease.proc is not proc or lease.intent_stop:
            return
        lease.proc, lease.in_turn = None, False
        lease.crashes += 1
        tail = (lease.dir / "stderr.log").read_text(errors="replace")[-400:] if (lease.dir / "stderr.log").exists() else ""
        cat = "auth" if re.search(r"log ?in|auth|credential", tail, re.I) else "process_crashed"
        lease.emit("error", error=cat, detail=f"exit {proc.returncode}:{tail[-200:]}")
        if lease.crashes >= 2:
            threading.Thread(target=detach, args=(lease, "crash"), daemon=True).start()
        else:
            lease.set_state("idle_stopped")  # 下一則訊息自動 --resume 一次


def send_message(lease: Lease, text: str) -> None:
    with _lock:
        if lease.proc is None or lease.proc.poll() is not None:
            lease.set_state("resuming")
            start_proc(lease, resume=True)
        lease.touch()
        lease.in_turn = True
        msg = json.dumps({"type": "user", "message": {"role": "user", "content": text}}, ensure_ascii=False)
        lease.proc.stdin.write(msg + "\n")
        lease.proc.stdin.flush()


# ---------- 收回與交接 ----------

def terminal_open(lease: Lease) -> bool:
    """終端機(lease-resume)還開著這個 session 嗎:transcript 最近 2 分鐘有被寫,或有程序開著它。"""
    slug = re.sub(r"[/.]", "-", str(pathlib.Path(lease.cwd).resolve()))
    tr = pathlib.Path.home() / ".claude" / "projects" / slug / f"{lease.id}.jsonl"
    if tr.exists() and time.time() - tr.stat().st_mtime < 120:
        return True
    r = subprocess.run(["/usr/sbin/lsof", "-t", str(tr)], capture_output=True, text=True) if tr.exists() else None
    return bool(r and r.stdout.strip())


def run_handover(lease: Lease) -> dict | None:
    cmd = [PY, str(HUB / "bin" / "llm.py"), "run", "lease-handover", "--cwd", lease.cwd, "--",
           "--resume", lease.id, "--strict-mcp-config", "--settings", str(deny_settings(lease.project))]
    for _ in range(2):  # 不合格重試一次
        r = subprocess.run(cmd, input=HANDOVER_PROMPT, cwd=lease.cwd, env=env_for(lease), capture_output=True, text=True,
                           timeout=900)
        try:
            so = json.loads(r.stdout).get("structured_output")
        except ValueError:
            so = None
        if r.returncode == 0 and isinstance(so, dict):
            return so
    return None


def hubbus(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["/usr/bin/python3", str(HUB / "bin" / "hubbus"), *args], capture_output=True, text=True)


def deliver(lease: Lease, h: dict) -> None:
    sid, proj, owner = lease.id, lease.project, hubconf.owner_address()
    keys = []
    for i, t in enumerate(h.get("tasks_for_owner") or []):
        key = f"lease-{sid}-task-{i}"
        args = ["send", "--from", proj, "--to", owner, "--type", "task", "--id", key, "--text", t["text"],
                "--context", f"lease:{sid}"]
        if t.get("due"):
            args += ["--due", t["due"]]
        hubbus(*args)
        t["key"] = key
        keys.append(key)
    for i, q in enumerate(h.get("open_questions") or []):
        hubbus("send", "--from", proj, "--to", owner, "--type", "question", "--id", f"lease-{sid}-q-{i}", "--text", q,
               "--context", f"lease:{sid}")
    hubbus("send", "--from", proj, "--to", owner, "--type", "progress", "--id", f"lease-{sid}-progress",
           "--text", f"[{proj} 深談收回] {h.get('summary', '')}", "--context", f"lease:{sid}")
    body = [f"深談(租借 {sid[:8]})收回的交接:", h.get("summary", "")]
    body += [f"- 決定:{d.get('what')}({d.get('why', '')})" for d in h.get("decisions") or []]
    body += [f"- 待回答:{q}" for q in h.get("open_questions") or []]
    hubbus("send", "--from", hubconf.hub_project(), "--to", proj, "--type", "info", "--id", f"lease-{sid}-report",
           "--text", "\n".join(body))
    # 筆記寫進專案 memory(managed-files 有登記);附上寄出的任務 key,免得 librarian 又把同一件事寄一次
    h["notes_written"] = False
    notes = (h.get("notes_markdown") or "").strip()
    mem = pathlib.Path(lease.cwd) / ".agent-memory" / "memory"
    if notes and mem.is_dir():
        f = mem / f"{now():%Y-%m-%d}.md"
        extra = ("\n\n已登錄給 Knas 的任務:" + "、".join(keys)) if keys else ""
        with open(f, "a") as fh:
            fh.write(f"\n## 租借對話 {now():%H:%M}(session {sid})\n\n{notes}{extra}\n")
        h["notes_written"] = True
    hubbus("publish", "session.report", "--source", proj, "--to", proj, "--id", f"lease-{sid}-handover",
           "--payload", json.dumps(h, ensure_ascii=False))
    subprocess.Popen(["/usr/bin/python3", str(HUB / "bin" / "hubbus"), "bridge"], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)  # 馬上送出,不等 15 分鐘


def detach(lease: Lease, reason: str) -> None:
    with _lock:
        if lease.state in ("detaching", "detached"):
            return
        prev = lease.state
        if prev == "terminal" and terminal_open(lease):
            lease.emit("error", error="busy", detail="terminal_open:終端機裡的 session 還開著,先結束它再收回")
            return
        stop_proc(lease, "detaching")
    so = None
    try:
        so = run_handover(lease)
    except Exception as e:
        log(f"{lease.id} 交接失敗:{e!r}")
    fallback = so is None
    h = {"v": 1, "project": lease.project, "session_id": lease.id, "mode": lease.mode, "reason": reason,
         "summary": "交接失敗(模型沒能產生合格的交接);對話紀錄還在,可以重新 attach 接續" if fallback else so.get("summary", ""),
         "decisions": [] if fallback else so.get("decisions", []),
         "open_questions": [] if fallback else so.get("open_questions", []),
         "tasks_for_owner": [] if fallback else so.get("tasks_for_owner", []),
         "notes_markdown": "" if fallback else so.get("notes_markdown", ""),
         "notes_written": False, "fallback": fallback, "branch": None, "commits": [], "tests": None}
    try:
        deliver(lease, h)
    except Exception as e:
        log(f"{lease.id} 交接送出失敗:{e!r}")
        lease.emit("error", error="handover_failed", detail=repr(e)[:300])
    if fallback:
        lease.emit("error", error="handover_failed", detail="交接不合格,已送出最小交接")
    lease.emit("handover", handover=h)
    lease.set_state("detached", reason=reason)


# ---------- 閒置 ----------

STATUS: dict = {"rate_limit": None}


def idle_loop() -> None:
    while True:
        time.sleep(IDLE_TICK_S)
        with _lock:
            leases = list(LEASES.values())
        for l in leases:
            try:
                if l.state not in ("running", "idle_stopped"):
                    continue
                idle = (now() - datetime.datetime.fromisoformat(l.last_activity)).total_seconds()
                if l.waiting or l.in_turn:
                    continue
                if l.state == "running" and idle >= PROC_IDLE_S:
                    stop_proc(l, "idle_stopped")
                if idle >= LEASE_IDLE_S and not l.idle_warned_at:
                    l.idle_warned_at = iso()
                    l.emit("idle_warning", idle_s=int(idle))
                if l.warned_at and (now() - datetime.datetime.fromisoformat(l.warned_at)).total_seconds() >= WARN_GRACE_S:
                    threading.Thread(target=detach, args=(l, "idle"), daemon=True).start()
            except Exception as e:
                log(f"idle_loop {l.id}:{e!r}")


# ---------- HTTP ----------

def token() -> str:
    if not TOKEN_FILE.exists():
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(pysecrets.token_urlsafe(32))
        TOKEN_FILE.chmod(0o600)
    return TOKEN_FILE.read_text().strip()


def live(l: Lease) -> bool:
    return l.state not in ("detached",)


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def reply(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def err(self, code: int, kind: str, detail: str = "") -> None:
        self.reply(code, {"v": 1, "error": kind, "detail": detail})

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            return {"__bad__": True}

    def authed(self) -> bool:
        if self.headers.get("Authorization") != f"Bearer {token()}":
            self.err(401, "unauthorized")
            return False
        return True

    def lease(self, lid: str) -> Lease | None:
        l = LEASES.get(lid)
        if l is None:
            self.err(404, "not_found", lid)
        return l

    def do_GET(self):
        if not self.authed():
            return
        path = urllib.parse.urlparse(self.path).path
        parts = path.strip("/").split("/")
        if path == "/v1/leases":
            with _lock:
                ls = sorted(LEASES.values(), key=lambda x: x.last_activity, reverse=True)
                return self.reply(200, {"v": 1, "leases": [l.public() for l in ls if live(l)] +
                                        [l.public() for l in ls if not live(l)][:20]})
        if path == "/v1/policy":  # 給 Muninn 的權限卡片:不能「整個租約都允許」的網域(唯一的一份清單)
            import registry
            idx = registry.refresh()
            return self.reply(200, {"v": 1, "no_session_domains": hubconf.get("lease.no_session_domains", []),
                                    "attachable": sorted(idx["projects"]), "modes": ["discuss"]})
        if path == "/v1/status":
            with _lock:
                n = sum(1 for l in LEASES.values() if live(l))
            return self.reply(200, {"v": 1, "leased": n, "cap": GLOBAL_CAP, "rate_limit": STATUS["rate_limit"]})
        if len(parts) == 4 and parts[:2] == ["v1", "leases"] and parts[3] == "events":
            l = self.lease(parts[2])
            if l:
                self.sse(l)
            return
        self.err(404, "not_found", path)

    def sse(self, l: Lease) -> None:
        last = int(self.headers.get("Last-Event-ID") or 0)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        # 先補送:檔案裡的(不含逐字)+ 記憶體裡的
        sent = last
        backlog = []
        f = l.dir / "events.jsonl"
        if f.exists():
            for line in f.read_text().splitlines():
                ev = json.loads(line)
                if ev["seq"] > sent:
                    backlog.append(ev)
        with _lock:
            backlog += [e for e in l.recent if e["seq"] > sent and e["type"] == "text_delta"]
        try:
            for ev in sorted(backlog, key=lambda e: e["seq"]):
                self.wfile.write(f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                sent = max(sent, ev["seq"])
            self.wfile.flush()
            while True:
                with l.cond:
                    l.cond.wait(timeout=15)
                    new = [e for e in l.recent if e["seq"] > sent]
                if not new:
                    self.wfile.write(b": keepalive\n\n")
                for ev in new:
                    self.wfile.write(f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                    sent = ev["seq"]
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def do_POST(self):
        if not self.authed():
            return
        b = self.body()
        if b.get("__bad__"):
            return self.err(400, "bad_request", "body 不是 JSON")
        parts = urllib.parse.urlparse(self.path).path.strip("/").split("/")
        if parts == ["v1", "leases"]:
            return self.create(b)
        if len(parts) == 4 and parts[:2] == ["v1", "internal"] and parts[3] == "relay":
            l = self.lease(parts[2])
            if l:
                with _lock:
                    if b.get("phase") == "start":
                        l.waiting += 1
                    elif l.waiting:
                        l.waiting -= 1
                    l.touch()
                l.emit("question" if b.get("tool") == "AskUserQuestion" else "permission", **{k: v for k, v in b.items()})
                self.reply(200, {"v": 1})
            return
        if len(parts) != 4 or parts[:2] != ["v1", "leases"]:
            return self.err(404, "not_found")
        l = self.lease(parts[2])
        if not l:
            return
        action = parts[3]
        if action == "messages":
            if not b.get("text") or not b.get("client_msg_id"):
                return self.err(400, "bad_request", "text、client_msg_id 必填")
            with _lock:
                if b["client_msg_id"] in l.seen_msgs:
                    return self.reply(202, {"v": 1, "duplicate": True})
                if l.state in ("terminal", "detaching", "detached"):
                    return self.err(409, "busy", l.state)
                l.seen_msgs.append(b["client_msg_id"])
            try:
                send_message(l, b["text"])
            except Exception as e:
                return self.err(500, "resume_failed", repr(e)[:300])
            return self.reply(202, {"v": 1})
        if action == "warned":
            with _lock:
                l.warned_at = iso()
            l.emit("state", state=l.state, warned=True)
            return self.reply(200, {"v": 1})
        if action == "keepalive":
            with _lock:
                l.touch()
            return self.reply(200, {"v": 1})
        if action == "terminal":
            with _lock:
                if l.state in ("detaching", "detached"):
                    return self.err(409, "busy", l.state)
                stop_proc(l, "terminal")
            return self.reply(200, {"v": 1, "command": f"~/agent-hub/bin/lease-resume {l.id}"})
        if action == "detach":
            if l.state in ("detaching", "detached"):
                return self.err(409, "busy", l.state)
            if l.state == "terminal" and terminal_open(l):
                return self.err(409, "busy", "terminal_open")
            threading.Thread(target=detach, args=(l, b.get("reason") or "user"), daemon=True).start()
            return self.reply(202, {"v": 1})
        self.err(404, "not_found", action)

    def create(self, b: dict) -> None:
        proj, sid = b.get("project"), str(b.get("session_id") or "")
        if b.get("mode", "discuss") != "discuss":
            return self.err(400, "bad_request", "目前只有 discuss(work 等第二階段 pilot 之後)")
        try:
            uuid.UUID(sid)
        except ValueError:
            return self.err(400, "bad_request", "session_id 要是 UUID")
        import registry
        registry.refresh()  # 剛加進登錄的專案也認得
        ws = hubconf.workspace(proj) if proj in hubconf.projects() or proj == hubconf.hub_project() else None
        if not ws or not ws.is_dir():
            return self.err(404, "not_registered", str(proj))
        with _lock:
            if sid in LEASES and live(LEASES[sid]):
                return self.reply(200, {"v": 1, **LEASES[sid].public()})
            if sum(1 for l in LEASES.values() if live(l)) >= GLOBAL_CAP:
                return self.reply(409, {"v": 1, "error": "cap_reached", "detail": f"同時最多 {GLOBAL_CAP} 個",
                                        "leases": [l.public() for l in LEASES.values() if live(l)]})
            lease = Lease({"lease_id": sid, "project": proj, "mode": "discuss", "thread": b.get("thread", ""),
                           "cwd": str(ws), "started_once": bool(b.get("resume")), "state": "attaching",
                           "max_turns": int(b.get("max_turns") or DEFAULT_MAX_TURNS)})
            LEASES[sid] = lease
            lease.emit("state", state="attaching", project=proj)
            try:
                start_proc(lease, resume=bool(b.get("resume")))
            except Exception as e:
                lease.set_state("detached", reason="crash")
                return self.err(500, "internal", repr(e)[:300])
        self.reply(201, {"v": 1, **lease.public()})


def main() -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    token()
    load()
    threading.Thread(target=idle_loop, daemon=True).start()
    srv = http.server.ThreadingHTTPServer((HOST, PORT), H)
    srv.daemon_threads = True
    log(f"lease-runner 開始,{HOST}:{PORT},{len(LEASES)} 個租約紀錄")
    srv.serve_forever()


if __name__ == "__main__":
    main()
