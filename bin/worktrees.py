"""各專案的 git worktree 一覽(狀態頁用)跟清理(bin/prune-worktrees 用)。標準函式庫。

一個 worktree = 一個平行 session 的 branch。這裡算:branch、最後一個 commit 多久前、合併進 main 了沒、
乾不乾淨、改了哪些檔(跟 main 比),以及兩個以上 branch 改同一個檔(提早知道會撞,不用事先宣告路徑)。
也算最近直接 commit 到 main、沒經過合併閘門(merge-branch)的 commit 數——只是資訊,不擋。

自動清理(auto_prune,平台 job agent-hub/prune-worktrees 每晚跑;2026-10-24 起):只碰「已合併、乾淨」的 worktree。
  閒置天數 = 分支最後一個 commit 跟那個 worktree 的 session 對話紀錄(~/.claude/projects/<slug>/*.jsonl)
  兩者中比較近的那個——只看 commit 會把「昨天還在聊、三天沒 commit」算成閒置三天。
  第一級(閒置 ≥ 2 天):只刪重建得回來的依賴——`.venv`(旁邊有 uv.lock 或 pyproject.toml)、`node_modules`
    (旁邊有 lock 檔;沒有 lock 檔的不刪,重裝不保證一樣)。資料夾、branch、檔案都留著,session 照樣接得回來;
    標記寫在那個 worktree 的 git 目錄(.git/worktrees/<名稱>/agent-hub-deps-pruned,不會讓 worktree 變髒),
    session 開始時 worktree-memory.sh 會提醒怎麼重建。muninn 的 worktree 一個約 190 MB,9 成是 .venv。
  第二級(閒置 ≥ 14 天,而且沒有任何 process 的 cwd 在裡面):`git worktree remove`(不加 --force),**branch 留著**
    (只是一個 ref;萬一還有沒封存的 session 要接回,可以從它重建)。被 ignore 的檔案只要有一個不在
    REGENERABLE 清單(例:.env、建置產物),整個跳過、在結果裡列出原因——不猜哪些能丟。
  **只要有 process 的 cwd 在 worktree 裡,兩級都完全不碰**:閒置但沒封存的 session 會留著一個 process
  (10-24 實測:Desktop 推過來的 claude),那可能只是 Knas 太忙還沒回來繼續。所以這支只清「沒有任何
  session 開著」的孤兒 worktree(10-24:37 個裡 35 個是這種)。做完了卻沒封存的子 session 看起來跟
  暫停中的 session 一樣,這裡分不出來——那要從 session 清單(startedBy)判斷,不是這支的事。
  由人跑的 `prune-worktrees --yes` 照舊:已合併、乾淨就連 branch 一起刪(人確認過沒有 session 開著)。
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import time

import hubconf

DEPS_IDLE_DAYS = 2
REMOVE_IDLE_DAYS = 14
# 依賴資料夾 → 旁邊要有其中一個「重建配方」才刪
DEPS = {".venv": ("uv.lock", "pyproject.toml"), "node_modules": ("package-lock.json", "pnpm-lock.yaml", "yarn.lock")}
REBUILD = {".venv": "uv sync", "node_modules": "npm ci"}
# 第二級:被 ignore 的東西只有這些(名稱)時才整個移除
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


def _cwds_in_use() -> list[str]:
    """所有 process 的 cwd(lsof)。閒置的 Desktop session 也有一個 process 待在它的 worktree。"""
    r = subprocess.run(["lsof", "-d", "cwd", "-Fn"], capture_output=True, text=True)
    return [l[1:] for l in r.stdout.splitlines() if l.startswith("n/")]


def _transcript_age_days(path: str) -> float | None:
    import runtime_ports  # 對話紀錄只透過介面讀(Claude Code 只是一個實作)
    try:
        last = runtime_ports.load(ports=("transcripts",)).last_activity(path)
    except runtime_ports.Unsupported:   # 沒有對話紀錄:只看資料夾本身的時間
        return None
    return round((time.time() - last.timestamp()) / 86400, 1) if last else None


def _idle_days(w: dict) -> float | None:
    ages = [a for a in (w.get("age_days"), _transcript_age_days(w["path"])) if a is not None]
    return min(ages) if ages else None


def _unknown_ignored(p: pathlib.Path) -> list[str]:
    """被 ignore、而且不在 REGENERABLE 的東西(第二級遇到就跳過)。"""
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
    """每晚的自動清理(見檔頭)。回傳結果(也寫進 .state/worktree-prune.json 給狀態頁)。"""
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
            # 有 session 開在裡面(就算閒置、只是 Knas 還沒回來繼續)就完全不碰,兩級都一樣
            if any(c == str(p) or c.startswith(str(p) + "/") for c in in_use):
                continue
            if idle >= REMOVE_IDLE_DAYS:
                unknown = _unknown_ignored(p)
                if unknown:
                    res["skipped"].append(f"{label}:有 ignore 的檔案不在可重建清單({', '.join(unknown[:3])}…),留著")
                else:
                    mb = _size_mb(p)
                    if not dry_run:
                        r = subprocess.run(["git", "-C", str(repo), "worktree", "remove", str(p)], capture_output=True, text=True)
                        if r.returncode != 0:
                            res["skipped"].append(f"{label}:git worktree remove 失敗:{r.stderr.strip()[:200]}")
                            continue
                    res["removed"].append(f"{label}(閒置 {idle} 天,{mb} MB;branch 留著)")
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
                            fh.write(f"{res['at'][:10]} {dep} → 重建:{REBUILD[dep]}\n")
                res["deps"].append(f"{label} {dep}(閒置 {idle} 天,{mb} MB)")
                res["freed_mb"] += mb
    res["freed_mb"] = round(res["freed_mb"], 1)
    if not dry_run:
        f = hubconf.STATE / "worktree-prune.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    return res
