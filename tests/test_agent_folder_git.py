"""What a platform run's agent does in a project's folder outside any sandbox (docs/design/mailbox-session-first.md, step 3's record).

`claude -p` runs `git status` of its own at its start, and a sandboxed project writes its own repo's config, so a clean filter there ran
as the user. Every way the platform starts it carries the variable that stops that, and each new version gets a real probe, since
the variable is a tool's. Real runs of the CLI are in the step record; here a stand-in `claude` that notes what it was given. Where a
fork starts, and whose files the settings pass writes, are the same question for a remembered folder.

    bin/run-tests        (running this module directly in a worktree reads the real config through .hub-home)
"""
import importlib.machinery
import importlib.util
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
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import runtime_claude  # noqa: E402
import runtime_ports  # noqa: E402
import store_fixture  # noqa: E402
import test_bus_sender as tbs  # noqa: E402
import test_mailbox_dispatch as tmd  # noqa: E402
import test_worktree_pointer as twp  # noqa: E402

VAR = "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS"
_LANG = mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "en", "AGENT_HUB_LANG_CONTENT": "en"})   # the assertions read the English texts


def setUpModule():
    _LANG.start()


def tearDownModule():
    _LANG.stop()


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    m = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(m)
    return m


class StandIn(unittest.TestCase):
    """A `claude` that notes its environment and folder, and answers as the real one does."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.seen = self.tmp / "seen.json"
        # the platform's checked copy, in a stand-in store (it answers with stream-json when asked, as complete() does)
        store_fixture.stand_in(self, body=f"#!{sys.executable}\nimport json, os, sys\n"
                               f"json.dump({{'var': os.environ.get({VAR!r}), 'cwd': os.getcwd()}}, open({str(self.seen)!r}, 'w'))\n"
                               f"'stream-json' in sys.argv and print({store_fixture.INIT!r})\n"
                               "print(json.dumps({'type': 'result', 'result': 'ok', 'is_error': False, 'session_id': 's', 'num_turns': 1}))\n")

    def var(self):
        return json.loads(self.seen.read_text())["var"]

    def test_every_point_of_the_agent_shape_carries_it(self):
        import llm
        import yaml
        points = yaml.safe_load((ROOT / "contracts" / "llm-points.yaml").read_text())
        points = points.get("points", points)
        ran = 0
        for point, spec in points.items():
            if not isinstance(spec, dict) or spec.get("shape") != "agent":
                continue
            with self.subTest(point=point):
                self.seen.unlink(missing_ok=True)
                env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.tmp), "MAILBOX_PROJECT": "x", "AGENT_JOB_PROJECT": "x",
                       "AGENT_JOB_ALLOW": "[]", "LEASE_MODE": "discuss", "LEASE_WS": str(self.tmp), "LEASE_PROJECT": "x"}
                task = runtime_ports.Task(point=point, prompt="p", spec=spec, cwd=str(self.tmp), env=env)
                try:
                    runtime_claude.run(task)
                except (ValueError, KeyError):   # a point whose flags need what only its caller has: its env is the same _env
                    self.assertEqual(runtime_claude._env(task)[VAR], "1")
                    continue
                self.assertEqual(self.var(), "1")
                ran += 1
        self.assertGreater(ran, 3, "the stand-in really ran")
        self.assertTrue(llm)

    def test_the_other_ways_it_starts(self):
        task = runtime_ports.Task(point="x", prompt="p", spec={}, cwd=str(self.tmp), env={"PATH": os.environ.get("PATH", "")})
        with mock.patch.object(runtime_claude.os, "execvpe") as ex:
            runtime_claude.exec_stream(task)
        self.assertEqual(ex.call_args[0][2][VAR], "1", "a lease's long-running session")
        runtime_claude.session_status("s")
        self.assertEqual(self.var(), "1", "the agents listing")
        self.seen.unlink()
        with mock.patch.object(runtime_claude, "auth_env", lambda mode, env: (dict(env), "")):
            runtime_claude.complete(runtime_ports.CompletionRequest(point="x", prompt="p"), {})
        self.assertEqual(self.var(), "1", "the completion path")

    def test_the_completion_path_starts_where_no_sandbox_writes_and_reads_no_settings_file(self):
        seen = self.tmp / "complete.json"
        store_fixture.stand_in(self, body=f"#!{sys.executable}\nimport json, os, sys\njson.dump({{'cwd': os.getcwd(), 'argv': sys.argv}}, open({str(seen)!r}, 'w'))\n"
                               f"print({store_fixture.INIT!r})\n"
                               "print(json.dumps({'type': 'result', 'result': 'ok', 'is_error': False, 'session_id': 's', 'num_turns': 1}))\n")
        private = self.tmp / "private"
        private.mkdir()
        with mock.patch.object(hubconf, "private_tmp", lambda: private), mock.patch.object(runtime_claude, "auth_env", lambda mode, env: (dict(env), "")):
            runtime_claude.complete(runtime_ports.CompletionRequest(point="x", prompt="p"), {})
        got = json.loads(seen.read_text())
        self.assertTrue(pathlib.Path(got["cwd"]).resolve().is_relative_to(private.resolve()))
        self.assertIn("--restricted", got["argv"])

    def test_a_point_that_says_restricted_gets_it(self):
        import yaml
        spec = yaml.safe_load((ROOT / "contracts" / "llm-points.yaml").read_text())["points"]["mailbox-propose"]
        self.assertIs(spec.get("restricted"), True, "its prompt comes from a letter")
        cmd = runtime_claude.describe(runtime_ports.Task(point="mailbox-propose", prompt="p", spec=spec, env={"PATH": ""}))
        self.assertIn("--restricted", cmd)

    def test_a_read_only_secrets_folder_is_a_failed_result_not_a_crash(self):
        if os.geteuid() == 0:
            self.skipTest("root writes anywhere")
        secrets = self.tmp / "ro"
        secrets.mkdir()
        secrets.chmod(0o500)
        self.addCleanup(secrets.chmod, 0o700)
        with mock.patch.object(hubconf, "secrets_dir", lambda: secrets), mock.patch.object(runtime_claude, "auth_env", lambda mode, env: (dict(env), "")):
            got = runtime_claude.complete(runtime_ports.CompletionRequest(point="x", prompt="p"), {})
        self.assertFalse(got.ok)
        self.assertTrue(got.error)
        # only making the folder reads that way: anything else keeps its own name
        with mock.patch.object(runtime_claude, "auth_env", lambda mode, env: (dict(env), "")), \
                mock.patch.object(runtime_claude.subprocess, "Popen", side_effect=PermissionError("the tool")), self.assertRaises(PermissionError):
            runtime_claude.complete(runtime_ports.CompletionRequest(point="x", prompt="p"), {})
        import propose_apply
        import runtime_command
        with mock.patch.object(hubconf, "secrets_dir", lambda: secrets), mock.patch.object(hubconf, "projects", lambda: {"p": {}}), \
                mock.patch.object(hubconf, "get", lambda k, d=None: {"run": ["true"]} if k == "runtime.command" else d):
            code, _out, why = runtime_command._run("p", None, None, None, 10)   # what its run makes the folder with
            self.assertIsNone(code)
            self.assertTrue(why)
            res = propose_apply.run("p", self.tmp, "mailbox-propose", "p", [])
            self.assertFalse(res["ok"])
            self.assertTrue(res["error"])

    def test_it_overrides_what_a_caller_set(self):
        task = runtime_ports.Task(point="x", prompt="p", spec={}, env={VAR: "0", "PATH": ""})
        self.assertEqual(runtime_claude._env(task)[VAR], "1")

    def test_a_sandboxed_projects_sessions_get_it_too(self):
        self.assertEqual(runtime_claude.session_env({"env": {"A": "b"}}), {"A": "b", VAR: "1"})
        self.assertEqual(runtime_claude.session_env({})[VAR], "1")


class Probe(unittest.TestCase):
    """conformance_claude.filter_probe (the one copy: a conformance check, and update-claude's before it), the request a
    stand-in: the probe's own logic, its positive controls included."""

    def setUp(self):
        import conformance_claude
        self.m = conformance_claude
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        (self.tmp / "probe").mkdir()

    def probe(self, run):
        return self.m.filter_probe(self.tmp / "probe", exe="/bin/false", run=run)

    @staticmethod
    def status(repo):
        subprocess.run(["git", "-C", str(repo), "--no-optional-locks", "status", "--short"], capture_output=True,
                       env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull})

    def as_claude_is(self, repo, guarded):
        """Runs its git without the variable, none with it."""
        if not guarded:
            self.status(repo)

    def test_a_run_that_leaves_the_filter_alone_only_with_the_variable(self):
        self.assertIsNone(self.probe(self.as_claude_is))

    def test_a_run_that_runs_git_status_with_the_variable_is_said(self):
        why = self.probe(lambda repo, guarded: self.status(repo))
        self.assertTrue(why.startswith("FAIL:"))
        self.assertIn("NO_FOLDER_GIT", why)

    def test_a_version_that_doesnt_run_it_without_the_variable_either_is_said(self):
        """Or the probe passes forever, telling nothing: said as nothing to guard (NONE:), not as a pass."""
        why = self.probe(lambda repo, guarded: None)
        self.assertTrue(why.startswith("NONE:"))
        self.assertIn("no longer tests", why)

    def test_a_run_that_couldnt_be_made_is_said(self):
        self.assertIn("no login", self.probe(lambda repo, guarded: "no login"))
        self.assertIn("no login", self.probe(lambda repo, guarded: "no login" if guarded else self.status(repo)))

    def test_a_probe_whose_control_shows_nothing_is_said(self):
        real = pathlib.Path.write_text

        def no_attributes(path, *a, **k):
            return 0 if path.name == "attributes" else real(path, *a, **k)
        with mock.patch.object(pathlib.Path, "write_text", no_attributes):
            why = self.probe(lambda repo, guarded: self.fail("never got this far"))
        self.assertIn("git status", why)

    def test_it_is_built_in_the_folder_it_is_given(self):
        seen = []

        def run(repo, guarded):
            seen.append(repo)
            self.as_claude_is(repo, guarded)
        self.probe(run)
        self.assertTrue(seen)
        self.assertTrue(all(self.tmp in r.parents for r in seen))

    def test_the_folder_is_denied_to_every_sandbox_both_ways(self):
        import policy
        with mock.patch.object(hubconf, "secrets_dir", lambda: self.tmp / "secrets"):
            d = hubconf.private_tmp()
            self.assertEqual(d.stat().st_mode & 0o777, 0o700)
            self.assertEqual(d.parent.stat().st_mode & 0o777, 0o700, "a secrets folder it had to make, too")
            self.assertTrue(any(d.is_relative_to(x) for x in policy.secret_dirs()), "every sandbox's deny_read; no write list has it")

class PrivateTmpPrune(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        p = mock.patch.object(hubconf, "secrets_dir", lambda: self.tmp)
        p.start()
        self.addCleanup(p.stop)
        self.d = hubconf.private_tmp()

    def old(self, *paths):
        t = __import__("time").time() - (hubconf.PRIVATE_TMP_KEEP_DAYS + 1) * 86400
        for p in paths:
            os.utime(p, (t, t), follow_symlinks=False)

    def leftover(self, name):
        d = self.d / name
        (d / "repo").mkdir(parents=True)
        (d / "repo" / "f").write_text("x")
        self.old(d / "repo" / "f", d / "repo", d)
        return d

    def test_an_old_leftover_goes_and_a_fresh_one_stays(self):
        old, fresh = self.leftover("old"), self.d / "fresh"
        fresh.mkdir()
        self.assertEqual(hubconf.prune_private_tmp(dry_run=True)["removed"], ["old"])
        self.assertTrue(old.exists(), "a dry run touches nothing")
        self.assertEqual(hubconf.prune_private_tmp()["removed"], ["old"])
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_something_written_deep_inside_keeps_it(self):
        d = self.leftover("busy")
        (d / "repo" / "f").write_text("y")
        self.assertEqual(hubconf.prune_private_tmp()["removed"], [])
        self.assertTrue(d.exists())

    def test_a_process_working_in_it_keeps_it_however_old(self):
        d = self.leftover("running")
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=d / "repo")
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        for _i in range(50):
            if hubconf.cwd_users(d):
                break
            __import__("time").sleep(0.1)
        self.assertTrue(hubconf.cwd_users(d), "this machine can tell who is in a folder")
        self.old(d / "repo" / "f", d / "repo", d)
        self.assertEqual(hubconf.prune_private_tmp()["removed"], [])
        self.assertTrue((d / "repo" / "f").exists())

    def test_when_it_cant_find_out_who_is_there_it_keeps_it_and_says_so(self):
        d = self.leftover("unknown")
        with mock.patch.object(hubconf, "process_cwds", lambda: []):
            got = hubconf.prune_private_tmp()
        self.assertEqual((got["removed"], got["cant_tell"]), ([], ["unknown"]))
        self.assertTrue((d / "repo" / "f").exists())

    def test_what_it_cant_empty_is_kept_whole_and_not_offered(self):
        if os.geteuid() == 0:
            self.skipTest("root writes anywhere")
        d = self.leftover("stuck")
        (d / "repo" / "g").write_text("x")
        self.old(d / "repo" / "g", d / "repo", d)
        (d / "repo").chmod(0o500)
        self.addCleanup((d / "repo").chmod, 0o700)
        for dry in (True, False):
            got = hubconf.prune_private_tmp(dry_run=dry)
            self.assertEqual((got["removed"], got["not_removable"]), ([], ["stuck"]), dry)
        self.assertTrue((d / "repo" / "f").exists() and (d / "repo" / "g").exists())

    def test_a_folder_it_cant_read_is_said_not_a_crash(self):
        if os.geteuid() == 0:
            self.skipTest("root reads anywhere")
        self.d.chmod(0)
        self.addCleanup(self.d.chmod, 0o700)
        got = hubconf.prune_private_tmp()
        self.assertTrue(got["unreadable"])
        env = {**os.environ, "AGENT_HUB_SECRETS": str(self.tmp), "AGENT_HUB_ROOT": str(ROOT), "AGENT_HUB_LANG_UI": "en", "HOME": str(self.tmp), "AGENT_HUB_CONFIG": str(self.tmp / "none"),
               "AGENT_HUB_DATA": str(self.tmp / "none"), "AGENT_HUB_STATE": str(self.tmp / "state")}
        r = subprocess.run([sys.executable, str(BIN / "prune-worktrees"), "--auto", "--dry-run"], capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("could not be read", r.stdout)

    def test_a_link_is_removed_not_followed(self):
        target = self.tmp / "keep"
        target.mkdir()
        (target / "f").write_text("x")
        link = self.d / "link"
        link.symlink_to(target)
        self.old(link)
        self.assertEqual(hubconf.prune_private_tmp()["removed"], ["link"])
        self.assertFalse(link.is_symlink())
        self.assertTrue((target / "f").exists())


class ProbeThroughTheRealPath(tbs.Fixture):
    """conformance_claude.agent_run on the platform's path, the agent a stand-in: the request reaches it in the repo, with the variable."""

    def test_llm_runs_it_in_the_folder_with_the_variable(self):
        (self.tmp / "config" / "hub.json").write_text(json.dumps({**json.loads((self.tmp / "config" / "hub.json").read_text()),
                                                                  "runtime": {"default": "claude"}}))
        (self.tmp / "home" / ".claude").mkdir()
        (self.tmp / "home" / ".claude" / "headless-oauth-token").write_text("t\n")
        seen = self.tmp / "seen.json"
        versions = iter(range(400, 500))

        def promote(body):
            """The child's checked copy (its store is <its STATE>/runtimes, this process's hubconf.STATE)."""
            import runtime_store
            v = f"2.1.{next(versions)}"
            runtime_store.install("claude", v, store_fixture.Dist(body))
            runtime_store.promote("claude", v)
        promote(f"#!{sys.executable}\nimport json, os\n"
                f"import sys; json.dump({{'var': os.environ.get({VAR!r}), 'cwd': os.getcwd(), 'argv': sys.argv[1:]}}, open({str(seen)!r}, 'w'))\n"
                "print(json.dumps({'type': 'result', 'result': 'ok', 'is_error': False, 'session_id': 's', 'num_turns': 1}))\n")
        repo = self.tmp / "repo"
        repo.mkdir()
        env = {**self.env, "AGENT_HUB_ROOT": str(ROOT), "AGENT_HUB_PYTHON": sys.executable, "LLM_AUTH": "token"}
        r = subprocess.run([sys.executable, str(BIN / "llm.py"), "run", "login-check", "--cwd", str(repo)], input="ok", cwd=repo,
                           capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        got = json.loads(seen.read_text())
        self.assertEqual((got["var"], got["cwd"]), ("1", str(repo)))
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["llm_runtime_version"], "2.1.400",
                         "each run records the version of the checked copy it ran")
        # both of the probe's runs, the same flags, with the variable and without it
        code = (f"import sys; sys.path.insert(0, {str(BIN)!r}); import conformance_claude as m\n"
                f"import pathlib; print(repr(m.agent_run(pathlib.Path({str(repo)!r}), sys.argv[1] == 'guarded')))\n")
        seen_argv = {}
        for how, want in (("guarded", "1"), ("bare", None)):
            seen.unlink()
            r = subprocess.run([sys.executable, "-c", code, how], cwd=repo, capture_output=True, text=True, timeout=60, env=env)
            self.assertEqual(r.stdout.strip(), "None", r.stdout + r.stderr)
            got = json.loads(seen.read_text())
            self.assertEqual((got["var"], got["cwd"]), (want, str(repo)), how)
            seen_argv[how] = got["argv"]
        self.assertEqual(seen_argv["guarded"], seen_argv["bare"], "the same flags")
        self.assertNotIn("--restricted", seen_argv["guarded"], "a version too old for the flag must still pass the check before an update")
        # an answer it can't count: ended in an error with exit 0, or nothing it can read
        for said in ("json.dumps({'type': 'result', 'result': 'boom', 'is_error': True, 'session_id': 's', 'num_turns': 1})", "'not an answer'"):
            promote(f"#!{sys.executable}\nimport json\nprint({said})\n")
            for how in ("guarded", "bare"):
                with self.subTest(said=said, how=how):
                    r = subprocess.run([sys.executable, "-c", code, how], cwd=repo, capture_output=True, text=True, timeout=60, env=env)
                    self.assertNotEqual(r.stdout.strip(), "None", "not a pass")
                    self.assertTrue(r.stdout.strip(), r.stderr)

    def test_a_first_installs_check_runs_with_an_empty_store(self):
        """A candidate is checked before any copy is promoted: the check's process names the binary under check
        (check_binary), the store is empty, and both of the probe's runs still reach that binary, the guarded one through
        llm.py in the process and the bare one through llm.py --dry-run in a child of its own."""
        (self.tmp / "config" / "hub.json").write_text(json.dumps({**json.loads((self.tmp / "config" / "hub.json").read_text()),
                                                                  "runtime": {"default": "claude"}}))
        (self.tmp / "home" / ".claude").mkdir()
        (self.tmp / "home" / ".claude" / "headless-oauth-token").write_text("t\n")
        import runtime_store
        self.assertIsNone(runtime_store.current("claude"), "nothing promoted")
        seen = self.tmp / "seen.json"
        exe = self.tmp / "candidate" / "node_modules" / ".bin" / "claude"
        exe.parent.mkdir(parents=True)
        exe.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                       f"json.dump({{'var': os.environ.get({VAR!r}), 'cwd': os.getcwd()}}, open({str(seen)!r}, 'w'))\n"
                       "print(json.dumps({'type': 'result', 'result': 'ok', 'is_error': False, 'session_id': 's', 'num_turns': 1}))\n")
        exe.chmod(0o755)
        repo = self.tmp / "repo"
        repo.mkdir()
        env = {**self.env, "AGENT_HUB_ROOT": str(ROOT), "AGENT_HUB_PYTHON": sys.executable}
        code = (f"import sys; sys.path.insert(0, {str(BIN)!r}); import conformance_claude as m, runtime_claude as rc\n"
                f"rc.check_binary({str(exe)!r})\n"
                f"import pathlib; print(repr(m.agent_run(pathlib.Path({str(repo)!r}), sys.argv[1] == 'guarded')))\n")
        for how, want in (("guarded", "1"), ("bare", None)):
            with self.subTest(how):
                seen.unlink(missing_ok=True)
                r = subprocess.run([sys.executable, "-c", code, how], cwd=repo, capture_output=True, text=True, timeout=60,
                                   env=env)
                self.assertEqual(r.stdout.strip(), "None", r.stdout + r.stderr)
                self.assertEqual(json.loads(seen.read_text())["var"], want)

    def on_command(self):
        self.ran = self.tmp / "ran"
        agent = [sys.executable, "-c", f"open({str(self.ran)!r}, 'a').write('x'); print('{{}}')"]
        (self.tmp / "config" / "hub.json").write_text(json.dumps({**json.loads((self.tmp / "config" / "hub.json").read_text()),
                                                                  "runtime": {"default": "command", "command": {"run": agent}}}))
        return {**self.env, "AGENT_HUB_ROOT": str(ROOT), "AGENT_HUB_PYTHON": sys.executable}

    def propose_at(self, folder, env):
        return subprocess.run([sys.executable, str(BIN / "llm.py"), "run", "mailbox-propose", "--cwd", str(folder)], input="ok", cwd=folder,
                              capture_output=True, text=True, timeout=60, env=env)

    def test_a_restricted_point_on_a_runtime_that_cant_runs_only_in_the_marked_copy(self):
        env = self.on_command()
        repo = self.tmp / "repo"
        repo.mkdir()
        for label, folder, e in (("a folder", repo, env), ("a folder marked", repo, {**env, "HUB_THROWAWAY_COPY": str(repo)})):
            with self.subTest(label):
                r = self.propose_at(folder, e)
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertFalse(self.ran.exists(), "the agent never started")
                self.assertIn("mailbox-propose", r.stdout + r.stderr, "what, and why")
        copy = self.tmp / "secrets" / "tmp" / "hub-propose-x-abc" / "ws"
        copy.mkdir(parents=True)
        unmarked = self.propose_at(copy, env)
        self.assertNotEqual(unmarked.returncode, 0, "the place without the mark")
        self.assertFalse(self.ran.exists())
        marked = self.propose_at(copy, {**env, "HUB_THROWAWAY_COPY": str(copy)})
        self.assertTrue(self.ran.exists(), marked.stdout + marked.stderr)

    MEMORY_POINTS = ("consolidate", "librarian-state", "librarian-todo-format")

    def memory_point_at(self, point, folder, env):
        return subprocess.run([sys.executable, str(BIN / "llm.py"), "run", point], input="ok", cwd=folder, capture_output=True, text=True, timeout=60, env=env)

    def memory_copy(self):
        d = self.tmp / "copy"
        (d / "memory" / "reference").mkdir(parents=True)
        (d / ".git").mkdir()   # the platform's own: not read
        (d / ".git" / "config").write_text("")
        for f in ("AGENT.md", "RULES", "STATE.md", "memory/2026-11-08.md", "memory/.gitkeep", "memory/reference/a.md"):
            (d / f).write_text("x\n")
        return d

    def test_an_agent_in_a_copy_of_a_memory_branch_starts_only_if_it_holds_only_notes(self):
        """The platform's own look at the files comes first, on every runtime (AGENTS.md, Tool neutrality); the flag reinforces where a
        runtime has it. The command runtime runs none of them even in a clean copy: a template can't carry the point's limits, so the
        agent would have a shell and read anything (docs/design/runtime-codex.md, step 0)."""
        env = self.on_command()
        d = self.memory_copy()
        for point in self.MEMORY_POINTS:
            with self.subTest(point):
                self.ran.unlink(missing_ok=True)
                r = self.memory_point_at(point, d, env)
                self.assertFalse(self.ran.exists(), "the agent never started")
                self.assertIn("mailbox-propose", r.stdout + r.stderr, "the only point it runs, and why")
        (d / ".claude").mkdir()
        (d / ".claude" / "settings.json").write_text("{}")
        for point in self.MEMORY_POINTS:
            with self.subTest(point, planted=True):
                self.ran.unlink(missing_ok=True)
                r = self.memory_point_at(point, d, env)
                self.assertFalse(self.ran.exists(), "the agent never started")
                self.assertIn(".claude/settings.json", r.stdout + r.stderr, "what, and why")

    def test_a_refusal_is_an_alert_and_a_clean_run_clears_it(self):
        """In this process: llm.py's alert, keyed by whose copy it is (HUB_MEMORY_OF), or by the folder."""
        import contextlib
        import io
        import alert
        import llm
        import runtime_fake
        from runtime_ports import TaskResult
        d = self.memory_copy()
        (d / ".claude").mkdir()
        (d / ".claude" / "settings.json").write_text("{}")
        said = []

        def run(of=None):
            runtime_fake.set_reply("consolidate", TaskResult(True, text="", structured_output={"folded": [], "kept": [], "corrections": []}))
            env = {"HUB_MEMORY_OF": of} if of else {}
            with mock.patch.object(sys, "argv", ["llm.py", "run", "consolidate", "--cwd", str(d)]), mock.patch.dict(os.environ, env), \
                    mock.patch.object(hubconf, "_cfg", {"runtime": {"default": "fake"}}), mock.patch.object(llm, "job_failed", lambda p, r: None), \
                    mock.patch.object(sys, "stdin", io.StringIO("prompt")), mock.patch.object(alert, "once", lambda k, p, ok: said.append((k, p))), \
                    contextlib.redirect_stdout(io.StringIO()):
                try:
                    return llm.main()
                except SystemExit as e:
                    return e.code
        self.addCleanup(runtime_fake.reset)
        self.assertNotEqual(run("box"), 0)
        self.assertEqual([k for k, _p in said], ["memory-copy-refused:box"])
        self.assertIn(".claude/settings.json", said[0][1][0])
        (d / ".claude" / "settings.json").unlink()
        (d / ".claude").rmdir()
        said.clear()
        run("box")
        self.assertEqual(said, [("memory-copy-refused:box", [])], "cleared by a clean run")
        said.clear()
        run()
        self.assertEqual(said, [(f"memory-copy-refused:{os.path.realpath(d)}", [])], "no name given: the folder")

    def test_those_points_ask_for_the_flag_and_the_look(self):
        import yaml
        points = yaml.safe_load((ROOT / "contracts" / "llm-points.yaml").read_text())["points"]
        self.assertEqual({p for p, spec in points.items() if spec.get("works_in") == "memory"}, set(self.MEMORY_POINTS))
        for p in self.MEMORY_POINTS:
            self.assertIs(points[p].get("restricted"), True, p)
            self.assertIn("--restricted", runtime_claude.describe(runtime_ports.Task(point=p, prompt="p", spec=points[p], env={"PATH": ""})), p)

    def test_the_rules_files_names_are_the_adapters(self):
        """Declared by each adapter, collected over every registered runtime, whichever is installed."""
        import importlib
        got = runtime_ports.rules_file_names()
        for name, mod in runtime_ports.RUNTIMES.items():
            self.assertLessEqual(set(getattr(importlib.import_module(mod), "RULES_FILE_NAMES", ())), set(got), name)
        self.assertEqual(runtime_claude.RULES_FILE_NAMES, ("CLAUDE.md", "CLAUDE.local.md"))
        self.assertIn("AGENTS.md", got)

    def test_the_look_at_a_memory_copy(self):
        import memory_paths
        import shutil
        d = self.memory_copy()
        self.assertEqual(memory_paths.folder_problems(d), [])
        (d / "memory" / ".DS_Store").write_text("x")
        (d / "memory" / "reference" / ".DS_Store").write_text("x")
        self.assertEqual(memory_paths.folder_problems(d), [], "Finder's file is in no agent's way")
        for label, make, says in (
                ("settings", lambda: ((d / ".claude").mkdir(), (d / ".claude" / "settings.json").write_text("{}")), ".claude/settings.json"),
                ("a hidden folder alone", lambda: (d / ".claude").mkdir(), ".claude"),
                ("nested", lambda: ((d / "memory" / ".claude").mkdir(), (d / "memory" / ".claude" / "x").write_text("{}")), "memory/.claude"),
                ("a rules file", lambda: (d / "CLAUDE.md").write_text("x"), "CLAUDE.md"),
                ("a link", lambda: (d / "memory" / "l.md").symlink_to("/etc/hosts"), "memory/l.md"),
                ("a folder link", lambda: (d / "memory" / "ld").symlink_to("/etc"), "memory/ld"),
                ("executable", lambda: ((d / "memory" / "x.md").write_text("x"), (d / "memory" / "x.md").chmod(0o755)), "memory/x.md"),
                ("a FIFO", lambda: os.mkfifo(d / "memory" / "f"), "memory/f"),
                ("a nested rules file", lambda: ((d / "memory" / "x").mkdir(), (d / "memory" / "x" / "CLAUDE.md").write_text("x")), "memory/x/CLAUDE.md"),
                ("another runtime's, lower case", lambda: (d / "memory" / "agents.md").write_text("x"), "memory/agents.md")):
            with self.subTest(label):
                shutil.rmtree(d)
                d = self.memory_copy()
                make()
                bad = memory_paths.folder_problems(d)
                self.assertTrue(any(says in x for x in bad), bad)

    def test_login_check_is_refused_on_a_default_command_runtime(self):
        """Its point doesn't ask for the flag, but the command runtime runs nothing outside the throwaway copy (check-claude-login
        doesn't ask it: runtime_ports.throwaway_only)."""
        env = self.on_command()
        repo = self.tmp / "repo"
        repo.mkdir()
        r = subprocess.run([sys.executable, str(BIN / "llm.py"), "run", "login-check", "--cwd", str(repo)], input="ok", cwd=repo,
                           capture_output=True, text=True, timeout=60, env=env)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.ran.exists())
        self.assertIn("login-check", r.stdout + r.stderr)


class ARuntimeThatDoesntSay(unittest.TestCase):
    def test_which_are_unknown(self):
        self.assertFalse(runtime_ports.folder_git_unknown("claude"))
        self.assertFalse(runtime_ports.folder_git_unknown("fake"))
        self.assertFalse(runtime_ports.folder_git_unknown("none"), "it runs nothing")
        self.assertTrue(runtime_ports.folder_git_unknown("acp"))
        self.assertTrue(runtime_ports.folder_git_unknown("command"))


class AForksFolder(tmd.Dispatch):
    """A fork starts where the session's hooks last were only if that really is the project's."""

    def setUp(self):
        super().setUp()
        self.ws = self.ws.resolve()
        self.other = (self.tmp / "other-project").resolve()
        self.other.mkdir()

    def at(self, cwd):
        self.c.execute("INSERT OR REPLACE INTO presence(session, project, cwd, seen, runtime, via) VALUES('S', 'proj', ?, ?, NULL, NULL)",
                       (str(cwd), self.m.iso()))
        return self.m.session_cwd(self.c, "S", self.ws)

    def test_a_folder_in_the_project(self):
        (self.ws / "sub").mkdir()
        self.assertEqual(self.at(self.ws / "sub"), self.ws / "sub")

    def test_swapped_for_a_link_into_another_project(self):
        (self.ws / "sub").symlink_to(self.other)
        self.assertIsNone(self.at(self.ws / "sub"))

    def test_a_folder_elsewhere_or_gone(self):
        self.assertIsNone(self.at(self.other))
        self.assertIsNone(self.at(self.ws / "gone"))

    def test_no_row(self):
        self.assertIsNone(self.m.session_cwd(self.c, "nobody", self.ws))

    def git(self, *a):
        subprocess.run(["git", *map(str, a)], check=True, capture_output=True, env={**os.environ, **tbs.GIT_ENV})

    def test_a_real_worktree_of_it_elsewhere(self):
        self.git("init", "-q", "-b", "main", self.ws)
        (self.ws / "f").write_text("x")
        self.git("-C", self.ws, "add", "f")
        self.git("-C", self.ws, "commit", "-qm", "x")
        wt = (self.tmp / "out").resolve()
        self.git("-C", self.ws, "worktree", "add", "-q", wt, "-b", "w")
        self.assertEqual(self.at(wt), wt)

    def test_another_projects_real_worktree_isnt_one(self):
        other = (self.tmp / "other-repo").resolve()
        self.git("init", "-q", "-b", "main", other)
        (other / "f").write_text("x")
        self.git("-C", other, "add", "f")
        self.git("-C", other, "commit", "-qm", "x")
        wt = (self.tmp / "other-out").resolve()
        self.git("-C", other, "worktree", "add", "-q", wt, "-b", "w")
        self.assertIsNone(self.at(wt))

    def test_a_pointer_naming_it_from_elsewhere_isnt_one(self):
        self.git("init", "-q", "-b", "main", self.ws)
        planted = (self.tmp / "planted").resolve()
        planted.mkdir()
        (planted / ".git").write_text(f"gitdir: {self.ws / '.git'}\n")
        self.assertIsNone(self.at(planted))

    def test_dispatch_starts_the_fork_in_the_projects_folder(self):
        (self.ws / "sub").symlink_to(self.other)
        self.rt.live.add("S")
        self.rt.tokens["S"] = 1000
        self.seen("S", minutes_ago=30)
        self.c.execute("UPDATE presence SET cwd=? WHERE session='S'", (str(self.ws / "sub"),))
        self.send("m1", "proj", "request")
        started = []
        self.m.resume = lambda c, r, sid, cwd, rt, ws=None, prompt=None: started.append(pathlib.Path(cwd).resolve())
        self.dispatch()
        self.assertEqual(started, [self.ws])


class SettingsThroughASwap(twp.Fixture):
    """The settings pass, as the user: it writes down from the workspace, whatever a session folder has become."""

    def setUp(self):
        super().setUp()
        self.target = self.ws["open"] / ".claude" / "settings.local.json"

    def render(self, folder):
        env = {**self.hook_env()}
        return subprocess.run([sys.executable, str(BIN / "render-project-settings.py"), "--worktree", str(folder)], capture_output=True,
                              text=True, timeout=60, env=env)

    def swap(self):
        wts = self.ws["box"] / ".claude" / "worktrees"
        wts.mkdir(parents=True)
        (wts / "w").symlink_to(self.ws["open"])
        return wts / "w"

    def test_a_worktree_swapped_for_a_link_into_another_project_isnt_written_through(self):
        self.render(self.swap())
        self.assertFalse(self.target.exists())

    def test_through_the_script_either(self):
        self.tree("open")
        link = self.swap()
        self.run_hook("start", link, "SessionStart", source="compact")
        self.assertFalse(self.target.exists())

    def test_write_settings_refuses_a_folder_outside_its_workspace(self):
        m = load("render_swap", BIN / "render-project-settings.py")
        doc = {**self.docs["box"], "name": "box"}
        link = self.swap()
        with mock.patch.object(hubconf, "projects", lambda: self.docs), self.assertRaises(ValueError):
            m.write_settings(doc, {"a": 1}, link)
        self.assertFalse(self.target.exists())

    def test_it_walks_down_from_the_workspace_whatever_the_folder_is_called(self):
        """The walk refuses a link at each step: the folder given isn't opened, so it can't change after the look."""
        m = load("render_walk", BIN / "render-project-settings.py")
        doc = {**self.docs["box"], "name": "box"}
        wt = self.ws["box"] / ".claude" / "worktrees" / "w"
        wt.mkdir(parents=True)
        with mock.patch.object(hubconf, "projects", lambda: self.docs), mock.patch.object(m.nofollow, "replace") as put:
            m.write_settings(doc, {"a": 1}, wt)
        self.assertEqual(put.call_args[0][0].resolve(), self.ws["box"].resolve())
        self.assertEqual(str(put.call_args[0][1]), ".claude/worktrees/w/.claude/settings.local.json")

    def test_a_real_folder_of_its_own_is_written(self):
        m = load("render_real", BIN / "render-project-settings.py")
        doc = {**self.docs["box"], "name": "box"}
        wt = self.ws["box"] / ".claude" / "worktrees" / "w"
        wt.mkdir(parents=True)
        with mock.patch.object(hubconf, "projects", lambda: self.docs):
            m.write_settings(doc, {"a": 1}, wt)
        self.assertEqual(json.loads((wt / ".claude" / "settings.local.json").read_text()), {"a": 1})


class TheHourlySync(twp.Fixture):
    def setUp(self):
        super().setUp()
        for name, extra in (("box", "enforce:\n  sandbox: true\n"), ("open", "")):   # the roster reads these
            (self.tmp / "config" / "projects" / f"{name}.yaml").write_text(f"v: 1\nname: {name}\npaths:\n  workspace: {self.ws[name]}\n{extra}")
        roster = subprocess.run([sys.executable, str(BIN / "hubconf.py"), "roster"], capture_output=True, text=True, env=self.hook_env()).stdout
        self.assertIn(str(self.ws["box"]), roster)
        self.assertIn(str(self.ws["open"]), roster)

    def sync(self, **env):
        return subprocess.run(["bash", str(BIN / "sync-all-memory.sh")], capture_output=True, text=True, timeout=180,
                              env={**self.hook_env(), **env}, cwd=self.tmp)

    def test_a_folder_the_registry_doesnt_name_runs_nothing(self):
        left = self.tmp / "home" / "workspace" / "left-behind"
        left.mkdir(parents=True)
        self.plant_memory(left)
        self.assertEqual(self.sync().returncode, 0)
        self.assertEqual(self.ran_as_the_user(), [])
        self.assertFalse(self.marker.exists())

    def test_a_sandboxed_projects_link_at_a_planted_repo_runs_nothing_as_the_user(self):
        """Outside its workspace, somewhere it writes, as the system's temp folder is."""
        ws = self.ws["box"]
        planted = self.tmp / "its-temp"
        planted.mkdir()
        (ws / ".agent-memory").symlink_to(self.plant_memory(planted))
        self.sync()
        self.assertEqual(self.ran_as_the_user(), [])

    def test_the_link_moving_between_the_look_and_the_question(self):
        """The flipper's race with the timing taken out: the sync went in by the link and looked where it is, the link then says
        something else, so that folder is nobody's and git runs there as the user. Going by the name, it never goes in."""
        ws = self.ws["box"]
        planted = self.tmp / "its-temp"
        planted.mkdir()
        (ws / "other").mkdir()
        mem = self.plant_memory(planted)
        (ws / ".agent-memory").symlink_to(mem)
        env = self.tmp / "moves-the-link.sh"
        env.write_text(f"""pwd() {{
  local here; here=$(builtin pwd "$@"); printf '%s\\n' "$here"
  [ "$here" = "{mem.resolve()}" ] && ln -sfn "{ws / 'other'}" "{ws / '.agent-memory'}"
  return 0
}}
""")
        self.sync(BASH_ENV=str(env))
        self.assertEqual(self.ran_as_the_user(), [])

    def test_the_same_link_inside_its_workspace(self):
        ws = self.ws["box"]
        planted = ws / "planted"
        planted.mkdir()
        (ws / ".agent-memory").symlink_to(self.plant_memory(planted))
        self.sync()
        self.assertEqual(self.ran_as_the_user(), [])

    def test_an_open_projects_memory_that_is_a_link_is_skipped(self):
        ws = self.ws["open"]
        planted = self.tmp / "planted"
        planted.mkdir()
        (ws / ".agent-memory").symlink_to(self.plant_memory(planted))
        self.sync()
        self.assertFalse(self.marker.exists(), "nothing ran, as the project or the user")
        self.assertIn("link", "".join(p.read_text() for p in self.log_files()))

    def log_files(self):
        return list((BIN.parent / ".logs").glob("sync-all-memory-*.log")) + list((self.tmp / "state").rglob("sync-all-memory-*.log"))

    def test_the_cli_goes_by_name(self):
        def cli(*a):
            return subprocess.run([sys.executable, str(BIN / "project_git.py"), *a], capture_output=True, text=True, env=self.hook_env(), timeout=60).returncode
        self.assertEqual(cli("sandboxed-project", "box"), 0)
        self.assertEqual(cli("sandboxed-project", "open"), 3)
        self.assertEqual(cli("sync-memory", "open"), 3, "that path is a sandboxed project's job")
        import project_git
        for args in (("sandboxed-project", "nobody"), ("sync-memory", "nobody"), ("run-as", "nobody", str(self.tmp), "--", "true")):
            self.assertEqual(cli(*args), project_git.COULD_NOT_TELL, args)

    def test_a_name_the_roster_gives_and_the_index_lacks_runs_nothing_as_the_user(self):
        self.plant_memory(self.ws["box"])
        index = self.tmp / "state" / "shared" / "registry" / "index.json"
        docs = json.loads(index.read_text())
        del docs["projects"]["box"]
        index.write_text(json.dumps(docs))
        self.assertEqual(self.sync().returncode, 0)
        self.assertEqual(self.ran_as_the_user(), [])
        self.assertFalse(self.marker.exists())
        self.assertIn("box", "".join(p.read_text() for p in self.log_files()), "said")

    def test_as_project_doesnt_pass_a_name_the_index_lacks_through_as_the_user(self):
        import isolation
        with mock.patch.object(hubconf, "projects", lambda: {}), mock.patch.object(hubconf, "roster", lambda: {"box": {}}):
            self.assertIn("--project", isolation.as_project("box", self.tmp, ["git", "status"]), "to bin/sandboxed-run, which refuses it")
            self.assertEqual(isolation.as_project("left-the-registry", self.tmp, ["git", "status"]), ["git", "status"])
            self.assertEqual(isolation.as_project(None, self.tmp, ["git", "status"]), ["git", "status"])

    def test_a_platform_scripts_sync_of_a_name_the_index_lacks_doesnt_run(self):
        import project_git
        with mock.patch.object(hubconf, "projects", lambda: {}), mock.patch.object(project_git.subprocess, "run", side_effect=AssertionError("as the user")):
            project_git.sync_memory_of("box", self.ws["box"])

    def test_git_sync_memory_doesnt_ask_by_folder(self):
        self.assertNotIn("project_git.py", (BIN / "git-sync-memory.sh").read_text().replace("project_git.py sync-memory <name>", ""))

    FLIP = """
import os, sys, time
ws, end, targets = sys.argv[1], time.time() + float(sys.argv[2]), sys.argv[3:]
link, tmp, i = ws + "/.agent-memory", ws + "/.am.tmp", 0
open(ws + "/flip.ok", "w").write("started")
while time.time() < end:
    os.symlink(targets[i % len(targets)], tmp)
    os.replace(tmp, link)
    i += 1
    time.sleep(0.004)
"""

    @unittest.skipUnless(tbs.seatbelt.can_sandbox() is None, "needs Seatbelt")
    def test_a_flipper_under_its_real_profile_gets_nothing(self):
        ws = self.ws["box"]
        (ws / "planted").mkdir()
        (self.tmp / "its-temp").mkdir()
        (ws / "other").mkdir()
        targets = (ws / "other", self.plant_memory(ws / "planted"), self.plant_memory(self.tmp / "its-temp"))
        (self.tmp / "f.py").write_text(self.FLIP)
        flip = subprocess.Popen([tbs.seatbelt.SANDBOX_EXEC, "-p", self.profile("box"), sys.executable, "-I", str(self.tmp / "f.py"),
                                 str(ws), "40", *map(str, targets)], cwd=ws, stderr=subprocess.DEVNULL)
        self.addCleanup(flip.wait)
        self.addCleanup(flip.kill)
        for _i in range(100):
            if (ws / "flip.ok").exists():
                break
            __import__("time").sleep(0.05)
        self.assertTrue((ws / "flip.ok").exists(), "it runs under the profile")
        for _i in range(int(os.environ.get("FLIP_ROUNDS", "3"))):
            self.sync()
            self.assertEqual(self.ran_as_the_user(), [])


class SystemTempIsNoPlaceForIt(unittest.TestCase):
    """The system's temp folder is every sandboxed project's to write. What the platform runs, loads or trusts as the user goes in
    hubconf.private_tmp; these are the uses left, each with its reason in docs/design/mailbox-session-first.md. A new one has to say why.
    A hint, not a proof: it reads lines, so a call split over lines or a path built elsewhere goes unseen."""
    LEFT = {"bin/gen-index-roster.sh", "bin/config-check", "bin/run-tests", "bin/test-runner.py", "bin/sync-local-setup.sh", "bin/runtime_acp.py", "bin/seatbelt.py", "bin/landlock.py",
            "bin/cap-runner.py", "bin/linux-test", "bin/lint", "bin/conformance.py",
            # they name the temp area only to deny control names there, and a canary stages a tree there to prove it
            # (docs/design/runtime-codex.md, Step 4 r2, review A-B2); nothing of the platform's goes there
            "bin/runtime_claude.py", "bin/conformance_claude.py"}
    USE = __import__("re").compile(r"tempfile[.](?:mkdtemp|TemporaryDirectory|NamedTemporaryFile|mkstemp|gettempdir|TemporaryFile)[(]"
                                   r"|[$][(]mktemp|[\"' ]/tmp[/\"' ]|/private/tmp|/var/folders|TMPDIR")
    MOVED = __import__("re").compile(r"dir=(?:hubconf[.])?(?:private_tmp[(][)]|STATE\b)")   # both denied to every sandbox

    def test_only_those_two_folders_count_as_moved(self):
        for line in ("tempfile.mkdtemp(dir=other)", "tempfile.mkdtemp(prefix='x', dir=hubconf.STATEFUL)", 'TMPDIR="$tmp" run'):
            self.assertTrue(self.USE.search(line) and not self.MOVED.search(line), line)
        for line in ("tempfile.mkdtemp(dir=hubconf.private_tmp())", "tempfile.mkdtemp(dir=hubconf.STATE)", "tempfile.mkdtemp(dir=private_tmp())"):
            self.assertTrue(self.MOVED.search(line), line)

    def test_no_new_one(self):
        found = set()
        for f in sorted((ROOT / "bin").rglob("*")):
            rel = str(f.relative_to(ROOT))
            if not f.is_file() or rel.startswith("bin/evals/") or "__pycache__" in rel:
                continue
            try:
                text = f.read_text()
            except UnicodeDecodeError:
                continue
            for line in text.splitlines():
                if self.USE.search(line) and not self.MOVED.search(line) and not line.lstrip().startswith("#"):
                    found.add(rel)
        self.assertEqual(sorted(found - self.LEFT), [])


class ThePlatformsOwnSync(twp.Fixture):
    def test_a_sandboxed_projects_goes_as_the_project(self):
        import project_git
        with mock.patch.object(hubconf, "projects", lambda: self.docs), \
                mock.patch.object(project_git, "sync_memory") as job, \
                mock.patch.object(project_git.subprocess, "run", side_effect=AssertionError("as the user")):
            project_git.sync_memory_of("box", self.ws["box"])
        self.assertEqual(job.call_args[1], {"lock": True})

    def test_an_open_projects_goes_the_hooks_way(self):
        import project_git
        done = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(hubconf, "projects", lambda: self.docs), mock.patch.object(project_git.subprocess, "run", return_value=done) as run:
            self.assertIsNone(project_git.sync_memory_of("open", self.ws["open"]))
        self.assertTrue(str(run.call_args[0][0][0]).endswith("with-lock.sh"))
        failed = subprocess.CompletedProcess([], 1, "", "a reason\n")
        with mock.patch.object(hubconf, "projects", lambda: self.docs), mock.patch.object(project_git.subprocess, "run", return_value=failed), \
                mock.patch("sys.stderr"):
            self.assertIn("a reason", project_git.sync_memory_of("open", self.ws["open"]), "the reason comes back to the caller")
        with mock.patch.object(hubconf, "projects", lambda: self.docs), mock.patch.object(project_git, "sync_memory", return_value="box: refused"):
            self.assertEqual(project_git.sync_memory_of("box", self.ws["box"]), "box: refused")


if __name__ == "__main__":
    unittest.main()
