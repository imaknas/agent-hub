#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
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

「落後」= 最新筆記(.agent-memory/memory/ 底下檔案的 mtime;memory 每小時才同步,
所以不看 commit)或 onboard 那一刻之後,有代表專案工作的 commit 還沒記:累積 2 個以上,
或最早那個已經超過 30 分鐘。每次 commit 都有記的話不會被打擾。排程自動產生的 commit
跟只動 agent-hub 管的檔案的 commit 不算。
其他情況一律不輸出、結束碼 0,不擋任何事。
"""
from __future__ import annotations  # 系統的 python3(3.9,hook 用的就是它)也要能 import

import datetime
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from hublib import NON_WORK_PATHS, is_work_subject  # noqa: E402  不算專案工作的 commit,定義在 hublib
GIT_WRITE = re.compile(r"\bgit\b(?:\s+-C\s+\S+)?[^|;&]*\b(commit|push)\b")
MIN_UNRECORDED = 2        # 落後 >= 2 個 commit 就提醒
MAX_AGE_MINUTES = 30      # 或最早那個沒記的 commit 已經超過 30 分鐘


def unrecorded_commits(root: pathlib.Path):
    """回傳最新筆記(或 onboard 那一刻)之後、代表專案工作的 commit 時間列表;不適用回傳 None。"""
    mem = root / ".agent-memory" / "memory"
    if not mem.is_dir():
        return None
    notes = [p.stat().st_mtime for p in mem.rglob("*.md")]
    # 基準至少是 onboard 那一刻(memory 分支第一個 commit):onboard 之前的工作不算欠筆記
    onboarded = subprocess.run(["git", "-C", str(root / ".agent-memory"), "log", "--reverse",
                                "--format=%ct", "agent-memory"],
                               capture_output=True, text=True).stdout.split()
    baseline = max(notes + ([int(onboarded[0])] if onboarded else []), default=0)
    out = subprocess.run(["git", "-C", str(root), "log", f"--since={int(baseline)}",
                          "--format=%ct%x09%s", "--", ".", *NON_WORK_PATHS],
                         capture_output=True, text=True).stdout
    times = []
    for line in out.splitlines():
        ts, _, subject = line.partition("\t")
        if ts.isdigit() and int(ts) > baseline and is_work_subject(subject):
            times.append(int(ts))
    return times


def should_nudge(times) -> bool:
    if not times:
        return False
    oldest_age = (datetime.datetime.now().timestamp() - min(times)) / 60
    return len(times) >= MIN_UNRECORDED or oldest_age >= MAX_AGE_MINUTES


def message(times) -> str:
    today = datetime.date.today().isoformat()
    since = datetime.datetime.fromtimestamp(min(times)).strftime("%m-%d %H:%M")
    return (f"memory 筆記落後了:從 {since} 起這個專案有 {len(times)} 個 commit 還沒記進 "
            f".agent-memory/memory/。在 .agent-memory/memory/{today}.md 補一段做了什麼、"
            f"做了什麼決定、踩到什麼坑(原始紀錄就好,不用摘要),再繼續。")


def nudge_text(root, command: str | None = None) -> str | None:
    """平台的邏輯:memory 筆記比最新 commit 舊就回傳提醒文字(沒落後就 None)。跟執行環境無關。
    command 給了 = 剛跑完一個指令(只有 git commit/push 之類才提醒);沒給 = session 剛開始。"""
    root = pathlib.Path(root)
    if command is not None and (not GIT_WRITE.search(command) or ".agent-memory" in command
                                or ".agent-memory" in root.parts):
        return None
    times = unrecorded_commits(root)
    return message(times) if times and should_nudge(times) else None


def main() -> None:
    """舊的入口:worktree 的使用者層級 hook 跟還沒換新設定的專案還在叫它(Knas 改使用者層級設定之前都要能用)。
    --post-tool 讀 hook 的輸入、輸出透過執行環境的 inject,跟以前一字不差;--session-start 直接印文字。"""
    import runtime_ports
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--post-tool":
        rt = runtime_ports.load(ports=("lifecycle",))
        ev = rt.parse_event("tool_used", sys.stdin.read())
        if ev is None:
            return
        text = nudge_text(ev.cwd or pathlib.Path.cwd(), ev.command or "")
        if text:
            print(rt.inject("tool_used", text))
    elif mode == "--session-start":
        text = nudge_text(pathlib.Path.cwd())
        if text:
            print(text)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # 提醒失敗不該影響 session
        pass
