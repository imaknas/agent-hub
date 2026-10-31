#!/usr/bin/env bash
# 把「push 因為遠端領先而被拒絕」的情況自動用 pull(merge)重試解決。
#
# 背景:agent-hub 這類 repo 會同時被 MBP(手動編輯腳本)跟 mini(librarian/
# hook 自動寫入)兩邊寫,單純的 `git push` 常常因為對方剛好先推過而被拒絕,
# 每次都要人工 pull+merge 才能重推。這支腳本把「divergent history 但沒有真的
# 內容衝突」這種常見情況自動處理掉。
#
# 如果是真的內容衝突(兩邊改了同一個檔案的同一段,merge 沒辦法自動合併),
# 不會用任何策略幫你硬猜要留哪一邊——直接印出錯誤、以非 0 結束碼結束,
# 這種情況本來就該讓人來看。
#
# 用法:在已經 cd 進目標 repo 之後呼叫,例如 git-safe-push.sh 或
# git-safe-push.sh origin main。
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/hubpaths.sh"

max_attempts=5
attempt=1

while [ $attempt -le $max_attempts ]; do
  push_err=$(git push -q "$@" 2>&1) && exit 0

  if echo "$push_err" | grep -qE "rule violations|GH0[0-9]+|required_signatures|protected branch"; then
    # branch protection 規則擋下(例如要求走 PR、要求簽名),不是分岔造成的,
    # pull 重試沒有用,重試只會浪費時間。
    hub_say "git-safe-push: blocked by a branch protection rule, not by diverged history; retrying will not help:" >&2
    echo "$push_err" >&2
    exit 1
  fi

  if ! echo "$push_err" | grep -qE "rejected|fetch first|non-fast-forward"; then
    # 不是分岔造成的(例如網路斷線、認證失敗),重試沒有意義。
    echo "$push_err" >&2
    exit 1
  fi

  if ! pull_err=$(git pull --no-rebase -q 2>&1); then
    hub_say "git-safe-push: the pull could not merge, possibly a real content conflict; a person has to look:" >&2
    echo "$pull_err" >&2
    exit 1
  fi

  attempt=$((attempt + 1))
done

hub_say "git-safe-push: the push still fails after {n} attempts" n="$max_attempts" >&2
exit 1
