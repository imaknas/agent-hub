#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Encrypted cloud backup (restic → Backblaze B2). Settings in {settings}'s backup.

  backup.py run       nightly (platform job): stage consistent copies → restic backup → apply the retention rules on
                      Sundays → restic check
  backup.py verify    monthly (platform job): restore the latest snapshot into a temp dir, check SQLite, git bundles and
                      required files, then restic check --read-data-subset=10%; the temp dir is deleted afterwards
  backup.py restore <destination> [--include <path>]   restore (new machine: first put backup-password and b2-env back
                                                       into the secrets directory)
  backup.py snapshots                            list the snapshots

What is backed up (what only the hub machine has, and cannot be recovered once lost):
  - SQLite (the bus's bus.db, the databases in backup.sqlite): copied with the backup API first, so the db/WAL being
    written is never touched
  - the bare repos under ~/git-origin (memory branches, brunna itself, projects without a GitHub remote):
    git bundle --all
  - what each project workspace has not pushed: commits not on any remote as a bundle, uncommitted changes as a diff
    (both made by the project's own job, project_git.py; where its sandbox refuses, the manifest lists it as skipped),
    .env* (up to three levels deep, e.g. backend/.env)
  - the directories listed in {settings} backup.include (the assistant's data, platform state, secrets, service data,
    Claude Code transcripts)
Not backed up: transcripts of the work profile (company content), GitHub mirrors, whole workspaces, generated videos,
venv/cache, and live sessions' wake addresses (hubconf.session_addresses_dir: one only works while its session runs).
On failure: job.failed on the bus and exit 1 (the scheduler records the failure → the status page goes red). The result
is written to .state/backup/last.json.
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
import i18n  # noqa: E402
import project_git  # noqa: E402
from project_git import EX_SANDBOX  # noqa: E402

_ = i18n.domain(__file__)

HUB = hubconf.ROOT
CFG = hubconf.get("backup", {})
STAGE = hubconf.STATE / "backup-staging"
OUT = hubconf.STATE / "backup"
PASSWORD = hubconf.secrets_dir() / "backup-password"
B2ENV = hubconf.secrets_dir() / "b2-env"
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
        detail = (r.stderr or '')[-400:] if capture else ''
        raise RuntimeError(_("restic {cmd} failed (exit {code}): {detail}", cmd=args[0], code=r.returncode, detail=detail)
                           if detail else _("restic {cmd} failed (exit {code})", cmd=args[0], code=r.returncode))
    return r


def fail(stage: str, err: str) -> int:
    say(_("Failed ({stage}): {err}", stage=stage, err=err))
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
        say(_("Generated the backup password: {path} (keep a copy of its content in a password manager: if the hub "
              "machine is lost, only that copy can decrypt the backups)", path=PASSWORD))
    if restic("cat", "config", check=False, capture=True).returncode != 0:
        say(_("Creating repository {repo}", repo=CFG["repo"]))
        restic("init")


# ---------- stage ----------

ENV_SKIP = {"node_modules", ".venv", "venv", ".git", "dist", "build", "__pycache__", ".next", ".agent-memory", ".claude"}


def is_env_name(name: str) -> bool:
    return name == ".env" or (name.startswith(".env.") and not name.endswith((".example", ".sample", ".template")))


def workspace_env_files(ws: pathlib.Path) -> list[pathlib.Path]:
    """.env / .env.* in the workspace (up to three levels deep, skipping node_modules and the like): not in version
    control, can't be recovered if lost (e.g. backend/.env)."""
    out = []
    for dirpath, dirnames, filenames in os.walk(ws):
        depth = len(pathlib.Path(dirpath).relative_to(ws).parts)
        dirnames[:] = [] if depth >= 3 else [d for d in dirnames if d not in ENV_SKIP]
        out += [pathlib.Path(dirpath) / f for f in filenames if is_env_name(f)]
    return sorted(p for p in out if p.is_file())


def covers(path: pathlib.Path) -> bool:
    """Whether this file goes into the backup (used by check-secrets.py; same rules as cmd_run / stage): under include
    and not excluded, or a .env / .env.* in a registered project's workspace (stage copies those, see
    workspace_env_files)."""
    import fnmatch
    path = path.expanduser().resolve()
    if {hubconf.session_addresses_dir().resolve(), (hubconf.secrets_dir() / "tmp").resolve()} & {path, *path.parents}:   # never backed up (module header)
        return False
    dbs = [hubconf.bus_db(), *(hubconf.path(d["path"]) for d in CFG.get("sqlite") or [])]
    if any(db and path == db.resolve() for db in dbs):  # SQLite is copied separately with the backup API
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
            raise RuntimeError(_("integrity_check of the copy of {name} is not ok", name=d["name"]))
        manifest["sqlite"].append(d["name"])
    root = hubconf.path(CFG.get("bare_repos", "~/git-origin"))
    for repo in sorted(root.glob("*.git")) if root and root.is_dir() else []:
        out = STAGE / "git" / f"{repo.name}.bundle"
        r = subprocess.run(["git", "-C", str(repo), "bundle", "create", str(out), "--all"], capture_output=True, text=True)
        if r.returncode == 0:
            manifest["bundles"].append(repo.name)
        elif "empty bundle" not in r.stderr:
            raise RuntimeError(_("git bundle {repo}: {err}", repo=repo.name, err=r.stderr.strip()[-200:]))
    for name in sorted(hubconf.projects()):
        ws = hubconf.workspace(name)
        if not ws or not (ws / ".git").exists():
            continue
        d = STAGE / "workspace" / name
        d.mkdir()
        got = []
        # git in the project's repo runs as the project (project_git.py: sandboxed when it is); the bundle and the diff
        # come back on stdout, as data
        with (d / "unpushed.bundle").open("wb") as fh:
            r = project_git.job(name, ws, "bundle-unpushed", ws, out=fh, timeout=project_git.BUNDLE_TIMEOUT_S,
                                cap=project_git.MAX_BUNDLE)
        diff = project_git.job(name, ws, "diff-head", ws, timeout=project_git.BUNDLE_TIMEOUT_S, cap=DIFF_LIMIT) \
            if r.returncode != EX_SANDBOX else r
        if EX_SANDBOX in (r.returncode, diff.returncode):
            why = diff.stderr.strip()[-200:]
            manifest.setdefault("skipped", []).append({"project": name, "why": why})
            say(_("{project}: its unpushed and uncommitted work is not in this backup (its git runs only as the "
                  "project, and that was refused: {why})", project=name, why=why))
        if r.returncode == 0:
            got.append("unpushed.bundle")
        else:
            (d / "unpushed.bundle").unlink()
        if diff.returncode == 0 and diff.stdout:   # over DIFF_LIMIT the job was stopped (TOO_BIG): not kept
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


# ---------- commands ----------

def cmd_run() -> int:
    try:
        ensure_repo()
        m = stage()
        say(_("stage: SQLite {sqlite}, bare repos {bundles}, workspaces with unpushed work {workspaces}",
              sqlite=len(m["sqlite"]), bundles=len(m["bundles"]), workspaces=len(m["workspace"])))
        # the stage directory is under .state, and .state itself is in include, so it isn't listed separately
        # (otherwise the snapshot would hold two copies)
        paths = [str(p) for p in (hubconf.path(x) for x in CFG.get("include") or []) if p and p.exists()]
        if not any(STAGE.is_relative_to(pathlib.Path(x)) for x in paths):
            paths.insert(0, str(STAGE))
        args = ["backup", "--tag", "nightly", "--json", "--exclude", str(hubconf.session_addresses_dir()),
                "--exclude", str(hubconf.secrets_dir() / "tmp")]
        for x in CFG.get("exclude") or []:
            args += ["--exclude", str(hubconf.path(x)) if x.startswith("~") else x]
        r = restic(*args, *paths, capture=True)
        summary = next((json.loads(l) for l in r.stdout.splitlines() if '"message_type":"summary"' in l), {})
        if retention_due(now().date(), last_retention()):   # Sundays, or the first backup after a missed one
            k = CFG.get("keep") or {}
            restic("forget", "--prune", "--keep-daily", str(k.get("daily", 14)), "--keep-weekly", str(k.get("weekly", 8)),
                   "--keep-monthly", str(k.get("monthly", 12)), capture=True)
            OUT.mkdir(parents=True, exist_ok=True)
            retention_file().write_text(now().date().isoformat() + "\n")
        restic("check", capture=True)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "last.json").write_text(json.dumps({
            "ok": True, "at": now().isoformat(), "snapshot": summary.get("snapshot_id"),
            "files": summary.get("total_files_processed"), "bytes": summary.get("total_bytes_processed"),
            "added_bytes": summary.get("data_added"), "manifest": m}, ensure_ascii=False, indent=1))
        say(_("Done: snapshot {snapshot}, {files} {files:file|files}, {mb} MB added", snapshot=str(summary.get("snapshot_id"))[:8],
              files=summary.get("total_files_processed"), mb=round((summary.get("data_added") or 0) / 1e6, 1)))
        return 0
    except Exception as e:
        return fail("run", repr(e))


def retention_file() -> pathlib.Path:
    """The day the retention rules last ran (retention_due); under OUT, read when called (tests move OUT)."""
    return OUT / "last-retention"


def last_retention() -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(retention_file().read_text().strip())
    except (OSError, ValueError):
        return None


def retention_due(today: datetime.date, last: datetime.date | None) -> bool:
    """The retention rules run on Sundays. A machine that slept through Sunday applies them at its next backup
    instead of waiting a week (docs/design/newcomer-path.md B7): due when the last run is a week old or unknown."""
    if last == today:
        return False
    return today.weekday() == 6 or last is None or (today - last).days >= 7


def cmd_verify() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="backup-verify-", dir=hubconf.STATE))
    try:
        restic("restore", "latest", "--target", str(tmp), capture=True)
        staged = next(tmp.rglob("backup-staging"), None)
        if staged is None:
            raise RuntimeError(_("The restored snapshot has no backup-staging"))
        m = json.loads((staged / "manifest.json").read_text())
        for name in m["sqlite"]:
            c = sqlite3.connect(staged / "sqlite" / f"{name}.db")
            if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError(_("integrity_check of the restored {name}.db is not ok", name=name))
        for b in m["bundles"]:
            r = subprocess.run(["git", "bundle", "verify", str(staged / "git" / f"{b}.bundle")], capture_output=True, text=True,
                               cwd=tmp)
            if r.returncode != 0:
                raise RuntimeError(_("The restored {bundle}.bundle failed verification: {err}", bundle=b,
                                     err=r.stderr.strip()[-200:]))
        need = [p for p in (hubconf.path(x) for x in CFG.get("include") or []) if p and p.exists()]
        missing = [str(p) for p in need if not (tmp / str(p).lstrip("/")).exists()]
        if missing:
            raise RuntimeError(_("The restored snapshot is missing: {paths}", paths=", ".join(missing[:5])))
        restic("check", "--read-data-subset=10%", capture=True)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "last-verify.json").write_text(json.dumps({"ok": True, "at": now().isoformat(), "sqlite": m["sqlite"],
                                                          "bundles": len(m["bundles"])}, ensure_ascii=False))
        say(_("Restore test passed: {sqlite} SQLite, {bundles} {bundles:bundle|bundles}, the required directories all "
              "present ({dirs}), 10% data sample OK", sqlite=len(m["sqlite"]), bundles=len(m["bundles"]), dirs=len(need)))
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
    print(_(__doc__))
    return 2


if __name__ == "__main__":
    sys.exit(main())
