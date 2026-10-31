"""bin/run-tests: the parallel runner the commit gate uses. Its summary must read like unittest's (bin/test-gate parses
it) and every kind of failure must fail the run.

    bin/py -m unittest tests.test_run_tests
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

RUNNER = pathlib.Path(__file__).resolve().parents[1] / "bin" / "run-tests"


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def module(self, name, body):
        (self.tmp / f"{name}.py").write_text("import os\nimport unittest\n\n" + textwrap.dedent(body))

    def run_(self, **env):
        return subprocess.run([sys.executable, str(RUNNER), str(self.tmp)], capture_output=True, text=True, timeout=120,
                              env={**os.environ, "TEST_JOBS": "3", **env})

    def test_all_pass(self):
        self.module("test_a", "class A(unittest.TestCase):\n    def test_1(self): pass\n    def test_2(self): pass\n")
        self.module("test_b", "class B(unittest.TestCase):\n    @unittest.skip('no')\n    def test_1(self): pass\n")
        r = self.run_()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"(?m)^Ran 3 tests in [\d.]+s$")
        self.assertIn("(2 modules, 2 workers)", r.stdout)
        self.assertRegex(r.stdout, r"(?m)^OK \(skipped=1\)$")

    def test_failures_and_crashes_fail_the_run(self):
        self.module("test_ok", "class A(unittest.TestCase):\n    def test_1(self): pass\n")
        self.module("test_bad", "class B(unittest.TestCase):\n    def test_1(self): self.assertEqual(1, 2)\n")
        self.module("test_broken", "import no_such_module_xyz\n")
        r = self.run_()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertRegex(r.stdout, r"(?m)^FAIL: test_1 \(test_bad\.B\.test_1\)$", "FAIL: blocks stay at line start")
        self.assertRegex(r.stdout, r"(?m)^FAILED \(failures=1, errors=1\)$")
        self.assertIn("failed modules: test_bad, test_broken", r.stdout)
        self.assertEqual(r.stdout.count("-" * 70), 3, "one under each FAIL/ERROR header and the final one, as unittest "
                                                      "prints them; not each module's own one before its Ran line")
        self.assertEqual(len([l for l in r.stdout.splitlines() if l.startswith(("OK", "FAILED"))]), 1,
                         "only the overall result line starts with OK/FAILED (bin/test-gate greps for them)")

    def test_golden_modules_also_run_on_english(self):
        body = "class G(unittest.TestCase):\n    def test_lang(self): print(os.environ.get('I18N_GOLDEN_LANG'))\n"
        self.module("test_i18n_golden_x", body)
        self.module("test_i18n_golden_en", "class E(unittest.TestCase):\n    def test_skip(self): pass\n")
        durations = self.tmp / "d.json"
        r = self.run_(TEST_DURATIONS=str(durations))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("(3 modules,", r.stdout)
        self.assertIn("test_i18n_golden_x@en", durations.read_text())
