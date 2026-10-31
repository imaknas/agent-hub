#!/usr/bin/env bash
# Called by the scheduler every Monday: produce the previous week's cross-project activity analysis
# (reflect/YYYY-Www.json, including the model's analysis), rebuild the report page for all weeks,
# reflect/index.html, and commit only these two files. The statistics are zero LLM; the analysis is once a week
# (subscription quota). Details in bin/weekly-reflect.py, bin/reflect_llm.py, templates/reflect.html.
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
HUB="${AGENT_HUB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
source "$(dirname "${BASH_SOURCE[0]}")/hubpaths.sh"

cd "$HUB" && git pull -q || true
out=$("$HUB/bin/py" "$HUB/bin/weekly-reflect.py" --llm "$@") || { hub_say "weekly-reflect.py failed" >&2; exit 1; }
json=$(echo "$out" | head -1)
hub_say "wrote {files}" files="$out"
"$HUB/bin/commit-hub-files.sh" "reflect: $(basename "$json" .json)" "reflect/$(basename "$json")" reflect/index.html
