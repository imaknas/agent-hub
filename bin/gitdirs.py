#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Where a folder's git repo is, read from the `.git` pointer files as plain files: no git runs.

A repo's config is its projects' to write, and git reads it before doing anything: `[include] path=<fifo>` hangs even
`git rev-parse`. So the platform asks git nothing in a project's repo to find out whose repo a folder is
(docs/design/projects-and-repos.md section 4); the session hooks, which the coding agent runs outside its sandbox, find
their project with this. What git would also honor and this doesn't (GIT_DIR, the config's core.worktree) is the
project's to set, not the platform's to follow.

  dirs(d) → Dirs(top, git_dir, common) | None
    top       the nearest folder at or above d with a .git entry (`rev-parse --show-toplevel`)
    git_dir   that .git folder, or the folder a .git file's "gitdir:" names, relative to the file's folder
              (`--git-dir`)
    common    git_dir's "commondir", relative to git_dir, when it has one; else git_dir (`--git-common-dir`)
  All three resolved. A pointer file is opened non-blocking and read only when it is a regular file (a FIFO there
  reads as nothing).
  confirmed(found) → bool
    Whether the repo it names says top is its linked worktree: git_dir is in common's worktrees/, and its "gitdir" names
    top's .git. What sits under top is top's to write, so all of the above is only a claim about whose repo this is. This
    much is written in the repo it names, so it proves something only about a repo the writer can't write: one that is itself
    planted confirms itself. Worth nothing without knowing whose common is (project_git.user_git_ok).
  main_checkout(found) → Path | None
    The main checkout, as far as the pointers agree: top itself, or a confirmed linked worktree's. A .git file naming another
    repo's .git, or a worktree that repo doesn't know, says nothing about whose folder this is. Same limit as confirmed.

CLI: gitdirs.py <dir> → "<top>\t<git_dir>\t<common>"; exit 1 when <dir> is in no repo.

Standard library only.
"""
from __future__ import annotations

import os
import pathlib
import stat
import sys
from typing import NamedTuple

MAX_POINTER = 4096


class Dirs(NamedTuple):
    top: pathlib.Path
    git_dir: pathlib.Path
    common: pathlib.Path


def read_pointer(f: pathlib.Path) -> str | None:
    try:
        fd = os.open(f, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return os.read(fd, MAX_POINTER).decode(errors="replace").strip()
    finally:
        os.close(fd)


def dirs(d) -> Dirs | None:
    real = pathlib.Path(d).resolve()
    for top in (real, *real.parents):
        dot = top / ".git"
        if dot.is_dir():
            gd = dot
        elif os.path.lexists(dot):
            text = read_pointer(dot) or ""
            if not text.startswith("gitdir:"):
                return None
            gd = top / text.removeprefix("gitdir:").strip()
        else:
            continue
        common = read_pointer(gd / "commondir")
        return Dirs(top, gd.resolve(), (gd / common).resolve() if common else gd.resolve())
    return None


def confirmed(found: Dirs) -> bool:
    if found.git_dir.parent != found.common / "worktrees":
        return False
    back = read_pointer(found.git_dir / "gitdir")
    return bool(back) and (found.git_dir / back).resolve() == (found.top / ".git").resolve()


def main_checkout(found: Dirs) -> pathlib.Path | None:
    main = found.common.parent
    return main if main == found.top or confirmed(found) else None


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("usage: gitdirs.py <dir>\n")
        sys.exit(2)
    found = dirs(sys.argv[1])
    if not found:
        sys.exit(1)
    print("\t".join(map(str, found)))
