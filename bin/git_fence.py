#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The git config fence's scan, report only (docs/design/git-config-fence.md, r2's order of work, step 1): for every
sandboxed project, whether anything in its repos can make the owner's git run code or read across, whatever the fence
will later prevent. It neutralises nothing and changes no project file.

  git_fence.py --report [--project P] [--json]   scan now and print; writes nothing (copies in the private temp folder,
                                                 removed afterwards)
  git_fence.py --apply                            the platform job brunna/git-fence: scan, keep the result in
                                                 .state/git-fence/scan.json (the status page's and doctor's rows), alert
                                                 on a new finding (alert.once: a finding is sent once, repeated daily
                                                 while it stays, and its clearing said)

What it looks at, per sandboxed project, in its folders (workspace, runtime folder, allow_write, and the memory clone
its workspace links to):
  - every `.git` entry anywhere (no link followed): the repo's own git dir at a folder's top; a gitfile into a known git
    dir's worktrees/<n> or modules/** (a worktree, a submodule); anything else is a finding (a nested repo, a gitfile
    pointing elsewhere, a link named .git), and folders shaped like a git dir (HEAD, objects, refs) outside any .git;
  - in each git dir (and its worktrees' and modules' own): every config source against the allowlist of inert keys
    (git-config-fence.md, "The allowlist", with r2 S2's additions), parsed from a copy with `git config --file`, so no
    include is followed; hooks other than git's samples and the platform's own (its current text); info/attributes;
    alternates; commondir and gitdir pointers; legacy remotes/ and branches/; every symbolic link, and every file with
    more than one name outside objects/ (git links objects between local clones; they are checked by their hash, and
    can't run anything); index entries the checkout would write into the git dir; rebase state with exec lines (said
    only: a session's own work);
  - the commit gate's state (r2 B3): on or off, the platform's hook in place, and whether this machine can run the
    project's tiers in its sandbox.
And the temp roots (r2 S1): folders shaped like a git dir there, which catch the owner's git run anywhere below them.

How it reads a project's files (r2 S4, S5): opened without following a link and without blocking, a regular file only
(fstat), at most a size cap, the whole scan under a time limit; what git has to parse is copied into hubconf.private_tmp()
first, and git runs only on the copy, from a folder of the platform's own, with no system or global config. No git runs
in a project's repo, so nothing of the project's (filters, hooks, fsmonitor) can run: the scan needs no git facts from
the repo itself (project_git would run them as the project).

Limits, said: a folder swapped for a link between the walk and an open can make the scan read a file the owner can
read (it is only parsed and named in the report); the temp walk covers the user's own temp folder and the shared one,
to a depth, and says when it stopped early.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402
import names  # noqa: E402

_ = i18n.domain(__file__)

RESULT = "git-fence/scan.json"     # under hubconf.STATE
FILE_CAP = 1 << 20                 # a config, a hook, a pointer file
INDEX_CAP = 256 << 20              # an index (large repos have large ones)
WALK_CAP = 400_000                 # entries per project's folders
TEMP_DEPTH, TEMP_CAP = 8, 1_000_000  # the temp walk
TIME_CAP = 300.0                   # seconds per project
GIT_TIMEOUT = 20
NOTE_KINDS = {"rebase-exec"}       # said, never "not holding"
TEMP_KEPT = 200                    # temp findings kept in the result (the count is kept whole)


class Unreadable(Exception):
    pass


# ---------- reading a project's files ----------

def read_capped(path, cap: int = FILE_CAP) -> bytes:
    """A regular file's bytes: no link followed, no blocking open (a FIFO), at most cap."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as e:
        raise Unreadable(_("can't be opened safely ({error})", error=e.strerror or e)) from e
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise Unreadable(_("not a regular file"))
        if st.st_size > cap:
            raise Unreadable(_("larger than {mb} MB", mb=cap >> 20))
        chunks, n = [], 0
        while n <= cap:
            b = os.read(fd, min(1 << 20, cap + 1 - n))
            if not b:
                break
            chunks.append(b)
            n += len(b)
        if n > cap:
            raise Unreadable(_("larger than {mb} MB", mb=cap >> 20))
        return b"".join(chunks)
    finally:
        os.close(fd)


def lexists(p) -> bool:
    return os.path.lexists(p)


def is_dir(p) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(p).st_mode)
    except OSError:
        return False


def is_file(p) -> bool:
    try:
        return stat.S_ISREG(os.lstat(p).st_mode)
    except OSError:
        return False


def git_shaped(d) -> bool:
    """git's own test for a git dir: HEAD, objects/ and refs/ (as entries, no link followed for the folders)."""
    return lexists(os.path.join(d, "HEAD")) and is_dir(os.path.join(d, "objects")) and is_dir(os.path.join(d, "refs"))


# ---------- the allowlist (git-config-fence.md, "The allowlist"; r2 S2) ----------

BOOL = {"true", "false", "yes", "no", "on", "off", "1", "0", ""}
TOKEN = re.compile(r"[A-Za-z0-9_.:-]{0,64}")
REFSIDE = re.compile(r"refs/[^\s:*]*(\*[^\s:*]*)?")


def url_ok(v: str) -> bool:
    if re.fullmatch(r"https://[^\s@/]+(@[^\s/]+)?/\S*", v) or re.fullmatch(r"ssh://[^\s/-][^\s/]*/\S*", v):
        return True
    if re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[^\s]+", v) and not v.split("@", 1)[1].startswith("-"):
        return True
    own = hubconf.p("git_origin")   # the memory bare repos: the platform's own
    try:
        real = pathlib.Path(os.path.realpath(os.path.expanduser(v)))
    except (OSError, ValueError):
        return False
    return bool(own) and v.startswith(("/", "~")) and pathlib.Path(os.path.realpath(own)) in real.parents


def refspec_ok(v: str) -> bool:
    v = v[1:] if v.startswith("+") else v
    src, _sep, dst = v.partition(":")
    return bool(REFSIDE.fullmatch(src)) and (not _sep or not dst or bool(REFSIDE.fullmatch(dst)))


def check_key(key: str, value: str | None, remotes: set[str]) -> str | None:
    """None when the key is in the allowlist with a value that passes its rule; 'key' or 'value' otherwise."""
    v = "" if value is None else value
    low = v.lower()
    section, _sep, rest = key.partition(".")
    sub, _dot, name = rest.rpartition(".")
    section, name = section.lower(), name.lower()
    plain = {
        ("core", "repositoryformatversion"): lambda: v in ("0", "1"),
        ("core", "filemode"): lambda: low in BOOL, ("core", "ignorecase"): lambda: low in BOOL,
        ("core", "precomposeunicode"): lambda: low in BOOL, ("core", "symlinks"): lambda: low in BOOL,
        ("core", "logallrefupdates"): lambda: low in BOOL | {"always"},
        ("core", "bare"): lambda: low in ("false", "no", "off", "0"),
        ("extensions", "objectformat"): lambda: low in ("sha1", "sha256"),
        ("extensions", "refstorage"): lambda: low in ("files", "reftable"),
        ("extensions", "worktreeconfig"): lambda: low in BOOL,
        ("user", "name"): lambda: 0 < len(v) <= 200 and v.isprintable(),
        ("user", "email"): lambda: 0 < len(v) <= 200 and v.isprintable(),
        ("push", "default"): lambda: low in ("current", "simple", "upstream", "nothing"),
        ("remote", "pushdefault"): lambda: v in remotes,
        ("branch", "autosetupmerge"): lambda: low in ("false", "no", "off", "0"),
        ("pull", "rebase"): lambda: low in ("true", "false", "merges"),
        ("pull", "ff"): lambda: low in ("true", "false", "only"),
    }
    if not sub:
        if (section, name) in plain:
            return None if plain[(section, name)]() else "value"
        if section == "maintenance":
            return None if TOKEN.fullmatch(v) else "value"
        return "key"
    if section == "maintenance":
        return None if TOKEN.fullmatch(v) else "value"
    if section == "remote":
        rule = {"url": lambda: url_ok(v), "pushurl": lambda: url_ok(v), "fetch": lambda: refspec_ok(v),
                "push": lambda: refspec_ok(v), "prune": lambda: low in BOOL,
                "tagopt": lambda: v in ("--tags", "--no-tags")}.get(name)
    elif section == "branch":
        rule = {"remote": lambda: v in remotes or v == ".", "pushremote": lambda: v in remotes or v == ".",
                "merge": lambda: bool(re.fullmatch(r"refs/heads/\S+", v)),
                "rebase": lambda: low in ("true", "false", "merges")}.get(name)
    elif section == "submodule":
        rule = {"url": lambda: url_ok(v), "active": lambda: low in BOOL}.get(name)
    else:
        rule = None
    if rule is None:
        return "key"
    return None if rule() else "value"


def config_pairs(copy: pathlib.Path, work: pathlib.Path) -> list[tuple[str, str | None]]:
    """(key, value) of a config file copy, as git reads it, includes not followed (--file reads only that file)."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(work), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CEILING_DIRECTORIES": str(work.parent), "LC_ALL": "C"}
    try:
        r = subprocess.run(["git", "config", "--file", str(copy), "--null", "--list"], cwd=work, env=env,
                           capture_output=True, timeout=GIT_TIMEOUT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:
        raise Unreadable(_("git took too long to read it")) from e
    if r.returncode != 0:
        raise Unreadable(_("git can't read it ({error})", error=r.stderr.decode(errors="replace").strip()[-160:]))
    out = []
    for item in r.stdout.decode(errors="replace").split("\0"):
        if not item:
            continue
        k, nl, v = item.partition("\n")
        out.append((k, v if nl else None))
    return out


# ---------- the index ----------

IGNORABLE = {0x200C, 0x200D, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x206A, 0x2070), 0xFEFF}


def into_git_dir(path: bytes) -> bool:
    """Whether an index entry's path has a component git treats as its own folder: compared as git's own path check
    does it (any case, the characters HFS+ ignores left out, trailing dots and spaces, the short name)."""
    for comp in path.split(b"/"):
        s = comp.decode("utf-8", errors="replace")
        s = "".join(ch for ch in s if ord(ch) not in IGNORABLE).casefold()
        t = s.rstrip(". ")
        if t in (".git", "git~1") or s.startswith((".git:", "git~1:")):
            return True
    return False


def _varint(data: bytes, off: int) -> tuple[int, int]:
    c = data[off]
    off += 1
    val = c & 0x7F
    while c & 0x80:
        val += 1
        c = data[off]
        off += 1
        val = (val << 7) + (c & 0x7F)
    return val, off


def index_paths(data: bytes, hash_len: int = 20) -> list[bytes]:
    """The paths of an index file (versions 2 to 4; a split index's shared part has the same format)."""
    if len(data) < 12 or data[:4] != b"DIRC":
        raise ValueError("not an index")
    version, count = struct.unpack(">II", data[4:12])
    if version not in (2, 3, 4):
        raise ValueError(f"version {version}")
    off, prev, out = 12, b"", []
    try:
        for _i in range(count):
            start = off
            off += 40 + hash_len
            (flags,) = struct.unpack(">H", data[off:off + 2])
            off += 2
            if version >= 3 and flags & 0x4000:
                off += 2
            if version == 4:
                strip, off = _varint(data, off)
                end = data.index(b"\0", off)
                if strip > len(prev):
                    raise ValueError("bad prefix")
                path = prev[:len(prev) - strip] + data[off:end]
                off = end + 1
            else:
                end = data.index(b"\0", off)
                path = data[off:end]
                off = start + ((off - start) + len(path) + 8 & ~7)
            if off > len(data):
                raise ValueError("truncated")
            out.append(path)
            prev = path
    except (struct.error, IndexError) as e:
        raise ValueError("truncated") from e
    return out


# ---------- findings ----------

def finding(kind: str, where: str, **detail) -> dict:
    key = detail.get("key") or detail.get("name") or detail.get("path") or ""
    return {"id": f"{kind}:{where}:{key}", "kind": kind, "where": where, **detail}


def describe(f: dict) -> str:
    w, k = f.get("where", ""), f.get("kind")
    texts = {
        "key": lambda: _("{where}: {key} is not in the allowlist of inert keys", where=w, key=f.get("key")),
        "include": lambda: _("{where}: an include ({key}), which the scan doesn't follow", where=w, key=f.get("key")),
        "hookspath": lambda: _("{where}: core.hooksPath is set ({value}): git takes its hooks from there", where=w,
                               value=f.get("value")),
        "value": lambda: _("{where}: {key} has a value its rule doesn't allow ({value})", where=w, key=f.get("key"),
                           value=f.get("value")),
        "hook": lambda: _("{where}: a hook the platform didn't write ({name})", where=w, name=f.get("name")),
        "attributes": lambda: _("{where}: info/attributes isn't empty", where=w),
        "alternates": lambda: _("{where}: {name} points git at another object store", where=w, name=f.get("name")),
        "commondir": lambda: _("{where}: a commondir pointer git would follow ({value})", where=w, value=f.get("value")),
        "gitdir": lambda: _("{where}: a worktree pointer that doesn't lead back ({value})", where=w,
                            value=f.get("value")),
        "legacy": lambda: _("{where}: {name}/ isn't empty (remotes git reads from files)", where=w, name=f.get("name")),
        "link": lambda: _("{where}: a symbolic link in the git dir ({path})", where=w, path=f.get("path")),
        "hardlink": lambda: _("{where}: a file in the git dir with more than one name ({path})", where=w,
                              path=f.get("path")),
        "index": lambda: _("{where}: {n} index {n:entry|entries} the checkout would write into the git dir ({path})",
                           where=w, n=f.get("n", 1), path=f.get("path")),
        "nested": lambda: _("{where}: a repo nested in the project's folders", where=w),
        "gitfile": lambda: _("{where}: a .git file pointing outside the project's git dirs ({value})", where=w,
                             value=f.get("value")),
        "dotgit-link": lambda: _("{where}: .git is a symbolic link", where=w),
        "shaped": lambda: _("{where}: a folder shaped like a git dir (HEAD, objects, refs) outside any .git", where=w),
        "unreadable": lambda: _("{where}: {path} can't be checked: {why}", where=w, path=f.get("path"),
                                why=f.get("why")),
        "incomplete": lambda: _("{where}: the scan stopped early ({why})", where=w, why=f.get("why")),
        "rebase-exec": lambda: _("{where}: a rebase in progress has exec lines (a session's own work; said only)",
                                 where=w),
    }
    return texts.get(k, lambda: f"{w}: {k}")()


# ---------- one project ----------

class Scan:
    def __init__(self, name: str, doc: dict, work: pathlib.Path, deadline: float):
        self.name, self.doc, self.work, self.deadline = name, doc, work, deadline
        self.findings: list[dict] = []
        self.notes: list[dict] = []
        self.repos = 0
        self.wanted_hooks = platform_hooks({**doc, "name": name})
        self.labels: list[tuple[pathlib.Path, str]] = []
        self.copies = 0

    def add(self, f: dict) -> None:
        (self.notes if f["kind"] in NOTE_KINDS else self.findings).append(f)

    def where(self, p) -> str:
        real = os.path.abspath(p)
        for root, label in self.labels:
            r = str(root)
            if real == r:
                return label
            if real.startswith(r + os.sep):
                return f"{label}/{os.path.relpath(real, r)}"
        return real

    def late(self) -> bool:
        return time.monotonic() > self.deadline

    def copy(self, path, cap: int = FILE_CAP) -> pathlib.Path:
        data = read_capped(path, cap)
        self.copies += 1
        c = self.work / f"c{self.copies}"
        c.write_bytes(data)
        return c

    # --- the folders ---
    def run(self) -> dict:
        roots = project_roots(self.name, self.doc)
        self.labels = [(r, label) for r, label in roots]
        git_entries: list[tuple[pathlib.Path, pathlib.Path]] = []   # (the .git entry, the root it was found under)
        seen = 0
        for root, _label in roots:
            stack = [root]
            while stack:
                if self.late() or seen > WALK_CAP:
                    self.add(finding("incomplete", self.where(root),
                                     why=_("time limit") if self.late() else _("{n} entries", n=WALK_CAP)))
                    stack = []
                    break
                d = stack.pop()
                try:
                    with os.scandir(d) as it:
                        entries = list(it)
                except OSError:
                    continue
                for e in entries:
                    seen += 1
                    if e.name.casefold() == ".git":
                        git_entries.append((pathlib.Path(e.path), root))
                        continue
                    if e.is_dir(follow_symlinks=False):
                        if git_shaped(e.path):
                            self.add(finding("shaped", self.where(e.path)))
                        stack.append(e.path)
        known: list[pathlib.Path] = []   # real git dirs found, whose worktrees/ and modules/ hold the expected gitfiles
        gitfiles = []
        for entry, root in git_entries:
            top = entry.parent == root
            if os.path.islink(entry):
                self.add(finding("dotgit-link", self.where(entry)))
            elif is_dir(entry):
                if not top:
                    self.add(finding("nested", self.where(entry.parent)))
                known.append(entry)
            else:
                gitfiles.append((entry, top))
        for gd in known:
            self.git_dir(gd, main=True)
        for entry, _top in gitfiles:
            self.gitfile(entry, known)
        return self.result()

    def gitfile(self, entry: pathlib.Path, known: list[pathlib.Path]) -> None:
        try:
            text = read_capped(entry).decode(errors="replace").strip()
        except Unreadable as e:
            self.add(finding("unreadable", self.where(entry.parent), path=".git", why=str(e)))
            return
        if not text.startswith("gitdir:"):
            self.add(finding("gitfile", self.where(entry.parent), value=text[:120]))
            return
        target = text[len("gitdir:"):].strip()
        real = pathlib.Path(os.path.realpath(entry.parent / target))
        for gd in known:
            g = pathlib.Path(os.path.realpath(gd))
            for kind in ("worktrees", "modules"):
                if (g / kind) in real.parents:
                    return
        self.add(finding("gitfile", self.where(entry.parent), value=target[:160]))

    # --- one git dir ---
    def git_dir(self, gd: pathlib.Path, main: bool) -> None:
        self.repos += main
        w = self.where(gd)
        hash_len = 20
        for src in [gd / "config", gd / "config.worktree",
                    *sorted(p / "config.worktree" for p in children(gd / "worktrees"))]:
            if not lexists(src):
                continue
            pairs = self.config(src)
            if any(k.lower() == "extensions.objectformat" and (v or "").lower() == "sha256" for k, v in pairs):
                hash_len = 32
        if main and lexists(gd / "commondir"):
            self.add(finding("commondir", w, value=self.text(gd / "commondir")))
        self.hooks(gd)
        attrs = gd / "info" / "attributes"
        if lexists(attrs):
            try:
                if read_capped(attrs).strip():
                    self.add(finding("attributes", w))
            except Unreadable as e:
                self.add(finding("unreadable", w, path="info/attributes", why=str(e)))
        for alt in ("alternates", "http-alternates"):
            if lexists(gd / "objects" / "info" / alt):
                self.add(finding("alternates", w, name=f"objects/info/{alt}"))
        for legacy in ("remotes", "branches"):
            if children(gd / legacy) or (lexists(gd / legacy) and not is_dir(gd / legacy)):
                self.add(finding("legacy", w, name=legacy))
        for wt in children(gd / "worktrees"):
            self.worktree_admin(wt)
        self.links(gd)
        for idx in [gd / "index", *(p / "index" for p in children(gd / "worktrees")),
                    *sorted(gd.glob("sharedindex.*"))]:
            if lexists(idx):
                self.index(idx, hash_len)
        for todo in [gd / "rebase-merge" / "git-rebase-todo",
                     *(p / "rebase-merge" / "git-rebase-todo" for p in children(gd / "worktrees"))]:
            if lexists(todo):
                try:
                    lines = read_capped(todo).decode(errors="replace").splitlines()
                except Unreadable:
                    continue
                if any(re.match(r"\s*(x|exec)\s", ln) for ln in lines):
                    self.add(finding("rebase-exec", self.where(todo.parent)))
        modules = gd / "modules"
        if is_dir(modules):
            for sub in sorted(walk_git_dirs(modules)):
                self.git_dir(sub, main=False)

    def text(self, p) -> str:
        try:
            return read_capped(p).decode(errors="replace").strip()[:160]
        except Unreadable as e:
            return str(e)

    def config(self, src: pathlib.Path) -> list[tuple[str, str | None]]:
        w = self.where(src)
        try:
            pairs = config_pairs(self.copy(src), self.work)
        except Unreadable as e:
            self.add(finding("unreadable", self.where(src.parent), path=src.name, why=str(e)))
            return []
        remotes = {k.split(".", 1)[1].rsplit(".", 1)[0] for k, _v in pairs
                   if k.lower().startswith("remote.") and k.count(".") >= 2}
        for k, v in pairs:
            low = k.lower()
            if low.startswith(("include.", "includeif.")):
                self.add(finding("include", w, key=k))
            elif low == "core.hookspath":
                self.add(finding("hookspath", w, key=k, value=(v or "")[:160]))
            elif (why := check_key(k, v, remotes)):
                self.add(finding(why, w, key=k, **({"value": (v or "")[:120]} if why == "value" else {})))
        return pairs

    def hooks(self, gd: pathlib.Path) -> None:
        hooks = gd / "hooks"
        if lexists(hooks) and not is_dir(hooks):
            self.add(finding("link" if os.path.islink(hooks) else "unreadable", self.where(gd), path="hooks",
                             why=_("not a folder")))
            return
        for h in children(hooks, files=True):
            if h.name.endswith(".sample"):
                continue
            try:
                body = read_capped(h).decode(errors="replace")
            except Unreadable:
                body = None
            if body is None or self.wanted_hooks.get(h.name) != body:
                self.add(finding("hook", self.where(gd), name=h.name))

    def worktree_admin(self, wt: pathlib.Path) -> None:
        w = self.where(wt)
        if lexists(wt / "commondir"):
            v = self.text(wt / "commondir")
            if v not in ("../..", "../../"):
                self.add(finding("commondir", w, value=v))
        if lexists(wt / "gitdir"):
            target = self.text(wt / "gitdir")
            back = pathlib.Path(target)
            ok = False
            if back.is_absolute() and is_file(back):
                try:
                    t = read_capped(back).decode(errors="replace").strip()
                    ok = t.startswith("gitdir:") and os.path.realpath(
                        back.parent / t[len("gitdir:"):].strip()) == os.path.realpath(wt)
                except Unreadable:
                    ok = False
            if not ok and lexists(back):
                self.add(finding("gitdir", w, value=target))

    def links(self, gd: pathlib.Path) -> None:
        own = set(names.all("repo_dir"))
        stack = [(gd, "")]
        n = 0
        while stack:
            d, rel = stack.pop()
            try:
                with os.scandir(d) as it:
                    entries = list(it)
            except OSError:
                continue
            for e in entries:
                n += 1
                if n > WALK_CAP or self.late():
                    self.add(finding("incomplete", self.where(gd), why=_("the git dir is too large to walk")))
                    return
                r = f"{rel}{e.name}"
                if not rel and e.name in own:
                    continue   # the platform's own folder (its caches make links: git-config-fence.md r2 B2)
                if e.is_symlink():
                    self.add(finding("link", self.where(gd), path=r))
                elif e.is_dir(follow_symlinks=False):
                    if not (rel == "" and e.name == "modules"):   # each module is a git dir of its own, walked there
                        stack.append((e.path, r + "/"))
                elif not r.startswith("objects/"):
                    try:
                        if e.stat(follow_symlinks=False).st_nlink > 1:
                            self.add(finding("hardlink", self.where(gd), path=r))
                    except OSError:
                        pass

    def index(self, idx: pathlib.Path, hash_len: int) -> None:
        w = self.where(idx.parent)
        try:
            paths = index_paths(read_capped(idx, INDEX_CAP), hash_len)
        except Unreadable as e:
            self.add(finding("unreadable", w, path=idx.name, why=str(e)))
            return
        except ValueError as e:
            self.add(finding("unreadable", w, path=idx.name, why=_("not an index git can read ({error})", error=e)))
            return
        bad = [p for p in paths if into_git_dir(p)]
        if bad:
            self.add(finding("index", w, path=idx.name, n=len(bad)))

    def result(self) -> dict:
        return {"holds": not self.findings, "findings": self.findings, "notes": self.notes, "repos": self.repos,
                "commit_gate": commit_gate(self.name, self.doc, self.wanted_hooks)}


def children(d, files: bool = False) -> list[pathlib.Path]:
    """A folder's entries (no link followed into it): its folders, or everything else in it (files, links, FIFOs)."""
    if not is_dir(d):
        return []
    try:
        with os.scandir(d) as it:
            return sorted(pathlib.Path(e.path) for e in it if e.is_dir(follow_symlinks=False) != files)
    except OSError:
        return []


def walk_git_dirs(top: pathlib.Path):
    """The git dirs under .git/modules (a module's own modules too), no link followed."""
    for d in children(top):
        if git_shaped(d):
            yield d
        else:
            yield from walk_git_dirs(d)


def project_roots(name: str, doc: dict) -> list[tuple[pathlib.Path, str]]:
    """(folder, label) for each folder the project's sandbox may write, resolved, and the memory clone its workspace links
    to; the label names it in findings without the machine's paths."""
    import registry
    d = registry.dirs({**doc, "name": name})
    out = []
    for p, label in ((d.get("workspace"), "workspace"), (d.get("runtime"), "runtime")):
        if p:
            out.append((pathlib.Path(os.path.realpath(p)), label))
    for i, w in enumerate(doc.get("allow_write") or []):
        if (p := hubconf.path(w)):
            out.append((pathlib.Path(os.path.realpath(p)), f"allow_write[{i}]"))
    ws = d.get("workspace")
    mem = pathlib.Path(ws) / ".agent-memory" if ws else None
    if mem and os.path.islink(mem):
        out.append((pathlib.Path(os.path.realpath(mem)), "memory"))
    seen, res = set(), []
    for p, label in out:
        if p not in seen and is_dir(p) and not any(q in p.parents for q, _l in out if q != p):
            seen.add(p)
            res.append((p, label))
    return res


def platform_hooks(doc: dict) -> dict[str, str]:
    """The hooks the platform writes for this project, by name: their current text (render-project-settings)."""
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("render_project_settings_gf",
                                                  str(hubconf.ROOT / "bin" / "render-project-settings.py"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return {hook: text for hook, (text, _why) in mod.wanted_hooks(doc).items()}


def commit_gate(name: str, doc: dict, wanted: dict) -> dict:
    """r2 B3: the commit gate on or off, the platform's hook in place, and whether its tiers can run in the project's
    sandbox on this machine (otherwise every commit stops)."""
    import isolation
    on = doc.get("commit_gate") is True
    out = {"on": on}
    if not on:
        return out
    import registry
    ws = registry.dirs({**doc, "name": name}).get("workspace")
    try:
        body = read_capped(pathlib.Path(ws) / ".git" / "hooks" / "pre-commit").decode(errors="replace") if ws else None
    except Unreadable:
        body = None
    out["hook"] = "current" if body is not None and body == wanted.get("pre-commit") else \
        ("missing" if body is None else "other")
    out["exempt"] = not isolation.sandboxed({**doc, "name": name})
    out["sandbox"] = isolation.can_sandbox() or ""
    return out


# ---------- the temp area (r2 S1) ----------

def temp_roots(given=None) -> list[pathlib.Path]:
    """The user's own temp folder and the shared one this system's sandboxes write (macOS's /private/var/folders holds
    every user's: only one's own is walked); given: these instead (--temp-root)."""
    shared = ["/private/tmp"] if sys.platform == "darwin" else ["/tmp", "/var/tmp"]
    out = []
    for p in given or [tempfile.gettempdir(), *shared]:
        r = pathlib.Path(os.path.realpath(p))
        if r.is_dir() and r not in out:
            out.append(r)
    return out


def scan_temp(deadline: float, roots=None) -> list[dict]:
    """Folders shaped like a git dir in the temp roots (not named .git, not inside one): the owner's git run anywhere
    below one takes it for a repo. To a depth, a count and the time limit; stopping early is said."""
    found, seen = [], 0
    private = pathlib.Path(os.path.realpath(hubconf.secrets_dir()))
    for root in temp_roots(roots):
        stack = [(str(root), 0)]
        while stack:
            if time.monotonic() > deadline or seen > TEMP_CAP:
                found.append(finding("incomplete", str(root), why=_("time limit") if time.monotonic() > deadline
                                     else _("{n} entries", n=TEMP_CAP)))
                return found
            d, depth = stack.pop()
            try:
                with os.scandir(d) as it:
                    entries = list(it)
            except OSError:
                continue
            for e in entries:
                seen += 1
                if e.name.casefold() == ".git" or not e.is_dir(follow_symlinks=False):
                    continue
                if pathlib.Path(e.path) == private or private in pathlib.Path(e.path).parents:
                    continue
                if git_shaped(e.path):
                    found.append(finding("shaped", e.path))
                    continue   # its own folders are git's
                if depth + 1 < TEMP_DEPTH:
                    stack.append((e.path, depth + 1))
    return found


# ---------- the whole machine ----------

def sandboxed_projects(only: str | None = None) -> dict[str, dict]:
    return {n: d for n, d in sorted(hubconf.projects().items())
            if (d.get("enforce") or {}).get("sandbox") is True and (only is None or n == only)}


def scan(only: str | None = None, temp: bool = True, temp_given=None) -> dict:
    out = {"v": 1, "at": datetime.datetime.now().isoformat(timespec="seconds"), "projects": {}, "temp": []}
    work_top = pathlib.Path(tempfile.mkdtemp(prefix="git-fence.", dir=hubconf.private_tmp()))
    try:
        for name, doc in sandboxed_projects(only).items():
            work = work_top / name
            work.mkdir()
            try:
                out["projects"][name] = Scan(name, doc, work, time.monotonic() + TIME_CAP).run()
            except Exception as e:   # one project's trouble is said; the next is still scanned
                out["projects"][name] = {"holds": False, "findings": [finding("incomplete", "workspace",
                                                                              why=f"{type(e).__name__}: {e}")],
                                         "notes": [], "repos": 0, "commit_gate": {}}
        if temp:
            found = scan_temp(time.monotonic() + TIME_CAP, temp_given)
            shaped = [f for f in found if f["kind"] == "shaped"]
            out["temp_shaped"] = len(shaped)
            out["temp"] = shaped[:TEMP_KEPT] + [f for f in found if f["kind"] != "shaped"]
    finally:
        shutil.rmtree(work_top, ignore_errors=True)
    return out


def result_path() -> pathlib.Path:
    return hubconf.STATE / RESULT


def load() -> dict | None:
    try:
        got = json.loads(result_path().read_text())
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) and isinstance(got.get("projects"), dict) else None


def gate_text(g: dict) -> str:
    if not g or not g.get("on"):
        return _("commit gate off")
    if g.get("exempt"):
        return _("commit gate on; exempt from the sandbox, so its tiers run as whoever commits")
    if g.get("sandbox"):
        return _("commit gate on, but this machine can't sandbox ({why}): every commit stops", why=g["sandbox"])
    return {"current": _("commit gate on; its tiers run in the project's sandbox"),
            "missing": _("commit gate on, but the platform's pre-commit isn't in place"),
            "other": _("commit gate on, but the pre-commit isn't the platform's current one")}.get(g.get("hook"), "")


def rows(rep: dict | None = None) -> list[tuple[str, str, str, list[str]]]:
    """(project, 'ok'|'warn', its one line, every finding's line) per sandboxed project, then the temp area's row
    ('temp area'): what the status page and doctor show."""
    rep = rep if rep is not None else load()
    if rep is None:
        return []
    out = []
    for name, p in sorted(rep["projects"].items()):
        fs = [describe(f) for f in p.get("findings") or []]
        notes = [describe(f) for f in p.get("notes") or []]
        line = (_("holds ({gate})", gate=gate_text(p.get("commit_gate") or {})) if not fs else
                _("not holding: {first}{more} ({gate})", first=fs[0],
                  more=_(" (+{n} more)", n=len(fs) - 1) if len(fs) > 1 else "",
                  gate=gate_text(p.get("commit_gate") or {})))
        out.append((name, "ok" if not fs else "warn", line, fs + notes))
    shaped = [f for f in rep.get("temp") or [] if f.get("kind") == "shaped"]
    stopped = [f for f in rep.get("temp") or [] if f.get("kind") != "shaped"]
    if shaped or stopped:
        line = (_("temp area: {n} {n:folder|folders} shaped like a git dir, e.g. {first}",
                  n=rep.get("temp_shaped") or len(shaped), first=shaped[0]["where"]) if shaped else _("temp area: nothing shaped like a git dir found"))
        if stopped:
            line += _("; the walk stopped early ({why})", why=stopped[0].get("why"))
        out.append(("", "warn" if shaped else "ok", line, [describe(f) for f in shaped + stopped]))
    return out


def problems(rep: dict) -> tuple[list[str], list[str]]:
    """(lines, ids) for alert.once: every finding of every project and the temp area, each with a stable id."""
    lines, ids = [], []
    for name, p in sorted(rep["projects"].items()):
        for f in p.get("findings") or []:
            lines.append(f"{name}: {describe(f)}")
            ids.append(f"{name}:{f['id']}")
    shaped = [f for f in rep.get("temp") or [] if f.get("kind") == "shaped"]
    if shaped:   # one problem for the area, however many: a test run can leave thousands
        n = rep.get("temp_shaped") or len(shaped)
        lines.append(_("temp area: {n} {n:folder|folders} shaped like a git dir, e.g. {first}", n=n,
                       first=shaped[0]["where"]))
        ids.append("temp:shaped")
    return lines, ids


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="git_fence.py")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--report", action="store_true")
    mode.add_argument("--apply", action="store_true")
    ap.add_argument("--project")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-temp", action="store_true")
    ap.add_argument("--temp-root", action="append", help="walk this instead of the system's temp roots")
    a = ap.parse_args(argv)
    rep = scan(a.project, temp=not a.no_temp, temp_given=a.temp_root)
    if a.apply:
        hubconf.write_atomic(result_path(), json.dumps(rep, ensure_ascii=False, indent=1))
        import alert
        lines, ids = problems(rep)
        alert.once("git-fence", lines, _("the git config fence's scan finds nothing any more"), ids=ids)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0
    if not rep["projects"]:
        print(_("no sandboxed project: nothing to scan"))
    for name, level, line, details in rows(rep):
        print(f"{'✓' if level == 'ok' else '✗'} {name + ': ' if name else ''}{line}")
        for d in details[1:] if level == "warn" else details:
            print(f"    {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
