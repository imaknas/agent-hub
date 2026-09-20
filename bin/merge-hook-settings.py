#!/usr/bin/env python3
"""機械化合併 .claude/settings.json 的 SessionStart/SessionEnd hook 設定,
不覆寫既有內容、不重複加入同樣的 command、不需要任何判斷——純粹的
「這個 command 存在嗎?不存在就 append」。

只有一種模式:memory 追蹤永遠在獨立的 .agent-memory worktree 裡,hook 一律
先 cd 進去再操作,不管這個專案有沒有外部 remote。

`.claude/settings.json` 會跟著這個 repo 到任何 clone 它的機器——如果專案是
多機器共用(例如同一個 repo 在 Mac 跟 Windows 都有人開發),`.agent-memory`
只存在於實際跑過 onboarding 的那台機器上(它被 gitignore 排除,不會隨
git clone/pull 帶過去)。所以 hook 一律先檢查 `.agent-memory` 存不存在,
不存在就整條安靜地不做事(用 `|| true` 保證結束碼永遠是 0,不會讓其他機器
上的 session 因為這個 hook 而顯示錯誤)。

用法:merge-hook-settings.py <settings.json 路徑>
"""
import json
import sys
from pathlib import Path

START_CMD = "[ -d .agent-memory ] && cd .agent-memory && ~/agent-hub/bin/with-lock.sh git pull -q || true"
END_CMD = "[ -d .agent-memory ] && cd .agent-memory && ~/agent-hub/bin/with-lock.sh ~/agent-hub/bin/git-sync-memory.sh || true"


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
