"""E1 golden (docs/design/i18n.md) for three small helpers, on zh-Hant: bin/memory-nudge.py's reminder (the text a hook
puts into the agent's session; the date and the first commit's time are <TODAY>/<SINCE>), bin/hublib.py's usage line,
and bin/edits.py's problems (each way an edit list is refused, each marker problem).

    bin/py -m unittest tests.test_i18n_golden_helpers
"""
import datetime
import importlib.util
import io
import os
import pathlib
import re
import sys
import unittest
from contextlib import redirect_stderr
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_lang  # noqa: E402

GOLDEN = golden_lang.GOLDEN
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
ZH = {"AGENT_HUB_LANG_UI": golden_lang.LANG}

KEY = re.compile(r"\(wait: [^)\n]+\)")
DOC = "# STATE\n\nNow: A.\n\n## To-do\n- [ ] renew the domain (wait: task-p-1)\n- [ ] write tests\n"


class Golden(unittest.TestCase):
    maxDiff = None

    def compare(self, name: str, text: str):
        f = GOLDEN / name
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())

    def test_memory_nudge(self):
        spec = importlib.util.spec_from_file_location("memory_nudge_golden", ROOT / "bin" / "memory-nudge.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        first = int(datetime.datetime(2026, 10, 30, 9, 5).timestamp())
        with mock.patch.dict(os.environ, ZH):
            text = m.message([first + 600, first, first + 1200])
        text = text.replace(datetime.date.today().isoformat(), "<TODAY>").replace(
            datetime.datetime.fromtimestamp(first).strftime("%m-%d %H:%M"), "<SINCE>")
        self.compare("memory-nudge.txt", text + "\n")

    def test_hublib(self):
        import hublib
        e = io.StringIO()
        with mock.patch.dict(os.environ, ZH), redirect_stderr(e):
            code = hublib.main([])
        self.compare("hublib.txt", f"exit {code}\n{e.getvalue()}")

    def test_edits(self):
        import edits
        out = []
        with mock.patch.dict(os.environ, ZH):
            for name, lst in [("not a dict", ["x"]), ("no new", [{"old": "Now"}]), ("empty old", [{"old": "", "new": "x"}]),
                              ("old missing", [{"old": "Now: A.", "new": "Now: B."}, {"old": "nowhere", "new": "x"}]),
                              ("old twice", [{"old": "- [ ] ", "new": "x"}])]:
                try:
                    edits.apply(DOC, lst)
                    out.append((name, "applied"))
                except edits.EditError as e:
                    out.append((name, str(e)))
            added = DOC.replace("write tests", "write tests (wait: task-p-9)")
            out.append(("marker added", "\n".join(edits.check_markers(DOC, added, KEY))))
            gone = DOC.replace("- [ ] renew the domain (wait: task-p-1)\n", "")
            out.append(("marker gone, not removable", "\n".join(edits.check_markers(DOC, gone, KEY, removable=False))))
            stripped = DOC.replace(" (wait: task-p-1)", "")
            out.append(("marker stripped, line kept", "\n".join(edits.check_markers(DOC, stripped, KEY))))
        self.compare("edits.txt", "\n".join(f"=== {k}\n{v}" for k, v in out) + "\n")


if __name__ == "__main__":
    sys.exit(unittest.main())
