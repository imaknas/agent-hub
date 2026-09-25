#!/usr/bin/env bash
# 圖書館員 agent：每晚讀各專案 memory/ 的新增內容，視情況更新 STATE.md，
# 並在 agent-hub 寫一份跨專案 digest。設計見 project-29/ROADMAP.md
# 「中心化整合 agent」一節。
#
# robust 化的決定：
# 1. 單一專案的 STATE.md 判斷用 haiku（量大、任務單純）；跨專案關聯用 sonnet
#    （一天只跑一次，成本差異可忽略，但這一步需要的推理明顯更難）。模型名稱
#    可以用 models.env 覆寫，不用改這支腳本。
# 2. 用 --output-format json --json-schema 拿結構化輸出，不再用字串標記
#    （---SUMMARY---）配 sed 解析；並且用「實際 git diff」而不是模型自報的
#    state_updated 當作要不要 commit 的依據，兩者不一致時記警告。
# 3. 每次真的動到 STATE.md，都額外寫一筆到 review-log.md（連 diff 一起），
#    給人事後抽查用，不做阻斷式審批。
# 4. 所有 push 都透過 git-safe-push.sh，遇到分岔自動 pull 合併重試，不用人工
#    處理 rejected push（真的內容衝突還是會失敗、需要人來看）。
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
SAFE_PUSH="$HUB/bin/git-safe-push.sh"
mkdir -p "$STATE_DIR" "$LOG_DIR"
TODAY=$(date +%Y-%m-%d)
LOG="$LOG_DIR/librarian-$TODAY.log"

# 模型設定：預設值寫死在這裡當 fallback，models.env 存在的話覆寫。
STATE_MODEL="haiku"
INDEX_MODEL="sonnet"
[ -f "$HUB/models.env" ] && source "$HUB/models.env"

# 只寫進 $LOG;手動在終端機跑時才另外印到螢幕。排程器把 stdout 導進同一個
# librarian-<日期>.log,用 tee 的話每行都會重複兩次。
log() {
  local line="[$(date '+%H:%M:%S')] $*"
  echo "$line" >>"$LOG"
  [ -t 1 ] && echo "$line"
  return 0
}

STATE_SCHEMA='{"type":"object","properties":{"state_updated":{"type":"boolean","description":"這次是否編輯了 STATE.md"},"worth_digesting":{"type":"boolean","description":"這次的內容是否值得放進跨專案 digest；純粹確認流程正常、沒有實質更新的話填 false"},"summary":{"type":"string","description":"給跨專案 digest 用的摘要，不超過三句話（worth_digesting 為 false 時可以簡短說明沒有更新的原因，或留空）"},"reason":{"type":"string","description":"為什麼做出這個判斷（更新或不更新 STATE.md），給人事後抽查用"}},"required":["state_updated","worth_digesting","summary","reason"]}'

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

  pre_call_sha=$(git -C "$proj_dir" rev-parse HEAD)

  output_json=$(cd "$proj_dir" && claude -p "$prompt" \
      --model "$STATE_MODEL" \
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
  [ -n "$cost" ] && { log "$proj: call cost \$$cost (model: $STATE_MODEL)"; add_cost "$cost"; }

  # 注意:布林欄位不能用 `// empty`,因為 jq 的 `//` 把 false 當 falsy,會把
  # 正確的 false 也吃掉變成空字串,跟「欄位不存在」混在一起分不出來。
  has_output=$(echo "$output_json" | jq -r 'if .structured_output == null then "no" else "yes" end' 2>/dev/null)

  if [ "$has_output" != "yes" ]; then
    log "$proj: 沒有拿到 structured_output,跳過(原始回應開頭: $(echo "$output_json" | head -c 200))"
    continue
  fi

  summary=$(echo "$output_json" | jq -r '.structured_output.summary // empty' 2>/dev/null)
  reason=$(echo "$output_json" | jq -r '.structured_output.reason // empty' 2>/dev/null)
  reported_updated=$(echo "$output_json" | jq -r '.structured_output.state_updated' 2>/dev/null)
  worth_digesting=$(echo "$output_json" | jq -r '.structured_output.worth_digesting' 2>/dev/null)

  # 以實際 git 狀態為準來決定要不要 commit,不直接信模型自報的 state_updated。
  # 陷阱:這個專案自己的 SessionEnd hook,在 claude -p 這個呼叫「進行中」就可能
  # 已經自動 commit+push 過 STATE.md 了(headless 呼叫本身也是一個完整 session,
  # 一樣會觸發它所在目錄的 hook)。所以不能只看 working tree 有沒有未 commit 的
  # 差異——要先比對 HEAD 有沒有動過,那才是不會被 hook 蓋掉的真相來源。
  post_call_sha=$(git -C "$proj_dir" rev-parse HEAD)
  actually_changed=false
  state_diff=""
  new_sha="$post_call_sha"

  if [ "$post_call_sha" != "$pre_call_sha" ]; then
    actually_changed=true
    state_diff=$(git -C "$proj_dir" diff "$pre_call_sha" "$post_call_sha" -- STATE.md)
  elif ! git -C "$proj_dir" diff --quiet -- STATE.md 2>/dev/null; then
    actually_changed=true
    state_diff=$(git -C "$proj_dir" diff -- STATE.md)
  fi

  if [ "$reported_updated" != "$actually_changed" ]; then
    log "$proj: 警告——agent 回報 state_updated=$reported_updated,但實際檔案變化=$actually_changed(以實際檔案為準)"
  fi

  if [ "$actually_changed" = "true" ]; then
    if [ "$post_call_sha" = "$pre_call_sha" ]; then
      # hook 沒接手(這個專案這次沒有走 hook,或 hook 沒設好),自己補 commit+push。
      ( cd "$proj_dir" && git add STATE.md && git commit -m "librarian: update STATE.md" -q && "$SAFE_PUSH" )
      new_sha=$(git -C "$proj_dir" rev-parse HEAD)
    fi
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
  else
    # 不更新也要留理由:不然 STATE.md 一直是佔位內容時,分不出是模型判斷
    # 太保守還是 prompt 有問題。
    log "$proj: STATE.md 未更新,理由: ${reason:-(agent 未提供理由)}"
  fi

  echo "$new_sha" > "$ckpt_file"

  if [ "$worth_digesting" = "true" ] && [ -n "$summary" ]; then
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
直接編輯 index.md,不要重寫整份格式風格。如果沒有值得改的地方,可以不動這個檔案。
<!-- roster:begin --> 到 <!-- roster:end --> 之間的專案名冊是 gen-index-roster.sh 機械產生的,
不要編輯那個區塊(改了也會被覆蓋);你的註記寫在區塊外。"

  index_output_json=$(claude -p "$index_prompt" \
      --model "$INDEX_MODEL" \
      --permission-mode acceptEdits \
      --tools "Read,Write,Edit,Glob,Grep" \
      --output-format json \
      --json-schema "$INDEX_SCHEMA" 2>>"$LOG")
  idx_rc=$?

  if [ $idx_rc -eq 0 ] && [ -n "$index_output_json" ]; then
    idx_cost=$(echo "$index_output_json" | jq -r '.total_cost_usd // empty' 2>/dev/null)
    [ -n "$idx_cost" ] && { log "index.md: call cost \$$idx_cost (model: $INDEX_MODEL)"; add_cost "$idx_cost"; }
    idx_relations=$(echo "$index_output_json" | jq -r '.structured_output.relations_found // empty' 2>/dev/null)
    log "index.md: $idx_relations"
  else
    log "index.md update failed (exit $idx_rc)"
  fi

  # 名冊在 LLM 之後才重生,就算模型誤動了區塊也會被蓋回正確內容。
  "$HUB/bin/gen-index-roster.sh" >>"$LOG" 2>&1 || log "gen-index-roster failed"
  # 只 commit librarian 自己的產出,不用 git add -A——mini 的 ~/agent-hub 如果
  # 有人留了手動編輯,不能被夜間 digest 一起掃進去。
  "$HUB/bin/commit-hub-files.sh" "librarian: digest $TODAY" \
      "digest/$TODAY.md" index.md review-log.md >>"$LOG" 2>&1 \
    && log "digest $TODAY written & pushed" \
    || log "digest $TODAY commit/push failed"
else
  log "nothing to digest today"
  # 沒有 digest 的日子也要同步名冊(不然新 onboard 的專案要等到某天有 digest
  # 才會出現在 index.md),以及迴圈裡可能寫進的 review-log.md。
  cd "$HUB"
  git pull -q || true
  "$HUB/bin/gen-index-roster.sh" >>"$LOG" 2>&1 || log "gen-index-roster failed"
  "$HUB/bin/commit-hub-files.sh" "librarian: index roster / review-log $TODAY" \
      index.md review-log.md >>"$LOG" 2>&1 \
    || log "index.md/review-log.md commit/push failed"
fi

log "=== librarian run end (total cost today: \$$total_cost) ==="
