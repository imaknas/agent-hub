"""agent-hub's own work in linked worktrees (docs/design/test-waiting.md A1, step 1).

What must keep holding while the platform learns to run from a worktree: a main checkout reads its pointer and state
from itself; test-gate hands the tests the private-names list when the instance has one and leaves the check to skip
when it has none; merge-branch runs the registry's runtime.test after its rebase and leaves main untouched when it
fails.

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


@unittest.skipUnless((pathlib.Path(PY).parent / "ruff").exists(), "no ruff next to the platform's Python")
class TestGatePrivateNames(Base):
    """test-gate passes the private-names list to the tests (test_personal_boundary reads AGENT_HUB_PRIVATE_NAMES)."""

    def gate(self, with_list: bool):
        h = self.hub(files=("hubconf.py", "i18n.py", "gitdirs.py"))
        for f in ("test-gate", "run-tests", "lint", "py", "hubpaths.sh"):
            (h / "bin" / f).symlink_to(BIN / f)
        (h / "ruff.toml").write_text((ROOT / "ruff.toml").read_text())
        (h / "tests").mkdir()
        (h / "tests" / "test_probe.py").write_text(
            "import os\nimport unittest\n\n\nclass T(unittest.TestCase):\n    def test_private(self):\n"
            "        self.assertEqual(os.environ.get('AGENT_HUB_PRIVATE_NAMES', ''), os.environ.get('EXPECT_PRIVATE', ''))\n")
        cfg = self.tmp / "config"
        cfg.mkdir()
        (cfg / "hub.json").write_text("{}")
        expect = ""
        if with_list:
            (cfg / "private-names.txt").write_text("secretproj\n")
            expect = str(cfg / "private-names.txt")
        self.git(h, "add", "-A")
        return subprocess.run([str(h / "bin" / "test-gate")], cwd=h, capture_output=True, text=True, timeout=300,
                              env={**self.env, "AGENT_HUB_CONFIG": str(cfg), "EXPECT_PRIVATE": expect})

    def test_list_present_is_passed_in(self):
        r = self.gate(with_list=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_no_list_passes_nothing(self):
        r = self.gate(with_list=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class MergeRunsRuntimeTest(Base):
    def setUp(self):
        super().setUp()
        for d in ("config/projects", "data", "secrets", "bus", "services/demo", "state"):
            (self.tmp / d).mkdir(parents=True)
        (self.tmp / "config" / "hub.json").write_text(json.dumps({
            "bus_dir": str(self.tmp / "bus"), "registry_dir": "projects", "services_root": str(self.tmp / "services")}))
        self.env.update({"AGENT_HUB_CONFIG": str(self.tmp / "config"), "AGENT_HUB_DATA": str(self.tmp / "data"),
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
