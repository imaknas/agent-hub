#!/usr/bin/env bash
# 給「native」類專案(直接在專案根目錄操作,不是獨立 .agent-memory worktree)
# 的 SessionEnd hook 用。跟 git-sync-push.sh 的差別,也是存在的理由:
# 只 add/commit memory/ 跟 STATE.md 這兩個 agent-hub 自己的檔案,不會
# `git add -A` 把使用者還沒準備好 commit 的其他程式碼改動一起打包進去。
#
# 沒有東西要 commit 就直接結束,不留空 commit、不做多餘的 push。
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# memory 的 commit/push 一律不跑專案自己的 git hook:.agent-memory 是同一個 repo 的
# worktree,會共用 core.hooksPath(例:專案的 husky 跑 npx lint-staged)。
# launchd 底下沒有 npx,hook 失敗讓筆記卡住沒 commit(2026-10-01);有 npx 時又會被
# 專案的 prettier 改寫格式。memory 筆記不是專案程式碼,不該經過專案的 lint/格式化。
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null

git add memory/ STATE.md 2>/dev/null

if git diff --cached --quiet; then
  exit 0
fi

git commit -q -m "agent-hub: sync memory/STATE.md"
# 連不到 mini 時(呼叫端已確認過)只 commit 在本機,之後有連線的那一輪會補 push
[ -n "${AGENT_HUB_OFFLINE:-}" ] && exit 0
"$SCRIPT_DIR/git-safe-push.sh"
