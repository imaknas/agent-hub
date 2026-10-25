#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""加密的雲端備份(restic → Backblaze B2)。設定在 hub.json 的 backup。

  backup.py run       每晚(平台 job):stage 一致的副本 → restic backup → 週日套保留規則 → restic check
  backup.py verify    每月(平台 job):還原最新快照到暫存目錄,檢查 SQLite、git bundle、必要檔案,
                      再 restic check --read-data-subset=10%;跑完刪掉暫存目錄
  backup.py restore <目的地> [--include <路徑>]   還原(新機器:先放回 .secrets/backup-password 跟 b2-env)
  backup.py snapshots                            列出快照

備份什麼(只有這台 mini 有、丟了救不回來的):
  - SQLite(匯流排 bus.db、muninn.db):用 backup API 複製一份再備份,不碰正在寫的 db/WAL
  - ~/git-origin 底下的 bare repo(memory 分支、agent-hub 本身、沒有 GitHub remote 的專案):git bundle --all
  - 各專案 workspace 還沒 push 的東西:沒在任何 remote 上的 commit 打成 bundle、還沒 commit 的改動存成 diff、
    .env*(三層內,例:backend/.env)
  - hub.json backup.include 列的目錄(muninn data、平台狀態、secrets、服務的資料、Claude Code transcript)
不備份:工作 profile 的 transcript(公司內容)、GitHub mirror、完整 workspace、產生的影片、venv/cache。
失敗就在匯流排發 job.failed,exit 1(排程器記失敗 → 狀態頁紅)。結果寫 .state/backup/last.json。
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import secrets as pysecrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402

HUB = hubconf.ROOT
CFG = hubconf.get("backup", {})
STAGE = hubconf.STATE / "backup-staging"
OUT = hubconf.STATE / "backup"
PASSWORD = HUB / ".secrets" / "backup-password"
B2ENV = HUB / ".secrets" / "b2-env"
RESTIC = shutil.which("restic") or "/opt/homebrew/bin/restic"
DIFF_LIMIT = 50 * 1024 * 1024


def now() -> datetime.datetime:
    return datetime.datetime.now().astimezone().replace(microsecond=0)


def say(msg: str) -> None:
    print(f"[{now():%H:%M:%S}] {msg}", flush=True)


def env() -> dict:
    e = dict(os.environ)
    for line in B2ENV.read_text().split():
        if "=" in line:
            k, v = line.split("=", 1)
            e[k] = v
    e["RESTIC_REPOSITORY"] = CFG["repo"]
    e["RESTIC_PASSWORD_FILE"] = str(PASSWORD)
    return e


def restic(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run([RESTIC, *args], env=env(), capture_output=capture, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"restic {args[0]} 失敗(exit {r.returncode}){(r.stderr or '')[-400:] if capture else ''}")
    return r


def fail(stage: str, err: str) -> int:
    say(f"失敗({stage}):{err}")
    subprocess.run([sys.executable, str(HUB / "bin" / "hubbus"), "publish", "job.failed", "--source", "backup",
                    "--payload", json.dumps({"job": f"backup:{stage}", "detail": err[:500]}, ensure_ascii=False)],
                   capture_output=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"last-{stage}-failure.json").write_text(json.dumps({"at": now().isoformat(), "error": err[:2000]}, ensure_ascii=False))
    return 1


def ensure_repo() -> None:
    if not PASSWORD.exists():
        PASSWORD.parent.mkdir(parents=True, exist_ok=True)
        PASSWORD.write_text(pysecrets.token_urlsafe(32) + "\n")
        PASSWORD.chmod(0o600)
        say(f"產生了備份密碼:{PASSWORD}(請把內容另外存進密碼管理器——mini 不見時只有那份能解開備份)")
    if restic("cat", "config", check=False, capture=True).returncode != 0:
        say(f"建立 repository {CFG['repo']}")
        restic("init")


# ---------- stage ----------

ENV_SKIP = {"node_modules", ".venv", "venv", ".git", "dist", "build", "__pycache__", ".next", ".agent-memory", ".claude"}


def is_env_name(name: str) -> bool:
    return name == ".env" or (name.startswith(".env.") and not name.endswith((".example", ".sample", ".template")))


def workspace_env_files(ws: pathlib.Path) -> list[pathlib.Path]:
    """workspace 裡(三層內,跳過 node_modules 之類)的 .env / .env.*:不進版控、丟了救不回來(例:backend/.env)。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(ws):
        depth = len(pathlib.Path(dirpath).relative_to(ws).parts)
        dirnames[:] = [] if depth >= 3 else [d for d in dirnames if d not in ENV_SKIP]
        out += [pathlib.Path(dirpath) / f for f in filenames if is_env_name(f)]
    return sorted(p for p in out if p.is_file())


def covers(path: pathlib.Path) -> bool:
    """這個檔案會不會進備份(check-secrets.py 用;規則跟 cmd_run / stage 一樣):在 include 底下、沒被 exclude,
    或是登錄的專案 workspace 裡的 .env / .env.*(stage 會複製,見 workspace_env_files)。"""
    import fnmatch
    path = path.expanduser().resolve()
    dbs = [hubconf.bus_db(), *(hubconf.path(d["path"]) for d in CFG.get("sqlite") or [])]
    if any(db and path == db.resolve() for db in dbs):  # SQLite 用 backup API 另外複製
        return True
    for x in CFG.get("exclude") or []:
        pat = str(hubconf.path(x)) if x.startswith(("~", "/")) else x
        if fnmatch.fnmatch(str(path) if "/" in pat else path.name, pat):
            return False
    for x in CFG.get("include") or []:
        inc = hubconf.path(x)
        if inc and (path == inc.resolve() or inc.resolve() in path.parents):
            return True
    if is_env_name(path.name):
        for n in hubconf.projects():
            ws = hubconf.workspace(n)
            if ws and path in (p.resolve() for p in workspace_env_files(ws.resolve())):
                return True
    return False


def stage() -> dict:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    (STAGE / "sqlite").mkdir(parents=True)
    (STAGE / "git").mkdir()
    (STAGE / "workspace").mkdir()
    manifest: dict = {"at": now().isoformat(), "sqlite": [], "bundles": [], "workspace": []}
    dbs = [{"name": "bus", "path": str(hubconf.bus_db())}] + list(CFG.get("sqlite") or [])
    for d in dbs:
        src = hubconf.path(d["path"])
        if not src or not src.exists():
            continue
        dst = STAGE / "sqlite" / f"{d['name']}.db"
        s, t = sqlite3.connect(src, timeout=60), sqlite3.connect(dst)
        s.backup(t)
        ok = t.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        s.close(); t.close()
        if not ok:
            raise RuntimeError(f"{d['name']} 的副本 integrity_check 不是 ok")
        manifest["sqlite"].append(d["name"])
    root = hubconf.path(CFG.get("bare_repos", "~/git-origin"))
    for repo in sorted(root.glob("*.git")) if root and root.is_dir() else []:
        out = STAGE / "git" / f"{repo.name}.bundle"
        r = subprocess.run(["git", "-C", str(repo), "bundle", "create", str(out), "--all"], capture_output=True, text=True)
        if r.returncode == 0:
            manifest["bundles"].append(repo.name)
        elif "empty bundle" not in r.stderr:
            raise RuntimeError(f"git bundle {repo.name}:{r.stderr.strip()[-200:]}")
    for name in sorted(hubconf.projects()):
        ws = hubconf.workspace(name)
        if not ws or not (ws / ".git").exists():
            continue
        d = STAGE / "workspace" / name
        d.mkdir()
        got = []
        r = subprocess.run(["git", "-C", str(ws), "bundle", "create", str(d / "unpushed.bundle"), "--branches", "--not", "--remotes"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            got.append("unpushed.bundle")
        diff = subprocess.run(["git", "-C", str(ws), "diff", "HEAD", "--binary"], capture_output=True)
        if diff.stdout and len(diff.stdout) <= DIFF_LIMIT:
            (d / "uncommitted.diff").write_bytes(diff.stdout)
            got.append("uncommitted.diff")
        for f in workspace_env_files(ws):
            rel = f.relative_to(ws)
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, d / rel)
            got.append(str(rel))
        if got:
            manifest["workspace"].append({"project": name, "files": got})
        else:
            d.rmdir()
    (STAGE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    return manifest


# ---------- 指令 ----------

def cmd_run() -> int:
    try:
        ensure_repo()
        m = stage()
        say(f"stage:SQLite {len(m['sqlite'])}、bare repo {len(m['bundles'])}、有沒 push 的 workspace {len(m['workspace'])}")
        # stage 目錄在 .state 底下,.state 本身在 include 裡,不另外列(不然快照裡會有兩份)
        paths = [str(p) for p in (hubconf.path(x) for x in CFG.get("include") or []) if p and p.exists()]
        if not any(STAGE.is_relative_to(pathlib.Path(x)) for x in paths):
            paths.insert(0, str(STAGE))
        args = ["backup", "--tag", "nightly", "--json"]
        for x in CFG.get("exclude") or []:
            args += ["--exclude", str(hubconf.path(x)) if x.startswith("~") else x]
        r = restic(*args, *paths, capture=True)
        summary = next((json.loads(l) for l in r.stdout.splitlines() if '"message_type":"summary"' in l), {})
        if now().weekday() == 6:  # 週日套保留規則
            k = CFG.get("keep") or {}
            restic("forget", "--prune", "--keep-daily", str(k.get("daily", 14)), "--keep-weekly", str(k.get("weekly", 8)),
                   "--keep-monthly", str(k.get("monthly", 12)), capture=True)
        restic("check", capture=True)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "last.json").write_text(json.dumps({
            "ok": True, "at": now().isoformat(), "snapshot": summary.get("snapshot_id"),
            "files": summary.get("total_files_processed"), "bytes": summary.get("total_bytes_processed"),
            "added_bytes": summary.get("data_added"), "manifest": m}, ensure_ascii=False, indent=1))
        say(f"完成:快照 {str(summary.get('snapshot_id'))[:8]}、{summary.get('total_files_processed')} 個檔、"
            f"新增 {round((summary.get('data_added') or 0) / 1e6, 1)} MB")
        return 0
    except Exception as e:
        return fail("run", repr(e))


def cmd_verify() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="backup-verify-", dir=hubconf.STATE))
    try:
        restic("restore", "latest", "--target", str(tmp), capture=True)
        staged = next(tmp.rglob("backup-staging"), None)
        if staged is None:
            raise RuntimeError("還原的快照裡沒有 backup-staging")
        m = json.loads((staged / "manifest.json").read_text())
        for name in m["sqlite"]:
            c = sqlite3.connect(staged / "sqlite" / f"{name}.db")
            if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError(f"還原的 {name}.db integrity_check 不是 ok")
        for b in m["bundles"]:
            r = subprocess.run(["git", "bundle", "verify", str(staged / "git" / f"{b}.bundle")], capture_output=True, text=True,
                               cwd=tmp)
            if r.returncode != 0:
                raise RuntimeError(f"還原的 {b}.bundle 驗證失敗:{r.stderr.strip()[-200:]}")
        need = [p for p in (hubconf.path(x) for x in CFG.get("include") or []) if p and p.exists()]
        missing = [str(p) for p in need if not (tmp / str(p).lstrip("/")).exists()]
        if missing:
            raise RuntimeError(f"還原的快照少了:{', '.join(missing[:5])}")
        restic("check", "--read-data-subset=10%", capture=True)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "last-verify.json").write_text(json.dumps({"ok": True, "at": now().isoformat(), "sqlite": m["sqlite"],
                                                          "bundles": len(m["bundles"])}, ensure_ascii=False))
        say(f"還原測試通過:SQLite {len(m['sqlite'])} 個、bundle {len(m['bundles'])} 個、{len(need)} 個目錄都在、10% 資料抽查 OK")
        return 0
    except Exception as e:
        return fail("verify", repr(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    a = sys.argv[1:]
    if a[:1] == ["run"]:
        return cmd_run()
    if a[:1] == ["verify"]:
        return cmd_verify()
    if a[:1] == ["snapshots"]:
        return restic("snapshots").returncode
    if a[:1] == ["restore"] and len(a) >= 2:
        args = ["restore", "latest", "--target", a[1]]
        if "--include" in a:
            args += ["--include", a[a.index("--include") + 1]]
        return restic(*args).returncode
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
