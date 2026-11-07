"""Waking a live session for a new letter (docs/design/mailbox-runtimes.md B1): the `wake` port, dispatch's
once-per-letter use of it, the delivery path the status page shows, and where a session's wake address is kept.

The characterization part (CharacterizeDispatch, CharacterizeSecrets) pins what dispatch, the backup and check-secrets
do today for a runtime without `wake`; it was committed before the port existed and must keep passing unchanged.

Claude's wake (ClaudeWaiter, ClaudeWake, ClaudeDispatch, ClaudeSessionFiles, WokenNotPickedUp): the asyncRewake Stop
waiter through its real code (runtime_claude.wait, hub-event wait) with a clock it is handed and a temporary secrets
folder; the request is taken exactly once, the newest waiter wins, it ends with its session and before its timeout,
headless runs arm none; wake() only for a live waiter; the hook as installed; "woken, not picked up" from the bus. The
characterization of Claude's wake as Unsupported (eaa6c64) was replaced by these when the waiter was built.

A temporary bus; the runtime is a fake (tests.test_mailbox_dispatch's) or the real Claude adapter, no agent or Claude
session is started, no socket is touched.

    bin/py -m unittest tests.test_mailbox_wake
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import test_mailbox_dispatch as tmd  # noqa: E402


def load_file(name: str, path: pathlib.Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Harness(unittest.TestCase):
    """tests.test_mailbox_dispatch's bus and fake runtime, without running its tests again."""
    setUp = tmd.Dispatch.setUp
    send = tmd.Dispatch.send
    seen = tmd.Dispatch.seen
    dispatch = tmd.Dispatch.dispatch

    def types(self) -> set:
        return {r[0] for r in self.c.execute("SELECT type FROM events")}


class CharacterizeDispatch(Harness):
    """A runtime without wake: the two places B1 wakes in behave as before and record nothing more."""

    def test_conversation_to_a_live_session_waits_and_records_only_the_letter(self):
        self.rt.live.add("S1")
        self.seen("S1")
        self.send("m1", "proj", "question", to_session="S1")
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual((self.handled, self.resumed), ([], []))
        self.assertEqual(self.types(), {"message"})

    def test_delegation_while_a_session_is_active_waits_and_records_only_the_letter(self):
        self.rt.live.add("S1")
        self.seen("S1")
        self.send("m1", "proj", "request")
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual((self.handled, self.resumed), ([], []))
        self.assertEqual(self.types(), {"message"})

    def test_after_the_wait_the_routes_are_unchanged(self):
        self.rt.live.add("S1")
        self.seen("S1", minutes_ago=10)
        self.send("m1", "proj", "question", to_session="S1", minutes_ago=10)
        self.send("m2", "proj", "request", minutes_ago=10)
        self.rt.tokens["S1"] = 1000
        self.dispatch()
        self.assertEqual(self.resumed, [("m1", "S1"), ("m2", "S1")])

    def test_status_keeps_its_keys(self):
        import contextlib
        import io
        self.send("m1", "proj", "request")
        out = io.StringIO()
        with mock.patch.object(self.m, "db", lambda *a, **k: self.c), contextlib.redirect_stdout(out):
            self.m.cmd_status(True)
        st = json.loads(out.getvalue())
        self.assertLessEqual({"projects", "stuck", "held", "job_failed_24h", "corrupt"}, set(st))
        self.assertEqual(st["projects"]["proj"]["undelivered"], 1)


class CharacterizeSecrets(unittest.TestCase):
    """The secrets directory's own files: still backed up, still scanned for undeclared credentials."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.secrets = self.tmp / "secrets"
        self.secrets.mkdir()
        p = mock.patch.object(hubconf, "secrets_dir", lambda: self.secrets)
        p.start()
        self.addCleanup(p.stop)

    def test_a_secret_in_the_secrets_directory_is_backed_up(self):
        backup = load_file("backup_wake", ROOT / "bin" / "backup.py")
        (self.secrets / "b2-env").write_text("x")
        with mock.patch.object(backup, "CFG", {"include": [str(self.secrets)]}):
            self.assertTrue(backup.covers(self.secrets / "b2-env"))

    def test_an_undeclared_token_in_the_secrets_directory_is_reported(self):
        cs = load_file("check_secrets_wake", ROOT / "bin" / "check-secrets.py")
        (self.secrets / "api-token").write_text("x")
        hub = hubconf.hub_project()
        self.assertEqual(cs.undeclared(hub, {"name": hub, "paths": {"workspace": str(self.tmp)}}, set()),
                         [self.secrets / "api-token"])


class WakingRuntime(tmd.FakeRuntime):
    """tests.test_mailbox_dispatch's fake runtime with the wake port."""

    def __init__(self, answer=True):
        super().__init__()
        self.answer, self.woken = answer, []

    def wake(self, sid, text):
        self.woken.append((sid, text))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


class Wake(Harness):
    def setUp(self):
        super().setUp()
        self.rt = WakingRuntime()

    def woken_events(self) -> list:
        return [(r["id"], {k: v for k, v in json.loads(r["payload"]).items() if k != "v"}) for r in
                self.c.execute("SELECT id, payload FROM events WHERE type='message.woken' ORDER BY seq")]

    def test_a_conversation_to_a_live_session_wakes_it_once_then_waits_as_before(self):
        self.rt.live.add("S1")
        self.seen("S1")
        self.send("m1", "proj", "question", to_session="S1")
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual([s for s, _t in self.rt.woken], ["S1"])
        self.assertIn("m1", self.rt.woken[0][1])
        self.assertIn("brunna inbox", self.rt.woken[0][1])
        self.assertEqual(self.woken_events(), [("m1:woken", {"session": "S1", "via": "wake", "ok": True})])
        self.assertEqual((self.handled, self.resumed), ([], []))

    def test_a_delegation_wakes_the_session_most_recently_active_in_the_project(self):
        self.rt.live |= {"S1", "S2"}
        self.seen("S1", minutes_ago=3)
        self.seen("S2", minutes_ago=1)
        self.seen("S3")   # not live
        self.send("m1", "proj", "request")
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual([s for s, _t in self.rt.woken], ["S2"])
        self.assertEqual(self.handled, [])

    def test_a_notice_replying_to_a_live_session_wakes_it_once_and_nothing_else(self):
        self.rt.live.add("S1")
        self.send("m1", "proj", "info", to_session="S1")
        self.send("m2", "proj", "done", to_session="S1")
        self.assertEqual(self.dispatch(), 0)   # nothing waits on a notice
        self.dispatch()
        self.assertEqual(sorted(t.split("(")[1].split(")")[0] for _s, t in self.rt.woken), ["m1", "m2"])
        self.assertEqual((self.handled, self.resumed), ([], []))

    def test_a_notice_is_not_woken_for_when_unaddressed_old_or_its_session_gone(self):
        self.rt.live.add("S1")
        self.seen("S1")
        self.send("m1", "proj", "info")                                   # to the project, no session asked
        self.send("m2", "proj", "info", to_session="S1", minutes_ago=30)  # older than the wait: the hook has it
        self.send("m3", "proj", "info", to_session="S9")                  # that session isn't running
        self.dispatch()
        self.assertEqual(self.rt.woken, [])

    def test_the_letter_never_travels_through_wake(self):
        self.rt.live.add("S1")
        self.seen("S1")
        self.send("m1", "proj", "request")
        self.dispatch()
        self.assertNotIn("text m1", self.rt.woken[0][1])
        self.assertEqual(len(self.m.inbox(self.c, "proj", "S1", only_new=True)), 1)   # still for the hook to deliver

    def test_a_delivered_letter_is_not_woken_for(self):
        self.rt.live.add("S1")
        self.seen("S1")
        self.send("m1", "proj", "request")
        self.c.execute("BEGIN IMMEDIATE")
        self.m.mark_delivered(self.c, self.m.inbox(self.c, "proj", "S1"), "S1", "prompt")
        self.c.execute("COMMIT")
        self.dispatch()
        self.assertEqual(self.rt.woken, [])

    def test_an_mcp_presence_alone_is_never_woken(self):
        self.running.add("mcp:1:2")
        self.seen("mcp:1:2", via="tool", runtime="mcp:codex")
        self.send("m1", "proj", "request")
        self.dispatch()
        self.assertEqual(self.rt.woken, [])

    def test_a_failed_wake_is_recorded_once_and_changes_nothing(self):
        self.rt.answer = False
        self.rt.live.add("S1")
        self.seen("S1")
        self.send("m1", "proj", "request")
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual(len(self.rt.woken), 1)
        self.assertEqual(self.woken_events(), [("m1:woken", {"session": "S1", "via": "wake", "ok": False})])

    def test_a_broken_wake_is_recorded_with_its_error_and_dispatch_goes_on(self):
        self.rt.answer = OSError("refused")
        self.rt.live.add("S1")
        self.seen("S1")
        self.send("m1", "proj", "request")
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual(self.woken_events()[0][1]["error"], "OSError")

    def test_a_runtime_that_cannot_wake_this_session_records_nothing(self):
        import runtime_ports
        self.rt.answer = runtime_ports.Unsupported("no")
        self.rt.live.add("S1")
        self.seen("S1")
        self.send("m1", "proj", "request")
        self.dispatch()
        self.assertEqual(self.woken_events(), [])

    def test_after_the_wait_the_routes_are_unchanged(self):
        CharacterizeDispatch.test_after_the_wait_the_routes_are_unchanged(self)
        self.assertEqual(self.rt.woken, [])   # past the wait: nothing to wake for

    def test_status_shows_how_each_letter_went(self):
        import contextlib
        import io
        self.rt.live.add("S1")
        self.seen("S1")
        self.send("m1", "proj", "request")
        self.send("n1", "proj", "info")   # a notification: no route
        self.dispatch()
        self.c.execute("BEGIN IMMEDIATE")
        self.m.mark_delivered(self.c, [r for r in self.m.inbox(self.c, "proj", "S1") if r["id"] == "m1"], "S1", "prompt")
        self.c.execute("COMMIT")
        out = io.StringIO()
        with mock.patch.object(self.m, "db", lambda *a, **k: self.c), contextlib.redirect_stdout(out):
            self.m.cmd_status(True)
        letters = json.loads(out.getvalue())["letters"]
        self.assertEqual([(x["id"], x["path"], x["done"]) for x in letters], [("m1", ["woken", "hook"], False)])


class WakeAddresses(unittest.TestCase):
    """Where a session's wake address is kept: the secrets directory's sessions/, mode 600; removed at the session's
    end or when it no longer runs; never backed up, never reported as an undeclared credential, in no sandbox."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.secrets = self.tmp / "secrets"
        p = mock.patch.object(hubconf, "secrets_dir", lambda: self.secrets)
        p.start()
        self.addCleanup(p.stop)
        self.dir = hubconf.session_addresses_dir()

    def project_at(self, m, answer):
        p = mock.patch.object(m.project_git, "project_at", lambda ws: answer)
        p.start()
        self.addCleanup(p.stop)

    def hub_event(self):
        import runtime_fake
        m = load_file("hub_event_wake", ROOT / "bin" / "hub-event")
        m.LOG = self.tmp / "hub-event.log"
        m._workspace = lambda cwd: self.tmp
        self.project_at(m, ("proj", {"name": "proj"}))
        m.HANDLERS = {"session_start": [("wake_address", m.wake_address)],
                      "session_end": [("wake_address_drop", m.wake_address_drop)]}
        runtime_fake.wake_address = lambda env: {"socket": env["FAKE_SOCKET"]} if env.get("FAKE_SOCKET") else None
        self.addCleanup(delattr, runtime_fake, "wake_address")
        return m, runtime_fake

    def raw(self, sid="S1"):
        return json.dumps({"sid": sid, "dir": str(self.tmp)})

    def test_kept_at_session_start_mode_600_and_removed_at_its_end(self):
        m, rt = self.hub_event()
        with mock.patch.dict("os.environ", {"FAKE_SOCKET": "/tmp/s.sock"}):
            m.handle("session_start", self.raw(), rt)
        f = self.dir / "S1.json"
        self.assertEqual(json.loads(f.read_text()), {"socket": "/tmp/s.sock"})
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.dir.stat().st_mode & 0o777, 0o700)
        m.handle("session_end", self.raw(), rt)
        self.assertFalse(f.exists())

    def test_not_kept_outside_a_project_or_for_the_platforms_own_runs(self):
        m, rt = self.hub_event()
        with mock.patch.dict("os.environ", {"FAKE_SOCKET": "/tmp/s.sock", "HUB_LLM_POINT": "librarian"}):
            m.handle("session_start", self.raw(), rt)
        self.project_at(m, None)
        with mock.patch.dict("os.environ", {"FAKE_SOCKET": "/tmp/s.sock"}):
            m.handle("session_start", self.raw(), rt)
        self.assertFalse(self.dir.exists() and any(self.dir.iterdir()))

    def test_a_session_id_that_is_no_file_name_is_never_a_path(self):
        self.assertIsNone(hubconf.session_address_file("../x"))
        self.assertIsNone(hubconf.session_address_file(""))
        self.assertEqual(hubconf.session_address_file("codex:abc").name, "codex:abc.json")

    def test_a_runtime_without_an_address_keeps_nothing(self):
        import runtime_fake
        m = load_file("hub_event_wake2", ROOT / "bin" / "hub-event")
        m.LOG = self.tmp / "hub-event.log"
        m._workspace = lambda cwd: self.tmp
        self.project_at(m, ("proj", {"name": "proj"}))
        m.HANDLERS = {"session_start": [("wake_address", m.wake_address)]}
        m.handle("session_start", self.raw(), runtime_fake)
        self.assertFalse(self.dir.exists())

    def test_addresses_of_sessions_no_longer_running_are_pruned(self):
        import os
        import time
        hb = tmd.load_hubbus()
        self.dir.mkdir(parents=True)
        for sid in ("live", "gone", "young"):
            (self.dir / f"{sid}.json").write_text("{}")
        old = time.time() - 3600
        for sid in ("live", "gone"):
            os.utime(self.dir / f"{sid}.json", (old, old))
        self.assertEqual(hb.prune_wake_addresses(live={"live"}), ["gone"])
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["live.json", "young.json"])

    def test_never_backed_up(self):
        backup = load_file("backup_wake2", ROOT / "bin" / "backup.py")
        self.dir.mkdir(parents=True)
        (self.dir / "S1.json").write_text("{}")
        with mock.patch.object(backup, "CFG", {"include": [str(self.secrets)]}):
            self.assertFalse(backup.covers(self.dir / "S1.json"))
            self.assertFalse(backup.covers(self.dir))

    def test_not_an_undeclared_credential(self):
        cs = load_file("check_secrets_wake2", ROOT / "bin" / "check-secrets.py")
        self.dir.mkdir(parents=True)
        (self.dir / "token-S1.json").write_text("{}")
        hub = hubconf.hub_project()
        self.assertEqual(cs.undeclared(hub, {"name": hub, "paths": {"workspace": str(self.tmp)}}, set()), [])

    def test_in_every_sandboxs_deny_read(self):
        import policy
        self.assertTrue(any(self.dir.is_relative_to(d) for d in policy.secret_dirs()))


class ClaudeSessionsDir(unittest.TestCase):
    """A temporary secrets directory for Claude's waiter and wake requests (never the real one)."""
    SID = "11111111-2222-4333-8444-555555555555"

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        p = mock.patch.object(hubconf, "secrets_dir", lambda: self.tmp / "secrets")
        p.start()
        self.addCleanup(p.stop)
        import runtime_claude
        self.rc = runtime_claude
        self.dir = hubconf.session_addresses_dir()

    def raw(self, sid=None) -> str:
        return json.dumps({"session_id": sid or self.SID, "cwd": str(self.tmp), "hook_event_name": "Stop"})

    def files(self) -> list[str]:
        return sorted(p.name for p in self.dir.iterdir()) if self.dir.is_dir() else []

    # Desktop's CLI as the 11-02 spike saw it: stream-json in and out, no -p
    DESKTOP_ARGV = ["/Users/u/.claude/remote/ccd-cli/2.1.309-40c3beb4ef2f", "--output-format", "stream-json",
                    "--verbose", "--input-format", "stream-json", "--permission-mode=auto",
                    "--session-id=11111111-2222-4333-8444-555555555555"]

    def wait(self, steps=(), env=None, start=1000.0, step_s=2.0, argv=DESKTOP_ARGV):
        """runtime_claude.wait through its real code, with a clock that moves step_s per poll; steps[i] runs at the
        i-th poll's sleep (then nothing more happens until the waiter ends by itself). The session's process is this
        one (CLAUDE_PID, alive) unless env says otherwise; its arguments are `argv` (None: unreadable)."""
        t = [start]
        steps = list(steps)

        def sleep(_s):
            if steps:
                steps.pop(0)()
            t[0] += step_s
        env = {"CLAUDE_PID": str(os.getpid()), **(env or {})}
        out = self.rc.wait(self.raw(), env, clock=lambda: t[0], sleep=sleep, poll=0, argv_of=lambda pid: argv)
        self.clock = t[0]
        return out

    def waiter_file(self):
        return hubconf.session_file(self.SID, "waiter")

    def request(self, text, age_s=0):
        """A wake request as wake() leaves it, written straight (wake() itself needs a live waiter)."""
        import os
        import time
        f = hubconf.session_file(self.SID, f"wake-{len(self.files())}x")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        if age_s:
            t = time.time() - age_s
            os.utime(f, (t, t))
        return f


class ClaudeWaiter(ClaudeSessionsDir):
    """The Stop hook's waiter (runtime_claude.wait; docs/design/mailbox-runtimes.md B1)."""

    def test_a_request_wakes_once_with_exit_2_and_the_text_on_stderr(self):
        code, text, how = self.wait([lambda: self.assertTrue(self.rc.wake(self.SID, "📬 brunna: 1 letter (m1)"))])
        self.assertEqual((code, text, how), (2, "📬 brunna: 1 letter (m1)", "woke"))
        self.assertEqual(self.files(), [], "the request taken, the waiter's record gone with it")

    def test_a_request_is_never_seen_twice_including_by_a_rearmed_waiter(self):
        self.request("one")
        self.request("two")
        self.assertEqual(self.wait()[:2], (2, "one\ntwo"), "every request waiting is shown in one wake")
        # the next Stop arms a new waiter: nothing left for it, so it waits out its time quietly
        self.assertEqual(self.wait(step_s=self.rc.WAIT_TIMEOUT_S / 3), (0, "", "deadline"))
        self.assertEqual(self.files(), [])

    def test_a_request_taken_by_another_waiter_is_not_taken_again(self):
        self.request("one")
        self.assertEqual(self.rc._take(self.SID, 0), ["one"])
        self.assertEqual(self.rc._take(self.SID, 0), [])

    def test_a_stale_request_is_dropped_unshown(self):
        self.request("old", age_s=self.rc.REQUEST_TTL_S + 60)
        self.assertEqual(self.wait(step_s=self.rc.WAIT_TIMEOUT_S / 3), (0, "", "deadline"))
        self.assertEqual(self.files(), [])

    def test_the_newest_waiter_wins(self):
        def newer_arms():
            self.waiter_file().write_text("999-newer")
        self.assertEqual(self.wait([newer_arms]), (0, "", "superseded"))
        self.assertEqual(self.waiter_file().read_text(), "999-newer", "the older one leaves the newer's record alone")
        self.assertTrue(self.rc.wake(self.SID, "x"), "the newer one is the live one")

    def test_it_ends_when_its_session_ends(self):
        hub_event = load_file("hub_event_wait", ROOT / "bin" / "hub-event")
        import runtime_ports

        def session_end():
            self.request("left for it")
            hub_event.wake_address_drop(runtime_ports.HubEvent("session_end", self.SID, str(self.tmp)), None)
        self.assertEqual(self.wait([session_end]), (0, "", "ended"))
        self.assertEqual(self.files(), [], "its end took the waiter's record and the request")

    def test_it_ends_when_its_address_file_is_gone(self):
        self.assertEqual(self.wait([lambda: self.waiter_file().unlink()]), (0, "", "ended"))

    def test_it_ends_when_the_session_process_is_gone(self):
        import subprocess
        p = subprocess.Popen(["true"])
        p.wait()
        self.assertEqual(self.wait(env={"CLAUDE_PID": str(p.pid)}), (0, "", "session-gone"))
        self.assertEqual(self.files(), [])

    def test_it_ends_before_its_hook_timeout(self):
        self.assertEqual(self.wait(start=0, step_s=self.rc.WAIT_MARGIN_S / 2), (0, "", "deadline"))
        self.assertLess(self.clock, self.rc.WAIT_TIMEOUT_S)
        self.assertGreaterEqual(self.clock, self.rc.WAIT_TIMEOUT_S - self.rc.WAIT_MARGIN_S)

    def test_headless_runs_do_not_arm(self):
        for ep in ("sdk-cli", "sdk-ts", "sdk-py"):
            self.assertEqual(self.wait(env={"CLAUDE_CODE_ENTRYPOINT": ep}), (0, "", "headless"))
        self.assertEqual(self.files(), [])
        hub_event = load_file("hub_event_wait2", ROOT / "bin" / "hub-event")
        hub_event.LOG = self.tmp / "hub-event.log"
        self.request("for a session")
        for var in ("HUB_LLM_POINT", "HUBBUS_LEASE"):
            with mock.patch.dict("os.environ", {var: "1"}):
                self.assertEqual(hub_event.wait("claude", self.raw()), 0)
        with mock.patch.dict("os.environ", {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}):
            for var in ("HUB_LLM_POINT", "HUBBUS_LEASE"):
                os.environ.pop(var, None)
            self.assertEqual(hub_event.wait("claude", self.raw()), 0)
        self.assertNotIn(f"{self.SID}.waiter", self.files(), "a platform run arms nothing")
        self.assertEqual(len(self.files()), 1, "and takes nothing")
        self.assertIn("wait - ran=headless", hub_event.LOG.read_text(), "a -p run's waiter is logged as such")

    def test_desktop_and_the_terminal_arm(self):
        for ep in ("claude-desktop", "cli", None):
            self.request("x")
            env = {"CLAUDE_CODE_ENTRYPOINT": ep} if ep else {}
            self.assertEqual(self.wait(env=env)[0], 2, ep)
        self.request("x")
        self.assertEqual(self.wait(argv=["claude", "--resume", self.SID])[0], 2, "a terminal session")

    def test_a_print_run_inside_a_session_does_not_arm(self):
        """`claude -p` started from inside a session (an agent's Bash) inherits that session's entrypoint, and waits
        for a background Stop hook to exit (seen 11-02 on 2.1.316): its own arguments decide, not the entrypoint."""
        self.request("x")
        for argv in (["claude", "-p", "say hi"], ["claude", "--print", "say hi"], ["claude", "--print=text"],
                     ["/opt/node", "/x/cli.js", "--model", "m", "-p", "hi"]):
            for ep in ("claude-desktop", "cli"):
                self.assertEqual(self.wait(env={"CLAUDE_CODE_ENTRYPOINT": ep}, argv=argv), (0, "", "headless"), argv)
        self.assertEqual([n for n in self.files() if n.endswith(".waiter")], [], "nothing armed")
        self.assertEqual(len(self.files()), 1, "and the request is left alone")

    def test_an_unknown_session_process_does_not_arm(self):
        self.request("x")
        self.assertEqual(self.wait(argv=None), (0, "", "no-session-process"), "arguments unreadable")
        self.assertEqual(self.rc.wait(self.raw(), {}, argv_of=lambda pid: self.DESKTOP_ARGV),
                         (0, "", "no-session-process"), "no CLAUDE_PID")
        self.assertEqual(self.rc.wait(self.raw(), {"CLAUDE_PID": "x"}), (0, "", "no-session-process"))
        self.assertEqual(len(self.files()), 1)

    def test_a_process_arguments_are_read_from_the_system(self):
        import subprocess
        import time
        code = "import time; time.sleep(30)"
        plain = subprocess.Popen([sys.executable, "-c", code])
        printing = subprocess.Popen([sys.executable, "-c", code, "-p", "say hi"])   # -p among its arguments

        def argv(p):   # once it has exec'd (before that, ps shows the parent's arguments)
            for _i in range(100):
                got = self.rc._argv(p.pid) or []
                if "time.sleep(30)" in " ".join(got):
                    return got
                time.sleep(0.05)
            self.fail(f"no arguments read for {p.pid}")
        try:
            self.assertFalse(self.rc._printing(argv(plain)))
            self.assertTrue(self.rc._printing(argv(printing)))
        finally:
            for p in (plain, printing):
                p.kill()
                p.wait()
        self.assertIsNone(self.rc._argv(plain.pid), "a process that is gone")

    def test_hub_event_passes_the_wake_on(self):
        import contextlib
        import io
        hub_event = load_file("hub_event_wait3", ROOT / "bin" / "hub-event")
        hub_event.LOG = self.tmp / "hub-event.log"
        self.request("📬 brunna: 1 letter from beta (m1). Run brunna inbox.")
        err = io.StringIO()
        import subprocess
        session = subprocess.Popen(["sleep", "30"])   # the session's process, read for real: no -p
        self.addCleanup(session.wait)
        self.addCleanup(session.kill)
        import time
        for _i in range(100):   # until it has exec'd (before that, ps shows this process's arguments)
            if "sleep" in " ".join(self.rc._argv(session.pid) or []):
                break
            time.sleep(0.05)
        with contextlib.redirect_stderr(err), mock.patch.dict("os.environ", {"CLAUDE_PID": str(session.pid)}):
            for var in ("HUB_LLM_POINT", "HUBBUS_LEASE", "CLAUDE_CODE_ENTRYPOINT"):
                os.environ.pop(var, None)
            self.assertEqual(hub_event.wait("claude", self.raw()), 2)
        self.assertEqual(err.getvalue().strip(), "📬 brunna: 1 letter from beta (m1). Run brunna inbox.")
        self.assertIn("wait - ran=woke", hub_event.LOG.read_text())


class ClaudeWake(ClaudeSessionsDir):
    """runtime_claude.wake: a request only for a live waiter."""

    def test_true_only_with_a_live_waiter(self):
        import os
        import time
        self.assertFalse(self.rc.wake(self.SID, "x"), "no waiter: the session isn't wakeable (before its first turn)")
        self.assertEqual(self.files(), [])
        f = self.waiter_file()
        f.parent.mkdir(parents=True)
        f.write_text("1-a")
        self.assertTrue(self.rc.wake(self.SID, "📬"))
        [req] = [n for n in self.files() if ".wake-" in n]
        self.assertEqual((self.dir / req).read_text(), "📬")
        self.assertEqual((self.dir / req).stat().st_mode & 0o777, 0o600)
        old = time.time() - self.rc.WAITER_FRESH_S - 5
        os.utime(f, (old, old))
        self.assertFalse(self.rc.wake(self.SID, "x"), "a waiter whose heartbeat stopped is gone")
        self.assertFalse(self.rc.wake("../x", "x"))

    def test_the_port_through_the_sessions_router(self):
        import runtime_ports
        self.assertIn("wake", runtime_ports.capabilities("claude"))
        self.assertFalse(runtime_ports.Sessions(["claude"]).wake(self.SID, "x"))
        self.assertFalse(hasattr(self.rc, "wake_address"), "no socket token is kept: the waiter needs none")
        self.assertIsNone(self.rc.unsupported("wake"))
        self.assertTrue(self.rc.note("wake"))

    def test_the_stop_hook_runs_in_the_background_with_its_timeout_and_nothing_waits_on_session_start(self):
        hooks = self.rc.install_hooks({})["hooks"]
        self.assertEqual(hooks["Stop"], [{"hooks": [self.rc.wait_hook()]}])
        h = self.rc.wait_hook()
        self.assertEqual((h["asyncRewake"], h["timeout"]), (True, 14 * 86400))
        self.assertNotIn("|| true", h["command"], "a wake's exit code 2 must not be swallowed")
        for name, entries in hooks.items():
            for e in entries:
                for x in e["hooks"]:
                    if name != "Stop":
                        self.assertFalse(x.get("asyncRewake") or x["command"].endswith(" wait; exit 0"), name)

    def test_the_hook_command_passes_the_exit_code_on_and_is_quiet_without_the_hub(self):
        import subprocess
        hub = self.tmp / "hub"
        (hub / "bin").mkdir(parents=True)
        with mock.patch.object(hubconf, "MAIN", hub):
            cmd = self.rc.wait_command()
            r = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, "no hub-event: nothing, quietly")
            (hub / "bin" / "hub-event").write_text('#!/bin/sh\necho "woke $*" >&2\nexit 2\n')
            (hub / "bin" / "py").write_text('#!/bin/sh\nexec /bin/sh "$@"\n')   # Linux: py in front
            for f in ("hub-event", "py"):
                (hub / "bin" / f).chmod(0o755)
            r = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, timeout=30)
        self.assertEqual((r.returncode, r.stderr.strip()), (2, "woke --runtime claude wait"))


class ClaudeDispatch(Harness):
    """Dispatch's wake through the real Claude adapter: a request for the session's live waiter, or not reached."""
    SID = ClaudeSessionsDir.SID

    def setUp(self):
        super().setUp()
        p = mock.patch.object(hubconf, "secrets_dir", lambda: self.tmp / "secrets")
        p.start()
        self.addCleanup(p.stop)

    def test_dispatch_wakes_through_the_real_adapter(self):
        import runtime_ports
        rt = runtime_ports.Sessions(["claude"])
        self.send("m1", "proj", "request", src="beta")
        self.m.wake(self.c, rt, self.m.get_message(self.c, "m1"), self.SID)   # no waiter yet: not reached
        f = hubconf.session_file(self.SID, "waiter")
        f.parent.mkdir(parents=True)
        f.write_text("1-a")
        self.send("m2", "proj", "request", src="beta")
        self.m.wake(self.c, rt, self.m.get_message(self.c, "m2"), self.SID)
        oks = [(r["id"], json.loads(r["payload"])["ok"]) for r in
               self.c.execute("SELECT id, payload FROM events WHERE type='message.woken' ORDER BY seq")]
        self.assertEqual(oks, [("m1:woken", False), ("m2:woken", True)])
        [req] = [p for p in f.parent.iterdir() if ".wake-" in p.name]
        self.assertEqual(req.read_text(), self.m._("📬 brunna: 1 letter from {project} ({id}). Run brunna inbox.",
                                                   project="beta", id="m2"))


class ClaudeSessionFiles(ClaudeSessionsDir):
    """What the sessions folder keeps, and for how long."""

    def test_waiters_requests_and_dot_files_are_pruned_by_age_not_by_live_sessions(self):
        import os
        import time
        hb = tmd.load_hubbus()
        self.dir.mkdir(parents=True)
        names = ["fresh.waiter", "stale.waiter", "stale.wake-a1", "fresh.wake-b2", ".stale.wake-c3.taken",
                 "live.json", "gone.json"]
        for n in names:
            (self.dir / n).write_text("x")
        old = time.time() - 3600
        for n in ("stale.waiter", "stale.wake-a1", ".stale.wake-c3.taken", "live.json", "gone.json"):
            os.utime(self.dir / n, (old, old))
        self.assertEqual(sorted(hb.prune_wake_addresses(live={"live"})), ["gone", "stale"])
        self.assertEqual(self.files(), ["fresh.waiter", "fresh.wake-b2", "live.json"])

    def test_a_sessions_files_and_only_its_own(self):
        self.dir.mkdir(parents=True)
        for n in ("s1.json", "s1.waiter", "s1.wake-ab", "s10.waiter", ".s1.wake-cd.taken", "codex:s1.json"):
            (self.dir / n).write_text("x")
        self.assertEqual(sorted(p.name for p in hubconf.session_files("s1")), ["s1.json", "s1.waiter", "s1.wake-ab"])
        self.assertEqual(hubconf.session_of_file("codex:s1.json"), "codex:s1")
        self.assertIsNone(hubconf.session_of_file(".s1.wake-cd.taken"))
        self.assertIsNone(hubconf.session_file("s1", "../x"))


class WokenNotPickedUp(Harness):
    """A wake that reached a session's waiter, after which that session didn't take the letter in time: shown per
    letter (woken-missed), listed for doctor (hubbus wake-misses). Computed from the bus events alone."""

    def woke(self, mid, minutes_ago, ok=True):
        self.c.execute("BEGIN IMMEDIATE")
        self.m.publish(self.c, f"{mid}:woken", "message.woken", "proj", "proj", mid,
                       {"session": "S1", "via": "wake", "ok": ok})
        self.c.execute("COMMIT")
        self.age(f"{mid}:woken", minutes_ago)

    def age(self, eid, minutes_ago):
        self.c.execute("UPDATE events SET ts=? WHERE id=?", (self.ts(minutes_ago), eid))

    def delivered(self, mid, minutes_ago, via="post-tool"):
        self.c.execute("BEGIN IMMEDIATE")
        self.m.mark_delivered(self.c, [self.m.get_message(self.c, mid)], "S1", via)
        self.c.execute("COMMIT")
        self.c.execute("UPDATE events SET ts=? WHERE type='message.delivered' AND ref=?", (self.ts(minutes_ago), mid))

    def ts(self, minutes_ago):
        import datetime
        return (datetime.datetime.now().astimezone() - datetime.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")

    def paths(self):
        return {x["id"]: x["path"] for x in self.m.delivery_paths(self.c)}

    def test_taken_in_time_is_woken(self):
        self.send("m1", "proj", "request", minutes_ago=20)
        self.woke("m1", 20)
        self.delivered("m1", 19, via="inbox")
        self.assertEqual(self.paths()["m1"], ["woken", "inbox"])
        self.assertEqual(self.m.wake_misses(self.c), [])

    def test_not_taken_in_time_is_woken_missed(self):
        self.send("m1", "proj", "request", minutes_ago=30)
        self.woke("m1", 30)
        self.c.execute("BEGIN IMMEDIATE")
        self.m.mark_delivered(self.c, [self.m.get_message(self.c, "m1")], "S9", "resume")   # a background copy later
        self.c.execute("COMMIT")
        self.send("m2", "proj", "request", minutes_ago=40)
        self.woke("m2", 40)
        self.delivered("m2", 20, via="prompt")   # the owner came back 20 minutes after the wake: too late
        self.assertEqual(self.paths(), {"m1": ["woken-missed", "resume"], "m2": ["woken-missed", "hook"]})
        self.assertEqual([x["id"] for x in self.m.wake_misses(self.c)], ["m1", "m2"])

    def test_still_within_the_time_is_just_woken(self):
        self.send("m1", "proj", "request", minutes_ago=1)
        self.woke("m1", 1)
        self.assertEqual(self.paths()["m1"], ["woken"])
        self.assertEqual(self.m.wake_misses(self.c), [])

    def test_a_wake_that_failed_is_no_miss(self):
        self.send("m1", "proj", "request", minutes_ago=30)
        self.woke("m1", 30, ok=False)
        self.assertEqual(self.paths()["m1"], ["wake-failed"])
        self.assertEqual(self.m.wake_misses(self.c), [])

    def test_the_command_reads_the_bus_and_without_one_says_none(self):
        import contextlib
        import io
        self.send("m1", "proj", "request", minutes_ago=30)
        self.woke("m1", 30)
        out = io.StringIO()
        with mock.patch.object(self.m, "db", lambda *a, **k: self.c), contextlib.redirect_stdout(out):
            self.m.cmd_wake_misses(True)
        [x] = json.loads(out.getvalue())
        self.assertEqual((x["id"], x["to"], x["days"]), ("m1", "proj", self.m.WAKE_MISS_DAYS))
        out = io.StringIO()
        with mock.patch.object(self.m, "DB", self.tmp / "none.db"), contextlib.redirect_stdout(out):
            self.m.cmd_wake_misses(True)
        self.assertEqual(json.loads(out.getvalue()), [])


if __name__ == "__main__":
    unittest.main()
