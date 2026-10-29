"""平台的 Python 最低 3.13(10-25 Knas 定的):全部經過 bin/py,不靠 PATH 上剛好是哪個 python3,也不寫死別的直譯器。

    bin/py -m unittest discover -s tests

刻意的例外(EXEMPT):scheduler-watchdog(平台的 Python 壞了它還要能發警報,用系統的 python3、只用標準函式庫)、
migrate-to-mini(在 MBP 上跑)、test_cap_profile(sandbox 裡拿系統的 python 當探針)、這支自己。
"""
import pathlib
import shutil
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHEBANG = '#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"'
OTHER_PYTHONS = ("/usr/bin/" + "python3", "/opt/homebrew/bin/" + "python3")
EXEMPT = {"bin/scheduler-watchdog.py", "bin/migrate-to-mini.py", "launchd/local.agent-hub-scheduler-watchdog.plist",
          "tests/test_cap_profile.py", "tests/test_python_floor.py"}
# 暫時的例外(要有理由、要拿掉):lease-runner 的服務換成 bin/py 要重載 launchd,會切斷正在進行的深談;
# render-services 還不會等服務閒置。沒有深談在跑時把登錄那行換成 `bin/py bin/lease-runner.py`,再刪掉這一項。
TEMPORARY: set = set()  # 10-25 lease-runner 換成 bin/py 之後沒有例外了
SCANNED = ["bin", "launchd", "tests", "githooks", "agent-jobs", "templates", "projects/agent-hub.yaml", "schedule.yaml"]


def files():
    for top in SCANNED:
        p = ROOT / top
        for f in ([p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file() and "__pycache__" not in x.parts)):
            rel = str(f.relative_to(ROOT))
            if rel in EXEMPT:
                continue
            try:
                yield rel, f.read_text()
            except UnicodeDecodeError:
                continue


class Floor(unittest.TestCase):
    def test_running_on_313_or_newer(self):
        self.assertGreaterEqual(sys.version_info[:2], (3, 13), "測試要用平台的 Python 跑(bin/py)")

    def test_every_python_script_goes_through_bin_py(self):
        bad = [rel for rel, text in files() if rel.startswith("bin/") and text.startswith("#!")
               and "python" in text.split("\n", 1)[0] and text.split("\n", 1)[0] != SHEBANG]
        self.assertEqual(bad, [], f"第一行要是 {SHEBANG}")

    def test_no_other_interpreter_named(self):
        bad = [f"{rel}:{i}" for rel, text in files() for i, line in enumerate(text.splitlines(), 1)
               if any(p in line for p in OTHER_PYTHONS) and not any(rel == f and t in line for f, t in TEMPORARY)]
        self.assertEqual(bad, [], "不要寫死別的直譯器:Python 程式用 sys.executable,shell 用 \"$HUB/bin/py\"")

    def test_platform_never_relies_on_the_first_line(self):
        """平台 Python 腳本的第一行只在 macOS 有用(Linux 的 kernel 把 #! 後面整串當一個參數):平台自己叫腳本時一律經過
        直譯器——Python 裡用 hubconf.launch() / sys.executable,shell 裡用 "$HUB/bin/py",hook 字串用 hubconf.sh_py()。"""
        import re
        pys = sorted((f.name for f in (ROOT / "bin").iterdir()
                      if f.is_file() and f.read_text(errors="ignore").startswith("#!/bin/sh -c exec")), key=len, reverse=True)
        names = "|".join(re.escape(n) for n in pys)
        argv0 = re.compile(r"\[\s*str\(\s*(?:BIN|HUB\s*/\s*\"bin\"|(?:hubconf\.)?ROOT\s*/\s*\"bin\")\s*/\s*\"(" + names + r")\"\s*\)")
        sh_cmd = re.compile(r"(?:^|[;&|(]\s*|\bexec\s+|\bthen\s+|\bdo\s+)\"?\$\{?(?:HUB|root|hub|SCRIPT_DIR|DIR|BIN|0%/\*)\}?"
                            r"(?:/bin)?/(" + names + r")\b")
        hook = re.compile(r"&&\s*~/[\w./-]*/bin/(" + names + r")\b")
        bad = []
        for f in sorted([*(ROOT / "bin").iterdir(), *(ROOT / "githooks").rglob("*")]):
            if not f.is_file():
                continue
            text = f.read_text(errors="ignore")
            py = text.startswith("#!/bin/sh -c exec") or f.suffix == ".py"
            for i, line in enumerate(text.splitlines(), 1):
                code = line.split("#", 1)[0] if py or not line.lstrip().startswith("#!") else ""
                for rx in ((argv0, hook) if py else (sh_cmd, hook)):
                    m = rx.search(code)
                    if m and not re.search(r"[\"']args[\"']\s*:\s*$", code[:m.start()]):   # MCP 設定:command 已經是直譯器
                        bad.append(f"{f.relative_to(ROOT)}:{i}:{m.group(1)}")
        self.assertEqual(bad, [], "直接執行平台的 Python 腳本(靠第一行,Linux 上跑不起來)")

    @unittest.skipUnless(sys.platform == "darwin", "第一行只在 macOS 有用;其他系統由上面的靜態檢查保證平台不靠它")
    def test_shebang_really_runs_on_the_platform_python(self):
        # 不經過 python 直接執行腳本:走的是第一行 → bin/py → 平台的 Python
        out = subprocess.run([str(ROOT / "bin" / "hub-runtime"), "local-ignores"], capture_output=True, text=True,
                             timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        probe = subprocess.run([str(ROOT / "bin" / "py"), "-c", "import sys, yaml; print(sys.version_info[:2] >= (3, 13))"],
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(probe.stdout.strip(), "True", probe.stderr)

    @unittest.skipUnless(shutil.which("/usr/bin/" + "python3"), "沒有系統的 python3")
    def test_watchdog_exemption_stays_honest(self):
        # 看門狗要在平台的 Python 壞掉時還能跑:系統的 python3 編得過、只 import 標準函式庫
        wd = ROOT / "bin" / "scheduler-watchdog.py"
        # (系統的 3.9 沒有 sys.stdlib_module_names:改成每個 import 都要在系統的 python3 找得到——它沒裝任何第三方套件)
        r = subprocess.run(["/usr/bin/" + "python3", "-c",
                            "import ast, sys, importlib.util; src = open(sys.argv[1]).read(); compile(src, sys.argv[1], 'exec');"
                            "mods = {a.name.split('.')[0] for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Import) for a in n.names}"
                            " | {n.module.split('.')[0] for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom) and n.module};"
                            "bad = sorted(m for m in mods if m != '__future__' and importlib.util.find_spec(m) is None);"
                            "print(','.join(bad))", str(wd)], capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""), r.stderr)


if __name__ == "__main__":
    unittest.main()
