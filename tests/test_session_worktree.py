"""The owner's checkout race (docs/design/git-broker.md, r3 B-1): any process of a sandboxed project can swap a tracked
folder for a symbolic link while someone else's unsandboxed checkout writes there, and the checkout then writes the
project's files wherever the link points, as the owner. What is built (mitigation 3, 11-14): a coding agent's own
worktree mode hands the worktree to the platform, which makes it as the project (session_worktree.py, `hub-event
worktree-make`; Claude Code's WorktreeCreate hook). The link rule for services and jobs (mitigation 2) was removed by
the owner on 11-15: a folder holding a link could still be moved in, so it was friction, not a boundary. B1Open says
what is true without it.

    bin/run-tests tests   (or: brunna test)
"""
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
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import isolation  # noqa: E402
import policy  # noqa: E402
import runtime_claude  # noqa: E402
import seatbelt  # noqa: E402

ENV = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, "GIT_CONFIG_GLOBAL": "/dev/null",
       "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t"}
HAS_SEATBELT = seatbelt.can_sandbox() is None


def git(cwd, *args, **kw) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], env=ENV, capture_output=True, text=True, check=True,
                          **kw).stdout


def registered(repo) -> list[str]:
    """The worktrees git lists for repo, by path (never a substring of the listing: the temp folder's random name can
    hold a worktree's name; it did on CI, 11-15)."""
    return [line[len("worktree "):] for line in git(repo, "worktree", "list", "--porcelain").splitlines()
            if line.startswith("worktree ")]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = self.tmp / "ws"
        self.ws.mkdir()
        self.rt = self.tmp / "runtime"
        self.rt.mkdir()

    def pol(self):
        return {"workspace": self.ws, "write": [], "other_projects": [], "deny_read": [], "credentials": [],
                "credential_files": [], "protect": [], "protect_roots": []}

    def run_in(self, script, pol=None, cwd=None, env=None):
        prof = seatbelt.service_profile(pol or self.pol(), [self.ws, self.rt])
        return subprocess.run([seatbelt.SANDBOX_EXEC, "-p", prof, "/bin/sh", "-c", script], cwd=cwd or self.ws,
                              capture_output=True, text=True, env=env or ENV)


@unittest.skipUnless(HAS_SEATBELT, "needs Seatbelt")
class B1Open(Base):
    """What is true since the owner removed the link rule (11-15): a sandboxed project's services and jobs can put a link
    at a tracked folder's place, directly or as a folder holding one, so B-1 is open for them as for sessions. When this
    fails, something refuses links again: say so in docs/design/git-broker.md."""

    def test_services_and_jobs_can_put_a_link_at_a_tracked_folders_place(self):
        (self.ws / "d").mkdir()
        (self.ws / "e").mkdir()
        r = self.run_in(f"""
mv d d.x && ln -s {self.tmp} d && [ -L d ] && echo link-made
mkdir -p h && ln -s {self.tmp} h/sub && mv e e.x && mv h e && [ -L e/sub ] && echo folder-with-link-moved-in
""")
        self.assertEqual(r.stdout.split(), ["link-made", "folder-with-link-moved-in"], r.stderr)


class WorktreeHook(Base):
    """Claude Code's WorktreeCreate / WorktreeRemove contract, as read from its binary (2.1.x): input {hook_event_name,
    cwd, name} / {…, worktree_path}; the path printed as the last line of stdout; a non-zero exit fails the worktree."""

    def test_the_hooks_are_in_a_sandboxed_projects_settings_and_fail_closed(self):
        hooks = runtime_claude.platform_hooks()
        for event, what in (("WorktreeCreate", "worktree-make"), ("WorktreeRemove", "worktree-drop")):
            cmd = hooks[event][0]["hooks"][0]["command"]
            self.assertIn(f"--runtime claude {what}", cmd)
            self.assertTrue(cmd.endswith("exit 1"), "never made as the user when the platform can't")
            self.assertNotIn("|| true", cmd)
            self.assertTrue(runtime_claude.MANAGED.search(cmd), "the platform's own: replaced, never kept as the user's")

    def test_the_request_and_the_reply(self):
        mk = runtime_claude.worktree_request(json.dumps({"hook_event_name": "WorktreeCreate", "cwd": "/w", "name": "x",
                                                         "session_id": "s"}))
        self.assertEqual(mk, {"make": True, "cwd": "/w", "name": "x"})
        rm = runtime_claude.worktree_request(json.dumps({"hook_event_name": "WorktreeRemove", "cwd": "/w",
                                                         "worktree_path": "/w/.claude/worktrees/x"}))
        self.assertEqual(rm, {"make": False, "cwd": "/w", "path": "/w/.claude/worktrees/x"})
        for raw in ("", "nope", json.dumps({"hook_event_name": "Stop", "cwd": "/w"}),
                    json.dumps({"hook_event_name": "WorktreeCreate", "cwd": "/w"})):
            self.assertIsNone(runtime_claude.worktree_request(raw))
        self.assertEqual(runtime_claude.worktree_location(pathlib.Path("/w"), "x"),
                         (pathlib.Path("/w/.claude/worktrees/x"), "worktree-x"))
        self.assertEqual(runtime_claude.worktree_reply(pathlib.Path("/w/.claude/worktrees/x")),
                         "/w/.claude/worktrees/x")

    def test_makes_worktrees_reads_the_settings_file(self):
        self.assertFalse(runtime_claude.makes_worktrees(self.ws))
        p = runtime_claude.settings_path(self.ws)
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"hooks": runtime_claude.worktree_maker_hooks()}))
        self.assertTrue(runtime_claude.makes_worktrees(self.ws))


class SessionWorktree(Base):
    """session_worktree.make / drop through project_git as the project. `wrap` is the real Seatbelt service profile
    (macOS), or a recorder that refuses like sandboxed-run where nothing can sandbox."""

    def setUp(self):
        super().setUp()
        git(self.ws, "init", "-q", "-b", "main")
        (self.ws / "src").mkdir()
        (self.ws / "src" / "a").write_text("a\n")
        os.symlink("a", self.ws / "src" / "l")
        (self.ws / ".ctl").mkdir()
        (self.ws / ".ctl" / "settings").write_text("{}\n")
        git(self.ws, "add", ".")
        git(self.ws, "commit", "-qm", "init")
        self.docs = {"proj": {"name": "proj", "paths": {"workspace": str(self.ws)}, "enforce": {"sandbox": True}}}
        self.wrapped = []
        for p in (mock.patch.object(hubconf, "projects", lambda: self.docs),
                  mock.patch.object(isolation, "wrap", self.wrap),
                  mock.patch.object(policy, "protect", lambda: [{"name": ".ctl", "tree": True}]),
                  mock.patch.object(policy, "protect_roots", lambda: [])):
            p.start()
            self.addCleanup(p.stop)
        import session_worktree
        p = mock.patch.object(session_worktree, "PENDING", self.tmp / "pending")
        p.start()
        self.addCleanup(p.stop)

    def wrap(self, project, cwd, argv, port=None):
        self.wrapped.append((project, list(argv)))
        if not HAS_SEATBELT:
            return ["/bin/sh", "-c", "echo 'sandboxed-run: refused' >&2; exit 78"]
        pol = {**self.pol(), "protect": policy.protect(), "protect_roots": policy.protect_roots()}
        return [seatbelt.SANDBOX_EXEC, "-p", seatbelt.service_profile(pol, [self.ws]), *argv]

    def test_it_is_made_as_the_project_never_as_the_user(self):
        import session_worktree
        if not HAS_SEATBELT:
            with self.assertRaises(session_worktree.Refused):
                session_worktree.make(self.ws, "w1", runtime_claude)
            self.assertFalse((self.ws / ".claude" / "worktrees" / "w1").exists(), "nothing made as the user")
            return
        path, skipped = session_worktree.make(self.ws / "src", "w1", runtime_claude)
        self.assertEqual(path, self.ws / ".claude" / "worktrees" / "w1")
        self.assertTrue((path / "src" / "a").is_file())
        self.assertTrue((path / "src" / "l").is_symlink(), "the tracked link, checked out as git does")
        self.assertEqual(skipped, [".ctl/settings"])
        self.assertFalse((path / ".ctl").exists(), "a control file its sandbox can't write is left out")
        self.assertEqual(git(path, "status", "--porcelain"), "", "left out as skip-worktree, not as deleted")
        self.assertEqual(git(path, "rev-parse", "--abbrev-ref", "HEAD").strip(), "worktree-w1")
        ops = [a for _p, a in self.wrapped]
        self.assertTrue(any("session-worktree" in a for a in ops), ops)
        self.assertTrue(all(p == "proj" for p, _a in self.wrapped))
        session_worktree.drop(self.ws, path, runtime_claude)
        self.assertFalse(path.exists())

    def test_refusals(self):
        import session_worktree
        for bad in ("", "../x", "a/../b", ".hidden", "a/.b", "/a", "a//b", "a b", "x" * 101):
            with self.assertRaises(session_worktree.Refused):
                session_worktree.make(self.ws, bad, runtime_claude)
        self.docs["proj"]["enforce"] = {}
        with self.assertRaises(session_worktree.Refused):
            session_worktree.make(self.ws, "w2", runtime_claude)
        self.assertEqual(self.wrapped, [], "nothing ran for a project that isn't sandboxed, nor for a bad name")

    @unittest.skipUnless(HAS_SEATBELT, "needs Seatbelt")
    def test_a_name_with_slashes_maps_as_the_tool_does(self):
        import session_worktree
        path, _s = session_worktree.make(self.ws, "feat/x", runtime_claude)
        self.assertEqual(path, self.ws / ".claude" / "worktrees" / "feat+x")
        self.assertEqual(git(path, "rev-parse", "--abbrev-ref", "HEAD").strip(), "worktree-feat+x")

    @unittest.skipUnless(HAS_SEATBELT, "needs Seatbelt")
    def test_an_existing_branch_is_checked_out(self):
        """The branch exists, with a tracked link HEAD doesn't have: that branch is what the worktree gets."""
        import session_worktree
        git(self.ws, "switch", "-q", "-c", "worktree-w5")
        (self.ws / "only").mkdir()
        os.symlink("../src/a", self.ws / "only" / "l")
        git(self.ws, "add", "only")
        git(self.ws, "commit", "-qm", "a link only here")
        git(self.ws, "switch", "-q", "main")
        path, _s = session_worktree.make(self.ws, "w5", runtime_claude)
        self.assertTrue((path / "only" / "l").is_symlink())

    @unittest.skipUnless(HAS_SEATBELT, "needs Seatbelt")
    def test_a_drop_after_a_session_start_wrote_its_settings(self):
        """The real check (11-15): a session start writes the platform's settings and the rules-file link into the
        worktree, which the project's sandbox may not remove. The drop clears those as the platform first, then removes the
        rest as the project; nothing is left registered or on disk."""
        import session_worktree
        protect = [{"name": ".claude/settings.local.json", "tree": False}, {"name": "CLAUDE.local.md", "tree": False},
                   {"name": ".ctl", "tree": True}]
        roots = [{"name": ".claude", "writable": ["worktrees"]}]   # a worktree's own .claude is held too
        with mock.patch.object(policy, "protect", lambda: protect), mock.patch.object(policy, "protect_roots", lambda: roots):
            path, _s = session_worktree.make(self.ws, "w7", runtime_claude)
            (path / ".claude").mkdir()
            (path / ".claude" / "settings.local.json").write_text("{}\n")   # what render-project-settings writes
            (self.ws / "CLAUDE.local.md").write_text("rules\n")
            os.symlink(self.ws / "CLAUDE.local.md", path / "CLAUDE.local.md")   # worktree-memory.sh's link
            os.symlink(self.ws, path / ".agent-memory")
            (path / "work.txt").write_text("uncommitted\n")
            with mock.patch.object(session_worktree, "platform_entries",
                                   lambda: ["CLAUDE.local.md", ".claude/settings.local.json"]):
                session_worktree.drop(self.ws, path, runtime_claude)
        self.assertFalse(os.path.lexists(path))
        self.assertNotIn(str(path), registered(self.ws))
        self.assertTrue((self.ws / "CLAUDE.local.md").is_file(), "the link removed, never what it points to")

    @unittest.skipUnless(HAS_SEATBELT, "needs Seatbelt")
    def test_a_folder_that_cant_be_emptied_stays_registered(self):
        """Never unregistered with its folder still there (git's own `worktree remove` does that)."""
        import session_worktree
        protect = [{"name": ".claude/settings.local.json", "tree": False}, {"name": ".ctl", "tree": True}]
        roots = [{"name": ".claude", "writable": ["worktrees"]}]
        with mock.patch.object(policy, "protect", lambda: protect), mock.patch.object(policy, "protect_roots", lambda: roots):
            path, _s = session_worktree.make(self.ws, "w8", runtime_claude)
            (path / ".claude").mkdir()
            (path / ".claude" / "settings.local.json").write_text("{}\n")   # not in platform_entries here: left
            with mock.patch.object(session_worktree, "platform_entries", lambda: []):
                with self.assertRaises(session_worktree.Refused):
                    session_worktree.drop(self.ws, path, runtime_claude)
        self.assertIn(str(path), registered(self.ws))
        self.assertTrue((path / ".git").is_file())

    @unittest.skipUnless(HAS_SEATBELT, "needs Seatbelt")
    def test_a_make_that_didnt_finish_is_undone_by_the_next(self):
        """Its hook killed (the job's own cleanup never ran): the record it left makes the next make of that name undo
        the worktree and the branch first."""
        import session_worktree
        path = self.ws / ".claude" / "worktrees" / "w6"
        git(self.ws, "worktree", "add", "-q", "--no-checkout", "-b", "worktree-w6", str(path), "HEAD")
        rec = session_worktree._pending("proj", path)
        rec.parent.mkdir(parents=True)
        rec.write_text(json.dumps({"path": str(path), "branch": "worktree-w6", "new": True}))
        got, _s = session_worktree.make(self.ws, "w6", runtime_claude)
        self.assertEqual(got, path)
        self.assertFalse(rec.exists())
        with self.assertRaises(session_worktree.Refused):   # a folder there that no record names is never undone
            session_worktree.make(self.ws, "w6", runtime_claude)

    def test_the_hook_timeout_covers_the_work_and_stays_under_the_tools_default(self):
        import session_worktree
        t = session_worktree.HOOK_TIMEOUT_S
        self.assertGreater(t, session_worktree.PLAN_TIMEOUT_S + session_worktree.JOB_TIMEOUT_S
                           + session_worktree.UNDO_TIMEOUT_S)
        self.assertLess(t, 600, "Claude Code's default hook timeout (600 s, read from its binary)")
        for entries in runtime_claude.worktree_maker_hooks().values():
            self.assertEqual(entries[0]["hooks"][0]["timeout"], t)

    def test_drop_only_a_worktree_at_the_tools_place(self):
        import session_worktree
        (self.ws / "src" / ".git").write_text("gitdir: /x\n")   # a pointer, but not at the tool's place
        for bad in (self.ws, self.tmp / "elsewhere", self.ws / ".." / "x", self.ws / "src",
                    self.ws / ".claude" / "worktrees" / "nothing-here"):
            with self.assertRaises(session_worktree.Refused):
                session_worktree.drop(self.ws, bad, runtime_claude)

    @unittest.skipUnless(HAS_SEATBELT, "needs Seatbelt")
    def test_a_failed_make_leaves_nothing_behind(self):
        """worktree add has registered the worktree and made the branch before the checkout: a checkout that fails
        takes both back, so the same name can be made again."""
        import session_worktree
        (self.ws / "fail-here").write_text("x\n")
        git(self.ws, "add", "fail-here")
        git(self.ws, "commit", "-qm", "f")
        real = self.wrap

        def failing(project, cwd, argv, port=None):
            got = real(project, cwd, argv, port)
            return [*got[:2], got[2] + '(deny file-write* (regex #"/fail-here$"))\n', *got[3:]]
        with mock.patch.object(isolation, "wrap", failing):
            with self.assertRaises(session_worktree.Refused):
                session_worktree.make(self.ws, "w3", runtime_claude)
        self.assertNotIn(str(self.ws / ".claude" / "worktrees" / "w3"), registered(self.ws))
        self.assertEqual(git(self.ws, "branch", "--list", "worktree-w3"), "")
        self.assertFalse((self.ws / ".claude" / "worktrees" / "w3").exists())
        path, _skipped = session_worktree.make(self.ws, "w3", runtime_claude)
        self.assertTrue((path / "fail-here").is_file())

    @unittest.skipUnless(HAS_SEATBELT, "needs Seatbelt")
    def test_a_race_against_the_hook_path_writes_nothing_outside(self):
        """The same swap, against the worktree the platform makes: the checkout runs in the project's sandbox, so a
        link can only redirect its writes to places the project can write. `outside` is a place it can't: denied to every
        process of the project here, the worktree job included. It must be said, not assumed: where the test's temp folder
        is the system's (a CI clone, which keeps it), the service profile lets every project write it, and a link there
        took the job's writes on CI (11-15), which is no escalation (the project writes there anyway) and not what this
        tests."""
        import session_worktree
        outside = self.tmp / "outside"
        outside.mkdir()
        deny_outside = f'(deny file-write* (subpath "{outside}"))\n'
        real = self.wrap

        def wrap(project, cwd, argv, port=None):
            got = real(project, cwd, argv, port)
            return [*got[:2], got[2] + deny_outside, *got[3:]]
        p = mock.patch.object(isolation, "wrap", wrap)
        p.start()
        self.addCleanup(p.stop)
        (self.ws / "d").mkdir()
        for i in range(400):
            (self.ws / "d" / f"a{i:04d}").write_text(f"planted {i}\n")
        git(self.ws, "add", "d")
        git(self.ws, "commit", "-qm", "B")
        stop = self.tmp / "STOP"
        wt = self.ws / ".claude" / "worktrees"
        attacker = f"""
end=$(( $(date +%s) + 20 ))
while [ $(date +%s) -lt $end ]; do
  for w in {wt}/r[024]/d; do [ -d "$w" ] && [ ! -L "$w" ] && mv "$w" "$w.x" 2>/dev/null && ln -s {outside} "$w" 2>/dev/null; done
  [ -e {stop} ] && exit 0
done
"""
        # the attacker as any process of the project: nothing refuses its links
        prof = seatbelt.service_profile(self.pol(), [self.ws]) + deny_outside
        a = subprocess.Popen([seatbelt.SANDBOX_EXEC, "-p", prof, "/bin/sh", "-c", attacker], cwd=self.ws,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            made = 0
            for i in range(6):
                try:
                    p, _s = session_worktree.make(self.ws, f"r{i}", runtime_claude)
                    made += 1
                    session_worktree.drop(self.ws, p, runtime_claude)
                except session_worktree.Refused:
                    pass
        finally:
            stop.write_text("")
            a.wait(timeout=30)
        self.assertEqual(list(outside.iterdir()), [])
        # the attacker goes after r0, r2 and r4 only: the others show makes go through while it runs (not vacuous)
        self.assertGreater(made, 0, "not vacuous: some makes went through while the attacker ran")


class Doctor(Base):
    def rows(self, settings=None):
        import importlib.machinery
        import importlib.util
        import runtime_ports
        loader = importlib.machinery.SourceFileLoader("agenthub_t", str(BIN / "agenthub"))
        spec = importlib.util.spec_from_loader("agenthub_t", loader)
        ah = importlib.util.module_from_spec(spec)
        loader.exec_module(ah)
        if settings is not None:
            p = runtime_claude.settings_path(self.ws)
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps(settings))
        docs = {"proj": {"name": "proj", "paths": {"workspace": str(self.ws)}, "enforce": {"sandbox": True}},
                "open": {"name": "open", "paths": {"workspace": str(self.tmp / "o")}}}
        out = []
        with mock.patch.object(hubconf, "projects", lambda: docs), \
                mock.patch.object(runtime_ports, "load", lambda name, ports=(): runtime_claude):
            ah.doctor_checkout_race(lambda level, what, fix="": out.append((level, what)), None, ["claude"])
        return out

    def test_one_warning_per_sandboxed_project_with_its_worktree_makers(self):
        [(_level, what)] = self.rows()
        self.assertTrue(what.endswith("-"), "no worktree hand-over without the hooks")
        [(level, what)] = self.rows(settings={"hooks": runtime_claude.worktree_maker_hooks()})
        self.assertEqual(level, "warn")
        self.assertIn("proj", what)
        self.assertTrue(what.endswith("claude"), what)


class HubEvent(Base):
    """`hub-event --runtime claude worktree-make|worktree-drop` as the hook runs it: the hook's input on stdin, the path
    on stdout, exit 1 with the reason on stderr when it can't (never `|| true`: the worktree then fails)."""

    def setUp(self):
        super().setUp()
        import contextlib
        import importlib.machinery
        import importlib.util
        import io
        loader = importlib.machinery.SourceFileLoader("hub_event_t", str(BIN / "hub-event"))
        spec = importlib.util.spec_from_loader("hub_event_t", loader)
        self.he = importlib.util.module_from_spec(spec)
        loader.exec_module(self.he)
        self.logged = []
        p = mock.patch.object(self.he, "_log", lambda *a, **k: self.logged.append(a))
        p.start()
        self.addCleanup(p.stop)
        self.io, self.cl = io, contextlib

    def call(self, what, payload, make=None):
        import session_worktree
        out, err = self.io.StringIO(), self.io.StringIO()
        with mock.patch.object(session_worktree, "make", make or (lambda cwd, name, rt: (self.ws / "wt" / name, []))), \
                mock.patch.object(session_worktree, "drop", lambda cwd, path, rt: None), \
                self.cl.redirect_stdout(out), self.cl.redirect_stderr(err):
            rc = self.he.main(["--runtime", "claude", what] if what else [])
        return rc, out.getvalue(), err.getvalue()

    def run_with(self, what, payload, **kw):
        with mock.patch.object(self.he, "_read_stdin", lambda: json.dumps(payload)):
            return self.call(what, payload, **kw)

    def test_the_path_is_the_last_line_of_stdout(self):
        rc, out, _err = self.run_with("worktree-make", {"hook_event_name": "WorktreeCreate", "cwd": str(self.ws),
                                                        "name": "w1"})
        self.assertEqual((rc, out.splitlines()[-1]), (0, str(self.ws / "wt" / "w1")))

    def test_a_refusal_exits_1_with_nothing_on_stdout(self):
        import session_worktree

        def refuse(cwd, name, rt):
            raise session_worktree.Refused("no")
        rc, out, err = self.run_with("worktree-make", {"hook_event_name": "WorktreeCreate", "cwd": str(self.ws),
                                                       "name": "w1"}, make=refuse)
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("no", err)

    def test_a_crash_exits_1_too(self):
        def boom(cwd, name, rt):
            raise RuntimeError("boom")
        rc, out, _err = self.run_with("worktree-make", {"hook_event_name": "WorktreeCreate", "cwd": str(self.ws),
                                                        "name": "w1"}, make=boom)
        self.assertEqual((rc, out), (1, ""))

    def test_the_wrong_input_for_the_command_is_refused(self):
        rc, out, _err = self.run_with("worktree-make", {"hook_event_name": "WorktreeRemove", "cwd": str(self.ws),
                                                        "worktree_path": str(self.ws / "wt")})
        self.assertEqual((rc, out), (1, ""))
        rc, out, _err = self.run_with("worktree-drop", {"hook_event_name": "WorktreeRemove", "cwd": str(self.ws),
                                                        "worktree_path": str(self.ws / "wt")})
        self.assertEqual((rc, out), (0, ""))


if __name__ == "__main__":
    unittest.main()
