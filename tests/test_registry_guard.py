"""registry.refresh() does not rebuild an index from another or an empty registry dir (bin/registry.py wrong_registry).

The index lives in STATE, which follows the hub root, not the config; on 10-30 something with another config rebuilt the
real one and every project vanished from it. Here two configs share one STATE.

    bin/py -m unittest tests.test_registry_guard
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"


class Guard(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state = self.tmp / "state"
        for c in ("a", "b"):
            (self.tmp / c / "projects").mkdir(parents=True)
            (self.tmp / c / "hub.json").write_text(json.dumps({"v": 1, "registry_dir": "projects",
                                                               "owner": {"address": "owner", "tag": "owner"}}))
            self.add(c, f"proj-{c}")

    def add(self, config, name):
        ws = self.tmp / "ws" / name
        ws.mkdir(parents=True, exist_ok=True)
        (self.tmp / config / "projects" / f"{name}.yaml").write_text(f"v: 1\nname: {name}\npaths:\n  workspace: {ws}\n")

    def check(self, config, *args):
        env = {**{k: v for k, v in os.environ.items() if not k.startswith("AGENT_HUB_") or k == "AGENT_HUB_PYTHON"},
               "HOME": str(self.tmp / "home"), "AGENT_HUB_CONFIG": str(self.tmp / config),
               "AGENT_HUB_STATE": str(self.state), "AGENT_HUB_LANG_UI": "en"}
        return subprocess.run([sys.executable, str(BIN / "registry.py"), "check", *args], env=env,
                              capture_output=True, text=True, timeout=60)

    def index(self):
        return json.loads((self.state / "registry" / "index.json").read_text())

    def users(self):
        return sorted(n for n in self.index()["projects"] if n != "agent-hub")

    def test_another_config_does_not_replace_the_index(self):
        self.assertEqual(self.check("a").returncode, 0)
        r = self.check("b")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("not rebuilt", r.stderr)
        self.assertEqual(self.users(), ["proj-a"])
        self.assertTrue((self.state / "registry" / "proj-a.json").exists(), "the cache stays")
        log = [json.loads(l) for l in (self.state / "registry-refused.jsonl").read_text().splitlines()]
        self.assertEqual(len(log), 1)
        self.assertEqual((log[0]["config"], log[0]["state"], log[0]["argv"][1:]),
                         (str(self.tmp / "b"), str(self.state), ["check"]))
        for k in ("at", "why", "pid", "ppid", "parent", "cwd", "home"):
            self.assertIn(k, log[0])
        self.assertIn(f"pid {log[0]['pid']}", r.stderr, "the error names the caller too")
        self.assertEqual(self.check("b", "--rebuild").returncode, 0)
        self.assertEqual(self.users(), ["proj-b"])
        self.assertEqual(self.check("b").returncode, 0, "rebuilt from b, b may refresh it again")

    def test_an_empty_registry_dir_does_not_drop_every_project(self):
        self.assertEqual(self.check("a").returncode, 0)
        (self.tmp / "a" / "projects" / "proj-a.yaml").unlink()
        r = self.check("a")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("has no project files", r.stdout)
        self.assertEqual(self.users(), ["proj-a"])
        self.assertEqual(self.check("a", "--rebuild").returncode, 0)
        self.assertEqual(self.users(), [])

    def test_removing_one_project_still_works(self):
        self.add("a", "proj-c")
        self.assertEqual(self.check("a").returncode, 0)
        (self.tmp / "a" / "projects" / "proj-c.yaml").unlink()
        self.assertEqual(self.check("a").returncode, 0)
        self.assertEqual(self.users(), ["proj-a"])
        self.assertFalse((self.state / "registry" / "proj-c.json").exists())

    def test_an_index_from_before_the_guard_is_rebuilt(self):
        self.assertEqual(self.check("a").returncode, 0)
        idx = self.index()
        del idx["registry_dir"]
        (self.state / "registry" / "index.json").write_text(json.dumps(idx))
        self.assertEqual(self.check("b").returncode, 0)
        self.assertEqual(self.users(), ["proj-b"])

    def test_config_check_staged_validates_in_a_state_of_its_own(self):
        """the config repo's pre-commit: the export is another registry dir, and the real index must not be rebuilt from it"""
        self.assertEqual(self.check("a").returncode, 0)
        before = self.index()
        repo = self.tmp / "a"
        git = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        env = {**{k: v for k, v in os.environ.items() if not k.startswith(("AGENT_HUB_", "GIT_")) or k == "AGENT_HUB_PYTHON"},
               **git, "HOME": str(self.tmp / "home"), "AGENT_HUB_CONFIG": str(repo), "AGENT_HUB_STATE": str(self.state),
               "AGENT_HUB_LANG_UI": "en", "AGENT_HUB_PYTHON": sys.executable}   # the child runs bin/py; HOME is empty
        subprocess.run(["git", "init", "-q", str(repo)], env=env, check=True)
        self.add("a", "proj-d")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], env=env, check=True)
        r = subprocess.run([sys.executable, str(BIN / "config-check"), "--staged"], cwd=repo, env=env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.index(), before)


if __name__ == "__main__":
    sys.exit(unittest.main())
