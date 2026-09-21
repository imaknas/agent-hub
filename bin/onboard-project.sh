#!/usr/bin/env bash
# 把任何專案接上 agent-hub 的 memory 追蹤管線。統一做法,不分 native/hosted、
# 不分「全新專案」跟「既有專案」:假設每個 repo 遲早都會有真的外部 remote
# (GitHub),所以 mini 永遠不當任何專案主分支的 origin,永遠只用一個完全
# 隔離的 orphan worktree 處理 memory 追蹤——這個安全性質不該建立在「這個
# 專案剛好還沒有外部歷史」這種會過期的前提上。
#
# 純機械化操作——每一步都是固定指令,沒有任何地方需要判斷。能自動補齊的
# 前提(還不是 git repo、還沒有任何 commit、還沒有 GitHub remote)一律自動
# 補齊,不需要使用者先手動做完才能跑這支腳本。唯一不會自動處理、直接失敗
# 退出的情況只有一種:**已經有 commit 歷史、但 working tree 目前是髒的**
# ——這代表使用者有真實的、還沒準備好 commit 的工作,腳本不會用猜的清掉它。
#
# 用法:onboard-project.sh [--public] [--no-remote] <本機專案路徑>
#   --public     建立 GitHub repo 時設成 public(預設 private)
#   --no-remote  不要自動建立 GitHub remote,維持純本機(native)——只有
#                真的確定這個專案不需要備份到 GitHub 時才用這個
#
# 做的事(照順序):
#   1. 前置條件檢查跟自動補齊:
#        - 目錄不存在就建立
#        - 不是 git repo 就 git init
#        - 完全沒有 commit 過,就把現有內容(可能是空的,會補一份最小
#          README.md)直接 commit 成第一個 commit——這個情況下沒有「使用者
#          還沒準備好的工作」需要保護,所以可以自動做
#        - 已經有 commit 但 working tree 髒:直接 fail,不猜、不清
#        - 沒有 origin 且沒帶 --no-remote:自動建立 GitHub repo(預設
#          private)、push 剛剛的內容過去
#   2. git worktree add --orphan -b agent-memory .agent-memory
#   3. 複製 AGENT.md/STATE.md 骨架、建空的 memory/,在 agent-memory 分支上 commit
#   4. 外層 .gitignore 加一行排除 .agent-memory/
#   5. 合併(不是覆寫)外層 .claude/settings.json 的 hook 設定
#   6. 合併(不是覆寫)外層 CLAUDE.md 的 agent-hub pointer 段落——沒有
#      CLAUDE.md 就直接用模板建一份,已經有就只在缺 pointer 時 append
#   7. commit 這三個外層檔案;如果有 origin 就 push(用 git-safe-push.sh,
#      遇到分岔自動重試),沒有 origin 就只留在本地
#   8. mini 上建 bare repo,push agent-memory 分支過去(不動任何外部 remote)
#   9. mini 上 clone 一份工作副本給 librarian
#   10. 如果有外部 origin,登記進 mirror-backup 清單;沒有就跳過
#   11. 登記進 agent-hub 自己的 onboarded-projects.tsv(不分 native/hosted,
#       一律登記)——這是換新機器或本機資料夾遺失時,唯一能找到「這裡曾經
#       onboard 過哪些專案」的地方,不依賴任何單一專案自己的 repo 內容
set -euo pipefail

MINI_HOST="hub-host"
AGENT_HUB="$HOME/agent-hub"
TEMPLATES="$AGENT_HUB/templates"

fail() { echo "onboard-project: $1" >&2; exit 1; }

VISIBILITY="--private"
NO_REMOTE=false
while [ $# -gt 0 ]; do
  case "$1" in
    --public) VISIBILITY="--public"; shift ;;
    --no-remote) NO_REMOTE=true; shift ;;
    --) shift; break ;;
    -*) fail "未知參數:$1" ;;
    *) break ;;
  esac
done
[ $# -eq 1 ] || fail "用法: onboard-project.sh [--public] [--no-remote] <本機專案路徑>"

# --- 0. mini 連線檢查,最先做——在任何本機/GitHub 副作用發生之前。
# 這支腳本後面一定要在 mini 上建 bare repo,連不上就不會有任何後續動作
# 才是安全的:不會 git init、不會建 GitHub repo、不會留下半套的狀態。
if ! ssh -o ConnectTimeout=8 -o BatchMode=yes "$MINI_HOST" true 2>/dev/null; then
  fail "連不上 mini($MINI_HOST)。這支腳本會在 mini 上建 bare repo,連不上就不做任何事——等 mini 恢復連線再重跑,這次呼叫沒有任何副作用。"
fi

mkdir -p "$1"
PROJ_PATH="$(cd "$1" && pwd)"
PROJ_NAME="$(basename "$PROJ_PATH")"
cd "$PROJ_PATH"

# --- 1. 前置條件檢查跟自動補齊 ---
if [ ! -d .git ]; then
  git init -q
fi

origin_url=$(git remote get-url origin 2>/dev/null || true)
case "$origin_url" in
  *"$MINI_HOST"*) fail "origin 目前指向 mini,這不該發生在新設計裡——這個專案可能是舊版 onboarding 遺留的,需要先手動處理" ;;
esac

if git rev-parse HEAD >/dev/null 2>&1; then
  # 已經有 commit 歷史——這才是需要保護使用者真實工作的情況,working tree
  # 必須乾淨,不猜、不自動清。
  [ -z "$(git status --porcelain)" ] || fail "working tree 不乾淨(有未 commit 的變動或未追蹤檔案),先自己處理乾淨再跑"
else
  # 完全是新 repo,還沒有任何 commit——這裡不存在「使用者還沒準備好的工作」
  # 需要保護,直接把現有內容(可能是空的)當成初始內容 commit 掉。
  # 用 find 排除 .git 本身再檢查是否為空——git init 之後 .git 一定存在,
  # `ls -A` 會把它算進去,單靠 ls -A 永遠判斷不出「空」。
  if [ -z "$(find . -mindepth 1 -not -path './.git' -not -path './.git/*')" ]; then
    printf '# %s\n' "$PROJ_NAME" > README.md
  fi
  git add -A
  git commit -q -m "initial commit"
fi

RESUME=false
if [ -e .agent-memory ]; then
  # 光檢查 bare repo 的目錄存不存在不夠——`git init --bare` 自己就會建出
  # 目錄,不代表 agent-memory 分支真的推上去過。要檢查那個 ref 是否存在。
  if ssh "$MINI_HOST" "cd ~/git-origin/${PROJ_NAME}-memory.git 2>/dev/null && git show-ref --verify --quiet refs/heads/agent-memory" 2>/dev/null; then
    fail ".agent-memory 已經存在,而且 mini 上也已經有對應的 bare repo(agent-memory 分支已推送)——這個專案應該已經 onboard 過了"
  fi
  # .agent-memory 在本機存在,但 mini 上沒有對應、有實際內容的 bare repo
  # ——這正是「mini 連不上導致上次 onboarding 卡在中途」的痕跡(本機步驟都
  # 做完了,mini 端的步驟沒機會跑,或跑到一半失敗)。不當成錯誤,直接從
  # mini 端的步驟繼續。
  RESUME=true
  echo "(偵測到本機已完成前面的步驟,但 mini 上沒有對應、有實際內容的 bare repo——像是上次被中途打斷,接著從 mini 端繼續,不重做已完成的部分)"
elif git show-ref --verify --quiet refs/heads/agent-memory; then
  fail "agent-memory 分支已經存在(但 .agent-memory worktree 不存在),可能上次跑到一半失敗——請先手動清理(git branch -D agent-memory)再重跑"
fi

if [ -z "$origin_url" ] && [ "$NO_REMOTE" != true ]; then
  gh repo create "imaknas/$PROJ_NAME" $VISIBILITY --source=. --remote=origin
  git remote set-url origin "git@github.com:imaknas/${PROJ_NAME}.git"
  outer_branch=$(git branch --show-current)
  git push -u origin "$outer_branch"
  origin_url=$(git remote get-url origin)
fi

if [ -n "$origin_url" ]; then
  echo "[1/11] 前置條件檢查通過:$PROJ_NAME(有外部 remote:$origin_url)"
else
  echo "[1/11] 前置條件檢查通過:$PROJ_NAME(--no-remote,維持純本機)"
fi

if [ "$RESUME" != true ]; then
  # --- 2. 建立 orphan worktree ---
  git worktree add --orphan -b agent-memory .agent-memory -q
  echo "[2/11] 已建立 .agent-memory worktree(agent-memory 分支,跟主分支完全隔離)"

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
  echo "[3/11] 骨架檔案就位並已在 agent-memory 分支上 commit"

  # --- 4. 外層 .gitignore ---
  touch .gitignore
  if ! grep -qxF '.agent-memory/' .gitignore; then
    printf '\n# agent-hub memory tracking(獨立 worktree,見 %s/bin/onboard-project.sh)\n.agent-memory/\n' "$AGENT_HUB" >> .gitignore
  fi
  # 有些專案的 .gitignore 有更廣的規則(例如給 data/ledger 檔案用的 `*.json`),
  # 會連帶把 hook 設定檔也忽略掉。不管目前有沒有撞到這個情況,一律加一條
  # negation 保證這個檔案一定會被追蹤到——沒撞到的話這行也不會有副作用。
  if ! grep -qxF '!.claude/settings.json' .gitignore; then
    printf '!.claude/settings.json\n' >> .gitignore
  fi
  echo "[4/11] .gitignore 已更新"

  # --- 5. 合併外層 .claude/settings.json ---
  mkdir -p .claude
  python3 "$AGENT_HUB/bin/merge-hook-settings.py" .claude/settings.json
  echo "[5/11] .claude/settings.json 已合併 hook 設定"

  # --- 6. 合併外層 CLAUDE.md 的 agent-hub pointer 段落 ---
  # 沒有 CLAUDE.md 就直接用模板;已經有(專案自己的 CLAUDE.md)就只在缺
  # pointer 時 append,不覆寫既有內容。這份 pointer 是唯一會讓「工作中的
  # session」發現有 memory 追蹤機制的地方——AGENT.md 本身不會被自動載入。
  if [ ! -f CLAUDE.md ]; then
    cp "$TEMPLATES/CLAUDE.md" CLAUDE.md
  elif ! grep -q '.agent-memory/AGENT.md' CLAUDE.md; then
    printf '\n' >> CLAUDE.md
    tail -n +2 "$TEMPLATES/CLAUDE.md" >> CLAUDE.md
  fi
  echo "[6/11] CLAUDE.md 的 agent-hub pointer 段落已就位"

  # --- 7. commit,有 origin 才 push ---
  git add .gitignore .claude/settings.json CLAUDE.md
  git commit -q -m "chore: add agent-hub memory tracking (isolated worktree, no app code touched)"
  if [ -n "$origin_url" ]; then
    outer_branch=$(git branch --show-current)
    "$AGENT_HUB/bin/git-safe-push.sh" -u origin "$outer_branch"
    echo "[7/11] 外層 commit 已推送到 origin"
  else
    echo "[7/11] 外層 commit 已建立(--no-remote,先留在本地)"
  fi
else
  echo "[2-7/11] resume 模式,已在前一次呼叫完成,跳過"
fi

# --- 8. mini 上建 bare repo,push agent-memory 分支 ---
ssh "$MINI_HOST" "mkdir -p ~/git-origin && git init --bare -q ~/git-origin/${PROJ_NAME}-memory.git"
# 注意:遠端分支名稱刻意跟本地一樣叫 agent-memory,不要用 main——名稱不一樣
# 會讓 branch.agent-memory.merge 指向 refs/heads/main,雖然功能上沒問題
# (那本來就是遠端唯一存在的 ref),但看起來像設定錯誤,容易讓人(或其他
# agent)誤判成 bug 去查。
# remote add 不是冪等的——resume 模式下,mini 這個 remote 可能已經從上次
# (在 mini 端步驟失敗前)設定好了,存在就改 URL,不存在才新增。
( cd .agent-memory && \
  if git remote get-url mini >/dev/null 2>&1; then
    git remote set-url mini "$MINI_HOST:~/git-origin/${PROJ_NAME}-memory.git"
  else
    git remote add mini "$MINI_HOST:~/git-origin/${PROJ_NAME}-memory.git"
  fi && \
  git push -u mini agent-memory:agent-memory -q )
# mini 的 git init.defaultBranch 是 main,新的 bare repo HEAD 預設指向
# refs/heads/main——但這裡只會推 agent-memory,main 永遠不會存在,HEAD 會
# 懸空,導致下一步的 plain git clone 抓不到東西。明確把 HEAD 導向正確的分支。
ssh "$MINI_HOST" "git --git-dir=\"\$HOME/git-origin/${PROJ_NAME}-memory.git\" symbolic-ref HEAD refs/heads/agent-memory"
echo "[8/11] mini 上已建 bare repo,agent-memory 分支已推送"

# --- 9. mini 上 clone 工作副本給 librarian ---
ssh "$MINI_HOST" "mkdir -p ~/projects && git clone -q ~/git-origin/${PROJ_NAME}-memory.git ~/projects/${PROJ_NAME}"
echo "[9/11] mini 上已 clone 工作副本到 ~/projects/${PROJ_NAME}"

# --- 10. 有外部 origin 才登記進 mirror-backup 清單 ---
if [ -n "$origin_url" ]; then
  touch "$AGENT_HUB/mirror-list.txt"
  if ! grep -qxF "$origin_url" "$AGENT_HUB/mirror-list.txt"; then
    echo "$origin_url" >> "$AGENT_HUB/mirror-list.txt"
  fi
  echo "[10/11] 已登記進 mirror-backup 清單"
else
  echo "[10/11] 沒有外部 remote,跳過 mirror-backup 登記"
fi

# --- 11. 登記進 agent-hub 自己的 onboarded-projects.tsv(一律登記,無論有沒有 origin) ---
MANIFEST="$AGENT_HUB/onboarded-projects.tsv"
[ -f "$MANIFEST" ] || printf 'name\tlocal_path\torigin\tmemory_bare_repo\n' > "$MANIFEST"
manifest_origin="${origin_url:-(native)}"
if ! awk -F'\t' -v n="$PROJ_NAME" '$1==n{found=1} END{exit !found}' "$MANIFEST"; then
  printf '%s\t%s\t%s\t%s\n' \
    "$PROJ_NAME" "$PROJ_PATH" "$manifest_origin" \
    "${MINI_HOST}:~/git-origin/${PROJ_NAME}-memory.git" >> "$MANIFEST"
fi
echo "[11/11] 已登記進 agent-hub 的 onboarded-projects.tsv"

echo
echo "完成:$PROJ_NAME 已接上 agent-hub 管線。"
