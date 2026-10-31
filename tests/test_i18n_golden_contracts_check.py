"""E1 golden (docs/design/i18n.md) for bin/contracts-check.py, on zh-Hant: every problem line of the copy check
(no provides, version mismatch, original or copy missing, copy differs), of the live data check (a bus event that does
not match, `handoffs --json` that is not JSON), of the llm.py lint, and main()'s output and job.failed detail.

Paths are a temp dir (<TMP>) and the hub (<HUB>); llm.py and hubbus are never run (subprocess.run is canned).

    bin/py -m unittest tests.test_i18n_golden_contracts_check
"""
import importlib.util
import io
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
GOLDEN = ROOT / "tests" / "fixtures" / "i18n-golden"
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
ZH = {"AGENT_HUB_LANG_UI": "zh-Hant"}


class Golden(unittest.TestCase):
    maxDiff = None

    def load(self):
        spec = importlib.util.spec_from_file_location("contracts_check_golden", ROOT / "bin" / "contracts-check.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def copies(self, m, tmp: pathlib.Path) -> list[str]:
        (tmp / "c.json").write_text('{"type": "object"}\n')
        producer = {"name": "helper", "paths": {"workspace": str(tmp)},
                    "provides": [{"contract": "helper-x", "v": 1, "schema": "c.json"},
                                 {"contract": "helper-gone", "v": 1, "schema": "gone.json"}]}

        def module(contract, v=1, copy="contracts/hubbus.v1.schema.json"):
            return {"name": "assistant", "type": "module", "consumes": [{"contract": contract, "v": v, "copy": copy,
                                                                        "from": "helper"}]}
        out = []
        for mod in (module("helper-x"), module("helper-x", v=2), module("helper-gone"),
                    module("helper-x", copy="contracts/no-such-copy.json"), module("helper-none")):
            out += m.cross_copies({"projects": {"helper": producer}, "modules": {"assistant": mod}})
        out += m.cross_copies({"projects": {}, "modules": {"assistant": module("helper-x")}})
        return out

    def live(self, m, tmp: pathlib.Path) -> list[str]:
        db = tmp / "bus.db"
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE events (seq INTEGER, id TEXT, ts TEXT, type TEXT, source TEXT, payload TEXT)")
        c.execute("INSERT INTO events VALUES (1, 'ev-1', 'not a time', 'x', 's', '{}')")
        c.commit()
        c.close()
        cli = tmp / "a" / "b" / "c" / "cli"
        cli.parent.mkdir(parents=True)
        cli.write_text("#!/bin/sh\necho not json\nexit 3\n")
        cli.chmod(0o755)
        (tmp / "registry").mkdir()
        with mock.patch.object(m.hubconf, "data_file", side_effect=lambda name: tmp / "data" / name), \
                mock.patch.object(m.hubconf, "registry_dir", return_value=tmp / "registry"), \
                mock.patch.object(m.hubconf, "bus_db", return_value=db), \
                mock.patch.object(m.hubconf, "p", side_effect=lambda key, *a: cli if key == "assistant.cli" else None):
            return m.live({"projects": {}})

    def lint(self, m) -> list[str]:
        out = []
        for answer in (subprocess.CompletedProcess([], 1, "bin/x.py:3 calls the CLI directly\n", ""),
                       subprocess.CompletedProcess([], 2, "", "Traceback ...\nboom")):
            with mock.patch.object(m.subprocess, "run", return_value=answer):
                out += m.llm_lint()
        return out

    def main_out(self, m, errs: list[str]) -> str:
        sent = []
        o = io.StringIO()

        def run(cmd, **kw):
            sent.append(json.loads(cmd[cmd.index("--payload") + 1]))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with mock.patch.object(m.registry, "refresh", return_value={}), \
                mock.patch.object(m, "cross_copies", return_value=errs[:1]), \
                mock.patch.object(m, "live", return_value=errs[1:]), \
                mock.patch.object(m, "llm_lint", return_value=[]), \
                mock.patch.object(m.subprocess, "run", side_effect=run), \
                mock.patch.object(sys, "argv", ["contracts-check.py"]), redirect_stdout(o):
            code = m.main()
        return f"exit {code}\nstdout: {o.getvalue()!r}\npayload: {sent!r}"

    def scenario(self) -> str:
        m = self.load()
        tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        out = []
        with mock.patch.dict(os.environ, ZH):
            out.append(("cross_copies", "\n".join(self.copies(m, tmp))))
            out.append(("live", "\n".join(self.live(m, tmp))))
            out.append(("llm_lint", "\n".join(self.lint(m))))
            out.append(("main, problems", self.main_out(m, ["a: one", "b: two", "c: three", "d: four"])))
            out.append(("main, none", self.main_out(m, [])))
        text = "\n".join(f"=== {k}\n{v}" for k, v in out) + "\n"
        return text.replace(str(tmp), "<TMP>").replace(str(ROOT), "<HUB>")

    def test_texts(self):
        text = self.scenario()
        f = GOLDEN / "contracts-check.txt"
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())


if __name__ == "__main__":
    sys.exit(unittest.main())
