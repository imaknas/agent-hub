#!/usr/bin/env python3
"""機械化合併 .claude/settings.json 的 SessionStart/SessionEnd hook 設定,
不覆寫既有內容、不重複加入同樣的 command、不需要任何判斷——純粹的
「這個 command 存在嗎?不存在就 append」。

兩種模式,差別只在要不要先 cd 進 .agent-memory,兩邊都只同步 memory/STATE.md,
不會有任何模式去動使用者自己的程式碼(no special cases——安全性不該靠「這個
目錄剛好只有 agent-hub 的檔案」這種隱性條件撐著,而是腳本本身就明確只加這
兩個路徑):
- native:專案本身就是 repo 根目錄,hook 直接對這個 repo 操作
- hosted:專案有獨立的 .agent-memory worktree,hook 要先 cd 進去再操作

用法:merge-hook-settings.py <settings.json 路徑> <native|hosted>
"""
import json
import sys
from pathlib import Path

COMMANDS = {
    "native": {
        "start": "~/agent-hub/bin/with-lock.sh git pull",
        "end": "~/agent-hub/bin/with-lock.sh ~/agent-hub/bin/git-sync-memory.sh",
    },
    "hosted": {
        "start": "cd .agent-memory && ~/agent-hub/bin/with-lock.sh git pull -q",
        "end": "cd .agent-memory && ~/agent-hub/bin/with-lock.sh ~/agent-hub/bin/git-sync-memory.sh",
    },
}


def has_command(entries, command):
    for e in entries:
        for h in e.get("hooks", []):
            if h.get("command") == command:
                return True
    return False


def main():
    if len(sys.argv) != 3 or sys.argv[2] not in COMMANDS:
        print("用法: merge-hook-settings.py <settings.json 路徑> <native|hosted>", file=sys.stderr)
        sys.exit(1)

    path = Path(sys.argv[1])
    cmds = COMMANDS[sys.argv[2]]
    settings = json.loads(path.read_text()) if path.exists() else {}

    hooks = settings.setdefault("hooks", {})

    start_entries = hooks.setdefault("SessionStart", [])
    if not has_command(start_entries, cmds["start"]):
        start_entries.append({"hooks": [{"type": "command", "command": cmds["start"]}]})

    end_entries = hooks.setdefault("SessionEnd", [])
    if not has_command(end_entries, cmds["end"]):
        end_entries.append({"hooks": [{"type": "command", "command": cmds["end"]}]})

    path.write_text(json.dumps(settings, indent=2) + "\n")


if __name__ == "__main__":
    main()
