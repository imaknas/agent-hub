#!/usr/bin/env bash
# 把一個「已經有外部 remote(GitHub)、有真實開發歷史」的專案接上 agent-hub
# 的 memory 追蹤管線。純機械化操作——每一步都是固定指令,沒有任何地方需要
# 判斷;前置條件不滿足就直接失敗退出,不會自己猜著做。
#
# 用法:onboard-hosted-project.sh <本機專案路徑>
#
# 做的事(照順序,見下面對應的編號註解):
#   1. 前置條件檢查(是 git repo、有非 mini 的 origin remote、working tree
#      乾淨、還沒 onboard 過)
#   2. git worktree add --orphan -b agent-memory .agent-memory
#      ——同一個 repo 開一個沒有共同祖先的孤兒分支,跟 main 的歷史/工作目錄
#      完全隔離
#   3. 從 templates 複製 AGENT.md/STATE.md 骨架、建空的 memory/
#   4. 外層 .gitignore 加一行排除 .agent-memory/
#   5. 合併(不是覆寫)外層 .claude/settings.json 的 hook 設定
#   6. commit 這兩個外層檔案、push 到 origin
#   7. mini 上建 bare repo,push agent-memory 分支過去(不動 origin/GitHub)
#   8. mini 上 clone 一份工作副本到 ~/projects/<name>
#   9. 把這個專案的 origin URL 登記進 mirror-backup 清單
set -euo pipefail

MINI_HOST="hub-host"
TEMPLATES="$HOME/templates"
AGENT_HUB="$HOME/agent-hub"

fail() { echo "onboard-hosted-project: $1" >&2; exit 1; }

[ $# -eq 1 ] || fail "用法: onboard-hosted-project.sh <本機專案路徑>"
PROJ_PATH="$(cd "$1" && pwd)"
PROJ_NAME="$(basename "$PROJ_PATH")"
cd "$PROJ_PATH"

# --- 1. 前置條件 ---
[ -d .git ] || fail "$PROJ_PATH 不是 git repo"

origin_url=$(git remote get-url origin 2>/dev/null) || fail "沒有 origin remote"
case "$origin_url" in
  *"$MINI_HOST"*) fail "origin 已經指向 mini,這是「native」類專案,不該用這支腳本" ;;
esac

[ -z "$(git status --porcelain)" ] || fail "working tree 不乾淨(有未 commit 的變動或未追蹤檔案),先自己處理乾淨再跑"

[ ! -e .agent-memory ] || fail ".agent-memory 已經存在,這個專案應該已經 onboard 過了"

if git show-ref --verify --quiet refs/heads/agent-memory; then
  fail "agent-memory 分支已經存在,可能上次跑到一半失敗——請先手動清理(git branch -D agent-memory)再重跑"
fi

echo "[1/9] 前置條件檢查通過:$PROJ_NAME (origin=$origin_url)"

# --- 2. 建立 orphan worktree ---
git worktree add --orphan -b agent-memory .agent-memory -q
echo "[2/9] 已建立 .agent-memory worktree(agent-memory 分支,跟 main 完全隔離)"

# --- 3. 複製骨架 ---
cp "$TEMPLATES/AGENT.md" .agent-memory/AGENT.md
cat > .agent-memory/STATE.md <<'EOF'
# STATE

> 由跨專案整合 agent(「圖書館員」)維護,只在有實質變化時改寫。整合 agent 尚未開始跑,
> 這份檔案目前只是佔位。
EOF
mkdir -p .agent-memory/memory
touch .agent-memory/memory/.gitkeep
( cd .agent-memory && git add -A && git commit -q -m "init: agent-hub memory skeleton (AGENT.md/STATE.md/memory)" )
echo "[3/9] 骨架檔案就位並已在 agent-memory 分支上 commit"

# --- 4. 外層 .gitignore ---
touch .gitignore
if ! grep -qxF '.agent-memory/' .gitignore; then
  printf '\n# agent-hub memory tracking(獨立 worktree,見 %s/bin/onboard-hosted-project.sh)\n.agent-memory/\n' "$AGENT_HUB" >> .gitignore
fi
echo "[4/9] .gitignore 已更新"

# --- 5. 合併外層 .claude/settings.json ---
mkdir -p .claude
python3 "$AGENT_HUB/bin/merge-hook-settings.py" .claude/settings.json
echo "[5/9] .claude/settings.json 已合併 hook 設定"

# --- 6. commit + push 外層小改動(用 git-safe-push.sh,遇到 origin 領先時
#         自動 pull 合併重試,不用因為分岔就整支腳本失敗退出) ---
git add .gitignore .claude/settings.json
git commit -q -m "chore: add agent-hub memory tracking (isolated worktree, no app code touched)"
"$AGENT_HUB/bin/git-safe-push.sh"
echo "[6/9] 外層 commit 已推送到 origin"

# --- 7. mini 上建 bare repo,push agent-memory 分支 ---
ssh "$MINI_HOST" "mkdir -p ~/git-origin && git init --bare -q ~/git-origin/${PROJ_NAME}-memory.git"
( cd .agent-memory && git remote add origin "$MINI_HOST:~/git-origin/${PROJ_NAME}-memory.git" && git push -u origin agent-memory:main -q )
echo "[7/9] mini 上已建 bare repo,agent-memory 分支已推送"

# --- 8. mini 上 clone 工作副本給 librarian ---
ssh "$MINI_HOST" "mkdir -p ~/projects && git clone -q ~/git-origin/${PROJ_NAME}-memory.git ~/projects/${PROJ_NAME}"
echo "[8/9] mini 上已 clone 工作副本到 ~/projects/${PROJ_NAME}"

# --- 9. 登記進 mirror-backup 清單 ---
touch "$AGENT_HUB/mirror-list.txt"
if ! grep -qxF "$origin_url" "$AGENT_HUB/mirror-list.txt"; then
  echo "$origin_url" >> "$AGENT_HUB/mirror-list.txt"
fi
echo "[9/9] 已登記進 mirror-backup 清單"

echo
echo "完成:$PROJ_NAME 已接上 agent-hub 管線(memory 追蹤 worktree + mirror 備份清單)。"
