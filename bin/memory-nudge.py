#!/usr/bin/env python3
"""提醒 agent 補寫 memory 筆記——只在「專案有新 commit、筆記卻比它舊」時出聲。

memory 靠 agent 自己寫,容易在忙的時候斷掉:project-25 2026-09-27 對話被
壓縮後,寫筆記的習慣沒被帶進摘要,之後 2.5 天、約 45 個 commit 都沒再記。
指令本身還在,缺的是在對的時間點被提醒。

兩種用法(都在專案根目錄執行):
  --post-tool     PostToolUse(Bash) hook:stdin 是 hook JSON。指令是 git commit/push
                  (不是在 .agent-memory 裡)、而且筆記過時,就輸出 additionalContext
                  讓模型看到。
  --session-start sync-local-setup.sh 在 SessionStart 呼叫(含壓縮後 source=compact):
                  筆記過時就印一行純文字,SessionStart 的 stdout 會進 context。

「過時」= 專案主 repo 最新 commit 的時間,晚於 .agent-memory/memory/ 底下最新的
檔案修改時間(看 mtime 不看 memory 的 commit,因為 memory 每小時才同步一次)。
其他情況一律不輸出、結束碼 0,不擋任何事。
"""
import datetime
import json
import pathlib
import re
import subprocess
import sys

GIT_WRITE = re.compile(r"\bgit\b(?:\s+-C\s+\S+)?[^|;&]*\b(commit|push)\b")


def stale_minutes(root: pathlib.Path):
    """回傳筆記落後主 repo 最新 commit 幾分鐘;不適用或沒落後回傳 None。"""
    mem = root / ".agent-memory" / "memory"
    if not mem.is_dir():
        return None
    head = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%ct"],
                          capture_output=True, text=True).stdout.strip()
    if not head.isdigit():
        return None
    notes = [p.stat().st_mtime for p in mem.rglob("*.md")]
    latest_note = max(notes) if notes else 0
    lag = int(head) - latest_note
    return int(lag // 60) if lag > 0 else None


def message(minutes: int) -> str:
    today = datetime.date.today().isoformat()
    return (f"memory 筆記落後了:這個專案最新的 commit 比 .agent-memory/memory/ 裡最新的筆記晚了約 "
            f"{minutes} 分鐘。在 .agent-memory/memory/{today}.md 補一段剛才做了什麼、做了什麼決定、"
            f"踩到什麼坑(原始紀錄就好,不用摘要),再繼續。")


def main() -> None:
    root = pathlib.Path.cwd()
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--post-tool":
        try:
            data = json.load(sys.stdin)
        except ValueError:
            return
        cmd = (data.get("tool_input") or {}).get("command", "")
        root = pathlib.Path(data.get("cwd") or root)
        if not GIT_WRITE.search(cmd) or ".agent-memory" in cmd or ".agent-memory" in root.parts:
            return
        minutes = stale_minutes(root)
        if minutes is not None:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                     "additionalContext": message(minutes)}},
                             ensure_ascii=False))
    elif mode == "--session-start":
        minutes = stale_minutes(root)
        if minutes is not None:
            print(message(minutes))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # 提醒失敗不該影響 session
        pass
