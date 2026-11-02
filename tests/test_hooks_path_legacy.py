"""render-project-settings gate() in the hub's own repo: the legacy core.hooksPath (the tracked githooks/ from before the
hub's gate moved into its git dir, step 2.3) is the platform's own, so it is unset and the hooks go into the git dir;
any other core.hooksPath (e.g. husky) is the project's, detected only. Report mode only says so.

gate() runs in a child Python whose AGENT_HUB_ROOT is a fake hub checkout (hubconf reads it at import), with its own
config and state.

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
PY = os.environ.get("AGENT_HUB_PYTHON") or os.environ.get("BRUNNA_PYTHON") or sys.executable
sys.path.insert(0, str(BIN))
import i18n  # noqa: E402

DOC = {"v": 2, "name": "brunna", "commit_gate": True, "commits": "conventional"}
GATE = ("import importlib.machinery, importlib.util, json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "loader = importlib.machinery.SourceFileLoader('rps', sys.argv[1] + '/render-project-settings.py')\n"
        "mod = importlib.util.module_from_spec(importlib.util.spec_from_loader('rps', loader))\n"
        "loader.exec_module(mod)\n"
        "acts = mod.gate('brunna', json.loads(sys.argv[2]), sys.argv[3] == 'report')\n"
        "print(json.dumps({'acts': acts, 'mark': mod.MARK}))\n")


class LegacyHooksPath(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        for d in ("home", "config/projects", "state", "bus"):
            (self.tmp / d).mkdir(parents=True)
        (self.tmp / "config" / "hub.json").write_text(json.dumps({"bus_dir": str(self.tmp / "bus"),
                                                                  "registry_dir": "projects"}))
        self.hub = self.tmp / "hub"
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("BRUNNA_", "AGENT_HUB_", "GIT_"))},
                    "AGENT_HUB_PYTHON": PY, "HOME": str(self.tmp / "home"), "GIT_CONFIG_GLOBAL": "/dev/null",
                    "AGENT_HUB_ROOT": str(self.hub), "AGENT_HUB_CONFIG": str(self.tmp / "config"),
                    "AGENT_HUB_STATE": str(self.tmp / "state"), "AGENT_HUB_DATA": str(self.tmp / "data"),
                    "AGENT_HUB_SECRETS": str(self.tmp / "secrets"), "AGENT_HUB_LANG_UI": "zh-Hant",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t"}
        r = subprocess.run(["git", "init", "-q", "-b", "main", str(self.hub)], env=self.env, capture_output=True)
        self.assertEqual(r.returncode, 0)
        (self.hub / "githooks").mkdir()
        (self.hub / ".husky").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hooks_path(self, value=None) -> str:
        if value is not None:
            subprocess.run(["git", "-C", str(self.hub), "config", "core.hooksPath", value], env=self.env, check=True)
        r = subprocess.run(["git", "-C", str(self.hub), "config", "core.hooksPath"], env=self.env, capture_output=True,
                           text=True)
        return r.stdout.strip()

    def gate(self, mode="apply", doc=None):
        r = subprocess.run([PY, "-c", GATE, str(BIN), json.dumps(doc or DOC), mode], env=self.env, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        *said, last = r.stdout.strip().splitlines()
        out = json.loads(last)
        return [tuple(a) for a in out["acts"]], "\n".join(said), out["mark"]

    def hook(self, name) -> pathlib.Path:
        return self.hub / ".git" / "hooks" / name

    def test_legacy_path_is_unset_and_hooks_go_into_the_git_dir(self):
        self.hooks_path("githooks")
        acts, said, mark = self.gate()
        self.assertEqual(acts, [("remove", "core.hooksPath"), ("add", "pre-commit"), ("add", "commit-msg")])
        self.assertEqual(self.hooks_path(), "", "unset")
        for h in ("pre-commit", "commit-msg"):
            self.assertTrue(self.hook(h).read_text().startswith("#!/bin/sh\n" + mark), h)
        self.assertIn(i18n.translate("render-project-settings", "{project}: core.hooksPath {path} unset; the platform's "
                                     "hooks go into the git dir", {"project": "brunna", "path": "githooks"},
                                     "zh-Hant"), said)
        self.assertEqual(self.gate()[0], [], "a second run has nothing to do")

    def test_absolute_spelling_of_the_legacy_path(self):
        self.hooks_path(str(self.hub / "githooks"))
        acts, _said, _mark = self.gate()
        self.assertEqual(acts[0], ("remove", "core.hooksPath"))
        self.assertEqual(self.hooks_path(), "")

    def test_report_mode_only_says_so(self):
        self.hooks_path("githooks")
        acts, said, _mark = self.gate("report")
        self.assertEqual(acts, [("remove", "core.hooksPath"), ("add", "pre-commit"), ("add", "commit-msg")])
        self.assertEqual(self.hooks_path(), "githooks", "report mode changes nothing")
        self.assertFalse(self.hook("pre-commit").exists())
        self.assertEqual(said, "")

    def test_the_projects_own_hooks_path_is_left_alone(self):
        self.hooks_path(".husky")
        acts, said, _mark = self.gate()
        self.assertEqual(acts, [])
        self.assertEqual(self.hooks_path(), ".husky")
        self.assertFalse(self.hook("pre-commit").exists())
        self.assertIn(i18n.translate("render-project-settings", "{project}: the platform's hooks are not installed (the "
                                     "project uses its own core.hooksPath={path}); detecting only",
                                     {"project": "brunna", "path": ".husky"}, "zh-Hant"), said)

    def test_legacy_path_unset_even_when_no_hook_is_wanted(self):
        self.hooks_path("githooks")
        acts, _said, _mark = self.gate(doc={"v": 2, "name": "brunna"})
        self.assertEqual(acts, [("remove", "core.hooksPath")])
        self.assertEqual(self.hooks_path(), "")
        self.assertFalse(self.hook("pre-commit").exists())


if __name__ == "__main__":
    unittest.main()
