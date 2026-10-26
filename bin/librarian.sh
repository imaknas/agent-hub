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

# claude 的憑證由 bin/llm.py 決定(預設 ~/.claude/headless-oauth-token)

# hub 根目錄由腳本位置推出(AGENT_HUB_ROOT 可覆蓋),memory 副本的位置從 hub.json(hubconf),不寫死
HUB="${AGENT_HUB_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
STATE_DIR="$HUB/.state"
LOG_DIR="$HUB/.logs"
REVIEW_LOG="$HUB/review-log.md"
SAFE_PUSH="$HUB/bin/git-safe-push.sh"
mkdir -p "$STATE_DIR" "$LOG_DIR"
MEMORY_COPIES=$("$HUB/bin/py" "$HUB/bin/hubconf.py" path memory_copies) || exit 1
# prompt 裡對人、對助理的稱呼(hub.json owner.tag、assistant.name)
OWNER=$("$HUB/bin/py" "$HUB/bin/hubconf.py" get owner.tag 2>/dev/null) || OWNER="owner"
ASSISTANT=$("$HUB/bin/py" "$HUB/bin/hubconf.py" get assistant.name 2>/dev/null) || ASSISTANT="個人助理"
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

# STATE.md / index.md 判斷的輸出 schema 在 contracts/llm/librarian-{state,index}.schema.json(bin/llm.py 套用)


total_cost="0"

repair_state_todo() {
  local proj="$1" dir="$2" problems fix_prompt fix_json
  problems=$("$HUB/bin/py" "$HUB/bin/check-state-todo.py" "$dir/STATE.md") && return 0
  log "$proj: STATE.md 待辦段落格式不對,請模型只修格式:$(echo "$problems" | head -3 | tr '\n' ' ')"
  fix_prompt="只修 ./STATE.md 的 \`## 待辦\` 段落格式,其他地方、待辦的內容都不要改,也不要動任何其他檔案。

正確格式(一行一項;沒有待辦就寫 \`- (無)\`;STATE.md 一定要有這個段落):
  \`- [ ] 〔${OWNER}〕…\`(只有 ${OWNER} 本人能做:帳號、密碼、付款、要他拍板的決定、他自己要買/做的事)
  \`- [ ] 〔agent〕…\`(agent 可以直接做的)
  \`- [ ] 〔待定〕…\`(還要先做決定才能動的)

如果 STATE.md 別的地方有待辦性質的清單(例如「待決事項」「需要 ${OWNER} 拍板」「可由 agent 執行」
「下一步」「未完成」),把那些項目**搬進** \`## 待辦\`(標上負責人)並刪掉原本那段;
只有整份 STATE.md 真的沒有任何待辦時才寫 \`- (無)\`。排程、已完成的事實不算待辦,留在原處。

目前的問題:
$problems"
  # 判斷點 librarian-todo-format(contracts/llm-points.yaml 宣告模型、權限、工具)
  fix_json=$(cd "$dir" && printf '%s' "$fix_prompt" | "$HUB/bin/py" "$HUB/bin/llm.py" run librarian-todo-format 2>>"$LOG")
  add_cost "$(echo "$fix_json" | jq -r '.total_cost_usd // empty' 2>/dev/null)"
  if problems=$("$HUB/bin/py" "$HUB/bin/check-state-todo.py" "$dir/STATE.md"); then
    log "$proj: 待辦段落格式已修正"
  else
    log "$proj: 待辦段落格式修正後仍有問題(todo.md 檔尾會列出):$(echo "$problems" | head -2 | tr '\n' ' ')"
  fi
}
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
handoffs_file=$(mktemp)
trap 'rm -f "$digest_file" "$handoffs_file"' EXIT

# --- 助理的轉交(handoffs):owner 告訴個人助理、跟某個專案有關的事實 ---
# hubbus-bridge 每 15 分鐘把助理的轉交收進各專案信箱;這裡每晚把還沒結案的直接當成這次
# STATE.md 呼叫的額外輸入(不寫成 memory 流水筆記,避免累積流水帳;改了什麼跟理由留在 review-log.md)。
# 這個專案的結果可信(checkpoint 會推進)才 `hubbus done`,bridge 再回報助理結案;不可信就留到隔晚。
# session 已經處理掉(done)的不會出現在這裡。工作(公司)的內容 助理那端就不會轉交。
HUBBUS="$HUB/bin/py $HUB/bin/hubbus"
echo '[]' >"$handoffs_file"
if $HUBBUS open-handoffs >"$handoffs_file.tmp" 2>>"$LOG" \
    && jq -e 'type == "array"' "$handoffs_file.tmp" >/dev/null 2>&1; then
  mv "$handoffs_file.tmp" "$handoffs_file"
  log "助理的轉交:$(jq length "$handoffs_file") 筆待處理"
  for p in $(jq -r '.[].project' "$handoffs_file" | sort -u); do
    [ -d "$MEMORY_COPIES/$p/.git" ] || log "助理的轉交:專案 $p 沒有 memory 副本,這次不處理(留在信箱)"
  done
else
  rm -f "$handoffs_file.tmp"
  log "hubbus open-handoffs 讀取失敗,這次不處理轉交"
fi

for proj_dir in "$MEMORY_COPIES"/*/; do
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

  # 帶任務 key 的轉交(done/dropped/already_done/reopened)不給模型,之後的 knas-tasks job 照 key 機械套用
  handoff_ids=$(jq -r --arg p "$proj" '[.[] | select(.project == $p and (.effect | IN("done", "dropped", "already_done", "reopened") | not)) | .id] | join(" ")' "$handoffs_file")
  handoff_nums=$(jq -r --arg p "$proj" '[.[] | select(.project == $p and (.effect | IN("done", "dropped", "already_done", "reopened") | not)) | "#\(.handoff_id)"] | join(" ")' "$handoffs_file")

  if [ "$head_sha" = "$last_sha" ] && [ -z "$handoff_ids" ]; then
    log "$proj: no new commits, skip"
    continue
  fi
  any_project_changed=true

  changed_memory=""
  if [ "$head_sha" != "$last_sha" ]; then
    if [ -n "$last_sha" ] && git -C "$proj_dir" cat-file -e "$last_sha" 2>/dev/null; then
      range="$last_sha..$head_sha"
    else
      range="$head_sha"
    fi
    changed_memory=$(git -C "$proj_dir" log --name-only --pretty=format: "$range" -- memory/ 2>/dev/null | grep -v '^$' | sort -u || true)
  fi

  if [ -z "$changed_memory" ] && [ -z "$handoff_ids" ]; then
    log "$proj: no memory/ changes in range $range"
    echo "$head_sha" > "$ckpt_file"
    continue
  fi

  memory_section="這次 memory/ 沒有新變動,只有下面 ${OWNER} 的轉交要處理。"
  if [ -n "$changed_memory" ]; then
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
    memory_section="這個專案自上次檢查後,以下 memory 檔案有變動:
$changed_memory

實際變動內容(git log -p,由舊到新)$diff_note:
$memory_diff"
  fi

  handoff_note=""
  if [ -n "$handoff_ids" ]; then
    log "$proj: 助理的轉交 $handoff_nums"
    handoff_items=$(jq -r --arg p "$proj" --arg owner "$OWNER" '.[] | select(.project == $p and (.effect | IN("done", "dropped", "already_done", "reopened") | not)) |
      "- [\(.effect)] 事實:\(.fact)" +
      (if .quote then "\n  \($owner) 原話:\(.quote)" else "" end) +
      "\n  對應待辦:\(.item // "(沒有指定)")\n  助理的判斷理由:\(.why)\n  (\(.ts[0:16]))"' "$handoffs_file")
    handoff_note="
${OWNER} 透過他的個人助理(${ASSISTANT})告訴我們這些跟這個專案有關的事(比舊的 memory 紀錄新,也比較可信):
$handoff_items

處理方式(這些轉交本身就算實質變化):
- [resolves]:${OWNER} 已經處理或回答了對應待辦。從 \`## 待辦\` 移除那一條(用意思比對,文字不一定一樣),
  並把事實寫進 STATE.md 合適的段落。
- [changes]:對應待辦還在,但前提或日期變了。依事實改寫那一條,必要時一併更新相關段落。
- [relevant]:只供參考,影響到現況才更新 STATE.md。
- [answers]:${OWNER} 回答了這個專案問過他的問題(對應待辦 = 當初的問題,事實 = 回答)。把決定寫進相關段落;
  有在等這個回答的待辦就依回答移除或改寫。
- 對應待辦在 STATE.md 找不到時不要硬套到別條,只把事實記進相關段落(有關的話)。
- 「事實」是助理轉述的,可能比 ${OWNER} 原話講得更肯定。有原話以原話為準;寫進 STATE.md 時保留
  不確定的語氣(預計、預期、可能、打算),不要寫成已確定。"
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
$memory_section
$handoff_note
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
- STATE.md 一定要有一個 \`## 待辦\` 段落,格式固定,一行一項:
  \`- [ ] 〔${OWNER}〕…\`(只有 ${OWNER} 本人能做:帳號、密碼、付款、要他拍板的決定)
  \`- [ ] 〔agent〕…\`(agent 可以直接做的)
  \`- [ ] 〔待定〕…\`(還要先做決定才能動的)
  〔${OWNER}〕行尾的「(等 ${OWNER}:task-…)」是 ${ASSISTANT} 任務登錄的 key,**原樣保留,不要改那一行的文字**
  (${OWNER} 在 ${ASSISTANT} 結案時會照 key 自動移掉;文字一改 key 就對不上)。
  沒有待辦就寫 \`- (無)\`。只列還沒完成、具體可行動的事;完成、被推翻或過時的直接移除,
  不要留著打勾。待辦只放在這個段落,不要在 STATE.md 別的地方另開「下一步」「未完成」之類的清單。
- 除了 ./STATE.md,不要建立、修改或刪除任何檔案。"

  pre_call_sha=$(git -C "$proj_dir" rev-parse HEAD)

  # 只准寫 ./STATE.md:prompt 講了也擋不住(2026-10-15 project-06 的 haiku 還是去改了
  # memory/reference/ 五個檔),所以用權限規則機械限制。acceptEdits 會放行目錄裡所有檔案,
  # dontAsk 只放行 allowedTools 列的;Edit(...) 規則涵蓋 Write/Edit 兩個工具。
  # 下面的 stray 檢查留著當第二道防線。
  # 判斷點 librarian-state(模型、dontAsk、只准 Edit(./STATE.md)、schema 都在 contracts/llm-points.yaml)
  output_json=$(cd "$proj_dir" && printf '%s' "$prompt" | "$HUB/bin/py" "$HUB/bin/llm.py" run librarian-state 2>>"$LOG")
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

  # 待辦段落格式檢查:haiku 不一定每次照格式寫(2026-10-02 sycophancy-eval 整段被拿掉、
  # 10-03 project-24 10 項沒標負責人),格式不對 gen-todo.py 就收不到。不對就請模型
  # 只修格式、內容不動,修完再驗一次;還是不對就記下來,gen-todo 會在 todo.md 檔尾列出。
  repair_state_todo "$proj" "$proj_dir"

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
      [ -n "$handoff_ids" ] && { echo "**${ASSISTANT} 轉交**: $handoff_nums"; echo; }
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

  if [ -n "$handoff_ids" ]; then
    # shellcheck disable=SC2086  # ids 是空白分隔的信 id,要拆成多個參數
    if $HUBBUS done $handoff_ids --note "librarian 套用進 STATE.md($TODAY,見 review-log)" >>"$LOG" 2>&1; then
      log "$proj: 助理的轉交 $handoff_nums 已結案(bridge 會回報助理)"
    else
      log "$proj: 助理的轉交結案失敗,隔晚會再處理一次(STATE.md 已經改過,重複處理應該不會再改)"
    fi
  fi

  if [ "$worth_digesting" = "true" ] && [ -n "$summary" ]; then
    { echo "## $proj"; echo; echo "$summary"; echo; } >> "$digest_file"
  fi
done

# --- 今天的 digest:專案摘要這一段,再請模型標出專案之間的關聯(index.md)---
# 後面幾件事是排在這之後的獨立 job(projects/agent-hub.yaml,after: librarian),不在這支裡:
# knas-tasks、reconcile-questions、todo(gen-todo)、memory-gaps、librarian-publish(名冊 + commit)。
# digest 是分段寫的(hublib.py digest-append):各 job 寫自己那段,重跑只換自己的,不會互相蓋掉。
( cd "$HUB" && git pull -q ) >>"$LOG" 2>&1 || true
if [ -s "$digest_file" ]; then
  "$HUB/bin/py" "$HUB/bin/hublib.py" digest-append projects <"$digest_file" >/dev/null \
    || log "digest $TODAY 寫入失敗"

  index_prompt="更新 index.md:今天($TODAY)各專案的摘要在 digest/$TODAY.md,讀進來後,
標出專案之間看得出來的關聯——沒有明顯關聯就不要硬掰,保持精簡。
直接編輯 index.md,不要重寫整份格式風格。如果沒有值得改的地方,可以不動這個檔案。
<!-- roster:begin --> 到 <!-- roster:end --> 之間的專案名冊是 gen-index-roster.sh 機械產生的,
不要編輯那個區塊(改了也會被覆蓋);你的註記寫在區塊外。"

  # 判斷點 librarian-index
  index_output_json=$(cd "$HUB" && printf '%s' "$index_prompt" | "$HUB/bin/py" "$HUB/bin/llm.py" run librarian-index 2>>"$LOG")
  idx_rc=$?

  if [ $idx_rc -eq 0 ] && [ -n "$index_output_json" ]; then
    idx_cost=$(echo "$index_output_json" | jq -r '.total_cost_usd // empty' 2>/dev/null)
    [ -n "$idx_cost" ] && { log "index.md: call cost \$$idx_cost (model: $INDEX_MODEL)"; add_cost "$idx_cost"; }
    idx_relations=$(echo "$index_output_json" | jq -r '.structured_output.relations_found // empty' 2>/dev/null)
    log "index.md: $idx_relations"
  else
    log "index.md update failed (exit $idx_rc)"
  fi
else
  # 今天沒有專案摘要:拿掉這一段(同一天重跑、上一次有寫過的話)
  printf '' | "$HUB/bin/py" "$HUB/bin/hublib.py" digest-append projects >/dev/null || true
  log "nothing to digest today"
fi

log "=== librarian run end (total cost today: \$$total_cost) ==="
