"""E1 golden (docs/design/i18n.md) for bin/check-state-anchors.py (docs/design/state-anchors.md): stale anchors
printed, --apply with and without stale anchors (the line it prints, the todo.md block, the digest section), a
project that cannot be checked. In-process, on a temp repository with fixed commit dates (stable commit ids); the
roster and the memory copies are canned.

    bin/py -m unittest tests.test_i18n_golden_state_anchors
    I18N_GOLDEN_UPDATE=1 bin/py -m unittest tests.test_i18n_golden_state_anchors    # rewrite (only on purpose)
"""
import contextlib
import datetime
import importlib.util
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_lang  # noqa: E402

GOLDEN = golden_lang.GOLDEN
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
TODAY = datetime.date.today().isoformat()
ZH = {"AGENT_HUB_LANG_UI": golden_lang.LANG, "COLUMNS": "100"}
GIT = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
       "GIT_AUTHOR_DATE": "2026-11-01T00:00:00+00:00", "GIT_COMMITTER_DATE": "2026-11-01T00:00:00+00:00"}
STATE = """# STATE

## Now

- The nightly job runs in ⚓`src/job.py#run_job`; retries in ⚓`src/job.py#retry_later`.
- Settings are read by ⚓`src/old.py`, the exporter is ⚓`src/gone.py`, and the cache in ⚓`data/` is never published.
- The new parser ⚓`src/feature.py` and ⚓`src/never.py`; see ⚓`src/job.py:12`.

## Tasks

- [ ] [agent] Write ⚓`src/later.py`
"""


def load():
    with mock.patch.dict(os.environ, ZH):
        spec = importlib.util.spec_from_file_location("state_anchors_golden", ROOT / "bin" / "check-state-anchors.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    return m


def capture(fn, argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), mock.patch.dict(os.environ, ZH), mock.patch.object(sys, "argv", ["x", *argv]):
        try:
            ret = fn()
        except SystemExit as e:
            ret = f"SystemExit({e.code})"
    return f"exit {ret}\n{buf.getvalue()}"


def repo(ws: pathlib.Path) -> None:
    def git(*a):
        subprocess.run(["git", "-C", str(ws), *a], check=True, capture_output=True, env=GIT)

    def write(rel, text):
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text)
    ws.mkdir()
    git("init", "-q", "-b", "main")
    write("src/job.py", "def run_job():\n    pass\n")
    write("src/old.py", "x = 1\n")
    write("src/gone.py", "y = 2\n")
    write(".gitignore", "data/\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    git("mv", "src/old.py", "src/settings.py")
    git("rm", "-q", "src/gone.py")
    git("commit", "-q", "-m", "rename and delete")
    git("checkout", "-q", "-b", "feature")
    write("src/feature.py", "z = 3\n")
    git("add", "-A")
    git("commit", "-q", "-m", "feature")
    git("checkout", "-q", "main")
    write("data/cache.bin", "c")


class Golden(unittest.TestCase):
    maxDiff = None

    def check(self, name, text):
        f = GOLDEN / name
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())

    def test_state_anchors(self):
        m = load()
        sys.path.insert(0, str(ROOT / "tests"))
        import store_fixture
        store_fixture.stand_in(self)   # what llm_ready reads: a checked copy is there
        out = []
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"AGENT_HUB_DATA": d}):
            d_ = pathlib.Path(d)
            todo = d_ / "todo.md"
            todo.write_text("# todo\n\n- [ ] x\n")
            copies = d_ / "copies"
            for name, text in (("alpha", STATE), ("beta", "- Uses ⚓`src/a.py`\n"), ("gamma", "- No anchors\n")):
                (copies / name).mkdir(parents=True)
                (copies / name / "STATE.md").write_text(text)
            repo(d_ / "ws-alpha")
            roster = {"alpha": {"workspace": str(d_ / "ws-alpha")}, "beta": {}, "gamma": {"workspace": str(d_)},
                      "delta": {"workspace": str(d_)}}
            import hublib
            # the instance's own hub.json must not decide what the to-do block says
            with mock.patch.object(m.hubconf, "roster", return_value=roster), \
                    mock.patch.object(m.hubconf, "p", return_value=copies), \
                    mock.patch.object(m.hubconf, "_cfg", {}), \
                    mock.patch.object(m.hubconf, "owner_tag", return_value="Alex"):
                out.append(("stale", capture(m.main, [])))
                out.append(("stale --apply", capture(m.main, ["--apply"])))
                out.append(("todo.md", todo.read_text()))
                out.append(("digest", hublib.digest_path().read_text()))
                with mock.patch.object(m.hubconf, "_cfg", {"runtime": {"default": "none"}}):
                    capture(m.main, ["--apply"])
                out.append(("todo.md without a coding agent", todo.read_text()))
                (copies / "alpha" / "STATE.md").write_text("- The nightly job runs in ⚓`src/job.py#run_job`.\n")
                out.append(("none stale --apply", capture(m.main, ["--apply", "--project", "alpha"])))
                out.append(("todo.md", todo.read_text()))
                out.append(("digest", hublib.digest_path().read_text()))
            out.append(("--help", capture(m.main, ["--help"])))
            text = "\n".join(f"=== {k}\n{v}" for k, v in out).replace(d, "<DIR>").replace(TODAY, "<TODAY>")
        self.check("check-state-anchors.txt", text)


if __name__ == "__main__":
    sys.exit(unittest.main())
