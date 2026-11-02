"""agenthub test in the hub's own repo (bin/test-runner.py declaration(); docs/design/test-waiting.md B4): the hub's
test declaration is read from the exported tree, and validated in a child process whose AGENT_HUB_ROOT is that export,
so a merge that changes agent-hub's tiers is tested by the tiers it brings, not by the main checkout's entry or the
registry index.

The hub here is a fake checkout (AGENT_HUB_ROOT) with only projects/brunna.yaml; the runner is the real one.

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
RUNNER = ROOT / "bin" / "test-runner.py"
PY = os.environ.get("AGENT_HUB_PYTHON") or sys.executable

MAIN_ENTRY = """v: 2
name: brunna
type: platform
tests:
- tier: old
  run: 'false'
  when: [merge]
"""
# the tier's command is a script that exists only in the tested tree: validating it needs AGENT_HUB_ROOT = the export
NEW_ENTRY = """v: 2
name: brunna
type: platform
tests:
- tier: fresh
  run: checks/ok.sh
  when: [merge]
"""


class HubDeclaration(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        for d in ("home", "config/projects", "state/registry", "bus"):
            (self.tmp / d).mkdir(parents=True)
        (self.tmp / "config" / "hub.json").write_text(json.dumps({"bus_dir": str(self.tmp / "bus"),
                                                                  "registry_dir": "projects"}))
        self.hub = self.tmp / "hub"
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("BRUNNA_", "AGENT_HUB_", "GIT_"))},
                    "AGENT_HUB_PYTHON": PY, "HOME": str(self.tmp / "home"), "GIT_CONFIG_GLOBAL": "/dev/null",
                    "AGENT_HUB_ROOT": str(self.hub), "AGENT_HUB_CONFIG": str(self.tmp / "config"),
                    "AGENT_HUB_STATE": str(self.tmp / "state"), "AGENT_HUB_DATA": str(self.tmp / "data"),
                    "AGENT_HUB_SECRETS": str(self.tmp / "secrets"), "AGENT_HUB_LANG_UI": "en",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t"}
        self.git(self.tmp, "init", "-q", "-b", "main", str(self.hub))
        (self.hub / "projects").mkdir()
        (self.hub / "projects" / "brunna.yaml").write_text(MAIN_ENTRY)
        self.git(self.hub, "add", "-A")
        self.git(self.hub, "commit", "-q", "-m", "chore: hub")
        # the index says what the main checkout says (a stale entry): the runner must not use it for the hub
        (self.tmp / "state" / "registry" / "index.json").write_text(json.dumps({"v": 1, "projects": {"agent-hub": {
            "v": 2, "name": "agent-hub", "tests": [{"tier": "old", "run": "false", "when": ["merge"]}]}}}))
        self.wt = self.tmp / "wt"
        self.git(self.hub, "worktree", "add", "-q", "-b", "work", str(self.wt))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, cwd, *args):
        r = subprocess.run(["git", "-C", str(cwd), *args], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def run_(self, *args, cwd=None):
        return subprocess.run([PY, str(RUNNER), *args], cwd=cwd or self.wt, env=self.env, capture_output=True,
                              text=True, timeout=120)

    def bring_new_tiers(self):
        (self.wt / "projects" / "brunna.yaml").write_text(NEW_ENTRY)
        (self.wt / "checks").mkdir()
        (self.wt / "checks" / "ok.sh").write_text("#!/bin/sh\nexit 0\n")
        (self.wt / "checks" / "ok.sh").chmod(0o755)

    def test_the_tiers_the_change_brings(self):
        self.bring_new_tiers()   # uncommitted: the default export is the worktree's content
        r = self.run_("--when", "merge")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("brunna/fresh", r.stdout)
        self.assertNotIn("brunna/old", r.stdout + r.stderr)

    def test_main_checkout_runs_its_own(self):
        """the control: the same runner in the main checkout reads main's entry (its failing tier)"""
        r = self.run_("--tier", "old", cwd=self.hub)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("brunna/old", r.stdout)

    def test_rev_reads_that_commit(self):
        self.bring_new_tiers()
        self.git(self.wt, "add", "-A")
        self.git(self.wt, "commit", "-q", "-m", "test: new tiers")
        self.assertEqual(self.run_("--tier", "fresh", "--rev", "HEAD").returncode, 0)
        r = self.run_("--tier", "fresh", "--rev", "main")
        self.assertEqual(r.returncode, 2, "main's tree has no such tier")

    def test_an_invalid_entry_in_the_tree_stops_the_run(self):
        (self.wt / "projects" / "brunna.yaml").write_text(NEW_ENTRY.replace("tier: fresh", "tier: Not Valid"))
        r = self.run_("--when", "merge")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("projects/brunna.yaml", r.stderr)
        self.assertNotIn("brunna/old", r.stdout, "no fallback to the main checkout's or the index's tiers")

    def test_a_script_only_the_tree_has_is_validated_against_the_tree(self):
        """the run: path resolves against the export (the child's AGENT_HUB_ROOT), so a tier whose script the change
        adds is valid; the same entry with the script missing from the tree is not"""
        self.bring_new_tiers()
        (self.wt / "checks" / "ok.sh").unlink()
        r = self.run_("--when", "merge")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("checks/ok.sh", r.stderr)

    def test_a_v1_entry_in_the_tree_is_upgraded(self):
        (self.wt / "projects" / "brunna.yaml").write_text("v: 1\nname: brunna\ntype: platform\nruntime:\n  test: 'true'\n")
        r = self.run_("--when", "merge")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("brunna/full", r.stdout)


if __name__ == "__main__":
    unittest.main()
