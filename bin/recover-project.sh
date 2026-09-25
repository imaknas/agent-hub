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
git remote add mini "$memory_bare_repo"
git fetch -q mini agent-memory
git worktree add -b agent-memory .agent-memory mini/agent-memory -q
echo "[3/5] .agent-memory 已接上 $memory_bare_repo 既有的 agent-memory 分支(完整歷史,不是新的)"

# --- 4. 確保 CLAUDE.local.md pointer 跟 .claude/settings.local.json 的 hook 都在(有些公開 repo 故意不收這些,見
#         adaptive-iteration 的先例——這裡用同一套模板/合併腳本補回本機需要的東西,
#         不會動 outer repo 的 git 歷史) ---
# pointer 寫進本機的 CLAUDE.local.md(Claude Code 會跟 CLAUDE.md 一起自動
# 載入,gitignored)——不動專案自己的 CLAUDE.md:段落裡有 agent-hub 名稱,
# 進了共享的 CLAUDE.md 就會跟著 repo 公開。舊專案若已經把 pointer 寫在
# CLAUDE.md,就不再重複加。
if ! grep -qs '.agent-memory/AGENT.md' CLAUDE.md CLAUDE.local.md; then
  [ -s CLAUDE.local.md ] && printf '\n' >> CLAUDE.local.md
  tail -n +3 "$TEMPLATES/CLAUDE.md" >> CLAUDE.local.md
fi
mkdir -p .claude
python3 "$AGENT_HUB/bin/merge-hook-settings.py" .claude/settings.local.json
echo "[4/5] CLAUDE.local.md pointer / .claude/settings.local.json hook 已補上(本機專用,不進 outer repo)"

# --- 5. 這兩個本機檔含 ~/agent-hub 路徑/名稱,確認 outer repo 不會 commit 到 ---
unignored=""
for f in .claude/settings.local.json CLAUDE.local.md; do
  git check-ignore -q "$f" 2>/dev/null || unignored="$unignored $f"
done
if [ -n "$unignored" ]; then
  echo "[5/5] ⚠️ 沒被這個 repo 的 .gitignore 排除:$unignored——在 .gitignore 加上它們,免得本機設定(含 ~/agent-hub 路徑)被 commit 進去。"
else
  echo "[5/5] 狀態正常(本機檔都已 gitignored)。"
fi

echo
echo "完成:$NAME 已還原到 $TARGET_PATH,agent-memory 完整歷史接回來了。"
