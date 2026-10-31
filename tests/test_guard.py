"""bin/guard.py's decide: a project's session cannot open a session in another project. The wording is for the agent
(it tells it to send a letter instead), and drifted once during the layer 3 abstraction when the neutral format lost
information, so this compares down to the words.

    bin/py -m unittest discover -s tests
"""
import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

HOW = "跨專案的事請寄信:agenthub send --to <專案> --type request --text \"…\";或請 Knas 自己到那個專案開 session。"


def load_guard():
    spec = importlib.util.spec_from_file_location("guard_t", ROOT / "bin" / "guard.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Decide(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "zh-Hant"})   # the reasons compared here are zh-Hant
        p.start()
        self.addCleanup(p.stop)
        self.root = pathlib.Path(tempfile.mkdtemp()).resolve() / "proj"
        (self.root / ".claude" / "worktrees" / "wt1").mkdir(parents=True)
        self.m = load_guard()
        self.m.own_project = lambda cwd: ("proj", self.root)

    def decide(self, target):
        return self.m.decide(target, str(self.root))

    def test_same_project_allowed(self):
        self.assertIsNone(self.decide({}))
        self.assertIsNone(self.decide({"cwd": None, "elsewhere": None}))
        self.assertIsNone(self.decide({"cwd": str(self.root)}))
        self.assertIsNone(self.decide({"cwd": str(self.root / ".claude" / "worktrees" / "wt1")}))

    def test_other_folder_denied(self):
        other = self.root.parent / "other"
        self.assertEqual(self.decide({"cwd": str(other)}),
                         f"proj 的 session 不能在 {other} 開 session(那不是這個專案)。{HOW}")

    def test_sibling_with_same_prefix_denied(self):
        # proj-2 starts with proj as a string, but is not under it
        self.assertIsNotNone(self.decide({"cwd": str(self.root.parent / "proj-2")}))

    def test_elsewhere_denied_with_its_own_reason(self):
        self.assertEqual(self.decide({"elsewhere": {"kind": "repository", "name": "o/r"}}),
                         f"proj 的 session 不能在別的 repository(o/r)開 session。{HOW}")
        self.assertEqual(self.decide({"elsewhere": {"kind": "machine", "name": "devbox"}}),
                         f"proj 的 session 不能到別台機器開 session。{HOW}")
        self.assertEqual(self.decide({"elsewhere": {"kind": "unknown_place"}}),
                         f"proj 的 session 用 preset 開 session 時看不出是不是同一個專案;同一個專案就不要給 preset/cwd。{HOW}")

    def test_not_an_onboarded_project_never_denied(self):
        self.m.own_project = lambda cwd: (None, None)
        self.assertIsNone(self.decide({"elsewhere": {"kind": "machine"}}))
        self.assertIsNone(self.decide({"cwd": "/somewhere/else"}))


if __name__ == "__main__":
    unittest.main()
