"""Waking a live session for a new letter (docs/design/mailbox-runtimes.md B1): the `wake` port, dispatch's
once-per-letter use of it, the delivery path the status page shows, and where a session's wake address is kept.

The characterization part (CharacterizeDispatch, CharacterizeSecrets) pins what dispatch, the backup and check-secrets
do today for a runtime without `wake`; it was committed before the port existed and must keep passing unchanged.

A temporary bus; the runtime is a fake (tests.test_mailbox_dispatch's), no agent is started, no socket is touched.

    bin/py -m unittest tests.test_mailbox_wake
"""
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
    spec = importlib.util.spec_from_file_location(name, path)
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


if __name__ == "__main__":
    unittest.main()
