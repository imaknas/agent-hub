"""agent-hub's own work in linked worktrees (docs/design/test-waiting.md A1, step 1).

What kept holding (characterized first): a main checkout reads its pointer and state from itself; merge-branch runs the
registry's runtime.test after its rebase and leaves main untouched when it fails.
What a linked worktree of the hub does now: it takes .hub-home, .state and .logs from its main checkout (hubconf and
hubpaths.sh alike), unless AGENT_HUB_ROOT is explicit; from there registry.refresh writes nothing and the scheduler and
config apply refuse; run-tests points AGENT_HUB_ROOT at the tree it tests. (The instance files tests read, hub.json
tests.config_inputs, are the runner's: tests/test_test_runner.py.)

    bin/py -m unittest discover -s tests
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
PY = os.environ.get("AGENT_HUB_PYTHON") or sys.executable
PROBE = ("import hubconf as h, json; print(json.dumps({'config': str(h.config_dir()), 'state': str(h.STATE), "
         "'logs': str(h.LOGS), 'pointer': str(h.POINTER)}))")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        (self.tmp / "home").mkdir()
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("AGENT_HUB_", "GIT_"))},
                    "AGENT_HUB_PYTHON": PY, "HOME": str(self.tmp / "home"), "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, cwd, *args):
        r = subprocess.run(["git", "-C", str(cwd), *args], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def hub(self, files=("hubconf.py", "i18n.py", "hubpaths.sh", "gitdirs.py")) -> pathlib.Path:
        """A hub checkout: a git repo with copies of the platform's files (copies, not links: hubconf takes its root
        from its own resolved location)."""
        h = self.tmp / "hub"
        (h / "bin").mkdir(parents=True)
        for f in files:
            if (BIN / f).exists():
                shutil.copy(BIN / f, h / "bin" / f)
        (h / ".gitignore").write_text(".state/\n.logs/\n.hub-home\n")
        self.git(self.tmp, "init", "-q", "-b", "main", str(h))
        self.git(h, "add", "-A")
        self.git(h, "commit", "-q", "-m", "chore: hub")
        return h

    def probe(self, checkout, **env) -> dict:
        r = subprocess.run([PY, "-c", PROBE], cwd=checkout / "bin", env={**self.env, **env}, capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)


class MainCheckout(Base):
    def test_main_checkout_reads_its_own_pointer_and_state(self):
        h = self.hub()
        inst = self.tmp / "inst"
        (h / ".hub-home").write_text(f"{inst}\n")
        self.assertEqual(self.probe(h), {"config": f"{inst}/config", "state": f"{h}/.state", "logs": f"{h}/.logs",
                                         "pointer": f"{h}/.hub-home"})

    def test_hubpaths_in_the_main_checkout(self):
        h = self.hub()
        inst = self.tmp / "inst"
        (h / ".hub-home").write_text(f"{inst}\n")
        r = subprocess.run(["bash", "-c", 'source bin/hubpaths.sh; echo "$HUB_CONFIG"'], cwd=h, env=self.env,
                           capture_output=True, text=True, check=True)
        self.assertEqual(r.stdout.strip(), f"{inst}/config")


class LinkedWorktree(Base):
    def setUp(self):
        super().setUp()
        self.main = self.hub()
        self.inst = self.tmp / "inst"
        (self.main / ".hub-home").write_text(f"{self.inst}\n")
        self.wt = self.tmp / "wt"
        self.git(self.main, "worktree", "add", "-q", "-b", "work", str(self.wt))

    def test_worktree_takes_pointer_and_state_from_the_main_checkout(self):
        m = self.main
        self.assertEqual(self.probe(self.wt), {"config": f"{self.inst}/config", "state": f"{m}/.state",
                                               "logs": f"{m}/.logs", "pointer": f"{m}/.hub-home"})

    def test_hubpaths_in_a_worktree(self):
        r = subprocess.run(["bash", "-c", 'source bin/hubpaths.sh; echo "$HUB_CONFIG|$HUB_STATE|$HUB_MAIN"'],
                           cwd=self.wt, env=self.env, capture_output=True, text=True, check=True)
        self.assertEqual(r.stdout.strip().split("|"), [f"{self.inst}/config", f"{self.main}/.state", str(self.main)])

    def test_explicit_root_skips_the_lookup(self):
        got = self.probe(self.wt, AGENT_HUB_ROOT=str(self.wt))
        self.assertEqual((got["state"], got["pointer"]), (f"{self.wt}/.state", f"{self.wt}/.hub-home"))

    def test_lookup_is_reported_unless_state_is_explicit(self):
        code = "import hubconf; print(hubconf.worktree_lookup())"
        def run(**env):
            return subprocess.run([PY, "-c", code], cwd=self.wt / "bin", env={**self.env, **env}, capture_output=True,
                                  text=True, check=True).stdout.strip()
        self.assertEqual(run(), "True")
        self.assertEqual(run(AGENT_HUB_STATE=str(self.tmp / "st")), "False")
        self.assertEqual(subprocess.run([PY, "-c", code], cwd=self.main / "bin", env=self.env, capture_output=True,
                                        text=True, check=True).stdout.strip(), "False")


class FromAWorktreeNothingLiveIsRebuilt(unittest.TestCase):
    """In-process with the lookup forced on: refresh validates without writing, the scheduler refuses."""

    def setUp(self):
        sys.path.insert(0, str(BIN))
        import hubconf
        self.hubconf = hubconf
        self.saved = hubconf.worktree_lookup
        hubconf.worktree_lookup = lambda: True

    def tearDown(self):
        self.hubconf.worktree_lookup = self.saved

    def test_refresh_writes_nothing(self):
        import registry
        with tempfile.TemporaryDirectory() as d:
            saved = registry.CACHE
            registry.CACHE = pathlib.Path(d) / "registry"
            try:
                index = registry.refresh()
            finally:
                registry.CACHE = saved
            self.assertIn("projects", index)
            self.assertFalse((pathlib.Path(d) / "registry").exists())

    def test_scheduler_refuses(self):
        import scheduler
        with self.assertRaises(SystemExit) as e:
            scheduler.main()
        self.assertIn("linked worktree", str(e.exception.code))


class RunTestsRoot(Base):
    def test_root_is_the_tree_under_test(self):
        tree = self.tmp / "tree"
        (tree / "tests").mkdir(parents=True)
        (tree / "tests" / "test_probe.py").write_text(
            "import os\nimport unittest\n\n\nclass T(unittest.TestCase):\n    def test_root(self):\n"
            f"        self.assertEqual(os.environ.get('AGENT_HUB_ROOT'), {str(tree)!r})\n"
            "        self.assertNotIn('AGENT_HUB_STATE', os.environ)\n")
        r = subprocess.run([PY, str(BIN / "run-tests"), str(tree / "tests")], env=self.env, capture_output=True,
                           text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class MergeRunsRuntimeTest(Base):
    def setUp(self):
        super().setUp()
        for d in ("config/projects", "data", "secrets", "bus", "services/demo", "state"):
            (self.tmp / d).mkdir(parents=True)
        (self.tmp / "config" / "hub.json").write_text(json.dumps({
            "bus_dir": str(self.tmp / "bus"), "registry_dir": "projects", "services_root": str(self.tmp / "services")}))
        self.env.update({"AGENT_HUB_ROOT": str(ROOT), "AGENT_HUB_CONFIG": str(self.tmp / "config"), "AGENT_HUB_DATA": str(self.tmp / "data"),
                         "AGENT_HUB_SECRETS": str(self.tmp / "secrets"), "AGENT_HUB_STATE": str(self.tmp / "state")})
        self.ws = self.tmp / "ws"
        self.git(self.tmp, "init", "-q", "-b", "main", str(self.ws))
        (self.ws / "a").write_text("a")
        self.git(self.ws, "add", "a")
        self.git(self.ws, "commit", "-q", "-m", "chore: a")
        self.wt = self.tmp / "wt"
        self.git(self.ws, "worktree", "add", "-q", "-b", "feature", str(self.wt))
        (self.wt / "b").write_text("b")
        self.git(self.wt, "add", "b")
        self.git(self.wt, "commit", "-q", "-m", "feat: b")

    def register(self, test):
        (self.tmp / "config" / "projects" / "demo.yaml").write_text(
            f"v: 1\nname: demo\npaths:\n  workspace: {self.ws}\nruntime:\n  test: {json.dumps(test)}\n")
        r = subprocess.run([PY, str(BIN / "registry.py"), "check", "demo"], env=self.env, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def merge(self):
        return subprocess.run([PY, str(BIN / "merge-branch")], cwd=self.wt, env=self.env, capture_output=True,
                              text=True, timeout=120)

    def head(self):
        return self.git(self.ws, "log", "--format=%s", "-1").strip()

    def test_failing_test_keeps_main(self):
        self.register("test -f nothing-here")
        r = self.merge()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("tests", r.stdout)
        self.assertEqual(self.head(), "chore: a")

    def test_passing_test_merges_and_runs_in_the_rebased_worktree(self):
        self.register("test -f b && test -f a")
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.head(), "feat: b")


if __name__ == "__main__":
    unittest.main()
