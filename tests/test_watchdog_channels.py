"""The scheduler watchdog on any alert channel hub.json sets up, not only Discord, and its grace after a Linux resume
(docs/design/newcomer-path.md B7). It still imports nothing of the platform (tests/test_python_floor.py).

    bin/py -m unittest tests.test_watchdog_channels
"""
import datetime
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]


class Watchdog(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for d in ("hub/.state", "hub/.logs", "config", "secrets"):
            (self.tmp / d).mkdir(parents=True)

    def load(self, alerts: dict):
        (self.tmp / "config" / "hub.json").write_text(json.dumps({"language": {"ui": "en"}, "alerts": alerts}))
        env = {"AGENT_HUB_ROOT": str(self.tmp / "hub"), "AGENT_HUB_CONFIG": str(self.tmp / "config"),
               "AGENT_HUB_SECRETS": str(self.tmp / "secrets"), "AGENT_HUB_LANG_UI": "en"}
        with mock.patch.dict(os.environ, env):
            spec = importlib.util.spec_from_file_location("watchdog_channels", ROOT / "bin" / "scheduler-watchdog.py")
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
        return m

    def test_ntfy_alone_is_a_channel(self):
        m = self.load({"ntfy": {"topic": "my-topic"}})
        self.assertEqual([n for n, _f in m.channels(m.hub_alerts())], ["ntfy"])
        sent = []

        def run(cmd, **kw):
            sent.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "200", "")
        with mock.patch.object(m.subprocess, "run", side_effect=run):
            self.assertTrue(m.send("the scheduler stopped"))
        self.assertTrue(sent[0][-1].endswith("ntfy.sh/my-topic"))

    def test_discord_from_the_default_files(self):
        (self.tmp / "secrets" / "discord-token").write_text("t")
        (self.tmp / "secrets" / "discord-alert-channel").write_text("c")
        m = self.load({"discord_token_file": ".secrets/discord-token",
                       "discord_channel_file": ".secrets/discord-alert-channel", "email": {"host": "h", "to": "x@y"}})
        self.assertEqual([n for n, _f in m.channels(m.hub_alerts())], ["discord", "email"])

    def test_no_channel_is_reported(self):
        m = self.load({"ntfy": {}})
        with mock.patch("builtins.print") as p:
            self.assertEqual(m.main(), 1)
        self.assertIn("no channel", " ".join(str(a) for c in p.call_args_list for a in c.args))

    def test_one_failing_channel_doesnt_stop_another(self):
        m = self.load({"ntfy": {"topic": "t"}, "macos": True})
        with mock.patch.object(m, "send_ntfy", return_value=False), mock.patch.object(m, "send_macos", return_value=True), \
                mock.patch.object(m.sys, "platform", "darwin"):
            self.assertTrue(m.send("x"))

    def test_resume_grace(self):
        m = self.load({"ntfy": {"topic": "t"}})
        now = datetime.datetime(2026, 11, 1, 9, 0)
        clocks = {7: 1000.0, m.time.CLOCK_MONOTONIC: 500.0}
        with mock.patch.object(m.time, "CLOCK_BOOTTIME", 7, create=True), \
                mock.patch.object(m.time, "clock_gettime", side_effect=lambda c: clocks[c]):
            st = {}
            m.resumed(st, now)
            self.assertNotIn("resumed_at", st, "the first check only records the gap")
            clocks[7] += 3600   # an hour asleep: boottime moved, monotonic didn't
            m.resumed(st, now)
            self.assertEqual(st["resumed_at"], now.isoformat(timespec="seconds"))
        tick_before = now - datetime.timedelta(hours=2)
        self.assertTrue(m.in_resume_grace(st, tick_before, now + datetime.timedelta(minutes=10)))
        self.assertFalse(m.in_resume_grace(dict(st), now + datetime.timedelta(minutes=1),
                                           now + datetime.timedelta(minutes=2)), "the scheduler ticked: over")
        self.assertFalse(m.in_resume_grace(dict(st), tick_before, now + datetime.timedelta(minutes=25)),
                         "the grace runs out")

    def test_no_boottime_clock_means_no_grace(self):
        m = self.load({"ntfy": {"topic": "t"}})
        st = {}
        with mock.patch.object(m, "time", mock.Mock(spec=["clock_gettime", "CLOCK_MONOTONIC"])):
            m.resumed(st, datetime.datetime(2026, 11, 1))
        self.assertEqual(st, {})


if __name__ == "__main__":
    unittest.main()
