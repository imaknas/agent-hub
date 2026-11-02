"""A newly connected project's first STATE.md (bin/first-look.py): read through git (a README that is a link is not
followed), written only over onboarding's placeholder, only when the model's to-do section follows the contract, and
nothing at all without a model. No model is called: llm.py and the project job are stand-ins.

    bin/py -m unittest tests.test_first_look
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
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
spec = importlib.util.spec_from_file_location("first_look_t", ROOT / "bin" / "first-look.py")
fl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fl)
GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
PLACEHOLDER = "# STATE\n\n> placeholder\n"


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=GIT_ENV)


class FirstLook(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = self.tmp / "app"
        (self.ws / ".agent-memory" / "memory").mkdir(parents=True)
        (self.ws / ".agent-memory" / "STATE.md").write_text(PLACEHOLDER)
        patches = [mock.patch.object(fl.hubconf, "projects", lambda: {"app": {"name": "app", "paths": {
                       "workspace": str(self.ws)}}}),
                   mock.patch.object(fl.hubconf, "lang_content", lambda: "en"),
                   mock.patch.object(fl.hubconf, "owner_tag", lambda: "Alex")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_main(self, answer=None, ready=True):
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            if "llm.py" in " ".join(map(str, argv)):
                return subprocess.CompletedProcess(argv, 0, json.dumps({"structured_output": answer}), "")
            return subprocess.CompletedProcess(argv, 0, "", "")
        import llm_ready
        import project_git
        out = io.StringIO()
        with mock.patch.object(llm_ready, "unsupported", lambda point: None if ready else "no model"), \
                mock.patch.object(project_git, "data", lambda *a: {"readme": "An app.", "commits": ["2026-11-01 init"],
                                                                   "tree": ["README.md"], "commit_count": 1}), \
                mock.patch.object(fl.subprocess, "run", fake_run), redirect_stdout(out):
            rc = fl.main(["app"])
        return rc, out.getvalue(), calls

    def state(self):
        return (self.ws / ".agent-memory" / "STATE.md").read_text()

    def test_writes_a_valid_first_state(self):
        good = "# STATE\n\nAn app.\n\n## Tasks\n\n- [ ] [agent] Add tests\n"
        rc, out, calls = self.run_main({"state": good, "summary": "A small app."})
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.state(), good)
        self.assertIn("A small app.", out)
        self.assertIn("- [ ] [agent] Add tests", out)
        self.assertTrue(any("git-sync-memory.sh" in " ".join(map(str, c)) for c in calls), "the memory branch is synced")

    def test_a_malformed_todo_section_is_not_written(self):
        rc, out, _calls = self.run_main({"state": "# STATE\n\nAn app.\n\n## Tasks\n\n* add tests\n", "summary": "x"})
        self.assertEqual(rc, 1)
        self.assertEqual(self.state(), PLACEHOLDER)

    def test_no_model_writes_nothing(self):
        rc, out, calls = self.run_main(ready=False)
        self.assertEqual(rc, 0)
        self.assertIn("brunna llm setup", out)
        self.assertEqual(calls, [])
        self.assertEqual(self.state(), PLACEHOLDER)

    def test_an_existing_state_or_notes_are_left_alone(self):
        (self.ws / ".agent-memory" / "memory" / "2026-11-01.md").write_text("did things\n")
        rc, _out, calls = self.run_main({"state": "# STATE\n\n## Tasks\n\n- (none)\n", "summary": "x"})
        self.assertEqual((rc, calls), (0, []))
        self.assertEqual(self.state(), PLACEHOLDER)


class GitFacts(unittest.TestCase):
    def test_a_linked_readme_is_not_read(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "secret").write_text("do not send\n")
        repo = tmp / "repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        (repo / "README.md").symlink_to(tmp / "secret")
        (repo / "a.py").write_text("x = 1\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "first")
        r = subprocess.run([sys.executable, str(ROOT / "bin" / "project-git-job.py"), "first-look", str(repo)],
                           capture_output=True, text=True, env=GIT_ENV)
        facts = json.loads(r.stdout)
        self.assertEqual(facts["readme"], "")
        self.assertEqual(facts["commits"][0][11:], "first")
        self.assertEqual(sorted(facts["tree"]), ["README.md", "a.py"])
        (repo / "README.md").unlink()
        (repo / "README.md").write_text("Hello\n")
        git(repo, "commit", "-qam", "real readme")
        r = subprocess.run([sys.executable, str(ROOT / "bin" / "project-git-job.py"), "first-look", str(repo)],
                           capture_output=True, text=True, env=GIT_ENV)
        self.assertEqual(json.loads(r.stdout)["readme"], "Hello\n")


if __name__ == "__main__":
    unittest.main()
