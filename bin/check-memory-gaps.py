#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""找出「有在對話、但 memory 沒在記」的專案——零 LLM 成本的機械檢查。

memory 筆記靠 agent 自己寫。session 比 onboard 早開(沒讀到說明)、或 agent 忙到
沒記,memory 就一直空著,librarian 沒東西可整理,也沒有人會發現。project-06
就是這樣:2026-09-21 onboard 後 88 則對話、0 筆 memory。

做法:對每個 onboarded 專案,數最近 WINDOW 小時內使用者在那個資料夾送出的訊息數
(含開在它底下的 worktree 裡的 session),再數同一段時間 agent 寫進 memory/ 的 commit
數。訊息數 >= THRESHOLD 而筆記 commit 為 0,就列出來。

筆記 commit 讀 workspace 的 .agent-memory(session 寫完、hook/每小時同步就 commit 在那裡;~/.memory-copies
要等 librarian 拉),而且不算平台自己的 commit:consolidate 把舊日期檔整理進 reference/、librarian 寫的、
onboard 的骨架(init:)、同步時的 merge——這些都會動到 memory/,算進去的話 agent 完全沒記也看不出來。

跑在 mini(librarian 呼叫);transcript 由 MBP 的 transcript-backup 每小時同步過來。
MBP 連不到 mini 的那段時間(例如 2026-09-30 18:18–22:24)transcript 不會更新,
這段期間的對話要等恢復連線後的下一次檢查才數得到。

用法:check-memory-gaps.py [--window-hours 72] [--threshold 10]
輸出:有缺口時每個專案一行 markdown(給 todo.md / digest 用),沒有就什麼都不印。
"""
import argparse
import datetime
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import hublib  # noqa: E402
import runtime_ports  # noqa: E402  對話紀錄只透過介面讀(Claude Code 只是一個實作)

# 平台自己寫進 memory/ 的 commit(不是 agent 的筆記);merge 另外用 --no-merges 排除
PLATFORM_SUBJECTS = ("^init:", "^consolidate", "^librarian")


def human_messages_since(paths: list[str], since: datetime.datetime) -> int:
    """只算 Knas 實際送出的訊息(2026-10-03 前連其他 session 的訊息、背景通知也算進去)。"""
    rt = runtime_ports.load(ports=("transcripts",))
    return sum(1 for t in rt.turns(paths, since, worktrees=True) if t.role == "human")


def note_commits_since(workspace: str, since: datetime.datetime) -> int:
    """workspace 的 .agent-memory 在 since 之後有幾個 agent 筆記 commit;沒有 .agent-memory 回 -1。"""
    repo = pathlib.Path(workspace).expanduser() / ".agent-memory"
    if not (repo / ".git").exists():
        return -1
    out = subprocess.run(
        ["git", "-C", str(repo), "log", f"--since={since.isoformat()}", "--format=%h", "--no-merges",
         "--invert-grep", *(f"--grep={g}" for g in PLATFORM_SUBJECTS), "--", "memory/"],
        capture_output=True, text=True,
    ).stdout
    return len(out.split())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-hours", type=int, default=72)
    ap.add_argument("--threshold", type=int, default=10)
    args = ap.parse_args()
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=args.window_hours)

    roster = hubconf.roster()
    for name, paths in hublib.project_paths().items():
        if name not in roster or not roster[name].get("workspace"):
            continue  # agent-hub / project-29 沒有 memory
        msgs = human_messages_since(paths, since)
        if msgs < args.threshold:
            continue
        commits = note_commits_since(roster[name]["workspace"], since)
        if commits == 0:
            print(f"- [ ] **{name}**:最近 {args.window_hours} 小時有 {msgs} 則對話,memory 卻沒有任何新筆記"
                  f"——請 {name} 的 session 把重要決定/進度整理進 `.agent-memory/memory/`")


if __name__ == "__main__":
    main()
