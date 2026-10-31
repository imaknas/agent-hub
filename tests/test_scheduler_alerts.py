"""A scheduled job that keeps failing raises an alert (10-27: contracts-check failed two nights running, only the
status page was red, nobody knew).

Failure → recorded in scheduler-failing.json (the start time kept); success → removed; only a failure lasting longer
than ALERT_AFTER goes on the alert list (one that the next tick's retry fixes makes no noise); the alert wording for
the same job must stay stable (alert.once deduplicates by wording).

    bin/py -m unittest discover -s tests
"""
import datetime
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))


def load():
    spec = importlib.util.spec_from_file_location("scheduler_al", ROOT / "bin" / "scheduler.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class FailingJobs(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.m.FAILING_FILE = pathlib.Path(tempfile.mkdtemp()) / "failing.json"

    def failing(self):
        return json.loads(self.m.FAILING_FILE.read_text()) if self.m.FAILING_FILE.exists() else {}

    def test_failure_is_recorded_and_success_clears_it(self):
        self.m.mark_job("agent-hub/contracts-check", True, "exit 1")
        first = self.failing()["agent-hub/contracts-check"]["since"]
        self.m.mark_job("agent-hub/contracts-check", True, "exit 1")   # fails once more: start time unchanged
        self.assertEqual(self.failing()["agent-hub/contracts-check"]["since"], first)
        self.m.mark_job("agent-hub/contracts-check", False)
        self.assertEqual(self.failing(), {})

    def test_only_long_failures_are_alerted_and_wording_is_stable(self):
        now = datetime.datetime(2026, 10, 27, 9, 0)
        failing = {"a/old": {"since": "2026-10-27T01:30:00", "detail": "exit 1"},
                   "b/new": {"since": "2026-10-27T08:50:00", "detail": "exit 2"}}
        probs = self.m.failing_problems(failing, now)
        self.assertEqual(len(probs), 1)
        self.assertIn("a/old", probs[0])
        self.assertEqual(probs, self.m.failing_problems(failing, now + datetime.timedelta(hours=3))[:1])

    def test_alert_gets_the_list_and_survives_a_broken_alert(self):
        sent = []
        import alert
        orig = alert.once
        self.addCleanup(setattr, alert, "once", orig)
        alert.once = lambda key, problems, ok: sent.append((key, problems))
        self.m.FAILING_FILE.write_text(json.dumps({"x/y": {"since": "2020-01-01T00:00:00", "detail": "exit 1"}}))
        self.m.alert_failing()
        self.assertEqual(sent[0][0], "scheduler-jobs")
        self.assertEqual(len(sent[0][1]), 1)
        alert.once = lambda *a: (_ for _ in ()).throw(RuntimeError("discord down"))
        self.m.log = lambda s: None
        self.m.alert_failing()   # must not bring the scheduler down


if __name__ == "__main__":
    unittest.main()
