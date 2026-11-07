#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""What a memory branch may hold (docs/design/mailbox-session-first.md, step 3's record): AGENT.md, RULES and STATE.md at its top, and notes
under memory/, plain files only, none under a name a runtime takes as rules. Agents the platform starts as the user work in copies of these
branches, and an agent loads what a folder tells it to (hooks in a settings file run as the user; a rules file next to a note it reads), so
what a project writes there must stay notes. An allow list: what a runtime calls its control files is not this module's to know, and every
one of them is outside it. The rules files' names are the adapters' to declare (runtime_ports.rules_file_names), refused at any depth.

Read off the machine's 26 memory repos, all their history (11-08): those paths and no others, every mode 100644, `memory/.gitkeep` the
only name that starts with a dot.

  refused(path, mode, inert_ok) → why | None               one entry; inert_ok: a name no runtime or tool reads as settings, rules or
                                                            hooks (INERT, Finder's folder data) is let through, where it can only be in
                                                            the way (an agent's folder), never where it would enter a branch
  unstage_refused(repo) → [entry text]                      after the sync's `git add` of memory/ and STATE.md: what this would refuse
                                                            goes back out of the index, left in the folder, so a stray file never
                                                            commits a project's notes to a refusal
  tree_problems(repo, rev, inert_ok) → [entry text]         a commit's whole tree (repo: one the platform owns)
  new_commit_problems(repo, old, new) → [entry text]        every commit in old..new, each one's whole tree, so a path added and removed
                                                            again counts, and a merge needs no reasoning. What old's own tree holds is
                                                            not new: a repo that already holds such a path goes on taking notes, and a
                                                            commit removing it, while agents stay out (folder_problems)
  folder_problems(folder) → [entry text]                    the files themselves, at the point of use: llm.py asks it before an agent
                                                            starts in a copy of a memory branch (works_in: memory), on every runtime

CLI (git-sync-memory.sh, in the memory checkout): memory_paths.py unstage   prints what it left out, once, on stderr
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import unicodedata

TOP = ("AGENT.md", "RULES", "STATE.md")
TREE = "memory"
KEEP = ".gitkeep"
INERT = (".DS_Store",)   # Finder's view settings for a folder: read by no runtime, tool or git as settings, rules or hooks
FILE_MODE = "100644"
MAX_TREES = 5000      # a bound on one bundle's work; memory history is far below it
MAX_SHOWN = 5
RULES_NAMES: tuple[str, ...] | None = None   # set by a caller that has them (the sandboxed sync job is handed them); else asked once


def rules_names() -> tuple[str, ...]:
    global RULES_NAMES
    if RULES_NAMES is None:
        sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
        import runtime_ports
        RULES_NAMES = tuple(runtime_ports.rules_file_names())
    return RULES_NAMES


def _plain_name(name: str) -> bool:
    return bool(name) and not name.startswith(".") and not any(unicodedata.category(c) in ("Cc", "Cf") for c in name)


def refused(path: str, mode: str = FILE_MODE, inert_ok: bool = False) -> str | None:
    """Why this entry may not be on a memory branch, or None."""
    if mode != FILE_MODE:
        return f"mode {mode}"
    if path in TOP:
        return None
    first, *rest = path.split("/")
    if first != TREE or not rest:
        return "outside what a memory branch holds"
    folders, name = rest[:-1], rest[-1]
    if not (all(map(_plain_name, folders)) and (_plain_name(name) or name == KEEP or (inert_ok and name in INERT))):
        return "a hidden name"
    rules = {n.casefold() for n in rules_names()}   # case-insensitive file systems load CLAUDE.md for claude.md
    if any(unicodedata.normalize("NFC", p).casefold() in rules for p in rest):
        return "a rules file's name"
    return None


def _shown(bad: list[str]) -> list[str]:
    more = len(bad) - MAX_SHOWN
    return bad[:MAX_SHOWN] + ([f"and {more} more"] if more > 0 else [])


def _text(path: str, why: str) -> str:
    return f"{json.dumps(path)} ({why})"   # a path is the project's own text: quoted, control characters and all


class Unreadable(Exception):
    pass


def _git(repo, *args) -> str:
    r = subprocess.run(["git", "-C", str(repo), "--literal-pathspecs", *args], capture_output=True)
    if r.returncode != 0:
        raise Unreadable(r.stderr.decode(errors="replace").strip()[-300:])
    return r.stdout.decode(errors="surrogateescape")


def _entries(repo, rev: str) -> set[tuple[str, str, str]]:
    """(mode, object, path) of every file, link and gitlink in rev's tree."""
    out = set()
    for line in _git(repo, "ls-tree", "-r", "-z", rev).split("\0"):
        if line:
            meta, path = line.split("\t", 1)
            mode, _kind, obj = meta.split()
            out.add((mode, obj, path))
    return out


def _problems(entries, known=(), inert_ok: bool = False) -> list[str]:
    bad = []
    for mode, obj, path in entries:
        if (mode, obj, path) not in known and (why := refused(path, mode, inert_ok)):
            bad.append(_text(path, why))
    return sorted(set(bad))


def unstage_refused(repo) -> list[str]:
    """What the sync just staged under memory/ and STATE.md that a memory branch may not hold, taken back out of the index (to what HEAD
    has); the files stay where they are. Deletions are kept: they only take something away. Raises Unreadable when git can't tell."""
    changed = [p for p in _git(repo, "diff", "--cached", "--name-only", "--no-renames", "-z", "--", TREE, "STATE.md").split("\0") if p]
    if not changed:
        return []
    bad = []
    for line in _git(repo, "ls-files", "-s", "-z", "--", *changed).split("\0"):
        if line:
            meta, path = line.split("\t", 1)
            if (why := refused(path, meta.split()[0])):
                bad.append((path, why))
    if bad:
        _git(repo, "reset", "-q", "--", *(p for p, _w in bad))
    return _shown(sorted(_text(p, w) for p, w in bad))


def tree_problems(repo, rev: str, inert_ok: bool = False) -> list[str]:
    try:
        return _shown(_problems(_entries(repo, rev), inert_ok=inert_ok))
    except Unreadable as e:
        return [f"can't be read: {e}"]


def new_commit_problems(repo, old: str | None, new: str) -> list[str]:
    try:
        trees = _git(repo, "log", "--format=%T", f"{old}..{new}" if old else new).split()
        if len(set(trees)) > MAX_TREES:
            return [f"over {MAX_TREES} new trees"]
        known = _entries(repo, old) if old else set()
        bad: set[str] = set()
        for tree in set(trees):
            bad.update(_problems(_entries(repo, tree), known))
        return _shown(sorted(bad))
    except Unreadable as e:
        return [f"can't be read: {e}"]


def folder_problems(folder) -> list[str]:
    """What's in a copy of a memory branch, read off the files, not git: this is what an agent started there would find. Its .git is the
    platform's. A file a branch committed as a link is a plain file in these copies (hublib.NO_SYMLINKS); a link found here is not
    followed and is refused. An inert name (INERT) is let through: refusing a project's notes over it keeps nothing out."""
    bad = []
    top = os.fspath(folder)
    for root, dirs, files in os.walk(top):
        rel = os.path.relpath(root, top)
        at = "" if rel == "." else rel + "/"
        if not at:
            dirs[:] = [d for d in dirs if d != ".git"]
            files = [f for f in files if f != ".git"]
        for name in dirs:
            st = os.lstat(os.path.join(root, name))
            path = at + name
            if stat.S_ISLNK(st.st_mode):
                bad.append(_text(path, "a link"))
            elif refused(path + "/x"):
                bad.append(_text(path, "a folder a memory branch doesn't hold"))
        for name in files:
            path = at + name
            st = os.lstat(os.path.join(root, name))
            why = ("a link" if stat.S_ISLNK(st.st_mode) else "not a plain file" if not stat.S_ISREG(st.st_mode)
                   else "executable" if st.st_mode & 0o111 else refused(path, inert_ok=True))
            if why:
                bad.append(_text(path, why))
    return _shown(sorted(bad))


def main(argv: list[str]) -> int:
    if argv != ["unstage"]:
        print("usage: memory_paths.py unstage", file=sys.stderr)
        return 2
    try:
        left = unstage_refused(".")
    except Unreadable as e:
        print(f"memory sync: what was staged can't be checked, nothing committed ({e})", file=sys.stderr)
        return 1
    if left:
        print("memory sync: left in the folder, not committed (a memory branch holds only plain notes): " + "; ".join(left), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
