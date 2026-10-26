#!/usr/bin/env bash
# 備份 Claude Code「個人」profile 的完整 session transcript
# (~/.claude-personal/projects/)到 mini,純備份、不處理、零 LLM 成本——
# 跟圖書館員的摘要管線完全分開(原始過程資料的價值不該用「先壓縮成摘要」
# 的方式犧牲掉,應該便宜地整份保留,以後有具體需求再挖)。
#
# 刻意只碰「個人」profile(~/.claude-personal),絕對不碰 ~/.claude(工作
# profile,裡面有公司專案的內容)——這條界線是使用者明確劃的,不要因為
# 方便就擴大範圍。
#
# 用 rsync -a(不加 --delete):mini 上的副本只會變多不會變少,就算本機的
# Claude Code 快取被清掉,mini 上還留著。
#
# 這支腳本要在 MBP(本機資料的來源機器)上跑,不是在 mini 上——跟 git
# mirror-backup 方向相反,因為 mini 沒辦法主動連進 MBP 讀檔案,只能等 MBP
# 醒著的時候自己把資料推過去。
set -uo pipefail
source "$(dirname "$0")/hubpaths.sh"

SRC="$HOME/.claude-personal/projects/"
DEST_HOST="$(hub_server)"   # 跑 hub 的那台(hub.json hosts.server)
[ -n "$DEST_HOST" ] || { echo "transcript-backup:hub.json 沒有 hosts.server(一台機器全包就不需要這支)" >&2; exit 0; }
DEST_PATH="transcript-backups/claude-personal-projects/"
LOG_DIR="$HOME/agent-hub/.logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/transcript-backup-$(date +%Y-%m-%d).log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

log "=== transcript-backup run start ==="

ssh "$DEST_HOST" "mkdir -p $DEST_PATH" 2>>"$LOG"

if rsync -az --stats "$SRC" "$DEST_HOST:$DEST_PATH" >>"$LOG" 2>&1; then
  log "transcript backup 完成"
else
  log "transcript backup 失敗,看 log 找原因"
fi

log "=== transcript-backup run end ==="
