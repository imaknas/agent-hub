#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""A project's permission policy, computed from the project registry, in runtime-neutral terms only (paths, domains,
capability names).

Translating it into each runtime's settings is other modules' job (design: docs/design/capabilities.md):
  runtime_claude.settings(policy) / lease_settings(policy)   Claude Code's settings
  seatbelt.profile(policy, cap)                               the macOS Seatbelt profile for platform-run commands
                                                              (cap-runner)
  seatbelt.service_profile / landlock.plan                    a sandboxed project's own services and command jobs
                                                              (through isolation.py: macOS / Linux)
No runtime syntax or paths allowed here (checked by tests/test_runtime_boundary.py).

policy(name, doc, index) → {
  workspace:          the project folder (Path or None)
  write:              what may be written beyond the platform defaults (its own runtime, the registry's
                      allow_write; strings starting with ~). Not the shared tool caches: one project could change a
                      package another installs next. The bus directory only when the project's mail doesn't go through
                      its outbox (mail_outbox; docs/design/bus-sender.md): a sandboxed project's letters do, so the
                      bus is in no sandbox
  env:                environment for its sessions, services and jobs: the tool caches (uv, pip, npm, corepack) in its
                      own repo's git dir (<workspace>/.git/brunna/cache/<tool>), which only it can write
  protect:            what its sessions load settings, hooks and tools from ([{name, tree}] matched at any depth under
                      the workspace; the runtime's control port): written by nothing of the project's, only by the
                      platform. Empty (and doctor says so) when the runtime can't name them
  protect_roots:      each runtime's own folder in the project with the entries in it that stay writable
                      ([{name, writable}], the control port's control_root): its services and jobs create or change
                      nothing else there (the macOS profile; sessions keep protect plus each entry present)
  other_projects:     other projects' workspace/runtime, and every memory repo that isn't its own: other projects'
                      bare repos and memory copies, the mirrors, anything else under git_origin / memory_copies (no
                      reading or writing; Path)
  deny_write:         folders nothing of the project's writes (Path): the platform's places (hub, bare repos, memory
                      copies, the instance dir, the bus for an outbox project), what runs as the user outside any
                      sandbox (the platform's Python and uv, the CLI's folder, nvm, git's user config, the service
                      manager's folder); deny_write_files: single files of the same kind (the shell's startup files,
                      ~/.gitconfig, uv). Seatbelt and Landlock deny every write outside the project's own folders
                      already: this is for the runtime's file tools
  deny_read:          the raw notes in the memory copies (glob), the platform's secrets (Path; during the move both the
                      old and new locations are blocked), the bus directory (a project whose mail goes through its
                      outbox reads its own letters from the view the platform writes into its folder)
  credentials:        user-level credential directories (Path); credential_files: credential files (Path): {settings}'s
                      credentials, CREDENTIALS below, and what each installed runtime declares (runtime_ports)
  platform_state:     the hub's run state and the owner's data (Path; empty for the hub itself): .state whole, .logs,
                      the data dir, the evals dir; never one that is or holds the hub root (the old layout's data dir)
  read:               the platform state sandboxed code reads, outside .state (hubconf.shared(): the registry index the
                      platform CLI reads (brunna send, complete, cap-run), the worker slots brunna test locks, the renames
                      a send to an old name follows; Path)
  network:            domains that may be reached (platform defaults + the registry's network)
  local_bind:         may listen on 127.0.0.1 (to test its own server before declaring it a service; 10-31 owner
                      confirmed in the brunna session)
  platform_commands:  the only channel across the boundary (mailbox CLI, merge gate; Path: the old form, removed once
                      the rules files use platform_cli)
  platform_cli:       the same channels as brunna subcommands on PATH (strings, e.g. "brunna send")
  capabilities:       the registry's enforce.capabilities
}
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import names  # noqa: E402
# registry needs PyYAML: imported only when computing the path policy; approvals() uses only the standard library
# (so lease-relay can import this without yaml)

HOME = pathlib.Path.home()
# each tool's cache (and uv's Python installs and tools) per repo, under its git dir: not shared, never untracked files
CACHE_ENV = {"UV_CACHE_DIR": "uv", "UV_PYTHON_INSTALL_DIR": "uv-python", "UV_TOOL_DIR": "uv-tools",
             "PIP_CACHE_DIR": "pip", "npm_config_cache": "npm", "COREPACK_HOME": "corepack"}
# credential locations every sandbox is denied whatever {settings} says (docs/design/capabilities.md, 11-08 A1 point 2);
# a runtime's own login is declared by its adapter (runtime_ports.credential_paths)
# (~/.codex stays here whichever runtimes are installed: it holds every project's Codex conversations too; the Codex
# adapter's credential_paths adds a $CODEX_HOME elsewhere; docs/design/runtime-codex.md r2)
CREDENTIALS = {"dirs": ["~/.ssh", "~/.config/gh", "~/.aws", "~/.config/gcloud", "~/.kube", "~/.codex"],
               "files": ["~/.docker/config.json", "~/.netrc", "~/.npmrc", "~/.git-credentials"]}
# what the user's shell and git run outside any sandbox (11-08 A1 point 3)
SHELL_FILES = ["~/.zshenv", "~/.zshrc", "~/.zprofile", "~/.zlogin", "~/.zlogout", "~/.bashrc", "~/.bash_profile",
               "~/.bash_login", "~/.bash_logout", "~/.profile"]
GIT_USER_DIRS, GIT_USER_FILES = ["~/.config/git"], ["~/.gitconfig"]
SERVICE_DIRS = ["~/Library/LaunchAgents", "~/.config/systemd/user"]
CLI_DIR = "~/.local/bin"   # install-cli's default folder (bin/agenthub)
DOMAINS = ["github.com", "api.github.com", "codeload.github.com", "objects.githubusercontent.com",
           "raw.githubusercontent.com", "pypi.org", "files.pythonhosted.org", "registry.npmjs.org"]


def tilde(p: pathlib.Path | str) -> str:
    s, h = str(p), str(HOME)
    return "~" + s[len(h):] if s == h or s.startswith(h + "/") else s


def project_paths(index: dict) -> dict[str, list[pathlib.Path]]:
    import registry
    out = {}
    for name, doc in index["projects"].items():
        if name == hubconf.hub_project():
            continue
        d = registry.dirs(doc)
        out[name] = [p for p in (d["workspace"], d["runtime"]) if p]
    return out


def secret_dirs() -> list[pathlib.Path]:
    """The folders holding the platform's credentials, all blocked: the current one (hubconf.secrets_dir()), plus the
    old pre-move location if it still exists and isn't the same one: the move only copies, and the old one is deleted
    only once confirmed fine (the 2026-10-26 instance-directory move), so meanwhile both hold real credentials."""
    cur = hubconf.secrets_dir()
    old = hubconf.legacy_secrets_dir()
    return [cur] + ([old] if old.exists() and old.resolve() != cur.resolve() else [])


def secret_paths(project: str, doc: dict) -> tuple[list[pathlib.Path], list[pathlib.Path]]:
    """(folders, files): the credentials a project's registry entry declares (secrets:), which none of its confined runs
    reads or writes (docs/design/project-agent-calls.md C). A relative path is taken under both its workspace and its
    runtime folder (a run works in either); ~/ and absolute ones as given; the platform's own (the hub, modules) through
    hubconf.path, as check-secrets reads them. A .env named with env_key is one file, kept whole. Not in policy(): a
    project's sessions and services read their own credentials."""
    import registry
    d = registry.dirs(doc)
    platform = project == hubconf.hub_project() or doc.get("type") == "module"
    bases = _uniq([d.get("workspace"), d.get("runtime")])
    folders, files = [], []
    for sec in doc.get("secrets") or []:
        raw = str((sec or {}).get("path") or "") if isinstance(sec, dict) else ""
        if not raw or not _secret_text(raw):   # the registry refuses such a path; never written into a rule
            continue
        places = [hubconf.path(raw)] if platform or raw.startswith(("~", "/")) else [b / raw for b in bases]
        for p in places:
            if p:
                (folders if p.is_dir() else files).append(p)
    return _uniq(folders), _uniq(files)


def secret_names(project: str, doc: dict) -> list[tuple[pathlib.Path, str]]:
    """(root, relative path): each relative secrets: path with the workspace and runtime folder it is under, to be denied
    at any depth below them too (a copy in a worktree, which a run started there reads): secret_paths names only the
    declared place."""
    import registry
    if project == hubconf.hub_project() or doc.get("type") == "module":
        return []
    d = registry.dirs(doc)
    out = []
    for sec in doc.get("secrets") or []:
        raw = str(sec.get("path") or "") if isinstance(sec, dict) else ""
        if raw and not raw.startswith(("~", "/")) and _secret_text(raw):
            out += [(b, raw.strip("/")) for b in _uniq([d.get("workspace"), d.get("runtime")])]
    return out


def _secret_text(raw: str) -> bool:
    """A secret's path holds nothing a deny rule would read as syntax (registry.SECRET_PATH, also checked there)."""
    import registry
    return bool(registry.SECRET_PATH.fullmatch(raw))


def memory_bare(name: str, doc: dict) -> pathlib.Path:
    return hubconf.path((doc.get("paths") or {}).get("memory_bare")) \
        or hubconf.p("git_origin", "~/git-origin") / f"{name}-memory.git"


def others_memory(name: str, doc: dict, index: dict) -> list[pathlib.Path]:
    """Every memory repo that isn't this project's: other projects' bare repos and copies (registered, whether they
    exist yet or not), plus whatever else is under git_origin (mirrors, the hub's bare repo, old ones) and
    memory_copies right now."""
    copies = hubconf.p("memory_copies", "~/.memory-copies")
    own = {memory_bare(name, doc), copies / name}
    out = [p for n, d in index["projects"].items() if n != name for p in (memory_bare(n, d), copies / n)]
    for root in (hubconf.p("git_origin", "~/git-origin"), copies):
        out += sorted(root.iterdir()) if root and root.is_dir() else []
    seen, res = set(), []
    for p in out:
        if p not in own and p not in seen:
            seen.add(p)
            res.append(p)
    return res


def protect() -> list[dict]:
    """The control files of every installed runtime that can name them (docs/design/mailbox-runtimes.md A1): the
    default runtime's first, as before; a runtime without the control port adds nothing (its hooks are never installed
    in a sandboxed project)."""
    import runtime_ports
    out = []
    for name in dict.fromkeys([runtime_ports.default_name(), *runtime_ports.installed()]):
        try:
            rt = runtime_ports.load(name, ports=("control",))
        except runtime_ports.Unsupported:
            continue
        out += [x for x in ({"name": n, "tree": bool(t)} for n, t in rt.control_paths()) if x not in out]
    return out


def protect_roots() -> list[dict]:
    """Each installed runtime's own folder in a project, with the entries in it that stay writable ([{name, writable}];
    the control port's optional control_root): nothing of the project's services or jobs creates or changes anything
    else under it (docs/design/runtime-versions.md section 7, V-M9)."""
    import runtime_ports
    out = []
    for name in dict.fromkeys([runtime_ports.default_name(), *runtime_ports.installed()]):
        try:
            rt = runtime_ports.load(name, ports=("control",))
        except runtime_ports.Unsupported:
            continue
        fn = getattr(rt, "control_root", None)
        got = fn() if callable(fn) else None
        if got and (x := {"name": got[0], "writable": list(got[1])}) not in out:
            out.append(x)
    return out


def protected_changes(paths) -> list[str]:
    """Of these paths (relative to a checkout), those in a runtime's own folder (protect_roots) outside the entries it
    names writable: a sandboxed project's own processes change none of them (its services by the macOS rule, its
    sessions by the denies on each entry present), so a merge or rebase that would change one stops halfway from inside
    its sandbox. Said before anything moves (merge-branch)."""
    roots = protect_roots()
    out = []
    for p in paths:
        parts = pathlib.PurePosixPath(p).parts
        if any(len(parts) > 1 and parts[0] == r["name"] and parts[1] not in r["writable"] for r in roots):
            out.append(p)
    return out


def cache_env(ws: pathlib.Path | None) -> dict[str, str]:
    return {k: str(names.repo_dir(ws / ".git") / "cache" / v) for k, v in CACHE_ENV.items()} if ws else {}


def mail_outbox(doc: dict | None) -> bool:
    """Whether a project's letters go through its outbox instead of the bus (docs/design/bus-sender.md): its sessions
    are sandboxed (enforce.sandbox, as render-project-settings decides it; the services-only exemption doesn't change
    it), so the bus file is in none of its sandboxes and the platform takes its letters from its own folder."""
    return bool(doc) and (doc.get("enforce") or {}).get("sandbox") is True


def beside(base: pathlib.Path, keeps) -> list[pathlib.Path]:
    """What is in base apart from keeps (resolved paths), level by level down to each: a deny on base that leaves keeps
    alone, written without an allow inside a deny. Only entries that exist now are listed (named under base as given)."""
    import os
    base = pathlib.Path(base)
    real = pathlib.Path(os.path.realpath(base))
    keeps = [k for k in keeps if real in k.parents]
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return []
    out = []
    for e in entries:
        if any(k == real / e for k in keeps):
            continue
        inner = [k for k in keeps if real / e in k.parents]
        out += beside(base / e, inner) if inner else [base / e]
    return out


def write_except(deny_write: list, keep: pathlib.Path) -> list[pathlib.Path]:
    """deny_write with one directory left writable (an unattended job's own state folder, its run's working directory):
    a runtime's deny can't be narrowed by an allow, so a denied place that holds keep is replaced by what sits beside the
    path down to keep, level by level (11-02: the whole hub denied a job writing its own state file). Only entries that
    exist are listed; anything else under the hub stays outside the job's allow rules."""
    keep, out = pathlib.Path(keep).resolve(), []
    for d in map(pathlib.Path, deny_write):
        base = d.resolve()
        out += beside(base, [keep]) if base in keep.parents else [d]
    return out


def notes_dir(project: str) -> pathlib.Path | None:
    """Where a project's memory notes are, by absolute path: what a mailbox handler or a fork may write, wherever it runs
    (the hub's are in its main checkout, not committed: memory: false). None when it has none, or when the folder is a
    link: a project's links don't decide what the platform's runs may write."""
    ws = hubconf.MAIN if project == hubconf.hub_project() else hubconf.workspace(project)
    if not ws:
        return None
    mem = ws / ".agent-memory"
    if mem.is_symlink() or not mem.is_dir() or (mem / "memory").is_symlink():
        return None
    return mem.resolve() / "memory"


def run_roots(project: str) -> list[pathlib.Path]:
    """Where a confined run of this project may follow a link to: its workspace, its runtime folder and the folder its
    notes are in (the .agent-memory a worktree's link leads to; resolved; a folder the registry doesn't give is left
    out), and what the owner let it write beyond them (allow_write, owner-only). The hub's own runs: its main checkout
    too, whose worktrees its forks run in. Not hubconf.project_roots (where projects live)."""
    import registry
    doc = registry.refresh()["projects"].get(project) or {}
    d = registry.dirs(doc) if doc else {}
    notes = notes_dir(project)
    extra = [hubconf.path(p) for p in doc.get("allow_write") or []]
    if project == hubconf.hub_project():
        extra.append(hubconf.MAIN)
    return [p.resolve() for p in (d.get("workspace"), d.get("runtime"), notes.parent if notes else None, *extra) if p]


def run_state_denied(project: str, keep: pathlib.Path | None = None) -> list[pathlib.Path]:
    """What a confined run of the hub may not read: its working directory is the hub root, where other projects' runs left
    fork logs and reports, and it could quote them to a sandboxed sender. keep: the run's own job folder. Other projects'
    runs have no such place in their working directory."""
    if project != hubconf.hub_project():
        return []
    places = [hubconf.STATE, hubconf.LOGS]
    return write_except(places, keep) if keep else places


def readable() -> list[pathlib.Path]:
    """policy.read: the platform state sandboxed code reads (the platform CLI inside a session, service or job), kept out
    of .state so that .state can be denied whole (docs/design/capabilities.md 11-08, A1 point 1)."""
    return [hubconf.shared()]


def _uniq(paths) -> list[pathlib.Path]:
    return list(dict.fromkeys(p for p in paths if p))


def platform_state(project: str, keep=()) -> list[pathlib.Path]:
    """What no sandboxed project reads of the hub's (policy platform_state): .state, .logs, the data dir, the evals dir (its
    configured path: evals_dir() would make it), each whole. keep: a confined job's own folder (in .state/agent-jobs) and
    the notes it writes: a folder holding one is listed beside it instead (beside), only in that run's settings. One that
    is or holds the hub root is left out (the old layout's data dir is the hub root). The hub's own sessions and leases
    read its state; its confined runs hide .state and .logs already (run_state_denied)."""
    import os
    if project == hubconf.hub_project():
        return []
    # a throwaway state (an override, a test's) holds its shared part inside it: left out the same way
    keeps = [pathlib.Path(os.path.realpath(k)) for k in [*keep, *readable()] if k]
    hub = {pathlib.Path(os.path.realpath(r)) for r in (hubconf.ROOT, hubconf.MAIN)}
    out = []
    for d in (hubconf.STATE, hubconf.LOGS, hubconf.data_dir(), hubconf.evals_path()):
        r = pathlib.Path(os.path.realpath(d)) if d else None
        if not r or any(h == r or r in h.parents for h in hub):
            continue
        out += beside(d, keeps) if any(r in k.parents for k in keeps) else [d]
    return _uniq(out)


def credentials() -> tuple[list[pathlib.Path], list[pathlib.Path]]:
    """(dirs, files): {settings}'s credentials, CREDENTIALS, and the installed runtimes' own (runtime_ports)."""
    import runtime_ports
    rt_dirs, rt_files = runtime_ports.credential_paths()
    dirs = [*hubconf.get("credentials.dirs", []), *CREDENTIALS["dirs"], *rt_dirs]
    files = [*hubconf.get("credentials.files", []), *CREDENTIALS["files"], *rt_files]
    return _uniq(map(hubconf.path, dirs)), _uniq(map(hubconf.path, files))


def platform_python() -> list[pathlib.Path]:
    """The platform's Python, found the way bin/py finds it (names.env("PYTHON"), else ~/.local/share/<share_dir>/python),
    every spelling's share folder whole, and the interpreter its pyvenv.cfg names (whose lib is on every platform
    script's path; sys.base_prefix when the file can't be read)."""
    import names
    share = [HOME / ".local" / "share" / n for n in names.all("share_dir")]
    py = names.env("PYTHON")
    found = [s / "python" for s in share if (s / "python" / "pyvenv.cfg").exists()]
    venv = pathlib.Path(py).parent.parent if py else (found or [share[0] / "python"])[0]
    interp = pathlib.Path(sys.base_prefix)
    try:
        for line in (venv / "pyvenv.cfg").read_text().splitlines():
            k, _sep, v = line.partition("=")
            if k.strip() == "home" and v.strip():
                interp = pathlib.Path(v.strip()).parent
    except OSError:
        pass
    return _uniq([*share, venv, interp])


def cli_dirs() -> list[pathlib.Path]:
    """The folder install-cli writes the wrapper to (by default CLI_DIR), whole: a new program there shadows the user's;
    and wherever the wrapper on PATH is."""
    out = [hubconf.path(CLI_DIR)]
    try:
        import upgrade
        w = upgrade.cli_wrapper()
    except Exception:   # noqa: BLE001 (no wrapper found is no reason to fail the policy)
        w = None
    return _uniq([*out, *([w.parent] if w else [])])


def uv_files() -> list[pathlib.Path]:
    """uv itself, as setup-python finds it (on PATH, else ~/.local/bin/uv)."""
    import shutil
    return [pathlib.Path(shutil.which("uv") or HOME / ".local" / "bin" / "uv")]


def instance_dirs() -> list[pathlib.Path]:
    """The instance dir (config, data, secrets), and each of the three that an override puts elsewhere."""
    home = hubconf.instance_home()
    parts = [hubconf.config_dir(), hubconf.data_dir(), hubconf.secrets_dir()]
    return _uniq([home, *[p for p in parts if not home or home not in p.parents]])


def policy(name: str, doc: dict, index: dict) -> dict:
    import registry
    d = registry.dirs(doc)
    outbox = mail_outbox(doc)
    others = [p for n, ps in project_paths(index).items() if n != name for p in ps]
    copies = hubconf.p("memory_copies", "~/.memory-copies")
    cred_dirs, cred_files = credentials()
    return {
        "workspace": d["workspace"],
        "write": ([tilde(d["runtime"])] if d["runtime"] else []) + ([] if outbox else [tilde(hubconf.BUS_DIR)])
                 + list(doc.get("allow_write") or []),
        "env": cache_env(d["workspace"]),
        "protect": protect(),
        "protect_roots": protect_roots(),
        "other_projects": others + others_memory(name, doc, index),
        "deny_write": _uniq([hubconf.ROOT, hubconf.p("git_origin"), copies, *instance_dirs(),
                             *([hubconf.BUS_DIR] if outbox else []), *platform_python(),
                             *cli_dirs(), hubconf.nvm_dir(), *map(hubconf.path, GIT_USER_DIRS + SERVICE_DIRS)]),
        "deny_write_files": _uniq([*uv_files(), *map(hubconf.path, SHELL_FILES + GIT_USER_FILES)]),
        "deny_read": [copies / "*" / "memory", *secret_dirs(), *([hubconf.BUS_DIR] if outbox else [])],
        "credentials": cred_dirs,
        "credential_files": cred_files,
        "platform_state": platform_state(name),
        # the registry index; the machine's worker slots (bin/test-runner.py: locked read-only, created by the platform);
        # the renames record (a letter to a renamed project's old name)
        "read": readable(),
        "network": DOMAINS + list(doc.get("network") or []),
        "local_bind": True,
        "platform_commands": [hubconf.ROOT / "bin" / "hubbus", hubconf.ROOT / "bin" / "merge-branch"],
        "platform_cli": platform_cli(),
        "capabilities": list((doc.get("enforce") or {}).get("capabilities") or []),
    }


# The platform commands a project's session uses (agenthub on PATH; the same list as bin/agenthub's
# SESSION_COMMANDS): permissions let only these subcommands through, not agenthub's admin commands (config apply,
# onboard…)
MAIL_SUBCOMMANDS = ("send", "inbox", "show", "ack", "done", "thread", "open-questions")
# pass: a session's word, never a mailbox agent's (not in MAIL_SUBCOMMANDS; a confined run has no mail command at all)
SESSION_SUBCOMMANDS = (*MAIL_SUBCOMMANDS, "pass", "merge-branch", "test", "cap-run", "complete", "agent")


def platform_cli() -> list[str]:
    """The platform's commands a sandboxed project's sessions run (a confined run has none: its mail is the platform's
    tools, docs/design/confined-mail-tools.md)."""
    # both names of the CLI (docs/design/rename-brunna.md): rules files written by either release name one of them
    return [f"{cli} {c}" for cli in names.all("cli") for c in SESSION_SUBCOMMANDS]


# ---------- the environment's tool servers in an unattended run (docs/design/evaluators.md step 0) ----------
# A judgment point declaring `tool_servers: allowed_only`: when its run loads the tool servers configured in its
# environment (a registry job's connectors: true needs the account's connectors, so it can't be held to the platform's
# own servers), every server it would load whose tools its allow list doesn't name is denied, all of its tools.
# What it would load is found by the runtime for that very run (the user's, the project's, plugins' and the account's
# servers), never kept by hand: a server added tomorrow is denied tomorrow. When the runtime can't tell, the run
# doesn't happen (llm.py).

def tool_server_denials(loaded, named) -> list[str]:
    """loaded: the servers this run would load from its environment; named: the servers its allow list names a tool
    of (both in the runtime's own server ids) → the servers to deny whole."""
    named = set(named)
    return sorted({s for s in loaded if s not in named})


def for_project(name: str) -> dict:
    import registry
    index = registry.refresh()
    return policy(name, index["projects"][name], index)


# ---------- which actions need a person to approve (approvals; docs/design/lease-contract-neutral.md) ----------
# Rule values:
#   allow       let through directly      deny        reject directly (no asking)
#   ask         ask the owner (phone); "allow for this whole lease" is possible
#   ask_once    ask the owner, every time ("allow for this whole lease" only counts for this once)
#   inside      let through inside the project folder, reject outside (no asking; paths are resolved to real paths
#               first)
#   listed      open_web domains in web_allowed are let through, others ask; no_session_domains may not be "allowed
#               for this whole lease"
# The work mode decided by the owner on 10-25: writing files inside the folder isn't asked, commands are asked every
# time. unattended: an agent run the platform started with nobody to ask (docs/design/runtime-acp.md); it belongs in
# the throwaway worktree, so these rules are what the agent is told, not the guarantee ("inside" for a read or search
# that names no place means where it was started).
APPROVALS = {
    "discuss": {"read": "allow", "search": "allow", "ask_user": "ask", "open_web": "listed",
                "write_file": "deny", "run_command": "deny", "other": "deny"},
    "work": {"read": "allow", "search": "allow", "ask_user": "ask", "open_web": "listed",
             "write_file": "inside", "run_command": "ask_once", "other": "deny"},
    "unattended": {"read": "inside", "search": "inside", "ask_user": "deny", "open_web": "deny",
                   "write_file": "inside", "run_command": "deny", "other": "deny"},
}


def approvals(name: str, mode: str, doc: dict | None = None) -> dict:
    """{mode, rules: {kind: rule}, web_allowed: [domains], no_session_domains: [domains]}: each runtime hooks it into
    its own permission mechanism."""
    doc = doc if doc is not None else (hubconf.projects().get(name) or {})
    return {"mode": mode, "rules": dict(APPROVALS["work" if mode == "work" else "discuss"]),
            "web_allowed": list(doc.get("network") or []),
            "no_session_domains": list(hubconf.get("lease.no_session_domains", []))}
