#!/usr/bin/env bash
# 把一個「沒有外部 remote、純本機」的專案接上 agent-hub 的 memory 追蹤管線。
# 純機械化操作——每一步都是固定指令,沒有任何地方需要判斷;前置條件不滿足
# 就直接失敗退出,不會自己猜著做。
#
# 跟 onboard-hosted-project.sh 的差異:這種專案沒有外部歷史要保護,不需要
# 開獨立 worktree——直接在 repo 根目錄放 AGENT.md/STATE.md/memory/,hook
# 直接對這個 repo 操作,mini 就是唯一的 origin。
#
# 用法:onboard-native-project.sh <本機專案路徑>
#
# 做的事(照順序):
#   1. 前置條件檢查(是 git repo、沒有外部 remote、working tree 乾淨、
#      還沒 onboard 過)
#   2. 從 templates 複製 AGENT.md/STATE.md 骨架、建空的 memory/
#   3. 合併(不是覆寫).claude/settings.json 的 hook 設定
#   4. commit
#   5. mini 上建 bare repo
#   6. 加 mini 當 origin、push
#   7. mini 上 clone 一份工作副本給 librarian
set -euo pipefail

MINI_HOST="hub-host"
TEMPLATES="$HOME/templates"
AGENT_HUB="$HOME/agent-hub"

fail() { echo "onboard-native-project: $1" >&2; exit 1; }

[ $# -eq 1 ] || fail "用法: onboard-native-project.sh <本機專案路徑>"
PROJ_PATH="$(cd "$1" && pwd)"
PROJ_NAME="$(basename "$PROJ_PATH")"
cd "$PROJ_PATH"

# --- 1. 前置條件 ---
[ -d .git ] || fail "$PROJ_PATH 不是 git repo"

if git remote get-url origin >/dev/null 2>&1; then
  fail "已經有 origin remote,這是「hosted」類專案,應該用 onboard-hosted-project.sh"
fi

[ -z "$(git status --porcelain)" ] || fail "working tree 不乾淨(有未 commit 的變動或未追蹤檔案),先自己處理乾淨再跑"

[ ! -f AGENT.md ] || fail "AGENT.md 已經存在,這個專案應該已經 onboard 過了"

echo "[1/7] 前置條件檢查通過:$PROJ_NAME(純本機,無外部 remote)"

# --- 2. 複製骨架 ---
cp "$TEMPLATES/AGENT.md" AGENT.md
cat > STATE.md <<'EOF'
# STATE

> 由跨專案整合 agent(「圖書館員」)維護,只在有實質變化時改寫。整合 agent 尚未開始跑,
> 這份檔案目前只是佔位。
EOF
mkdir -p memory
touch memory/.gitkeep
echo "[2/7] 骨架檔案就位(AGENT.md/STATE.md/memory/)"

# --- 3. 合併 .claude/settings.json ---
mkdir -p .claude
python3 "$AGENT_HUB/bin/merge-hook-settings.py" .claude/settings.json native
echo "[3/7] .claude/settings.json 已合併 hook 設定"

# --- 4. commit ---
git add -A
git commit -q -m "init: agent-hub skeleton (AGENT.md/STATE.md/memory) + sync hooks"
echo "[4/7] 已 commit"

# --- 5. mini 上建 bare repo ---
ssh "$MINI_HOST" "mkdir -p ~/git-origin && git init --bare -q ~/git-origin/${PROJ_NAME}.git"
echo "[5/7] mini 上已建 bare repo"

# --- 6. 加 origin、push ---
current_branch=$(git branch --show-current)
git remote add origin "$MINI_HOST:~/git-origin/${PROJ_NAME}.git"
git push -u origin "$current_branch" -q
echo "[6/7] 已推送到 mini"

# --- 7. mini 上 clone 工作副本給 librarian ---
ssh "$MINI_HOST" "mkdir -p ~/projects && git clone -q ~/git-origin/${PROJ_NAME}.git ~/projects/${PROJ_NAME}"
echo "[7/7] mini 上已 clone 工作副本到 ~/projects/${PROJ_NAME}"

echo
echo "完成:$PROJ_NAME 已接上 agent-hub 管線(mini 現在是唯一的 origin)。"
