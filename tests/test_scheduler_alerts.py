"""排程 job 持續失敗要發警報(10-27:contracts-check 連兩晚失敗,只有狀態頁紅,沒人知道)。

失敗 → 記進 scheduler-failing.json(起始時間保留);成功 → 拿掉;失敗超過 ALERT_AFTER 才進警報清單(下個 tick 重試就好的不吵);
警報字句同一個 job 要穩定(alert.once 照字句去重)。

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
        self.m.mark_job("agent-hub/contracts-check", True, "exit 1")   # 再失敗一次:起始時間不變
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
        self.m.alert_failing()   # 不能讓排程掛掉


if __name__ == "__main__":
    unittest.main()
