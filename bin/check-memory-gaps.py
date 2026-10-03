#!/usr/bin/env python3
"""找出「有在對話、但 memory 沒在記」的專案——零 LLM 成本的機械檢查。

memory 筆記靠 agent 自己寫。session 比 onboard 早開(沒讀到說明)、或 agent 忙到
沒記,memory 就一直空著,librarian 沒東西可整理,也沒有人會發現。project-06
就是這樣:2026-09-21 onboard 後 88 則對話、0 筆 memory。

做法:對每個 onboarded 專案,數最近 WINDOW 小時內使用者在那個資料夾送出的訊息數
(從 MBP 備份到 mini 的 Claude Code transcript),再數同一段時間 memory/ 的 commit
數。訊息數 >= THRESHOLD 而 memory commit 為 0,就列出來。

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
import hublib  # noqa: E402

TRANSCRIPTS = hublib.HOME / "transcript-backups" / "claude-personal-projects"
MEMORY_COPIES = hublib.HOME / "projects"


def human_messages_since(paths: list[str], since: datetime.datetime) -> int:
    """只算 Knas 實際送出的訊息(2026-10-03 前連其他 session 的訊息、背景通知也算進去)。"""
    return sum(1 for e in hublib.iter_entries(TRANSCRIPTS, paths, since)
               if hublib.is_human_prompt(e.entry))


def memory_commits_since(name: str, since: datetime.datetime) -> int:
    repo = MEMORY_COPIES / name
    if not (repo / ".git").exists():
        return -1
    out = subprocess.run(
        # onboard 時的骨架 commit(init:)只放了 memory/.gitkeep,不算筆記
        ["git", "-C", str(repo), "log", f"--since={since.isoformat()}", "--format=%h",
         "--invert-grep", "--grep=^init:", "--", "memory/"],
        capture_output=True, text=True,
    ).stdout
    return len(out.split())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-hours", type=int, default=72)
    ap.add_argument("--threshold", type=int, default=10)
    args = ap.parse_args()
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=args.window_hours)

    onboarded = {l.split("\t")[0] for l in hublib.MANIFEST.read_text().splitlines()[1:] if l.strip()}
    for name, paths in hublib.project_paths().items():
        if name not in onboarded:
            continue  # agent-hub / project-29 沒有 memory 副本
        msgs = human_messages_since(paths, since)
        if msgs < args.threshold:
            continue
        commits = memory_commits_since(name, since)
        if commits == 0:
            print(f"- [ ] **{name}**:最近 {args.window_hours} 小時有 {msgs} 則對話,memory 卻沒有任何新筆記"
                  f"——請 {name} 的 session 把重要決定/進度整理進 `.agent-memory/memory/`")


if __name__ == "__main__":
    main()
