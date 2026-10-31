"""consolidate-memory.py's mechanical checks on note text: which lines carry a hedge (has_hedge), what is compared once
the hedges and punctuation are gone (strip_hedges), and which links and code names of a folded note must survive
(salient_missing). The sentences and expected results are in tests/fixtures/consolidate-hedges.json (notes in either
language, full- and half-width punctuation), captured from the code before the hedge lists moved to locales/.

    bin/py -m unittest tests.test_consolidate_hedges
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIX = json.loads((ROOT / "tests" / "fixtures" / "consolidate-hedges.json").read_text())
GIT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
       "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


def load():
    spec = importlib.util.spec_from_file_location("consolidate_hedges", ROOT / "bin" / "consolidate-memory.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Hedges(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load()

    def test_hedged_and_stripped(self):
        for c in FIX["cases"]:
            with self.subTest(text=c["text"]):
                self.assertEqual(self.m.has_hedge(c["text"]), c["hedged"])
                self.assertEqual(self.m.strip_hedges(c["text"]), c["stripped"])

    def test_salient_missing(self):
        """links end at whitespace, a closing bracket of either width; code names in backquotes"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            env = {**os.environ, **GIT}
            (repo / "memory").mkdir()
            (repo / "memory" / "2026-09-01.md").write_text(FIX["salient"]["text"])
            (repo / "STATE.md").write_text("# STATE\n")
            for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
                subprocess.run(["git", *cmd], cwd=repo, env=env, check=True, capture_output=True)
            (repo / "memory" / "2026-09-01.md").unlink()
            got = self.m.salient_missing(repo, "HEAD", ["memory/2026-09-01.md"])
        self.assertEqual(got, {"memory/2026-09-01.md": FIX["salient"]["missing"]})


if __name__ == "__main__":
    unittest.main()
