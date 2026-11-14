"""Runtime contract tests (docs/design/runtime-abstraction.md): every implementation registered in runtime_ports runs
the same set of checks.

The same scenario is put into each implementation (Claude: written as a real jsonl transcript; Fake: the data placed
directly), then checked with the same assertions: the interface's semantics are tested, not agreed on. An
implementation in the registry without a matching scenario setup fails the test outright: a new implementation cannot
skip the contract tests.

    bin/py -m unittest discover -s tests
"""
import datetime
import json
import os
import pathlib
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import runtime_ports  # noqa: E402
import hubconf as _hubconf  # noqa: E402
from sessions import Turn  # noqa: E402  the scenario's entries (Claude's fixture writes them as transcripts)

UTC = datetime.timezone.utc
T0 = datetime.datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
WS = "/Users/someone/workspace/proj"
SID = "aaaaaaaa-1111-4111-8111-111111111111"
SID2 = "bbbbbbbb-2222-4222-8222-222222222222"   # another project's session (a real session is in one project only)

# Scenario: one human message, one system injection (not human), one assistant turn that calls a subagent, a skill,
# runs a command (not collaboration), one tool result; one human message in another project
SCENARIO = [
    (WS, Turn(T0, SID, "human", "regarde les tests, s'il te plaît", id="e1", folder=WS)),
    (WS, Turn(T0 + datetime.timedelta(minutes=1), SID, "other", id="e2", folder=WS)),
    (WS, Turn(T0 + datetime.timedelta(minutes=2), SID, "assistant", "",
                            (runtime_ports.Action("other", "", "subagent"), runtime_ports.Action("other", "dataviz", "skill"),
                             runtime_ports.Action("run_command", call="call-1", command="ls")), id="e3", folder=WS)),
    (WS, Turn(T0 + datetime.timedelta(minutes=3), SID, "other", id="e4", folder=WS, results=("call-1",))),
    ("/Users/someone/workspace/other", Turn(T0, SID2, "human", "l'autre projet")),
]
# an entry of a subagent the session started: counts only with turns(subagents=True)
SUBAGENT_SCENARIO = [(WS, Turn(T0 + datetime.timedelta(minutes=4), SID, "assistant", "",
                                             (runtime_ports.Action("run_command", call="call-2", command="pytest -q"),),
                                             id="s1", folder=WS, subagent=True))]
# one human message from a session in a worktree the runtime opened under the same project: counts only with
# turns(worktrees=True)
SID3 = "cccccccc-3333-4333-8333-333333333333"
WORKTREE_SCENARIO = [(WS, Turn(T0 + datetime.timedelta(seconds=30), SID3, "human", "un message du worktree"))]


# three scenarios for "run once": completed normally, a result but not completed normally (e.g. turn limit), failed
RUN_OK = {"text": "done", "structured_output": {"x": 1}, "cost": 0.5, "session": "s1", "turns": 2}


def _iso(t):
    return t.isoformat().replace("+00:00", "Z")


def setUpModule():
    """The runtimes' temp folders, away from the tree's own."""
    d = pathlib.Path(tempfile.mkdtemp()).resolve()
    unittest.addModuleCleanup(shutil.rmtree, d, ignore_errors=True)
    p = mock.patch.object(_hubconf, "secrets_dir", lambda: d)
    p.start()
    unittest.addModuleCleanup(p.stop)


class ClaudeFixture:
    """Write the scenario in Claude Code's transcript format (~/.claude/projects/<slug>/<session>.jsonl)."""

    def setup(self, tmp: pathlib.Path):
        import runtime_claude as m
        self.m, self._projects, self._tmp = m, m.PROJECTS, tmp
        root = tmp / "projects"
        m.PROJECTS = root
        for path, t in SCENARIO + SUBAGENT_SCENARIO + [(p + "/.claude/worktrees/wt1", t) for p, t in WORKTREE_SCENARIO]:
            d = root / m.slug_for_path(path)
            d.mkdir(parents=True, exist_ok=True)
            if t.role == "human":
                o = {"type": "user", "timestamp": _iso(t.ts), "sessionId": t.session, "origin": {"kind": "human"},
                     "message": {"role": "user", "content": t.text}}
            elif t.role == "assistant":
                names = {"subagent": "Agent", "workflow": "Workflow", "peer_message": "SendMessage", "skill": "Skill",
                         "run_command": "Bash"}
                inputs = {"skill": {"skill": None}, "run_command": {"command": "ls"}}
                blocks = []
                for a in t.actions:
                    key = a.collab or a.kind
                    inp = dict(inputs.get(key, {}))
                    if "skill" in inp:
                        inp["skill"] = a.detail
                    if a.command:
                        inp["command"] = a.command
                    blocks.append({"type": "tool_use", "name": names[key], "input": inp, **({"id": a.call} if a.call else {})})
                o = {"type": "assistant", "timestamp": _iso(t.ts), "sessionId": t.session,
                     "message": {"role": "assistant", "content": blocks,
                                 "usage": {"input_tokens": 10, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 0}}}
            elif t.ts == T0 + datetime.timedelta(minutes=1):
                o = {"type": "user", "timestamp": _iso(t.ts), "sessionId": t.session, "isMeta": True,
                     "message": {"role": "user", "content": "<system-reminder>…</system-reminder>"}}
            else:  # tool result: also type=user, but not human
                o = {"type": "user", "timestamp": _iso(t.ts), "sessionId": t.session,
                     "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok",
                                                              **({"tool_use_id": t.results[0]} if t.results else {})}]}}
            if t.id:
                o["uuid"] = t.id
            if t.folder:
                o["cwd"] = t.folder
            if t.subagent:   # a subagent's transcript: beside its session, isSidechain on every entry
                o["isSidechain"] = True
                (d / t.session / "subagents").mkdir(parents=True, exist_ok=True)
                with open(d / t.session / "subagents" / "agent-1.jsonl", "a") as fh:
                    fh.write(json.dumps(o, ensure_ascii=False) + "\n")
                continue
            with open(d / f"{t.session}.jsonl", "a") as fh:
                fh.write(json.dumps(o, ensure_ascii=False) + "\n")
        return m, {"CLAUDE_CODE_SESSION_ID": SID}

    def configure_run(self, tmp: pathlib.Path, mode: str) -> dict:
        """A fake claude as the platform's checked copy, in a stand-in store: calls no model, prints claude -p's JSON or
        fails, depending on mode."""
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import store_fixture
        out = {"ok": {"type": "result", "is_error": False, "result": RUN_OK["text"], "structured_output": RUN_OK["structured_output"],
                      "total_cost_usd": RUN_OK["cost"], "session_id": RUN_OK["session"], "num_turns": RUN_OK["turns"], "subtype": "success"},
               "incomplete": {"type": "result", "is_error": True, "result": "stopped", "num_turns": 50, "subtype": "error_max_turns"}}
        self._store = store_fixture.Store(tmp)
        self._store.start("#!/bin/sh\necho 'boom: login expired' >&2\nexit 1\n" if mode == "fail"
                          else "#!/bin/sh\ncat <<'EOF'\n" + json.dumps(out[mode]) + "\nEOF\n")
        return dict(os.environ)

    def raw(self, sid, cwd, command=None, target_cwd=None, elsewhere=None):
        """Claude Code's hook JSON."""
        d = {"session_id": sid, "cwd": cwd} if sid else {"cwd": cwd}
        ti = {}
        if command is not None:
            ti["command"] = command
        if target_cwd:
            ti["cwd"] = target_cwd
        if elsewhere == "machine":
            ti["host"] = "box"
        if ti:
            d["tool_input"] = ti
        return json.dumps(d)

    LOGIN_ERROR = "Invalid API key · Please run /login"

    def configure_auth(self, tmp: pathlib.Path, token: bool) -> None:
        """Login scenario: HOME swapped for a temp directory, the token file present or not."""
        self._home = os.environ.get("HOME")
        os.environ["HOME"] = str(tmp)
        (tmp / ".claude").mkdir(exist_ok=True)
        if token:
            (tmp / ".claude" / "headless-oauth-token").write_text("contract-token\n")

    def start_live(self, sid: str) -> None:
        """A live session to wake: its waiter heartbeats (runtime_claude.wait records it), in a temp secrets folder."""
        import hubconf
        from unittest import mock
        self._secrets = mock.patch.object(hubconf, "secrets_dir", lambda: self._tmp / "secrets")
        self._secrets.start()
        f = hubconf.session_file(sid, "waiter")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("1-test")

    def teardown(self):
        self.m.PROJECTS = self._projects
        if getattr(self, "_secrets", None):
            self._secrets.stop()
            self._secrets = None
        if getattr(self, "_store", None):
            self._store.stop()
            self._store = None
        if getattr(self, "_home", None):
            os.environ["HOME"] = self._home


class FakeFixture:
    def setup(self, tmp: pathlib.Path):
        import runtime_fake as m
        m.reset()
        m.set_context(SID, 1010)
        return m, {"FAKE_RUNTIME_SESSION": SID}

    def raw(self, sid, cwd, command=None, target_cwd=None, elsewhere=None):
        """Fake's own format (deliberately different from Claude's)."""
        d = {"sid": sid, "dir": cwd} if sid else {"dir": cwd}
        if command is not None:
            d["cmd"] = command
        if target_cwd:
            d["where"] = target_cwd
        if elsewhere:
            d["elsewhere"] = elsewhere
        return json.dumps(d)

    def configure_run(self, tmp: pathlib.Path, mode: str) -> dict:
        import runtime_fake as m
        from runtime_ports import TaskResult
        m.set_reply("p", {"ok": TaskResult(True, False, RUN_OK["text"], RUN_OK["structured_output"], RUN_OK["cost"],
                                           RUN_OK["session"], RUN_OK["turns"], "success"),
                          "incomplete": TaskResult(True, True, "stopped", turns=50, stop="error_max_turns"),
                          "fail": TaskResult(False, error="boom: login expired")}[mode])
        return {}

    LOGIN_ERROR = "fake: not signed in"

    def configure_auth(self, tmp: pathlib.Path, token: bool) -> None:
        import runtime_fake
        runtime_fake.set_credential("token", token)

    def start_live(self, sid: str) -> None:
        """A session of this runtime running now (the wake contract)."""
        import runtime_fake
        runtime_fake.start(sid)

    def teardown(self):
        import runtime_fake
        runtime_fake.reset()


class CommandFixture:
    """The command template from settings (runtime_command): a fake CLI agent. It returns only text/JSON: no cost,
    session, turn count or "not completed normally"."""
    REPORTS = frozenset()   # what this runtime can report (cost, session, turns, incomplete)
    POINT = "mailbox-propose"   # the only point it runs, and only in the platform's copy (made in configure_run)

    def setup(self, tmp: pathlib.Path):
        import runtime_command as m
        import hubconf
        script = tmp / "agent.py"
        script.write_text("import os, sys\nmode = os.environ['CMD_MODE']\nprompt = open(sys.argv[1]).read()\n"
                          "if mode == 'fail':\n    print('boom: not signed in', file=sys.stderr); sys.exit(3)\n"
                          "print('{\"x\": 1}')\n")
        self._get = hubconf.get
        conf = {"run": [sys.executable, str(script), "{prompt_file}"]}
        hubconf.get = lambda k, default=None: conf if k == "runtime.command" else self._get(k, default)
        return m, {}

    def configure_run(self, tmp: pathlib.Path, mode: str) -> dict:
        # as AcpFixture: the Task has no cwd = the process's own directory, made and marked where propose_apply does
        import hubconf
        copy = hubconf.throwaway_copy("contract")
        copy.mkdir()
        self._cwd = os.getcwd()
        os.chdir(copy)
        return {**os.environ, "CMD_MODE": mode, "HUB_THROWAWAY_COPY": str(copy)}

    def teardown(self):
        import hubconf
        hubconf.get = self._get
        if getattr(self, "_cwd", None):
            os.chdir(self._cwd)


class AcpFixture:
    """An Agent Client Protocol agent (runtime_acp): the fake agent in tests/fixtures/fake_acp_agent.py speaking JSON-RPC
    over stdio. ACP reports no turn count."""
    REPORTS = frozenset({"cost", "session", "incomplete"})
    POINT = "mailbox-propose"   # the only point ACP runs, and only in the platform's copy (made in configure_run)
    LOGIN_ERROR = "auth_required: the agent needs a login (fake-login): Authentication required"

    def setup(self, tmp: pathlib.Path):
        import runtime_acp as m
        import hubconf
        self._get = hubconf.get
        self._secrets = hubconf.secrets_dir
        self.secrets = tmp / "secrets"
        self.secrets.mkdir()
        conf = {"agent": "fake", "agents": {"fake": {
            "command": [sys.executable, str(ROOT / "tests" / "fixtures" / "fake_acp_agent.py")],
            "credential": {"env": "FAKE_ACP_KEY", "file": "fake-acp-key"}, "login": "fake-acp login"}}}
        hubconf.get = lambda k, default=None: conf if k == "runtime.acp" else self._get(k, default)
        hubconf.secrets_dir = lambda: self.secrets
        return m, {}

    def configure_run(self, tmp: pathlib.Path, mode: str) -> dict:
        # the Task has no cwd = the process's own directory: made where propose_apply makes its copy, and marked as
        # it marks it
        import hubconf
        copy = hubconf.throwaway_copy("contract")
        copy.mkdir()
        self._cwd = os.getcwd()
        os.chdir(copy)
        return {**os.environ, "FAKE_ACP_MODE": mode, "HUB_THROWAWAY_COPY": str(copy)}

    def configure_auth(self, tmp: pathlib.Path, token: bool) -> None:
        if token:
            (self.secrets / "fake-acp-key").write_text("contract-token\n")

    def teardown(self):
        import hubconf
        hubconf.get, hubconf.secrets_dir = self._get, self._secrets
        if getattr(self, "_cwd", None):
            os.chdir(self._cwd)


class CodexFixture:
    """Codex (runtime_codex): its hook input as 0.169 sends it (tests/fixtures/codex), a rollout in a scratch CODEX_HOME for
    the context size, the thread id a tool shell carries. Its hooks are user-level: nothing per project to install."""

    def setup(self, tmp: pathlib.Path):
        import runtime_codex as m
        self.m, home = m, tmp / "codex-home"
        day = home / "sessions" / "2026" / "10" / "20"
        day.mkdir(parents=True)
        lines = [{"type": "session_meta", "payload": {"session_id": SID, "cwd": WS, "originator": "codex_cli_rs"}},
                 {"type": "event_msg", "payload": {"type": "token_count", "info": {
                     "last_token_usage": {"input_tokens": 1000, "cached_input_tokens": 600, "output_tokens": 10}}}}]
        (day / f"rollout-2026-10-20T09-00-00-{SID}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
        self._home = mock.patch.object(m, "codex_home", lambda: home)
        self._home.start()
        return m, {"CODEX_THREAD_ID": SID}

    def raw(self, sid, cwd, command=None, target_cwd=None, elsewhere=None):
        d = json.loads((ROOT / "tests" / "fixtures" / "codex" / "post_tool_use.from-source.json").read_text())
        d["cwd"] = cwd
        if sid:
            d["session_id"] = sid
        else:
            del d["session_id"]
        if command is not None:
            d["tool_input"] = {"command": command}
        return json.dumps(d)

    # the platform's own runs (Step 1): a memory-copy point in a plain clone of a memory repo, a stand-in codex
    REPORTS = frozenset({"session"})
    POINT = "librarian-state"
    SCHEMA = True
    ENV_CREDENTIAL = False   # its login is a file linked into each run's home, not a variable (M9)
    LOGIN_ERROR = "unexpected status 401 Unauthorized: Missing bearer or basic authentication in header"

    def configure_run(self, tmp: pathlib.Path, mode: str) -> dict:
        import yaml
        import codex_fake
        m = self.m
        self.SPEC = yaml.safe_load((ROOT / "contracts" / "llm-points.yaml").read_text())["points"][self.POINT]
        origin, copy = tmp / "origin", tmp / "copies" / "box"
        (origin / "box-memory.git").mkdir(parents=True)
        (copy / ".git").mkdir(parents=True)
        (copy / ".git" / "config").write_text(f'[core]\n\tbare = false\n[remote "origin"]\n\turl = '
                                               f'{origin / "box-memory.git"}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n')
        (copy / "STATE.md").write_text("s\n")
        self.cwd = str(copy)
        (tmp / "codex-home" / "auth.json").write_text("{}")
        exe = codex_fake.write(tmp / "prefix", **({"answer": json.dumps(RUN_OK["structured_output"])} if mode == "ok"
                                                    else {"fail": "boom: login expired"}))
        m.check_binary(str(exe))
        p = _hubconf.p
        for patch in (mock.patch.object(_hubconf, "runtimes_dir", lambda: tmp / "runtimes"),
                      mock.patch.object(_hubconf, "p", lambda k, d=None: {"git_origin": origin, "memory_copies": copy.parent}.get(k) or p(k, d))):
            patch.start()
            self._patches.append(patch)
        return {"HUB_LLM_POINT": self.POINT}

    def configure_auth(self, tmp: pathlib.Path, token: bool) -> None:
        if token:
            (tmp / "codex-home" / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "tokens": "contract-token"}))

    _patches: list = []

    def teardown(self):
        self._home.stop()
        self.m.check_binary(None)
        while self._patches:
            self._patches.pop().stop()


FIXTURES = {"claude": ClaudeFixture, "fake": FakeFixture, "command": CommandFixture, "acp": AcpFixture,
            "codex": CodexFixture}
ALL_REPORTS = frozenset({"cost", "session", "turns", "incomplete"})


def having(port: str) -> list[str]:
    """Implementations with this interface: the contract tests run only on them (the others raise Unsupported from
    load(), checked by NoneRuntime)."""
    return [n for n in runtime_ports.RUNTIMES if port in runtime_ports.capabilities(n)]


class Registry(unittest.TestCase):
    def test_every_registered_runtime_has_a_fixture(self):
        withports = {n for n in runtime_ports.RUNTIMES if runtime_ports.capabilities(n)}
        self.assertEqual(withports, set(FIXTURES),
                         "a new runtime needs its scenario setup here to count as having run the contract tests")

    def test_load_rejects_incomplete_module(self):
        bad = types.ModuleType("runtime_broken")
        bad.parse_event = lambda *a: None
        sys.modules["runtime_broken"] = bad
        runtime_ports.RUNTIMES["broken"] = "runtime_broken"
        try:
            with self.assertRaises(runtime_ports.MissingFunctions):
                runtime_ports.load("broken")
        finally:
            del runtime_ports.RUNTIMES["broken"]
            del sys.modules["runtime_broken"]

    def test_unknown_runtime(self):
        with self.assertRaises(KeyError):
            runtime_ports.load("nope")


class NoneRuntime(unittest.TestCase):
    """No coding agent (runtime.default: none): every interface is "unsupported", neither an error nor empty data."""

    def test_has_no_ports(self):
        self.assertEqual(runtime_ports.capabilities("none"), set())

    def test_every_port_is_unsupported_with_a_reason(self):
        for port in runtime_ports.PORTS:
            with self.subTest(port=port):
                with self.assertRaises(runtime_ports.Unsupported) as cm:
                    runtime_ports.load("none", ports=(port,))
                self.assertIn(runtime_ports.PORT_LABELS[port], str(cm.exception))

    def test_unsupported_is_not_missing_functions(self):
        self.assertFalse(issubclass(runtime_ports.Unsupported, runtime_ports.MissingFunctions))

    def test_partial_port_is_still_a_bug(self):
        bad = types.ModuleType("runtime_half")
        bad.parse_event = lambda *a: None   # only part of lifecycle
        sys.modules["runtime_half"] = bad
        runtime_ports.RUNTIMES["half"] = "runtime_half"
        try:
            with self.assertRaises(runtime_ports.MissingFunctions):
                runtime_ports.load("half", ports=("lifecycle",))
            with self.assertRaises(runtime_ports.Unsupported):
                runtime_ports.load("half", ports=("agent",))
        finally:
            del runtime_ports.RUNTIMES["half"]
            del sys.modules["runtime_half"]

    def test_current_session_is_unknown_not_an_error(self):
        from unittest import mock
        with mock.patch.object(runtime_ports, "default_name", lambda: "none"):
            self.assertIsNone(runtime_ports.current_session({}))

    def test_every_label(self):
        self.assertEqual(set(runtime_ports.PORT_LABELS), set(runtime_ports.PORTS))


class Contract(unittest.TestCase):
    """Every implementation runs these (subTest marks which one)."""

    def each(self, check):
        """For each implementation with session state: put the scenario in → check the interface mechanically → run
            check(m, env) → clean up. A failure names the implementation."""
        for name in having("sessions"):
            with self.subTest(runtime=name):
                fx = FIXTURES[name]()
                with tempfile.TemporaryDirectory() as d:
                    m, env = fx.setup(pathlib.Path(d))
                    try:
                        runtime_ports.load(name, ports=("sessions",))
                        check(m, env)
                    finally:
                        fx.teardown()

    def test_sessions(self):
        def check(m, env):
            self.assertFalse(m.alive("00000000-0000-4000-8000-000000000000"))
            self.assertIsNone(m.context_size("00000000-0000-4000-8000-000000000000"))
            self.assertEqual(m.context_size(SID), 1010)
            self.assertIsInstance(m.live_sessions(), set)
            self.assertEqual(m.current_session(env), SID)
            self.assertIsNone(m.current_session({}))
        self.each(check)


class WakeContract(unittest.TestCase):
    """The optional wake port (docs/design/mailbox-runtimes.md B1): a live session takes the notice; one that ended or
    never existed doesn't, and that is an answer, not an error. A runtime without it says why (doctor)."""

    def test_wake(self):
        for name in having("wake"):
            with self.subTest(runtime=name):
                fx = FIXTURES[name]()
                with tempfile.TemporaryDirectory() as d:
                    m, _env = fx.setup(pathlib.Path(d))
                    try:
                        runtime_ports.load(name, ports=("wake", "sessions"))
                        fx.start_live(SID)
                        self.assertIs(m.wake(SID, "📬 1 letter"), True)
                        self.assertIs(m.wake("00000000-0000-4000-8000-000000000000", "📬 1 letter"), False)
                    finally:
                        fx.teardown()

    def test_a_runtime_with_wake_has_can_wake(self):
        """Routing asks can_wake before it counts a session as a candidate: a wake it can't foresee is a letter routed to
        nobody. Whichever is declared alone, of every runtime registered."""
        for name in runtime_ports.RUNTIMES:
            with self.subTest(runtime=name):
                m = runtime_ports._module(name)
                self.assertEqual(callable(getattr(m, "wake", None)), callable(getattr(m, "can_wake", None)))
        half = types.ModuleType("runtime_half")
        half.wake = lambda sid, text: True
        with mock.patch.dict(runtime_ports.RUNTIMES, {"half": "runtime_half"}), mock.patch.dict(sys.modules, {"runtime_half": half}), \
                self.assertRaises(runtime_ports.MissingFunctions):
            runtime_ports.load("half", ports=("wake",))

    def test_the_session_ports_are_optional_and_answer_within_their_vocabulary(self):
        self.assertLessEqual({"wake", "session_status", "session_summary"}, set(runtime_ports.OPTIONAL))
        for name in ("acp", "command", "none"):
            with self.subTest(runtime=name):
                self.assertNotIn("session_status", runtime_ports.capabilities(name))
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import store_fixture
        store_fixture.empty(self)   # no copy to list with: within the vocabulary still (unlisted)
        for name in having("session_status"):
            with self.subTest(runtime=name):
                self.assertIn(runtime_ports._module(name).session_status("00000000-0000-4000-8000-000000000000"),
                              runtime_ports.SESSION_STATUSES)

    def test_without_wake_it_is_unsupported_with_the_runtimes_reason(self):
        self.assertNotIn("wake", runtime_ports.capabilities("acp"))
        with self.assertRaises(runtime_ports.Unsupported):
            runtime_ports.load("acp", ports=("wake",))
        self.assertEqual(runtime_ports.missing_label("acp", "wake"), runtime_ports.PORT_LABELS["wake"])
        [row] = [miss for f, ok, miss in runtime_ports.feature_table("acp") if not ok and f in runtime_ports.optional_features()]
        self.assertIn(runtime_ports.PORT_LABELS["wake"], row)

    def test_a_runtime_with_wake_says_what_to_know_about_it(self):
        self.assertIn("wake", runtime_ports.capabilities("claude"))
        self.assertTrue(all(ok for _f, ok, _m in runtime_ports.feature_table("claude")))
        [feat] = runtime_ports.optional_features()
        self.assertEqual(runtime_ports.feature_notes("claude"), {feat: runtime_claude_note("wake")})
        self.assertEqual(runtime_ports.feature_notes("acp"), {})


def runtime_claude_note(port):
    import runtime_claude
    return runtime_claude.note(port)


class RunContract(unittest.TestCase):
    """Layer 1, "run once": the same for every implementation: completed normally, a result but not completed
    normally (not a failure), failed."""

    def each_run(self, mode, check):
        for name in having("agent"):
            with self.subTest(runtime=name, mode=mode):
                fx = FIXTURES[name]()
                with tempfile.TemporaryDirectory() as d:
                    m, _ = fx.setup(pathlib.Path(d))
                    try:
                        reports = getattr(fx, "REPORTS", ALL_REPORTS)
                        if mode == "incomplete" and "incomplete" not in reports:
                            continue   # this runtime cannot tell "a result but not completed normally"
                        env = fx.configure_run(pathlib.Path(d), mode)
                        rt = runtime_ports.load(name, ports=("agent",))
                        task = runtime_ports.Task(point=getattr(fx, "POINT", "p"), prompt="hi", spec=getattr(fx, "SPEC", {}),
                                                  env=env or None, timeout_s=30, cwd=getattr(fx, "cwd", None),
                                                  output_schema={"type": "object"} if name in ("command", "acp")
                                                  or getattr(fx, "SCHEMA", False) else None)
                        self.reports = reports
                        check(rt.run(task), rt.describe(task))
                    finally:
                        fx.teardown()

    def test_ok(self):
        def check(res, desc):
            self.assertTrue(res.ok)
            self.assertFalse(res.incomplete)
            self.assertEqual(res.structured_output, RUN_OK["structured_output"])
            if self.reports == ALL_REPORTS:
                self.assertEqual((res.text, res.cost_usd, res.session_id, res.turns),
                                 (RUN_OK["text"], RUN_OK["cost"], RUN_OK["session"], RUN_OK["turns"]))
            else:   # what it cannot report is None (never make one up)
                for field, attr in (("cost", "cost_usd"), ("session", "session_id"), ("turns", "turns")):
                    if field not in self.reports:
                        self.assertIsNone(getattr(res, attr), field)
            self.assertTrue(desc and all(isinstance(x, str) for x in desc))
        self.each_run("ok", check)

    def test_incomplete_is_not_failure(self):
        def check(res, desc):
            self.assertTrue(res.ok, "a result means ok; not completing normally is the caller's call "
                                    "(the librarian relies on this)")
            self.assertTrue(res.incomplete)
            if "turns" in self.reports:
                self.assertEqual(res.turns, 50)
        self.each_run("incomplete", check)

    def test_failure(self):
        def check(res, desc):
            self.assertFalse(res.ok)
            self.assertIn("boom", res.error)
        self.each_run("fail", check)


class LifecycleContract(unittest.TestCase):
    """Layer 3: every implementation turns its native hook input into the same neutral events, with output only when
    there should be some."""

    def each(self, check):
        for name in having("lifecycle"):
            with self.subTest(runtime=name):
                fx = FIXTURES[name]()
                with tempfile.TemporaryDirectory() as d:
                    m, _ = fx.setup(pathlib.Path(d))
                    try:
                        runtime_ports.load(name, ports=("lifecycle",))
                        check(m, fx, pathlib.Path(d))
                    finally:
                        fx.teardown()

    def test_parse(self):
        def check(m, fx, tmp):
            ev = m.parse_event("prompt", fx.raw(SID, WS))
            self.assertEqual((ev.event, ev.session, ev.cwd), ("prompt", SID, WS))
            self.assertEqual(m.parse_event("tool_used", fx.raw(SID, WS, command="git commit -m x")).command, "git commit -m x")
            if "open_session_attempt" in getattr(m, "EVENTS_MAPPED", runtime_ports.EVENTS):
                t = m.parse_event("open_session_attempt", fx.raw(SID, WS, target_cwd="/elsewhere")).target
                self.assertEqual((t["cwd"], t["elsewhere"]), ("/elsewhere", None))
                t = m.parse_event("open_session_attempt", fx.raw(SID, WS, elsewhere="machine")).target
                self.assertEqual(t["elsewhere"], {"kind": "machine"})
            for ev in set(runtime_ports.EVENTS) - set(getattr(m, "EVENTS_MAPPED", runtime_ports.EVENTS)):
                self.assertIsNone(m.parse_event(ev, fx.raw(SID, WS)), f"{ev} isn't mapped: no event, never a guess")
            self.assertIsNone(m.parse_event("prompt", fx.raw(None, WS)), "no session = not a real session running")
            self.assertIsNone(m.parse_event("prompt", "not json"))
            why = getattr(m, "unusable", None)
            if callable(why):   # optional: why an input gave no event (runtime-versions.md section 6)
                self.assertIsNone(why("prompt", ""), "empty input is no session, not a fault")
                self.assertIsNone(why("prompt", fx.raw(SID, WS)), "a usable input has no reason")
                for bad in ("not json", fx.raw(None, WS)):
                    reason = why("prompt", bad)
                    self.assertTrue(reason and isinstance(reason, str), bad)
                    self.assertIsNone(m.parse_event("prompt", bad))
        self.each(check)

    def test_inject_and_deny_only_where_meaningful(self):
        def check(m, fx, tmp):
            for ev in ("session_start", "prompt", "tool_used"):
                self.assertIn("hello", m.inject(ev, "hello"), ev)
                self.assertEqual(m.inject(ev, ""), "", f"{ev}: no text, no output")
            for ev in ("session_end", "pre_compact", "open_session_attempt"):
                self.assertEqual(m.inject(ev, "hello"), "", f"{ev} cannot go into the context")
            if "open_session_attempt" in getattr(m, "EVENTS_MAPPED", runtime_ports.EVENTS):
                self.assertIn("no", m.deny("open_session_attempt", "no"))
            else:
                self.assertEqual(m.deny("open_session_attempt", "no"), "", "nothing it maps can be blocked")
            self.assertEqual(m.deny("prompt", "no"), "")
        self.each(check)

    def test_install_idempotent(self):
        def check(m, fx, tmp):
            ws = tmp / "ws"
            ws.mkdir()
            if getattr(m, "USER_LEVEL_HOOKS", False):
                # its hooks are the user's, written only by the owner's own command (docs/design/runtime-codex.md r2 B1):
                # hub-event install and render-project-settings change nothing in any project
                self.assertFalse(m.install_lifecycle(ws, check=True))
                self.assertFalse(m.install_lifecycle(ws))
                self.assertFalse(list(ws.rglob("*")), "nothing is written in the project")
                return
            self.assertTrue(m.install_lifecycle(ws, check=True), "nothing installed yet: installing would change it")
            self.assertFalse([p for p in ws.rglob("*") if p.is_file()], "check writes nothing")
            self.assertTrue(m.install_lifecycle(ws))
            snap = {p: p.read_bytes() for p in ws.rglob("*") if p.is_file()}
            self.assertTrue(snap, "installing leaves something behind")
            self.assertFalse(m.install_lifecycle(ws, check=True), "installed: nothing left to change")
            self.assertFalse(m.install_lifecycle(ws))
            self.assertEqual(snap, {p: p.read_bytes() for p in ws.rglob("*") if p.is_file()},
                             "a rerun gives the same result")
        self.each(check)


class AuthContract(unittest.TestCase):
    """Layer 6: the platform only says which login to use (token / subscription); each implementation decides where
    the credentials go and what the variables are called."""

    BASE = {"PATH": "/usr/bin", "LLM_AUTH": "x-not-the-runtimes"}

    def each(self, token, check):
        for name in having("auth"):
            with self.subTest(runtime=name, token=token):
                fx = FIXTURES[name]()
                with tempfile.TemporaryDirectory() as d:
                    m, _ = fx.setup(pathlib.Path(d))
                    try:
                        fx.configure_auth(pathlib.Path(d), token)
                        runtime_ports.load(name, ports=("auth",))
                        check(m, fx)
                    finally:
                        fx.teardown()

    def test_token_adds_a_credential_that_clean_env_removes(self):
        def check(m, fx):
            env, why = m.auth_env("token", dict(self.BASE))
            self.assertEqual(why, "")
            if not getattr(fx, "ENV_CREDENTIAL", True):   # its login isn't a variable: nothing to add or remove
                self.assertEqual(env, m.clean_env(env))
                return
            self.assertNotEqual(env, m.clean_env(env), "a token login should carry extra credentials")
            self.assertEqual(m.clean_env(env), m.clean_env(dict(self.BASE)))
            # an env already carrying old credentials: replaced with this one's, never two stacked
            again, _ = m.auth_env("token", env)
            self.assertEqual(again, env)
        self.each(True, check)

    def test_missing_credential_says_why_instead_of_falling_back(self):
        def check(m, fx):
            env, why = m.auth_env("token", dict(self.BASE))
            self.assertIsNone(env)
            self.assertTrue(why)
        self.each(False, check)

    def test_subscription_never_carries_the_token(self):
        def check(m, fx):
            tok, _ = m.auth_env("token", dict(self.BASE))
            env, why = m.auth_env("subscription", tok)
            self.assertEqual((env, why), (m.clean_env(tok), ""))
        self.each(True, check)

    def test_platform_variables_pass_through(self):
        def check(m, fx):
            env, _ = m.auth_env("token", dict(self.BASE))
            self.assertEqual((env["PATH"], env["LLM_AUTH"]), ("/usr/bin", "x-not-the-runtimes"))
        self.each(True, check)

    def test_credentials_describe_without_values(self):
        def check(m, fx):
            creds = m.credentials()
            self.assertTrue(creds)
            self.assertEqual(len({c.name for c in creds}), len(creds))
            for c in creds:
                self.assertIsInstance(c, runtime_ports.Credential)
                self.assertIn(c.mode, (None, "token", "subscription"))
                self.assertTrue(c.label and c.renew)
            self.assertIn("token", {c.mode for c in creds})
            token = next(c for c in creds if c.mode == "token")
            self.assertTrue(token.present)
            self.assertNotIn("contract-token", repr(creds))
        self.each(True, check)

    def test_missing_token_shows_as_not_present(self):
        def check(m, fx):
            self.assertFalse(next(c for c in m.credentials() if c.mode == "token").present)
        self.each(False, check)

    def test_login_errors_told_apart(self):
        def check(m, fx):
            self.assertTrue(m.is_login_error(fx.LOGIN_ERROR))
            self.assertFalse(m.is_login_error("timed out after 120s"))
            self.assertFalse(m.is_login_error(""))
        self.each(True, check)


class InstructionsContract(unittest.TestCase):
    """Layer 4: the rules file is in the project folder, and is itself on the local-only file list (otherwise it
    would be committed into the repo)."""

    def test_instructions_file_is_local_and_ignored(self):
        for name in having("instructions"):
            with self.subTest(runtime=name):
                m = runtime_ports.load(name, ports=("instructions",))
                ws = pathlib.Path("/Users/someone/workspace/proj")
                f = m.instructions_file(ws)
                self.assertEqual(f.parent, ws, "the rules file goes directly in the project folder")
                ignores = m.local_ignores()
                self.assertIn(f.name, ignores, "the rules file must be on the local-only file list")
                self.assertNotIn(".agent-memory", ignores, ".agent-memory belongs to the platform, not the runtime")
                self.assertTrue(all(not pathlib.PurePath(p).is_absolute() and ".." not in pathlib.PurePath(p).parts
                                    for p in ignores), "the list holds only paths relative to the project folder")


class ClaudeHooks(unittest.TestCase):
    """Claude only: the replacement rules for hook settings, the output format."""

    def test_replace_managed_keep_user(self):
        import runtime_claude as m
        settings = {"model": "x", "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "[ -d .agent-memory ] && ~/agent-hub/bin/sync-local-setup.sh --quiet || true"}]},
                             {"hooks": [{"type": "command", "command": "echo mine"}]}],
            "PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "~/agent-hub/bin/memory-nudge.py --post-tool"},
                                                            {"type": "command", "command": "my-linter"}]}],
            "Notification": [{"hooks": [{"type": "command", "command": "~/agent-hub/bin/old-thing"}]}]}}
        out = m.install_hooks(json.loads(json.dumps(settings)))
        cmds = [h["command"] for es in out["hooks"].values() for e in es for h in e["hooks"]]
        self.assertIn("echo mine", cmds)
        self.assertIn("my-linter", cmds)
        self.assertFalse([c for c in cmds if "agent-hub/bin/" in c and "hub-event" not in c],
                         "old platform hooks are removed")
        self.assertNotIn("Notification", out["hooks"], "an event left with only old platform hooks is removed entirely")
        self.assertEqual(out["hooks"]["Stop"], [{"hooks": [m.wait_hook()]}], "one waiter, the platform's")
        self.assertEqual(out["model"], "x", "other settings untouched")
        for name, matcher, ev in m.HOOK_TABLE:
            entries = [e for e in out["hooks"][name]
                       if any(f"hub-event --runtime claude {ev}" in h["command"] for h in e["hooks"])]
            self.assertEqual(len(entries), 1, ev)
            self.assertEqual(entries[0].get("matcher"), matcher)
        again = m.install_hooks(json.loads(json.dumps(out)))
        self.assertEqual(again, out, "a rerun changes nothing")

    def test_output_shapes(self):
        import runtime_claude as m
        self.assertEqual(m.inject("session_start", "hi"), "hi")
        self.assertEqual(json.loads(m.inject("prompt", "hi"))["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertEqual(json.loads(m.inject("tool_used", "hi"))["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        d = json.loads(m.deny("open_session_attempt", "no"))["hookSpecificOutput"]
        self.assertEqual((d["hookEventName"], d["permissionDecision"], d["permissionDecisionReason"]), ("PreToolUse", "deny", "no"))


class CommandTemplate(unittest.TestCase):
    """Placeholders in the command template: a tool that does not read stdin (e.g. agy -p) gets the full prompt as an
        argument."""

    def test_prompt_as_argument_is_not_substituted_again(self):
        import runtime_command as m
        argv = m._argv(["tool", "--model", "{model}", "-p", "{prompt}"], None, "/p", "/o", "use {model} to write {prompt_file}")
        self.assertEqual(argv, ["tool", "-p", "use {model} to write {prompt_file}"],
                         "no model drops the whole flag group; the prompt goes in as is")

    def test_prompt_argument_means_no_stdin(self):
        import runtime_command as m
        import hubconf
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        script = tmp / "agent.py"
        script.write_text("import sys\nprint(repr(sys.stdin.read()), sys.argv[1])\n")
        saved = hubconf.get
        conf = {"run": [sys.executable, str(script), "{prompt}"]}
        hubconf.get = lambda k, default=None: conf if k == "runtime.command" else saved(k, default)
        try:
            code, out, _ = m._run("héllo", None, str(tmp), None, 30)
        finally:
            hubconf.get = saved
        self.assertEqual((code, out.strip()), (0, "'' héllo"))

    def test_cwd_placeholder_is_the_absolute_workdir(self):
        import runtime_command as m
        argv = m._argv(["tool", "--allow", "read_file({cwd})"], None, "/p", "/o", "x", "/w/demo")
        self.assertEqual(argv, ["tool", "--allow", "read_file(/w/demo)"])

    def test_silent_exit_zero_is_a_failure(self):
        import runtime_command as m
        import hubconf
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        script = tmp / "agent.py"
        script.write_text("import sys\nprint('a tool was auto-denied', file=sys.stderr)\n")
        saved = hubconf.get
        conf = {"run": [sys.executable, str(script)]}
        hubconf.get = lambda k, default=None: conf if k == "runtime.command" else saved(k, default)
        copy = hubconf.throwaway_copy("contract")
        copy.mkdir()
        try:
            res = m.run(runtime_ports.Task(point="mailbox-propose", prompt="héllo", spec={}, cwd=str(copy), model=None,
                                           output_schema=None, timeout_s=30, env={**os.environ, m.COPY_VAR: str(copy)}))
        finally:
            hubconf.get = saved
        self.assertFalse(res.ok, "a tool that was blocked by permissions and ended without saying anything "
                                 "is not a successful empty answer")
        self.assertIn("auto-denied", res.error)


class CommandRuntimeFailsClosed(unittest.TestCase):
    """runtime_command can't carry a judgment point's limits (tools, permissions, its environment): it runs only
    mailbox-propose in the platform's throwaway copy, and has no completion interface (docs/design/runtime-codex.md,
    step 0)."""

    def setUp(self):
        import hubconf
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ran = self.tmp / "ran"
        script = self.tmp / "agent.py"
        script.write_text(f"open({str(self.ran)!r}, 'w').write('x')\nprint('done')\n")
        conf = {"run": [sys.executable, str(script)]}
        saved = hubconf.get
        p = mock.patch.object(hubconf, "get", lambda k, default=None: conf if k == "runtime.command" else saved(k, default))
        p.start()
        self.addCleanup(p.stop)

    def copy(self):
        import hubconf
        copy = hubconf.throwaway_copy("contract")
        copy.mkdir()
        return copy

    def run_task(self, point, cwd, mark):
        import runtime_command as m
        env = {k: v for k, v in os.environ.items() if k != m.COPY_VAR}
        if mark is not None:
            env[m.COPY_VAR] = mark
        return m.run(runtime_ports.Task(point=point, prompt="p", spec={}, cwd=str(cwd), env=env, timeout_s=30))

    def test_it_is_throwaway_only(self):
        self.assertTrue(runtime_ports.throwaway_only("command"))

    def test_refused_outside_the_copy_and_nothing_runs(self):
        copy = self.copy()
        for point, cwd, mark in (("librarian-state", self.tmp, None),       # another point, anywhere
                                 ("librarian-state", copy, str(copy)),      # another point, even in the copy
                                 ("mailbox-propose", self.tmp, None),       # the point, in a folder of its own
                                 ("mailbox-propose", copy, None),           # the copy, not marked
                                 ("mailbox-propose", copy, str(self.tmp))):  # marked for another folder
            res = self.run_task(point, cwd, mark)
            self.assertFalse(res.ok, (point, cwd, mark))
            self.assertIn(point, res.error)   # the refusal, in either language: it names the point and where
            self.assertIn(os.path.realpath(cwd), res.error)
            self.assertFalse(self.ran.exists(), (point, cwd, mark))

    def test_runs_mailbox_propose_in_the_copy(self):
        copy = self.copy()
        res = self.run_task("mailbox-propose", copy, str(copy))
        self.assertTrue(res.ok, res.error)
        self.assertTrue(self.ran.exists())

    def test_no_completion_interface_and_says_why(self):
        self.assertNotIn("completion", runtime_ports.capabilities("command"))
        with self.assertRaises(runtime_ports.Unsupported):
            runtime_ports.load_completion("command")
        self.assertIn("brunna llm setup", runtime_ports.missing_label("command", "completion"))

    def test_a_completion_point_on_it_is_refused_not_run(self):
        import completion
        import llm
        spec = llm.points()["points"]["librarian-state-edits"]
        with mock.patch.object(completion, "settings", lambda: None), \
                mock.patch.object(runtime_ports, "default_name", lambda: "command"), self.assertRaises(completion.Refused):
            llm.completion_target("librarian-state-edits", spec)
        self.assertFalse(self.ran.exists())

    def test_callers_with_a_fallback_see_it_off(self):
        """llm_ready says a point it refuses is off, so the librarian takes its -edits points instead of failing each one."""
        import llm_ready
        with mock.patch.object(runtime_ports, "default_name", lambda: "command"):
            self.assertIsNotNone(llm_ready.unsupported("librarian-state"))
            self.assertIsNone(llm_ready.unsupported("mailbox-propose"))

    def test_it_is_no_completion_provider(self):
        import completion
        self.assertTrue([e for e in completion.config_errors({"providers": {"c": {"impl": "command"}}}) if "command" in e])


if __name__ == "__main__":
    unittest.main()
