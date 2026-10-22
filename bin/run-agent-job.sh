#!/usr/bin/env bash
# 跑一個「需要 Claude 判斷」的排程 job(例:Upwork 每日檢查、project-36 的分析)。
# 取代 Claude Desktop 的「Scheduled」任務:那些只在 MBP 開著、醒著時才跑;這支跑在 mini,
# 由 scheduler.py 依 schedule.yaml 觸發,所有排程只有 schedule.yaml 一個地方管。
#
# 用法(寫在 schedule.yaml 的 command):run-agent-job.sh <name> [工作目錄] [prompt 名稱]
#   prompt:agent-jobs/<prompt 名稱,預設 name>.md(版控);工作目錄沒給就用這個 job 自己的狀態目錄
#   (hubmail 的升級用同一份 agent-jobs/mailbox.md,name 是 mailbox-<專案>,各自一個狀態目錄)
#   產出:.state/agent-jobs/<name>/
#     <YYYY-MM-DD-HHMM>.md   每次的報告(Claude 最後的回覆)
#     status.json            最後一次的狀態(running/ok/failed、時間、session id、報告路徑)
#     STOP                   job 自己判斷「之後不用再跑」時建立(例:停止條件成立),之後直接略過
#   報告第一行以 🔔 開頭 = 需要 Knas 處理,狀態頁會標出來。
#
# 會在背景跑完就馬上 return:scheduler 是一個一個依序跑 job,LLM job 動輒半小時,
# 不能卡住後面準時要跑的上傳 job。成功/失敗記在 status.json,不是 scheduler 的 log。
#
# 工作目錄是 onboard 過的專案時,那個專案的 hook 也會在這次 claude -p 裡觸發(見 librarian.sh
# 的同類註解):SessionEnd 會 commit+push .agent-memory 的筆記——這正是我們要的(job 寫的筆記
# 自動進 memory),job 只准 append 筆記,不碰專案程式碼。
#
# 用 claude.ai 訂閱登入(mini 的 keychain;launchd 底下讀得到,SSH 底下讀不到),
# 刻意不用 CLAUDE_CODE_OAUTH_TOKEN:有那個變數時 claude.ai connector(Upwork 等)不會載入。
set -uo pipefail

HUB="$HOME/agent-hub"
NAME="${1:?用法: run-agent-job.sh <name> [工作目錄] [prompt 名稱]}"
PROMPT="$HUB/agent-jobs/${3:-$NAME}.md"
case "${3:-}" in /*) PROMPT="$3" ;; esac   # 專案登錄的 agent job:prompt 是 projects/<name>/jobs/*.md 的完整路徑
OUT="$HUB/.state/agent-jobs/$NAME"
CWD="${2:-$OUT}"
mkdir -p "$OUT"

status() {  # status <state> [key=value...]
  python3 - "$OUT/status.json" "$@" <<'PY'
import json, sys, datetime, pathlib
path, state, *kv = sys.argv[1:]
p = pathlib.Path(path)
d = json.loads(p.read_text()) if p.exists() else {}
d["state"] = state
d["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
for item in kv:
    k, v = item.split("=", 1)
    d[k] = v
p.write_text(json.dumps(d, ensure_ascii=False, indent=2))
PY
}

[ -f "$PROMPT" ] || { echo "run-agent-job: 找不到 $PROMPT" >&2; exit 1; }
if [ -f "$OUT/STOP" ]; then
  echo "run-agent-job: $NAME 已經自己停止($(head -1 "$OUT/STOP")),略過"
  exit 0
fi
if [ ! -d "$CWD" ]; then
  # 輸入不見要大聲失敗(known-issues.md):例如專案還沒搬到 mini 的 ~/workspace
  status failed "error=工作目錄 $CWD 不存在" "started_at=$(date -Iseconds)"
  echo "run-agent-job: 工作目錄 $CWD 不存在" >&2
  exit 1
fi

if [ -z "${RUN_AGENT_JOB_FOREGROUND:-}" ]; then
  # 完全脫離(double fork + setsid):launchd 的 job 結束時會殺掉同一個 process group
  # 裡剩下的程序,單純 nohup ... & 會跟著 scheduler 的 tick 一起被殺(2026-10-14 踩到)
  python3 - "$OUT/runner.log" "$0" "$@" <<'PY'
import os, sys
log, *argv = sys.argv[1:]
if os.fork():
    sys.exit(0)
os.setsid()
if os.fork():
    os._exit(0)
fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(fd, 1); os.dup2(fd, 2)
os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
os.environ["RUN_AGENT_JOB_FOREGROUND"] = "1"
os.execv("/bin/bash", ["bash", *argv])
PY
  echo "run-agent-job: $NAME 已在背景開始,結果見 $OUT/status.json"
  exit 0
fi

# ---- 背景:實際執行 ----
unset CLAUDE_CODE_OAUTH_TOKEN
export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
stamp=$(date +%Y-%m-%d-%H%M)
report="$OUT/$stamp.md"
raw="$OUT/.$stamp.json"
status running "started_at=$(date -Iseconds)" "report=" "session_id=" "error=" "cwd=$CWD"

cd "$CWD" || exit 1
# 判斷點 agent-job(權限模式等宣告在 contracts/llm-points.yaml)
/opt/homebrew/bin/python3 "$HUB/bin/llm.py" run agent-job --prompt-file "$PROMPT" >"$raw" 2>"$raw.err"
rc=$?

python3 - "$raw" "$report" "$NAME" "$CWD" <<'PY' >"$OUT/.parsed" 2>&1
import json, sys
raw, report, name, cwd = sys.argv[1:]
try:
    d = json.loads(open(raw).read())
except ValueError:
    print("error=claude 沒有輸出 JSON")
    sys.exit(0)
result = (d.get("result") or "").strip()
with open(report, "w") as f:
    f.write(result + "\n\n---\n")
    f.write(f"job: {name} · 工作目錄: {cwd} · session: {d.get('session_id')} · "
            f"{d.get('num_turns')} turns · {round((d.get('duration_ms') or 0) / 60000, 1)} 分鐘\n")
    f.write(f"(接著討論:在 mini 上 cd {cwd} && claude --resume {d.get('session_id')})\n")
print(f"session_id={d.get('session_id', '')}")
print(f"notify={'yes' if result.startswith('🔔') else 'no'}")
print(f"summary={result.splitlines()[0][:200] if result else ''}")
if d.get("is_error"):
    print(f"error={d.get('subtype', 'is_error')}")
PY

mapfile_args=()
while IFS= read -r line; do mapfile_args+=("$line"); done <"$OUT/.parsed"
if [ $rc -eq 0 ] && ! grep -q '^error=' "$OUT/.parsed"; then
  status ok "finished_at=$(date -Iseconds)" "report=$report" "${mapfile_args[@]}"
  rm -f "$raw" "$raw.err"
else
  status failed "finished_at=$(date -Iseconds)" "exit=$rc" "stderr=$(tail -c 500 "$raw.err" | tr '\n' ' ')" "${mapfile_args[@]}"
fi
rm -f "$OUT/.parsed"
# 狀態頁馬上反映結果(不用等下一個 scheduler tick)
/opt/homebrew/bin/python3 "$HUB/bin/gen-status-page.py" >/dev/null 2>&1 || true
# 舊報告留 90 天
find "$OUT" -name '20*.md' -mtime +90 -delete 2>/dev/null
