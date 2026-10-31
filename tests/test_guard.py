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
import i18n  # noqa: E402


def zh(msgid, **kw):
    """guard.py's reason in zh-Hant, the language these tests run it in; the whole sentence, word for word"""
    return i18n.translate("guard", msgid, kw, "zh-Hant")


HOW = zh("To ask another project for something, send a letter: agenthub send --to <project> --type request "
         "--text \"…\"; or ask the owner to open a session in that project themselves.")


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
                         zh("A {project} session cannot open a session in {cwd} (that is not this project). {how}",
                            project="proj", cwd=str(other), how=HOW))

    def test_sibling_with_same_prefix_denied(self):
        # proj-2 starts with proj as a string, but is not under it
        self.assertIsNotNone(self.decide({"cwd": str(self.root.parent / "proj-2")}))

    def test_elsewhere_denied_with_its_own_reason(self):
        self.assertEqual(self.decide({"elsewhere": {"kind": "repository", "name": "o/r"}}),
                         zh("A {project} session cannot open a session in another repository ({name}). {how}",
                            project="proj", name="o/r", how=HOW))
        self.assertEqual(self.decide({"elsewhere": {"kind": "machine", "name": "devbox"}}),
                         zh("A {project} session cannot open a session on another machine. {how}", project="proj",
                            how=HOW))
        self.assertEqual(self.decide({"elsewhere": {"kind": "unknown_place"}}),
                         zh("When a {project} session opens a session with a preset, there is no way to tell whether "
                            "it is the same project; for the same project, give no preset or cwd. {how}",
                            project="proj", how=HOW))

    def test_not_an_onboarded_project_never_denied(self):
        self.m.own_project = lambda cwd: (None, None)
        self.assertIsNone(self.decide({"elsewhere": {"kind": "machine"}}))
        self.assertIsNone(self.decide({"cwd": "/somewhere/else"}))


if __name__ == "__main__":
    unittest.main()
