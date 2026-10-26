#!/usr/bin/env bash
# 給 agent 用的集中化總覽:每個專案的路徑/origin、跟它綁定的服務(有沒有
# 在跑,如果這台機器上跑得到 launchctl 的話)、還有它目前的 STATE.md。
#
# 刻意不做成另一份要手動維護、會跟真相脫節的檔案——專案登錄(projects/*.yaml)、
# services.tsv、每個專案自己的 .agent-memory/STATE.md 已經是三個獨立但
# 各自準確的真相來源,這支腳本只是每次執行時即時把三個接起來顯示,不儲存
# 任何自己的狀態。
#
# 用法:overview.sh [專案名稱]——不帶參數就列出全部專案的總覽。
set -uo pipefail

AGENT_HUB="$HOME/agent-hub"
source "$(dirname "$0")/hubpaths.sh"
SERVICES="$HUB_CONFIG/services.tsv"


FILTER="${1:-}"

"$AGENT_HUB/bin/py" "$AGENT_HUB/bin/hubconf.py" roster | while IFS=$'\t' read -r name local_path origin memory_bare_repo; do
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
  # local_path 是 mini 上的 ~/workspace/<name>(2026-10-15 起);在 MBP 上跑時不存在,
  # 這時讀 mini 上 onboard-project.sh 步驟 9 建的 memory-only clone
  # (~/.memory-copies/<name>/STATE.md)——兩個路徑都試,不用管在哪台機器跑。
  state_dir="$local_path/.agent-memory"
  state_file="$state_dir/STATE.md"
  mini_clone_state_file="$HOME/.memory-copies/$name/STATE.md"
  # .agent-memory 平常只在該專案自己開新 session 時(SessionStart hook)才
  # 會 git pull——這支腳本是隨時可能在別的資料夾被叫用來看別的專案,不會
  # 觸發那個 hook,直接讀本地檔案可能讀到 librarian 已經 push 但這台機器
  # 還沒拉下來的舊內容。讀之前先自己 pull 一次,拉不到(離線、還沒連上
  # mini)就當作沒這回事,不讓整支腳本失敗。
  if [ -d "$state_dir" ]; then
    git -C "$state_dir" pull -q 2>/dev/null || true
  fi
  if [ -f "$state_file" ]; then
    cat "$state_file"
  elif [ -f "$mini_clone_state_file" ]; then
    echo "(讀自 mini 的 memory-only clone:$mini_clone_state_file)"
    cat "$mini_clone_state_file"
  else
    echo "(這台機器上讀不到 STATE.md——試過 $state_file 跟 $mini_clone_state_file,都不存在)"
  fi
  echo
done
