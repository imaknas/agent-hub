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

HUB="${AGENT_HUB:-${AGENT_HUB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}}"   # 預設 = 這支腳本所在的 hub
PY="$(dirname "$0")/py"   # 平台的 Python 跟著這支腳本走(AGENT_HUB 可能指向別的資料目錄)
source "$(dirname "${BASH_SOURCE[0]}")/hubpaths.sh"
[ -n "${AGENT_HUB:-}" ] && HUB_DATA="$HUB"   # AGENT_HUB 指到別的資料目錄時(測試)用它
INDEX="$HUB_DATA/index.md"

[ -f "$INDEX" ] || printf '# agent-hub index\n' > "$INDEX"

block=$(mktemp); out=$(mktemp); rows=$(mktemp); ideas=$(mktemp)
trap 'rm -f "$block" "$out" "$rows" "$ideas"' EXIT
AGENT_HUB_ROOT="$HUB" "$PY" "$HUB/bin/hubconf.py" roster > "$rows"
AGENT_HUB_ROOT="$HUB" "$PY" "$HUB/bin/hubconf.py" ideas > "$ideas"   # 想法沙盒另外列一組
# 注意:過濾時想法清單在 awk 的 BEGIN 讀,不要改回 `NR==FNR` 兩個檔案的寫法——清單是空檔(還沒有任何想法)
# 時 NR==FNR 對每一行都成立,所有專案都會被濾掉(2026-10-24 名冊因此空了一次)
[ -s "$rows" ] || { hub_say "gen-index-roster: the project registry is empty ({dir})" dir="$HUB/projects" >&2; exit 1; }

{
  echo "<!-- roster:begin — 由 bin/gen-index-roster.sh 從專案登錄產生,不要手動改 -->"
  hub_say "| Project | Path (hub machine) |"
  echo "|---|---|"
  cut -f1,2 "$rows" | sort | awk -F'\t' -v f="$ideas" 'BEGIN{while((getline l < f) > 0) if (l != "") idea[l]=1} !($1 in idea)' \
    | while IFS=$'\t' read -r name path; do
        echo "| \`$name\` | \`$path\` |"
      done
  if [ -s "$ideas" ]; then
    echo
    hub_say "Idea sandboxes (projects the assistant opens to explore ideas, registered as {type}):" type='`type: idea`'
    echo
    hub_say "| Idea | Path (hub machine) |"
    echo "|---|---|"
    cut -f1,2 "$rows" | sort | awk -F'\t' -v f="$ideas" 'BEGIN{while((getline l < f) > 0) if (l != "") idea[l]=1} ($1 in idea)' \
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
  awk -v blockfile="$block" -v heading="$(hub_msg "## Project roster")" '
    function emit() { print heading; print ""; while ((getline l < blockfile) > 0) print l; print ""; done=1 }
    /^## / && !done { emit() }
    { print }
    END { if (!done) { print ""; emit() } }
  ' "$INDEX" > "$out"
fi

if cmp -s "$out" "$INDEX"; then
  hub_say "gen-index-roster: the roster is unchanged"
else
  cat "$out" > "$INDEX"
  hub_say "gen-index-roster: roster updated ({n} projects)" n="$(grep -c . "$rows")"
fi
