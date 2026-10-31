#!/usr/bin/env bash
# 讀 agent-hub/mirror-list.txt(每行一個 git remote URL),對每個 URL 在 mini
# 上維護一份 --mirror clone 當災難復原備份。純只讀:對外部 remote(GitHub)
# 只 fetch,不 push 任何東西,不會觸發任何 CI/部署。
#
# 由 schedule.yaml 排程執行,不用另外開 plist。
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

HUB="${AGENT_HUB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
source "$(dirname "$0")/hubpaths.sh"
LIST="$HUB_CONFIG/mirror-list.txt"
GIT_ORIGIN="$(hub_json_get git_origin)"; GIT_ORIGIN="${GIT_ORIGIN:-~/git-origin}"; GIT_ORIGIN="${GIT_ORIGIN/#\~/$HOME}"
MIRROR_DIR="$GIT_ORIGIN/mirrors"
LOG_DIR="$HUB/.logs"
mkdir -p "$MIRROR_DIR" "$LOG_DIR"
LOG="$LOG_DIR/mirror-backup-$(date +%Y-%m-%d).log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

if [ ! -f "$LIST" ]; then
  log "$(hub_msg "{list} does not exist; no projects to back up" list="$LIST")"
  exit 0
fi

log "=== mirror-backup run start ==="

while IFS= read -r url; do
  [ -z "$url" ] && continue
  [[ "$url" == \#* ]] && continue

  name=$(basename "$url" .git)
  dest="$MIRROR_DIR/$name.git"

  if [ -d "$dest" ]; then
    if git --git-dir="$dest" remote update --prune >>"$LOG" 2>&1; then
      log "$(hub_msg "{name}: mirror updated" name="$name")"
    else
      log "$(hub_msg "{name}: remote update failed, the old mirror is left as it was" name="$name")"
    fi
  else
    if git clone --mirror "$url" "$dest" >>"$LOG" 2>&1; then
      log "$(hub_msg "{name}: first clone --mirror done" name="$name")"
    else
      log "$(hub_msg "{name}: clone --mirror failed" name="$name")"
      rm -rf "$dest"
    fi
  fi
done < "$LIST"

log "=== mirror-backup run end ==="
