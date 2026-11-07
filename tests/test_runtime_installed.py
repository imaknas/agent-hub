"""More than one coding agent the user opens sessions with (docs/design/mailbox-runtimes.md A1): hub.json
`runtime.installed` and `runtime.bare_ids`, session ids per runtime, hooks carrying their runtime, session calls routed
by runtime_of(sid).

The characterization part (ClaudeOnlyToday) pins what a machine with only Claude Code does today, through the real
Claude adapter: it was committed before A1 and must keep passing with `runtime.installed` absent or `[claude]`. The
bus is a temporary one; no agent, no hook, no session socket is ever touched; ps and transcripts are stubbed.

    bin/py -m unittest tests.test_runtime_installed
"""
import importlib.machinery
import io
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

SID = "0a1b2c3d-0000-4000-8000-00000000abcd"


def load_file(name: str, path: pathlib.Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


class Bus(unittest.TestCase):
    """A temporary bus with one project, hubbus loaded once and handed to hub-event's handlers."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.ws = self.tmp / "proj"
        self.ws.mkdir()
        hb = self.hb = load_file("hubbus_installed", BIN / "hubbus")
        hb.DB = self.tmp / "bus.db"
        hb.projects = lambda: {"proj": self.ws}
        hb.project_of = lambda cwd: "proj"
        hb.lease_ids = lambda: set()
        self.logged = []
        hb.log = self.logged.append
        for name, value in (("STATE", self.tmp / "state"),):
            p = mock.patch.object(hubconf, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.c = hb.db()
        self.addCleanup(self.c.close)
        self.he = load_file("hub_event_installed", BIN / "hub-event")
        self.he.LOG = self.tmp / "hub-event.log"
        self.he.HANDLERS = {k: [(n, f) for n, f in v if n == "mail"] for k, v in self.he.HANDLERS.items()}
        real_load = self.he._load
        self.he._load = lambda name, path: hb if name == "hubbus_mod" else real_load(name, path)

    def send(self, mid, typ="request", to_session=None, minutes_ago=0):
        import datetime
        p = {"type": typ, "text": f"text {mid}", **({"to_session": to_session} if to_session else {})}
        self.hb.publish(self.c, mid, "message", "other", "proj", None, p)
        ts = (datetime.datetime.now().astimezone() - datetime.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
        self.c.execute("UPDATE events SET ts=? WHERE id=?", (ts, mid))

    def claude_hook(self, event, sid=SID, **extra):
        return json.dumps({"session_id": sid, "cwd": str(self.ws), "hook_event_name": "UserPromptSubmit", **extra})

    def presence(self):
        return [tuple(r) for r in self.c.execute("SELECT session, project FROM presence ORDER BY session")]

    def delivered(self):
        return sorted(r[0] for r in self.c.execute("SELECT id FROM events WHERE type='message.delivered'"))


class ClaudeOnlyToday(Bus):
    """Claude Code alone (no runtime.installed): what a session, a hook and dispatch see."""

    def test_a_hook_records_the_bare_session_id_and_delivers_to_it(self):
        self.send("m1")
        out = self.he.handle("prompt", self.claude_hook("prompt"), runtime_claude)
        self.assertEqual(self.presence(), [(SID, "proj")])
        self.assertEqual(self.delivered(), [f"m1:delivered:{SID}"])
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("[m1]", json.loads(out)["hookSpecificOutput"]["additionalContext"])

    def test_the_sending_session_is_claudes_own_variable(self):
        self.assertEqual(runtime_ports.current_session({"CLAUDE_CODE_SESSION_ID": SID}), SID)
        self.assertIsNone(runtime_ports.current_session({}))

    def dispatch(self, live, tokens):
        started = []

        class Proc:
            def __init__(self, argv, **kw):
                started.append(argv)
                self.stdin = io.StringIO()

        with mock.patch.object(runtime_claude, "live_sessions", lambda: set(live)), \
             mock.patch.object(runtime_claude, "context_tokens", lambda sid: tokens.get(sid)), \
             mock.patch.object(self.hb.subprocess, "Popen", Proc):
            waiting = self.hb.dispatch(self.c)
        return waiting, started

    def test_a_conversation_to_a_live_session_waits(self):
        self.send("m1", "question", to_session=SID)
        self.assertEqual(self.dispatch({SID}, {SID: 1000}), (1, []))

    def test_an_idle_session_is_resumed_with_claudes_arguments(self):
        self.send("m1", "question", to_session=SID, minutes_ago=10)
        waiting, started = self.dispatch(set(), {SID: 1000})
        self.assertEqual(len(started), 1)
        argv = started[0]
        self.assertEqual(argv[1:5], [str(BIN / "llm.py"), "run", "mailbox-conversation", "--cwd"])
        self.assertEqual(argv[6:], ["--", "--resume", SID, "--fork-session"])

    def test_a_delegation_forks_the_recent_live_session(self):
        self.hb.record_presence(self.c, SID, "proj", self.ws)
        self.c.commit()
        self.send("m1", "request", minutes_ago=10)
        _w, started = self.dispatch({SID}, {SID: 1000})
        self.assertEqual(started[0][6:], ["--", "--resume", SID, "--fork-session"])

    def test_the_rules_file_and_local_files(self):
        env = {**os.environ, "AGENT_HUB_ROOT": str(ROOT), "AGENT_HUB_CONFIG": str(self.tmp / "cfg")}
        r = subprocess.run([sys.executable, str(BIN / "hub-runtime"), "instructions-file", str(self.ws)], env=env,
                           capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, "CLAUDE.local.md\n"))
        r = subprocess.run([sys.executable, str(BIN / "hub-runtime"), "local-ignores"], env=env, capture_output=True,
                           text=True)
        self.assertEqual(r.stdout.split(), ["CLAUDE.local.md", ".claude/settings.local.json"])

    def test_the_protected_control_files_are_claudes(self):
        import policy
        self.assertEqual(policy.protect(), [{"name": n, "tree": t} for n, t in runtime_claude.CONTROL])

    def test_doctor_rows_are_the_default_runtimes(self):
        self.assertEqual([f for f, _ok, _m in runtime_ports.feature_table()],
                         [f for f, _ok, _m in runtime_ports.feature_table("claude")])


# ---------- A1 itself: more than one installed runtime ----------

import types  # noqa: E402

import runtime_fake  # noqa: E402

FAKE_SID = "F1"


def hub_json(**runtime):
    """hub.json with these runtime settings, for the test's duration (hubconf.config() is cached in _cfg)."""
    return mock.patch.object(hubconf, "_cfg", {"runtime": runtime})


def half_runtime():
    """A runtime with session events and session state but neither control nor confine (registered as "half" while
    the test runs): what a sandboxed project must refuse."""
    m = types.ModuleType("runtime_half")
    m.parse_event = lambda event, raw: runtime_fake.parse_event(event, raw)
    m.inject = runtime_fake.inject
    m.deny = runtime_fake.deny
    m.install_lifecycle = lambda ws, check=False: (pathlib.Path(ws) / ".half-hooks").exists() is False and (
        check or (pathlib.Path(ws) / ".half-hooks").write_text("x") is None)
    m.alive = lambda sid: False
    m.live_sessions = lambda: set()
    m.context_size = lambda sid: None
    m.current_session = lambda env: env.get("HALF_SESSION")
    return m


class WithHalf(unittest.TestCase):
    def setUp(self):
        sys.modules["runtime_half"] = half_runtime()
        p = mock.patch.dict(runtime_ports.RUNTIMES, {"half": "runtime_half"})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(sys.modules.pop, "runtime_half", None)
        runtime_fake.reset()
        self.addCleanup(runtime_fake.reset)


class Ids(unittest.TestCase):
    def test_installed_defaults_to_the_default_runtime(self):
        with hub_json():
            self.assertEqual(runtime_ports.installed(), ["claude"])
        with hub_json(default="fake"):
            self.assertEqual(runtime_ports.installed(), ["fake"])
        with hub_json(installed=["claude", "fake", "claude", "nosuch"]):
            self.assertEqual(runtime_ports.installed(), ["claude", "fake"])

    def test_bare_ids_are_claudes_unless_pinned_and_never_follow_the_default(self):
        with hub_json(default="fake", installed=["fake", "claude"]):
            self.assertEqual(runtime_ports.qualify("claude", SID), SID)
            self.assertEqual(runtime_ports.qualify("fake", FAKE_SID), "fake:F1")
            self.assertEqual(runtime_ports.runtime_of(SID), ("claude", SID))
            self.assertEqual(runtime_ports.runtime_of("fake:F1"), ("fake", "F1"))
        with hub_json(bare_ids="fake", installed=["fake", "claude"]):
            self.assertEqual(runtime_ports.qualify("fake", FAKE_SID), FAKE_SID)
            self.assertEqual(runtime_ports.qualify("claude", SID), f"claude:{SID}")
            self.assertEqual(runtime_ports.runtime_of(f"claude:{SID}"), ("claude", SID))

    def test_an_mcp_id_belongs_to_no_runtime(self):
        self.assertEqual(runtime_ports.runtime_of("mcp:12:34"), (None, "mcp:12:34"))
        self.assertEqual(runtime_ports.runtime_of(None), (None, None))

    def test_the_schema_and_config_check_know_the_keys(self):
        import minischema
        schema = json.loads((ROOT / "contracts" / "hub.v1.schema.json").read_text())
        base = minischema.errors(schema, {})
        self.assertEqual(minischema.errors(schema, {"runtime": {"installed": ["claude", "fake"], "bare_ids": "claude"}}),
                         base)
        self.assertNotEqual(minischema.errors(schema, {"runtime": {"installed": "claude"}}), base)
        tmp = pathlib.Path(tempfile.mkdtemp())
        (tmp / "hub.json").write_text(json.dumps({"runtime": {"installed": ["claude", "nosuch"]}}))
        r = subprocess.run([sys.executable, str(BIN / "config-check")], cwd=tmp, capture_output=True, text=True,
                           env={**os.environ, "AGENT_HUB_ROOT": str(ROOT), "AGENT_HUB_CONFIG": str(tmp),
                                "AGENT_HUB_LANG_UI": "en"})
        self.assertIn("`nosuch`", r.stdout + r.stderr)


class CurrentSession(WithHalf):
    def test_one_installed_runtime_answers_from_the_environment(self):
        with hub_json(installed=["fake"], default="fake"):
            self.assertEqual(runtime_ports.current_session({"FAKE_RUNTIME_SESSION": FAKE_SID}), "fake:F1")

    def test_several_the_nearest_ancestor_a_runtime_claims_decides(self):
        env = {"CLAUDE_CODE_SESSION_ID": SID, "FAKE_RUNTIME_SESSION": FAKE_SID}
        claims = mock.patch.object(runtime_fake, "session_process", lambda cmd: "fakeagent" in cmd, create=True)
        with hub_json(installed=["claude", "fake"]), claims:
            for chain, want in (([(9, "/bin/sh -c x"), (8, "fakeagent --serve"), (7, "claude --resume x")], "fake:F1"),
                                ([(9, "/bin/zsh"), (8, "/usr/local/bin/claude"), (7, "fakeagent")], SID),
                                ([(9, "/bin/zsh"), (8, "launchd")], None)):
                with mock.patch.object(runtime_ports, "ancestors", lambda chain=chain: iter(chain)):
                    self.assertEqual(runtime_ports.current_session(env), want)
            # only one of them finds an id: that one, no walk
            with mock.patch.object(runtime_ports, "ancestors", lambda: iter([])):
                self.assertEqual(runtime_ports.current_session({"FAKE_RUNTIME_SESSION": FAKE_SID}), "fake:F1")

    def test_claude_claims_its_own_processes(self):
        self.assertTrue(runtime_claude.session_process("/Users/x/.local/bin/claude --resume abc"))
        self.assertTrue(runtime_claude.session_process("node /x/node_modules/@anthropic-ai/claude-code/cli.js"))
        self.assertFalse(runtime_claude.session_process("/bin/zsh -c claudette"))

    def test_ancestors_walks_up_from_this_process(self):
        chain = list(runtime_ports.ancestors())
        self.assertTrue(chain)
        self.assertEqual(chain[0][0], os.getppid())


class Routed(WithHalf):
    def test_sessions_are_the_union_and_each_call_goes_to_its_runtime(self):
        runtime_fake.start(FAKE_SID)
        runtime_fake.set_context(FAKE_SID, 77)
        with hub_json(installed=["claude", "fake", "half"]), \
                mock.patch.object(runtime_claude, "live_sessions", lambda: {SID}), \
                mock.patch.object(runtime_claude, "context_tokens", lambda sid: 1000 if sid == SID else None):
            s = runtime_ports.Sessions()
            self.assertEqual(s.live_sessions(), {SID, "fake:F1"})
            self.assertEqual((s.context_size(SID), s.context_size("fake:F1"), s.context_size("half:x")), (1000, 77, None))
            self.assertTrue(s.alive("fake:F1"))
            self.assertFalse(s.alive(FAKE_SID))
            self.assertTrue(s.can_resume(SID))
            self.assertEqual(s.resume_args(SID), ["--resume", SID, "--fork-session"])
            self.assertFalse(s.can_resume("fake:F1"))   # not the runtime mailbox-conversation runs on
            with self.assertRaises(runtime_ports.Unsupported):
                s.resume_args("fake:F1")
            self.assertTrue(s.wake("fake:F1", "📬"))
            self.assertEqual(runtime_fake._woken, [(FAKE_SID, "📬")])
            with tempfile.TemporaryDirectory() as d, mock.patch.object(hubconf, "secrets_dir", lambda: pathlib.Path(d)):
                self.assertFalse(s.wake(SID, "📬"))   # Claude's wake, routed to it: no waiter armed for SID
            with self.assertRaises(runtime_ports.Unsupported):
                s.wake("half:x", "📬")


class RoutedDispatch(Bus):
    def setUp(self):
        super().setUp()
        runtime_fake.reset()
        self.addCleanup(runtime_fake.reset)
        self.handled = []

        def run_handler(c, proj, ws, rows):
            c.execute("BEGIN IMMEDIATE")
            self.hb.claim(c, rows, "handler", run="h")
            c.execute("COMMIT")
            self.handled.append([r["id"] for r in rows])
        self.hb.run_handler = run_handler

    def dispatch(self, live=(), tokens=None):
        started = []

        class Proc:
            def __init__(self, argv, **kw):
                started.append(argv)
                self.stdin = io.StringIO()

        with hub_json(installed=["claude", "fake"]), \
                mock.patch.object(runtime_claude, "live_sessions", lambda: set(live)), \
                mock.patch.object(runtime_claude, "context_tokens", lambda sid: (tokens or {}).get(sid)), \
                mock.patch.object(self.hb.subprocess, "Popen", Proc):
            waiting = self.hb.dispatch(self.c)
        return waiting, started

    def test_a_live_session_of_another_runtime_waits_and_is_woken_through_it(self):
        runtime_fake.start(FAKE_SID)
        self.send("m1", "question", to_session="fake:F1")
        self.assertEqual(self.dispatch(), (1, []))
        self.assertEqual([s for s, _t in runtime_fake._woken], [FAKE_SID])

    def test_an_idle_session_of_a_runtime_the_platform_cannot_resume_is_a_delegation(self):
        runtime_fake.set_context(FAKE_SID, 10)
        self.send("m1", "question", to_session="fake:F1", minutes_ago=10)
        _w, started = self.dispatch()
        self.assertEqual((started, self.handled), ([], [["m1"]]))

    def test_a_delegation_forks_only_a_session_that_can_be_resumed(self):
        runtime_fake.start(FAKE_SID)
        runtime_fake.set_context(FAKE_SID, 10)
        self.hb.record_presence(self.c, SID, "proj", self.ws, "claude")
        self.hb.record_presence(self.c, "fake:F1", "proj", self.ws, "fake")
        self.c.execute("UPDATE presence SET seen='2000-01-01T00:00:00+00:00' WHERE session=?", (SID,))
        self.c.commit()
        self.send("m1", "request", minutes_ago=10)
        _w, started = self.dispatch(live={SID}, tokens={SID: 1000})
        self.assertEqual(started, [])   # the Claude session is too old for FORK_RECENT_H, the fake one can't be resumed
        self.assertEqual(self.handled, [["m1"]])


class Hooks(WithHalf, Bus):
    def setUp(self):
        Bus.setUp(self)
        WithHalf.setUp(self)

    def fake_hook(self, sid=FAKE_SID):
        return json.dumps({"sid": sid, "dir": str(self.ws)})

    def runtimes(self):
        return [tuple(r) for r in self.c.execute("SELECT session, runtime FROM presence ORDER BY session")]

    def test_each_runtimes_hook_records_its_sessions_in_the_platforms_ids(self):
        self.send("m1")
        self.hb.sessions_port = lambda: mock.Mock(live_sessions=lambda: {SID, "fake:F1"}, judges=lambda sid: True)
        with hub_json(installed=["claude", "fake"]):
            self.he.handle("prompt", self.claude_hook("prompt"), name="claude")
            out = self.he.handle("prompt", self.fake_hook(), name="fake")
        self.assertEqual(self.runtimes(), [(SID, "claude"), ("fake:F1", "fake")])
        self.assertEqual(self.delivered(), [f"m1:delivered:{SID}"])
        self.assertEqual(out, "")   # the Claude session was told first and holds it

    def test_a_hook_without_runtime_is_the_default_runtimes(self):
        with hub_json():
            self.he.handle("prompt", self.claude_hook("prompt"))
        self.assertEqual(self.runtimes(), [(SID, "claude")])

    def test_main_reads_the_runtime_flag(self):
        seen = []
        self.he.handle = lambda event, raw, rt=None, name=None: seen.append((event, name)) or ""
        self.he._read_stdin = lambda: ""
        self.assertEqual(self.he.main(["--runtime", "fake", "prompt"]), 0)
        self.assertEqual(self.he.main(["prompt"]), 0)
        self.assertEqual(seen, [("prompt", "fake"), ("prompt", None)])

    def test_hubbus_hook_takes_the_runtime_too(self):
        self.send("m1")
        out = io.StringIO()
        with hub_json(installed=["claude", "fake"]), \
                mock.patch.object(self.hb, "hook_input", lambda: json.loads(self.fake_hook())), \
                mock.patch("sys.stdout", out):
            self.hb.cmd_hook("prompt", "fake")
        self.assertEqual(self.runtimes(), [("fake:F1", "fake")])
        self.assertEqual(self.delivered(), ["m1:delivered:fake:F1"])
        self.assertIn("[m1]", json.loads(out.getvalue())["say"])

    def test_in_a_sandboxed_project_a_runtime_without_control_and_confine_does_nothing(self):
        self.send("m1")
        caged = (mock.patch.object(self.he.project_git, "project_at", lambda ws: ("proj", {"name": "proj"})),
                 mock.patch.object(self.he.project_git.isolation, "sandboxed", lambda doc: True))
        self.he._workspace = lambda cwd: self.ws
        with hub_json(installed=["claude", "half"]), caged[0], caged[1]:
            self.assertEqual(self.he.handle("prompt", self.fake_hook(), name="half"), "")
            self.he.handle("prompt", self.claude_hook("prompt"), name="claude")
        self.assertEqual(self.runtimes(), [(SID, "claude")])
        self.assertIn("refused:half", self.he.LOG.read_text())

    def test_install_puts_in_every_installed_runtimes_hooks(self):
        ws = self.tmp / "inst"
        ws.mkdir()
        with hub_json(installed=["claude", "fake"]):
            self.assertEqual(self.he.install(ws), 0)
        self.assertTrue((ws / ".claude" / "settings.local.json").exists())
        self.assertTrue((ws / ".fake-hooks.json").exists())
        with hub_json(default="none"):
            self.assertEqual(self.he.install(self.tmp / "nothing"), 3)


class Projects(WithHalf):
    def setUp(self):
        super().setUp()
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()

    def test_the_protected_control_files_are_every_installed_runtimes(self):
        import policy
        with hub_json(installed=["claude", "fake", "half"]):
            names = [x["name"] for x in policy.protect()]
        self.assertEqual(names, [n for n, _t in runtime_claude.CONTROL] + [".fake-hooks.json"])

    def test_sandboxed_projects_get_only_the_hooks_of_a_runtime_with_control_and_confine(self):
        rps = load_file("rps_installed", BIN / "render-project-settings.py")
        ws = {}
        for name in ("open", "boxed"):
            ws[name] = self.tmp / name
            (ws[name] / ".agent-memory").mkdir(parents=True)
        index = {"projects": {
            "open": {"name": "open", "paths": {"workspace": str(ws["open"])}},
            "boxed": {"name": "boxed", "paths": {"workspace": str(ws["boxed"])}, "enforce": {"sandbox": True}}}}
        with hub_json(installed=["claude", "fake", "half"]):
            plan = rps.other_lifecycles(index, apply=False)
            self.assertEqual(sorted((c.target, c.detail.rsplit("(", 1)[-1]) for c in plan),
                             [("agent-settings:boxed", "fake)"), ("agent-settings:open", "fake)"),
                              ("agent-settings:open", "half)")])
            rps.other_lifecycles(index, apply=True)
            self.assertEqual(rps.other_lifecycles(index, apply=False), [])
        self.assertTrue((ws["boxed"] / ".fake-hooks.json").exists())
        self.assertFalse((ws["boxed"] / ".half-hooks").exists())
        self.assertTrue((ws["open"] / ".half-hooks").exists())

    def test_doctor_has_a_column_per_runtime(self):
        ah = load_file("agenthub_installed", BIN / "agenthub")
        rows = []
        with hub_json(installed=["claude", "fake", "half"]), mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "en"}):
            ah.doctor_runtimes(lambda level, what, fix="": rows.append((level, what)), "claude",
                               runtime_ports.installed(), runtime_ports.optional_features())
        text = "\n".join(f"{level} {what}" for level, what in rows)
        self.assertIn("coding agents: claude, fake, half", text)
        self.assertIn("claude available; fake available; half off (has no", text)
        self.assertIn("claude: the platform's rules go into CLAUDE.local.md", text)
        self.assertIn("half: no rules file", text)
        self.assertIn("half: its hooks are not installed in sandboxed projects", text)

    def test_one_managed_block_per_runtime_with_a_rules_file(self):
        env = {**os.environ, "AGENT_HUB_ROOT": str(ROOT), "AGENT_HUB_CONFIG": str(self.tmp)}
        (self.tmp / "hub.json").write_text(json.dumps({"runtime": {"installed": ["claude", "fake", "none"]}}))
        r = subprocess.run([sys.executable, str(BIN / "hub-runtime"), "instructions-files", str(self.tmp)], env=env,
                           capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout.split()), (0, ["CLAUDE.local.md", ".fake-instructions.md"]))
        r = subprocess.run([sys.executable, str(BIN / "hub-runtime"), "local-ignores"], env=env, capture_output=True,
                           text=True)
        self.assertEqual(r.stdout.split(), ["CLAUDE.local.md", ".claude/settings.local.json", ".fake-instructions.md",
                                            ".fake-hooks.json"])


class SyncLocalSetup(unittest.TestCase):
    """sync-local-setup writes the managed block into each installed runtime's rules file, in a temp instance."""
    import test_i18n_golden_shell as _g
    setUp, write_cfg, git, repo, run_ = (_g.ShellGolden.setUp, _g.ShellGolden.write_cfg, _g.ShellGolden.git,
                                         _g.ShellGolden.repo, _g.ShellGolden.run_)

    def test_a_block_in_each_rules_file(self):
        self.cfg["runtime"] = {"installed": ["claude", "fake"]}
        self.write_cfg()
        p = self.repo(self.tmp / "ws" / "proj")
        (p / ".agent-memory").mkdir()
        out = self.run_(BIN / "sync-local-setup.sh", p)
        for f in ("CLAUDE.local.md", ".fake-instructions.md"):
            self.assertIn("<!-- brunna:begin -->", (p / f).read_text(), out)
            self.assertIn(f, out)
        self.assertTrue((p / ".fake-hooks.json").exists(), out)
        exclude = (p / ".git" / "info" / "exclude").read_text()
        for f in ("CLAUDE.local.md", ".fake-instructions.md", ".fake-hooks.json"):
            self.assertIn(f, exclude.split())


if __name__ == "__main__":
    unittest.main()
