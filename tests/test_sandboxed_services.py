"""A sandboxed project's own services and command jobs run isolated (bin/sandboxed-run → isolation.py → the OS's
adapter: seatbelt.service_profile on macOS, landlock.py on Linux), never unsandboxed: a manifest.update adds them
without the owner's approval, so they must not get more than the project's sessions (10-31, Linux the same day).

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
import isolation  # noqa: E402
import landlock  # noqa: E402
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
        with mock.patch.object(isolation, "can_sandbox", lambda: None):
            plist, problem = render_services.render("proj", self.doc, self.doc["services"][0])
        self.assertIsNone(problem)
        args = plist["ProgramArguments"]
        self.assertTrue(args[1].endswith("sandboxed-run"), args)
        i = args.index("--")
        self.assertEqual(args[2:i], ["--project", "proj", "--cwd", str(self.ws), "--port", "18123"])
        self.assertEqual(args[i + 1:], [str(self.ws / "bin" / "serve.sh")])

    def test_unsandboxed_and_exempt_services_are_unchanged(self):
        for doc in ({**self.doc, "enforce": {}}, self.doc):
            with mock.patch.object(isolation, "can_sandbox", lambda: None), \
                    mock.patch.object(isolation.hubconf, "get",
                                      lambda k, d=None: ["proj"] if k == "service_sandbox_exempt" else d):
                plist, _ = render_services.render("proj", doc, doc["services"][0])
            self.assertEqual(plist["ProgramArguments"], [str(self.ws / "bin" / "serve.sh")])

    def test_no_adapter_means_not_rendered(self):
        with mock.patch.object(isolation, "can_sandbox", lambda: "this system has no Seatbelt (linux)"):
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
    def test_refuses_without_an_adapter_and_never_runs_the_command(self):
        with mock.patch.object(isolation, "can_sandbox", lambda: "this system has no Seatbelt (linux)"), \
                mock.patch.object(os, "execv") as ex, redirect_stderr(io.StringIO()) as err:
            rc = sandboxed_run.main(["--project", "proj", "--cwd", "/", "--", "true"])
        self.assertEqual(rc, 78)
        ex.assert_not_called()
        self.assertIn("never runs unsandboxed", err.getvalue())

    def test_unknown_project_is_refused(self):
        with mock.patch.object(isolation, "can_sandbox", lambda: None), \
                mock.patch.object(sandboxed_run.hubconf, "projects", lambda: {}), \
                mock.patch.object(os, "execv") as ex, redirect_stderr(io.StringIO()):
            self.assertEqual(sandboxed_run.main(["--project", "nope", "--cwd", "/", "--", "true"]), 78)
        ex.assert_not_called()


class Adapter(unittest.TestCase):
    def test_picks_the_adapter_for_this_os(self):
        with mock.patch.object(isolation.sys, "platform", "darwin"):
            self.assertEqual(isolation.adapter()[0], "seatbelt")
        with mock.patch.object(isolation.sys, "platform", "linux"):
            self.assertEqual(isolation.adapter()[0], "landlock")
        with mock.patch.object(isolation.sys, "platform", "win32"):
            self.assertIsNone(isolation.adapter())
            self.assertIn("no sandbox adapter", isolation.can_sandbox())

    def test_landlock_refuses_an_old_kernel(self):
        with mock.patch.object(landlock.sys, "platform", "linux"), \
                mock.patch.object(landlock.platform, "machine", lambda: "x86_64"), \
                mock.patch.object(landlock, "abi", lambda: 3):
            self.assertIn("ABI is 3", landlock.can_sandbox())

    def test_exec_service_refuses_when_the_adapter_cant(self):
        with mock.patch.object(isolation.sys, "platform", "linux"), \
                mock.patch.object(landlock, "can_sandbox", lambda: "no Landlock here"), \
                mock.patch.object(landlock, "exec_service") as ex:
            with self.assertRaises(OSError):
                isolation.exec_service({}, [], None, ["true"])
        ex.assert_not_called()


class LandlockPlan(Fixture):
    """The deny-list policy → Landlock's allow-only rules: a denied path is cut out by allowing its siblings."""

    def pol(self, other, cred):
        return {"workspace": self.ws, "write": [str(self.tmp / "bus")], "other_projects": [other],
                "deny_read": [str(self.tmp / "copies" / "*" / "memory")], "credentials": [self.tmp / "home" / ".ssh"],
                "credential_files": [cred]}

    def plan(self):
        home = self.tmp / "home"
        for d in (home / ".ssh", home / ".config", self.tmp / "copies" / "a" / "memory", self.tmp / "other"):
            d.mkdir(parents=True)
        (home / ".netrc").write_text("x")
        (home / ".gitconfig").write_text("x")
        (home / "link-to-ssh").symlink_to(home / ".ssh")
        (self.tmp / "copies" / "a" / "STATE.md").write_text("x")
        with mock.patch.object(landlock.hubconf, "p", lambda k, d=None: None), \
                mock.patch.object(landlock.hubconf, "STATE", self.tmp / "state"), \
                mock.patch.object(landlock.hubconf, "BUS_DIR", self.tmp / "bus"):
            deny = landlock.denied(self.pol(self.tmp / "other", home / ".netrc"))
            return landlock.read_roots(deny, self.tmp), landlock.plan(self.pol(self.tmp / "other", home / ".netrc"),
                                                                     [self.rt, self.ws], 18123)

    def test_denied_paths_are_cut_out(self):
        read, pl = self.plan()
        home = self.tmp / "home"
        self.assertIn(home / ".config", read)
        self.assertIn(home / ".gitconfig", read)
        self.assertIn(self.tmp / "copies" / "a" / "STATE.md", read)
        self.assertIn(self.ws, read)
        for p in (home, home / ".ssh", home / ".netrc", home / "link-to-ssh", self.tmp / "other",
                  self.tmp / "copies", self.tmp / "copies" / "a", self.tmp / "copies" / "a" / "memory", self.tmp):
            self.assertNotIn(p, read)
        self.assertEqual(pl["bind"], [18123])
        self.assertIn(self.rt, pl["write"])
        self.assertIn(self.tmp / "bus", pl["write"])

    def test_no_port_no_bind_rule(self):
        with mock.patch.object(landlock.hubconf, "p", lambda k, d=None: None):
            pl = landlock.plan(self.pol(self.tmp / "other", self.tmp / "cred"), [self.ws])
        self.assertEqual(pl["bind"], [])


# what the real run checks, from a shell and from a child python (rules are inherited)
PROBE = """
touch "$RT/a" && echo rt-write
touch "$WS/a" && echo ws-write
touch "$BUS/a" && echo bus-write
touch "$TOP/outside" 2>/dev/null || echo outside-write-blocked
cat "$OTHER/secret" >/dev/null 2>&1 || echo other-blocked
cat "$CRED" >/dev/null 2>&1 || echo cred-blocked
cat /etc/hostname >/dev/null && echo system-read
python3 - <<'PY'
import socket
def bind(port):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port)); return "ok"
    except OSError as e:
        return "blocked" if e.errno == 13 else str(e)
    finally:
        s.close()
print("bind-declared-" + bind(int(__import__("os").environ["PORT"])))
print("bind-other-" + bind(int(__import__("os").environ["PORT"]) + 1))
try:
    socket.socket(socket.AF_UNIX).close(); print("unix-ok")
except OSError:
    print("unix-blocked")
a, b = socket.socketpair(); print("socketpair-ok")
PY
"""

# runs sandboxed-run's main() in a child: the registry and hub.json's credentials replaced with the fixture's
RUNNER = """
import importlib.machinery, importlib.util, pathlib, sys, json
args = json.loads(sys.argv[1])
sys.path.insert(0, args["bin"])
import hubconf, landlock, policy
policy.WRITE_CACHES = []   # exec_service would create ~/.npm and the like: no writing into HOME from a test
landlock.TMP = []   # the fixture lives in /tmp: a writable /tmp would make every path in it readable too
hubconf.projects = lambda: args["projects"]
real_get = hubconf.get
hubconf.get = lambda k, d=None: [args["cred"]] if k == "credentials.files" else real_get(k, d)
loader = importlib.machinery.SourceFileLoader("sr", args["bin"] + "/sandboxed-run")
spec = importlib.util.spec_from_loader("sr", loader); m = importlib.util.module_from_spec(spec); loader.exec_module(m)
sys.exit(m.main(["--project", "proj", "--cwd", args["ws"], "--port", str(args["port"]), "--", "/bin/sh", "-c",
                 args["probe"]]))
"""


@unittest.skipUnless(sys.platform.startswith("linux") and landlock.can_sandbox() is None,
                     "needs Linux with Landlock ABI 4")
class LandlockRun(Fixture):
    def test_under_the_rules(self):
        other, bus = self.tmp / "other", pathlib.Path(os.path.realpath(hubconf_bus()))
        other.mkdir()
        (other / "secret").write_text("x")
        cred = self.tmp / "cred"
        cred.write_text("token")
        port = 18125
        args = {"bin": str(BIN), "ws": str(self.ws), "cred": str(cred), "port": port, "probe": PROBE,
                "projects": {"proj": self.doc,
                             "other": {"name": "other", "paths": {"workspace": str(other)}}}}
        # no runtime in the fixture (it would be under services_root): the workspace stands in for it
        env = {**os.environ, "RT": str(self.ws), "WS": str(self.ws), "BUS": str(bus), "TOP": str(self.tmp),
               "OTHER": str(other), "CRED": str(cred), "PORT": str(port)}
        r = subprocess.run([sys.executable, "-c", RUNNER, __import__("json").dumps(args)], capture_output=True,
                           text=True, env=env)
        self.assertEqual(r.stdout.split(), ["rt-write", "ws-write", "bus-write", "outside-write-blocked",
                                            "other-blocked", "cred-blocked", "system-read", "bind-declared-ok",
                                            "bind-other-blocked", "unix-blocked", "socketpair-ok"], r.stderr)


def hubconf_bus():
    sys.path.insert(0, str(BIN))
    import hubconf
    return hubconf.BUS_DIR


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
