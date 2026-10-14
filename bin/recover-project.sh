#!/usr/bin/env bash
# 在新機器上,或本機資料夾遺失後,重新接上一個「已經 onboard 過」的專案。
# 跟 onboard-project.sh 不是同一件事,不能互相取代:
#
#   onboard-project.sh 假設這個專案從來沒接過 agent-hub,會建一個全新的
#   orphan agent-memory 分支 + mini 上全新的 bare repo。對一個「其實已經
#   onboard 過,只是本機資料夾不見了」的專案跑 onboard-project.sh,會建出
#   一個全新、空白、跟舊歷史完全斷開的 agent-memory 分支,等於把 mini 上
#   已經存在的真實 memory 歷史孤兒化——不是救回來,是製造第二份互不相干
#   的記憶。
#
#   recover-project.sh 假設 mini 上的 agent-memory 分支歷史還在(從
#   onboarded-projects.tsv 查 memory_bare_repo),只是把本機工作副本重新接
#   上那個既有分支,不建立任何新的 branch/bare repo。
#
# 前置條件不滿足就直接失敗退出,不會自己猜著做。
#
# 用法:recover-project.sh <name> [目標路徑,預設用 manifest 記錄的 local_path]
set -euo pipefail

MINI_HOST="hub-host"
AGENT_HUB="$HOME/agent-hub"
TEMPLATES="$AGENT_HUB/templates"
MANIFEST="$AGENT_HUB/onboarded-projects.tsv"

fail() { echo "recover-project: $1" >&2; exit 1; }

[ $# -ge 1 ] || fail "用法: recover-project.sh <name> [目標路徑]"
NAME="$1"

[ -f "$MANIFEST" ] || fail "找不到 $MANIFEST——先確認 agent-hub 本身已經 clone 好"

row=$(awk -F'\t' -v n="$NAME" '$1==n{print; found=1} END{exit !found}' "$MANIFEST") \
  || fail "在 $MANIFEST 裡找不到 '$NAME'——確認名稱拼對了,或這個專案本來就沒登記過"

origin_url=$(echo "$row" | cut -f3)
memory_bare_repo=$(echo "$row" | cut -f4)
manifest_path=$(echo "$row" | cut -f2)
TARGET_PATH="${2:-$manifest_path}"

[ "$origin_url" != "(native)" ] || fail "'$NAME' 當初是 native 專案(沒有外部 GitHub remote)。
這代表真實程式碼從來沒有備份在這台機器以外的地方——manifest 裡只有
memory_bare_repo($memory_bare_repo),那只是 memory 追蹤的歷史,不是程式碼。
如果原本那台機器的磁碟還在,先從那裡把程式碼救出來,再手動決定怎麼接上
$memory_bare_repo;這支腳本不會用猜的方式生成程式碼。"

[ ! -e "$TARGET_PATH" ] || fail "$TARGET_PATH 已經存在,不覆蓋——確認清楚後手動處理再重跑"

echo "[1/5] 前置條件檢查通過:$NAME(origin=$origin_url,還原到 $TARGET_PATH)"

# --- 2. clone 真實程式碼 ---
git clone -q "$origin_url" "$TARGET_PATH"
cd "$TARGET_PATH"
echo "[2/5] 已從 $origin_url clone 程式碼"

# --- 3. 接上既有的 agent-memory 分支(不是建新的) ---
# 在 mini 本機還原(工作副本放 ~/workspace/<name>)時,bare repo 就在這台:
# mini 解析不到自己的 tailnet 名稱,直接用本機路徑,之後 memory 的 pull/push 也不經過 ssh
bare_rel="${memory_bare_repo#*:~/}"
if [ "$bare_rel" != "$memory_bare_repo" ] && git -C "$HOME/$bare_rel" rev-parse --is-bare-repository >/dev/null 2>&1; then
  memory_bare_repo="$HOME/$bare_rel"
fi
git remote add mini "$memory_bare_repo"
git fetch -q mini agent-memory
git worktree add -b agent-memory .agent-memory mini/agent-memory -q
echo "[3/5] .agent-memory 已接上 $memory_bare_repo 既有的 agent-memory 分支(完整歷史,不是新的)"

# --- 4-5. 本機專用設定:CLAUDE.local.md 的 agent-hub 區塊、.claude/settings.local.json
#         hook、確保兩者被 ignore(沒被 .gitignore 排除就寫進 .git/info/exclude)。
#         都不進 outer repo——含 ~/agent-hub 路徑/名稱,有些公開 repo 故意不收
#         (見 adaptive-iteration)。跟 onboard、每次 SessionStart 用同一支腳本。
"$AGENT_HUB/bin/sync-local-setup.sh" .
echo "[4-5/5] 本機設定已對齊(CLAUDE.local.md 區塊 / hook / ignore)"

echo
echo "完成:$NAME 已還原到 $TARGET_PATH,agent-memory 完整歷史接回來了。"
