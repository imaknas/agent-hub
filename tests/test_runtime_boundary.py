"""中立的檔案裡不准出現執行環境(Claude Code)的語法或路徑——翻譯只在 runtime_claude.py(docs/design/capabilities.md)。

檢查的是程式碼跟字串,不是「Claude」這個字:說明文字可以提到翻譯在哪,但不能自己寫出 Claude 的設定格式。
新的中立檔案加進 NEUTRAL;m10251446(契約跟執行環境無關)做完再把 lease-runner.py 加進來。
"""
import json
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
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



# ---------- ratchet:平台其他地方的執行環境語法只准變少(docs/design/llm-providers.md 步驟 1)----------
# 目標是「Claude 只出現在 runtime_claude.py」;現在還有很多,一次清不完,所以跟 test_personal_boundary 一樣記基準、只准變少。
# 多一個 Claude 專用的模型名稱也算(判斷點要寫 tier,不是 haiku/sonnet/opus)。
RATCHET_SCOPE = ("bin", "modules", "agent-jobs", "contracts/llm-points.yaml")
RATCHET_EXEMPT = {"bin/runtime_claude.py"}   # 翻譯只在這裡
RATCHET = PATTERNS + [(re.compile(r"\b(haiku|sonnet|opus)\b", re.I), "Claude 的模型名稱")]
RUNTIME_BASELINE = ROOT / "tests" / "fixtures" / "runtime-baseline.json"


def ratchet_files() -> list[pathlib.Path]:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--", *RATCHET_SCOPE],
                             capture_output=True, text=True, check=True).stdout
        paths = [ROOT / p for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        paths = []
    if not paths:  # test-gate 的匯出副本不是 git repo
        paths = [p for d in RATCHET_SCOPE for p in ((ROOT / d).rglob("*") if (ROOT / d).is_dir() else [ROOT / d])]
    return sorted(p for p in paths if p.is_file() and "__pycache__" not in p.parts
                  and str(p.relative_to(ROOT)) not in RATCHET_EXEMPT)


def ratchet_counts() -> dict[str, int]:
    out = {}
    for f in ratchet_files():
        try:
            text = f.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if n := sum(len(p.findall(text)) for p, _ in RATCHET):
            out[str(f.relative_to(ROOT))] = n
    return out


class TestRuntimeRatchet(unittest.TestCase):
    def test_scans_something(self):
        self.assertGreater(len(ratchet_files()), 50)

    def test_counts_only_go_down(self):
        base = json.loads(RUNTIME_BASELINE.read_text())
        worse = [f"{f}:{n}(基準 {base.get(f, 0)})" for f, n in ratchet_counts().items() if n > base.get(f, 0)]
        self.assertEqual(worse, [], "執行環境(Claude)專用的語法變多了——放進 runtime_claude.py、或判斷點寫 tier;"
                                    "真的有理由才手動改 tests/fixtures/runtime-baseline.json")


def update() -> int:
    """基準只降不升(跟 test_personal_boundary 一樣)。"""
    now = ratchet_counts()
    base = json.loads(RUNTIME_BASELINE.read_text()) if RUNTIME_BASELINE.exists() else dict(now)
    for f in list(base):
        n = now.get(f, 0)
        if n == 0:
            del base[f]
        elif n < base[f]:
            base[f] = n
    RUNTIME_BASELINE.write_text(json.dumps(dict(sorted(base.items())), ensure_ascii=False, indent=1) + "\n")
    print(f"{len(base)} 個檔案,共 {sum(base.values())} 處")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--update"]:
        sys.exit(update())
    unittest.main()
