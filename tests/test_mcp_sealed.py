"""平台的 headless 判斷點不載入環境裡的 MCP 伺服器(10-27 封閉實驗查到的洞)。

實驗:`claude -p --tools ""`(工具全關)照樣拿到全域/plugin 的 MCP 工具——265 個,含 project-06 正式資料庫的
execute_sql。關工具不等於關 MCP;只有 `--strict-mcp-config` 擋得掉(實驗確認它也擋 plugin 的 MCP,不影響 --mcp-config
明確給的)。所以每個判斷點都要帶它,例外要寫在這裡、有理由。

    bin/py -m unittest discover -s tests
"""
import pathlib
import re
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
FLAG = "--strict-mcp-config"
POINTS = yaml.safe_load((ROOT / "contracts" / "llm-points.yaml").read_text())["points"]
# 不在判斷點宣告裡、由叫它的程式加上旗標的點 → 哪支程式(測試確認那支程式真的有加)
ADDED_BY_CALLER = {
    "agent-job": "bin/run-agent-job.sh",            # 例外:登錄 job 寫 connectors: true(要 claude.ai connector)的不加
    "mailbox-escalation": "bin/run-agent-job.sh",   # 信箱的交辦也經過 run-agent-job.sh
    "lease-session": "bin/lease-runner.py",         # 租約只用 --mcp-config 給的權限轉接
    "lease-work": "bin/lease-runner.py",
    "lease-handover": "bin/lease-runner.py",
}


class McpSealed(unittest.TestCase):
    def test_every_point_is_sealed_from_ambient_mcp(self):
        missing = []
        for name, spec in POINTS.items():
            if FLAG in (spec.get("extra_args") or []):
                continue
            caller = ADDED_BY_CALLER.get(name)
            if caller and FLAG in (ROOT / caller).read_text():
                continue
            missing.append(name)
        self.assertEqual(missing, [], f"這些判斷點會載入環境裡的 MCP(含正式資料庫):加 {FLAG},或在這裡寫明誰加、為什麼")

    def test_no_point_gets_the_flag_twice(self):
        # 宣告裡有、呼叫端又加一次:不需要,也不確定 CLI 收不收重複的旗標
        both = [n for n, c in ADDED_BY_CALLER.items() if FLAG in (POINTS.get(n, {}).get("extra_args") or [])]
        self.assertEqual(both, [])

    def test_agent_jobs_skip_it_only_for_connectors(self):
        src = (ROOT / "bin" / "run-agent-job.sh").read_text()
        line = next(l for l in src.splitlines() if "llm.py" in l and " run " in l)
        self.assertIn("$MCP_ARGS", line.replace("${MCP_ARGS[@]}", "$MCP_ARGS"))
        self.assertRegex(src, r'LLM_AUTH:-\}" *(=|!=) *"?subscription')

    def test_trajectory_is_sealed_too(self):
        # trajectory.py 還沒經過 llm.py(直接叫 claude -p):先在那裡帶上
        src = (ROOT / "bin" / "trajectory.py").read_text()
        self.assertTrue(re.search(r'"-p".*' + re.escape(FLAG) + "|" + re.escape(FLAG), src))


if __name__ == "__main__":
    unittest.main()
