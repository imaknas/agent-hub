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
sys.path.insert(0, str(BIN))
import isolation  # noqa: E402
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


LEAVES_TMPDIR = """import tempfile, unittest
class T(unittest.TestCase):
    def test_x(self):
        tempfile.mkdtemp(prefix="left-behind-")
"""
SAYS_TMPDIR = """import os, pathlib, unittest
class T(unittest.TestCase):
    def test_x(self):
        pathlib.Path(os.environ["SAY_TO"]).write_text(os.environ.get("TMPDIR") or "")
"""


WRITES_BYTECODE = """import os, subprocess, sys, unittest
class T(unittest.TestCase):
    def test_x(self):
        self.assertTrue(sys.dont_write_bytecode)
        # what macOS's python3 does: its .pyc under HOME, unless told to write none
        subprocess.run([sys.executable, "-c", "import sys, py_compile, os; "
                        "None if sys.dont_write_bytecode else py_compile.compile(sys.argv[1], cfile=os.path.join(os.environ['HOME'], 'x.pyc'))",
                        __file__], check=True)
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

    def test_what_a_test_leaves_in_its_home_or_a_temp_folder_fails_the_run(self):
        for body, says in ((WRITES_HOME, "HOME: left"), (LEAVES_TEMP, "private temp: consolidate-left"),
                           (LEAVES_TMPDIR, "temp folder: left-behind-")):
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
                    if label == "given a private one" and isolation.in_system_tmp(private):   # bin/linux-test copies the tree into /tmp
                        self.skipTest("this tree is in the system's temp folder")
                    r = subprocess.run([sys.executable, str(BIN / "run-tests"), str(d / "tests")], capture_output=True, text=True, timeout=120,
                                       env={**base, **extra})
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    said = pathlib.Path((d / "said").read_text())
                    if want_private:
                        self.assertTrue(said.is_relative_to(d / "instance" / "secrets" / "tmp"), said)
                    else:   # a folder of the run's own in the one it was given, removed with the run
                        self.assertEqual(said.parent, pathlib.Path(extra["TMPDIR"]))
                        self.assertTrue(said.name.startswith("run-tests-"), said)
                    self.assertFalse(said.exists(), "removed with the run")

    def test_an_install_with_no_private_folder_outside_a_repo_does_not_run(self):
        """The old layout: settings in the hub root, secrets in <hub>/.secrets, inside the hub's repo. As the test runner's tiers, no run."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory(dir="/tmp") as system:
            d = pathlib.Path(d).resolve()
            (d / ".git").mkdir()
            (d / "brunna.json").write_text("{}")
            (d / "tests").mkdir()
            (d / "tests" / "test_one.py").write_text(SAYS_TMPDIR)
            env = {**{k: v for k, v in os.environ.items() if not k.startswith(("BRUNNA_", "AGENT_HUB_"))},
                   "AGENT_HUB_ROOT": str(d), "AGENT_HUB_PYTHON": sys.executable, "SAY_TO": str(d / "said"), "TMPDIR": system}
            r = subprocess.run([sys.executable, str(BIN / "run-tests"), str(d / "tests")], capture_output=True, text=True, timeout=120, env=env)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("move-instance", r.stderr)
            self.assertFalse((d / "said").exists(), "no test ran")

    def test_an_interpreter_writes_no_bytecode_into_home(self):
        """CI's macOS runner: its python3 caches bytecode under HOME, which the leak check then found (11-08)"""
        r = self.run_on(WRITES_BYTECODE)
        self.assertEqual(r.returncode, 0, r.stdout)

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
