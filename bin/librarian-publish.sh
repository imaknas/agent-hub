#!/usr/bin/env bash
# librarian 那一串夜間 job 的最後一步(平台 job agent-hub/librarian-publish,after: librarian、knas-tasks、todo、
# memory-gaps):重生 index.md 的專案名冊,再只 commit 這一串寫的檔案(今天的 digest、index.md、review-log.md、
# todo.md)。以前寫在 librarian.sh 的最後;拆出來之後,沒有助理(knas-tasks)的部署一樣照這個順序跑。
# 名冊在模型改過 index.md 之後才重生,就算模型誤動了區塊也會被蓋回正確內容。
# 只 commit 列出的檔案,不用 git add -A——working tree 裡可能有別人(其他 session、手動編輯)改到一半的東西。
set -uo pipefail
HUB="${AGENT_HUB_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
TODAY=$(date +%Y-%m-%d)
cd "$HUB" || exit 1
git pull -q || true

rc=0
"$HUB/bin/gen-index-roster.sh" || { echo "gen-index-roster 失敗"; rc=1; }
files=(index.md review-log.md todo.md)
[ -f "digest/$TODAY.md" ] && files+=("digest/$TODAY.md")
if "$HUB/bin/commit-hub-files.sh" "librarian: digest / index / review-log / todo $TODAY" "${files[@]}"; then
  echo "已 commit(有變化的話)並 push:${files[*]}"
else
  echo "commit/push 失敗:${files[*]}"
  rc=1
fi
exit $rc
