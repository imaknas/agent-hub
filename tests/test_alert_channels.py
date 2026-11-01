"""bin/alert.py: what is recorded as "an alert could not be sent" (the status page turns that red), with and without
a channel set up in hub.json alerts.

    bin/py -m unittest tests.test_alert_channels
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


def load():
    spec = importlib.util.spec_from_file_location("alert_channels", ROOT / "bin" / "alert.py")   # a private copy
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Channels(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        for name in ("SENT", "FAILED"):
            p = mock.patch.object(self.m, name, self.tmp / f"{name}.json")
            p.start()
            self.addCleanup(p.stop)

    def failed(self) -> dict:
        f = self.tmp / "FAILED.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def test_a_channel_that_fails_is_recorded(self):
        with mock.patch.dict(self.m.CHANNELS, {"ntfy": (lambda text: False, lambda: True)}, clear=True):
            self.m.once("k", ["broken"], "fine again")
        self.assertIn("k", self.failed())

    def test_no_channel_set_up(self):
        """nothing was there to send on: not a failed send (the status page shows a setup reminder instead)"""
        (self.tmp / "FAILED.json").write_text(json.dumps({"old": {"at": "2026-10-01T00:00:00", "text": "x"}}))
        with mock.patch.dict(self.m.CHANNELS, {"ntfy": (lambda text: False, lambda: False)}, clear=True):
            self.m.once("k", ["broken"], "fine again")
            self.assertNotIn("k", self.failed())
            self.assertEqual(self.m.failures(), {}, "records from channels that are gone do not count")


if __name__ == "__main__":
    unittest.main()
