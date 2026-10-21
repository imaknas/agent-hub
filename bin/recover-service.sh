#!/usr/bin/env bash
# 新機器/mini 重灌後,重新建立一個在 services.tsv 裡登記過的背景服務。
# 只有 agent-hub 自己管的 plist(plist_location 開頭是 agent-hub/launchd/)
# 能全自動處理;其他 owner(OpenClaw、Homebrew、GitHub Actions 等)的東西
# 印出 recreate_notes 讓人自己判斷,不會用猜的去重建——那些不是 agent-hub
# 管轄範圍,亂做反而可能弄壞。
#
# 用法:recover-service.sh <label>
set -euo pipefail

AGENT_HUB="$HOME/agent-hub"
MANIFEST="$AGENT_HUB/services.tsv"

fail() { echo "recover-service: $1" >&2; exit 1; }

[ $# -eq 1 ] || fail "用法: recover-service.sh <label>"
LABEL="$1"

[ -f "$MANIFEST" ] || fail "找不到 $MANIFEST"

row=$(awk -F'\t' -v n="$LABEL" '$1==n{print; found=1} END{exit !found}' "$MANIFEST") \
  || fail "在 $MANIFEST 裡找不到 '$LABEL'"

project=$(echo "$row" | cut -f2)
owner=$(echo "$row" | cut -f3)
plist_location=$(echo "$row" | cut -f4)
purpose=$(echo "$row" | cut -f5)
recreate_notes=$(echo "$row" | cut -f6)

case "$plist_location" in
  projects/*)
    # 專案登錄裡宣告的 service:照登錄重新產生 plist 並載入
    /opt/homebrew/bin/python3 "$AGENT_HUB/bin/render-services.py" --only "$LABEL"
    echo "已照 $plist_location 重建:$LABEL"
    echo "用途:$purpose"
    [ -n "$recreate_notes" ] && echo "注意:$recreate_notes"
    ;;
  agent-hub/launchd/*)
    plist_path="$AGENT_HUB/${plist_location#agent-hub/}"
    [ -f "$plist_path" ] || fail "$plist_path 不存在——agent-hub 本身是不是還沒 pull 到最新?"
    cp "$plist_path" "$HOME/Library/LaunchAgents/${LABEL}.plist"
    launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/${LABEL}.plist"
    echo "已重建並啟動:$LABEL"
    echo "用途:$purpose"
    [ -n "$recreate_notes" ] && echo "注意:$recreate_notes"
    ;;
  *)
    echo "'$LABEL' 不是 agent-hub 管轄的服務(owner: $owner),無法自動重建。"
    echo "用途:$purpose"
    echo "怎麼重建:$recreate_notes"
    ;;
esac
