"""Every scheduled run has a time limit (round 6, 11-01): jobs run one at a time, and a run that hangs (e.g. git in a
repo whose config names a FIFO) held up every tick after it. Past the limit the run's whole process group is killed,
what it started included, and the run counts as failed.

    bin/py -m unittest tests.test_scheduler_timeout
"""
import importlib.util
import pathlib
import shutil
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))


def load():
    spec = importlib.util.spec_from_file_location("scheduler_to", ROOT / "bin" / "scheduler.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TimeLimit(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_hung_run_and_what_it_started_are_stopped(self):
        late = self.tmp / "late"
        cmd = f"(sleep 4; touch '{late}') & sleep 60"
        t = time.monotonic()
        with open(self.tmp / "log", "a") as log:
            rc = self.m.run_bounded(cmd, log, 1 / 60)
        self.assertLess(time.monotonic() - t, 20)
        self.assertEqual(rc, self.m.TIMED_OUT)
        time.sleep(5)
        self.assertFalse(late.exists(), "the job's child outlived its time limit")
        self.assertIn("time limit", (self.tmp / "log").read_text())

    def test_a_quick_run_keeps_its_exit_code(self):
        with open(self.tmp / "log", "a") as log:
            self.assertEqual(self.m.run_bounded("exit 3", log, 1), 3)

    def test_limits(self):
        self.assertEqual(self.m.timeout_of({}), self.m.FOREGROUND_TIMEOUT_MIN)
        self.assertEqual(self.m.timeout_of({"background": True}), self.m.BACKGROUND_TIMEOUT_MIN)
        self.assertEqual(self.m.timeout_of({"timeout_minutes": 90}), 90)



class RegistryTimeout(unittest.TestCase):
    """A registry job sets its own limit as `timeout: 90m` (validated like every:), handed to the scheduler in minutes."""

    def test_timeout_reaches_the_scheduler(self):
        try:
            import registry
        except ImportError:
            self.skipTest("needs PyYAML")
        doc = {"v": 1, "name": "p", "paths": {"workspace": "/tmp"},
               "jobs": [{"name": "slow", "at": "03:00", "run": "true", "cwd": "workspace", "timeout": "90m"}]}
        self.assertEqual([e for e in registry.validate(doc, "p") if "timeout" in e], [])
        bad = {**doc, "jobs": [{**doc["jobs"][0], "timeout": "soon"}]}
        self.assertTrue([e for e in registry.validate(bad, "p") if "timeout" in e])
        jobs, _skipped, _renames = registry.scheduler_jobs({"projects": {"p": doc}}, set())
        self.assertEqual(jobs[0]["timeout_minutes"], 90)

if __name__ == "__main__":
    unittest.main()
