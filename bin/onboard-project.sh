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
#   在 mini 上跑,新專案放 ~/workspace/<name>(2026-10-15 起所有專案都在 mini)
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
#   4. .agent-memory/ 寫進本機的 .git/info/exclude(不動會 commit 的 .gitignore)
#   5. 合併(不是覆寫)外層 .claude/settings.local.json 的 hook 設定(步驟
#      5-6 都交給 sync-local-setup.sh)——
#      刻意不用共享的 settings.json:hook 指令裡有 ~/agent-hub/... 路徑,
#      寫進會 commit 的檔案就等於把個人工具的名字/路徑放進 repo 歷史
#      (2026-09 在公開的 sycophancy-eval、the-crucible 發生過)。
#      換機器時 recover-project.sh 會重新產生這份本機檔案。
#   6. agent-hub 區塊寫進本機的 CLAUDE.local.md(gitignored,Claude Code 會
#      跟 CLAUDE.md 一起載入)——不動專案共享的 CLAUDE.md。區塊有標記,之後
#      每次 SessionStart 都會照 templates/CLAUDE.md 重生
#   7. 外層 repo 不 commit 任何東西(舊版會 commit .gitignore)
#   8. mini 上建 bare repo,push agent-memory 分支過去(不動任何外部 remote)
#   9. mini 上 clone 一份工作副本給 librarian
#   10. 如果有外部 origin,登記進 mirror-backup 清單;沒有就跳過
#   11. 登記進 agent-hub 自己的 onboarded-projects.tsv(不分 native/hosted,
#       一律登記)——這是換新機器或本機資料夾遺失時,唯一能找到「這裡曾經
#       onboard 過哪些專案」的地方,不依賴任何單一專案自己的 repo 內容
set -euo pipefail

AGENT_HUB="$HOME/agent-hub"
# 在 mini 上跑(2026-10-15 起的常態:新專案開在 mini 的 ~/workspace/<name>)就直接在本機做 mini 端的步驟
. "$AGENT_HUB/bin/minilib.sh"
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
if ! on_mini && ! ssh -o ConnectTimeout=8 -o BatchMode=yes "$MINI_HOST" true 2>/dev/null; then
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
  *"$MINI_HOST"*|*/git-origin/*) fail "origin 目前指向 mini,這不該發生在新設計裡——這個專案可能是舊版 onboarding 遺留的,需要先手動處理" ;;
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
  if mini_sh "cd ~/git-origin/${PROJ_NAME}-memory.git 2>/dev/null && git show-ref --verify --quiet refs/heads/agent-memory" 2>/dev/null; then
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
  # 所有專案一律啟用的規則(內容在 templates/rules/,由 sync-local-setup.sh 放進
  # CLAUDE.local.md)。2026-09-27 起新專案預設登記,既有專案同日全部補上。
  printf 'engineering\n' > .agent-memory/RULES
  ( cd .agent-memory && git add -A && git commit -q -m "init: agent-hub memory skeleton (AGENT.md/STATE.md/memory)" )
  echo "[3/11] 骨架檔案就位並已在 agent-memory 分支上 commit"

  # --- 4. ignore .agent-memory/ ---
  # 寫進本機的 .git/info/exclude,不寫 .gitignore:.gitignore 會 commit,連
  # `.agent-memory/` 這個名稱都不該進 repo(之後會不會公開沒有定論)。
  # sync-local-setup.sh(步驟 5-6、每次 SessionStart、recover)也會確保這件事。
  "$AGENT_HUB/bin/sync-local-setup.sh" --quiet . >/dev/null
  git check-ignore -q .agent-memory || fail ".agent-memory 沒有被 ignore,停下來(不然 git add -A 會把它當 embedded repo 加進去)"
  echo "[4/11] .agent-memory/ 已在 .git/info/exclude 排除(不動 .gitignore)"

  # --- 5-6. 本機專用設定:hook(.claude/settings.local.json)跟 CLAUDE.local.md
  # 的 agent-hub 區塊——都不進 repo(含 ~/agent-hub 路徑/名稱,公開 repo 會外洩)。
  # 交給 sync-local-setup.sh,跟 recover、每次 SessionStart 用同一份邏輯。
  "$AGENT_HUB/bin/sync-local-setup.sh" .
  echo "[5-6/11] .claude/settings.local.json hook 跟 CLAUDE.local.md 區塊已就位(不進版控)"

  # --- 7. 外層 repo 不 commit 任何東西 ---
  # 以前這裡會 commit .gitignore(訊息還寫著工具名稱);現在本機設定全在
  # ignore/exclude 的檔案裡,專案 repo 的歷史完全不會出現這套機制。
  echo "[7/11] 外層 repo 沒有任何變更、不 commit"
else
  echo "[2-7/11] resume 模式,已在前一次呼叫完成,跳過"
fi

# --- 8. mini 上建 bare repo,push agent-memory 分支 ---
mini_sh "mkdir -p ~/git-origin && git init --bare -q ~/git-origin/${PROJ_NAME}-memory.git"
# 注意:遠端分支名稱刻意跟本地一樣叫 agent-memory,不要用 main——名稱不一樣
# 會讓 branch.agent-memory.merge 指向 refs/heads/main,雖然功能上沒問題
# (那本來就是遠端唯一存在的 ref),但看起來像設定錯誤,容易讓人(或其他
# agent)誤判成 bug 去查。
# remote add 不是冪等的——resume 模式下,mini 這個 remote 可能已經從上次
# (在 mini 端步驟失敗前)設定好了,存在就改 URL,不存在才新增。
( cd .agent-memory && \
  if git remote get-url mini >/dev/null 2>&1; then
    git remote set-url mini "$(mini_memory_url "$PROJ_NAME")"
  else
    git remote add mini "$(mini_memory_url "$PROJ_NAME")"
  fi && \
  git push -u mini agent-memory:agent-memory -q )
# mini 的 git init.defaultBranch 是 main,新的 bare repo HEAD 預設指向
# refs/heads/main——但這裡只會推 agent-memory,main 永遠不會存在,HEAD 會
# 懸空,導致下一步的 plain git clone 抓不到東西。明確把 HEAD 導向正確的分支。
mini_sh "git --git-dir=\"\$HOME/git-origin/${PROJ_NAME}-memory.git\" symbolic-ref HEAD refs/heads/agent-memory"
echo "[8/11] mini 上已建 bare repo,agent-memory 分支已推送"

# --- 9. mini 上 clone 工作副本給 librarian ---
mini_sh "mkdir -p ~/.memory-copies && git clone -q ~/git-origin/${PROJ_NAME}-memory.git ~/.memory-copies/${PROJ_NAME}"
echo "[9/11] mini 上已 clone librarian 用的 memory 副本到 ~/.memory-copies/${PROJ_NAME}"

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
# 登記完直接 commit+push agent-hub——以前只寫檔不 commit,mini 上的 librarian
# /mirror-backup 要等有人剛好手動 commit 才看得到新專案(project-36 就是這樣
# 卡在 MBP 本地)。只 commit 這三個檔案,不碰 agent-hub 裡其他未 commit 的東西。
"$AGENT_HUB/bin/gen-index-roster.sh"
"$AGENT_HUB/bin/commit-hub-files.sh" "register $PROJ_NAME (onboard-project.sh)" \
    onboarded-projects.tsv mirror-list.txt index.md \
  || echo "警告:agent-hub 登記檔 commit/push 失敗,請手動處理(登記內容本身已寫入)" >&2
echo "[11/11] 已登記進 agent-hub 的 onboarded-projects.tsv,並 commit+push agent-hub"

echo
echo "完成:$PROJ_NAME 已接上 agent-hub 管線。"
