"""bin/setup on a new machine: what it says about the service manager and how it tells you to install what is missing,
per operating system. The system's commands are stand-ins (uname, systemctl, the package manager), nothing is
installed or built: only `bin/setup --check`'s lines before the platform Python check are looked at.

    bin/py -m unittest tests.test_setup_hints
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class SetupHints(unittest.TestCase):
    def setup(self, system: str, *have: str, systemd: bool = False) -> str:
        """bin/setup --check on `system` (uname -s) with only `have` among the optional/required commands"""
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        stubs = tmp / "bin"
        stubs.mkdir()

        def stub(name, body="exit 0"):
            f = stubs / name
            f.write_text(f"#!/bin/sh\n{body}\n")
            f.chmod(0o755)
        stub("uname", f"echo {system}")
        stub("systemctl", "exit 0" if systemd else "exit 1")
        for name in ("git", "jq", "uv", "plutil", *have):
            stub(name)
        # only the stand-ins and the basic tools the script itself needs (dirname, cat…): no real git/jq/gh from the
        # machine running the test
        basic = tmp / "basic"
        basic.mkdir()
        for tool in ("dirname", "cat", "sh", "bash", "env", "printf"):
            for d in ("/usr/bin", "/bin"):
                if os.path.exists(f"{d}/{tool}"):
                    (basic / tool).symlink_to(f"{d}/{tool}")
                    break
        env = {"PATH": f"{stubs}:{basic}", "HOME": str(tmp), "AGENT_HUB_PYTHON": sys.executable}
        r = subprocess.run(["/bin/bash", str(ROOT / "bin" / "setup"), "--check"], env=env, capture_output=True,
                           text=True, timeout=60)
        return r.stdout

    def test_macos(self):
        out = self.setup("Darwin", "brew")
        self.assertIn("brew install gh", out)
        self.assertIn("brew install restic", out)
        self.assertNotIn("service manager", out, "launchd is always there")

    def test_linux_without_systemd(self):
        """a container: says nothing runs by itself, and how to install with apt"""
        out = self.setup("Linux", "apt-get")
        self.assertIn("no service manager", out)
        self.assertNotIn("come later", out)
        self.assertIn("sudo apt-get install gh, then gh auth login", out)
        self.assertIn("sudo apt-get install restic", out)
        self.assertNotIn("brew", out)

    def test_linux_with_systemd(self):
        out = self.setup("Linux", "dnf", systemd=True)
        self.assertIn("✓ systemd", out)
        self.assertIn("sudo dnf install gh", out)

    def test_linux_unknown_package_manager(self):
        out = self.setup("Linux")
        self.assertIn("install gh with your package manager", out)

if __name__ == "__main__":
    unittest.main()
