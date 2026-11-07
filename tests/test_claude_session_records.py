"""Which Claude Code sessions dispatch counts as live (runtime_claude.live_sessions; docs/design/mailbox-runtimes.md B1,
"Left": a forked session's id is not on its command line).

CharacterizeArgv pins what live_sessions reads from the process table: the ids on Claude Code processes' command lines
(--resume / --session-id, spaced or with =). It was committed before the hook records existed and must keep passing
unchanged; that includes reading a fork's parent id off `--resume=<id> --fork-session` (that process runs another id).

The hook records (runtime_ports.record_session, written by hub-event's session_record): a session on no command line
counts while its recorded process runs with the recorded start time (Records), recorded by the real hook path for a
plain and a sandboxed project (HookPath), seen by dispatch as the session to wake and to hold back for (Dispatch), and
pruned when their process is gone however long they were idle (Prune).

A temporary secrets folder and bus; the process table is handed in (CharacterizeArgv) or holds a `sleep` this test
starts as the "session" process. No Claude session is started, nothing is sent.

    bin/py -m unittest tests.test_claude_session_records
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import runtime_claude  # noqa: E402
import runtime_ports  # noqa: E402
import test_mailbox_dispatch as tmd  # noqa: E402
import test_mailbox_wake as tmw  # noqa: E402

A = "11111111-2222-4333-8444-555555555555"
B = "22222222-3333-4444-8555-666666666666"
C = "33333333-4444-4555-8666-777777777777"
DESKTOP = "/Users/u/.claude/remote/ccd-cli/2.1.309-40c3beb4ef2f --output-format stream-json --verbose"


def ps(*lines):
    """subprocess.run as live_sessions calls it, answering with this process table."""
    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, "\n".join(lines) + "\n", "")
    return run


class SecretsDir(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        p = mock.patch.object(hubconf, "secrets_dir", lambda: self.tmp / "secrets")
        p.start()
        self.addCleanup(p.stop)


class CharacterizeArgv(SecretsDir):
    """The command-line path, as it was before the hook records."""

    def live(self, *lines):
        with mock.patch.object(runtime_claude.subprocess, "run", ps(*lines)):
            return runtime_claude.live_sessions()

    def test_ids_on_claude_command_lines(self):
        self.assertEqual(self.live(f"{DESKTOP} --resume={A}", f"claude --session-id {B}", "vim notes.md"), {A, B})

    def test_a_fork_reads_as_its_parent(self):
        self.assertEqual(self.live(f"{DESKTOP} --resume={A} --fork-session"), {A})

    def test_a_desktop_session_started_fresh(self):
        self.assertEqual(self.live(f"{DESKTOP} --session-id={C}"), {C})

    def test_no_session_without_an_id_on_the_command_line(self):
        self.assertEqual(self.live(f"{DESKTOP}", "claude", "claude -p hello"), set())

    def test_an_unreadable_process_table_is_a_failed_lookup_not_no_sessions(self):
        def fail(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, "", "ps: denied")
        with mock.patch.object(runtime_claude.subprocess, "run", fail):
            with self.assertRaises(runtime_ports.SessionsUnknown):
                runtime_claude.live_sessions()
            self.assertTrue(runtime_claude.alive(A), "can't tell: not gone")

    def test_a_process_table_that_hangs_is_a_failed_lookup(self):
        seen = {}

        def hang(argv, **kw):
            seen.update(kw)
            raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
        with mock.patch.object(runtime_claude.subprocess, "run", hang):
            with self.assertRaises(runtime_ports.SessionsUnknown):
                runtime_claude.live_sessions()
        self.assertTrue(seen.get("timeout"), "it has a timeout")

    def test_process_records_that_cannot_be_looked_up_are_a_failed_lookup(self):
        d = hubconf.session_addresses_dir()
        d.mkdir(parents=True)
        (d / f"{A}.live").write_text(json.dumps({"runtime": "claude", "pid": os.getpid(),
                                                 "start": runtime_ports.process_start(os.getpid()), "cwd": "/x"}))
        real = subprocess.run

        def only_table(argv, **kw):
            if "command" in argv:
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.CompletedProcess(argv, 1, "", "ps: denied")

        if os.path.exists("/proc/self/stat"):
            with mock.patch.object(runtime_ports.os.path, "exists", lambda p: p != "/proc/self/stat"), \
                    mock.patch("subprocess.run", only_table), self.assertRaises(runtime_ports.SessionsUnknown):
                runtime_claude.live_sessions()
        else:
            with mock.patch("subprocess.run", only_table), self.assertRaises(runtime_ports.SessionsUnknown):
                runtime_claude.live_sessions()
        self.assertIs(subprocess.run, real)
        with mock.patch("subprocess.run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "")):
            self.assertEqual(runtime_ports.process_starts([999999]), {} if not os.path.exists("/proc/self/stat") else {},
                             "ps -p with no such pid exits 1 and says nothing: that isn't a failure")

    def test_alive_asks_the_same_set(self):
        with mock.patch.object(runtime_claude.subprocess, "run", ps(f"claude --resume {A}")):
            self.assertTrue(runtime_claude.alive(A))
            self.assertFalse(runtime_claude.alive(B))
            self.assertFalse(runtime_claude.alive(None))


def session_process(test) -> subprocess.Popen:
    """A process standing in for a session's CLI (its command line names no session), killed at cleanup."""
    p = subprocess.Popen(["sleep", "60"])
    test.addCleanup(p.wait)
    test.addCleanup(p.kill)
    for _i in range(100):   # until its start time can be read
        if runtime_ports.process_start(p.pid):
            break
        time.sleep(0.02)
    return p


def end(p: subprocess.Popen) -> None:
    p.kill()
    p.wait()


class Records(SecretsDir):
    """A session the hooks recorded with its process: live while that process runs with the start time recorded."""

    def setUp(self):
        super().setUp()
        self.dir = hubconf.session_addresses_dir()

    def record(self, sid, pid, **kw):
        return runtime_ports.record_session(sid, "claude", pid, str(self.tmp), **kw)

    def test_a_fork_on_no_command_line_is_live_through_its_record(self):
        p = session_process(self)
        self.assertNotIn(B, runtime_claude.live_sessions())
        self.assertTrue(self.record(B, p.pid))
        self.assertIn(B, runtime_claude.live_sessions())
        self.assertTrue(runtime_claude.alive(B))
        self.assertIn(B, runtime_ports.Sessions(["claude"]).live_sessions(), "the router's union has it too")
        rec = json.loads((self.dir / f"{B}.live").read_text())
        self.assertEqual(rec, {"runtime": "claude", "pid": p.pid, "start": runtime_ports.process_start(p.pid),
                               "cwd": str(self.tmp)})
        self.assertEqual((self.dir / f"{B}.live").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.dir.stat().st_mode & 0o777, 0o700)

    def test_a_dead_process_is_not_live(self):
        p = session_process(self)
        self.record(B, p.pid)
        end(p)
        self.assertNotIn(B, runtime_claude.live_sessions())

    def test_another_start_time_is_not_live(self):
        """The pid now runs another process (reused): its start time differs from the recorded one."""
        p = session_process(self)
        self.record(B, p.pid)
        f = self.dir / f"{B}.live"
        rec = json.loads(f.read_text())
        f.write_text(json.dumps({**rec, "start": str(int(rec["start"]) - 100)}))
        self.assertNotIn(B, runtime_claude.live_sessions())

    def test_malformed_or_foreign_records_do_not_count(self):
        p = session_process(self)
        self.dir.mkdir(parents=True)
        start = runtime_ports.process_start(p.pid)
        (self.dir / f"{A}.live").write_text("not json")
        (self.dir / f"{B}.live").write_text(json.dumps({"runtime": "claude", "pid": str(p.pid), "start": start}))
        (self.dir / f"codex:{C}.live").write_text(json.dumps({"runtime": "codex", "pid": p.pid, "start": start}))
        (self.dir / f"{C}.live").write_text(json.dumps({"runtime": "codex", "pid": p.pid, "start": start}))
        self.assertEqual(runtime_ports.recorded_sessions("claude"), set(),
                         "a record says whose it is, and that must match its id")

    def test_one_process_runs_one_session(self):
        """A fork whose first hook still carried the parent's id, or a /clear: the newer id replaces the older."""
        p, q = session_process(self), session_process(self)
        self.record(A, p.pid)
        self.record(C, q.pid)
        self.record(B, p.pid, refresh=False)
        self.assertEqual(runtime_ports.recorded_sessions("claude"), {B, C})
        self.assertEqual(sorted(f.name for f in self.dir.iterdir()), [f"{B}.live", f"{C}.live"])

    def test_after_the_start_a_record_of_the_same_process_is_left_as_it_is(self):
        p = session_process(self)
        self.assertTrue(self.record(B, p.pid))
        with mock.patch.object(runtime_ports, "process_starts", side_effect=AssertionError("looked up")):
            self.assertFalse(self.record(B, p.pid, refresh=False), "a tool call costs no process lookup")
        q = session_process(self)
        self.assertTrue(self.record(B, q.pid, refresh=False), "another process: written again")
        self.assertEqual(json.loads((self.dir / f"{B}.live").read_text())["pid"], q.pid)

    def test_nothing_recorded_for_what_names_no_process_or_no_file(self):
        self.assertFalse(self.record(B, None))
        self.assertFalse(self.record(B, 1))
        self.assertFalse(self.record("../x", os.getpid()))
        p = session_process(self)
        end(p)
        self.assertFalse(self.record(B, p.pid), "a process that is gone")
        self.assertFalse(self.dir.exists() and any(self.dir.iterdir()))

    def test_the_session_process_comes_from_the_hook_environment(self):
        self.assertEqual(runtime_claude.session_pid({"CLAUDE_PID": "4242"}), 4242)
        for env in ({}, {"CLAUDE_PID": ""}, {"CLAUDE_PID": "x"}, {"CLAUDE_PID": "1"}):
            self.assertIsNone(runtime_claude.session_pid(env))

    def test_one_identity_rule_with_the_mcp_presence(self):
        hb = tmd.load_hubbus()
        self.assertEqual(hb.proc_start(os.getpid()), runtime_ports.process_start(os.getpid()))
        p = session_process(self)
        self.assertEqual(runtime_ports.process_starts([os.getpid(), p.pid, 0, -3]),
                         {os.getpid(): hb.proc_start(os.getpid()), p.pid: hb.proc_start(p.pid)})


class HookPath(SecretsDir):
    """hub-event records the session through the real Claude adapter, for a plain and a sandboxed project."""

    def setUp(self):
        super().setUp()
        self.dir = hubconf.session_addresses_dir()
        m = self.m = tmw.load_file("hub_event_records", ROOT / "bin" / "hub-event")
        m.LOG = self.tmp / "hub-event.log"
        m._workspace = lambda cwd: self.tmp
        self.proj = ("proj", {"name": "proj"})
        p = mock.patch.object(m.project_git, "project_at", lambda ws: self.proj)
        p.start()
        self.addCleanup(p.stop)
        # only the handlers under test: no bus, no git
        keep = {"session_record", "wake_address_drop"}
        m.HANDLERS = {k: [(n, f) for n, f in v if n in keep] for k, v in m.HANDLERS.items()}
        self.session = session_process(self)
        p = mock.patch.dict(os.environ, {"CLAUDE_PID": str(self.session.pid)})
        p.start()
        self.addCleanup(p.stop)
        for var in ("HUB_LLM_POINT", "HUBBUS_LEASE", "HUB_AS_PROJECT", "CLAUDE_CODE_ENTRYPOINT"):
            os.environ.pop(var, None)

    def hook(self, event, sid, **extra):
        raw = json.dumps({"session_id": sid, "cwd": str(self.tmp), **extra})
        return self.m.handle(event, raw, runtime_claude, "claude")

    def test_every_hook_in_a_project_records_and_the_end_removes(self):
        for event in ("session_start", "prompt", "tool_used"):
            self.hook(event, B)
            self.assertIn(B, runtime_claude.live_sessions(), event)
        self.assertIn("ran=session_record", self.m.LOG.read_text())
        self.hook("session_end", B)
        self.assertEqual(list(self.dir.iterdir()), [])
        self.assertNotIn(B, runtime_claude.live_sessions())

    def test_agent_hubs_own_sessions_are_recorded(self):
        """The hub is a project to dispatch (hubbus.projects) though the registry index leaves it out: its main checkout
        and its linked worktrees record like any project's."""
        self.proj = None
        for ws in (hubconf.MAIN, hubconf.MAIN / ".claude" / "worktrees" / "w"):
            self.m._workspace = lambda cwd, ws=ws: ws
            self.hook("prompt", B)
            self.assertEqual(runtime_ports.recorded_sessions("claude"), {B}, ws)
            self.hook("session_end", B)

    def test_a_fork_whose_start_carried_the_parents_id(self):
        self.hook("session_start", A, source="fork")
        self.hook("prompt", B)
        self.assertEqual(runtime_ports.recorded_sessions("claude"), {B})

    def test_not_for_the_platforms_own_runs_nor_outside_a_project(self):
        with mock.patch.dict(os.environ, {"HUB_LLM_POINT": "librarian"}):
            self.hook("session_start", B)
        self.proj = None
        self.hook("prompt", B)
        self.assertFalse(self.dir.exists() and any(self.dir.iterdir()))

    def test_not_for_headless_runs_a_person_is_not_in(self):
        """The waiter's rules: an SDK entrypoint, or -p among the session process's own arguments (a -p run inside a
        session inherits that session's entrypoint)."""
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}):
            self.hook("session_start", B)
        printing = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "-p", "say hi"])
        self.addCleanup(printing.wait)
        self.addCleanup(printing.kill)
        for _i in range(100):   # once it has exec'd (before that, ps shows the parent's arguments)
            if "-p" in (runtime_claude._argv(printing.pid) or []):
                break
            time.sleep(0.05)
        with mock.patch.dict(os.environ, {"CLAUDE_PID": str(printing.pid), "CLAUDE_CODE_ENTRYPOINT": "cli"}):
            self.hook("session_start", B)
            self.hook("prompt", B)
        self.assertFalse(self.dir.exists() and any(self.dir.iterdir()))
        self.hook("prompt", B)
        self.assertEqual(runtime_ports.recorded_sessions("claude"), {B}, "the person's own session still is")

    def test_a_sandboxed_projects_session_is_recorded_by_its_hook_outside_the_sandbox(self):
        self.assertLessEqual(self.m.CAGED_NEEDS, runtime_ports.capabilities("claude"))
        self.assertNotIn("session_record", self.m.AS_PROJECT, "it writes the secrets directory, never as the project")
        with mock.patch.object(self.m.isolation, "sandboxed", lambda proj: True), \
                mock.patch.object(self.m, "_as_project", side_effect=AssertionError("ran as the project")):
            for event in ("session_start", "prompt", "tool_used"):
                self.hook(event, B)
        self.assertEqual(runtime_ports.recorded_sessions("claude"), {B})
        import policy
        self.assertTrue(any((self.dir / f"{B}.live").is_relative_to(d) for d in policy.secret_dirs()),
                        "where the sandbox can neither read nor write")


class Dispatch(tmw.Harness):
    """Dispatch through the real Claude adapter: a forked session (id on no command line, a hook record) is the one
    a delegation wakes and the one dispatch holds back for; once its process is gone, neither."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(hubconf, "secrets_dir", lambda: self.tmp / "secrets")
        p.start()
        self.addCleanup(p.stop)
        self.rt = runtime_ports.Sessions(["claude"])
        self.session = session_process(self)
        runtime_ports.record_session(B, "claude", self.session.pid, str(self.ws))

    def woken(self):
        return [(r["id"], json.loads(r["payload"])["session"]) for r in
                self.c.execute("SELECT id, payload FROM events WHERE type='message.woken' ORDER BY seq")]

    def test_a_forked_session_is_the_wake_target_and_is_held_back_for(self):
        self.seen(B)
        live = self.rt.live_sessions()
        self.assertEqual(self.m.wake_target(self.c, "proj", live), B)
        self.assertEqual(self.m.active_sessions(self.c, "proj", live, []), [B])
        self.send("m1", "proj", "request")
        self.assertEqual(self.dispatch(), 1, "waits for the session's hook")
        self.assertEqual((self.handled, self.resumed), ([], []))
        self.assertEqual(self.woken(), [("m1:woken", B)])

    def test_a_reply_to_a_forked_session_wakes_it_and_waits(self):
        self.seen(B)
        self.send("m1", "proj", "question", to_session=B)
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual((self.handled, self.resumed), ([], []))
        self.assertEqual(self.woken(), [("m1:woken", B)])

    def test_once_its_process_is_gone_the_project_is_unattended(self):
        self.seen(B)
        end(self.session)
        self.assertIsNone(self.m.wake_target(self.c, "proj", self.rt.live_sessions()))
        self.send("m1", "proj", "request")
        self.dispatch()
        self.assertEqual(self.handled, [("proj", ["m1"])])
        self.assertEqual(self.woken(), [])


class Prune(SecretsDir):
    """Records go with their process, not with their age; the other files as before."""

    def test_records_of_ended_sessions_go_and_idle_running_ones_stay(self):
        hb = tmd.load_hubbus()
        d = hubconf.session_addresses_dir()
        running, ended, reused = session_process(self), session_process(self), session_process(self)
        for sid, p in ((A, running), (B, ended), (C, reused)):
            runtime_ports.record_session(sid, "claude", p.pid, str(self.tmp))
        f = d / f"{C}.live"
        f.write_text(json.dumps({**json.loads(f.read_text()), "start": "1"}))
        (d / "D.live").write_text("{}")
        end(ended)
        old = time.time() - hb.ADDRESS_GRACE_S - 3600
        for n in (f"{A}.live", f"{B}.live"):
            os.utime(d / n, (old, old))
        self.assertEqual(sorted(hb.prune_wake_addresses(live=set())), sorted([B, C, "D"]))
        self.assertEqual([p.name for p in d.iterdir()], [f"{A}.live"], "idle for an hour, its process still runs")


if __name__ == "__main__":
    unittest.main()
