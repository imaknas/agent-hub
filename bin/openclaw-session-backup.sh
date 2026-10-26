#!/usr/bin/env bash
# 備份 OpenClaw 在 mini 上的完整 session 對話紀錄(~/.openclaw/agents/*/
# sessions/)到 MBP,純備份、不處理、零 LLM 成本——跟 cross-channel-memory
# 那種「先摘要再寫進 memory/log」的做法完全分開,原始對話不該被摘要犧牲掉。
#
# 跟 transcript-backup.sh 方向相反:那支是 MBP 的資料推去 mini(因為 mini
# 24/7、MBP 會睡);這支的資料源頭本來就在 mini(OpenClaw 的 session 檔案),
# 要備份到「不是 mini 自己」的地方才有意義,所以反過來讓 MBP(醒著的時候)
# 主動去 mini 拉。這支要在 MBP 上跑,不是在 mini 上——跟 transcript-backup.sh
# 一樣,只是方向相反。
#
# 用 rsync -a(不加 --delete):MBP 上的副本只會變多不會變少,就算 mini 上
# 的 OpenClaw session 檔案之後被清掉,MBP 這邊還留著。
#
# 為什麼還要備份一個要退役的系統:project-36 的 cron job 目前還在 OpenClaw
# 上跑(還沒遷移,故意排在最後),所以 session 檔案還在持續增加,不是純歷史
# 資料;而且就算 OpenClaw 全部退役了,這些對話紀錄本身仍然有價值(不只是
# 圖書館員摘要進 memory/log 的那些精煉版本)。
#
# 2026-09-22:原本掛了每天排程(launchd),使用者確認不需要——真正要的是把
# session 內容沉澱進 agent-hub 能管理的專案 memory(見各專案 .agent-memory/
# memory/ 底下 openclaw-raw-transcript-backfill 這類筆記),不是持續鏡像
# 原始檔案本身。排程已經拔掉,這支腳本留著只給手動跑:已經有一份 2026-09-22
# 的快照在 ~/openclaw-session-backups/,之後如果需要「最後一次快照」(例如
# OpenClaw 真的要整個停用之前)手動執行即可,不用重新設計。
set -uo pipefail
source "$(dirname "$0")/hubpaths.sh"

SRC_HOST="$(hub_server)"   # 跑 hub 的那台(hub.json hosts.server)
SRC_PATH="~/.openclaw/agents/"
DEST="$HOME/openclaw-session-backups/agents/"
LOG_DIR="$HOME/agent-hub/.logs"
mkdir -p "$LOG_DIR" "$DEST"
LOG="$LOG_DIR/openclaw-session-backup-$(date +%Y-%m-%d).log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

log "=== openclaw-session-backup run start ==="

if rsync -az --stats -e ssh "$SRC_HOST:$SRC_PATH" "$DEST" >>"$LOG" 2>&1; then
  log "openclaw session backup 完成"
else
  log "openclaw session backup 失敗,看 log 找原因"
fi

log "=== openclaw-session-backup run end ==="
