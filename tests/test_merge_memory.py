"""merge-branch never merges a project's memory branch, and --dry-run moves nothing (11-10: a session in a project's
.agent-memory worktree merged the memory branch into a public main, and a dry run rebased a branch in place).

Each test runs merge-branch for real against a throwaway project repo: main, its .agent-memory worktree on an orphan
agent-memory branch (made without `worktree add --orphan`, which older git lacks), and session worktrees.

    bin/py -m unittest tests.test_merge_memory
"""
import pathlib
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_merge_landing import BIN, PY, ROOT, Base  # noqa: E402

sys.path.insert(0, str(BIN))
import i18n  # noqa: E402


class Project(Base):
    def setUp(self):
        super().setUp()
        self.env["AGENT_HUB_ROOT"] = str(ROOT)
        self.ws = self.tmp / "ws"
        self.git(self.tmp, "init", "-q", "-b", "main", str(self.ws))
        self.commit(self.ws, "a", "chore: initial commit")
        # the memory worktree as onboarding leaves it: an orphan agent-memory branch with notes
        self.mem = self.ws / ".agent-memory"
        self.git(self.ws, "worktree", "add", "-q", "--detach", str(self.mem))
        self.git(self.mem, "checkout", "-q", "--orphan", "agent-memory")
        self.git(self.mem, "rm", "-rfq", ".")
        (self.mem / "memory").mkdir()
        for name in ("AGENT.md", "STATE.md", "memory/2026-11-09.md"):
            (self.mem / name).write_text(name)
            self.git(self.mem, "add", name)
            self.git(self.mem, "commit", "-q", "-m", f"memory: {name}")
        (self.ws / ".git" / "info" / "exclude").write_text(".agent-memory/\n")
        self.index({"demo": {"v": 2, "name": "demo", "paths": {"workspace": str(self.ws)}}})

    def worktree(self, name: str, start: str = "main") -> pathlib.Path:
        wt = self.tmp / name
        self.git(self.ws, "worktree", "add", "-q", "-b", name, str(wt), start)
        return wt

    def merge(self, cwd, *args):
        return subprocess.run([PY, str(BIN / "merge-branch"), *args], cwd=cwd, env=self.env, capture_output=True,
                              text=True, timeout=180)

    def snapshot(self) -> dict:
        """Every ref, every worktree and its HEAD, each worktree's status and ORIG_HEAD."""
        trees = [line.split(" ", 1)[1] for line in self.git(self.ws, "worktree", "list", "--porcelain").splitlines()
                 if line.startswith("worktree ")]
        return {"refs": self.git(self.ws, "for-each-ref", "--format=%(refname) %(objectname)"),
                "worktrees": self.git(self.ws, "worktree", "list", "--porcelain"),
                "status": {t: self.git(t, "status", "--porcelain", "--untracked-files=all") for t in trees},
                "orig_head": {t: subprocess.run(["git", "-C", t, "rev-parse", "-q", "--verify", "ORIG_HEAD"],
                                                env=self.env, capture_output=True, text=True).stdout for t in trees}}


class MemoryBranchRefused(Project):
    def refused(self, cwd, *args, why: str):
        before = self.snapshot()
        r = self.merge(cwd, *args)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(i18n.translate("merge-branch", "merge-branch: {why}. A project's memory branch never goes into "
                                     "its main (memory notes live only on {mem}, in the .agent-memory worktree); "
                                     "nothing was merged", {"why": "\0", "mem": "agent-memory"},
                                     "zh-Hant").split("\0")[1], r.stderr)
        self.assertIn(why, r.stderr)
        self.assertEqual(self.snapshot(), before, "nothing moved")
        self.assertEqual(self.git(self.ws, "log", "--format=%s", "main"), "chore: initial commit")
        self.assertEqual(self.events("merge.failed") + self.events("merge.done"), [], "refused before the gate")
        return r

    def test_the_memory_worktree_without_branch(self):
        """the 11-10 case: a session whose folder was the .agent-memory worktree"""
        self.refused(self.mem, why=str(self.mem.resolve()))

    def test_the_memory_worktree_from_a_subfolder(self):
        self.refused(self.mem / "memory", why=str(self.mem.resolve()))

    def test_the_memory_branch_named(self):
        self.refused(self.mem, "--branch", "agent-memory", why=str(self.mem.resolve()))

    def test_a_memory_branch_under_another_name(self):
        """refused by what it holds: its own worktree, its own name, its tree a memory branch's"""
        wt = self.worktree("notes", "agent-memory")
        why = i18n.translate("merge-branch", "{branch} holds only what a memory branch holds (AGENT.md, RULES, "
                             "STATE.md, notes under memory/)", {"branch": "notes"}, "zh-Hant")
        self.refused(wt, why=why)
        self.refused(wt, "--branch", "notes", why=why)

    def test_a_branch_carrying_copies_of_the_memory_commits(self):
        """the dry-run aftermath: a session branch rebased onto a main that briefly held the memory commits, after
        main was put back: its own commit plus copies of every memory commit"""
        wt = self.worktree("pilot")
        for sha in self.git(self.ws, "rev-list", "--reverse", "agent-memory").split():
            self.git(wt, "cherry-pick", sha)
        self.commit(wt, "b", "feat: b")
        why = i18n.translate("merge-branch", "{n} of the {total} {total:commit|commits} {branch} would bring into "
                             "{base} are the memory branch's ({mem}) or copies of them, e.g. {sha}: rebuild the branch "
                             "on {base} with only its own commits (`git rebase -i {base}` and drop the others, or "
                             "cherry-pick its own onto a new branch from {base})",
                             {"n": 3, "total": 4, "branch": "pilot", "base": "main", "mem": "agent-memory",
                              "sha": "\0"}, "zh-Hant").split("\0")[0]
        self.refused(wt, why=why)

    def test_a_branch_holding_the_memory_branch_itself(self):
        """the memory branch's own commits, not copies: the memory branch rebased onto main (as on 11-10), and a
        branch started from it with code of its own"""
        self.git(self.mem, "rebase", "-q", "main")
        wt = self.worktree("mixed", "agent-memory")
        self.commit(wt, "b", "feat: b")
        why = i18n.translate("merge-branch", "{n} of the {total} {total:commit|commits} {branch} would bring into "
                             "{base} are the memory branch's ({mem}) or copies of them, e.g. {sha}: rebuild the branch "
                             "on {base} with only its own commits (`git rebase -i {base}` and drop the others, or "
                             "cherry-pick its own onto a new branch from {base})",
                             {"n": 3, "total": 4, "branch": "mixed", "base": "main", "mem": "agent-memory",
                              "sha": self.git(self.ws, "rev-parse", "agent-memory")[:10]}, "zh-Hant")
        self.refused(wt, why=why)

    def test_a_branch_with_no_history_in_common(self):
        wt = self.tmp / "other"
        self.git(self.ws, "worktree", "add", "-q", "--detach", str(wt))
        self.git(wt, "checkout", "-q", "--orphan", "other")
        self.commit(wt, "code.py", "feat: unrelated")
        self.refused(wt, why=i18n.translate("merge-branch", "{branch} shares no history with {base}: merging it would "
                                            "bring all of its history (a memory branch starts on its own)",
                                            {"branch": "other", "base": "main"}, "zh-Hant"))

    def test_branch_must_be_the_one_checked_out(self):
        """--branch naming another branch would fast-forward main to what the gate never rebased or tested"""
        wt = self.worktree("feature")
        self.commit(wt, "b", "feat: b")
        before = self.snapshot()
        r = self.merge(wt, "--branch", "agent-memory")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(i18n.translate("merge-branch", "merge-branch: --branch names {branch}, but this folder has "
                                     "{here} checked out; the gate rebases and tests what is checked out here, so run "
                                     "it in {branch}'s own worktree; nothing was merged",
                                     {"branch": "agent-memory", "here": "feature"}, "zh-Hant"), r.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_an_ordinary_branch_merges(self):
        """the control: with the memory branch beside it, a session branch merges"""
        wt = self.worktree("feature")
        self.commit(wt, "b", "feat: b")
        r = self.merge(wt)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.git(self.ws, "log", "--format=%s", "-1", "main"), "feat: b")
        self.assertEqual(self.git(self.ws, "ls-tree", "--name-only", "main"), "a\nb")


class DryRunMovesNothing(Project):
    """A dry run tries everything (rebase, titles, tests) on a temporary copy: every ref, worktree and status as it
    was. main moves after the branch forks, so a rebase in place would show."""

    def setUp(self):
        super().setUp()
        self.wt = self.worktree("feature")
        self.commit(self.wt, "b", "feat: b")
        self.commit(self.ws, "c", "chore: c on main")

    def dry_run(self):
        before = self.snapshot()
        r = self.merge(self.wt, "--dry-run")
        self.assertEqual(self.snapshot(), before, "a dry run moves no ref and touches no worktree")
        self.assertEqual(self.events("merge.failed") + self.events("merge.done"), [])
        return r

    def test_passes_without_moving_anything(self):
        # the tier passes only on the rebased commit (main's c is in it): the dry run tests what a merge would
        self.index({"demo": {"v": 2, "name": "demo", "paths": {"workspace": str(self.ws)},
                             "tests": [{"tier": "quick", "run": "test -f c && test -f b", "when": ["merge"]}]}})
        r = self.dry_run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(i18n.translate("merge-branch", "(dry-run) would fast-forward {n} {n:commit|commits} of {branch} "
                                     "into {base}; tests: {tests}", {"n": 1, "branch": "feature", "base": "main",
                                                                     "tests": True}, "zh-Hant"), r.stdout)
        self.assertNotIn("merge-dry-run", self.git(self.ws, "worktree", "list"))

    def test_update_refs_moves_no_other_branch(self):
        """with rebase.updateRefs on, a rebase moves every branch pointing into what it rebases: a dry run's doesn't"""
        self.git(self.ws, "config", "rebase.updateRefs", "true")
        self.git(self.ws, "branch", "parked", "feature")
        r = self.dry_run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_conflict_moves_nothing(self):
        (self.ws / "b").write_text("main's b")
        self.git(self.ws, "add", "b")
        self.git(self.ws, "commit", "-q", "-m", "chore: b on main")
        r = self.dry_run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("b", r.stdout)

    def test_a_failing_tier_moves_nothing(self):
        self.index({"demo": {"v": 2, "name": "demo", "paths": {"workspace": str(self.ws)},
                             "tests": [{"tier": "quick", "run": "false", "when": ["merge"]}]}})
        r = self.dry_run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_the_real_merge_after_it(self):
        self.dry_run()
        r = self.merge(self.wt)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.git(self.ws, "log", "--format=%s", "main"), "feat: b\nchore: c on main\nchore: initial commit")


if __name__ == "__main__":
    unittest.main()
