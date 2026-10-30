"""commit 標題的格式(bin/commit_title.py)跟合併閘門的檢查(bin/merge-branch,登錄 commits: conventional)。

merge-branch 的部分用隔離的 config + 真的 git repo / worktree 跑。

    bin/py -m unittest discover -s tests
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
import commit_title  # noqa: E402


class Title(unittest.TestCase):
    def test_accepts(self):
        for t in ("feat: 新增信箱的 MCP 伺服器", "fix(hubbus): dispatch 不再重試", "feat(llm)!: 組名改成 fast/standard/strong",
                  "chore: initial commit", "revert: feat(llm): 換模型", 'Revert "feat: x"', "Merge branch 'x'"):
            with self.subTest(t=t):
                self.assertIsNone(commit_title.problem(t))

    def test_rejects(self):
        for t in ("Add LICENSE", "feat:沒有空格", "feature: 不在清單", "Fix: 大寫", "fix(): 空的 scope", "", "feat: ",
                  "Merge gate checks titles", "Revert to old behaviour"):
            with self.subTest(t=t):
                self.assertIsNotNone(commit_title.problem(t))

    def test_autosquash_only_on_the_branch(self):
        self.assertIsNone(commit_title.problem("fixup! feat: x"))
        self.assertIsNotNone(commit_title.problem("fixup! feat: x", final=True))

    def test_hook_reads_the_first_real_line(self):
        self.assertEqual(commit_title.title_of("\n# 註解\nfix: x\n\n內文\n"), "fix: x")

    def test_cli(self):
        ok = subprocess.run([sys.executable, str(BIN / "commit_title.py"), "docs: x"], capture_output=True, text=True)
        bad = subprocess.run([sys.executable, str(BIN / "commit_title.py"), "x"], capture_output=True, text=True)
        self.assertEqual((ok.returncode, bad.returncode), (0, 1))
        self.assertIn("Conventional Commits", bad.stderr)


class MergeGate(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        for d in ("home", "config/projects", "data", "secrets", "bus"):
            (self.tmp / d).mkdir(parents=True)
        (self.tmp / "config" / "hub.json").write_text(json.dumps({"bus_dir": str(self.tmp / "bus"),
                                                                  "registry_dir": "projects"}))
        self.ws = self.tmp / "ws"
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith("AGENT_HUB_") or k == "AGENT_HUB_PYTHON"},
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
        self.commit(self.wt, "b", "feat: 好的")
        self.commit(self.wt, "c", "Update stuff")
        r = self.merge()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("commit-titles", r.stdout)
        self.assertIn("Update stuff", r.stdout)
        self.assertNotIn("好的", r.stdout.split("Conventional Commits")[-1], "只列不合格的")
        self.assertEqual(self.git(self.ws, "log", "--format=%s", "-1").strip(), "chore: initial commit", "main 沒動")

    def test_good_titles_merge(self):
        self.register(commits="conventional")
        self.commit(self.wt, "b", "feat(x): 好的")
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.git(self.ws, "log", "--format=%s", "-1").strip(), "feat(x): 好的")

    def test_registry_only_knows_conventional(self):
        (self.tmp / "config" / "projects" / "demo.yaml").write_text(
            f"v: 1\nname: demo\npaths:\n  workspace: {self.ws}\ncommits: angular\n")
        r = subprocess.run([sys.executable, str(BIN / "registry.py"), "check", "demo"],
                           env={**self.env, "AGENT_HUB_LANG_UI": "zh-Hant"}, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertIn("commits 只能是 conventional", r.stdout)

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
