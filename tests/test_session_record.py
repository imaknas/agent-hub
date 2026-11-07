"""The session record (docs/design/mailbox-session-first.md, step 3): what a session's files say about it, before any routing
reads them.

A temporary secrets folder, bus, log and state; the process table holds `sleep`s this test starts as the "session"
processes, and the agents listing is a fake command. No Claude session is started, nothing is sent, nothing reaches a live
file.

    bin/run-tests        (running this module directly in a worktree reads the real config through .hub-home)
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
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import runtime_ports  # noqa: E402
import test_bus_sender as tbs  # noqa: E402
import test_claude_session_records as tcsr  # noqa: E402
import test_mailbox_triage as tmt  # noqa: E402
import test_mailbox_wake as tmw  # noqa: E402

A, B = tcsr.A, tcsr.B
LONG = "x" * 700


class CharacterizeRecord(tcsr.SecretsDir):
    """A session's process record as hub-event's session_record leaves it today."""

    def test_its_fields(self):
        p = tcsr.session_process(self)
        runtime_ports.record_session(B, "claude", p.pid, str(self.tmp))
        rec = json.loads(hubconf.session_file(B, "live").read_text())
        self.assertEqual(rec, {"runtime": "claude", "pid": p.pid, "start": runtime_ports.process_start(p.pid),
                               "cwd": str(self.tmp)})

    def test_after_the_start_it_is_left_as_it_is_at_no_cost(self):
        p = tcsr.session_process(self)
        runtime_ports.record_session(B, "claude", p.pid, str(self.tmp))
        with mock.patch.object(runtime_ports, "process_starts", side_effect=AssertionError("looked up")):
            self.assertFalse(runtime_ports.record_session(B, "claude", p.pid, "/elsewhere", refresh=False))
        self.assertEqual(json.loads(hubconf.session_file(B, "live").read_text())["cwd"], str(self.tmp))

    def test_a_refresh_rewrites_the_folder_it_saw(self):
        p = tcsr.session_process(self)
        runtime_ports.record_session(B, "claude", p.pid, str(self.tmp))
        self.assertTrue(runtime_ports.record_session(B, "claude", p.pid, "/elsewhere"))
        self.assertEqual(json.loads(hubconf.session_file(B, "live").read_text())["cwd"], "/elsewhere")


class Project(tcsr.SecretsDir):
    """`project` in the record: set when the process is first recorded, carried over whenever it is rewritten."""

    def record(self, sid, pid, cwd="/here", **kw):
        return runtime_ports.record_session(sid, "claude", pid, cwd, **kw)

    def read(self, sid):
        return json.loads(hubconf.session_file(sid, "live").read_text())

    def test_set_when_first_recorded(self):
        p = tcsr.session_process(self)
        self.record(B, p.pid, project="alpha")
        self.assertEqual(self.read(B)["project"], "alpha")

    def test_a_compaction_after_wandering_doesnt_move_it(self):
        p = tcsr.session_process(self)
        self.record(B, p.pid, project="alpha")
        self.assertTrue(self.record(B, p.pid, cwd="/there", project="beta"))   # refresh: the session_start of a compaction
        rec = self.read(B)
        self.assertEqual((rec["project"], rec["cwd"]), ("alpha", "/there"))
        self.assertFalse(self.record(B, p.pid, cwd="/there", refresh=False, project="beta"), "a tool call: nothing to do")
        self.assertEqual(self.read(B)["project"], "alpha")

    def test_a_new_id_in_the_same_process_keeps_it(self):
        """A /clear, or a fork whose first hook still carried the parent's id."""
        p = tcsr.session_process(self)
        self.record(A, p.pid, project="alpha")
        self.record(B, p.pid, cwd="/there", refresh=False, project="beta")
        self.assertEqual(self.read(B)["project"], "alpha")
        self.assertFalse(hubconf.session_file(A, "live").exists())

    def test_a_new_process_is_recorded_where_it_is(self):
        """A resumed session has a new process, with the same id."""
        p, q = tcsr.session_process(self), tcsr.session_process(self)
        self.record(B, p.pid, project="alpha")
        self.record(B, q.pid, project="beta")
        self.assertEqual(self.read(B)["project"], "beta")

    def test_a_reused_pid_is_a_new_process(self):
        p = tcsr.session_process(self)
        self.record(B, p.pid, project="alpha")
        f = hubconf.session_file(B, "live")
        f.write_text(json.dumps({**self.read(B), "start": "1"}))
        self.record(B, p.pid, project="beta")
        self.assertEqual(self.read(B)["project"], "beta")

    def test_an_old_record_gets_it_at_its_next_hook_at_no_cost(self):
        p = tcsr.session_process(self)
        self.record(B, p.pid)
        self.assertNotIn("project", self.read(B))
        with mock.patch.object(runtime_ports, "process_starts", side_effect=AssertionError("looked up")):
            self.assertTrue(self.record(B, p.pid, cwd="/there", refresh=False, project="alpha",
                                        wanted=lambda pid: self.fail("asked again")))
        rec = self.read(B)
        self.assertEqual((rec["project"], rec["cwd"], rec["pid"]), ("alpha", "/here", p.pid), "the rest as it was")
        self.assertEqual(hubconf.session_file(B, "live").stat().st_mode & 0o777, 0o600)

    def test_an_old_record_gets_it_from_a_start_too(self):
        p = tcsr.session_process(self)
        self.record(B, p.pid)
        self.record(B, p.pid, project="alpha")
        self.assertEqual(self.read(B)["project"], "alpha")

    def test_a_project_of_the_wrong_shape_is_no_project(self):
        p = tcsr.session_process(self)
        self.record(B, p.pid)
        f = hubconf.session_file(B, "live")
        f.write_text(json.dumps({**self.read(B), "project": 7}))
        self.assertIn(B, runtime_ports.recorded_sessions("claude"), "the record still counts")
        self.assertTrue(self.record(B, p.pid, refresh=False, project="alpha"))
        self.assertEqual(self.read(B)["project"], "alpha")

    def test_open_sessions_carry_it(self):
        p, q = tcsr.session_process(self), tcsr.session_process(self)
        self.record(A, p.pid, project="alpha")
        self.record(B, q.pid, project="beta")
        got = runtime_ports.Sessions(["claude"]).open_sessions()
        self.assertEqual({s: r["project"] for s, r in got.items()}, {A: "alpha", B: "beta"})
        tcsr.end(q)
        self.assertEqual(set(runtime_ports.open_sessions()), {A})


class ProjectThroughTheHook(tcsr.HookFixture):
    """hub-event's session_record, through the real Claude adapter."""

    def read(self, sid):
        return json.loads(hubconf.session_file(sid, "live").read_text())

    def test_the_project_at_its_folder(self):
        self.hook("session_start", B)
        self.assertEqual(self.read(B)["project"], "proj")

    def test_wandering_and_compacting_keep_it(self):
        self.hook("session_start", B)
        self.proj = ("other", {"name": "other"})
        self.hook("prompt", B)
        self.hook("session_start", B, source="compact")
        self.assertEqual(self.read(B)["project"], "proj")

    def test_an_old_record_gets_it_even_with_refresh_false(self):
        p = self.session
        runtime_ports.record_session(B, "claude", p.pid, str(self.tmp))
        self.assertNotIn("project", self.read(B))
        self.hook("tool_used", B)
        self.assertEqual(self.read(B)["project"], "proj")

    def test_the_hubs_own_sessions_get_the_hubs_project(self):
        self.proj = None
        for ws in (hubconf.MAIN, hubconf.MAIN / ".claude" / "worktrees" / "w"):
            self.m._workspace = lambda cwd, ws=ws: ws
            self.hook("prompt", B)
            self.assertEqual(self.read(B)["project"], hubconf.hub_project(), ws)
            self.hook("session_end", B)


class CharacterizeWaiter(tmw.ClaudeSessionsDir):
    """The waiter as it reads the Stop hook's input."""

    def hub_event(self):
        m = tmw.load_file("hub_event_record", BIN / "hub-event")
        m.LOG = self.tmp / "hub-event.log"
        return m

    def stdin(self, text):
        """A stdin as a hook's: not a terminal, readable at once, larger than a pipe's buffer if it wants to be."""
        f = self.tmp / "stdin"
        f.write_text(text)
        fh = open(f)
        self.addCleanup(fh.close)
        return mock.patch.object(sys, "stdin", fh)

    def test_a_headless_run_reads_its_input_and_arms_nothing(self):
        m = self.hub_event()
        raw = json.dumps({"session_id": self.SID, "cwd": str(self.tmp), "hook_event_name": "Stop",
                          "last_assistant_message": "y" * 200000})
        env = {"CLAUDE_PID": str(os.getpid()), "CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}
        with self.stdin(raw), mock.patch.dict(os.environ, env):
            for var in ("HUB_LLM_POINT", "HUBBUS_LEASE"):
                os.environ.pop(var, None)
            self.assertEqual(m.main(["--runtime", "claude", "wait"]), 0)
        self.assertEqual(self.files(), [], "nothing armed, nothing kept")
        self.assertIn("wait - ran=headless", m.LOG.read_text())

    def test_a_platform_run_reads_its_input_and_does_nothing(self):
        m = self.hub_event()
        with self.stdin(self.raw()), mock.patch.dict(os.environ, {"HUB_LLM_POINT": "librarian"}):
            self.assertEqual(m.main(["--runtime", "claude", "wait"]), 0)
        self.assertEqual(self.files(), [])
        self.assertFalse(m.LOG.exists())

    def test_the_hook_declares_the_waiters_own_timeout(self):
        self.assertEqual(self.rc.wait_hook()["timeout"], self.rc.WAIT_TIMEOUT_S)


class WaitLifetime(tmw.ClaudeSessionsDir):
    def test_the_hook_timeout_is_fourteen_days(self):
        self.assertEqual(self.rc.wait_hook()["timeout"], 14 * 86400)
        self.assertLess(self.rc.WAIT_TIMEOUT_S * 1000, 2 ** 31, "fits a 32-bit millisecond timer")

    def test_the_waiter_outlives_what_the_old_timeout_was(self):
        """Past a day with its clock moved on, it is still there and still heartbeats."""
        seen = []

        def at_a_day_and_more():
            seen.append(self.waiter_file().exists())
            self.request("wake")
        steps = [at_a_day_and_more]
        self.assertEqual(self.wait(steps, step_s=2 * 86400)[0], 2)
        self.assertEqual(seen, [True])
        self.assertGreaterEqual(self.clock, 1000 + 2 * 86400)


class Last(tmw.ClaudeSessionsDir):
    """<session>.last: the end of the last reply, in the adapter, only under a label that lets it."""

    def raw(self, sid=None, **kw) -> str:
        return json.dumps({"session_id": sid or self.SID, "cwd": str(self.tmp), "hook_event_name": "Stop", **kw})

    def stop(self, keep=True, **kw):
        """One Stop, the waiter woken by a request at once so that it ends."""
        self.request("wake")
        out = self.rc.wait(self.raw(**kw), {"CLAUDE_PID": str(os.getpid())}, keep_last=keep, poll=0,
                           argv_of=lambda pid: self.DESKTOP_ARGV, sleep=lambda s: None)
        self.assertEqual(out[0], 2)

    def last(self):
        return hubconf.session_file(self.SID, "last")

    def test_written_mode_600_and_read_through_the_port(self):
        self.stop(last_assistant_message="said this")
        self.assertEqual(self.last().read_text(), "said this")
        self.assertEqual(self.last().stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.rc.session_summary(self.SID), "said this")
        self.assertEqual(runtime_ports.Sessions(["claude"]).session_summary(self.SID), "said this")

    def test_truncated_to_its_last_500_characters(self):
        self.stop(last_assistant_message="a" * 10 + "\u5b57" * 500)
        self.assertEqual(self.rc.session_summary(self.SID), "\u5b57" * 500, "characters, not bytes, from the end")

    def test_absent_nothing_is_written(self):
        self.stop()
        self.assertFalse(self.last().exists())
        self.assertIsNone(self.rc.session_summary(self.SID))
        for odd in ("", None, 7, ["x"]):
            self.stop(last_assistant_message=odd)
            self.assertFalse(self.last().exists(), odd)

    def test_a_turn_with_no_reply_leaves_no_earlier_one_behind(self):
        self.stop(last_assistant_message="said this")
        self.stop()
        self.assertFalse(self.last().exists(), "it would read as this turn's")

    def test_not_written_under_a_label_that_isnt_include_and_what_is_there_goes(self):
        self.stop(keep=False, last_assistant_message="said this")
        self.assertFalse(self.last().exists())
        self.stop(last_assistant_message="said this")
        self.stop(keep=False, last_assistant_message="said that")
        self.assertFalse(self.last().exists(), "wandered to a project that doesn't let it, or the label changed")

    def test_a_lone_surrogate_from_a_json_escape_doesnt_stop_the_waiter(self):
        raw = '{"session_id": "%s", "cwd": "/x", "last_assistant_message": "a\\ud83d b"}' % self.SID
        self.request("wake")
        self.assertEqual(self.rc.wait(raw, {"CLAUDE_PID": str(os.getpid())}, keep_last=True, poll=0,
                                      argv_of=lambda pid: self.DESKTOP_ARGV)[0], 2)
        self.assertIn("ud83d", self.rc.session_summary(self.SID))

    def test_a_write_that_fails_doesnt_stop_the_waiter(self):
        real = os.open

        def refuse(path, *a, **k):
            if ".last." in str(path):
                raise PermissionError(path)
            return real(path, *a, **k)
        with mock.patch.object(os, "open", refuse):
            self.stop(last_assistant_message="said this")
        self.assertFalse(self.last().exists())

    def test_a_headless_run_keeps_nothing(self):
        env = {"CLAUDE_PID": str(os.getpid()), "CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}
        self.assertEqual(self.rc.wait(self.raw(last_assistant_message="x"), env, keep_last=True, poll=0)[2], "headless")
        self.assertEqual(self.rc.wait(self.raw(last_assistant_message="x"), {"CLAUDE_PID": str(os.getpid())}, keep_last=True,
                                      poll=0, argv_of=lambda pid: ["claude", "-p", "hi"])[2], "headless")
        self.assertEqual(self.files(), [])

    def test_its_text_reaches_no_log_and_no_stderr(self):
        m = tmw.load_file("hub_event_last", BIN / "hub-event")
        m.LOG = self.tmp / "hub-event.log"
        secret = "the words of the last reply"
        self.request("wake")
        err = __import__("io").StringIO()
        with mock.patch.object(self.rc, "_argv", lambda pid: self.DESKTOP_ARGV), \
                mock.patch.object(m, "keep_last", lambda rt, raw: True), \
                mock.patch.dict(os.environ, {"CLAUDE_PID": str(os.getpid())}), \
                __import__("contextlib").redirect_stderr(err):
            for var in ("HUB_LLM_POINT", "HUBBUS_LEASE", "CLAUDE_CODE_ENTRYPOINT"):
                os.environ.pop(var, None)
            self.assertEqual(m.wait("claude", self.raw(last_assistant_message=secret)), 2)
        self.assertEqual(self.rc.session_summary(self.SID), secret)
        self.assertNotIn(secret, err.getvalue())
        self.assertNotIn(secret, m.LOG.read_text())

    def test_never_backed_up_and_in_every_sandboxs_deny_read(self):
        import backup
        import policy
        self.stop(last_assistant_message="said this")
        self.assertFalse(backup.covers(self.last()))
        self.assertTrue(any(self.last().is_relative_to(d) for d in policy.secret_dirs()))


class LastLabel(tcsr.SecretsDir):
    """hub-event's keep_last: the project at the session's folder, from the registry's label."""

    def setUp(self):
        super().setUp()
        self.m = tmw.load_file("hub_event_label", BIN / "hub-event")
        self.m.LOG = self.tmp / "hub-event.log"
        self.proj, self.private = ("proj", {"name": "proj"}), set()
        self.m._workspace = lambda cwd: self.tmp
        for target, fn in ((self.m.project_git, "project_at"), (hubconf, "reflection_private")):
            p = mock.patch.object(target, fn, {"project_at": lambda ws: self.proj,
                                               "reflection_private": lambda: self.private}[fn])
            p.start()
            self.addCleanup(p.stop)

    def keep(self):
        import runtime_claude
        return self.m.keep_last(runtime_claude, json.dumps({"session_id": B, "cwd": str(self.tmp)}))

    def test_include_keeps(self):
        self.assertTrue(self.keep())

    def test_any_other_label_doesnt(self):
        self.private = {"proj"}
        self.assertFalse(self.keep())

    def test_a_registry_that_cant_be_read_doesnt(self):
        self.private = None
        self.assertFalse(self.keep())

    def test_a_folder_in_no_project_doesnt(self):
        self.proj = None
        self.assertFalse(self.keep())

    def test_the_hubs_own_folder_counts_as_its_project(self):
        self.proj, self.m._workspace = None, lambda cwd: hubconf.MAIN
        self.assertTrue(self.keep())
        self.private = {hubconf.hub_project()}
        self.assertFalse(self.keep())

    def test_a_broken_lookup_doesnt_stop_the_waiter(self):
        import runtime_claude
        with mock.patch.object(self.m.project_git, "project_at", side_effect=OSError("no")):
            self.assertFalse(self.keep())
        self.assertFalse(self.m.keep_last(runtime_claude, "not json"))
        self.assertFalse(self.m.keep_last(object(), "{}"), "a runtime with no lifecycle")


class LastAtTheEnd(tcsr.HookFixture):
    def test_the_sessions_end_takes_it(self):
        self.hook("session_start", B)
        f = hubconf.session_file(B, "last")
        f.write_text("said this")
        self.hook("pre_compact", B)
        self.assertTrue(f.exists(), "compacting isn't ending")
        self.hook("session_end", B)
        self.assertFalse(f.exists())


class LastThroughTheHook(tbs.WorktreeFixture):
    """The real entry point with the runtime's real hook input, the label from the registry: a request waits, so the waiter
    takes it and ends at once."""
    REPLY = "the words of the last reply"

    def stop(self, project):
        d = self.tmp / "secrets" / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{self.SID}.wake-0").write_text("wake")
        payload = json.dumps({"session_id": self.SID, "cwd": str(self.ws[project]), "hook_event_name": "Stop",
                              "last_assistant_message": self.REPLY})
        r = subprocess.run([sys.executable, str(BIN / "hub-event"), "--runtime", "claude", "wait"], cwd=self.ws[project],
                           input=payload, capture_output=True, text=True, timeout=60, env=self.hook_env())
        self.assertEqual((r.returncode, r.stderr.strip()), (2, "wake"))
        self.assertNotIn(self.REPLY, r.stdout + r.stderr)
        return d / f"{self.SID}.last"

    def label(self, project, value):
        self.docs[project]["privacy"] = {"reflection": value}
        (self.tmp / "state" / "registry" / "index.json").write_text(json.dumps({"projects": self.docs}))

    def test_a_label_of_include_keeps_it(self):
        self.label("open", "include")
        f = self.stop("open")
        self.assertEqual(f.read_text(), self.REPLY)
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)

    def test_no_label_is_include_as_in_the_registry(self):
        self.assertTrue(self.stop("open").exists())

    def test_any_other_label_keeps_nothing_and_takes_what_was_there(self):
        self.assertTrue(self.stop("open").exists())
        for value in ("stats_only", "exclude"):
            self.label("open", value)
            self.assertFalse(self.stop("open").exists(), value)

    def test_a_folder_in_no_project_keeps_nothing(self):
        self.ws["nowhere"] = self.tmp / "nowhere"
        self.ws["nowhere"].mkdir()
        self.assertFalse(self.stop("nowhere").exists())

    def test_nothing_on_the_bus_or_in_the_log(self):
        self.letter_to("open", "a bus to be on")
        self.stop("open")
        self.assertFalse([r for r in self.rows() if self.REPLY in r["payload"]])
        logs = (ROOT / ".logs" / "hub-event.log")
        self.assertNotIn(self.REPLY, logs.read_text() if logs.exists() else "")

    def test_a_sessions_end_in_a_worktree_takes_it(self):
        wt = self.tree("open")
        self.hook("prompt", wt, "UserPromptSubmit")
        f = self.stop("open")
        self.assertTrue(f.exists())
        self.hook("end", wt, "PreCompact")
        self.assertTrue(f.exists())
        self.hook("end", wt, "SessionEnd")
        self.assertFalse(f.exists())


class LastPrune(tcsr.SecretsDir):
    def setUp(self):
        super().setUp()
        self.hb = tcsr.tmd.load_hubbus()
        self.dir = hubconf.session_addresses_dir()

    def last(self, sid):
        f = hubconf.session_file(sid, "last")
        f.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        f.write_text("said this")
        old = time.time() - self.hb.ADDRESS_GRACE_S - 3600
        os.utime(f, (old, old))
        return f

    def test_it_survives_an_idle_prune_while_its_process_runs(self):
        p = tcsr.session_process(self)
        runtime_ports.record_session(B, "claude", p.pid, str(self.tmp))
        f = self.last(B)
        self.assertEqual(self.hb.prune_wake_addresses(live=set()), [])
        self.assertTrue(f.exists(), "idle for hours, its process still runs")

    def test_it_goes_when_its_process_is_gone(self):
        p = tcsr.session_process(self)
        runtime_ports.record_session(B, "claude", p.pid, str(self.tmp))
        f = self.last(B)
        tcsr.end(p)
        self.assertEqual(self.hb.prune_wake_addresses(live=set()), [B])
        self.assertFalse(f.exists())

    def test_one_with_no_record_goes_when_it_is_old(self):
        f = self.last(B)
        self.assertEqual(self.hb.prune_wake_addresses(live=set()), [B])
        self.assertFalse(f.exists())

    def test_it_is_kept_while_the_process_lookup_fails(self):
        p = tcsr.session_process(self)
        runtime_ports.record_session(B, "claude", p.pid, str(self.tmp))
        f = self.last(B)
        with mock.patch.object(runtime_ports, "running_records", side_effect=runtime_ports.SessionsUnknown("x")):
            self.assertEqual(self.hb.prune_wake_addresses(live=set()), [])
        self.assertTrue(f.exists())

    def test_it_is_one_of_the_sessions_files_at_its_end(self):
        f = self.last(B)
        self.assertIn(f, hubconf.session_files(B))
        self.assertEqual(hubconf.session_of_file(f.name), B)


class CanWake(tmw.ClaudeSessionsDir):
    def beat(self, age=0):
        f = self.waiter_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("1-test")
        t = time.time() - age
        os.utime(f, (t, t))

    def test_a_fresh_heartbeat(self):
        self.beat()
        self.assertTrue(self.rc.can_wake(self.SID))
        self.assertIsNone(self.rc.wake_gap(self.SID))

    def test_a_stale_heartbeat(self):
        self.beat(age=self.rc.WAITER_FRESH_S + 5)
        self.assertFalse(self.rc.can_wake(self.SID))
        self.assertEqual(self.rc.wake_gap(self.SID), "stopped")

    def test_a_missing_heartbeat(self):
        self.assertFalse(self.rc.can_wake(self.SID))
        self.assertEqual(self.rc.wake_gap(self.SID), "not_armed")

    def test_an_id_that_is_no_file_name(self):
        self.assertFalse(self.rc.can_wake("../x"))
        self.assertEqual(self.rc.wake_gap("../x"), "not_armed")

    def test_it_says_what_wake_would_do(self):
        self.assertFalse(self.rc.wake(self.SID, "x"))
        self.beat()
        self.assertTrue(self.rc.wake(self.SID, "x"))

    def test_through_the_router(self):
        rt = runtime_ports.Sessions(["claude"])
        self.assertFalse(rt.can_wake(self.SID))
        self.assertEqual(rt.wake_gap(self.SID), "not_armed")
        self.beat()
        self.assertTrue(rt.can_wake(self.SID))
        self.assertIsNone(rt.wake_gap(self.SID))

    def test_a_runtime_with_no_wake_port(self):
        rt = runtime_ports.Sessions(["claude"])
        rt.mods = {"claude": object()}
        self.assertFalse(rt.can_wake(self.SID))
        self.assertEqual(rt.wake_gap(self.SID), "no_port")


class SessionStatus(unittest.TestCase):
    """session_status through a real process standing in for the CLI: the arguments it gets, each answer, each failure."""
    SID = "11111111-2222-4333-8444-555555555555"

    def setUp(self):
        import runtime_claude
        self.rc = runtime_claude
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.exe = self.tmp / "claude"
        p = mock.patch.object(hubconf, "tool", lambda name: str(self.exe))
        p.start()
        self.addCleanup(p.stop)

    def cli(self, body):
        self.exe.write_text("#!/bin/sh\n" + body)
        self.exe.chmod(0o755)

    def listing(self, *rows):
        self.cli(f"cat <<'E'\n{json.dumps(list(rows))}\nE\n")

    def row(self, status, sid=None):
        return {"pid": 1, "cwd": "/x", "kind": "interactive", "startedAt": 1, "sessionId": sid or self.SID, "name": "n",
                "status": status}

    def test_each_status(self):
        for status in ("busy", "idle", "waiting"):
            self.listing(self.row("idle", "other"), self.row(status))
            self.assertEqual(self.rc.session_status(self.SID), status)

    def test_a_status_it_doesnt_know(self):
        self.listing(self.row("thinking"))
        self.assertEqual(self.rc.session_status(self.SID), "unknown")

    def test_an_unlisted_session(self):
        self.listing(self.row("busy", "other"))
        self.assertEqual(self.rc.session_status(self.SID), "unknown")

    def test_a_failure_exit(self):
        self.cli(f"echo '{json.dumps([self.row('busy')])}'\nexit 3\n")
        self.assertEqual(self.rc.session_status(self.SID), "unknown")

    def test_output_that_isnt_a_listing(self):
        for body in ("echo not json\n", "echo '{\"a\": 1}'\n", "echo '[1, null]'\n", "printf '\\377\\376'\n"):
            self.cli(body)
            self.assertEqual(self.rc.session_status(self.SID), "unknown", body)

    def test_no_such_executable(self):
        self.assertEqual(self.rc.session_status(self.SID), "unknown")

    def test_a_timeout(self):
        self.cli("exec sleep 30\n")
        with mock.patch.object(self.rc, "AGENTS_TIMEOUT_S", 0.2):
            t = time.time()
            self.assertEqual(self.rc.session_status(self.SID), "unknown")
        self.assertLess(time.time() - t, 5)

    def test_the_timeout_is_short(self):
        self.assertLessEqual(self.rc.AGENTS_TIMEOUT_S, 5)

    def test_only_the_listing_is_asked_for_and_it_cant_wait_on_a_person(self):
        """Bare, the command is the agent view: the listing is --json, with nothing on stdin."""
        seen = self.tmp / "seen"
        self.cli(f'echo "$*" > "{seen}"\nif [ -t 0 ] || read -r _x; then echo tty-or-input >> "{seen}"; fi\necho "[]"\n')
        self.rc.session_status(self.SID)
        self.assertEqual(seen.read_text().split("\n")[0], "agents --json")
        self.assertNotIn("tty-or-input", seen.read_text())

    def test_through_the_router(self):
        self.listing(self.row("busy"))
        self.assertEqual(runtime_ports.Sessions(["claude"]).session_status(self.SID), "busy")

    def test_a_runtime_with_no_such_port_or_an_odd_answer_cant_tell(self):
        rt = runtime_ports.Sessions(["claude"])
        rt.mods = {"claude": object()}
        self.assertEqual(rt.session_status(self.SID), "unknown")
        self.assertIsNone(rt.session_summary(self.SID))
        rt.mods = {"claude": mock.Mock(session_status=lambda sid: "asleep")}
        self.assertEqual(rt.session_status(self.SID), "unknown")


class WorktreeMail(tbs.WorktreeFixture):
    """A linked worktree's mail goes through `hub-event worktree`, outside any sandbox, through the real script with the
    runtime's real hook input. Before: told in an open project, and in a sandboxed one the re-run couldn't reach the bus."""

    def hook_env(self):   # the after-tool rate limit's stamp is per session id, in the temp folder
        return {**super().hook_env(), "TMPDIR": str(self.tmp)}

    def told(self, text):
        [letter] = [r["id"] for r in self.rows("type='message'") if json.loads(r["payload"]).get("text") == text]
        return [json.loads(r["payload"]).get("session") for r in self.rows("type='message.delivered' AND ref=?", (letter,))]

    def hook_out(self, what, wt, name, **extra):
        payload = json.dumps({"session_id": self.SID, "cwd": str(wt), "hook_event_name": name, "prompt": "hello", **extra})
        r = subprocess.run(["bash", str(BIN / "worktree-memory.sh"), what], cwd=wt, input=payload + "\n",
                           capture_output=True, text=True, timeout=120, env=self.hook_env())
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def context(self, out):
        """A hook's stdout as the runtime reads it: one JSON object, or nothing it can use."""
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]

    def test_an_open_project_is_told_once(self):
        wt = self.tree("open")
        self.letter_to("open", "for the open project", typ="request")
        out = self.hook_out("prompt", wt, "UserPromptSubmit")
        self.assertIn("for the open project", self.context(out))
        self.assertEqual(self.told("for the open project"), [self.SID])
        self.assertNotIn("for the open project", self.hook_out("prompt", wt, "UserPromptSubmit"))
        self.assertEqual(self.told("for the open project"), [self.SID])

    def test_a_sandboxed_project_is_told(self):
        wt = self.tree("box")
        self.letter_to("box", "for the box", typ="request")
        out = self.hook_out("prompt", wt, "UserPromptSubmit")
        self.assertIn("for the box", self.context(out))
        self.assertEqual(self.told("for the box"), [self.SID])
        self.assertTrue(self.record().exists(), "on record first: a tick in between mustn't judge it closed")
        self.assertEqual(json.loads(self.record().read_text())["project"], "box")

    def test_a_sandboxed_project_is_told_at_its_start_and_after_a_tool(self):
        for what, name, extra in (("start", "SessionStart", {}), ("nudge", "PostToolUse", {"tool_input": {"command": "ls"}})):
            with self.subTest(what=what):
                wt = self.tree("box") if what == "start" else self.wt
                self.wt = wt
                self.letter_to("box", f"for the box at {what}", typ="request")
                out = self.hook_out(what, wt, name, **extra)
                self.assertIn(f"for the box at {what}", out)
                self.assertEqual(self.told(f"for the box at {what}"), [self.SID])

    def test_the_hook_the_platform_may_have_written_too_tells_nothing_twice(self):
        """The sandboxed worktree's own settings carry hub-event's hooks; if the session picked them up, both run."""
        wt = self.tree("box")
        self.letter_to("box", "for the box", typ="request")
        payload = json.dumps({"session_id": self.SID, "cwd": str(wt), "hook_event_name": "UserPromptSubmit", "prompt": "hi"})
        first = subprocess.run([sys.executable, str(BIN / "hub-event"), "--runtime", "claude", "prompt"], cwd=wt, input=payload,
                               capture_output=True, text=True, timeout=60, env=self.hook_env())
        self.assertIn("for the box", first.stdout)
        self.assertNotIn("for the box", self.hook_out("prompt", wt, "UserPromptSubmit"))
        self.assertEqual(self.told("for the box"), [self.SID])

    def behind(self, project):
        """A worktree whose memory notes are two commits behind, with its link to them (what `start` makes)."""
        wt = self.tree(project)
        ws, env = self.ws[project], {**os.environ, **tbs.GIT_ENV}
        mem = ws / ".agent-memory"
        (mem / "memory").mkdir()
        (mem / "AGENT.md").write_text("x")
        old = {**env, "GIT_AUTHOR_DATE": "2020-01-01T00:00:00", "GIT_COMMITTER_DATE": "2020-01-01T00:00:00"}
        for args, e in ((("init", "-q", "-b", "agent-memory"), env), (("add", "AGENT.md"), env), (("commit", "-qm", "x"), old)):
            subprocess.run(["git", "-C", str(mem), *args], check=True, env=e)
        (ws / "g").write_text("y")
        subprocess.run(["git", "-C", str(ws), "add", "g"], check=True, env=env)
        subprocess.run(["git", "-C", str(ws), "commit", "-qm", "feat: more"], check=True, env=env)
        subprocess.run(["git", "-C", str(wt), "reset", "-q", "--hard", "main"], check=True, env=env)
        (wt / ".agent-memory").symlink_to(mem)
        return wt

    def test_a_letter_and_a_reminder_come_as_one_output(self):
        """Printed apart they'd be two JSON documents, which the runtime reads as none: the letter told, and lost."""
        for project in ("open", "box"):
            with self.subTest(project=project):
                wt = self.behind(project)
                self.letter_to(project, f"for {project}", typ="request")
                out = self.hook_out("nudge", wt, "PostToolUse", tool_input={"command": "git commit -m x"})
                self.assertIn(f"for {project}", self.context(out))
                self.assertIn(".agent-memory/memory/", self.context(out))
                (pathlib.Path(self.tmp) / f"hubbus-post-tool-{self.SID}").unlink()

    def test_a_tool_that_commits_nothing_gets_no_reminder(self):
        wt = self.behind("box")
        out = self.hook_out("nudge", wt, "PostToolUse", tool_input={"command": "ls"})
        self.assertNotIn(".agent-memory/memory/", out)

    def test_a_broken_record_doesnt_stop_the_mail(self):
        wt = self.tree("open")
        self.letter_to("open", "for the open project", typ="request")
        env = {**self.hook_env(), "CLAUDE_PID": "not a pid"}
        payload = json.dumps({"session_id": self.SID, "cwd": str(wt), "hook_event_name": "UserPromptSubmit", "prompt": "hi"})
        r = subprocess.run(["bash", str(BIN / "worktree-memory.sh"), "prompt"], cwd=wt, input=payload + "\n",
                           capture_output=True, text=True, timeout=120, env=env)
        self.assertIn("for the open project", r.stdout)


class Detach(unittest.TestCase):
    """run-agent-job.sh when its detach step can't run: a failed start, which start_handler sees as one."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.hub = self.tmp / "hub"
        (self.hub / "bin").mkdir(parents=True)
        (self.hub / "agent-jobs").mkdir()
        (self.hub / "agent-jobs" / "job.md").write_text("do it\n")
        for f in ("run-agent-job.sh", "hubpaths.sh"):
            (self.hub / "bin" / f).write_text((BIN / f).read_text())
        py = self.hub / "bin" / "py"
        py.write_text("#!/bin/sh\nexit 1\n")
        py.chmod(0o755)
        (self.hub / "bin" / "run-agent-job.sh").chmod(0o755)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("BRUNNA_", "AGENT_HUB_"))}
        self.env.update(AGENT_HUB_ROOT=str(self.hub), AGENT_HUB_STATE=str(self.tmp / "state"), AGENT_HUB_LANG_UI="en")

    def run_job(self):
        return subprocess.run([str(self.hub / "bin" / "run-agent-job.sh"), "job", str(self.tmp)], env=self.env,
                              capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)

    def test_a_failing_python_is_a_failed_start(self):
        r = self.run_job()
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("started in the background", r.stdout)
        self.assertIn("could not be started", r.stderr)

    def test_a_detach_that_fails_after_its_fork_is_a_failed_start(self):
        """The log can't be opened: its folder is a file by then. The first process would have exited 0 long before."""
        py = self.hub / "bin" / "py"
        py.unlink()
        py.symlink_to(BIN / "py")
        self.env["AGENT_HUB_PYTHON"] = sys.executable
        for f in ("i18n.py", "hubconf.py", "names.py"):
            (self.hub / "bin" / f).symlink_to(BIN / f)
        out = self.hub / ".state" / "agent-jobs" / "job"
        out.mkdir(parents=True)
        (out / "runner.log").mkdir()   # a folder: opening it for writing fails
        r = self.run_job()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("started in the background", r.stdout)
        self.assertEqual(json.loads((out / "status.json").read_text())["state"], "failed")

    def test_start_handler_takes_it_as_failed(self):
        m = tcsr.tmd.load_hubbus()
        m.HUB = self.hub
        m.AGENT_JOBS = self.tmp / "jobs"
        with mock.patch.object(m.prompts, "render", lambda *a, **k: "prompt"), mock.patch.dict(os.environ, self.env):
            why = m.start_handler("job", self.tmp, [{"id": "m1", "payload": "{}"}])
        self.assertIn("could not be started", why or "")


class DispatchPerProject(tmt.Base):
    def test_a_prompt_that_cannot_be_rendered_is_a_failed_start_for_that_project_alone(self):
        self.real_handler()
        self.send("m1", "proj", "request")
        self.send("m2", self.hub, "request")
        real = self.m.prompts.render
        self.assertTrue(callable(real))

        def render(*a, **k):
            if "proj" in str(k.get("notes")):
                raise ValueError("bad template")
            return "prompt"
        self.m.notes_of = lambda p: p
        with mock.patch.object(self.m.prompts, "render", render):
            self.dispatch()
        self.assertEqual([self.letters_of(s) for s in self.started], [["m2"]], "the later project went on")
        p = json.loads(self.rows("type='job.failed' AND ref='m1'")[0]["payload"])
        self.assertEqual(p["job"], "mailbox-start:proj")
        self.assertIn("bad template", p["detail"])
        self.assertEqual(self.vias("m1"), [], "nothing spent")

    def test_anything_else_a_project_breaks_on_stays_with_that_project(self):
        self.send("m1", "proj", "request")
        self.send("m2", self.hub, "request")
        real = self.m.addressed_to

        def addressed(c, proj):
            if proj == "proj":
                c.execute("BEGIN IMMEDIATE")   # broke inside a transaction, as run_handler or claim would
                raise KeyError("boom")
            return real(c, proj)
        self.m.addressed_to = addressed
        self.assertGreaterEqual(self.dispatch(), 1)
        self.assertEqual(self.handled, [(self.hub, ["m2"])])
        self.assertFalse(self.c.in_transaction)
        self.assertIn("boom", "".join(self.logged))
        self.dispatch()
        self.assertEqual(sum("boom" in x for x in self.logged), 1, "logged once while it lasts")
        [f] = self.rows("type='job.failed'")
        p = json.loads(f["payload"])
        self.assertEqual((p["job"], p["detail"]), ("mailbox-dispatch:proj", "KeyError"), "no text on the bus")
        self.m.addressed_to = real
        self.dispatch()
        self.assertEqual(self.handled[-1][0], "proj", "it goes on once it can")


class OwnerQuestion(tmt.Base):
    """A confined run's question on a letter that carries a handoff stays a question: it reaches the owner, it is no answer."""

    def asks(self):
        self.m.publish(self.c, "asks", "message", self.owner, "proj", None,
                       {"type": "question", "text": "x", "effect": "asks", "handoff_id": 7})

    def ask(self, env, *extra):
        self.cli("send", "--to", self.owner, "--reply-to", "asks", "--type", "question", "--text", "which one?", *extra, env=env)

    def question(self):
        [q] = self.rows("type='message' AND source='proj'")
        return q

    def test_a_handlers_goes_without_the_handoff(self):
        self.asks()
        self.ask(tmt.with_letters(tmt.ESCALATION, "asks"))
        q = self.question()
        item = self.m.to_assistant(self.c, q)
        self.assertEqual(item["text"], "which one?", "it reaches the owner")
        self.assertNotIn("handoff_id", item, "not said as the project's answer")
        self.assertIsNone(q["ref"])
        self.assertEqual(self.state("asks").taken, "", "the letter stays open")

    def test_a_forks_too(self):
        self.asks()
        self.ask(tmt.with_letters(tmt.FORK, "asks"))
        self.assertNotIn("handoff_id", self.m.to_assistant(self.c, self.question()))

    def test_a_confined_run_with_no_letters_too(self):
        self.asks()
        self.ask(tmt.EVENT_JOB)
        self.assertNotIn("handoff_id", self.m.to_assistant(self.c, self.question()))

    def test_as_a_record_it_is_a_question_the_owner_side_can_answer(self):
        self.m.RECORDS = True
        self.asks()
        self.ask(tmt.with_letters(tmt.ESCALATION, "asks"))
        [r] = [json.loads(x["payload"]) for x in self.rows("type='record' AND source='proj'")]
        self.assertEqual((r["record"]["type"], r["record"]["terms"]["text"]), ("raised", "which one?"))

    def test_a_session_still_replies_as_before(self):
        self.asks()
        self.ask({})
        self.assertEqual(self.m.to_assistant(self.c, self.question())["handoff_id"], 7, "not a confined run: unchanged")

    def test_its_real_answer_still_carries_the_handoff(self):
        self.asks()
        self.cli("send", "--to", self.owner, "--reply-to", "asks", "--type", "done", "--text", "the answer",
                 env=tmt.with_letters(tmt.ESCALATION, "asks"))
        self.assertEqual(self.m.to_assistant(self.c, self.question())["handoff_id"], 7)

    def test_a_letter_with_no_handoff_keeps_its_thread(self):
        self.send("m1", "proj", "request")
        self.cli("send", "--to", self.owner, "--reply-to", "m1", "--type", "question", "--text", "which one?",
                 env=tmt.with_letters(tmt.ESCALATION, "m1"))
        self.assertEqual(self.question()["ref"], "m1")


class SandboxedOwnerQuestion(tbs.Fixture):
    """The same through a sandboxed project's outbox and view: what the run writes is what the bus gets."""

    @unittest.skipUnless(tbs.seatbelt.can_sandbox() is None, "needs Seatbelt")
    def test_the_file_carries_no_reply(self):
        self.letter_to("open", "a bus to be on")
        m = tbs.load_hubbus()
        m.DB = self.tmp / "bus" / "bus.db"
        c = m.db()
        self.addCleanup(c.close)
        owner = m.OWNER_ADDRESS
        m.publish(c, "asks1", "message", owner, "box", None,
                  {"type": "question", "text": "x", "effect": "asks", "handoff_id": 7})
        self.dispatch()
        self.env.update(HUB_LLM_POINT="mailbox-escalation", AGENT_HUB_CONFINED="1", MAILBOX_LETTERS=json.dumps(["asks1"]))
        r = self.in_sandbox("send", "--to", owner, "--reply-to", "asks1", "--type", "question", "--text", "which one?")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for var in ("HUB_LLM_POINT", "AGENT_HUB_CONFINED", "MAILBOX_LETTERS"):
            del self.env[var]
        self.dispatch()
        got = [json.loads(x["payload"]) for x in self.rows("source='box'")]
        self.assertFalse([p for p in got if p.get("handoff_id") is not None], got)
        self.assertTrue(got, "it reached the bus")


class StatusCount(tcsr.SecretsDir):
    """The status counts open sessions a letter can't wake, and says why."""

    def setUp(self):
        super().setUp()
        self.hb = tcsr.tmd.load_hubbus()
        self.hb.sessions_port = lambda: runtime_ports.Sessions(["claude"])

    def beat(self, sid, age=0):
        f = hubconf.session_file(sid, "waiter")
        f.write_text("1-test")
        t = time.time() - age
        os.utime(f, (t, t))

    def test_each_reason(self):
        p, q, r = [tcsr.session_process(self) for _i in range(3)]
        for sid, x in ((A, p), (B, q), (tcsr.C, r)):
            runtime_ports.record_session(sid, "claude", x.pid, str(self.tmp), project="alpha")
        self.beat(A)
        self.beat(B, age=3600)
        self.assertEqual(self.hb.wake_gaps(), {"open": 3, "n": 2, "why": {"not_armed": 1, "stopped": 1}})

    def test_a_closed_session_doesnt_count(self):
        p = tcsr.session_process(self)
        runtime_ports.record_session(A, "claude", p.pid, str(self.tmp))
        tcsr.end(p)
        self.assertEqual(self.hb.wake_gaps(), {"open": 0, "n": 0, "why": {}})

    def test_a_runtime_with_no_wake_port(self):
        p = tcsr.session_process(self)
        runtime_ports.record_session(A, "claude", p.pid, str(self.tmp))
        rt = runtime_ports.Sessions(["claude"])
        rt.mods = {"claude": object()}
        self.hb.sessions_port = lambda: rt
        self.assertEqual(self.hb.wake_gaps(), {"open": 1, "n": 1, "why": {"no_port": 1}})

    def test_a_lookup_that_fails_says_nothing(self):
        with mock.patch.object(runtime_ports, "running_records", side_effect=runtime_ports.SessionsUnknown("x")):
            self.assertIsNone(self.hb.wake_gaps())
        self.hb.sessions_port = lambda: object()
        self.assertIsNone(self.hb.wake_gaps())


if __name__ == "__main__":
    unittest.main()
