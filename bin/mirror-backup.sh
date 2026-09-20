#!/usr/bin/env bash
# 讀 agent-hub/mirror-list.txt(每行一個 git remote URL),對每個 URL 在 mini
# 上維護一份 --mirror clone 當災難復原備份。純只讀:對外部 remote(GitHub)
# 只 fetch,不 push 任何東西,不會觸發任何 CI/部署。
#
# 由 schedule.yaml 排程執行,不用另外開 plist。
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

HUB="$HOME/agent-hub"
LIST="$HUB/mirror-list.txt"
MIRROR_DIR="$HOME/git-origin/mirrors"
LOG_DIR="$HUB/.logs"
mkdir -p "$MIRROR_DIR" "$LOG_DIR"
LOG="$LOG_DIR/mirror-backup-$(date +%Y-%m-%d).log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

if [ ! -f "$LIST" ]; then
  log "沒有 $LIST,沒有要備份的專案"
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
      log "$name: mirror 已更新"
    else
      log "$name: remote update 失敗,保留舊的 mirror 不動"
    fi
  else
    if git clone --mirror "$url" "$dest" >>"$LOG" 2>&1; then
      log "$name: 第一次 clone --mirror 完成"
    else
      log "$name: clone --mirror 失敗"
      rm -rf "$dest"
    fi
  fi
done < "$LIST"

log "=== mirror-backup run end ==="
