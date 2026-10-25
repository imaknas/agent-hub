"""平台代跑的 Seatbelt profile(bin/seatbelt.py)真的擋得住逃脫(docs/design/capabilities.md)。

在 sandbox-exec 底下跑每一項檢查,只看成功或失敗,不印出任何機密內容。測試資料夾建在 workspace_root 底下
(profile 先擋整個 workspace_root、再放行自己的資料夾,放在暫存目錄測不出這個重疊);不在有 workspace_root 的
mac 上、或本身就在 sandbox 裡(套不了第二層)就 skip。

「該擋」的檢查先在 sandbox 外跑一次:外面本來就失敗(例:這台沒有那個檔)的那項不算數。
啟動程式那幾項不在外面先跑(會真的開 app);`open` 沒擋住時 Calculator 會被開起來,tearDown 會關掉。
"""
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

try:
    import hubconf  # noqa: E402
    import policy  # noqa: E402  需要 PyYAML
    import registry  # noqa: E402
    import seatbelt  # noqa: E402
except ImportError:
    policy = None

HOME = pathlib.Path.home()
KEYCHAIN_ITEM = "Claude Code-credentials"  # 這台一定有的 Keychain 項目(只查在不在,不讀值)


def can_sandbox() -> bool:
    if sys.platform != "darwin" or policy is None or not shutil.which("sandbox-exec"):
        return False
    r = subprocess.run(["sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"], capture_output=True)
    return r.returncode == 0 and bool(hubconf.p("workspace_root")) and hubconf.p("workspace_root").is_dir()


def ok(cmd: str, cwd, profile=None, env=None) -> bool:
    argv = ["/bin/bash", "-c", cmd]
    if profile:
        argv = ["sandbox-exec", "-f", str(profile)] + argv
    return subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          timeout=60).returncode == 0


@unittest.skipUnless(can_sandbox(), "要在 mac 上、有 workspace_root、不在 sandbox 裡")
class CapProfile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tag = uuid.uuid4().hex[:8]
        root = hubconf.p("workspace_root")
        cls.ws = (root / f"zz-cap-probe-{tag}").resolve()
        cls.other = (root / f"zz-cap-probe-other-{tag}").resolve()
        for d in (cls.ws, cls.other):
            d.mkdir()
            cls.addClassCleanup(shutil.rmtree, d, ignore_errors=True)  # setUpClass 中途失敗也清掉
        (cls.other / "secret.txt").write_text("x")
        cls.escape = HOME / f"zz-cap-escape-{tag}.txt"
        index = registry.refresh()
        pol = policy.policy(cls.ws.name, {"name": cls.ws.name, "paths": {"workspace": str(cls.ws)}}, index)
        pol["other_projects"].append(cls.other)
        cls.tmp = pathlib.Path(tempfile.mkdtemp())
        cls.profile = cls.tmp / "gpu.sb"
        cls.profile.write_text(seatbelt.profile(pol, "gpu"))
        # unix socket:平台自己開一個在聽,看 sandbox 裡連不連得到
        cls.sock_path = f"/private/tmp/zz-cap-{tag}.sock"
        cls.srv = socket.socket(socket.AF_UNIX)
        cls.srv.bind(cls.sock_path)
        cls.srv.listen(1)
        cls.sleeper = subprocess.Popen(["/bin/sleep", "60"])
        # 不經過 security 這支程式、直接叫 Security framework:擋的是 Keychain 服務,不只是那支程式
        (cls.ws / "kc.py").write_text(
            "import ctypes, sys\n"
            "S = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')\n"
            "s = sys.argv[1].encode()\n"
            "sys.exit(0 if S.SecKeychainFindGenericPassword(None, len(s), s, 0, None, None, None, None) == 0 else 1)\n")

    @classmethod
    def tearDownClass(cls):
        cls.sleeper.kill()
        cls.srv.close()
        for p in (cls.sock_path, cls.escape):
            pathlib.Path(p).unlink(missing_ok=True)
        for d in (cls.ws, cls.other, cls.tmp):
            shutil.rmtree(d, ignore_errors=True)
        subprocess.run(["osascript", "-e", 'if application "Calculator" is running then quit app "Calculator"'],
                       capture_output=True)
        subprocess.run(["launchctl", "remove", "local.zz-cap-escape-test"], capture_output=True)

    def blocked(self, name: str, cmd: str, control: bool = True):
        with self.subTest(name):
            if control and not ok(cmd, self.ws):
                self.skipTest(f"{name}:sandbox 外本來就失敗,這項不算數")
            self.assertFalse(ok(cmd, self.ws, self.profile), f"{name} 沒被擋住")

    def allowed(self, name: str, cmd: str):
        with self.subTest(name):
            self.assertTrue(ok(cmd, self.ws, self.profile), f"{name} 被擋了(應該放行)")

    def test_files(self):
        self.blocked("寫資料夾外", f"echo x > {self.escape}", control=False)
        self.assertFalse(self.escape.exists())
        self.blocked("讀 ~/.ssh", "ls ~/.ssh")
        for f in policy.policy(self.ws.name, {"name": self.ws.name, "paths": {"workspace": str(self.ws)}},
                               registry.refresh())["credential_files"]:
            self.blocked(f"讀憑證檔 {f.name}", f"wc -c < '{f}'")
        self.blocked("讀別的專案", f"cat '{self.other}/secret.txt'")
        self.blocked("列 workspace_root", f"ls '{hubconf.p('workspace_root')}'")
        if hubconf.p("services_root") and hubconf.p("services_root").is_dir():
            self.blocked("列 services_root", f"ls '{hubconf.p('services_root')}'")
        self.blocked("讀平台 secrets", f"ls '{hubconf.ROOT / '.secrets'}'")
        self.blocked("讀平台狀態", f"ls '{hubconf.STATE}'")
        self.blocked("讀匯流排", f"ls '{hubconf.BUS_DIR}'")
        self.allowed("寫自己的資料夾", "echo x > inside.txt")
        self.allowed("讀自己的資料夾", "cat inside.txt")
        self.allowed("寫暫存區", 'f=$(mktemp) && echo x > "$f" && rm "$f"')
        self.allowed("讀系統檔案", "test -r /etc/hosts && ls /usr/bin >/dev/null")

    def test_keychain(self):
        self.blocked("Keychain(Security framework)", f"/usr/bin/python3 kc.py '{KEYCHAIN_ITEM}'")
        self.blocked("Keychain(/usr/bin/security)", f"security find-generic-password -s '{KEYCHAIN_ITEM}'")

    def test_network(self):
        self.blocked("連外", "curl -s -m 8 -o /dev/null https://example.com")
        port = os.environ.get("LEASE_RUNNER_PORT", "18797")
        self.blocked("連 127.0.0.1", f"curl -s -m 5 -o /dev/null http://127.0.0.1:{port}/")
        self.blocked("連 unix socket", f"/usr/bin/python3 -c \"import socket; s=socket.socket(socket.AF_UNIX); "
                                       f"s.connect('{self.sock_path}')\"")

    def test_launch_and_processes(self):
        self.blocked("open 開 app", "open -g -j -a Calculator", control=False)
        self.blocked("launchctl submit", f"launchctl submit -l local.zz-cap-escape-test -- /usr/bin/touch {self.escape}",
                     control=False)
        self.blocked("launchctl list", "launchctl list")
        self.blocked("osascript", "osascript -e 'tell application \"Finder\" to get name'", control=False)
        self.blocked("crontab", "crontab -l", control=False)
        self.blocked("對別的程序送信號", f"kill -TERM {self.sleeper.pid}", control=False)
        self.assertIsNone(self.sleeper.poll(), "別的程序被 sandbox 裡的工作殺掉了")
        self.allowed("自己開的子程序", "sleep 0.1 & kill -0 $! && wait")


if __name__ == "__main__":
    unittest.main()
