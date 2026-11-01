"""The single entry point for platform settings and the project registry (standard library only; the platform's
Python is bin/py, 3.13 at least).

New code always takes paths, names and the owner from here; do not hard-code ~/agent-hub, ~/services or project names.
  - hub root: the parent of the bin/ this file is in; the environment variable AGENT_HUB_ROOT can override it
  - settings: <config>/hub.json (instance directory, see below)
  - instance directory (docs/design/new-user.md 3.1): the user's own things in three parts -- config (hub.json,
    registry, schedule.yaml…; the config repo), data (digest, todo, review-log, reflect…; not in git, relies on
    backup), secrets. Where they are is decided by **one pointer file**: <hub>/.hub-home holds the instance
    directory's path -> <instance>/config, <instance>/data, <instance>/secrets; no such file = the old layout (all
    three are the hub root, secrets is <hub>/.secrets), i.e. how things were before the move. The move writes the
    pointer file last; deleting it falls back.
    Individual overrides: AGENT_HUB_HOME (= the pointer file's content), AGENT_HUB_CONFIG, AGENT_HUB_DATA,
    AGENT_HUB_SECRETS (e.g. the config repo's pre-commit exports the index to a temporary directory and points only
    config there).
    .state and .logs are not in the instance: like the bus (bus_dir) and the platform's Python they are per-machine
    run state and stay in the hub root (the .logs path is written into every service's plist; moving it means
    restarting every service).
    A linked worktree of the hub (MAIN != ROOT) takes .hub-home, .state and .logs from its main checkout, found
    through the .git pointer files; its own copy only supplies the code. worktree_lookup() says when that happened,
    for the scripts that must not rebuild live state from a worktree. An explicit AGENT_HUB_ROOT skips the lookup.
  - The platform's own registry entry (<hub>/projects/agent-hub.yaml) is part of the platform: registry reads it
    wherever registry_dir is
  - Project registry: <registry_dir>/<name>.yaml (YAML edited by people); every tick the scheduler validates it with
    registry.py and writes <hub>/.state/registry/index.json -- this module reads that JSON (no PyYAML needed); if it
    has not been produced yet (new machine, the laptop), fall back to roster(): names and paths read straight from
    the YAML files

For shell scripts ("$HUB/bin/py" hubconf.py …):
  hubconf.py ideas     names of idea sandboxes (registry type: idea), one per line
  hubconf.py get <key> a single value from hub.json (dot-separated, e.g. github.owner), for shell scripts
  hubconf.py roster    one line per project name<TAB>workspace<TAB>origin<TAB>memory_bare, no header line;
                       workspace with ~ expanded, (native) when there is no origin, memory_bare prefixed with
                       hub.json's memory_host:
  hubconf.py test-config-inputs
                       the instance files tests may read, <variable><TAB><file> per line (hub.json
                       tests.config_inputs)
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import i18n  # noqa: E402

_ = i18n.domain(__file__)

ROOT = pathlib.Path(os.environ.get("AGENT_HUB_ROOT") or pathlib.Path(__file__).resolve().parents[1])


def _main_checkout(root: pathlib.Path) -> pathlib.Path:
    """The main checkout of the repo whose linked worktree `root` is (read from the .git pointer files, no git
    subprocess: this module is also imported by the system Python), or `root` itself. A worktree has no .hub-home,
    .state or .logs of its own; they are the machine's, in the main checkout (docs/design/test-waiting.md A1)."""
    import gitdirs
    found = gitdirs.dirs(root)
    if found and found.top == root.resolve() and found.common.name == ".git":
        return found.common.parent
    return root


# An explicit AGENT_HUB_ROOT (the commit gate's export, every test) is taken as it is: no lookup
MAIN = ROOT if os.environ.get("AGENT_HUB_ROOT") else _main_checkout(ROOT)
POINTER = MAIN / ".hub-home"
_cfg: dict | None = None


def instance_home() -> pathlib.Path | None:
    """The instance directory; None = the old layout (everything in the hub root)."""
    v = os.environ.get("AGENT_HUB_HOME")
    if not v and POINTER.is_file():
        v = POINTER.read_text().strip()
    return pathlib.Path(os.path.expanduser(v)) if v else None


def _part(env: str, sub: str, legacy: pathlib.Path) -> pathlib.Path:
    if os.environ.get(env):
        return pathlib.Path(os.path.expanduser(os.environ[env]))
    home = instance_home()
    return home / sub if home else legacy


def config_dir() -> pathlib.Path:
    """Where config lives (hub.json, registry, mirror-list.txt, services.tsv, schedule.yaml)."""
    return _part("AGENT_HUB_CONFIG", "config", ROOT)


def data_dir() -> pathlib.Path:
    """Where data lives (digest/, reflect/, todo.md, index.md, review-log.md, openclaw-migration-status.md,
    legacy-paths.tsv)."""
    return _part("AGENT_HUB_DATA", "data", ROOT)


def config_file(name: str) -> pathlib.Path:
    return config_dir() / name


def data_file(name: str) -> pathlib.Path:
    return data_dir() / name


def config() -> dict:
    global _cfg
    if _cfg is None:
        p = config_file("hub.json")
        _cfg = json.loads(p.read_text()) if p.exists() else {}
    return _cfg


def path(value: str | None, base: pathlib.Path | None = None) -> pathlib.Path | None:
    """Expand ~; a relative path is taken from base (by default the config directory hub.json is in; in the old layout
    that is the hub root). `.secrets/<name>` always means the platform's secrets directory (secrets_dir()), wherever
    hub.json is."""
    if not value:
        return None
    if base is None and value.startswith(".secrets/"):
        return secrets_dir() / value[len(".secrets/"):]
    p = pathlib.Path(os.path.expanduser(value))
    return p if p.is_absolute() else (base or config_dir()) / p


def get(key: str, default=None):
    """A dot-separated key: get("owner.tag")."""
    cur = config()
    for k in key.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def p(key: str, default: str | None = None) -> pathlib.Path | None:
    return path(get(key, default))


def secrets_dir() -> pathlib.Path:
    """The platform's secrets directory (not in version control, backed up encrypted every night). Code always takes it
    from here instead of building the path itself."""
    return _part("AGENT_HUB_SECRETS", "secrets", legacy_secrets_dir())


def legacy_secrets_dir() -> pathlib.Path:
    """Where secrets were before the move (<hub>/.secrets). The move only copies and deletes after checking, so during
    that time it still holds real secrets -- the sandbox's read block has to cover it too (policy.secret_dirs())."""
    return ROOT / ".secrets"


def host(role: str) -> str:
    """Machine role -> ssh name; "" = this machine (one machine does everything). For now there is only one role,
    server: the machine running the hub (memory bare repos, scheduler and workspaces are all on it). Scripts on the
    other machine (the laptop used as a remote) ssh over; on the server itself they act locally. Without hosts.server
    the old memory_host is used."""
    if role != "server":
        raise KeyError(role)
    return get("hosts.server") or get("memory_host") or ""


def nvm_bin() -> pathlib.Path | None:
    """The bin/ of the newest installed version matching nvm's default version (~/.nvm/alias/default, e.g. 24, lts/*,
    node, v24.19.0).

    node (and the claude installed under it) is managed only by nvm (decided by the owner on 2026-10-24, after
    Homebrew's node was deleted by autoremove and a LINE bot was down for a day and a half). Background services and
    scheduled jobs have no shell nvm setup, so this turns it into a full path; after an nvm upgrade the next
    render-services switches over and restarts the services."""
    nvm = pathlib.Path(os.environ.get("NVM_DIR") or pathlib.Path.home() / ".nvm")
    versions = nvm / "versions" / "node"
    if not versions.is_dir():
        return None
    spec = "default"
    for _hop in range(5):  # an alias can point to another alias (lts/* -> lts/jod -> v22…)
        f = nvm / "alias" / spec
        if not f.is_file():
            break
        spec = f.read_text().strip()

    def ver(p: pathlib.Path) -> tuple:
        return tuple(int(x) for x in p.name.lstrip("v").split(".") if x.isdigit())
    installed = sorted((p for p in versions.iterdir() if (p / "bin" / "node").exists()), key=ver)
    if spec not in ("node", "stable"):
        want = tuple(int(x) for x in spec.lstrip("v").split(".") if x.isdigit())
        installed = [p for p in installed if want and ver(p)[:len(want)] == want]
    return installed[-1] / "bin" if installed else None


# The PATH under launchd (when a plist sets no PATH; render-services also writes this into plists): commands must be
# resolved against this, not the PATH of the shell running the check. The nvm default version's bin comes first
# (node, claude).
LAUNCHD_PATH = ":".join([*([str(nvm_bin())] if nvm_bin() else []), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"])


def expand_env(value: str) -> str:
    """Replace $PLATFORM_PATH in a registry service's env values with LAUNCHD_PATH (for when it needs both Homebrew and
    nvm's node/claude)."""
    return value.replace("$PLATFORM_PATH", LAUNCHD_PATH)


def tool(name: str) -> str:
    """Full path of a command an unattended program runs (claude etc.): first by the current PATH, then by
    LAUNCHD_PATH; not found: returned as it is."""
    import shutil
    return shutil.which(name, path=f"{os.environ.get('PATH', '')}:{LAUNCHD_PATH}") or name

# tests use another
STATE = pathlib.Path(os.environ["AGENT_HUB_STATE"]) if os.environ.get("AGENT_HUB_STATE") else MAIN / ".state"
LOGS = MAIN / ".logs"


def worktree_lookup() -> bool:
    """This copy of the platform is a linked worktree that found the machine's live state through the main checkout:
    scripts that rebuild that state (registry refresh, config apply, the scheduler) must not run from here, where
    they would build it from a half-edited copy. An explicit AGENT_HUB_STATE (config-check --staged) is not live."""
    return MAIN != ROOT and not os.environ.get("AGENT_HUB_STATE")
REGISTRY_INDEX = STATE / "registry" / "index.json"


# The event bus has its own directory (hub.json bus_dir), in no project's sandbox: a sandboxed project's letters go
# through its outbox (docs/design/bus-sender.md), and the hooks that deliver letters run outside the sandbox.
BUS_DIR = p("bus_dir", ".state/bus")
BUS_LOG = BUS_DIR / "hubbus.log"
_OLD_BUS = [STATE / "bus" / "bus.db", STATE / "bus.db"]  # where it used to be (searched in order)


def bus_db() -> pathlib.Path:
    """The bus's SQLite file. Not yet at the new location but present at an old one -> move it: one connection takes
    the RESERVED lock (BEGIN IMMEDIATE) to hold off other writes, another connection reads it out with the backup API
    (a connection that holds the lock and then backs itself up deadlocks), and the lock is released only after the
    rename."""
    new = BUS_DIR / "bus.db"
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    old = next((o for o in _OLD_BUS if o.exists() and o != new), None)
    if new.exists() or old is None:
        return new
    import sqlite3
    lock = sqlite3.connect(old, timeout=60, isolation_level=None)
    try:
        lock.execute("BEGIN IMMEDIATE")
        if not new.exists():  # another process may just have moved it
            tmp = BUS_DIR / "bus.db.migrating"
            src, dst = sqlite3.connect(old, timeout=60), sqlite3.connect(tmp)
            src.backup(dst)
            dst.close()
            src.close()
            tmp.replace(new)
            for suffix in ("", "-wal", "-shm"):
                f = pathlib.Path(str(old) + suffix)
                if f.exists():
                    f.rename(pathlib.Path(str(old.parent / "bus.db.moved") + suffix))
        lock.execute("ROLLBACK")
    finally:
        lock.close()
    oldlog = old.parent / "hubbus.log" if old.parent.name == "bus" else LOGS / "hubbus.log"
    if oldlog.exists() and not oldlog.is_symlink() and not BUS_LOG.exists():
        oldlog.rename(BUS_LOG)
    link = LOGS / "hubbus.log"  # whatever still reads .logs/hubbus.log (other sessions' monitors) can still read it
    if link.is_symlink() or not link.exists():
        link.unlink(missing_ok=True)
        LOGS.mkdir(parents=True, exist_ok=True)   # a fresh checkout (or the commit gate's copy) has no .logs yet
        link.symlink_to(BUS_LOG)
    return new


LANGUAGES = ("en", "zh-Hant")


def lang_ui() -> str:
    """The language the platform shows the owner (CLI output, status page, alerts, headings): hub.json `language.ui`,
    or AGENT_HUB_LANG_UI. Default en (docs/design/i18n.md)."""
    return _lang("AGENT_HUB_LANG_UI", "language.ui")


def lang_content() -> str:
    """The language models and agents write in (STATE.md, notes, digests, letters): hub.json `language.content`, or
    AGENT_HUB_LANG_CONTENT. Default en."""
    return _lang("AGENT_HUB_LANG_CONTENT", "language.content")


def _lang(env: str, key: str) -> str:
    """A wrong value in the environment is an error, not a quiet fallback to en (a test that sets zh-hant would otherwise
    pass in English). A wrong value in hub.json gives en: config-check reports it, and must not crash on it while it
    formats its own messages."""
    v = os.environ.get(env)
    if v:
        if v not in LANGUAGES:
            raise ValueError(f"{env}={v!r}: the languages are {', '.join(LANGUAGES)}")
        return v
    v = get(key)
    return v if v in LANGUAGES else "en"


def evals_dir() -> pathlib.Path | None:
    """Where eval cases and runs live (docs/design/evaluators.md, i18n.md): hub.json `evals_dir`, or AGENT_HUB_EVALS.
    None = not set up, nothing is recorded. Created 0700; keep it out of git and out of the backup."""
    d = pathlib.Path(os.environ["AGENT_HUB_EVALS"]) if os.environ.get("AGENT_HUB_EVALS") else p("evals_dir")
    if d is None:
        return None
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    d.chmod(0o700)
    return d


def registry_dir() -> pathlib.Path:
    return p("registry_dir", "projects")


def platform_registry() -> pathlib.Path:
    """The platform's own registry entry (core jobs, services, secret declarations, contracts): part of the platform,
    in the platform repo, not in the user's registry_dir."""
    return ROOT / "projects" / f"{hub_project()}.yaml"


def owner_address() -> str:
    return get("owner.address", "owner")


def owner_addresses() -> set[str]:
    """The owner's address and aliases (old letters use knas; both are accepted)."""
    return {owner_address(), *get("owner.aliases", [])}


def owner_tag() -> str:
    return get("owner.tag", "Owner")


def owner_side() -> str:
    """Who the Principal Handoff's principal side is (hub.json owner_side): assistant = the assistant set in hub.json
    assistant (the bridge exchanges with its API); builtin = the platform's minimal built-in owner side (bin/owner.py).
    Not set: assistant if there is assistant.api, otherwise builtin."""
    v = get("owner_side")
    if v in ("assistant", "builtin"):
        return v
    return "assistant" if get("assistant.api") else "builtin"


def party(role: str, project: str | None = None) -> str:
    """Principal Handoff participant identifiers (spec core §1, R6; a record's by and authority.decider use these).
    principal / principal_side are read from hub.json (owner.principal_id, assistant.party_id); requester and agent
    are project:<project> (project must be given); registrar is <platform>:registrar (one registrar serves every
    project)."""
    if role == "principal":
        return get("owner.principal_id") or f"person:{owner_address()}"
    if role == "principal_side":
        if owner_side() == "builtin":
            return f"{hub_project()}:owner-side"
        return get("assistant.party_id") or get("assistant.project") or "assistant"
    if role in ("requester", "agent"):
        if not project:
            raise ValueError(_("party({role}) needs a project", role=repr(role)))
        return f"project:{project}"
    if role == "registrar":
        return f"{hub_project()}:registrar"
    raise ValueError(_("unknown role {role}", role=repr(role)))


def former_names(project: str) -> list[str]:
    """The names this project had before being renamed (from .state/project-renames.json, counting only finished
    renames, stage done). Old letters on the bus and records the assistant sends may still use an old name (e.g.
    idea-1 upgraded and renamed to garden-app)."""
    try:
        renames = json.loads((STATE / "project-renames.json").read_text())
    except (OSError, ValueError):
        return []

    def now(name: str) -> str:
        seen = set()
        while name in renames and name not in seen and (renames[name] or {}).get("stage") == "done":
            seen.add(name)
            name = renames[name]["new"]
        return name
    return sorted(old for old in renames if old != project and now(old) == project)


def principal_is_legal_person() -> bool:
    return bool(get("owner.legal_person", False))


def assistant_name() -> str:
    """The personal assistant's name as people see it (hub.json assistant.name); a generic term when not set.
    The default works as a noun phrase mid-sentence ("sent to the personal assistant"), as a configured name does."""
    return get("assistant.name") or _("the personal assistant")


def owner_alias() -> str:
    """The address agents are told to mail the owner at (`agenthub send --to …`): the first of owner.aliases, else the
    owner's address. Templates name it as {{owner_alias}} (bin/prompts.py fills them)."""
    aliases = get("owner.aliases") or []
    return aliases[0] if aliases else owner_address()


_PATHS_LINE = re.compile(r"^\s+(\w+):\s*(.*?)\s*(?:#.*)?$")


def roster() -> dict[str, dict]:
    """The projects in the registry (except the platform itself) -> their paths (raw strings, ~ not expanded).

    Reads the file names and `paths:` blocks of <registry_dir>/*.yaml directly: no validation, no PyYAML, no .state
    cache, so it works on a new machine (recover) and on the laptop. For the full validated registry use projects()."""
    out = {}
    d = registry_dir()
    for f in sorted(d.glob("*.yaml")) if d and d.is_dir() else []:
        if f.stem != hub_project():
            out[f.stem] = roster_paths(f.read_text())
    return out


def roster_paths(text: str) -> dict:
    """One registry file's `paths:` block (parsed line by line, block style only; registry.py checks during validation
    that it matches what YAML reads)."""
    paths, inside = {}, False
    for line in text.splitlines():
        if line.split("#")[0].rstrip() == "paths:":
            inside = True
        elif inside and (m := _PATHS_LINE.match(line)):
            paths[m.group(1)] = m.group(2).strip("'\"") or None
        elif inside and line.strip() and not line.lstrip().startswith("#"):
            break
    return paths


def roster_sandboxed(text: str) -> bool:
    """`enforce: {sandbox: true}` in one registry file, block or flow style (line by line, like roster_paths); every
    spelling YAML reads as true counts, so the fallback never says "not sandboxed" where the registry says it is."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        bare = line.split("#")[0].rstrip()
        if re.match(r"^enforce:\s*\{.*\bsandbox:\s*['\"]?(true|yes|on)\b", bare, re.I):
            return True
        if bare == "enforce:":
            for sub in lines[i + 1:]:
                if sub.strip() and not sub.startswith((" ", "\t")):
                    break
                if re.match(r"^\s+sandbox:\s*['\"]?(true|yes|on)['\"]?\s*(#.*)?$", sub, re.I):
                    return True
    return False


_TYPE_LINE = re.compile(r"^type:\s*['\"]?(\w+)['\"]?\s*(?:#.*)?$")


def ideas() -> list[str]:
    """Projects registered with `type: idea` (idea sandboxes). Like roster(), reads the top-level `type:` line by line;
    no PyYAML needed."""
    d = registry_dir()
    out = []
    for f in sorted(d.glob("*.yaml")) if d and d.is_dir() else []:
        for line in f.read_text().splitlines():
            if (m := _TYPE_LINE.match(line)):
                if m.group(1) == "idea":
                    out.append(f.stem)
                break
    return out


def projects() -> dict[str, dict]:
    """Name -> validated registry entry (dict). If the cache has not been produced yet (new machine, the laptop),
    roster() supplies the minimal fields."""
    if REGISTRY_INDEX.exists():
        try:
            return json.loads(REGISTRY_INDEX.read_text())["projects"]
        except (ValueError, KeyError):
            pass
    out = {}
    d = registry_dir()
    for f in sorted(d.glob("*.yaml")) if d and d.is_dir() else []:
        if f.stem != hub_project():
            text = f.read_text()
            # whether it is sandboxed must not be lost here: everything that decides to wrap a project's work reads it
            out[f.stem] = {"name": f.stem, "paths": roster_paths(text),
                           **({"enforce": {"sandbox": True}} if roster_sandboxed(text) else {})}
    return out


def reflection_private() -> set[str] | None:
    """Projects whose privacy.reflection is not include (stats_only / exclude): their conversation content (summaries
    included) is not sent to cloud models. Returns None when the registry has not been produced or cannot be read --
    the caller must treat that as "no content may be sent for any project", not as "no restriction"."""
    if not REGISTRY_INDEX.exists():
        return None
    try:
        projs = json.loads(REGISTRY_INDEX.read_text())["projects"]
    except (ValueError, KeyError):
        return None
    return {n for n, d in projs.items() if (d.get("privacy") or {}).get("reflection", "include") != "include"}


def workspace(name: str) -> pathlib.Path | None:
    if name == hub_project():
        return ROOT
    info = projects().get(name)
    ws = (info or {}).get("paths", {}).get("workspace")
    return path(ws) if ws else None


def capabilities() -> dict[str, dict]:
    """The capabilities the platform has (docs/design/capabilities.md): hub.json's capabilities whose
    capabilities/<name>.sb exists."""
    cat = {k: v for k, v in (get("capabilities") or {}).items() if not k.startswith("_") and isinstance(v, dict)}
    return {k: v for k, v in cat.items() if (ROOT / "capabilities" / f"{k}.sb").is_file()}


def code_stamp() -> float:
    """When the code under bin/ was last changed. A long-running service records it at start; once it changes, the
    service exits at a quiet moment and lets launchd restart it -- otherwise the old modules it loaded get mixed with
    the new ones on disk (on 2026-10-25 lease-runner and cap-runner each broke once because of this)."""
    stamps = [f.stat().st_mtime for f in (ROOT / "bin").iterdir() if f.is_file()]
    return max(stamps) if stamps else 0.0


PY_SHEBANG = "#!/bin/sh -c exec"   # first line of platform Python scripts (bin/py header): only the
                                   # macOS kernel splits arguments by it


def is_platform_python(script: pathlib.Path) -> bool:
    try:
        with open(script, "rb") as f:
            return f.read(len(PY_SHEBANG)) == PY_SHEBANG.encode()
    except OSError:
        return False


def launch(script: pathlib.Path | str) -> list[str]:
    """The argv that launches one of the platform's scripts. Platform Python scripts always go through the current
    interpreter (sys.executable = the platform's Python), not their first line: that line works only on macOS (the
    Linux kernel takes everything after #! as one argument). Shell scripts are called directly as usual."""
    script = pathlib.Path(script)
    return [sys.executable, str(script)] if is_platform_python(script) else [str(script)]


def sh_py(path_expr: str) -> str:
    """A platform Python script written into a shell string (hook commands and the like): on macOS it is called directly
    as before (output identical to before, character for character); on other systems the py from the same folder is
    put in front. path_expr is the path as written in shell (e.g. ~/agent-hub/bin/hubbus)."""
    if sys.platform == "darwin":
        return path_expr
    return f"{path_expr.rsplit('/', 1)[0]}/py {path_expr}"


def login_shell() -> str:
    """The login shell that runs jobs and test commands (`<shell> -lc <command>`, reading the user's PATH, nvm and the
    like): always /bin/zsh on macOS (as before); on other systems bash when there is no zsh, otherwise sh."""
    for sh in ("/bin/zsh", "/bin/bash", "/usr/bin/bash"):
        if os.access(sh, os.X_OK):
            return sh
    return "/bin/sh"


_SERVICE_MANAGER: list = []


def service_manager() -> str | None:
    """Which service manager this machine uses for the scheduler, the watchdog and projects' long-running services:
    macOS = launchd; Linux with a systemd user level (`systemctl --user` works) = systemd (bin/systemd_units.py).
    Neither (e.g. a container) = None: install, status and reload all say plainly "this system has no service
    manager" instead of pretending it is running."""
    if not _SERVICE_MANAGER:
        if sys.platform == "darwin":
            _SERVICE_MANAGER.append("launchd")
        else:
            import subprocess
            try:
                ok = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True,
                                    timeout=10).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
            _SERVICE_MANAGER.append("systemd" if ok else None)
    return _SERVICE_MANAGER[0]


def no_service_manager() -> str:
    """Why nothing runs by itself on this system (service_manager() is None)."""
    return _("this system has no supported service manager (neither macOS launchd nor the Linux systemd user level): the "
             "scheduler, the watchdog and projects' long-running services will not run by themselves")


def process_cwds() -> list[tuple[int, str, str]]:
    """(pid, program name, working directory): every process's working directory. lsof when there is one (macOS
    always has it); otherwise read /proc (Linux often has no lsof); neither = empty (the caller says it could not
    find out)."""
    import shutil
    import subprocess
    if shutil.which("lsof"):
        r = subprocess.run(["lsof", "-d", "cwd", "-Fpcn"], capture_output=True, text=True)
        out, pid, name = [], 0, ""
        for line in r.stdout.splitlines():
            tag, val = line[:1], line[1:]
            if tag == "p":
                pid, name = int(val) if val.isdigit() else 0, ""
            elif tag == "c":
                name = val
            elif tag == "n" and val.startswith("/"):
                out.append((pid, name, val))
        return out
    proc = pathlib.Path("/proc")
    out = []
    for d in proc.glob("[0-9]*") if proc.is_dir() else []:
        try:
            out.append((int(d.name), (d / "comm").read_text().strip(), os.readlink(d / "cwd")))
        except OSError:
            continue
    return out


def cwd_users(path: str | pathlib.Path) -> list[str]:
    """Processes whose working directory is in this folder (subfolders included): "program name(pid)"."""
    root = os.path.realpath(path)
    return sorted({f"{name}({pid})" for pid, name, cwd in process_cwds()
                   if cwd == root or cwd.startswith(root.rstrip("/") + "/")})


def test_config_inputs() -> list[tuple[str, pathlib.Path]]:
    """The instance files tests may read (hub.json tests.config_inputs, docs/design/test-waiting.md A1/B1): (the
    variable the tests find it in, the file in the config directory). Bare file names only; anything else is skipped.
    A file listed here that is missing fails the commit gate; one not listed is not passed in, and the test that
    needs it skips (a deployment without a private-names list)."""
    out = []
    for e in (get("tests.config_inputs") or []):
        f, env = (e or {}).get("file"), (e or {}).get("env")
        if f and env and "/" not in f and f not in (".", "..") and re.fullmatch(r"[A-Z][A-Z0-9_]*", env):
            out.append((env, config_dir() / f))
    return out


def hub_project() -> str:
    """The platform's own name in the registry."""
    return "agent-hub"


def main(argv: list[str]) -> int:
    if argv == ["roster"]:
        server = host("server")
        for name, paths in roster().items():
            bare = paths.get("memory_bare") or f"~/git-origin/{name}-memory.git"
            print("\t".join([name, str(path(paths.get("workspace")) or ""), paths.get("origin") or "(native)",
                             f"{server}:{bare}" if server else bare]))
        return 0
    if len(argv) == 2 and argv[0] == "get":
        v = get(argv[1])
        if v is None or isinstance(v, (dict, list)):
            print(_("hubconf.py get: {key} is not set or not a single value", key=argv[1]), file=sys.stderr)
            return 1
        print(v)
        return 0
    if len(argv) == 2 and argv[0] == "dir" and argv[1] in ("config", "data", "secrets", "state", "logs"):
        print({"config": config_dir, "data": data_dir, "secrets": secrets_dir}.get(argv[1], lambda: None)()
              or (STATE if argv[1] == "state" else LOGS))
        return 0
    if argv == ["test-config-inputs"]:   # <variable>TAB<file>, one per line, for test-gate
        for env, f in test_config_inputs():
            print(f"{env}\t{f}")
        return 0
    # processes whose working directory is in this folder (for shell scripts; works without lsof too)
    if len(argv) == 2 and argv[0] == "cwd-users":
        print(" ".join(cwd_users(argv[1])))
        return 0
    if len(argv) == 2 and argv[0] == "host":  # print the ssh name; an empty line when one machine does everything
        print(host(argv[1]))
        return 0
    # shell scripts get the expanded path (e.g. memory_copies) instead of hard-coding it
    if len(argv) == 2 and argv[0] == "path":
        v = p(argv[1])
        if v is None:
            print(_("hubconf.py path: {key} is not set", key=argv[1]), file=sys.stderr)
            return 1
        print(v)
        return 0
    if argv == ["ideas"]:
        for name in ideas():
            print(name)
        return 0
    print(_("usage: hubconf.py roster | ideas | get <key> | path <key> | host server | dir config|data|secrets|state|logs"),
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
