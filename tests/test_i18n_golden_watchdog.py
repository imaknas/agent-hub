"""E1 golden (docs/design/i18n.md) for bin/scheduler-watchdog.py: every text it sends or logs, on zh-Hant.

The watchdog runs on the system Python with no platform modules, so it is loaded in-process from a temp hub (its paths
come from AGENT_HUB_ROOT at import); Discord, launchctl and ps are replaced by canned answers.

    bin/py -m unittest tests.test_i18n_golden_watchdog
"""
import datetime
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "i18n-golden"
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"


class Watchdog(unittest.TestCase):
    maxDiff = None

    def load(self, tmp: pathlib.Path, alerts: bool = True, secrets: bool = True, lang: str = "zh-Hant"):
        for d in ("hub/.state", "hub/.logs", "config", "secrets"):
            (tmp / d).mkdir(parents=True, exist_ok=True)
        cfg = {"language": {"ui": lang, "content": lang}}
        if alerts:
            cfg["alerts"] = {"discord": {}}
        (tmp / "config" / "hub.json").write_text(json.dumps(cfg))
        if secrets:
            (tmp / "secrets" / "discord-token").write_text("t")
            (tmp / "secrets" / "discord-alert-channel").write_text("c")
        env = {"AGENT_HUB_ROOT": str(tmp / "hub"), "AGENT_HUB_CONFIG": str(tmp / "config"),
               "AGENT_HUB_SECRETS": str(tmp / "secrets"), "AGENT_HUB_LANG_UI": ""}
        with mock.patch.dict(os.environ, env):
            spec = importlib.util.spec_from_file_location("watchdog_golden", ROOT / "bin" / "scheduler-watchdog.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        return mod

    def fake_run(self, launchd_ok: bool):
        def run(cmd, **kw):
            if cmd[0] == "/usr/bin/curl":
                return subprocess.CompletedProcess(cmd, 0, "200", "")
            if cmd[0] == "/bin/launchctl":
                out = "state = running\npid = 123\nlast exit code = 0\n"
                return subprocess.CompletedProcess(cmd, 0 if launchd_ok else 113, out if launchd_ok else "", "")
            if cmd[0] == "/bin/ps":
                return subprocess.CompletedProcess(cmd, 0, "  PID ELAPSED COMMAND\n 99 01:00 python3 bin/scheduler.py\n", "")
            if cmd[0] == "systemctl":
                return subprocess.CompletedProcess(cmd, 0, "active", "")
            raise AssertionError(cmd)
        return run

    def scenario(self, tmp: pathlib.Path) -> str:
        out = []
        mod = self.load(tmp)
        (tmp / "hub" / ".logs" / "scheduler.log").write_text("a\nb\nlast line\n")
        hb = tmp / "hub" / ".state" / "scheduler-heartbeat"
        hb.write_text("")
        old = datetime.datetime(2026, 10, 30, 8, 0).timestamp()
        os.utime(hb, (old, old))
        sent = []

        def send(text):
            sent.append(text)
            return True
        now = datetime.datetime(2026, 10, 30, 10, 0)

        class FakeDT(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return now
        with mock.patch.object(mod.subprocess, "run", side_effect=self.fake_run(True)), \
                mock.patch.object(mod.sys, "platform", "darwin"), mock.patch.object(mod.os, "getuid", return_value=501), \
                mock.patch.object(mod.datetime, "datetime", FakeDT):
            out.append(("diagnose launchd up", mod.diagnose()))
            with mock.patch.object(mod.subprocess, "run", side_effect=self.fake_run(False)):
                out.append(("diagnose launchd missing", mod.diagnose()))
            with mock.patch.object(mod.sys, "platform", "linux"):
                out.append(("diagnose linux", mod.diagnose()))
            with mock.patch.object(mod, "send", side_effect=send), mock.patch.object(mod, "diagnose", return_value="<diag>"):
                for _ in range(2):          # stale twice → alert
                    mod.main()
                now = datetime.datetime(2026, 10, 30, 17, 0)
                mod.main()                  # reminder after REMIND_H
                os.utime(hb, (now.timestamp(), now.timestamp()))
                mod.main()                  # recovered
                hb.unlink()
                (mod.WATCH_STATE).unlink(missing_ok=True)
                mod.main(), mod.main()      # never ticked
                with mock.patch.object(mod.sys, "argv", ["x", "--test"]):
                    with mock.patch("builtins.print") as p:
                        mod.main()
                        out.append(("--test printed", " ".join(str(a) for c in p.call_args_list for a in c.args)))
            out.append(("sent", "\n---\n".join(sent)))
            for name, kw in (("no alerts", {"alerts": False}), ("no secrets", {"secrets": False})):
                sub = tmp / name.replace(" ", "-")
                m2 = self.load(sub, **kw)
                with mock.patch("builtins.print") as p, mock.patch.object(m2.datetime, "datetime", FakeDT):
                    rc = m2.main()
                printed = " ".join(str(a) for c in p.call_args_list for a in c.args)
                out.append((name, f"exit {rc}; printed: {printed}"))
                if m2.LOG.exists():
                    out.append((f"{name} log", m2.LOG.read_text()))
            with mock.patch.object(mod.subprocess, "run", side_effect=lambda cmd, **kw: subprocess.CompletedProcess(
                    cmd, 0, "500", "boom")):
                mod.send("x")
            (mod.SECRETS_DIR / "discord-token").unlink()
            mod.send("x")
        out.append(("log", mod.LOG.read_text()))
        text = "\n".join(f"=== {k}\n{v}" for k, v in out) + "\n"
        text = text.replace(str(tmp), "<TMP>")
        import re
        return re.sub(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?", "<TS>", text, flags=re.M)

    def test_texts(self):
        with tempfile.TemporaryDirectory() as d:
            text = self.scenario(pathlib.Path(d).resolve())
        f = GOLDEN / "scheduler-watchdog.txt"
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())


if __name__ == "__main__":
    sys.exit(unittest.main())
