"""每晚的 worktree 自動清理(bin/worktrees.py auto_prune):在暫存的 git repo 裡實際建 worktree,
確認該動的才動、不該動的不動。

    python3 -m unittest discover -s tests
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hubconf  # noqa: E402
import worktrees  # noqa: E402


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout


class AutoPrune(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / ".gitignore").write_text(".venv/\nnode_modules/\n.env\n")
        (self.repo / "uv.lock").write_text("lock\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "init")
        self.saved = (worktrees.overview, worktrees._cwds_in_use, worktrees._transcript_age_days,
                      hubconf.workspace, hubconf.STATE)
        hubconf.STATE = self.tmp / "state"
        hubconf.workspace = lambda name: self.repo
        worktrees._transcript_age_days = lambda path: None
        self.busy: list[str] = []
        worktrees._cwds_in_use = lambda: self.busy
        self.wts: list[dict] = []
        worktrees.overview = lambda: {"proj": {"worktrees": self.wts}}

    def tearDown(self):
        (worktrees.overview, worktrees._cwds_in_use, worktrees._transcript_age_days,
         hubconf.workspace, hubconf.STATE) = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, name, idle, merged=True, venv=True, extra_ignored=None, recipe=True):
        p = self.tmp / name
        git(self.repo, "worktree", "add", "-q", str(p), "-b", name)
        if not recipe:
            (p / "uv.lock").unlink()
            git(p, "commit", "-qam", "no lock")  # 分支多一個 commit → 未合併;merged 由下面的 dict 決定
        if venv:
            (p / ".venv").mkdir()
            (p / ".venv" / "x").write_text("x")
        if extra_ignored:
            (p / extra_ignored).write_text("secret")
        self.wts.append({"path": str(p), "branch": name, "merged": merged, "clean": True, "exists": True,
                         "age_days": idle})
        return p

    def branches(self):
        return git(self.repo, "branch", "--format=%(refname:short)").split()

    def test_recent_untouched(self):
        p = self.add("fresh", idle=1)
        r = worktrees.auto_prune(dry_run=False)
        self.assertTrue((p / ".venv").exists())
        self.assertEqual(r["deps"] + r["removed"], [])

    def test_tier1_deletes_venv_keeps_worktree_and_stays_clean(self):
        p = self.add("idle3", idle=3)
        r = worktrees.auto_prune(dry_run=False)
        self.assertFalse((p / ".venv").exists())
        self.assertTrue(p.exists())
        self.assertEqual(git(p, "status", "--porcelain"), "", "標記不能讓 worktree 變髒")
        gd = pathlib.Path(git(p, "rev-parse", "--absolute-git-dir").strip())
        self.assertIn("uv sync", (gd / worktrees.MARKER).read_text())
        self.assertEqual(len(r["deps"]), 1)

    def test_tier1_needs_recipe(self):
        p = self.add("norecipe", idle=3, recipe=False)
        worktrees.auto_prune(dry_run=False)
        self.assertTrue((p / ".venv").exists(), "沒有 uv.lock/pyproject.toml 就不刪 .venv")

    def test_tier2_removes_worktree_keeps_branch(self):
        p = self.add("old", idle=20)
        r = worktrees.auto_prune(dry_run=False)
        self.assertFalse(p.exists())
        self.assertIn("old", self.branches())
        self.assertEqual(len(r["removed"]), 1)

    def test_session_open_inside_untouched(self):
        """閒置但還開著的 session(可能只是還沒回來繼續):依賴也不刪。"""
        p = self.add("busy", idle=20)
        self.busy = [str(p / "src")]
        r = worktrees.auto_prune(dry_run=False)
        self.assertTrue(p.exists())
        self.assertTrue((p / ".venv").exists())
        self.assertEqual(r["deps"] + r["removed"], [])

    def test_tier2_unknown_ignored_skipped(self):
        p = self.add("secret", idle=20, extra_ignored=".env")
        r = worktrees.auto_prune(dry_run=False)
        self.assertTrue((p / ".env").exists())
        self.assertTrue(p.exists())
        self.assertEqual(len(r["skipped"]), 1)

    def test_unmerged_untouched(self):
        p = self.add("wip", idle=30, merged=False)
        worktrees.auto_prune(dry_run=False)
        self.assertTrue((p / ".venv").exists())
        self.assertTrue(p.exists())

    def test_dry_run_changes_nothing(self):
        p = self.add("dry", idle=20)
        r = worktrees.auto_prune(dry_run=True)
        self.assertTrue((p / ".venv").exists())
        self.assertEqual(len(r["removed"]), 1)
        self.assertFalse((hubconf.STATE / "worktree-prune.json").exists())


if __name__ == "__main__":
    unittest.main()
