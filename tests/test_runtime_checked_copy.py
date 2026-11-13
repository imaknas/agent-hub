"""Platform runs start the store's checked copy, never one on PATH (docs/design/runtime-versions.md section 3 and
step 2's plan r2, items 4, 5, 6 and 7): the no-copy split (the ports that start runs refused with the adapter's reason,
the ones that describe the user's sessions kept), the hold on the version in use (across exec too), the run
environment by what the run is, the user's folder never moved by the platform's config dir, a terminal resume, and a
completion's tools checked as it runs. Against stand-in stores (tests/store_fixture.py): no npm, no model.

    bin/py -m unittest tests.test_runtime_checked_copy
"""
import fcntl
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import runtime_claude  # noqa: E402
import runtime_ports  # noqa: E402
import runtime_store  # noqa: E402
import store_fixture  # noqa: E402

TOKEN = runtime_claude.TOKEN_VAR
SID = "11111111-2222-4333-8444-555555555555"


def task(env, spec=None, extra=()):
    return runtime_ports.Task(point="p", prompt="hi", spec=spec if spec is not None else {}, env=env, extra=tuple(extra))


class NoCopy(unittest.TestCase):
    """An empty store (a fresh install, a restore): what starts platform runs says why; what describes the user's
    sessions, and every protection, stays."""

    def setUp(self):
        store_fixture.empty(self)
        _copy, self.why = runtime_claude.checked_copy()

    def test_it_says_why_and_the_adapter_says_it(self):
        self.assertTrue(self.why)
        self.assertEqual(runtime_ports.no_checked_copy("claude"), self.why)
        with self.assertRaises(runtime_ports.NoCheckedCopy) as e:
            runtime_claude.binary()
        self.assertEqual(str(e.exception), self.why)

    def test_the_ports_that_start_runs_are_refused_the_others_load(self):
        for port in runtime_ports.PLATFORM_RUNS:
            with self.subTest(port=port), self.assertRaises(runtime_ports.NoCheckedCopy) as e:
                runtime_ports.load("claude", ports=(port,))
            self.assertEqual(str(e.exception), self.why)
            self.assertIsInstance(e.exception, runtime_ports.Unsupported, "callers that handle Unsupported handle it")
        for port in ("lifecycle", "control", "sessions", "auth", "instructions", "tool_servers", "rule_check",
                     "session_status", "wake"):
            with self.subTest(port=port):
                runtime_ports.load("claude", ports=(port,))
        runtime_ports.load("claude", ports=("agent", "stream"), platform_runs=False)   # a reader starts nothing

    def test_capabilities_are_unchanged_so_a_sandboxed_projects_hooks_stay(self):
        """hub-event and render-project-settings keep a sandboxed project's hooks while CAGED_NEEDS <= capabilities."""
        have = runtime_ports.capabilities("claude")
        self.assertLessEqual({"control", "confine", "lifecycle", "agent", "completion", "stream"}, have)
        store_fixture.stand_in(self)
        self.assertEqual(runtime_ports.capabilities("claude"), have)

    def test_a_sandboxed_projects_settings_are_the_same_with_no_copy(self):
        """The deny rules and the platform's hooks a sandboxed project's sessions get don't depend on the store."""
        import policy
        ws = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(__import__("shutil").rmtree, ws, True)
        pol = {"other_projects": [], "deny_write": [], "deny_read": [], "platform_commands": [], "write": [],
               "network": [], "workspace": ws, "protect": policy.protect()}
        self.assertTrue(pol["protect"], "the control port is read with no copy")
        empty = (runtime_claude.settings(pol), runtime_claude.platform_hooks(), runtime_claude.session_env(pol))
        self.assertTrue(empty[0][1], "deny rules")
        store_fixture.stand_in(self)
        self.assertEqual((runtime_claude.settings(pol), runtime_claude.platform_hooks(), runtime_claude.session_env(pol)),
                         empty)

    def test_what_reads_through_it_says_why_instead_of_raising(self):
        self.assertEqual(runtime_claude.session_statuses(["a", "b"]), {"a": "unlisted", "b": "unlisted"})
        self.assertEqual(runtime_claude.session_status("a"), "unlisted", "routed, never woken")
        self.assertEqual(runtime_claude.health({"auth": "subscription"}), (False, self.why))
        with self.assertRaises(ValueError) as e:
            runtime_claude._listed_servers(task(dict(os.environ)))
        self.assertEqual(str(e.exception), self.why)
        res = runtime_claude.run(task({"PATH": os.environ["PATH"]}))
        self.assertEqual((res.ok, res.error), (False, self.why))
        got = runtime_claude.complete(runtime_ports.CompletionRequest(point="p", prompt="hi"), {"auth": "subscription"})
        self.assertEqual((got.ok, got.error), (False, self.why))
        self.assertEqual(runtime_claude.describe(task({}))[0], "claude", "a description names it, never runs it")

    def test_llm_ready_reads_it(self):
        import llm_ready
        import completion
        with mock.patch.object(completion, "settings", lambda: None), \
                mock.patch.object(runtime_ports, "default_name", lambda: "claude"):
            self.assertEqual(llm_ready.unsupported("librarian-state"), self.why)
            self.assertEqual(llm_ready.unsupported("librarian-state-edits"), self.why)
            store_fixture.stand_in(self)
            self.assertIsNone(llm_ready.unsupported("librarian-state"))
            self.assertIsNone(llm_ready.unsupported("librarian-state-edits"))

    def test_a_copy_that_changed_after_its_check_is_no_copy(self):
        exe = store_fixture.stand_in(self)
        self.assertEqual(runtime_claude.binary(), str(exe))
        self.assertIsNone(runtime_ports.no_checked_copy("claude"))
        exe.write_text(exe.read_text() + "# changed\n")
        copy, why = runtime_claude.checked_copy()
        self.assertIsNone(copy)
        self.assertTrue(why)
        with self.assertRaises(runtime_ports.NoCheckedCopy):
            runtime_claude.binary()

    def test_a_conformance_check_counts_the_copy_under_check(self):
        """The first install's checks run before any copy is promoted: the binary under check is the copy."""
        runtime_claude.check_binary("/store/2.1.400/node_modules/.bin/claude")
        self.addCleanup(runtime_claude.check_binary, None)
        self.assertEqual(runtime_claude.checked_copy(), (None, None))
        self.assertIsNone(runtime_ports.no_checked_copy("claude"))
        runtime_ports.load("claude", ports=("agent", "completion"))


class Hold(unittest.TestCase):
    """One shared flock per process on the version in use, which prune can't take; let go when current moves on, and
    passed on across exec (a lease, a resume in a terminal)."""

    def setUp(self):
        self.exe = store_fixture.stand_in(self)
        self.folder = self.exe.parents[2]

    def taken(self, folder) -> bool:
        fd = os.open(folder / ".lock", os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except OSError:
            return True
        finally:
            os.close(fd)

    def test_held_while_in_use_and_let_go_when_current_moves(self):
        runtime_claude.binary()
        runtime_claude.binary()
        self.assertTrue(self.taken(self.folder))
        self.assertEqual(len([n for n in runtime_store._HELD if n == "claude"]), 1)
        runtime_store.install("claude", "2.1.401", store_fixture.Dist(store_fixture.ANSWER))
        runtime_store.promote("claude", "2.1.401")
        self.assertTrue(runtime_claude.binary().startswith(str(self.folder.parent / "2.1.401")))
        self.assertFalse(self.taken(self.folder), "the old version is let go")
        self.assertTrue(self.taken(self.folder.parent / "2.1.401"))

    def test_the_hold_passes_across_exec(self):
        """A process that holds the copy and then execs keeps holding it (exec_stream, resume_exec): until the exec'd
        process ends, the version folder can't be taken."""
        state = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(__import__("shutil").rmtree, state, True)
        with mock.patch.object(hubconf, "runtimes_dir", lambda: state / "runtimes"):
            runtime_store.install("claude", "2.1.400", store_fixture.Dist(store_fixture.ANSWER))
            runtime_store.promote("claude", "2.1.400")
        import names
        env = names.env_set(dict(os.environ), "STATE", str(state))
        gate = state / "go"
        code = (f"import os, sys; sys.path.insert(0, {str(BIN)!r}); import runtime_claude, runtime_store\n"
                "runtime_claude.binary(); runtime_store.inheritable('claude')\n"
                f"os.execv('/bin/sh', ['sh', '-c', 'touch {state}/ready; while [ ! -e {gate} ]; do sleep 0.05; done'])\n")
        p = subprocess.Popen([sys.executable, "-c", code], env=env)
        self.addCleanup(lambda: (gate.touch(), p.wait(timeout=10)))
        for _i in range(200):
            if (state / "ready").exists():
                break
            __import__("time").sleep(0.05)
        self.assertTrue((state / "ready").exists(), "the child ran")
        self.assertTrue(self.taken(state / "runtimes" / "claude" / "2.1.400"), "the exec'd process holds it")
        gate.touch()
        p.wait(timeout=10)
        self.assertFalse(self.taken(state / "runtimes" / "claude" / "2.1.400"))

    def test_exec_stream_makes_it_inheritable(self):
        with mock.patch.object(runtime_claude.os, "execvpe") as ex:
            runtime_claude.exec_stream(task({"PATH": os.environ["PATH"]}, spec={"shape": "agent"}))
        self.assertEqual(ex.call_args[0][0], str(self.exe))
        self.assertTrue(os.get_inheritable(runtime_store._HELD["claude"][1]))


class RunEnvironment(unittest.TestCase):
    """The table of item 5: the config dir and the refusing `security` by what the run is; the copy first on PATH, its
    updater pinned, nvm's after it."""

    def setUp(self):
        self.exe = store_fixture.stand_in(self)
        self.folder = self.exe.parents[2]
        self.base = {"PATH": "/usr/bin:/bin", TOKEN: "t"}

    def head(self, env, n=2):
        return env["PATH"].split(":")[:n]

    def test_a_token_run_that_neither_resumes_nor_forks(self):
        env = runtime_claude._env(task(self.base, spec={"shape": "completion"}))
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(runtime_claude.platform_config_dir()))
        self.assertTrue(runtime_claude.platform_config_dir().is_dir())
        stub = runtime_claude._store() / "stub"
        self.assertEqual(self.head(env), [str(stub), str(self.exe.parent)])
        self.assertEqual((env["DISABLE_AUTOUPDATER"], env["npm_config_prefix"]), ("1", str(self.folder)))
        self.assertEqual(env[TOKEN], "t")
        self.assertEqual((stub / "security").read_text(), runtime_claude.SECURITY_STUB)
        self.assertEqual(subprocess.run([str(stub / "security"), "find-generic-password"]).returncode, 44)
        self.assertEqual(runtime_claude._env(SimpleNamespace(env=self.base), own=True)["CLAUDE_CONFIG_DIR"],
                         str(runtime_claude.platform_config_dir()), "every completion")

    def test_other_token_runs_stay_on_the_users_folder(self):
        cases = {"an agent-shaped point": task(self.base, spec={"shape": "agent"}),
                 "a fork": task(self.base, spec={"shape": "completion"}, extra=["--resume", SID, "--fork-session"]),
                 "a resume in its declaration": task(self.base, spec={"shape": "completion", "extra_args": [f"--resume={SID}"]}),
                 "no declaration": task(self.base),
                 "the platform's dir handed down": task({**self.base, "CLAUDE_CONFIG_DIR": str(runtime_claude.platform_config_dir())},
                                                        spec={"shape": "agent"})}
        for what, t in cases.items():
            with self.subTest(what):
                env = runtime_claude._env(t)
                self.assertNotIn("CLAUDE_CONFIG_DIR", env)
                self.assertEqual(self.head(env), [str(runtime_claude._store() / "stub"), str(self.exe.parent)])
                self.assertEqual(runtime_claude.run_kind(t), "shared")
        mine = runtime_claude._env(task({**self.base, "CLAUDE_CONFIG_DIR": "/elsewhere"}, spec={"shape": "agent"}))
        self.assertEqual(mine["CLAUDE_CONFIG_DIR"], "/elsewhere", "a caller's own config dir is left as it is")

    def test_no_token_no_stub(self):
        """The subscription login (login-check, connectors jobs) reads the Keychain: no stub, the user's folder."""
        env = runtime_claude._env(task({"PATH": "/usr/bin:/bin"}, spec={"shape": "completion"}))
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)
        self.assertEqual(self.head(env, 1), [str(self.exe.parent)])
        self.assertNotIn(str(runtime_claude._store() / "stub"), env["PATH"].split(":"))
        self.assertEqual(env["DISABLE_AUTOUPDATER"], "1")

    def test_nothing_it_starts_finds_the_users_claude_by_name(self):
        user = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(__import__("shutil").rmtree, user, True)
        (user / "claude").write_text("#!/bin/sh\necho user\n")
        (user / "claude").chmod(0o755)
        env = runtime_claude._env(task({"PATH": f"{user}:/usr/bin:/bin", TOKEN: "t"}, spec={"shape": "agent"}))
        import shutil
        self.assertEqual(shutil.which("claude", path=env["PATH"]), str(self.exe))

    def test_a_token_run_refuses_when_its_stub_cant_be_there(self):
        store = runtime_claude._store()
        (store / "stub").mkdir(exist_ok=True)
        (store / "stub").chmod(0o500)
        self.addCleanup((store / "stub").chmod, 0o700)
        if os.geteuid() == 0:
            self.skipTest("root writes anywhere")
        with self.assertRaises(ValueError):
            runtime_claude._env(task(self.base, spec={"shape": "agent"}))
        res = runtime_claude.run(task(self.base, spec={"shape": "agent"}))
        self.assertFalse(res.ok)
        runtime_claude._env(task({"PATH": "/usr/bin"}, spec={"shape": "agent"}))   # no token: no stub needed

    def test_in_a_conformance_check_the_world_decides(self):
        runtime_claude.check_binary("/w/store/2.1.400/node_modules/.bin/claude")
        self.addCleanup(runtime_claude.check_binary, None)
        env = runtime_claude._env(task({**self.base, "CLAUDE_CONFIG_DIR": "/w/tool"}, spec={"shape": "completion"}))
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/w/tool")
        self.assertEqual(self.head(env, 1), ["/w/store/2.1.400/node_modules/.bin"])
        self.assertNotIn("npm_config_prefix", env)

    def test_the_run_records_its_version(self):
        res = runtime_claude.run(task({"PATH": "/usr/bin:/bin"}, spec={"shape": "agent"}))
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.runtime_version, store_fixture.VERSION)


class UsersFolder(unittest.TestCase):
    """_home() feeds credential_paths() and user_control(), which become every sandboxed project's denies: the
    platform's config dir in the environment never moves them off ~/.claude (DV3)."""

    def setUp(self):
        store_fixture.empty(self)

    def test_the_platform_dir_is_ignored(self):
        home = pathlib.Path.home() / ".claude"
        for spelled in (str(runtime_claude.platform_config_dir()), str(runtime_claude.platform_config_dir()) + "/"):
            with self.subTest(spelled), mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": spelled}):
                self.assertEqual(runtime_claude._home(), home)
                self.assertEqual(runtime_claude._user_config_file(), pathlib.Path.home() / ".claude.json")
                self.assertEqual(runtime_claude.credential_paths()["files"][0], str(home / ".credentials.json"))
                self.assertTrue(all(p.parent == home or p == pathlib.Path.home() / ".claude.json"
                                    for p, _tree in runtime_claude.user_control()))

    def test_a_users_own_config_dir_still_counts(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/somewhere/mine"}):
            self.assertEqual(runtime_claude._home(), pathlib.Path("/somewhere/mine"))
            self.assertEqual(runtime_claude._user_config_file(), pathlib.Path("/somewhere/mine/.claude.json"))


class TerminalResume(unittest.TestCase):
    """bin/lease-resume goes through resume_exec: the checked copy by its path, its updater off, the hold passed on;
    the token and the stub only when the copy isn't logged in itself."""

    def setUp(self):
        self.home = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(__import__("shutil").rmtree, self.home, True)
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "headless-oauth-token").write_text("tok\n")
        p = mock.patch.dict(os.environ, {"HOME": str(self.home), "PATH": "/usr/bin:/bin"})
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop(TOKEN, None)

    def copy(self, logged_in: bool):
        status = json.dumps({"loggedIn": logged_in})
        return store_fixture.stand_in(self, body=f"#!/bin/sh\n[ \"$1\" = auth ] && echo '{status}'\nexit 0\n")

    def test_logged_in(self):
        exe = self.copy(True)
        argv, env = runtime_claude.resume_exec(SID)
        self.assertEqual(argv, [str(exe), "--resume", SID])
        self.assertNotIn(TOKEN, env)
        self.assertEqual(env["PATH"].split(":")[0], str(exe.parent))
        self.assertEqual(env["DISABLE_AUTOUPDATER"], "1")
        self.assertNotIn("CLAUDE_CONFIG_DIR", env, "the session is stored in the user's folder")
        self.assertTrue(os.get_inheritable(runtime_store._HELD["claude"][1]))

    def test_not_logged_in_brings_the_token_and_the_stub(self):
        exe = self.copy(False)
        _argv, env = runtime_claude.resume_exec(SID)
        self.assertEqual(env[TOKEN], "tok")
        self.assertEqual(env["PATH"].split(":")[:2], [str(runtime_claude._store() / "stub"), str(exe.parent)])
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)

    def test_the_script_imports_nothing_from_the_leases_folder(self):
        """bin/lease-resume chooses the binary and its environment before it enters the lease's folder, which the
        project writes: a module there named like the standard library's, or like the platform's, is never imported;
        only the exec'd tool starts there."""
        tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        hub, ws, marker, out = tmp / "hub", tmp / "ws", tmp / "imported", tmp / "out"
        (hub / ".state" / "lease-runner").mkdir(parents=True)
        (hub / "bin").symlink_to(BIN)
        (hub / ".state" / "lease-runner" / "leases.json").write_text(json.dumps({SID: {"cwd": str(ws)}}))
        ws.mkdir()
        for name in ("json", "fcntl", "runtime_ports", "hashlib", "encodings"):
            (ws / f"{name}.py").write_text(f"open({str(marker)!r}, 'a').write({name!r} + '\\n')\n")
        import names
        env = names.env_set({**os.environ, "HOME": str(self.home)}, "STATE", str(tmp / "state"))
        names.env_set(env, "PYTHON", sys.executable)
        run = lambda: subprocess.run([str(hub / "bin" / "lease-resume"), SID], cwd=ws, env=env, capture_output=True,  # noqa: E731
                                     text=True, timeout=60, stdin=subprocess.DEVNULL)
        r = run()   # an empty store: it says why and starts nothing
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(marker.exists(), marker.read_text() if marker.exists() else "")
        store_fixture.promote_into(tmp / "state" / "runtimes", body=(
            "#!/bin/sh\n[ \"$1\" = auth ] && { echo '{\"loggedIn\": true}'; exit 0; }\n"
            f"pwd > '{out}'\necho \"$@\" >> '{out}'\n"))
        r = run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(marker.exists(), marker.read_text() if marker.exists() else "")
        self.assertEqual(out.read_text().split("\n")[:2], [str(ws), f"--resume {SID}"], "the tool starts in the folder")

    def test_no_copy_or_no_session_id(self):
        store_fixture.empty(self)
        with self.assertRaises(runtime_ports.NoCheckedCopy):
            runtime_claude.resume_exec(SID)
        with self.assertRaises(ValueError):
            runtime_claude.resume_exec("--evil")


class CompletionTools(unittest.TestCase):
    """Section 4 for Claude: a completion reads stream-json; tools beyond the pinned set in the init event end the run
    and refuse the result, as does a tool_use of another name, or no init event at all; the owner is alerted once."""

    def setUp(self):
        self.alerts = []
        import alert
        p = mock.patch.object(alert, "once", lambda key, problems, *a, **k: self.alerts.append((key, list(problems))))
        p.start()
        self.addCleanup(p.stop)
        self.private = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(__import__("shutil").rmtree, self.private, True)
        for p in (mock.patch.object(hubconf, "private_tmp", lambda: self.private),
                  mock.patch.object(runtime_claude, "auth_env", lambda mode, env: ({**env, TOKEN: "t"}, ""))):
            p.start()
            self.addCleanup(p.stop)

    def answering(self, *events, result=None):
        lines = [json.dumps(e) for e in events]
        lines.append(json.dumps(result or {"type": "result", "result": "ok", "is_error": False, "structured_output": None}))
        self.argv = self.private / "argv"
        body = "#!/bin/sh\ncat > /dev/null\n" + f"printf '%s\\n' \"$@\" > '{self.argv}'\n" + "".join(
            f"printf '%s\\n' '{line}'\n" for line in lines)
        store_fixture.stand_in(self, body=body)

    def complete(self, schema=None):
        return runtime_claude.complete(runtime_ports.CompletionRequest(point="p", prompt="hi", schema=schema), {})

    def init(self, *tools):
        return {"type": "system", "subtype": "init", "tools": list(tools)}

    def test_a_clean_run(self):
        self.answering(self.init())
        got = self.complete()
        self.assertTrue(got.ok, got.error)
        self.assertEqual((got.text, got.runtime_version), ("ok", store_fixture.VERSION))
        argv = self.argv.read_text().split("\n")
        self.assertEqual(argv.count("stream-json"), 1)
        self.assertEqual(argv.count("--verbose"), 1)
        self.assertEqual(self.alerts, [("completion-tools:claude", [])], "a clean run says the stop is over")

    def test_a_schema_allows_only_structured_output(self):
        self.answering(self.init("StructuredOutput"),
                       result={"type": "result", "result": "", "is_error": False, "structured_output": {"a": 1}})
        got = self.complete(schema={"type": "object"})
        self.assertTrue(got.ok, got.error)
        self.assertEqual(json.loads(got.text), {"a": 1})
        self.answering(self.init("StructuredOutput"))
        self.assertFalse(self.complete().ok, "no schema, no tool at all")

    def test_an_extra_tool_in_the_init_event_refuses(self):
        self.answering(self.init("Bash", "Read"))
        got = self.complete()
        self.assertFalse(got.ok)
        self.assertIn("Bash", got.error)
        self.assertEqual(self.alerts[-1][0], "completion-tools:claude")
        self.assertTrue(self.alerts[-1][1])

    def test_a_tool_use_of_another_name_refuses(self):
        use = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "WebFetch", "input": {}}]}}
        self.answering(self.init(), use)
        got = self.complete()
        self.assertFalse(got.ok)
        self.assertIn("WebFetch", got.error)
        self.assertTrue(self.alerts[-1][1])

    def test_no_init_event_refuses(self):
        self.answering()
        self.assertFalse(self.complete().ok)
        self.assertTrue(self.alerts[-1][1])

    def test_an_error_with_no_init_event_is_the_runs_own_failure(self):
        """A login that failed ends before any init event: said as the run's error, not as a tools refusal, no alert."""
        self.answering(result={"type": "result", "is_error": True, "result": "Invalid API key · Please run /login"})
        got = self.complete()
        self.assertFalse(got.ok)
        self.assertIn("Invalid API key", got.error)
        self.assertTrue(runtime_claude.is_login_error(got.error))
        self.assertEqual(self.alerts, [("completion-tools:claude", [])], "nothing refused")

    def test_its_error_output_is_read_to_the_end(self):
        store_fixture.stand_in(self, body="#!/bin/sh\ncat > /dev/null\necho 'the tool said why' >&2\nexit 3\n")
        got = self.complete()
        self.assertFalse(got.ok)
        self.assertIn("the tool said why", got.error)

    def test_conformance_reads_the_same_argv(self):
        import conformance_claude
        store_fixture.stand_in(self)
        argv = conformance_claude._completion_argv()
        self.assertEqual((argv.count("stream-json"), argv.count("--verbose")), (1, 1))
        self.assertEqual(argv[:argv.index("--tools")], runtime_claude.completion_command(
            runtime_ports.CompletionRequest(point="login-check", prompt="x", model=conformance_claude.FAST))[1:][:argv.index("--tools")])
        self.assertNotIn("--tools", conformance_claude._completion_argv(tools_off=False))


class InitFindsTheAgent(unittest.TestCase):
    """init's agent question: either the user's own copy or the store's checked copy is the agent being there, so --yes
    keeps choosing Claude Code on an empty store."""

    def setUp(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("agenthub_checked_copy", str(BIN / "agenthub"))
        self.ah = importlib.util.module_from_spec(importlib.util.spec_from_loader("agenthub_checked_copy", loader))
        loader.exec_module(self.ah)
        self.info = runtime_claude.install_info()
        self.user = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(__import__("shutil").rmtree, self.user, True)
        for p in (mock.patch.object(hubconf, "nvm_bin", lambda: None),
                  mock.patch.dict(os.environ, {"PATH": str(self.user)})):
            p.start()
            self.addCleanup(p.stop)

    def test_either_copy_counts(self):
        store_fixture.empty(self)
        self.assertIsNone(self.ah.agent_found(self.info, "claude"))
        (self.user / "claude").write_text("#!/bin/sh\n")
        (self.user / "claude").chmod(0o755)
        self.assertEqual(self.ah.agent_found(self.info, "claude"), self.user / "claude", "the user's own, empty store")
        (self.user / "claude").unlink()
        exe = store_fixture.stand_in(self)
        self.assertEqual(self.ah.agent_found(self.info, "claude"), exe, "the store's")
        self.assertIsNone(self.ah.agent_found(self.info), "what runs a command by name needs the user's own")

    def test_a_token_with_no_copy_of_the_users_still_installs_the_platforms(self):
        """The token is there (made earlier, or restored) and the user has no copy of their own: the platform's copy is
        still installed and checked with it, then the login checked."""
        store_fixture.empty(self)
        home = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(__import__("shutil").rmtree, home, True)
        (home / ".claude").mkdir()
        (home / ".claude" / "headless-oauth-token").write_text("t\n")
        steps = []
        with mock.patch.dict(os.environ, {"HOME": str(home)}), \
                mock.patch.object(self.ah, "store_install", lambda choice: steps.append(("install", choice)) or True), \
                mock.patch.object(self.ah, "agent_login_check", lambda: steps.append(("login",)) and None), \
                mock.patch("builtins.print"):
            self.ah.init_dialogue.use(self.ah.init_dialogue.Terminal(True))
            presets = {"claude": runtime_claude.install_info()}
            self.ah.agent_credential("claude", presets)
        self.assertEqual(steps, [("install", "claude"), ("login",)])

    def test_store_install_says_why_it_cant(self):
        store_fixture.empty(self)
        import io
        import contextlib
        out = io.StringIO()
        with mock.patch.object(self.ah.subprocess, "run") as run, contextlib.redirect_stdout(out):
            self.assertFalse(self.ah.store_install("claude"))
        run.assert_not_called()
        self.assertIn(runtime_claude.install_blocker(), out.getvalue(), "no nvm here: said, nothing run")
        store_fixture.stand_in(self)
        with mock.patch.object(self.ah.subprocess, "run") as run:
            self.assertTrue(self.ah.store_install("claude"))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
