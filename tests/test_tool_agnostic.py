"""工具中立的硬規則(AGENTS.md「工具中立」;docs/design/tool-agnostic.md)的機械檢查。

1. 平台每晚的核心判斷點要能用任何 LLM provider 跑(shape: completion)。還沒改完的列在 PENDING,**只准變少**:
   改完一個(宣告成 completion)就從 PENDING 拿掉,不然這裡會失敗提醒。
2. 直接 import Claude 轉接器(runtime_claude)的平台檔案列在 DIRECT,**只准變少**;其他地方一律經過 runtime_ports。

    bin/py -m unittest discover -s tests
"""
import pathlib
import re
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 每晚的核心:只有 API key、沒有任何 coding agent 的人也要能跑的判斷點
CORE = {"librarian-state", "librarian-todo-format", "librarian-index", "reflect-week"}
# 還沒改成 completion 的(影子模式比對沒問題才切換;llm-providers.md 步驟 4)
PENDING = {"librarian-state", "librarian-todo-format", "librarian-index"}

# 直接用 Claude 轉接器的檔案(都是 Claude 專屬的機制,之後搬進 runtime 介面):
DIRECT = {
    "bin/lease-relay.py",               # Claude Code 的權限轉接(--permission-prompt-tool)本身
    "bin/lease-runner.py",              # 長駐 session 的旗標、MCP 設定(第 1 層 open_session 之後搬進 runtime)
    "bin/render-project-settings.py",   # 中立政策 → Claude 的 settings.local.json
    "bin/merge-hook-settings.py",       # 舊入口,交給 runtime_claude.install_hooks
    "bin/migrate-to-mini.py",           # MBP 上跑的一次性搬遷工具
}
IMPORT = re.compile(r"^\s*(?:import runtime_claude\b|from runtime_claude\b)", re.M)


class ToolAgnostic(unittest.TestCase):
    def test_core_points_run_on_any_provider(self):
        points = yaml.safe_load((ROOT / "contracts" / "llm-points.yaml").read_text())["points"]
        self.assertTrue(CORE <= set(points), CORE - set(points))
        not_yet = {p for p in CORE if points[p].get("shape") != "completion"}
        self.assertLessEqual(not_yet, PENDING, f"核心判斷點變回 agent 形狀了:{sorted(not_yet - PENDING)}")
        self.assertEqual(PENDING - not_yet, set(), f"{sorted(PENDING - not_yet)} 已經是 completion:從 PENDING 拿掉")

    def test_direct_use_of_the_claude_adapter_only_shrinks(self):
        found = {f"bin/{f.name}" for f in (ROOT / "bin").iterdir()
                 if f.is_file() and f.name != "runtime_claude.py" and IMPORT.search(f.read_text(errors="replace"))}
        self.assertLessEqual(found, DIRECT, f"新的檔案直接 import runtime_claude:{sorted(found - DIRECT)}——經過 runtime_ports")
        self.assertEqual(DIRECT - found, set(), f"{sorted(DIRECT - found)} 已經不直接用了:從 DIRECT 拿掉")


if __name__ == "__main__":
    unittest.main()
