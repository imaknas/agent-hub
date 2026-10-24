#!/usr/bin/env bash
# 從專案登錄(projects/*.yaml,bin/hubconf.py roster)機械產生 index.md 裡的「專案名冊」區塊。
#
# 為什麼要有這支:index.md 原本整份交給 librarian 的 LLM 步驟維護,專案清單
# 只會出現模型「自己決定要寫」的專案——2026-09-25 檢查時 19 個已 onboard 的
# 專案只有 7 個在 index.md 裡(adaptive-iteration 9/23 明明進了 digest,模型
# 還是沒幫它加一行)。「有哪些專案」是確定性的事實,不該依賴模型判斷。
#
# 分工:
# - 名冊區塊(<!-- roster:begin --> 到 <!-- roster:end -->)只由這支腳本寫,
#   內容只取專案登錄,不讀任何機器相依的狀態,所以在 MBP 或
#   mini 跑出來的結果一模一樣,不會互相覆蓋造成 diff 抖動。
# - 區塊外的註記/跨專案關聯才是 librarian LLM 的工作範圍。
#
# 區塊不存在的話,插在第一個 `## ` 標題前面(沒有任何標題就接在檔尾)。
# 可重複執行;內容沒變就不改檔案。只改 index.md,不 commit——commit 由呼叫
# 端(librarian.sh / onboard-project.sh)負責。
set -euo pipefail

HUB="${AGENT_HUB:-$HOME/agent-hub}"
INDEX="$HUB/index.md"

[ -f "$INDEX" ] || printf '# agent-hub index\n' > "$INDEX"

block=$(mktemp); out=$(mktemp); rows=$(mktemp); ideas=$(mktemp)
trap 'rm -f "$block" "$out" "$rows" "$ideas"' EXIT
AGENT_HUB_ROOT="$HUB" python3 "$HUB/bin/hubconf.py" roster > "$rows"
AGENT_HUB_ROOT="$HUB" python3 "$HUB/bin/hubconf.py" ideas > "$ideas"   # 想法沙盒另外列一組
[ -s "$rows" ] || { echo "gen-index-roster: 專案登錄是空的($HUB/projects)" >&2; exit 1; }

{
  echo "<!-- roster:begin — 由 bin/gen-index-roster.sh 從專案登錄產生,不要手動改 -->"
  echo "| 專案 | 路徑(mini) |"
  echo "|---|---|"
  cut -f1,2 "$rows" | sort | awk -F'\t' 'NR==FNR{idea[$1]=1; next} !($1 in idea)' "$ideas" - \
    | while IFS=$'\t' read -r name path; do
        echo "| \`$name\` | \`$path\` |"
      done
  if [ -s "$ideas" ]; then
    echo
    echo "想法沙盒(Muninn 替想法開的探討用專案,登錄 \`type: idea\`):"
    echo
    echo "| 想法 | 路徑(mini) |"
    echo "|---|---|"
    cut -f1,2 "$rows" | sort | awk -F'\t' 'NR==FNR{idea[$1]=1; next} ($1 in idea)' "$ideas" - \
      | while IFS=$'\t' read -r name path; do
          echo "| \`$name\` | \`$path\` |"
        done
  fi
  echo "<!-- roster:end -->"
} > "$block"

if grep -q '^<!-- roster:begin' "$INDEX"; then
  awk -v blockfile="$block" '
    /^<!-- roster:begin/ { while ((getline l < blockfile) > 0) print l; skip=1; next }
    /^<!-- roster:end -->/ { skip=0; next }
    !skip { print }
  ' "$INDEX" > "$out"
else
  awk -v blockfile="$block" '
    function emit() { print "## 專案名冊"; print ""; while ((getline l < blockfile) > 0) print l; print ""; done=1 }
    /^## / && !done { emit() }
    { print }
    END { if (!done) { print ""; emit() } }
  ' "$INDEX" > "$out"
fi

if cmp -s "$out" "$INDEX"; then
  echo "gen-index-roster: 名冊沒有變化"
else
  cat "$out" > "$INDEX"
  echo "gen-index-roster: 名冊已更新($(grep -c . "$rows") 個專案)"
fi
