"""The Seatbelt profile the platform runs things under (bin/seatbelt.py) really blocks escapes
(docs/design/capabilities.md).

Each check runs under sandbox-exec and looks only at success or failure; no secret content is printed. The test
folder is created under workspace_root (the profile first blocks all of workspace_root, then allows its own folder; a
temp directory would not test that overlap); skipped when not on a mac with workspace_root, or when already inside a
sandbox (a second layer cannot be applied).

Each "should block" check first runs once outside the sandbox: one that already fails outside (e.g. this machine has
no such file) does not count.
The program-launching checks do not run outside first (they would really open the app); if `open` is not blocked,
Calculator gets opened, and tearDown quits it.
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

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

try:
    import hubconf  # noqa: E402
    import policy  # noqa: E402  needs PyYAML
    import registry  # noqa: E402
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
    return r.returncode == 0 and bool(hubconf.p("workspace_root")) and hubconf.p("workspace_root").is_dir()


def ok(cmd: str, cwd, profile=None, env=None) -> bool:
    argv = ["/bin/bash", "-c", cmd]
    if profile:
        argv = ["sandbox-exec", "-f", str(profile)] + argv
    return subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          timeout=60).returncode == 0


@unittest.skipUnless(can_sandbox(), "needs a mac, a workspace_root, and not being inside a sandbox")
class CapProfile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tag = uuid.uuid4().hex[:8]
        root = hubconf.p("workspace_root")
        cls.ws = (root / f"zz-cap-probe-{tag}").resolve()
        cls.other = (root / f"zz-cap-probe-other-{tag}").resolve()
        for d in (cls.ws, cls.other):
            d.mkdir()
            cls.addClassCleanup(shutil.rmtree, d, ignore_errors=True)  # cleaned up even if setUpClass fails midway
        (cls.other / "secret.txt").write_text("x")
        cls.escape = HOME / f"zz-cap-escape-{tag}.txt"
        index = registry.refresh()
        pol = policy.policy(cls.ws.name, {"name": cls.ws.name, "paths": {"workspace": str(cls.ws)}}, index)
        pol["other_projects"].append(cls.other)
        cls.tmp = pathlib.Path(tempfile.mkdtemp())
        cls.profile = cls.tmp / "gpu.sb"
        cls.profile.write_text(seatbelt.profile(pol, "gpu"))
        # unix socket: the platform opens one listening itself, and checks whether the sandbox can reach it
        cls.sock_path = f"/private/tmp/zz-cap-{tag}.sock"
        cls.srv = socket.socket(socket.AF_UNIX)
        cls.srv.bind(cls.sock_path)
        cls.srv.listen(1)
        cls.sleeper = subprocess.Popen(["/bin/sleep", "60"])
        # call the Security framework directly, not through the security program: what is blocked is the Keychain
        # service, not just that program
        (cls.ws / "kc.py").write_text(
            "import ctypes, sys\n"
            "S = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')\n"
            "s = sys.argv[1].encode()\n"
            "sys.exit(0 if S.SecKeychainFindGenericPassword(None, len(s), s, 0, None, None, None, None) == 0 else 1)\n")

    @classmethod
    def tearDownClass(cls):
        cls.sleeper.kill()
        cls.srv.close()
        for p in (cls.sock_path, cls.escape):
            pathlib.Path(p).unlink(missing_ok=True)
        for d in (cls.ws, cls.other, cls.tmp):
            shutil.rmtree(d, ignore_errors=True)
        subprocess.run(["osascript", "-e", 'if application "Calculator" is running then quit app "Calculator"'],
                       capture_output=True)
        subprocess.run(["launchctl", "remove", "local.zz-cap-escape-test"], capture_output=True)

    def blocked(self, name: str, cmd: str, control: bool = True):
        with self.subTest(name):
            if control and not ok(cmd, self.ws):
                self.skipTest(f"{name}: already fails outside the sandbox, does not count")
            self.assertFalse(ok(cmd, self.ws, self.profile), f"{name} was not blocked")

    def allowed(self, name: str, cmd: str):
        with self.subTest(name):
            self.assertTrue(ok(cmd, self.ws, self.profile), f"{name} was blocked (should be allowed)")

    def test_files(self):
        self.blocked("write outside the folder", f"echo x > {self.escape}", control=False)
        self.assertFalse(self.escape.exists())
        self.blocked("read ~/.ssh", "ls ~/.ssh")
        for f in policy.policy(self.ws.name, {"name": self.ws.name, "paths": {"workspace": str(self.ws)}},
                               registry.refresh())["credential_files"]:
            self.blocked(f"read credential file {f.name}", f"wc -c < '{f}'")
        self.blocked("read another project", f"cat '{self.other}/secret.txt'")
        self.blocked("list workspace_root", f"ls '{hubconf.p('workspace_root')}'")
        if hubconf.p("services_root") and hubconf.p("services_root").is_dir():
            self.blocked("list services_root", f"ls '{hubconf.p('services_root')}'")
        self.blocked("read platform secrets", f"ls '{hubconf.ROOT / '.secrets'}'")
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

    def test_launch_and_processes(self):
        self.blocked("open launches an app", "open -g -j -a Calculator", control=False)
        self.blocked("launchctl submit", f"launchctl submit -l local.zz-cap-escape-test -- /usr/bin/touch {self.escape}",
                     control=False)
        self.blocked("launchctl list", "launchctl list")
        self.blocked("osascript", "osascript -e 'tell application \"Finder\" to get name'", control=False)
        self.blocked("crontab", "crontab -l", control=False)
        self.blocked("signal another process", f"kill -TERM {self.sleeper.pid}", control=False)
        self.assertIsNone(self.sleeper.poll(), "another process was killed by work inside the sandbox")
        self.allowed("own child process", "sleep 0.1 & kill -0 $! && wait")


if __name__ == "__main__":
    unittest.main()
