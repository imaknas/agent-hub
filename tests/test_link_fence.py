"""The owner's checkout race (docs/design/git-broker.md, r3 B-1): any process of a sandboxed project could swap a tracked
folder for a symbolic link while someone else's unsandboxed checkout writes there, and the checkout then wrote the
project's files wherever the link pointed, as the owner. Two mitigations, built 11-14:
- its services and jobs make no link in their checkouts (policy links, Seatbelt's link_rules), but in the tool folders
  that need them (.venv, node_modules) and the platform's own test runs;
- a coding agent's own worktree mode hands the worktree to the platform, which makes it as the project
  (session_worktree.py, `hub-event worktree-make`; Claude Code's WorktreeCreate hook).

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


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = self.tmp / "ws"
        self.ws.mkdir()
        self.rt = self.tmp / "runtime"
        self.rt.mkdir()

    def pol(self, links=True, allow=()):
        lk = policy.links({"workspace": self.ws, "runtime": self.rt})
        lk["allow"] = list(allow)
        return {"workspace": self.ws, "write": [], "other_projects": [], "deny_read": [], "credentials": [],
                "credential_files": [], "protect": [], "protect_roots": [], **({"links": lk} if links else {})}

    def run_in(self, script, pol=None, cwd=None, env=None):
        prof = seatbelt.service_profile(pol or self.pol(), [self.ws, self.rt])
        return subprocess.run([seatbelt.SANDBOX_EXEC, "-p", prof, "/bin/sh", "-c", script], cwd=cwd or self.ws,
                              capture_output=True, text=True, env=env or ENV)


class Policy(Base):
    def test_the_port_names_its_checkouts_the_tool_folders_and_the_test_runs(self):
        lk = policy.links({"workspace": self.ws, "runtime": self.rt})
        self.assertEqual(lk["roots"], [self.ws, self.rt])
        self.assertEqual([f["name"] for f in lk["folders"]], [".venv", "node_modules"])
        self.assertIn(self.ws / ".brunna" / "test", lk["inside"])
        self.assertEqual(lk["allow"], [])
        self.assertEqual(policy.links({"workspace": None, "runtime": None})["roots"], [])

    def test_seatbelt_renders_the_deny_then_the_allows_and_nothing_outside_the_roots(self):
        rules = seatbelt.link_rules(self.pol(allow=[self.ws / "wt" / "l", self.tmp / "elsewhere" / "l"]))
        self.assertTrue(rules[1].startswith("(deny file-write-create (require-all (vnode-type SYMLINK)"))
        self.assertIn(f'(literal "{self.ws}/wt/l")', rules[2])
        self.assertNotIn("elsewhere", rules[2], "an allowance outside its own folders is dropped")
        self.assertEqual(seatbelt.link_rules({"links": {"roots": []}}), [])
        self.assertEqual(seatbelt.link_rules({}), [])

    def test_they_come_before_the_control_rules_so_those_still_win(self):
        prof = seatbelt.service_profile({**self.pol(), "protect": [{"name": ".ctl", "tree": True}]}, [self.ws])
        self.assertLess(prof.index("vnode-type SYMLINK"), prof.index("the files its sessions load settings"))

    def test_seatbelt_declares_it_and_landlock_does_not(self):
        import landlock
        self.assertIn("links-blocked", seatbelt.guarantees())
        with mock.patch.object(landlock, "abi", lambda: 6):
            self.assertNotIn("links-blocked", landlock.guarantees())


@unittest.skipUnless(HAS_SEATBELT, "needs Seatbelt")
class SeatbeltLinks(Base):
    def test_no_link_in_its_checkouts_but_in_the_tool_folders(self):
        (self.tmp / "stage").mkdir()
        pol = self.pol()
        pol["write"] = [str(self.tmp / "stage")]   # a folder it may write that isn't a checkout (allow_write)
        r = self.run_in(pol=pol, script=f"""
ln -s /etc l 2>/dev/null || echo ws-refused
ln -s /etc {self.rt}/l 2>/dev/null || echo runtime-refused
ln -s /etc {self.tmp}/stage/l && {{ mv {self.tmp}/stage/l moved 2>/dev/null || echo moved-in-refused; }}
mkdir -p node_modules/.bin sub/node_modules .venv/bin sub/.venv d .brunna/test/run.1/tree
ln -s /etc node_modules/.bin/x && echo node-modules-ok
ln -s /etc sub/node_modules/x && echo nested-node-modules-ok
ln -s /etc .venv/bin/python && echo venv-ok
ln -s /etc sub/.venv/x 2>/dev/null || echo nested-venv-refused
ln -s /etc .brunna/test/run.1/tree/l && echo test-run-ok
ln -s /etc node_modules/y && rmdir d && {{ mv node_modules/y d 2>/dev/null || echo tool-link-onto-d-refused; }}
rm -rf node_modules && {{ ln -s /etc node_modules 2>/dev/null || echo tool-folder-itself-refused; }}
echo x > plain && echo files-ok
""")
        self.assertEqual(r.stdout.split(), ["ws-refused", "runtime-refused", "moved-in-refused", "node-modules-ok",
                                            "nested-node-modules-ok", "venv-ok", "nested-venv-refused", "test-run-ok",
                                            "tool-link-onto-d-refused", "tool-folder-itself-refused", "files-ok"],
                         r.stderr)

    def test_an_exact_allowance_for_one_job(self):
        (self.ws / "wt").mkdir()
        r = self.run_in("ln -s /etc wt/l && echo allowed; ln -s /etc wt/m 2>/dev/null || echo other-refused",
                        pol=self.pol(allow=[self.ws / "wt" / "l"]))
        self.assertEqual(r.stdout.split(), ["allowed", "other-refused"], r.stderr)

    def test_the_positive_control_without_the_rule(self):
        r = self.run_in("ln -s /etc l && echo made", pol=self.pol(links=False))
        self.assertEqual(r.stdout.split(), ["made"], r.stderr)

    def test_a_venv_still_works(self):
        r = self.run_in(f"{sys.executable} -m venv --without-pip .venv && [ -L .venv/bin/python ] && echo venv-ok")
        self.assertIn("venv-ok", r.stdout, r.stderr)

    @unittest.skipUnless(shutil.which("uv"), "needs uv")
    def test_uv_venv_still_works(self):
        env = {**ENV, "UV_CACHE_DIR": str(self.tmp / "uvcache"), "UV_NO_CONFIG": "1", "UV_PYTHON_DOWNLOADS": "never",
               "HOME": str(self.tmp)}
        pol = self.pol()
        pol["write"] = [str(self.tmp / "uvcache")]
        r = self.run_in("uv venv -q .venv && [ -L .venv/bin/python ] && echo uv-ok", pol=pol, env=env)
        self.assertIn("uv-ok", r.stdout, r.stderr)

    @unittest.skipUnless(shutil.which("npm"), "needs npm")
    def test_npm_bin_links_still_work(self):
        pkg = self.ws / "vendor" / "pkg"   # in its own folder: the test's temp folder may be one no sandbox reads
        (pkg / "bin").mkdir(parents=True)
        (pkg / "package.json").write_text('{"name":"pkg","version":"1.0.0","bin":{"lp":"bin/lp.js"}}\n')
        (pkg / "bin" / "lp.js").write_text("#!/usr/bin/env node\n")
        (self.ws / "package.json").write_text('{"name":"w","version":"1.0.0"}\n')
        env = {**ENV, "npm_config_cache": str(self.tmp / "npmcache"), "npm_config_update_notifier": "false",
               "npm_config_audit": "false", "npm_config_fund": "false", "HOME": str(self.tmp)}
        pol = self.pol()
        pol["write"] = [str(self.tmp / "npmcache")]
        r = self.run_in(f"npm install -s --no-save {pkg} && [ -L node_modules/.bin/lp ] && echo npm-ok", pol=pol,
                        env=env)
        self.assertIn("npm-ok", r.stdout, r.stderr)

    def test_the_owner_race_writes_nothing_outside(self):
        """The review's owner_race2: a project process swaps d/ for a link to a folder it can't write while the owner's
        unsandboxed checkout writes d/. Without the rule, the first round wrote ~2,900 files there (measured 11-14);
        with it, the swap itself fails."""
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "a0100").write_text("ORIGINAL\n")
        git(self.ws, "init", "-q", "-b", "main")
        (self.ws / "keep").write_text("k\n")
        git(self.ws, "add", "keep")
        git(self.ws, "commit", "-qm", "A")
        (self.ws / "d").mkdir()
        for i in range(800):
            (self.ws / "d" / f"a{i:04d}").write_text(f"planted {i}\n")
        git(self.ws, "add", "d")
        git(self.ws, "commit", "-qm", "B")
        git(self.ws, "checkout", "-q", "main~1")
        pol = self.pol()
        stop = self.tmp / "STOP"
        swapped = self.tmp / "SWAPPED"
        attacker = f"""
end=$(( $(date +%s) + 20 ))
while [ $(date +%s) -lt $end ]; do
  if [ -d d ] && [ ! -L d ]; then mv d d.$$ 2>/dev/null && {{ ln -s {outside} d 2>/dev/null && touch {swapped}; }}; fi
  [ -e {stop} ] && exit 0
done
"""
        prof = seatbelt.service_profile(pol, [self.ws, self.rt]) + f'(deny file-write* (subpath "{outside}"))\n'
        a = subprocess.Popen([seatbelt.SANDBOX_EXEC, "-p", prof, "/bin/sh", "-c", attacker], cwd=self.ws,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(8):
                subprocess.run(["git", "switch", "-q", "main"], cwd=self.ws, env=ENV, capture_output=True)
                subprocess.run(["/bin/sh", "-c", "rm -rf d d.*"], cwd=self.ws)
                subprocess.run(["git", "checkout", "-q", "-f", "main~1"], cwd=self.ws, env=ENV, capture_output=True)
        finally:
            stop.write_text("")
            a.wait(timeout=30)
        self.assertEqual(sorted(p.name for p in outside.iterdir()), ["a0100"])
        self.assertEqual((outside / "a0100").read_text(), "ORIGINAL\n")
        self.assertFalse(swapped.exists(), "the project made the link")


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
    """session_worktree.make / drop through project_git as the project. `wrap` is the real Seatbelt service profile with
    the link rule (macOS), or a recorder that refuses like sandboxed-run where nothing can sandbox."""

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

    def wrap(self, project, cwd, argv, port=None, links=()):
        self.wrapped.append((project, list(argv), list(links)))
        if not HAS_SEATBELT:
            return ["/bin/sh", "-c", "echo 'sandboxed-run: refused' >&2; exit 78"]
        pol = {**self.pol(allow=links), "protect": [{"name": ".ctl", "tree": True}]}
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
        self.assertTrue((path / "src" / "l").is_symlink(), "the tracked link, by the job's exact allowance")
        self.assertEqual(skipped, [".ctl/settings"])
        self.assertFalse((path / ".ctl").exists(), "a control file its sandbox can't write is left out")
        self.assertEqual(git(path, "status", "--porcelain"), "", "left out as skip-worktree, not as deleted")
        self.assertEqual(git(path, "rev-parse", "--abbrev-ref", "HEAD").strip(), "worktree-w1")
        ops = [a for _p, a, _l in self.wrapped]
        self.assertTrue(any("session-worktree" in a for a in ops), ops)
        self.assertTrue(all(p == "proj" for p, _a, _l in self.wrapped))
        self.assertIn([path / "src" / "l"], [lk for _p, a, lk in self.wrapped if "session-worktree" in a])
        session_worktree.drop(self.ws, path)
        self.assertFalse(path.exists())

    def test_refusals(self):
        import session_worktree
        for bad in ("", "../x", "a/b", ".hidden", "x" * 80):
            with self.assertRaises(session_worktree.Refused):
                session_worktree.make(self.ws, bad, runtime_claude)
        self.docs["proj"]["enforce"] = {}
        with self.assertRaises(session_worktree.Refused):
            session_worktree.make(self.ws, "w2", runtime_claude)
        self.assertEqual(self.wrapped, [], "nothing ran for a project that isn't sandboxed, nor for a bad name")

    def test_drop_only_inside_the_workspace(self):
        import session_worktree
        for bad in (self.ws, self.tmp / "elsewhere", self.ws / ".." / "x"):
            with self.assertRaises(session_worktree.Refused):
                session_worktree.drop(self.ws, bad)

    def test_tracked_links_are_allowed_only_under_the_new_worktree(self):
        import session_worktree
        wt = self.ws / ".claude" / "worktrees" / "w"
        self.assertEqual(session_worktree.link_paths(wt, ["src/l", "/etc/x", "../out", "a/../../b", ""]),
                         [wt / "src" / "l"])

    @unittest.skipUnless(HAS_SEATBELT, "needs Seatbelt")
    def test_a_failed_make_leaves_nothing_behind(self):
        """worktree add has registered the worktree and made the branch before the checkout: a checkout that fails
        takes both back, so the same name can be made again."""
        import session_worktree
        (self.ws / "fail-here").write_text("x\n")
        git(self.ws, "add", "fail-here")
        git(self.ws, "commit", "-qm", "f")
        real = self.wrap

        def failing(project, cwd, argv, port=None, links=()):
            got = real(project, cwd, argv, port, links)
            return [*got[:2], got[2] + '(deny file-write* (regex #"/fail-here$"))\n', *got[3:]]
        with mock.patch.object(isolation, "wrap", failing):
            with self.assertRaises(session_worktree.Refused):
                session_worktree.make(self.ws, "w3", runtime_claude)
        self.assertNotIn("w3", git(self.ws, "worktree", "list"))
        self.assertEqual(git(self.ws, "branch", "--list", "worktree-w3"), "")
        self.assertFalse((self.ws / ".claude" / "worktrees" / "w3").exists())
        path, _skipped = session_worktree.make(self.ws, "w3", runtime_claude)
        self.assertTrue((path / "fail-here").is_file())

    @unittest.skipUnless(HAS_SEATBELT, "needs Seatbelt")
    def test_a_race_against_the_hook_path_writes_nothing_outside(self):
        """The same swap, against the worktree the platform makes: the checkout runs in the project's sandbox, so a
        link could only redirect writes to places the project can write, and its sandbox makes no link anyway."""
        import session_worktree
        outside = self.tmp / "outside"
        outside.mkdir()
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
  for w in {wt}/*/d; do [ -d "$w" ] && [ ! -L "$w" ] && mv "$w" "$w.x" 2>/dev/null && ln -s {outside} "$w" 2>/dev/null; done
  [ -e {stop} ] && exit 0
done
"""
        # the attacker as a session's own command: no agent's sandbox refuses links, so no link rule here
        prof = seatbelt.service_profile(self.pol(links=False), [self.ws]) + f'(deny file-write* (subpath "{outside}"))\n'
        a = subprocess.Popen([seatbelt.SANDBOX_EXEC, "-p", prof, "/bin/sh", "-c", attacker], cwd=self.ws,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for i in range(4):
                try:
                    p, _s = session_worktree.make(self.ws, f"r{i}", runtime_claude)
                    session_worktree.drop(self.ws, p)
                except session_worktree.Refused:
                    pass
        finally:
            stop.write_text("")
            a.wait(timeout=30)
        self.assertEqual(list(outside.iterdir()), [])


class RunAs(unittest.TestCase):
    def test_run_as_takes_exact_link_allowances_before_the_command(self):
        import project_git
        got = []
        with mock.patch.object(project_git, "run_as_cli", lambda *a: got.append(a) or 0):
            self.assertEqual(project_git.main(["run-as", "p", "/d", "--allow-link", "/d/a", "--allow-link", "/d/b",
                                               "--", "bash", "x"]), 0)
            self.assertEqual(project_git.main(["run-as", "p", "/d", "--", "bash", "x"]), 0)
        self.assertEqual(got, [("p", "/d", ["bash", "x"], ["/d/a", "/d/b"]), ("p", "/d", ["bash", "x"], [])])

    def test_wrap_passes_them_to_sandboxed_run(self):
        argv = isolation.wrap("p", "/d", ["x"], links=["/d/a"])
        i = argv.index("--allow-link")
        self.assertEqual(argv[i:i + 2], ["--allow-link", "/d/a"])
        self.assertNotIn("--allow-link", isolation.wrap("p", "/d", ["x"]))


class Doctor(Base):
    def rows(self, guarantees, tracked=(), settings=None, adapter=("seatbelt", None)):
        import importlib.machinery
        import importlib.util
        import project_git
        import runtime_ports
        loader = importlib.machinery.SourceFileLoader("agenthub_t", str(BIN / "agenthub"))
        spec = importlib.util.spec_from_loader("agenthub_t", loader)
        ah = importlib.util.module_from_spec(spec)
        loader.exec_module(ah)
        (self.ws / ".git").mkdir()
        if settings is not None:
            p = runtime_claude.settings_path(self.ws)
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps(settings))
        docs = {"proj": {"name": "proj", "paths": {"workspace": str(self.ws)}, "enforce": {"sandbox": True}},
                "open": {"name": "open", "paths": {"workspace": str(self.tmp / "o")}}}
        out = []
        with mock.patch.object(hubconf, "projects", lambda: docs), \
                mock.patch.object(isolation, "guarantees", lambda: list(guarantees)), \
                mock.patch.object(isolation, "adapter", lambda: adapter), \
                mock.patch.object(project_git, "data", lambda *a, **k: list(tracked)), \
                mock.patch.object(runtime_ports, "load", lambda name, ports=(): runtime_claude):
            ah.doctor_checkout_race(lambda level, what, fix="": out.append((level, what)), None, ["claude"])
        return out

    def test_macos_rows(self):
        rows = self.rows(["links-blocked"], settings={"hooks": runtime_claude.worktree_maker_hooks()})
        self.assertEqual([r[0] for r in rows], ["info", "warn"])
        self.assertIn("proj", rows[1][1])
        self.assertTrue(rows[1][1].endswith("claude"), rows[1][1])

    def test_linux_and_tracked_tool_folders(self):
        rows = self.rows([], tracked=["node_modules/x/a"], adapter=("landlock", None))
        self.assertEqual([r[0] for r in rows], ["warn", "warn", "warn"])
        self.assertIn("landlock", rows[0][1])
        self.assertTrue(rows[1][1].endswith("-"), "no worktree hand-over without the hooks")
        self.assertIn("node_modules/x/a", rows[2][1])


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
                mock.patch.object(session_worktree, "drop", lambda cwd, path: None), \
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
