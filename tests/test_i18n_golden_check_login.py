"""E1 golden (docs/design/i18n.md) for bin/check-claude-login.py, on zh-Hant: the failure texts of a probe request
(ask()), which tests/test_check_login.py replaces. Every other text of the script is compared word for word there.

llm.py is never run: subprocess.run is a canned answer.

    bin/py -m unittest tests.test_i18n_golden_check_login
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import unittest
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

    def scenario(self) -> str:
        spec = importlib.util.spec_from_file_location("check_login_golden", ROOT / "bin" / "check-claude-login.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        cases = [
            ("timeout", subprocess.TimeoutExpired(["llm.py"], 180)),
            ("no json", subprocess.CompletedProcess([], 3, "not json\n", "Traceback ...\nboom")),
            ("empty output", subprocess.CompletedProcess([], 1, "", "")),
            ("llm_error", subprocess.CompletedProcess([], 0, json.dumps({"llm_error": "Login expired"}), "")),
            ("is_error", subprocess.CompletedProcess([], 0, json.dumps({"is_error": True, "result": "401"}), "")),
            ("ok", subprocess.CompletedProcess([], 0, json.dumps({"result": "ok"}), "")),
        ]
        out = []
        with mock.patch.dict(os.environ, ZH):
            for name, answer in cases:
                kw = {"side_effect": answer} if isinstance(answer, Exception) else {"return_value": answer}
                with mock.patch.object(m.subprocess, "run", **kw):
                    out.append((name, repr(m.ask({}))))
        return "\n".join(f"=== {k}\n{v}" for k, v in out) + "\n"

    def test_texts(self):
        text = self.scenario()
        f = GOLDEN / "check-claude-login.txt"
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())


if __name__ == "__main__":
    sys.exit(unittest.main())
