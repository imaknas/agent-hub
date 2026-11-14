#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""A coding agent's own worktree mode in a sandboxed project, done by the platform as the project
(docs/design/git-broker.md, r3 B-1, mitigation 3).

A tool that makes a worktree itself runs git as the user: any process of the project running at that moment can swap a
tracked folder for a link, and the checkout then writes the project's files wherever the link points, as the owner
(measured 11-14: `git worktree add` wrote 2,920 files into a folder the project couldn't write). So for a sandboxed
project the tool's hook hands the work over (runtime_ports' worktree_maker; `hub-event --runtime <name> worktree-make`)
and the checkout runs as the project, inside its sandbox (project-git-job.py session-worktree through project_git): a
swap there can only redirect writes to places the project could write anyway. Never as the owner: when this can't be
done, the worktree fails.

  make(cwd, name, rt) → (path, skipped)   the project the session's folder belongs to (sandboxed only), the worktree at
                                          the tool's place in its workspace (rt.worktree_location), its branch new from
                                          HEAD or the existing one; skipped: tracked paths its sandbox may not write
                                          (control names), left out of the checkout and marked skip-worktree. A make
                                          that didn't finish (failed, timed out, or its hook killed) is recorded in the
                                          platform's state until it is undone: the next make of that name undoes it
                                          first, so no worktree or branch it made is left behind
  HOOK_TIMEOUT_S                          what a tool's hook must allow: the plan, the job and the undo, plus a margin
                                          (under Claude Code's 10-minute default for hooks)
  drop(cwd, path)                         `git worktree remove --force` of that worktree, as the project; only one inside
                                          that project's workspace
Both raise Refused with a reason a person can act on.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402
import policy  # noqa: E402
import project_git  # noqa: E402

_ = i18n.domain(__file__)

SEGMENT = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")   # each `/`-separated part of a name: no leading dot
PLAN_TIMEOUT_S, JOB_TIMEOUT_S, UNDO_TIMEOUT_S = 60, 360, 60
HOOK_TIMEOUT_S = PLAN_TIMEOUT_S + JOB_TIMEOUT_S + UNDO_TIMEOUT_S + 60
PENDING = hubconf.STATE / "session-worktree"   # {project}/{folder}.json while a make is under way or wasn't undone


class Refused(Exception):
    pass


def _project(cwd) -> tuple[str, dict, pathlib.Path]:
    found = project_git.sandbox_of(cwd)
    if not found:
        raise Refused(_("{folder} isn't in a sandboxed project: the tool makes its own worktrees there",
                        folder=cwd))
    name, doc = found
    ws = hubconf.path((doc.get("paths") or {}).get("workspace"))
    if not ws or not ws.is_dir():
        raise Refused(_("{project} has no workspace on this machine", project=name))
    return name, doc, ws


def rules() -> dict:
    """What the project's sandbox may not write in a checkout, for the job to leave out (policy.protect, protect_roots)."""
    return {"names": [c["name"] for c in policy.protect()], "roots": policy.protect_roots()}


def link_paths(path: pathlib.Path, links: list[str]) -> list[pathlib.Path]:
    """The exact places the job may make a link: the tracked links' paths, which come from the project's own repo (its
    word, read as the project), so only relative ones without a `..` that stay under the new worktree."""
    out = []
    for p in links:
        rel = pathlib.PurePosixPath(p)
        if p and not rel.is_absolute() and ".." not in rel.parts and "" not in rel.parts:
            out.append(path / rel)
    return out


def valid_name(name: str) -> bool:
    parts = (name or "").split("/")
    return 0 < len(name) <= 100 and all(SEGMENT.match(p) for p in parts) and ".." not in parts


def _pending(project: str, path: pathlib.Path) -> pathlib.Path:
    return PENDING / project / f"{path.name}.json"


def _undo(project: str, ws: pathlib.Path, rec: dict) -> None:
    project_git.job(project, ws, "session-worktree-undo", ws, rec["path"], rec["branch"], "1" if rec.get("new") else "0",
                    timeout=UNDO_TIMEOUT_S)


def make(cwd, name: str, rt) -> tuple[pathlib.Path, list[str]]:
    project, _doc, ws = _project(cwd)
    if not valid_name(name):
        raise Refused(_("not a worktree name the platform makes: {name}", name=name))
    path, branch = rt.worktree_location(ws, name)
    path = pathlib.Path(path)
    if not path.is_relative_to(ws) or path == ws:
        raise Refused(_("{path} is already there or isn't in {project}'s workspace", path=path, project=project))
    rec_file = _pending(project, path)
    try:
        left = json.loads(rec_file.read_text())
    except (OSError, ValueError):
        left = None
    if isinstance(left, dict) and left.get("path") == str(path):   # a make of this name that didn't finish
        _undo(project, ws, left)
        rec_file.unlink(missing_ok=True)
    if os.path.lexists(path):
        raise Refused(_("{path} is already there or isn't in {project}'s workspace", path=path, project=project))
    try:
        plan = project_git.data(project, ws, "worktree-plan", ws, branch, timeout=PLAN_TIMEOUT_S)
    except project_git.Unavailable as e:
        raise Refused(_("couldn't list {project}'s tracked links ({why})", project=project, why=e)) from None
    if not isinstance(plan, dict):
        raise Refused(_("couldn't list {project}'s tracked links ({why})", project=project, why=plan))
    rec = {"path": str(path), "branch": branch, "new": not plan.get("exists")}
    rec_file.parent.mkdir(parents=True, exist_ok=True)
    rec_file.write_text(json.dumps(rec))
    r = project_git.job(project, ws, "session-worktree", ws, path, branch, json.dumps(rules()),
                        links=link_paths(path, project_git.strs(plan.get("links"))), timeout=JOB_TIMEOUT_S)
    try:
        got = json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        got = None
    if not isinstance(got, dict) or got.get("path") != str(path):
        _undo(project, ws, rec)   # the job's own cleanup doesn't run when it was killed (its timeout)
        rec_file.unlink(missing_ok=True)
        raise Refused(_("making the worktree as {project} failed ({why}); nothing was made as you", project=project,
                        why=(r.stderr.strip()[-300:] or f"exit {r.returncode}")))
    rec_file.unlink(missing_ok=True)
    return path, project_git.strs(got.get("skipped"))


def drop(cwd, path) -> None:
    project, _doc, ws = _project(cwd)
    p = pathlib.Path(path)
    if not p.is_absolute():
        p = pathlib.Path(cwd) / p
    if p == ws or not p.is_relative_to(ws) or ".." in p.parts:
        raise Refused(_("{path} isn't a worktree in {project}'s workspace", path=path, project=project))
    r = project_git.job(project, ws, "worktree-remove", ws, p, "--force")
    if r.returncode != 0:
        raise Refused(_("removing the worktree as {project} failed ({why})", project=project,
                        why=r.stderr.strip()[-300:] or f"exit {r.returncode}"))
