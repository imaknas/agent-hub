#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Rename a whole project (a regular project or an idea sandbox), GitHub repo included: used by lease-runner's
POST /v1/projects/<name>/rename (10-25, the assistant's m10251650, confirmed by the owner).

rename-project.sh handles the registration, the memory bare repo/copies, the mirror and the local folder, but requires
the GitHub repo to be renamed first; this adds that step, then hands over to it, recording how far it got so that
calling it again after a failure picks up where it stopped.

Usage: project-rename.py <old name> <new name> [--apply] [--json]
  Without --apply it only prints what it would do. --json: the last line prints the result:
    {"result": "renamed"|"already", "name", "origin"}                                         exit 0
    {"result": "bad_name"|"not_found"|"name_taken"|"busy"|"failed", "detail"}                exit 1
What it does (in order; each step that changes something outside is recorded in hubconf.renames() when done):
  1. Checks (all done before touching anything): the new name's rules (lowercase letters, digits and -, at most 40
     characters; the same rules as sandboxes), not the platform itself, the new name not taken by the registry/a
     folder/a memory repo; if the origin is GitHub, no repo with the new name exists on GitHub (or it's the one we
     renamed last time); **no process has its working directory in the old folder** (subfolders included, e.g. a
     session in a worktree) → if one does, busy, with GitHub still untouched (10-25 review); this project has no
     half-done upgrade (upgrade-idea.py's record) → if it does, finish retrying the upgrade first
  2. Rename the GitHub repo (gh repo rename; GitHub redirects the old URL to the new one) and point the local origin at
     the new URL; skipped for projects without a remote (idea sandboxes)
  3. rename-project.sh <old> <new> --origin <new URL> --apply (registration, memory, mirror, folder, legacy-paths
     all at once); it refuses if a process's working directory is still in the old folder (e.g. an open session) → busy
Retry: old name no longer in the registry, new name present, and the record says it's this rename → already. If GitHub
is already renamed (the record's stage), step 2 is skipped.
Not done here: leases and conversation records (lease-runner handles those itself after calling this), renaming the
name inside the project's code (the project's own business).
"""
from __future__ import annotations

import datetime
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import names  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)

RECORD = hubconf.renames()
UPGRADES = hubconf.STATE / "idea-upgrades.json"   # upgrade-idea.py's record: a half-done upgrade must be finished first
BUSY = 75  # rename-project.sh's exit code for "a process's working directory is in the old folder"
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
GITHUB = re.compile(r"^(?:git@github\.com:|https://github\.com/)([^/]+)/([^/]+?)(?:\.git)?$")
JSON = False


def done(result: str, code: int, **extra):
    if JSON:
        print(json.dumps({"result": result, **extra}, ensure_ascii=False))
    elif code:
        print(f"project-rename: {extra.get('detail', result)}", file=sys.stderr)
    sys.exit(code)


def fail(msg: str, result: str = "failed") -> None:
    done(result, 1, detail=msg)


def records() -> dict:
    try:
        return json.loads(RECORD.read_text())
    except (OSError, ValueError):
        return {}


def record(old: str, **fields) -> None:
    rec = records()
    rec[old] = {**rec.get(old, {}), **fields, "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text(json.dumps(rec, ensure_ascii=False, indent=1))


def load(name: str) -> dict | None:
    import yaml
    f = hubconf.registry_dir() / f"{name}.yaml"
    return (yaml.safe_load(f.read_text()) or {}) if f.exists() else None


def users_in(path) -> str:
    """Processes whose working directory is in this folder (subfolders included)."""
    return " ".join(hubconf.cwd_users(path))


def unfinished_upgrade(*names: str) -> str:
    try:
        ups = json.loads(UPGRADES.read_text())
    except (OSError, ValueError):
        return ""
    for idea, rec in ups.items():
        if rec.get("stage", "done") != "done" and ({idea, rec.get("name")} & set(names)):
            return _("{idea} → {name}, stage {stage}", idea=idea, name=rec.get('name'), stage=rec.get('stage'))
    return ""


def gh_exists(repo: str) -> bool:
    return subprocess.run(["gh", "repo", "view", repo, "--json", "name"], capture_output=True).returncode == 0


def main(argv: list[str]) -> int:
    global JSON
    args = [a for a in argv if not a.startswith("--")]
    apply, JSON = "--apply" in argv, "--json" in argv
    if len(args) != 2:
        fail(_("usage: project-rename.py <old name> <new name> [--apply] [--json]"), "bad_name")
    old, new = args
    rec = records().get(old) or {}

    # retry: the last run already finished
    if load(old) is None:
        doc = load(new)
        if rec.get("new") == new and rec.get("stage") == "done" and doc is not None:
            done("already", 0, name=new, origin=(doc.get("paths") or {}).get("origin", ""))
        fail(_("{name} is not in the registry", name=old), "not_found")
    if not NAME.match(new) or "--" in new:
        fail(_("the new name may only use lowercase letters, digits and - (not at the start or end, at most 40 "
               "characters): {name!r}", name=new), "bad_name")
    if new == old:
        fail(_("the new name is the same as the old one"), "bad_name")
    if {old, new} & set(names.all("project")):   # the platform under either of its names
        fail(_("the platform itself cannot be renamed"), "bad_name")
    taken = [str(p) for p in (hubconf.registry_dir() / f"{new}.yaml", hubconf.p("workspace_root", "~/workspace") / new,
                              hubconf.p("services_root", "~/services") / new,
                              hubconf.p("git_origin", "~/git-origin") / f"{new}-memory.git",
                              hubconf.p("memory_copies", "~/.memory-copies") / new) if p.exists()]
    if taken:
        fail(_("{name} is already taken: {paths}", name=new, paths=', '.join(taken)), "name_taken")

    doc = load(old)
    origin = (doc.get("paths") or {}).get("origin") or "(native)"
    ws = hubconf.path((doc.get("paths") or {}).get("workspace"))
    up = unfinished_upgrade(old, new)
    if up:
        fail(_("{name} has an upgrade that stopped halfway ({upgrade}): retry the upgrade until it completes, then "
               "rename",
               name=old, upgrade=up))
    who = users_in(ws) if ws and ws.is_dir() else ""
    if who:
        fail(_("processes still have their working directory in {path}: {who}; close that project's sessions / "
               "terminal tabs first", path=ws, who=who), "busy")
    m = GITHUB.match(origin)
    new_origin, gh_step = origin, None
    if m:
        owner, repo = m.group(1), m.group(2)
        new_origin = origin[:m.start(2)] + new + (".git" if origin.endswith(".git") else "")
        if repo != new:
            already = rec.get("new") == new and rec.get("stage") in ("github", "done")
            if gh_exists(f"{owner}/{new}") and not already:
                fail(_("GitHub already has {owner}/{name}", owner=owner, name=new), "name_taken")
            gh_step = None if already else (f"{owner}/{repo}", new)

    plan = [_("GitHub: {old} → {owner}/{name}", old=gh_step[0], owner=owner, name=new) if gh_step
            else _("GitHub: nothing to change (no remote, or already renamed)"),
            _("local origin: {old} → {new}", old=origin, new=new_origin) if new_origin != origin
            else _("local origin: unchanged"),
            _("rename-project.sh {old} {new} --origin {origin} (registry entry, memory, mirror, folders)",
              old=old, new=new, origin=new_origin)]
    if not JSON:
        print("\n".join(f"  {i}. {p}" for i, p in enumerate(plan, 1)))
    if not apply:
        if not JSON:
            print(_("(dry-run; add --apply to run it)"))
        return 0

    if gh_step:
        r = subprocess.run(["gh", "repo", "rename", gh_step[1], "-R", gh_step[0], "--yes"], capture_output=True, text=True)
        if r.returncode != 0:
            fail(_("renaming the GitHub repo failed: {err}", err=(r.stderr or r.stdout).strip()[-300:]))
        record(old, new=new, stage="github")
    if new_origin != origin and ws:
        subprocess.run(["git", "-C", str(ws), "remote", "set-url", "origin", new_origin], capture_output=True)
    cmd = [str(hubconf.ROOT / "bin" / "rename-project.sh"), old, new, "--apply"]
    if new_origin != "(native)":
        cmd[3:3] = ["--origin", new_origin]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        out = (r.stderr or r.stdout).strip()
        if r.returncode == BUSY:  # someone went in between the checks and here
            fail(_("processes are still in the {name} folder (e.g. an open session); close them first: {out}",
                   name=old, out=out[-300:]), "busy")
        fail(_("rename-project.sh failed: {out}", out=out[-400:]))
    record(old, new=new, stage="done", origin=new_origin)
    done("renamed", 0, name=new, origin=new_origin)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
