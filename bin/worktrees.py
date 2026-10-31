"""Overview of every project's git worktrees (for the status page) and their cleanup (for bin/prune-worktrees).
Standard library only.

One worktree = one parallel session's branch. Computed here: the branch, how long ago the last commit was, whether it's
merged into main, whether it's clean, which files it changed (compared with main), and two or more branches changing
the same file (to know early about a collision, without declaring paths up front).
Also the number of recent commits made directly on main without the merge gate (merge-branch): information only, never
blocking.

Automatic cleanup (auto_prune, run nightly by the platform job agent-hub/prune-worktrees; since 2026-10-24): only
touches "merged and clean" worktrees.
  Idle days = whichever is more recent of the branch's last commit and that worktree's session transcript
  (~/.claude/projects/<slug>/*.jsonl): looking only at commits would count "chatting yesterday, no commit for three
  days" as idle for three days.
  Level 1 (idle ≥ 2 days): only deletes dependencies that can be rebuilt: `.venv` (with uv.lock or pyproject.toml
    next to it), `node_modules` (with a lock file next to it; without a lock file it's not deleted, since a reinstall
    isn't guaranteed identical). Folder, branch and files all stay, and the session can still be resumed;
    the marker is written in that worktree's git directory (.git/worktrees/<name>/agent-hub-deps-pruned, which
    doesn't make the worktree dirty), and worktree-memory.sh reminds how to rebuild when a session starts. One muninn
    worktree is about 190 MB, 90% of it .venv.
  Level 2 (idle ≥ 14 days, and no process has its cwd inside): `git worktree remove` (without --force), **the branch
    stays** (it's only a ref; if a session that was never archived needs resuming, it can be rebuilt from it). If even
    one ignored file isn't on the REGENERABLE list (e.g. .env, build output), the whole worktree is skipped with the
    reason listed in the result: no guessing what can be thrown away.
  **If any process has its cwd in the worktree, neither level touches it at all**: an idle session that wasn't
  archived keeps a process around (tested 10-24: the claude that Desktop pushed over), and it may just be that the
  owner is busy and hasn't come back to it yet. So this only cleans orphan worktrees with "no session open at all"
  (10-24: 35 of 37 were like that). A child session that is done but not archived looks the same as a paused session,
  and this can't tell them apart: that has to be decided from the session list (startedBy), not here.
  The `prune-worktrees --yes` a person runs works as before: merged and clean → deleted along with the branch (the
  person has confirmed no session is open).
"""
from __future__ import annotations

import datetime
import json
import pathlib
import shutil
import sqlite3
import subprocess
import time

import hubconf
import i18n

_ = i18n.domain(__file__)

DEPS_IDLE_DAYS = 2
REMOVE_IDLE_DAYS = 14
# dependency folder → it's deleted only with one of these "rebuild recipes" next to it
DEPS = {".venv": ("uv.lock", "pyproject.toml"), "node_modules": ("package-lock.json", "pnpm-lock.yaml", "yarn.lock")}
REBUILD = {".venv": "uv sync", "node_modules": "npm ci"}
# level 2: remove the whole worktree only when the ignored things are just these (names)
REGENERABLE = {".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".DS_Store"}
MARKER = "agent-hub-deps-pruned"


def git(cwd, *args) -> str:
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def base_branch(repo: pathlib.Path) -> str | None:
    return next((b for b in ("main", "master") if git(repo, "rev-parse", "--verify", "-q", b)), None)


def project_worktrees(repo: pathlib.Path) -> list[dict]:
    base = base_branch(repo)
    if not base:
        return []
    out, cur = [], {}
    for line in git(repo, "worktree", "list", "--porcelain").splitlines() + [""]:
        if line.startswith("worktree "):
            cur = {"path": line[9:]}
        elif line.startswith("branch "):
            cur["branch"] = line[7:].removeprefix("refs/heads/")
        elif line == "" and cur:
            p = pathlib.Path(cur["path"])
            if p.resolve() != repo.resolve() and p.name != ".agent-memory" and cur.get("branch") not in (None, "agent-memory"):
                b = cur["branch"]
                last = git(repo, "log", "-1", "--format=%ct", b).strip()
                merged = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", b, base]).returncode == 0
                cur.update(base=base, merged=merged,
                           age_days=round((time.time() - int(last)) / 86400, 1) if last else None,
                           clean=p.exists() and not git(p, "status", "--porcelain").strip(),
                           exists=p.exists(),
                           files=git(repo, "diff", "--name-only", f"{base}...{b}").split())
                out.append(cur)
            cur = {}
    return out


def ungated_main_commits(repo: pathlib.Path, project: str, days: int = 7) -> int:
    """The number of commits on main in the last days days that didn't come in through merge-branch (counted only,
    never blocked)."""
    base = base_branch(repo)
    if not base:
        return 0
    shas = git(repo, "log", f"--since={days} days ago", "--format=%H", base).split()
    gated = set()
    bus = hubconf.bus_db()
    if bus.exists():
        for (pl,) in sqlite3.connect(bus, timeout=10).execute(
                "SELECT payload FROM events WHERE type='merge.done' AND source=?", (project,)):
            gated |= set(json.loads(pl).get("commits") or [])
    return sum(1 for s in shas if s not in gated)


def overview() -> dict[str, dict]:
    out = {}
    for name in sorted(hubconf.projects()):
        repo = hubconf.workspace(name)
        if not repo or not (repo / ".git").exists():
            continue
        wts = project_worktrees(repo)
        touched: dict[str, list[str]] = {}
        for w in wts:
            if not w["merged"]:
                for f in w["files"]:
                    touched.setdefault(f, []).append(w["branch"])
        out[name] = {"worktrees": wts, "overlaps": {f: bs for f, bs in touched.items() if len(bs) > 1},
                     "ungated_main_7d": ungated_main_commits(repo, name)}
    hub = hubconf.ROOT
    if hubconf.hub_project() not in out:
        wts = project_worktrees(hub)
        out[hubconf.hub_project()] = {"worktrees": wts, "overlaps": {}, "ungated_main_7d": ungated_main_commits(hub, hubconf.hub_project())}
    return out


def prune(project: str | None, yes: bool) -> int:
    n = 0
    for name, info in overview().items():
        if project and name != project:
            continue
        repo = hubconf.ROOT if name == hubconf.hub_project() else hubconf.workspace(name)
        for w in info["worktrees"]:
            if w["merged"] and w["clean"]:
                n += 1
                kw = dict(project=name, branch=w['branch'], path=w['path'], days=w['age_days'])
                print(_("Deleting: {project} {branch} ({path}, untouched for {days} {days:day|days})", **kw) if yes
                      else _("Can delete: {project} {branch} ({path}, untouched for {days} {days:day|days})", **kw))
                if yes:
                    subprocess.run(["git", "-C", str(repo), "worktree", "remove", w["path"]])
                    subprocess.run(["git", "-C", str(repo), "branch", "-d", w["branch"]], capture_output=True)
            elif not w["merged"]:
                print(_("Kept (not merged yet): {project} {branch}", project=name, branch=w['branch']))
    if n and not yes:
        print(_("(only listed; once you are sure no session is still open in them, add --yes to delete them)"))
    return 0


def _cwds_in_use() -> list[str]:
    """Every process's cwd (hubconf.process_cwds: lsof or /proc). An idle Desktop session also has a process sitting
    in its worktree."""
    import hubconf
    return [cwd for _pid, _name, cwd in hubconf.process_cwds()]


def _transcript_age_days(path: str) -> float | None:
    import runtime_ports  # conversation records only through the interface (Claude Code is one implementation)
    try:
        last = runtime_ports.load(ports=("transcripts",)).last_activity(path)
    except runtime_ports.Unsupported:   # no conversation records: look only at the folder's own time
        return None
    return round((time.time() - last.timestamp()) / 86400, 1) if last else None


def _idle_days(w: dict) -> float | None:
    ages = [a for a in (w.get("age_days"), _transcript_age_days(w["path"])) if a is not None]
    return min(ages) if ages else None


def _unknown_ignored(p: pathlib.Path) -> list[str]:
    """Things that are ignored and not in REGENERABLE (level 2 skips when it meets one)."""
    out = []
    for line in git(p, "status", "--porcelain", "--ignored").splitlines():
        if line.startswith("!! "):
            entry = line[3:].rstrip("/")
            if pathlib.PurePath(entry).name not in REGENERABLE:
                out.append(entry)
    return out


def _git_dir(p: pathlib.Path) -> pathlib.Path | None:
    g = git(p, "rev-parse", "--absolute-git-dir").strip()
    return pathlib.Path(g) if g else None


def _size_mb(p: pathlib.Path) -> float:
    r = subprocess.run(["du", "-sk", str(p)], capture_output=True, text=True)
    return round(int(r.stdout.split()[0]) / 1024, 1) if r.returncode == 0 and r.stdout else 0.0


def auto_prune(dry_run: bool) -> dict:
    """The nightly automatic cleanup (see the file header). Returns the result (also written to
    .state/worktree-prune.json for the status page)."""
    in_use = _cwds_in_use()
    res = {"at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "dry_run": dry_run,
           "deps": [], "removed": [], "skipped": [], "freed_mb": 0.0}
    for name, info in overview().items():
        repo = hubconf.ROOT if name == hubconf.hub_project() else hubconf.workspace(name)
        for w in info["worktrees"]:
            if not (w["exists"] and w["merged"] and w["clean"]):
                continue
            p = pathlib.Path(w["path"])
            idle = _idle_days(w)
            label = f"{name} {w['branch']}"
            if idle is None or idle < DEPS_IDLE_DAYS:
                continue
            # a session open inside (even idle, the owner just not back yet) → don't touch it at all, either level
            if any(c == str(p) or c.startswith(str(p) + "/") for c in in_use):
                continue
            if idle >= REMOVE_IDLE_DAYS:
                unknown = _unknown_ignored(p)
                if unknown:
                    res["skipped"].append(_("{label}: ignored files not on the rebuildable list ({files}…); kept",
                                            label=label, files=", ".join(unknown[:3])))
                else:
                    mb = _size_mb(p)
                    if not dry_run:
                        r = subprocess.run(["git", "-C", str(repo), "worktree", "remove", str(p)], capture_output=True, text=True)
                        if r.returncode != 0:
                            res["skipped"].append(_("{label}: git worktree remove failed: {err}", label=label,
                                                    err=r.stderr.strip()[:200]))
                            continue
                    res["removed"].append(_("{label} (idle {days} {days:day|days}, {mb} MB; branch kept)", label=label, days=idle,
                                            mb=mb))
                    res["freed_mb"] += mb
                    continue
            gd = _git_dir(p)
            for dep, recipes in DEPS.items():
                d = p / dep
                if not d.is_dir() or d.is_symlink():
                    continue
                if not any((p / r).exists() for r in recipes):
                    continue
                mb = _size_mb(d)
                if not dry_run:
                    shutil.rmtree(d)
                    if gd:
                        with open(gd / MARKER, "a") as fh:
                            fh.write(_("{date} {dep} → rebuild: {cmd}", date=res['at'][:10], dep=dep, cmd=REBUILD[dep])
                                     + "\n")
                res["deps"].append(_("{label} {dep} (idle {days} {days:day|days}, {mb} MB)", label=label, dep=dep, days=idle, mb=mb))
                res["freed_mb"] += mb
    res["freed_mb"] = round(res["freed_mb"], 1)
    if not dry_run:
        f = hubconf.STATE / "worktree-prune.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    return res
