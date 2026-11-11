"""E1 golden (docs/design/i18n.md) for the scheduler side: schedulectl, scheduler.py, render-services.py,
render-project-settings.py and appliers.py, on zh-Hant.

Everything runs in-process against a temp directory: the scripts' paths (state, logs, LaunchAgents, rendered records)
are module attributes that get pointed there, the registry is a canned index, and launchctl / systemctl / git commits
/ the bus / alerts are replaced by recorders. Nothing touches the machine's services. Not covered (needs the real
machine): `schedulectl run` on launchd (writes a plist into /tmp and bootstraps it), and the background fork of
scheduler.py (spawn_background; the "background process gone" path is covered through reconcile).

    bin/py -m unittest tests.test_i18n_golden_scheduler
    I18N_GOLDEN_UPDATE=1 bin/py -m unittest tests.test_i18n_golden_scheduler    # rewrite (only on purpose)
"""
import contextlib
import copy
import datetime
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import plistlib
import re
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_lang  # noqa: E402

GOLDEN = golden_lang.GOLDEN
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
ZH = {"AGENT_HUB_LANG_UI": golden_lang.LANG}
CP = subprocess.CompletedProcess


def load(name: str, file: str):
    loader = importlib.machinery.SourceFileLoader(name, str(BIN / file))
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def call(fn, *args) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            code = fn(*args)
        except SystemExit as e:
            code = e.code
    return f"[exit {code}]\n{buf.getvalue()}"


class Golden(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        env = mock.patch.dict(os.environ, ZH)
        env.start()
        self.addCleanup(env.stop)
        # where a scene has no service manager, it is a system without one: its reason, not the one this test's own
        # HOME would give (hubconf.home_is_the_accounts)
        home = mock.patch.object(hubconf, "home_is_the_accounts", lambda: True)
        home.start()
        self.addCleanup(home.stop)

    def normalize(self, text: str) -> str:
        for p, tag in ((self.tmp, "<TMP>"), (hubconf.ROOT, "<HUB>"), (ROOT, "<HUB>"), (hubconf.MAIN, "<HUB>")):
            text = text.replace(str(p), tag)
        text = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?", "<TS>", text)
        text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "<DATE>", text)
        return re.sub(r"\b\d{2}-\d{2} \d{2}:\d{2}\b", "<TS>", text)

    def check(self, name: str, parts: list[tuple[str, str]]):
        text = self.normalize("\n".join(f"=== {k}\n{v.rstrip(chr(10))}" for k, v in parts) + "\n")
        f = GOLDEN / f"{name}.txt"
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text(), f"output differs from {f.relative_to(ROOT)}")

    # --- schedulectl ----------------------------------------------------------------------------------------------
    def test_schedulectl(self):
        m = load("schedulectl_golden", "schedulectl")
        hub = self.tmp / "hub"
        for d in (".state", ".logs", "config"):
            (hub / d).mkdir(parents=True)
        m.HUB, m.LOG_DIR, m.SCHEDULE = hub, hub / ".logs", hub / "config" / "schedule.yaml"
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return CP(cmd, 1 if "commit-hub-files" in str(cmd[0]) else 0, "", "")
        class Popen:   # a job run in the foreground (no service manager): its output, then exit 3
            def __init__(self, cmd, **kw):
                calls.append(cmd)
                self.stdout = iter(["job says hi\n", "job done\n"])

            def wait(self):
                return 3

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        fake_sp = types.SimpleNamespace(run=run, Popen=Popen, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT,
                                        check_output=lambda *a, **k: b"501\n")
        jobs = [
            {"name": "nightly", "time": "03:00", "command": "echo nightly"},
            {"name": "weekly", "time": "07:00", "days_of_week": ["mon", "thu"], "enabled": False, "command": "echo w"},
            {"name": "quarterly", "time": "09:00", "months": [1, 4, 7, 10], "days_of_month": [1],
             "max_attempts_per_day": 2, "command": "echo q"},
            {"name": "monthly", "time": "10:00", "days_of_month": [1, 15], "end_date": "2026-12-31", "command": "echo m"},
            {"name": "often", "every_minutes": 15, "between": "08:00-23:00", "command": "echo o"},
            {"name": "hourly", "every_minutes": 60, "command": "echo h"},
            {"name": "demo/job", "time": "05:30", "command": "cd /x && echo j"},
        ]
        m.SCHEDULE.write_text("jobs:\n- name: nightly\n  time: '03:00'\n  command: echo nightly\n")
        cfg = hub / "config" / "hub.json"
        cfg.write_text(json.dumps({"v": 1}))
        points = {"points": {"p-plain": {"model_group": "strong"}, "p-pinned": {}, "p-none": {},
                             "p-comp": {"shape": "completion"}, "p-refused": {"shape": "completion"}}}
        import completion
        import llm
        real_get = hubconf.get

        def get(key, *a, **k):
            if key == "llm":
                return {"points": {"p-pinned": {"model": "m-pin"}}}
            return real_get(key, *a, **k)

        def resolve(name, spec):
            if name == "p-refused":
                raise completion.Refused("no key for provider x")
            return {"provider": "x", "model": "x-large"}
        out = []

        def ctl(label, *args, sm="launchd"):
            with mock.patch.object(sys, "argv", ["schedulectl", *args]), \
                    mock.patch.object(hubconf, "service_manager", lambda: sm):
                out.append((label, call(m.main)))
        with mock.patch.object(m, "subprocess", fake_sp), mock.patch.object(m, "all_jobs", lambda: jobs), \
                mock.patch.object(hubconf, "config_file", lambda n: hub / "config" / n), \
                mock.patch.object(hubconf, "config_path", lambda: hub / "config" / "hub.json"), \
                mock.patch.object(llm, "points", lambda: points), \
                mock.patch.object(llm, "model_for", lambda name, spec=None: {"p-plain": "m-strong", "p-pinned": "m-pin"}
                                  .get(name)):
            ctl("usage")
            ctl("bad command", "frobnicate")
            ctl("list", "list")
            with mock.patch.object(m, "all_jobs", lambda: []):
                ctl("list empty", "list")
            ctl("pause unknown", "pause", "nosuch")
            ctl("pause", "pause", "nightly")
            out.append(("schedule.yaml after pause", m.SCHEDULE.read_text()))
            ctl("resume", "resume", "nightly")
            ctl("logs, no log yet", "logs")
            ctl("logs of an unknown job", "logs", "nosuch")
            (m.LOG_DIR / "scheduler.log").write_text("".join(f"line {i}\n" for i in range(12)))
            (m.LOG_DIR / "nightly-2026-10-30.log").write_text("a\nb\nc\n")
            ctl("logs", "logs")
            ctl("logs of a job, 2 lines", "logs", "nightly", "2")
            for sm in ("launchd", "systemd"):
                ctl(f"status ({sm})", "status", sm=sm)
                ctl(f"stop ({sm})", "stop", sm=sm)
                ctl(f"start ({sm})", "start", sm=sm)
            for cmd in ("status", "run", "stop", "start"):
                ctl(f"{cmd} without a service manager", cmd, "nightly", sm=None)
            ctl("run unknown", "run", "nosuch")
            ctl("run (systemd)", "run", "demo/job", sm="systemd")
            out.append(("last-run state", (hub / ".state" / "scheduler-last-run.json").read_text()))
            with mock.patch.object(hubconf, "get", get), mock.patch.object(completion, "settings", lambda: None):
                ctl("models, no providers", "models")
            with mock.patch.object(hubconf, "get", get), mock.patch.object(completion, "settings", lambda: {"x": 1}), \
                    mock.patch.object(completion, "resolve", resolve):
                ctl("models, with providers", "models")
            ctl("set-model unknown", "set-model", "nosuch", "m-x")
            ctl("set-model state", "set-model", "state", "m-x")
            out.append(("hub.json", cfg.read_text()))
            ctl("set-model state default", "set-model", "state", "default")
            ctl("set-model one point", "set-model", "p-plain", "m-y")
            out.append(("hub.json", cfg.read_text()))
        self.check("schedulectl", out)

    # --- scheduler.py ---------------------------------------------------------------------------------------------
    def test_scheduler(self):
        m = load("scheduler_golden", "scheduler.py")
        st = self.tmp / "state"
        m.STATE_FILE, m.RUNS_DIR, m.HEARTBEAT = st / "scheduler-last-run.json", st / "runs", st / "scheduler-heartbeat"
        m.LOG_DIR, m.FAILING_FILE, m.SCHEDULE = self.tmp / "logs", st / "scheduler-failing.json", self.tmp / "none.yaml"
        m.LOCK_FILE = st / "scheduler.lock"
        st.mkdir()
        m.RUNS_DIR.mkdir()
        recorded, alerts, spawned = [], [], []

        def run(cmd, **kw):
            if any(x in str(c) for c in cmd[:2] for x in ("hubbus", "gen-status-page")):
                recorded.append([pathlib.Path(cmd[1]).name, *cmd[2:]])
                return CP(cmd, 0, "", "")
            return subprocess.run(cmd, **kw)
        fake_sp = types.SimpleNamespace(run=run, CalledProcessError=subprocess.CalledProcessError, STDOUT=subprocess.STDOUT,
                                        Popen=subprocess.Popen, TimeoutExpired=subprocess.TimeoutExpired)
        now = datetime.datetime(2026, 10, 30, 12, 0)

        class FakeDT(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return now
        jobs = [
            {"name": "ok", "time": "00:00", "command": "echo fine"},
            {"name": "bad", "time": "00:00", "command": "exit 3"},
            {"name": "late", "time": "23:59", "command": "echo late"},
            {"name": "waits", "time": "00:00", "after": ["late"], "command": "echo w"},
            {"name": "off", "time": "00:00", "enabled": False, "command": "echo off"},
            {"name": "often", "every_minutes": 15, "command": "echo often"},
            {"name": "bg", "time": "00:00", "background": True, "command": "echo bg"},
        ]
        info = {"alias": {}, "renames": {}, "skipped": [], "errors": {}}
        (m.RUNS_DIR / "gone__job.json").write_text(json.dumps({"date": "2026-10-30", "started": "2026-10-30T10:00:00",
                                                               "pid": None}))
        import alert
        import registry
        out = []
        with mock.patch.object(m, "subprocess", fake_sp), mock.patch.object(m.datetime, "datetime", FakeDT), \
                mock.patch.object(hubconf, "login_shell", lambda: "/bin/sh"), \
                mock.patch.object(alert, "once", lambda *a: alerts.append(a)), \
                mock.patch.object(m, "spawn_background", lambda *a: spawned.append(a[:2])):
            with mock.patch.object(m, "all_jobs", lambda log_errors=False: (jobs, info)):
                out.append(("tick", call(m.main)))
            out.append(("failing", m.FAILING_FILE.read_text()))
            out.append(("bus and status page", "\n".join(json.dumps(r, ensure_ascii=False) for r in recorded)))
            out.append(("spawned", repr(spawned)))
            out.append(("alert.once", "\n".join(repr(a) for a in alerts)))
            out.append(("job log", (m.LOG_DIR / "ok-2026-10-30.log").read_text()))
            failing = {"a/old": {"since": "2026-10-30T01:30:00", "detail": "exit 1"},
                       "b/nodetail": {"since": "2026-10-29T23:00:00"},
                       "c/recent": {"since": "2026-10-30T11:30:00", "detail": "exit 2"}}
            out.append(("failing_problems", "\n".join(m.failing_problems(failing, now))))
            with mock.patch.object(alert, "once", side_effect=RuntimeError("discord down")):
                out.append(("alert broken", call(m.alert_failing)))
            with mock.patch.object(registry, "refresh", side_effect=RuntimeError("boom")):
                out.append(("registry unreadable", call(m.all_jobs, True)))
        out.append(("scheduler.log", (m.LOG_DIR / "scheduler.log").read_text()))
        self.check("scheduler", out)

    # --- render-services.py ---------------------------------------------------------------------------------------
    def test_render_services(self):
        m = load("render_services_golden", "render-services.py")
        m.AGENTS, m.RENDERED, m.DEFAULT_PATH = self.tmp / "LaunchAgents", self.tmp / "state" / "rendered.json", "/usr/bin:/bin"
        cli = mock.patch.object(hubconf, "cli_folder", lambda: self.tmp / "shared" / "cli")   # never the live hub's
        cli.start()
        self.addCleanup(cli.stop)
        m.AGENTS.mkdir()
        m.RENDERED.parent.mkdir()
        ws = self.tmp / "ws"
        ws.mkdir()
        index = {"v": 1, "errors": {}, "modules": {}, "projects": {"demo": {
            "name": "demo", "paths": {"workspace": str(ws)}, "services": [
                {"name": "web", "run": "/bin/echo hi", "keepalive": True, "log": "web.log"},
                {"name": "worker", "run": "/bin/echo w", "keepalive": True},
                {"name": "once", "run": "/bin/echo o", "interval_s": 600},
                {"name": "broken", "run": "no-such-cmd-golden --flag"},
                {"name": "failboot", "run": "/bin/echo f"},
                {"name": "ghost", "run": "/bin/echo g"}]}}}
        (m.AGENTS / "local.demo.once.plist").write_bytes(plistlib.dumps(
            {"Label": "local.demo.once", "ProgramArguments": ["/bin/echo", "old"], "RunAtLoad": True}))
        m.RENDERED.write_text(json.dumps(["local.demo.old", "local.demo.web"]))
        loaded, alerts = set(), []

        def launchctl(*args):
            op = args[0]
            label = pathlib.Path(args[2]).stem if op == "bootstrap" else args[1].rsplit("/", 1)[-1]
            if op == "print":
                if label in loaded and label != "local.demo.ghost":
                    state = "not running" if label == "local.demo.worker" else "running"
                    return CP(args, 0, f"x = {{\n\tstate = {state}\n}}\n", "")
                return CP(args, 113, "", "")
            if op == "bootstrap":
                if label == "local.demo.failboot":
                    return CP(args, 5, "", "Bootstrap failed: 5: Input/output error\n")
                loaded.add(label)
            elif op == "bootout":
                loaded.discard(label)
            return CP(args, 0, "", "")
        import alert
        out = []

        def main(label, *argv):
            with mock.patch.object(sys, "argv", ["render-services.py", *argv]):
                out.append((label, call(m.main)))
        with mock.patch.object(m, "launchctl", launchctl), mock.patch.object(m.registry, "refresh", lambda: index), \
                mock.patch.object(alert, "once", lambda *a: alerts.append(a)), mock.patch("time.sleep"):
            with mock.patch.object(hubconf, "service_manager", lambda: None):
                main("no service manager")
            with mock.patch.object(hubconf, "service_manager", lambda: "launchd"):
                out.append(("plan", "\n".join(c.line() for c in m.plan(index))))
                main("dry-run", "--dry-run")
                main("apply")
                main("again")
                main("only one label, dry-run", "--dry-run", "--only", "local.demo.web")
            out.append(("alert.once", "\n".join(repr(a) for a in alerts)))
            out.append(("rendered", m.RENDERED.read_text()))

            # systemd: the same plan in unit files; enable fails for one service
            import systemd_units
            units = self.tmp / "units"
            units.mkdir()
            (units / "local.demo.once.timer").write_text("[Timer]\nOnUnitActiveSec=60\n")
            m.RENDERED.write_text("[]")

            def systemctl(*args):
                if args[:1] == ("enable",) and "web" in args[-1]:
                    return CP(args, 1, "", "Failed to enable unit: Unit file local.demo.web.service is masked.\n")
                return CP(args, 0, "inactive\n" if args[0] == "is-active" else "", "")
            sd_index = copy.deepcopy(index)
            sd_index["projects"]["demo"]["services"] = [s for s in sd_index["projects"]["demo"]["services"]
                                                        if s["name"] in ("web", "once")]
            with mock.patch.object(hubconf, "service_manager", lambda: "systemd"), \
                    mock.patch.object(systemd_units, "UNIT_DIR", units), \
                    mock.patch.object(systemd_units, "systemctl", systemctl), \
                    mock.patch.object(m.registry, "refresh", lambda: sd_index):
                out.append(("systemd plan", "\n".join(c.line() for c in m.plan(sd_index))))
                main("systemd apply")
        self.check("render-services", out)

    # --- render-project-settings.py -------------------------------------------------------------------------------
    def test_render_project_settings(self):
        m = load("render_settings_golden", "render-project-settings.py")
        m.RENDERED = self.tmp / "state" / "rendered-settings"
        sandbox = {"enabled": True, "filesystem": {"allowWrite": ["/rt", "/bus"], "denyRead": ["/other"]},
                   "network": {"allowedDomains": ["a.example", "b.example", "c.example"]}}
        rules = {"deny": ["Read(/other/**)", "Edit(/hub/**)"], "allow": ["Bash(agenthub send:*)"]}

        def desired(name, doc, index):
            return copy.deepcopy(sandbox), list(rules["deny"]), list(rules["allow"])

        def git(ws, *a):
            subprocess.run(["git", "-C", str(ws), *a], check=True, capture_output=True,
                           env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})

        def project(name, **extra):
            ws = self.tmp / "ws" / name
            ws.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", "--template=", str(ws)], check=True, capture_output=True,
                           env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
            (ws / ".git" / "hooks").mkdir(exist_ok=True)
            return ws, {"name": name, "paths": {"workspace": str(ws)}, **extra}
        alpha, a_doc = project("alpha", enforce={"sandbox": True}, gate="strict")
        beta, b_doc = project("beta", gate="strict")
        (beta / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\necho own hook\n")
        gamma, g_doc = project("gamma", gate="strict")
        git(gamma, "config", "core.hooksPath", ".husky")
        index = {"v": 1, "errors": {}, "modules": {}, "projects": {"alpha": a_doc, "beta": b_doc, "gamma": g_doc}}
        out = []

        def main(label, *argv):
            with mock.patch.object(sys, "argv", ["render-project-settings.py", *argv]):
                out.append((label, call(m.main)))

        def plan(label):
            out.append((label, "\n".join(c.line() for c in m.plan(index)) or "(none)"))
        settings = alpha / ".claude" / "settings.local.json"
        with mock.patch.object(m, "desired", desired), mock.patch.object(m.registry, "refresh", lambda: index), \
                mock.patch.object(m, "desired_env", lambda name, doc, index: {"UV_CACHE_DIR": "/cache/uv"}), \
                mock.patch.object(m.runtime_claude, "platform_hooks",   # the command's form differs by OS
                                  lambda: {"SessionStart": [{"hooks": [{"type": "command", "command": "hub-event"}]}]}):
            plan("plan, first")
            main("report", "--report")
            main("write")
            out.append(("alpha pre-commit", (alpha / ".git" / "hooks" / "pre-commit").read_text()))
            out.append(("alpha settings", settings.read_text()))
            plan("plan, after write")
            cur = json.loads(settings.read_text())
            cur["sandbox"] = {"enabled": False}
            cur["permissions"]["deny"] = cur["permissions"]["deny"][1:]
            settings.write_text(json.dumps(cur))
            plan("plan, after drift")
            main("report, drift", "--report")
            main("write, drift")
            rules["allow"] = rules["allow"] + ["Bash(agenthub merge-branch:*)"]
            plan("plan, policy changed")
            main("only alpha", "--only", "alpha")
            a_doc.pop("enforce")
            a_doc.pop("gate")
            plan("plan, enforce and gate off")
            main("enforce and gate off")
            out.append(("alpha settings", settings.read_text()))
        self.check("render-project-settings", out)

    # --- appliers.py ----------------------------------------------------------------------------------------------
    def test_appliers(self):
        import appliers
        bad = self.tmp / "bad.py"
        bad.write_text("def plan(index=None):\n    return []\n")
        with mock.patch.dict(appliers.APPLIERS, {"broken": str(bad)}):
            try:
                appliers.load("broken")
                text = "(loaded)"
            except appliers.MissingFunctions as e:
                text = str(e)
            finally:
                appliers._loaded.pop("broken", None)
        self.check("appliers", [("not an applier", text)])


if __name__ == "__main__":
    sys.exit(unittest.main())
