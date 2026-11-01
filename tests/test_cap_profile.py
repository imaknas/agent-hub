"""The Seatbelt profile the platform runs things under (bin/seatbelt.py) really blocks escapes
(docs/design/capabilities.md).

Each check runs under sandbox-exec and looks only at success or failure; no secret content is printed. The test makes
its own instance in a temporary folder (workspace_root, services_root, platform state, the bus, the secrets folder, a
credential file), so it runs under the gate's fresh HOME instead of skipping: the probe folder is created under that
workspace_root (the profile first blocks reading all of workspace_root, then allows its own folder; that overlap is
what is tested). The instance lives in the temp area, which the profile lets everything write, so writes are probed
against HOME instead. Skipped when not on a mac, or when already inside a sandbox (a second layer cannot be applied).

Each "should block" check first runs once outside the sandbox: one that already fails outside (e.g. a fresh HOME has no
~/.ssh) does not count.
Nothing here starts launchctl or opens an app: test_launch_and_processes stays skipped (a test may not reach this
machine's launchd session or desktop); the process checks that stay inside the test run are in test_processes.
"""
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

try:
    import hubconf  # noqa: E402
    import policy  # noqa: E402  needs PyYAML
    import seatbelt  # noqa: E402
except ImportError:
    policy = None

HOME = pathlib.Path.home()
# a Keychain item this machine always has (only checks it exists, never reads the value)
KEYCHAIN_ITEM = "Claude Code-credentials"


def can_sandbox() -> bool:
    if sys.platform != "darwin" or policy is None or not shutil.which("sandbox-exec"):
        return False
    r = subprocess.run(["sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"], capture_output=True)
    return r.returncode == 0


def in_tmp(d: pathlib.Path) -> bool:
    return any(d.resolve().is_relative_to(t) for t in seatbelt.TMP)


def ok(cmd: str, cwd, profile=None, env=None) -> bool:
    argv = ["/bin/bash", "-c", cmd]
    if profile:
        argv = ["sandbox-exec", "-f", str(profile)] + argv
    return subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          timeout=60).returncode == 0


@unittest.skipUnless(can_sandbox(), "needs a mac with sandbox-exec, and not being inside a sandbox")
class CapProfile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tag = uuid.uuid4().hex[:8]
        # resolved: Seatbelt matches real paths (/var is a link to /private/var)
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="cap-profile-")).resolve()
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        inst = {d: cls.tmp / d for d in ("workspace", "services", "state", "bus", "secrets", "creds", "git-origin",
                                          "copies")}
        for d in inst.values():
            d.mkdir()
        # something in each blocked place, so the control run outside the sandbox succeeds and the check counts
        (inst["services"] / "svc").mkdir()
        (inst["state"] / "registry").mkdir()
        (inst["bus"] / "bus.db").write_text("x")
        (inst["secrets"] / "a-key").write_text("x")
        (inst["creds"] / "token").write_text("x")
        cfg = {"workspace_root": str(inst["workspace"]), "services_root": str(inst["services"]),
               "bus_dir": str(inst["bus"]), "git_origin": str(inst["git-origin"]), "memory_copies": str(inst["copies"]),
               "credentials": {"files": [str(inst["creds"] / "token")]}}
        for p in (mock.patch.object(hubconf, "_cfg", cfg), mock.patch.object(hubconf, "STATE", inst["state"]),
                  mock.patch.object(hubconf, "BUS_DIR", inst["bus"]),
                  mock.patch.dict(os.environ, {"AGENT_HUB_SECRETS": str(inst["secrets"])})):
            p.start()
            cls.addClassCleanup(p.stop)
        cls.root = inst["workspace"]
        cls.ws = cls.root / f"zz-cap-probe-{tag}"
        cls.other = cls.root / f"zz-cap-probe-other-{tag}"
        for d in (cls.ws, cls.other):
            d.mkdir()
        (cls.other / "secret.txt").write_text("x")
        # outside the temp area (which the profile lets everything write): HOME, or the tree under test when HOME is a
        # temporary one; written only if the sandbox fails, and removed again
        cls.escape = next((d / f"zz-cap-escape-{tag}.txt" for d in (HOME, ROOT) if not in_tmp(d)), None)
        if cls.escape:
            cls.addClassCleanup(cls.escape.unlink, missing_ok=True)
        pol = policy.policy(cls.ws.name, {"name": cls.ws.name, "paths": {"workspace": str(cls.ws)}}, {"projects": {}})
        pol["other_projects"].append(cls.other)
        cls.pol = pol
        cls.profile = cls.tmp / "gpu.sb"
        cls.profile.write_text(seatbelt.profile(pol, "gpu"))
        # unix socket: the platform opens one listening itself, and checks whether the sandbox can reach it
        cls.sock_path = f"/private/tmp/zz-cap-{tag}.sock"
        cls.srv = socket.socket(socket.AF_UNIX)
        cls.srv.bind(cls.sock_path)
        cls.addClassCleanup(pathlib.Path(cls.sock_path).unlink, missing_ok=True)
        cls.addClassCleanup(cls.srv.close)
        cls.srv.listen(1)
        cls.sleeper = subprocess.Popen(["/bin/sleep", "60"])
        cls.addClassCleanup(cls.sleeper.kill)
        # call the Security framework directly, not through the security program: what is blocked is the Keychain
        # service, not just that program
        (cls.ws / "kc.py").write_text(
            "import ctypes, sys\n"
            "S = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')\n"
            "s = sys.argv[1].encode()\n"
            "sys.exit(0 if S.SecKeychainFindGenericPassword(None, len(s), s, 0, None, None, None, None) == 0 else 1)\n")

    def blocked(self, name: str, cmd: str, control: bool = True):
        with self.subTest(name):
            if control and not ok(cmd, self.ws):
                self.skipTest(f"{name}: already fails outside the sandbox, does not count")
            self.assertFalse(ok(cmd, self.ws, self.profile), f"{name} was not blocked")

    def allowed(self, name: str, cmd: str):
        with self.subTest(name):
            self.assertTrue(ok(cmd, self.ws, self.profile), f"{name} was blocked (should be allowed)")

    def test_the_instance_is_the_tests_own(self):
        self.assertEqual(hubconf.p("workspace_root"), self.root)
        self.assertEqual(self.pol["credential_files"], [self.tmp / "creds" / "token"])
        self.assertIn(self.tmp / "secrets", self.pol["deny_read"])
        text = self.profile.read_text()
        for p in (self.root, self.tmp / "services", self.tmp / "state", self.tmp / "bus", self.other):
            self.assertIn(f'(subpath "{p}")', text)

    def test_files(self):
        with self.subTest("write outside the folder"):
            if not self.escape:
                self.skipTest("write outside the folder: HOME and the tree are both in the temp area, which the profile "
                              "lets everything write")
            self.blocked("write outside the folder", f"echo x > '{self.escape}'", control=False)
            self.assertFalse(self.escape.exists())
        self.blocked("read ~/.ssh", "ls ~/.ssh")
        for f in self.pol["credential_files"]:
            self.blocked(f"read credential file {f.name}", f"wc -c < '{f}'")
        self.blocked("read another project", f"cat '{self.other}/secret.txt'")
        self.blocked("list workspace_root", f"ls '{hubconf.p('workspace_root')}'")
        self.blocked("list services_root", f"ls '{hubconf.p('services_root')}'")
        for d in policy.secret_dirs():
            self.blocked(f"read platform secrets ({d.name})", f"ls '{d}'")
        self.blocked("read platform state", f"ls '{hubconf.STATE}'")
        self.blocked("read the bus", f"ls '{hubconf.BUS_DIR}'")
        self.allowed("write own folder", "echo x > inside.txt")
        self.allowed("read own folder", "cat inside.txt")
        self.allowed("write temp area", 'f=$(mktemp) && echo x > "$f" && rm "$f"')
        self.allowed("read system files", "test -r /etc/hosts && ls /usr/bin >/dev/null")

    def test_keychain(self):
        self.blocked("Keychain(Security framework)", f"/usr/bin/python3 kc.py '{KEYCHAIN_ITEM}'")
        self.blocked("Keychain(/usr/bin/security)", f"security find-generic-password -s '{KEYCHAIN_ITEM}'")

    def test_network(self):
        self.blocked("connect outside", "curl -s -m 8 -o /dev/null https://example.com")
        port = os.environ.get("LEASE_RUNNER_PORT", "18797")
        self.blocked("connect to 127.0.0.1", f"curl -s -m 5 -o /dev/null http://127.0.0.1:{port}/")
        self.blocked("connect to unix socket", f"/usr/bin/python3 -c \"import socket; s=socket.socket(socket.AF_UNIX); "
                                               f"s.connect('{self.sock_path}')\"")

    def test_processes(self):
        self.blocked("signal another process", f"kill -TERM {self.sleeper.pid}", control=False)
        self.assertIsNone(self.sleeper.poll(), "another process was killed by work inside the sandbox")
        self.allowed("own child process", "sleep 0.1 & kill -0 $! && wait")

    @unittest.skip("never run on this machine: if the profile failed, these would submit a launchd job, open an app or "
                   "send Apple Events in the user's session (launchctl, open, osascript); checked by hand only")
    def test_launch_and_processes(self):
        self.blocked("open launches an app", "open -g -j -a Calculator", control=False)
        self.blocked("launchctl submit", f"launchctl submit -l local.zz-cap-escape-test -- /usr/bin/touch {self.escape}",
                     control=False)
        self.blocked("launchctl list", "launchctl list")
        self.blocked("osascript", "osascript -e 'tell application \"Finder\" to get name'", control=False)
        self.blocked("crontab", "crontab -l", control=False)


if __name__ == "__main__":
    unittest.main()
