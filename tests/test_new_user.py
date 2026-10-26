"""全新使用者的驗收(docs/design/new-user.md 3.6):一個只有 hub.example.json 的空實例 + 假的 HOME,
`hub init --yes --no-launchd` 寫得出 hub.json、建得出資料夾,config 合格,`hub config plan` 跟 `hub doctor` 跑得起來(doctor
會報「排程器沒載入」之類的問題,但不能當掉),而且產生的設定裡沒有任何個人名稱。不碰 launchd、不呼叫模型。

平台自己的登錄(projects/agent-hub.yaml)現在還放在使用者的登錄資料夾裡,所以照樣複製一份——第 6 步要把它搬回平台。

    bin/py -m unittest discover -s tests
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PERSONAL = re.compile(r"knas|muninn|project-30|imaknas|/Users/you\b", re.I)


class NewUser(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.hub, self.home = self.tmp / "hub", self.tmp / "home"
        self.home.mkdir()
        for d in ("bin", "contracts", "templates", "modules", "launchd", "capabilities"):
            shutil.copytree(ROOT / d, self.hub / d, ignore=shutil.ignore_patterns("__pycache__"))
        (self.hub / "projects").mkdir()
        shutil.copy(ROOT / "projects" / "agent-hub.yaml", self.hub / "projects")
        shutil.copy(ROOT / "hub.example.json", self.hub)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "AGENT_HUB"))}
        self.env = {**env, "HOME": str(self.home), "AGENT_HUB_ROOT": str(self.hub),
                    "AGENT_HUB_PYTHON": sys.executable, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hub_cmd(self, *args):
        return subprocess.run([sys.executable, str(self.hub / "bin" / "hub"), *args], env=self.env,
                              capture_output=True, text=True, timeout=120, cwd=self.hub)

    def test_empty_instance_comes_up(self):
        r = self.hub_cmd("init", "--yes", "--no-launchd", "--owner", "Alex", "--timezone", "UTC",
                         "--workspace", str(self.home / "workspace"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cfg = (self.hub / "hub.json").read_text()
        self.assertIn('"tag": "Alex"', cfg)
        self.assertEqual(PERSONAL.findall(cfg), [], "新使用者的 hub.json 不該有別人的名字")
        for d in (self.hub / ".state", self.hub / ".secrets", self.home / "workspace"):
            self.assertTrue(d.is_dir(), d)
        self.assertEqual(oct((self.hub / ".secrets").stat().st_mode & 0o777), "0o700")

        r = subprocess.run([sys.executable, str(self.hub / "bin" / "config-check")], env=self.env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        r = self.hub_cmd("config", "plan")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("job:consolidate/consolidate-memory", r.stdout, "範本預設開 consolidate")
        self.assertNotIn("job:assistant/", r.stdout, "沒開助理就沒有它的 job")

        r = self.hub_cmd("doctor")
        self.assertNotIn("Traceback", r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"✗ 排程器.*(沒有載入|不是這份 hub)", "這份 hub 的排程器沒裝,doctor 要說出來")

    def test_init_never_overwrites_hub_json(self):
        (self.hub / "hub.json").write_text('{"v": 1, "registry_dir": "projects", "owner": {"address": "owner", "tag": "Keep"}}\n')
        r = self.hub_cmd("init", "--yes", "--no-launchd", "--owner", "Other")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('"tag": "Keep"', (self.hub / "hub.json").read_text())


if __name__ == "__main__":
    unittest.main()
