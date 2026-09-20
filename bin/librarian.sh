#!/usr/bin/env bash
# 圖書館員 agent：每晚讀各專案 memory/ 的新增內容，視情況更新 STATE.md，
# 並在 agent-hub 寫一份跨專案 digest。設計見 project-29/ROADMAP.md
# 「中心化整合 agent」一節。
#
# robust 化的三個決定：
# 1. 單一專案的 STATE.md 判斷用 haiku（量大、任務單純）；跨專案關聯用 sonnet
#    （一天只跑一次，成本差異可忽略，但這一步需要的推理明顯更難）。
# 2. 用 --output-format json --json-schema 拿結構化輸出，不再用字串標記
#    （---SUMMARY---）配 sed 解析；並且用「實際 git diff」而不是模型自報的
#    state_updated 當作要不要 commit 的依據，兩者不一致時記警告。
# 3. 每次真的動到 STATE.md，都額外寫一筆到 review-log.md（連 diff 一起），
#    給人事後抽查用，不做阻斷式審批。
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

TOKEN_FILE="$HOME/.claude/headless-oauth-token"
if [ -r "$TOKEN_FILE" ]; then
  export CLAUDE_CODE_OAUTH_TOKEN
  CLAUDE_CODE_OAUTH_TOKEN="$(cat "$TOKEN_FILE")"
fi

HUB="$HOME/agent-hub"
STATE_DIR="$HUB/.state"
LOG_DIR="$HUB/.logs"
REVIEW_LOG="$HUB/review-log.md"
mkdir -p "$STATE_DIR" "$LOG_DIR"
TODAY=$(date +%Y-%m-%d)
LOG="$LOG_DIR/librarian-$TODAY.log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

STATE_SCHEMA='{"type":"object","properties":{"state_updated":{"type":"boolean","description":"這次是否編輯了 STATE.md"},"summary":{"type":"string","description":"給跨專案 digest 用的摘要，不超過三句話；沒有值得記錄的事就寫「(無實質更新)」"},"reason":{"type":"string","description":"為什麼做出這個判斷（更新或不更新 STATE.md），給人事後抽查用"}},"required":["state_updated","summary","reason"]}'

INDEX_SCHEMA='{"type":"object","properties":{"index_updated":{"type":"boolean","description":"是否編輯了 index.md"},"relations_found":{"type":"string","description":"今天找到的跨專案關聯簡述；沒有明顯關聯就寫「(無明顯關聯)」，不要硬掰"}},"required":["index_updated","relations_found"]}'

total_cost="0"
add_cost() {
  local c="$1"
  if [[ "$c" =~ ^[0-9.]+$ ]]; then
    total_cost=$(awk "BEGIN{printf \"%.6f\", $total_cost + $c}")
  fi
}

log "=== librarian run start ==="

digest_file=$(mktemp)
trap 'rm -f "$digest_file"' EXIT

for proj_dir in "$HOME"/projects/*/; do
  [ -d "$proj_dir/.git" ] || continue
  proj=$(basename "$proj_dir")
  log "--- $proj ---"

  if ! ( cd "$proj_dir" && git pull -q ) >>"$LOG" 2>&1; then
    log "$proj: git pull failed, skip"
    continue
  fi

  head_sha=$(git -C "$proj_dir" rev-parse HEAD)
  ckpt_file="$STATE_DIR/$proj.sha"
  last_sha=$(cat "$ckpt_file" 2>/dev/null || true)

  if [ "$head_sha" = "$last_sha" ]; then
    log "$proj: no new commits, skip"
    continue
  fi

  if [ -n "$last_sha" ] && git -C "$proj_dir" cat-file -e "$last_sha" 2>/dev/null; then
    range="$last_sha..$head_sha"
  else
    range="$head_sha"
  fi

  changed_memory=$(git -C "$proj_dir" log --name-only --pretty=format: "$range" -- memory/ 2>/dev/null | grep -v '^$' | sort -u || true)

  if [ -z "$changed_memory" ]; then
    log "$proj: no memory/ changes in range $range"
    echo "$head_sha" > "$ckpt_file"
    continue
  fi

  log "$proj: changed memory files: $(echo "$changed_memory" | tr '\n' ' ')"

  prompt="你是跨專案整合 agent(圖書館員),現在在專案 $proj 的目錄裡工作。
這個專案自上次檢查後,以下 memory 檔案有新增內容(用 git log -p ${range} -- memory/ 自己確認實際新增了什麼):
$changed_memory

背景請讀 AGENT.md/ROADMAP.md/現有 STATE.md。

規則:
- 只有在有『實質變化』時才編輯 STATE.md(精煉現況,不是流水帳);沒有實質變化就完全不要動 STATE.md。
- 不要動其他檔案。"

  output_json=$(cd "$proj_dir" && claude -p "$prompt" \
      --model haiku \
      --permission-mode acceptEdits \
      --tools "Read,Write,Edit,Glob,Grep" \
      --output-format json \
      --json-schema "$STATE_SCHEMA" 2>>"$LOG")
  rc=$?

  if [ $rc -ne 0 ] || [ -z "$output_json" ]; then
    log "$proj: claude invocation failed (exit $rc), leaving checkpoint untouched"
    continue
  fi

  cost=$(echo "$output_json" | jq -r '.total_cost_usd // empty' 2>/dev/null)
  [ -n "$cost" ] && { log "$proj: call cost \$$cost"; add_cost "$cost"; }

  summary=$(echo "$output_json" | jq -r '.structured_output.summary // empty' 2>/dev/null)
  reason=$(echo "$output_json" | jq -r '.structured_output.reason // empty' 2>/dev/null)
  # 注意:不能用 `// empty`,因為 jq 的 `//` 把 false 當 falsy,會把正確的
  # false 也吃掉變成空字串,跟「欄位不存在」混在一起分不出來。
  reported_updated=$(echo "$output_json" | jq -r '.structured_output.state_updated' 2>/dev/null)

  if [ -z "$summary" ]; then
    log "$proj: 沒有拿到 structured_output,跳過(原始回應開頭: $(echo "$output_json" | head -c 200))"
    continue
  fi

  # 以實際檔案變化為準來決定要不要 commit,不直接信模型自報的 state_updated。
  actually_changed=false
  state_diff=""
  if ! git -C "$proj_dir" diff --quiet -- STATE.md 2>/dev/null; then
    actually_changed=true
    state_diff=$(git -C "$proj_dir" diff -- STATE.md)
  fi

  if [ "$reported_updated" != "$actually_changed" ]; then
    log "$proj: 警告——agent 回報 state_updated=$reported_updated,但實際檔案變化=$actually_changed(以實際檔案為準)"
  fi

  if [ "$actually_changed" = "true" ]; then
    ( cd "$proj_dir" && git add STATE.md && git commit -m "librarian: update STATE.md" -q && git push -q )
    new_sha=$(git -C "$proj_dir" rev-parse HEAD)
    log "$proj: STATE.md updated & pushed ($new_sha)"

    {
      echo "## $TODAY · $proj"
      echo
      echo "**commit**: \`$new_sha\`"
      echo
      echo "**理由**: ${reason:-(agent 未提供理由)}"
      echo
      echo '```diff'
      echo "$state_diff"
      echo '```'
      echo
      echo "---"
      echo
    } >> "$REVIEW_LOG"
    log "$proj: 已寫入 review-log.md,供事後抽查"
  fi

  echo "$head_sha" > "$ckpt_file"

  if [ -n "$summary" ] && ! echo "$summary" | grep -q '(無實質更新)'; then
    { echo "## $proj"; echo; echo "$summary"; echo; } >> "$digest_file"
  fi
done

if [ -s "$digest_file" ]; then
  cd "$HUB"
  git pull -q || true
  {
    echo "# Digest $TODAY"
    echo
    cat "$digest_file"
  } > "digest/$TODAY.md"

  index_prompt="更新 index.md:今天($TODAY)各專案的摘要在 digest/$TODAY.md,讀進來後,
標出專案之間看得出來的關聯——沒有明顯關聯就不要硬掰,保持精簡。
直接編輯 index.md,不要重寫整份格式風格。如果沒有值得改的地方,可以不動這個檔案。"

  index_output_json=$(claude -p "$index_prompt" \
      --model sonnet \
      --permission-mode acceptEdits \
      --tools "Read,Write,Edit,Glob,Grep" \
      --output-format json \
      --json-schema "$INDEX_SCHEMA" 2>>"$LOG")
  idx_rc=$?

  if [ $idx_rc -eq 0 ] && [ -n "$index_output_json" ]; then
    idx_cost=$(echo "$index_output_json" | jq -r '.total_cost_usd // empty' 2>/dev/null)
    [ -n "$idx_cost" ] && { log "index.md: call cost \$$idx_cost"; add_cost "$idx_cost"; }
    idx_relations=$(echo "$index_output_json" | jq -r '.structured_output.relations_found // empty' 2>/dev/null)
    log "index.md: $idx_relations"
  else
    log "index.md update failed (exit $idx_rc)"
  fi

  git add -A
  git commit -m "librarian: digest $TODAY" -q
  git push -q
  log "digest $TODAY written & pushed"
else
  log "nothing to digest today"
fi

log "=== librarian run end (total cost today: \$$total_cost) ==="
