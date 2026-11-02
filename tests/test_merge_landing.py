"""After a merge of agent-hub: `brunna land` (bin/brunna cmd_land) and merge-branch's landing step (step 4b), plus
merge-branch refusing a registry entry that is not v2 (docs/design/test-waiting.md steps 2.2/2.3).

cmd_land runs in-process with subprocess.run replaced (it is never started for real here); the landing step runs
merge-branch against a fake hub checkout (a git repo whose bin/brunna is a stand-in that records how it was called),
with its own config, state, registry index and bus.

    bin/py -m unittest discover -s tests
"""
import contextlib
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
PY = os.environ.get("AGENT_HUB_PYTHON") or sys.executable
sys.path.insert(0, str(BIN))
import i18n  # noqa: E402


def load_agenthub():
    loader = importlib.machinery.SourceFileLoader("agenthub_land_cli", str(BIN / "agenthub"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


class CmdLand(unittest.TestCase):
    """The registry check first (with the caller's arguments), then the hub's own hooks from its entry; both run
    whatever the other returned, and the worse exit code is the result."""

    def setUp(self):
        self.mod = load_agenthub()

    def land(self, codes, argv=()):
        calls = []

        def fake_run(cmd, *a, **k):
            calls.append([str(x) for x in cmd])
            return subprocess.CompletedProcess(cmd, codes[len(calls) - 1])

        import upgrade
        with mock.patch.object(self.mod.subprocess, "run", fake_run), mock.patch.object(upgrade, "cli_wrapper",
                                                                                        lambda: None):
            rc = self.mod.main(["land", *argv])
        return rc, calls

    def test_order_and_arguments(self):
        rc, calls = self.land([0, 0, 0, 0], ["--rebuild"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0][-3:], [str(BIN / "registry.py"), "check", "--rebuild"])
        self.assertEqual(calls[1][-3:], [str(BIN / "render-project-settings.py"), "--only",
                                         self.mod.hubconf.hub_project()])
        self.assertEqual(calls[0][0], calls[1][0], "both with the platform's Python (hubconf.launch)")
        # what a machine that takes its code by merging needs at once (docs/design/rename-brunna.md)
        self.assertEqual([c[-2:] for c in calls[2:]], [["init", "--scheduler-only"], ["config", "apply"]])

    def test_both_run_and_the_worse_code_wins(self):
        for codes, want in (([1, 0, 0, 0], 1), ([0, 2, 0, 0], 2), ([3, 1, 0, 0], 3), ([0, 0, 0, 4], 4)):
            with self.subTest(codes=codes):
                rc, calls = self.land(codes)
                self.assertEqual(len(calls), 4, "every step runs even when one failed")
                self.assertEqual(rc, want)

    def test_is_a_command(self):
        self.assertIs(self.mod.COMMANDS["land"], self.mod.cmd_land)


STUB = """#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
import json, os, sys
with open(os.environ["LAND_LOG"], "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\\n")
print("stub-land-said-this", file=sys.stderr)
sys.exit(int(os.environ.get("LAND_RC", "0")))
"""

HUB_ENTRY = """v: 2
name: brunna
type: platform
commits: conventional
tests:
- tier: quick
  run: 'true'
  when: [merge]
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        for d in ("home", "config/projects", "data", "secrets", "bus", "state/registry"):
            (self.tmp / d).mkdir(parents=True)
        (self.tmp / "config" / "hub.json").write_text(json.dumps({"bus_dir": str(self.tmp / "bus"),
                                                                  "registry_dir": "projects"}))
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("BRUNNA_", "AGENT_HUB_", "GIT_"))},
                    "AGENT_HUB_PYTHON": PY, "HOME": str(self.tmp / "home"),
                    "AGENT_HUB_CONFIG": str(self.tmp / "config"), "AGENT_HUB_DATA": str(self.tmp / "data"),
                    "AGENT_HUB_SECRETS": str(self.tmp / "secrets"), "AGENT_HUB_STATE": str(self.tmp / "state"),
                    "AGENT_HUB_LANG_UI": "zh-Hant", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_AUTHOR_NAME": "t",
                    "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, cwd, *args):
        r = subprocess.run(["git", "-C", str(cwd), *args], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def commit(self, cwd, name, title):
        (cwd / name).write_text(name)
        self.git(cwd, "add", name)
        self.git(cwd, "commit", "-q", "-m", title)

    def index(self, projects: dict):
        """The validated registry index as registry.refresh writes it."""
        (self.tmp / "state" / "registry" / "index.json").write_text(json.dumps({"v": 1, "projects": projects}))

    def events(self, etype: str) -> list[dict]:
        db = self.tmp / "bus" / "bus.db"
        if not db.exists():
            return []
        with contextlib.closing(sqlite3.connect(db)) as c:
            return [json.loads(p) for (p,) in c.execute("SELECT payload FROM events WHERE type=? ORDER BY seq", (etype,))]


class MergeLanding(Base):
    """merge-branch in the hub's own repo: after the fast-forward, `<main checkout>/bin/brunna land` from the main
    checkout; its outcome is recorded in the report (`landed`), and a failure is reported without undoing the merge."""

    def setUp(self):
        super().setUp()
        self.hub = self.tmp / "hub"
        self.git(self.tmp, "init", "-q", "-b", "main", str(self.hub))
        (self.hub / "projects").mkdir()
        (self.hub / "projects" / "brunna.yaml").write_text(HUB_ENTRY)
        (self.hub / ".gitignore").write_text("/bin/\n")
        self.git(self.hub, "add", "-A")
        self.git(self.hub, "commit", "-q", "-m", "chore: hub")
        # the hub's scripts merge-branch starts from the main checkout: the bus and the runner are the real ones, the
        # land step a stand-in with the platform's first line (hubconf.launch runs it with the platform's Python, as
        # it does the real one); bin/ is ignored, so neither checkout counts as dirty
        (self.hub / "bin").mkdir()
        for f in ("hubbus", "test-runner.py"):
            (self.hub / "bin" / f).symlink_to(BIN / f)
        (self.hub / "bin" / "agenthub").write_text(STUB)
        self.log = self.tmp / "land.jsonl"
        self.env.update(AGENT_HUB_ROOT=str(self.hub), LAND_LOG=str(self.log))
        self.index({"brunna": {"v": 2, "name": "brunna", "type": "platform", "commits": "conventional",
                                  "tests": [{"tier": "quick", "run": "true", "when": ["merge"]}]}})
        self.wt = self.tmp / "wt"
        self.git(self.hub, "worktree", "add", "-q", "-b", "feature", str(self.wt))
        self.commit(self.wt, "b", "feat: b")

    def merge(self, **env):
        return subprocess.run([PY, str(BIN / "merge-branch")], cwd=self.wt, env={**self.env, **env},
                              capture_output=True, text=True, timeout=180)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_land_runs_from_the_main_checkout_after_the_merge(self):
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.git(self.hub, "log", "--format=%s", "-1"), "feat: b")
        self.assertEqual(self.calls(), [{"argv": ["land"], "cwd": str(self.hub)}])
        report = self.events("session.report")
        self.assertEqual(len(report), 1)
        self.assertIs(report[0]["landed"], True)
        self.assertIs(report[0]["merged"], True)
        self.assertIs(report[0]["tests"]["ok"], True, "the hub's tiers ran through the runner")
        self.assertNotIn("stub-land-said-this", r.stderr, "a clean land prints nothing")

    def test_a_failed_land_is_reported_and_the_merge_stands(self):
        r = self.merge(LAND_RC="3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.git(self.hub, "log", "--format=%s", "-1"), "feat: b", "the merge stands")
        self.assertEqual(len(self.calls()), 1)
        self.assertIn("stub-land-said-this", r.stderr, "land's own output tail is shown")
        self.assertIn("brunna land", r.stderr)
        self.assertIs(self.events("session.report")[0]["landed"], False)
        self.assertEqual(len(self.events("merge.done")), 1)

    def test_no_land_when_tests_fail(self):
        self.index({"brunna": {"v": 2, "name": "brunna", "type": "platform"}})
        (self.wt / "projects" / "brunna.yaml").write_text(HUB_ENTRY.replace("run: 'true'", "run: 'false'"))
        self.git(self.wt, "commit", "-q", "-am", "test: a failing tier")
        r = self.merge()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.git(self.hub, "log", "--format=%s", "-1"), "chore: hub")

    def test_dry_run_does_not_land(self):
        r = subprocess.run([PY, str(BIN / "merge-branch"), "--dry-run"], cwd=self.wt, env=self.env,
                           capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.git(self.hub, "log", "--format=%s", "-1"), "chore: hub")


class MergeRefusesOldIndex(Base):
    """An index entry that is not v2 (an index written by older code, before the v1 → v2 upgrade on refresh): its tests
    and commit rules are unknown, so nothing is merged. (A missing entry and roster()'s paths-only fallback are pinned
    in tests/test_commit_title.py.)"""

    def setUp(self):
        super().setUp()
        self.env["AGENT_HUB_ROOT"] = str(ROOT)
        self.ws = self.tmp / "ws"
        self.git(self.tmp, "init", "-q", "-b", "main", str(self.ws))
        self.commit(self.ws, "a", "chore: initial commit")
        self.wt = self.tmp / "wt"
        self.git(self.ws, "worktree", "add", "-q", "-b", "feature", str(self.wt))
        self.commit(self.wt, "b", "feat: b")

    def refused(self, entry: dict):
        self.index({"demo": {"name": "demo", "paths": {"workspace": str(self.ws)}, **entry}})
        r = subprocess.run([PY, str(BIN / "merge-branch")], cwd=self.wt, env=self.env, capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        want = i18n.translate("merge-branch", "merge-branch: {project} is not in the validated project registry, so "
                              "its test command and commit rules are unknown; nothing was merged. `brunna config "
                              "check` rebuilds the registry and says what is wrong", {"project": "demo"}, "zh-Hant")
        self.assertIn(want, r.stderr)
        self.assertEqual(self.git(self.ws, "log", "--format=%s", "-1"), "chore: initial commit", "main untouched")
        self.assertEqual(self.events("merge.failed"), [], "refused before the gate starts: nothing recorded")

    def test_v1_entry(self):
        self.refused({"v": 1, "runtime": {"test": "true"}})

    def test_entry_without_a_version(self):
        self.refused({})

    def test_v2_entry_merges(self):
        """the control: the same index with v: 2 merges"""
        self.index({"demo": {"v": 2, "name": "demo", "paths": {"workspace": str(self.ws)}}})
        r = subprocess.run([PY, str(BIN / "merge-branch")], cwd=self.wt, env=self.env, capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.git(self.ws, "log", "--format=%s", "-1"), "feat: b")


if __name__ == "__main__":
    unittest.main()
