"""E1 golden (docs/design/i18n.md) for bin/gen-status-page.py: the status page and the two reading pages, on zh-Hant.

The page is built in-process from a temp instance (temp hub root, config, data, secrets, state, bus): real files for the
scheduler log and state, agent-job status and reports, backup records, worktree-prune record, merge.failed events,
digests and todo.md; canned answers for what main() asks other modules (scheduler.all_jobs, alert.failures, appliers,
worktrees.overview, systemd_units.state), launchctl and `hubbus status --json`. Time is frozen. The service-manager /
alerts checks are an elif ladder, so several variants each take one branch.

    bin/py -m unittest tests.test_i18n_golden_status_page
    I18N_GOLDEN_UPDATE=1 bin/py -m unittest tests.test_i18n_golden_status_page    # rewrite (only on purpose)
"""
import datetime
import importlib.util
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_lang  # noqa: E402

GOLDEN = golden_lang.GOLDEN
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
INPUTS = json.loads((golden_lang.INPUTS / "status-page-inputs.json").read_text())
NOW = datetime.datetime(2026, 10, 30, 10, 0)   # a Friday


class FakeDT(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.combine(NOW.date(), NOW.time())


class FakeDate(datetime.date):
    @classmethod
    def today(cls):
        return cls(NOW.year, NOW.month, NOW.day)


def iso(delta: datetime.timedelta) -> str:
    return (NOW + delta).isoformat()


H = datetime.timedelta(hours=1)
D = datetime.timedelta(days=1)

JOBS = [
    {"name": "alpha/daily", "command": "echo daily", "time": "08:00", "days_of_week": ["mon", "wed", "fri"]},
    {"name": "alpha/broken", "command": "echo broken", "time": "09:00"},
    {"name": "beta/poll", "command": "echo poll", "every_minutes": 15, "between": "08:00-20:00"},
    {"name": "beta/poll2", "command": "echo poll2", "every_minutes": 30},
    {"name": "beta/monthly", "command": "echo m", "time": "06:00", "months": [1, 7], "days_of_month": [1, 15],
     "end_date": "2027-12-31"},
    {"name": "beta/paused", "command": "echo p", "time": "07:00", "days_of_month": [3], "enabled": False},
    {"name": "beta/ended", "command": "echo e", "time": "05:00", "end_date": "2026-01-01"},
    {"name": "beta/tomorrow", "command": "echo t", "time": "07:00"},
    {"name": "alpha/review", "command": "bin/run-agent-job.sh alpha-review", "time": "03:00", "agent_name": "alpha-review"},
    {"name": "beta/notify", "command": "AGENT_JOB_ALLOW=Read bin/run-agent-job.sh beta-notify", "time": "04:00",
     "days_of_week": ["sat", "sun"], "agent_name": "beta-notify"},
]
LOG = """[2026-10-30 08:00:01] running job 'alpha/daily'
[2026-10-30 08:00:05] job 'alpha/daily' finished OK
[2026-10-30 09:00:01] running job 'alpha/broken'
[2026-10-30 09:00:09] job 'alpha/broken' FAILED (exit 2)
[2026-10-30 09:30:00] running job 'beta/poll2'
[2026-10-30 09:45:00] tick
"""
WORKTREES = {
    "alpha": {"worktrees": [
        {"branch": "feat-a", "age_days": 1, "clean": True, "merged": True, "files": []},
        {"branch": "feat-b", "age_days": 3, "clean": False, "merged": True, "files": ["x.py"]},
        {"branch": "feat-c", "age_days": 0, "clean": False, "merged": False, "files": ["a.py", "b.py"]},
        {"branch": "feat-d", "age_days": 2, "clean": False, "merged": False, "files": ["a.py"]}],
        "overlaps": {"a.py": ["feat-c", "feat-d"]}, "ungated_main_7d": 3},
    "beta": {"worktrees": [], "overlaps": {}, "ungated_main_7d": 1},
}


def full_options() -> dict:
    """the busy instance: something in every section"""
    return dict(sm="launchd", launchctl={"local.brunna-scheduler": 0, "local.brunna-scheduler-watchdog": 113},
                alerts=True, secrets=True, tick_age=2 * H, jobs=JOBS, reg_errors={"gamma": [f"e{i}" for i in range(1, 6)]},
                failures={"discord": {"at": iso(-H), "text": "HTTP 401"}}, applied={"commit": "a" * 40, "at": iso(-D), "ok": True},
                commit="b" * 40 + "+dirty", backup_age=48 * H, verify_age=None, bus=INPUTS["bus_busy"], worktrees=WORKTREES,
                prune={"at": iso(-5 * H), "deps": [1, 2], "removed": [1], "freed_mb": 120, "skipped": ["w"]},
                merge_failed=True, agent=True, digests=True, todo=True)


def quiet_options(**over) -> dict:
    """nothing to do: every check passes"""
    o = dict(sm="launchd", launchctl={"local.brunna-scheduler": 0, "local.brunna-scheduler-watchdog": 0},
             alerts=True, secrets=True, tick_age=datetime.timedelta(minutes=5), jobs=[JOBS[0]], reg_errors={},
             failures={}, applied={"commit": "c" * 40, "at": iso(-D), "ok": True}, commit="c" * 40, backup_age=H,
             verify_age=D, bus=INPUTS["bus_quiet"], worktrees={}, prune=None, merge_failed=False, agent=False,
             digests=False, todo=False)
    o.update(over)
    return o


class StatusPage(unittest.TestCase):
    maxDiff = None

    def build(self, tmp: pathlib.Path, o: dict) -> dict[str, str]:
        hub, cfg_dir, data, secrets, state, bus = (tmp / d for d in ("hub", "config", "data", "secrets", "state", "bus"))
        for d in (hub / ".state", hub / ".logs", cfg_dir, data, secrets, state, bus):
            d.mkdir(parents=True, exist_ok=True)
        if o.get("store", True):   # the coding agent's checked copy, in the instance's store (<STATE>/runtimes)
            import store_fixture
            store_fixture.promote_into(state / "runtimes")
        cfg = {"language": {"ui": golden_lang.LANG, "content": golden_lang.LANG}, "bus_dir": str(bus)}
        if o["alerts"]:
            cfg["alerts"] = o["alerts"] if isinstance(o["alerts"], dict) else {"discord": {}}
        if o.get("backup_module", True):
            cfg["modules"] = {"backup": True}
        (cfg_dir / "hub.json").write_text(json.dumps(cfg))
        if o["secrets"]:
            for f in ("discord-token", "discord-alert-channel"):
                (secrets / f).write_text("x")
        sched_state = hub / ".state" / "scheduler-last-run.json"
        if o["tick_age"] is not None:
            sched_state.write_text(json.dumps({"alpha/daily": NOW.date().isoformat(), "beta/tomorrow": NOW.date().isoformat(),
                                               "beta/poll2.last_ok": iso(-20 * datetime.timedelta(minutes=1))}))
            t = (NOW - o["tick_age"]).timestamp()
            os.utime(sched_state, (t, t))
        (hub / ".logs" / "scheduler.log").write_text(LOG)
        backup = hub / ".state" / "backup"
        backup.mkdir()
        for fname, age in (("last.json", o["backup_age"]), ("last-verify.json", o["verify_age"])):
            if age is not None:
                (backup / fname).write_text(json.dumps({"at": iso(-age)}))
        if o["agent"]:
            jobs = hub / ".state" / "agent-jobs"
            rev, note = jobs / "alpha-review", jobs / "beta-notify"
            rev.mkdir(parents=True)
            note.mkdir()
            (rev / "status.json").write_text(json.dumps({"state": "failed", "started_at": iso(-7 * H), "error": "exit 1: boom"}))
            (rev / "2026-10-29-0300.md").write_text("old report <b>x</b>\n")
            (rev / "2026-10-30-0300.md").write_text("# report\nall checked\n")
            (note / "status.json").write_text(json.dumps({"state": "ok", "started_at": iso(-6 * H), "finished_at": iso(-5 * H),
                                                          "notify": "yes", "summary": "two items need a look"}))
            (note / "STOP").write_text("nothing left to do\n")
        if o["prune"]:
            (state / "worktree-prune.json").write_text(json.dumps(o["prune"]))
        db = sqlite3.connect(bus / "bus.db")
        db.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY, ts TEXT, type TEXT, payload TEXT)")
        if o["merge_failed"]:
            db.execute("INSERT INTO events (ts, type, payload) VALUES (?, 'merge.failed', ?)",
                       (iso(-H), json.dumps({"project": "alpha", "branch": "feat-c", "stage": "test", "detail": "2 tests failed"})))
        db.commit()
        db.close()
        if o["digests"]:
            (data / "digest").mkdir()
            (data / "digest" / "2026-10-29.md").write_text("# Digest 10-29\n\n## alpha\n- did `x`\n")
            (data / "digest" / "2026-10-30.md").write_text("# Digest 10-30\n\n**quiet** day\n")
            (data / "reflect").mkdir()
        if o["todo"]:
            (data / "todo.md").write_text("# To-do\n\n- [ ] item one\n")
        applied = state / "config-applied.json"
        if o["applied"]:
            applied.write_text(json.dumps(o["applied"]))

        def fake(name, **attrs):
            m = types.ModuleType(name)
            for k, v in attrs.items():
                setattr(m, k, v)
            return m

        def raises(e):
            def f(*a, **k):
                raise e
            return f
        channels = list(o["alerts"]) if isinstance(o["alerts"], dict) else ["discord"] if o["alerts"] else []
        mods = {
            "scheduler": fake("scheduler", all_jobs=lambda: (o["jobs"], {"errors": o["reg_errors"]})),
            # as bin/alert.py: with no channel set up there are no failed sends (tests/test_alert_channels.py)
            "alert": fake("alert", failures=raises(RuntimeError("alerts file unreadable")) if o["failures"] is None
                          else lambda: o["failures"] if channels else {}, configured=lambda: channels),
            "appliers": fake("appliers", config_commit=raises(RuntimeError("no config repo")) if o["commit"] is None
                             else lambda: o["commit"], applied_path=lambda: applied),
            "worktrees": fake("worktrees", overview=raises(RuntimeError("overview broke")) if o["worktrees"] is None
                              else lambda: o["worktrees"]),
            "systemd_units": fake("systemd_units", state=lambda label: o.get("systemd", {}).get(label, "inactive")),
        }

        def run(cmd, **kw):
            if cmd[0] == "launchctl":
                return subprocess.CompletedProcess(cmd, o["launchctl"][cmd[2].rsplit("/", 1)[1]], "", "")
            if cmd[1].endswith("hubbus"):
                b = o["bus"]
                return subprocess.CompletedProcess(cmd, 1 if b is None else 0, "" if b is None else json.dumps(b),
                                                   "Traceback ...\nsqlite3.DatabaseError: file is not a database\n")
            raise AssertionError(cmd)

        env = {"AGENT_HUB_ROOT": str(hub), "AGENT_HUB_CONFIG": str(cfg_dir), "AGENT_HUB_DATA": str(data),
               "AGENT_HUB_SECRETS": str(secrets), "AGENT_HUB_STATE": str(state), "AGENT_HUB_HOME": "",
               "AGENT_HUB_LANG_UI": golden_lang.LANG, "HOME": str(tmp)}
        with mock.patch.dict(os.environ, env), mock.patch.dict(sys.modules):
            for m in ("hubconf", "i18n", "completion", "runtime_store"):   # completion.USAGE and the store follow STATE
                sys.modules.pop(m, None)
            sys.modules.update(mods)
            spec = importlib.util.spec_from_file_location("gsp_golden", ROOT / "bin" / "gen-status-page.py")
            gsp = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(gsp)
            hubconf = sys.modules["hubconf"]
            ns = types.SimpleNamespace(datetime=FakeDT, date=FakeDate, time=datetime.time, timedelta=datetime.timedelta)
            with mock.patch.object(gsp, "datetime", ns), mock.patch.object(gsp.subprocess, "run", side_effect=run), \
                    mock.patch.object(hubconf, "service_manager", return_value=o["sm"]):
                gsp.main()
            out = gsp.OUT.parent
        return {n: (out / n).read_text().replace(str(tmp), "<TMP>") for n in ("index.html", "digest.html", "todo.html")}

    @staticmethod
    def main_part(page: str) -> str:
        return page[page.index("<main>"):page.index("</main>") + len("</main>")]

    def scenario(self, base: pathlib.Path) -> str:
        out = []
        pages = self.build(base / "full", full_options())
        out += [("full: index.html", pages["index.html"]), ("full: digest.html", pages["digest.html"]),
                ("full: todo.html", pages["todo.html"])]
        variants = [
            ("scheduler down, systemd watchdog off, lookups fail",
             quiet_options(sm="systemd", failures=None, commit=None, bus=None, worktrees=None, jobs=[],
                           backup_age=None, verify_age=None)),
            ("no service manager, config apply left gaps",
             quiet_options(sm=None, applied={"commit": "c" * 40, "at": iso(-H), "ok": False})),
            ("no alerts configured, no scheduler state", quiet_options(alerts=False, tick_age=None)),
            ("alerts set, no discord secrets", quiet_options(secrets=False)),
            ("all clear", quiet_options()),
            # what `agenthub init` leaves on a machine with no service manager, no alerts and the backup module off,
            # after check-secrets ran once
            ("newcomer", quiet_options(sm=None, alerts=False, secrets=False, tick_age=None, backup_age=None,
                                       verify_age=None, backup_module=False, failures={
                                           "discord-probe": {"at": iso(-H), "text": "No alert channel is set"},
                                           "secrets": {"at": iso(-H), "text": "No alert channel"}})),
            ("newcomer whose scheduler cron runs", quiet_options(sm=None, alerts=False, secrets=False,
                                                                 backup_module=False)),
            ("an alert on ntfy could not be sent", quiet_options(alerts={"ntfy": {"topic": "t"}}, secrets=False, failures={
                "secrets": {"at": iso(-H), "text": "HTTP 500"}})),
            # a fresh install or a restore before its first install: no platform run starts, said in red
            ("no checked copy of the coding agent", quiet_options(store=False)),
        ]
        for name, o in variants:
            pages = self.build(base / name.split(",")[0].replace(" ", "-"), o)
            out.append((name, self.main_part(pages["index.html"])))
            if name.startswith("no alerts"):
                out += [(f"{name}: digest.html", self.main_part(pages["digest.html"])),
                        (f"{name}: todo.html", self.main_part(pages["todo.html"]))]
        return "\n".join(f"=== {k}\n{v}" for k, v in out) + "\n"

    def test_pages(self):
        with tempfile.TemporaryDirectory() as d:
            text = self.scenario(pathlib.Path(d).resolve())
        f = GOLDEN / "status-page.txt"
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())


if __name__ == "__main__":
    sys.exit(unittest.main())
