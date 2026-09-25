#!/usr/bin/env bash
# 對 onboarded-projects.tsv 裡每個本機有 .agent-memory 的專案,補跑一次
# memory 同步——SessionEnd/PreCompact hook 的安全網。session 沒正常結束
# (當掉、一直開著沒關)的話,當天寫的 memory/ 筆記會一直留在 MBP 本機,
# mini 上的 librarian 看不到。
#
# 跑在 MBP(專案資料夾在這裡),launchd 每小時一次(local.sync-all-memory)。
# 零 LLM 成本;沒東西要 commit 就什麼都不做。
#
# 每個專案:with-lock 底下跑 git-sync-memory.sh(只 add memory/ 跟 STATE.md,
# 絕不 git add -A),再把還沒 push 出去的 commit(例如上次 push 失敗、或
# sync-local-setup.sh 的 AGENT.md commit)補 push。
set -uo pipefail

HUB="$HOME/agent-hub"
MANIFEST="$HUB/onboarded-projects.tsv"
LOG_DIR="$HUB/.logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/sync-all-memory-$(date +%Y-%m-%d).log"

log() { echo "[$(date '+%H:%M:%S')] $*" >>"$LOG"; }

tail -n +2 "$MANIFEST" | cut -f1,2 | while IFS=$'\t' read -r name path; do
  mem="$path/.agent-memory"
  [ -d "$mem" ] || continue
  (
    cd "$mem" || exit 1
    before=$(git rev-parse HEAD)
    "$HUB/bin/with-lock.sh" "$HUB/bin/git-sync-memory.sh" >>"$LOG" 2>&1 \
      || log "$name: git-sync-memory 失敗"
    [ "$(git rev-parse HEAD)" != "$before" ] && log "$name: 已 commit+push 新的 memory"
    if [ -n "$(git rev-list '@{u}..HEAD' 2>/dev/null)" ]; then
      "$HUB/bin/with-lock.sh" "$HUB/bin/git-safe-push.sh" >>"$LOG" 2>&1 \
        && log "$name: 補 push 了之前沒推出去的 commit" \
        || log "$name: 補 push 失敗"
    fi
  )
done
exit 0
