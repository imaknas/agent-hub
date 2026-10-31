#!/usr/bin/env bash
# 每週一由 scheduler 呼叫:產生上一週的跨專案活動分析(reflect/YYYY-Www.json,含模型
# 寫的分析),重建所有週的報告頁 reflect/index.html,只 commit 這兩個檔案。統計部分
# 零 LLM,分析每週一次(訂閱額度)。細節見 bin/weekly-reflect.py、bin/reflect_llm.py、
# templates/reflect.html。
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
HUB="${AGENT_HUB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
source "$(dirname "${BASH_SOURCE[0]}")/hubpaths.sh"

cd "$HUB" && git pull -q || true
out=$("$HUB/bin/py" "$HUB/bin/weekly-reflect.py" --llm "$@") || { hub_say "weekly-reflect.py failed" >&2; exit 1; }
json=$(echo "$out" | head -1)
hub_say "wrote {files}" files="$out"
"$HUB/bin/commit-hub-files.sh" "reflect: $(basename "$json" .json)" "reflect/$(basename "$json")" reflect/index.html
