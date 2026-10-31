#!/usr/bin/env bash
# 新機器/mini 重灌後,重新建立一個在 services.tsv 裡登記過的背景服務。
# 只有 agent-hub 自己管的 plist(plist_location 開頭是 agent-hub/launchd/)
# 能全自動處理;其他 owner(OpenClaw、Homebrew、GitHub Actions 等)的東西
# 印出 recreate_notes 讓人自己判斷,不會用猜的去重建——那些不是 agent-hub
# 管轄範圍,亂做反而可能弄壞。
#
# 用法:recover-service.sh <label>
set -euo pipefail

AGENT_HUB="${AGENT_HUB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
source "$(dirname "$0")/hubpaths.sh"
MANIFEST="$HUB_CONFIG/services.tsv"

fail() { echo "recover-service: $1" >&2; exit 1; }

[ $# -eq 1 ] || fail "$(hub_msg "usage: recover-service.sh <label>")"
LABEL="$1"

[ -f "$MANIFEST" ] || fail "$(hub_msg "{path} not found" path="$MANIFEST")"

row=$(awk -F'\t' -v n="$LABEL" '$1==n{print; found=1} END{exit !found}' "$MANIFEST") \
  || fail "$(hub_msg "'{label}' is not in {path}" label="$LABEL" path="$MANIFEST")"

project=$(echo "$row" | cut -f2)
owner=$(echo "$row" | cut -f3)
plist_location=$(echo "$row" | cut -f4)
purpose=$(echo "$row" | cut -f5)
recreate_notes=$(echo "$row" | cut -f6)

case "$plist_location" in
  projects/*)
    # 專案登錄裡宣告的 service:照登錄重新產生 plist 並載入
    "$AGENT_HUB/bin/py" "$AGENT_HUB/bin/render-services.py" --only "$LABEL"
    hub_say "Rebuilt from {source}: {label}" source="$plist_location" label="$LABEL"
    hub_say "Purpose: {purpose}" purpose="$purpose"
    if [ -n "$recreate_notes" ]; then hub_say "Note: {notes}" notes="$recreate_notes"; fi
    ;;
  agent-hub/launchd/*)
    plist_path="$AGENT_HUB/${plist_location#agent-hub/}"
    [ -f "$plist_path" ] || fail "$(hub_msg "{path} does not exist: is agent-hub itself behind (not pulled)?" path="$plist_path")"
    # launchd/ 底下是樣板:{{hub}} 換成這台的 hub 位置(用 sed,遙控那台不一定有平台的 Python)
    sed "s#{{hub}}#$AGENT_HUB#g" "$plist_path" >"$HOME/Library/LaunchAgents/${LABEL}.plist"
    launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/${LABEL}.plist"
    hub_say "Rebuilt and started: {label}" label="$LABEL"
    hub_say "Purpose: {purpose}" purpose="$purpose"
    if [ -n "$recreate_notes" ]; then hub_say "Note: {notes}" notes="$recreate_notes"; fi
    ;;
  *)
    hub_say "'{label}' is not a service agent-hub manages (owner: {owner}); it cannot be rebuilt automatically." label="$LABEL" owner="$owner"
    hub_say "Purpose: {purpose}" purpose="$purpose"
    hub_say "How to rebuild: {notes}" notes="$recreate_notes"
    ;;
esac
