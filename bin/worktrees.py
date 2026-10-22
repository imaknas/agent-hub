"""各專案的 git worktree 一覽(狀態頁用)跟清理(bin/prune-worktrees 用)。標準函式庫。

一個 worktree = 一個平行 session 的 branch。這裡算:branch、最後一個 commit 多久前、合併進 main 了沒、
乾不乾淨、改了哪些檔(跟 main 比),以及兩個以上 branch 改同一個檔(提早知道會撞,不用事先宣告路徑)。
也算最近直接 commit 到 main、沒經過合併閘門(merge-branch)的 commit 數——只是資訊,不擋。

不自動刪任何 worktree:閒置但沒封存的 session 的 cwd 可能還在裡面,刪了 resume 會壞。
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import subprocess
import time

import hubconf


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
    """最近 days 天 main 上不是 merge-branch 合進來的 commit 數(只算,不擋)。"""
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
                print(f"{'刪' if yes else '可以刪'}:{name} {w['branch']}({w['path']},{w['age_days']} 天沒動)")
                if yes:
                    subprocess.run(["git", "-C", str(repo), "worktree", "remove", w["path"]])
                    subprocess.run(["git", "-C", str(repo), "branch", "-d", w["branch"]], capture_output=True)
            elif not w["merged"]:
                print(f"留著(還沒合併):{name} {w['branch']}")
    if n and not yes:
        print("(只列出來;確定沒有 session 還開在裡面,再加 --yes 真的刪)")
    return 0
