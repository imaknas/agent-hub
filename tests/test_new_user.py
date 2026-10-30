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
PERSONAL = re.compile(r"knas|muninn|imaknas", re.I)


class NewUser(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.hub, self.home = self.tmp / "hub", self.tmp / "home"
        self.home.mkdir()
        for d in ("bin", "contracts", "templates", "modules", "launchd", "capabilities", "githooks", "locales"):
            shutil.copytree(ROOT / d, self.hub / d, ignore=shutil.ignore_patterns("__pycache__"))
        (self.hub / "projects").mkdir()
        shutil.copy(ROOT / "projects" / "agent-hub.yaml", self.hub / "projects")
        shutil.copy(ROOT / "hub.example.json", self.hub)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "AGENT_HUB"))}
        self.env = {**env, "HOME": str(self.home), "AGENT_HUB_ROOT": str(self.hub),
                    "AGENT_HUB_PYTHON": sys.executable, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "AGENT_HUB_LANG_UI": "zh-Hant",
                    # 假的 HOME 沒有 git 的身分:別讓 commit 靠 git 自己猜(macOS 猜得到、Linux 容器猜不到)
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hub_cmd(self, *args):
        return subprocess.run([sys.executable, str(self.hub / "bin" / "agenthub"), *args], env=self.env,
                              capture_output=True, text=True, timeout=120, cwd=self.hub)

    def test_empty_instance_comes_up(self):
        r = self.hub_cmd("init", "--yes", "--no-launchd", "--owner", "Alex", "--timezone", "UTC",
                         "--workspace", str(self.home / "workspace"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        inst = self.home / ".agent-hub"   # 新使用者一開始就是新的擺法:實例目錄 config/ + data/ + secrets/
        self.assertEqual((self.hub / ".hub-home").read_text().strip(), str(inst))
        self.assertFalse((self.hub / "hub.json").exists(), "平台 repo 裡不放使用者的 hub.json")
        cfg = (inst / "config" / "hub.json").read_text()
        self.assertIn('"tag": "Alex"', cfg)
        self.assertEqual(PERSONAL.findall(cfg), [], "新使用者的 hub.json 不該有別人的名字")
        for d in (inst / "config" / "projects", inst / "data", inst / "secrets", self.hub / ".state", self.home / "workspace"):
            self.assertTrue(d.is_dir(), d)
        self.assertEqual(oct((inst / "secrets").stat().st_mode & 0o777), "0o700")
        self.assertTrue((inst / "config" / ".git").is_dir(), "config as code:config 是自己的 git repo")
        hp = subprocess.run(["git", "-C", str(inst / "config"), "config", "core.hooksPath"], capture_output=True, text=True)
        self.assertEqual(pathlib.Path(hp.stdout.strip()).resolve(), (self.hub / "githooks" / "config").resolve())

        r = subprocess.run([sys.executable, str(self.hub / "bin" / "config-check")], env=self.env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        r = self.hub_cmd("config", "plan")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("job:consolidate/consolidate-memory", r.stdout, "範本預設開 consolidate")
        self.assertNotIn("job:assistant/", r.stdout, "沒開助理就沒有它的 job")

        r = self.hub_cmd("doctor")
        self.assertNotIn("Traceback", r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"✗ 排程器.*(沒有載入|不是這份 hub|服務管理器|沒有裝)", "這份 hub 的排程器沒裝,doctor 要說出來")

    def test_config_repo_gate_rejects_bad_config(self):
        self.hub_cmd("init", "--yes", "--no-launchd", "--owner", "Alex")
        cfg = self.home / ".agent-hub" / "config"
        (cfg / "hub.json").write_text((cfg / "hub.json").read_text().replace('"modules"', '"modulez"'))
        git = ["git", "-C", str(cfg), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*git, "add", "-A"], env=self.env, check=True, capture_output=True)
        r = subprocess.run([*git, "commit", "-q", "-m", "x"], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, "config repo 的 pre-commit 要擋下不合格的 config")
        self.assertIn("modulez", r.stdout + r.stderr)

    def test_move_instance_copies_validates_then_points(self):
        """舊的擺法(hub.json 在 hub 根目錄)搬到實例目錄:只複製(來源不動)、config repo 有第一個 commit、最後才寫指標檔。"""
        (self.hub / "hub.json").write_text((self.hub / "hub.example.json").read_text())
        (self.hub / "todo.md").write_text("# todo\n")
        (self.hub / ".secrets").mkdir()
        (self.hub / ".secrets" / "x").write_text("s")
        inst = self.tmp / "inst"
        r = self.hub_cmd("move-instance", "--to", str(inst))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(inst.exists(), "沒加 --apply 只列出,不動手")
        r = self.hub_cmd("move-instance", "--to", str(inst), "--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.hub / ".hub-home").read_text().strip(), str(inst.resolve()))
        for f in ("config/hub.json", "config/projects", "data/todo.md", "secrets/x"):
            self.assertTrue((inst / f).exists(), f)
        self.assertTrue((self.hub / "hub.json").exists() and (self.hub / "todo.md").exists(), "來源不刪")
        log = subprocess.run(["git", "-C", str(inst / "config"), "log", "--oneline"], capture_output=True, text=True)
        self.assertIn("config moved", log.stdout)
        self.assertFalse((inst / "config" / "projects" / "agent-hub.yaml").exists(), "平台自己的登錄留在平台")
        r = self.hub_cmd("move-instance", "--to", str(self.tmp / "again"), "--apply")
        self.assertNotEqual(r.returncode, 0, "已經搬過就不再搬")

    def test_init_never_overwrites_hub_json(self):
        (self.hub / "hub.json").write_text('{"v": 1, "registry_dir": "projects", "owner": {"address": "owner", "tag": "Keep"}}\n')
        r = self.hub_cmd("init", "--yes", "--no-launchd", "--owner", "Other")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('"tag": "Keep"', (self.hub / "hub.json").read_text())


if __name__ == "__main__":
    unittest.main()
