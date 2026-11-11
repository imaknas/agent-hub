"""The single entry point for platform settings and the project registry (standard library only; the platform's
Python is bin/py, 3.13 at least).

New code always takes paths, names and the owner from here; do not hard-code the hub's own location, ~/services or
project names.
  - hub root: the parent of the bin/ this file is in; the environment variable AGENT_HUB_ROOT can override it. What
    the platform writes for later (hook commands and the like) names it through hub_bin()/sh_py(), at the moment it
    is written
  - settings: <config>/{settings} (instance directory, see below)
  - instance directory (docs/design/new-user.md 3.1): the user's own things in three parts -- config ({settings},
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
  - The platform's own registry entry (<hub>/projects/brunna.yaml) is part of the platform: registry reads it
    wherever registry_dir is
  - Project registry: <registry_dir>/<name>.yaml (YAML edited by people); every tick the scheduler validates it with
    registry.py and writes <hub>/.shared/registry/index.json (REGISTRY_INDEX) -- this module reads that JSON (no PyYAML needed); if it
    has not been produced yet (new machine, the laptop), fall back to roster(): names and paths read straight from
    the YAML files

For shell scripts ("$HUB/bin/py" hubconf.py …):
  hubconf.py ideas     names of idea sandboxes (registry type: idea), one per line
  hubconf.py get <key> a single value from {settings} (dot-separated, e.g. github.owner), for shell scripts
  hubconf.py roster    one line per project name<TAB>workspace<TAB>origin<TAB>memory_bare, no header line;
                       workspace with ~ expanded, (native) when there is no origin, memory_bare prefixed with
                       {settings}'s memory_host:
  hubconf.py test-config-inputs
                       the instance files tests may read, <variable><TAB><file> per line ({settings}
                       tests.config_inputs)
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shlex
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import i18n  # noqa: E402
import names  # noqa: E402

_ = i18n.domain(__file__)

ROOT = pathlib.Path(names.env("ROOT") or pathlib.Path(__file__).resolve().parents[1])


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
MAIN = ROOT if names.env("ROOT") else _main_checkout(ROOT)
POINTER = MAIN / ".hub-home"
_cfg: dict | None = None


def instance_home() -> pathlib.Path | None:
    """The instance directory; None = the old layout (everything in the hub root)."""
    v = names.env("HOME")
    if not v and POINTER.is_file():
        v = POINTER.read_text().strip()
    return pathlib.Path(os.path.expanduser(v)) if v else None


def _part(var: str, sub: str, legacy: pathlib.Path) -> pathlib.Path:
    if (v := names.env(var)):
        return pathlib.Path(os.path.expanduser(v))
    home = instance_home()
    return home / sub if home else legacy


def config_dir() -> pathlib.Path:
    """Where config lives (hub.json, registry, mirror-list.txt, services.tsv, schedule.yaml)."""
    return _part("CONFIG", "config", ROOT)


def data_dir() -> pathlib.Path:
    """Where data lives (digest/, reflect/, todo.md, index.md, review-log.md, openclaw-migration-status.md,
    legacy-paths.tsv)."""
    return _part("DATA", "data", ROOT)


def config_file(name: str) -> pathlib.Path:
    return config_dir() / name


def config_path() -> pathlib.Path:
    """The instance's settings file, hub.json or brunna.json, whichever exists (docs/design/rename-brunna.md): every
    reader, writer and "is this instance set up" check goes through here, so a release that renamed it and one that
    didn't agree. Both present: config_conflict() says so (doctor, config-check)."""
    return names.resolve(config_dir(), "config_file")


def config_conflict() -> str | None:
    """Both spellings of the settings file present (they would drift apart), or None."""
    both = names.resolve_both(config_dir(), "config_file")
    return " and ".join(str(p) for p in both) if len(both) > 1 else None


def data_file(name: str) -> pathlib.Path:
    return data_dir() / name


def config() -> dict:
    global _cfg
    if _cfg is None:
        p = config_path()
        _cfg = json.loads(p.read_text()) if p.exists() else {}
    return _cfg


def path(value: str | None, base: pathlib.Path | None = None) -> pathlib.Path | None:
    """Expand ~; a relative path is taken from base (by default the config directory hub.json is in; in the old layout
    that is the hub root). `.secrets` / `.secrets/<name>` always mean the platform's secrets directory (secrets_dir()),
    and `.state` / `.state/<name>` the hub's run state (STATE, in the main checkout), wherever hub.json is."""
    if not value:
        return None
    if base is None:
        for mark, where in ((".secrets", secrets_dir), (".state", lambda: STATE)):
            if value == mark or value.startswith(mark + "/"):
                return where() / value[len(mark) + 1:]
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
    return _part("SECRETS", "secrets", legacy_secrets_dir())


def legacy_secrets_dir() -> pathlib.Path:
    """Where secrets were before the move (<hub>/.secrets). The move only copies and deletes after checking, so during
    that time it still holds real secrets -- the sandbox's read block has to cover it too (policy.secret_dirs())."""
    return ROOT / ".secrets"


_SESSION_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


def session_addresses_dir() -> pathlib.Path:
    """Where a live session's wake address is kept (docs/design/mailbox-runtimes.md B1): under the secrets directory,
    which every sandbox is denied, because whoever holds it can post into that session. Not in the bus or .state
    (sandboxed sessions read those), not in the backup (an address only works while its session runs), and skipped by
    check-secrets' scan for undeclared credentials."""
    return secrets_dir() / "sessions"


def mailbox_route_dir() -> pathlib.Path:
    """What mailbox-route's calls leave (bin/mailbox_route.py): which letters were asked about, when, and the session each
    call picked. Under the secrets directory, which every sandbox is denied both ways: when a project's sessions were asked
    about is no sandbox's to read, and an answer is no sandbox's to write. Not the sessions folder, whose prune takes what
    isn't a session's."""
    return secrets_dir() / "mailbox-route"


def _private_tmp_path() -> pathlib.Path:
    return secrets_dir() / "tmp"


def private_tmp() -> pathlib.Path:
    """A temp folder for what the platform runs, loads or trusts as the user. The system's isn't one: every sandboxed project's own
    processes can write it (the same uid, whatever its mode). This is under the secrets directory, which every sandbox is denied
    both ways. An instance directory has no repo or rules file above it (.state is under the hub's own); the old layout
    (<hub>/.secrets) does, so a run that must not read one says so itself, as `complete` does. Not backed up, not scanned.
    Take a folder of your own in it for a run, never this one as a working directory: what a run may reach, others' files are in reach of."""
    top = secrets_dir()
    top.mkdir(mode=0o700, parents=True, exist_ok=True)   # a mode applies to the last folder made, so one at a time
    d = _private_tmp_path()
    d.mkdir(mode=0o700, exist_ok=True)
    return d


def test_tmp(prefix: str) -> pathlib.Path:
    """A folder of its own, in private_tmp, for a test run's temp folder: tests run as the user and execute what they write there. Raises
    OSError when it can't be made, or when private_tmp is inside a repo (the old layout's <hub>/.secrets): tests that strip
    GIT_CEILING_DIRECTORIES would find that repo, so such a run is refused, not moved to the system's temp folder."""
    import tempfile
    top = private_tmp()
    if (repo := next((d for d in (top, *top.parents) if os.path.lexists(d / ".git")), None)):
        raise OSError(0, f"{top} is inside the repo {repo}")
    return pathlib.Path(tempfile.mkdtemp(prefix=prefix, dir=private_tmp()))


PRIVATE_TMP_KEEP_DAYS = 7   # far over the longest run's time limit (the longest in contracts/llm-points.yaml is an hour)


def prune_private_tmp(dry_run: bool = False, now: float | None = None) -> dict:
    """Leftovers of runs that didn't get to clean up (killed, or kept by a failure): what's in private_tmp with nothing written for
    PRIVATE_TMP_KEEP_DAYS and no process working in it. A running job's folder fails one or the other: a run ends at its time limit,
    and a silent one still has its agent in the folder. A link is removed, never followed. Returns {removed, cant_tell (old, and who is
    working where can't be found out: kept), not_removable (old, with a folder in it this user can't empty: kept whole, or it would go
    in part and come back every night), unreadable (why the folder couldn't be listed, or None)}."""
    import shutil
    import time
    out = {"removed": [], "cant_tell": [], "not_removable": [], "unreadable": None}
    top = _private_tmp_path()
    limit = (time.time() if now is None else now) - PRIVATE_TMP_KEEP_DAYS * 86400
    try:
        entries = sorted(top.iterdir())
    except FileNotFoundError:
        return out
    except OSError as e:
        out["unreadable"] = str(e)
        return out

    def newest(p: pathlib.Path) -> float:
        best = p.lstat().st_mtime
        if p.is_dir() and not p.is_symlink():
            for root, dirs, files in os.walk(p):
                for name in [*dirs, *files]:
                    best = max(best, os.lstat(os.path.join(root, name)).st_mtime)
        return best

    def can_empty(p: pathlib.Path) -> bool:
        if not p.is_dir() or p.is_symlink():
            return True
        return all(os.access(root, os.R_OK | os.W_OK | os.X_OK) for root, _dirs, _files in os.walk(p))

    procs = None
    for p in entries:
        try:
            if newest(p) >= limit:
                continue
            if not can_empty(p):
                out["not_removable"].append(p.name)
                continue
            procs = process_cwds() if procs is None else procs
            if not procs:   # there is always this process: empty means it can't be found out, not that nobody is there
                out["cant_tell"].append(p.name)
                continue
            if cwd_users(p, procs):
                continue
            if not dry_run:
                if p.is_dir() and not p.is_symlink():
                    shutil.rmtree(p)
                else:
                    p.unlink()
            out["removed"].append(p.name)
        except OSError:
            out["not_removable"].append(p.name)
    return out


COPY_PREFIX = "hub-propose-"   # with COPY_LEAF: where propose_apply makes its throwaway copy, and how a runtime knows one
COPY_LEAF = "ws"


def throwaway_copy(project: str) -> pathlib.Path:
    """A path for a throwaway copy of this project's repo, not made yet, in a run's folder of its own."""
    import tempfile
    return pathlib.Path(tempfile.mkdtemp(prefix=f"{COPY_PREFIX}{project}-", dir=private_tmp())).resolve() / COPY_LEAF


def is_throwaway_copy(cwd, marked: str | None = None) -> bool:
    """Whether this folder is where throwaway_copy puts one (by path only: no git is run here). marked: what the run's mark says
    (propose_apply sets it), which then has to be this folder too."""
    real = pathlib.Path(os.path.realpath(cwd))
    if marked is not None and (not marked or os.path.realpath(marked) != str(real)):
        return False
    return real.name == COPY_LEAF and real.parent.name.startswith(COPY_PREFIX) \
        and real.parent.parent == pathlib.Path(os.path.realpath(_private_tmp_path()))


def session_address_file(session: str) -> pathlib.Path | None:
    """<session_addresses_dir>/<session>.json, or None when the id isn't a plain file name."""
    return session_file(session, "json")


# A session's files there: <session>.json (an address a runtime keeps), <session>.waiter (the runtime's process waiting
# to wake it), <session>.wake-<nonce> (a wake request for it), <session>.live (the process its hooks saw it run in:
# runtime_ports.record_session), <session>.last (the end of its last reply, a runtime's session_summary). Dot files are a
# writer's or a claimer's in passing.
_SUFFIXES = r"(?:json|waiter|live|last|wake-[A-Za-z0-9_]{1,64})"
_SESSION_SUFFIX = re.compile(rf"^{_SUFFIXES}$")
_SESSION_OF = re.compile(rf"^(?P<session>[A-Za-z0-9][A-Za-z0-9._:-]{{0,199}})\.{_SUFFIXES}$")


def session_file(session: str, suffix: str) -> pathlib.Path | None:
    """<session_addresses_dir>/<session>.<suffix>, or None when either isn't a plain file name."""
    ok = _SESSION_FILE.match(session or "") and _SESSION_SUFFIX.match(suffix or "")
    return session_addresses_dir() / f"{session}.{suffix}" if ok else None


def session_of_file(name: str) -> str | None:
    """Whose file this is in session_addresses_dir (None: a dot file, or not one of a session's)."""
    m = _SESSION_OF.match(name or "")
    return m["session"] if m else None


def session_files(session: str) -> list[pathlib.Path]:
    """Every file of this session there (its address, waiter, wake requests, process record, last reply): what its end
    removes."""
    d = session_addresses_dir()
    try:
        with os.scandir(d) as it:
            return [d / e.name for e in it if session_of_file(e.name) == session]
    except OSError:
        return []


def host(role: str) -> str:
    """Machine role -> ssh name; "" = this machine (one machine does everything). For now there is only one role,
    server: the machine running the hub (memory bare repos, scheduler and workspaces are all on it). Scripts on the
    other machine (the laptop used as a remote) ssh over; on the server itself they act locally. Without hosts.server
    the old memory_host is used."""
    if role != "server":
        raise KeyError(role)
    return get("hosts.server") or get("memory_host") or ""


def nvm_dir() -> pathlib.Path:
    """nvm's folder ($NVM_DIR, else ~/.nvm)."""
    return pathlib.Path(os.environ.get("NVM_DIR") or pathlib.Path.home() / ".nvm")


def nvm_bin() -> pathlib.Path | None:
    """The bin/ of the newest installed version matching nvm's default version (~/.nvm/alias/default, e.g. 24, lts/*,
    node, v24.19.0).

    node (and the claude installed under it) is managed only by nvm (decided by the owner on 2026-10-24, after
    Homebrew's node was deleted by autoremove and a LINE bot was down for a day and a half). Background services and
    scheduled jobs have no shell nvm setup, so this turns it into a full path; after an nvm upgrade the next
    render-services switches over and restarts the services."""
    nvm = nvm_dir()
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
STATE = pathlib.Path(names.env("STATE")) if names.env("STATE") else MAIN / ".state"
LOGS = MAIN / ".logs"


def worktree_lookup() -> bool:
    """This copy of the platform is a linked worktree that found the machine's live state through the main checkout:
    scripts that rebuild that state (registry refresh, config apply, the scheduler) must not run from here, where
    they would build it from a half-edited copy. An explicit AGENT_HUB_STATE (config-check --staged) is not live."""
    return MAIN != ROOT and not names.env("STATE")


# What sandboxed code reads of the platform's run state (the platform CLI inside a project's session, service or job:
# policy.py read), apart from .state, which every sandbox is denied whole (docs/design/capabilities.md 11-08, A1). Written
# only by the platform; nothing private ever goes here. In the hub root beside .state (the main checkout's, as .state is);
# a state other than the hub's own (an explicit override, a test's) is a throwaway one and holds it inside.
def shared() -> pathlib.Path:
    return MAIN / ".shared" if STATE == MAIN / ".state" else STATE / "shared"


SHARED = shared()
REGISTRY_INDEX = SHARED / "registry" / "index.json"   # registry.py's cache (registry.CACHE)
RENAMES_FILE = "project-renames.json"                 # finished and half-done renames (bin/project-rename.py): renames()
TEST_SLOTS_DIR = "test-slots"                         # brunna test's worker slots (bin/test-runner.py): test_slots()


def _migrates() -> bool:
    """Whether this process may move the live layout's state: only the main checkout's own code (a linked worktree's
    main checkout may still run the code reading the old place), and only its own state (never with an explicit
    STATE override, never a throwaway or test state, whichever way it was set)."""
    return ROOT == MAIN and not names.env("STATE") and STATE == MAIN / ".state"


def _same(a: pathlib.Path, b: pathlib.Path) -> bool:
    try:
        return a.is_file() and b.is_file() and not a.is_symlink() and not b.is_symlink() and a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def write_atomic(path: pathlib.Path, text: str) -> None:
    """path's new content, never half written: a file beside it, synced, renamed over it."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _merge_renames(old: pathlib.Path, new: pathlib.Path) -> list[str] | None:
    """Two renames records (an old-code rename across the merge started its own from nothing): merged by key into new,
    old removed. None when merged; else the keys the two give differently (or ["?"] when one isn't a JSON object), and
    nothing is touched: never pick a side."""
    try:
        a, b = json.loads(old.read_text()), json.loads(new.read_text())
    except (OSError, ValueError):
        return ["?"]
    if not isinstance(a, dict) or not isinstance(b, dict):
        return ["?"]
    clash = sorted(k for k in a.keys() & b.keys() if a[k] != b[k])
    if clash:
        return clash
    write_atomic(new, json.dumps({**a, **b}, ensure_ascii=False, indent=1))
    old.unlink()
    return None


def _move(old: pathlib.Path, new: pathlib.Path) -> None:
    """old → new, never leaving a half-written new: a rename on one filesystem; across two (EXDEV), a copy beside new,
    synced, renamed into place, then old removed."""
    import errno
    import shutil
    new.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(old, new)
        return
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
    tmp = new.with_name(f".{new.name}.{os.getpid()}.moving")   # beside new, on its filesystem
    if os.path.lexists(tmp):
        shutil.rmtree(tmp) if tmp.is_dir() and not tmp.is_symlink() else tmp.unlink()
    if old.is_dir() and not old.is_symlink():
        shutil.copytree(old, tmp, symlinks=True)
    else:
        shutil.copy2(old, tmp, follow_symlinks=False)
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
    os.replace(tmp, new)
    fd = os.open(new.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    shutil.rmtree(old) if old.is_dir() and not old.is_symlink() else old.unlink()


def moved_from_state(new: pathlib.Path) -> pathlib.Path:
    """new, after a one-time move of what was at the same name in .state before shared() existed (11-08), done by the
    first process that may (_migrates) and looks. A sandboxed one can't see the old place: it reads new only. Never read
    in both places. Both there: the same file → the old one goes; a rebuilt cache folder (the registry's, the slots) →
    the old one goes; two renames records → merged by key when no key differs; anything else, or a move that fails,
    stays where it is and is said (stderr here, and doctor while
    shared_leftovers() names it): the renames record is the one thing in it that can't be rebuilt."""
    old = STATE / new.name
    if not _migrates() or not os.path.lexists(old):
        return new
    try:
        if not os.path.lexists(new):
            _move(old, new)
        elif _same(old, new):
            old.unlink()
        elif new.is_dir() and old.is_dir() and not old.is_symlink() and new.name != RENAMES_FILE:
            import shutil
            shutil.rmtree(old)   # derived: the registry rebuilds its cache, the slots are empty lock files
        elif new.name == RENAMES_FILE and old.is_file() and not old.is_symlink():
            if (clash := _merge_renames(old, new)):
                print(_("{old} and {new} give {keys} differently: both are kept until one is put right by hand", old=old,
                        new=new, keys=", ".join(clash)), file=sys.stderr)
        else:
            print(_("{old} and {new} both exist and differ: keep the right one, remove the other", old=old, new=new),
                  file=sys.stderr)
    except OSError as e:
        print(_("{old} could not be moved to {new}: {why}", old=old, new=new, why=e), file=sys.stderr)
    return new


def shared_items() -> list[pathlib.Path]:
    """What moved out of .state into shared(): the registry cache, the renames record, brunna test's slots."""
    return [REGISTRY_INDEX.parent, shared() / RENAMES_FILE, shared() / TEST_SLOTS_DIR]


def move_shared() -> list[pathlib.Path]:
    """Every one moved now (brunna land, right after a merge), not on first look: what still sits in .state afterwards."""
    for p in shared_items():
        moved_from_state(p)
    return shared_leftovers()


def shared_leftovers() -> list[pathlib.Path]:
    """The old places in the live .state that still exist although shared() is in use (a move that failed, or two
    copies that differ): doctor shows them red. Nothing outside the live layout counts."""
    if not _migrates():
        return []
    return [STATE / p.name for p in shared_items() if os.path.lexists(STATE / p.name)]


def cli_folder() -> pathlib.Path:
    """A folder holding only the platform's `brunna` wrapper, for services and scheduled commands to find it by name
    (docs/design/project-agent-calls.md D): every service's PATH ends with it (render-services), every scheduled command
    puts it last on its PATH (job_command). In shared(), which sandboxed code reads; under the hub root, which nothing of
    a project's writes."""
    return shared() / "cli"


def cli_wrapper_text() -> str:
    return (f'#!/bin/sh\n# the {names.current("product")} CLI, for services and scheduled commands (written by the '
            f'platform)\nexec "{MAIN / "bin" / names.current("cli")}" "$@"\n')


def write_cli_wrapper() -> bool:
    """cli_folder()/brunna as it should be: a plain file (never a link), mode 755, beside nothing else of ours; a link in
    its place, or as the folder, is replaced. True when something was written."""
    d = cli_folder()
    if d.is_symlink() or (d.exists() and not d.is_dir()):
        d.unlink()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o755)
    f, text = d / names.current("cli"), cli_wrapper_text()
    try:
        if not f.is_symlink() and f.is_file() and f.read_text() == text and (f.stat().st_mode & 0o777) == 0o755:
            return False
    except OSError:
        pass
    tmp = d / f".{f.name}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o755)
    os.replace(tmp, f)   # replaces a link itself, never its target
    return True


def job_command(command: str) -> str:
    """A scheduled command as the login shell runs it (`<shell> -lc`): after the shell's profile, cli_folder() last on
    its PATH, so `brunna` is found by name and nothing found today changes."""
    return f'PATH="$PATH":{shlex.quote(str(cli_folder()))}; {command}'


def renames() -> pathlib.Path:
    """The renames record, in shared() (moved out of .state the first time): every reader and the writer take it here."""
    return moved_from_state(shared() / RENAMES_FILE)


def test_slots() -> pathlib.Path:
    """brunna test's worker slots, in shared() (moved out of .state the first time): sandboxed callers lock them too."""
    return moved_from_state(shared() / TEST_SLOTS_DIR)


# The event bus has its own directory (hub.json bus_dir), in no project's sandbox: a sandboxed project's letters go
# through its outbox (docs/design/bus-sender.md), and the hooks that deliver letters run outside the sandbox.
BUS_DIR = p("bus_dir", ".state/bus")
BUS_LOG = BUS_DIR / "hubbus.log"
_OLD_BUS = [STATE / "bus" / "bus.db", STATE / "bus.db"]  # where it used to be (searched in order)


def _own_instance() -> bool:
    """This process sees its root's live layout through the root's own instance: _migrates() (the main checkout's
    code, its own .state) and the instance the root's .hub-home names (or the old layout without one), with no
    config override. Only then are the bus_dir it reads and the .logs it would touch the same machine's. A throwaway
    world, an eval run or a test that points the HOME or CONFIG override at a temp instance from the live checkout's
    code is not (it once repointed the live .logs/hubbus.log at its temp bus, runtime-versions V-M6)."""
    if not _migrates() or names.env("CONFIG"):
        return False
    v = names.env("HOME")
    if not v:
        return True
    pointed = POINTER.read_text().strip() if POINTER.is_file() else ""
    return bool(pointed) and pathlib.Path(os.path.expanduser(v)).resolve() == pathlib.Path(
        os.path.expanduser(pointed)).resolve()


def bus_db() -> pathlib.Path:
    """The bus's SQLite file. Not yet at the new location but present at an old one -> move it: one connection takes
    the RESERVED lock (BEGIN IMMEDIATE) to hold off other writes, another connection reads it out with the backup API
    (a connection that holds the lock and then backs itself up deadlocks), and the lock is released only after the
    rename. The move and the .logs link change the root's live layout, so only the root's own instance does them
    (_own_instance()); any other process gets the path and nothing moves."""
    new = BUS_DIR / "bus.db"
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    old = next((o for o in _OLD_BUS if o.exists() and o != new), None)
    if new.exists() or old is None or not _own_instance():
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


def project_roots() -> list[pathlib.Path]:
    """The folders the owner named as holding projects (hub.json connect.roots) plus workspace_root, as real paths:
    the only places a folder may be connected from an agent session (docs/design/newcomer-path.md B5)."""
    raw = [*(get("connect.roots") or []), get("workspace_root") or "~/workspace"]
    out = []
    for r in raw:
        p = pathlib.Path(os.path.expanduser(str(r)))
        if p.is_dir():
            p = p.resolve()
            if p not in out:
                out.append(p)
    return out


def language_of(tag: str) -> str:
    """A locale or language tag (zh_TW.UTF-8, zh-Hant-TW, en_US, de) → one of LANGUAGES. Traditional Chinese (Hant, or
    Taiwan / Hong Kong / Macau) is zh-Hant; Simplified Chinese and everything else is en until it has a catalog."""
    t = tag.split(".")[0].split("@")[0].replace("-", "_").lower()
    parts = t.split("_")
    if parts[0] == "zh" and ("hant" in parts or set(parts) & {"tw", "hk", "mo"}):
        return "zh-Hant"
    return "en"


def is_simplified_chinese(tag: str) -> bool:
    """zh that language_of maps to en (zh_CN, zh-Hans): init says so instead of pretending English was asked for."""
    t = tag.split(".")[0].replace("-", "_").lower()
    return t.split("_")[0] == "zh" and language_of(tag) == "en"


def _apple_languages() -> list[str]:
    """macOS's preferred languages, in order (AppleLanguages). LANG there follows the Region setting, not the language
    the user reads, so this comes first. Empty when it can't be read."""
    if sys.platform != "darwin":
        return []
    import plistlib
    import subprocess
    try:
        r = subprocess.run(["/usr/bin/defaults", "export", "-g", "-"], capture_output=True, timeout=5)
        return [str(x) for x in plistlib.loads(r.stdout).get("AppleLanguages") or []] if r.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, ValueError, plistlib.InvalidFileException):
        return []


def system_tag(env=None, apple: list[str] | None = None) -> str:
    """The language tag this machine prefers: macOS's first AppleLanguages entry, else LC_ALL, LC_MESSAGES, LANG."""
    langs = _apple_languages() if apple is None else apple
    if langs:
        return langs[0]
    env = os.environ if env is None else env
    return next((env[k] for k in ("LC_ALL", "LC_MESSAGES", "LANG") if env.get(k)), "")


def system_language(env=None, apple: list[str] | None = None) -> str:
    """init's default language (docs/design/newcomer-path.md B0)."""
    return language_of(system_tag(env, apple))


def lang_ui() -> str:
    """The language the platform shows the owner (CLI output, status page, alerts, headings): hub.json `language.ui`,
    or AGENT_HUB_LANG_UI. Default en (docs/design/i18n.md)."""
    return _lang("LANG_UI", "language.ui")


def lang_content() -> str:
    """The language models and agents write in (STATE.md, notes, digests, letters): hub.json `language.content`, or
    AGENT_HUB_LANG_CONTENT. Default en."""
    return _lang("LANG_CONTENT", "language.content")


def _lang(var: str, key: str) -> str:
    """A wrong value in the environment is an error, not a quiet fallback to en (a test that sets zh-hant would otherwise
    pass in English). A wrong value in hub.json gives en: config-check reports it, and must not crash on it while it
    formats its own messages."""
    v = names.env(var)
    if v:
        if v not in LANGUAGES:
            raise ValueError(f"{names.env_names(var)[0]}={v!r}: the languages are {', '.join(LANGUAGES)}")
        return v
    v = get(key)
    return v if v in LANGUAGES else "en"


def evals_path() -> pathlib.Path | None:
    """Where evals_dir() is configured, without making it (what a sandbox denies: it must not create the folder)."""
    return pathlib.Path(names.env("EVALS")) if names.env("EVALS") else p("evals_dir")


def evals_dir() -> pathlib.Path | None:
    """Where eval cases and runs live (docs/design/evaluators.md, i18n.md): hub.json `evals_dir`, or AGENT_HUB_EVALS.
    None = not set up, nothing is recorded. Created 0700; keep it out of git and out of the backup."""
    d = evals_path()
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
    return names.resolve(ROOT / "projects", "project", "{}.yaml")   # whichever spelling the tree has


def owner_address() -> str:
    return get("owner.address", "owner")


def owner_addresses() -> set[str]:
    """The owner's address and its aliases from hub.json (old letters may use an alias; all are accepted)."""
    return {owner_address(), *get("owner.aliases", [])}


def owner_tag() -> str:
    return get("owner.tag", "Owner")


def tz():
    """The owner's time zone (hub.json owner.timezone, an IANA name) as a tzinfo, for what is counted by the owner's
    clock (the weekly report's hours). Not set: this machine's local zone. Set but not found -- the zone data is
    missing (a bare Linux has no tzdata package; bin/setup-python installs the Python one) or the name is wrong --
    says so on stderr and uses the local zone rather than stopping the job."""
    import datetime
    import zoneinfo
    name = get("owner.timezone")
    if name:
        try:
            return zoneinfo.ZoneInfo(name)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError) as e:
            print(_("time zone {name} ({settings} owner.timezone) not found ({error}); using this machine's local time "
                    "instead. Without the system's tzdata, rerun bin/setup-python (it installs the Python tzdata "
                    "package)", name=name, error=e), file=sys.stderr)
    return datetime.datetime.now().astimezone().tzinfo


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
            return f"{names.current('party')}:owner-side"
        return get("assistant.party_id") or get("assistant.project") or "assistant"
    if role in ("requester", "agent"):
        if not project:
            raise ValueError(_("party({role}) needs a project", role=repr(role)))
        # the platform's own project speaks under its party name, which switches only with the party ids
        return f"project:{names.current('party') if project in names.all('project') else project}"
    if role == "registrar":
        return f"{names.current('party')}:registrar"
    raise ValueError(_("unknown role {role}", role=repr(role)))


def party_ids(role: str) -> set[str]:
    """Every spelling of the platform's own party id for this role (registrar, principal_side when built in): records
    written under either are the platform's, for good (docs/design/rename-brunna.md)."""
    if role == "registrar":
        return {f"{s}:registrar" for s in names.PAIRS["party"]}
    if role == "principal_side" and owner_side() == "builtin":
        return {f"{s}:owner-side" for s in names.PAIRS["party"]}
    return {party(role)}


def former_names(project: str) -> list[str]:
    """The names this project had before being renamed (from renames(), counting only finished
    renames, stage done). Old letters on the bus and records the assistant sends may still use an old name (e.g.
    idea-1 upgraded and renamed to garden-app). The platform's own other spelling (agent-hub / brunna) counts in both
    directions, from the name table, so a release on either side of the rename reads the other's letters and records."""
    hub = [names.other("project")] if project == hub_project() else []
    try:
        recs = json.loads(renames().read_text())
    except (OSError, ValueError):
        return hub

    def now(name: str) -> str:
        seen = set()
        while name in recs and name not in seen and (recs[name] or {}).get("stage") == "done":
            seen.add(name)
            name = recs[name]["new"]
        return name
    # a rename that ended at the platform's other spelling ended at the platform
    mine = {project, *hub}
    return hub + sorted(old for old in recs if old not in mine and now(old) in mine)


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


_SH_PLAIN = re.compile(r"[A-Za-z0-9._/+@%=:,-]+")


def sh_path(p: pathlib.Path | str) -> str:
    """A path as the platform writes it into a shell string it installs for later (hook commands and the like):
    `~/<rest>` when it is under the home folder and needs no quoting (the form every hook was written in so far, so
    such a machine's installed text stays the same, character for character), else the absolute path, quoted when it
    has to be."""
    p = pathlib.Path(p)
    try:
        rest = str(p.relative_to(pathlib.Path.home()))
    except ValueError:
        rest = ""
    if rest not in ("", ".") and _SH_PLAIN.fullmatch(rest):
        return f"~/{rest}"
    return shlex.quote(str(p))


def hub_bin(name: str) -> str:
    """One of the hub's programs, written into a shell string that runs later (a hook): the main checkout's bin/ (a
    linked worktree's copy goes away with the worktree), resolved now. A hook names the hub's real location at the
    moment it is written; after the hub moves, the installers write it again (sync-local-setup at a project's session
    start, `agenthub config apply` for the hub's own sessions and every onboarded project)."""
    return sh_path(MAIN / "bin" / name)


def sh_py(script: pathlib.Path | str) -> str:
    """A platform Python script written into a shell string (hook commands and the like): on macOS it is called directly
    as before (output identical to before, character for character); on other systems the py from the same folder is
    put in front. script is the script's path (written with sh_path), or a path already written as shell."""
    if isinstance(script, pathlib.PurePath):
        expr = sh_path(script)
        return expr if sys.platform == "darwin" else f"{sh_path(script.parent / 'py')} {expr}"
    if sys.platform == "darwin":
        return script
    return f"{script.rsplit('/', 1)[0]}/py {script}"


def login_shell() -> str:
    """The login shell that runs jobs and test commands (`<shell> -lc <command>`, reading the user's PATH, nvm and the
    like): always /bin/zsh on macOS (as before); on other systems bash when there is no zsh, otherwise sh."""
    for sh in ("/bin/zsh", "/bin/bash", "/usr/bin/bash"):
        if os.access(sh, os.X_OK):
            return sh
    return "/bin/sh"


_SERVICE_MANAGER: list = []


def home_is_the_accounts() -> bool:
    """HOME is this account's own home directory. A service manager loads units from the account's home only, but
    acts on the account whatever HOME says: with another HOME (a test's, a sandbox's) installing or reloading a unit
    would stop the account's real one and load a file nothing keeps (11-02: a test's init unloaded the mailbox
    dispatch for 14 hours)."""
    import pwd
    try:
        return pathlib.Path.home().resolve() == pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    except (KeyError, OSError, RuntimeError):
        return True   # no account record to compare with: as before


def service_manager() -> str | None:
    """Which service manager this machine uses for the scheduler, the watchdog and projects' long-running services:
    macOS = launchd; Linux with a systemd user level (`systemctl --user` works) = systemd (bin/systemd_units.py).
    Neither (e.g. a container) = None: install, status and reload all say plainly "this system has no service
    manager" instead of pretending it is running. None as well when HOME is not the account's (home_is_the_accounts):
    the platform then never touches the account's units."""
    if not _SERVICE_MANAGER:
        if not home_is_the_accounts():
            _SERVICE_MANAGER.append(None)
        elif sys.platform == "darwin":
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
    if not home_is_the_accounts():
        return _("HOME ({home}) is not this account's home directory: the platform leaves the account's background "
                 "services alone (installing one from here would replace the account's real one)", home=pathlib.Path.home())
    return _("this system has no supported service manager (neither macOS launchd nor the Linux systemd user level): the "
             "scheduler, the watchdog and projects' long-running services will not run by themselves")


def manual_scheduler() -> str:
    """Where there is no service manager: what to schedule by hand so the nightly flow runs -- the scheduler's tick as
    one absolute, copy-pasteable command (the main checkout's: the scheduler does not run from a worktree)."""
    command = f"{shlex.quote(str(MAIN / 'bin' / 'py'))} {shlex.quote(str(MAIN / 'bin' / 'scheduler.py'))}"
    return _("schedule `{command}` yourself every 15 minutes (for example the crontab line `*/15 * * * * {command}`)",
             command=command)


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


def cwd_users(path: str | pathlib.Path, procs: list | None = None) -> list[str]:
    """Processes whose working directory is in this folder (subfolders included): "program name(pid)". procs: process_cwds(), already read."""
    root = os.path.realpath(path)
    return sorted({f"{name}({pid})" for pid, name, cwd in (process_cwds() if procs is None else procs)
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
    """The platform's own name in the registry (bin/names.py: agent-hub until the switch to brunna)."""
    return names.current("project")


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
    if argv == ["test-config-inputs"]:   # <variable>TAB<file>, one per line, for shell callers
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
