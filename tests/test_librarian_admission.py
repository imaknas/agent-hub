"""The librarian's anchor admission inside process() (bin/librarian.py; docs/design/state-anchors.md, "Admission"):
after the model's call, an anchor it added that does not resolve on the project's main loses its sign. Pinned here:

  hook-mixed  the project's hook already committed the model's edit (good and bad anchors) → admission is committed on
              top, no second commit, the checkpoint advances to it, the review-log entry names it
  hook-only   the hook committed an edit whose only change was a bad anchor → admission's commit gives the file back;
              the run still counts as done (checkpoint advances, no misreport warning, no review-log entry)
  only        the same edit with no hook → nothing committed, nothing pushed, the run counts as done
  mixed       no hook, good and bad anchors → one "librarian: update STATE.md" commit with the bad one unanchored
In every case the eval outcome records what the model wrote, before admission.

process() runs in-process on a real memory copy (bare repo + clone) and a real project repo; the model call, the
shadow comparison, the to-do repair and the bus are stand-ins, the push a recording script.

    bin/py -m unittest tests.test_librarian_admission
"""
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
BEFORE = "# STATE\n\n- The job runs in ⚓`src/job.py`.\n- The parser is in `pkg/parse.py`.\n"
GOOD_AND_BAD = BEFORE + "- Retries go through ⚓`src/job.py#run_job`.\n- Settings live in ⚓`core/settings.py`.\n"
ONLY_BAD = BEFORE.replace("`pkg/parse.py`", "⚓`pkg/parse.py`")
HOOK_SUBJECT = "agent-hub: sync memory/STATE.md"


def load():
    spec = importlib.util.spec_from_file_location("librarian_admission_t", ROOT / "bin" / "librarian.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Admission(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        p = mock.patch.dict(os.environ, GIT_ENV)
        p.start()
        self.addCleanup(p.stop)
        self.lib = load()
        # the project's code: src/job.py with run_job on main; core/settings.py and pkg/parse.py exist nowhere
        self.ws = self.tmp / "ws"
        self.git(self.tmp, "init", "-q", "-b", "main", str(self.ws))
        (self.ws / "src").mkdir()
        (self.ws / "src" / "job.py").write_text("def run_job():\n    return 1\n")
        self.git(self.ws, "add", "-A")
        self.git(self.ws, "commit", "-qm", "init")
        # the memory copy: the checkpoint at init, one new note pushed (the librarian's pull brings it in)
        bare, self.d = self.tmp / "demo-memory.git", self.tmp / "copies" / "demo"
        self.git(self.tmp, "init", "-q", "--bare", "-b", "agent-memory", str(bare))
        self.git(self.tmp, "clone", "-q", str(bare), str(self.d))
        (self.d / "memory").mkdir()
        (self.d / "AGENT.md").write_text("agent\n")
        (self.d / "memory" / "2026-10-20.md").write_text("- old note\n")
        (self.d / "STATE.md").write_text(BEFORE)
        self.git(self.d, "add", "-A")
        self.git(self.d, "commit", "-qm", "init")
        self.git(self.d, "push", "-q", "origin", "HEAD")
        self.state = self.tmp / "state"
        self.state.mkdir()
        (self.state / "demo.sha").write_text(self.git(self.d, "rev-parse", "HEAD").strip() + "\n")
        (self.d / "memory" / "2026-10-27.md").write_text("- moved the settings\n")
        self.git(self.d, "add", "-A")
        self.git(self.d, "commit", "-qm", "new note")
        self.git(self.d, "push", "-q", "origin", "HEAD")
        self.git(self.d, "reset", "-q", "--hard", "HEAD~1")
        # where process() writes, and the push stand-in
        self.pushes = self.tmp / "pushes"
        push = self.tmp / "safe-push.sh"
        push.write_text(f"#!/bin/sh\necho \"$PWD\" >> '{self.pushes}'\n")
        push.chmod(0o755)
        self.case = self.tmp / "case"
        self.case.mkdir()
        self.outcomes = []
        for name, value in {"STATE_DIR": self.state, "LOG_DIR": self.tmp, "LOG": self.tmp / "librarian.log",
                            "REVIEW_LOG": self.tmp / "review-log.md", "SAFE_PUSH": push}.items():
            setattr(self.lib, name, value)
        real_outcome = self.lib.record_outcome
        def outcome(case, o, files):
            self.outcomes.append(o)
            real_outcome(case, o, files)
        for name, value in {"load_records": lambda proj: [], "shadow_state": lambda *a, **k: None,
                            "repair_state_todo": lambda proj, d: None,
                            "record_state_case": lambda *a, **k: self.case, "record_outcome": outcome}.items():
            p = mock.patch.object(self.lib, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(self.lib.hubconf, "roster", return_value={"demo": {"workspace": str(self.ws)}})
        p.start()
        self.addCleanup(p.stop)

    def git(self, cwd, *args) -> str:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def model(self, text: str, hook: bool):
        """the stand-in for the librarian-state call: writes STATE.md (and, as the project's SessionEnd hook would,
        commits it while the call is still running)"""
        def llm(point, prompt, cwd, project=None):
            self.assertEqual(point, "librarian-state")
            (pathlib.Path(cwd) / "STATE.md").write_text(text)
            if hook:
                self.git(cwd, "add", "STATE.md")
                self.git(cwd, "commit", "-qm", HOOK_SUBJECT)
            obj = {"type": "result", "is_error": False, "structured_output": {
                "state_updated": True, "summary": "summary demo", "reason": "reason demo", "worth_digesting": True}}
            return 0, obj, json.dumps(obj)
        return llm

    def process(self, text: str, hook: bool) -> list[str]:
        digest: list[str] = []
        with mock.patch.object(self.lib, "llm", self.model(text, hook)):
            self.lib.process("demo", self.d, [], digest)
        return digest

    def subjects(self) -> list[str]:
        return self.git(self.d, "log", "--format=%s").splitlines()

    def head(self) -> str:
        return self.git(self.d, "rev-parse", "HEAD").strip()

    def log(self) -> str:
        return (self.tmp / "librarian.log").read_text()

    def pushed(self) -> int:
        return len(self.pushes.read_text().splitlines()) if self.pushes.exists() else 0

    def review_log(self) -> str:
        f = self.tmp / "review-log.md"
        return f.read_text() if f.exists() else ""

    def assert_done(self):
        """the run counts as done: checkpoint at HEAD, clean copy, no misreport warning, not retried"""
        self.assertEqual((self.state / "demo.sha").read_text().strip(), self.head())
        self.assertEqual(self.git(self.d, "status", "--porcelain"), "")
        _ = self.lib._
        self.assertNotIn(_("{project}: this result is not trustworthy; the checkpoint is not advanced, retrying next "
                           "time", project="demo"), self.log())
        self.assertNotIn(_("{project}: warning: the agent reported state_updated={reported}, but the actual file "
                           "change={actually} (going by the actual files)", project="demo", reported="true",
                           actually="false"), self.log())

    def unanchored_line(self, raws: str) -> str:
        return self.lib._("{project}: anchors that do not resolve on the project's main were turned back into plain "
                          "code: {anchors}", project="demo", anchors=raws)

    def undid_line(self) -> str:
        return self.lib._("{project}: admission undid the model's only edit (anchors that do not resolve)",
                          project="demo")

    def test_hook_committed_mixed_edit_gets_admission_on_top(self):
        digest = self.process(GOOD_AND_BAD, hook=True)
        admitted = GOOD_AND_BAD.replace("⚓`core/settings.py`", "`core/settings.py`")
        self.assertEqual(self.subjects(), ["librarian: admit STATE.md anchors", HOOK_SUBJECT, "new note", "init"],
                         "admission is committed on top of the hook's commit, and nothing after it")
        self.assertEqual(self.git(self.d, "show", "HEAD:STATE.md"), admitted)
        self.assertEqual(self.git(self.d, "show", "HEAD~1:STATE.md"), GOOD_AND_BAD, "the hook's commit is left as is")
        self.assertEqual(self.pushed(), 1)
        self.assert_done()
        self.assertIn(self.unanchored_line("core/settings.py"), self.log())
        self.assertNotIn(self.undid_line(), self.log())
        rl = self.review_log()
        self.assertIn(f"**commit**: `{self.head()}`", rl)
        self.assertIn("+- Settings live in `core/settings.py`.", rl)
        self.assertIn("+- Retries go through ⚓`src/job.py#run_job`.", rl)
        self.assertEqual(digest, ["## demo\n\nsummary demo\n\n"])
        self.assertEqual((self.outcomes[0]["changed"], self.outcomes[0]["unanchored"]), (True, ["core/settings.py"]))
        self.assertEqual((self.case / "after.STATE.md").read_text(), GOOD_AND_BAD, "the eval case keeps what the model "
                                                                                 "wrote, before admission")

    def test_hook_committed_only_a_bad_anchor_still_completes(self):
        digest = self.process(ONLY_BAD, hook=True)
        self.assertEqual(self.subjects(), ["librarian: admit STATE.md anchors", HOOK_SUBJECT, "new note", "init"])
        self.assertEqual(self.git(self.d, "show", "HEAD:STATE.md"), BEFORE, "admission gives the file back")
        self.assertEqual(self.git(self.d, "diff", "HEAD~2", "HEAD", "--", "STATE.md"), "")
        self.assertEqual(self.pushed(), 1)
        self.assert_done()
        self.assertIn(self.unanchored_line("pkg/parse.py"), self.log())
        self.assertIn(self.undid_line(), self.log())
        self.assertEqual(self.review_log(), "", "no empty review-log entry")
        self.assertEqual(digest, ["## demo\n\nsummary demo\n\n"])
        self.assertEqual((self.outcomes[0]["changed"], self.outcomes[0]["unanchored"]), (False, ["pkg/parse.py"]))
        self.assertEqual((self.case / "after.STATE.md").read_text(), ONLY_BAD)

    def test_only_a_bad_anchor_without_the_hook_still_completes(self):
        digest = self.process(ONLY_BAD, hook=False)
        self.assertEqual(self.subjects(), ["new note", "init"], "nothing to commit")
        self.assertEqual((self.d / "STATE.md").read_text(), BEFORE)
        self.assertEqual(self.pushed(), 0)
        self.assert_done()
        self.assertIn(self.undid_line(), self.log())
        self.assertEqual(self.review_log(), "")
        self.assertEqual(digest, ["## demo\n\nsummary demo\n\n"])
        self.assertEqual((self.outcomes[0]["changed"], self.outcomes[0]["unanchored"]), (False, ["pkg/parse.py"]))
        self.assertEqual((self.case / "after.STATE.md").read_text(), ONLY_BAD)

    def test_mixed_edit_without_the_hook_is_one_commit(self):
        self.process(GOOD_AND_BAD, hook=False)
        self.assertEqual(self.subjects(), ["librarian: update STATE.md", "new note", "init"])
        self.assertEqual(self.git(self.d, "show", "HEAD:STATE.md"),
                         GOOD_AND_BAD.replace("⚓`core/settings.py`", "`core/settings.py`"))
        self.assertEqual(self.pushed(), 1)
        self.assert_done()
        self.assertNotIn(self.undid_line(), self.log())
        self.assertIn(f"**commit**: `{self.head()}`", self.review_log())


if __name__ == "__main__":
    unittest.main()
