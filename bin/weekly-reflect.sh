#!/usr/bin/env bash
# 每週一由 scheduler 呼叫:產生上一週的跨專案活動總覽(reflect/YYYY-Www.md),
# 含模型寫的主題與 AI 協作觀察,只 commit 那一個檔案。統計部分零 LLM,模型段落
# 每週一次(訂閱額度)。細節見 bin/weekly-reflect.py、bin/reflect_llm.py。
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
HUB="$HOME/agent-hub"
TOKEN_FILE="$HOME/.claude/headless-oauth-token"
[ -r "$TOKEN_FILE" ] && export CLAUDE_CODE_OAUTH_TOKEN="$(cat "$TOKEN_FILE")"
INDEX_MODEL="sonnet"
[ -f "$HUB/models.env" ] && source "$HUB/models.env"
export INDEX_MODEL

cd "$HUB" && git pull -q || true
out=$(python3 "$HUB/bin/weekly-reflect.py" --llm "$@") || { echo "weekly-reflect.py 失敗" >&2; exit 1; }
echo "寫入 $out"
"$HUB/bin/commit-hub-files.sh" "reflect: $(basename "$out" .md)" "reflect/$(basename "$out")"
