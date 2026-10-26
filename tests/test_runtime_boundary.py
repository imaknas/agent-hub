"""中立的檔案裡不准出現執行環境(Claude Code)的語法或路徑——翻譯只在 runtime_claude.py(docs/design/capabilities.md)。

檢查的是程式碼跟字串,不是「Claude」這個字:說明文字可以提到翻譯在哪,但不能自己寫出 Claude 的設定格式。
新的中立檔案加進 NEUTRAL;m10251446(契約跟執行環境無關)做完再把 lease-runner.py 加進來。
"""
import pathlib
import re
import unittest

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"
NEUTRAL = ["policy.py", "seatbelt.py", "cap-runner.py", "hub-run", "appliers.py", "applier_scheduler.py", "config-check", "hub"]
PATTERNS = [
    (re.compile(r"(?<![\w])\.claude\b"), "Claude 的設定/憑證目錄"),
    (re.compile(r"\b(Read|Edit|Write|MultiEdit|NotebookEdit|Bash|WebFetch|WebSearch|Glob|Grep)\("), "Claude 的權限規則語法"),
    (re.compile(r"settings\.local\.json"), "Claude 的設定檔"),
    (re.compile(r"\bclaude\s+-p\b|[\"']claude[\"']\s*,"), "直接呼叫 claude"),
    (re.compile(r"--(resume|session-id|permission-prompt-tool|allowedTools|append-system-prompt)\b"), "claude 的旗標"),
    (re.compile(r"\bCLAUDE_[A-Z_]+"), "Claude 的環境變數"),
]


class TestRuntimeBoundary(unittest.TestCase):
    def test_neutral_files_have_no_runtime_syntax(self):
        checked = 0
        for name in NEUTRAL:
            f = BIN / name
            if not f.exists():
                continue
            checked += 1
            for i, line in enumerate(f.read_text().splitlines(), 1):
                for pat, what in PATTERNS:
                    self.assertIsNone(pat.search(line), f"{name}:{i} 出現{what}:{line.strip()}")
        self.assertGreater(checked, 0)

    def test_patterns_catch_examples(self):
        for bad in ['deny += ["Read(~/x/**)"]', 'p = home / ".claude"', "settings.local.json",
                    'cmd = ["claude", "-p"]', '"--resume", sid', "os.environ['CLAUDE_CODE_OAUTH_TOKEN']"]:
            self.assertTrue(any(p.search(bad) for p, _ in PATTERNS), bad)
        for ok in ["runtime_claude.settings(pol)", "翻成 Claude Code 的設定"]:
            self.assertFalse(any(p.search(ok) for p, _ in PATTERNS), ok)


if __name__ == "__main__":
    unittest.main()
