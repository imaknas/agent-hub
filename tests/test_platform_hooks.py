"""The git hooks the platform writes into a repo from its registry entry (bin/render-project-settings.py
wanted_hooks; docs/design/test-waiting.md A1): gate: strict refuses commits on main, commit_gate runs the commit tiers
on a branch and the merge tiers on main through the main checkout's runner, commits: conventional checks titles.

    bin/py -m unittest discover -s tests
"""
import importlib.machinery
import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
_loader = importlib.machinery.SourceFileLoader("render_project_settings", str(ROOT / "bin" / "render-project-settings.py"))
rps = importlib.util.module_from_spec(importlib.util.spec_from_loader(_loader.name, _loader))
_loader.exec_module(rps)


class WantedHooks(unittest.TestCase):
    def test_strict_alone_is_todays_hook(self):
        want = rps.wanted_hooks({"gate": "strict"})
        self.assertEqual(set(want), {"pre-commit", "pre-merge-commit"})
        self.assertEqual(want["pre-commit"][0], "#!/bin/sh\n" + rps.gate_hook())

    def test_fields_choose_the_hooks(self):
        self.assertEqual(rps.wanted_hooks({}), {})
        self.assertEqual(set(rps.wanted_hooks({"commits": "conventional"})), {"commit-msg"})
        self.assertEqual(set(rps.wanted_hooks({"commit_gate": True})), {"pre-commit"})
        self.assertEqual(set(rps.wanted_hooks({"commit_gate": True, "gate": "strict"})), {"pre-commit", "pre-merge-commit"})
        for text, _why in rps.wanted_hooks({"commit_gate": True, "gate": "strict", "commits": "conventional"}).values():
            self.assertTrue(text.startswith("#!/bin/sh\n" + rps.MARK))


class HooksInARepo(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t"}
        self.repo = self.tmp / "r"
        self.git(self.tmp, "init", "-q", "-b", "main", str(self.repo))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, cwd, *args, check=True):
        r = subprocess.run(["git", "-C", str(cwd), *args], env=self.env, capture_output=True, text=True)
        if check:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def install(self, hook, text):
        f = self.repo / ".git" / "hooks" / hook
        f.write_text(text)
        f.chmod(0o755)

    def commit(self, title):
        (self.repo / "f").write_text(title)
        self.git(self.repo, "add", "f")
        return self.git(self.repo, "commit", "-q", "-m", title, check=False)

    def test_commit_gate_runs_merge_tiers_on_main_and_commit_tiers_on_a_branch(self):
        text, _why = rps.wanted_hooks({"commit_gate": True})["pre-commit"]
        # the runner is the main checkout's; here a stand-in records what it was asked
        rec = self.tmp / "asked"
        exec_line = next(line for line in text.splitlines() if line.startswith("exec "))
        self.install("pre-commit", text.replace(exec_line, f'echo "$moment" >> {rec}'))
        self.assertEqual(self.commit("chore: a").returncode, 0)
        self.git(self.repo, "checkout", "-q", "-b", "work")
        self.assertEqual(self.commit("chore: b").returncode, 0)
        self.assertEqual(rec.read_text().split(), ["merge", "commit"])
        self.assertIn("test-runner.py", exec_line)
        self.assertIn("--index", exec_line)

    def test_strict_with_commit_gate_refuses_main(self):
        text, _why = rps.wanted_hooks({"commit_gate": True, "gate": "strict"})["pre-commit"]
        exec_line = next(line for line in text.splitlines() if line.startswith("exec "))
        self.install("pre-commit", text.replace(exec_line, "exit 0"))
        r = self.commit("chore: a")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("main", r.stderr, "the refusal names the branch")
        self.git(self.repo, "checkout", "-q", "-b", "work")
        self.assertEqual(self.commit("chore: b").returncode, 0)

    def test_commit_msg_checks_titles(self):
        text, _why = rps.wanted_hooks({"commits": "conventional"})["commit-msg"]
        self.install("commit-msg", text)
        self.assertNotEqual(self.commit("Update stuff").returncode, 0)
        self.assertEqual(self.commit("fix: stuff").returncode, 0)


if __name__ == "__main__":
    unittest.main()
