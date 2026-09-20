#!/usr/bin/env python3
"""機械化合併 .claude/settings.json 的 SessionStart/SessionEnd hook 設定,
不覆寫既有內容、不重複加入同樣的 command、不需要任何判斷——純粹的
「這個 command 存在嗎?不存在就 append」。

只有一種模式:memory 追蹤永遠在獨立的 .agent-memory worktree 裡,hook 一律
先 cd 進去再操作,不管這個專案有沒有外部 remote。

用法:merge-hook-settings.py <settings.json 路徑>
"""
import json
import sys
from pathlib import Path

START_CMD = "cd .agent-memory && ~/agent-hub/bin/with-lock.sh git pull -q"
END_CMD = "cd .agent-memory && ~/agent-hub/bin/with-lock.sh ~/agent-hub/bin/git-sync-memory.sh"


def has_command(entries, command):
    for e in entries:
        for h in e.get("hooks", []):
            if h.get("command") == command:
                return True
    return False


def main():
    path = Path(sys.argv[1])
    settings = json.loads(path.read_text()) if path.exists() else {}

    hooks = settings.setdefault("hooks", {})

    start_entries = hooks.setdefault("SessionStart", [])
    if not has_command(start_entries, START_CMD):
        start_entries.append({"hooks": [{"type": "command", "command": START_CMD}]})

    end_entries = hooks.setdefault("SessionEnd", [])
    if not has_command(end_entries, END_CMD):
        end_entries.append({"hooks": [{"type": "command", "command": END_CMD}]})

    path.write_text(json.dumps(settings, indent=2) + "\n")


if __name__ == "__main__":
    main()
