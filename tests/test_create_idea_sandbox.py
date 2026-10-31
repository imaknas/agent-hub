"""create-idea-sandbox.py's cleanup when onboarding fails midway (docs/design/idea-sandboxes.md).

The whole hub is set up in a temp directory (AGENT_HUB_ROOT), onboard-project.sh is replaced with a fake script that
"fails halfway through building", and the real ~/workspace, ~/git-origin, ~/.memory-copies and projects/ are never
touched.

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

try:
    import yaml  # noqa: F401  create-idea-sandbox.py needs it to read the registry
except ImportError:
    yaml = None

# fake onboard: builds, in order like the real one, the workspace contents, bare repo, memory copy and registry
# file, then fails
FAKE_ONBOARD = r"""#!/bin/bash
ws="${@: -1}"; name=$(basename "$ws")
mkdir -p "$ws/.agent-memory" "$GO/$name-memory.git" "$MC/$name"
printf 'v: 1\nname: %s\n' "$name" > "$HUB/projects/$name.yaml"
if [ -n "$FAKE_COMMIT" ]; then git -C "$HUB" add "projects/$name.yaml" && git -C "$HUB" commit -qm reg; fi
echo "boom: mini 上建 bare repo 失敗" >&2
exit 1
"""


@unittest.skipIf(yaml is None, "needs PyYAML")
class CleanupOnFailure(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.hub = self.tmp / "hub"
        (self.hub / "bin").mkdir(parents=True)
        (self.hub / "projects").mkdir()
        for f in ("create-idea-sandbox.py", "hubconf.py", "i18n.py"):
            shutil.copy(ROOT / "bin" / f, self.hub / "bin" / f)
        (self.hub / "locales" / "zh-Hant").mkdir(parents=True)
        shutil.copy(ROOT / "locales" / "zh-Hant" / "create-idea-sandbox.json", self.hub / "locales" / "zh-Hant")
        fake = self.hub / "bin" / "onboard-project.sh"
        fake.write_text(FAKE_ONBOARD)
        fake.chmod(0o755)
        self.ws, self.go, self.mc = self.tmp / "workspace", self.tmp / "git-origin", self.tmp / "memory-copies"
        (self.hub / "hub.json").write_text(json.dumps({
            "v": 1, "registry_dir": "projects", "workspace_root": str(self.ws), "git_origin": str(self.go),
            "memory_copies": str(self.mc), "services_root": str(self.tmp / "services"), "ideas": {"limit": 20}}))
        subprocess.run(["git", "init", "-q", str(self.hub)], check=True)
        subprocess.run(["git", "-C", str(self.hub), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                        "--allow-empty", "-m", "init"], check=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def create(self, name="idea-5", **env):
        e = {**os.environ, "AGENT_HUB_ROOT": str(self.hub), "AGENT_HUB_LANG_UI": "zh-Hant", "HUB": str(self.hub), "GO": str(self.go),
             "MC": str(self.mc), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@t", **env}
        r = subprocess.run([sys.executable, str(self.hub / "bin" / "create-idea-sandbox.py"), name, "試試看"],
                           capture_output=True, text=True, env=e)
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])

    def test_untracked_registry_everything_removed(self):
        code, res = self.create()
        self.assertEqual(code, 1)
        self.assertEqual(res["result"], "create_failed")
        self.assertIn("boom", res["detail"])
        for p in (self.ws / "idea-5", self.go / "idea-5-memory.git", self.mc / "idea-5",
                  self.hub / "projects" / "idea-5.yaml"):
            self.assertFalse(p.exists(), f"{p} should be cleaned up")

    def test_committed_registry_is_kept_and_reported(self):
        code, res = self.create(FAKE_COMMIT="1")
        self.assertEqual(res["result"], "create_failed")
        self.assertIn("手動處理", res["detail"])
        self.assertTrue((self.hub / "projects" / "idea-5.yaml").exists(),
                        "a committed registry file must not be deleted automatically")
        for p in (self.ws / "idea-5", self.go / "idea-5-memory.git", self.mc / "idea-5"):
            self.assertFalse(p.exists(), f"{p} should be cleaned up")

    def test_retry_after_cleanup_is_not_blocked(self):
        self.create()
        code, res = self.create()  # sent again after cleanup: should try onboard again, not return exists
        self.assertEqual(res["result"], "create_failed")


class Names(CleanupOnFailure):
    """The name is the idea's code name, not limited to idea-<n> (10-25, the assistant's m10251646); whether it is an
        idea sandbox depends only on the registry's type."""

    def test_human_readable_name_passes_checks(self):
        code, res = self.create("room-3d")  # onboard is reached only after the name check (the fake one fails)
        self.assertEqual(res["result"], "create_failed")
        self.assertFalse((self.ws / "room-3d").exists(), "a failure is cleaned up")

    def test_old_style_still_ok(self):
        self.assertEqual(self.create("idea-12")[1]["result"], "create_failed")

    def test_bad_names(self):
        for name in ("Room", "-x", "x-", "a--b", "a_b", "idea 1", "x" * 41, "", "../x"):
            with self.subTest(name):
                self.assertEqual(self.create(name)[1]["result"], "bad_name")

    def test_taken_names(self):
        (self.ws / "notes").mkdir(parents=True)  # not registered, but the folder exists
        (self.tmp / "services" / "svc").mkdir(parents=True)
        for name in ("agent-hub", "notes", "svc"):
            with self.subTest(name):
                self.assertEqual(self.create(name)[1]["result"], "exists")


if __name__ == "__main__":
    unittest.main()
