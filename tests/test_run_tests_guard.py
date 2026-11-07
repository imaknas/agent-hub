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


WRITES_HOME = """import os, pathlib, unittest
class T(unittest.TestCase):
    def test_x(self):
        (pathlib.Path(os.environ["HOME"]) / "left").write_text("x")
"""
LEAVES_TEMP = """import os, pathlib, unittest, sys
class T(unittest.TestCase):
    def test_x(self):
        sys.path.insert(0, os.environ["REAL_BIN"])
        import hubconf
        (hubconf.private_tmp() / "consolidate-left").mkdir()
"""


SAYS_TMPDIR = """import os, pathlib, unittest
class T(unittest.TestCase):
    def test_x(self):
        pathlib.Path(os.environ["SAY_TO"]).write_text(os.environ.get("TMPDIR") or "")
"""


class Guard(unittest.TestCase):
    def run_on(self, body):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "test_one.py").write_text(body)
            (pathlib.Path(d) / ".secrets" / "tmp").mkdir(parents=True)
            (pathlib.Path(d) / ".secrets" / "tmp" / "another-runs").write_text("x")   # another run's, or a live job's: not this run's leak
            env = {**os.environ, "AGENT_HUB_ROOT": d, "REAL_BIN": str(BIN)}
            env.pop("BRUNNA_ROOT", None)
            return subprocess.run([sys.executable, str(BIN / "run-tests"), d], capture_output=True, text=True, timeout=120, env=env)

    def test_what_a_test_leaves_in_its_home_or_the_private_temp_folder_fails_the_run(self):
        for body, says in ((WRITES_HOME, "HOME: left"), (LEAVES_TEMP, "private temp: consolidate-left")):
            with self.subTest(says=says):
                r = self.run_on(body)
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn(says, r.stdout)

    def test_a_system_temp_folder_is_left_for_a_private_one(self):
        """Run by hand. The tests run as the user and execute what they write there, and the system's is every sandboxed project's to write."""
        state = BIN.parent / ".state"   # outside the system's temp folder, whichever TMPDIR this suite runs under
        state.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory(dir="/tmp") as system, tempfile.TemporaryDirectory(dir=state) as private:
            d = pathlib.Path(d).resolve()
            (d / "tests").mkdir()
            (d / "tests" / "test_one.py").write_text(SAYS_TMPDIR)
            base = {**{k: v for k, v in os.environ.items() if not k.startswith(("BRUNNA_", "AGENT_HUB_"))},
                    "AGENT_HUB_ROOT": str(d), "AGENT_HUB_PYTHON": sys.executable, "SAY_TO": str(d / "said")}
            for label, extra, want_private in (("an instance", {"AGENT_HUB_HOME": str(d / "instance"), "TMPDIR": system}, True),
                                               ("a clone with no instance", {"TMPDIR": system}, False),
                                               ("given a private one", {"AGENT_HUB_HOME": str(d / "instance"), "TMPDIR": private}, False)):
                with self.subTest(label):
                    r = subprocess.run([sys.executable, str(BIN / "run-tests"), str(d / "tests")], capture_output=True, text=True, timeout=120,
                                       env={**base, **extra})
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    said = pathlib.Path((d / "said").read_text())
                    if want_private:
                        self.assertTrue(said.is_relative_to(d / "instance" / "secrets" / "tmp"), said)
                        self.assertFalse(said.exists(), "removed with the run")
                    else:
                        self.assertEqual(said, pathlib.Path(extra["TMPDIR"]))

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
