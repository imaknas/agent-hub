#!/usr/bin/env bash
# 把任何專案接上 agent-hub 的 memory 追蹤管線。統一做法,不分 native/hosted:
# 假設每個 repo 遲早都會有真的外部 remote(GitHub),所以 mini 永遠不當任何
# 專案主分支的 origin,永遠只用一個完全隔離的 orphan worktree 處理 memory
# 追蹤——這個安全性質不該建立在「這個專案剛好還沒有外部歷史」這種會過期的
# 前提上。
#
# 純機械化操作——每一步都是固定指令,沒有任何地方需要判斷;前置條件不滿足
# 就直接失敗退出,不會自己猜著做。有沒有外部 remote 只影響兩個「有就做、
# 沒有就跳過」的小步驟(setup commit 要不要 push、要不要登記進 mirror 清單),
# 不是兩套不同流程。
#
# 用法:onboard-project.sh <本機專案路徑>
#
# 做的事(照順序):
#   1. 前置條件檢查(是 git repo、working tree 乾淨、還沒 onboard 過)
#   2. git worktree add --orphan -b agent-memory .agent-memory
#   3. 複製 AGENT.md/STATE.md 骨架、建空的 memory/,在 agent-memory 分支上 commit
#   4. 外層 .gitignore 加一行排除 .agent-memory/
#   5. 合併(不是覆寫)外層 .claude/settings.json 的 hook 設定
#   6. commit 這兩個外層檔案;如果有 origin 就 push(用 git-safe-push.sh,
#      遇到分岔自動重試),沒有 origin 就只留在本地,不強行加一個
#   7. mini 上建 bare repo,push agent-memory 分支過去(不動任何外部 remote)
#   8. mini 上 clone 一份工作副本給 librarian
#   9. 如果有外部 origin,登記進 mirror-backup 清單;沒有就跳過
set -euo pipefail

MINI_HOST="hub-host"
TEMPLATES="$HOME/templates"
AGENT_HUB="$HOME/agent-hub"

fail() { echo "onboard-project: $1" >&2; exit 1; }

[ $# -eq 1 ] || fail "用法: onboard-project.sh <本機專案路徑>"
PROJ_PATH="$(cd "$1" && pwd)"
PROJ_NAME="$(basename "$PROJ_PATH")"
cd "$PROJ_PATH"

# --- 1. 前置條件 ---
[ -d .git ] || fail "$PROJ_PATH 不是 git repo"

origin_url=$(git remote get-url origin 2>/dev/null || true)
case "$origin_url" in
  *"$MINI_HOST"*) fail "origin 目前指向 mini,這不該發生在新設計裡——這個專案可能是舊版 onboarding 遺留的,需要先手動處理" ;;
esac

[ -z "$(git status --porcelain)" ] || fail "working tree 不乾淨(有未 commit 的變動或未追蹤檔案),先自己處理乾淨再跑"

[ ! -e .agent-memory ] || fail ".agent-memory 已經存在,這個專案應該已經 onboard 過了"

if git show-ref --verify --quiet refs/heads/agent-memory; then
  fail "agent-memory 分支已經存在,可能上次跑到一半失敗——請先手動清理(git branch -D agent-memory)再重跑"
fi

if [ -n "$origin_url" ]; then
  echo "[1/9] 前置條件檢查通過:$PROJ_NAME(有外部 remote:$origin_url)"
else
  echo "[1/9] 前置條件檢查通過:$PROJ_NAME(目前沒有外部 remote,純本機)"
fi

# --- 2. 建立 orphan worktree ---
git worktree add --orphan -b agent-memory .agent-memory -q
echo "[2/9] 已建立 .agent-memory worktree(agent-memory 分支,跟主分支完全隔離)"

# --- 3. 複製骨架、commit ---
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
  printf '\n# agent-hub memory tracking(獨立 worktree,見 %s/bin/onboard-project.sh)\n.agent-memory/\n' "$AGENT_HUB" >> .gitignore
fi
echo "[4/9] .gitignore 已更新"

# --- 5. 合併外層 .claude/settings.json ---
mkdir -p .claude
python3 "$AGENT_HUB/bin/merge-hook-settings.py" .claude/settings.json
echo "[5/9] .claude/settings.json 已合併 hook 設定"

# --- 6. commit,有 origin 才 push ---
git add .gitignore .claude/settings.json
git commit -q -m "chore: add agent-hub memory tracking (isolated worktree, no app code touched)"
if [ -n "$origin_url" ]; then
  outer_branch=$(git branch --show-current)
  "$AGENT_HUB/bin/git-safe-push.sh" -u origin "$outer_branch"
  echo "[6/9] 外層 commit 已推送到 origin"
else
  echo "[6/9] 外層 commit 已建立(沒有 origin,先留在本地,之後接上遠端會自然跟著推過去)"
fi

# --- 7. mini 上建 bare repo,push agent-memory 分支 ---
ssh "$MINI_HOST" "mkdir -p ~/git-origin && git init --bare -q ~/git-origin/${PROJ_NAME}-memory.git"
# 注意:遠端分支名稱刻意跟本地一樣叫 agent-memory,不要用 main——名稱不一樣
# 會讓 branch.agent-memory.merge 指向 refs/heads/main,雖然功能上沒問題
# (那本來就是遠端唯一存在的 ref),但看起來像設定錯誤,容易讓人(或其他
# agent)誤判成 bug 去查。
( cd .agent-memory && git remote add mini "$MINI_HOST:~/git-origin/${PROJ_NAME}-memory.git" && git push -u mini agent-memory:agent-memory -q )
# mini 的 git init.defaultBranch 是 main,新的 bare repo HEAD 預設指向
# refs/heads/main——但這裡只會推 agent-memory,main 永遠不會存在,HEAD 會
# 懸空,導致下一步的 plain git clone 抓不到東西。明確把 HEAD 導向正確的分支。
ssh "$MINI_HOST" "git --git-dir=\"\$HOME/git-origin/${PROJ_NAME}-memory.git\" symbolic-ref HEAD refs/heads/agent-memory"
echo "[7/9] mini 上已建 bare repo,agent-memory 分支已推送"

# --- 8. mini 上 clone 工作副本給 librarian ---
ssh "$MINI_HOST" "mkdir -p ~/projects && git clone -q ~/git-origin/${PROJ_NAME}-memory.git ~/projects/${PROJ_NAME}"
echo "[8/9] mini 上已 clone 工作副本到 ~/projects/${PROJ_NAME}"

# --- 9. 有外部 origin 才登記進 mirror-backup 清單 ---
if [ -n "$origin_url" ]; then
  touch "$AGENT_HUB/mirror-list.txt"
  if ! grep -qxF "$origin_url" "$AGENT_HUB/mirror-list.txt"; then
    echo "$origin_url" >> "$AGENT_HUB/mirror-list.txt"
  fi
  echo "[9/9] 已登記進 mirror-backup 清單"
else
  echo "[9/9] 沒有外部 remote,跳過 mirror-backup 登記"
fi

echo
echo "完成:$PROJ_NAME 已接上 agent-hub 管線。"
