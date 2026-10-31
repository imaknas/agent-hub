"""E1 golden (docs/design/i18n.md) for bin/weekly-reflect.py and bin/reflect_llm.py, on zh-Hant: the weekly report's
`--help`, the line it prints when the runtime has no transcripts, and the texts reflect_llm.analyze() writes into the
week's json for the report page (no activity; the model call failed; the model shown when none is configured).
The prompt itself is not here (step 5).

llm.py is never run: subprocess.run is a canned answer.

    bin/py -m unittest tests.test_i18n_golden_reflect
"""
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_lang  # noqa: E402

GOLDEN = golden_lang.GOLDEN
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
ZH = {"AGENT_HUB_LANG_UI": golden_lang.LANG}


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

    def test_weekly_reflect(self):
        r = subprocess.run([sys.executable, str(ROOT / "bin" / "weekly-reflect.py"), "--help"], capture_output=True,
                           text=True, env={**os.environ, **ZH, "COLUMNS": "120"})
        out = [("--help", f"exit {r.returncode}\n{r.stdout}{r.stderr}")]
        spec = importlib.util.spec_from_file_location("weekly_reflect_golden", ROOT / "bin" / "weekly-reflect.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        tmp = pathlib.Path(tempfile.mkdtemp())
        o = io.StringIO()
        with mock.patch.dict(os.environ, ZH), mock.patch.object(m, "OUT", tmp), \
                mock.patch.object(m.runtime_ports, "load", side_effect=m.runtime_ports.Unsupported("no transcripts here")), \
                mock.patch.object(m, "build_stats", return_value={}), \
                mock.patch.object(m, "build_html", return_value=tmp / "index.html"), \
                mock.patch.object(sys, "argv", ["weekly-reflect.py", "--week", "2026-W40"]), redirect_stdout(o):
            m.main()
        out.append(("no transcripts", o.getvalue().replace(str(tmp), "<TMP>")))
        self.compare("weekly-reflect.txt", "\n".join(f"=== {k}\n{v}" for k, v in out))

    def test_reflect_llm(self):
        import reflect_llm as m
        fake_llm = types.SimpleNamespace(model_for=lambda point: None)   # EXCLUDED None: no session digests asked for
        out = []
        with mock.patch.dict(os.environ, ZH), mock.patch.dict(sys.modules, {"llm": fake_llm}), \
                mock.patch.object(m, "EXCLUDED", None):
            out.append(("no activity", m.analyze({"week": "2026-W40", "totals": {"prompts": 0}})))
            with mock.patch.object(m, "stats_brief", return_value="STATS"), \
                    mock.patch.object(m.subprocess, "run", return_value=subprocess.CompletedProcess([], 3, "oops", "")):
                out.append(("model call failed", m.analyze({"week": "2026-W40", "totals": {"prompts": 5}})))
        self.compare("reflect_llm.txt", "\n".join(f"=== {k}\n{json.dumps(v, ensure_ascii=False)}" for k, v in out) + "\n")


if __name__ == "__main__":
    sys.exit(unittest.main())
