"""`agenthub --version`: the release tag the hub checkout is at (git describe against cliff.toml's v* tags).

    bin/py -m unittest tests.test_agenthub_version
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class Version(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.hub = self.tmp / "hub"
        shutil.copytree(ROOT / "bin", self.hub / "bin", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "locales", self.hub / "locales")
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t", "AGENT_HUB_ROOT": str(self.hub)}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.hub), *args], env=self.env, check=True, capture_output=True)

    def version(self):
        r = subprocess.run([sys.executable, str(self.hub / "bin" / "agenthub"), "--version"], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_outside_git_unknown(self):
        self.assertEqual(self.version(), "agenthub unknown")

    def test_untagged_then_tagged(self):
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: a")
        sha = subprocess.run(["git", "-C", str(self.hub), "rev-parse", "--short", "HEAD"], env=self.env,
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(self.version(), f"agenthub {sha}")
        self.git("tag", "not-a-release")
        self.assertEqual(self.version(), f"agenthub {sha}", "only v* tags are releases")
        self.git("tag", "v0.1.0")
        self.assertEqual(self.version(), "agenthub v0.1.0")


if __name__ == "__main__":
    unittest.main()
