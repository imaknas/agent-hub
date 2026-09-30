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
# 隔離區只是留證據,14 天後自動清掉,不讓它一直累積。
find "$STATE_DIR/stray" -mindepth 1 -maxdepth 1 -mtime +14 -exec rm -rf {} + 2>/dev/null || true

any_project_changed=false
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
  any_project_changed=true

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

  # diff 由腳本直接給:模型的 --tools 沒有 Bash,自己跑不了 git log,也讀不到
  # 已經被 git rm 的檔案。太大就截斷,模型再用 Read 讀現存檔案補。
  MAX_DIFF_BYTES=60000
  memory_diff=$(git -C "$proj_dir" log -p --reverse --format='=== commit %h %ad %s' --date=short "$range" -- memory/ 2>/dev/null)
  diff_note=""
  if [ "${#memory_diff}" -gt "$MAX_DIFF_BYTES" ]; then
    memory_diff="${memory_diff:0:$MAX_DIFF_BYTES}"
    diff_note="(diff 太長,只附前 ${MAX_DIFF_BYTES} 字元;其餘請用 Read 讀上面列出、目前還存在的檔案)"
  fi

  # 機械檢查:STATE.md 裡提到的 memory/ 路徑(可含 *)現在還存不存在。
  dangling=""
  while read -r ref; do
    [ -n "$ref" ] || continue
    ( cd "$proj_dir" && compgen -G "$ref" >/dev/null ) || dangling="$dangling $ref"
  done < <(grep -oE 'memory/[A-Za-z0-9._*/-]+\.md' "$proj_dir/STATE.md" 2>/dev/null | grep -v 'YYYY' | sort -u)
  dangling_note=""
  if [ -n "$dangling" ]; then
    log "$proj: STATE.md 引用了不存在的檔案:$dangling"
    dangling_note="
STATE.md 目前引用了這些已經不存在的檔案(被搬走或刪除),這次一定要修正引用,
指向現在實際存放內容的地方(通常是 memory/reference/ 底下的主題文件):$dangling"
  fi

  prompt="你是跨專案整合 agent(圖書館員),現在在專案 $proj 的目錄裡工作。
這個專案自上次檢查後,以下 memory 檔案有變動:
$changed_memory

實際變動內容(git log -p,由舊到新)$diff_note:
$memory_diff
$dangling_note

背景請讀 ./AGENT.md 跟 ./STATE.md。**STATE.md 在專案根目錄(./STATE.md),不在 memory/ 底下**;
memory/ 底下沒有、也不能有 STATE.md。

memory/ 的結構:
- memory/YYYY-MM-DD.md、memory/YYYY-MM-DD-<主題>.md:原始流水紀錄,只會往後加。
- memory/reference/<主題>.md(有 README.md 當索引):整理過的主題文件,會被改寫,
  內容是目前最準的版本;跟舊的流水紀錄衝突時以 reference/ 為準。

規則:
- 只有在有『實質變化』時才編輯 STATE.md(精煉現況,不是流水帳);沒有實質變化就完全不要動 STATE.md。
  STATE.md 還是 onboard 時的佔位內容、而 memory/ 已經有實質內容的話,這本身就算實質變化。
- STATE.md 裡提到已被搬走的檔案、或已經被新內容回答/推翻的待決事項,要一併修正。
- STATE.md 一定要有一個 `## 待辦` 段落,格式固定,一行一項:
  `- [ ] 〔Knas〕…`(只有 Knas 本人能做:帳號、密碼、付款、要他拍板的決定)
  `- [ ] 〔agent〕…`(agent 可以直接做的)
  `- [ ] 〔待定〕…`(還要先做決定才能動的)
  沒有待辦就寫 `- (無)`。只列還沒完成、具體可行動的事;完成、被推翻或過時的直接移除,
  不要留著打勾。待辦只放在這個段落,不要在 STATE.md 別的地方另開「下一步」「未完成」之類的清單。
- 除了 ./STATE.md,不要建立、修改或刪除任何檔案。"

  pre_call_sha=$(git -C "$proj_dir" rev-parse HEAD)

  output_json=$(cd "$proj_dir" && claude -p "$prompt" \
      --model "$STATE_MODEL" \
      --permission-mode acceptEdits \
      --tools "Read,Write,Edit,Glob,Grep" \
      --output-format json \
      --json-schema "$STATE_SCHEMA" 2>>"$LOG")
  rc=$?

  if [ $rc -ne 0 ] || [ -z "$output_json" ]; then
    # 呼叫本身失敗:可能改到一半,不採用。把副本還原成乾淨狀態,不留給下一晚。
    git -C "$proj_dir" checkout -q -- . 2>/dev/null
    git -C "$proj_dir" clean -fdq 2>/dev/null
    log "$proj: claude invocation failed (exit $rc), 已還原副本,checkpoint 不推進"
    continue
  fi

  cost=$(echo "$output_json" | jq -r '.total_cost_usd // empty' 2>/dev/null)
  [ -n "$cost" ] && { log "$proj: call cost \$$cost (model: $STATE_MODEL)"; add_cost "$cost"; }

  # 注意:布林欄位不能用 `// empty`,因為 jq 的 `//` 把 false 當 falsy,會把
  # 正確的 false 也吃掉變成空字串,跟「欄位不存在」混在一起分不出來。
  has_output=$(echo "$output_json" | jq -r 'if .structured_output == null then "no" else "yes" end' 2>/dev/null)

  # 拿不到 structured_output(模型說它呼叫過,但結果裡沒有;每幾晚會發生一次):
  # 不再直接 continue——那樣模型已經改好的 STATE.md 會留在副本裡沒 commit,要到隔晚
  # 才被下一次呼叫順手帶進去(2026-09-28 project-36、09-29 the-crucible)。照常用
  # git 判斷實際有沒有改、有改就 commit;只是沒有摘要可放 digest,checkpoint 也不推進。
  no_output=false
  if [ "$has_output" != "yes" ]; then
    no_output=true
    log "$proj: 沒有拿到 structured_output(subtype=$(echo "$output_json" | jq -r '.subtype // "?"' 2>/dev/null) turns=$(echo "$output_json" | jq -r '.num_turns // "?"' 2>/dev/null) 模型最後說:$(echo "$output_json" | jq -r '.result // ""' 2>/dev/null | tr '\n' ' ' | head -c 300)),仍以實際檔案變化為準"
  fi

  summary=$(echo "$output_json" | jq -r '.structured_output.summary // empty' 2>/dev/null)
  reason=$(echo "$output_json" | jq -r '.structured_output.reason // empty' 2>/dev/null)
  reported_updated=$(echo "$output_json" | jq -r 'if .structured_output == null then "unknown" else .structured_output.state_updated end' 2>/dev/null)
  worth_digesting=$(echo "$output_json" | jq -r '.structured_output.worth_digesting' 2>/dev/null)

  # 以實際 git 狀態為準來決定要不要 commit,不直接信模型自報的 state_updated。
  # 陷阱:這個專案自己的 SessionEnd hook,在 claude -p 這個呼叫「進行中」就可能
  # 已經自動 commit+push 過 STATE.md 了(headless 呼叫本身也是一個完整 session,
  # 一樣會觸發它所在目錄的 hook)。所以不能只看 working tree 有沒有未 commit 的
  # 差異——要先比對 HEAD 有沒有動過,那才是不會被 hook 蓋掉的真相來源。
  # 機械檢查:模型只准動 ./STATE.md。多出來的檔案(2026-09-26 有 5 個專案把 STATE.md
  # 寫成 memory/STATE.md,resume 還多生了 memory/MEMORY.md)搬到隔離區並記錄,
  # 不讓它們留在 memory 副本裡、也不讓下一次呼叫把它們當成真的內容讀進去。
  stray=$(git -C "$proj_dir" status --porcelain --untracked-files=all | awk '{print $2}' | grep -vx 'STATE.md' || true)
  if [ -n "$stray" ]; then
    qdir="$STATE_DIR/stray/$proj-$TODAY"; mkdir -p "$qdir"
    for f in $stray; do
      if git -C "$proj_dir" ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
        # 被追蹤的檔案被改(或刪):留一份改過的版本當證據,再還原
        [ -e "$proj_dir/$f" ] && cp "$proj_dir/$f" "$qdir/$(echo "$f" | tr / _)"
        git -C "$proj_dir" checkout -q -- "$f"
      else
        mv "$proj_dir/$f" "$qdir/$(echo "$f" | tr / _)"
      fi
    done
    log "$proj: 警告——模型動了 STATE.md 以外的檔案,已搬到 $qdir:$(echo $stray)"
    stray_found=true
  else
    stray_found=false
  fi

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

  if [ "$reported_updated" != "unknown" ] && [ "$reported_updated" != "$actually_changed" ]; then
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

  # 模型說有更新、實際沒改,或動了不該動的檔案:這次當作沒處理完,不推進 checkpoint,
  # 下次再跑同一個範圍(不然這批 memory 變化就被永遠跳過)。
  if { [ "$reported_updated" = "true" ] && [ "$actually_changed" != "true" ]; } || [ "$stray_found" = true ] || [ "$no_output" = true ]; then
    log "$proj: 這次結果不可信,checkpoint 不推進,下次重試"
    continue
  fi
  echo "$new_sha" > "$ckpt_file"

  if [ "$worth_digesting" = "true" ] && [ -n "$summary" ]; then
    { echo "## $proj"; echo; echo "$summary"; echo; } >> "$digest_file"
  fi
done

# --- 跨專案待辦清單 todo.md ---
# 機械產生:收集各專案 STATE.md 的 `## 待辦` 段落(固定格式,由上面每個專案的呼叫
# 維護)跟 openclaw-migration-status.md 的待辦。不用 LLM,輸入一樣輸出就一樣——
# 2026-09-30 前是每晚叫 sonnet 從頭推導一次,項目數 46→88→114→73 亂跳,又是每晚
# 最大的一筆額度用量。
cd "$HUB" && git pull -q || true
if todo_out=$(python3 "$HUB/bin/gen-todo.py" "$HUB/todo.md" 2>>"$LOG"); then
  while IFS= read -r l; do log "$l"; done <<< "$todo_out"
else
  log "gen-todo.py 失敗,todo.md 這次沒更新"
fi

# --- memory 缺口:有對話但沒記 memory 的專案(機械檢查,零 LLM 成本)---
# 每晚都跑(不管 todo.md 這次有沒有重生),結果放進 todo.md 的固定區塊跟今天的 digest。
# 不管結果都記一行:不然「0 個缺口」跟「根本沒跑」分不出來。
if gaps=$(python3 "$HUB/bin/check-memory-gaps.py" 2>>"$LOG"); then
  log "memory 缺口檢查:$(printf '%s' "$gaps" | grep -c '^- ' || true) 個專案有對話但沒記 memory"
else
  log "memory 缺口檢查失敗(exit $?),看上面的錯誤;todo.md 的缺口區塊這次不動"
  gaps="__failed__"
fi
if [ -f "$HUB/todo.md" ] && [ "$gaps" != "__failed__" ]; then
  python3 - "$HUB/todo.md" "$gaps" <<'PY'
import re, sys
from pathlib import Path
p, gaps = Path(sys.argv[1]), sys.argv[2].strip()
s = re.sub(r"\n*<!-- memory-gaps:begin -->.*?<!-- memory-gaps:end -->\n?", "\n", p.read_text(), flags=re.S).rstrip() + "\n"
if gaps:
    s += ("\n<!-- memory-gaps:begin -->\n## memory 沒在記的專案(機械檢查)\n\n"
          "有對話、但 memory 沒有新筆記——librarian 沒東西可整理,STATE.md 會一直過時。\n\n"
          + gaps + "\n<!-- memory-gaps:end -->\n")
p.write_text(s)
PY
fi
if [ -n "$gaps" ] && [ "$gaps" != "__failed__" ]; then
  log "memory 缺口:$(echo "$gaps" | grep -o '\*\*[^*]*\*\*' | tr -d '*' | tr '\n' ' ')"
  { echo "## memory 沒在記的專案"; echo; echo "$gaps"; echo; } >> "$digest_file"
fi

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
      "digest/$TODAY.md" index.md review-log.md todo.md >>"$LOG" 2>&1 \
    && log "digest $TODAY written & pushed" \
    || log "digest $TODAY commit/push failed"
else
  log "nothing to digest today"
  # 沒有 digest 的日子也要同步名冊(不然新 onboard 的專案要等到某天有 digest
  # 才會出現在 index.md),以及迴圈裡可能寫進的 review-log.md。
  cd "$HUB"
  git pull -q || true
  "$HUB/bin/gen-index-roster.sh" >>"$LOG" 2>&1 || log "gen-index-roster failed"
  "$HUB/bin/commit-hub-files.sh" "librarian: index roster / review-log / todo $TODAY" \
      index.md review-log.md todo.md >>"$LOG" 2>&1 \
    || log "index.md/review-log.md/todo.md commit/push failed"
fi

log "=== librarian run end (total cost today: \$$total_cost) ==="
