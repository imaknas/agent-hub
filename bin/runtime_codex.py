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
  distribution latest / published / install / version_of / pin_env / MIN_VERSION, executables: the store's copies, npm's
               package installed into a prefix of its own, the native binary returned (Step 1)
  completion   complete / health: the platform's own runs on the store's checked copy, in a run home of their own
               (HOME_CONFIG), tool-free, checked before (prompt-input) and after (the rollout) against the pinned shape
  agent        run / describe: only the judgment points that work in a copy of a memory branch (point_refusal), in a
               folder whose git config the platform owns (folder_refusal: Codex runs git as you there, B1), the shell
               under a per-run permission profile
  auth         auth_env / clean_env / credentials / is_login_error / billing, login_window, login_check: the user's
               login file, linked into each run's home (M9); never `codex login` or `logout` with a run's home
Absent (Unsupported; doctor's feature table names each, with unsupported(port)'s reason): instructions (the rules arrive by
the SessionStart injection, so nothing writes the tracked AGENTS.md), confine (it would make hub-event serve a sandboxed
project's Codex sessions while B1 holds: Step 4), stream, wake (D4: a Codex thread is never woken until M11),
session_status, session_summary, tool_servers, rule_check, run_server. So dispatch neither routes to nor forks a Codex
session (its candidates need a wake, a fork the default runtime's resume): a letter reaches a Codex session only through
its hooks, then triage on the default runtime.

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
# a judgment point's model_group → model (runtime_ports.models). Measured with the owner's login (11-14, 0.169): a ChatGPT
# account's live catalog lists only gpt-6-luna ("fast and affordable"), gpt-5.6-terra ("balanced") and gpt-5.6-luna, and
# the server refuses the bundled catalog's others ("not supported when using Codex with a ChatGPT account"). So strong is
# the best the plan serves, not the frontier model; agent-models checks each against the account's catalog. Each is
# code_mode_only, so its tools are seen before a run; sub-agents go with the run home's `[agents] enabled = false`
MODELS = {"fast": "gpt-6-luna", "standard": "gpt-5.6-terra", "strong": "gpt-5.6-terra"}
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
    """Its whole home: the login (auth.json), and every project's conversations (runtime_ports.credential_paths, asked
    whether or not Codex is installed: ALWAYS_PROTECTED). The default ~/.codex always, and a $CODEX_HOME elsewhere where
    the process computing the policy has it. And the platform's own Codex folder in the store, whole: its run homes hold
    each run's rollout (a project's notes as the prompt, and the answer), last message and logs, and the login link
    (runtime-codex.md, Step 1 section 2; a review found them readable from a sandbox, B1)."""
    dirs = [str(pathlib.Path.home() / ".codex"), str(codex_home())]
    try:
        dirs.append(str(_store()))
    except Exception:   # noqa: BLE001 (no store to name: nothing of the platform's runs to deny)
        pass
    return {"dirs": list(dict.fromkeys(dirs)), "files": []}


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
        "confine": _("Codex runs git as you in a session's folder (B1), so its sessions in sandboxed projects aren't "
                     "served until that is fenced (runtime-codex.md, Step 4)"),
        "stream": _("leases stay on Claude Code"),
        "tool_servers": _("the platform's Codex runs load no tool server at all"),
        "rule_check": _("the platform's Codex runs hold no project's own rules"),
        "run_server": _("the platform's Codex runs get no mail tools"),
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
# Where it loads a project's skills from (the repo scope, measured 11-14 above; docs/design/skills-sync.md)
SKILLS_FOLDER = ".agents"
SKILL_DIRS = (f"{SKILLS_FOLDER}/skills",)


def control_paths() -> list[tuple[str, bool]]:
    return list(CONTROL)


def user_control_paths() -> list[tuple[pathlib.Path, bool]]:
    """What Codex loads for every session of the user's from the home folder, outside its own home (which every sandbox
    denies whole as a credential): ~/.agents (its skills; runtime_ports.user_controls; Step 4 r2, review S1)."""
    return [(pathlib.Path.home() / ".agents", True)]


def control_root() -> tuple[str, tuple[str, ...]]:
    """(the tool's folder in a project, the entries in it that stay writable): none (nothing of the project's makes or
    changes anything under .codex)."""
    return TOOL_FOLDER, ()


def session_folders(workspace) -> list[pathlib.Path]:
    """Where its sessions start: the folder itself (where Codex makes its own worktrees isn't measured; a nested .codex is
    covered by the control names at any depth)."""
    return [pathlib.Path(workspace)]


FOREIGN_SKIP = (".git", "node_modules", ".venv", "venv", "__pycache__")   # not walked into when looking for planted ones
FOREIGN_WALK_MAX = 50000   # folders looked through at most


def foreign_controls(workspace, owned=None) -> list[str]:
    """What in the folder its sessions would load that the platform didn't put there: every .codex and .agents folder at
    any depth (a nested one loads for a session started below it), in every spelling the volume opens as it, the top
    .codex's entries named one by one. The top .agents is looked into: a skill `brunna skills sync` installed and still
    exactly as installed (owned) is the platform's, anything else in it is named. Walked without following links (a
    link of the name is said as one), past FOREIGN_SKIP; planted before the protection came, or by hand
    (render-project-settings reports them as drift; Step 4 r2, review S5)."""
    ws = pathlib.Path(workspace)
    names = {f for n, _t in CONTROL for f in _folds(n)}
    out, seen = [], 0
    for top, dirs, files in os.walk(ws):
        seen += 1
        if seen > FOREIGN_WALK_MAX:
            out.append(_("{folder} has too many folders to look through for planted ones", folder=str(ws)))
            break
        here = pathlib.Path(top)
        for n in sorted(set(dirs + files) & names):
            rel = str((here / n).relative_to(ws))
            if (here / n).is_symlink():
                out.append(_("{file} is a link", file=rel))
            elif rel == TOOL_FOLDER:
                try:
                    out += [_("{file} would be loaded by its Codex sessions", file=f"{rel}/{e}")
                            for e in sorted(os.listdir(here / n))]
                except OSError as e:
                    out.append(_("{file} can't be listed ({why})", file=rel, why=e))
            elif rel == SKILLS_FOLDER and owned:
                out += _skills_folder(ws, owned)
            else:
                out.append(_("{file} would be loaded by its Codex sessions", file=rel))
        dirs[:] = [d for d in dirs if d not in names and d not in FOREIGN_SKIP]
    return out


def _skills_folder(ws: pathlib.Path, owned) -> list[str]:
    """The top .agents, entry by entry: only the skills sync installed (owned) in its skills folder are the platform's;
    every other entry, and a skills folder that is a link or can't be listed, is said."""
    out, top = [], ws / SKILLS_FOLDER
    try:
        entries = sorted(os.listdir(top))
    except OSError as e:
        return [_("{file} can't be listed ({why})", file=SKILLS_FOLDER, why=e)]
    skills = pathlib.PurePosixPath(SKILL_DIRS[0]).name
    for e in entries:
        rel = f"{SKILLS_FOLDER}/{e}"
        if e != skills or (top / e).is_symlink() or not (top / e).is_dir():
            out.append(_("{file} is a link", file=rel) if (top / e).is_symlink() else
                       _("{file} would be loaded by its Codex sessions", file=rel))
            continue
        try:
            names = sorted(os.listdir(top / e))
        except OSError as err:
            out.append(_("{file} can't be listed ({why})", file=rel, why=err))
            continue
        out += [_("{file} would be loaded by its Codex sessions", file=f"{rel}/{x}") for x in names
                if not owned(f"{rel}/{x}")]
    return out


def _folds(name: str) -> list[str]:
    """name and every spelling the volume opens as it (the long s, U+017F, folds to s; as runtime_claude._folds)."""
    out = [""]
    for ch in name:
        out = [x + c for x in out for c in ((ch, "ſ") if ch in "sS" else (ch,))]
    return out


def profile_control_entries(workspace) -> dict[str, str]:
    """The permission-profile entries that keep a session's tools off every control path at any depth (measured 11-14:
    a `read` entry can't be a glob, and `deny` globs on <ws>/**/<name> and <ws>/**/<name>/** refused making one by mkdir,
    cp, mv and a symlink, the top one included). What conformance_codex's control-globs check holds per version, and
    what the renderer of Step 4 r2 steps 3-5 writes."""
    ws = os.path.realpath(workspace)
    return {f"{ws}/**/{n}{tail}": "deny" for n, _tree in CONTROL for tail in ("", "/**")}


# ---------- the binary: the one under check, else the store's checked copy (Step 1; distribution below) ----------
_CHECKED: str | None = None


def check_binary(path: str | None) -> None:
    """For bin/conformance.py's child alone: this process's runs use the binary under check (in process, never from the
    environment: nothing a project hands a run chooses which binary runs)."""
    global _CHECKED
    _CHECKED = str(path) if path else None


def _store() -> pathlib.Path:
    import hubconf
    return hubconf.runtimes_dir() / NAME


NO_COPY = ("no checked copy of Codex yet: the nightly brunna/update-runtimes makes one once Codex runs the platform's "
           "work (a judgment point pinned to it, a provider with impl codex, or runtime.default), after the checks that "
           "need your login have run")


def checked_copy() -> tuple:
    """(the store's current copy, None) once this process holds it and it is still what was checked, or (None, why not).
    In a conformance check the copy under check counts: (None, None). Nothing falls back to the codex on PATH."""
    if _CHECKED:
        return None, None
    import runtime_store
    try:
        e, why = runtime_store.held(NAME)
    except (OSError, ValueError) as err:
        return None, _("the store of checked copies of Codex can't be read: {error}", error=getattr(err, "strerror", None) or err)
    if e is None:
        return None, _(NO_COPY)
    return (None, why) if why else (e, None)


def _run_copy() -> tuple:
    """(the executable, its store entry) for one run, resolved once, held and verified; in a conformance check (the binary
    under check, None). Raises NoCheckedCopy."""
    if _CHECKED:
        return _CHECKED, None
    import runtime_ports
    e, why = checked_copy()
    if e is None:
        raise runtime_ports.NoCheckedCopy(why)
    return str(e.exe), e


def binary() -> str:
    """The codex a platform run (or a check) starts: the one under check in a conformance child, else the store's current
    copy, held and verified. Never one on PATH: with no checked copy this raises NoCheckedCopy."""
    return _run_copy()[0]


_VERSION_LINE = re.compile(r"^codex-cli (\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)\s*$")


def version_of(exe, env: dict | None = None) -> str | None:
    """The version this codex says it is (`codex --version`: "codex-cli X.Y.Z"), or None. env: the environment to run it
    in (a throwaway world's)."""
    import subprocess
    try:
        r = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
                           env=dict(os.environ if env is None else env))
    except (OSError, subprocess.SubprocessError):
        return None
    import runtime_ports
    m = _VERSION_LINE.match(r.stdout.strip())
    # the platform's one version grammar (runtime_ports.checkable_version): a pre-release isn't one it records
    return m.group(1) if r.returncode == 0 and m and runtime_ports.checkable_version(m.group(1)) else None


FOLDER_GIT_CHECK = "session-folder-git"   # conformance_codex's check of B1


def unconfined() -> str:
    """Why a Codex session in a sandboxed project isn't confined, whatever is rendered (docs/design/runtime-codex.md,
    r2 B1): Codex runs git itself in the session's folder, outside its tool sandbox, and the repo's filters run from it.
    From the newest recorded probe (conformance_codex's session-folder-git, per version) once there is one (review N4);
    until then, what was measured by hand."""
    import hubconf
    newest = None
    folder = hubconf.STATE / "runtime-conformance" / NAME
    for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            doc = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and str(doc.get("at", "")) > str((newest or {}).get("at", "")):
            newest = doc
    state = (((newest or {}).get("checks") or {}).get(FOLDER_GIT_CHECK) or {}).get("state")
    if state == "fail":
        return _("Codex {version} runs git in the session's folder outside its own sandbox, and a filter in the repo's "
                 "config ran from it as you (the {check} check; runtime-codex.md, B1)", version=newest.get("version"),
                 check=FOLDER_GIT_CHECK)
    if state == "pass":
        return _("Codex {version}'s own git ran none of the folder's filters (the {check} check), but its sessions get "
                 "no profile from the platform yet (runtime-codex.md, Step 4 steps 3-5)", version=newest.get("version"),
                 check=FOLDER_GIT_CHECK)
    return _("Codex runs git in the session's folder outside its own sandbox, and a filter in the repo's config runs "
             "from it as you (runtime-codex.md, B1; measured on 0.167, no probe recorded here yet)")


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


META_LINE_MAX = 16 << 20   # a session_meta line carries the base instructions; longer than this isn't read


def _session_meta(raw: str | None) -> dict | None:
    """The session_meta payload of the rollout the hook input's transcript_path names, only when:
    - that file is a rollout in this user's Codex home (sessions/ or archived_sessions/, both sides resolved, so a
      linked sessions folder works and `sessions/../..` doesn't; every sandbox denies the home), a file elsewhere being a
      transcript anyone may have written;
    - its session id is the hook's (session_id).
    None when it can't be read."""
    try:
        hook = json.loads(raw or "{}") or {}
        path, sid = hook.get("transcript_path"), hook.get("session_id")
        if not isinstance(path, str) or not re.fullmatch(r"rollout-[^/]*\.jsonl", os.path.basename(path)):
            return None
        real, home = pathlib.Path(os.path.realpath(path)), codex_home()
        if not any(real.is_relative_to(os.path.realpath(home / d)) for d in ("sessions", "archived_sessions")):
            return None
        with open(real, errors="replace") as fh:
            line = fh.readline(META_LINE_MAX)
        meta = (json.loads(line or "null") or {}).get("payload") or {}
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    if not isinstance(meta, dict) or not isinstance(sid, str) or sid not in (meta.get("session_id"), meta.get("id")):
        return None
    return meta


DAEMON_RELEASE = re.compile(r"(\d+\.\d+\.\d+)-[^/]+")   # packages/<pkg>/releases/<version>-<target>/
PACKAGE = "@openai/codex"


def version_at(exe: pathlib.Path) -> tuple:
    """(version, build) from the real path of the codex a session's process runs, read as files, never by running it:
    - the app-server daemon's copy, <codex home>/packages/<pkg>/releases/<version>-<target>/…: the folder's name (the
      daemon updates itself; measured 11-14);
    - nvm's global copy, …/lib/node_modules/@openai/codex/…: that package's package.json.
    Anything else is (UNREADABLE, None)."""
    import hubconf
    import runtime_ports
    no = (runtime_ports.UNREADABLE, None)
    try:
        store = pathlib.Path(os.path.realpath(_store()))
        if store in exe.parents:   # a copy of the store's: <runtimes>/codex/<version>/…
            v = exe.relative_to(store).parts[0]
            return (v, None) if runtime_ports.checkable_version(v) and len(exe.relative_to(store).parts) > 1 else no
        home = pathlib.Path(os.path.realpath(codex_home()))
        if (home / "packages") in exe.parents:
            parts = exe.relative_to(home / "packages").parts
            if len(parts) > 3 and parts[1] == "releases" and (m := DAEMON_RELEASE.fullmatch(parts[2])) \
                    and runtime_ports.checkable_version(m[1]):
                return m[1], None
            return no
        versions = pathlib.Path(os.path.realpath(hubconf.nvm_dir() / "versions" / "node"))
        if versions in exe.parents:
            parts = exe.relative_to(versions).parts
            tail = ("lib", "node_modules", *PACKAGE.split("/"))
            if len(parts) > 1 + len(tail) and parts[1:1 + len(tail)] == tail:
                pkg = json.loads(versions.joinpath(parts[0], *tail, "package.json").read_text())
                v = pkg.get("version") if isinstance(pkg, dict) and pkg.get("name") == PACKAGE else None
                return (v, None) if runtime_ports.checkable_version(v) else no
    except (OSError, ValueError):
        return no
    return no


ROLLOUT_BUILD = "rollout"   # the build field of a version read from the rollout, not from the running process


def session_version(pid, raw: str | None = None) -> tuple | None:
    """(version, build) of the Codex that runs this session: the running process's (session_pid: the daemon under the
    Desktop app and the TUIs, the native codex under exec; version_at), which a daemon that updated itself under a
    resumed thread changes (review B1). When that can't be read, the rollout's session_meta.cli_version (raw: the hook's
    input), marked build "rollout": the version that created the thread, which a resumed one keeps."""
    import runtime_ports
    real = runtime_ports.process_path(pid)
    got = version_at(pathlib.Path(real)) if real else (runtime_ports.UNREADABLE, None)
    if runtime_ports.checkable_version(got[0]):
        return got
    v = (_session_meta(raw) or {}).get("cli_version")
    return (v, ROLLOUT_BUILD) if runtime_ports.checkable_version(v) else got


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


# ====================== the platform's own runs (docs/design/runtime-codex.md, Step 1 and its r2) ======================
# Measured on 0.169.0 and 0.167.0 with no login ("Measured 11-14 for Step 1"); what needs a model call is the plan's login
# list. Every platform run of Codex: the store's checked copy (the native binary, never npm's node shim), a fresh Codex home
# of the run's own (HOME_CONFIG, the login a link to the user's file, nothing else), a HOME of the run's own (no rc file,
# skill or git config of the user's), a permission profile on the command line, `codex debug prompt-input` before the run
# and the rollout after it, each compared with the pinned shape; a folder whose git config the platform owns (B1).

import signal  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

# ---------- distribution (runtime_ports' distribution port; runtime-versions.md section 1) ----------

# The oldest version the platform's runs are known to work on: the measurements of Step 1 (and its login list) are on it
MIN_VERSION = "0.169.0"
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
NPM_SINK = ".npm-sink"
# (sys.platform, machine) → (the platform package under @openai/, its target triple): the wrapper's optionalDependencies
TARGETS = {("darwin", "arm64"): ("codex-darwin-arm64", "aarch64-apple-darwin"),
           ("darwin", "x86_64"): ("codex-darwin-x64", "x86_64-apple-darwin"),
           ("linux", "x86_64"): ("codex-linux-x64", "x86_64-unknown-linux-musl"),
           ("linux", "aarch64"): ("codex-linux-arm64", "aarch64-unknown-linux-musl")}
# what a copy's runs execute besides the binary, relative to its vendor/<triple>/ folder
OTHER_EXECUTABLES = ("bin/codex-code-mode-host", "codex-path/rg")
LOGIN_VARS = ("OPENAI_API_KEY", "CODEX_API_KEY")   # an API key: billed per use, ahead of the ChatGPT login


def _target() -> tuple[str, str] | None:
    import platform
    return TARGETS.get((sys.platform, platform.machine()))


def _npm() -> str:
    import hubconf
    d = hubconf.nvm_bin()
    return str(d / "npm") if d else "npm"


def _npm_env(home=None) -> dict:
    """npm's environment, built from nothing of the caller's: nvm's node on PATH, the store's cache, the language, and a
    HOME (home: the install's own folder, where the candidate's code may run; else the user's, for npm view and the
    user's own copy)."""
    import hubconf
    env = {"PATH": hubconf.LAUNCHD_PATH, "npm_config_cache": str(hubconf.runtimes_dir() / ".npm-cache"),
           "HOME": str(home if home is not None else pathlib.Path.home())}
    for k in ("LANG", "LC_ALL", "USER", "LOGNAME", "TMPDIR"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def install_blocker() -> str | None:
    """Why the store's copies of Codex can't be installed here, or None."""
    import hubconf
    if _target() is None:
        import platform
        return _("Codex publishes no build for this machine ({system} {machine})", system=sys.platform,
                 machine=platform.machine())
    nb = hubconf.nvm_bin()
    if nb and (nb / "npm").exists():
        return None
    return _("no npm from nvm's node on this machine: the platform installs its own copy of Codex with it")


def latest() -> str:
    """The newest version npm publishes. RuntimeError with the reason when it can't be told."""
    try:
        r = subprocess.run([_npm(), "view", PACKAGE, "version"], capture_output=True, text=True, timeout=120,
                           env=_npm_env(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(_("npm could not be asked for the latest version: {error}", error=type(e).__name__)) from e
    got = r.stdout.strip()
    if r.returncode != 0 or not _SEMVER.match(got):
        raise RuntimeError(_("npm could not say the latest version (exit {code})", code=r.returncode))
    return got


def published() -> list[str]:
    """Every X.Y.Z version npm publishes (the package also lists each platform build, 0.169.0-darwin-arm64, and alphas)."""
    try:
        r = subprocess.run([_npm(), "view", PACKAGE, "versions", "--json"], capture_output=True, text=True, timeout=120,
                           env=_npm_env(), stdin=subprocess.DEVNULL)
        got = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        raise RuntimeError(_("npm could not list the published versions: {error}", error=type(e).__name__)) from e
    if not isinstance(got, list):
        raise RuntimeError(_("npm could not list the published versions (exit {code})", code=r.returncode))
    return [v for v in got if isinstance(v, str) and _SEMVER.match(v)]


def vendor_folder(folder) -> pathlib.Path | None:
    """<folder>/node_modules/@openai/<platform package>/vendor/<triple>/ of an npm prefix, or None (no build for here)."""
    t = _target()
    return pathlib.Path(folder) / "node_modules" / "@openai" / t[0] / "vendor" / t[1] if t else None


def copy_problems(folder, version: str) -> list[str]:
    """Why an installed copy in this npm prefix isn't the one asked for: a resolved package with no integrity hash in the
    lockfile (the wrapper and the platform builds, optional ones included), or a platform package whose codex-package.json
    doesn't name this version and this machine's target. [] when it is."""
    folder = pathlib.Path(folder)
    try:
        lock = json.loads((folder / "package-lock.json").read_text())
    except (OSError, ValueError):
        return [_("the install left no readable package-lock.json")]
    packages = {k: v for k, v in (lock.get("packages") or {}).items() if k and isinstance(v, dict)}
    unhashed = sorted(k for k, v in packages.items() if v.get("resolved") and not v.get("integrity"))
    if not packages or unhashed:
        return [_("the install resolved packages with no integrity hash: {names}",
                  names=", ".join(unhashed) or _("(none resolved)"))]
    vendor = vendor_folder(folder)
    try:
        meta = json.loads((vendor / "codex-package.json").read_text()) if vendor else None
    except (OSError, ValueError):
        meta = None
    if not isinstance(meta, dict) or meta.get("version") != version or meta.get("target") != (_target() or ("", ""))[1] \
            or not isinstance(meta.get("entrypoint"), str):
        return [_("the platform build in {folder} isn't Codex {version} for this machine", folder=vendor or folder,
                  version=version)]
    exe = vendor / meta["entrypoint"]
    if vendor.resolve() not in exe.resolve().parents or not exe.is_file():
        return [_("the platform build's executable isn't a file inside it")]
    return []


def install(version: str, folder) -> pathlib.Path:
    """Install exactly this version from npm into folder (an npm prefix of its own) and return its native binary, never
    npm's node shim (.bin/codex), whose digest would say nothing. npm checks each tarball against the registry's integrity
    hash; copy_problems refuses an install whose lockfile lacks one. Raises RuntimeError."""
    folder = pathlib.Path(folder)
    if not _SEMVER.match(version or ""):
        raise RuntimeError(_("not a version: {version!r}", version=version))
    if (why := install_blocker()):
        raise RuntimeError(why)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run([_npm(), "install", "--prefix", str(folder), f"{PACKAGE}@{version}", "--no-audit", "--no-fund",
                            "--save-exact", "--ignore-scripts"], capture_output=True, text=True, timeout=900, env=_npm_env(folder),
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(_("npm install did not run: {error}", error=type(e).__name__)) from e
    if r.returncode != 0:
        raise RuntimeError(_("npm install failed (exit {code})", code=r.returncode))
    if (bad := copy_problems(folder, version)):
        raise RuntimeError(bad[0])
    vendor = vendor_folder(folder)
    exe = vendor / json.loads((vendor / "codex-package.json").read_text())["entrypoint"]
    # asking its version runs the candidate's code: a home of its own, nothing of the user's
    home = folder / ".version-home"
    home.mkdir(exist_ok=True)
    try:
        said = version_of(exe, env={"HOME": str(home), "CODEX_HOME": str(home), "PATH": "/usr/bin:/bin",
                                    "LANG": os.environ.get("LANG", "C")})
    finally:
        import shutil
        shutil.rmtree(home, ignore_errors=True)
    if said != version:
        raise RuntimeError(_("the installed codex does not say version {version}", version=version))
    return exe


def executables(exe) -> list[pathlib.Path]:
    """Every executable a run of this copy starts: the binary, its code-mode host and its rg (runtime_store records and
    verifies each)."""
    vendor = pathlib.Path(exe).parent.parent
    return [pathlib.Path(exe), *(p for p in (vendor / rel for rel in OTHER_EXECUTABLES) if p.is_file())]


def pin_env(folder) -> dict:
    """What keeps a run of this copy from updating itself: npm's global prefix at a sink of the store's own (an updater
    that ignored the home's settings writes nowhere that runs). `codex exec` has no update path (measured with a newer
    version published); the home's config (HOME_CONFIG) turns the updater off too."""
    return {"npm_config_prefix": str(pathlib.Path(folder).parent / NPM_SINK)}


def user_copy() -> pathlib.Path | None:
    """The user's own codex, nvm's global one (runtime-versions.md section 8), or None."""
    import hubconf
    d = hubconf.nvm_bin()
    return d / "codex" if d and (d / "codex").exists() else None


def install_global(version: str) -> None:
    """Raise the user's own copy (nvm's global one) to this version (D12). Raises RuntimeError."""
    import tempfile
    if not _SEMVER.match(version or ""):
        raise RuntimeError(_("not a version: {version!r}", version=version))
    try:
        r = subprocess.run([_npm(), "install", "-g", f"{PACKAGE}@{version}", "--no-audit", "--no-fund", "--ignore-scripts"],
                           capture_output=True, text=True, timeout=900, env=_npm_env(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(_("npm install did not run: {error}", error=type(e).__name__)) from e
    if r.returncode != 0:
        raise RuntimeError(_("npm install failed (exit {code})", code=r.returncode))
    exe = user_copy()
    import hubconf
    with tempfile.TemporaryDirectory(dir=hubconf.private_tmp()) as home:   # never the user's Codex home
        said = version_of(exe, env={"HOME": home, "CODEX_HOME": home, "PATH": hubconf.LAUNCHD_PATH}) if exe else None
    if said != version:
        raise RuntimeError(_("the installed codex does not say version {version}", version=version))


# ---------- the run's Codex home (Step 1 r2, B1 and section 2) ----------

# Every key is measured to load under --strict-config on 0.167.0 and 0.169.0. Each widening a run needs is a -c of its
# own (run_args), so the file is the tightest form. Never [hooks], hooks.json, [projects], mcp_servers or a profile.
HOME_CONFIG = """# The platform's Codex home for one run (written by brunna for that run, removed after it)
check_for_update_on_startup = false
cli_auth_credentials_store = "file"
web_search = "disabled"
default_permissions = ":read-only"
approval_policy = "never"
project_doc_max_bytes = 0

[features]
shell_tool = false
unified_exec = false
apps = false
plugins = false
goals = false
multi_agent_v2 = false
daemon_auto_start = false
in_app_updates = false
view_image = false
sleep_tool = false
memories = false
hooks = false
shell_snapshot = false

[agents]
enabled = false

[tools.experimental_request_user_input]
enabled = false

[skills]
include_instructions = false

[analytics]
enabled = false
"""
RUNS = "runs"
RUN_HOME_MAX_AGE_S = 86400
GRACE_S = 10   # SIGTERM, then this long before SIGKILL: a run killed mid-refresh may still write the new login (r2 S5)
TEMP_ROOTS = ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")
PROFILE = "brunna-run"


def user_auth_file() -> pathlib.Path:
    """The user's Codex login file ($CODEX_HOME or ~/.codex), resolved: what a run's home links to (M9)."""
    return pathlib.Path(os.path.realpath(codex_home() / "auth.json"))


def run_home() -> pathlib.Path:
    """A fresh Codex home for one run, <runtimes>/codex/runs/<random>/ (mode 700): HOME_CONFIG, auth.json a link to the
    user's login file when there is one, and home/, the run's HOME. Homes a crash left are removed first. The caller
    removes it (end_run_home)."""
    import secrets
    import shutil
    import time
    base = _store() / RUNS
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    now = time.time()
    for old in base.iterdir():
        try:
            if old.is_dir() and not old.is_symlink() and now - old.lstat().st_mtime > RUN_HOME_MAX_AGE_S:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass
    d = base / secrets.token_hex(8)
    d.mkdir(mode=0o700)
    (d / "config.toml").write_text(HOME_CONFIG)
    (d / "home").mkdir(mode=0o700)
    auth = user_auth_file()
    if auth.is_file():
        (d / "auth.json").symlink_to(auth)
    return d


def end_run_home(home) -> None:
    import shutil
    if home and pathlib.Path(home).parent == _store() / RUNS:
        shutil.rmtree(home, ignore_errors=True)


def run_env(home, base: dict | None = None) -> dict:
    """A run's environment: minimal, never the caller's whole one (M6: it reaches every tool shell): the run's own HOME and
    Codex home, a private TMPDIR, the platform's markers (HUB_LLM_POINT, the confinement marker), the language, and the
    system's PATH (the native binary needs no node)."""
    base = os.environ if base is None else base
    keep = {k: v for k, v in base.items() if k in ("LANG", "LC_ALL", "LC_CTYPE", "USER", "LOGNAME", "HUB_LLM_POINT")
            or k.endswith("_CONFINED")}
    tmp = pathlib.Path(home) / "tmp-run"
    tmp.mkdir(mode=0o700, exist_ok=True)
    keep.update(HOME=str(pathlib.Path(home) / "home"), CODEX_HOME=str(home), TMPDIR=str(tmp), PATH="/usr/bin:/bin")
    keep["npm_config_prefix"] = str(_store() / NPM_SINK)   # pin_env's sink
    return keep


def _toml(s: str) -> str:
    return json.dumps(str(s), ensure_ascii=False)   # a TOML basic string: JSON's escapes are TOML's


# Folders denied whole whatever lives in them, the run's own folder and its version's vendor folder read back (r2 review
# S5): where the user's other files, other volumes and other installed software are, beyond the home
WIDE_DENIES = ("/Users", "/Volumes", "/opt", "/usr/local", "/home")


def platform_denies() -> list[str]:
    """What every run's profile denies, from the same sources the policy's sandboxes use (seatbelt.read_denies): the user's
    home and the wide folders, the temp roots, the store's Codex folder, every credential place (policy.credentials: the
    settings' credentials, policy.CREDENTIALS, each protected runtime's), every project root, the services, the bare
    repos, the memory copies, the bus, the hub's state and logs, the evals, the instance with its secrets and data, the
    keychains. Raises ValueError when any of it can't be computed: a run is refused then, never run with less."""
    import hubconf
    import policy
    try:
        home = pathlib.Path.home()
        dirs, files = policy.credentials()
        places = [home, *(pathlib.Path(w) for w in WIDE_DENIES), *(pathlib.Path(t) for t in TEMP_ROOTS), _store(),
                  *dirs, *files, *hubconf.project_roots(), hubconf.p("workspace_root"), hubconf.p("services_root"),
                  hubconf.p("git_origin"), hubconf.p("memory_copies", "~/.memory-copies"), hubconf.BUS_DIR,
                  hubconf.STATE, hubconf.LOGS, hubconf.evals_path(), hubconf.secrets_dir(), hubconf.data_dir(),
                  hubconf.instance_home(), home / "Library" / "Keychains"]
    except Exception as e:   # noqa: BLE001 (what fails is said, and the run refused)
        raise ValueError(_("the places a Codex run is denied can't be computed ({error}): not run",
                           error=type(e).__name__)) from None
    out = []
    for p in places:
        if p is None:
            continue
        for s in dict.fromkeys([str(p), os.path.realpath(p)]):   # as given and as it resolves (/tmp, /var: links)
            if os.path.isabs(s) and os.path.exists(s) and s not in out:   # a profile names what is there
                out.append(s)
    return out


def profile_entries(read=(), write=(), exe: str | None = None) -> dict[str, str]:
    """The run's permission profile's filesystem entries (r2 S2, S3, S4, S5): platform_denies(); the folders the run works
    in, and the running version's own vendor folder (its code-mode host and rg) read back; each written path, with deny
    globs on git's own files under it (a .gitattributes made mid-run would be read by Codex's git). A more specific entry
    wins over a deny around it (measured). ValueError when the denies can't be computed."""
    out = {p: "deny" for p in platform_denies()}
    if exe:
        out[os.path.realpath(pathlib.Path(exe).parent.parent)] = "read"
    for p in read:
        out[os.path.realpath(p)] = "read"
    for p in write:
        real = os.path.realpath(p)
        out[real] = "write"
        if os.path.isdir(real):
            out[f"{real}/**/.git*"] = "deny"
    return out


def run_args(model: str | None, entries: dict[str, str], shell: bool = False, system: str | None = None) -> list[str]:
    """The -c list a run and its check before it both get (one function, so they can't differ). Not -m and not
    --strict-config: `debug prompt-input` takes neither (r2 nits)."""
    args = ["-c", 'web_search="disabled"']   # also on the command line: the home's file isn't the only word (S3)
    if model:
        args += ["-c", f"model={_toml(model)}"]
    if system:
        args += ["-c", f"developer_instructions={_toml(system)}"]
    if shell:
        args += ["-c", "features.shell_tool=true", "-c", "features.unified_exec=true"]
    table = ", ".join(f"{_toml(k)} = {_toml(v)}" for k, v in entries.items())
    args += ["-c", f"permissions.{PROFILE}.extends=\":read-only\"", "-c", f"permissions.{PROFILE}.filesystem={{{table}}}",
             "-c", f"permissions.{PROFILE}.network.enabled=false", "-c", f"default_permissions={_toml(PROFILE)}"]
    return args


# ---------- the shape a run sees, before and after (runtime-versions.md section 4, V-M1) ----------

PINNED_NESTED = frozenset({"apply_patch", "list_mcp_resource_templates", "list_mcp_resources", "read_mcp_resource",
                           "clock__curr_time"})   # the clock on GPT-6 models (its catalog's experimental tools)
SHELL_NESTED = frozenset({"exec_command", "write_stdin"})
PINNED_NAMESPACES = {"functions": ["exec", "wait"]}
# developer and user messages that would mean more than the run asked for: deferred tools, skills, sub-agents, AGENTS.md
FORBIDDEN_CONTEXT = ("<tools>", "<skills_instructions>", "<multi_agent_role>", "<multi_agent_mode>", "# AGENTS.md instructions")
_DECLARED = re.compile(r"declare const tools: \{\s*([A-Za-z_][A-Za-z0-9_]*)\(")


def tools_shape(items) -> tuple[dict, set] | None:
    """({namespace: [its tools]}, the nested tools exec declares) of every additional_tools item together (a second one
    later in the run, or a namespace named twice, adds to the first), or None: there is none (a model whose tools aren't
    sent as code mode: they can't be seen before the run, so it is never pinned)."""
    spaces, nested, seen = {}, set(), False
    for it in items:
        if isinstance(it, dict) and it.get("type") == "additional_tools" and isinstance(it.get("tools"), list):
            seen = True
            for ns in it["tools"]:
                if not isinstance(ns, dict):
                    spaces.setdefault("?", []).append(repr(ns)[:40])
                    continue
                subs = [s for s in ns.get("tools") or [] if isinstance(s, dict)]
                name = str(ns.get("name") or ns.get("type"))
                spaces[name] = sorted({*spaces.get(name, []), *(str(s.get("name") or s.get("type")) for s in subs)})
                for s in subs:
                    nested.update(_DECLARED.findall(str(s.get("description") or "")))
    return (spaces, nested) if seen else None


def _texts(items) -> list[str]:
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("type") == "message" and it.get("role") in ("developer", "user"):
            out += [str(c.get("text") or "") for c in it.get("content") or [] if isinstance(c, dict)]
    return out


def shape_problems(items, shell: bool = False) -> list[str]:
    """What in a run's input (prompt-input's list, or the rollout's response items) is more than the pinned shape."""
    got = tools_shape(items)
    if got is None:
        return [_("no tool list to check: the model's tools aren't shown before the run")]
    spaces, nested = got
    out = []
    if spaces != PINNED_NAMESPACES:
        out.append(_("tool namespaces {got}, pinned {want}", got=json.dumps(spaces, sort_keys=True),
                     want=json.dumps(PINNED_NAMESPACES)))
    if (extra := sorted(nested - PINNED_NESTED - (SHELL_NESTED if shell else frozenset()))):
        out.append(_("nested tools beyond the pinned ones: {tools}", tools=", ".join(extra[:8])))
    for t in _texts(items):
        if (hit := next((m for m in FORBIDDEN_CONTEXT if t.lstrip().startswith(m)), None)):
            out.append(_("context the run doesn't ask for: {what}", what=hit))
    return out


def _entries_of(profile) -> set:
    out = set()
    for e in ((profile or {}).get("file_system") or {}).get("entries") or []:
        p = e.get("path") or {}
        key = p.get("path") or p.get("pattern") or ("special:" + str((p.get("value") or {}).get("kind")))
        out.add((str(key), str(e.get("access"))))
    return out


def rollout_problems(path, shell: bool, entries: dict[str, str], home, model: str | None = None) -> list[str]:
    """What the rollout of a run says it was sent beyond the pinned shape: its additional_tools and context, each turn's
    settings (the model asked for, approval never, sub-agents disabled, the network restricted, the run's own profile with
    exactly the rendered entries, beside the root read and Codex's own read of its arg0 folder in the run's home), and
    every world_state's top-level tools."""
    items, turns, worlds = [], [], []
    try:
        with open(path) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                p = e.get("payload") if isinstance(e, dict) else None
                if not isinstance(p, dict):
                    continue
                if e.get("type") == "response_item":
                    items.append(p)
                elif e.get("type") == "turn_context":
                    turns.append(p)
                elif e.get("type") == "world_state":
                    worlds.append(p.get("state") or {})
    except OSError as e:
        return [_("the run's rollout can't be read: {error}", error=e.strerror or e)]
    out = shape_problems(items, shell)
    if not turns:
        out.append(_("the rollout holds no turn settings"))
    want = {(k, v) for k, v in entries.items()} | {("special:root", "read")}
    arg0 = os.path.realpath(pathlib.Path(home) / "tmp" / "arg0")
    for t in turns:
        if model and t.get("model") != model:
            out.append(_("model {got}, asked for {want}", got=t.get("model"), want=model))
        if (t.get("permission_profile") or {}).get("network") != "restricted":
            out.append(_("network {got}", got=(t.get("permission_profile") or {}).get("network")))
        sandbox = t.get("sandbox_policy")
        if isinstance(sandbox, dict) and sandbox.get("network_access") not in (None, False):
            out.append(_("network {got}", got=sandbox.get("network_access")))
        if t.get("approval_policy") != "never":
            out.append(_("approval policy {got}", got=t.get("approval_policy")))
        if t.get("multi_agent_version") != "disabled":
            out.append(_("sub-agents {got}", got=t.get("multi_agent_version")))
        if (t.get("active_permission_profile") or {}).get("id") != PROFILE:
            out.append(_("permission profile {got}", got=(t.get("active_permission_profile") or {}).get("id")))
        got = {g for g in _entries_of(t.get("permission_profile"))
               if not (g[1] == "read" and os.path.realpath(g[0]).startswith(arg0))}
        if got != want:
            out.append(_("permission entries differ from the rendered ones: {extra}",
                         extra=", ".join(f"{a} {p}" for p, a in sorted(got ^ want))[:300]))
    for world in worlds:
        if not set(world.get("top_level_tools") or {}) <= {"functions", "functions.exec", "functions.wait"}:
            out.append(_("top-level tools {got}", got=", ".join(sorted(world.get("top_level_tools") or {}))))
    return list(dict.fromkeys(out))


def _rollout(home) -> pathlib.Path | None:
    hits = sorted((pathlib.Path(home) / "sessions").glob("*/*/*/rollout-*.jsonl"))
    return hits[0] if len(hits) == 1 else None


def _tools_alert(point: str, why: str | None) -> None:
    """A run whose tools were more than pinned is a safety stop the owner hears of, per point (once, reminded every 24
    hours, said again once that point's run is seen clean)."""
    try:
        import alert
        alert.once(f"completion-tools:{NAME}:{point}", [why] if why else [],
                   _("{point}: Codex's runs have only their pinned tools again", point=point), ids=["tools"] if why else [])
    except Exception:
        pass


def _execute(argv, cwd, env, prompt: str, timeout_s: float) -> tuple[int | None, str, str, bool]:
    """(exit code, stdout, stderr, timed out). Its own process group; past the timeout SIGTERM, then SIGKILL after GRACE_S
    (r2 S5: a run killed mid-refresh may still write the new login)."""
    import threading
    try:
        p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, start_new_session=True)
    except OSError as e:
        return None, "", str(e), False
    got = {}

    def feed():
        try:
            p.stdin.write(prompt)
            p.stdin.close()
        except (OSError, ValueError):
            pass
    threading.Thread(target=feed, daemon=True).start()
    reader = threading.Thread(target=lambda: got.update(out=p.stdout.read()), daemon=True)
    errs = threading.Thread(target=lambda: got.update(err=p.stderr.read()), daemon=True)
    reader.start()
    errs.start()
    timed_out = False
    try:
        p.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        for sig, wait in ((signal.SIGTERM, GRACE_S), (signal.SIGKILL, 10)):
            try:
                os.killpg(p.pid, sig)
            except (ProcessLookupError, PermissionError):
                break
            try:
                p.wait(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                continue
    reader.join(timeout=10)
    errs.join(timeout=10)
    return p.returncode, got.get("out") or "", got.get("err") or "", timed_out


def _events(stdout: str) -> list[dict]:
    out = []
    for line in stdout.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def _usage(events) -> tuple[int | None, int | None]:
    for e in reversed(events):
        if e.get("type") == "turn.completed" and isinstance(e.get("usage"), dict):
            u = e["usage"]
            return (u.get("input_tokens") if isinstance(u.get("input_tokens"), int) else None,
                    u.get("output_tokens") if isinstance(u.get("output_tokens"), int) else None)
    return None, None


def _failure(events, stderr: str) -> str:
    for e in reversed(events):
        if e.get("type") == "turn.failed":
            return str((e.get("error") or {}).get("message") or "")[:300]
        if e.get("type") == "error":
            return str(e.get("message") or "")[:300]
    return stderr.strip()[-300:]


def _no_git_above(folder) -> bool:
    real = pathlib.Path(os.path.realpath(folder))
    return not any((p / ".git").exists() for p in real.parents)


def _schema_prompt(prompt: str, schema) -> str:
    """A schema goes into the prompt: whether the server takes the platform's non-strict schemas through --output-schema
    is on the login list (r2 (9)); completion.py and llm.py validate the answer either way."""
    if schema is None:
        return prompt
    return prompt + "\n\n" + SCHEMA_ASK + "\n" + json.dumps(schema, ensure_ascii=False)


SCHEMA_ASK = "Answer with only a JSON value that matches this JSON Schema, and nothing else:"   # to the model, not people


def _one_run(point: str, exe: str, folder, home, args: list, prompt: str, timeout_s: float, shell: bool,
             entries: dict, base_env=None, model: str | None = None) -> dict:
    """prompt-input with args, then the run with them, then the rollout: {"ok", "text", "error", "tokens", "refused"}."""
    env = run_env(home, base_env)
    pre = subprocess.run([exe, "debug", "prompt-input", *args, "x"], cwd=folder, env=env, capture_output=True, text=True,
                         timeout=120, stdin=subprocess.DEVNULL)
    try:
        items = json.loads(pre.stdout) if pre.returncode == 0 else None
    except ValueError:
        items = None
    if not isinstance(items, list):
        return {"ok": False, "error": _("the check before the run couldn't read what Codex would send (exit {code})",
                                        code=pre.returncode)}
    if (why := shape_problems(items, shell)):
        refused = _("the run of {point} would get more than its pinned tools ({what}): not run", point=point,
                    what="; ".join(why)[:300])
        _tools_alert(point, refused)
        return {"ok": False, "error": refused, "refused": True}
    out_file = pathlib.Path(home) / "last-message.txt"
    argv = [exe, "exec", "--strict-config", "--skip-git-repo-check", "--json", *args, "-o", str(out_file), "-"]
    code, stdout, stderr, timed_out = _execute(argv, folder, env, prompt, timeout_s)
    events = _events(stdout)
    tokens = _usage(events)
    rollout = _rollout(home)
    why = rollout_problems(rollout, shell, entries, home, model) if rollout else [_("the run left no single rollout to check")]
    # what the run was sent was more than pinned (whatever became of it), or can't be told of a run that answered: refused
    if (why and rollout) or (why and code == 0):
        refused = _("the run of {point} was sent more than its pinned tools ({what}): its result refused", point=point,
                    what="; ".join(why)[:300])
        _tools_alert(point, refused)
        return {"ok": False, "error": refused, "refused": True, "tokens": tokens}
    if timed_out:
        return {"ok": False, "error": _("over {seconds} {seconds:second|seconds}", seconds=int(timeout_s)), "tokens": tokens}
    if code != 0:
        return {"ok": False, "error": _("exit {code}: {detail}", code=code, detail=_failure(events, stderr)),
                "tokens": tokens}
    _tools_alert(point, None)
    try:
        text = out_file.read_text()
    except OSError:
        text = ""
    thread = next((str(e.get("thread_id")) for e in events if e.get("type") == "thread.started"), None)
    return {"ok": True, "text": text, "tokens": tokens, "thread": thread}


# ---------- completion (runtime_ports' completion port) ----------

def complete(req, conf):
    """A judgment point's answer only, on the store's checked copy, in an empty folder of its own: no tool but code mode's
    exec, whose nested tools have nothing to reach (no MCP server, `apply_patch` held by the read-only profile), every read
    outside the folder denied (r2 S2), the network off."""
    import shutil
    import tempfile
    import hubconf
    import runtime_ports
    from runtime_ports import CompletionResult
    try:
        exe, copy = _run_copy()
    except runtime_ports.Unsupported as e:
        return CompletionResult(False, stop="error", error=str(e))
    version = copy.version if copy else None
    if not user_auth_file().is_file():
        return CompletionResult(False, stop="error", error=_(NOT_LOGGED_IN), runtime_version=version)
    try:
        folder = pathlib.Path(tempfile.mkdtemp(prefix="hub-codex-", dir=hubconf.private_tmp()))
    except OSError as e:
        return CompletionResult(False, stop="error", error=_("could not make a folder for the run: {e}", e=e))
    home = None
    try:
        if not _no_git_above(folder):
            return CompletionResult(False, stop="error", error=_(FOLDER_GIT), runtime_version=version)
        home = run_home()
        entries = profile_entries(read=[folder], exe=exe)
        args = run_args(req.model, entries, system=req.system)
        base = {**os.environ, "HUB_LLM_POINT": req.point}
        got = _one_run(req.point, exe, folder, home, args, _schema_prompt(req.prompt, req.schema), req.timeout_s,
                       False, entries, base, model=req.model)
    except ValueError as e:   # the denies couldn't be computed: refused, said
        return CompletionResult(False, stop="error", error=str(e), runtime_version=version)
    except (OSError, subprocess.SubprocessError) as e:
        return CompletionResult(False, stop="error", error=_("the run could not start: {error}", error=type(e).__name__),
                                runtime_version=version)
    finally:
        end_run_home(home)
        shutil.rmtree(folder, ignore_errors=True)
    tin, tout = got.get("tokens") or (None, None)
    if not got["ok"]:
        return CompletionResult(False, stop="error", error=got["error"], input_tokens=tin, output_tokens=tout,
                                runtime_version=version)
    return CompletionResult(True, text=got["text"], stop="end", input_tokens=tin, output_tokens=tout,
                            runtime_version=version)


def health(conf) -> tuple:
    """No request: whether the checked copy is there and the user's login file is."""
    e, why = checked_copy()
    if e is None and not _CHECKED:
        return False, why
    return (True, "") if user_auth_file().is_file() else (False, _(NOT_LOGGED_IN))


NOT_LOGGED_IN = "Codex isn't logged in for your user (no auth.json in its home): run codex login once in a terminal"
FOLDER_GIT = ("Codex runs git as you in the run's folder (runtime-codex.md, B1), and this folder is inside another git repo: "
              "not run")


# ---------- agent (runtime_ports' agent port): the judgment points that work in a memory copy ----------

FILE_TOOLS = frozenset({"Read", "Write", "Edit", "Glob", "Grep"})
# a declaration's fields, as Codex can honour them; any other field refuses the run (never dropped: step 0's bug)
KNOWN_FIELDS = frozenset({"caller", "shape", "model_group", "purpose", "output", "timeout_s", "on_failure", "max_tokens",
                          "max_attempts", "tools", "allowed_tools", "permission_mode", "extra_args", "restricted",
                          "works_in", "auth"})
MODES = ("dontAsk", "acceptEdits")
ARGS_HELD = ("--strict-mcp-config", "--no-session-persistence")   # what the run's home always does
_EDIT_RULE = re.compile(r"^Edit\(\./([^()*?\[\]{}\s]+?)(/\*\*)?\)$")


def _edit_rule_ok(rule: str) -> bool:
    """An Edit(./<path>) or Edit(./<dir>/**) rule naming something below the folder: never the folder itself (./.), a
    parent, or anything of git's (a .git folder, .gitattributes, .gitmodules: Codex's own git reads them, B1)."""
    m = _EDIT_RULE.match(rule)
    if not m:
        return False
    parts = m[1].split("/")
    return not any(part in ("", ".", "..") or part.lower().startswith(".git") for part in parts)


def point_refusal(point: str, spec: dict) -> str | None:
    """Why this judgment point doesn't run on Codex (doctor, llm_ready, llm.py), or None. One table from the declaration
    to what Codex can hold (docs/design/runtime-codex.md, Step 1 (1d) and its r2)."""
    if spec.get("shape") == "completion":
        return None
    extra = sorted(set(spec) - KNOWN_FIELDS)
    if extra:
        return _("{point} declares {fields}, which a Codex run can't hold: it runs on another runtime", point=point,
                 fields=", ".join(extra))
    if spec.get("works_in") != "memory":
        return _("{point} doesn't work in a copy of a memory branch, and Codex runs git as you in its folder (B1): only "
                 "those points run on Codex", point=point)
    tools = {t for t in str(spec.get("tools") or "").split(",") if t}
    if tools - FILE_TOOLS:
        return _("{point} asks for tools Codex has no counterpart for: {tools}", point=point,
                 tools=", ".join(sorted(tools - FILE_TOOLS)))
    if tools and spec.get("permission_mode") not in MODES:
        return _("{point}'s permission mode {mode} has no counterpart in Codex", point=point,
                 mode=spec.get("permission_mode"))
    rules = list(spec.get("allowed_tools") or [])
    if (bad := [r for r in rules if not _edit_rule_ok(str(r))]):
        return _("{point}'s rules {rules} have no counterpart in Codex's profile", point=point, rules=", ".join(map(str, bad)))
    if rules and not tools & {"Write", "Edit"}:
        return _("{point} has rules for writing but no tool that writes: Codex would give it the shell for nothing",
                 point=point)
    if tools & {"Write", "Edit"} and not rules:
        return _("{point} writes with no rule saying where: Codex's profile would have nothing to hold it to", point=point)
    if (bad := [a for a in spec.get("extra_args") or [] if a not in ARGS_HELD]):
        return _("{point}'s arguments {args} mean nothing to Codex", point=point, args=", ".join(map(str, bad)))
    return None


def _git_config_problems(config: pathlib.Path) -> list[str]:
    """What in a clone's .git/config isn't what the platform's own clone of a memory repo holds: only [core] keys of a
    plain clone, remotes whose url is a bare repo under the platform's git origin folder, and branches (r2 S1). Read as a
    file, never by running git."""
    import hubconf
    origin = hubconf.p("git_origin")
    origin = os.path.realpath(origin) if origin else None
    core = {"repositoryformatversion", "filemode", "bare", "logallrefupdates", "ignorecase", "precomposeunicode", "symlinks"}
    section, out = None, []
    try:
        lines = config.read_text().splitlines()
    except OSError as e:
        return [_("its git config can't be read: {error}", error=e.strerror or e)]
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        m = re.fullmatch(r'\[\s*([A-Za-z0-9.-]+)(?:\s+"((?:[^"\\]|\\.)*)")?\s*\]', line)
        if m:
            section = (m[1].lower(), m[2])
            if section[0] not in ("core", "remote", "branch"):
                out.append(_("[{section}]", section=m[1]))
            continue
        key, _sep, value = line.partition("=")
        key, value = key.strip().lower(), value.strip()
        if section is None:
            out.append(key)
        elif section[0] == "core" and key not in core:
            out.append(f"core.{key}")
        elif section[0] == "remote":
            if key == "url":
                if not origin or not value.endswith(".git") or os.path.dirname(os.path.realpath(value)) != origin:
                    out.append(_("remote {url}", url=value))
            elif key != "fetch":
                out.append(f"remote.{key}")
        elif section[0] == "branch" and key not in ("remote", "merge"):
            out.append(f"branch.{key}")
    return out


def folder_refusal(cwd) -> str | None:
    """Why an agent run of Codex may not start in this folder, or None (r2 S1, S7). Codex runs git as you in the run's
    folder, outside its sandbox (B1), so a run starts only where the platform owns git's whole configuration: a real .git
    folder (never a linked worktree's .git file, a throwaway copy's), no .git above it, a config that is a plain clone of a
    bare memory repo under the git origin folder, and no info/attributes or config.worktree. Read as files only."""
    if not cwd:
        return _("a Codex run needs a folder")
    real = pathlib.Path(os.path.realpath(cwd))
    import hubconf
    roots = [pathlib.Path(os.path.realpath(r)) for r in (hubconf.p("memory_copies", "~/.memory-copies"),
                                                         hubconf._private_tmp_path()) if r]
    if not any(r in real.parents for r in roots):
        return _("{folder} is neither under the memory copies nor in the platform's private temp folder: Codex runs only "
                 "there (B1)", folder=real)
    if not _no_git_above(real):
        return _(FOLDER_GIT)
    git = real / ".git"
    if git.is_file() or git.is_symlink():
        return _("{folder} is a linked worktree: its git config is the project's, and Codex runs git as you there (B1)",
                 folder=real)
    if not git.is_dir():
        return _("{folder} isn't a copy of a memory branch: Codex runs only there (B1)", folder=real)
    bad = [n for n in ("config.worktree", "info/attributes", "commondir", "modules") if (git / n).exists()]
    # a hook git would run (Codex's git sets core.hooksPath=/dev/null, but the platform's own git there doesn't): only
    # the samples a clone makes
    hooks = git / "hooks"
    try:
        bad += [f"hooks/{h.name}" for h in hooks.iterdir() if not h.name.endswith(".sample")] if hooks.is_dir() else []
    except OSError:
        bad.append("hooks")
    if bad:
        return _("{folder}'s .git holds {files}, which the platform's own clone doesn't", folder=real, files=", ".join(bad))
    if (bad := _git_config_problems(git / "config")):
        return _("{folder}'s git config isn't a plain clone of a memory repo ({what}): Codex runs git as you there (B1)",
                 folder=real, what=", ".join(bad[:5]))
    return None


def _writes(task, cwd: pathlib.Path, make: bool) -> list[pathlib.Path]:
    """The paths a run may write, from its Edit(./…) rules, resolved under its folder; make: a folder rule's folder made
    first, since the profile can't make what doesn't exist (r2 S1). ValueError for one that leaves the folder."""
    out = []
    for rule in task.spec.get("allowed_tools") or []:
        m = _EDIT_RULE.match(str(rule))
        p = cwd / m[1]
        if cwd.resolve() not in pathlib.Path(os.path.realpath(p)).parents and os.path.realpath(p) != str(cwd.resolve()):
            raise ValueError(_("{rule} leaves the run's folder", rule=rule))
        if m[2] and make:
            p.mkdir(parents=True, exist_ok=True)
        out.append(p)
    return out


def _agent_plan(task, exe: str, make: bool = True) -> tuple:
    """(cwd, args, entries, shell) of an agent run; ValueError / Unsupported with the reason when it can't run."""
    import runtime_ports
    if (why := point_refusal(task.point, task.spec)):
        raise runtime_ports.Unsupported(why)
    if task.extra:
        raise runtime_ports.Unsupported(_("{point} hands Codex arguments of another runtime", point=task.point))
    if (why := folder_refusal(task.cwd)):
        raise runtime_ports.Unsupported(why)
    cwd = pathlib.Path(os.path.realpath(task.cwd))
    shell = bool({t for t in str(task.spec.get("tools") or "").split(",") if t})
    entries = profile_entries(read=[cwd], write=_writes(task, cwd, make) if shell else [], exe=exe)
    return cwd, run_args(task.model, entries, shell=shell), entries, shell


def describe(task) -> list:
    """How it would run (llm.py --dry-run): the exec argv, never run."""
    import runtime_ports
    try:
        exe = binary()
    except runtime_ports.Unsupported:
        exe = NAME
    _cwd, args, _entries, _shell = _agent_plan(task, exe, make=False)
    return [exe, "exec", "--strict-config", "--skip-git-repo-check", "--json", *args, "-o", "<file>", "-"]


def run(task):
    import runtime_ports
    from runtime_ports import TaskResult
    try:
        exe, copy = _run_copy()
    except runtime_ports.Unsupported as e:
        return TaskResult(False, error=str(e))
    version = copy.version if copy else None
    if not user_auth_file().is_file():
        return TaskResult(False, error=_(NOT_LOGGED_IN), runtime_version=version)
    home = None
    try:
        cwd, args, entries, shell = _agent_plan(task, exe)
        home = run_home()
        got = _one_run(task.point, exe, cwd, home, args, _schema_prompt(task.prompt, task.output_schema), task.timeout_s,
                       shell, entries, task.env, model=task.model)
    except (ValueError, runtime_ports.Unsupported) as e:
        return TaskResult(False, error=str(e), runtime_version=version)
    except (OSError, subprocess.SubprocessError) as e:
        return TaskResult(False, error=_("the run could not start: {error}", error=type(e).__name__),
                          runtime_version=version)
    finally:
        end_run_home(home)
    if not got["ok"]:
        return TaskResult(False, error=got["error"], runtime_version=version)
    structured = None
    if task.output_schema is not None:
        try:
            structured = json.loads(got["text"].strip().removeprefix("```json").removesuffix("```").strip())
        except ValueError:
            structured = None
    return TaskResult(True, text=got["text"], structured_output=structured, session_id=got.get("thread"),
                      stop="end", runtime_version=version)


# ---------- auth (runtime_ports' auth port) ----------

# How often Codex refreshes a ChatGPT login, assumed short of what its source says until the login list confirms it (r2
# (9)): a check that would run past it waits for the next night instead
REFRESH_INTERVAL_S = 7 * 86400
LOGIN_MARGIN_S = 3600
_LOGIN_ERROR = re.compile(r"401|unauthori[sz]ed|not logged in|log ?in|authentication|refresh token|expired", re.I)


def _auth_fields() -> dict:
    """auth_mode and last_refresh of the user's login file, and nothing else of it (no token is kept or printed)."""
    try:
        doc = json.loads(user_auth_file().read_text())
    except (OSError, ValueError):
        return {}
    return {k: doc.get(k) for k in ("auth_mode", "last_refresh")} if isinstance(doc, dict) else {}


def clean_env(env) -> dict:
    return {k: v for k, v in env.items() if k not in LOGIN_VARS and not k.startswith("CODEX_")}


def auth_env(mode: str, env) -> tuple:
    """Codex has one login for both modes: the user's, linked into each run's home (M9). Its absence is said."""
    out = clean_env(env)
    if not user_auth_file().is_file():
        return None, _(NOT_LOGGED_IN)
    return out, ""


def billing(mode: str, env, restricted: bool = False) -> str:
    """metered for an API key (in the environment, or the login file's mode), subscription for a ChatGPT login, else
    unknown. A run's home loads no config but the platform's, so nothing else can name a key or an endpoint."""
    if any((env or {}).get(k) for k in LOGIN_VARS):
        return "metered"
    m = str(_auth_fields().get("auth_mode") or "").lower()
    return "subscription" if m == "chatgpt" else "metered" if m in ("apikey", "api_key") else "unknown"


def credentials() -> list:
    from runtime_ports import Credential
    f = user_auth_file()
    note = None
    store = _user_store()
    if store not in (None, "file"):
        note = _("your Codex config keeps the login in {store}, which the platform's runs can't share (they need the file "
                 "store, M9)", store=store)
    return [Credential(name="codex login", label=_("Codex's login"), mode="token", present=f.is_file(),
                       renew=_("run codex login once in a terminal"), path=str(f), used_by=_("the platform's Codex runs"),
                       note=note)]


def _user_store() -> str | None:
    import tomllib
    try:
        doc = tomllib.loads((codex_home() / "config.toml").read_text())
    except (OSError, ValueError):
        return None
    v = doc.get("cli_auth_credentials_store")
    return str(v) if v is not None else None


def is_login_error(text: str) -> bool:
    return bool(_LOGIN_ERROR.search(text or "")) or any(s in (text or "") for s in _.variants(NOT_LOGGED_IN))


def login_window(seconds: float) -> str | None:
    """None when no refresh of the user's login can fall due in the next `seconds` (and a margin), else why it might: the
    checks that need the login wait for the next night then (section 3a, r2 S4)."""
    import datetime
    f = _auth_fields()
    if not f:
        return _(NOT_LOGGED_IN)
    if str(f.get("auth_mode") or "").lower() in ("apikey", "api_key"):
        return None
    try:
        last = datetime.datetime.fromisoformat(str(f.get("last_refresh")).replace("Z", "+00:00"))
    except ValueError:
        return _("when Codex's login was last refreshed can't be read: the checks that need it wait")
    if last.tzinfo is None:
        last = last.astimezone()
    left = (last + datetime.timedelta(seconds=REFRESH_INTERVAL_S) - datetime.datetime.now().astimezone()).total_seconds()
    if left < seconds + LOGIN_MARGIN_S:
        return _("Codex's login may refresh while the checks run: they wait for the next night (open Codex once to "
                 "refresh it now)")
    return None


def login_check() -> list[tuple[str, str]]:
    """(id, problem) for the neutral login job (bin/check-runtime-login.py), no model call: the login file there, the
    file store in use, and `codex login status` through a fresh run home on the checked copy."""
    out = []
    if not user_auth_file().is_file():
        return [("missing", _(NOT_LOGGED_IN))]
    store = _user_store()
    if store not in (None, "file"):
        out.append(("store", _("your Codex config keeps the login in {store}, which the platform's runs can't share (they "
                               "need the file store, M9)", store=store)))
    try:
        exe = binary()
    except Exception:   # no checked copy: said by doctor and the store's own rows
        return out
    home = run_home()
    try:
        r = subprocess.run([exe, "login", "status"], capture_output=True, text=True, timeout=60, env=run_env(home),
                           stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            out.append(("status", _("codex login status says the login doesn't work: {detail}",
                                    detail=(r.stderr or r.stdout).strip()[-200:])))
    except (OSError, subprocess.SubprocessError) as e:
        out.append(("status", _("codex login status could not run: {error}", error=type(e).__name__)))
    finally:
        end_run_home(home)
    return out


ADMIN_LAYERS = ("/etc/codex/config.toml", "/etc/codex/requirements.toml", "/etc/codex/managed_config.toml")


def doctor_notes() -> list[tuple[str, str, str]]:
    """(level, what, fix) rows of Codex's own for doctor, about the platform's own Codex runs, so only while the platform
    runs work on Codex (runtime_ports.platform_work): an administrator's layer that can override the run homes, the
    account-delivered layers it can't see, a login store the runs can't share. A sessions-only Codex gets none."""
    import runtime_ports
    if NAME not in runtime_ports.platform_work():
        return []
    out = []
    for f in ADMIN_LAYERS:
        if os.path.exists(f):
            out.append(("warn", _("{file} exists: an administrator's Codex settings apply to the platform's runs too, and "
                                  "can turn its updater or sandbox settings over", file=f), ""))
    out.append(("info", _("Codex's account-delivered settings (cloud requirements and managed config) can't be seen "
                          "without a login: the platform's runs check their tools before and after each run instead"), ""))
    store = _user_store()
    if store not in (None, "file"):
        out.append(("warn", _("your Codex config keeps the login in {store}: the platform's Codex runs need the file store "
                              "(M9)", store=store), _("set cli_auth_credentials_store = \"file\" in Codex's config.toml")))
    return out
