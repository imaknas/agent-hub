#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The git config fence's scan, report only (docs/design/git-config-fence.md, r2's order of work, step 1): for every
sandboxed project, a best-effort look for anything in its repos that can make the owner's git run code or read across.
It is detection, not a guarantee: it re-reads what git reads, and each review of it found more ways a re-reading can
differ from git's own. The guarantee has to come from the fence preventing the writes; nothing may take this scan's
"no known problem found" as proof (the confined checks don't). It neutralises nothing and changes no project file.

  git_fence.py --report [--project P] [--json] [--no-temp] [--temp-root D]...
                        scan now and print; writes nothing (copies in the private temp folder, removed afterwards)
  git_fence.py --apply  the platform job brunna/git-fence (every 15 minutes): every sandboxed project scanned, the temp
                        area at most once a day (its last result kept in between), the result in
                        .state/git-fence/scan.json for the status page's and doctor's rows, and an alert on a new
                        project finding (alert.once; the temp area is on the status page only). It takes no narrowing
                        option: a partial scan would end findings it never looked at

What it looks at, per sandboxed project, in its folders (workspace, runtime folder, allow_write, the memory clone its
workspace links to), with no link followed:
  - every `.git` entry: the repo's own git dir at a folder's top; a gitfile accepted only when git would read it as a
    pointer and it leads exactly to a git dir this scan inspected (a worktree's admin dir, a module's git dir); anything
    else is a finding (a nested repo, a gitfile pointing elsewhere or written so git reads it differently, a link named
    .git), as is any folder shaped like a git dir by git's own rule (HEAD and objects and refs as entries of any kind,
    or HEAD and commondir), the folders themselves included;
  - each git dir: its config sources against the allowlist of inert keys (git-config-fence.md, "The allowlist", r2 S2
    and Step 1's additions), parsed from a copy so no include is followed; hooks other than git's samples and the
    platform's own (its text in any UI language); info/attributes; alternates; commondir and gitdir pointers, read as
    git reads them; remotes/ and branches/ entries; every symbolic link, and every file with more than one name outside
    the objects/ of a repo's or a module's git dir (git links objects between local clones, and checks them by their
    hash); the index entries the scan reports (a split index's shared part as the index names it); rebase state with
    exec lines (said only). Worktrees' admin dirs and every folder under modules/ (a worktree's too) are inspected the
    same way;
  - the commit gate's state (r2 B3).
And the temp roots (r2 S1), breadth first: a `.git` at a root (it makes the whole area a checkout), every other repo
inspected as a project's is, folders shaped like a git dir, and folders of the owner's it can't list.

How it reads (r2 S4, S5): every file opened without following a link and without blocking, a regular file only, at most
a size cap; a folder it can't list is a finding, never skipped; the time limit is checked per folder entry; what git must
parse is copied into hubconf.private_tmp() and git runs only on the copy, from a folder of the platform's own, with no
system or global config. No git runs in a project's repo, so nothing of the project's can run, and it needs no git facts
through project_git.

Limits, said: a folder swapped for a link between the walk and an open (a git dir's `info`, its `rebase-merge`, the
commit gate's `.git`) is opened by path, not by a descriptor held from the walk; a link there is itself a finding of the
same scan, but one made between two steps can make the scan read a file the owner can read (only parsed and named).
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import datetime
import hashlib
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
WALK_CAP = 400_000                 # entries per project
FINDINGS_CAP = 300                 # findings kept per project (the rest counted)
TEMP_DEPTH, TEMP_CAP = 8, 1_000_000
TEMP_KEPT = 200                    # temp findings kept in the result (the count is kept whole)
TEMP_EVERY = datetime.timedelta(hours=24)
TIME_CAP = float(os.environ.get("GIT_FENCE_TIME_CAP") or 300)   # seconds per project, and for the temp walk
GIT_TIMEOUT = 20
NOTE_KINDS = {"rebase-exec"}       # said, never a problem
EVERY_MIN = 15                     # the job's period: the detection window the rows state


class Unreadable(Exception):
    pass


class Late(Exception):
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


def pointer(path, prefix: bytes = b"") -> tuple[str | None, str | None]:
    """(the path git reads from a pointer file, what is odd about it) as git reads one: the prefix with its space, then
    the rest with only trailing CR and LF trimmed, ended by a NUL if there is one. Any other leading or trailing
    whitespace is odd (git keeps it as part of the path). Raises Unreadable."""
    raw = read_capped(path)
    if prefix:
        if not raw.startswith(prefix):
            return None, _("not a pointer git reads")
        raw = raw[len(prefix):]
    raw = raw.rstrip(b"\r\n")
    odd = None
    if b"\0" in raw:
        raw, odd = raw.split(b"\0", 1)[0], _("a NUL in it")
    if raw != raw.strip():
        odd = odd or _("whitespace around the path")
    if not raw:
        return None, odd or _("empty")
    return os.fsdecode(raw), odd


def lexists(p) -> bool:
    return os.path.lexists(p)


def is_dir(p) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(p).st_mode)
    except OSError:
        return False


def git_shaped(d) -> bool:
    """git's own test for a git dir, loosened to what can be planted: HEAD (any kind of entry) with objects and refs
    (any kind: a link, a file), or HEAD with a commondir."""
    j = os.path.join
    return lexists(j(d, "HEAD")) and ((lexists(j(d, "objects")) and lexists(j(d, "refs"))) or lexists(j(d, "commondir")))


def shown(p) -> str:
    """A path as text that always encodes (a name that isn't UTF-8 keeps its bytes as escapes)."""
    return os.fsencode(str(p)).decode("utf-8", "backslashreplace")


def no_creds(v) -> str:
    """A value with a URL's credentials left out (what is kept in the result)."""
    return re.sub(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/@\s]*@", r"\1***@", str(v))


def short(v, n: int) -> str:
    """A value kept in a finding: credentials left out first, then cut (a cut could split them)."""
    return no_creds("" if v is None else v)[:n]


def clean(v) -> str:
    """A value from a project's files, made safe to print or send: a URL's credentials left out, control and invisible
    characters escaped, an @ that would mention someone on a chat channel broken (by a zero-width space)."""
    s = no_creds(v)
    s = "".join(ch if ch.isprintable() else f"\\x{ord(ch):02x}" if ord(ch) < 0x100 else f"\\u{ord(ch):04x}"
                for ch in s)
    return s.replace("@", "@​")


# ---------- the allowlist (git-config-fence.md, "The allowlist"; r2 S2; Step 1 as built) ----------

BOOL = {"true", "false", "yes", "no", "on", "off", "1", "0", ""}
TOKEN = re.compile(r"[A-Za-z0-9_.:-]{0,64}")
REFNAME = re.compile(r"[A-Za-z0-9_./-]{1,200}")
REFSIDE = re.compile(r"refs/[^\s:*]*(\*[^\s:*]*)?")


def url_ok(v: str, own: tuple = ()) -> bool:
    """https://, ssh:// or user@host:path (no host that reads as an option), or one of the project's own platform
    repos (its memory bare repo)."""
    if re.fullmatch(r"https://[^\s@/]+(@[^\s/]+)?/\S*", v) or re.fullmatch(r"ssh://[^\s/-][^\s/]*/\S*", v):
        return True
    if re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9][A-Za-z0-9.-]*:[^\s]+", v):
        return True
    try:
        real = os.path.realpath(os.path.expanduser(v))
    except (OSError, ValueError):
        return False
    return v.startswith(("/", "~")) and any(real == os.path.realpath(o) for o in own)


def refspec_ok(v: str) -> bool:
    v = v[1:] if v.startswith("+") else v
    src, sep, dst = v.partition(":")
    return bool(REFSIDE.fullmatch(src)) and (not sep or not dst or bool(REFSIDE.fullmatch(dst)))


def check_key(key: str, value: str | None, remotes: set[str], own: tuple = (), module_rule=None) -> str | None:
    """None when the key is in the allowlist with a value that passes its rule; 'key' or 'value' otherwise.
    module_rule: in a module's git dir, the rule for the core.worktree git writes there."""
    v = "" if value is None else value
    low = v.lower()
    section, _sep, rest = key.partition(".")
    sub, _dot, name = rest.rpartition(".")
    section, name = section.lower(), name.lower()

    def printable(n):
        return 0 < len(v) <= n and all(ch.isprintable() or ch == "\n" for ch in v)
    plain = {
        ("core", "repositoryformatversion"): lambda: v in ("0", "1"),
        ("core", "filemode"): lambda: low in BOOL, ("core", "ignorecase"): lambda: low in BOOL,
        ("core", "precomposeunicode"): lambda: low in BOOL, ("core", "symlinks"): lambda: low in BOOL,
        ("core", "logallrefupdates"): lambda: low in BOOL | {"always"},
        ("core", "bare"): lambda: low in ("false", "no", "off", "0"),
        ("core", "autocrlf"): lambda: low in BOOL | {"input"},
        ("extensions", "objectformat"): lambda: low in ("sha1", "sha256"),
        ("extensions", "refstorage"): lambda: low in ("files", "reftable"),
        ("extensions", "worktreeconfig"): lambda: low in BOOL,
        ("user", "name"): lambda: printable(200) and "\n" not in v,
        ("user", "email"): lambda: printable(200) and "\n" not in v,
        ("push", "default"): lambda: low in ("current", "simple", "upstream", "nothing"),
        ("remote", "pushdefault"): lambda: v in remotes,
        ("branch", "autosetupmerge"): lambda: low in ("false", "no", "off", "0"),
        ("pull", "rebase"): lambda: low in ("true", "false", "merges"),
        ("pull", "ff"): lambda: low in ("true", "false", "only"),
        ("lfs", "repositoryformatversion"): lambda: v == "0",
        ("lfs", "locksverify"): lambda: low in BOOL,
    }
    if not sub:
        if section == "core" and name == "worktree" and module_rule:
            return None if module_rule(v) else "value"
        if (section, name) in plain:
            return None if plain[(section, name)]() else "value"
        if section == "maintenance":
            return None if TOKEN.fullmatch(v) else "value"
        return "key"
    if section == "maintenance":
        return None if TOKEN.fullmatch(v) else "value"
    if section == "remote":
        rule = {"url": lambda: url_ok(v, own), "pushurl": lambda: url_ok(v, own), "fetch": lambda: refspec_ok(v),
                "push": lambda: refspec_ok(v), "prune": lambda: low in BOOL,
                "tagopt": lambda: v in ("--tags", "--no-tags"),
                "gh-resolved": lambda: bool(REFNAME.fullmatch(v))}.get(name)
    elif section == "branch":
        rule = {"remote": lambda: v in remotes or v == ".", "pushremote": lambda: v in remotes or v == ".",
                "merge": lambda: bool(re.fullmatch(r"refs/heads/\S+", v)),
                "rebase": lambda: low in ("true", "false", "merges"),
                "vscode-merge-base": lambda: bool(REFNAME.fullmatch(v)),
                "description": lambda: printable(2000)}.get(name)
    elif section == "submodule":
        rule = {"url": lambda: url_ok(v, own), "active": lambda: low in BOOL}.get(name)
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
        err = r.stderr.decode(errors="replace").replace(str(copy), "<config>").replace(str(work), "<copy>")
        raise Unreadable(_("git can't read it ({error})", error=err.strip()[-160:]))
    out = []
    for item in r.stdout.decode(errors="replace").split("\0"):
        if not item:
            continue
        k, nl, v = item.partition("\n")
        out.append((k, v if nl else None))
    return out


def hash_len_of(pairs) -> int:
    return 32 if any(k.lower() == "extensions.objectformat" and (v or "").lower() == "sha256" for k, v in pairs) else 20


# ---------- the index ----------

IGNORABLE = {0x200C, 0x200D, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x206A, 0x2070), 0xFEFF}


def _reported_name(comp: bytes) -> bool:
    s = comp.decode("utf-8", errors="replace")
    s = "".join(ch for ch in s if ord(ch) not in IGNORABLE).casefold()
    t = s.rstrip(". ")
    return t in (".git", "git~1") or s.startswith((".git:", "git~1:"))


def reported_entry(path: bytes, mode: int = 0o100644) -> bool:
    """Whether the scan reports an index entry with this path and mode (docs/design/git-config-fence.md, r2 B1)."""
    if mode & 0o170000 == 0o040000 and path.endswith(b"/"):
        path = path[:-1]
    if not path or path.startswith(b"/"):
        return True
    return any(comp in (b"", b".", b"..") or _reported_name(comp) for comp in path.split(b"/"))


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


def _entries(data: bytes, off: int, count: int, version: int, hash_len: int, end_all: int, prev: bytes = b""):
    """(entries [(name, mode)], how many the scan reports for their own form, the offset after them)."""
    out, odd = [], 0
    for _i in range(count):
        start = off
        (mode,) = struct.unpack(">I", data[start + 24:start + 28])
        off += 40 + hash_len
        (flags,) = struct.unpack(">H", data[off:off + 2])
        off += 2
        if flags & 0x4000:
            off += 2
        length = flags & 0xFFF
        if version == 4:
            strip, off = _varint(data, off)
            if strip > len(prev):
                raise ValueError("prefix")
            keep = prev[:len(prev) - strip]
            if length == 0xFFF:
                stop = data.index(b"\0", off, end_all)
            else:
                stop = off + length - len(keep)
                if stop < off:
                    raise ValueError("length")
                if stop >= end_all or data[stop] != 0:
                    odd += 1
            name = keep + data[off:stop]
            off = stop + 1
        else:
            if length == 0xFFF:
                stop = data.index(b"\0", off, end_all)
            else:
                stop = off + length
                if stop >= end_all or data[stop] != 0:
                    odd += 1
            name = data[off:stop]
            off = start + (((off - start) + len(name) + 8) & ~7)
        if b"\0" in name:
            odd += 1
        if off > end_all:
            raise ValueError("truncated")
        out.append((name, mode))
        prev = name
    return out, odd, off


def read_index(data: bytes, hash_len: int = 20) -> tuple[list[tuple[bytes, int]], int, str | None]:
    """(entries [(name, mode)], how many entries the scan reports for their own form, the shared index a split index
    names (hex) or None) of an index (versions 2 to 4), read as git's reader reads it: the name length as git reads
    it, the extended flags wherever set, the entries ending where the extensions begin, the checksum verified (all
    zeros: not written); with an entry-offset table, each of its blocks read again as git's threads would, the union
    kept. ValueError when git couldn't read it either."""
    if len(data) < 12 + hash_len or data[:4] != b"DIRC":
        raise ValueError("not an index")
    version, count = struct.unpack(">II", data[4:12])
    if version not in (2, 3, 4):
        raise ValueError(f"version {version}")
    end_all = len(data) - hash_len
    digest = data[end_all:]
    if digest != b"\0" * hash_len:
        h = hashlib.sha1 if hash_len == 20 else hashlib.sha256
        if h(data[:end_all]).digest() != digest:
            raise ValueError("checksum")
    try:
        entries, odd, off = _entries(data, 12, count, version, hash_len, end_all)
        exts = {}
        while off < end_all:
            if end_all - off < 8:
                raise ValueError("extensions")
            sig, (size,) = data[off:off + 4], struct.unpack(">I", data[off + 4:off + 8])
            exts[sig] = data[off + 8:off + 8 + size]
            off += 8 + size
        if off != end_all:
            raise ValueError("extensions")
        if b"EOIE" in exts and b"IEOT" in exts and len(exts[b"IEOT"]) >= 4:
            table = exts[b"IEOT"][4:]
            seen = set(entries)
            for i in range(0, len(table) - 7, 8):
                at, n = struct.unpack(">II", table[i:i + 8])
                try:
                    block, more, _o = _entries(data, at, n, version, hash_len, end_all)
                except (ValueError, struct.error, IndexError):
                    odd += 1
                    continue
                odd += more
                entries += [e for e in block if e not in seen]
                seen.update(block)
    except (struct.error, IndexError) as e:
        raise ValueError("truncated") from e
    link = exts.get(b"link")
    return entries, odd, (link[:hash_len].hex() if link and len(link) >= hash_len else None)


# ---------- findings ----------

def finding(kind: str, where: str, **detail) -> dict:
    detail = {k: no_creds(v) if isinstance(v, str) else v for k, v in detail.items()}
    key = detail.get("key") or detail.get("name") or detail.get("path") or ""
    return {"id": f"{kind}:{where}:{key}", "kind": kind, "where": where, **detail}


def describe(f: dict) -> str:
    c = {k: clean(v) for k, v in f.items() if isinstance(v, str)}
    w, k = c.get("where", ""), f.get("kind")
    texts = {
        "key": lambda: _("{where}: {key} is not in the allowlist of inert keys", where=w, key=c.get("key")),
        "include": lambda: _("{where}: an include ({key}), which the scan doesn't follow", where=w, key=c.get("key")),
        "hookspath": lambda: _("{where}: core.hooksPath is set ({value}): git takes its hooks from there", where=w,
                               value=c.get("value")),
        "value": lambda: _("{where}: {key} has a value its rule doesn't allow ({value})", where=w, key=c.get("key"),
                           value=c.get("value")),
        "hook": lambda: _("{where}: a hook the platform didn't write ({name})", where=w, name=c.get("name")),
        "attributes": lambda: _("{where}: info/attributes isn't empty", where=w),
        "alternates": lambda: _("{where}: {name} points git at another object store", where=w, name=c.get("name")),
        "commondir": lambda: _("{where}: a commondir pointer git would follow ({value})", where=w, value=c.get("value")),
        "gitdir": lambda: _("{where}: a worktree pointer that doesn't lead back ({value})", where=w,
                            value=c.get("value")),
        "legacy": lambda: _("{where}: {name} (remotes git reads from files)", where=w, name=c.get("name")),
        "link": lambda: _("{where}: a symbolic link in the git dir ({path})", where=w, path=c.get("path")),
        "hardlink": lambda: _("{where}: a file in the git dir with more than one name ({path})", where=w,
                              path=c.get("path")),
        "index": lambda: _("{where}: {n} index {n:entry|entries} the scan reports ({path})", where=w, n=f.get("n", 1),
                           path=c.get("path")),
        "nested": lambda: _("{where}: a repo nested in the project's folders", where=w),
        "gitfile": lambda: _("{where}: a .git file leading to a git dir the scan didn't inspect ({value})", where=w,
                             value=c.get("value")),
        "pointer": lambda: _("{where}: {name} is written so git reads it differently ({why})", where=w,
                             name=c.get("name"), why=c.get("why")),
        "dotgit-link": lambda: _("{where}: .git is a symbolic link", where=w),
        "shaped": lambda: _("{where}: a folder shaped like a git dir outside any .git", where=w),
        "temp-dotgit": lambda: _("{where}: a .git here makes every folder below it a checkout of that repo", where=w),
        "unreadable": lambda: _("{where}: {path} can't be checked: {why}", where=w, path=c.get("path"),
                                why=c.get("why")),
        "incomplete": lambda: _("{where}: the scan stopped early ({why})", where=w, why=c.get("why")),
        "rebase-exec": lambda: _("{where}: a rebase in progress has exec lines (a session's own work; said only)",
                                 where=w),
    }
    return texts.get(k, lambda: f"{w}: {clean(k)}")()


# ---------- one project ----------

class Scan:
    def __init__(self, name: str, doc: dict, work: pathlib.Path, deadline: float):
        self.name, self.doc, self.work, self.deadline = name, doc, work, deadline
        self.findings: list[dict] = []
        self.notes: list[dict] = []
        self.dropped = 0
        self.repos = 0
        self.wanted_hooks = platform_hooks({**doc, "name": name}) if name else {}
        self.labels: list[tuple[pathlib.Path, str]] = []
        self.copies = 0
        self.inspected: set[str] = set()
        self.objects: list[str] = []
        self.ids: set[str] = set()
        self.indexes: set[tuple[int, int]] = set()
        self.seen = 0
        self.cap = WALK_CAP
        self.keep = FINDINGS_CAP
        if name:
            import policy
            self.own = (str(policy.memory_bare(name, doc)),)
        else:
            self.own = ()

    def add(self, f: dict) -> None:
        if f["id"] in self.ids:   # one folder can be listed twice (its hooks, then the whole git dir's walk)
            return
        self.ids.add(f["id"])
        if f["kind"] in NOTE_KINDS:
            self.notes.append(f)
        elif len(self.findings) < self.keep:
            self.findings.append(f)
        else:
            self.dropped += 1

    def where(self, p) -> str:
        real = os.path.abspath(p)
        for root, label in self.labels:
            r = str(root)
            if real == r:
                return label
            if real.startswith(r + os.sep):
                return shown(f"{label}/{os.path.relpath(real, r)}")
        return shown(real)

    def tick(self) -> None:
        self.seen += 1
        if time.monotonic() > self.deadline:
            raise Late(_("time limit"))
        if self.seen > self.cap:
            raise Late(_("{n} entries", n=self.cap))

    def listable(self, d, err: OSError) -> bool:
        """Whether a folder that can't be listed is said (a project's: always)."""
        return True

    def entries(self, d, where=None):
        """A folder's entries one at a time (no link followed into it), the time limit checked at each; a folder that
        can't be listed is a finding, never skipped."""
        try:
            it = os.scandir(d)
        except FileNotFoundError:
            return
        except OSError as e:
            if self.listable(d, e):
                self.add(finding("unreadable", where or self.where(d), path=".",
                                 why=_("can't be listed ({error})", error=e.strerror or e)))
            return
        with it:
            while True:
                try:
                    e = next(it)
                except StopIteration:
                    return
                except OSError as err:
                    self.add(finding("unreadable", where or self.where(d), path=".",
                                     why=_("can't be listed ({error})", error=err.strerror or err)))
                    return
                self.tick()
                yield e

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
        try:
            self.walk([r for r, _l in roots])
        except Late as e:
            self.add(finding("incomplete", self.labels[0][1] if self.labels else "workspace", why=str(e)))
        return self.result()

    def walk(self, roots: list[pathlib.Path], depth_cap: int | None = None) -> None:
        """Breadth first from each root: `.git` entries, folders shaped like a git dir; then every git dir found is
        inspected, and every gitfile checked against what was inspected."""
        mains, gitfiles, cut, stopped = [], [], False, None
        queue = collections.deque((root, root, 0) for root in roots)
        for root in roots:
            if git_shaped(root):
                self.add(finding("shaped", self.where(root)))
        try:
            self.bfs(queue, mains, gitfiles, depth_cap)
        except Late as e:   # what was found is still inspected, with a budget of its own
            stopped = e
        cut = self.cut
        self.seen, self.deadline = 0, max(self.deadline, time.monotonic() + TIME_CAP)
        for gd in mains:
            self.git_dir(gd, "main")
            self.links(gd)
        for entry in gitfiles:
            self.gitfile(entry)
        if stopped:
            raise stopped
        if cut:
            raise Late(_("{n} levels deep", n=depth_cap))

    def bfs(self, queue, mains, gitfiles, depth_cap) -> None:
        self.cut = False
        while queue:
            d, root, depth = queue.popleft()
            for e in self.entries(d):
                if e.name.casefold() == ".git":
                    path = pathlib.Path(e.path)
                    if e.is_symlink():
                        self.add(finding("dotgit-link", self.where(d)))
                    elif e.is_dir(follow_symlinks=False):
                        self.found_repo(pathlib.Path(d), root)
                        mains.append(path)
                    else:
                        gitfiles.append(path)
                elif e.is_dir(follow_symlinks=False):
                    if git_shaped(e.path):
                        self.add(finding("shaped", self.where(e.path)))
                    elif depth_cap is None or depth + 1 < depth_cap:
                        queue.append((e.path, root, depth + 1))
                    else:
                        self.cut = True

    def found_repo(self, folder: pathlib.Path, root: pathlib.Path) -> None:
        if folder != root:
            self.add(finding("nested", self.where(folder)))

    def gitfile(self, entry: pathlib.Path) -> None:
        w = self.where(entry.parent)
        try:
            target, odd = pointer(entry, b"gitdir: ")
        except Unreadable as e:
            self.add(finding("unreadable", w, path=".git", why=str(e)))
            return
        if odd:
            self.add(finding("pointer", w, name=".git", why=odd))
            return
        real = os.path.realpath(entry.parent / target)
        if real not in self.inspected:
            self.add(finding("gitfile", w, value=short(target, 160)))

    # --- one git dir ---
    def git_dir(self, gd: pathlib.Path, kind: str, hash_len: int | None = None) -> None:
        """kind: main (a repo's own), worktree (worktrees/<n>, its hash length given by the repo's), module (a folder
        under modules/)."""
        if time.monotonic() > self.deadline:
            raise Late(_("time limit"))
        self.inspected.add(os.path.realpath(gd))
        self.repos += kind == "main"
        w = self.where(gd)
        if kind != "worktree":
            self.objects.append(str(gd / "objects") + os.sep)
            pairs = self.config(gd / "config", module=gd if kind == "module" else None) if lexists(gd / "config") else []
            hash_len = hash_len_of(pairs)   # the common dir's config only
        if lexists(gd / "config.worktree"):
            self.config(gd / "config.worktree", module=gd if kind == "module" else None)
        hash_len = hash_len or 20
        if kind == "worktree":
            self.worktree_admin(gd)
        elif lexists(gd / "commondir"):
            self.add(finding("commondir", w, value=short(self.text(gd / "commondir"), 160)))
        if kind != "worktree":
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
                if lexists(gd / legacy) and not is_dir(gd / legacy):
                    self.add(finding("legacy", w, name=legacy))
                for e in self.entries(gd / legacy) if is_dir(gd / legacy) else ():
                    self.add(finding("legacy", w, name=f"{legacy}/{shown(e.name)}"))
            for e in self.entries(gd / "worktrees") if is_dir(gd / "worktrees") else ():
                if e.is_dir(follow_symlinks=False):
                    self.git_dir(pathlib.Path(e.path), "worktree", hash_len)
        if lexists(gd / "index"):
            self.index(gd / "index", hash_len)
        for shared in self.matching(gd, "sharedindex."):
            self.index(shared, hash_len)
        todo = gd / "rebase-merge" / "git-rebase-todo"
        if lexists(todo):
            try:
                lines = read_capped(todo).decode(errors="replace").splitlines()
            except Unreadable:
                lines = []
            if any(re.match(r"\s*(x|exec)\s", ln) for ln in lines):
                self.add(finding("rebase-exec", w))
        if is_dir(gd / "modules"):
            self.module_tree(gd / "modules")

    def matching(self, d: pathlib.Path, prefix: str) -> list[pathlib.Path]:
        """Entries whose name starts with prefix in any case (the volume may open either spelling as git's)."""
        return [pathlib.Path(e.path) for e in self.entries(d) if e.name.casefold().startswith(prefix)]

    def module_tree(self, top: pathlib.Path) -> None:
        """Every folder under modules/: a module's git dir (it has HEAD, a config, a commondir or an index) is inspected
        as one; any other folder (a module's name can hold slashes) is walked into."""
        for e in self.entries(top):
            if not e.is_dir(follow_symlinks=False):
                continue
            d = pathlib.Path(e.path)
            if any(lexists(d / n) for n in ("HEAD", "config", "commondir", "index")):
                self.git_dir(d, "module")
            else:
                self.module_tree(d)

    def text(self, p) -> str:
        try:
            return read_capped(p).decode(errors="replace").strip()[:160]
        except Unreadable as e:
            return str(e)

    def config(self, src: pathlib.Path, module: pathlib.Path | None = None) -> list[tuple[str, str | None]]:
        if time.monotonic() > self.deadline:
            raise Late(_("time limit"))
        w = self.where(src)
        try:
            pairs = config_pairs(self.copy(src), self.work)
        except Unreadable as e:
            self.add(finding("unreadable", self.where(src.parent), path=src.name, why=str(e)))
            return []
        remotes = {k.split(".", 1)[1].rsplit(".", 1)[0] for k, _v in pairs
                   if k.lower().startswith("remote.") and k.count(".") >= 2}
        rule = (lambda v: self.module_worktree_ok(module, v)) if module is not None else None
        for k, v in pairs:
            low = k.lower()
            if low.startswith(("include.", "includeif.")):
                self.add(finding("include", w, key=k))
            elif low == "core.hookspath":
                self.add(finding("hookspath", w, key=k, value=short(v, 160)))
            elif (why := check_key(k, v, remotes, self.own, rule)):
                self.add(finding(why, w, key=k, **({"value": short(v, 120)} if why == "value" else {})))
        return pairs

    def module_worktree_ok(self, module: pathlib.Path, v: str) -> bool:
        """A module's core.worktree as git writes it: a folder in the project's folders whose .git file leads back to
        this module's git dir, read as git reads it."""
        target = pathlib.Path(os.path.realpath(module / v))
        if not any(target == r or r in target.parents for r, _l in self.labels):
            return False
        try:
            back, odd = pointer(target / ".git", b"gitdir: ")
        except Unreadable:
            return False
        return not odd and os.path.realpath(target / back) == os.path.realpath(module)

    def hooks(self, gd: pathlib.Path) -> None:
        hooks = gd / "hooks"
        if lexists(hooks) and not is_dir(hooks):
            self.add(finding("hook", self.where(gd), name="hooks"))
            return
        for e in self.entries(hooks, self.where(hooks)) if lexists(hooks) else ():
            if e.name.endswith(".sample") and e.is_file(follow_symlinks=False):
                continue
            try:
                body = read_capped(e.path).decode(errors="replace")
            except Unreadable:
                body = None
            if body is None or body not in self.wanted_hooks.get(e.name, ()):
                self.add(finding("hook", self.where(gd), name=shown(e.name)))

    def worktree_admin(self, wt: pathlib.Path) -> None:
        """A worktree's admin dir: its commondir must lead to its repo's git dir (`../..`, read as git reads it), and its
        gitdir to a worktree whose .git file leads back."""
        w = self.where(wt)
        try:
            common, odd = pointer(wt / "commondir") if lexists(wt / "commondir") else (None, None)
        except Unreadable as e:
            common, odd = None, str(e)
        if odd:
            self.add(finding("pointer", w, name="commondir", why=odd))
        elif common not in ("../..", "../../"):
            self.add(finding("commondir", w, value=short(common or "-", 160)))
        if not lexists(wt / "gitdir"):
            return
        try:
            target, odd = pointer(wt / "gitdir")
        except Unreadable as e:
            self.add(finding("unreadable", w, path="gitdir", why=str(e)))
            return
        if odd:
            self.add(finding("pointer", w, name="gitdir", why=odd))
            return
        back = pathlib.Path(target) if os.path.isabs(target) else wt / target   # worktree.useRelativePaths
        if not lexists(back):
            return   # its worktree was removed: prunable, not a pointer elsewhere
        try:
            t, odd2 = pointer(back, b"gitdir: ")
            ok = not odd2 and os.path.realpath(back.parent / t) == os.path.realpath(wt)
        except Unreadable:
            ok = False
        if not ok:
            self.add(finding("gitdir", w, value=short(target, 160)))

    def links(self, gd: pathlib.Path) -> None:
        """Every entry of a repo's git dir (its worktrees' and modules' included): a symbolic link, or a file with more
        than one name outside the objects/ of a repo's or a module's git dir. The platform's own folder in it holds
        caches that make links."""
        own = set(names.all("repo_dir"))
        stack = [(gd, ())]
        while stack:
            d, rel = stack.pop()
            for e in self.entries(d):
                parts = (*rel, e.name)
                if len(parts) == 1 and e.name in own:
                    continue
                r = shown("/".join(parts))
                if e.is_symlink():
                    self.add(finding("link", self.where(gd), path=r))
                elif e.is_dir(follow_symlinks=False):
                    stack.append((e.path, parts))
                elif not any(e.path.startswith(o) for o in self.objects):
                    try:
                        if e.stat(follow_symlinks=False).st_nlink > 1:
                            self.add(finding("hardlink", self.where(gd), path=r))
                    except OSError:
                        pass

    def index(self, idx: pathlib.Path, hash_len: int) -> None:
        w = self.where(idx.parent)
        try:
            st = os.lstat(idx)
            if (st.st_dev, st.st_ino) in self.indexes:
                return
            self.indexes.add((st.st_dev, st.st_ino))
            entries, odd, link = read_index(read_capped(idx, INDEX_CAP), hash_len)
        except Unreadable as e:
            self.add(finding("unreadable", w, path=idx.name, why=str(e)))
            return
        except (OSError, ValueError) as e:
            self.add(finding("unreadable", w, path=idx.name, why=_("not an index git can read ({error})", error=e)))
            return
        n = odd + sum(1 for name, mode in entries if reported_entry(name, mode))
        if n:
            self.add(finding("index", w, path=shown(idx.name), n=n))
        if link:   # a split index: the shared part it names, the file git opens
            shared = idx.parent / f"sharedindex.{link}"
            if lexists(shared):
                self.index(shared, hash_len)
            else:
                self.add(finding("unreadable", w, path=f"sharedindex.{link}", why=_("missing")))

    def result(self) -> dict:
        if self.dropped:
            self.findings.append(finding("incomplete", "workspace", why=_("{n} more findings not kept",
                                                                         n=self.dropped)))
        return {"nothing_found": not self.findings, "findings": self.findings, "notes": self.notes,
                "repos": self.repos, "commit_gate": commit_gate(self.name, self.doc, self.wanted_hooks)}


class TempScan(Scan):
    """The temp roots (r2 S1): a repo anywhere in them is inspected as a project's is, and one at a root is said
    whatever it holds (it makes the whole area a checkout). Folders of other users are skipped; the owner's that can't
    be listed are said."""

    def __init__(self, work: pathlib.Path, deadline: float, roots: list[pathlib.Path]):
        super().__init__("", {}, work, deadline)
        self.labels = [(r, shown(r)) for r in roots]
        self.roots = roots
        self.cap = TEMP_CAP
        self.keep = TEMP_KEPT
        self.private = pathlib.Path(os.path.realpath(hubconf.secrets_dir()))

    def listable(self, d, err: OSError) -> bool:
        try:
            return os.lstat(d).st_uid == os.getuid()
        except OSError:
            return False

    def entries(self, d, where=None):
        for e in super().entries(d, where):
            p = pathlib.Path(e.path)
            if p == self.private or self.private in p.parents:
                continue
            yield e

    def found_repo(self, folder: pathlib.Path, root: pathlib.Path) -> None:
        if folder == root:
            self.add(finding("temp-dotgit", self.where(folder)))

    def scan(self) -> tuple[list[dict], int]:
        try:
            self.walk(self.roots, TEMP_DEPTH)
        except Late as e:
            self.findings.append(finding("incomplete", self.labels[0][1] if self.labels else "temp", why=str(e)))
        return self.findings, len(self.findings) + self.dropped


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


def platform_hooks(doc: dict) -> dict[str, set[str]]:
    """The hooks the platform writes for this project, by name: their current text in every UI language (a hook's
    message is in the language the platform had when it wrote it)."""
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("render_project_settings_gf",
                                                  str(hubconf.ROOT / "bin" / "render-project-settings.py"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    out: dict[str, set[str]] = {}
    real = i18n.lang
    try:
        for lang in ("en", *i18n.languages()):
            i18n.lang = lambda lang=lang: lang
            for hook, (text, _why) in mod.wanted_hooks(doc).items():
                out.setdefault(hook, set()).add(text)
    finally:
        i18n.lang = real
    return out


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
    out["hook"] = "current" if body is not None and body in wanted.get("pre-commit", ()) else \
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


# ---------- the whole machine ----------

def sandboxed_projects(only: str | None = None) -> dict[str, dict]:
    return {n: d for n, d in sorted(hubconf.projects().items())
            if (d.get("enforce") or {}).get("sandbox") is True and (only is None or n == only)}


def scan(only: str | None = None, temp: bool = True, temp_given=None) -> dict:
    out = {"v": 2, "at": datetime.datetime.now().isoformat(timespec="seconds"), "projects": {}, "temp": []}
    work_top = pathlib.Path(tempfile.mkdtemp(prefix="git-fence.", dir=hubconf.private_tmp()))
    try:
        for name, doc in sandboxed_projects(only).items():
            work = work_top / "p"
            work.mkdir()
            try:
                out["projects"][name] = Scan(name, doc, work, time.monotonic() + TIME_CAP).run()
            except Exception as e:   # one project's trouble is said; the next is still scanned
                out["projects"][name] = {"nothing_found": False, "findings": [finding("incomplete", "workspace",
                                                                                      why=f"{type(e).__name__}: {e}")],
                                         "notes": [], "repos": 0, "commit_gate": {}}
            finally:
                shutil.rmtree(work, ignore_errors=True)
        if temp:
            work = work_top / "t"
            work.mkdir()
            found, total = TempScan(work, time.monotonic() + TIME_CAP, temp_roots(temp_given)).scan()
            out["temp_at"] = out["at"]
            out["temp_count"] = total
            out["temp"] = found
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


def temp_due(last: dict | None, now: datetime.datetime) -> bool:
    try:
        return now - datetime.datetime.fromisoformat(str((last or {}).get("temp_at"))) >= TEMP_EVERY
    except ValueError:
        return True


def gate_text(g: dict) -> str:
    if not g or not g.get("on"):
        return _("commit gate off")
    if g.get("exempt"):
        return _("commit gate on; exempt from the sandbox, so its tiers run as whoever commits")
    if g.get("sandbox"):
        return _("commit gate on, but this machine can't sandbox ({why}): every commit stops", why=clean(g["sandbox"]))
    return {"current": _("commit gate on; its tiers run in the project's sandbox"),
            "missing": _("commit gate on, but the platform's pre-commit isn't in place"),
            "other": _("commit gate on, but the pre-commit isn't the platform's current one")}.get(g.get("hook"), "")


def rows(rep: dict | None = None, temp: bool = True) -> list[tuple[str, str, str, list[str]]]:
    """(project, 'ok'|'warn', its one line, every finding's line) per sandboxed project, then (temp) the temp area's
    row ('' for the project): what the status page shows; doctor leaves the temp area out."""
    rep = rep if rep is not None else load()
    if rep is None:
        return []
    out = []
    for name, p in sorted(rep["projects"].items()):
        fs = [describe(f) for f in p.get("findings") or []]
        notes = [describe(f) for f in p.get("notes") or []]
        line = (_("no known problem found, a best-effort check ({gate})", gate=gate_text(p.get("commit_gate") or {}))
                if not fs else
                _("problems found: {first}{more} ({gate})", first=fs[0],
                  more=_(" (+{n} more)", n=len(fs) - 1) if len(fs) > 1 else "",
                  gate=gate_text(p.get("commit_gate") or {})))
        out.append((clean(name), "ok" if not fs else "warn", line, fs + notes))
    found = [f for f in rep.get("temp") or [] if f.get("kind") != "incomplete"]
    stopped = [f for f in rep.get("temp") or [] if f.get("kind") == "incomplete"]
    if temp and (found or stopped):
        line = (_("temp area: {n} {n:problem|problems}, e.g. {first}", n=rep.get("temp_count") or len(found),
                  first=describe(found[0])) if found
                else _("temp area: no known problem found, a best-effort check"))
        if stopped:
            line += _("; the walk stopped early ({why})", why=clean(stopped[0].get("why")))
        out.append(("", "warn" if found else "ok", line, [describe(f) for f in found + stopped]))
    return out


def window_text() -> str:
    return _("scanned every {n} minutes, a best-effort check: a change can stand that long before it is seen, and "
             "the guarantee has to come from the fence preventing the writes", n=EVERY_MIN)


def problems(rep: dict) -> tuple[list[str], list[str]]:
    """(lines, ids) for alert.once: every finding of every project, each with a stable id. The temp area is on the
    status page only until the fence's HEAD rule exists (r2 S1)."""
    lines, ids = [], []
    for name, p in sorted(rep["projects"].items()):
        for f in p.get("findings") or []:
            lines.append(f"{clean(name)}: {describe(f)}")
            ids.append(f"{name}:{f['id']}")
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
    if a.apply:
        if a.project or a.no_temp or a.temp_root:
            print(_("--apply scans everything: --project, --no-temp and --temp-root are for --report"), file=sys.stderr)
            return 2
        last = load()
        now = datetime.datetime.now()
        due = temp_due(last, now)
        rep = scan(temp=due)
        if not due and last:
            rep.update({k: last[k] for k in ("temp", "temp_at", "temp_count") if k in last})
        hubconf.write_atomic(result_path(), json.dumps(rep, ensure_ascii=False, indent=1))
        import alert
        lines, ids = problems(rep)
        alert.once("git-fence", lines, _("the git config fence's scan (a best-effort check) finds no known problem "
                                         "any more"), ids=ids)
    else:
        rep = scan(a.project, temp=not a.no_temp, temp_given=a.temp_root)
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
    with contextlib.suppress(BrokenPipeError):
        sys.exit(main(sys.argv[1:]))
