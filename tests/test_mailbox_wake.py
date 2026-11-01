"""Waking a live session for a new letter (docs/design/mailbox-runtimes.md B1): the `wake` port, dispatch's
once-per-letter use of it, the delivery path the status page shows, and where a session's wake address is kept.

The characterization part (CharacterizeDispatch, CharacterizeSecrets) pins what dispatch, the backup and check-secrets
do today for a runtime without `wake`; it was committed before the port existed and must keep passing unchanged.

A temporary bus; the runtime is a fake (tests.test_mailbox_dispatch's), no agent is started, no socket is touched.

    bin/py -m unittest tests.test_mailbox_wake
"""
import importlib.machinery
import importlib.util
import json
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


class CharacterizeClaudeWake(Harness):
    """Claude's wake before the asyncRewake waiter (docs/design/mailbox-runtimes.md B1), through the real adapter:
    Unsupported, so dispatch records nothing and routes as usual; doctor's row is off; no Stop hook is installed."""

    def test_the_claude_sessions_port_cannot_wake(self):
        import runtime_ports
        with self.assertRaises(runtime_ports.Unsupported):
            runtime_ports.Sessions(["claude"]).wake("S1", "x")

    def test_dispatch_through_the_claude_adapter_records_no_wake(self):
        import runtime_ports
        self.send("m1", "proj", "request")
        self.m.wake(self.c, runtime_ports.Sessions(["claude"]), self.m.get_message(self.c, "m1"), "S1")
        self.assertEqual(self.types(), {"message"})

    def test_doctors_wake_row_is_off(self):
        import runtime_ports
        [row] = [r for r in runtime_ports.feature_table("claude") if r[0] in runtime_ports.optional_features()]
        self.assertFalse(row[1])

    def test_no_stop_hook_is_installed(self):
        import runtime_claude
        self.assertNotIn("Stop", runtime_claude.install_hooks({})["hooks"])


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
        self.assertIn("agenthub inbox", self.rt.woken[0][1])
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


class ClaudeAddress(unittest.TestCase):
    def test_the_inbox_socket_and_token_from_the_hook_environment(self):
        import runtime_claude
        self.assertEqual(runtime_claude.wake_address({"CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/cc-socks-1/x",
                                                      "CLAUDE_CODE_MESSAGING_TOKEN": "t"}),
                         {"socket": "/tmp/cc-socks-1/x", "token": "t"})
        self.assertEqual(runtime_claude.wake_address({"CLAUDE_CODE_MESSAGING_SOCKET": "/s"}), {"socket": "/s"})
        self.assertIsNone(runtime_claude.wake_address({"CLAUDE_CODE_MESSAGING_TOKEN": "t"}))

    def test_no_wake_until_the_message_format_is_documented(self):
        import runtime_claude
        self.assertFalse(hasattr(runtime_claude, "wake"))
        self.assertTrue(runtime_claude.unsupported("wake"))


if __name__ == "__main__":
    unittest.main()
