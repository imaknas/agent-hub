"""E1 golden (docs/design/i18n.md) for bin/backup.py, on zh-Hant: the progress lines, every failure text (printed, sent
to the bus as job.failed, written to .state/backup/last-<stage>-failure.json) and the usage text.

In-process, in a temp dir: restic, hubbus and the git calls that should fail are canned answers (restic is never run);
SQLite files and git repos are real. The password file is generated into the temp dir and never printed.

    bin/py -m unittest tests.test_i18n_golden_backup
"""
import contextlib
import datetime
import gc
import importlib.util
import io
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import warnings
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_lang  # noqa: E402

GOLDEN = golden_lang.GOLDEN
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
ZH = {"AGENT_HUB_LANG_UI": golden_lang.LANG}
REAL_RUN = subprocess.run
SUMMARY = {"message_type": "summary", "snapshot_id": "abcdef0123456789", "total_files_processed": 12,
           "total_bytes_processed": 3456, "data_added": 1500000}


def load():
    spec = importlib.util.spec_from_file_location("backup_golden", ROOT / "bin" / "backup.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def make_db(path: pathlib.Path) -> None:
    c = sqlite3.connect(path)
    c.execute("create table t(x)")
    c.commit()
    c.close()


def git(*args, cwd=None):
    REAL_RUN(["git", *args], cwd=cwd, check=True, capture_output=True,
             env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                  "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})


class BadDB:
    """sqlite3 stand-in whose integrity_check is not ok"""
    def connect(self, *a, **kw):
        return self

    def backup(self, other):
        pass

    def execute(self, sql):
        return self

    def fetchone(self):
        return ("*** corrupt",)

    def close(self):
        pass


class Golden(unittest.TestCase):
    maxDiff = None

    def scenario(self, tmp: pathlib.Path) -> str:
        m = load()
        out = []
        state = tmp / "state"
        inc = tmp / "inc"
        inc.mkdir()
        (inc / "f").write_text("x")
        make_db(tmp / "bus.db")
        make_db(tmp / "app.db")
        ws = tmp / "ws"
        ws.mkdir()
        git("init", "-q", "-b", "main", cwd=ws)
        (ws / "a.txt").write_text("1\n")
        git("add", "a.txt", cwd=ws)
        git("commit", "-q", "-m", "c1", cwd=ws)
        (ws / "a.txt").write_text("2\n")
        (ws / "backend").mkdir()
        (ws / "backend" / ".env").write_text("K=dummy\n")
        origin = tmp / "origin"
        origin.mkdir()
        git("clone", "-q", "--bare", str(ws), str(origin / "demo-memory.git"))
        (tmp / "b2-env").write_text("B2_ACCOUNT_ID=dummy\n")
        cfg = {"repo": "b2:bucket:hub", "sqlite": [{"name": "app", "path": str(tmp / "app.db")}],
               "bare_repos": str(origin), "include": [str(inc)], "exclude": ["*.tmp"]}
        bus, restic_fail, bundle_fail, verify_fail = [], set(), [False], [False]
        restore = {"write": None}

        def run(cmd, **kw):
            if cmd[0] == m.RESTIC:
                sub = cmd[1]
                if sub in restic_fail:
                    return subprocess.CompletedProcess(cmd, 1, "", "Fatal: canned restic error\n")
                if sub == "cat":
                    return subprocess.CompletedProcess(cmd, 1, "", "")
                if sub == "backup":
                    return subprocess.CompletedProcess(cmd, 0, json.dumps(SUMMARY, separators=(",", ":")) + "\n", "")
                if sub == "restore" and restore["write"]:
                    restore["write"](pathlib.Path(cmd[cmd.index("--target") + 1]))
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if str(cmd[1]).endswith("hubbus"):
                bus.append(json.loads(cmd[cmd.index("--payload") + 1]))
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd[:2] == ["git", "-C"] and cmd[3] == "bundle" and bundle_fail[0] and cmd[2].endswith(".git"):
                return subprocess.CompletedProcess(cmd, 128, "", "fatal: canned bundle error\n")
            if cmd[:3] == ["git", "bundle", "verify"] and verify_fail[0]:
                return subprocess.CompletedProcess(cmd, 1, "", "error: canned verify error\n")
            return REAL_RUN(cmd, **kw)

        fixed = datetime.datetime(2026, 11, 1, 3, 4, 5).astimezone()

        def call(name, fn, *a):
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    rc = fn(*a)
                res = f"exit {rc}"
            except Exception as e:
                res = f"raised {e!r}"
            out.append((name, f"{res}\n{buf.getvalue()}"))

        def failure(stage):
            f = m.OUT / f"last-{stage}-failure.json"
            text = json.loads(f.read_text())["error"] if f.exists() else "(none)"
            f.unlink(missing_ok=True)
            return text

        def staging(target: pathlib.Path, manifest: dict | None = None, include=True):
            s = target / "x" / "backup-staging"
            (s / "git").mkdir(parents=True)
            (s / "sqlite").mkdir()
            make_db(s / "sqlite" / "bus.db")
            git("bundle", "create", str(s / "git" / "demo-memory.git.bundle"), "--all", cwd=origin / "demo-memory.git")
            (s / "manifest.json").write_text(json.dumps(manifest or {"sqlite": ["bus"], "bundles": ["demo-memory.git"]}))
            if include:
                p = target / str(inc).lstrip("/")
                p.mkdir(parents=True)

        with mock.patch.dict(os.environ, ZH), \
                mock.patch.object(m, "CFG", cfg), mock.patch.object(m, "STAGE", state / "backup-staging"), \
                mock.patch.object(m, "OUT", state / "backup"), mock.patch.object(m, "PASSWORD", tmp / "secrets" / "pw"), \
                mock.patch.object(m, "B2ENV", tmp / "b2-env"), mock.patch.object(m.hubconf, "STATE", state), \
                mock.patch.object(m.hubconf, "bus_db", lambda: tmp / "bus.db"), \
                mock.patch.object(m.hubconf, "projects", lambda: {"demo": {"name": "demo"}}), \
                mock.patch.object(m.hubconf, "workspace", lambda n: ws), \
                mock.patch.object(m, "now", lambda: fixed), mock.patch.object(m.subprocess, "run", side_effect=run):
            state.mkdir()
            git("init", "-q", cwd=state)   # the real .state sits inside the hub's git repo: `git bundle verify` needs one
            call("run ok (new password, new repo)", m.cmd_run)
            out.append(("manifest", (state / "backup-staging" / "manifest.json").read_text()))
            restic_fail.add("backup")
            call("run: restic backup fails", m.cmd_run)
            restic_fail.clear()
            out.append(("run failure file", failure("run")))
            bundle_fail[0] = True
            call("run: git bundle fails", m.cmd_run)
            bundle_fail[0] = False
            out.append(("run failure file", failure("run")))
            with mock.patch.object(m, "sqlite3", BadDB()):
                call("run: sqlite integrity", m.cmd_run)
            out.append(("run failure file", failure("run")))

            call("verify: nothing restored", m.cmd_verify)
            restore["write"] = staging
            with mock.patch.object(m, "sqlite3", BadDB()):
                call("verify: sqlite integrity", m.cmd_verify)
            verify_fail[0] = True
            call("verify: bundle", m.cmd_verify)
            verify_fail[0] = False
            restore["write"] = lambda t: staging(t, include=False)
            call("verify: missing include", m.cmd_verify)
            restore["write"] = staging
            call("verify ok", m.cmd_verify)
            out.append(("verify failure file", failure("verify")))
            out.append(("bus job.failed", "\n".join(json.dumps(b, ensure_ascii=False) for b in bus)))
            restic_fail.add("snapshots")
            with mock.patch.object(m.sys, "argv", ["backup.py", "snapshots"]):
                call("snapshots fails", m.main)
            with mock.patch.object(m.sys, "argv", ["backup.py"]):
                call("usage", m.main)
        text = "\n".join(f"=== {k}\n{v}" for k, v in out) + "\n"
        text = text.replace(str(tmp), "<TMP>")
        return re.sub(r'"at": "[^"]+"', '"at": "<TS>"', text)

    def test_texts(self):
        # cmd_verify leaves its sqlite connections unclosed: collect them here, or the ResourceWarning lands in the
        # captured output of whichever test runs when the garbage collector does
        with warnings.catch_warnings(), tempfile.TemporaryDirectory() as d:
            warnings.simplefilter("ignore", ResourceWarning)
            text = self.scenario(pathlib.Path(d).resolve())
            gc.collect()
        f = GOLDEN / "backup.txt"
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())


if __name__ == "__main__":
    sys.exit(unittest.main())
