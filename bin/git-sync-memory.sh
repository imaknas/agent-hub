#!/usr/bin/env bash
# 給「native」類專案(直接在專案根目錄操作,不是獨立 .agent-memory worktree)
# 的 SessionEnd hook 用。跟 git-sync-push.sh 的差別,也是存在的理由:
# 只 add/commit memory/ 跟 STATE.md 這兩個 agent-hub 自己的檔案,不會
# `git add -A` 把使用者還沒準備好 commit 的其他程式碼改動一起打包進去。
#
# 沒有東西要 commit 就直接結束,不留空 commit、不做多餘的 push。
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

git add memory/ STATE.md 2>/dev/null

if git diff --cached --quiet; then
  exit 0
fi

git commit -q -m "agent-hub: sync memory/STATE.md"
"$SCRIPT_DIR/git-safe-push.sh"
