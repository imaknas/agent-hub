#!/usr/bin/env python3
"""找出「有在對話、但 memory 沒在記」的專案——零 LLM 成本的機械檢查。

memory 筆記靠 agent 自己寫。session 比 onboard 早開(沒讀到說明)、或 agent 忙到
沒記,memory 就一直空著,librarian 沒東西可整理,也沒有人會發現。project-06
就是這樣:2026-09-21 onboard 後 88 則對話、0 筆 memory。

做法:對每個 onboarded 專案,數最近 WINDOW 小時內使用者在那個資料夾送出的訊息數
(從 MBP 備份到 mini 的 Claude Code transcript),再數同一段時間 memory/ 的 commit
數。訊息數 >= THRESHOLD 而 memory commit 為 0,就列出來。

跑在 mini(librarian 呼叫);transcript 由 MBP 的 transcript-backup 同步過來,
會有最多一小時的落差,窗口開 72 小時足夠涵蓋。

用法:check-memory-gaps.py [--window-hours 72] [--threshold 10]
輸出:有缺口時每個專案一行 markdown(給 todo.md / digest 用),沒有就什麼都不印。
"""
import argparse
import datetime
import json
import pathlib
import subprocess

HOME = pathlib.Path.home()
MANIFEST = HOME / "agent-hub" / "onboarded-projects.tsv"
TRANSCRIPTS = HOME / "transcript-backups" / "claude-personal-projects"
MEMORY_COPIES = HOME / "projects"


def slug(path: str) -> str:
    """Claude Code 用來存 transcript 的資料夾名:路徑裡的 / 跟 . 換成 -。"""
    return path.replace("/", "-").replace(".", "-")


def user_messages_since(project_path: str, since: datetime.datetime) -> int:
    d = TRANSCRIPTS / slug(project_path)
    count = 0
    for f in d.glob("*.jsonl"):
        with f.open(errors="replace") as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("type") != "user" or not isinstance(o.get("message", {}).get("content"), str):
                    continue  # tool result 之類不算,只算使用者實際打的訊息
                ts = o.get("timestamp")
                if ts and datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")) >= since:
                    count += 1
    return count


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

    for line in MANIFEST.read_text().splitlines()[1:]:
        name, path = line.split("\t")[:2]
        msgs = user_messages_since(path, since)
        if msgs < args.threshold:
            continue
        commits = memory_commits_since(name, since)
        if commits == 0:
            print(f"- [ ] **{name}**:最近 {args.window_hours} 小時有 {msgs} 則對話,memory 卻沒有任何新筆記"
                  f"——請 {name} 的 session 把重要決定/進度整理進 `.agent-memory/memory/`")


if __name__ == "__main__":
    main()
