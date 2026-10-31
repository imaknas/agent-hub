"""runtime_claude.py recognises texts other processes print, whatever this process's UI language: claude not being
found (llm.py's message, in the language llm.py ran in), and a usage limit in an error result of claude's stream. The
texts are in tests/fixtures/runtime-claude-texts.json; every case runs under each UI language.

    bin/py -m unittest tests.test_runtime_claude_texts
"""
import json
import os
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import runtime_claude as rc  # noqa: E402

FIX = json.loads((ROOT / "tests" / "fixtures" / "runtime-claude-texts.json").read_text())


class Texts(unittest.TestCase):
    def test_login_error(self):
        for lang in ("en", "zh-Hant"):
            for c in FIX["login_error"]:
                with self.subTest(lang=lang, text=c["text"]), mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": lang}):
                    self.assertEqual(rc.is_login_error(c["text"]), c["expect"])

    def test_rate_limit(self):
        for lang in ("en", "zh-Hant"):
            for c in FIX["rate_limit"]:
                line = json.dumps({"type": "result", "is_error": True, "result": c["text"]})
                with self.subTest(lang=lang, text=c["text"]), mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": lang}):
                    errors = [e.data.get("error") for e in rc.parse_stream(line) if e.type == "error"]
                    self.assertEqual("rate_limit" in errors, c["expect"])


if __name__ == "__main__":
    unittest.main()
