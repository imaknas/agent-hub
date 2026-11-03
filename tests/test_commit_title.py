"""The commit title format (bin/commit_title.py) and the merge gate's check (bin/merge-branch, registry
commits: conventional).

The merge-branch part runs with an isolated config + a real git repo / worktree.

    bin/py -m unittest discover -s tests
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
import commit_title  # noqa: E402
import i18n  # noqa: E402


class Title(unittest.TestCase):
    def test_accepts(self):
        for t in ("feat: add the mailbox's MCP server", "fix(hubbus): dispatch no longer retries",
                  "feat(llm)!: group names become fast/standard/strong", "docs: café résumé, non-ASCII is fine",
                  "chore: initial commit", "revert: feat(llm): switch models", 'Revert "feat: x"', "Merge branch 'x'"):
            with self.subTest(t=t):
                self.assertIsNone(commit_title.problem(t))

    def test_rejects(self):
        for t in ("Add LICENSE", "feat:no space", "feature: not in the list", "Fix: capitalised", "fix(): empty scope",
                  "", "feat: ",
                  "Merge gate checks titles", "Revert to old behaviour"):
            with self.subTest(t=t):
                self.assertIsNotNone(commit_title.problem(t))

    def test_autosquash_only_on_the_branch(self):
        self.assertIsNone(commit_title.problem("fixup! feat: x"))
        self.assertIsNotNone(commit_title.problem("fixup! feat: x", final=True))

    def test_hook_reads_the_first_real_line(self):
        self.assertEqual(commit_title.title_of("\n# a comment\nfix: x\n\nthe body\n"), "fix: x")

    def test_cli(self):
        ok = subprocess.run([sys.executable, str(BIN / "commit_title.py"), "docs: x"], capture_output=True, text=True)
        bad = subprocess.run([sys.executable, str(BIN / "commit_title.py"), "x"], capture_output=True, text=True)
        self.assertEqual((ok.returncode, bad.returncode), (0, 1))
        self.assertIn("Conventional Commits", bad.stderr)


class PrivateNames(unittest.TestCase):
    """In the platform's own repo (public history) a commit message may not name a private project: the commit-msg
    hook refuses it (bin/private_names.py's rules, as the boundary test applies them to files)."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        names = self.tmp / "private-names.txt"
        names.write_text("# private projects\nsecret-garden\nledger~\n")
        patch = mock.patch.dict(os.environ, {"AGENT_HUB_PRIVATE_NAMES": str(names)})
        patch.start()
        self.addCleanup(patch.stop)

    def check(self, message, platform=True):
        f = self.tmp / "COMMIT_EDITMSG"
        f.write_text(message)
        with mock.patch.object(commit_title, "platform_repo", return_value=platform):
            return commit_title.main(["--file", str(f)])

    def test_a_private_name_is_refused_in_the_platform_repo(self):
        self.assertEqual(self.check("fix: x\n\nseen in secret-garden last week\n"), 1)
        self.assertEqual(self.check("fix: x\n\nthe ledger project grew\n"), 1, "a common-word name next to 'project'")

    def test_plain_words_comments_and_other_repos_pass(self):
        self.assertEqual(self.check("fix: x\n\nseen in one project last week\n"), 0)
        self.assertEqual(self.check("fix: balance the ledger\n"), 0, "a common word alone is not the name")
        self.assertEqual(self.check("fix: x\n# On branch secret-garden\n"), 0, "git's comment lines are not the message")
        self.assertEqual(self.check("fix: x\n\nseen in secret-garden\n", platform=False), 0, "only the platform's repo")


class MergeGate(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        for d in ("home", "config/projects", "data", "secrets", "bus"):
            (self.tmp / d).mkdir(parents=True)
        (self.tmp / "config" / "hub.json").write_text(json.dumps({"bus_dir": str(self.tmp / "bus"),
                                                                  "registry_dir": "projects"}))
        self.ws = self.tmp / "ws"
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("AGENT_HUB_", "BRUNNA_")) or k in ("AGENT_HUB_PYTHON", "BRUNNA_PYTHON")},
                    "AGENT_HUB_ROOT": str(ROOT),   # the tree under test (no lookup of a main checkout from a worktree)
                    "HOME": str(self.tmp / "home"), "AGENT_HUB_CONFIG": str(self.tmp / "config"),
                    "AGENT_HUB_DATA": str(self.tmp / "data"), "AGENT_HUB_SECRETS": str(self.tmp / "secrets"),
                    "AGENT_HUB_STATE": str(self.tmp / "state"), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null"}
        self.git(self.tmp, "init", "-q", "-b", "main", str(self.ws))
        self.commit(self.ws, "a", "chore: initial commit")
        self.wt = self.tmp / "wt"
        self.git(self.ws, "worktree", "add", "-q", "-b", "feature", str(self.wt))

    def git(self, cwd, *args):
        r = subprocess.run(["git", "-C", str(cwd), *args], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def commit(self, cwd, name, title):
        (cwd / name).write_text(name)
        self.git(cwd, "add", name)
        self.git(cwd, "commit", "-q", "-m", title)

    def register(self, commits=None):
        (self.tmp / "config" / "projects" / "demo.yaml").write_text(
            f"v: 1\nname: demo\npaths:\n  workspace: {self.ws}\n" + (f"commits: {commits}\n" if commits else ""))
        r = subprocess.run([sys.executable, str(BIN / "registry.py"), "check", "demo"], env=self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def merge(self):
        return subprocess.run([sys.executable, str(BIN / "merge-branch")], cwd=self.wt, env=self.env,
                              capture_output=True, text=True, timeout=120)

    def test_bad_title_blocks_when_registered(self):
        self.register(commits="conventional")
        self.commit(self.wt, "b", "feat: a fine change")
        self.commit(self.wt, "c", "Update stuff")
        r = self.merge()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("commit-titles", r.stdout)
        self.assertIn("Update stuff", r.stdout)
        self.assertNotIn("a fine change", r.stdout.split("Conventional Commits")[-1], "lists only the invalid ones")
        self.assertEqual(self.git(self.ws, "log", "--format=%s", "-1").strip(), "chore: initial commit",
                         "main untouched")

    def test_good_titles_merge(self):
        self.register(commits="conventional")
        self.commit(self.wt, "b", "feat(x): a fine change")
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.git(self.ws, "log", "--format=%s", "-1").strip(), "feat(x): a fine change")

    def test_registry_only_knows_conventional(self):
        (self.tmp / "config" / "projects" / "demo.yaml").write_text(
            f"v: 1\nname: demo\npaths:\n  workspace: {self.ws}\ncommits: angular\n")
        r = subprocess.run([sys.executable, str(BIN / "registry.py"), "check", "demo"],
                           env={**self.env, "AGENT_HUB_LANG_UI": "zh-Hant"}, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertIn(i18n.translate("registry", "commits can only be conventional (absent = commit titles are not checked)",
                                     {}, "zh-Hant"), r.stdout)

    def test_not_registered_means_no_check(self):
        self.register()
        self.commit(self.wt, "b", "Update stuff")
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_project_missing_from_the_registry_is_not_merged(self):
        """10-30: a project was briefly missing from the index; merge-branch --project skipped its tests and merged"""
        self.register(commits="conventional")
        (self.tmp / "config" / "projects" / "demo.yaml").unlink()
        subprocess.run([sys.executable, str(BIN / "registry.py"), "check", "--rebuild"], env=self.env, capture_output=True,
                       timeout=60)
        self.commit(self.wt, "b", "Update stuff")
        r = subprocess.run([sys.executable, str(BIN / "merge-branch"), "--project", "demo"], cwd=self.wt, env=self.env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("not in the validated project registry", r.stderr)
        self.assertEqual(self.git(self.ws, "log", "--format=%s", "-1").strip(), "chore: initial commit", "main untouched")

    def test_project_must_own_the_repo(self):
        """Round 6: --project chose whose runtime.test ran while the repo came from the cwd, so naming a project with
        no tests merged untested."""
        (self.tmp / "config" / "hub.json").write_text(json.dumps({"bus_dir": str(self.tmp / "bus"), "registry_dir": "projects",
                                                                  "services_root": str(self.tmp / "services")}))
        (self.tmp / "services" / "demo").mkdir(parents=True)
        (self.tmp / "config" / "projects" / "demo.yaml").write_text(
            f"v: 1\nname: demo\npaths:\n  workspace: {self.ws}\nruntime:\n  test: 'false'\n")
        other = self.tmp / "other"
        other.mkdir()
        (self.tmp / "config" / "projects" / "other.yaml").write_text(f"v: 1\nname: other\npaths:\n  workspace: {other}\n")
        r = subprocess.run([sys.executable, str(BIN / "registry.py"), "check", "--rebuild"], env=self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.commit(self.wt, "b", "feat: b")
        r = subprocess.run([sys.executable, str(BIN / "merge-branch"), "--project", "other"], cwd=self.wt, env=self.env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.git(self.ws, "log", "--format=%s", "-1").strip(), "chore: initial commit", "main untouched")

    def test_roster_paths_alone_are_not_enough(self):
        """no index yet: hubconf.projects() falls back to roster(), which knows the folder but not the tests"""
        (self.tmp / "config" / "projects" / "demo.yaml").write_text(
            f"v: 1\nname: demo\npaths:\n  workspace: {self.ws}\nruntime:\n  test: 'false'\n")
        self.commit(self.wt, "b", "feat: b")
        r = self.merge()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("not in the validated project registry", r.stderr)


if __name__ == "__main__":
    unittest.main()
