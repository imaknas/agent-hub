#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""通道租借的 runner:Muninn 把一個對話串「借」給某個專案的 Claude Code session,這裡負責開、接續、轉送、收回。

只聽 127.0.0.1(預設 18797),每個請求要 `Authorization: Bearer <~/agent-hub/.secrets/runner-token>`。
契約 v2(contracts/lease-runner.v2.schema.json;docs/design/lease-contract-neutral.md):跟執行環境無關——動作用 kind、
配額用 quota.windows、錯誤碼 agent_login。v1 在 10-25 Muninn 換好之後刪了(schema 在 contracts/retired/);改版前存下的事件照原樣送。
Claude Code 的輸出怎麼翻成中立事件在 runtime 的 parse_stream(rt())。
API:
  POST /v1/leases                     {v, project, mode: discuss, session_id, thread, resume?, max_turns?} → 201
  POST /v1/leases/<id>/messages       {v, text, client_msg_id, attachments?} → 202(同一個 client_msg_id 只送一次;程序停了自動接回)
                                      attachments: [{name, media_type, data(base64)}],最多 8 個、每個 ≤ 10 MB;存進專案的
                                      attachments/<時間>-<名稱>(不進 git),圖片也直接給模型看;有附件時 text 可以是空字串
  GET  /v1/leases/<id>/events         SSE;Last-Event-ID 之後的補送
  POST /v1/leases/<id>/warned         Muninn 確認「還要繼續嗎」的推播真的送出去了(之後 10 分鐘沒回應才收回)
  POST /v1/leases/<id>/keepalive      回答「繼續」
  POST /v1/leases/<id>/terminal       L3:停掉程序,回 {command, hint}(runtime 的 resume_command)
  POST /v1/leases/<id>/detach         {v, reason} → 202;交接在背景跑,結果走事件
  GET  /v1/leases、GET /v1/status、GET /v1/policy(不能 allow_session 的網域,hub.json lease.no_session_domains)
  POST /v1/leases/<id>/mode           {v, mode: discuss|work} → 200;正在回答中 409 busy;work 只限想法沙盒(見 work_ready)
  POST /v1/sandboxes                  {v, name: <想法的代號>, purpose} → 201 新建 / 200 同名同 purpose 已存在(重送)
                                      / 400 bad_name / 409 exists / 429 too_many(帶 limit、current)/ 500 create_failed
                                      想法沙盒:bin/create-idea-sandbox.py 建(docs/design/idea-sandboxes.md),建好就在 attachable
  POST /v1/projects/<name>/rename     {v, to} → 200 {name, origin, renamed}(改名到一半的重試回 renamed:false)/ 400 bad_name
                                      / 404 not_found / 409 busy|name_taken / 500 rename_failed = bin/project-rename.py
                                      (GitHub repo、登記、memory、資料夾);深談的處理跟升級一樣(quiesce、follow_move)
  POST /v1/sandboxes/<name>/upgrade   {v, rename?} → 200 {name, origin, upgraded}(升級過的再送回 upgraded:false + 現在的名稱)
                                      / 404 not_found / 409 not_idea|dirty|name_taken / 500 upgrade_failed
                                      = bin/upgrade-idea.py --apply --json(GitHub repo 預設 private;Muninn 問過 Knas 才送)
  POST /v1/internal/<id>/relay        權限轉接回報(稽核 + 等待中不算閒置)
  POST /v1/internal/<id>/show         {path, summary?}:給使用者看專案裡的一個檔案(lease_files.py 檢查、複製)→ v2 的 file 事件
  GET  /v1/leases/<id>/files/<file id> 取那個檔案(複製的那份,保留 30 天);設計 docs/design/lease-show-file.md
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
import runtime_ports  # noqa: E402


def rt():
    """這個 runner 用的執行環境(runtime_ports;現在全部是 hub.json 的 runtime.default)。Claude Code 的細節都在它裡面。"""
    return runtime_ports.load()

HUB = hubconf.ROOT
HOST, PORT = "127.0.0.1", int(os.environ.get("LEASE_RUNNER_PORT", "18797"))
DIR = pathlib.Path(os.environ.get("LEASE_RUNNER_DIR") or hubconf.STATE / "lease-runner")  # 測試用另一個目錄
TOKEN_FILE = HUB / ".secrets" / "runner-token"
PY = sys.executable
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

PREAMBLE_WORK = """這是「通道租借」的深談,而且 Knas 已經允許你在這個專案裡動手(work):Knas 正在手機上透過他的助理 Muninn 跟你對話。
- 你可以讀檔、搜尋、查資料,也可以直接改這個專案資料夾裡的檔案(不用問);**每一個指令(Bash)都會先送到他手機上確認**,
  所以一次跑一件清楚的事,先用一兩句說明你要跑什麼、為什麼。被拒絕就不要重試同一件事,用文字問他。
- 只能動這個專案的資料夾;資料夾外的寫入會直接被拒絕,指令也在 sandbox 裡(寫不到別處、網路只有白名單)。
- 要 Knas 在幾個選項裡選時,用提問工具(AskUserQuestion)。回覆簡短、適合在手機上讀。
- 收回時系統會要你寫一份結構化交接(摘要、決定、未決問題、Knas 要做的事、memory 筆記)。"""

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
                "state": self.state, "last_activity": self.last_activity, "waiting": self.waiting > 0,
                "resumable": rt().resumable(self.id)}

    @property
    def dir(self) -> pathlib.Path:
        return DIR / self.id

    def emit(self, etype: str, **data) -> dict:
        """契約 v2 的事件。`_` 開頭的欄位是內部訊號,不存、不送。"""
        with _lock:
            self.seq += 1
            ev = {"v": 2, "seq": self.seq, "lease_id": self.id, "ts": iso(), "type": etype,
                  **{k: v for k, v in data.items() if not k.startswith("_")}}
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


def render(ev: dict) -> dict:
    """存下來的事件 → 送出去的樣子(拿掉內部欄位)。改版前存的事件(v: 1)照原樣送。"""
    return {k: v for k, v in ev.items() if k != "_v1"}


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
    """跟 render-project-settings 同一份政策的 deny(不管專案有沒有開 enforce)+ 使用者層級的憑證(runtime_claude.lease_settings)。"""
    import policy
    import runtime_claude
    p = DIR / project / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(runtime_claude.lease_settings(policy.for_project(project)), ensure_ascii=False))
    return p


def mcp_config(lease: Lease) -> pathlib.Path:
    import runtime_claude  # 租約的 MCP 設定還是 Claude Code 的格式(長駐 session 那層之後搬進 runtime)
    p = lease.dir / "mcp.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"mcpServers": {runtime_claude.RELAY_SERVER: {
        "command": PY, "args": [str(HUB / "bin" / "lease-relay.py")],
        "env": {"LEASE_ID": lease.id, "LEASE_PROJECT": lease.project, "LEASE_RUNNER": f"http://{HOST}:{PORT}"}}}}))
    return p


def allow_rules(project: str) -> list[str]:
    doc = hubconf.projects().get(project) or {}
    return [f"WebFetch(domain:{d})" for d in (doc.get("network") or [])]


def env_for(lease: Lease) -> dict:
    env = rt().clean_env(os.environ)  # 憑證由 llm.py exec 決定(runtime 的 auth_env)
    env.update(HUBBUS_LEASE="1", LEASE_ALLOW=json.dumps(allow_rules(lease.project)),
               LEASE_MODE=lease.mode, LEASE_WS=lease.cwd,  # 權限轉接:work 模式才放行(問 Knas)寫入
               PATH=os.environ.get("LEASE_PATH_PREFIX", "") +  # 測試用:把假的 claude 放前面
                    f"{hubconf.LAUNCHD_PATH}:{pathlib.Path.home()}/.local/bin:/usr/sbin:/sbin")
    return env


# ---------- 程序 ----------

def work_ready(project: str) -> tuple[bool, str]:
    """work 模式(第二階段)只開給想法沙盒,而且 OS sandbox 要真的已經寫進那個專案的設定——登錄的
    enforce.sandbox 只是意圖,render-project-settings 寫出來的 settings.local.json 才是事實(沙盒剛建好、
    設定還沒渲染的那段時間,不能讓 work 在沒有 sandbox 的情況下跑)。"""
    doc = hubconf.projects().get(project) or {}
    if doc.get("type") != "idea":
        return False, f"{project} 不是想法沙盒;work 模式目前只開給想法沙盒"
    import runtime_claude
    return runtime_claude.sandbox_rendered(hubconf.workspace(project))


def work_projects() -> list[str]:
    return sorted(n for n in hubconf.projects() if work_ready(n)[0])


def preamble(project: str, mode: str = "discuss") -> str:
    """租約說明(discuss 唯讀 / work 可以動手,每次寫入都問);想法沙盒(登錄 type: idea)多一段:在探討哪個想法。"""
    base = PREAMBLE_WORK if mode == "work" else PREAMBLE
    base += """
- 要讓 Knas 看專案裡的圖或檔案(png、jpg、pdf、glb、md、csv…),用 show_file 工具(專案資料夾裡的路徑 + 一句說明):
  他在手機上會直接看到。不要叫他回電腦打開;一次給一個,太大(20 MB 以上)或像憑證的檔不會送。"""
    doc = hubconf.projects().get(project) or {}
    caps = (doc.get("enforce") or {}).get("capabilities") or []
    if mode == "work" and caps:  # 平台代跑(docs/design/capabilities.md):sandbox 擋住的東西(例:GPU)走這裡
        hub_run = hubconf.ROOT / "bin" / "hub-run"
        base += f"""
- 這個專案可以用平台代跑的能力:{', '.join(caps)}。需要 GPU 的程式(例:Blender)在一般指令裡會被 sandbox 擋住,改用
  `{hub_run} --cap gpu -- <指令…>` 跑(在專案資料夾裡;一樣是一個要 Knas 確認的指令)。代跑的程式只能寫專案資料夾、
  不能連網路。輸出最後才一次出現;這次指令逾時中斷的話,工作照跑,用 `{hub_run} --wait <id>` 接回。"""
    if doc.get("type") != "idea":
        return base
    return base + f"""
- 這個專案是想法沙盒:Muninn 替 Knas 的一個想法開的探討用專案,想法是:「{doc.get('purpose', '')}」。
  目標是幫他把這個想法查清楚、想清楚(可行嗎、要什麼、風險、下一步),結論和決定會進交接筆記。"""


def start_proc(lease: Lease, resume: bool) -> None:
    # 接續的前提是對話紀錄已經存在:Claude Code 要跑完第一輪才會寫出來。還沒說過話就切換模式、閒置停掉、
    # runner 重啟時用 --resume 會 "No conversation found" 掛掉(10-25 Knas 遇到,lease 34c84c53)
    import runtime_claude  # 旗標還是 Claude Code 的(長駐 session 那層之後搬進 runtime)
    wanted = resume or lease.started_once
    can_resume = wanted and rt().resumable(lease.id)
    flag = ["--resume", lease.id] if can_resume else ["--session-id", lease.id]
    point = "lease-work" if lease.mode == "work" else "lease-session"
    cmd = [PY, str(HUB / "bin" / "llm.py"), "exec", point, "--cwd", lease.cwd, "--",
           *flag, "--strict-mcp-config", "--mcp-config", str(mcp_config(lease)),
           "--permission-prompt-tool", runtime_claude.APPROVE_TOOL,
           "--settings", str(deny_settings(lease.project)), "--append-system-prompt", preamble(lease.project, lease.mode),
           "--max-turns", str(lease.max_turns)]
    lease.dir.mkdir(parents=True, exist_ok=True)
    err = open(lease.dir / "stderr.log", "a")
    lease.proc = subprocess.Popen(cmd, cwd=lease.cwd, env=env_for(lease), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=err, text=True, bufsize=1)
    lease.intent_stop = False  # started_once 等第一輪真的跑完才設(reader 收到 num_turns > 0 的 result)
    if wanted and not can_resume:  # 要接回卻接不回(紀錄不在):照實說,不默默開新的
        lease.set_state("running", pid=lease.proc.pid, resumed=False,
                        detail="之前的對話紀錄已經不在(還沒說過話,或保留期限過了),從新的對話開始")
    else:
        lease.set_state("running", pid=lease.proc.pid, resumed=can_resume)
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


def reader(lease: Lease, proc: subprocess.Popen) -> None:
    """runtime 的原生輸出 → 中立的對話事件(rt().parse_stream)→ 送出;回合結束時更新租約的狀態。"""
    runtime = rt()
    for line in proc.stdout:
        events = runtime.parse_stream(line)
        done = [e for e in events if e.type == "turn_end" and "num_turns" in e.data]
        if done:
            with _lock:
                lease.in_turn = False
                # 啟動就失敗的程序也會先吐一個 is_error、num_turns=0 的 result 再退出:那不算成功,
                # 不能把 crashes 歸零(不然永遠到不了 2、不會自動收回,每則訊息都重新掛一次)
                if not (done[0].data.get("is_error") and not done[0].data.get("num_turns")):
                    lease.crashes = 0
                if done[0].data.get("num_turns"):
                    lease.started_once = True  # 對話紀錄寫出來了,之後可以接回
                lease.touch()
        for e in events:
            if e.type == "quota":
                STATUS["quota"] = {**e.data, "at": iso()}
            lease.emit(e.type, **e.data)
            if e.data.get("_detach"):
                threading.Thread(target=detach, args=(lease, e.data["_detach"]), daemon=True).start()
    proc.wait()
    with _lock:
        if lease.proc is not proc or lease.intent_stop:
            return
        lease.proc, lease.in_turn = None, False
        lease.crashes += 1
        tail = (lease.dir / "stderr.log").read_text(errors="replace")[-400:] if (lease.dir / "stderr.log").exists() else ""
        e = rt().classify_exit(proc.returncode, tail)
        lease.emit(e.type, **e.data)
        if lease.crashes >= 2:
            threading.Thread(target=detach, args=(lease, "crash"), daemon=True).start()
        else:
            lease.set_state("idle_stopped")  # 下一則訊息自動 --resume 一次


MAX_ATTACHMENTS, MAX_ATTACHMENT_BYTES, MAX_BODY_BYTES = 8, 10 * 1024 * 1024, 100 * 1024 * 1024
IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")


def check_attachments(atts) -> str | None:
    """不合格就回原因(400 bad_request)。"""
    import base64
    if atts is None:
        return None
    if not isinstance(atts, list) or len(atts) > MAX_ATTACHMENTS:
        return f"attachments 要是最多 {MAX_ATTACHMENTS} 個的 list"
    for a in atts:
        if not isinstance(a, dict) or not a.get("name") or not a.get("media_type") or not isinstance(a.get("data"), str):
            return "每個附件要有 name、media_type、data(base64)"
        try:
            raw = base64.b64decode(a["data"], validate=True)
        except ValueError:
            return f"{a['name']}:data 不是合格的 base64"
        if len(raw) > MAX_ATTACHMENT_BYTES:
            return f"{a['name']}:超過 {MAX_ATTACHMENT_BYTES // 1024 // 1024} MB"
    return None


def save_attachments(lease: Lease, atts: list) -> list[str]:
    """存進專案資料夾的 attachments/(檔名清乾淨、不覆蓋),回傳相對路徑。attachments/ 寫進 .git/info/exclude:
    照片不進 repo 歷史(想法沙盒沒有 remote,但 memory 以外的東西也不該被 commit 進去)。"""
    import base64
    ws = pathlib.Path(lease.cwd)
    d = ws / "attachments"
    d.mkdir(exist_ok=True)
    r = subprocess.run(["git", "-C", str(ws), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        ex = pathlib.Path(r.stdout.strip()) / "info" / "exclude"
        ex.parent.mkdir(parents=True, exist_ok=True)
        lines = ex.read_text().splitlines() if ex.exists() else []
        if "attachments/" not in lines:
            ex.write_text("\n".join(lines + ["attachments/"]) + "\n")
    stamp, out = f"{now():%Y%m%d-%H%M%S}", []
    for a in atts:
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", pathlib.Path(a["name"]).name).strip("._") or "file"
        f, i = d / f"{stamp}-{name[:80]}", 1
        while f.exists():
            f, i = d / f"{stamp}-{i}-{name[:80]}", i + 1
        f.write_bytes(base64.b64decode(a["data"]))
        out.append(str(f.relative_to(ws)))
    return out


def send_message(lease: Lease, text: str, attachments: list | None = None) -> None:
    content: str | list = text
    if attachments:
        paths = save_attachments(lease, attachments)
        note = "附件已存到:" + "、".join(paths)
        blocks = [{"type": "image", "source": {"type": "base64", "media_type": a["media_type"], "data": a["data"]}}
                  for a in attachments if a["media_type"] in IMAGE_TYPES]
        content = blocks + [{"type": "text", "text": (text + "\n\n" if text else "") + note}]
        lease.emit("attachments", paths=paths)
    with _lock:
        if lease.proc is None or lease.proc.poll() is not None:
            lease.set_state("resuming")
            start_proc(lease, resume=True)
        lease.touch()
        lease.in_turn = True
        msg = json.dumps({"type": "user", "message": {"role": "user", "content": content}}, ensure_ascii=False)
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


EMPTY_HANDOVER = {"summary": "這次深談沒有開始對話(session 沒有啟動成功,或還沒說過話就收回),沒有內容要交接。",
                  "decisions": [], "open_questions": [], "tasks_for_owner": [], "notes_markdown": ""}


def run_handover(lease: Lease) -> tuple[dict | None, str]:
    """(交接, 失敗的原因)。沒有對話紀錄 = 沒說過話:回固定的「沒有內容」,不算失敗(10-25 租約 34c84c53:
    session 一開始就掛掉,交接拿 --resume 去接一段不存在的對話,被當成「模型沒寫出合格交接」)。"""
    if not rt().resumable(lease.id):
        return dict(EMPTY_HANDOVER), ""
    cmd = [PY, str(HUB / "bin" / "llm.py"), "run", "lease-handover", "--cwd", lease.cwd, "--",
           "--resume", lease.id, "--strict-mcp-config", "--settings", str(deny_settings(lease.project))]
    why = ""
    for attempt in (1, 2):  # 不合格重試一次
        r = subprocess.run(cmd, input=HANDOVER_PROMPT, cwd=lease.cwd, env=env_for(lease), capture_output=True, text=True,
                           timeout=900)
        try:
            out = json.loads(r.stdout)
        except ValueError:
            out = {}
        so = out.get("structured_output")
        if r.returncode == 0 and isinstance(so, dict):
            return so, ""
        why = str(out.get("llm_error") or out.get("result") or r.stderr.strip()[-300:] or f"exit {r.returncode}")[:300]
        log(f"{lease.id} 交接第 {attempt} 次不合格:{why}")
    return None, why


def hubbus(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HUB / "bin" / "hubbus"), *args], capture_output=True, text=True)


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
    subprocess.Popen([sys.executable, str(HUB / "bin" / "hubbus"), "bridge"], stdout=subprocess.DEVNULL,
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
    so, why = None, ""
    try:
        so, why = run_handover(lease)
        failed = "模型沒能產生合格的交接"
    except Exception as e:  # 平台自己的錯(不是模型的):原因照實說
        log(f"{lease.id} 交接失敗:{e!r}")
        why, failed = repr(e)[:300], "平台出錯,不是模型的問題"
    fallback = so is None
    h = {"v": 1, "project": lease.project, "session_id": lease.id, "mode": lease.mode, "reason": reason,
         "summary": f"交接失敗({failed});對話紀錄還在,可以重新 attach 接續" if fallback else so.get("summary", ""),
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
        lease.emit("error", error="handover_failed", detail=f"已送出最小交接。原因:{why}"[:300])
    lease.emit("handover", handover=h)
    lease.set_state("detached", reason=reason)


# ---------- 閒置 ----------

STATUS: dict = {"quota": None}


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
        try:
            prune_files()
        except Exception as e:
            log(f"prune_files:{e!r}")
        restart_if_code_changed()


BUSY = ("attaching", "running", "resuming", "detaching")
CODE_AT_START = 0.0
CODE_SETTLE_S = 60  # 每次重啟 launchd 要等 10 秒才拉起來(throttle),連續改檔時不要一直重啟


def restart_if_code_changed() -> None:
    """bin/ 的程式改過、而且沒有租約在跑或在交接:自己結束,讓 launchd(KeepAlive)用新的程式重啟。
    不然舊的模組會跟磁碟上的新模組混用(10-25 租約 9af483fb 的交接因此失敗)。閒置的租約重啟後照常 --resume 接回。"""
    stamp = hubconf.code_stamp()
    if stamp <= CODE_AT_START or time.time() - stamp < CODE_SETTLE_S:  # 改到一半(連續存檔)先不重啟,穩定了才一次重啟
        return
    with _lock:
        busy = [l.id[:8] for l in LEASES.values() if l.state in BUSY or l.waiting or l.in_turn]
        if busy:
            return
        log("bin/ 的程式改過了,沒有租約在跑:結束,讓 launchd 用新的程式重啟")
        os._exit(0)


# ---------- HTTP ----------

def token() -> str:
    if not TOKEN_FILE.exists():
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(pysecrets.token_urlsafe(32))
        TOKEN_FILE.chmod(0o600)
    return TOKEN_FILE.read_text().strip()


def relay_event(b: dict) -> tuple:
    """權限轉接的回報 → (事件類型, 欄位)。kind 是動作種類(runtime_ports.KINDS);轉接送原生工具名時由 runtime 翻譯。"""
    kind = b.get("kind")
    if kind not in runtime_ports.KINDS:
        kind = rt().action(b.get("tool") or "", {})[0]
    data = {"phase": b.get("phase"), "kind": kind, "summary": b.get("summary", "")}
    for k in ("target", "decision"):
        if b.get(k) is not None:
            data[k] = b[k]
    return ("question" if kind == "ask_user" else "permission"), data


def secret_paths(project: str) -> list:
    """專案登錄 secrets: 宣告的檔案(相對 runtime,沒有 runtime 就相對 workspace)——給使用者看檔案時一律不送。"""
    doc = hubconf.projects().get(project) or {}
    base = (hubconf.p("services_root") / project) if doc.get("runtime") is not None else hubconf.workspace(project)
    out = []
    for sec in doc.get("secrets") or []:
        p = pathlib.Path(os.path.expanduser(str(sec.get("path", ""))))
        if str(p) and base is not None:
            out.append(p if p.is_absolute() else base / p)
    return out


def show_file(lease: "Lease", path: str, summary: str | None) -> tuple:
    """給使用者看專案裡的一個檔案(docs/design/lease-show-file.md):檢查、複製到租約目錄、送 file 事件(只有 v2)。"""
    import lease_files
    meta, why = lease_files.take(lease.cwd, path, summary, lease.dir / "files", secret_paths(lease.project))
    if meta is None:
        log(f"{lease.id[:8]} show_file 拒絕:{path}:{why}")
        return None, why
    lease.emit("file", **meta)
    log(f"{lease.id[:8]} show_file {meta['path']}({meta['size']} bytes)→ {meta['id']}")
    return meta, ""


LAST_FILE_PRUNE = [0.0]


def prune_files() -> None:
    """給使用者看過的檔案保留 lease_files.KEEP_DAYS 天;一小時清一次。"""
    import lease_files
    if time.time() - LAST_FILE_PRUNE[0] < 3600:
        return
    LAST_FILE_PRUNE[0] = time.time()
    with _lock:
        dirs = [l.dir / "files" for l in LEASES.values()]
    for d in dirs:
        lease_files.prune(d, lease_files.KEEP_DAYS * 86400)


def quiesce(project: str) -> str:
    """改名、升級之前:這個專案有深談正在回答、等回覆、在終端機或正在收回 → 回原因(不動任何東西);
    其他的先停程序(租約還在,之後的訊息自動接回)。"""
    with _lock:
        mine = [l for l in LEASES.values() if l.project == project]
        if any(live(l) and (l.in_turn or l.waiting or l.state in ("attaching", "resuming", "detaching", "terminal"))
               for l in mine):
            return f"{project} 有深談正在回答、等回覆、在終端機或正在收回"
        for l in mine:
            if l.proc is not None:
                stop_proc(l, "idle_stopped")
    return ""


def follow_move(old: str, new: str, old_ws: str) -> None:
    """改名、升級完:這個專案的租約跟上新的名稱、資料夾(對話紀錄跟著搬);work 改回 discuss(work 只開給想法沙盒)。"""
    import registry
    registry.refresh()
    new_ws = str(hubconf.workspace(new) or old_ws)
    with _lock:
        mine = [l for l in LEASES.values() if l.project == old]
        for l in mine:
            moved = old_ws != new_ws and rt().relocate(l.id, old_ws, new_ws)
            renamed = (l.project, l.cwd) != (new, new_ws)
            l.project, l.cwd = new, new_ws
            downgraded = l.mode == "work" and not work_ready(new)[0]
            if downgraded:
                l.mode = "discuss"
            if live(l) and (renamed or downgraded):
                extra = {"project": new} if renamed else {}
                if downgraded:
                    extra.update(mode="discuss", detail="升級成正式專案了:work 模式目前只開給想法沙盒,這段深談改回唯讀")
                l.emit("state", state=l.state, **extra)
            log(f"upgrade {old} → {new}:租約 {l.id[:8]} 跟上(對話紀錄{'搬了' if moved else '沒搬'})")
        save()


def live(l: Lease) -> bool:
    return l.state not in ("detached",)


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def reply(self, code: int, body: dict) -> None:
        body = {**body, "v": 2} if "v" in body else body  # 契約 v2(v1 已刪)
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def err(self, code: int, kind: str, detail: str = "") -> None:
        self.reply(code, {"v": 2, "error": kind, "detail": detail})

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY_BYTES:  # 附件是 base64 放在 JSON 裡;太大的請求不讀(不然整個吃進記憶體)
            self.rfile.read(0)
            return {"__bad__": True}
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
                                    "attachable": sorted(idx["projects"]), "modes": ["discuss", "work"],
                                    "work_projects": work_projects()})
        if path == "/v1/status":
            with _lock:
                n = sum(1 for l in LEASES.values() if live(l))
            return self.reply(200, {"v": 2, "leased": n, "cap": GLOBAL_CAP, "quota": STATUS["quota"]})
        if len(parts) == 5 and parts[:2] == ["v1", "leases"] and parts[3] == "files":
            return self.send_file(parts[2], parts[4])
        if len(parts) == 4 and parts[:2] == ["v1", "leases"] and parts[3] == "events":
            l = self.lease(parts[2])
            if l:
                self.sse(l)
            return
        self.err(404, "not_found", path)

    def send_file(self, lid: str, fid: str) -> None:
        """GET /v1/leases/<id>/files/<file id>:給使用者看過的檔案(送的是當時複製的那份)。"""
        import lease_files
        l = LEASES.get(lid)
        got = lease_files.read(l.dir / "files", fid) if l else None
        if not got:
            return self.err(404, "not_found", "沒有這個檔案(或已經過了保留期限)")
        meta, data = got
        self.send_response(200)
        self.send_header("Content-Type", meta["mime"])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", "inline; filename*=UTF-8''" + urllib.parse.quote(meta["name"]))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def sse_line(self, ev: dict) -> bytes:
        out = render(ev)
        return f"id: {out['seq']}\nevent: {out['type']}\ndata: {json.dumps(out, ensure_ascii=False)}\n\n".encode()

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
                self.wfile.write(self.sse_line(ev))
                sent = max(sent, ev["seq"])
            self.wfile.flush()
            while True:
                with l.cond:
                    l.cond.wait(timeout=15)
                    new = [e for e in l.recent if e["seq"] > sent]
                if not new:
                    self.wfile.write(b": keepalive\n\n")
                for ev in new:
                    self.wfile.write(self.sse_line(ev))
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
        if parts == ["v1", "sandboxes"]:
            return self.create_sandbox(b)
        if len(parts) == 4 and parts[:2] == ["v1", "projects"] and parts[3] == "rename":
            return self.rename_project(parts[2], b)
        if len(parts) == 4 and parts[:2] == ["v1", "sandboxes"] and parts[3] == "upgrade":
            return self.upgrade_sandbox(parts[2], b)
        if len(parts) == 4 and parts[:2] == ["v1", "internal"] and parts[3] == "show":
            l = self.lease(parts[2])
            if l:
                meta, why = show_file(l, b.get("path"), b.get("summary"))
                if meta is None:
                    return self.err(400, "file_refused", why)
                with _lock:
                    l.touch()
                self.reply(200, {"v": 2, **meta})
            return
        if len(parts) == 4 and parts[:2] == ["v1", "internal"] and parts[3] == "relay":
            l = self.lease(parts[2])
            if l:
                with _lock:
                    if b.get("phase") == "start":
                        l.waiting += 1
                    elif l.waiting:
                        l.waiting -= 1
                    l.touch()
                etype, data = relay_event(b)
                l.emit(etype, **data)
                self.reply(200, {"v": 1})
            return
        if len(parts) != 4 or parts[:2] != ["v1", "leases"]:
            return self.err(404, "not_found")
        l = self.lease(parts[2])
        if not l:
            return
        action = parts[3]
        if action == "messages":
            atts = b.get("attachments")
            bad = check_attachments(atts)
            if bad:
                return self.err(400, "bad_request", bad)
            if not b.get("client_msg_id") or not (b.get("text") or atts) or not isinstance(b.get("text", ""), str):
                return self.err(400, "bad_request", "client_msg_id 必填;text 跟 attachments 至少要有一個")
            with _lock:
                if b["client_msg_id"] in l.seen_msgs:
                    return self.reply(202, {"v": 1, "duplicate": True})
                if l.state in ("terminal", "detaching", "detached"):
                    return self.err(409, "busy", l.state)
                l.seen_msgs.append(b["client_msg_id"])
            try:
                send_message(l, b.get("text") or "", atts)
            except Exception as e:
                return self.err(500, "resume_failed", repr(e)[:300])
            return self.reply(202, {"v": 1})
        if action == "warned":
            with _lock:
                l.warned_at = iso()
            l.emit("state", state=l.state, warned=True)
            return self.reply(200, {"v": 1})
        if action == "mode":
            mode = b.get("mode")
            if mode not in ("discuss", "work"):
                return self.err(400, "bad_request", "mode 只能是 discuss 或 work")
            if mode == "work":
                ok, why = work_ready(l.project)
                if not ok:
                    return self.err(400, "work_not_allowed", why)
            with _lock:
                if l.state in ("terminal", "detaching", "detached"):
                    return self.err(409, "busy", l.state)
                if l.in_turn or l.waiting:
                    return self.err(409, "busy", "正在回答或等 Knas 回覆,等這一輪結束再切換")
                if l.mode == mode:
                    return self.reply(200, {"v": 1, **l.public()})
                # 換判斷點 = 換程序:停掉、用同一個 session --resume 接回(權限轉接也跟著重開)
                stop_proc(l, "attaching")
                l.mode = mode
                l.emit("state", state="attaching", mode=mode)
                try:
                    start_proc(l, resume=True)
                except Exception as e:
                    l.set_state("detached", reason="crash")
                    return self.err(500, "internal", repr(e)[:300])
                save()
            log(f"lease {l.id[:8]} {l.project}: mode → {mode}")
            return self.reply(200, {"v": 1, **l.public()})
        if action == "keepalive":
            with _lock:
                l.touch()
            return self.reply(200, {"v": 1})
        if action == "terminal":
            with _lock:
                if l.state in ("detaching", "detached"):
                    return self.err(409, "busy", l.state)
                stop_proc(l, "terminal")
            command, hint = rt().resume_command(l.id)
            return self.reply(200, {"v": 2, "command": command, "hint": hint})
        if action == "detach":
            if l.state in ("detaching", "detached"):
                return self.err(409, "busy", l.state)
            if l.state == "terminal" and terminal_open(l):
                return self.err(409, "busy", "terminal_open")
            threading.Thread(target=detach, args=(l, b.get("reason") or "user"), daemon=True).start()
            return self.reply(202, {"v": 1})
        self.err(404, "not_found", action)

    def create_sandbox(self, b: dict) -> None:
        """想法沙盒:交給 create-idea-sandbox.py(驗證、重送、上限、失敗清理都在那裡),把結果轉成 HTTP。"""
        name, purpose = b.get("name"), b.get("purpose")
        if not isinstance(name, str) or not isinstance(purpose, str):
            return self.err(400, "bad_name", "name、purpose 要是字串")
        env = {**os.environ, "PATH": f"{hubconf.LAUNCHD_PATH}:/usr/sbin:/sbin"}
        try:
            r = subprocess.run([PY, str(HUB / "bin" / "create-idea-sandbox.py"), name, purpose],
                               capture_output=True, text=True, timeout=180, env=env)
            res = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
        except (subprocess.TimeoutExpired, ValueError) as e:
            log(f"sandbox {name}: {e!r}")
            return self.err(500, "create_failed", repr(e)[:300])
        log(f"sandbox {name}: {res.get('result')} {res.get('detail', '')}")
        result = res.get("result")
        if result in ("created", "exists_same"):
            return self.reply(201 if result == "created" else 200,
                              {"v": 1, "name": res["name"], "created": result == "created"})
        code = {"bad_name": 400, "exists": 409, "too_many": 429}.get(result, 500)
        body = {"v": 1, "error": result if code != 500 else "create_failed",
                "detail": res.get("detail") or (r.stderr or "").strip()[-300:]}
        body.update({k: res[k] for k in ("limit", "current") if k in res})
        return self.reply(code, body)

    def rename_project(self, name: str, b: dict) -> None:
        """POST /v1/projects/<name>/rename {v, to}:正式專案或想法沙盒整套改名(10-25 Muninn m10251650)。
        交給 project-rename.py(GitHub repo、登記、memory、資料夾;重試接得上);深談的處理跟升級一樣(quiesce、follow_move)。"""
        to = b.get("to")
        if not isinstance(to, str) or not to:
            return self.err(400, "bad_name", "to 要是新名稱")
        why = quiesce(name)
        if why:
            return self.err(409, "busy", why + ",等它停下來再改名")
        old_ws = str(hubconf.workspace(name) or "")
        cmd = [str(HUB / "bin" / "project-rename.py"), name, to, "--apply", "--json"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                               env={**os.environ, "PATH": f"{hubconf.LAUNCHD_PATH}:/usr/sbin:/sbin"})
            res = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
        except (subprocess.TimeoutExpired, ValueError) as e:
            log(f"rename {name} → {to}: {e!r}")
            return self.err(500, "rename_failed", repr(e)[:300])
        log(f"rename {name} → {to}: {res.get('result')} {res.get('detail', '')}")
        result = res.get("result")
        if result in ("renamed", "already"):
            follow_move(name, res["name"], old_ws)
            return self.reply(200, {"v": 2, "name": res["name"], "origin": res.get("origin", ""),
                                    "renamed": result == "renamed"})
        code = {"bad_name": 400, "not_found": 404, "name_taken": 409, "busy": 409}.get(result, 500)
        return self.reply(code, {"v": 2, "error": result if code != 500 else "rename_failed",
                                 "detail": res.get("detail") or (r.stderr or "").strip()[-300:]})

    def upgrade_sandbox(self, name: str, b: dict) -> None:
        """想法沙盒升級成正式專案:交給 upgrade-idea.py(檢查、改名、建 GitHub repo、登錄),把結果轉成 HTTP。
        那個沙盒有租約時(10-25 m10251626、m10251631):正在回答或等使用者回覆 → 409 busy;否則先停程序(租約還在,
        之後的訊息自動接回)。改名的話資料夾會搬:對話紀錄跟著搬到新資料夾(rt().relocate)、租約的 project/cwd 改成新的,
        還沒收回的租約送 state 事件帶新的 project——之後的訊息照常送,runner 在新資料夾接回同一段對話。
        work 模式目前只開給想法沙盒:升級後,work 中的租約改回 discuss(state 事件帶 mode 跟 detail)。"""
        rename = b.get("rename")
        if rename is not None and not isinstance(rename, str):
            return self.err(400, "bad_request", "rename 要是字串")
        why = quiesce(name)
        if why:
            return self.err(409, "busy", why + ",等它停下來再升級")
        old_ws = str(hubconf.workspace(name) or "")
        cmd = [PY, str(HUB / "bin" / "upgrade-idea.py"), name, "--apply", "--json"] + (["--rename", rename] if rename else [])
        env = {**os.environ, "PATH": f"{hubconf.LAUNCHD_PATH}:/usr/sbin:/sbin"}
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
            res = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
        except (subprocess.TimeoutExpired, ValueError) as e:
            log(f"upgrade {name}: {e!r}")
            return self.err(500, "upgrade_failed", repr(e)[:300])
        log(f"upgrade {name}: {res.get('result')} {res.get('name', '')} {res.get('detail', '')}")
        result = res.get("result")
        if result in ("upgraded", "already"):
            follow_move(name, res["name"], old_ws)
            return self.reply(200, {"v": 1, "name": res["name"], "origin": res["origin"], "upgraded": result == "upgraded"})
        code = {"not_found": 404, "not_idea": 409, "dirty": 409, "name_taken": 409}.get(result, 500)
        return self.reply(code, {"v": 1, "error": result if code != 500 else "upgrade_failed",
                                 "detail": res.get("detail") or (r.stderr or "").strip()[-300:]})

    def create(self, b: dict) -> None:
        proj, sid = b.get("project"), str(b.get("session_id") or "")
        mode = b.get("mode", "discuss")
        if mode not in ("discuss", "work"):
            return self.err(400, "bad_request", "mode 只能是 discuss 或 work")
        try:
            uuid.UUID(sid)
        except ValueError:
            return self.err(400, "bad_request", "session_id 要是 UUID")
        import registry
        registry.refresh()  # 剛加進登錄的專案也認得
        ws = hubconf.workspace(proj) if proj in hubconf.projects() or proj == hubconf.hub_project() else None
        if not ws or not ws.is_dir():
            return self.err(404, "not_registered", str(proj))
        if mode == "work":
            ok, why = work_ready(proj)
            if not ok:
                return self.err(400, "work_not_allowed", why)
        with _lock:
            if sid in LEASES and live(LEASES[sid]):
                return self.reply(200, {"v": 1, **LEASES[sid].public()})
            if sum(1 for l in LEASES.values() if live(l)) >= GLOBAL_CAP:
                return self.reply(409, {"v": 1, "error": "cap_reached", "detail": f"同時最多 {GLOBAL_CAP} 個",
                                        "leases": [l.public() for l in LEASES.values() if live(l)]})
            prev = LEASES.get(sid)  # 已收回的租約用同一個 id 接回(Muninn 回到同一段對話):
            if prev and prev.cwd != str(ws):  # 資料夾搬過(改名、升級):對話紀錄跟過來,才接得回
                rt().relocate(sid, prev.cwd, str(ws))
            # 事件序號、處理過的訊息要接著舊的,不然序號從 0 重來、跟 events.jsonl 裡的舊事件撞號,SSE 續接會亂
            lease = Lease({"lease_id": sid, "project": proj, "mode": mode, "thread": b.get("thread", ""),
                           "cwd": str(ws), "started_once": bool(b.get("resume")), "state": "attaching",
                           "max_turns": int(b.get("max_turns") or DEFAULT_MAX_TURNS),
                           "seq": prev.seq if prev else 0, "seen_msgs": prev.seen_msgs if prev else []})
            LEASES[sid] = lease
            lease.emit("state", state="attaching", project=proj)
            try:
                start_proc(lease, resume=bool(b.get("resume")))
            except Exception as e:
                lease.set_state("detached", reason="crash")
                return self.err(500, "internal", repr(e)[:300])
        self.reply(201, {"v": 1, **lease.public()})


def main() -> None:
    global CODE_AT_START
    CODE_AT_START = hubconf.code_stamp()
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
