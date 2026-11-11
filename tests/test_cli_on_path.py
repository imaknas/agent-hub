"""Services and scheduled commands find `brunna` by name (docs/design/project-agent-calls.md D): a platform-owned folder
holding only the wrapper, last on every service's PATH and every scheduled command's, so nothing found today changes.

    bin/py -m unittest tests.test_cli_on_path
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import shlex
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
        self.assertIn(f'exec {shlex.quote(str(hubconf.MAIN / "bin" / names.current("cli")))} "$@"', f.read_text())
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
        (self.ws / "bin" / "serve.sh").write_text('#!/bin/sh\nprintf %s "$PATH"\n')
        (self.ws / "bin" / "serve.sh").chmod(0o755)
        self.doc = {"name": "p", "paths": {"workspace": str(self.ws)}}

    def render(self, svc, doc=None):
        plist, problem = rs.render("p", doc or self.doc, svc)
        self.assertIsNone(problem)
        return plist

    def test_a_service_naming_its_path_or_started_by_name_gets_the_folder_last(self):
        cases = {"own PATH": ({"name": "a", "run": "bin/serve.sh", "env": {"PATH": "/opt/x/bin:/usr/bin"}},
                              f"/opt/x/bin:/usr/bin:{self.cli}"),
                 "own PATH with the platform's": ({"name": "b", "run": "sh -c true", "env": {"PATH": "$PLATFORM_PATH"}},
                                                  f"{hubconf.LAUNCHD_PATH}:{self.cli}"),
                 "by name": ({"name": "d", "run": "sh -c true"}, f"{rs.DEFAULT_PATH}:{self.cli}")}
        for what, (svc, want) in cases.items():
            with self.subTest(what):
                self.assertEqual(self.render(svc)["EnvironmentVariables"]["PATH"], want)

    def test_a_service_started_by_path_keeps_the_managers_path_when_it_starts(self):
        plist = self.render({"name": "e", "run": "bin/serve.sh", "env": {"X": "1"}})
        self.assertNotIn("PATH", plist["EnvironmentVariables"])   # the unit names none: a manager's change changes nothing
        self.assertEqual(plist["ProgramArguments"][:2], ["/bin/sh", "-c"])
        self.assertEqual(plist["ProgramArguments"][-1], str(self.ws / "bin" / "serve.sh"))
        # launchd and systemd always give one (a shell given none makes up its own)
        for path in ("/usr/bin:/bin:/usr/sbin:/sbin", "/usr/local/bin:/usr/bin:/bin"):
            with self.subTest(path=path):
                out = subprocess.run(plist["ProgramArguments"], env={"PATH": path}, capture_output=True, text=True,
                                     check=True)
                self.assertEqual(out.stdout, f"{path}:{self.cli}")

    def test_a_sandboxed_one_gets_it_around_its_sandbox(self):
        import isolation
        doc = {**self.doc, "enforce": {"sandbox": True}}
        with mock.patch.object(isolation, "can_sandbox", lambda: None):
            args = self.render({"name": "f", "run": "bin/serve.sh"}, doc)["ProgramArguments"]
        self.assertEqual(args[:2], ["/bin/sh", "-c"])
        self.assertEqual(args[3], "sh")
        self.assertTrue(any(a.endswith("sandboxed-run") for a in args[4:6]), args)

    def test_brunna_by_name_resolves_to_the_wrapper(self):
        hubconf.write_cli_wrapper()
        plist = self.render({"name": "g", "run": f"{names.current('cli')} glance --json"})
        self.assertEqual(plist["ProgramArguments"][0], str(self.cli / names.current("cli")))


class WaitForIdle(Base):
    def setUp(self):
        super().setUp()
        for name, value in (("WAITED", self.tmp / "waiting.json"),):
            p = mock.patch.object(rs, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(hubconf, "STATE", self.tmp / "state")
        p.start()
        self.addCleanup(p.stop)
        self.index = {"projects": {}, "modules": {"sandbox": {
            "name": "sandbox", "type": "module", "paths": {"workspace": str(self.tmp)},
            "services": [{"name": "cap-runner", "label": "local.x.cap-runner", "run": "/bin/echo"},
                         {"name": "other", "label": "local.x.other", "run": "/bin/echo"}]}}}

    def active(self, pid, jobs):
        f = self.tmp / "state" / "cap-runner" / "active.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"pid": pid, "jobs": jobs}))

    def test_cap_runners_unit_waits_while_it_has_a_job(self):
        for is_busy in (True, False):
            with self.subTest(busy=is_busy), mock.patch.object(rs, "busy", lambda p, s, b=is_busy: b and s["name"] == "cap-runner"):
                _want, problems, keep, _ = rs._desired(self.index)
                self.assertEqual(problems, [])
                self.assertEqual(keep, {"local.x.cap-runner"} if is_busy else set())
                self.assertEqual(rs.waiting, ["local.x.cap-runner"] if is_busy else [])

    def test_busy_is_the_running_runners_own_count(self):
        dead = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
        dead.wait()
        svc = {"name": "cap-runner"}
        self.assertFalse(rs.busy("sandbox", svc))           # no record
        for pid, jobs, want in ((os.getpid(), 1, True), (os.getpid(), 0, False), (dead.pid, 3, False)):
            with self.subTest(pid=pid, jobs=jobs):
                self.active(pid, jobs)
                self.assertEqual(rs.busy("sandbox", svc), want)
        (self.tmp / "state" / "cap-runner" / "active.json").write_text("{")
        self.assertFalse(rs.busy("sandbox", svc))
        self.active(os.getpid(), 1)
        self.assertFalse(rs.busy("p", svc))                 # only the module's cap-runner waits

    def test_after_two_hours_it_is_changed_anyway(self):
        self.active(os.getpid(), 1)
        rs._desired(self.index)
        rs.note_waiting()
        first = json.loads(rs.WAITED.read_text())["local.x.cap-runner"]
        rs._desired(self.index)
        rs.note_waiting()
        self.assertEqual(json.loads(rs.WAITED.read_text())["local.x.cap-runner"], first)   # since the first wait
        rs.WAITED.write_text(json.dumps({"local.x.cap-runner": first - rs.wait_limit_s() - 1}))
        _want, _problems, keep, _ = rs._desired(self.index)
        self.assertEqual((keep, rs.waiting, rs.overdue), (set(), [], ["local.x.cap-runner"]))
        rs.note_waiting()
        self.assertEqual(json.loads(rs.WAITED.read_text()), {})

    def test_cap_runner_notes_its_jobs_in_flight(self):
        active = self.tmp / "state" / "cap-runner" / "active.json"
        with mock.patch.object(cap_runner, "ACTIVE", active):
            r = cap_runner.Runner()
            r.note_active()
            self.assertEqual(json.loads(active.read_text()), {"pid": os.getpid(), "jobs": 0, "procs": []})
            r.queue.append(object())
            r.note_active()
            self.assertEqual(json.loads(active.read_text())["jobs"], 1)
            active.unlink()
            r.note_active()                                  # unchanged: not written again
            self.assertFalse(active.exists())


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
