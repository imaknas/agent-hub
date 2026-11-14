#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""A project's skills, developed in an ordinary folder and installed into the folders its agents load them from only
when the owner has reviewed them (docs/design/skills-sync.md; the owner's decision of 11-15).

Every sandbox keeps a sandboxed project's own processes off the coding agents' control folders (policy.protect): a
skill there is instructions its sessions load by themselves, so nothing inside the sandbox may plant one. The project
writes `skills/<name>/` (each with a SKILL.md) like any other file; the owner runs

  brunna skills sync <project> [--yes] [--adopt]
                        shows what would change per skill (added / changed / removed, a unified diff of text files, the
                        executables and anything an adapter flags listed first) and installs it after a typed `yes`;
                        --yes skips the question (and is the only way without a terminal); --adopt lets it replace
                        a same-named entry it didn't install (a project's old copy, a link)
  brunna skills status [<project>]
                        what is waiting for review, per project (reads only)
  skills_sync.py remind the platform job brunna/skills-pending (every 15 minutes): a status-page row and one owner to-do
                        per change set waiting for review; never an install

Where skills go: each installed runtime's optional SKILL_DIRS (its adapter: the folders, relative to a session folder,
it loads project skills from), in each of its session folders (the workspace and the worktrees its sessions start in).
Nothing of the tool's is named here.

How it copies (the source and the destinations are in the project's folder, where its processes can swap anything for a
link at any moment; on Linux nothing keeps them off the control folders either):
- the source is read once, as a snapshot: every folder opened from the one above it with O_NOFOLLOW, every file opened
  without following a link or blocking, a regular file with one link only, under the size caps; a link, a hard link, a
  special file, a control name inside a skill or a name that can't be printed is refused (said, and nothing installed).
  The diff, the hashes, the question and the install all use that snapshot: what was reviewed is what is installed,
  whatever happens to skills/ after the `yes`;
- each skill is written into a new folder next to its place (descriptors held from the workspace down, new files
  O_EXCL), then renamed into place; the entry it replaces is renamed aside and removed without following anything;
- a skill is removed only when sync installed it (its manifest), and an entry sync didn't install is replaced only with
  --adopt; destination folders the project's git still tracks are refused (docs/design/skills-sync.md, "Moving a
  project's skills");
- the manifest (what was approved, and what was installed where, file by file with hashes and modes) is in the
  platform's state, never in the project. render-project-settings' foreign check counts an installed skill as the
  platform's only while its tree is exactly that.
Refused inside a sandbox (isolation.confined_now) and from a linked worktree of the hub (it writes the live state).
"""
from __future__ import annotations

import contextlib
import datetime
import difflib
import errno
import fcntl
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)
_c = i18n.domain(__file__, content=True)

STATE_DIR = hubconf.STATE / "skills-sync"
SOURCE = "skills"
FILE_MAX = 2 << 20          # one file
TOTAL_MAX = 64 << 20        # all of a project's skills
FILES_MAX = 4000            # files in all of them
DEPTH_MAX = 12              # folders below a skill's own
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")   # a skill's folder name
DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
NEW, OLD = ".sync-new", ".sync-old"   # the in-between names next to a skill's place: .<name>.sync-new-<pid>


class Refused(Exception):
    """Said to the owner as it is; nothing was installed."""


# ---------- reading a tree without following anything ----------

def _open_dirs(base, rel: str, create: bool) -> int:
    """A descriptor of base/rel, every folder below base opened with O_NOFOLLOW from the one above (created when
    create); base itself (a workspace) opened normally."""
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for p in pathlib.PurePosixPath(rel).parts if rel else ():
            if p in (".", ".."):
                raise OSError(errno.EINVAL, f"not a plain relative path: {rel}")
            try:
                nfd = os.open(p, DIR, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(p, 0o755, dir_fd=fd)
                nfd = os.open(p, DIR, dir_fd=fd)
            os.close(fd)
            fd = nfd
    except BaseException:
        os.close(fd)
        raise
    return fd


def printable(s: str) -> str:
    """s as it can be shown on a terminal: control characters, the bidirectional overrides and what isn't text escaped
    (a file of the project's must not hide lines from the owner reading the diff)."""
    out = []
    for ch in s:
        o = ord(ch)
        if ch == "\t":
            out.append(ch)
        elif o < 0x20 or 0x7f <= o < 0xa0 or 0x202a <= o <= 0x202e or 0x2066 <= o <= 0x2069 or 0xd800 <= o <= 0xdfff:
            out.append(f"\\x{o:02x}" if o < 0x100 else f"\\u{o:04x}")
        else:
            out.append(ch)
    return "".join(out)


def _control_tops() -> set[str]:
    """The first part of every control name (policy.protect): never a folder or file inside a skill."""
    import policy
    return {pathlib.PurePosixPath(c["name"]).parts[0] for c in policy.protect()}


class Tree:
    """A folder read as a snapshot: files {rel: (executable, bytes)}, and what isn't a plain file or folder (odd)."""

    def __init__(self):
        self.files: dict[str, tuple[bool, bytes]] = {}
        self.odd: list[str] = []
        self.size = 0


def _read_file(fd: int, name: str, rel: str, tree: Tree, budget: list[int]) -> None:
    try:
        f = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
    except OSError as e:
        tree.odd.append(_("{file}: can't be opened as a plain file ({why})", file=rel, why=e.strerror or e))
        return
    try:
        st = os.fstat(f)
        if not stat.S_ISREG(st.st_mode):
            tree.odd.append(_("{file} is not a plain file", file=rel))
            return
        if st.st_nlink != 1:
            tree.odd.append(_("{file} is a hard link (it has {n} names)", file=rel, n=st.st_nlink))
            return
        if st.st_size > FILE_MAX:
            tree.odd.append(_("{file} is over {cap} bytes", file=rel, cap=FILE_MAX))
            return
        chunks, n = [], 0
        while chunk := os.read(f, 1 << 16):
            n += len(chunk)
            if n > FILE_MAX:
                tree.odd.append(_("{file} is over {cap} bytes", file=rel, cap=FILE_MAX))
                return
            chunks.append(chunk)
        budget[0] -= n
        budget[1] -= 1
        if budget[0] < 0 or budget[1] < 0:
            raise Refused(_("{folder} holds more than {size} bytes or {files} files; nothing was read further",
                            folder=SOURCE, size=TOTAL_MAX, files=FILES_MAX))
        tree.files[rel] = (bool(st.st_mode & 0o111), b"".join(chunks))
        tree.size += n
    finally:
        os.close(f)


def _walk(fd: int, prefix: str, tree: Tree, budget: list[int], depth: int, tops: set[str] | None) -> None:
    """Every entry under the open folder fd into tree (rel names under prefix). tops (a source, _control_tops): a
    control name or a name that can't be printed is odd too."""
    strict = tops is not None
    for name in sorted(os.listdir(fd)):
        rel = f"{prefix}/{name}" if prefix else name
        if strict and (printable(name) != name or not name.isprintable()):
            tree.odd.append(_("{file}: a name that can't be shown as it is", file=ascii(rel)))
            continue
        if strict and name in tops:
            tree.odd.append(_("{file}: a coding agent's control name inside a skill", file=rel))
            continue
        try:
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except OSError as e:
            tree.odd.append(_("{file}: can't be looked at ({why})", file=rel, why=e.strerror or e))
            continue
        if stat.S_ISLNK(st.st_mode):
            tree.odd.append(_("{file} is a link", file=rel))
        elif stat.S_ISDIR(st.st_mode):
            if depth >= DEPTH_MAX:
                tree.odd.append(_("{file}: folders nested deeper than {n}", file=rel, n=DEPTH_MAX))
                continue
            try:
                sub = os.open(name, DIR, dir_fd=fd)
            except OSError as e:
                tree.odd.append(_("{file}: can't be opened as a folder ({why})", file=rel, why=e.strerror or e))
                continue
            try:
                _walk(sub, rel, tree, budget, depth + 1, tops)
            finally:
                os.close(sub)
        elif stat.S_ISREG(st.st_mode):
            _read_file(fd, name, rel, tree, budget)
        else:
            tree.odd.append(_("{file} is not a plain file", file=rel))


class Snapshot:
    """The source as read once: skills {name: Tree}, problems (each refuses the sync), notes (said, nothing refused)."""

    def __init__(self):
        self.skills: dict[str, Tree] = {}
        self.problems: list[str] = []
        self.notes: list[str] = []


def snapshot(ws) -> Snapshot:
    """<ws>/skills, read once (see the header). A missing folder is an empty snapshot."""
    snap = Snapshot()
    try:
        fd = _open_dirs(ws, SOURCE, create=False)
    except FileNotFoundError:
        return snap
    except OSError as e:
        snap.problems.append(_("{folder} can't be opened as a folder ({why})", folder=SOURCE, why=e.strerror or e))
        return snap
    budget, tops = [TOTAL_MAX, FILES_MAX], _control_tops()
    try:
        for name in sorted(os.listdir(fd)):
            try:
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except OSError as e:
                snap.problems.append(_("{file}: can't be looked at ({why})", file=f"{SOURCE}/{ascii(name)}",
                                       why=e.strerror or e))
                continue
            if stat.S_ISLNK(st.st_mode):
                snap.problems.append(_("{file} is a link", file=f"{SOURCE}/{printable(name)}"))
                continue
            if not stat.S_ISDIR(st.st_mode):
                continue   # a README beside the skills: not a skill
            if not NAME.fullmatch(name):
                snap.problems.append(_("{file}: a skill's folder name is letters, digits, '.', '_' and '-' only",
                                       file=f"{SOURCE}/{ascii(name)}"))
                continue
            try:
                sub = os.open(name, DIR, dir_fd=fd)
            except OSError as e:
                snap.problems.append(_("{file}: can't be opened as a folder ({why})", file=f"{SOURCE}/{name}",
                                       why=e.strerror or e))
                continue
            tree = Tree()
            try:
                _walk(sub, "", tree, budget, 0, tops)
            finally:
                os.close(sub)
            snap.problems += [f"{SOURCE}/{name}/{o}" for o in tree.odd]
            if "SKILL.md" not in tree.files:
                snap.notes.append(_("{file} has no SKILL.md: not a skill, left out", file=f"{SOURCE}/{name}"))
                continue
            snap.skills[name] = tree
    except Refused as e:
        snap.problems.append(str(e))
    finally:
        os.close(fd)
    return snap


def installed_tree(ws, rel: str) -> Tree | None:
    """What is at <ws>/<rel> now, read as a snapshot without following anything: None when nothing is there; a link
    or anything but a folder is a tree with only an odd entry."""
    parent, name = str(pathlib.PurePosixPath(rel).parent), pathlib.PurePosixPath(rel).name
    tree = Tree()
    try:
        fd = _open_dirs(ws, "" if parent == "." else parent, create=False)
    except FileNotFoundError:
        return None
    except OSError as e:
        tree.odd.append(_("{file}: a folder on the way can't be opened ({why})", file=rel, why=e.strerror or e))
        return tree
    try:
        try:
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(st.st_mode):
            tree.odd.append(_("{file} is a link", file=rel))
            return tree
        if not stat.S_ISDIR(st.st_mode):
            tree.odd.append(_("{file} is not a folder", file=rel))
            return tree
        sub = os.open(name, DIR, dir_fd=fd)
        try:
            _walk(sub, "", tree, [TOTAL_MAX, FILES_MAX], 0, None)
        except Refused as e:
            tree.odd.append(str(e))
        finally:
            os.close(sub)
        return tree
    finally:
        os.close(fd)


# ---------- hashes and the manifest ----------

def meta(tree: Tree) -> dict[str, list]:
    """{rel: [sha256, executable]} of a tree's files."""
    return {rel: [hashlib.sha256(data).hexdigest(), exe] for rel, (exe, data) in sorted(tree.files.items())}


def tree_hash(files_meta: dict) -> str:
    return hashlib.sha256(json.dumps(sorted(files_meta.items()), separators=(",", ":")).encode()).hexdigest()


def set_hash(snap: Snapshot) -> str:
    """One change set: every skill's hash, and the problems (a refused source waits for review too)."""
    doc = {"skills": {n: tree_hash(meta(t)) for n, t in sorted(snap.skills.items())}, "problems": sorted(snap.problems)}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def empty_hash() -> str:
    return set_hash(Snapshot())


def manifest_path(project: str) -> pathlib.Path:
    return STATE_DIR / f"{project}.json"


def load(project: str) -> dict:
    try:
        doc = json.loads(manifest_path(project).read_text())
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def save(project: str, doc: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path(project).with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True))
    os.replace(tmp, manifest_path(project))


@contextlib.contextmanager
def locked(project: str):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / f"{project}.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def matches(ws, rel: str, files_meta: dict) -> bool:
    """The tree at <ws>/<rel> is exactly what sync installed there: the same files, hashes and executable bits, nothing
    else, no link or special file anywhere in it."""
    tree = installed_tree(ws, rel)
    return tree is not None and not tree.odd and meta(tree) == files_meta


def owned(project: str, ws):
    """For the foreign-control check (render-project-settings): rel → whether that entry is a skill sync installed and
    still exactly as it installed it."""
    installed = load(project).get("installed") or {}
    flat = {f"{d}/{n}": m.get("files") or {} for d, skills in installed.items() if isinstance(skills, dict)
            for n, m in skills.items() if isinstance(m, dict)}
    return lambda rel: rel in flat and matches(ws, rel, flat[rel])


# ---------- where skills go ----------

def destinations(ws) -> list[str]:
    """Each folder (relative to the workspace) an installed runtime loads project skills from, in each of its session
    folders: the adapters' SKILL_DIRS (runtime_ports.skill_dirs)."""
    import runtime_ports
    ws = pathlib.Path(ws)
    out = []
    for rname in runtime_ports.installed():
        dirs = runtime_ports.skill_dirs(rname)
        if not dirs:
            continue
        try:
            folders = runtime_ports.load(rname, ports=("control",)).session_folders(ws)
        except runtime_ports.Unsupported:
            folders = [ws]
        for folder in folders:
            try:
                base = pathlib.PurePosixPath(pathlib.Path(folder).relative_to(ws))
            except ValueError:
                continue
            out += [str(base / d) if str(base) != "." else d for d in dirs]
    return list(dict.fromkeys(out))


def workspace(project: str) -> pathlib.Path:
    docs = hubconf.projects()
    if project not in docs or project == hubconf.hub_project():
        raise Refused(_("{project} is not a registered project", project=project))
    ws = hubconf.path((docs[project].get("paths") or {}).get("workspace"))
    if not ws or not ws.is_dir():
        raise Refused(_("{project} has no workspace folder on this machine", project=project))
    return ws


# ---------- the plan ----------

class Step:
    """One skill in one destination: add / change / remove / same, or blocked (an entry sync didn't install)."""

    def __init__(self, dest: str, name: str, kind: str, current: Tree | None, new: Tree | None, adopt: bool = False):
        self.dest, self.name, self.kind, self.current, self.new, self.adopt = dest, name, kind, current, new, adopt


def plan(project: str, ws, snap: Snapshot, adopt: bool) -> list[Step]:
    man = load(project)
    installed = man.get("installed") or {}
    dests = destinations(ws)
    # a destination sync installed into before (another runtime's, a worktree's) keeps being kept, while its folder is
    # there
    for d in installed:
        parent = pathlib.PurePosixPath(d).parent
        if d not in dests and (str(parent) == "." or (pathlib.Path(ws) / parent).is_dir()):
            dests.append(d)
    steps = []
    for dest in dests:
        mine = installed.get(dest) or {}
        for name in sorted(set(snap.skills) | set(mine)):
            rel = f"{dest}/{name}"
            cur = installed_tree(ws, rel)
            if name not in snap.skills:
                if cur is not None:   # in the manifest: sync installed it
                    steps.append(Step(dest, name, "remove", cur, None))
                continue
            new = snap.skills[name]
            if cur is None:
                steps.append(Step(dest, name, "add", None, new))
            elif name not in mine:
                steps.append(Step(dest, name, "change" if adopt else "blocked", cur, new, adopt=True))
            elif cur.odd or meta(cur) != meta(new):
                steps.append(Step(dest, name, "change", cur, new))
            else:
                steps.append(Step(dest, name, "same", cur, new))
    return steps


def _text(data: bytes) -> list[str] | None:
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return None


def diff_lines(name: str, cur: Tree | None, new: Tree | None) -> list[str]:
    """The unified diff of one skill, current → new, every line printable."""
    out = []
    a, b = (cur.files if cur else {}), (new.files if new else {})
    for o in (cur.odd if cur else []):
        out.append(_("  (now) {what}", what=printable(o)))
    for rel in sorted(set(a) | set(b)):
        x, y = a.get(rel), b.get(rel)
        if x == y:
            continue
        path = printable(f"{name}/{rel}")
        if x and y and x[1] == y[1]:
            out.append(_("  {file}: executable {old} → {new}", file=path, old=x[0], new=y[0]))
            continue
        tx, ty = _text(x[1]) if x else [], _text(y[1]) if y else []
        if tx is None or ty is None:
            out.append(_("  {file}: binary, {old} → {new} bytes", file=path, old=len(x[1]) if x else 0,
                         new=len(y[1]) if y else 0))
            continue
        for line in difflib.unified_diff(tx, ty, f"a/{path}" if x else "/dev/null", f"b/{path}" if y else "/dev/null"):
            out.append(printable(line.rstrip("\n")))
    return out


def adapter_notes(tree: Tree) -> list[str]:
    """What each installed runtime says about a skill's files (its optional skill_notes, e.g. a skill that declares
    hooks its sessions would run)."""
    import runtime_ports
    out = []
    for rname in runtime_ports.installed():
        try:
            fn = getattr(runtime_ports._module(rname), "skill_notes", None)
        except KeyError:
            continue
        if callable(fn):
            out += [n for n in fn({rel: data for rel, (_exe, data) in tree.files.items()}) if n not in out]
    return out


def show(project: str, snap: Snapshot, steps: list[Step]) -> list[str]:
    """What the owner reads before answering: a summary per skill, then the executables and the adapters' notes, then
    one diff per distinct current content of each skill."""
    out = [_("{project}: {source}/ → {dests}", project=project, source=SOURCE,
             dests=", ".join(dict.fromkeys(printable(s.dest) for s in steps)) or "-")]
    sign = {"add": "+", "change": "~", "remove": "-", "blocked": "!"}
    for s in steps:
        if s.kind == "same":
            continue
        what = {"add": _("added"), "change": _("changed"), "remove": _("removed (sync installed it; it is no longer in "
                                                                      "{source}/)", source=SOURCE),
                "blocked": _("not installed by sync: left alone (--adopt replaces it)")}[s.kind]
        if s.kind == "change" and s.adopt:
            what = _("replaces an entry sync didn't install (--adopt)")
        out.append(f"  {sign[s.kind]} {printable(s.dest)}/{s.name}: {what}")
    news = {s.name: s.new for s in steps if s.kind in ("add", "change") and s.new}
    exe = [f"{n}/{rel}" for n, t in sorted(news.items()) for rel, (x, _d) in sorted(t.files.items()) if x]
    if exe:
        out.append(_("⚠️ executables (a skill can tell the agent to run them): {files}",
                     files=", ".join(printable(e) for e in exe)))
    for n, t in sorted(news.items()):
        for note in adapter_notes(t):
            out.append(_("⚠️ {skill}: {note}", skill=n, note=note))
    for note in snap.notes:
        out.append(printable(note))
    shown: set[tuple[str, str]] = set()
    for s in steps:
        if s.kind in ("same", "blocked"):
            continue
        key = (s.name, tree_hash(meta(s.current)) + repr(s.current.odd) if s.current else "-")
        if key in shown:
            continue
        shown.add(key)
        where = [x.dest for x in steps if x.name == s.name and x.kind == s.kind and
                 (tree_hash(meta(x.current)) + repr(x.current.odd) if x.current else "-") == key[1]]
        out.append(_("--- {skill} ({where})", skill=s.name, where=", ".join(printable(w) for w in where)))
        out += diff_lines(s.name, s.current, s.new)
    return out


# ---------- writing ----------

def _remove_tree(fd: int, name: str) -> None:
    """Remove the entry name in the open folder fd: a link or file unlinked, a folder emptied the same way and removed;
    nothing followed."""
    st = os.stat(name, dir_fd=fd, follow_symlinks=False)
    if not stat.S_ISDIR(st.st_mode):
        os.unlink(name, dir_fd=fd)
        return
    sub = os.open(name, DIR, dir_fd=fd)
    try:
        for e in os.listdir(sub):
            _remove_tree(sub, e)
    finally:
        os.close(sub)
    os.rmdir(name, dir_fd=fd)


def _write_tree(top: int, tree: Tree) -> None:
    for rel, (exe, data) in sorted(tree.files.items()):
        parts = pathlib.PurePosixPath(rel).parts
        fd = os.dup(top)
        try:
            for p in parts[:-1]:
                try:
                    nfd = os.open(p, DIR, dir_fd=fd)
                except FileNotFoundError:
                    os.mkdir(p, 0o755, dir_fd=fd)
                    nfd = os.open(p, DIR, dir_fd=fd)
                os.close(fd)
                fd = nfd
            f = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                        dir_fd=fd)
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(f, view):]
                os.fchmod(f, 0o755 if exe else 0o644)
            finally:
                os.close(f)
        finally:
            os.close(fd)


def install(ws, dest: str, name: str, tree: Tree | None) -> None:
    """name in <ws>/<dest> becomes tree (None: removed). The new one is written next to its place and renamed in; the
    old entry is renamed aside first and then removed, all through descriptors held from the workspace down."""
    d = _open_dirs(ws, dest, create=tree is not None)
    try:
        new, old = f".{name}{NEW}-{os.getpid()}", f".{name}{OLD}-{os.getpid()}"
        for left in os.listdir(d):   # what an interrupted run left
            if left.startswith((f".{name}{NEW}-", f".{name}{OLD}-")):
                with contextlib.suppress(FileNotFoundError):
                    _remove_tree(d, left)
        if tree is not None:
            os.mkdir(new, 0o755, dir_fd=d)
            top = os.open(new, DIR, dir_fd=d)
            try:
                _write_tree(top, tree)
            finally:
                os.close(top)
        try:
            os.rename(name, old, src_dir_fd=d, dst_dir_fd=d)
            had = True
        except FileNotFoundError:
            had = False
        if tree is not None:
            os.rename(new, name, src_dir_fd=d, dst_dir_fd=d)
        if had:
            _remove_tree(d, old)
    finally:
        os.close(d)


# ---------- the owner's command ----------

def refuse_here() -> str | None:
    """Why this process may not install skills: inside a sandbox (a project's session or job), or a linked worktree
    of the hub (the state it writes is the live one)."""
    import isolation
    if isolation.confined_now():
        return _("brunna skills sync runs only in your own terminal, never inside a project's sandbox")
    if hubconf.worktree_lookup():
        return _("{root} is a linked worktree of the hub; run brunna skills from the main checkout ({main})",
                 root=hubconf.ROOT, main=hubconf.MAIN)
    return None


def folder_of(dest: str) -> str:
    """The session folder a destination is in, relative to the workspace ('' for the workspace itself)."""
    return dest[:-len(_top_dest(dest))].rstrip("/")


def still_tracked(project: str, ws, dests: list[str], folder: str = "") -> list[str]:
    """The tracked files in these skill folders of one session folder's checkout (read as the project, project_git):
    an install there would leave that checkout changed. Raises Refused when it can't be told."""
    import project_git
    repo = pathlib.Path(ws) / folder if folder else pathlib.Path(ws)
    if not os.path.lexists(repo / ".git") or not dests:
        return []
    try:
        return project_git.strs(project_git.data(project, ws, "tracked", repo, *dests))
    except project_git.Unavailable as e:
        raise Refused(_("could not tell whether {project} still tracks files in {dests} ({why})", project=project,
                        dests=", ".join(dests), why=e))


def exclude(project: str, ws, dests: list[str]) -> None:
    """Each destination into the project's git excludes (as the project; shared by its worktrees), so an installed
    skill never shows as a change of its checkouts."""
    import project_git
    if not os.path.lexists(pathlib.Path(ws) / ".git"):
        return
    for d in dict.fromkeys(_top_dest(d) for d in dests):
        r = project_git.job(project, ws, "exclude", ws, f"/{d}/")
        if r.returncode != 0:
            print(_("⚠️ {project}: /{dest}/ could not be added to its git excludes; add it to its .gitignore",
                    project=project, dest=d), file=sys.stderr)


def _top_dest(d: str) -> str:
    """A destination as a session folder names it (a worktree's .../<folder>/<skills dir> → the skills dir)."""
    import runtime_ports
    for rname in runtime_ports.installed():
        for s in runtime_ports.skill_dirs(rname):
            if d == s or d.endswith("/" + s):
                return s
    return d


def owner_notify(*args: str) -> None:
    """One hubbus send as the platform (tests replace it)."""
    r = subprocess.run([*hubconf.launch(pathlib.Path(__file__).resolve().parent / "hubbus"), "send", "--from",
                        hubconf.hub_project(), "--to", hubconf.owner_address(), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise OSError((r.stderr or r.stdout).strip()[-300:])


def close_reminder(man: dict, kind: str, text: str) -> None:
    rem = man.pop("reminder", None)
    if rem and rem.get("key"):
        try:
            owner_notify("--type", kind, "--task", rem["key"], "--text", text, "--context", "skills-sync")
        except OSError as e:
            print(_("⚠️ the owner to-do {key} could not be closed ({why})", key=rem["key"], why=e), file=sys.stderr)


def prune(man: dict, ws, snap: Snapshot) -> bool:
    """Drop what the manifest holds of skills no longer in the source and no longer at their place (gone by hand, or a
    worktree removed); whether anything was dropped."""
    inst, dropped = man.get("installed") or {}, False
    for d in list(inst):
        for n in [n for n in inst[d] if n not in snap.skills and installed_tree(ws, f"{d}/{n}") is None]:
            del inst[d][n]
            dropped = True
    man["installed"] = {d: v for d, v in inst.items() if v}
    return dropped


def sync(project: str, yes: bool, adopt: bool, ask=input, tty=None) -> int:
    why = refuse_here()
    if why:
        print(why, file=sys.stderr)
        return 2
    tty = sys.stdin.isatty() if tty is None else tty
    if not yes and not tty:
        print(_("brunna skills sync asks you to type yes; without a terminal it needs --yes"), file=sys.stderr)
        return 2
    try:
        ws = workspace(project)
        with locked(project):
            snap = snapshot(ws)
            if snap.problems:
                print(_("{project}: {source}/ can't be installed; nothing was installed:", project=project,
                        source=SOURCE), file=sys.stderr)
                for p in snap.problems:
                    print("  " + printable(p), file=sys.stderr)
                return 1
            steps = plan(project, ws, snap, adopt)
            skipped = []
            for folder in dict.fromkeys(folder_of(s.dest) for s in steps):
                tops = list(dict.fromkeys(_top_dest(s.dest) for s in steps if folder_of(s.dest) == folder))
                tracked = still_tracked(project, ws, tops, folder)
                if tracked and not folder:
                    raise Refused(_("{project} still tracks {n} {n:file|files} in its skill folders (e.g. {example}); "
                                    "move them to {source}/ first (docs/design/skills-sync.md, \"Moving a project's "
                                    "skills\")", project=project, n=len(tracked), example=", ".join(tracked[:3]),
                                    source=SOURCE))
                if tracked:   # a worktree from before the move: it gets them once it has the move too
                    skipped.append(printable(folder))
                    steps = [s for s in steps if folder_of(s.dest) != folder]
            todo = [s for s in steps if s.kind in ("add", "change", "remove")]
            blocked = [s for s in steps if s.kind == "blocked"]
            for f in skipped:
                print(_("{folder} still tracks files in its skill folders (a checkout from before they moved to "
                        "{source}/): left out", folder=f, source=SOURCE))
            for line in show(project, snap, steps):
                print(line)
            if blocked:
                print(_("{n} {n:entry|entries} in the skill folders {n:was|were} not installed by sync; look at "
                        "{n:it|them} and run again with --adopt to replace {n:it|them}; nothing was installed",
                        n=len(blocked)), file=sys.stderr)
                return 1
            man = load(project)
            approved = set_hash(snap)
            if not todo:
                print(_("{project}: the skills are installed as they are in {source}/", project=project, source=SOURCE))
                dropped = prune(man, ws, snap)
                if man.get("approved", {}).get("set") != approved or dropped:
                    if man.get("approved", {}).get("set") != approved:
                        man["approved"] = {"set": approved, "at": _now()}
                        close_reminder(man, "done", _c("the skills in {source}/ were reviewed (brunna skills sync)",
                                                       source=SOURCE))
                    save(project, man)
                drop_pending(project)
                return 0
            if not yes and ask(_("Type yes to install: ")).strip() != "yes":
                print(_("nothing was installed"))
                return 1
            exclude(project, ws, [s.dest for s in steps])
            failed = []
            inst = man.setdefault("installed", {})
            for s in todo:
                try:
                    install(ws, s.dest, s.name, s.new)
                except OSError as e:
                    failed.append(_("{dest}/{skill}: {why}", dest=printable(s.dest), skill=s.name,
                                    why=e.strerror or e))
                    continue
                if s.new is None:
                    inst.get(s.dest, {}).pop(s.name, None)
                else:
                    inst.setdefault(s.dest, {})[s.name] = {"hash": tree_hash(meta(s.new)), "files": meta(s.new)}
            prune(man, ws, snap)
            if not failed:
                man["approved"] = {"set": approved, "at": _now()}
                close_reminder(man, "done", _c("the skills in {source}/ were reviewed and installed (brunna skills sync)",
                                               source=SOURCE))
            save(project, man)
            if failed:
                print(_("✗ not installed: {what}", what="; ".join(failed)), file=sys.stderr)
                return 1
            drop_pending(project)
            print(_("✓ {project}: {n} {n:change|changes} installed", project=project, n=len(todo)))
            return 0
    except Refused as e:
        print(str(e), file=sys.stderr)
        return 1


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------- what waits for review, and the reminder ----------

def watched() -> list[tuple[str, pathlib.Path]]:
    """The projects whose skills the platform watches: every sandboxed one, and any sync has installed into."""
    out = []
    for name, doc in sorted(hubconf.projects().items()):
        if name == hubconf.hub_project():
            continue
        ws = hubconf.path((doc.get("paths") or {}).get("workspace"))
        if ws and ws.is_dir() and ((doc.get("enforce") or {}).get("sandbox") is True or manifest_path(name).exists()):
            out.append((name, ws))
    return out


def pending(project: str, ws) -> dict | None:
    """What waits for review in this project's source: None when it is what was last approved (or empty and nothing was
    ever approved); else {set, changed, problems}."""
    snap = snapshot(ws)
    now = set_hash(snap)
    man = load(project)
    if now == (man.get("approved") or {}).get("set", empty_hash()):
        return None
    before = {n: m.get("hash") for d in (man.get("installed") or {}).values() if isinstance(d, dict)
              for n, m in d.items() if isinstance(m, dict)}
    names = set(snap.skills) | set(before)
    changed = sum(1 for n in names if n not in snap.skills or tree_hash(meta(snap.skills[n])) != before.get(n))
    return {"set": now, "changed": changed, "problems": len(snap.problems)}


def key_for(project: str) -> str:
    """A new owner to-do's key: one per review period (a key closed once is never raised again: the same key and
    terms would be nothing new to the owner side)."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", f"skills-{project}")[:100] + \
        datetime.datetime.now().strftime("-%Y%m%d%H%M%S%f")


def remind() -> int:
    """The platform job: per watched project, the pending row for the status page, and one owner to-do per change set
    (the same key amended while it waits; done/drop'd when it no longer does)."""
    if hubconf.worktree_lookup():
        print(_("{root} is a linked worktree of the hub; run brunna skills from the main checkout ({main})",
                root=hubconf.ROOT, main=hubconf.MAIN), file=sys.stderr)
        return 1
    rows, bad = {}, 0
    for project, ws in watched():
        try:
            with locked(project):
                p = pending(project, ws)
                man = load(project)
                rem = man.get("reminder") or {}
                if p is None:
                    if rem:
                        close_reminder(man, "drop", _c("{source}/ is back to what was reviewed", source=SOURCE))
                        save(project, man)
                    continue
                rows[project] = {"changed": p["changed"], "problems": p["problems"],
                                 "since": rem.get("since") or _now()}
                if rem.get("set") == p["set"]:
                    continue
                key = rem.get("key") or key_for(project)
                text = _c("{project}: changes to its skills wait for your review ({n} {n:skill|skills}{problems}); run "
                          "brunna skills sync {project}", project=project, n=p["changed"],
                          problems=_c(", {n} {n:problem|problems}", n=p["problems"]) if p["problems"] else "")
                owner_notify("--type", "task", "--id", key, "--text", text, "--context", "skills-sync")
                man["reminder"] = {"key": key, "set": p["set"], "since": rows[project]["since"], "sent": _now()}
                save(project, man)
        except (OSError, Refused) as e:
            bad += 1
            print(_("⚠️ {project}: its skills could not be checked ({why})", project=project, why=e), file=sys.stderr)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_DIR / f"pending.json.{os.getpid()}"
    tmp.write_text(json.dumps({"at": _now(), "projects": rows}, ensure_ascii=False, indent=1))
    os.replace(tmp, STATE_DIR / "pending.json")
    return 1 if bad else 0


def drop_pending(project: str) -> None:
    """The status page's row goes with the review, not 15 minutes later."""
    f = STATE_DIR / "pending.json"
    try:
        doc = json.loads(f.read_text())
        if (doc.get("projects") or {}).pop(project, None) is None:
            return
    except (OSError, ValueError, AttributeError):
        return
    tmp = STATE_DIR / f"pending.json.{os.getpid()}"
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    os.replace(tmp, f)


def pending_rows() -> list[tuple[str, dict]]:
    """The last reminder run's rows, for the status page and doctor."""
    try:
        doc = json.loads((STATE_DIR / "pending.json").read_text())
        return sorted((doc.get("projects") or {}).items())
    except (OSError, ValueError, AttributeError):
        return []


def pending_text(project: str, row: dict) -> str:
    return _("{project}: changes to its skills wait for your review ({n} {n:skill|skills}{problems}): brunna skills "
             "sync {project}", project=project, n=row.get("changed", 0),
             problems=_(", {n} {n:problem|problems}", n=row["problems"]) if row.get("problems") else "")


def status(project: str | None) -> int:
    rows = []
    for name, ws in watched():
        if project and name != project:
            continue
        p = pending(name, ws)
        rows.append(pending_text(name, p) if p else _("{project}: nothing waits for review", project=name))
    if project and not rows:
        print(_("{project}: its skills aren't watched (not sandboxed, never synced)", project=project))
        return 0
    for r in rows:
        print(r)
    return 0


def usage() -> str:
    return _("usage: brunna skills sync <project> [--yes] [--adopt]\n"
             "       brunna skills status [<project>]")


def main(argv: list[str]) -> int:
    if argv[:1] == ["remind"] and len(argv) == 1:
        return remind()
    if argv[:1] == ["status"] and len(argv) <= 2:
        return status(argv[1] if len(argv) == 2 else None)
    if argv[:1] == ["sync"]:
        rest = [a for a in argv[1:] if a not in ("--yes", "--adopt")]
        if len(rest) == 1 and not rest[0].startswith("-"):
            return sync(rest[0], "--yes" in argv, "--adopt" in argv)
    print(usage(), file=sys.stderr)
    return 0 if argv in ([], ["-h"], ["--help"]) else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
