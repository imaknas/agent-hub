"""A `.git` pointer a sandboxed project wrote decides nothing that runs outside its sandbox (found by the cold review of
docs/design/mailbox-session-first.md step 3).

The attack: `box` writes `box/sub/.git` naming a git dir of its own whose `commondir` is an unsandboxed project's `.git`, and a
`box/sub/.agent-memory` repo whose config runs its script. Through the real script with the runtime's real hook input; a scratch
instance and repos only. The planted script notes whether it ran as the project (HUB_AS_PROJECT): under the project's sandbox it
may run, and that is the project's own business; as the user it must not.

    bin/run-tests        (running this module directly in a worktree reads the real config through .hub-home)
"""
import json
import os
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gitdirs  # noqa: E402
import guard  # noqa: E402
import hubconf  # noqa: E402
import project_git  # noqa: E402
import test_bus_sender as tbs  # noqa: E402


class Fixture(tbs.WorktreeFixture):
    def hook_env(self):
        return {**super().hook_env(), "TMPDIR": str(self.tmp)}

    def git(self, *args):
        subprocess.run(["git", *map(str, args)], check=True, capture_output=True, env={**os.environ, **tbs.GIT_ENV})

    def script(self):
        """What the planted config runs: one line per run, saying as whom."""
        self.marker = self.tmp / "RAN"
        s = self.tmp / "planted.sh"
        s.write_text(f'#!/bin/sh\necho "as-project=$HUB_AS_PROJECT" >> "{self.marker}"\nexit 1\n')
        s.chmod(0o755)
        return s

    def ran_as_the_user(self):
        lines = self.marker.read_text().splitlines() if self.marker.exists() else []
        return [x for x in lines if x == "as-project="]

    def plant_memory(self, folder):
        """A memory repo in the planted folder: every way its config gets to run something, with work to sync."""
        mem = folder / ".agent-memory"
        (mem / "memory").mkdir(parents=True)
        (mem / "memory" / "n.md").write_text("x")
        script = self.script()
        self.git("init", "-q", "-b", "agent-memory", mem)
        for k, v in (("core.sshCommand", script), ("core.fsmonitor", script), ("branch.agent-memory.remote", "origin"),
                     ("branch.agent-memory.merge", "refs/heads/agent-memory")):
            self.git("-C", mem, "config", k, v)
        self.git("-C", mem, "remote", "add", "origin", "ssh://x.invalid/y")
        return mem

    def plant(self, folder, git_home=None):
        """<folder>/.git names <git_home>/fake/worktrees/x, whose commondir is the unsandboxed project's .git."""
        self.tree("open")
        fake = (git_home or folder) / "fake" / "worktrees" / "x"
        fake.mkdir(parents=True)
        (fake / "commondir").write_text(str(self.ws["open"] / ".git") + "\n")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ".git").write_text(f"gitdir: {fake}\n")
        return self.plant_memory(folder)

    def run_hook(self, what, cwd, name, **extra):
        payload = json.dumps({"session_id": self.SID, "cwd": str(cwd), "hook_event_name": name, **extra})
        return subprocess.run(["bash", str(BIN / "worktree-memory.sh"), what], cwd=cwd, input=payload + "\n",
                              capture_output=True, text=True, timeout=120, env=self.hook_env())


class PlantedInASandboxedProject(Fixture):
    def setUp(self):
        super().setUp()
        self.sub = self.ws["box"] / "sub"
        self.plant(self.sub, self.ws["box"])

    def test_the_pointer_says_what_the_review_said(self):
        found = gitdirs.dirs(self.sub)
        self.assertEqual(found.common.parent, self.ws["open"].resolve(), "the plant does what it claims")

    def test_a_start_runs_nothing_as_the_user(self):
        for source in ("startup", "compact"):
            self.run_hook("start", self.sub, "SessionStart", source=source)
            self.assertEqual(self.ran_as_the_user(), [], source)

    def test_an_end_runs_nothing_as_the_user(self):
        for what, name in (("end", "PreCompact"), ("end", "SessionEnd")):
            self.run_hook(what, self.sub, name)
            self.assertEqual(self.ran_as_the_user(), [], name)

    def test_a_prompt_and_a_tool_run_nothing_as_the_user(self):
        self.run_hook("prompt", self.sub, "UserPromptSubmit", prompt="x")
        self.run_hook("nudge", self.sub, "PostToolUse", tool_input={"command": "git commit -m x"})
        self.assertEqual(self.ran_as_the_user(), [])

    @unittest.skipUnless(tbs.seatbelt.can_sandbox() is None, "the fixture's repos are in /tmp, which Landlock's profile lets the "
                                                             "project write; a real project's aren't")
    def test_nothing_is_written_into_the_other_projects_repo(self):
        exclude = self.ws["open"] / ".git" / "info" / "exclude"
        before = exclude.read_text() if exclude.exists() else None
        self.run_hook("start", self.sub, "SessionStart", source="startup")
        self.assertEqual(exclude.read_text() if exclude.exists() else None, before)
        self.assertFalse((self.sub / "CLAUDE.local.md").is_symlink())

    def test_the_end_syncs_the_projects_own_memory_not_the_one_named(self):
        env = self.hook_env()
        r = subprocess.run([sys.executable, str(BIN / "project_git.py"), "worktree-scope", str(self.sub), str(self.ws["open"])],
                           capture_output=True, text=True, env=env)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, str(self.ws["box"] / ".agent-memory")))


class PlantedOutsideEveryProject(Fixture):
    """A sandboxed project's service can write the temp folder: there no project's path says whose it is."""

    def setUp(self):
        super().setUp()
        self.out = self.tmp / "elsewhere" / "planted"

    def test_a_forged_pointer_is_not_believed(self):
        self.plant(self.out)
        for what, name in (("start", "SessionStart"), ("end", "SessionEnd")):
            r = self.run_hook(what, self.out, name)
            self.assertEqual(self.ran_as_the_user(), [], name)
            self.assertEqual(r.stdout.strip(), "", name)
        self.assertFalse((self.out / "CLAUDE.local.md").is_symlink())

    def test_nor_one_naming_a_real_worktrees_git_dir(self):
        """Its back pointer names the real worktree, and only the repo's owner can write that."""
        wt = self.tree("open")
        self.out.mkdir(parents=True)
        (self.out / ".git").write_text(f"gitdir: {self.ws['open'] / '.git' / 'worktrees' / wt.name}\n")
        self.plant_memory(self.out)
        self.run_hook("start", self.out, "SessionStart")
        self.assertEqual(self.ran_as_the_user(), [])

    def test_the_project_it_names_doesnt_get_it_for_its_mail(self):
        """Nor at the session's first hook: the letter stays for the project's own sessions."""
        self.plant(self.out)
        self.letter_to("open", "for the open project", typ="request")
        r = self.run_hook("prompt", self.out, "UserPromptSubmit", prompt="x")
        self.assertNotIn("for the open project", r.stdout)
        self.assertEqual(self.rows("type='message.delivered'"), [])

    def test_a_real_worktree_outside_its_projects_folder_goes_on(self):
        self.tree("open")
        (self.ws["open"] / "memory-file").write_text("x")
        real = self.tmp / "outside" / "real"
        real.parent.mkdir()
        self.git("-C", self.ws["open"], "worktree", "add", "-q", real, "-b", "outside")
        self.run_hook("start", real, "SessionStart")
        self.assertTrue((real / ".agent-memory").is_symlink(), "linked, as a worktree of an open project is")


class RealWorktrees(Fixture):
    def test_an_open_projects_is_handled_as_before(self):
        wt = self.tree("open")
        r = self.run_hook("start", wt, "SessionStart")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((wt / ".agent-memory").is_symlink())

    def test_the_scope_of_each_kind(self):
        wt, env = self.tree("open"), self.hook_env()

        def scope(top, main):
            r = subprocess.run([sys.executable, str(BIN / "project_git.py"), "worktree-scope", str(top), str(main)],
                               capture_output=True, text=True, env=env)
            return r.returncode, r.stdout.strip()
        self.assertEqual(scope(wt, self.ws["open"]), (project_git.NOT_SANDBOXED, ""))
        box = self.tree("box")
        self.assertEqual(scope(box, self.ws["box"]), (0, str(self.ws["box"] / ".agent-memory")))
        # a folder in an open project naming a sandboxed project's repo: that repo's own config is never run as the user
        self.assertEqual(scope(wt, self.ws["box"]), (0, str(self.ws["box"] / ".agent-memory")))


class BackPointer(Fixture):
    def test_main_checkout_believes_only_what_it_can(self):
        wt = self.tree("open")
        self.assertEqual(gitdirs.main_checkout(gitdirs.dirs(wt)), self.ws["open"].resolve())
        self.assertEqual(gitdirs.main_checkout(gitdirs.dirs(self.ws["open"])), self.ws["open"].resolve(), "its own")
        direct = self.tmp / "direct"
        direct.mkdir()
        (direct / ".git").write_text(f"gitdir: {self.ws['open'] / '.git'}\n")
        self.assertIsNone(gitdirs.main_checkout(gitdirs.dirs(direct)), "a .git file naming another repo's .git")

    def test_a_real_linked_worktree_is_confirmed(self):
        wt = self.tree("open")
        self.assertTrue(gitdirs.confirmed(gitdirs.dirs(wt)))

    def test_a_relative_back_pointer_is_read_from_the_git_dir(self):
        wt = self.tree("open")
        found = gitdirs.dirs(wt)
        (found.git_dir / "gitdir").write_text(os.path.relpath(wt / ".git", found.git_dir) + "\n")
        self.assertTrue(gitdirs.confirmed(found))

    def test_what_isnt(self):
        wt = self.tree("open")
        found = gitdirs.dirs(wt)
        self.assertFalse(gitdirs.confirmed(found._replace(top=self.tmp)), "it names another folder")
        (found.git_dir / "gitdir").unlink()
        self.assertFalse(gitdirs.confirmed(found), "no back pointer")
        self.assertFalse(gitdirs.confirmed(gitdirs.Dirs(self.tmp, self.tmp / "x", found.common)), "outside the repo's worktrees")


class Guard(Fixture):
    """The guard stops a session opening a session elsewhere. A pointer must not make the sandboxed project's session another's."""

    def setUp(self):
        super().setUp()
        self.sub = self.ws["box"] / "sub"
        p = mock.patch.object(hubconf, "projects", lambda: self.docs)
        p.start()
        self.addCleanup(p.stop)

    def test_a_planted_pointer_doesnt_let_it_open_a_session_in_the_project_named(self):
        self.plant(self.sub, self.ws["box"])
        self.assertTrue(guard.decide({"cwd": str(self.ws["open"])}, self.sub))

    def test_a_pointer_that_says_nothing_doesnt_either(self):
        self.sub.mkdir()
        (self.sub / ".git").write_text("nonsense\n")
        self.assertTrue(guard.decide({"cwd": str(self.ws["open"])}, self.sub))

    def test_inside_its_own_folder_it_still_may(self):
        self.plant(self.sub, self.ws["box"])
        self.assertIsNone(guard.decide({"cwd": str(self.ws["box"] / "other")}, self.sub))

    def test_an_open_project_as_before(self):
        self.tree("open")
        self.assertTrue(guard.decide({"cwd": str(self.ws["box"])}, self.ws["open"]))


if __name__ == "__main__":
    unittest.main()
