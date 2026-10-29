"""Linux 的服務管理器(bin/systemd_units.py):平台產生的 launchd plist → systemd user unit;render-services 在 systemd 上
plan / apply 的樣子(systemctl 是假的,只記下叫了什麼)。真的 systemd 上的驗證在 docs/design/tool-agnostic.md 第 6 步。

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
        self.assertIn('ExecStart="/bin/echo" "cost $$5"', f["local.x.tick.service"], "ExecStart 的 $ 要跳脫")
        self.assertNotIn("[Install]", f["local.x.tick.service"], "timer 叫它,service 本身不 enable")
        self.assertIn("OnActiveSec=1\nOnUnitActiveSec=900", f["local.x.tick.timer"], "載入就跑一次(RunAtLoad),之後每 900 秒")
        self.assertEqual(systemd_units.target({"Label": "local.x.tick", "StartInterval": 900}), "local.x.tick.timer")

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
        self.assertFalse(self.calls, "plan 不動 systemd")
        self.m.apply(self.index)
        unit = (self.tmp / "units" / "local.demo.web.service").read_text()
        self.assertIn('ExecStart="/bin/echo" "hi"', unit)
        self.assertIn(("enable", "--now", "local.demo.web.service"), self.calls)
        self.assertEqual(self.m.plan(self.index), [], "套用完再 plan 是空的")
        self.index["projects"]["demo"]["services"] = []
        self.assertEqual([c.op for c in self.m.plan(self.index)], ["remove"])
        self.m.apply(self.index)
        self.assertFalse((self.tmp / "units" / "local.demo.web.service").exists())


if __name__ == "__main__":
    unittest.main()
