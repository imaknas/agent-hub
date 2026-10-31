"""沒有 coding agent(hub.json runtime.default: none)時,平台每個用到執行環境的地方都明確說「不支援」,
不是當掉、也不是拿空資料照跑(docs/design/tool-agnostic.md 原則 3)。在隔離的 HOME / config / data 跑真的指令。

    bin/py -m unittest discover -s tests
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"


class RuntimeNone(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        for d in ("home", "config", "data", "secrets", "bus", "ws"):
            (self.tmp / d).mkdir()
        (self.tmp / "config" / "hub.json").write_text(json.dumps({
            "runtime": {"default": "none"}, "bus_dir": str(self.tmp / "bus"), "registry_dir": "projects"}))
        (self.tmp / "config" / "projects").mkdir()
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith("AGENT_HUB_") or k == "AGENT_HUB_PYTHON"},
                    "HOME": str(self.tmp / "home"), "AGENT_HUB_CONFIG": str(self.tmp / "config"),
                    "AGENT_HUB_DATA": str(self.tmp / "data"), "AGENT_HUB_SECRETS": str(self.tmp / "secrets")}

    def run_(self, *args, stdin="", env=None):
        return subprocess.run([sys.executable, *map(str, args)], input=stdin, env={**self.env, **(env or {})}, cwd=self.tmp,
                              capture_output=True, text=True, timeout=120)

    def test_instructions_file_is_unsupported(self):
        r = self.run_(BIN / "hub-runtime", "instructions-file", self.tmp / "ws",
                      env={"AGENT_HUB_LANG_UI": "zh-Hant"})   # the message asserted is the zh-Hant one
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("規則檔", r.stderr)
        self.assertEqual(r.stdout.strip(), "", "不能猜一個預設檔名")

    def test_hooks_are_not_installed(self):
        r = self.run_(BIN / "hub-event", "install", self.tmp / "ws")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual(list((self.tmp / "ws").iterdir()), [], "沒有 hook 可裝就什麼都不寫")

    def test_llm_point_fails_with_the_reason(self):
        r = self.run_(BIN / "llm.py", "run", "reflect-week", stdin="x")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        self.assertTrue(out["is_error"])
        self.assertIn("沒有", out["llm_error"])

    def test_memory_gaps_says_it_did_not_check(self):
        r = self.run_(BIN / "check-memory-gaps.py", env={"AGENT_HUB_LANG_UI": "zh-Hant"})   # the message asserted is the zh-Hant one
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("不檢查", r.stdout)

    def test_doctor_feature_table(self):
        sys.path.insert(0, str(BIN))
        import runtime_ports
        rows = runtime_ports.feature_table("none")
        self.assertTrue(rows and all(not ok and miss for _, ok, miss in rows))
        self.assertTrue(all(ok for _, ok, _ in runtime_ports.feature_table("claude")))


if __name__ == "__main__":
    unittest.main()
