"""執行環境的轉接層(Claude Code):平台只透過這裡知道 Claude Code 的 session 跟設定格式。

換工具、或 Claude Code 出了正式的「叫醒一個開著的 session」方法,只改這個檔;hubbus 跟各專案的 agent 都不用動。
設計:docs/design/mailbox-routing.md(session)、docs/design/capabilities.md(設定)。標準函式庫,系統的 python3(3.9)
也能 import——所以這裡不 import registry/policy,只吃 policy.policy() 算好的 dict。

  live_sessions()        還活著的 session id(Claude Code 程序的參數帶著 --resume=<id> 或 --session-id=<id>;
                         10-24 驗證過跟 hook 的 session_id、CLAUDE_CODE_SESSION_ID 是同一個)
  context_tokens(sid)    接續這個 session 要載入多少 context(對話紀錄最後一則回覆的 usage;壓縮過的就是
                         壓縮後的大小)。找不到就 None
  resume_args(sid)       背景接續用的 claude 參數:--resume <sid> --fork-session(10-24 實測:分支帶著完整 context、
                         拿到新的 id、原本的對話紀錄完全不動——開著的 session 不受影響)

設定(中立政策 → Claude Code 的 settings;政策的欄位見 policy.py):
  settings(pol)          → (sandbox, deny, allow):專案 .claude/settings.local.json 裡平台管的部分
                         (render-project-settings.py 負責寫檔、記錄、偵測偏移)
  lease_settings(pol)    → 租約 `--settings` 的內容:同一份 deny + 使用者層級的憑證 + 擋 `security`
  settings_path(ws)、sandbox_rendered(ws)   設定檔在哪、OS sandbox 是不是真的已經寫進去了
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess

PROJECTS = pathlib.Path.home() / ".claude" / "projects"
_ID = re.compile(r"--(?:resume|session-id)[= ]([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def live_sessions() -> set[str]:
    r = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True)
    return set(_ID.findall(r.stdout)) if r.returncode == 0 else set()


def alive(sid: str | None) -> bool:
    return bool(sid) and sid in live_sessions()


def transcript(sid: str) -> pathlib.Path | None:
    hits = list(PROJECTS.glob(f"*/{sid}.jsonl")) if sid and re.fullmatch(r"[0-9a-f-]{36}", sid) else []
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def context_tokens(sid: str) -> int | None:
    t = transcript(sid)
    if not t:
        return None
    try:
        lines = t.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        u = (e.get("message") or {}).get("usage") if e.get("type") == "assistant" else None
        if u:
            return int(u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                       + u.get("cache_creation_input_tokens", 0))
    return None


def resume_args(sid: str) -> list[str]:
    return ["--resume", sid, "--fork-session"]


# ---------- 設定 ----------

def _tilde(p) -> str:
    s, h = str(p), str(pathlib.Path.home())
    return "~" + s[len(h):] if s == h or s.startswith(h + "/") else s


def settings(pol: dict) -> tuple[dict, list[str], list[str]]:
    deny = []
    for p in pol["other_projects"]:
        deny += [f"Read(/{p}/**)", f"Edit(/{p}/**)"]  # //abs:絕對路徑
    deny += [f"Edit({_tilde(p)}/**)" for p in pol["deny_write"]]
    deny += [f"Read({_tilde(p)}/**)" for p in pol["deny_read"]]
    allow = [f"Bash({_tilde(c)} *)" for c in pol["platform_commands"]]
    sandbox = {
        "enabled": True,
        "failIfUnavailable": True,
        "allowUnsandboxedCommands": False,
        "filesystem": {
            "allowWrite": list(pol["write"]),
            "denyRead": [_tilde(p) for p in pol["other_projects"]] + [_tilde(p) for p in pol["deny_read"]],
        },
        "network": {"allowedDomains": list(pol["network"])},
    }
    return sandbox, deny, allow


def lease_settings(pol: dict) -> dict:
    deny = settings(pol)[1]
    deny += [f"Read({_tilde(p)})" for p in pol["credential_files"]] + [f"Read({_tilde(p)}/**)" for p in pol["credentials"]]
    # Keychain 不是檔案,OS sandbox 擋不住(10-25 實測:sandbox 裡的 Bash 查得到 Claude 的登入憑證)。
    # 這幾條只是補強——換個寫法(sh -c、python)就繞得過,真正的把關是 work 模式的指令每次都問 Knas
    deny += ["Bash(security:*)", "Bash(security *)", "Bash(/usr/bin/security *)"]
    return {"permissions": {"deny": deny}}


def settings_path(ws: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(ws) / ".claude" / "settings.local.json"


def sandbox_rendered(ws) -> tuple[bool, str]:
    """登錄的 enforce.sandbox 只是意圖,寫出來的設定檔才是事實。"""
    try:
        st = json.loads(settings_path(ws).read_text())
    except (OSError, ValueError, TypeError):
        return False, "sandbox 還沒寫進這個專案的設定(settings.local.json 讀不到)"
    if not (st.get("sandbox") or {}).get("enabled"):
        return False, "sandbox 還沒寫進這個專案的設定(sandbox.enabled 不是 true)"
    return True, ""


# ---------- sessions(runtime_ports 的 sessions 介面)----------

def context_size(sid: str) -> int | None:
    return context_tokens(sid)


def current_session(env) -> str | None:
    """Claude Code 在 session 裡跑的程序(Bash、hook)都帶著這個環境變數。"""
    return env.get("CLAUDE_CODE_SESSION_ID") or None


# ---------- 對話紀錄(runtime_ports 的 transcripts 介面)----------
# Claude Code 的對話紀錄是沒有公開文件的內部格式(~/.claude/projects/<slug>/<session>.jsonl)。這裡是平台
# 唯一讀它的地方:解析成中立的 Turn/Action,認不得的格式明確失敗(TranscriptFormatError),不讓統計默默算錯。

import datetime  # noqa: E402

from runtime_ports import Action, Turn  # noqa: E402


class TranscriptFormatError(Exception):
    pass


def _mini_home() -> str:
    try:
        import hubconf
        return hubconf.get("hosts.mini_home", str(pathlib.Path.home()))
    except Exception:
        return str(pathlib.Path.home())


def transcript_roots() -> tuple:
    """讀哪些地方:MBP 每小時備份過來的(個人 profile)+ mini 本機 session 的。
    **不要**加工作 profile 的備份(~/transcript-backups/claude-work-projects):weekly-reflect 會把這裡讀到的
    訊息原文送雲端模型,工作對話只能在本機處理(見 known-issues)。"""
    return (pathlib.Path.home() / "transcript-backups" / "claude-personal-projects",
            pathlib.Path(_mini_home()) / ".claude" / "projects",
            pathlib.Path.home() / ".claude-personal" / "projects")   # MBP 上手動跑(trajectory)時的個人 profile


def slug_for_path(path: str) -> str:
    """Claude Code 存對話紀錄的資料夾名:路徑裡的 / 跟 . 換成 -。"""
    return path.replace("/", "-").replace(".", "-")


def _is_human_prompt(entry: dict) -> bool:
    """只算使用者實際送出的訊息。排除:其他 session 傳來的、背景工作通知、slash command 的輸出、壓縮摘要、
    hook 注入等(它們在對話紀錄裡也是 type=user)。"""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isCompactSummary"):
        return False
    if not isinstance(entry.get("message", {}).get("content"), str):
        return False  # 工具結果
    origin = entry.get("origin")
    return isinstance(origin, dict) and origin.get("kind") == "human"


def _action(block: dict) -> Action:
    """Claude 的工具名稱 → 平台中立的動作種類(runtime_ports.ACTIONS)。"""
    name = block.get("name", "")
    if name == "Agent":
        return Action("subagent")
    if name == "Workflow":
        return Action("workflow")
    if name == "SendMessage" or name.endswith("send_message"):
        return Action("peer_message")
    if name == "Skill":
        return Action("skill", (block.get("input") or {}).get("skill", "?"))
    return Action("tool", name)


def _parse_ts(ts: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))


_KNOWN = {"user", "assistant", "system", "summary", "attachment", "progress", "queue-operation", "file-history-snapshot"}


def _turn(o: dict, fallback_sid: str) -> Turn | None:
    ts = o.get("timestamp")
    if not ts:
        return None
    sid = o.get("sessionId") or fallback_sid
    if _is_human_prompt(o):
        return Turn(_parse_ts(ts), sid, "human", o["message"]["content"])
    if o.get("type") == "assistant":
        content = (o.get("message") or {}).get("content") or []
        acts = tuple(_action(c) for c in content if isinstance(c, dict) and c.get("type") == "tool_use")
        return Turn(_parse_ts(ts), sid, "assistant", "", acts)
    return Turn(_parse_ts(ts), sid, "other")


def turns(paths, since: datetime.datetime, until: datetime.datetime | None = None):
    for root, path in ((r, p) for r in transcript_roots() for p in paths):
        for f in (root / slug_for_path(str(path))).glob("*.jsonl"):
            if datetime.datetime.fromtimestamp(f.stat().st_mtime, datetime.timezone.utc) < since:
                continue  # 範圍開始前就沒再寫過
            parsed = recognised = 0
            with f.open(errors="replace") as fh:
                for line in fh:
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    parsed += 1
                    if not isinstance(o, dict) or o.get("type") not in _KNOWN:
                        continue
                    recognised += 1
                    t = _turn(o, f.stem)
                    if t and t.ts >= since and (until is None or t.ts < until):
                        yield t
            if parsed >= 5 and recognised == 0:
                raise TranscriptFormatError(f"{f}:{parsed} 筆紀錄沒有一筆是認得的格式(Claude Code 改了對話紀錄格式?)")


def last_activity(path) -> datetime.datetime | None:
    ts = [f.stat().st_mtime for root in transcript_roots()
          for f in (root / slug_for_path(str(path))).glob("*.jsonl")]
    return datetime.datetime.fromtimestamp(max(ts), datetime.timezone.utc) if ts else None


# ---------- 跑一次(runtime_ports 的 agent 介面)----------
# Claude Code 的 `claude -p` 旗標只在這裡組。判斷點宣告(Task.spec)裡的 permission_mode / allowed_tools / tools /
# prompts / extra_args / allow_from_env / extra_allow_env 是 Claude 專用的,第 2 層中立政策定案前暫時由這裡讀。

import os  # noqa: E402

from runtime_ports import TaskResult  # noqa: E402


def _flags(task) -> list:
    spec = task.spec
    try:
        import hubconf
        exe = hubconf.tool("claude")  # 排程底下的 PATH 沒有 nvm:先照 PATH,再照平台的(nvm 預設版本)
    except Exception:
        exe = "claude"
    cmd = [exe, "-p", task.prompt]
    if task.model:
        cmd += ["--model", task.model]
    mode, allowed, prompts = spec.get("permission_mode"), list(spec.get("allowed_tools") or []), spec.get("prompts")
    env = os.environ if task.env is None else task.env
    if spec.get("extra_allow_env") and env.get(spec["extra_allow_env"]):
        allowed += json.loads(env[spec["extra_allow_env"]])  # 只加規則,不改權限模式
    if spec.get("allow_from_env") and env.get(spec["allow_from_env"]):
        # 登錄 job 宣告的 allow 清單:無人值守 = dontAsk,沒列的一律拒絕、不提示
        allowed += json.loads(env[spec["allow_from_env"]])
        mode, prompts = "dontAsk", "none"
    if mode:
        cmd += ["--permission-mode", mode]
    if prompts:
        cmd += ["--permission-prompts", prompts]
    if "tools" in spec:
        cmd += ["--tools", spec["tools"]]
    if allowed:
        cmd += ["--allowedTools", *allowed]
    return cmd + list(spec.get("extra_args") or []) + list(task.extra or [])


def describe(task) -> list:
    cmd = _flags(task) + ["--output-format", "json"]
    if task.output_schema is not None:
        cmd += ["--json-schema", json.dumps(task.output_schema, ensure_ascii=False)]
    return cmd


def run(task) -> TaskResult:
    cmd = describe(task)
    try:
        r = subprocess.run(cmd, cwd=task.cwd or None, capture_output=True, text=True, timeout=task.timeout_s,
                           env=task.env)
    except subprocess.TimeoutExpired:
        return TaskResult(False, error=f"超過 {task.timeout_s // 60} 分鐘")
    except FileNotFoundError:
        return TaskResult(False, error="找不到 claude")
    import sys
    sys.stderr.write(r.stderr)
    if r.returncode != 0 or not r.stdout.strip():
        # claude -p 的錯誤(例:Login expired)常常只在 stdout 的 JSON 裡
        return TaskResult(False, error=f"exit {r.returncode}:{(r.stderr.strip() or r.stdout.strip())[-300:]}")
    try:
        out = json.loads(r.stdout)
    except ValueError:
        return TaskResult(False, error="輸出不是 JSON:" + r.stdout[:200])
    # exit 0 而且 JSON 讀得到 = 跑完了(ok)。is_error 只標成 incomplete:以前 llm.py 就是照樣交給呼叫端,
    # librarian 靠這個「仍以實際檔案變化為準」,不能改成失敗
    return TaskResult(
        ok=True, incomplete=bool(out.get("is_error")), text=str(out.get("result") or ""),
        structured_output=out.get("structured_output"), cost_usd=out.get("total_cost_usd"),
        session_id=out.get("session_id"), turns=out.get("num_turns"), stop=str(out.get("subtype") or ""),
        denials=tuple(out.get("permission_denials") or ()))


def exec_stream(task) -> None:
    """長駐的串流 session(通道租借用):照宣告組好旗標,直接換成 claude,stdin/stdout 交給呼叫的人。
    暫時的:之後由第 1 層的 open_session 取代(租約的契約中立化 m10251446 還沒定由誰做),所以不在介面裡。"""
    cmd = _flags(task)
    cmd[1:3] = ["-p"]  # 沒有 prompt 參數,訊息從 stdin(stream-json)來
    cmd += ["--input-format", "stream-json", "--output-format", "stream-json", "--include-partial-messages", "--verbose"]
    os.execvpe(cmd[0], cmd, task.env if task.env is not None else os.environ)
