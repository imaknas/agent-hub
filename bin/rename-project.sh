#!/usr/bin/env bash
# 把一個已 onboard 的專案整套改名:agent-hub 登記、mini 上的 memory bare repo、
# librarian 用的 ~/.memory-copies 副本、librarian checkpoint、mirror 備份、本機資料夾
# (預設跟著改成新名稱,~/workspace/<name> 的慣例)、legacy-paths.tsv。在 mini 上跑
# (2026-10-15 起的常態),mini 端的步驟直接在本機做;在 MBP 上跑則走 ssh。memory 歷史完整保留,只是搬家,不重建。
#
# 不做的事:改 GitHub repo 名稱(先在 GitHub / 專案自己的 session 改好,
# origin 已經指向新 URL 才跑這支);改專案程式碼裡的名稱(專案自己的事)。
#
# 前置條件不滿足就直接失敗,不猜:
#   - <old> 在專案登錄(projects/*.yaml)裡、<new> 不在
#   - 本機資料夾存在,主 repo 沒有未 commit 的追蹤檔變更
#   - .agent-memory 乾淨而且已經 push
#   - 本機專案 origin 已經是新的 URL(或用 --origin 指定)
#   - 新路徑、mini 上的新路徑都還不存在
#
# 在那個專案裡開著的 Claude session 要先關掉:資料夾搬走後它的工作目錄就不見了。
# --apply 時如果還有程序的工作目錄在舊資料夾裡,直接拒絕(2026-09-27 ordal 改名時
# 沒擋到,正在開著的 session 被搬走了工作目錄)。
#
# 用法:rename-project.sh <old> <new> [--path <新本機路徑>] [--origin <新 URL>] [--apply]
#   沒有 --apply 只印出會做的事。--path 省略時,資料夾名稱等於舊名稱就跟著改成新名稱。
set -euo pipefail

HUB="$HOME/agent-hub"
MIRRORS="$HUB/mirror-list.txt"
LEGACY="$HUB/legacy-paths.tsv"
. "$HUB/bin/minilib.sh"

fail() { echo "rename-project: $1" >&2; exit 1; }

[ $# -ge 2 ] || fail "用法: rename-project.sh <old> <new> [--path <新本機路徑>] [--origin <新 URL>] [--apply]"
OLD="$1"; NEW="$2"; shift 2
NEW_PATH=""; NEW_ORIGIN=""; APPLY=false
while [ $# -gt 0 ]; do
  case "$1" in
    --path) NEW_PATH="$2"; shift 2 ;;
    --origin) NEW_ORIGIN="$2"; shift 2 ;;
    --apply) APPLY=true; shift ;;
    *) fail "不認得的參數:$1" ;;
  esac
done
[[ "$NEW" =~ ^[a-z0-9][a-z0-9-]*$ ]] || fail "新名稱只能用小寫英數跟 -:$NEW"

ROSTER=$(python3 "$HUB/bin/hubconf.py" roster)
row=$(awk -F'\t' -v n="$OLD" '$1==n' <<<"$ROSTER")
[ -n "$row" ] || fail "專案登錄裡沒有 '$OLD'"
awk -F'\t' -v n="$NEW" '$1==n{f=1} END{exit !f}' <<<"$ROSTER" && fail "'$NEW' 已經登記過了"
OLD_PATH=$(echo "$row" | cut -f2); OLD_ORIGIN=$(echo "$row" | cut -f3)
if [ -z "$NEW_PATH" ]; then
  # 資料夾名稱就是專案名稱(~/workspace/<name> 的慣例)時,跟著改名
  [ "$(basename "$OLD_PATH")" = "$OLD" ] && NEW_PATH="$(dirname "$OLD_PATH")/$NEW" || NEW_PATH="$OLD_PATH"
fi

[ -d "$OLD_PATH/.agent-memory" ] || fail "$OLD_PATH/.agent-memory 不存在"
[ -z "$(git -C "$OLD_PATH" status --porcelain --untracked-files=no)" ] || fail "$OLD_PATH 有未 commit 的追蹤檔變更,先處理"
[ -z "$(git -C "$OLD_PATH/.agent-memory" status --porcelain)" ] || fail ".agent-memory 有未 commit 的東西,先同步"
git -C "$OLD_PATH/.agent-memory" fetch -q mini
[ "$(git -C "$OLD_PATH/.agent-memory" rev-list --count @{u}..HEAD)" = 0 ] || fail ".agent-memory 有還沒 push 的 commit"
[ "$NEW_PATH" = "$OLD_PATH" ] || [ ! -e "$NEW_PATH" ] || fail "$NEW_PATH 已經存在"

CUR_ORIGIN=$(git -C "$OLD_PATH" remote get-url origin 2>/dev/null || echo "(native)")
NEW_ORIGIN="${NEW_ORIGIN:-$CUR_ORIGIN}"
if [ "$NEW_ORIGIN" != "(native)" ]; then
  git ls-remote -q "$NEW_ORIGIN" HEAD >/dev/null 2>&1 || fail "連不到 $NEW_ORIGIN"
fi
OLD_MIRROR_NAME=$(basename "$OLD_ORIGIN" .git); NEW_MIRROR_NAME=$(basename "$NEW_ORIGIN" .git)

mini_sh "[ -d ~/git-origin/$OLD-memory.git ] && [ ! -e ~/git-origin/$NEW-memory.git ] && [ ! -e ~/.memory-copies/$NEW ]" \
  || fail "mini 上舊的 bare repo 不在,或新名稱的路徑已經存在"

cat <<PLAN
rename-project: $OLD → $NEW
  projects/<name>.yaml    name $OLD → $NEW
                          path $OLD_PATH → $NEW_PATH
                          origin $OLD_ORIGIN → $NEW_ORIGIN
                          memory ~/git-origin/$OLD-memory.git → ~/git-origin/$NEW-memory.git
  mirror-list.txt         $OLD_ORIGIN → $NEW_ORIGIN
  mini                    mv ~/git-origin/$OLD-memory.git → $NEW-memory.git
                          mv ~/.memory-copies/$OLD → ~/.memory-copies/$NEW(origin 指向新 bare repo)
                          mv .state/$OLD.sha → $NEW.sha
                          mirrors/$OLD_MIRROR_NAME.git → $NEW_MIRROR_NAME.git(如果名稱有變)
  本機                    remote mini → $(mini_memory_url "$NEW")
                          $( [ "$NEW_PATH" != "$OLD_PATH" ] && echo "mv $OLD_PATH → $NEW_PATH + git worktree repair" || echo "本機資料夾不搬" )
PLAN
$APPLY || { echo "(dry-run,加 --apply 才會執行)"; exit 0; }

if [ "$NEW_PATH" != "$OLD_PATH" ]; then
  users=$(lsof +d "$OLD_PATH" 2>/dev/null | awk 'NR>1{print $1"("$2")"}' | sort -u | tr '\n' ' ')
  [ -z "$users" ] || fail "還有程序的工作目錄在 $OLD_PATH:$users——先關掉那個專案的 Claude session / 終端機分頁再跑"
fi

# --- mini ---
mini_bash_stdin <<EOF
set -euo pipefail
mv ~/git-origin/$OLD-memory.git ~/git-origin/$NEW-memory.git
mv ~/.memory-copies/$OLD ~/.memory-copies/$NEW
git -C ~/.memory-copies/$NEW remote set-url origin ~/git-origin/$NEW-memory.git
git -C ~/.memory-copies/$NEW fetch -q origin
[ -f ~/agent-hub/.state/$OLD.sha ] && mv ~/agent-hub/.state/$OLD.sha ~/agent-hub/.state/$NEW.sha || true
if [ "$OLD_MIRROR_NAME" != "$NEW_MIRROR_NAME" ] && [ -d ~/git-origin/mirrors/$OLD_MIRROR_NAME.git ]; then
  mv ~/git-origin/mirrors/$OLD_MIRROR_NAME.git ~/git-origin/mirrors/$NEW_MIRROR_NAME.git
  git -C ~/git-origin/mirrors/$NEW_MIRROR_NAME.git remote set-url origin "$NEW_ORIGIN"
fi
EOF
echo "[mini] bare repo / ~/.memory-copies / checkpoint / mirror 已改名"

# --- 本機 ---
git -C "$OLD_PATH" remote set-url mini "$(mini_memory_url "$NEW")"
if [ "$NEW_PATH" != "$OLD_PATH" ]; then
  mkdir -p "$(dirname "$NEW_PATH")"
  mv "$OLD_PATH" "$NEW_PATH"
  git -C "$NEW_PATH" worktree repair "$NEW_PATH/.agent-memory"
fi
git -C "$NEW_PATH/.agent-memory" fetch -q mini
[ "$(git -C "$NEW_PATH/.agent-memory" rev-parse HEAD)" = "$(git -C "$NEW_PATH/.agent-memory" rev-parse @{u})" ] \
  || fail "搬完之後 .agent-memory 跟 mini 對不上,停下來人工檢查"
"$HUB/bin/sync-local-setup.sh" --quiet "$NEW_PATH" >/dev/null || true
# Claude Code 的 auto memory 跟著資料夾路徑(slug)走,資料夾搬了就把它帶到新路徑
if [ "$NEW_PATH" != "$OLD_PATH" ]; then
  slug() { echo "$1" | tr '/.' '--'; }
  for cfg in "$HOME/.claude" "$HOME/.claude-personal"; do
    old_mem="$cfg/projects/$(slug "$OLD_PATH")/memory"; new_mem="$cfg/projects/$(slug "$NEW_PATH")/memory"
    if [ -d "$old_mem" ] && [ -n "$(ls -A "$old_mem")" ] && [ ! -e "$new_mem" ]; then
      mkdir -p "$(dirname "$new_mem")" && cp -R "$old_mem" "$new_mem" && echo "[本機] auto memory 帶到新路徑($cfg)"
    fi
  done
  echo "注意:這個專案如果有專案範圍的 MCP server(claude mcp list),設定是跟著舊路徑存的,要在新資料夾重新加"
fi
echo "[本機] remote / 資料夾 / worktree 已更新"

# --- 登記 ---
# 專案登錄是正本(job、service、隱私標籤都在裡面,一起搬)
/opt/homebrew/bin/python3 "$HUB/bin/registry.py" rename "$OLD" "$NEW" "$NEW_PATH" "$NEW_ORIGIN" "~/git-origin/$NEW-memory.git" \
  || fail "專案登錄改名失敗(projects/$OLD.yaml → $NEW.yaml),其他步驟已經做完,手動補登錄"
# 舊名稱的 transcript 要算到新名稱:legacy-paths.tsv 的名稱改掉,搬走的舊路徑也記進去
awk -F'\t' -v OFS='\t' -v o="$OLD" -v n="$NEW" '$1==o{$1=n} {print}' "$LEGACY" > "$LEGACY.tmp" && mv "$LEGACY.tmp" "$LEGACY"
[ "$NEW_PATH" != "$OLD_PATH" ] && printf '%s\t%s\t%s\n' "$NEW" "$OLD_PATH" "改名前($OLD)的位置" >> "$LEGACY"
if [ "$OLD_ORIGIN" != "$NEW_ORIGIN" ] && grep -qxF "$OLD_ORIGIN" "$MIRRORS"; then
  grep -vxF "$OLD_ORIGIN" "$MIRRORS" > "$MIRRORS.tmp"; echo "$NEW_ORIGIN" >> "$MIRRORS.tmp"; mv "$MIRRORS.tmp" "$MIRRORS"
fi
"$HUB/bin/gen-index-roster.sh"
"$HUB/bin/commit-hub-files.sh" "rename project $OLD → $NEW (rename-project.sh)" \
    "projects/$OLD.yaml" "projects/$NEW.yaml" "projects/$OLD" "projects/$NEW" \
    legacy-paths.tsv mirror-list.txt index.md
echo "完成:${OLD} → ${NEW}。在 ${NEW_PATH} 開新的 Claude session(舊 session 的工作目錄已經不在了)。"
