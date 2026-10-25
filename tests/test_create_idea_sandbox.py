"""create-idea-sandbox.py 在 onboard 中途失敗時的清理(docs/design/idea-sandboxes.md)。

整個 hub 搭在暫存目錄(AGENT_HUB_ROOT),onboard-project.sh 換成一支「建一半就失敗」的假腳本,
不碰真的 ~/workspace、~/git-origin、~/.memory-copies、projects/。

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
    import yaml  # noqa: F401  create-idea-sandbox.py 讀登錄要用
except ImportError:
    yaml = None

# 假的 onboard:跟真的一樣依序建出 workspace 裡的東西、bare repo、memory 副本、登錄檔,然後失敗
FAKE_ONBOARD = r"""#!/bin/bash
ws="${@: -1}"; name=$(basename "$ws")
mkdir -p "$ws/.agent-memory" "$GO/$name-memory.git" "$MC/$name"
printf 'v: 1\nname: %s\n' "$name" > "$HUB/projects/$name.yaml"
if [ -n "$FAKE_COMMIT" ]; then git -C "$HUB" add "projects/$name.yaml" && git -C "$HUB" commit -qm reg; fi
echo "boom: mini 上建 bare repo 失敗" >&2
exit 1
"""


@unittest.skipIf(yaml is None, "需要 PyYAML")
class CleanupOnFailure(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.hub = self.tmp / "hub"
        (self.hub / "bin").mkdir(parents=True)
        (self.hub / "projects").mkdir()
        for f in ("create-idea-sandbox.py", "hubconf.py"):
            shutil.copy(ROOT / "bin" / f, self.hub / "bin" / f)
        fake = self.hub / "bin" / "onboard-project.sh"
        fake.write_text(FAKE_ONBOARD)
        fake.chmod(0o755)
        self.ws, self.go, self.mc = self.tmp / "workspace", self.tmp / "git-origin", self.tmp / "memory-copies"
        (self.hub / "hub.json").write_text(json.dumps({
            "v": 1, "registry_dir": "projects", "workspace_root": str(self.ws), "git_origin": str(self.go),
            "memory_copies": str(self.mc), "ideas": {"limit": 20}}))
        subprocess.run(["git", "init", "-q", str(self.hub)], check=True)
        subprocess.run(["git", "-C", str(self.hub), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                        "--allow-empty", "-m", "init"], check=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def create(self, **env):
        e = {**os.environ, "AGENT_HUB_ROOT": str(self.hub), "HUB": str(self.hub), "GO": str(self.go),
             "MC": str(self.mc), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@t", **env}
        r = subprocess.run([sys.executable, str(self.hub / "bin" / "create-idea-sandbox.py"), "idea-5", "試試看"],
                           capture_output=True, text=True, env=e)
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])

    def test_untracked_registry_everything_removed(self):
        code, res = self.create()
        self.assertEqual(code, 1)
        self.assertEqual(res["result"], "create_failed")
        self.assertIn("boom", res["detail"])
        for p in (self.ws / "idea-5", self.go / "idea-5-memory.git", self.mc / "idea-5",
                  self.hub / "projects" / "idea-5.yaml"):
            self.assertFalse(p.exists(), f"{p} 應該被清掉")

    def test_committed_registry_is_kept_and_reported(self):
        code, res = self.create(FAKE_COMMIT="1")
        self.assertEqual(res["result"], "create_failed")
        self.assertIn("手動處理", res["detail"])
        self.assertTrue((self.hub / "projects" / "idea-5.yaml").exists(), "已 commit 的登錄檔不能自己刪")
        for p in (self.ws / "idea-5", self.go / "idea-5-memory.git", self.mc / "idea-5"):
            self.assertFalse(p.exists(), f"{p} 應該被清掉")

    def test_retry_after_cleanup_is_not_blocked(self):
        self.create()
        code, res = self.create()  # 清乾淨之後再送一次:應該再試一次 onboard,不是回 exists
        self.assertEqual(res["result"], "create_failed")


if __name__ == "__main__":
    unittest.main()
