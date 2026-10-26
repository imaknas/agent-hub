#!/usr/bin/env bash
# 對專案登錄(projects/*.yaml)裡每個本機有 .agent-memory 的專案,補跑一次
# memory 同步——SessionEnd/PreCompact hook 的安全網。session 沒正常結束
# (當掉、一直開著沒關)的話,當天寫的 memory/ 筆記會一直留在 MBP 本機,
# mini 上的 librarian 看不到。
#
# 跑在 mini(local.sync-all-memory-mini),launchd 每小時一次:manifest 的路徑(~/workspace/<name>)。
# MBP 上也還有一份(local.sync-all-memory),但 2026-10-15 所有專案搬到 mini 之後,manifest 的路徑
# 在 MBP 上都不存在,那份實際上不做事;MBP 上留著的備份副本不再同步。
# 零 LLM 成本;沒東西要 commit 就什麼都不做。
#
# 每個專案:with-lock 底下跑 git-sync-memory.sh(只 add memory/ 跟 STATE.md,
# 絕不 git add -A),再把還沒 push 出去的 commit(例如上次 push 失敗、或
# sync-local-setup.sh 的 AGENT.md commit)補 push。
set -uo pipefail

HUB="${AGENT_HUB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
SERVER="$(plutil -extract hosts.server raw -o - "$HUB/hub.json" 2>/dev/null || plutil -extract memory_host raw -o - "$HUB/hub.json" 2>/dev/null)"   # 跑 hub 的那台(hub.json hosts.server);空 = 就是這台
LOG_DIR="$HUB/.logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/sync-all-memory-$(date +%Y-%m-%d).log"

# 補 push 也一樣:memory 的 commit/push 一律不跑專案自己的 git hook:.agent-memory 是同一個 repo 的
# worktree,會共用 core.hooksPath(例:project-06 的 husky 跑 npx lint-staged)。
# launchd 底下沒有 npx,hook 失敗讓筆記卡住沒 commit(2026-10-01);有 npx 時又會被
# 專案的 prettier 改寫格式。memory 筆記不是專案程式碼,不該經過專案的 lint/格式化。
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null

log() { echo "[$(date '+%H:%M:%S')] $*" >>"$LOG"; }

# 先確認連得到 mini。連不到(MBP 不在家、tailnet 斷線;2026-09-30、10-08、10-13 都發生過)
# 就只在本機 commit、不 push,整輪只記一行——不然每個專案各等一次 SSH 逾時,
# 一整天累積上百行失敗紀錄。恢復連線後的那一輪會把沒推出去的 commit 補 push。
# 在 mini 上(~/git-origin 在本機)memory remote 是本機路徑,不用檢查。
if [ -z "$SERVER" ] || [ -d "$HOME/git-origin" ] || ssh -o ConnectTimeout=10 -o BatchMode=yes "$SERVER" true 2>/dev/null; then
  OFFLINE=""
else
  OFFLINE=1
  log "連不到 mini,這輪只在本機 commit、不 push"
fi
export AGENT_HUB_OFFLINE="$OFFLINE"

# manifest 的路徑從 2026-10-15 起就是 mini 的 ~/workspace/<name>;另外掃一次 ~/workspace,
# 涵蓋還沒登記的資料夾。同一個路徑只做一次。
{
  "$HUB/bin/py" "$HUB/bin/hubconf.py" roster | cut -f1,2
  for d in "$HOME"/workspace/*/; do
    [ -d "$d.agent-memory" ] && printf '%s\t%s\n' "$(basename "$d")" "${d%/}"
  done
} | awk -F'\t' '!seen[$2]++' | while IFS=$'\t' read -r name path; do
  mem="$path/.agent-memory"
  [ -d "$mem" ] || continue
  (
    cd "$mem" || exit 1
    before=$(git rev-parse HEAD)
    "$HUB/bin/with-lock.sh" "$HUB/bin/git-sync-memory.sh" >>"$LOG" 2>&1 \
      || log "$name: git-sync-memory 失敗"
    [ "$(git rev-parse HEAD)" != "$before" ] && log "$name: 已 commit${OFFLINE:+(連不到 mini,先不 push)}${OFFLINE:-+push} 新的 memory"
    if [ -z "$OFFLINE" ] && [ -n "$(git rev-list '@{u}..HEAD' 2>/dev/null)" ]; then
      "$HUB/bin/with-lock.sh" "$HUB/bin/git-safe-push.sh" >>"$LOG" 2>&1 \
        && log "$name: 補 push 了之前沒推出去的 commit" \
        || log "$name: 補 push 失敗"
    fi
  )
done
exit 0
