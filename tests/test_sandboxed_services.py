"""A sandboxed project's own services and command jobs run under the platform's Seatbelt profile (bin/sandboxed-run,
seatbelt.service_profile), never unsandboxed: a manifest.update adds them without the owner's approval, so they must not
get more than the project's sessions (10-31).

    bin/py -m unittest tests.test_sandboxed_services
"""
import importlib.machinery
import importlib.util
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import registry  # noqa: E402
import seatbelt  # noqa: E402


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


render_services = load("render_services_sb", BIN / "render-services.py")
sandboxed_run = load("sandboxed_run_sb", BIN / "sandboxed-run")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws, self.rt = self.tmp / "ws", self.tmp / "rt"
        for d in (self.ws, self.rt):
            d.mkdir()
        (self.ws / "bin").mkdir()   # no runtime: the service runs in the workspace
        (self.ws / "bin" / "serve.sh").write_text("#!/bin/sh\n")
        (self.ws / "bin" / "serve.sh").chmod(0o755)
        self.doc = {"name": "proj", "paths": {"workspace": str(self.ws)},
                    "enforce": {"sandbox": True},
                    "services": [{"name": "viewer", "run": "bin/serve.sh", "keepalive": True,
                                  "expose": {"tailnet_https": 8445, "local_port": 18123}}],
                    "jobs": [{"name": "nightly", "at": "03:00", "run": "python3 x.py"},
                             {"name": "think", "at": "04:00", "kind": "agent"}]}


class Render(Fixture):
    def test_sandboxed_service_goes_through_sandboxed_run(self):
        with mock.patch.object(seatbelt, "can_sandbox", lambda: None):
            plist, problem = render_services.render("proj", self.doc, self.doc["services"][0])
        self.assertIsNone(problem)
        args = plist["ProgramArguments"]
        self.assertTrue(args[1].endswith("sandboxed-run"), args)
        i = args.index("--")
        self.assertEqual(args[2:i], ["--project", "proj", "--cwd", str(self.ws), "--port", "18123"])
        self.assertEqual(args[i + 1:], [str(self.ws / "bin" / "serve.sh")])

    def test_unsandboxed_and_exempt_services_are_unchanged(self):
        for doc in ({**self.doc, "enforce": {}}, self.doc):
            with mock.patch.object(seatbelt, "can_sandbox", lambda: None), \
                    mock.patch.object(seatbelt.hubconf, "get",
                                      lambda k, d=None: ["proj"] if k == "service_sandbox_exempt" else d):
                plist, _ = render_services.render("proj", doc, doc["services"][0])
            self.assertEqual(plist["ProgramArguments"], [str(self.ws / "bin" / "serve.sh")])

    def test_no_seatbelt_means_not_rendered(self):
        with mock.patch.object(seatbelt, "can_sandbox", lambda: "this system has no Seatbelt (linux)"):
            _, problem = render_services.render("proj", self.doc, self.doc["services"][0])
        self.assertIn("linux", problem or "")   # reported (in the UI language), so it is not rendered


class Jobs(Fixture):
    def jobs(self, doc):
        out, _, _ = registry.scheduler_jobs({"projects": {"proj": doc}}, set())
        return {j["name"]: j["command"] for j in out}

    def test_command_job_is_wrapped_agent_job_is_not(self):
        cmds = self.jobs(self.doc)
        self.assertIn("sandboxed-run --project proj --cwd", cmds["proj/nightly"])
        self.assertTrue(cmds["proj/nightly"].endswith("-- /bin/sh -c 'python3 x.py'"), cmds["proj/nightly"])
        self.assertNotIn("sandboxed-run", cmds["proj/think"])
        self.assertNotIn("sandboxed-run", self.jobs({**self.doc, "enforce": {}})["proj/nightly"])


class Launcher(unittest.TestCase):
    def test_refuses_without_seatbelt_and_never_runs_the_command(self):
        with mock.patch.object(seatbelt, "can_sandbox", lambda: "this system has no Seatbelt (linux)"), \
                mock.patch.object(os, "execv") as ex, redirect_stderr(io.StringIO()) as err:
            rc = sandboxed_run.main(["--project", "proj", "--cwd", "/", "--", "true"])
        self.assertEqual(rc, 78)
        ex.assert_not_called()
        self.assertIn("never runs unsandboxed", err.getvalue())

    def test_unknown_project_is_refused(self):
        with mock.patch.object(seatbelt, "can_sandbox", lambda: None), \
                mock.patch.object(sandboxed_run.hubconf, "projects", lambda: {}), \
                mock.patch.object(os, "execv") as ex, redirect_stderr(io.StringIO()):
            self.assertEqual(sandboxed_run.main(["--project", "nope", "--cwd", "/", "--", "true"]), 78)
        ex.assert_not_called()


class Profile(Fixture):
    def pol(self, other):
        return {"workspace": self.ws, "write": [str(self.tmp / "bus")], "other_projects": [other], "deny_read": [],
                "credentials": [], "credential_files": []}

    def test_port_rule_only_with_a_port(self):
        p = seatbelt.service_profile(self.pol(self.tmp / "other"), [self.rt, self.ws])
        self.assertNotIn("allow network-bind", p)
        p = seatbelt.service_profile(self.pol(self.tmp / "other"), [self.rt, self.ws], 18123)
        self.assertIn('(allow network-bind (local ip "*:18123"))', p)
        self.assertIn('(allow network-inbound (local ip "localhost:*"))', p)

    @unittest.skipUnless(seatbelt.can_sandbox() is None, "needs Seatbelt")
    def test_under_the_profile(self):
        other = self.tmp / "other"
        other.mkdir()
        (other / "secret").write_text("x")
        (self.tmp / "bus").mkdir()
        prof = seatbelt.service_profile(self.pol(other), [self.rt, self.ws], 18124)
        script = f"""
set -u
touch {self.rt}/a && echo rt-write
touch {self.ws}/a && echo ws-write
touch {self.tmp}/bus/a && echo bus-write
cat {other}/secret 2>/dev/null || echo other-blocked
"""
        # (writes outside are covered by the temp area here, which the profile allows; checked live on 10-31)
        r = subprocess.run([seatbelt.SANDBOX_EXEC, "-p", prof, "/bin/sh", "-c", script], capture_output=True, text=True,
                           cwd=self.rt)
        self.assertEqual(r.stdout.split(), ["rt-write", "ws-write", "bus-write", "other-blocked"],
                         r.stderr)


if __name__ == "__main__":
    unittest.main()
