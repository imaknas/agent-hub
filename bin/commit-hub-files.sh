#!/usr/bin/env bash
# 腳本寫了 agent-hub 裡被版控的檔案之後,用這支 commit+push——只 commit
# 明確列出的路徑,不會帶到 working tree 裡其他人(其他 session、手動編輯)
# 還沒 commit 的東西。整段在 with-lock.sh 底下跑,push 走 git-safe-push.sh。
#
# 規則:哪支腳本寫了被版控的檔案,就由那支腳本自己呼叫這支 commit+push,
# 不要留給「之後剛好有人手動 commit」(project-36 的登記就這樣卡過)。
#
# 用法:commit-hub-files.sh "<commit message>" <path>...(路徑相對於 agent-hub)
# 列出的檔案都沒變化就什麼都不做,exit 0。
set -uo pipefail

HUB="${AGENT_HUB:-${AGENT_HUB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}}"   # 預設 = 這支腳本所在的 hub
cd "$HUB" || exit 1

if [ -z "${_COMMIT_HUB_LOCKED:-}" ]; then
  _COMMIT_HUB_LOCKED=1 exec "$HUB/bin/with-lock.sh" "$0" "$@"
fi

[ $# -ge 2 ] || { echo "用法: commit-hub-files.sh \"<message>\" <path>..." >&2; exit 1; }
msg="$1"; shift

# 不存在、也從來沒被追蹤過的路徑直接略過(例如某支腳本這次沒產生那個檔案),
# 不然 git add 會因為 pathspec 對不到而讓整個 commit 失敗。
paths=()
for f in "$@"; do
  if [ -e "$f" ] || git ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
    paths+=("$f")
  fi
done
[ ${#paths[@]} -gt 0 ] || { echo "commit-hub-files: $* 都不存在,不 commit"; exit 0; }
set -- "${paths[@]}"

if [ -z "$(git status --porcelain -- "$@")" ]; then
  echo "commit-hub-files: $* 沒有變化,不 commit"
  exit 0
fi

git add -- "$@" \
  && git commit -q -m "$msg" -- "$@" \
  && "$HUB/bin/git-safe-push.sh" \
  && echo "commit-hub-files: 已 commit+push:$*"
