#!/usr/bin/env bash
# 給 agent 用的集中化總覽:每個專案的路徑/origin、跟它綁定的服務(有沒有
# 在跑,如果這台機器上跑得到 launchctl 的話)、還有它目前的 STATE.md。
#
# 刻意不做成另一份要手動維護、會跟真相脫節的檔案——onboarded-projects.tsv、
# services.tsv、每個專案自己的 .agent-memory/STATE.md 已經是三個獨立但
# 各自準確的真相來源,這支腳本只是每次執行時即時把三個接起來顯示,不儲存
# 任何自己的狀態。
#
# 用法:overview.sh [專案名稱]——不帶參數就列出全部專案的總覽。
set -uo pipefail

AGENT_HUB="$HOME/agent-hub"
PROJECTS="$AGENT_HUB/onboarded-projects.tsv"
SERVICES="$AGENT_HUB/services.tsv"

[ -f "$PROJECTS" ] || { echo "overview: 找不到 $PROJECTS" >&2; exit 1; }

FILTER="${1:-}"

tail -n +2 "$PROJECTS" | while IFS=$'\t' read -r name local_path origin memory_bare_repo; do
  [ -z "$name" ] && continue
  [ -n "$FILTER" ] && [ "$name" != "$FILTER" ] && continue

  echo "=========================================="
  echo "專案:$name"
  echo "路徑:$local_path"
  echo "origin:$origin"
  echo

  echo "-- 綁定的服務 --"
  if [ -f "$SERVICES" ]; then
    matched=$(awk -F'\t' -v p="$name" '$2==p{print}' "$SERVICES")
    if [ -z "$matched" ]; then
      echo "(沒有登記任何服務)"
    else
      echo "$matched" | while IFS=$'\t' read -r label _project owner _plist purpose _notes; do
        running="(這台機器上無法查 launchctl 狀態,或不是用 launchd 管理)"
        if command -v launchctl >/dev/null 2>&1; then
          if launchctl list 2>/dev/null | awk '{print $3}' | grep -qxF "$label"; then
            running="running"
          else
            running="NOT running"
          fi
        fi
        echo "  - $label [$running] ($owner) — $purpose"
      done
    fi
  fi
  echo

  echo "-- STATE.md --"
  state_file="$local_path/.agent-memory/STATE.md"
  if [ -f "$state_file" ]; then
    cat "$state_file"
  else
    echo "(這台機器上讀不到 $state_file——路徑不存在,或這個專案沒 clone 在這台機器上)"
  fi
  echo
done
