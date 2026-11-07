"""bin/run-tests keeps every test off the real coding agent: a recording `claude` first on PATH and a fresh HOME, and any call fails the run.

    bin/run-tests
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"
CALLS = """import subprocess, unittest
class T(unittest.TestCase):
    def test_x(self):
        subprocess.run(["claude", "-p", "hi"], capture_output=True)
"""
QUIET = """import os, pathlib, unittest
class T(unittest.TestCase):
    def test_x(self):
        self.assertFalse((pathlib.Path(os.environ["HOME"]) / ".nvm").exists(), "a fresh HOME")
"""


class Guard(unittest.TestCase):
    def run_on(self, body):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "test_one.py").write_text(body)
            return subprocess.run([sys.executable, str(BIN / "run-tests"), d], capture_output=True, text=True, timeout=120, env={**os.environ})

    def test_a_call_fails_the_run_and_says_so(self):
        r = self.run_on(CALLS)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("real coding agent", r.stdout)
        self.assertIn("-p hi", r.stdout)

    def test_a_quiet_run_passes_under_a_fresh_home(self):
        r = self.run_on(QUIET)
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()
