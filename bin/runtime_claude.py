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

import i18n

_ = i18n.domain(__file__)
NOT_FOUND = "claude not found"   # also what is_login_error looks for (with its translation)
PROJECTS = pathlib.Path.home() / ".claude" / "projects"
MODELS = {"fast": "haiku", "standard": "sonnet", "strong": "opus"}   # 判斷點的 model_group → 模型(runtime_ports.models)
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


def _cli_rules(commands) -> list[str]:
    """平台的 session 指令(「agenthub send」)→ Claude 的規則:不帶參數跟帶參數各一條。"""
    return [r for c in commands for r in (f"Bash({c})", f"Bash({c} *)")]


def settings(pol: dict) -> tuple[dict, list[str], list[str]]:
    deny = []
    for p in pol["other_projects"]:
        deny += [f"Read(/{p}/**)", f"Edit(/{p}/**)"]  # //abs:絕對路徑
    deny += [f"Edit({_tilde(p)}/**)" for p in pol["deny_write"]]
    deny += [f"Read({_tilde(p)}/**)" for p in pol["deny_read"]]
    allow = [f"Bash({_tilde(c)} *)" for c in pol["platform_commands"]] + _cli_rules(pol.get("platform_cli") or [])
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
        return False, _("the sandbox is not in this project's settings yet (settings.local.json cannot be read)")
    if not (st.get("sandbox") or {}).get("enabled"):
        return False, _("the sandbox is not in this project's settings yet (sandbox.enabled is not true)")
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
            pathlib.Path.home() / ".claude-personal" / "projects")   # 在遙控那台手動跑時的個人 profile


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
    """對話紀錄裡的一個工具呼叫 → 中立的 Action(kind 是 KINDS、collab 是協作方式)。翻譯表只有一份:action()。
    detail 只放技能名稱——Claude 的工具名稱不外流到中立格式。"""
    name, inp = block.get("name", ""), block.get("input") or {}
    kind, collab, _target, _summary = action(name, inp)
    return Action(kind, str(inp.get("skill", "?")) if collab == "skill" else "", collab)


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


def _transcript_dirs(root: pathlib.Path, path: str, worktrees: bool) -> list:
    """這個工作目錄的對話紀錄資料夾;worktrees=True 再加上 Claude Code 開在 <path>/.claude/worktrees/<名稱> 的
    session(資料夾名 <slug>--claude-worktrees-<名稱>)。"""
    slug = slug_for_path(str(path))
    return [root / slug, *(sorted(root.glob(slug + "--claude-worktrees-*")) if worktrees else ())]


def turns(paths, since: datetime.datetime, until: datetime.datetime | None = None, worktrees: bool = False):
    for root, path in ((r, p) for r in transcript_roots() for p in paths):
        for f in (f for d in _transcript_dirs(root, path, worktrees) for f in d.glob("*.jsonl")):
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
                raise TranscriptFormatError(_("{file}: {n} {n:record|records}, none in a known format (did Claude Code "
                                              "change its transcript format?)", file=f, n=parsed))


def last_activity(path) -> datetime.datetime | None:
    ts = [f.stat().st_mtime for root in transcript_roots()
          for f in (root / slug_for_path(str(path))).glob("*.jsonl")]
    return datetime.datetime.fromtimestamp(max(ts), datetime.timezone.utc) if ts else None


# ---------- 跑一次(runtime_ports 的 agent 介面)----------
# Claude Code 的 `claude -p` 旗標只在這裡組。判斷點宣告(Task.spec)裡的 permission_mode / allowed_tools / tools /
# prompts / extra_args / allow_from_env / extra_allow_env 是 Claude 專用的,第 2 層中立政策定案前暫時由這裡讀。

import os  # noqa: E402

from runtime_ports import TaskResult  # noqa: E402


def _env(task) -> dict:
    """claude 的環境變數:PATH 補上 nvm 預設版本的 bin。claude 本身用完整路徑叫得到,但 plugin 的 hook
    (例:vercel 的 SessionEnd `node …`)照 PATH 找 node;排程底下的 PATH 沒有 nvm、Homebrew 的 node 也沒了
    (2026-10-24),librarian 每次都印 node: command not found。"""
    env = dict(os.environ if task.env is None else task.env)
    try:
        import hubconf
        nvm = hubconf.nvm_bin()
    except Exception:
        nvm = None
    if nvm and str(nvm) not in env.get("PATH", "").split(":"):
        env["PATH"] = f"{nvm}:{env['PATH']}" if env.get("PATH") else str(nvm)
    return env


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
    if "{platform_mail}" in allowed:   # 判斷點宣告的「平台的信箱指令」:舊的 hubbus 路徑 + PATH 上的 agenthub 收寄信子指令
        import policy
        i = allowed.index("{platform_mail}")
        hub_bin = pathlib.Path(__file__).resolve().parent
        allowed[i:i + 1] = [f"Bash({_tilde(hub_bin / 'hubbus')} *)", *_cli_rules(policy.platform_cli(mail_only=True))]
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
                           env=_env(task))
    except subprocess.TimeoutExpired:
        return TaskResult(False, error=_("over {minutes} {minutes:minute|minutes}", minutes=task.timeout_s // 60))
    except FileNotFoundError:
        return TaskResult(False, error=_(NOT_FOUND))
    import sys
    sys.stderr.write(r.stderr)
    if r.returncode != 0 or not r.stdout.strip():
        # claude -p 的錯誤(例:Login expired)常常只在 stdout 的 JSON 裡
        return TaskResult(False, error=_("exit {code}: {detail}", code=r.returncode,
                                                 detail=(r.stderr.strip() or r.stdout.strip())[-300:]))
    try:
        out = json.loads(r.stdout)
    except ValueError:
        return TaskResult(False, error=_("output is not JSON: {output}", output=r.stdout[:200]))
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
    os.execvpe(cmd[0], cmd, _env(task))


# ---------- 事件(runtime_ports 的 lifecycle 介面)----------
# Claude Code 的 hook:事件名稱、matcher、輸入的 JSON、輸出的 hookSpecificOutput 格式只在這裡。
# 平台這邊只有一個入口 `bin/hub-event <平台事件>`,處理程式回傳中立的文字或拒絕理由。

from runtime_ports import HubEvent  # noqa: E402

# (Claude 的 hook 事件, matcher, 平台事件);matcher None = 這個事件全部都觸發
HOOK_TABLE = (
    ("SessionStart", None, "session_start"),
    ("SessionEnd", None, "session_end"),
    ("PreCompact", None, "pre_compact"),
    ("UserPromptSubmit", None, "prompt"),
    ("PostToolUse", "Bash", "tool_used"),                                    # 目前只接「跑指令」
    ("PreToolUse", "mcp__ccd_session__start_session", "open_session_attempt"),  # Desktop 的開 session 工具
)
_HOOK_NAME = {ev: name for name, _matcher, ev in HOOK_TABLE}
MANAGED = "/agent-hub/bin/"   # 指令裡有這段 = 平台管的 hook(使用者自己加的不會有)


def hook_command(event: str) -> str:
    # 跟以前一樣:入口不在就安靜地不做事,結束碼永遠 0,不會讓 session 顯示錯誤
    import hubconf
    return f"[ -x ~/agent-hub/bin/hub-event ] && {hubconf.sh_py('~/agent-hub/bin/hub-event')} {event} || true"


def parse_event(event: str, raw: str):
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return None
    session = data.get("session_id")
    if not session:  # 沒有 hook JSON = 不是真的 session 在跑(onboard/recover 也會跑設定)
        return None
    cwd = data.get("cwd") or os.getcwd()
    ti = data.get("tool_input") or {}
    if event == "tool_used":
        return HubEvent(event, session, cwd, command=str(ti.get("command", "")))
    if event == "open_session_attempt":
        elsewhere = None  # 中立的種類(說明文字由平台寫):repository / machine / unknown_place
        if ti.get("repository"):
            elsewhere = {"kind": "repository", "name": str(ti["repository"])}
        elif ti.get("host") or ti.get("target") == "ssh":
            elsewhere = {"kind": "machine"}
        elif ti.get("preset"):
            elsewhere = {"kind": "unknown_place"}
        target_cwd = os.path.expanduser(ti["cwd"]) if ti.get("cwd") else None
        return HubEvent(event, session, cwd, target={"cwd": target_cwd, "elsewhere": elsewhere})
    return HubEvent(event, session, cwd)


def inject(event: str, text: str) -> str:
    if not text:
        return ""
    if event == "session_start":
        return text  # SessionStart 的 stdout 直接進 context
    if event in ("prompt", "tool_used"):
        return json.dumps({"hookSpecificOutput": {"hookEventName": _HOOK_NAME[event], "additionalContext": text}},
                          ensure_ascii=False)
    return ""


def deny(event: str, reason: str) -> str:
    if event != "open_session_attempt":
        return ""
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}, ensure_ascii=False)


def install_hooks(settings: dict) -> dict:
    """平台管的 hook(指令裡有 MANAGED)整組換成 HOOK_TABLE 的 hub-event;使用者自己加的留著;重跑結果一樣。"""
    hooks = settings.setdefault("hooks", {})
    for name in list(hooks):
        kept = []
        for entry in hooks[name]:
            inner = [h for h in entry.get("hooks", []) if MANAGED not in str(h.get("command", ""))]
            if inner:
                kept.append({**entry, "hooks": inner})
        if kept:
            hooks[name] = kept
        else:
            del hooks[name]
    for name, matcher, event in HOOK_TABLE:
        entry = {"hooks": [{"type": "command", "command": hook_command(event)}]}
        if matcher:
            entry = {"matcher": matcher, **entry}
        hooks.setdefault(name, []).append(entry)
    return settings


def install_lifecycle(workspace) -> None:
    path = settings_path(workspace)
    settings = json.loads(path.read_text()) if path.exists() else {}
    out = json.dumps(install_hooks(settings), indent=2) + "\n"
    if not path.exists() or path.read_text() != out:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(out)



# ---------- 規則檔(runtime_ports 的 instructions 介面)----------

def instructions_file(workspace) -> pathlib.Path:
    """Claude Code 會跟 CLAUDE.md 一起載入的本機檔(不進版控);平台的受管區塊寫在這裡。"""
    return pathlib.Path(workspace) / "CLAUDE.local.md"


def local_ignores() -> list:
    return ["CLAUDE.local.md", ".claude/settings.local.json"]


# ---------- 長駐 session 的輸出翻譯(stream 介面;docs/design/lease-contract-neutral.md)----------

from runtime_ports import StreamEvent  # noqa: E402

_TOOL_KIND = {"Read": "read", "Glob": "search", "Grep": "search", "WebSearch": "search", "WebFetch": "open_web",
              "Edit": "write_file", "Write": "write_file", "MultiEdit": "write_file", "NotebookEdit": "write_file",
              "Bash": "run_command", "AskUserQuestion": "ask_user"}
_TARGET_KEYS = ("file_path", "notebook_path", "url", "command", "pattern", "query", "path")
_QUOTA_STATUS = {"allowed": "ok", "allowed_warning": "warning", "rejected": "exhausted"}
RELAY_SERVER = "lease_relay"                      # 租約的平台 MCP server(lease-relay.py)在設定裡的名字
APPROVE_TOOL = f"mcp__{RELAY_SERVER}__approve"     # 權限轉接(--permission-prompt-tool)
SHOW_FILE_TOOL = f"mcp__{RELAY_SERVER}__show_file"  # 平台工具 show_file(llm-points 的允許清單也是這個名字)


def _verbs() -> dict:
    return {"read": _("read"), "search": _("search"), "open_web": _("open web page"), "write_file": _("write"),
            "run_command": _("run")}


def _windows() -> tuple:
    return (("five_hour", _("5 hours")), ("seven_day", _("7 days")))


def _questions(inp: dict) -> str:
    return " / ".join(q.get("question", "") for q in inp.get("questions") or [])[:200]


def action(tool: str, inp: dict | None) -> tuple:
    """Claude 的工具 → (kind, collab, target, summary)。target 是判斷用的(路徑、網址、指令),summary 是給人看的一句。"""
    inp = inp or {}
    kind = _TOOL_KIND.get(tool, "other")
    collab = {"Agent": "subagent", "Task": "subagent", "Workflow": "workflow", "Skill": "skill"}.get(tool) or (
        "peer_message" if tool == "SendMessage" or tool.endswith("send_message") else None)
    target = next((str(inp[k]) for k in _TARGET_KEYS if inp.get(k)), "")
    if tool == SHOW_FILE_TOOL:  # 平台的「給使用者看」工具(租約的 MCP):讀一個檔給他看
        return "read", None, str(inp.get("path", "")), _("showing you {path}", path=inp.get("path", "")).strip()
    if kind == "ask_user":
        summary = _("question: {questions}", questions=_questions(inp))
    elif kind == "other":
        summary = f"{tool} {target[:160]}".strip()
    else:
        summary = f"{_verbs()[kind]} {target[:160]}".strip()
    return kind, collab, target, summary


def _iso(epoch) -> str | None:
    import datetime
    return datetime.datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds") if epoch else None


def parse_stream(line: str) -> list:
    """Claude Code 的 stream-json 輸出(`--output-format stream-json`)的一行 → 中立的對話事件。認不得的行回 []。"""
    try:
        d = json.loads(line)
    except ValueError:
        return []
    if not isinstance(d, dict):
        return []
    t, out = d.get("type"), []
    if t == "stream_event":
        ev = d.get("event") or {}
        delta = ev.get("delta") or {}
        if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
            out.append(StreamEvent("text_delta", {"text": delta.get("text", "")}))
    elif t == "assistant":
        for block in (d.get("message") or {}).get("content") or []:
            if block.get("type") == "text" and block.get("text"):
                out.append(StreamEvent("assistant", {"text": block["text"]}))
            elif block.get("type") == "tool_use":
                name, inp = block.get("name", ""), block.get("input") or {}
                kind, _collab, _target, summary = action(name, inp)
                out.append(StreamEvent("tool_use", {"kind": kind, "summary": summary, "detail": {"tool": name}}))
    elif t == "user":
        content = (d.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_result":
                c = block.get("content")
                text = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict))
                out.append(StreamEvent("tool_result", {"is_error": bool(block.get("is_error")), "summary": text[:200],
                                                       "truncated": len(text) > 200}))
    elif t == "rate_limit_event":
        info = d.get("rate_limit_info") or {}
        w = info.get("unifiedWindows") or {}
        windows = [{"id": wid, "label": label, "used": (w.get(wid) or {})["utilization"],
                    "resets_at": _iso((w.get(wid) or {}).get("resetsAt"))}
                   for wid, label in _windows() if (w.get(wid) or {}).get("utilization") is not None]
        out.append(StreamEvent("quota", {"status": _QUOTA_STATUS.get(info.get("status")), "windows": windows}))
    elif t == "system" and d.get("subtype") == "post_turn_summary":
        out.append(StreamEvent("turn_end", {"summary": d.get("status_detail", "")}))
    elif t == "result":
        text = str(d.get("result") or "")
        if d.get("is_error") and re.search(r"rate.?limit|usage limit|額度", text, re.I):
            out.append(StreamEvent("error", {"error": "rate_limit", "detail": text[:300]}))
        elif d.get("subtype") == "error_max_turns":
            out.append(StreamEvent("error", {"error": "bad_request", "detail": _("turn limit reached; detaching the lease"),
                                             "_detach": "budget"}))
        out.append(StreamEvent("turn_end", {"cost": d.get("total_cost_usd"), "num_turns": d.get("num_turns"),
                                            "duration_ms": d.get("duration_ms"), "is_error": bool(d.get("is_error"))}))
    return out


def classify_exit(code, stderr_tail: str) -> StreamEvent:
    raw = _("exit {code}: {detail}", code=code, detail=stderr_tail[-200:])
    if is_login_error(stderr_tail):  # 跟 llm.py 的登入判斷同一份(第 6 層)
        return StreamEvent("error", {"error": "agent_login", "detail": _(
            "Claude Code (used by project sessions) is not logged in, or its credentials have expired (the platform's "
            "agent-hub/claude-login sends an alert). {raw}", raw=raw)})
    return StreamEvent("error", {"error": "process_crashed", "detail": raw})


def resumable(sid: str) -> bool:
    """接得回來 = 對話紀錄還在(Claude Code 要跑完第一輪才寫出來,保留期限過了也會被清掉)。"""
    return transcript(sid) is not None


def resume_command(sid: str) -> tuple:
    return (f"~/agent-hub/bin/lease-resume {sid}",
            _("Paste it in a terminal on the hub machine: it resumes the same conversation (Claude Code). When you are "
              'done, go back to the assistant and press "detach" for the handover.'))


def neutral_question(inp: dict) -> dict:
    """AskUserQuestion 的 input → 通用的提問結構 {questions: [{question, options: [{label, description?}], multi}]}。"""
    qs = []
    for q in (inp or {}).get("questions") or []:
        opts = [{"label": o.get("label", ""), **({"description": o["description"]} if o.get("description") else {})}
                for o in q.get("options") or [] if isinstance(o, dict)]
        qs.append({"question": q.get("question", ""), "options": opts, "multi": bool(q.get("multiSelect"))})
    return {"questions": qs}


def native_answers(answers: dict) -> dict:
    """通用的答案 {問題: label 或 [label…]} → AskUserQuestion 要的 {問題: "label"}(多選用逗號接起來)。"""
    return {q: (", ".join(a) if isinstance(a, list) else a) for q, a in (answers or {}).items()}


# ---------- auth(第 6 層,登入)----------
# 三份登入:訂閱登入(keychain,launchd 底下讀得到,SSH 底下讀不到)、headless token(`claude setup-token`,一年,
# 無人值守用的預設)、~/.claude/.credentials.json(SSH 底下互動的 claude 用)。路徑在呼叫時才算(測試換 HOME 就換掉)。
# 不讀、不印任何值,只有 auth_env 把 token 放進要交給 claude 的環境變數。

from runtime_ports import Credential  # noqa: E402

TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"
_LOGIN_ERROR = re.compile(r"login|401|unauthori[sz]ed|oauth|invalid api key|authentication|credential|expired", re.I)


def _token_file() -> pathlib.Path:
    return pathlib.Path.home() / ".claude" / "headless-oauth-token"


def _creds_file() -> pathlib.Path:
    return pathlib.Path.home() / ".claude" / ".credentials.json"


def clean_env(env) -> dict:
    return {k: v for k, v in env.items() if k != TOKEN_VAR}


def auth_env(mode: str, env) -> tuple:
    out = clean_env(env)
    if mode != "token":  # subscription:不帶 token,claude 自己用 keychain 的登入
        return out, ""
    f = _token_file()
    if not f.is_file():
        return None, _("{file} does not exist (the default is the headless token; to use the subscription login, "
                       "declare auth: subscription)", file=f)
    out[TOKEN_VAR] = f.read_text().strip()
    return out, ""


def credentials() -> list:
    token, creds = _token_file(), _creds_file()
    out = [Credential(name=_("subscription login (launchd)"), label=_("claude's subscription login"), mode="subscription",
                      present=True, renew=_("log in on the hub machine (`claude` → /login)")),
           Credential(name="headless token", label="headless token", mode="token", present=token.exists(),
                      renew=_("rerun `claude setup-token` and write it to {path}", path=token), path=str(token),
                      since=datetime.datetime.fromtimestamp(token.stat().st_mtime) if token.exists() else None,
                      max_age=datetime.timedelta(days=335), lifetime=_("one year"))]
    expires, note, present = None, None, creds.exists()
    if present:
        try:
            oauth = json.loads(creds.read_text()).get("claudeAiOauth") or {}
        except ValueError:
            oauth = {}
        exp = oauth.get("refreshTokenExpiresAt")
        if exp:
            expires = datetime.datetime.fromtimestamp(exp / 1000)
        elif oauth:
            note = _(".credentials.json: no refreshTokenExpiresAt, expiry check skipped")
    out.append(Credential(name=_(".credentials.json (for SSH)"), label="~/.claude/.credentials.json", mode=None,
                          present=present, renew=_("run claude once in a terminal on the hub machine so it refreshes, "
                                                   "or /login again"),
                          path=str(creds), expires=expires, warn_before=datetime.timedelta(days=7),
                          used_by=_("used by claude under SSH"), note=note))
    return out


def is_login_error(text: str) -> bool:
    """claude 的登入錯誤(Login expired、/login、401、OAuth…);找不到 claude 本身也要人處理,一起算。"""
    text = text or ""
    # the not-found message comes from another process (llm.py's JSON), maybe in another language: the English msgid,
    # this UI language's text, or the zh-Hant text from before the catalog
    return bool(_LOGIN_ERROR.search(text)) or any(s in text for s in (NOT_FOUND, _(NOT_FOUND), "找不到 claude"))


def relocate(sid: str, old_cwd: str, new_cwd: str) -> bool:
    """資料夾搬了(改名、升級):Claude Code 的對話紀錄照資料夾的 slug 放,`--resume` 只在目前資料夾的那格找——
    把 <sid>.jsonl(跟子 agent 的 <sid>/)搬到新資料夾那格。已經在新的地方 = True;哪裡都找不到 = False。"""
    import shutil
    if not re.fullmatch(r"[0-9a-f-]{36}", sid or ""):
        return False
    old_dir, new_dir = PROJECTS / slug_for_path(str(old_cwd)), PROJECTS / slug_for_path(str(new_cwd))
    if (new_dir / f"{sid}.jsonl").exists():
        return True
    if not (old_dir / f"{sid}.jsonl").exists():
        return False
    new_dir.mkdir(parents=True, exist_ok=True)
    for name in (f"{sid}.jsonl", sid):
        if (old_dir / name).exists() and not (new_dir / name).exists():
            shutil.move(str(old_dir / name), str(new_dir / name))
    return True


# ---------- completion(runtime_ports 的 completion 介面;docs/design/llm-providers.md)----------
# 判斷點的「只回答案」交給 Claude Code 跑:關掉所有工具、不載入任何 MCP、不留 session;prompt 從 stdin 給(argv 有長度上限,
# 「放得下就全放」的 prompt 會超過);在空的暫存目錄跑——在專案副本裡跑會觸發專案自己的 hook(librarian 記過這個坑)。

from runtime_ports import CompletionResult  # noqa: E402


def _exe() -> str:
    try:
        import hubconf
        return hubconf.tool("claude")
    except Exception:
        return "claude"


def complete(req, conf) -> CompletionResult:
    import tempfile
    cmd = [_exe(), "-p", "--tools", "", "--strict-mcp-config", "--no-session-persistence", "--output-format", "json"]
    if req.model:
        cmd += ["--model", req.model]
    if req.system:
        cmd += ["--append-system-prompt", req.system]
    if req.schema is not None:
        cmd += ["--json-schema", json.dumps(req.schema, ensure_ascii=False)]
    env, why = auth_env(conf.get("auth") or "token", os.environ)
    if env is None:
        return CompletionResult(False, stop="error", error=why)
    env["HUB_LLM_POINT"] = req.point   # 叫起的程序(含它的 hook)知道自己是平台的 headless 呼叫
    env = _env(type("T", (), {"env": env})())
    with tempfile.TemporaryDirectory(prefix="hub-completion-") as cwd:
        try:
            r = subprocess.run(cmd, input=req.prompt, cwd=cwd, capture_output=True, text=True, timeout=req.timeout_s, env=env)
        except subprocess.TimeoutExpired:
            return CompletionResult(False, stop="error", error=_("over {seconds} {seconds:second|seconds}", seconds=req.timeout_s))
        except FileNotFoundError:
            return CompletionResult(False, stop="error", error=_(NOT_FOUND))
    try:
        out = json.loads(r.stdout)
    except ValueError:
        return CompletionResult(False, stop="error", error=_("exit {code}: {detail}", code=r.returncode,
                                                                    detail=(r.stderr.strip() or r.stdout.strip())[-300:]))
    usage = out.get("usage") or {}
    tokens_in = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")) or None
    if r.returncode != 0 or out.get("is_error"):
        text = str(out.get("result") or r.stderr.strip())[-300:]
        return CompletionResult(False, stop="error", error=_("exit {code}: {detail}", code=r.returncode, detail=text),
                                input_tokens=tokens_in,
                                output_tokens=usage.get("output_tokens"), cost_usd=out.get("total_cost_usd"))
    so = out.get("structured_output")
    return CompletionResult(True, text=json.dumps(so, ensure_ascii=False) if so is not None else str(out.get("result") or ""),
                            stop="end", input_tokens=tokens_in, output_tokens=usage.get("output_tokens"),
                            cost_usd=out.get("total_cost_usd"))


def health(conf) -> tuple:
    """不打請求(每小時的 claude-login 已經實際打過):claude 在不在、要用的登入在不在。"""
    import shutil
    if not shutil.which(_exe()) and not pathlib.Path(_exe()).is_file():
        return False, _(NOT_FOUND)
    env, why = auth_env(conf.get("auth") or "token", {})
    return (env is not None), why
