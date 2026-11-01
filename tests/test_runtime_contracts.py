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
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import runtime_ports  # noqa: E402

UTC = datetime.timezone.utc
T0 = datetime.datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
WS = "/Users/someone/workspace/proj"
SID = "aaaaaaaa-1111-4111-8111-111111111111"
SID2 = "bbbbbbbb-2222-4222-8222-222222222222"   # another project's session (a real session is in one project only)

# Scenario: one human message, one system injection (not human), one assistant turn that calls a subagent, a skill,
# runs a command (not collaboration), one tool result; one human message in another project
SCENARIO = [
    (WS, runtime_ports.Turn(T0, SID, "human", "regarde les tests, s'il te plaît", id="e1", folder=WS)),
    (WS, runtime_ports.Turn(T0 + datetime.timedelta(minutes=1), SID, "other", id="e2", folder=WS)),
    (WS, runtime_ports.Turn(T0 + datetime.timedelta(minutes=2), SID, "assistant", "",
                            (runtime_ports.Action("other", "", "subagent"), runtime_ports.Action("other", "dataviz", "skill"),
                             runtime_ports.Action("run_command", call="call-1", command="ls")), id="e3", folder=WS)),
    (WS, runtime_ports.Turn(T0 + datetime.timedelta(minutes=3), SID, "other", id="e4", folder=WS, results=("call-1",))),
    ("/Users/someone/workspace/other", runtime_ports.Turn(T0, SID2, "human", "l'autre projet")),
]
# an entry of a subagent the session started: counts only with turns(subagents=True)
SUBAGENT_SCENARIO = [(WS, runtime_ports.Turn(T0 + datetime.timedelta(minutes=4), SID, "assistant", "",
                                             (runtime_ports.Action("run_command", call="call-2", command="pytest -q"),),
                                             id="s1", folder=WS, subagent=True))]
# one human message from a session in a worktree the runtime opened under the same project: counts only with
# turns(worktrees=True)
SID3 = "cccccccc-3333-4333-8333-333333333333"
WORKTREE_SCENARIO = [(WS, runtime_ports.Turn(T0 + datetime.timedelta(seconds=30), SID3, "human", "un message du worktree"))]


# three scenarios for "run once": completed normally, a result but not completed normally (e.g. turn limit), failed
RUN_OK = {"text": "done", "structured_output": {"x": 1}, "cost": 0.5, "session": "s1", "turns": 2}


def _iso(t):
    return t.isoformat().replace("+00:00", "Z")


class ClaudeFixture:
    """Write the scenario in Claude Code's transcript format (~/.claude/projects/<slug>/<session>.jsonl)."""

    def setup(self, tmp: pathlib.Path):
        import runtime_claude as m
        self.m, self._roots, self._projects = m, m.transcript_roots, m.PROJECTS
        root = tmp / "projects"
        m.transcript_roots = lambda: (root,)
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
        """Put a fake claude first on PATH: calls no model, prints claude -p's JSON or fails, depending on mode."""
        b = tmp / "bin"
        b.mkdir(exist_ok=True)
        out = {"ok": {"type": "result", "is_error": False, "result": RUN_OK["text"], "structured_output": RUN_OK["structured_output"],
                      "total_cost_usd": RUN_OK["cost"], "session_id": RUN_OK["session"], "num_turns": RUN_OK["turns"], "subtype": "success"},
               "incomplete": {"type": "result", "is_error": True, "result": "stopped", "num_turns": 50, "subtype": "error_max_turns"}}
        script = b / "claude"
        if mode == "fail":
            script.write_text("#!/bin/sh\necho 'boom: login expired' >&2\nexit 1\n")
        else:
            script.write_text("#!/bin/sh\ncat <<'EOF'\n" + json.dumps(out[mode]) + "\nEOF\n")
        script.chmod(0o755)
        self._path = os.environ["PATH"]
        os.environ["PATH"] = f"{b}:{self._path}"
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

    def teardown(self):
        self.m.transcript_roots, self.m.PROJECTS = self._roots, self._projects
        if getattr(self, "_path", None):
            os.environ["PATH"] = self._path
        if getattr(self, "_home", None):
            os.environ["HOME"] = self._home


class FakeFixture:
    def setup(self, tmp: pathlib.Path):
        import runtime_fake as m
        m.reset()
        for path, t in SCENARIO:
            m.add_turn(path, t)
        for path, t in WORKTREE_SCENARIO:
            m.add_turn(path, t, worktree=True)
        for path, t in SUBAGENT_SCENARIO:
            m.add_turn(path, t)
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
        return {**os.environ, "CMD_MODE": mode}

    def teardown(self):
        import hubconf
        hubconf.get = self._get


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
        copy = pathlib.Path(tempfile.mkdtemp(prefix="hub-propose-contract-")) / "ws"
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


FIXTURES = {"claude": ClaudeFixture, "fake": FakeFixture, "command": CommandFixture, "acp": AcpFixture}
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
        bad.turns = lambda *a: iter(())
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
        bad.turns = lambda *a: iter(())   # only half of transcripts
        sys.modules["runtime_half"] = bad
        runtime_ports.RUNTIMES["half"] = "runtime_half"
        try:
            with self.assertRaises(runtime_ports.MissingFunctions):
                runtime_ports.load("half", ports=("transcripts",))
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
        """For each implementation with transcripts: put the scenario in → check the interface mechanically → run
            check(m, env) → clean up. A failure names the implementation."""
        for name in having("transcripts"):
            with self.subTest(runtime=name):
                fx = FIXTURES[name]()
                with tempfile.TemporaryDirectory() as d:
                    m, env = fx.setup(pathlib.Path(d))
                    try:
                        runtime_ports.load(name, ports=("transcripts", "sessions"))
                        check(m, env)
                    finally:
                        fx.teardown()

    def test_turns_roles_text_actions(self):
        def check(m, env):
            ts = sorted(m.turns([WS], T0 - datetime.timedelta(hours=1)), key=lambda t: t.ts)
            self.assertEqual([t.role for t in ts], ["human", "other", "assistant", "other"])
            self.assertEqual(ts[0].text, "regarde les tests, s'il te plaît")
            self.assertEqual(ts[0].session, SID)
            self.assertEqual([(a.kind, a.collab, a.detail) for a in ts[2].actions],
                             [("other", "subagent", ""), ("other", "skill", "dataviz"), ("run_command", None, "")])
            self.assertTrue(all(t.ts.tzinfo is not None for t in ts), "timestamps carry a time zone")
        self.each(check)

    def test_entry_ids_folders_and_calls(self):
        """What the waiting report needs (docs/design/test-waiting.md B7): ids to count copied entries once, the
        session's folder, a command's text and its call id, and the entry that returned its result."""
        def check(m, env):
            ts = sorted(m.turns([WS], T0 - datetime.timedelta(hours=1)), key=lambda t: t.ts)
            self.assertEqual([t.id for t in ts], ["e1", "e2", "e3", "e4"])
            self.assertTrue(all(t.folder == WS for t in ts))
            run = ts[2].actions[2]
            self.assertEqual((run.call, run.command, run.background), ("call-1", "ls", False))
            self.assertEqual(ts[3].results, ("call-1",))
            self.assertFalse(any(t.subagent for t in ts), "subagents only when asked")
        self.each(check)

    def test_subagents(self):
        def check(m, env):
            subs = [t for t in m.turns([WS], T0 - datetime.timedelta(hours=1), subagents=True) if t.subagent]
            self.assertEqual([(t.id, t.session, t.actions[0].command) for t in subs], [("s1", SID, "pytest -q")])
        self.each(check)

    def test_time_window(self):
        def check(m, env):
            mid = T0 + datetime.timedelta(minutes=2)
            self.assertEqual(len(list(m.turns([WS], mid))), 2)
            self.assertEqual(len(list(m.turns([WS], T0 - datetime.timedelta(hours=1), mid))), 2)
        self.each(check)

    def test_paths_are_separate(self):
        def check(m, env):
            humans = [t.text for t in m.turns(["/Users/someone/workspace/other"], T0 - datetime.timedelta(hours=1))]
            self.assertEqual(humans, ["l'autre projet"])
            self.assertEqual(list(m.turns(["/Users/someone/workspace/none"], T0 - datetime.timedelta(hours=1))), [])
        self.each(check)

    def test_worktree_sessions_only_when_asked(self):
        def check(m, env):
            def humans(**kw):
                return {t.text for t in m.turns([WS], T0 - datetime.timedelta(hours=1), **kw) if t.role == "human"}
            self.assertEqual(humans(), {"regarde les tests, s'il te plaît"})
            self.assertEqual(humans(worktrees=True), {"regarde les tests, s'il te plaît", "un message du worktree"})
            self.assertEqual({t.text for t in m.turns(["/Users/someone/workspace/other"], T0 - datetime.timedelta(hours=1),
                                                      worktrees=True) if t.role == "human"}, {"l'autre projet"})
        self.each(check)

    def test_last_activity(self):
        def check(m, env):
            self.assertIsNotNone(m.last_activity(WS))
            self.assertIsNone(m.last_activity("/Users/someone/workspace/none"))
        self.each(check)

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

    def test_without_wake_it_is_unsupported_with_the_runtimes_reason(self):
        self.assertNotIn("wake", runtime_ports.capabilities("claude"))
        with self.assertRaises(runtime_ports.Unsupported):
            runtime_ports.load("claude", ports=("wake",))
        row = [miss for f, ok, miss in runtime_ports.feature_table("claude") if not ok]
        self.assertEqual(len(row), 1)
        self.assertIn(runtime_ports.missing_label("claude", "wake"), row[0])
        self.assertNotEqual(runtime_ports.missing_label("claude", "wake"), runtime_ports.PORT_LABELS["wake"])
        self.assertEqual(runtime_ports.missing_label("acp", "wake"), runtime_ports.PORT_LABELS["wake"])


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
                        task = runtime_ports.Task(point=getattr(fx, "POINT", "p"), prompt="hi", spec={}, env=env or None,
                                                  timeout_s=30,
                                                  output_schema={"type": "object"} if name in ("command", "acp") else None)
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
            t = m.parse_event("open_session_attempt", fx.raw(SID, WS, target_cwd="/elsewhere")).target
            self.assertEqual((t["cwd"], t["elsewhere"]), ("/elsewhere", None))
            t = m.parse_event("open_session_attempt", fx.raw(SID, WS, elsewhere="machine")).target
            self.assertEqual(t["elsewhere"], {"kind": "machine"})
            self.assertIsNone(m.parse_event("prompt", fx.raw(None, WS)), "no session = not a real session running")
            self.assertIsNone(m.parse_event("prompt", "not json"))
        self.each(check)

    def test_inject_and_deny_only_where_meaningful(self):
        def check(m, fx, tmp):
            for ev in ("session_start", "prompt", "tool_used"):
                self.assertIn("hello", m.inject(ev, "hello"), ev)
                self.assertEqual(m.inject(ev, ""), "", f"{ev}: no text, no output")
            for ev in ("session_end", "pre_compact", "open_session_attempt"):
                self.assertEqual(m.inject(ev, "hello"), "", f"{ev} cannot go into the context")
            self.assertIn("no", m.deny("open_session_attempt", "no"))
            self.assertEqual(m.deny("prompt", "no"), "")
        self.each(check)

    def test_install_idempotent(self):
        def check(m, fx, tmp):
            ws = tmp / "ws"
            ws.mkdir()
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
            "Stop": [{"hooks": [{"type": "command", "command": "~/agent-hub/bin/old-thing"}]}]}}
        out = m.install_hooks(json.loads(json.dumps(settings)))
        cmds = [h["command"] for es in out["hooks"].values() for e in es for h in e["hooks"]]
        self.assertIn("echo mine", cmds)
        self.assertIn("my-linter", cmds)
        self.assertFalse([c for c in cmds if "agent-hub/bin/" in c and "hub-event" not in c],
                         "old platform hooks are removed")
        self.assertNotIn("Stop", out["hooks"], "an event left with only old platform hooks is removed entirely")
        self.assertEqual(out["model"], "x", "other settings untouched")
        for name, matcher, ev in m.HOOK_TABLE:
            entries = [e for e in out["hooks"][name] if any(f"hub-event {ev}" in h["command"] for h in e["hooks"])]
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


class ClaudeOnly(unittest.TestCase):
    """The Claude implementation's own concern: a transcript format it does not recognise fails explicitly, never
    silently miscounts."""

    def test_unrecognised_format_fails_loudly(self):
        import runtime_claude as m
        saved = m.transcript_roots
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            sub = root / m.slug_for_path(WS)
            sub.mkdir(parents=True)
            (sub / "x.jsonl").write_text("".join(json.dumps({"kind": "message", "at": i}) + "\n" for i in range(6)))
            m.transcript_roots = lambda: (root,)
            try:
                with self.assertRaises(m.TranscriptFormatError):
                    list(m.turns([WS], T0 - datetime.timedelta(days=3650)))
            finally:
                m.transcript_roots = saved


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
        script = tmp / "agent.py"
        script.write_text("import sys\nprint('a tool was auto-denied', file=sys.stderr)\n")
        saved = hubconf.get
        conf = {"run": [sys.executable, str(script)]}
        hubconf.get = lambda k, default=None: conf if k == "runtime.command" else saved(k, default)
        try:
            res = m.run(runtime_ports.Task(point="p", prompt="héllo", spec={}, cwd=str(tmp), model=None,
                                           output_schema=None, timeout_s=30, env=None))
        finally:
            hubconf.get = saved
        self.assertFalse(res.ok, "a tool that was blocked by permissions and ended without saying anything "
                                 "is not a successful empty answer")
        self.assertIn("auto-denied", res.error)


if __name__ == "__main__":
    unittest.main()
