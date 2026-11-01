"""The test system's declarations and results (bin/tiers.py; docs/design/test-waiting.md B1–B3).

    bin/py -m unittest discover -s tests
"""
import datetime
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import tiers  # noqa: E402

FULL = {"tier": "full", "run": "pytest -n {jobs}", "when": ["merge", "nightly", "deploy"]}
E2E = {"tier": "e2e", "run": "pytest -m e2e", "paths": ["src/web/**"], "when": ["merge"]}


class Declarations(unittest.TestCase):
    def test_upgrade(self):
        v1 = {"v": 1, "name": "p", "runtime": {"deploy": "bin/deploy.sh", "test": "uv run pytest -q"}}
        v2 = tiers.upgrade(v1)
        self.assertEqual(v2["v"], 2)
        self.assertEqual(v2["runtime"], {"deploy": "bin/deploy.sh"})
        self.assertEqual(v2["tests"], [{"tier": "full", "run": "uv run pytest -q", "when": ["merge", "nightly", "deploy"]}])
        self.assertNotIn("code", v2)
        self.assertEqual(v1["runtime"]["test"], "uv run pytest -q", "the input is not changed")
        self.assertEqual(tiers.upgrade({"v": 1, "name": "p", "runtime": {"test": "x"}})["runtime"], {},
                         "an emptied runtime stays: its presence means the runtime folder")
        self.assertNotIn("tests", tiers.upgrade({"v": 1, "name": "p"}))
        self.assertIs(tiers.upgrade(v2), v2)

    def test_is_code(self):
        self.assertTrue(tiers.is_code({}, ["README.md"]), "no code = every file")
        self.assertFalse(tiers.is_code({}, []))
        doc = {"code": ["src/**", "uv.lock"]}
        self.assertTrue(tiers.is_code(doc, ["docs/x.md", "src/a/b.py"]))
        self.assertFalse(tiers.is_code(doc, ["docs/x.md"]))

    def test_selected(self):
        doc = {"code": ["src/**"], "tests": [FULL, E2E]}
        names = lambda changed, moment: [t["tier"] for t in tiers.selected(doc, changed, moment)]  # noqa: E731
        self.assertEqual(names(["src/api/x.py"], "merge"), ["full"])
        self.assertEqual(names(["src/web/app.js"], "merge"), ["full", "e2e"])
        self.assertEqual(names(["docs/x.md"], "merge"), [], "not code")
        self.assertEqual(names(None, "merge"), ["full", "e2e"], "no base: every tier of the moment")
        self.assertEqual(names(None, "nightly"), ["full"])
        self.assertEqual(names(["src/web/app.js"], "commit"), [])

    def test_key(self):
        k = tiers.key("tree1", FULL, [["cmd:python3 --version", "abc"]], {"PATH": "/bin"})
        self.assertEqual(k, tiers.key("tree1", dict(FULL), [["cmd:python3 --version", "abc"]], {"PATH": "/bin"}))
        for other in (tiers.key("tree2", FULL, [["cmd:python3 --version", "abc"]], {"PATH": "/bin"}),
                      tiers.key("tree1", {**FULL, "run": "pytest"}, [["cmd:python3 --version", "abc"]], {"PATH": "/bin"}),
                      tiers.key("tree1", {**FULL, "jobs": {"max": 2}}, [["cmd:python3 --version", "abc"]], {"PATH": "/bin"}),
                      tiers.key("tree1", FULL, [["cmd:python3 --version", "abd"]], {"PATH": "/bin"}),
                      tiers.key("tree1", FULL, [["cmd:python3 --version", "abc"]], {"PATH": "/usr/bin"})):
            self.assertNotEqual(k, other)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.store = tiers.Store(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_pass_and_fail(self):
        self.assertFalse(self.store.passed("k"))
        self.store.record("k", "p", "full", True, "merge")
        self.assertTrue(self.store.passed("k"))
        self.store.record("k", "p", "full", False, "merge")
        self.assertFalse(self.store.passed("k"), "the latest run failed")

    def test_nightly_failure_overrides_a_pass(self):
        self.store.record("k", "p", "full", True, "merge")
        self.store.record("k", "p", "full", False, "nightly")
        self.store.record("k", "p", "full", True, "merge")
        self.assertFalse(self.store.passed("k"), "flaky: the nightly failure stands until a nightly passes")
        self.store.record("k", "p", "full", True, "nightly")
        self.assertTrue(self.store.passed("k"))

    def test_old_passes_expire(self):
        self.store.record("k", "p", "full", True, "merge")
        later = datetime.datetime.now().astimezone() + datetime.timedelta(days=tiers.MAX_AGE_DAYS + 1)
        self.assertFalse(self.store.passed("k", now=later))

    def test_a_planted_link_is_not_followed(self):
        target = self.dir / "elsewhere.txt"
        target.write_text("")
        self.store.path.parent.mkdir(parents=True)
        os.symlink(target, self.store.path)
        with self.assertRaises(OSError):
            self.store.record("k", "p", "full", True, "merge")
        self.assertEqual(target.read_text(), "")
        self.assertFalse(self.store.passed("k"))

    def test_a_fifo_does_not_hang(self):
        self.store.path.parent.mkdir(parents=True)
        os.mkfifo(self.store.path)
        self.assertEqual(self.store.records(), [])


if __name__ == "__main__":
    unittest.main()
