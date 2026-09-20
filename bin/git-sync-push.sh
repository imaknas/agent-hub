#!/usr/bin/env bash
# 給每個專案 .claude/settings.json 的 SessionEnd hook 用,取代原本的
# `git add -A && git commit ... ; git push -q` 內聯指令。
# 差別只有一個:push 交給 git-safe-push.sh,遇到分岔會自動 pull 合併重試,
# 不用每次都手動處理 rejected push。
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

git add -A
git commit -m 'session end' --allow-empty-message -q 2>/dev/null

"$SCRIPT_DIR/git-safe-push.sh"
