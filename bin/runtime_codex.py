"""The runtime adapter (Codex): the only place the platform learns about Codex's hooks, sessions and settings format.
Design: docs/design/runtime-codex.md, Step 2 (a Codex session the owner opens is served by the platform). Standard library
only (tomllib needs Python 3.11, the platform's Python; nothing the system python3 imports loads this module).

Ports (runtime_ports infers them from the functions below):
  lifecycle    parse_event / unusable / inject / deny for Codex's hook input and output. Its hooks are user-level
               ($CODEX_HOME/hooks.json, docs/design/runtime-codex.md D1), installed only by the owner with `brunna
               user-settings --install --runtime codex`, never by the platform: install_lifecycle(ws) is a per-project
               no-op (r2 B1)
  sessions     alive / live_sessions / context_size / current_session. Every thread of the Desktop app and of the
               terminal UIs runs under one shared daemon (M7), so a session's record is keyed by Codex's session id, never by
               its process, and is live from its SessionStart until its SessionEnd or SESSION_STALE_S without a hook (a TUI
               that quits fires no SessionEnd, and the daemon lives for days; r2 B2). session_version: the rollout's
               cli_version (the daemon's)
  control      control_paths / control_root / session_folders / foreign_controls: .codex and .agents, protected in every
               sandbox whether or not Codex is installed (ALWAYS_PROTECTED; Step 4 r2, S1/S3). Its sessions still aren't
               confined: no confine port, and unconfined() says why (B1)
Absent (Unsupported; doctor's feature table names each, with unsupported(port)'s reason): instructions (the rules arrive by
the SessionStart injection, so nothing writes the tracked AGENTS.md), agent, completion, auth, confine, stream,
wake (D4: a Codex thread is never woken until M11), session_status, session_summary, tool_servers, rule_check, run_server,
distribution. So dispatch neither routes to nor forks a Codex session (its candidates need a wake, a fork the default
runtime's resume): a letter reaches a Codex session only through its hooks, then triage on the default runtime.

What hub-event and hubbus read from here besides the ports (each declared, never a runtime's name in their code):
  EVENTS_MAPPED            the platform events its hooks deliver (no open_session_attempt: no such tool)
  TELLS_ON                 where a letter shown by a hook is measured to reach the model: SessionStart only (M2 on 0.165;
                           on 0.169 every event's additionalContext is recorded in the thread as a developer message before
                           the request, but whether the model reads it on UserPromptSubmit and PostToolUse needs the owner's
                           login to measure). The other events record presence and tell nothing
  LETTERS_AS_NOTICE        its injected context is developer-role (it outranks the user): letters are a notice with ids
                           and the `brunna inbox` pointer, never a letter's body
  RULES_AT_START           the platform's rules arrive as SessionStart context (rules_note() adds what is Codex's own)
  PLATFORM_RUNS_FIRE_HOOKS a platform run can't skip the user's hooks (M5 on 0.165): hub-event does nothing under
                           HUB_LLM_POINT
  SESSION_STALE_S          the records are shared-process ones (runtime_ports.record_session stale_s)
  END_BUDGET_S             Codex kills a SessionEnd hook after at most 3 s: a slow handler there (memory sync) is detached

Measured on codex-cli 0.169.0 with scratch CODEX_HOMEs and no login (docs/design/runtime-codex.md, "Measured 11-14"):
SessionStart (source "startup"), UserPromptSubmit and SessionEnd (reason "other") fire under `codex exec` around the failed
model call; the hook's parent is the native codex process, its environment the caller's (CODEX_HOME, HUB_LLM_POINT, a
Claude session's own id variable), with no CODEX_THREAD_ID; the trust key and hash below made the hooks fire, none or a
wrong hash skipped them silently, and a key naming a linked CODEX_HOME by its link didn't count.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re

import i18n

_ = i18n.domain(__file__)
NAME = "codex"   # this runtime's name in runtime_ports.RUNTIMES: the hooks say whose they are
MODELS: dict = {}   # no platform runs on Codex yet (Step 1)
# what its agent takes as rules from a folder it works in (one per directory, git root down to the cwd; the override
# replaces the other): never on a memory branch (runtime_ports.rules_file_names, bin/memory_paths.py)
RULES_FILE_NAMES = ("AGENTS.md", "AGENTS.override.md")

EVENTS_MAPPED = ("session_start", "prompt", "tool_used", "pre_compact", "session_end")
TELLS_ON = ("session_start",)
LETTERS_AS_NOTICE = True
RULES_AT_START = True
PLATFORM_RUNS_FIRE_HOOKS = True
SESSION_STALE_S = 2 * 3600
END_BUDGET_S = 3
USER_LEVEL_HOOKS = True

# (Codex hook event, its key in hooks.state, matcher, platform event); matcher None = every occurrence. Stop isn't mapped:
# it fires at the end of every turn (r2)
HOOK_TABLE = (
    ("SessionStart", "session_start", None, "session_start"),
    ("UserPromptSubmit", "user_prompt_submit", None, "prompt"),
    ("PostToolUse", "post_tool_use", "Bash", "tool_used"),   # exec_command and the shell tools: tool_name "Bash"
    ("PreCompact", "pre_compact", None, "pre_compact"),
    ("SessionEnd", "session_end", None, "session_end"),
)
_HOOK_NAME = {ev: name for name, _key, _m, ev in HOOK_TABLE}
# Codex's defaults the trust hash normalizes in (0.169 discovery.rs normalize_command_hook): SessionEnd 1 s (at most 3),
# every other event 600 s; additionalContext over 2,500 tokens is cut to a preview and the rest spilled to a temp file
# unless the handler sets additionalContextLimit (0: never), which the rules need (about 16 KB)
DEFAULT_TIMEOUT_S, END_TIMEOUT_S, MAX_END_TIMEOUT_S = 600, 1, 3
DEFAULT_CONTEXT_LIMIT = 2500
CONTEXT_EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse")
# a platform-managed handler, wherever the hub was when it was written: it runs hub-event for this runtime
MANAGED = re.compile(r"/bin/hub-event['\"]?\s+--runtime\s+" + NAME + r"\s")


def codex_home() -> pathlib.Path:
    """The user's Codex folder: $CODEX_HOME, else ~/.codex (Codex's own rule)."""
    return pathlib.Path(os.environ.get("CODEX_HOME") or pathlib.Path.home() / ".codex").expanduser()


def credential_paths() -> dict:
    """Its whole home: the login (auth.json), and every project's conversations (runtime_ports.credential_paths). policy.py
    denies ~/.codex itself whichever runtimes are installed; this adds a $CODEX_HOME elsewhere."""
    return {"dirs": [str(codex_home())], "files": []}


def credential_override(env) -> tuple[str, list[str], list[str]] | None:
    """(variable, dirs, files) when env moves Codex's home away from ~/.codex: the platform's jobs (the scheduler's
    environment has no CODEX_HOME) then deny ~/.codex and not that folder, unless {settings} credentials.dirs names it
    (doctor says so)."""
    v = env.get("CODEX_HOME")
    if not v:
        return None
    home = os.path.realpath(os.path.expanduser(v))
    if home == os.path.realpath(pathlib.Path.home() / ".codex"):
        return None
    return "CODEX_HOME", [home], []


def unsupported(port: str) -> str | None:
    """Why it lacks a port (doctor's feature table)."""
    why = {
        "instructions": _("Codex reads no untracked rules file, so the platform's rules arrive by its session-start hook "
                          "instead"),
        "wake": _("a Codex thread runs on in its daemon after its window closes, so it is never woken until the platform "
                  "can tell a window is attached"),
        "agent": _("the platform starts no Codex runs yet"),
        "completion": _("the platform starts no Codex runs yet"),
    }
    return why.get(port)


def note(port: str) -> str | None:
    """What to know about a port it has (doctor, after "available")."""
    if port == "lifecycle":
        return _("Codex sessions get letters at their start only, as a notice to read them with brunna inbox; their hooks "
                 "are installed for your user with brunna user-settings --install --runtime codex")
    return None


def rules_note() -> str:
    """What a Codex session gets after the platform's rules: its sandbox and the mailbox."""
    return _("In Codex: if a `brunna` command (send, inbox, done, ack) fails because this session's sandbox can't write the "
             "mailbox, use the same tools of the brunna MCP server instead; if this session has no such server, tell the "
             "user (brunna mcp config prints what to register). Letters are shown to a Codex session only when it starts: "
             "when you wait for a reply, or before you finish a task, run `brunna inbox` yourself.")


# ---------- control (runtime_ports' control port; docs/design/runtime-codex.md, Step 4 r2, S1/S3) ----------
# What Codex loads settings, hooks, tools and instructions from inside a project folder, measured 11-14: a trusted folder's
# .codex/ (config.toml: the permission profile, the approval policy, MCP servers, inline hooks, `features.hooks = false`,
# which switches off the platform's user-level hooks too; hooks.json; execpolicy rules/), at any depth (a nested
# .codex/config.toml loads for a session started below it), and .agents/skills (loaded as skills). Every sandbox the
# platform renders keeps a sandboxed project's own processes off both, whether or not Codex is installed: a planted one
# would run in the owner's later Codex session (ALWAYS_PROTECTED, runtime_ports.protected_runtimes).
CONTROL = ((".codex", True), (".agents", True))
TOOL_FOLDER = ".codex"
ALWAYS_PROTECTED = True


def control_paths() -> list[tuple[str, bool]]:
    return list(CONTROL)


def control_root() -> tuple[str, tuple[str, ...]]:
    """(the tool's folder in a project, the entries in it that stay writable): none (nothing of the project's makes or
    changes anything under .codex)."""
    return TOOL_FOLDER, ()


def session_folders(workspace) -> list[pathlib.Path]:
    """Where its sessions start: the folder itself (where Codex makes its own worktrees isn't measured; a nested .codex is
    covered by the control names at any depth)."""
    return [pathlib.Path(workspace)]


def foreign_controls(workspace) -> list[str]:
    """What in the folder its sessions would load that the platform didn't put there: the platform writes nothing into
    .codex/ yet, so every entry there (read without following links)."""
    base = pathlib.Path(workspace) / TOOL_FOLDER
    if base.is_symlink():
        return [_("{file} is a link", file=TOOL_FOLDER)]
    try:
        names = sorted(os.listdir(base)) if base.is_dir() else []
    except OSError as e:
        return [_("{file} can't be listed ({why})", file=TOOL_FOLDER, why=e)]
    return [_("{file} would be loaded by its Codex sessions", file=f"{TOOL_FOLDER}/{n}") for n in names]


def unconfined() -> str:
    """Why a Codex session in a sandboxed project isn't confined, whatever is rendered (docs/design/runtime-codex.md,
    r2 B1): Codex runs git itself in the session's folder, outside its tool sandbox, and the repo's filters run from it.
    conformance_codex's session-folder-git check tests it per version."""
    return _("Codex runs git in the session's folder outside its own sandbox, and a filter in the repo's config runs "
             "from it as you (runtime-codex.md, B1)")


# ---------- lifecycle ----------

def _hook_input(raw: str) -> tuple[dict | None, str | None]:
    """(the hook's JSON, None) or (None, why it can't be used); empty input is no session, not a fault."""
    if not (raw or "").strip():
        return None, None
    try:
        data = json.loads(raw)
    except ValueError:
        return None, "not_json"
    if not isinstance(data, dict):
        return None, "not_an_object"
    if not data.get("session_id"):
        return None, "no_session_id"
    if not isinstance(data["session_id"], str):
        return None, "session_id_not_text"
    if not data.get("cwd"):
        return None, "no_cwd"
    if not isinstance(data["cwd"], str):
        return None, "cwd_not_text"
    return data, None


def unusable(event: str, raw: str) -> str | None:
    data, why = _hook_input(raw)
    if why or data is None:
        return why
    return None if event in EVENTS_MAPPED else "not_mapped"


def parse_event(event: str, raw: str):
    from runtime_ports import HubEvent
    data = _hook_input(raw)[0]
    if data is None or event not in EVENTS_MAPPED:
        return None
    if event == "tool_used":
        ti = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
        command = ti.get("command") if data.get("tool_name") == "Bash" else ""
        return HubEvent(event, data["session_id"], data["cwd"], command=command if isinstance(command, str) else "")
    return HubEvent(event, data["session_id"], data["cwd"])


def inject(event: str, text: str) -> str:
    """Always the JSON form: plain stdout that looks like JSON fails a SessionStart hook (0.169 parse_completed)."""
    if not text or event not in ("session_start", "prompt", "tool_used"):
        return ""
    return json.dumps({"hookSpecificOutput": {"hookEventName": _HOOK_NAME[event], "additionalContext": text}},
                      ensure_ascii=False)


def deny(event: str, reason: str) -> str:
    return ""   # no event of it is one the platform blocks (open_session_attempt isn't mapped)


def install_lifecycle(workspace, check: bool = False) -> bool:
    """Nothing per project (r2 B1): hub-event install runs at every session start and render-project-settings every 15
    minutes, and a user-level change waits for the owner (`brunna user-settings --install --runtime codex`)."""
    return False


def remove_lifecycle(workspace, check: bool = False) -> bool:
    return False   # none per project; the user-level ones go with user_cleanup


# ---------- sessions ----------

# the executable itself (argv[0], a path with no spaces): an argument naming codex is no Codex process
_SESSION_PROCESS = re.compile(r"^(?:\S*/)?codex(?:\s|$)")
# `codex [options] exec|e …`: an option's value may follow it (`-m o3`, `-c k=v`); the first word that isn't one is the
# subcommand
_HEADLESS = re.compile(r"^(?:\S*/)?codex(?:\s+-{1,2}[^\s=]+(?:=\S*|\s+(?!-)(?!(?:exec|e)(?:\s|$))\S+)?)*\s+(?:exec|e)(?:\s|$)")
_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def session_process(command: str) -> bool:
    """Whether a process with this command line is Codex's (runtime_ports.current_session's ancestor walk; session_pid)."""
    return bool(_SESSION_PROCESS.search(command or ""))


def current_session(env) -> str | None:
    """Codex puts the thread's id in every tool shell's environment (M6; 0.169 exec_env.rs). With a Claude id in the same
    environment (the daemon's can carry one, M6/M7) the nearest ancestor decides (runtime_ports.current_session), and a
    Codex process is nearer than any session that started it."""
    sid = env.get("CODEX_THREAD_ID")
    return sid if isinstance(sid, str) and sid else None


def session_pid(env, ancestors=None) -> int | None:
    """The process the session runs in, as its hook sees it: the nearest ancestor that is Codex's (the hook's parent was the
    native codex process, measured on 0.169; under the Desktop app or a TUI, the shared daemon, M7). The environment
    doesn't name it."""
    import runtime_ports
    for pid, cmd in (ancestors if ancestors is not None else runtime_ports.ancestors()):
        if session_process(cmd):
            return pid
    return None


EXEC_ORIGINATOR = "codex_exec"   # session_meta.originator of a `codex exec` run (M4; sessions_codex.NOT_A_PERSON)


def _session_meta(raw: str | None) -> dict | None:
    """The session_meta payload of the rollout the hook input's transcript_path names, only when that file is a rollout
    in this user's Codex home (sessions/ or archived_sessions/, resolved; every sandbox denies the home): a file elsewhere
    is a transcript anyone may have written. None when it can't be read."""
    try:
        path = (json.loads(raw or "{}") or {}).get("transcript_path")
        if not isinstance(path, str) or not re.fullmatch(r"rollout-[^/]*\.jsonl", os.path.basename(path)):
            return None
        real, home = pathlib.Path(os.path.realpath(path)), pathlib.Path(os.path.realpath(codex_home()))
        if not any(real.is_relative_to(home / d) for d in ("sessions", "archived_sessions")):
            return None
        with open(real, errors="replace") as fh:
            meta = (json.loads(fh.readline(1 << 20) or "null") or {}).get("payload") or {}
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    return meta if isinstance(meta, dict) else None


_CLI_VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?")


def session_version(pid, raw: str | None = None) -> tuple[str, None] | None:
    """(version, None) of the Codex that runs this session: its rollout's session_meta.cli_version (raw: the hook's
    input). A thread runs in the shared daemon, which updates itself, so the version is the daemon's, not the client's
    (measured 11-14: 0.169.0 threads behind a 0.167.0 TUI); its process (pid) can't tell it."""
    v = (_session_meta(raw) or {}).get("cli_version")
    return (v, None) if isinstance(v, str) and _CLI_VERSION.fullmatch(v) else None


def _originator(raw: str | None) -> str | None:
    """The originator the session's rollout names (its first line, session_meta), from the hook input's transcript_path;
    None when it can't be read. Only a rollout file's first line is read."""
    try:
        path = (json.loads(raw or "{}") or {}).get("transcript_path")
        if not isinstance(path, str) or not re.fullmatch(r"rollout-[^/]*\.jsonl", os.path.basename(path)):
            return None
        with open(path, errors="replace") as fh:
            meta = (json.loads(fh.readline(1 << 20) or "null") or {}).get("payload") or {}
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    o = meta.get("originator") if isinstance(meta, dict) else None
    return o if isinstance(o, str) and o else None


def person_session(pid: int, env, raw: str | None = None) -> bool:
    """A session a person works in: not `codex exec` (a script's or the platform's run, as Claude's -p). What the session's
    rollout says (raw: the hook's input, with its transcript_path) decides; its process's command line when that can't be
    read."""
    o = _originator(raw)
    if o is not None:
        return o != EXEC_ORIGINATOR
    import runtime_ports
    cmd = runtime_ports._parent_and_command(pid)[1]
    return bool(cmd) and not _HEADLESS.search(cmd)


def live_sessions() -> set[str]:
    """The sessions its hooks recorded whose process runs and that had a hook within SESSION_STALE_S (runtime_ports'
    running_records). Raises runtime_ports.SessionsUnknown when that can't be looked up."""
    import runtime_ports
    return runtime_ports.recorded_sessions(NAME)


def alive(sid: str | None) -> bool:
    import runtime_ports
    try:
        return bool(sid) and sid in live_sessions()
    except runtime_ports.SessionsUnknown:
        return bool(sid)   # can't tell: not gone


def rollout(sid: str) -> pathlib.Path | None:
    """The session's rollout file (M4): sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl, or an archived one."""
    if not isinstance(sid, str) or not _ID.match(sid):
        return None
    home = codex_home()
    hits = [f for d in ("sessions", "archived_sessions") if (home / d).is_dir()
            for f in (home / d).rglob(f"rollout-*-{sid}.jsonl")]
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def context_size(sid: str) -> int | None:
    """How much context the session had at its last model call: its last token_count event's last_token_usage
    (input + output). The format is M4's (0.165); 0.169 wrote none without a model call, so it is unmeasured there."""
    f = rollout(sid)
    if not f:
        return None
    try:
        lines = f.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"token_count"' not in line:
            continue
        try:
            p = json.loads(line).get("payload") or {}
        except (ValueError, AttributeError):
            continue
        u = ((p.get("info") or {}).get("last_token_usage") or {}) if p.get("type") == "token_count" else {}
        if isinstance(u.get("input_tokens"), int):
            return int(u["input_tokens"]) + int(u.get("output_tokens") or 0)
    return None


def unrecorded(since_s: int = 3600, now: float | None = None) -> list[str]:
    """Sessions a person ran in the last since_s (their rollout changed) that have no record: their hooks didn't run, being
    absent, untrusted or modified, which Codex skips without a word (M2). Left out: a program's run (`codex exec`), a
    session in no onboarded project or in a sandboxed one (the hooks record neither), and one that ended on record (its
    end leaves <session>.ended, runtime_ports.SESSION_ENDED)."""
    import time
    import hubconf
    now = time.time() if now is None else now
    import datetime
    out, base = [], codex_home() / "sessions"
    days = {datetime.date.fromtimestamp(t) for t in (now, now - since_s)}   # rollouts are filed by their local start day
    found = [f for d in sorted(days) for f in (base / f"{d:%Y/%m/%d}").glob("rollout-*.jsonl")]
    for f in found:
        try:
            if now - f.stat().st_mtime > since_s:
                continue
            with f.open(errors="replace") as fh:
                meta = (json.loads(fh.readline() or "null") or {}).get("payload") or {}
        except (OSError, ValueError, AttributeError):
            continue
        sid = meta.get("session_id") or meta.get("id")
        if not isinstance(sid, str) or not _ID.match(sid) or meta.get("originator") == EXEC_ORIGINATOR:
            continue
        cwd = meta.get("cwd")
        if not isinstance(cwd, str) or not cwd or not _served_folder(cwd):
            continue
        import runtime_ports
        key = runtime_ports.qualify(NAME, sid)
        if not any((p := hubconf.session_file(key, s)) and p.exists() for s in (runtime_ports.SESSION_RECORD,
                                                                                  runtime_ports.SESSION_VERSION,
                                                                                  runtime_ports.SESSION_ENDED)):
            out.append(sid)
    return out


def _served_folder(cwd: str) -> bool:
    """Whether a session in this folder is one the hooks record: in an onboarded project (or the hub), not a sandboxed one."""
    import hubconf
    import project_git
    try:
        if project_git.sandbox_of(cwd):
            return False
        here = pathlib.Path(cwd).resolve()
        return bool(project_git.project_at(cwd)) or here.is_relative_to(hubconf.MAIN.resolve())
    except (OSError, RuntimeError, ValueError):
        return False


# ---------- the user-level hooks and their trust (D1; only on the owner's yes) ----------
# Codex loads $CODEX_HOME/hooks.json ({"hooks": {<Event>: [{"matcher"?, "hooks": [handler]}]}}, unknown top-level keys
# refused) and runs a handler only when config.toml's [hooks.state."<key>"] trusted_hash is the hash of its definition
# (0.169 hooks/src/engine/discovery.rs, config/src/fingerprint.rs; measured):
#   key   "<the real path of hooks.json>:<event key>:<group index>:<handler index>" (positional: the platform appends its
#         groups after the user's and updates its own in place, so no hook of the user's moves)
#   hash  "sha256:" + hex sha256 of the compact JSON, keys sorted, of {event_name, matcher?, hooks: [handler]}, the handler
#         normalized: type, command, timeout (the default filled in), async, statusMessage?, additionalContextLimit?
#         (left out at the default 2,500)
# The trust is written only for a handler read back from the file that is the platform's own (MANAGED), with the hash
# of what is there, never from what Codex reports.

def hook_command(event: str) -> str:
    """The handler's command: the hub's own Python and hub-event by absolute path (a bare `brunna` can be shadowed on PATH,
    and a script's first line isn't relied on); Codex runs it with `$SHELL -lc`."""
    import hubconf
    return f"{hubconf.sh_path(hubconf.MAIN / 'bin' / 'py')} {hubconf.sh_path(hubconf.MAIN / 'bin' / 'hub-event')} " \
           f"--runtime {NAME} {event}"


def platform_handler(codex_event: str, event: str) -> dict:
    h = {"type": "command", "command": hook_command(event)}
    if codex_event == "SessionEnd":
        h["timeout"] = MAX_END_TIMEOUT_S
    if codex_event in CONTEXT_EVENTS:
        h["additionalContextLimit"] = 0
    return h


def trust_hash(event_key: str, matcher: str | None, handler: dict) -> str:
    """Codex's hash of one handler's definition (above)."""
    if handler.get("type") != "command" or not isinstance(handler.get("command"), str):
        raise ValueError("not a command handler")
    default = END_TIMEOUT_S if event_key == "session_end" else DEFAULT_TIMEOUT_S
    timeout = handler.get("timeout", default)
    if event_key == "session_end":
        timeout = max(1, min(int(timeout), MAX_END_TIMEOUT_S))
    norm = {"type": "command", "command": handler["command"], "timeout": max(1, int(timeout)),
            "async": bool(handler.get("async", False))}
    if handler.get("statusMessage") is not None:
        norm["statusMessage"] = handler["statusMessage"]
    limit = handler.get("additionalContextLimit")
    if limit is not None and limit != DEFAULT_CONTEXT_LIMIT:
        norm["additionalContextLimit"] = limit
    ident = {"event_name": event_key, "hooks": [norm]}
    if matcher is not None:
        ident["matcher"] = matcher
    text = json.dumps(ident, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


class Refused(Exception):
    """A file the platform won't edit (a link, not the shape Codex reads, a form of TOML this writer doesn't know)."""


def _hooks_doc(path: pathlib.Path) -> dict:
    if path.is_symlink():
        raise Refused(_("{file} is a link", file=path))
    if not path.exists():
        return {}
    try:
        doc = json.loads(_read(path))
    except ValueError as e:
        raise Refused(_("{file} isn't JSON ({error})", file=path, error=e)) from e
    if not isinstance(doc, dict) or not isinstance(doc.get("hooks", {}), dict) or set(doc) - {"hooks", "description"}:
        raise Refused(_("{file} isn't in the shape Codex reads", file=path))
    for groups in doc.get("hooks", {}).values():
        if not isinstance(groups, list) or not all(isinstance(g, dict) and isinstance(g.get("hooks", []), list)
                                                   for g in groups):
            raise Refused(_("{file} isn't in the shape Codex reads", file=path))
    return doc


def _ours(handler) -> bool:
    return isinstance(handler, dict) and MANAGED.search(str(handler.get("command", "")) + " ") is not None


def merge_hooks(doc: dict) -> dict:
    """doc with this version's platform handlers: one already there (for any hub location) is rewritten in place, one
    missing is appended as its own group after the user's; the user's own hooks and every other key stay where they are."""
    hooks = doc.setdefault("hooks", {})
    for codex_event, _key, matcher, event in HOOK_TABLE:
        groups = hooks.setdefault(codex_event, [])
        want = platform_handler(codex_event, event)
        found = False
        for g in groups:
            inner = g.get("hooks", [])
            if any(_ours(h) for h in inner) and not all(_ours(h) for h in inner):
                # its matcher is the user's too: rewriting it would change what their hook runs on
                raise Refused(_("a group of {event} in hooks.json holds the platform's hook and one of yours: edit it by "
                                "hand (move yours to a group of its own)", event=codex_event))
            for i, h in enumerate(inner):
                if _ours(h):
                    g["hooks"][i] = dict(want)
                    if matcher is None:
                        g.pop("matcher", None)
                    else:
                        g["matcher"] = matcher
                    found = True
        if not found:
            groups.append({**({"matcher": matcher} if matcher else {}), "hooks": [dict(want)]})
    return doc


def trust_entries(doc: dict, hooks_file: pathlib.Path) -> dict[str, str]:
    """{key: hash} for every platform handler in doc, at its position, hashed from what is there."""
    src = os.path.realpath(hooks_file)
    keys = {name: key for name, key, _m, _e in HOOK_TABLE}
    out = {}
    for codex_event, groups in (doc.get("hooks") or {}).items():
        if codex_event not in keys:
            continue
        for gi, g in enumerate(groups):
            for hi, h in enumerate(g.get("hooks", [])):
                if _ours(h):
                    out[f"{src}:{keys[codex_event]}:{gi}:{hi}"] = trust_hash(keys[codex_event], g.get("matcher"), h)
    return out


def _toml_string(s: str) -> str:
    """A TOML basic string (JSON's escapes are all TOML's)."""
    return json.dumps(s, ensure_ascii=False)


_HEADER = re.compile(r"^\s*\[\s*hooks\s*\.\s*state\s*\.\s*\"")   # a candidate; tomllib reads its key


def _header_key(line: str) -> str | None:
    """The key a [hooks.state."<key>"] header line names, as TOML reads it (escapes included); None for anything else."""
    import tomllib
    if not _HEADER.match(line):
        return None
    try:
        state = tomllib.loads(line + "\n").get("hooks", {}).get("state", {})
    except (tomllib.TOMLDecodeError, AttributeError):
        return None
    return next(iter(state)) if isinstance(state, dict) and len(state) == 1 else None


def _same(a, b) -> bool:
    """Equal TOML values, a NaN equal to itself."""
    if isinstance(a, float) and isinstance(b, float) and a != a and b != b:
        return True
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b
_TABLE = re.compile(r"^\s*\[")
_TRUSTED = re.compile(r"^\s*trusted_hash\s*=")


def set_trust(text: str, entries: dict[str, str]) -> str:
    """config.toml's text with [hooks.state."<key>"] trusted_hash = "<hash>" for each entry: a table that is there gets its
    line replaced or added, a missing one is appended at the end. Everything else is kept byte for byte, and the result is
    checked: it parses, and differs from the text before only by these values. Refused (nothing written) for a form this
    writer doesn't edit (the key set inline or dotted, a trusted_hash split over lines)."""
    try:
        return _set_trust(text, entries)
    except Refused:
        raise
    except Exception as e:   # anything this writer didn't foresee: refused, never a traceback in doctor or upgrade
        raise Refused(_("config.toml can't be edited here ({error})", error=f"{type(e).__name__}: {e}")) from e


def _set_trust(text: str, entries: dict[str, str]) -> str:
    import copy
    import tomllib
    try:
        before = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise Refused(_("config.toml doesn't parse ({error})", error=e)) from e
    nl = "\r\n" if "\r\n" in text else "\n"   # the file's own line ends for what is added
    lines = text.splitlines(keepends=True)
    state = ((before.get("hooks") or {}).get("state") or {}) if isinstance(before.get("hooks"), dict) else {}
    for key, value in entries.items():
        start = None
        for i, line in enumerate(lines):
            if _header_key(line.rstrip("\r\n")) == key:
                start = i
                break
        if start is None:
            if key in state:
                raise Refused(_("config.toml sets the trust of {key} in a form this writer doesn't edit", key=key))
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += nl
            lines += [*([nl] if lines and lines[-1].strip() else []),
                      f"[hooks.state.{_toml_string(key)}]{nl}", f"trusted_hash = {_toml_string(value)}{nl}"]
            continue
        end = next((j for j in range(start + 1, len(lines)) if _TABLE.match(lines[j])), len(lines))
        at = next((j for j in range(start + 1, end) if _TRUSTED.match(lines[j])), None)
        line = f"trusted_hash = {_toml_string(value)}{nl}"
        if at is None:
            lines.insert(start + 1, line)
        else:
            lines[at] = line
    out = "".join(lines)
    try:
        after = tomllib.loads(out)
    except tomllib.TOMLDecodeError as e:
        raise Refused(_("config.toml would not parse after the edit ({error})", error=e)) from e
    expect = copy.deepcopy(before)
    st = expect.setdefault("hooks", {}).setdefault("state", {})
    for key, value in entries.items():
        st.setdefault(key, {})["trusted_hash"] = value
    if not _same(after, expect):
        raise Refused(_("config.toml would change more than the platform's trust entries; nothing was written"))
    return out


def _read(path: pathlib.Path) -> str:
    """The file's text as it is, line ends included (CRLF stays CRLF); "" when there is none."""
    if not path.exists():
        return ""
    with open(path, newline="") as fh:
        return fh.read()


def _write(path: pathlib.Path, text: str) -> None:
    """Written whole or not at all, on disk before it replaces the file, keeping the file's mode (600 for a new one)."""
    mode = path.stat().st_mode & 0o7777 if path.exists() else 0o600
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
        with os.fdopen(fd, "w", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _files() -> tuple[pathlib.Path, pathlib.Path]:
    home = codex_home()
    return home / "hooks.json", home / "config.toml"


def installed_hooks() -> bool:
    """Whether the owner installed the platform's hooks here (any of them, for any hub location)."""
    hooks_file, _cfg = _files()
    try:
        doc = _hooks_doc(hooks_file)
    except Refused:
        return False
    return any(_ours(h) for groups in (doc.get("hooks") or {}).values() for g in groups for h in g.get("hooks", []))


def _plan() -> tuple[str, str, str, str]:
    """(hooks.json before, after, config.toml before, after) for this version's hooks and their trust. Anything unforeseen
    is Refused, never a traceback (doctor, --update and upgrade say it and go on)."""
    try:
        return _plan_files()
    except Refused:
        raise
    except Exception as e:
        raise Refused(_("Codex's settings can't be read here ({error})", error=f"{type(e).__name__}: {e}")) from e


def _plan_files() -> tuple[str, str, str, str]:
    hooks_file, cfg = _files()
    if cfg.is_symlink():
        raise Refused(_("{file} is a link", file=cfg))
    doc = _hooks_doc(hooks_file)
    before_hooks = _read(hooks_file)
    after_doc = merge_hooks(json.loads(json.dumps(doc)))
    nl = "\r\n" if "\r\n" in before_hooks else "\n"
    after_hooks = (json.dumps(after_doc, indent=2, ensure_ascii=False) + "\n").replace("\n", nl)
    if before_hooks and json.loads(before_hooks) == after_doc:
        after_hooks = before_hooks   # nothing of ours changed: the user's formatting stays
    before_cfg = _read(cfg)
    after_cfg = set_trust(before_cfg, trust_entries(after_doc, hooks_file))
    return before_hooks, after_hooks, before_cfg, after_cfg


def _apply(plan) -> bool:
    """Both files written, each only if it still holds what the plan read (someone else's edit in between refuses it).
    hooks.json goes first: if config.toml then fails, the hooks are there untrusted (Codex skips them; doctor says so)."""
    before_hooks, after_hooks, before_cfg, after_cfg = plan
    hooks_file, cfg = _files()
    if _read(hooks_file) != before_hooks or _read(cfg) != before_cfg:
        raise Refused(_("{hooks} or {config} changed while this ran; nothing was written: run it again", hooks=hooks_file,
                        config=cfg))
    changed = False
    if after_hooks != before_hooks:
        hooks_file.parent.mkdir(parents=True, exist_ok=True)
        _write(hooks_file, after_hooks)
        changed = True
    if after_cfg != before_cfg:
        try:
            if _read(cfg) != before_cfg:
                raise Refused(_("{config} changed while this ran", config=cfg))
            _write(cfg, after_cfg)
        except Exception as e:
            if changed:
                raise Refused(_("{hooks} was written, but {config} wasn't ({error}): the hooks are there untrusted, and "
                                "doctor says so until this runs again", hooks=hooks_file, config=cfg, error=e)) from e
            raise
        changed = True
    return changed


def user_install(apply: bool) -> list[str]:
    """`brunna user-settings --install --runtime codex`, run by the owner (AGENTS.md: a user-level change only on the owner's
    own yes): the platform's hooks in $CODEX_HOME/hooks.json and their trust in config.toml. What is (or would be) written.
    Raises Refused for a file it won't edit."""
    plan = _plan()
    if plan[1] == plan[0] and plan[3] == plan[2]:
        return []
    if apply:
        _apply(plan)
    hooks_file, cfg = _files()
    return [_("the platform's Codex hooks in {hooks} and their trust in {config}", hooks=hooks_file, config=cfg)]


def user_refresh(apply: bool) -> list[str]:
    """agenthub user-settings --update (run by upgrade) and doctor: only where the owner installed them, this version's
    hooks (a hub that moved, a hook the set gained) and the trust of each, from what the file then holds. A file it
    won't edit is left alone."""
    if not installed_hooks():
        return []
    try:
        if not apply:   # what is behind is the hooks themselves; trust alone missing is user_missing's to say
            plan = _plan()
            return user_install(False) if plan[1] != plan[0] else []
        return user_install(apply)
    except Refused:
        return []


def user_cleanup(apply: bool) -> list[str]:
    """agenthub uninstall, user level (only on the owner's own yes): the platform's handlers out of hooks.json, a group
    left empty with them, and their trust entries left in config.toml, which then name nothing (a group of the user's after
    one of ours would move, and lose its trust: such a file is left for the owner to edit, and said)."""
    hooks_file, _cfg = _files()
    try:
        doc = _hooks_doc(hooks_file)
    except Refused:
        return []
    hooks = doc.get("hooks") or {}
    changed = False
    for codex_event in list(hooks):
        groups = hooks[codex_event]
        kept = []
        for g in groups:
            inner = [h for h in g.get("hooks", []) if not _ours(h)]
            if len(inner) < len(g.get("hooks", [])):
                changed = True
                if inner:
                    return [_("the platform's Codex hooks in {file}: remove them by hand (they share a group with yours)",
                              file=hooks_file)]
                if any(not any(_ours(h) for h in later.get("hooks", [])) for later in groups[groups.index(g) + 1:]):
                    return [_("the platform's Codex hooks in {file}: remove them by hand (yours come after them)",
                              file=hooks_file)]
                continue
            kept.append(g)
        if kept:
            hooks[codex_event] = kept
        else:
            del hooks[codex_event]
    if not changed:
        return []
    if apply:
        _write(hooks_file, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    return [_("the platform's Codex hooks in {file}", file=hooks_file)]


def user_missing() -> str | None:
    """For doctor: why Codex sessions get nothing from the platform's hooks, or None when they're installed and trusted."""
    if not installed_hooks():
        return _("the platform's Codex hooks aren't installed for your user: Codex sessions get no letters, notes reminders or "
                 "rules from the platform")
    try:
        plan = _plan()
    except Refused as e:
        return _("the platform's Codex hooks can't be checked: {why}", why=e)
    except Exception as e:   # never a traceback in doctor
        return _("the platform's Codex hooks can't be checked: {why}", why=type(e).__name__)
    if plan[3] != plan[2]:
        return _("some of the platform's Codex hooks aren't trusted in config.toml (Codex skips them without a word)")
    return None
