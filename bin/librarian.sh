#!/usr/bin/env bash
# 圖書館員 agent：每晚讀各專案 memory/ 的新增內容，視情況更新 STATE.md，
# 並在 agent-hub 寫一份跨專案 digest。設計見 project-29/ROADMAP.md
# 「中心化整合 agent」一節。
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

HUB="$HOME/agent-hub"
STATE_DIR="$HUB/.state"
LOG_DIR="$HUB/.logs"
mkdir -p "$STATE_DIR" "$LOG_DIR"
TODAY=$(date +%Y-%m-%d)
LOG="$LOG_DIR/librarian-$TODAY.log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

log "=== librarian run start ==="

summary_file=$(mktemp)
trap 'rm -f "$summary_file" "$summary_file".*' EXIT

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
- 不要動其他檔案。
- 最後不論有沒有改 STATE.md,一定要輸出一段不超過 3 句話的摘要給跨專案 digest 用,格式:
---SUMMARY---
<這裡放摘要,如果這次沒有值得記錄的事,寫『(無實質更新)』>"

  proj_summary="$summary_file.$proj"
  if ! ( cd "$proj_dir" && claude -p "$prompt" \
      --model haiku \
      --permission-mode acceptEdits \
      --tools "Read,Write,Edit,Glob,Grep" \
      --output-format text ) > "$proj_summary" 2>>"$LOG"; then
    log "$proj: claude invocation failed, leaving checkpoint untouched"
    rm -f "$proj_summary"
    continue
  fi

  cat "$proj_summary" >> "$LOG"

  if ! git -C "$proj_dir" diff --quiet -- STATE.md 2>/dev/null; then
    ( cd "$proj_dir" && git add STATE.md && git commit -m "librarian: update STATE.md" -q && git push -q )
    log "$proj: STATE.md updated & pushed"
  fi

  echo "$head_sha" > "$ckpt_file"

  body=$(sed -n '/---SUMMARY---/,$p' "$proj_summary" | tail -n +2)
  if [ -n "$body" ] && ! echo "$body" | grep -q '(無實質更新)'; then
    { echo "## $proj"; echo; echo "$body"; echo; } >> "$summary_file"
  fi
  rm -f "$proj_summary"
done

if [ -s "$summary_file" ]; then
  cd "$HUB"
  git pull -q || true
  {
    echo "# Digest $TODAY"
    echo
    cat "$summary_file"
  } > "digest/$TODAY.md"

  claude -p "更新 index.md:今天($TODAY)各專案的摘要在 digest/$TODAY.md,讀進來後,
標出專案之間看得出來的關聯——沒有明顯關聯就不要硬掰,保持精簡。
直接編輯 index.md,不要重寫整份格式風格。" \
    --model haiku \
    --permission-mode acceptEdits \
    --tools "Read,Write,Edit,Glob,Grep" \
    --output-format text >> "$LOG" 2>&1 || log "index.md update failed"

  git add -A
  git commit -m "librarian: digest $TODAY" -q
  git push -q
  log "digest $TODAY written & pushed"
else
  log "nothing to digest today"
fi

log "=== librarian run end ==="
