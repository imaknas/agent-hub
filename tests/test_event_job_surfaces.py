"""Where an event job (docs/design/event-jobs.md) shows up outside the tick: the status page's row texts (when, next,
the detail line, the over-a-day alert), schedulectl's events / skip / run on an event job and its 30-second wait for
the scheduler's state lock, and the scheduler applier's plan when a job's trigger changes.

Everything runs in-process against a temp directory and a real SQLite bus in it; schedulectl's HUB and job list point
at the temp directory, the scheduler's state lock is the real one. The texts are read in English (pinned below).

    bin/py -m unittest tests.test_event_job_surfaces
"""
import contextlib
import datetime
import fcntl
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import event_jobs  # noqa: E402
import hubconf  # noqa: E402

LOCAL = datetime.datetime.now().astimezone().tzinfo
NOW = datetime.datetime(2026, 10, 30, 12, 0)
NAME = "p/react"

_PATCHES = [mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "en"}),
            mock.patch.object(hubconf, "former_names", lambda p: [])]


def setUpModule():
    for p in _PATCHES:
        p.start()


def tearDownModule():
    for p in reversed(_PATCHES):
        p.stop()


def load(name: str, path: pathlib.Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def ejob(**trigger):
    on = {"events": ["merge.done"], "projects": ["p"], "sources": [], "where": {}, "debounce_minutes": 0, **trigger}
    return {"name": NAME, "project": "p", "command": "echo react", "trigger": on, "max_attempts_per_day": 12}


class Bus:
    def __init__(self, d: pathlib.Path, generation="g1"):
        self.db = d / "bus.db"
        c = self.c = sqlite3.connect(self.db, isolation_level=None)
        c.execute("CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, ts TEXT NOT NULL,"
                  " type TEXT NOT NULL, source TEXT NOT NULL, to_project TEXT, ref TEXT, payload TEXT NOT NULL DEFAULT '{}')")
        c.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        c.execute("INSERT INTO meta VALUES('generation', ?)", (generation,))
        self.n = 0

    def publish(self, at: datetime.datetime, type="merge.done", project="p", **payload):
        self.n += 1
        self.c.execute("INSERT INTO events(id, ts, type, source, to_project, payload) VALUES(?,?,?,?,?,?)",
                       (f"e{self.n}", at.replace(tzinfo=LOCAL).isoformat(), type, project, project,
                        json.dumps({"v": 1, **payload})))
        return self.n


class WithBus(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = Bus(self.tmp)
        self.addCleanup(self.bus.c.close)
        p = mock.patch.object(hubconf, "BUS_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)


# ---------- the status page (bin/gen-status-page.py) ----------

class StatusPageRows(WithBus):
    @classmethod
    def setUpClass(cls):
        cls.gsp = load("gsp_events", ROOT / "bin" / "gen-status-page.py")

    def test_when_column(self):
        g = self.gsp
        self.assertEqual(g.describe_when(ejob()), "on merge.done")
        self.assertEqual(g.describe_when(ejob(events=["merge.done", "merge.failed"], debounce_minutes=10,
                                              projects=["p", "*"], where={"branch": "c/*"})),
                         "on merge.done, merge.failed (quiet 10 min; from any project; branch=c/*)")
        self.assertEqual(g.describe_when(ejob(projects=["p", "q"])), "on merge.done (from q)")
        self.assertEqual(g.describe_when({**ejob(), "end_date": "2026-12-31"}), "on merge.done (until 2026-12-31)")

    def test_next_column(self):
        g = self.gsp
        self.assertEqual(g.next_run({**ejob(), "enabled": False}, {}, NOW), "paused")
        self.assertEqual(g.next_run(ejob(), {}, NOW), "on the next event (starts counting at the next tick)")
        self.bus.publish(NOW - datetime.timedelta(hours=3))
        state = {f"{NAME}.event_seq": 1}
        self.assertEqual(g.next_run(ejob(), state, NOW), "on the next event")
        self.bus.publish(NOW - datetime.timedelta(hours=2))
        self.bus.publish(NOW - datetime.timedelta(hours=1))
        self.bus.publish(NOW, type="merge.failed")                    # not a type the job takes
        self.bus.publish(NOW, project="q")                            # another project's
        since = NOW - datetime.timedelta(hours=2)
        self.assertEqual(g.next_run(ejob(), state, NOW), f"2 events waiting since {since:%m/%d %H:%M}")
        capped = {**state, f"{NAME}.attempts": {"date": NOW.strftime("%Y-%m-%d"), "count": 12}}
        self.assertEqual(g.next_run(ejob(), capped, NOW),
                         f"2 events waiting since {since:%m/%d %H:%M}; today's 12 runs are used up")
        one = {**ejob(), "max_attempts_per_day": 1}
        self.assertEqual(g.next_run(one, {**state, f"{NAME}.attempts": {"date": NOW.strftime("%Y-%m-%d"), "count": 1}},
                                    NOW), f"2 events waiting since {since:%m/%d %H:%M}; today's 1 run is used up")
        yesterday = {**state, f"{NAME}.attempts": {"date": "2026-10-29", "count": 12}}
        self.assertEqual(g.next_run(ejob(), yesterday, NOW), f"2 events waiting since {since:%m/%d %H:%M}")

    def test_next_column_on_an_unreadable_bus(self):
        self.bus.c.close()
        self.bus.db.write_bytes(b"not a database, " * 64)
        got = self.gsp.next_run(ejob(), {f"{NAME}.event_seq": 0}, NOW)
        self.assertTrue(got.startswith("the bus can't be read: "), got)
        alerts = []
        self.assertEqual(self.gsp.event_detail(ejob(), {f"{NAME}.event_seq": 0}, NOW, alerts), [])
        self.assertEqual(alerts, [], "an unreadable bus is not an over-a-day alert")

    def test_detail_line(self):
        g = self.gsp
        alerts = []
        self.assertEqual(g.event_detail(ejob(), {}, NOW, alerts), [])
        state = {f"{NAME}.event_last": {"count": 3, "at": "2026-10-30T11:22:33"},
                 f"{NAME}.event_held": {"date": "2026-10-30", "count": 2}}
        self.assertEqual(g.event_detail(ejob(), state, NOW, alerts), [
            "last run: 3 events, 10-30 11:22",
            f"2 events held back today by the loop chain (schedulectl events {NAME})"])
        state = {f"{NAME}.event_last": {"count": 1, "at": "2026-10-30T11:22:33"},
                 f"{NAME}.event_held": {"date": "2026-10-29", "count": 2}}     # held yesterday: not shown
        self.assertEqual(g.event_detail(ejob(), state, NOW, alerts), ["last run: 1 event, 10-30 11:22"])
        self.assertEqual(alerts, [])

    def test_alert_after_a_day_of_waiting(self):
        g = self.gsp
        self.bus.publish(NOW - datetime.timedelta(hours=23))
        state = {f"{NAME}.event_seq": 0}
        alerts = []
        g.event_detail(ejob(), state, NOW, alerts)
        self.assertEqual(alerts, [], "under a day: no alert")
        g.event_detail(ejob(), {}, NOW + datetime.timedelta(days=3), alerts)
        self.assertEqual(alerts, [], "no cursor yet: nothing counts as waiting")
        later = NOW + datetime.timedelta(hours=2)                     # the event is now 25 hours old
        g.event_detail(ejob(), state, later, alerts)
        since = NOW - datetime.timedelta(hours=23)
        self.assertEqual(alerts, [("notify", f"Event job {NAME} has had events waiting since {since:%m/%d %H:%M} "
                                             f"(schedulectl events {NAME})")])
        alerts.clear()
        g.event_detail(ejob(), {f"{NAME}.event_seq": 1}, later, alerts)
        self.assertEqual(alerts, [], "the cursor passed it: nothing waits")


# ---------- schedulectl (bin/schedulectl) ----------

class Schedulectl(WithBus):
    def setUp(self):
        super().setUp()
        self.m = load("schedulectl_events", ROOT / "bin" / "schedulectl")
        self.hub = self.tmp / "hub"
        (self.hub / ".state").mkdir(parents=True)
        self.state_file = self.hub / ".state" / "scheduler-last-run.json"
        self.lock_file = self.hub / ".state" / "scheduler.lock"
        self.jobs = [ejob(), {"name": "clock", "time": "06:00", "command": "echo clock"}]
        self.sched = load("scheduler_for_schedulectl", ROOT / "bin" / "scheduler.py")
        for p in (mock.patch.object(self.m, "HUB", self.hub), mock.patch.object(self.m, "all_jobs", lambda: self.jobs),
                  mock.patch.object(self.m, "scheduler_module", lambda: self.sched),
                  mock.patch.object(hubconf, "login_shell", lambda: "/bin/sh"),
                  mock.patch.dict(os.environ, {"AGENT_HUB_JOB_CHAIN": "", "AGENT_HUB_JOB_DEPTH": ""})):
            p.start()
            self.addCleanup(p.stop)

    def state(self) -> dict:
        return json.loads(self.state_file.read_text()) if self.state_file.exists() else {}

    def write_state(self, d: dict) -> None:
        self.state_file.write_text(json.dumps(d))

    def out(self, fn, *a) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(*a)
        return buf.getvalue()

    @contextlib.contextmanager
    def held(self, release_after: float | None = None):
        """the state lock held as a tick holds it (another open file, so flock conflicts even in this process)"""
        fd = os.open(self.lock_file, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        closed = []

        def release():
            if not closed:
                closed.append(1)
                os.close(fd)
        timer = threading.Timer(release_after, release) if release_after is not None else None
        if timer:
            timer.start()
        try:
            yield
        finally:
            if timer:
                timer.cancel()
                timer.join()
            release()

    def publish_some(self):
        t = datetime.datetime.now() - datetime.timedelta(minutes=30)
        self.bus.publish(t)                                       # 1: before the cursor
        self.bus.publish(t, branch="b")                           # 2: waiting
        self.bus.publish(t, chain=[NAME])                         # 3: the loop chain holds it back
        self.bus.publish(t, type="merge.failed")                  # 4: a type the job doesn't take
        self.bus.publish(t, project="q")                          # 5: another project's
        return t

    def test_only_event_jobs(self):
        for name in ("clock", "nope"):
            for cmd in (self.m.cmd_events, self.m.cmd_skip):
                with self.subTest(name=name, cmd=cmd.__name__), self.assertRaises(SystemExit) as e:
                    cmd(name)
                self.assertEqual(e.exception.code, f"'{name}' is not an event job (a registry job with trigger:)")

    def test_events_without_a_cursor(self):
        self.assertEqual(self.out(self.m.cmd_events, NAME).splitlines(),
                         [f"{NAME}: on merge.done (at most 12 tries a day)",
                          "No cursor yet: the next tick starts it at the bus head"])

    def test_events_lists_waiting_and_held(self):
        t = self.publish_some()
        self.write_state({f"{NAME}.event_seq": 1, f"{NAME}.event_gen": "g1"})
        lines = self.out(self.m.cmd_events, NAME).splitlines()
        when = f"{t:%m/%d %H:%M}"
        self.assertEqual(lines, [
            f"{NAME}: on merge.done (at most 12 tries a day)",
            f"cursor 1, bus head 5; next: 1 event waiting since {when}",
            "waiting:",
            f"        2  {when}  merge.done       p                ",
            "held back by the loop chain:",
            f"        3  {when}  merge.done       p                {NAME}"])
        self.assertEqual(self.state(), {f"{NAME}.event_seq": 1, f"{NAME}.event_gen": "g1"}, "events only reads")

    def test_skip_moves_the_cursor_to_the_head(self):
        self.publish_some()
        self.write_state({f"{NAME}.event_seq": 1, "other": "kept"})
        self.assertEqual(self.out(self.m.cmd_skip, NAME).strip(),
                         f"{NAME}: cursor 1 → 5; the events in between won't be run")
        self.assertEqual(self.state(), {f"{NAME}.event_seq": 5, f"{NAME}.event_gen": "g1", "other": "kept"})
        self.assertEqual(event_jobs.pending(ejob(), self.state()[f"{NAME}.event_seq"])["events"], [])

    def test_skip_without_a_cursor_starts_one(self):
        self.publish_some()
        self.assertEqual(self.out(self.m.cmd_skip, NAME).strip(),
                         f"{NAME}: cursor None → 5; the events in between won't be run")
        self.assertEqual(self.state(), {f"{NAME}.event_seq": 5, f"{NAME}.event_gen": "g1"})

    def test_run_an_event_job_here_gets_no_events_and_keeps_the_cursor(self):
        self.publish_some()
        self.jobs[0]["command"] = 'printf "%s|%s|%s|%s\\n" "$AGENT_HUB_EVENTS" "$AGENT_HUB_JOB" "$AGENT_HUB_JOB_CHAIN" ' \
                                  '"$AGENT_HUB_JOB_DEPTH"'
        self.write_state({f"{NAME}.event_seq": 1, f"{NAME}.event_gen": "g1"})
        with mock.patch.object(hubconf, "service_manager", lambda: None):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = self.m.cmd_run(NAME)
        self.assertEqual(code, 0)
        self.assertIn(f'{{"events": []}}|{NAME}|["{NAME}"]|1', buf.getvalue().splitlines())
        self.assertEqual(self.state(), {f"{NAME}.event_seq": 1, f"{NAME}.event_gen": "g1",
                                        NAME: datetime.date.today().isoformat()}, "a manual run is a test: cursor stays")
        logs = list((self.hub / ".logs").glob("p__react-*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn('{"events": []}', logs[0].read_text())

    def test_run_env(self):
        self.assertEqual(json.loads(self.m.run_env(ejob())["AGENT_HUB_EVENTS"]), {"events": []})
        self.assertNotIn("AGENT_HUB_EVENTS", self.m.run_env(self.jobs[1]))
        with mock.patch.dict(os.environ, {"AGENT_HUB_JOB_CHAIN": json.dumps(["a", NAME]), "AGENT_HUB_JOB_DEPTH": "2"}):
            env = self.m.run_env(ejob())
        self.assertEqual((json.loads(env["AGENT_HUB_JOB_CHAIN"]), env["AGENT_HUB_JOB_DEPTH"]), (["a", NAME], "3"))

    def test_a_start_that_fails_takes_the_mark_back(self):
        for prev in (None, "2026-01-01"):
            with self.subTest(prev=prev):
                self.write_state({f"{NAME}.event_seq": 1, **({NAME: prev} if prev else {})})
                with mock.patch.object(hubconf, "service_manager", lambda: "launchd"), \
                        mock.patch.object(self.m, "start", mock.Mock(side_effect=RuntimeError("no launchd"))), \
                        self.assertRaises(RuntimeError):
                    self.m.cmd_run(NAME)
                self.assertEqual(self.state(), {f"{NAME}.event_seq": 1, **({NAME: prev} if prev else {})})

    def test_waits_for_a_tick_then_writes(self):
        self.publish_some()
        self.write_state({f"{NAME}.event_seq": 1})
        t0 = time.monotonic()
        with self.held(release_after=1.0):
            out = self.out(self.m.cmd_skip, NAME)
        self.assertGreaterEqual(time.monotonic() - t0, 0.9, "it waited for the lock")
        self.assertIn("cursor 1 → 5", out)
        self.assertEqual(self.state()[f"{NAME}.event_seq"], 5)

    def test_gives_up_after_its_wait_and_changes_nothing(self):
        """edit_state asks the scheduler's lock for 30 seconds; the wait is shortened here, the branch is the same"""
        asked = []
        real = self.sched.state_lock

        def short(timeout=0, path=None):
            asked.append((timeout, path))
            return real(timeout=0.4, path=path)
        self.sched.state_lock = short
        self.publish_some()
        before = {f"{NAME}.event_seq": 1, NAME: "2026-01-01"}
        self.write_state(before)
        held = ("The scheduler's state is held by a tick in progress (a job running in the foreground, or this command "
                "was started by one); nothing was changed. Try again after the tick")
        with self.held():
            with self.assertRaises(SystemExit) as e:
                self.m.cmd_skip(NAME)
            self.assertEqual(e.exception.code, held)
            with mock.patch.object(hubconf, "service_manager", lambda: None), \
                    mock.patch.object(self.m, "run_here", mock.Mock(return_value=0)) as here, \
                    self.assertRaises(SystemExit) as e:
                self.m.cmd_run(NAME)
            self.assertEqual(e.exception.code, held)
            here.assert_not_called()                              # the mark couldn't be written: nothing ran
        self.assertEqual(asked, [(30, self.lock_file)] * 2)
        self.assertEqual(self.state(), before)


# ---------- the scheduler applier (bin/applier_scheduler.py) ----------

class ApplierTrigger(unittest.TestCase):
    def setUp(self):
        import applier_scheduler
        import registry
        self.m = applier_scheduler
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for p in (mock.patch.object(self.m, "SNAPSHOT", self.tmp / "jobs.json"),
                  mock.patch.object(registry.isolation, "sandboxed", lambda doc: False)):
            p.start()
            self.addCleanup(p.stop)

    def index(self, *jobs):
        return {"v": 1, "errors": {}, "projects": {"demo": {"name": "demo", "paths": {"workspace": str(self.tmp / "ws")},
                                                            "jobs": list(jobs)}}}

    def plan(self, *jobs):
        return [tuple(c) for c in self.m.plan(self.index(*jobs))]

    def test_trigger_changes_show_in_the_plan(self):
        react = {"name": "react", "trigger": {"events": ["merge.done"], "debounce": "10m"}, "run": "/bin/echo hi"}
        self.assertEqual(self.plan(react), [("add", "job:demo/react", "on merge.done")])
        self.m.apply(self.index(react))
        self.assertEqual(self.plan(react), [])
        for trigger in ({"events": ["merge.done"], "debounce": "20m"},
                        {"events": ["merge.done", "merge.failed"], "debounce": "10m"},
                        {"events": ["merge.done"], "debounce": "10m", "where": {"branch": "c/*"}}):
            with self.subTest(trigger=trigger):
                self.assertEqual(self.plan({**react, "trigger": trigger}),
                                 [("update", "job:demo/react", "changed trigger")])

    def test_clock_to_trigger(self):
        clock = {"name": "react", "at": "03:00", "run": "/bin/echo hi"}
        self.m.apply(self.index(clock))
        on = {"name": "react", "trigger": {"events": ["merge.done"]}, "run": "/bin/echo hi"}
        # an event job gets the registry's default daily cap, which a clock job doesn't carry
        self.assertEqual(self.plan(on), [("update", "job:demo/react", "changed max_attempts_per_day, time, trigger")])


if __name__ == "__main__":
    unittest.main()
