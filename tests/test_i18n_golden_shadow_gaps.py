"""E1 goldens (docs/design/i18n.md) on zh-Hant for bin/librarian-shadow-report.py (the report, and no records) and
bin/check-memory-gaps.py (not supported, gaps printed, --apply with and without gaps: the line it prints, the todo.md
block, the digest section). In-process, on temp dirs; the transcripts and the roster are canned.

    bin/py -m unittest tests.test_i18n_golden_shadow_gaps
    I18N_GOLDEN_UPDATE=1 bin/py -m unittest tests.test_i18n_golden_shadow_gaps    # rewrite (only on purpose)
"""
import contextlib
import datetime
import importlib.util
import io
import json
import os
import pathlib
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


def load(name, file):
    with mock.patch.dict(os.environ, ZH):
        spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / file)
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

    def test_shadow_report(self):
        m = load("shadow_report_golden", "librarian-shadow-report.py")
        out = []
        with tempfile.TemporaryDirectory() as d:
            m.DIR = pathlib.Path(d)
            out.append(("no records", capture(m.main, ["--days", "3"])))
            recs = [
                {"point": "librarian-state-edits", "project": "p0", "ok": True, "same_decision": True, "similarity": 0.9,
                 "identical_to_agent": True, "cost_usd": 0.01},
                {"point": "librarian-state-edits", "project": "p1", "ok": True, "same_decision": False, "similarity": 0.5,
                 "shadow_changed": True, "agent_changed": False, "reason": "the shadow wanted a change",
                 "marker_problems": ["k1"], "cost_usd": 0.02},
                {"point": "librarian-state-edits", "project": "p2", "ok": False, "edit_error": "old not found", "cost_usd": 0.01},
                {"point": "librarian-state-edits", "project": "p3", "ok": False, "error": "exit 1"},
                {"point": "librarian-index-edits", "ok": True, "same_decision": True, "roster_untouched": True,
                 "similarity": 0.7, "cost_usd": 0.03},
            ]
            (m.DIR / f"{TODAY}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
            out.append(("report", capture(m.main, [])))
            out.append(("--help", capture(m.main, ["--help"])))
            text = "\n".join(f"=== {k}\n{v}" for k, v in out).replace(d, "<DIR>").replace(TODAY, "<TODAY>")
        self.check("librarian-shadow-report.txt", text)

    def test_memory_gaps(self):
        m = load("memory_gaps_golden", "check-memory-gaps.py")
        out = []
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"AGENT_HUB_DATA": d}):
            todo = pathlib.Path(d) / "todo.md"
            todo.write_text("# todo\n\n- [ ] x\n")
            import hublib

            def unsupported(**kw):
                raise m.runtime_ports.Unsupported("the runtime keeps no transcripts")
            with mock.patch.object(m.runtime_ports, "load", side_effect=unsupported):
                out.append(("unsupported", capture(m.main, [])))
                out.append(("unsupported --apply", capture(m.main, ["--apply"])))
                out.append(("digest", hublib.digest_path().read_text()))
            msgs = {"a": 12, "b": 40, "c": 3, "d": 15}
            notes = {"ws-a": 0, "ws-b": 0, "ws-d": 2}
            with mock.patch.object(m.runtime_ports, "load"), \
                    mock.patch.object(m.hubconf, "roster", return_value={k: {"workspace": f"ws-{k}"} for k in msgs}), \
                    mock.patch.object(m.hublib, "project_paths", return_value={k: [k] for k in (*msgs, "unregistered")}), \
                    mock.patch.object(m, "human_messages_since", side_effect=lambda paths, since: msgs[paths[0]]), \
                    mock.patch.object(m, "note_commits_since", side_effect=lambda ws, since: notes[ws]):
                out.append(("gaps", capture(m.main, [])))
                out.append(("gaps --apply", capture(m.main, ["--apply", "--window-hours", "48"])))
                out.append(("todo.md", todo.read_text()))
                out.append(("digest", hublib.digest_path().read_text()))
                notes.update({"ws-a": 1, "ws-b": 1})
                out.append(("no gaps --apply", capture(m.main, ["--apply"])))
                out.append(("todo.md", todo.read_text()))
            out.append(("--help", capture(m.main, ["--help"])))
            text = "\n".join(f"=== {k}\n{v}" for k, v in out).replace(d, "<DIR>").replace(TODAY, "<TODAY>")
        self.check("check-memory-gaps.txt", text)


if __name__ == "__main__":
    sys.exit(unittest.main())
