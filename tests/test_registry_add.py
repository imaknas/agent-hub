"""registry.py add makes a new project known at once: it refreshes the index other commands read (hubconf.projects()),
instead of leaving that to the scheduler's next tick. Found 11-03 in a clean install: right after `brunna connect`, its
first-look said "no project" and `brunna send` in the new folder couldn't tell which project it was in.

    bin/py -m unittest tests.test_registry_add
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


class Add(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "config" / "projects").mkdir(parents=True)
        (self.tmp / "config" / "hub.json").write_text(json.dumps({"v": 1, "registry_dir": "projects",
                                                                  "owner": {"address": "owner", "tag": "owner"}}))
        self.env = {**{k: v for k, v in os.environ.items()
                       if not k.startswith(("AGENT_HUB_", "BRUNNA_")) or k in ("AGENT_HUB_PYTHON", "BRUNNA_PYTHON")},
                    "HOME": str(self.tmp / "home"), "AGENT_HUB_CONFIG": str(self.tmp / "config"),
                    "AGENT_HUB_STATE": str(self.tmp / "state"), "AGENT_HUB_LANG_UI": "en"}

    def registry(self, *args):
        return subprocess.run([sys.executable, str(BIN / "registry.py"), *args], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def known(self):
        r = subprocess.run([sys.executable, "-c", "import hubconf, json; print(json.dumps(sorted(hubconf.projects())))"],
                           env={**self.env, "PYTHONPATH": str(BIN)}, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_a_new_project_is_known_right_after_add(self):
        self.assertEqual(self.registry("check").returncode, 0)   # an index exists, as after brunna init
        self.assertNotIn("garden-app", self.known())
        ws = self.tmp / "ws" / "garden-app"
        ws.mkdir(parents=True)
        r = self.registry("add", "garden-app", str(ws), "(native)", str(self.tmp / "git-origin" / "garden-app-memory.git"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("garden-app", self.known())


if __name__ == "__main__":
    unittest.main()
