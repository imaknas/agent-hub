"""E1 golden (docs/design/i18n.md) for bin/weekly-reflect.py and bin/reflect_llm.py, on zh-Hant: the weekly report's
`--help`, the line it prints when the runtime has no transcripts, and the texts reflect_llm.analyze() writes into the
week's json for the report page (no activity; the model call failed; the model shown when none is configured).
The prompt itself is not here (step 5).

The report page (reflect/index.html) is rendered by its own script in the browser: tests/reflect_page.js runs that script
in node with a stub DOM over the weeks in tests/fixtures/i18n-golden/reflect-weeks.json (every kind of week the page
shows) and the golden is what each week renders. Skipped where there is no node (the hub machine has it: nvm).

llm.py is never run: subprocess.run is a canned answer.

    bin/py -m unittest tests.test_i18n_golden_reflect
"""
import importlib.util
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
INPUTS = golden_lang.INPUTS
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
ZH = {"AGENT_HUB_LANG_UI": golden_lang.LANG}


def node() -> str | None:
    import hubconf
    found = shutil.which("node")
    if found:
        return found
    b = hubconf.nvm_bin()
    return str(b / "node") if b and (b / "node").exists() else None


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
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        o, e = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, ZH), mock.patch.object(m, "OUT", tmp), redirect_stderr(e), \
                mock.patch.object(m.sessions, "require", side_effect=m.sessions.NoSessions("no session records here")), \
                mock.patch.object(m, "build_stats", return_value={}), \
                mock.patch.object(m, "build_html", return_value=tmp / "index.html"), \
                mock.patch.object(sys, "argv", ["weekly-reflect.py", "--week", "2026-W40"]), redirect_stdout(o):
            m.main()
        # bin/weekly-reflect.sh takes stdout's first line as the json written
        out.append(("no transcripts: stdout", o.getvalue().replace(str(tmp), "<TMP>")))
        out.append(("no transcripts: stderr", e.getvalue()))
        self.compare("weekly-reflect.txt", "\n".join(f"=== {k}\n{v}" for k, v in out))

    @unittest.skipUnless(node(), "no node")
    def test_page(self):
        spec = importlib.util.spec_from_file_location("weekly_reflect_page", ROOT / "bin" / "weekly-reflect.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        out = []
        for label, weeks in (("weeks", json.loads((INPUTS / "reflect-weeks.json").read_text())), ("no weeks", [])):
            tmp = pathlib.Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
            for w in weeks:
                (tmp / f"{w['week']}.json").write_text(json.dumps(w, ensure_ascii=False, indent=1))
            # the page names the configured assistant (hub.json assistant.name)
            with mock.patch.dict(os.environ, ZH), mock.patch.object(m, "OUT", tmp), \
                    mock.patch.object(m.hubconf, "assistant_name", return_value="Helper"):
                page = m.build_html()
            r = subprocess.run([node(), str(ROOT / "tests" / "reflect_page.js"), str(page)], capture_output=True,
                               text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            out.append(f"##### {label}\n{r.stdout}")
        self.compare("reflect-page.txt", "".join(out))

    def test_reflect_llm(self):
        import reflect_llm as m
        import store_fixture
        store_fixture.stand_in(self)   # what llm_ready reads: a checked copy is there
        fake_llm = types.SimpleNamespace(model_for=lambda point: None)   # EXCLUDED None: no session digests asked for
        out = []
        import hubconf
        # what llm.py prints when no model can be reached (runtime none, no hub.json llm section)
        none = json.dumps({"type": "result", "is_error": True, "structured_output": None, "llm_point": "reflect-week",
                           "llm_error": "runtime none has no completion interface"})
        with mock.patch.dict(os.environ, ZH), mock.patch.dict(sys.modules, {"llm": fake_llm}), \
                mock.patch.object(m, "EXCLUDED", None), mock.patch.object(hubconf, "_cfg", {}):
            out.append(("no activity", m.analyze({"week": "2026-W40", "totals": {"prompts": 0}})))
            with mock.patch.object(m, "stats_brief", return_value="STATS"), \
                    mock.patch.object(m.subprocess, "run", return_value=subprocess.CompletedProcess([], 3, "oops", "")):
                out.append(("model call failed", m.analyze({"week": "2026-W40", "totals": {"prompts": 5}})))
            with mock.patch.object(m, "stats_brief", return_value="STATS"), \
                    mock.patch.object(hubconf, "_cfg", {"runtime": {"default": "none"}}), \
                    mock.patch.object(m.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, none, "")):
                out.append(("no model at all", m.analyze({"week": "2026-W40", "totals": {"prompts": 5}})))
        self.compare("reflect_llm.txt", "\n".join(f"=== {k}\n{json.dumps(v, ensure_ascii=False)}" for k, v in out) + "\n")


if __name__ == "__main__":
    sys.exit(unittest.main())
