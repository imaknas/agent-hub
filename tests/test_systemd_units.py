"""The Linux service manager (bin/systemd_units.py): the launchd plists the platform generates → systemd user units;
what render-services' plan / apply look like on systemd (systemctl is fake, it only records what was called).
Verification on a real systemd is in docs/design/tool-agnostic.md step 6.

    bin/py -m unittest discover -s tests
"""
import importlib.util
import pathlib
import plistlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import systemd_units  # noqa: E402


class Files(unittest.TestCase):
    def test_keepalive_service(self):
        f = systemd_units.files({"Label": "local.demo.web", "ProgramArguments": ["/usr/bin/node", "server.js", "--x=a b"],
                                 "WorkingDirectory": "/w", "EnvironmentVariables": {"PATH": "/usr/bin", "MSG": '50% "ok" $HOME'},
                                 "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30,
                                 "StandardOutPath": "/w/web.log", "StandardErrorPath": "/w/web.log"})
        self.assertEqual(list(f), ["local.demo.web.service"])
        self.assertEqual(f["local.demo.web.service"], "\n".join([
            "[Unit]", "Description=agent-hub local.demo.web", "", "[Service]", "Type=simple",
            'ExecStart="/usr/bin/node" "server.js" "--x=a b"', "WorkingDirectory=/w",
            'Environment="MSG=50%% \\"ok\\" $HOME"', 'Environment="PATH=/usr/bin"',
            "StandardOutput=append:/w/web.log", "StandardError=append:/w/web.log", "Restart=always", "RestartSec=30",
            "", "[Install]", "WantedBy=default.target"]) + "\n")

    def test_interval_job_is_a_timer(self):
        f = systemd_units.files({"Label": "local.x.tick", "ProgramArguments": ["/bin/echo", "cost $5"], "StartInterval": 900,
                                 "RunAtLoad": True})
        self.assertEqual(sorted(f), ["local.x.tick.service", "local.x.tick.timer"])
        self.assertIn("Type=oneshot", f["local.x.tick.service"])
        self.assertIn('ExecStart="/bin/echo" "cost $$5"', f["local.x.tick.service"], "$ in ExecStart is escaped")
        self.assertNotIn("[Install]", f["local.x.tick.service"],
                         "the timer calls it, the service itself is not enabled")
        self.assertIn("OnActiveSec=1\nOnUnitActiveSec=900", f["local.x.tick.timer"],
                      "runs once on load (RunAtLoad), then every 900 seconds")
        self.assertEqual(systemd_units.target({"Label": "local.x.tick", "StartInterval": 900}), "local.x.tick.timer")

    def test_a_tick_doesnt_take_what_it_started_to_outlive_it(self):
        """Dispatch and the scheduler are interval units, and start runs that outlive the tick in a new session: systemd's
        default (KillMode=control-group) would kill them when the oneshot ends, a new session or not. An always-running
        service keeps the default: what it starts goes when it is stopped or restarted."""
        import yaml
        dispatch = next(s for s in yaml.safe_load((ROOT / "projects" / "brunna.yaml").read_text())["services"]
                        if s["name"] == "mailbox-dispatch")
        self.assertEqual(dispatch["interval_s"], 60)
        tick = systemd_units.files({"Label": "local.x.tick", "ProgramArguments": ["/bin/true"], "StartInterval": 60})
        self.assertIn("KillMode=process", tick["local.x.tick.service"].splitlines())
        for label in ("local.agent-hub-scheduler",):
            plist = plistlib.loads((ROOT / "launchd" / f"{label}.plist").read_text().replace("{{hub}}", "/hub").encode())
            self.assertIn("KillMode=process", systemd_units.files(plist)[f"{label}.service"].splitlines())
        web = systemd_units.files({"Label": "local.x.web", "ProgramArguments": ["/bin/true"], "KeepAlive": True})
        self.assertNotIn("KillMode", web["local.x.web.service"])

    def test_core_templates_translate(self):
        for label in ("local.agent-hub-scheduler", "local.agent-hub-scheduler-watchdog"):
            plist = plistlib.loads((ROOT / "launchd" / f"{label}.plist").read_text().replace("{{hub}}", "/hub").encode())
            f = systemd_units.files(plist)
            self.assertEqual(sorted(f), [f"{label}.service", f"{label}.timer"], label)
            self.assertIn("/hub/bin/", f[f"{label}.service"])


def load_render_services():
    spec = importlib.util.spec_from_file_location("rs_systemd", ROOT / "bin" / "render-services.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class RenderServicesOnSystemd(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.m = load_render_services()
        self.calls = []
        self.saved = (systemd_units.UNIT_DIR, systemd_units.systemctl, self.m.hubconf.service_manager, self.m.RENDERED)
        systemd_units.UNIT_DIR = self.tmp / "units"
        systemd_units.systemctl = lambda *a: self.calls.append(a) or subprocess.CompletedProcess(a, 0, "active\n", "")
        self.m.hubconf.service_manager = lambda: "systemd"
        self.m.RENDERED = self.tmp / "rendered.json"
        (self.tmp / "ws").mkdir()
        self.index = {"v": 1, "errors": {}, "projects": {"demo": {
            "name": "demo", "paths": {"workspace": str(self.tmp / "ws")},
            "services": [{"name": "web", "run": "/bin/echo hi", "keepalive": True, "log": "web.log"}]}}}

    def tearDown(self):
        systemd_units.UNIT_DIR, systemd_units.systemctl, self.m.hubconf.service_manager, self.m.RENDERED = self.saved

    def test_plan_apply_then_nothing_left(self):
        first = self.m.plan(self.index)
        self.assertEqual([c.op for c in first], ["add"])
        self.assertFalse(self.calls, "plan does not touch systemd")
        self.m.apply(self.index)
        unit = (self.tmp / "units" / "local.demo.web.service").read_text()
        self.assertIn('ExecStart="/bin/echo" "hi"', unit)
        self.assertIn(("enable", "--now", "local.demo.web.service"), self.calls)
        self.assertEqual(self.m.plan(self.index), [], "plan after apply is empty")
        self.index["projects"]["demo"]["services"] = []
        self.assertEqual([c.op for c in self.m.plan(self.index)], ["remove"])
        self.m.apply(self.index)
        self.assertFalse((self.tmp / "units" / "local.demo.web.service").exists())


if __name__ == "__main__":
    unittest.main()
