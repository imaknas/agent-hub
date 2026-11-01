"""Characterization of scheduler.py's tick (written before event-triggered jobs, docs/design/event-jobs.md, so the
existing behaviour is pinned while the tick changes): clock jobs, retries and max_attempts_per_day, interval jobs and
between, day filters, after:, background runs and reconcile, and the registry's before: → after: conversion.

Everything runs in-process: the state paths point at a temp directory, the clock is fixed, job commands and the bus
are recorded instead of run.

    bin/py -m unittest tests.test_scheduler_tick
"""
import datetime
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hubconf  # noqa: E402

CP = subprocess.CompletedProcess


def load():
    spec = importlib.util.spec_from_file_location("scheduler_tick", ROOT / "bin" / "scheduler.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Tick:
    """One scheduler module pointed at a temp directory; tick(jobs, now) runs main() once."""

    def __init__(self):
        self.m = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        st = self.tmp / "state"
        st.mkdir()
        m = self.m
        m.STATE_FILE, m.RUNS_DIR, m.HEARTBEAT = st / "last-run.json", st / "runs", st / "heartbeat"
        m.LOG_DIR, m.FAILING_FILE, m.SCHEDULE = self.tmp / "logs", st / "failing.json", self.tmp / "none.yaml"
        m.RUNS_DIR.mkdir()
        m.LOCK_FILE = st / "scheduler.lock"
        self.ran, self.bus, self.spawned = [], [], []

    def run(self, cmd, **kw):
        if any(x in str(c) for c in cmd[:2] for x in ("hubbus", "gen-status-page")):
            if "hubbus" in str(cmd[1]):
                self.bus.append({"argv": cmd[2:], "env": kw.get("env")})
            return CP(cmd, 0, "", "")
        command = cmd[-1]
        self.ran.append({"command": command, "env": kw.get("env")})
        if command.startswith("exit "):
            raise subprocess.CalledProcessError(int(command.split()[1]), cmd)
        return CP(cmd, 0, "", "")

    def bounded(self, command, job_log, minutes, env=None):
        """the scheduler's run_bounded (its own process group, a time limit), recorded like run"""
        self.ran.append({"command": command, "env": env, "minutes": minutes})
        return int(command.split()[1]) if command.startswith("exit ") else 0

    def tick(self, jobs, now, info=None):
        info = info or {"alias": {}, "renames": {}, "skipped": [], "errors": {}}
        fake_sp = types.SimpleNamespace(run=self.run, CalledProcessError=subprocess.CalledProcessError,
                                        STDOUT=subprocess.STDOUT)

        class FakeDT(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return now
        with mock.patch.object(self.m, "subprocess", fake_sp), mock.patch.object(self.m.datetime, "datetime", FakeDT), \
                mock.patch.object(hubconf, "login_shell", lambda: "/bin/sh"), \
                mock.patch.object(self.m, "alert_failing", lambda: None), \
                mock.patch.object(self.m, "spawn_background", lambda *a: self.spawned.append(a)), \
                mock.patch.object(self.m, "run_bounded", self.bounded), \
                mock.patch.object(self.m, "all_jobs", lambda log_errors=False: (jobs, info)), \
                mock.patch("builtins.print"):
            self.m.main()

    def state(self):
        return json.loads(self.m.STATE_FILE.read_text())

    def commands(self):
        return [r["command"] for r in self.ran]


DAY = datetime.datetime(2026, 10, 30, 12, 0)   # a Friday


class ClockJobs(unittest.TestCase):
    def setUp(self):
        self.t = Tick()

    def test_due_job_runs_once_a_day(self):
        jobs = [{"name": "a", "time": "06:00", "command": "echo a"},
                {"name": "later", "time": "13:00", "command": "echo later"}]
        self.t.tick(jobs, DAY)
        self.assertEqual(self.t.commands(), ["echo a"])
        self.assertEqual(self.t.state()["a"], "2026-10-30")
        self.assertEqual(self.t.state()["a.attempts"], {"date": "2026-10-30", "count": 1})
        self.t.tick(jobs, DAY + datetime.timedelta(minutes=15))
        self.assertEqual(self.t.commands(), ["echo a"])
        self.t.tick(jobs, DAY + datetime.timedelta(days=1, hours=1))
        self.assertEqual(self.t.commands(), ["echo a", "echo a", "echo later"])

    def test_failure_retries_until_the_daily_cap(self):
        jobs = [{"name": "f", "time": "06:00", "command": "exit 3", "max_attempts_per_day": 2}]
        for i in range(4):
            self.t.tick(jobs, DAY + datetime.timedelta(minutes=15 * i))
        self.assertEqual(self.t.commands(), ["exit 3", "exit 3"])
        self.assertNotIn("f", self.t.state())
        failed = [b["argv"] for b in self.t.bus if b["argv"][:2] == ["publish", "job.failed"]]
        self.assertEqual(len(failed), 2)
        payload = json.loads(failed[0][failed[0].index("--payload") + 1])
        self.assertEqual((payload["job"], payload["exit"]), ("f", 3))
        self.assertIn("f", json.loads(self.t.m.FAILING_FILE.read_text()))

    def test_failure_without_a_cap_retries_every_tick(self):
        jobs = [{"name": "f", "time": "06:00", "command": "exit 1"}]
        for i in range(3):
            self.t.tick(jobs, DAY + datetime.timedelta(minutes=15 * i))
        self.assertEqual(len(self.t.ran), 3)

    def test_disabled_and_day_filters(self):
        jobs = [{"name": "off", "time": "06:00", "enabled": False, "command": "echo off"},
                {"name": "mon", "time": "06:00", "days_of_week": ["mon"], "command": "echo mon"},
                {"name": "fri", "time": "06:00", "days_of_week": ["fri"], "command": "echo fri"},
                {"name": "first", "time": "06:00", "days_of_month": [1], "command": "echo first"},
                {"name": "nov", "time": "06:00", "months": [11], "command": "echo nov"},
                {"name": "ended", "time": "06:00", "end_date": "2026-10-29", "command": "echo ended"},
                {"name": "ends", "time": "06:00", "end_date": "2026-10-30", "command": "echo ends"}]
        self.t.tick(jobs, DAY)
        self.assertEqual(self.t.commands(), ["echo fri", "echo ends"])

    def test_catch_up_runs_a_missed_weekly_or_monthly_job_after_waking(self):
        # DAY is Friday 10-30, 12:00. Monday 10-26 and the 1st of October passed with no run (asleep or off)
        jobs = [{"name": "mon", "time": "07:00", "days_of_week": ["mon"], "catch_up": "period", "command": "echo mon"},
                {"name": "first", "time": "06:30", "days_of_month": [1], "catch_up": "period", "command": "echo first"},
                {"name": "plain-mon", "time": "07:00", "days_of_week": ["mon"], "command": "echo plain"}]
        self.t.tick(jobs, DAY.replace(hour=0, minute=15))   # the first tick after waking, before any job's own hour
        self.assertEqual(self.t.commands(), ["echo mon", "echo first"], "opted in: run now; not opted in: skipped")
        self.t.tick(jobs, DAY.replace(hour=13))
        self.assertEqual(self.t.commands(), ["echo mon", "echo first"], "caught up once, not again")

    def test_catch_up_does_nothing_when_the_job_ran(self):
        jobs = [{"name": "mon", "time": "07:00", "days_of_week": ["mon"], "catch_up": "period", "command": "echo mon"}]
        self.t.m.STATE_FILE.write_text(json.dumps({"mon": "2026-10-26"}))   # ran on Monday
        self.t.tick(jobs, DAY)
        self.assertEqual(self.t.commands(), [])

    def test_job_env_carries_safe_push(self):
        self.t.tick([{"name": "a", "time": "06:00", "command": "echo a"}], DAY)
        self.assertTrue(self.t.ran[0]["env"]["SAFE_PUSH"].endswith("git-safe-push.sh"))


class IntervalJobs(unittest.TestCase):
    def setUp(self):
        self.t = Tick()

    def test_runs_again_after_the_interval_with_a_minute_of_grace(self):
        jobs = [{"name": "i", "every_minutes": 30, "command": "echo i"}]
        self.t.tick(jobs, DAY)
        self.assertEqual(self.t.state()["i.last_ok"], "2026-10-30T12:00:00")
        self.t.tick(jobs, DAY + datetime.timedelta(minutes=15))
        self.assertEqual(len(self.t.ran), 1)
        self.t.tick(jobs, DAY + datetime.timedelta(minutes=29))
        self.assertEqual(len(self.t.ran), 2)

    def test_between_window(self):
        jobs = [{"name": "i", "every_minutes": 15, "between": "08:00-11:00", "command": "echo i"}]
        self.t.tick(jobs, DAY)
        self.assertEqual(self.t.ran, [])
        self.t.tick(jobs, DAY.replace(hour=9))
        self.assertEqual(len(self.t.ran), 1)

    def test_cap_counts_successes_too(self):
        jobs = [{"name": "i", "every_minutes": 15, "max_attempts_per_day": 2, "command": "echo i"}]
        for i in range(4):
            self.t.tick(jobs, DAY + datetime.timedelta(minutes=15 * i))
        self.assertEqual(len(self.t.ran), 2)


class After(unittest.TestCase):
    def setUp(self):
        self.t = Tick()

    def test_waits_for_a_job_not_yet_due_then_runs_after_it(self):
        jobs = [{"name": "dep", "time": "13:00", "command": "echo dep"},
                {"name": "w", "time": "06:00", "after": ["dep"], "command": "echo w"}]
        self.t.tick(jobs, DAY)
        self.assertEqual(self.t.ran, [])
        self.t.tick(jobs, DAY.replace(hour=13))
        self.assertEqual(self.t.commands(), ["echo dep", "echo w"])

    def test_stops_waiting_for_a_job_out_of_attempts_or_not_today(self):
        jobs = [{"name": "dep", "time": "06:00", "command": "exit 1", "max_attempts_per_day": 1},
                {"name": "w", "time": "06:00", "after": ["dep", "monday-only"], "command": "echo w"},
                {"name": "monday-only", "time": "06:00", "days_of_week": ["mon"], "command": "echo m"}]
        self.t.tick(jobs, DAY)
        self.assertEqual(self.t.commands(), ["exit 1", "echo w"])

    def test_interval_dependency_is_waited_for_only_while_running(self):
        jobs = [{"name": "i", "every_minutes": 60, "background": True, "command": "echo i"},
                {"name": "w", "time": "06:00", "after": ["i"], "command": "echo w"}]
        self.t.tick(jobs, DAY)   # i spawned in the background this tick → w waits
        self.assertEqual([a[0] for a in self.t.spawned], ["i"])
        self.assertEqual(self.t.ran, [])
        # i finished (its success merged): not due again, not running → w no longer waits
        st = self.t.state()
        st["i.last_ok"] = "2026-10-30T12:05:00"
        self.t.m.STATE_FILE.write_text(json.dumps(st))
        self.t.tick(jobs, DAY + datetime.timedelta(minutes=15))
        self.assertEqual(self.t.commands(), ["echo w"])

    def test_alias_lets_after_name_the_other_side(self):
        jobs = [{"name": "old-dep", "time": "13:00", "command": "echo dep"},
                {"name": "w", "time": "06:00", "after": ["p/dep"], "command": "echo w"}]
        info = {"alias": {"p/dep": "old-dep", "old-dep": "p/dep"}, "renames": {}, "skipped": [], "errors": {}}
        self.t.tick(jobs, DAY, info)
        self.assertEqual(self.t.ran, [])


class Background(unittest.TestCase):
    def setUp(self):
        self.t = Tick()

    def run_file(self, name, rec):
        (self.t.m.RUNS_DIR / f"{name}.json").write_text(json.dumps(rec))

    def test_spawned_with_attempt_recorded_first(self):
        self.t.tick([{"name": "p/b", "time": "06:00", "background": True, "command": "echo b"}], DAY)
        self.assertEqual(self.t.spawned[0][:2], ("p/b", "echo b"))
        self.assertEqual(self.t.state()["p/b.attempts"]["count"], 1)
        self.assertNotIn("p/b", self.t.state())

    def test_running_copy_is_not_started_again(self):
        import os
        self.run_file("p__b", {"date": "2026-10-30", "started": "2026-10-30T11:50:00", "pid": os.getpid()})
        self.t.tick([{"name": "p/b", "time": "06:00", "background": True, "command": "echo b"}], DAY)
        self.assertEqual(self.t.spawned, [])
        self.assertTrue((self.t.m.RUNS_DIR / "p__b.json").exists())

    def test_reconcile_merges_success_and_interval_last_ok(self):
        self.run_file("p__b", {"date": "2026-10-30", "started": "2026-10-30T11:00:00", "pid": 1, "exit": 0})
        self.run_file("p__i", {"date": "2026-10-30", "started": "2026-10-30T11:30:00", "pid": 1, "exit": 0})
        jobs = [{"name": "p/b", "time": "06:00", "background": True, "command": "echo b"},
                {"name": "p/i", "every_minutes": 60, "background": True, "command": "echo i"}]
        self.t.tick(jobs, DAY)
        st = self.t.state()
        self.assertEqual(st["p/b"], "2026-10-30")
        self.assertEqual(st["p/i.last_ok"], "2026-10-30T11:30:00")
        self.assertEqual(self.t.spawned, [])
        self.assertEqual(list(self.t.m.RUNS_DIR.iterdir()), [])

    def test_reconcile_failure_and_vanished_run(self):
        self.run_file("p__f", {"date": "2026-10-30", "started": "2026-10-30T11:00:00", "pid": 1, "exit": 2})
        self.run_file("p__gone", {"date": "2026-10-30", "started": "2026-10-30T10:00:00", "pid": None})
        self.t.tick([], DAY)
        failed = [json.loads(b["argv"][b["argv"].index("--payload") + 1]) for b in self.t.bus]
        self.assertEqual([f["job"] for f in failed], ["p/gone"])
        self.assertEqual(failed[0]["exit"], -1)
        self.assertNotIn("p/f", self.t.state())

    def test_renamed_job_takes_the_old_state(self):
        st = self.t.m.STATE_FILE
        st.write_text(json.dumps({"old": "2026-10-30", "old.attempts": {"date": "2026-10-30", "count": 1}}))
        info = {"alias": {}, "renames": {"p/new": "old"}, "skipped": [], "errors": {}}
        self.t.tick([{"name": "p/new", "time": "06:00", "command": "echo new"}], DAY, info)
        self.assertEqual(self.t.ran, [])
        self.assertEqual(self.t.state()["p/new"], "2026-10-30")


class RegistryConversion(unittest.TestCase):
    def test_at_every_filters_after_and_before(self):
        import registry
        index = {"projects": {"p": {"name": "p", "paths": {"workspace": "/tmp/p-ws"}, "jobs": [
            {"name": "a", "at": "06:00", "run": "true", "days": ["mon"], "max_attempts_per_day": 2},
            {"name": "b", "every": "1h", "run": "true", "between": "08:00-20:00", "after": ["a", "q/x"],
             "background": True},
        ]}}, "modules": {"m": {"name": "m", "type": "module", "jobs": [
            {"name": "c", "at": "05:00", "run": "true", "before": ["p/a"]}]}}}
        with mock.patch.object(registry.isolation, "sandboxed", lambda doc: False):
            jobs, skipped, renames = registry.scheduler_jobs(index, set())
        by = {j["name"]: j for j in jobs}
        self.assertEqual(by["p/a"]["time"], "06:00")
        self.assertEqual(by["p/a"]["days_of_week"], ["mon"])
        self.assertEqual(by["p/a"]["max_attempts_per_day"], 2)
        self.assertEqual(by["p/a"]["after"], ["m/c"])
        self.assertEqual(by["p/a"]["project"], "p")
        self.assertEqual(by["p/b"]["every_minutes"], 60)
        self.assertEqual(by["p/b"]["after"], ["p/a", "q/x"])
        self.assertTrue(by["p/b"]["background"])
        self.assertTrue(by["p/a"]["command"].endswith("&& true"))
        self.assertEqual((skipped, renames), ([], {}))


if __name__ == "__main__":
    unittest.main()
