"""A `.git` pointer a sandboxed project wrote decides nothing that runs outside its sandbox (found by the cold review of
docs/design/mailbox-session-first.md step 3).

The first attack: `box` writes `box/sub/.git` naming a git dir of its own whose `commondir` is an unsandboxed project's `.git`, and
a `box/sub/.agent-memory` repo whose config runs its script. The second cold review's: a repo of its own that confirms itself, a real
worktree of a repo nested in the project, a folder swapped for a link after the runtime took it, a folder in no project at all.
The rule these pin: a folder is the sandboxed project's as the hook's cwd names it, and git runs as the user only in a registered
project, the hub, or a confirmed worktree of a registered main that isn't sandboxed. Through the real script and entry point with
the runtime's real hook input; a scratch instance and repos only. The planted script notes whether it ran as the project
(HUB_AS_PROJECT): under the project's sandbox it may run, and that is the project's own business; as the user it must not.

    bin/run-tests        (running this module directly in a worktree reads the real config through .hub-home)
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import shutil
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


def load_hub_event():
    loader = importlib.machinery.SourceFileLoader("hub_event_pointer", str(BIN / "hub-event"))
    m = importlib.util.module_from_spec(importlib.util.spec_from_loader("hub_event_pointer", loader))
    loader.exec_module(m)
    return m


class Fixture(tbs.WorktreeFixture):
    marker = pathlib.Path("/nonexistent")

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

    def scope(self, top, main, cwd=None):
        """`hub-event worktree-scope`, as the script asks it."""
        payload = json.dumps({"session_id": self.SID, "cwd": str(cwd or top)})
        r = subprocess.run([sys.executable, str(BIN / "hub-event"), "worktree-scope", str(top), str(main)], input=payload,
                           capture_output=True, text=True, env=self.hook_env(), timeout=60)
        out = r.stdout.strip()
        return r.returncode, f"{out.split(chr(9))[1]}/.agent-memory" if chr(9) in out else out

    def handle(self, event, cwd, name, **extra):
        """A project's own hook, as the runtime fires it."""
        payload = json.dumps({"session_id": self.SID, "cwd": str(cwd), "hook_event_name": name, **extra})
        return subprocess.run([sys.executable, str(BIN / "hub-event"), "--runtime", "claude", event], cwd=cwd, input=payload,
                              capture_output=True, text=True, timeout=120, env=self.hook_env())

    def forged(self, out):
        """A worktree of a repo of the attacker's own: both ends agree, and nothing of another project's is touched."""
        fake = out / "fake"
        gd = fake / ".git" / "worktrees" / "x"
        gd.mkdir(parents=True)
        (fake / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        (gd / "commondir").write_text("../..\n")
        (gd / "HEAD").write_text("ref: refs/heads/main\n")
        (gd / "gitdir").write_text(str(out / ".git") + "\n")
        (out / ".git").write_text(f"gitdir: {gd}\n")
        self.plant_memory(out)                # the session folder's own .agent-memory
        (fake / ".agent-memory").mkdir()      # what the script asks of $main
        return fake

    def signed_log(self, mem):
        """What a `git log` in this memory repo runs: gpg.program, for a signed commit."""
        self.git("-C", mem, "config", "log.showSignature", "true")
        self.git("-C", mem, "config", "gpg.program", self.tmp / "planted.sh")
        self.git("-C", mem, "add", "memory/n.md")
        self.git("-C", mem, "commit", "-qm", "x")
        env = {**os.environ, **tbs.GIT_ENV}
        tree = subprocess.run(["git", "-C", str(mem), "rev-parse", "HEAD^{tree}"], capture_output=True, text=True, env=env).stdout.strip()
        body = (f"tree {tree}\nauthor a <a@b> 1700000000 +0000\ncommitter a <a@b> 1700000000 +0000\ngpgsig -----BEGIN PGP SIGNATURE-----\n"
                " \n xx\n -----END PGP SIGNATURE-----\n\nsigned\n")
        sha = subprocess.run(["git", "-C", str(mem), "hash-object", "-t", "commit", "-w", "--stdin"], input=body, capture_output=True,
                             text=True, env=env).stdout.strip()
        self.git("-C", mem, "update-ref", "refs/heads/agent-memory", sha)

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
        self.assertEqual(self.scope(self.sub, self.ws["open"]), (0, str(self.ws["box"] / ".agent-memory")))


class PlantedOutsideEveryProject(Fixture):
    """A sandboxed project's service can write the temp folder: there no project's path says whose it is."""

    def setUp(self):
        super().setUp()
        self.out = self.tmp / "elsewhere" / "planted"

    def test_a_forged_pointer_is_not_believed(self):
        self.plant(self.out)
        for what, name in (("start", "SessionStart"), ("end", "SessionEnd")):
            self.run_hook(what, self.out, name)
            self.assertEqual(self.ran_as_the_user(), [], name)
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

    def test_a_real_worktree_in_the_temp_folder_doesnt_run_a_memory_planted_in_it(self):
        """The worktree is real and its repo registered, but a sandboxed project's service can write there."""
        self.tree("open")
        real = self.tmp / "outside" / "real"
        real.parent.mkdir()
        self.git("-C", self.ws["open"], "worktree", "add", "-q", real, "-b", "outside")
        self.plant_memory(real)
        for what, name in (("start", "SessionStart"), ("end", "SessionEnd")):
            r = self.run_hook(what, real, name)
            self.assertEqual(self.ran_as_the_user(), [], what)
            self.assertNotIn("git worktree repair", r.stdout, "it isn't that")
        self.handle("session_start", real, "SessionStart", source="startup")
        self.assertEqual(self.ran_as_the_user(), [])

    def test_a_real_worktree_outside_its_projects_folder_goes_on(self):
        self.tree("open")
        (self.ws["open"] / "memory-file").write_text("x")
        real = self.tmp / "outside" / "real"
        real.parent.mkdir()
        self.git("-C", self.ws["open"], "worktree", "add", "-q", real, "-b", "outside")
        self.run_hook("start", real, "SessionStart")
        self.assertTrue((real / ".agent-memory").is_symlink(), "linked, as a worktree of an open project is")
        self.assertEqual(self.scope(real, self.ws["open"]), (project_git.NOT_SANDBOXED, ""), "and goes on once linked")


class RealWorktrees(Fixture):
    def test_an_open_projects_is_handled_as_before(self):
        wt = self.tree("open")
        r = self.run_hook("start", wt, "SessionStart")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((wt / ".agent-memory").is_symlink())

    def test_the_scope_of_each_kind(self):
        wt = self.tree("open")
        self.assertEqual(self.scope(wt, self.ws["open"]), (project_git.NOT_SANDBOXED, ""))
        box = self.tree("box")
        self.assertEqual(self.scope(box, self.ws["box"]), (0, str(self.ws["box"] / ".agent-memory")))
        # a folder in an open project naming a sandboxed project's repo: that repo's own config is never run as the user
        self.assertEqual(self.scope(wt, self.ws["box"]), (0, str(self.ws["box"] / ".agent-memory")))


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


class ForgedAtBothEnds(Fixture):
    """In a folder in no project the attacker owns the repo too: the back pointer proves nothing there."""

    def setUp(self):
        super().setUp()
        self.out = self.tmp / "elsewhere" / "p"
        self.out.mkdir(parents=True)

    def test_a_repo_of_its_own_confirms_itself_and_it_counts_for_nothing(self):
        fake = self.forged(self.out)
        found = gitdirs.dirs(self.out)
        self.assertTrue(gitdirs.confirmed(found), "the forgery does what it claims")
        self.assertEqual(self.scope(self.out, fake), (1, ""))

    def test_a_start_and_an_end_run_nothing_as_the_user(self):
        self.forged(self.out)
        for what, name in (("start", "SessionStart"), ("end", "SessionEnd"), ("end", "PreCompact")):
            self.run_hook(what, self.out, name, source="startup")
            self.assertEqual(self.ran_as_the_user(), [], f"{what} {name}")

    def test_with_its_memory_linked_as_a_real_worktrees_is(self):
        """What a worktree of a registered repo may have: the same shape, in a repo of its own, is still a stranger's."""
        fake = self.forged(self.out)
        shutil.rmtree(self.out / ".agent-memory")
        shutil.rmtree(fake / ".agent-memory")
        self.plant_memory(fake)
        (self.out / ".agent-memory").symlink_to(fake / ".agent-memory")
        self.assertEqual(self.scope(self.out, fake), (1, ""))
        for what, name in (("start", "SessionStart"), ("end", "SessionEnd")):
            self.run_hook(what, self.out, name, source="startup")
            self.assertEqual(self.ran_as_the_user(), [], what)

    def test_no_forging_needed_a_real_worktree_of_a_repo_nested_in_the_sandboxed_project(self):
        nested = self.ws["box"] / "nested"
        nested.mkdir()
        self.git("init", "-q", "-b", "main", nested)
        (nested / "f").write_text("x")
        self.git("-C", nested, "add", "f")
        self.git("-C", nested, "commit", "-qm", "x")
        (nested / ".agent-memory").mkdir()
        wt = self.tmp / "elsewhere" / "wt"
        self.git("-C", nested, "worktree", "add", "-q", wt, "-b", "w")
        self.assertTrue(gitdirs.confirmed(gitdirs.dirs(wt)))
        self.plant_memory(wt)
        self.assertEqual(self.scope(wt, nested), (1, ""))
        self.run_hook("start", wt, "SessionStart", source="startup")
        self.run_hook("end", wt, "SessionEnd")
        self.assertEqual(self.ran_as_the_user(), [])

    def test_nor_of_a_repo_in_a_temp_folder(self):
        repo = self.tmp / "elsewhere" / "repo"
        repo.mkdir()
        self.git("init", "-q", "-b", "main", repo)
        (repo / "f").write_text("x")
        self.git("-C", repo, "add", "f")
        self.git("-C", repo, "commit", "-qm", "x")
        (repo / ".agent-memory").mkdir()
        wt = self.tmp / "elsewhere" / "wt"
        self.git("-C", repo, "worktree", "add", "-q", wt, "-b", "w")
        self.plant_memory(wt)
        self.run_hook("start", wt, "SessionStart", source="startup")
        self.assertEqual(self.ran_as_the_user(), [])


class NudgeOutsideAProject(Fixture):
    """The reminder's `git log` runs what the repo's config names, and hub-event worktree comes before the script's scope."""

    def setUp(self):
        super().setUp()
        self.out = self.tmp / "elsewhere" / "q"
        self.plant(self.out)
        self.signed_log(self.out / ".agent-memory")

    def test_an_unconfirmed_pointer(self):
        before = len(self.ran_as_the_user())
        self.run_hook("nudge", self.out, "PostToolUse", tool_input={"command": "git commit -m x"})
        self.assertEqual(len(self.ran_as_the_user()), before)

    def test_the_project_hook_too(self):
        before = len(self.ran_as_the_user())
        self.handle("tool_used", self.out, "PostToolUse", tool_input={"command": "git commit -m x"})
        self.assertEqual(len(self.ran_as_the_user()), before)

    def test_the_old_entry_point_too(self):
        before = len(self.ran_as_the_user())
        payload = json.dumps({"session_id": self.SID, "cwd": str(self.out), "tool_input": {"command": "git commit -m x"}})
        subprocess.run([sys.executable, str(BIN / "memory-nudge.py"), "--post-tool"], cwd=self.out, input=payload,
                       capture_output=True, text=True, timeout=60, env=self.hook_env())
        subprocess.run([sys.executable, str(BIN / "memory-nudge.py"), "--session-start"], cwd=self.out,
                       capture_output=True, text=True, timeout=60, env=self.hook_env())
        self.assertEqual(len(self.ran_as_the_user()), before)


class SwappedFolder(Fixture):
    """The route read from the CLI: `cd box/sub` stores that string, then a background process swaps sub for a link. A hook's input
    still says box/sub; what it resolves to is whatever the project pointed it at."""

    def setUp(self):
        super().setUp()
        self.sub = self.ws["box"] / "sub"

    def link(self, target):
        self.sub.symlink_to(target)

    def plain(self):
        out = self.tmp / "plain"
        out.mkdir()
        self.git("init", "-q", "-b", "main", out)
        self.plant_memory(out)
        return out

    def test_to_a_forged_worktree_the_user_level_hooks_run_nothing_as_the_user(self):
        out = self.tmp / "elsewhere" / "p"
        out.mkdir(parents=True)
        self.forged(out)
        self.link(out)
        for what, name in (("start", "SessionStart"), ("end", "PreCompact"), ("nudge", "PostToolUse")):
            self.run_hook(what, self.sub, name, source="compact", tool_input={"command": "git commit -m x"})
            self.assertEqual(self.ran_as_the_user(), [], what)

    def test_to_a_plain_repo_the_projects_hooks_run_nothing_as_the_user(self):
        self.link(self.plain())
        for event, name, extra in (("session_start", "SessionStart", {"source": "compact"}), ("pre_compact", "PreCompact", {}),
                                   ("session_end", "SessionEnd", {"reason": "other"}),
                                   ("tool_used", "PostToolUse", {"tool_input": {"command": "git commit -m x"}})):
            self.handle(event, self.sub, name, **extra)
            self.assertEqual(self.ran_as_the_user(), [], event)

    def test_a_folder_its_workspace_doesnt_hold_gets_no_work_at_all(self):
        """Not as the user, and not as the project either: nothing of that folder is its project's."""
        self.link(self.plain())
        for event, name, extra in (("session_start", "SessionStart", {"source": "compact"}), ("pre_compact", "PreCompact", {})):
            self.handle(event, self.sub, name, **extra)
        self.assertFalse(self.marker.exists())

    def test_a_plain_repo_in_no_project_runs_nothing_as_the_user_either(self):
        out = self.plain()
        for event, name, extra in (("session_start", "SessionStart", {"source": "compact"}), ("pre_compact", "PreCompact", {}),
                                   ("session_end", "SessionEnd", {"reason": "other"})):
            self.handle(event, out, name, **extra)
            self.assertEqual(self.ran_as_the_user(), [], event)

    def test_to_the_open_project_its_letter_isnt_delivered(self):
        self.tree("open")
        self.letter_to("open", "for the open project", typ="request")
        self.link(self.ws["open"])
        r = self.handle("prompt", self.sub, "UserPromptSubmit", prompt="x")
        self.assertNotIn("for the open project", r.stdout)
        self.assertEqual(self.rows("type='message.delivered'"), [])

    def test_through_the_worktree_hook_neither(self):
        wt = self.tree("open")
        self.letter_to("open", "for the open project", typ="request")
        self.link(wt)
        r = self.run_hook("prompt", self.sub, "UserPromptSubmit", prompt="x")
        self.assertNotIn("for the open project", r.stdout)
        self.assertEqual(self.rows("type='message.delivered'"), [])

    def test_the_record_stays_the_sandboxed_projects(self):
        self.tree("open")
        self.link(self.ws["open"])
        self.handle("prompt", self.sub, "UserPromptSubmit", prompt="x")
        rec = json.loads((self.tmp / "secrets" / "sessions" / f"{self.SID}.live").read_text())
        self.assertEqual(rec["project"], "box", "routing and the send-time gate read this")

    def test_the_last_reply_goes_by_the_sandboxed_projects_label(self):
        self.docs["box"]["privacy"] = {"reflection": "stats_only"}
        (self.tmp / "state" / "shared" / "registry" / "index.json").write_text(json.dumps({"projects": self.docs}))
        self.tree("open")
        self.link(self.ws["open"])
        d = self.tmp / "secrets" / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{self.SID}.wake-0").write_text("wake")
        payload = json.dumps({"session_id": self.SID, "cwd": str(self.sub), "last_assistant_message": "said this"})
        subprocess.run([sys.executable, str(BIN / "hub-event"), "--runtime", "claude", "wait"], cwd=self.sub, input=payload,
                       capture_output=True, text=True, timeout=60, env=self.hook_env())
        self.assertFalse((d / f"{self.SID}.last").exists())

    def test_to_another_sandboxed_project_nothing_of_it_is_printed_here(self):
        self.add_box("zbox")
        wt = self.tree("zbox")
        (self.ws["zbox"] / "CLAUDE.local.md").write_text("THE OTHER PROJECT'S RULES\n")
        self.link(wt)
        r = self.run_hook("start", self.sub, "SessionStart", source="compact")
        self.assertNotIn("THE OTHER PROJECT'S RULES", r.stdout)

    def test_the_scope_names_the_projects_own_memory(self):
        out = self.plain()
        self.link(out)
        self.assertEqual(self.scope(out, out, cwd=self.sub), (0, str(self.ws["box"] / ".agent-memory")))


class AScriptInTheFolder(Fixture):
    def test_inline_python_doesnt_import_from_the_folder(self):
        """hub_json_get's fallback, where there is no plutil: -c puts the current folder on the import path."""
        out = self.tmp / "elsewhere" / "py"
        out.mkdir(parents=True)
        self.marker = self.tmp / "RAN"
        (out / "json.py").write_text(f"open({str(self.marker)!r}, 'a').write('as-project=\\n')\nraise SystemExit(3)\n")
        script = f'command() {{ return 1; }}; source "{BIN / "hubpaths.sh"}"; hub_json_get hosts.server'
        subprocess.run(["bash", "-c", script], cwd=out, capture_output=True, text=True, timeout=30, env=self.hook_env())
        self.assertFalse(self.marker.exists())

    def test_the_reminder_doesnt_print_a_file_its_marker_links_to(self):
        old = self.tmp / "laptop" / "copy"
        old.mkdir(parents=True)
        self.git("init", "-q", "-b", "main", old)
        self.git("init", "-q", "-b", "agent-memory", old / ".agent-memory")
        secret = self.tmp / "secret-file"
        secret.write_text("the secret\t2026-10-15\n")
        (old / ".git" / "agent-hub-migrated-to-mini").symlink_to(secret)
        r = self.handle("session_start", old, "SessionStart", source="startup")
        self.assertNotIn("the secret", r.stdout)


class TheMovedFolderReminder(Fixture):
    """A laptop's old copy, in no project: it keeps its reminder, and no git runs."""

    def setUp(self):
        super().setUp()
        self.old = self.tmp / "laptop" / "oldcopy"
        self.old.mkdir(parents=True)
        self.git("init", "-q", "-b", "main", self.old)
        self.plant_memory(self.old)
        (self.old / ".git" / "agent-hub-migrated-to-mini").write_text("mini:/x\t2026-10-15\n")

    def test_it_is_said_and_nothing_runs(self):
        r = self.handle("session_start", self.old, "SessionStart", source="startup")
        self.assertIn("mini:/x", r.stdout)
        self.assertIn("2026-10-15", r.stdout)
        self.assertEqual(self.ran_as_the_user(), [])

    def test_a_copy_with_no_marker_says_nothing(self):
        (self.old / ".git" / "agent-hub-migrated-to-mini").unlink()
        r = self.handle("session_start", self.old, "SessionStart", source="startup")
        self.assertEqual(r.stdout.strip(), "")
        self.assertEqual(self.ran_as_the_user(), [])


class AWorktreeMovedByHand(Fixture):
    """Off, and it says why (AGENTS.md, Tool neutrality)."""

    def setUp(self):
        super().setUp()
        wt = self.tree("open")
        self.moved = self.tmp / "moved"
        wt.rename(self.moved)

    def test_a_start_says_how_to_repair_it(self):
        r = self.run_hook("start", self.moved, "SessionStart", source="startup")
        self.assertIn("git worktree repair", r.stdout)
        self.assertIn(str(self.ws["open"]), r.stdout)

    def test_the_others_say_nothing_and_run_nothing(self):
        for what, name in (("prompt", "UserPromptSubmit"), ("nudge", "PostToolUse"), ("end", "SessionEnd")):
            r = self.run_hook(what, self.moved, name)
            self.assertNotIn("git worktree repair", r.stdout, what)
        self.assertFalse((self.moved / ".agent-memory").exists())

    def test_the_repair_brings_it_back(self):
        self.git("-C", self.ws["open"], "worktree", "repair", self.moved)
        self.assertEqual(self.scope(self.moved, self.ws["open"]), (project_git.NOT_SANDBOXED, ""))

    def test_moved_inside_its_project_it_goes_on(self):
        inside = self.ws["open"] / "moved-here"
        self.moved.rename(inside)
        self.assertEqual(self.scope(inside, self.ws["open"]), (project_git.NOT_SANDBOXED, ""))


class ByTheFileNotTheSpelling(Fixture):
    """The name of the folder, however it is written, and the nearest of several."""

    def setUp(self):
        super().setUp()
        self.sub = self.ws["box"] / "sub"
        self.sub.mkdir()
        p = mock.patch.object(hubconf, "projects", lambda: self.docs)
        p.start()
        self.addCleanup(p.stop)

    def spellings(self):
        box = str(self.ws["box"])
        out = [f"/{box}/sub", f"{self.ws['open']}/../box/sub"]
        if pathlib.Path(box.upper()).exists() and pathlib.Path(box.upper()).samefile(box):   # a case-insensitive disk
            out.append(f"{box.upper()}/sub")
        return out

    def test_each_is_the_project(self):
        for s in self.spellings():
            self.assertEqual((project_git.sandbox_of(s) or [None])[0], "box", s)

    def test_a_link_swapped_in_doesnt_change_it(self):
        self.sub.rmdir()
        self.sub.symlink_to(self.ws["open"])
        for s in self.spellings():
            self.assertEqual((project_git.sandbox_of(s) or [None])[0], "box", s)

    def test_the_letter_of_the_project_it_links_to_isnt_delivered(self):
        self.sub.rmdir()
        self.sub.symlink_to(self.ws["open"])
        self.letter_to("open", "for the open project", typ="request")
        for s in self.spellings():
            payload = json.dumps({"session_id": self.SID, "cwd": s, "hook_event_name": "UserPromptSubmit", "prompt": "x"})
            r = subprocess.run([sys.executable, str(BIN / "hub-event"), "--runtime", "claude", "prompt"], cwd=self.ws["box"], input=payload,
                               capture_output=True, text=True, timeout=60, env=self.hook_env())
            self.assertNotIn("for the open project", r.stdout, s)
        self.assertEqual(self.rows("type='message.delivered'"), [])

    def test_a_link_from_outside_into_the_project_is_the_project(self):
        out = self.tmp / "link-in"
        out.symlink_to(self.sub)
        self.assertEqual((project_git.sandbox_of(out) or [None])[0], "box")
        self.assertEqual(self.mail_project(out), "box")

    def mail_project(self, cwd):
        hb = tbs.load_hubbus()
        return hb.project_of(pathlib.Path(cwd))

    def test_the_nearest_of_two_nested_and_everyone_agrees(self):
        inner = self.ws["box"] / "inner"
        inner.mkdir()
        for order in (("inner", "box"), ("box", "inner")):
            docs = {"inner": {"v": 1, "name": "inner", "paths": {"workspace": str(inner)}, "enforce": {"sandbox": True}},
                    "box": self.docs["box"]}
            docs = {k: docs[k] for k in order}
            with mock.patch.object(hubconf, "projects", lambda docs=docs: docs):
                self.assertEqual(project_git.sandbox_of(inner / "x")[0], "inner", order)
                self.assertEqual(guard.own_project(inner / "x")[0], "inner", order)
                self.assertEqual(project_git.sandbox_of(self.sub)[0], "box", order)
                self.assertEqual(self.mail_project(inner), "inner", order)

    def test_a_hook_input_with_no_folder_is_no_session(self):
        import runtime_claude
        self.assertIsNone(runtime_claude.parse_event("prompt", json.dumps({"session_id": "s"})), "not this process's own folder")


class OneAnswer(Fixture):
    """Which project a session is in: mail and the session's record ask one function (hubbus.project_of), so a session that gets a
    project's letters can be counted and woken for them, and nothing else can."""

    def answers(self, folder):
        he = load_hub_event()
        with mock.patch.object(hubconf, "projects", lambda: self.docs):
            return tbs.load_hubbus().project_of(pathlib.Path(folder)), he._project_of_folder(str(folder))

    def outside(self, project):
        """A real linked worktree beside its project's folder, under workspace_root."""
        self.tree(project)
        out = self.tmp / "ws" / f"{project}-beside"
        self.git("-C", self.ws[project], "worktree", "add", "-q", out, "-b", f"{project}-beside")
        return out

    def test_a_worktree_beside_an_open_project_counts_as_it_for_both(self):
        self.assertEqual(self.answers(self.outside("open")), ("open", "open"))

    def test_through_the_real_hook_it_goes_on_record_under_that_project(self):
        out = self.outside("open")
        r = self.run_hook("start", out, "SessionStart")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(self.record().read_text())["project"], "open")

    def test_a_worktree_beside_a_sandboxed_project_counts_for_neither(self):
        out = self.outside("box")
        with mock.patch.object(hubconf, "projects", lambda: self.docs):
            self.assertTrue(gitdirs.confirmed(gitdirs.dirs(out)), "it does confirm itself: that proves nothing")
        self.assertEqual(self.answers(out), (None, None))

    def test_a_planted_folder_claiming_a_sandboxed_repo_counts_for_neither(self):
        box = self.ws["box"]
        (box / ".git" / "worktrees" / "x").mkdir(parents=True)
        folder = self.tmp / "planted"
        folder.mkdir()
        (box / ".git" / "worktrees" / "x" / "commondir").write_text("../..\n")
        (box / ".git" / "worktrees" / "x" / "gitdir").write_text(str(folder / ".git") + "\n")
        (folder / ".git").write_text(f"gitdir: {box}/.git/worktrees/x\n")
        self.assertEqual(self.answers(folder), (None, None))

    def test_a_folder_of_a_sandboxed_project_never_counts_as_the_repo_it_claims(self):
        sub = self.ws["box"] / "sub"
        self.plant(sub)
        self.assertEqual(self.answers(sub), ("box", "box"))

    def test_the_main_checkout_and_a_worktree_inside_it_are_as_before(self):
        wt = self.tree("open")
        for folder in (self.ws["open"], wt):
            self.assertEqual(self.answers(folder), ("open", "open"), folder)
        self.assertEqual(self.answers(self.ws["box"]), ("box", "box"))


class EachLineOfTheRule(Fixture):
    """What the rule's other lines hold, each of which an earlier check doesn't."""

    def setUp(self):
        super().setUp()
        self.sub = self.ws["box"] / "sub"

    def test_git_as_the_user_is_no_in_a_sandboxed_projects_folder(self):
        with mock.patch.object(hubconf, "projects", lambda: self.docs):
            self.assertFalse(project_git.user_git_ok(self.ws["box"]), "memory-nudge.py asks it directly")
            self.assertFalse(project_git.folder_git_ok(self.ws["box"]))
            self.assertTrue(project_git.user_git_ok(self.ws["open"]))
            self.assertTrue(project_git.folder_git_ok(self.ws["open"]))

    def test_a_sandboxed_project_is_no_registered_main(self):
        wt = self.tree("box")
        out = self.tmp / "out"
        wt.rename(out)
        self.git("-C", self.ws["box"], "worktree", "repair", out)
        with mock.patch.object(hubconf, "projects", lambda: self.docs):
            self.assertTrue(gitdirs.confirmed(gitdirs.dirs(out)))
            self.assertFalse(project_git.registered_main(self.ws["box"]))
            self.assertFalse(project_git.user_git_ok(out))
            self.assertTrue(project_git.registered_main(self.ws["open"]))

    def test_a_worktree_out_in_the_temp_folder_gets_git_in_its_memory_only(self):
        self.tree("open")
        out = self.tmp / "out"
        self.git("-C", self.ws["open"], "worktree", "add", "-q", out, "-b", "o")
        with mock.patch.object(hubconf, "projects", lambda: self.docs):
            self.assertTrue(project_git.user_git_ok(out))
            self.assertFalse(project_git.folder_git_ok(out))
            self.assertEqual(project_git.memory_dir(out), self.ws["open"].resolve() / ".agent-memory")
            self.assertEqual(project_git.memory_dir(self.ws["open"]), self.ws["open"] / ".agent-memory")

    def behind_at(self, wt):
        """A real worktree of the open project at wt, its memory notes behind, linked as `start` links it."""
        self.tree("open")
        ws, env = self.ws["open"], {**os.environ, **tbs.GIT_ENV}
        mem = ws / ".agent-memory"
        (mem / "memory").mkdir()
        (mem / "AGENT.md").write_text("x")
        old = {**env, "GIT_AUTHOR_DATE": "2020-01-01T00:00:00", "GIT_COMMITTER_DATE": "2020-01-01T00:00:00"}
        for args, e in ((("init", "-q", "-b", "agent-memory"), env), (("add", "AGENT.md"), env), (("commit", "-qm", "x"), old)):
            subprocess.run(["git", "-C", str(mem), *args], check=True, env=e)
        (ws / "g").write_text("y")
        self.git("-C", ws, "add", "g")
        self.git("-C", ws, "commit", "-qm", "feat: more")
        wt.parent.mkdir(parents=True, exist_ok=True)
        self.git("-C", ws, "worktree", "add", "-q", wt, "-b", "out", "main")
        (wt / ".agent-memory").symlink_to(mem)
        return wt

    def reminder(self, cwd, how="hook"):
        extra = {"tool_input": {"command": "git commit -m x"}}
        if how == "hook":
            return ".agent-memory/memory/" in self.handle("tool_used", cwd, "PostToolUse", **extra).stdout
        return ".agent-memory/memory/" in self.run_hook("nudge", cwd, "PostToolUse", **extra).stdout

    def clear_rate_limit(self):
        (self.tmp / "state" / "hubbus-post-tool" / self.SID).unlink(missing_ok=True)

    def last_log(self):
        """This worktree's own line: bin/run-tests runs modules side by side, and they all log to this file. The logs
        follow a state of the hook's own, as hubconf.LOGS does."""
        import names
        state = names.env("STATE", environ=self.hook_env())
        logs = pathlib.Path(state) / "logs" if state else BIN.parent / ".logs"
        lines = (logs / "hub-event.log").read_text().splitlines()
        return [x for x in lines if " session_start open-side " in x][-1]

    def test_a_worktree_under_workspace_root_gets_the_reminder_and_local_setup(self):
        wt = self.behind_at(self.ws["open"].parent / "open-side")
        self.assertTrue(self.reminder(wt))
        self.clear_rate_limit()
        self.assertTrue(self.reminder(wt, "script"))
        self.handle("session_start", wt, "SessionStart", source="startup")
        self.assertIn("local_setup", self.last_log())
        self.assertIn("nudge", self.last_log())

    def test_the_same_worktree_in_the_temp_folder_doesnt(self):
        wt = self.behind_at(self.tmp / "elsewhere" / "open-side")
        self.assertFalse(self.reminder(wt))
        self.clear_rate_limit()
        self.assertFalse(self.reminder(wt, "script"))
        self.handle("session_start", wt, "SessionStart", source="startup")
        self.assertNotIn("local_setup", self.last_log())
        self.assertIn("memory_pull", self.last_log(), "its main checkout's memory still goes")

    def test_a_link_in_a_sandboxed_workspace_pointing_at_one_doesnt(self):
        wt = self.behind_at(self.ws["open"].parent / "open-side")
        self.sub.symlink_to(wt)
        self.assertFalse(self.reminder(self.sub))
        self.clear_rate_limit()
        self.assertFalse(self.reminder(self.sub, "script"))
        with mock.patch.object(hubconf, "projects", lambda: self.docs):
            self.assertFalse(project_git.folder_git_ok(self.sub))

    def under_root(self, wt):
        """folder_git_ok in this process, with this instance's registry and workspace_root."""
        real = hubconf.p
        return (mock.patch.object(hubconf, "projects", lambda: self.docs),
                mock.patch.object(hubconf, "p", lambda k, d=None: self.ws["open"].parent if k == "workspace_root" else real(k, d)))

    def test_a_worktree_in_a_sandboxed_workspace_or_what_it_may_write_doesnt(self):
        self.tree("open")
        inside = self.ws["box"] / "open-side"
        self.git("-C", self.ws["open"], "worktree", "add", "-q", inside, "-b", "in-box")
        allowed = self.ws["open"].parent / "shared"
        allowed.mkdir()
        beside = allowed / "open-side"
        self.git("-C", self.ws["open"], "worktree", "add", "-q", beside, "-b", "beside")
        a, b = self.under_root(inside)
        with a, b:
            self.assertFalse(project_git.folder_git_ok(inside))
            self.assertTrue(project_git.folder_git_ok(beside), "nothing sandboxed writes there yet")
            self.docs["box"]["allow_write"] = [str(allowed)]
            self.assertFalse(project_git.folder_git_ok(beside), "the owner let a sandbox write there")

    def test_under_its_main_checkouts_folder_is_the_hubs_case_too(self):
        """A confirmed worktree in no project's workspace, under its main: the hub's are, whose entry has no workspace."""
        wt = self.tree("open")
        docs = {n: d for n, d in self.docs.items() if n != "open"}
        with mock.patch.object(hubconf, "projects", lambda: docs), mock.patch.object(hubconf, "MAIN", self.ws["open"]):
            self.assertTrue(project_git.folder_git_ok(wt))

    def test_a_memory_link_flipped_beside_a_real_worktree_isnt_followed(self):
        """Planted after any look: what runs is the main checkout's, by name."""
        self.tree("open")
        mem = self.ws["open"] / ".agent-memory"
        self.git("init", "-q", "-b", "agent-memory", mem)
        out = self.tmp / "out"
        self.git("-C", self.ws["open"], "worktree", "add", "-q", out, "-b", "o")
        planted = self.tmp / "planted"
        planted.mkdir()
        self.plant_memory(planted)
        (out / ".agent-memory").symlink_to(planted / ".agent-memory")
        for what, name in (("start", "SessionStart"), ("end", "SessionEnd")):
            self.run_hook(what, out, name)
            self.assertEqual(self.ran_as_the_user(), [], what)
        self.handle("session_start", out, "SessionStart", source="startup")
        self.handle("pre_compact", out, "PreCompact")
        self.assertEqual(self.ran_as_the_user(), [])

    def test_the_scope_prints_the_memory_as_it_resolves(self):
        """The script compares it with the top it found, which is resolved: a project registered through a link still runs."""
        real = self.tmp / "real-box"
        self.ws["box"].rename(real)
        self.ws["box"].symlink_to(real)
        self.assertEqual(self.scope(real, real), (0, str(real / ".agent-memory")))

    def swap_to_a_repo_with_a_signed_log(self):
        out = self.tmp / "plain"
        out.mkdir()
        self.git("init", "-q", "-b", "main", out)
        self.plant_memory(out)
        self.signed_log(out / ".agent-memory")
        self.sub.symlink_to(out)
        self.marker.unlink(missing_ok=True)

    def test_the_reminder_in_a_folder_not_held_doesnt_run_even_as_the_project(self):
        self.swap_to_a_repo_with_a_signed_log()
        self.run_hook("nudge", self.sub, "PostToolUse", tool_input={"command": "git commit -m x"})
        self.assertFalse(self.marker.exists())
        m = load_hub_event()
        ev = m.runtime_ports.HubEvent("tool_used", self.SID, str(self.sub), "git commit -m x")
        with mock.patch.object(hubconf, "projects", lambda: self.docs), \
                mock.patch.object(m, "_as_project", side_effect=AssertionError("ran as the project")):
            self.assertIsNone(m._worktree_nudge(ev, self.sub.resolve(), project_git.sandbox_of(self.sub)))

    def test_the_memory_sync_in_a_folder_not_held_doesnt_run_even_as_the_project(self):
        m = load_hub_event()
        m.LOG = self.tmp / "hub-event.log"
        (self.tmp / "plain").mkdir()
        self.sub.symlink_to(self.tmp / "plain")
        (self.tmp / "plain" / ".git").mkdir()
        (self.tmp / "plain" / ".agent-memory" / ".git").mkdir(parents=True)
        import runtime_claude
        raw = json.dumps({"session_id": self.SID, "cwd": str(self.sub), "hook_event_name": "PreCompact"})
        with mock.patch.object(hubconf, "projects", lambda: self.docs), \
                mock.patch.object(m, "_memory_sync_as_project", side_effect=AssertionError("ran")) as sync:
            m.handle("pre_compact", raw, runtime_claude, "claude")
        self.assertEqual(sync.call_count, 0)

    def test_a_sandboxed_folder_swapped_to_an_old_copy_doesnt_say_its_reminder(self):
        old = self.tmp / "oldcopy"
        old.mkdir()
        self.git("init", "-q", "-b", "main", old)
        self.git("init", "-q", "-b", "agent-memory", old / ".agent-memory")
        (old / ".git" / "agent-hub-migrated-to-mini").write_text("mini:/x\t2026-10-15\n")
        self.sub.symlink_to(old)
        r = self.handle("session_start", self.sub, "SessionStart", source="compact")
        self.assertNotIn("mini:/x", r.stdout)


class OneLook(Fixture):
    """What a hook does in a real worktree out in the temp folder comes from one look at its pointers."""

    def setUp(self):
        super().setUp()
        self.tree("open")
        self.out = self.tmp / "out"
        self.git("-C", self.ws["open"], "worktree", "add", "-q", self.out, "-b", "o")
        self.planted = self.tmp / "planted"
        self.planted.mkdir()
        self.plant_memory(self.planted)
        (self.out / ".agent-memory").symlink_to(self.planted / ".agent-memory")
        p = mock.patch.object(hubconf, "projects", lambda: self.docs)
        p.start()
        self.addCleanup(p.stop)

    def test_scope_of_reads_the_pointers_once(self):
        with mock.patch.object(project_git.gitdirs, "dirs", wraps=gitdirs.dirs) as look:
            scope = project_git.scope_of(self.out)
        self.assertEqual(look.call_count, 1)
        self.assertEqual(scope.memdir, self.ws["open"].resolve() / ".agent-memory")

    def test_a_git_file_rewritten_after_the_look_doesnt_move_the_memory(self):
        m = load_hub_event()
        m.LOG = self.tmp / "hub-event.log"
        real, pointer = project_git.scope_of, (self.out / ".git")

        def look_then_rewrite(folder):
            scope = real(folder)
            pointer.write_text("gitdir: /nowhere\n")
            return scope
        ran = []
        import runtime_claude
        raw = json.dumps({"session_id": self.SID, "cwd": str(self.out), "hook_event_name": "PreCompact"})
        with mock.patch.object(project_git, "scope_of", look_then_rewrite), \
                mock.patch.object(m.subprocess, "run", lambda argv, **k: ran.append(k.get("cwd")) or subprocess.CompletedProcess(argv, 0, "", "")):
            (self.ws["open"] / ".agent-memory" / ".git").mkdir(exist_ok=True)
            m.handle("pre_compact", raw, runtime_claude, "claude")
        self.assertEqual(ran, [self.ws["open"].resolve() / ".agent-memory"])

    def test_a_main_read_from_a_pointer_since_rewritten_isnt_believed(self):
        """The script read main a moment ago; by the time it asks, the pointer is real again and the top passes."""
        self.assertEqual(self.scope(self.out, self.planted)[0], 1)
        self.assertEqual(self.scope(self.out, self.ws["open"])[0], project_git.NOT_SANDBOXED)

    def test_allow_write_counts_before_the_project_does(self):
        """A sandboxed project's allow_write naming a folder in an open project: that folder isn't only its owner's."""
        shared = self.ws["open"] / "shared"
        shared.mkdir()
        self.assertTrue(project_git.folder_git_ok(shared))
        self.docs["box"]["allow_write"] = [str(shared)]
        self.assertFalse(project_git.folder_git_ok(shared))

    def test_what_its_runtime_folder_holds_doesnt_count_either(self):
        """~/services/<project>: a sandboxed project writes it, wherever services_root is."""
        services = self.ws["open"].parent / "svc"
        wt = services / "box" / "side"
        wt.parent.mkdir(parents=True)
        self.git("-C", self.ws["open"], "worktree", "add", "-q", wt, "-b", "under-services")
        self.docs["box"]["runtime"] = {"test": "x"}
        real = hubconf.p
        with mock.patch.object(hubconf, "p", lambda k, d=None: services if k == "services_root" else self.ws["open"].parent if k == "workspace_root" else real(k, d)):
            self.assertFalse(project_git.folder_git_ok(wt))
            del self.docs["box"]["runtime"]
            self.assertTrue(project_git.folder_git_ok(wt), "with no runtime nothing sandboxed writes there")


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

    def test_swapped_for_a_link_to_the_open_project_it_still_cant(self):
        self.tree("open")
        self.sub.symlink_to(self.ws["open"])
        self.assertTrue(guard.decide({"cwd": str(self.ws["open"])}, self.sub))
        self.assertEqual(guard.own_project(self.sub)[0], "box")

    def test_swapped_for_a_link_to_a_plain_folder_it_still_cant(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        self.sub.symlink_to(plain)
        self.assertTrue(guard.decide({"cwd": str(self.ws["open"])}, self.sub))
        self.assertTrue(guard.decide({"elsewhere": {"kind": "machine"}}, self.sub))

    def test_from_a_planted_folder_outside_every_project_nothing_opens(self):
        """Before the pointer was doubted it denied all but the project named; doubted and not refused, it allowed everything."""
        out = self.tmp / "elsewhere" / "p"
        self.plant(out)
        self.assertTrue(guard.decide({"cwd": str(self.ws["box"])}, out))
        self.assertTrue(guard.decide({"cwd": str(self.ws["open"])}, out), "nor the project it names")
        self.assertTrue(guard.decide({"elsewhere": {"kind": "machine"}}, out))

    def test_a_worktree_moved_by_hand_is_told_how_to_repair_it(self):
        wt = self.tree("open")
        moved = self.tmp / "moved"
        wt.rename(moved)
        self.assertIn("git worktree repair", guard.decide({"cwd": str(self.ws["box"])}, moved))

    def test_a_real_worktree_of_an_open_project_as_before(self):
        wt = self.tree("open")
        self.assertEqual(guard.own_project(wt)[0], "open")
        self.assertIsNone(guard.decide({"cwd": str(wt)}, wt))

    def test_a_worktree_moved_inside_its_project_is_still_that_projects(self):
        wt = self.tree("open")
        inside = self.ws["open"] / "moved-here"
        wt.rename(inside)
        self.assertEqual(guard.own_project(inside), ("open", self.ws["open"].resolve()))
        self.assertTrue(guard.decide({"cwd": str(self.ws["box"])}, inside))

    def test_a_folder_with_no_repo_in_no_project_as_before(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertIsNone(guard.decide({"cwd": str(self.ws["open"])}, plain))


if __name__ == "__main__":
    unittest.main()
