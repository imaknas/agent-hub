"""Services and scheduled commands find `brunna` by name (docs/design/project-agent-calls.md D): a platform-owned folder
holding only the wrapper, last on every service's PATH and every scheduled command's, so nothing found today changes.

    bin/py -m unittest tests.test_cli_on_path
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import names  # noqa: E402
import systemd_units  # noqa: E402


def load(name, file):
    loader = importlib.machinery.SourceFileLoader(name, str(BIN / file))
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


rs = load("render_services_cli", "render-services.py")
scheduler = load("scheduler_cli", "scheduler.py")
cap_runner = load("cap_runner_cli", "cap-runner.py")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cli = self.tmp / "shared" / "cli"
        p = mock.patch.object(hubconf, "cli_folder", lambda: self.cli)
        p.start()
        self.addCleanup(p.stop)


class Wrapper(Base):
    def test_a_plain_executable_file_running_the_hubs_cli(self):
        self.assertTrue(hubconf.write_cli_wrapper())
        f = self.cli / names.current("cli")
        self.assertFalse(f.is_symlink())
        self.assertEqual(f.stat().st_mode & 0o777, 0o755)
        self.assertIn(f'exec "{hubconf.MAIN / "bin" / names.current("cli")}" "$@"', f.read_text())
        self.assertEqual(os.listdir(self.cli), [f.name])
        self.assertFalse(hubconf.write_cli_wrapper())   # as it should be: left alone

    def test_a_link_or_a_wrong_mode_is_replaced(self):
        self.cli.mkdir(parents=True)
        target = self.tmp / "elsewhere"
        target.write_text("keep")
        (self.cli / names.current("cli")).symlink_to(target)
        self.assertTrue(hubconf.write_cli_wrapper())
        self.assertFalse((self.cli / names.current("cli")).is_symlink())
        self.assertEqual(target.read_text(), "keep")
        (self.cli / names.current("cli")).chmod(0o777)
        self.assertTrue(hubconf.write_cli_wrapper())
        self.assertEqual((self.cli / names.current("cli")).stat().st_mode & 0o777, 0o755)

    def test_a_linked_folder_is_replaced(self):
        other = self.tmp / "other"
        other.mkdir()
        self.cli.parent.mkdir(parents=True)
        self.cli.symlink_to(other)
        hubconf.write_cli_wrapper()
        self.assertFalse(self.cli.is_symlink())
        self.assertEqual(os.listdir(other), [])


class Services(Base):
    def setUp(self):
        super().setUp()
        self.ws = self.tmp / "ws"
        (self.ws / "bin").mkdir(parents=True)
        (self.ws / "bin" / "serve.sh").write_text("#!/bin/sh\n")
        (self.ws / "bin" / "serve.sh").chmod(0o755)
        self.doc = {"name": "p", "paths": {"workspace": str(self.ws)}}
        rs._MANAGER_PATH.clear()
        self.addCleanup(rs._MANAGER_PATH.clear)

    def path_of(self, svc, manager="launchd"):
        with mock.patch.object(hubconf, "service_manager", lambda: manager):
            plist, problem = rs.render("p", self.doc, svc)
        self.assertIsNone(problem)
        return plist["EnvironmentVariables"]["PATH"]

    def test_every_kind_of_service_ends_with_the_folder(self):
        cases = {"own PATH": {"name": "a", "run": "bin/serve.sh", "env": {"PATH": "/opt/x/bin:/usr/bin"}},
                 "own PATH with the platform's": {"name": "b", "run": "sh -c true", "env": {"PATH": "$PLATFORM_PATH"}},
                 "by name": {"name": "c", "run": "sh -c true"},
                 "by path": {"name": "d", "run": "bin/serve.sh"}}
        for what, svc in cases.items():
            with self.subTest(what):
                self.assertTrue(self.path_of(svc).endswith(f":{self.cli}"))
        self.assertEqual(self.path_of(cases["own PATH"]), f"/opt/x/bin:/usr/bin:{self.cli}")
        self.assertEqual(self.path_of(cases["own PATH with the platform's"]), f"{hubconf.LAUNCHD_PATH}:{self.cli}")
        self.assertEqual(self.path_of(cases["by name"]), f"{rs.DEFAULT_PATH}:{self.cli}")

    def test_a_service_started_by_path_keeps_the_managers_own_path_before_it(self):
        svc = {"name": "d", "run": "bin/serve.sh"}
        self.assertEqual(self.path_of(svc), f"/usr/bin:/bin:/usr/sbin:/sbin:{self.cli}")
        with mock.patch.object(systemd_units, "default_path", lambda: "/usr/local/bin:/usr/bin"):
            self.assertEqual(self.path_of(svc, "systemd"), f"/usr/local/bin:/usr/bin:{self.cli}")

    def test_brunna_by_name_resolves_to_the_wrapper(self):
        hubconf.write_cli_wrapper()
        plist, problem = rs.render("p", self.doc, {"name": "e", "run": f"{names.current('cli')} glance --json"})
        self.assertIsNone(problem)
        self.assertEqual(plist["ProgramArguments"][0], str(self.cli / names.current("cli")))

    def test_systemds_default_comes_from_the_user_manager(self):
        cp = subprocess.CompletedProcess
        with mock.patch.object(systemd_units, "systemctl",
                               lambda *a: cp(a, 0, "LANG=C\nPATH=/usr/local/bin:/usr/bin:/bin\n", "")):
            self.assertEqual(systemd_units.default_path(), "/usr/local/bin:/usr/bin:/bin")
        for broken in (cp((), 1, "", "Failed to connect to bus"), cp((), 0, "LANG=C\n", "")):
            with mock.patch.object(systemd_units, "systemctl", lambda *a, r=broken: r):
                self.assertEqual(systemd_units.default_path(), systemd_units.FIXED_PATH)


class WaitForIdle(Base):
    def test_cap_runners_unit_waits_while_it_has_a_job(self):
        index = {"projects": {}, "modules": {"sandbox": {
            "name": "sandbox", "type": "module", "paths": {"workspace": str(self.tmp)},
            "services": [{"name": "cap-runner", "label": "local.x.cap-runner", "run": "/bin/echo"},
                         {"name": "other", "label": "local.x.other", "run": "/bin/echo"}]}}}
        for is_busy in (True, False):
            with self.subTest(busy=is_busy), mock.patch.object(rs, "busy", lambda p, s, b=is_busy: b and s["name"] == "cap-runner"):
                _want, problems, keep, _ = rs._desired(index)
                self.assertEqual(problems, [])
                self.assertEqual(keep, {"local.x.cap-runner"} if is_busy else set())
                self.assertEqual(rs.waiting, ["local.x.cap-runner"] if is_busy else [])

    def test_only_cap_runner_asks(self):
        self.assertFalse(rs.busy("p", {"name": "cap-runner"}))

    def test_busy_means_a_queued_or_running_job(self):
        ws = self.tmp / "proj"
        for state in ("done", "lost", "queued", "running"):
            with self.subTest(state=state):
                d = ws / ".hub-run" / "jobs" / "j1"
                d.mkdir(parents=True, exist_ok=True)
                (d / "state.json").write_text(json.dumps({"state": state}))
                with mock.patch.object(cap_runner, "eligible", lambda: {"proj": {"dirs": [ws]}}):
                    self.assertEqual(cap_runner.busy(), state in ("queued", "running"))


class Jobs(Base):
    def test_a_scheduled_command_finds_brunna_last_on_its_path(self):
        hubconf.write_cli_wrapper()
        early = self.tmp / "early"
        early.mkdir()
        cli = names.current("cli")
        log = self.tmp / "log"
        for path, want in (("/usr/bin:/bin", str(self.cli / cli)), (f"{early}:/usr/bin:/bin", str(early / cli))):
            (early / cli).unlink(missing_ok=True)
            if str(early) in path:
                (early / cli).write_text("#!/bin/sh\n")
                (early / cli).chmod(0o755)
            with self.subTest(path=path), open(log, "w") as f:
                env = {"PATH": path, "HOME": str(self.tmp)}
                self.assertEqual(scheduler.run_bounded(f"command -v {cli}", f, 1, env), 0)
            self.assertEqual(log.read_text().strip().splitlines()[-1], want)

    def test_the_prefix(self):
        self.assertEqual(hubconf.job_command("x y"), f'PATH="$PATH":{self.cli}; x y')


if __name__ == "__main__":
    unittest.main()
