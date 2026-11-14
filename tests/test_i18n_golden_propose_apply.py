"""E1 golden (docs/design/i18n.md) for bin/propose_apply.py, on zh-Hant: the usage text, the line for a mailbox call
with no letters to handle, and the note the platform writes when it closes a letter the agent marked done without one.
The letters text is prompt (step 5), not here.

The agent never runs: run() and the bus are canned; tests/test_propose_apply.py runs the real flow.

    bin/py -m unittest tests.test_i18n_golden_propose_apply
"""
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_lang  # noqa: E402

GOLDEN = golden_lang.GOLDEN
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
ZH = {"AGENT_HUB_LANG_UI": golden_lang.LANG}


class FakeConn:
    def __init__(self, row):
        self.row = row

    def execute(self, sql, args=()):
        return types.SimpleNamespace(fetchone=lambda: self.row)


def fake_hubbus(row, published):
    return types.SimpleNamespace(
        db=lambda: FakeConn(row), payload=lambda r: {"type": "request", "text": "letter"}, new_id=lambda: "m-new",
        canon=lambda s: s, OWNER_ADDRESS="owner", Live=lambda c: types.SimpleNamespace(load=lambda: None),
        taken_by_another=lambda c, r, live: False,
        publish=lambda c, mid, typ, src, to, ref, payload: published.append((typ, payload)))


class Golden(unittest.TestCase):
    maxDiff = None

    def scenario(self) -> str:
        r = subprocess.run([sys.executable, str(ROOT / "bin" / "propose_apply.py")], capture_output=True, text=True,
                           env={**os.environ, **ZH})
        out = [("usage", f"exit {r.returncode}\nstdout: {r.stdout!r}\nstderr:\n{r.stderr}")]
        import propose_apply as m
        with mock.patch.dict(os.environ, ZH):
            e = io.StringIO()
            with mock.patch.object(m, "_hubbus", return_value=fake_hubbus(None, [])), redirect_stderr(e):
                code = m._mailbox("demo", ["m-1"])
            out.append(("no letters", f"exit {code}\nstderr: {e.getvalue()!r}"))
            ws = pathlib.Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, ws, ignore_errors=True)
            row = {"id": "m-1", "to_project": "demo", "ref": None, "source": "other"}
            published = []
            res = {"ok": True, "output": {"done": [{"id": "m-1"}]}, "applied": [], "refused": [], "error": ""}
            o = io.StringIO()
            with mock.patch.object(m, "_hubbus", return_value=fake_hubbus(row, published)), \
                    mock.patch.object(m.hubconf, "workspace", return_value=ws), \
                    mock.patch.object(m, "run", return_value=res), redirect_stdout(o):
                code = m._mailbox("demo", ["m-1"])
            out.append(("done without a note", f"exit {code}\npublished: {json.dumps(published, ensure_ascii=False)}"))
        return "\n".join(f"=== {k}\n{v}" for k, v in out) + "\n"

    def test_texts(self):
        text = self.scenario()
        f = GOLDEN / "propose_apply.txt"
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())


if __name__ == "__main__":
    sys.exit(unittest.main())
