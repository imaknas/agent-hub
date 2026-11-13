"""The runtime adapter (Claude Code): the only place the platform learns about Claude Code's sessions and settings
format.

Switching tools, or Claude Code changing how an open session is woken, changes only this file; hubbus and the projects'
agents don't need to change. Design: docs/design/mailbox-routing.md (sessions),
docs/design/capabilities.md (settings). Standard library only; the system python3 (3.9) can import it too, which is why
this doesn't import registry/policy and only takes the dict policy.policy() computed.

  live_sessions()        session ids still alive: those on Claude Code processes' command lines (--resume=<id> or
                         --session-id=<id>; verified 10-24 to be the same as the hook's session_id and
                         CLAUDE_CODE_SESSION_ID), plus those the hooks recorded with their process
                         (runtime_ports.recorded_sessions): a session started as a fork (`--resume=<old>
                         --fork-session`, Desktop's branch, /branch) runs under a new id on no command line
  session_pid(env)       the session's own process, from a hook's environment (CLAUDE_PID: Desktop's CLI or the
                         terminal's claude, never looked for by name), for those records
  context_tokens(sid)    how much context resuming this session would load (the usage of the last reply in the
                         transcript; for a compacted one, the size after compaction). None if not found
  resume_args(sid)       the claude arguments for resuming in the background: --resume <sid> --fork-session (tested
                         10-24: the fork carries the full context, gets a new id, and the original transcript is left
                         completely untouched: an open session isn't affected)
  wake(sid, text)        wake an idle open session for a letter: a request for the session's waiter, an asyncRewake
                         Stop hook (`hub-event --runtime claude wait` → wait()) that exits 2 with the notice; True only
                         while a waiter heartbeats. Details in the "waking a live session" section below
                         (docs/design/mailbox-runtimes.md B1)
  can_wake(sid)          whether wake would be handed over now: the waiter's heartbeat is fresh; wake_gap(sid) why not
  session_status(sid)    busy, idle, waiting or unknown, from `claude agents --json` (it lists Desktop's sessions too, and
                         only the listing: without --json the command is the agent view, which can start things);
                         session_statuses(sids) the same for many from one listing
  session_summary(sid)   how its last reply ended (<session>.last): what wait() kept, for a project whose label lets it

Settings (neutral policy → Claude Code's settings; the policy's fields are in policy.py):
  settings(pol)          → (sandbox, deny, allow): the platform-managed part of the project's
                         .claude/settings.local.json
                         (render-project-settings.py writes the file, records it, detects drift)
  lease_settings(pol)    → the content of a lease's `--settings`: the same deny + user-level credentials + blocking
                         `security`
  settings_path(ws), sandbox_rendered(ws)   where the settings file is, whether the OS sandbox really has been written
  control_paths(), session_folders(ws), foreign_controls(ws), platform_hooks()
                                            the files sessions load settings, hooks and tools from (runtime_ports'
                                            control interface)
                                            into it

Tool servers (runtime_ports' tool_servers interface; docs/design/evaluators.md step 0):
  ambient_servers(task)  the MCP servers a run without --strict-mcp-config would load: `claude mcp list` for that run
                         (the only place the account's claude.ai connectors show up) plus the config files; None for a
                         sealed run; anything it can't read → ValueError, so the run doesn't happen
  servers_named(task)    the servers its allow rules name; Task.deny_servers becomes --disallowedTools mcp__<id>
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess

import i18n

_ = i18n.domain(__file__)
NOT_FOUND = "claude not found"   # also what is_login_error looks for (with its translation)
PROJECTS = pathlib.Path.home() / ".claude" / "projects"
# a judgment point's model_group → model (runtime_ports.models)
MODELS = {"fast": "haiku", "standard": "sonnet", "strong": "opus"}
_ID = re.compile(r"--(?:resume|session-id)[= ]([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def live_sessions() -> set[str]:
    """Raises runtime_ports.SessionsUnknown when the process table can't be read: that says nothing about any session."""
    import runtime_ports
    try:
        r = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        raise runtime_ports.SessionsUnknown(repr(e)) from e
    if r.returncode != 0:
        raise runtime_ports.SessionsUnknown(r.stderr.strip()[:200] or str(r.returncode))
    return set(_ID.findall(r.stdout)) | runtime_ports.recorded_sessions(NAME)


def session_pid(env) -> int | None:
    """The session's process, as its hooks see it (CLAUDE_PID): None when not set or not a pid."""
    try:
        pid = int(env.get("CLAUDE_PID") or 0)
    except ValueError:
        return None
    return pid if pid > 1 else None


def alive(sid: str | None) -> bool:
    import runtime_ports
    try:
        return bool(sid) and sid in live_sessions()
    except runtime_ports.SessionsUnknown:
        return bool(sid)   # can't tell: not gone


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


# ---------- settings ----------

def _tilde(p) -> str:
    s, h = str(p), str(pathlib.Path.home())
    return "~" + s[len(h):] if s == h or s.startswith(h + "/") else s


def _cli_rules(commands) -> list[str]:
    """The platform's session commands ("agenthub send") → Claude rules: one without arguments and one with."""
    return [r for c in commands for r in (f"Bash({c})", f"Bash({c} *)")]


def _rule_path(p) -> str:
    """A path in a permission rule: ~/… under the home folder, //… (absolute) elsewhere (a single / is the settings
    file's project folder)."""
    s = _tilde(p)
    return s if s.startswith("~") else "/" + s


def _spellings(p) -> list[str]:
    """The path as configured and, when it differs, what it resolves to (the part before a glob's first *): a rule
    naming only a link would leave its target readable (seatbelt.spellings does the same for the platform's profiles)."""
    s = str(p)
    head, star, _tail = s.partition("*")
    cut = head[:head.rfind("/")] if star else s
    r = os.path.realpath(cut) + s[len(cut):] if cut else s
    return [s] if r == s else [s, r]


def _spelled(paths) -> list[str]:
    return list(dict.fromkeys(x for p in paths for x in _spellings(p)))


def _home() -> pathlib.Path:
    """Claude Code's user-level folder: $CLAUDE_CONFIG_DIR, else ~/.claude."""
    return pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude").expanduser()


def _user_config_file() -> pathlib.Path:
    """~/.claude.json, or the one in $CLAUDE_CONFIG_DIR (as configured_servers reads it)."""
    return (_home() if os.environ.get("CLAUDE_CONFIG_DIR") else pathlib.Path.home()) / ".claude.json"


def credential_paths() -> dict:
    """Its login (runtime_ports credential_paths), under its home as configured now: the credential file, not the folder
    it is in, whose other entries its sessions use (transcripts, plans, skills; docs/design/capabilities.md 11-08, A2 for
    the finer rules there). And the headless token the platform's runs log in with (_token_file), which is not under
    $CLAUDE_CONFIG_DIR."""
    return {"dirs": [], "files": list(dict.fromkeys([str(_home() / ".credentials.json"), str(_token_file())]))}


# What it loads settings, hooks and tools from for every session of the user, anywhere: never written by a sandboxed
# project's file tools (its Bash has no allowWrite there), or the user's next session would run it outside any sandbox.
# (name under its home, tree); and the user config file. Reading them stays allowed (skills' files, plans).
USER_CONTROL = (("settings.json", False), ("settings.local.json", False), ("CLAUDE.md", False), ("hooks", True),
                ("commands", True), ("agents", True), ("plugins", True), ("output-styles", True), ("rules", True),
                ("skills", True))
# The Keychain isn't a file, so the OS sandbox can't block it (tested 10-25: Bash inside the sandbox could look up
# Claude's login credentials). These rules are only reinforcement: another way of writing it (sh -c, python) gets around
# them (doctor's sandbox row says so)
SECURITY_DENY = ("Bash(security:*)", "Bash(security *)", "Bash(/usr/bin/security *)")


def user_control() -> list[tuple[pathlib.Path, bool]]:
    return [(_home() / n, t) for n, t in USER_CONTROL] + [(_user_config_file(), False)]


def _not_home(paths) -> list:
    """Credential folders, less any that is or holds its own home: its sessions' Bash and file tools use the home (the
    settings' ~/.claude stays denied whole to the platform's own sandboxes; finer rules inside it are A2)."""
    home = pathlib.Path(os.path.realpath(_home()))
    real = {p: pathlib.Path(os.path.realpath(p)) for p in paths}
    return [p for p in paths if real[p] != home and real[p] not in home.parents]


def _edit(p, tree: bool) -> list[str]:
    """Edit denied on a file, or on a folder and everything in it (also as the entry itself: it can't be replaced)."""
    return [r for s in _spellings(p) for r in ([f"Edit({_rule_path(s)})"] + ([f"Edit({_rule_path(s)}/**)"] if tree else []))]


def settings(pol: dict) -> tuple[dict, list[str], list[str]]:
    deny = []
    for p in pol["other_projects"]:
        deny += [f"Read(/{p}/**)", f"Edit(/{p}/**)"]  # //abs: an absolute path
    secret = _spelled(pol["deny_read"])
    deny += [f"Read({_rule_path(p)}/**)" for p in secret]
    # the files its sessions load settings and hooks from, at any depth (worktrees included): never written by the
    # session's file tools (its Bash is kept off them by the sandbox itself)
    ws = pol.get("workspace")
    deny += [r for c in pol.get("protect") or [] if c["tree"] and ws
             for r in (f"Edit(/{ws}/**/{c['name']})", f"Edit(/{ws}/**/{c['name']}/**)")]
    # docs/design/capabilities.md 11-08, A1: what its services are denied, its sessions are too (points 5 and 6)
    state = _spelled(pol.get("platform_state") or [])
    cred_dirs = _spelled(_not_home(pol.get("credentials") or []))
    cred_files = _spelled(pol.get("credential_files") or [])
    deny += [r for p in secret for r in _edit(p, True)]
    deny += [r for p in [*state, *cred_dirs] for r in (f"Read({_rule_path(p)})", f"Read({_rule_path(p)}/**)", *_edit(p, True))]
    deny += [r for p in cred_files for r in (f"Read({_rule_path(p)})", *_edit(p, False))]
    deny += [r for p in pol.get("deny_write_files") or [] for r in _edit(p, False)]
    deny += [r for p in pol["deny_write"] for r in _edit(p, True)]
    deny += [r for p, tree in user_control() for r in _edit(p, tree)]
    deny += SECURITY_DENY
    deny = list(dict.fromkeys(deny))
    allow = [f"Bash({_tilde(c)} *)" for c in pol["platform_commands"]] + _cli_rules(pol.get("platform_cli") or [])
    sandbox = {
        "enabled": True,
        "failIfUnavailable": True,
        "allowUnsandboxedCommands": False,
        "filesystem": {
            "allowWrite": list(pol["write"]),
            # not the workspace root: git dies when a folder above its -C can't be stat'ed, and these settings have no
            # stat-only allow (other_projects names each project instead)
            "denyRead": list(dict.fromkeys([_tilde(p) for p in pol["other_projects"]] + [_tilde(p) for p in secret]
                                           + [_tilde(p) for p in [*cred_dirs, *cred_files, *state]])),
        },
        "network": {"allowedDomains": list(pol["network"]), "allowLocalBinding": bool(pol.get("local_bind"))},
    }
    return sandbox, deny, allow


def session_env(pol: dict) -> dict[str, str]:
    """The settings file's env for a sandboxed project's sessions: its own tool caches (policy.py env), and no git of the CLI's own
    in the folder. A session a person opens has the tool's own protection; this is reinforcement."""
    return {**(pol.get("env") or {}), **NO_FOLDER_GIT}


def lease_settings(pol: dict) -> dict:
    """settings()' deny, plus every credential folder whole, its own home included (a lease's tools only read; the real
    gate is that every work-mode command asks the owner)."""
    deny = settings(pol)[1]
    deny += [f"Read({_rule_path(p)})" for p in pol["credential_files"]] + [f"Read({_rule_path(p)}/**)" for p in pol["credentials"]]
    deny += SECURITY_DENY
    return {"permissions": {"deny": list(dict.fromkeys(deny))}}


def platform_settings(project: str, spec: dict, run_dir, keep: pathlib.Path | None = None, cwd=None,
                      folders=()) -> pathlib.Path:
    """A judgment point with settings: platform: the only settings file the run loads (with --restricted, Claude Code
    reads none of the user's, project's or local files): the project's sandbox when it's enforced and the deny list,
    both from the policy, never from what is rendered in the workspace. keep: an unattended
    job's own state folder that its allow rules write (own_state_dir), left out of the deny list; cwd: the run's working
    directory, which when it is the job's folder (job_folder) stays readable even with no rule writing it; folders: every
    folder the run works in (its own, the project's, those its rules add), none of whose git directories it writes
    (docs/design/confined-mail-tools.md M7). Written in the run's own folder (Task.run_dir), which llm.py removes."""
    import policy
    import registry
    if not run_dir:
        raise ValueError(f"the confined run of {project} has no run folder (llm.py makes one): its settings are never "
                         "written anywhere shared")
    index = registry.refresh()
    doc = index["projects"][project]
    pol = policy.policy(project, doc, index)
    folder = keep or job_folder(cwd)   # read: the job's own folder, whether or not its rules write it
    hidden = policy.run_state_denied(project, folder)
    # the project's own declared credentials (project-agent-calls.md C): its sessions and services read them, its
    # confined runs never (folders whole; files one by one, below)
    own_dirs, own_files = policy.secret_paths(project, doc)
    # the hub's platform state for another project's run, less the job's own folder and the notes it writes (the hub's
    # own runs: hidden above)
    pol = {**pol, "deny_read": [*pol["deny_read"], *hidden, *own_dirs, *own_files],
           "platform_state": policy.platform_state(project, [folder, policy.notes_dir(project)])}
    sandbox = settings(pol)[0]
    # a runtime's deny can't be narrowed by an allow: the job's own folder and the project's notes come off the list here,
    # and only the run's allow rules decide whether it writes them
    carved = pol["deny_write"]
    for place in (keep, policy.notes_dir(project)):
        if place:
            carved = policy.write_except(carved, place)
    pol = {**pol, "deny_write": carved}
    # each carved entry is denied as itself too (settings: a file beside the path, not only a folder's contents)
    deny = lease_settings(pol)["permissions"]["deny"]
    deny += [r for x in hidden if not x.is_dir() for r in (f"Read({_rule_path(x)})",) if r not in deny]
    deny += [r for x in _spelled(own_files) for r in (f"Read({_rule_path(x)})",) if r not in deny]
    # and at any depth below the project's folders: a copy in a worktree is the same credential
    deny += [r for base, rel in policy.secret_names(project, doc) for b in _spelled([base])
             for tail in ("", "/**") for t in ("Read", "Edit") for r in (f"{t}({_rule_path(b)}/**/{rel}{tail})",)
             if r not in deny]
    deny += [r for r in run_write_denied(project, [*(cwd and [cwd] or []), *folders]) if r not in deny]
    if spec.get("prompt_source") == "untrusted":
        deny += [r for r in instructions_denied(project, [*(cwd and [cwd] or []), *folders]) if r not in deny]
    # no allow list: the run's own rules are its whole boundary, and its mail is the platform's tools, never a command
    out = {"permissions": {"deny": deny}}
    # the registry's enforce.sandbox, as render-project-settings decides it (not the services-only exemption)
    if (doc.get("enforce") or {}).get("sandbox") is True:
        # autoAllowBashIfSandboxed defaults to true: a sandboxed Bash command would be approved before allow: is
        # consulted, so allow: would stop being the boundary exactly in the sandboxed projects
        out["sandbox"] = {**sandbox, "autoAllowBashIfSandboxed": False}
    p = pathlib.Path(run_dir) / "settings.json"
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(out, ensure_ascii=False))
    return p


def _folds(name: str) -> list[str]:
    """name and every spelling a case-insensitive volume opens as it: on APFS the long s (U+017F) folds to s, and a run
    that writes `ſTATE.md` while no STATE.md exists makes a file the volume serves as STATE.md (probe 11-10). No other
    character folds to a letter these names use."""
    out = [""]
    for ch in name:
        out = [x + c for x in out for c in ((ch, "\u017f") if ch in "sS" else (ch,))]
    return out


def instructions_denied(project: str, folders) -> list[str]:
    """What a run whose prompt anyone can write may not write (docs/design/confined-mail-tools.md M6): what the owner's
    sessions or the platform read as instructions. Every runtime's rules-file names at any depth and .claude/rules under
    each folder it works in; and .agent-memory but its notes: the registrar sends STATE.md's owner to-do lines every
    night. A deny can't be narrowed by an allow, so .agent-memory is written as a list: its top files and .git by name,
    whether they exist or not (a missing STATE.md must not be made), and anything else in it now. Each name in every
    spelling the volume folds to it (_folds)."""
    import memory_paths
    import policy
    import runtime_ports
    places = list(dict.fromkeys(pathlib.Path(os.path.realpath(f)) for f in [*project_roots(project), *folders] if f))
    rules = []
    for f in places:
        base = _rule_path(f)
        for name in runtime_ports.rules_file_names():
            rules += [f"{t}({base}{mid}{n})" for n in _folds(name) for mid in ("/", "/**/") for t in ("Edit", "Write")]
        rules += [f"{t}({base}{mid}.claude/{n}{tail})" for n in _folds("rules") for mid in ("/", "/**/")
                  for tail in ("", "/**") for t in ("Edit", "Write")]
    notes = policy.notes_dir(project)
    # resolved: in a worktree .agent-memory is a link to the main checkout's, and beside() keeps only paths under the
    # resolved folder, so a link spelling would deny the notes folder itself
    mems = {pathlib.Path(os.path.realpath(pathlib.Path(f) / ".agent-memory")) for f in places}
    if notes:
        mems.add(notes.parent)
    for mem in sorted(mems):
        named = [mem / f for n in memory_paths.TOP for f in _folds(n)] + [mem / ".git"]
        present = policy.beside(mem, [mem / memory_paths.TREE, *named]) if mem.is_dir() else []
        for p in [*named, *present]:
            rules += [f"{t}({_rule_path(p)}{tail})" for tail in ("", "/**") for t in ("Edit", "Write")]
    return list(dict.fromkeys(rules))


HUB_RUN = ".hub-run"   # the platform's request folder in a project's workspace and runtime folder (cap-runner.py)


def _git_rules(folder) -> list[str]:
    """Edit and Write denied on every git directory or pointer file at any depth under folder, whether it exists yet or
    not (a run can't make a new one either)."""
    f = _rule_path(folder)
    return [f"{t}({f}{tail})" for tail in ("/.git", "/.git/**", "/**/.git", "/**/.git/**") for t in ("Edit", "Write")]


def run_write_denied(project: str, folders) -> list[str]:
    """What no confined run writes, wherever its rules reach (docs/design/confined-mail-tools.md M7, project-agent-calls.md
    A2): a git directory under any folder it works in (its own, the project's, the ones its rules add), the project's git
    common directory (from the pointer files, never by running git in the project's repo), and the platform's request
    folder in the project's workspace and runtime folder. The runtime matches these as the volume does (case, too)."""
    import gitdirs
    import registry
    places = list(dict.fromkeys(pathlib.Path(os.path.realpath(f)) for f in [*project_roots(project), *folders] if f))
    rules = [r for f in places for r in _git_rules(f)]
    for f in places:
        found = gitdirs.dirs(f)
        if found:
            common = _rule_path(found.common)
            rules += [f"{t}({common}{tail})" for tail in ("", "/**") for t in ("Edit", "Write")]
    doc = registry.refresh()["projects"].get(project) or {}
    d = registry.dirs(doc) if doc else {}
    for base in (d.get("workspace"), d.get("runtime")):
        if base:
            hub_run = _rule_path(pathlib.Path(os.path.realpath(base)) / HUB_RUN)
            rules += [f"{t}({hub_run}{tail})" for tail in ("", "/**") for t in ("Edit", "Write")]
    return list(dict.fromkeys(rules))


def settings_path(ws: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(ws) / ".claude" / "settings.local.json"


def sandbox_rendered(ws) -> tuple[bool, str]:
    """The registry's enforce.sandbox is only an intention; the settings file written is the fact."""
    import nofollow   # the project's folder: no link followed, no FIFO waited on
    try:
        st = json.loads(nofollow.read(ws, settings_path(ws).relative_to(ws)) or b"")
    except (OSError, ValueError, TypeError):
        return False, _("the sandbox is not in this project's settings yet (settings.local.json cannot be read)")
    if not (st.get("sandbox") or {}).get("enabled"):
        return False, _("the sandbox is not in this project's settings yet (sandbox.enabled is not true)")
    return True, ""


# ---------- sessions (runtime_ports' sessions interface) ----------

def context_size(sid: str) -> int | None:
    return context_tokens(sid)


def current_session(env) -> str | None:
    """The processes Claude Code runs inside a session (Bash, hooks) all carry this environment variable."""
    return env.get("CLAUDE_CODE_SESSION_ID") or None


_SESSION_PROCESS = re.compile(r"(?:^|/)claude(?:\s|$)|/claude-code/|@anthropic-ai/claude-code")


def session_process(command: str) -> bool:
    """Whether a process with this command line is a Claude Code session (runtime_ports.current_session asks, walking
    up from the current process, only when more than one runtime is installed): the claude executable, or the npm
    package's entry point."""
    return bool(_SESSION_PROCESS.search(command or ""))


# ---------- waking a live session (runtime_ports' wake; docs/design/mailbox-runtimes.md B1) ----------
# Through an asyncRewake hook (code.claude.com/docs/en/hooks, "Run hooks in the background"): a hook run in the
# background that exits 2 wakes Claude even when the session is idle, its stderr shown to Claude as a system reminder
# (it reads "Stop hook blocking error from command …: <stderr>", so the text says what it is). Its timeout is enforced.
# Live spike 11-02 (Desktop, CLI 2.1.309): the wake comes about 1 s after the exit; a timeout of 86400 holds (a waiter
# armed for 12 minutes still woke); a waiter on SessionStart holds the session's first reply until it ends, so it is
# armed only on Stop; a request matched again after it was taken woke the session ~10 times for one letter.
# The timeout is 14 days since step 3 of docs/design/mailbox-session-first.md. The docs state no maximum and the CLI's limit
# is unverified (14 days in milliseconds does fit a 32-bit timer, the usual way a large timeout fires at once); a hook
# installed earlier keeps the timeout it was installed with, and is killed at it, the waiter's record left to go stale.
#
# So: every Stop (the end of every turn) arms one waiter, `hub-event --runtime claude wait` (wait() below), which
#   - records itself in <sessions>/<session>.waiter (hubconf.session_file; the secrets directory, in no sandbox), the
#     newest winning: an older one that finds another's token there, or no file (SessionEnd, prune), exits 0;
#   - heartbeats that file every poll (its mtime), which is how wake() tells a live waiter from a dead one;
#   - takes each <session>.wake-<nonce> request by renaming it to a dot name no lookup matches (rename is atomic: of
#     two waiters only one gets it), then unlinks it, prints the notices to stderr and exits 2: each request wakes once;
#   - exits 0 when the session's process (CLAUDE_PID, the session's own CLI: Desktop's is not nvm's) is gone, and
#     before its own timeout.
# wake(sid, text) writes a request (tmp + rename) only while that session's waiter heartbeats; otherwise False, and
# dispatch's usual routing goes on. A session is wakeable once its first turn has ended, until it is idle for longer
# than the hook timeout. Agent-view background sessions can stop after about an hour unpinned (the docs), which no timeout
# reaches. Not armed: platform runs (HUB_LLM_POINT, HUBBUS_LEASE: checked by hub-event), -p runs (the
# session process's own arguments; the docs say -p kills background hooks at teardown, but on 2.1.316 (11-02) `claude
# -p` waited for the waiter to exit, so a waiter there hangs the run), SDK runs (CLAUDE_CODE_ENTRYPOINT, a second signal:
# a -p run inside another session inherits that session's), and a session whose process can't be read (fail safe).
# The inbox socket (cross-session-messaging) is not used: its message format is still undocumented (checked 11-01).

WAIT_TIMEOUT_S = 14 * 86400  # the Stop hook's timeout (enforced for asyncRewake); the waiter leaves before it
WAIT_MARGIN_S = 60
WAIT_POLL_S = 2.0
WAITER_FRESH_S = 10          # a waiter whose heartbeat is older is taken as gone
REQUEST_TTL_S = 600          # a request no waiter took within this is dropped unshown (its letter is routed by then)
HEADLESS_ENTRYPOINTS = {"sdk-cli", "sdk-ts", "sdk-py"}   # -p (the CLI turns "cli" into sdk-cli), Agent SDK programs
LAST_CHARS = 500             # how much of a session's last reply wait() keeps
AGENTS_TIMEOUT_S = 5         # the agents listing: about 0.1 s when measured (again 11-13); past this it is unlisted


def note(port: str) -> str | None:
    """What to know about a port this runtime has, for doctor's feature table."""
    if port == "wake":
        return _("a session can be woken once its first turn has ended, while it has been idle under {days} days; a "
                 "background session from the agent view can stop after about an hour unless pinned, and then can't be",
                 days=WAIT_TIMEOUT_S // 86400)
    return None


def _session_key(sid: str) -> str:
    import runtime_ports
    return runtime_ports.qualify(NAME, sid)


def _fresh(path, now: float) -> bool:
    try:
        return now - path.stat().st_mtime <= WAITER_FRESH_S
    except OSError:
        return False


def wake(sid: str, text: str, now=None) -> bool:
    """Leave a wake request for this session's waiter: True when a live one will take it."""
    import secrets
    import hubconf
    key = _session_key(sid)
    if not can_wake(sid, now):
        return False
    nonce = secrets.token_hex(8)
    dest = hubconf.session_file(key, f"wake-{nonce}")
    tmp = dest.with_name(f".{dest.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.replace(tmp, dest)
    return True


def can_wake(sid: str, now=None) -> bool:
    """Whether this session's waiter heartbeats: a wake would be handed over."""
    import time
    import hubconf
    waiter = hubconf.session_file(_session_key(sid), "waiter")
    return bool(waiter) and _fresh(waiter, time.time() if now is None else now)


def wake_gap(sid: str, now=None) -> str | None:
    """Why can_wake is false (runtime_ports.WAKE_GAPS): no waiter's record, or one whose heartbeat stopped: killed with no
    chance to leave, by a timeout installed earlier or the CLI itself."""
    import hubconf
    if can_wake(sid, now):
        return None
    waiter = hubconf.session_file(_session_key(sid), "waiter")
    return "stopped" if waiter and waiter.exists() else "not_armed"


def session_status(sid: str) -> str:
    """From the CLI's listing of active sessions. Only with --json: bare, the command is the agent view. A session the
    listing doesn't name, or names with a status it doesn't know, is unknown; a listing that failed is unlisted
    (runtime_ports.SESSION_STATUSES)."""
    return session_statuses([sid])[sid]


def session_statuses(sids) -> dict:
    """session_status for many sessions from one listing: the listing has them all, and each call starts a process. A
    listing that errors, times out or isn't a JSON list (a version that changed it) makes every session unlisted, not
    unknown: unknown counts as idle and would wake sessions that may be busy (runtime-versions.md section 5)."""
    from types import SimpleNamespace
    try:
        r = subprocess.run([_exe(), "agents", "--json"], capture_output=True, text=True, timeout=AGENTS_TIMEOUT_S,
                           stdin=subprocess.DEVNULL, env=_env(SimpleNamespace(env=None)))
        rows = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        rows = None
    if not isinstance(rows, list):
        return {s: "unlisted" for s in sids}
    out = {s: "unknown" for s in sids}
    for row in rows:
        if isinstance(row, dict) and row.get("sessionId") in out:
            status = row.get("status")
            out[row["sessionId"]] = status if status in ("busy", "idle", "waiting") else "unknown"
    return out


def _keep_last(key: str, text) -> None:
    """<session>.last: the end of the session's last reply, mode 600, in the sessions folder (in no sandbox, the bus, the logs
    or the backup). Nothing to keep (no such field, or its project's label doesn't let it) removes what an earlier turn
    left: it would read as this turn's, or outlive the label. Never in the way of the wait."""
    import hubconf
    import runtime_ports
    f = hubconf.session_file(key, runtime_ports.SESSION_SUMMARY)
    if not f:
        return
    try:
        if not isinstance(text, str) or not text:
            f.unlink(missing_ok=True)
            return
        f.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", errors="backslashreplace") as fh:   # a lone surrogate from a JSON escape
            fh.write(text[-LAST_CHARS:])
        os.replace(tmp, f)
    except OSError:
        pass


def session_summary(sid: str) -> str | None:
    """How the session's last reply ended; None: nothing is kept for it."""
    import hubconf
    import runtime_ports
    f = hubconf.session_file(_session_key(sid), runtime_ports.SESSION_SUMMARY)
    try:
        return f.read_text(encoding="utf-8", errors="replace") if f else None
    except OSError:
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        pass   # someone else's process: it exists
    return True


def _take(key: str, now: float) -> list[str]:
    """Every wake request for this session, each taken once: renamed to a dot name first (a rename is atomic; a
    request another waiter took is gone), then read and unlinked. One past REQUEST_TTL_S is taken and dropped."""
    import hubconf
    d, prefix, out = hubconf.session_addresses_dir(), f"{key}.wake-", []
    try:
        with os.scandir(d) as it:
            names = sorted(e.name for e in it if e.name.startswith(prefix))
    except OSError:
        return []
    for name in names:
        taken = d / f".{name}.taken"
        try:
            os.rename(d / name, taken)
        except OSError:
            continue
        try:
            stale = now - taken.stat().st_mtime > REQUEST_TTL_S
            text = taken.read_text(errors="replace").strip()
        except OSError:
            stale, text = True, ""
        taken.unlink(missing_ok=True)
        if text and not stale:
            out.append(text)
    return out


def _argv(pid: int) -> list[str] | None:
    """The session process's arguments (None: not readable). Linux: /proc's exact list; elsewhere ps's text, split on
    spaces (an argument with a space in it comes out in pieces: enough to spot a flag)."""
    import subprocess
    try:
        if os.path.exists(f"/proc/{pid}/cmdline"):
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                return [a.decode(errors="replace") for a in fh.read().split(b"\0") if a] or None
        r = subprocess.run(["ps", "-o", "args=", "-p", str(pid)], capture_output=True, text=True, timeout=10)
        return (r.stdout.split() or None) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _printing(argv: list[str]) -> bool:
    """Whether the session runs with -p / --print: such a run waits for an asyncRewake Stop hook to exit before it
    exits itself (seen 11-02 on 2.1.316, not what the docs say), so a waiter there would hold it for its whole timeout."""
    return any(a in ("-p", "--print") or a.startswith("--print=") for a in argv[1:])


def person_session(pid: int, env) -> bool:
    """Whether the session in process pid is one a person works in, by the waiter's rules: not an SDK or -p run (the
    entrypoint, then the process's own arguments, since a -p run inside a session inherits its entrypoint), and not one
    whose arguments can't be read. Only such a session gets a record (runtime_ports.record_session): dispatch holds a
    project's letters back for a live one."""
    if env.get("CLAUDE_CODE_ENTRYPOINT") in HEADLESS_ENTRYPOINTS:
        return False
    argv = _argv(pid)
    return argv is not None and not _printing(argv)


def wait(raw: str, env, *, keep_last: bool = False, clock=None, sleep=None, poll: float = WAIT_POLL_S,
         argv_of=None) -> tuple[int, str, str]:
    """The Stop hook's waiter (see above): (exit code, stderr, how it ended). Exit 2 with the notices wakes the
    session; 0 ends quietly. It arms only when the session's process is known (CLAUDE_PID) and isn't a -p run: a
    print-mode claude (-p) started inside another session inherits that session's CLAUDE_CODE_ENTRYPOINT (claude-desktop, cli),
    so the entrypoint alone doesn't tell. keep_last: this session's project lets it keep the end of its last reply; a run it
    doesn't arm for keeps nothing either. argv_of: how to read a process's arguments (tests)."""
    import secrets
    import time
    import hubconf
    clock, sleep = clock or time.time, sleep or time.sleep
    try:
        data = json.loads(raw or "{}") or {}
        sid = data.get("session_id")
    except (ValueError, AttributeError):
        data, sid = {}, None
    if not sid or not isinstance(sid, str):
        return 0, "", "no-session"
    if env.get("CLAUDE_CODE_ENTRYPOINT") in HEADLESS_ENTRYPOINTS:
        return 0, "", "headless"
    key = _session_key(sid)
    waiter = hubconf.session_file(key, "waiter")
    if not waiter:
        return 0, "", "no-session"
    pid = session_pid(env)
    argv = (argv_of or _argv)(pid) if pid else None
    if argv is None:
        return 0, "", "no-session-process"   # can't tell it isn't -p: a missed wake, never a hung run
    if _printing(argv):
        return 0, "", "headless"
    _keep_last(key, data.get("last_assistant_message") if keep_last else None)
    token = f"{os.getpid()}-{secrets.token_hex(8)}"
    waiter.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = waiter.with_name(f".{waiter.name}.{token}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(token)
    os.replace(tmp, waiter)   # newest wins

    def leave(code, text, why):
        try:
            if waiter.read_text() == token:
                waiter.unlink()
        except OSError:
            pass
        return code, text, why

    deadline = clock() + WAIT_TIMEOUT_S - WAIT_MARGIN_S
    while True:
        try:
            if waiter.read_text() != token:
                return 0, "", "superseded"
        except OSError:
            return 0, "", "ended"
        if not _alive(pid):
            return leave(0, "", "session-gone")
        texts = _take(key, time.time())   # request ages are file times
        if texts:
            return leave(2, "\n".join(texts), "woke")
        if clock() >= deadline:
            return leave(0, "", "deadline")
        try:
            os.utime(waiter)   # the heartbeat wake() looks at
        except OSError:
            return 0, "", "ended"
        sleep(poll)


def unsupported(port: str) -> str | None:
    """Why a port is missing here, for doctor's feature table (none is, today)."""
    return None


# ---------- where Claude Code stores a session ----------
# Reading sessions for statistics is not a runtime's job: bin/sessions_claude.py reads these files whatever the
# platform's runtime is. Here only what the runtime needs to resume or move its own sessions.

import datetime  # noqa: E402


def slug_for_path(path: str) -> str:
    """The folder name Claude Code stores transcripts under: / and . in the path replaced by -."""
    return path.replace("/", "-").replace(".", "-")


# ---------- a single run (runtime_ports' agent interface) ----------
# Claude Code's `claude -p` flags are assembled only here. The permission_mode / allowed_tools / tools / prompts /
# extra_args / allow_from_env / extra_allow_env in the judgment point declaration (Task.spec) are Claude-specific and
# read here for now, until layer 2's neutral policy is settled.

import itertools  # noqa: E402

from runtime_ports import TaskResult  # noqa: E402


# At its start `claude -p` runs `git status` in its folder for the prompt, outside any sandbox: the CLI turns fsmonitor, hooks and
# signatures off, not a repo's clean filter, and a sandboxed project writes its own repo's config. This variable stops that git
# (real runs, 2.1.319: docs/design/mailbox-session-first.md, step 3's record). It is a tool's feature and an update could drop
# it: update-claude.py runs a real probe for each new version and alerts.
NO_FOLDER_GIT = {"CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "1"}
FOLDER_GIT_OFF = True   # runtime_ports.folder_git_unknown


def _env(task) -> dict:
    """claude's environment variables: no git of its own in the folder (above), and PATH gets nvm's default-version bin added.
    claude itself can be called by its full path, but plugin hooks (e.g. vercel's SessionEnd `node …`) find node through PATH;
    under the scheduler PATH has no nvm and Homebrew's node is gone too (2026-10-24), so the librarian printed node: command
    not found every time."""
    env = {**(os.environ if task.env is None else task.env), **NO_FOLDER_GIT}
    try:
        import hubconf
        nvm = hubconf.nvm_bin()
    except Exception:
        nvm = None
    if nvm and str(nvm) not in env.get("PATH", "").split(":"):
        env["PATH"] = f"{nvm}:{env['PATH']}" if env.get("PATH") else str(nvm)
    return env


def _rules(task) -> tuple:
    """(permission mode, allow rules, permission prompts) the run gets: the declaration's, the caller's additions, and
    a registry job's allow list (which makes it unattended)."""
    spec = task.spec
    mode, allowed, prompts = spec.get("permission_mode"), list(spec.get("allowed_tools") or []), spec.get("prompts")
    env = os.environ if task.env is None else task.env
    if spec.get("extra_allow_env") and env.get(spec["extra_allow_env"]):
        allowed += json.loads(env[spec["extra_allow_env"]])  # adds rules only, doesn't change the permission mode
    if spec.get("allow_from_env") and env.get(spec["allow_from_env"]):
        # the allow list a registry job declares: unattended = dontAsk, anything not listed is refused without a prompt
        allowed += json.loads(env[spec["allow_from_env"]])
        mode, prompts = "dontAsk", "none"
    return mode, allowed, prompts


_GLOB = re.compile(r"[*?\[{]")
# a permission rule: a tool name, optionally with its argument in parentheses, on one line (nothing that starts with "-")
RULE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*(\([^\n\r\0]*\))?")   # - : a tool server's id keeps it (mcp_id)


WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def job_folder(cwd) -> pathlib.Path | None:
    """The run's working directory when it is an unattended job's own state folder (<state>/agent-jobs/<job>, registry
    `cwd: state`; never the platform's .settings), whatever its rules allow."""
    import hubconf
    if not cwd:
        return None
    base = pathlib.Path(cwd).resolve()
    if base.parent != (hubconf.STATE / "agent-jobs").resolve() or base.name.startswith("."):
        return None
    return base


def own_state_dir(allowed, cwd) -> pathlib.Path | None:
    """The run's working directory when it is an unattended job's own state folder (<state>/agent-jobs/<job>, never the
    platform's .settings) and one of its allow rules writes into it: the folder platform_settings leaves writable."""
    base = job_folder(cwd)
    if base is None:
        return None
    home = pathlib.Path.home()
    for r in allowed:
        m = re.fullmatch(r"(\w+)\((.+)\)", r)
        if not m or m.group(1) not in WRITE_TOOLS:
            continue
        path = m.group(2)
        root = (home if path.startswith("~/") else pathlib.Path("/") if path.startswith("//")
                else base if not path.startswith("/") else None)
        if root is None:   # /x is relative to the settings file: not a state folder rule
            continue
        parts = path.lstrip("~").lstrip("/").removeprefix("./").split("/")
        fixed = list(itertools.takewhile(lambda p: not _GLOB.search(p), parts))
        target = root.joinpath(*fixed).resolve()
        if target == base or base in target.parents:
            return base
    return None


def project_roots(project: str) -> list[pathlib.Path]:
    """Where a confined run of a project may follow a link to: the policy's (neutral), named here so the tests that patch
    it apply; imported when called, as this module's other platform imports are."""
    import policy
    return policy.run_roots(project)


FORBIDDEN_PARTS = (".git", HUB_RUN)   # no confined run writes into one, compared case-insensitively (the volume may be)


def _forbidden_part(p) -> bool:
    return any(part.lower() in FORBIDDEN_PARTS for part in pathlib.PurePath(p).parts)


def _inside(p: pathlib.Path, places) -> bool:
    return any(p == x or x in p.parents for x in places)


def _linked_rules(allowed, cwd, roots=None) -> tuple[list[str], list[str]]:
    """--restricted confines the file tools to the working directories, and it checks a path where it resolves: a
    worktree's .agent-memory is a link to the main checkout's (worktree-memory.sh), so a `./.agent-memory/…` rule
    matches nothing there (live probe 11-02, 2.1.316). For each `./` rule whose fixed part resolves outside the
    folder: that directory as an extra working directory, and the same rule on the resolved path → (dirs, rules).
    roots (a confined run: project_roots): a rule whose link leads outside every one of them raises ValueError, and the
    run doesn't happen. A project writes its own folder, links included: a `data -> ~` it planted would otherwise turn
    `Edit(./data/.zshenv)` into an edit of the user's shell, by the file tools, which run outside any OS sandbox."""
    base = pathlib.Path(cwd or os.getcwd()).resolve()
    dirs, rules = [], []
    for r in allowed:
        m = re.fullmatch(r"(\w+)\(\./(.+)\)", r)
        if not m:
            continue
        parts = m.group(2).split("/")
        fixed = list(itertools.takewhile(lambda p: not _GLOB.search(p), parts))
        real = base.joinpath(*fixed).resolve() if fixed else base
        if roots is not None:
            # every step of the path, not only where it ends: the first one becomes a working directory (--add-dir), and
            # `data -> ~` with `./data/workspace/<project>/**` ends inside the project while opening the whole home
            for i in range(1, len(fixed) + 1):
                step = base.joinpath(*fixed[:i]).resolve()
                if not _inside(step, [base, *roots]):
                    raise ValueError(f"the rule {r} follows a link out of the project's folders, to {step}; a confined "
                                     "run never follows one there")
                # a link the project planted inside its own folder (`out -> .git/brunna/outbox`) turns a plain rule into
                # a write there (M7)
                if _forbidden_part(step.relative_to(base) if base in step.parents else step):
                    raise ValueError(f"the rule {r} leads into {step}; a confined run never writes a .git or "
                                     f"{HUB_RUN} folder")
        if real == base or base in real.parents:
            continue
        top = base.joinpath(fixed[0]).resolve()
        if str(top) not in dirs:
            dirs.append(str(top))
        rules.append(f"{m.group(1)}(/{real}/{'/'.join(parts[len(fixed):])})" if len(fixed) < len(parts)
                     else f"{m.group(1)}(/{real})")
    return dirs, rules


# ---------- what a confined run's own rules may be (runtime_ports' rule_check; project-agent-calls.md A2) ----------
FILE_TOOLS = ("Read", "Glob", "Grep", *WRITE_TOOLS)
# all a sandboxed project's confined run may name: its shell can't be kept from the user's Keychain, so it has none
SANDBOXED_TOOLS = ("Read", "Glob", "Grep", *WRITE_TOOLS)
_PARTS = re.compile(r"([A-Za-z][A-Za-z0-9_-]*)(?:\((.*)\))?", re.S)


def _outside_path(path: str, cwd) -> bool:
    """A rule's path that isn't relative to the run's folder: ~/…, //… or /… (the settings file's folder), or one with
    ..; except an absolute one whose fixed part is the run's own job folder or inside it (registry `cwd: state`: what
    own_state_dir already reads)."""
    if ".." in path.split("/"):
        return True
    if not path.startswith(("~", "/")):
        return False
    base = job_folder(cwd)
    if base is None or not path.startswith(("~/", "//")):
        return True
    root = pathlib.Path.home() if path.startswith("~/") else pathlib.Path("/")
    parts = path[2:].split("/")
    target = root.joinpath(*itertools.takewhile(lambda p: not _GLOB.search(p), parts)).resolve()
    return not (target == base or base in target.parents)


def _cli_words() -> set[str]:
    """The platform's commands by name, as a Bash rule could run them (lowercase)."""
    import names
    return {*(c.lower() for c in names.all("cli")), "hubbus"}


def _names_cli(arg: str) -> bool:
    """A Bash rule's argument that runs the platform's CLI (brunna, agenthub, bin/hub, hubbus), whatever the subcommand."""
    for word in re.split(r"[\s;&|()]+", arg.lower()):
        word = word.strip("'\"`")
        base = word.rsplit("/", 1)[-1]
        if base in _cli_words() or word.endswith("bin/hub") or word == "hub":
            return True
    return False


def _reserved_server(tool: str) -> bool:
    import names
    parts = tool.split("__")
    return parts[0] == "mcp" and len(parts) > 1 and parts[1].lower() in {f"{c}-run" for c in names.all("cli")}


def command_rules(rules) -> list[str]:
    """The rules that start a command (Bash): what they run is held by the sandbox, not by the file-tool rules."""
    return [str(r) for r in rules or [] if str(r) == "Bash" or str(r).startswith("Bash(")]


def rules_problems(rules, *, sandboxed: bool, network, untrusted: bool, cwd=None, servers: bool = True) -> list[str]:
    """Why a confined run with these rules (the point's and the job's own, never what the platform adds) doesn't start,
    one sentence each; [] = it may. untrusted: its prompt is text anyone who can write the bus wrote, so it is held to the
    same list as a sandboxed project's run (docs/design/confined-mail-tools.md M6): a shell or the open web, with Read
    always on, would let that text read the project's files and carry them out. servers: False for a run that loads no
    tool servers (a project's declared agent, docs/design/project-agent-calls.md A2): a rule naming one is refused."""
    out = []
    for r in rules:
        m = _PARTS.fullmatch(r)
        if not m:
            out.append(_("{rule} is not a permission rule", rule=r))
            continue
        tool, arg = m.group(1), m.group(2)
        if tool in FILE_TOOLS and arg is not None:
            if _outside_path(arg, cwd):
                out.append(_("{rule} names a path that isn't relative to the run's folder (write ./… or a name in it)",
                             rule=r))
            fixed = itertools.takewhile(lambda p: not _GLOB.search(p), arg.split("/"))
            if any(p.lower() in FORBIDDEN_PARTS for p in fixed):
                out.append(_("{rule} enters a .git or {folder} folder, which no confined run writes", rule=r,
                             folder=HUB_RUN))
        # reinforcement, not the boundary (a broader rule or a path gets past a name): a confined run reaches the
        # platform only through its tools; a script the job runs may call the CLI itself
        if tool == "Bash" and arg is not None and _names_cli(arg):
            out.append(_("{rule} runs the platform's command line, which a confined run reaches only through the "
                         "platform's tools", rule=r))
        if _reserved_server(tool):
            out.append(_("{rule} names the platform's own tool server, which only the platform gives a run", rule=r))
        elif not servers and tool.split("__")[0] == "mcp":
            out.append(_("{rule} names a tool server, and this run loads none", rule=r))
        if sandboxed or untrusted:
            if tool == "WebFetch":
                domain = arg[len("domain:"):] if arg and arg.startswith("domain:") else None
                if domain is None or domain not in (network or []):
                    out.append(_("{rule}: a sandboxed project's confined run fetches only from the domains in its own "
                                 "network: list", rule=r) if sandboxed else
                               _("{rule}: a run whose prompt anyone can write fetches only from the domains in its "
                                 "project's network: list", rule=r))
            elif tool not in SANDBOXED_TOOLS:
                out.append(_("{rule}: a sandboxed project's confined run may name only reading and editing files, and "
                             "WebFetch to its own domains", rule=r) if sandboxed else
                           _("{rule}: a run whose prompt anyone can write (an event job, a mail handler) may name only "
                             "reading and editing files, and WebFetch to its project's own domains", rule=r))
    return out


def run_server(task):
    """The platform's tool servers for this run, made final: note only for a run whose own rules write the notes (the
    neutral question "do these rules write the notes?" is answered here, MEMORY_RULE). It adds no rule; _flags allows
    exactly each server's tools."""
    if not task.servers:
        return task
    notes = MEMORY_RULE in _rules(task)[1]
    return task._replace(servers=tuple(s._replace(tools=tuple(t for t in s.tools if t != "note" or notes))
                                       for s in task.servers))


def _server_config(task) -> pathlib.Path:
    """The run's --mcp-config file, in its own folder: each server's command, arguments and env as given (none of them
    holds a $: llm.py checks, since Claude Code expands ${…} there)."""
    if not task.run_dir:
        raise ValueError(f"judgment point {task.point}: a run with the platform's tools needs its run folder")
    p = pathlib.Path(task.run_dir) / "mcp.json"
    body = {"mcpServers": {s.id: {"command": s.command, "args": list(s.args), "env": dict(s.env)} for s in task.servers}}
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(body, ensure_ascii=False))
    return p


def rule_problems(task, *, sandboxed: bool, network, untrusted: bool) -> list[str]:
    """rules_problems over the run's own rules: the point's and the caller's and the job's (_rules), in its folder."""
    return rules_problems(_rules(task)[1], sandboxed=sandboxed, network=network, untrusted=untrusted, cwd=task.cwd,
                          servers=task.spec.get("tool_servers") != "none")


MEMORY_RULE = "Edit(./.agent-memory/memory/**)"


def _notes_rules(allowed, project, cwd) -> list[str]:
    """A run whose rules write notes writes them where the project's are, by absolute path, wherever it runs: a fork of a
    hub session may run in a linked worktree with no link to them. Adds the rule to allowed; → the working directories it
    needs (--restricted confines the file tools to them), its .agent-memory so it can read the project's state too."""
    import policy
    notes = policy.notes_dir(project) if MEMORY_RULE in allowed else None
    if not notes:
        return []
    if _forbidden_part(notes):
        raise ValueError(f"the notes of {project} resolve to {notes}; a confined run never writes a .git or {HUB_RUN} "
                         "folder")
    rule = f"Edit(/{notes}/**)"
    if rule not in allowed:
        allowed.append(rule)
    base = pathlib.Path(cwd or os.getcwd()).resolve()
    return [] if base in notes.parents else [str(notes.parent)]


def _flags(task) -> list:
    spec = task.spec
    # the prompt goes on stdin (run()), never as an argument: a prompt is often a project's text, and one starting with
    # "--" would be read as an option (docs/design/project-agent-calls.md A4)
    cmd = [_exe(), "-p"]   # the scheduler's PATH has no nvm: PATH first, then the platform's (nvm default)
    if task.model:
        cmd += ["--model", task.model]
    mode, allowed, prompts = _rules(task)
    env = os.environ if task.env is None else task.env
    if mode:
        cmd += ["--permission-mode", mode]
    if prompts:
        cmd += ["--permission-prompts", prompts]
    if spec.get("settings") == "platform":
        # --restricted: no user/project/local settings files (only --settings applies), and the tools that run code
        # are off unless --tools names them; the tools named are the ones the rules use, plus reading
        project = env.get(spec.get("project_env") or "")
        if not project:
            raise ValueError(f"judgment point {task.point}: settings: platform needs the project in "
                             f"${spec.get('project_env')}")
        # built-in tools only (MCP tools aren't named in --tools): the point's own list when it declares one (a mailbox
        # handler), else reading and the tools the rules use (an allow: job): Bash only when a rule names it, the mail
        # being the platform's tools (docs/design/confined-mail-tools.md)
        if "tools" in spec:
            tools = {t for t in spec["tools"].split(",") if t}
        else:
            tools = {"Read", "Glob", "Grep"} | {r.split("(", 1)[0] for r in allowed if not r.startswith("mcp__")}
            # an Edit rule governs every file-writing tool: a new file (or a whole one rewritten) needs Write
            if "Edit" in tools:
                tools.add("Write")
        keep = own_state_dir(allowed, task.cwd)
        roots = project_roots(project)
        # the folder it runs in, where it resolves: a folder of the project's (a worktree, a session's folder) can be a
        # link the project made, and every ./ rule is relative to it
        here = pathlib.Path(task.cwd or os.getcwd()).resolve()
        if not (_inside(here, roots) or job_folder(task.cwd)):
            raise ValueError(f"judgment point {task.point}: its folder {task.cwd} resolves to {here}, outside the "
                             "project's folders; a confined run never starts there")
        dirs, extra = _linked_rules(allowed, task.cwd, roots)
        allowed += extra
        dirs += [d for d in _notes_rules(allowed, project, task.cwd) if d not in dirs]
        # written once every folder it works in is known: nothing under a .git of any of them is written (M7)
        cmd += ["--restricted", "--settings",
                str(platform_settings(project, spec, task.run_dir, keep, task.cwd, folders=[here, *dirs])),
                "--tools", ",".join(sorted(tools))]
        for d in dirs:
            cmd += ["--add-dir", d]
    elif "tools" in spec:
        cmd += ["--tools", spec["tools"]]
    if task.servers:
        # the platform's own servers, by --mcp-config; only their tools allowed, added after every check of the run's
        # own rules (rule_problems and servers_named see the point's and the job's rules only)
        cmd += ["--mcp-config", str(_server_config(task))]
        allowed += [f"mcp__{mcp_id(s.id)}__{t}" for s in task.servers for t in s.tools]
    if spec.get("restricted") and spec.get("settings") != "platform":
        cmd += ["--restricted"]   # no settings file of the folder it runs in: its hooks would run as the user
    if allowed:
        # a registry job's rules are the project's own text (manifest.update): one that isn't a rule must never reach
        # the command line, where `--allowedTools` takes every following word that isn't an option
        if (bad := [r for r in allowed if not RULE.fullmatch(r)]):
            raise ValueError(f"judgment point {task.point}: not a permission rule: {', '.join(map(repr, bad[:3]))}")
        cmd += ["--allowedTools", *allowed]
    if task.deny_servers:
        # a whole server: `mcp__<id>` with no tool matches every tool it has (deny wins over any allow), and a tool
        # denied this way isn't even offered to the model (live probe 11-01). The built-in resource tools reach any
        # connected server's resources by name, past those rules: off too
        cmd += ["--disallowedTools", *(f"mcp__{s}" for s in task.deny_servers), *MCP_RESOURCE_TOOLS]
    extra = list(spec.get("extra_args") or []) + list(task.extra or [])
    if spec.get("tool_servers") == "none" and SEAL not in extra:
        extra.append(SEAL)   # loads none of the environment's servers (the point's tool_servers: none)
    return cmd + extra


# ---------- the environment's MCP servers (runtime_ports' tool_servers interface) ----------
# A run without --strict-mcp-config loads every MCP server configured where it runs: the user's (~/.claude.json
# mcpServers), the folder's (local scope in ~/.claude.json, .mcp.json), plugins', the managed file's, and with the
# subscription login the account's claude.ai connectors (fetched from the account; nowhere on disk). Turning tools off
# doesn't stop them loading (10-27 experiment: 265 MCP tools, execute_sql on a production database among them).
SEAL = "--strict-mcp-config"   # only the servers --mcp-config gives
MCP_RESOURCE_TOOLS = ("ListMcpResourcesTool", "ReadMcpResourceTool", "ReadMcpResourceDirTool")
MCP_LIST_TIMEOUT_S = 180       # `claude mcp list` health-checks every server (each has its own connect timeout)
_LISTED = re.compile(r"^(.+?): .* - .+$")   # `<name>: <url or command> - <status>`; names have no ": " in them
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def mcp_id(name: str) -> str:
    """A server's name → its id in tool names (mcp__<id>__<tool>) and in permission rules: Claude Code turns every
    character outside [a-zA-Z0-9_-] into _ (`claude.ai Upwork` → claude_ai_Upwork, `plugin:vercel:vercel` →
    plugin_vercel_vercel)."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)


def _sealed(task) -> bool:
    return task.spec.get("tool_servers") == "none" or SEAL in [*(task.spec.get("extra_args") or []), *(task.extra or ())]


def parse_mcp_list(out: str) -> list[str]:
    """`claude mcp list`'s output → the server names it lists. Anything not in the shape it has today → ValueError
    (fail closed: the deny is never computed from a list that was only half read)."""
    lines = [_ANSI.sub("", line).rstrip() for line in out.splitlines()]
    if any(line.startswith("No MCP servers configured") for line in lines):
        return []
    start = next((i for i, line in enumerate(lines) if line.startswith("Checking MCP server health")), None)
    if start is None:
        raise ValueError(_("`claude mcp list` printed no server list: {output}", output=out.strip()[:200]))
    names, begun = [], False
    for line in lines[start + 1:]:
        if not line.strip():
            if begun:
                break   # what follows (config diagnostics) isn't the list
            continue
        begun = True
        m = _LISTED.match(line)
        if not m:
            raise ValueError(_("cannot read this line of `claude mcp list`: {line}", line=line[:200]))
        names.append(m.group(1))
    return names


def _listed_servers(task) -> list[str]:
    """The runtime's own answer, for this run: `claude mcp list` in the run's folder with the run's environment (login,
    config directory): the only place the account's connectors show up. It health-checks each server (connects and
    lists its tools, calls none), as the run itself would on starting."""
    try:
        r = subprocess.run([_exe(), "mcp", "list"], cwd=task.cwd or None, env=_env(task), capture_output=True,
                           text=True, timeout=MCP_LIST_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise ValueError(_("`claude mcp list` took over {seconds} seconds", seconds=MCP_LIST_TIMEOUT_S)) from None
    except OSError as e:
        raise ValueError(_("cannot run `claude mcp list`: {err}", err=e)) from None
    if r.returncode != 0:
        raise ValueError(_("`claude mcp list` failed (exit {code}): {detail}", code=r.returncode,
                           detail=(r.stderr.strip() or r.stdout.strip())[-200:]))
    return parse_mcp_list(r.stdout)


def _json_file(path: pathlib.Path) -> dict:
    """A config file: missing = empty; present but unreadable or not an object → ValueError."""
    try:
        d = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise ValueError(_("cannot read {file}: {err}", file=path, err=e)) from None
    if not isinstance(d, dict):
        raise ValueError(_("cannot read {file}: not a JSON object", file=path))
    return d


def _names(d) -> list[str]:
    servers = d.get("mcpServers") if isinstance(d, dict) else None
    return list(servers) if isinstance(servers, dict) else []


MANAGED_MCP = (pathlib.Path("/Library/Application Support/ClaudeCode/managed-mcp.json"),
               pathlib.Path("/etc/claude-code/managed-mcp.json"))


def configured_servers(cwd, env) -> tuple[list[str], list[str]]:
    """(the user scope's servers, every server named) in the config files: ~/.claude.json (or $CLAUDE_CONFIG_DIR's)
    mcpServers and every project's local ones (more than this run loads: denying a server that isn't loaded changes
    nothing), .mcp.json in the run's folder and each folder above it, and the managed file. Plugins' servers and the
    account's connectors aren't in these files: `claude mcp list` has them."""
    base = pathlib.Path(env.get("CLAUDE_CONFIG_DIR") or env.get("HOME") or pathlib.Path.home()).expanduser()
    top = _json_file(base / ".claude.json")
    user = _names(top)
    found = list(user)
    for proj in (top.get("projects") or {}).values():
        found += _names(proj)
    here = pathlib.Path(cwd).resolve()
    for d in (here, *here.parents):
        found += _names(_json_file(d / ".mcp.json"))
    for f in MANAGED_MCP:
        found += _names(_json_file(f))
    return user, found


def ambient_servers(task) -> list[str] | None:
    """The MCP servers this run would load from its environment, as ids (mcp_id): None when it carries
    --strict-mcp-config. The union of what `claude mcp list` says for the run and what the config files name; a user
    server the list doesn't show means its output changed under us → ValueError, as does anything unreadable."""
    if _sealed(task):
        return None
    env = _env(task)
    user, configured = configured_servers(task.cwd or os.getcwd(), env)
    listed = _listed_servers(task)
    if missing := sorted(set(user) - set(listed)):
        raise ValueError(_("`claude mcp list` does not show {names}, configured for the user: its output may have "
                           "changed", names=", ".join(missing)))
    return sorted({mcp_id(n) for n in (*configured, *listed)})


def servers_named(task) -> list[str]:
    """The servers whose tools the run's allow rules name (`mcp__<id>` or `mcp__<id>__<tool>`), as ids."""
    out = set()
    for rule in _rules(task)[1]:
        parts = rule.split("(", 1)[0].split("__")
        if parts[0] == "mcp" and len(parts) > 1 and parts[1]:
            out.add(mcp_id(parts[1]))
    return sorted(out)


def describe(task) -> list:
    cmd = _flags(task) + ["--output-format", "json"]
    if task.output_schema is not None:
        cmd += ["--json-schema", json.dumps(task.output_schema, ensure_ascii=False)]
    return cmd


def run(task) -> TaskResult:
    cmd = describe(task)
    try:
        r = subprocess.run(cmd, cwd=task.cwd or None, capture_output=True, text=True, timeout=task.timeout_s,
                           env=_env(task), input=task.prompt)
    except subprocess.TimeoutExpired:
        return TaskResult(False, error=_("over {minutes} {minutes:minute|minutes}", minutes=task.timeout_s // 60))
    except FileNotFoundError:
        return TaskResult(False, error=_(NOT_FOUND))
    import sys
    sys.stderr.write(r.stderr)
    if r.returncode != 0 or not r.stdout.strip():
        # claude -p errors (e.g. Login expired) are often only in the JSON on stdout, with what the run cost
        try:
            cost = json.loads(r.stdout).get("total_cost_usd")
        except (ValueError, AttributeError):
            cost = None
        return TaskResult(False, error=_("exit {code}: {detail}", code=r.returncode,
                                                 detail=(r.stderr.strip() or r.stdout.strip())[-300:]),
                          cost_usd=cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None)
    try:
        out = json.loads(r.stdout)
    except ValueError:
        return TaskResult(False, error=_("output is not JSON: {output}", output=r.stdout[:200]))
    # exit 0 with readable JSON = it ran to the end (ok). is_error only marks it incomplete: llm.py always passed it on
    # to the caller as is, and the librarian relies on that ("still goes by the actual file changes"), so it can't
    # become a failure
    return TaskResult(
        ok=True, incomplete=bool(out.get("is_error")), text=str(out.get("result") or ""),
        structured_output=out.get("structured_output"), cost_usd=out.get("total_cost_usd"),
        session_id=out.get("session_id"), turns=out.get("num_turns"), stop=str(out.get("subtype") or ""),
        denials=tuple(out.get("permission_denials") or ()))


def exec_stream(task) -> None:
    """A long-running streaming session (for channel leases): assemble the flags as declared and exec into claude, with
    stdin/stdout handed to the caller.
    Temporary: layer 1's open_session will replace it (who does the lease contract neutralisation m10251446 isn't
    decided yet), so it isn't in the interface."""
    cmd = _flags(task)   # no prompt argument (never is): messages come from stdin (stream-json)
    cmd += ["--input-format", "stream-json", "--output-format", "stream-json", "--include-partial-messages", "--verbose"]
    os.execvpe(cmd[0], cmd, _env(task))


# ---------- events (runtime_ports' lifecycle interface) ----------
# Claude Code's hooks: event names, matchers, the input JSON and the hookSpecificOutput output format are only here.
# The platform side has a single entry point `bin/hub-event <platform event>`; handlers return neutral text or a reason
# to refuse.

from runtime_ports import HubEvent  # noqa: E402

# (Claude hook event, matcher, platform event); matcher None = every occurrence of the event triggers it
HOOK_TABLE = (
    ("SessionStart", None, "session_start"),
    ("SessionEnd", None, "session_end"),
    ("PreCompact", None, "pre_compact"),
    ("UserPromptSubmit", None, "prompt"),
    ("PostToolUse", "Bash", "tool_used"),                                    # only "run a command" for now
    ("PreToolUse", "mcp__ccd_session__start_session", "open_session_attempt"),  # Desktop's open-session tool
)
_HOOK_NAME = {ev: name for name, _matcher, ev in HOOK_TABLE}
# A platform-managed hook (the user's own never look like these): one calling the platform's entry point, wherever the
# hub was when it was written (so a hub that moved replaces its old ones instead of adding a second set), or one of the
# hooks written before the entry point, which all named the hub's scripts under an agent-hub folder.
MANAGED = re.compile(r"/bin/hub-event(?=['\s]|$)|/agent-hub/bin/")


NAME = "claude"   # this runtime's name in runtime_ports.RUNTIMES: the hooks say whose they are


def hook_command(event: str) -> str:
    # as before: if the entry point is missing, quietly do nothing; the exit code is always 0, so the session never
    # shows an error. The hub's location is resolved now (hubconf.hub_bin); after the hub moves, installing again
    # rewrites it. --runtime: hub-event parses the input with this runtime's lifecycle and records the session in the
    # platform's ids (docs/design/mailbox-runtimes.md A1)
    import hubconf
    return (f"[ -x {hubconf.hub_bin('hub-event')} ] && {hubconf.sh_py(hubconf.MAIN / 'bin' / 'hub-event')} "
            f"--runtime {NAME} {event} || true")


# The waiter (wait() above): a Stop hook run in the background that may wake the session (asyncRewake), with a timeout
# long enough to wait through an idle day. Never on SessionStart: the session's first reply would wait for it. Its
# command passes the waiter's exit code on (exec; no `|| true`, which would turn the wake's 2 into 0), and is silent
# when the entry point is missing.
WAIT_HOOK = "Stop"


def wait_command() -> str:
    import hubconf
    return (f"[ -x {hubconf.hub_bin('hub-event')} ] && exec {hubconf.sh_py(hubconf.MAIN / 'bin' / 'hub-event')} "
            f"--runtime {NAME} wait; exit 0")


def wait_hook() -> dict:
    return {"type": "command", "command": wait_command(), "asyncRewake": True, "timeout": WAIT_TIMEOUT_S}


def _hook_input(raw: str) -> tuple[dict | None, str | None]:
    """(the hook's JSON, None) or (None, why it can't be used). Empty input is no session, not a fault: onboard, recover
    and sync-local-setup run the hooks with none."""
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
    """Why this hook input gave no event (runtime_ports lifecycle, optional): None when it is no session at all (empty
    input), else a neutral reason that hub-event logs, so a payload a new version changed doesn't pass in silence."""
    return _hook_input(raw)[1]


def parse_event(event: str, raw: str):
    data = _hook_input(raw)[0]
    if data is None:   # no hook JSON = no real session running (onboard/recover also run the setup), or unusable
        return None
    session = data["session_id"]
    cwd = data["cwd"]   # as the input names it: this process's own folder would be it resolved, and the name decides
    ti = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    if event == "tool_used":
        return HubEvent(event, session, cwd, command=str(ti.get("command", "")))
    if event == "open_session_attempt":
        elsewhere = None  # a neutral kind (the platform writes the wording): repository / machine / unknown_place
        if ti.get("repository"):
            elsewhere = {"kind": "repository", "name": str(ti["repository"])}
        elif ti.get("host") or ti.get("target") == "ssh":
            elsewhere = {"kind": "machine"}
        elif ti.get("preset"):
            elsewhere = {"kind": "unknown_place"}
        target_cwd = os.path.expanduser(ti["cwd"]) if ti.get("cwd") else None
        return HubEvent(event, session, cwd, target={"cwd": target_cwd, "elsewhere": elsewhere})
    return HubEvent(event, session, cwd)


def session_ending(raw: str) -> bool:
    """Whether this hook input is the session's end. Worktrees' user-level hooks hang one command off both SessionEnd and
    PreCompact (worktree-memory.sh end), and installed settings change only with the owner."""
    try:
        return json.loads(raw or "{}").get("hook_event_name") == "SessionEnd"
    except (ValueError, AttributeError):
        return False


def inject(event: str, text: str) -> str:
    if not text:
        return ""
    if event == "session_start":
        return text  # SessionStart's stdout goes straight into context
    if event in ("prompt", "tool_used"):
        return json.dumps({"hookSpecificOutput": {"hookEventName": _HOOK_NAME[event], "additionalContext": text}},
                          ensure_ascii=False)
    return ""


def deny(event: str, reason: str) -> str:
    if event != "open_session_attempt":
        return ""
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}, ensure_ascii=False)


def _strip_hooks(settings: dict, ours) -> dict:
    """settings without the hooks whose command `ours` matches; an entry or event left empty goes with them."""
    hooks = settings.setdefault("hooks", {})
    for name in list(hooks):
        kept = []
        for entry in hooks[name]:
            inner = [h for h in entry.get("hooks", []) if not ours(str(h.get("command", "")))]
            if inner:
                kept.append({**entry, "hooks": inner})
        if kept:
            hooks[name] = kept
        else:
            del hooks[name]
    return settings


def install_hooks(settings: dict) -> dict:
    """Platform-managed hooks (MANAGED in the command) are replaced as a set by HOOK_TABLE's hub-event and the Stop
    waiter (wait_hook); the user's own stay; rerunning gives the same result."""
    hooks = _strip_hooks(settings, MANAGED.search)["hooks"]
    for name, matcher, event in HOOK_TABLE:
        entry = {"hooks": [{"type": "command", "command": hook_command(event)}]}
        if matcher:
            entry = {"matcher": matcher, **entry}
        hooks.setdefault(name, []).append(entry)
    hooks.setdefault(WAIT_HOOK, []).append({"hooks": [wait_hook()]})
    return settings


def install_lifecycle(workspace, check: bool = False) -> bool:
    path = settings_path(workspace)
    settings = json.loads(path.read_text()) if path.exists() else {}
    out = json.dumps(install_hooks(settings), indent=2) + "\n"
    changed = not path.exists() or path.read_text() != out
    if changed and not check:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(out)
    return changed


def remove_lifecycle(workspace, check: bool = False) -> bool:
    """agenthub uninstall: the platform's hooks out of this project's settings, the user's own kept; whether that
    changed anything (a settings file that is a link is left alone)."""
    path = settings_path(workspace)
    if not path.is_file() or path.is_symlink():
        return False
    before = path.read_text()
    settings = _strip_hooks(json.loads(before), MANAGED.search)
    if not settings["hooks"]:
        del settings["hooks"]
    out = json.dumps(settings, indent=2) + "\n"
    if out != before and not check:
        if settings:
            path.write_text(out)
        else:   # only the platform's were there: the file goes
            path.unlink()
    return out != before


# ---------- a linked worktree's own settings ----------
# A session in a linked worktree (<main>/.claude/worktrees/<name>) reaches the main checkout's .agent-memory through a
# link (worktree-memory.sh). Claude Code asks before any write whose resolved path is outside the session's working
# directories, in auto mode too, so every note asked (measured 11-09 on 2.1.319: Bash redirection and Edit alike; an
# Edit allow rule doesn't lift it, a working directory does). So the worktree's settings name that folder as a working
# directory and deny Edit on what in it isn't the session's to write: the notes are memory/ and STATE.md
# (memory_paths.TOP, TREE). A deny rule also holds for Bash redirection; an ask rule doesn't (measured the same day).
# Claude Code reads hooks from a settings file that is a link but no permissions (also measured), so the worktree's file
# is a plain one, not a link to the main checkout's.
MEMORY_DIR = ".agent-memory"
MEMORY_DENY = (("AGENT.md", False), ("RULES", False), (".git", True))   # (name in it, tree)
_MEMORY_RULE = re.compile(r"Edit\(.*/" + re.escape(MEMORY_DIR) + r"/(AGENT\.md|RULES|\.git)(/\*\*)?\)")


def memory_access(main) -> tuple[list[str], list[str]]:
    """(working directories, deny rules) that let a worktree session write its main checkout's notes and STATE.md, and
    nothing else there. Nothing when the folder is a link or missing: a project's links never decide what is granted."""
    mem = pathlib.Path(main) / MEMORY_DIR
    if mem.is_symlink() or not mem.is_dir():
        return [], []
    return _spellings(mem), list(dict.fromkeys(r for name, tree in MEMORY_DENY for r in _edit(mem / name, tree)))


def _platform_memory(entry, key: str) -> bool:
    if not isinstance(entry, str):
        return False
    if key == "additionalDirectories":
        return entry.rstrip("/").endswith("/" + MEMORY_DIR)
    return bool(_MEMORY_RULE.fullmatch(entry))


def worktree_memory(settings: dict, main) -> dict:
    """settings with the platform's memory entries (memory_access) for this main checkout, any it wrote before for
    another location replaced, everything else kept; rerunning gives the same result."""
    dirs, deny = memory_access(main)
    perms = dict(settings.get("permissions") or {})
    for key, mine in (("additionalDirectories", dirs), ("deny", deny)):
        keep = [e for e in perms.get(key) or [] if not _platform_memory(e, key)]
        merged = keep + [e for e in mine if e not in keep]
        if merged:
            perms[key] = merged
        else:
            perms.pop(key, None)
    out = {k: v for k, v in settings.items() if k != "permissions"}
    if perms:
        out["permissions"] = perms
    return out


def install_worktree(folder, main, check: bool = False) -> bool:
    """worktree-memory.sh, at a session's start in a linked worktree of a project that isn't sandboxed (a sandboxed one's
    is render-project-settings'): its settings as a plain file, the platform's hooks (install_hooks) and memory entries
    (worktree_memory) in it, the user's own kept. Missing, or the link it used to be: seeded from the main checkout's
    file, so what the link gave carries over. Whether it changed."""
    path, src = settings_path(folder), settings_path(main)
    if path.is_symlink() or not path.exists():
        before = None
        cur = json.loads(src.read_text()) if src.is_file() and not src.is_symlink() else {}
    else:
        before = path.read_text()
        cur = json.loads(before)
    out = json.dumps(worktree_memory(install_hooks(cur), main), indent=2) + "\n"
    if out != before and not check:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}")
        tmp.write_text(out)
        tmp.replace(path)   # a link there is replaced, never written through
    return out != before


# ---------- the worktree hooks in the user's own settings ----------
# A linked worktree has no project settings, so only user-level hooks fire there. The owner installs them once
# (merge-hook-settings.py --worktree); after that the platform keeps its own entries current, never anything else in the
# file: upgrade (the owner's own command) rewrites them, doctor says when they are behind (AGENTS.md, user-level tool
# settings).
WORKTREE_SCRIPT = "worktree-memory.sh"


def worktree_hooks() -> tuple[tuple[str, str | None, str], ...]:
    """(event, matcher, command); a matcher of None fires on every instance of the event."""
    import hubconf
    s = hubconf.hub_bin(WORKTREE_SCRIPT)
    wt = f"[ -x {s} ] && {s}"
    return (
        ("SessionStart", None, f"{wt} start || true"),
        ("SessionEnd", None, f"{wt} end || true"),
        ("PreCompact", None, f"{wt} end || true"),
        ("PostToolUse", "Bash", f"{wt} nudge || true"),
        ("UserPromptSubmit", None, f"{wt} prompt || true"),
        # the waiter that wakes an idle session for a letter (wait_hook): run in the background, its exit code passed on
        # (exec, no `|| true`); worktree-memory.sh hands it to hub-event in a linked worktree only
        ("Stop", None, f"[ -x {s} ] && exec {s} wait; exit 0"),
    )


def _worktree_fields(command: str) -> dict:
    """The fields a worktree hook entry carries besides its command: the waiter's are the project hooks' own."""
    return {k: v for k, v in wait_hook().items() if k not in ("type", "command")} if command.endswith(" wait; exit 0") else {}


def _is_worktree_hook(command) -> bool:
    """One of the platform's worktree hooks, for this hub's location or an earlier one."""
    return f"/bin/{WORKTREE_SCRIPT}" in str(command or "")


def merge_worktree_hooks(settings: dict) -> dict:
    """settings with this version's set of worktree hooks: a hook the set has gained is added, one written for another hub
    location or no longer in the set goes (an entry or event emptied by that goes with it), the same command is never
    added twice, and an installed one gets today's fields (the waiter's timeout). The user's own hooks, entries in a shape
    this doesn't know (no list of hooks) and every other setting stay as they are."""
    hooks = settings.setdefault("hooks", {})
    table = worktree_hooks()
    wanted = {command for _e, _m, command in table}

    def inner(e):
        return e["hooks"] if isinstance(e, dict) and isinstance(e.get("hooks"), list) else []

    for event in list(hooks):
        if not isinstance(hooks[event], list):
            continue
        kept, dropped = [], False
        for e in hooks[event]:
            if not inner(e):
                kept.append(e)
                continue
            left = [h for h in inner(e) if not (isinstance(h, dict) and _is_worktree_hook(h.get("command"))
                                                and h.get("command") not in wanted)]
            dropped |= len(left) < len(inner(e))
            if left:
                kept.append({**e, "hooks": left})
        if kept or not dropped:
            hooks[event] = kept
        else:
            del hooks[event]
    for event, matcher, command in table:
        entries = hooks.setdefault(event, [])
        mine = [h for e in entries for h in inner(e) if isinstance(h, dict) and h.get("command") == command]
        if not mine:
            entry = {"hooks": [{"type": "command", "command": command, **_worktree_fields(command)}]}
            entries.append({"matcher": matcher, **entry} if matcher else entry)
        for h in mine:
            h.update(_worktree_fields(command))
    return settings


def _has_worktree_hooks(settings) -> bool:
    """Whether the owner installed the platform's worktree hooks here (any of them, for any hub location)."""
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    return isinstance(hooks, dict) and any(
        isinstance(h, dict) and _is_worktree_hook(h.get("command"))
        for es in hooks.values() if isinstance(es, list)
        for e in es if isinstance(e, dict) and isinstance(e.get("hooks"), list)
        for h in e["hooks"])


def user_settings_file() -> pathlib.Path:
    return pathlib.Path.home() / ".claude" / "settings.json"


def user_refresh(apply: bool) -> list[str]:
    """agenthub user-settings (run by upgrade, the owner's own command; apply) and doctor (not): the platform's set of
    worktree hooks in the user's settings brought to this version (merge_worktree_hooks: a hook the set gained is added,
    one the owner took out of the set comes back). Only where the owner installed them (one of them is there): nothing
    is added to settings that have none; nothing else in the file changes; a file that is a link, isn't JSON or has
    hooks in a shape Claude Code wouldn't read is left alone. What is (or was) behind; written whole or not at all, with
    the file's mode kept."""
    import os
    path = user_settings_file()
    if not path.is_file() or path.is_symlink():
        return []
    try:
        before = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    try:
        if not _has_worktree_hooks(before):
            return []
        after = merge_worktree_hooks(json.loads(json.dumps(before)))
    except (AttributeError, TypeError):   # hooks in a shape Claude Code wouldn't read either: not the platform's to fix
        return []
    if after == before:
        return []
    if apply:
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(after, indent=2, ensure_ascii=False) + "\n")
            os.chmod(tmp, path.stat().st_mode & 0o7777)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    return [_("the worktree hooks in {file}", file=path)]


def user_cleanup(apply: bool) -> list[str]:
    """agenthub uninstall, user level (only on the owner's own yes): the worktree hooks merge-hook-settings.py --worktree
    put in the user's settings for this hub. What there is (or was) to remove."""
    path = user_settings_file()
    if not path.is_file() or path.is_symlink():
        return []
    import hubconf
    mark = hubconf.hub_bin("worktree-memory.sh")
    before = path.read_text()
    try:
        settings = _strip_hooks(json.loads(before), lambda c: mark in c)
    except ValueError:
        return []
    if not settings["hooks"]:
        del settings["hooks"]
    out = json.dumps(settings, indent=2) + "\n"
    if out == before:
        return []
    if apply:
        path.write_text(out)
    return [_("the worktree hooks in {file}", file=path)]



# ---------- control files (runtime_ports' control interface) ----------
# What Claude Code loads settings, hooks and code from inside a project folder (its docs: sandboxing, "protected
# paths"; hooks, "hook locations"). The session's own sandbox already keeps its Bash off them; the platform's sandbox
# for a project's services and jobs, and its Edit rules, use this list too (policy.py protect).
RULES_FILE_NAMES = ("CLAUDE.md", "CLAUDE.local.md")   # at any depth: a nested one is loaded when a file beside it is read
CONTROL = ((".claude", False), (".claude/settings.json", True), (".claude/settings.local.json", True),
           (".claude/hooks", True), (".claude/skills", True), (".claude/agents", True), (".claude/commands", True),
           # every .md under it is loaded like CLAUDE.md (code.claude.com/docs/en/memory.md)
           (".claude/rules", True), (".mcp.json", True))
WORKTREES = ".claude/worktrees"


def control_paths() -> list[tuple[str, bool]]:
    return list(CONTROL)


def session_folders(workspace) -> list[pathlib.Path]:
    """The folder and its worktrees (the app makes them under .claude/worktrees): real folders only, no links."""
    import os
    ws = pathlib.Path(workspace)
    out = [ws]
    try:
        with os.scandir(ws / WORKTREES) as it:
            out += [ws / WORKTREES / e.name for e in it if e.is_dir(follow_symlinks=False)]
    except OSError:
        pass
    return out


def foreign_controls(workspace) -> list[str]:
    """Committed hooks or tool servers its sessions would load: the platform can't remove a committed file, so these
    are only reported (read without following links: nofollow.py)."""
    import nofollow
    ws, out = pathlib.Path(workspace), []
    try:
        raw = nofollow.read(ws, ".claude/settings.json")
        doc = json.loads(raw) if raw else {}
        if isinstance(doc, dict) and doc.get("hooks"):
            out.append(_("{file} declares hooks", file=".claude/settings.json"))
    except (OSError, ValueError) as e:
        out.append(_("{file} can't be read as a plain file ({why})", file=".claude/settings.json", why=e))
    if os.path.lexists(ws / ".mcp.json"):
        out.append(_("{file} declares tool servers", file=".mcp.json"))
    for kind in ("agents", "skills"):
        for f in sorted((ws / ".claude" / kind).rglob("*.md")) if (ws / ".claude" / kind).is_dir() else []:
            if not f.is_symlink() and re.search(r"(?m)^hooks:", f.read_text(errors="replace")[:4000]):
                out.append(_("{file} declares hooks", file=str(f.relative_to(ws))))
    return out


def platform_hooks() -> dict:
    """The hooks a sandboxed project's settings hold: the platform's only (render-project-settings writes them)."""
    return install_hooks({})["hooks"]


# ---------- rules file (runtime_ports' instructions interface) ----------

def instructions_file(workspace) -> pathlib.Path:
    """The local file Claude Code loads along with CLAUDE.md (untracked); the platform's managed block goes here."""
    return pathlib.Path(workspace) / "CLAUDE.local.md"


def local_ignores() -> list:
    return ["CLAUDE.local.md", ".claude/settings.local.json"]


# ---------- translating a long-running session's output (stream interface; docs/design/lease-contract-neutral.md) ----

from runtime_ports import StreamEvent  # noqa: E402

_TARGET_KEYS = ("file_path", "notebook_path", "url", "command", "pattern", "query", "path")
_QUOTA_STATUS = {"allowed": "ok", "allowed_warning": "warning", "rejected": "exhausted"}
RELAY_SERVER = "lease_relay"                      # the name in the settings of the lease's MCP server (lease-relay.py)
APPROVE_TOOL = f"mcp__{RELAY_SERVER}__approve"     # permission relay (--permission-prompt-tool)
SHOW_FILE_TOOL = f"mcp__{RELAY_SERVER}__show_file"  # platform tool show_file (llm-points' allow list uses this name)


def _verbs() -> dict:
    return {"read": _("read"), "search": _("search"), "open_web": _("open web page"), "write_file": _("write"),
            "run_command": _("run")}


def _windows() -> tuple:
    return (("five_hour", _("5 hours")), ("seven_day", _("7 days")))


def _questions(inp: dict) -> str:
    return " / ".join(q.get("question", "") for q in inp.get("questions") or [])[:200]


def action(tool: str, inp: dict | None) -> tuple:
    """Claude's tool → (kind, collab, target, summary). target is for judging (path, URL, command), summary a sentence
    for people."""
    import sessions_claude   # the one table of Claude's tools, shared with the session source
    inp = inp or {}
    kind, collab = sessions_claude.kind_of(tool)
    target = next((str(inp[k]) for k in _TARGET_KEYS if inp.get(k)), "")
    if tool == SHOW_FILE_TOOL:  # the platform's "show the user" tool (the lease's MCP): read a file to show them
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
    """One line of Claude Code's stream-json output (`--output-format stream-json`) → neutral conversation events.
    Unrecognised lines return []."""
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
        # the usage-limit wording in any language claude may write it in (the catalog's variants)
        if d.get("is_error") and re.search("|".join([r"rate.?limit", *map(re.escape, _.variants("usage limit"))]),
                                           text, re.I):
            out.append(StreamEvent("error", {"error": "rate_limit", "detail": text[:300]}))
        elif d.get("subtype") == "error_max_turns":
            out.append(StreamEvent("error", {"error": "bad_request", "detail": _("turn limit reached; detaching the lease"),
                                             "_detach": "budget"}))
        out.append(StreamEvent("turn_end", {"cost": d.get("total_cost_usd"), "num_turns": d.get("num_turns"),
                                            "duration_ms": d.get("duration_ms"), "is_error": bool(d.get("is_error"))}))
    return out


def classify_exit(code, stderr_tail: str) -> StreamEvent:
    raw = _("exit {code}: {detail}", code=code, detail=stderr_tail[-200:])
    if is_login_error(stderr_tail):  # the same login check as llm.py's (layer 6)
        return StreamEvent("error", {"error": "agent_login", "detail": _(
            "Claude Code (used by project sessions) is not logged in, or its credentials have expired (the platform's "
            "brunna/claude-login sends an alert). {raw}", raw=raw)})
    return StreamEvent("error", {"error": "process_crashed", "detail": raw})


def resumable(sid: str) -> bool:
    """Resumable = the transcript still exists (Claude Code writes it only after the first turn finishes, and it's
    cleaned up once the retention period passes)."""
    return transcript(sid) is not None


def resume_command(sid: str) -> tuple:
    import hubconf
    return (f"{hubconf.hub_bin('lease-resume')} {sid}",
            _("Paste it in a terminal on the hub machine: it resumes the same conversation (Claude Code). When you are "
              'done, go back to the assistant and press "detach" for the handover.'))


def neutral_question(inp: dict) -> dict:
    """AskUserQuestion's input → the generic question structure {questions: [{question, options: [{label,
    description?}], multi}]}."""
    qs = []
    for q in (inp or {}).get("questions") or []:
        opts = [{"label": o.get("label", ""), **({"description": o["description"]} if o.get("description") else {})}
                for o in q.get("options") or [] if isinstance(o, dict)]
        qs.append({"question": q.get("question", ""), "options": opts, "multi": bool(q.get("multiSelect"))})
    return {"questions": qs}


def native_answers(answers: dict) -> dict:
    """Generic answers {question: label or [label…]} → the {question: "label"} AskUserQuestion wants (multiple
    choices joined with commas)."""
    return {q: (", ".join(a) if isinstance(a, list) else a) for q, a in (answers or {}).items()}


# ---------- auth (layer 6, login) ----------
# Three logins: the subscription login (keychain; readable under launchd, not under SSH), the headless token (`claude
# setup-token`, one year, the default for unattended use), ~/.claude/.credentials.json (for interactive claude under
# SSH). Paths are computed at call time (tests swapping HOME swap them).
# No value is ever read or printed; only auth_env puts the token into the environment variables handed to claude.

from runtime_ports import Credential  # noqa: E402

TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"
_LOGIN_ERROR = re.compile(r"login|401|unauthori[sz]ed|oauth|invalid api key|authentication|credential|expired", re.I)


def _token_file() -> pathlib.Path:
    return pathlib.Path.home() / ".claude" / "headless-oauth-token"


def _creds_file() -> pathlib.Path:
    return pathlib.Path.home() / ".claude" / ".credentials.json"


def install_info() -> dict:
    """What `agenthub init`'s agent step needs to offer Claude Code (docs/design/newcomer-path.md B4): how to find it,
    how to install it (through nvm only: the platform's one copy, kept current by update-claude), its credential for
    unattended runs, and the settings that make it the platform's agent. The steps are shell lines run on the owner's
    yes, user level only."""
    return {"label": "Claude Code",
            "gives": _("everything: letters and note reminders inside your chats, and work in the background"),
            "find": "claude",
            "install": ['[ -s "$HOME/.nvm/nvm.sh" ] || curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash',
                        '. "$HOME/.nvm/nvm.sh" && nvm install --lts && nvm alias default "lts/*" '
                        '&& npm install -g @anthropic-ai/claude-code'],
            # made and saved by the owner in their own shell: agent-hub never receives or writes the token (Claude
            # Code's terms: a third-party tool may not collect, store or intermediate Claude.ai credentials)
            "credential": {"kind": "own-token", "command": ["claude", "setup-token"], "file": str(_token_file()),
                           "store": "(umask 077; read -rs t && printf '%s\\n' \"$t\" > ~/.claude/headless-oauth-token)",
                           "needs": _("a Claude Pro, Max, Ultra, Team or Enterprise plan")},
            # user scope: ~/.claude.json, which the Desktop app's Code tab reads too
            "mcp_register": ["claude", "mcp", "add", "--scope", "user", "{server}", "--", "{cli}", "mcp"],
            "mcp_unregister": ["claude", "mcp", "remove", "--scope", "user", "{server}"],
            "settings": {"runtime": {"default": "claude"}, "modules": {"claude": True}}}


def clean_env(env) -> dict:
    return {k: v for k, v in env.items() if k != TOKEN_VAR}


def auth_env(mode: str, env) -> tuple:
    out = clean_env(env)
    if mode != "token":  # subscription: no token; claude uses the keychain login itself
        return out, ""
    f = _token_file()
    if not f.is_file():
        return None, _("{file} does not exist (the default is the headless token; to use the subscription login, "
                       "declare auth: subscription)", file=f)
    out[TOKEN_VAR] = f.read_text().strip()
    return out, ""


# what Claude Code bills per use with, ahead of any login: an API key, or a cloud provider's own billing
METERED_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                "CLAUDE_CODE_USE_FOUNDRY")


def billing(mode: str, env, restricted: bool = False) -> str:
    """How a run is paid for (runtime_ports' auth interface). Only a run that loads no settings file but the platform's
    (restricted) with the headless token, which only a Claude plan issues (claude setup-token), can be told: metered when
    its environment names an API key or a cloud provider (Claude Code uses those ahead of the token), unknown when it
    reaches another endpoint (ANTHROPIC_BASE_URL), else subscription. Unknown too for the interactive login (mode
    subscription), which may be a pay-per-use Console login, and for a run that loads the user's or the project's saved
    settings, which can name a key, a helper or an endpoint of their own."""
    if mode != "token" or not restricted:
        return "unknown"
    seen = dict(env or {})
    if any(seen.get(k) for k in METERED_VARS):
        return "metered"
    return "unknown" if seen.get("ANTHROPIC_BASE_URL") else "subscription"


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
    """claude's login errors (Login expired, /login, 401, OAuth…); claude itself not being found also needs a person,
    so it counts too."""
    text = text or ""
    # the not-found message comes from another process (llm.py's JSON), maybe in another language: the msgid or any
    # catalog's text
    return bool(_LOGIN_ERROR.search(text)) or any(s in text for s in _.variants(NOT_FOUND))


def relocate(sid: str, old_cwd: str, new_cwd: str) -> bool:
    """The folder moved (rename, upgrade): Claude Code stores transcripts by the folder's slug, and `--resume` looks
    only in the current folder's slot, so move <sid>.jsonl (and the subagents' <sid>/) to the new folder's slot.
    Already in the new place = True; found nowhere = False."""
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


# ---------- completion (runtime_ports' completion interface; docs/design/llm-providers.md) ----------
# A judgment point's "answer only", run by Claude Code: all tools off, no MCP loaded, no session kept; the prompt goes
# in through stdin (argv has a length limit, and "put it all in if it fits" prompts exceed it); runs in an empty temp
# directory: running in a project copy would trigger the project's own hooks (the librarian hit that one).

from runtime_ports import CompletionResult  # noqa: E402


def _exe() -> str:
    return binary()


# ---------- distribution (docs/design/runtime-versions.md section 1): getting, installing and pinning Claude Code ----------

PACKAGE = "@anthropic-ai/claude-code"
# The oldest version the platform's runs are known to work on: the completion flags (--restricted, --strict-mcp-config,
# --no-session-persistence) and the measurements V-M1 to V-M11 were all taken on 2.1.319 (runtime-versions.md, 11-09).
# Step 2 never promotes a version below it.
MIN_VERSION = "2.1.319"
_VERSION = re.compile(r"\s*(\d+\.\d+\.\d+)")
_CHECKED: str | None = None   # the binary a conformance check runs; set in process only (check_binary), never from the environment


def check_binary(path: str | None) -> None:
    """For bin/conformance.py's child alone: make this process's runs use the binary under check. Not an environment
    variable or a flag of llm.py, so nothing a project hands a run can choose which binary runs (runtime-versions.md, the
    advisor's 11-13 rule)."""
    global _CHECKED
    _CHECKED = str(path) if path else None


def binary() -> str:
    """The claude a platform run starts: the one under check in a conformance child, else the one on the platform's PATH
    (nvm's; step 2 makes it the store's checked copy)."""
    if _CHECKED:
        return _CHECKED
    try:
        import hubconf
        return hubconf.tool("claude")
    except Exception:
        return "claude"


def _npm_env() -> dict:
    """npm is a `#!/usr/bin/env node` script: PATH must find nvm's node."""
    import hubconf
    return {**os.environ, "PATH": hubconf.LAUNCHD_PATH}


def _npm() -> str:
    import hubconf
    d = hubconf.nvm_bin()
    return str(d / "npm") if d else "npm"


def latest() -> str:
    """The newest version npm publishes. Raises RuntimeError with the reason when it can't be told."""
    try:
        r = subprocess.run([_npm(), "view", PACKAGE, "version"], capture_output=True, text=True, timeout=120,
                           env=_npm_env(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(_("npm could not be asked for the latest version: {error}", error=type(e).__name__)) from e
    got = r.stdout.strip()
    if r.returncode != 0 or not re.fullmatch(r"\d+\.\d+\.\d+", got):
        raise RuntimeError(_("npm could not say the latest version (exit {code})", code=r.returncode))
    return got


def install(version: str, folder) -> pathlib.Path:
    """Install exactly this version from npm into folder (its own prefix, nothing global) and return its claude. npm checks
    each tarball against the registry's integrity hash as it installs; this also refuses an install whose lockfile lacks
    one for any package it resolved (the wrapper and the platform package with the executable, optional ones included).
    Raises RuntimeError with the reason."""
    folder = pathlib.Path(folder)
    if not re.fullmatch(r"\d+\.\d+\.\d+", version or ""):
        raise RuntimeError(_("not a version: {version!r}", version=version))
    folder.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run([_npm(), "install", "--prefix", str(folder), f"{PACKAGE}@{version}", "--no-audit", "--no-fund",
                            "--save-exact"], capture_output=True, text=True, timeout=900, env=_npm_env(),
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(_("npm install did not run: {error}", error=type(e).__name__)) from e
    if r.returncode != 0:
        raise RuntimeError(_("npm install failed (exit {code})", code=r.returncode))
    try:
        lock = json.loads((folder / "package-lock.json").read_text())
    except (OSError, ValueError):
        raise RuntimeError(_("the install left no readable package-lock.json")) from None
    packages = {k: v for k, v in (lock.get("packages") or {}).items() if k and isinstance(v, dict)}
    unhashed = sorted(k for k, v in packages.items() if v.get("resolved") and not v.get("integrity"))
    if not packages or unhashed:
        raise RuntimeError(_("the install resolved packages with no integrity hash: {names}",
                             names=", ".join(unhashed) or _("(none resolved)")))
    exe = folder / "node_modules" / ".bin" / "claude"
    if version_of(exe) != version:
        raise RuntimeError(_("the installed claude does not say version {version}", version=version))
    return exe


def version_of(exe) -> str | None:
    """The version this claude says it is (`claude --version`), or None."""
    try:
        r = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
                           env=_npm_env())
    except (OSError, subprocess.SubprocessError):
        return None
    m = _VERSION.match(r.stdout)
    return m.group(1) if r.returncode == 0 and m else None


def pin_env(folder) -> dict:
    """What keeps a run of this copy from updating itself (V-M5): the updater off, and npm's global prefix at the copy's
    own folder, so an updater that ignored the flag could write only into this copy, where the digest check sees it."""
    return {"DISABLE_AUTOUPDATER": "1", "npm_config_prefix": str(folder)}


def complete(req, conf) -> CompletionResult:
    import tempfile
    # --restricted: no settings file or rules file of any folder (real run: docs/design/mailbox-session-first.md, step 3's record)
    cmd = [_exe(), "-p", "--restricted", "--tools", "", "--strict-mcp-config", "--no-session-persistence", "--output-format", "json"]
    if req.model:
        cmd += ["--model", req.model]
    if req.system:
        cmd += ["--append-system-prompt", req.system]
    if req.schema is not None:
        cmd += ["--json-schema", json.dumps(req.schema, ensure_ascii=False)]
    env, why = auth_env(conf.get("auth") or "token", os.environ)
    if env is None:
        return CompletionResult(False, stop="error", error=why)
    env["HUB_LLM_POINT"] = req.point   # the process started (hooks included) knows it's a platform headless call
    env = _env(type("T", (), {"env": env})())
    import hubconf
    try:
        tmp = tempfile.TemporaryDirectory(prefix="hub-completion-", dir=hubconf.private_tmp(), ignore_cleanup_errors=True)
    except OSError as e:   # the secrets folder read-only, the disk full: no folder for it, not a crash
        return CompletionResult(False, stop="error", error=_("could not make a folder for the run: {e}", e=e))
    with tmp as cwd:
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
    """No request made (the hourly claude-login already makes real ones): whether claude is there and whether the login
    to use is there."""
    import shutil
    if not shutil.which(_exe()) and not pathlib.Path(_exe()).is_file():
        return False, _(NOT_FOUND)
    env, why = auth_env(conf.get("auth") or "token", {})
    return (env is not None), why
