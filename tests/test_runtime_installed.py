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


if __name__ == "__main__":
    unittest.main()
