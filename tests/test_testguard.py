"""A test run started by hand in a linked worktree reaches neither the main checkout's live .state nor the user's temp folder
(bin/testguard.py, hubconf's last lines): whichever way it is started, it runs again as a guarded child with the tree as
the platform and a throwaway temp folder, and what a test left behind is reported and removed."""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import names  # noqa: E402

PROBE = '''import os, pathlib, sys, tempfile, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "bin"))
import hubconf
FIXED = hubconf.STATE / "probe-fixed.json"   # a path fixed at import, as alert.SENT is


class Probe(unittest.TestCase):
    def test_writes(self):
        hubconf.STATE.mkdir(parents=True, exist_ok=True)
        (hubconf.STATE / "probe.json").write_text("{}")
        FIXED.write_text("{}")
        tempfile.mkdtemp(prefix="probe-leak-")   # never cleaned up
        print("probe state:", hubconf.STATE)


if __name__ == "__main__":
    unittest.main()
'''


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main", *args], cwd=cwd,
                   check=True, capture_output=True)


class BareRunInWorktree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp())
        main = cls.tmp / "hub"
        shutil.copytree(ROOT / "bin", main / "bin", ignore=shutil.ignore_patterns("__pycache__"), symlinks=True)
        (main / "tests").mkdir()
        shutil.copy(ROOT / "tests" / "__init__.py", main / "tests")
        (main / "tests" / "test_probe.py").write_text(PROBE)
        (main / ".gitignore").write_text(".state/\n.hub-home\n")
        git("init", "-q", cwd=main)
        git("add", "-A", cwd=main)
        git("commit", "-qm", "hub", cwd=main)
        # the live machine: the main checkout's .state and its instance
        (main / ".state").mkdir()
        inst = cls.tmp / "instance"
        (inst / "config").mkdir(parents=True)
        (main / ".hub-home").write_text(str(inst))
        git("worktree", "add", "-q", str(cls.tmp / "wt"), cwd=main)
        cls.main, cls.wt, cls.inst = main, cls.tmp / "wt", inst

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_bare(self, *argv):
        """argv run as an agent would by hand: nothing of a runner's in the environment, the user's own temp folder."""
        user_tmp = pathlib.Path(tempfile.mkdtemp(dir=self.tmp))
        env = {k: v for k, v in os.environ.items() if k != "TMPDIR"}
        for x in ("ROOT", "STATE", "HOME", "CONFIG", "DATA", "SECRETS", "TEST_RUNNER", "TEST_GUARD"):
            for spelling in names.env_names(x):
                env.pop(spelling, None)
        env.update(TMPDIR=str(user_tmp), HOME=str(self.tmp / "user-home"))
        r = subprocess.run([sys.executable, *argv], cwd=self.wt, env=env, capture_output=True, text=True, timeout=120)
        return r, user_tmp

    def check(self, *argv):
        r, user_tmp = self.run_bare(*argv)
        out = r.stdout + r.stderr
        self.assertIn("Ran 1 test", out)
        self.assertIn("probe state:", out)
        self.assertEqual(list((self.main / ".state").iterdir()), [], "a bare run wrote the live .state")
        self.assertFalse((self.inst / "secrets").exists() and any((self.inst / "secrets").rglob("*probe*")))
        self.assertEqual(list(user_tmp.iterdir()), [], "a bare run left folders in the user's temp folder")
        # the probe's own leak is reported and fails the run, though its test passed
        self.assertIn("tests left files behind", out)
        self.assertIn("probe-leak-", out)
        self.assertEqual(r.returncode, 1, out)

    def test_as_package_module(self):
        self.check("-m", "unittest", "tests.test_probe")

    def test_discover(self):
        self.check("-m", "unittest", "discover", "-s", "tests")

    def test_file_as_script(self):
        self.check("tests/test_probe.py")

    def test_a_runner_s_run_is_left_alone(self):
        """bin/run-tests (TEST_RUNNER) and a guarded child set things up themselves: no second run."""
        import testguard
        env = names.env_set({**os.environ}, "TEST_GUARD", "1")
        r = subprocess.run([sys.executable, "-c", "import sys, testguard; sys.exit(0 if testguard.guarded() else 3)"],
                           env={**env, "PYTHONPATH": str(ROOT / "bin")}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(testguard.guarded())   # this process: run-tests' or a guard's child, whichever started it


if __name__ == "__main__":
    unittest.main()
