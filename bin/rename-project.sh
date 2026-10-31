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

HUB="${AGENT_HUB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
source "$(dirname "$0")/hubpaths.sh"
MIRRORS="$HUB_CONFIG/mirror-list.txt"
LEGACY="$HUB_DATA/legacy-paths.tsv"
. "$HUB/bin/minilib.sh"

fail() { echo "rename-project: $1" >&2; exit 1; }

[ $# -ge 2 ] || fail "$(hub_msg "usage: rename-project.sh <old> <new> [--path <new local path>] [--origin <new URL>] [--apply]")"
OLD="$1"; NEW="$2"; shift 2
NEW_PATH=""; NEW_ORIGIN=""; APPLY=false
while [ $# -gt 0 ]; do
  case "$1" in
    --path) NEW_PATH="$2"; shift 2 ;;
    --origin) NEW_ORIGIN="$2"; shift 2 ;;
    --apply) APPLY=true; shift ;;
    *) fail "$(hub_msg "Unknown argument: {arg}" arg="$1")" ;;
  esac
done
[[ "$NEW" =~ ^[a-z0-9][a-z0-9-]*$ ]] || fail "$(hub_msg "The new name may only use lowercase letters, digits and -: {name}" name="$NEW")"

ROSTER=$("$HUB/bin/py" "$HUB/bin/hubconf.py" roster)
row=$(awk -F'\t' -v n="$OLD" '$1==n' <<<"$ROSTER")
[ -n "$row" ] || fail "$(hub_msg "'{name}' is not in the project registry" name="$OLD")"
awk -F'\t' -v n="$NEW" '$1==n{f=1} END{exit !f}' <<<"$ROSTER" && fail "$(hub_msg "'{name}' is already registered" name="$NEW")"
OLD_PATH=$(echo "$row" | cut -f2); OLD_ORIGIN=$(echo "$row" | cut -f3)
if [ -z "$NEW_PATH" ]; then
  # 資料夾名稱就是專案名稱(~/workspace/<name> 的慣例)時,跟著改名
  [ "$(basename "$OLD_PATH")" = "$OLD" ] && NEW_PATH="$(dirname "$OLD_PATH")/$NEW" || NEW_PATH="$OLD_PATH"
fi

[ -d "$OLD_PATH/.agent-memory" ] || fail "$(hub_msg "{path}/.agent-memory does not exist" path="$OLD_PATH")"
[ -z "$(git -C "$OLD_PATH" status --porcelain --untracked-files=no)" ] || fail "$(hub_msg "{path} has uncommitted changes to tracked files; deal with them first" path="$OLD_PATH")"
[ -z "$(git -C "$OLD_PATH/.agent-memory" status --porcelain)" ] || fail "$(hub_msg ".agent-memory has uncommitted changes; sync it first")"
git -C "$OLD_PATH/.agent-memory" fetch -q mini
[ "$(git -C "$OLD_PATH/.agent-memory" rev-list --count @{u}..HEAD)" = 0 ] || fail "$(hub_msg ".agent-memory has commits that are not pushed")"
[ "$NEW_PATH" = "$OLD_PATH" ] || [ ! -e "$NEW_PATH" ] || fail "$(hub_msg "{path} already exists" path="$NEW_PATH")"

CUR_ORIGIN=$(git -C "$OLD_PATH" remote get-url origin 2>/dev/null || echo "(native)")
NEW_ORIGIN="${NEW_ORIGIN:-$CUR_ORIGIN}"
if [ "$NEW_ORIGIN" != "(native)" ]; then
  git ls-remote -q "$NEW_ORIGIN" HEAD >/dev/null 2>&1 || fail "$(hub_msg "Cannot reach {origin}" origin="$NEW_ORIGIN")"
fi
OLD_MIRROR_NAME=$(basename "$OLD_ORIGIN" .git); NEW_MIRROR_NAME=$(basename "$NEW_ORIGIN" .git)

mini_sh "[ -d ~/git-origin/$OLD-memory.git ] && [ ! -e ~/git-origin/$NEW-memory.git ] && [ ! -e ~/.memory-copies/$NEW ]" \
  || fail "$(hub_msg "On the hub machine the old bare repo is missing, or a path with the new name already exists")"

# remote=mini: the name of the git remote (a token)
if [ "$NEW_PATH" != "$OLD_PATH" ]; then folder="mv $OLD_PATH → $NEW_PATH + git worktree repair"
else folder="$(hub_msg "the local folder stays where it is")"; fi
hub_say "rename-project: {old} → {new}
  projects/<name>.yaml    name {old} → {new}
                          path {old_path} → {new_path}
                          origin {old_origin} → {new_origin}
                          memory ~/git-origin/{old}-memory.git → ~/git-origin/{new}-memory.git
  mirror-list.txt         {old_origin} → {new_origin}
  hub machine             mv ~/git-origin/{old}-memory.git → {new}-memory.git
                          mv ~/.memory-copies/{old} → ~/.memory-copies/{new} (origin points at the new bare repo)
                          mv .state/{old}.sha → {new}.sha
                          mirrors/{old_mirror}.git → {new_mirror}.git (if the name changes)
  this machine            remote {remote} → {url}
                          {folder}" \
  old="$OLD" new="$NEW" old_path="$OLD_PATH" new_path="$NEW_PATH" old_origin="$OLD_ORIGIN" new_origin="$NEW_ORIGIN" \
  old_mirror="$OLD_MIRROR_NAME" new_mirror="$NEW_MIRROR_NAME" remote=mini url="$(mini_memory_url "$NEW")" folder="$folder"
$APPLY || { hub_say "(dry-run; add --apply to carry it out)"; exit 0; }

if [ "$NEW_PATH" != "$OLD_PATH" ]; then
  # lsof 找不到任何程序時 exit 1;加上 pipefail + set -e,沒有 `|| true` 的話「沒人在用」這個正常情況會讓整支腳本
  # 不出聲地結束(10-25 想法升級的實測抓到)
  # 只看「工作目錄」(-d cwd),而且要遞迴(+D):worktree 裡的 session 工作目錄在子資料夾,+d 只看最上層會漏(10-25 review)
  # 沒有 lsof(Linux 常見)就問 hubconf(讀 /proc);兩邊都是「工作目錄在這個資料夾或子資料夾裡」
  if command -v lsof >/dev/null; then
    users=$(lsof -a -d cwd +D "$OLD_PATH" 2>/dev/null | awk 'NR>1{print $1"("$2")"}' | sort -u | tr '\n' ' ' || true)
  else
    users=$("$HUB/bin/py" "$HUB/bin/hubconf.py" cwd-users "$OLD_PATH" || true)
  fi
  # 專屬的 exit code(75 = EX_TEMPFAIL,「現在有人在用」):呼叫的程式(project-rename.py)看 code,不比對字句
  [ -z "$users" ] || { hub_say "rename-project: processes still have their working directory in {path}: {users}(close that project's agent sessions / terminal tabs first, then run again)" path="$OLD_PATH" users="$users" >&2; exit 75; }
fi

# --- mini ---
mini_bash_stdin <<EOF
set -euo pipefail
mv ~/git-origin/$OLD-memory.git ~/git-origin/$NEW-memory.git
mv ~/.memory-copies/$OLD ~/.memory-copies/$NEW
git -C ~/.memory-copies/$NEW remote set-url origin ~/git-origin/$NEW-memory.git
git -C ~/.memory-copies/$NEW fetch -q origin
[ -f ~/agent-hub/.state/$OLD.sha ] && mv ~/agent-hub/.state/$OLD.sha ~/agent-hub/.state/$NEW.sha || true
EOF
# mirror 改名只在 origin 真的是網址時才做。沒有 remote 的專案 origin 是 "(native)":以前直接展開進上面那段遠端
# 腳本,變成 `[ -d …/(native).git ]` → bash 語法錯誤,而且前面的 mv 已經執行了,留下改到一半的狀態
# (10-25 想法升級的實測抓到;想法沙盒都沒有 remote)
if [ "$OLD_ORIGIN" != "(native)" ] && [ "$NEW_ORIGIN" != "(native)" ] && [ "$OLD_MIRROR_NAME" != "$NEW_MIRROR_NAME" ]; then
  mini_bash_stdin <<EOF
set -euo pipefail
if [ -d ~/git-origin/mirrors/'$OLD_MIRROR_NAME'.git ]; then
  mv ~/git-origin/mirrors/'$OLD_MIRROR_NAME'.git ~/git-origin/mirrors/'$NEW_MIRROR_NAME'.git
  git -C ~/git-origin/mirrors/'$NEW_MIRROR_NAME'.git remote set-url origin '$NEW_ORIGIN'
fi
EOF
fi
hub_say "[hub machine] bare repo / ~/.memory-copies / checkpoint / mirror renamed"

# --- 本機 ---
git -C "$OLD_PATH" remote set-url mini "$(mini_memory_url "$NEW")"
if [ "$NEW_PATH" != "$OLD_PATH" ]; then
  mkdir -p "$(dirname "$NEW_PATH")"
  mv "$OLD_PATH" "$NEW_PATH"
  git -C "$NEW_PATH" worktree repair "$NEW_PATH/.agent-memory"
fi
git -C "$NEW_PATH/.agent-memory" fetch -q mini
[ "$(git -C "$NEW_PATH/.agent-memory" rev-parse HEAD)" = "$(git -C "$NEW_PATH/.agent-memory" rev-parse @{u})" ] \
  || fail "$(hub_msg "After the move .agent-memory does not match the hub machine; stopping for a manual check")"
"$HUB/bin/sync-local-setup.sh" --quiet "$NEW_PATH" >/dev/null || true
# Claude Code 的 auto memory 跟著資料夾路徑(slug)走,資料夾搬了就把它帶到新路徑
if [ "$NEW_PATH" != "$OLD_PATH" ]; then
  slug() { echo "$1" | tr '/.' '--'; }
  for cfg in "$HOME/.claude" "$HOME/.claude-personal"; do
    old_mem="$cfg/projects/$(slug "$OLD_PATH")/memory"; new_mem="$cfg/projects/$(slug "$NEW_PATH")/memory"
    if [ -d "$old_mem" ] && [ -n "$(ls -A "$old_mem")" ] && [ ! -e "$new_mem" ]; then
      mkdir -p "$(dirname "$new_mem")" && cp -R "$old_mem" "$new_mem" && hub_say "[local] auto memory carried to the new path ({dir})" dir="$cfg"
    fi
  done
  hub_say "Note: if this project has project-scoped MCP servers (claude mcp list), their settings are stored under the old path; add them again in the new folder"
fi
hub_say "[local] remote / folder / worktree updated"

# --- 登記 ---
# 專案登錄是正本(job、service、隱私標籤都在裡面,一起搬)
"$HUB/bin/py" "$HUB/bin/registry.py" rename "$OLD" "$NEW" "$NEW_PATH" "$NEW_ORIGIN" "~/git-origin/$NEW-memory.git" \
  || fail "$(hub_msg "Renaming the registry entry failed (projects/{old}.yaml → {new}.yaml); the other steps are done, fix the registry by hand" old="$OLD" new="$NEW")"
# 舊名稱的 transcript 要算到新名稱:legacy-paths.tsv 的名稱改掉,搬走的舊路徑也記進去
awk -F'\t' -v OFS='\t' -v o="$OLD" -v n="$NEW" '$1==o{$1=n} {print}' "$LEGACY" > "$LEGACY.tmp" && mv "$LEGACY.tmp" "$LEGACY"
[ "$NEW_PATH" != "$OLD_PATH" ] && printf '%s\t%s\t%s\n' "$NEW" "$OLD_PATH" "$(hub_msg "where it was before the rename ({old})" old="$OLD")" >> "$LEGACY"
if [ "$OLD_ORIGIN" != "$NEW_ORIGIN" ] && grep -qxF "$OLD_ORIGIN" "$MIRRORS"; then
  grep -vxF "$OLD_ORIGIN" "$MIRRORS" > "$MIRRORS.tmp"; echo "$NEW_ORIGIN" >> "$MIRRORS.tmp"; mv "$MIRRORS.tmp" "$MIRRORS"
fi
"$HUB/bin/gen-index-roster.sh"
"$HUB/bin/commit-hub-files.sh" "rename project $OLD → $NEW (rename-project.sh)" \
    "projects/$OLD.yaml" "projects/$NEW.yaml" "projects/$OLD" "projects/$NEW" \
    legacy-paths.tsv mirror-list.txt index.md
hub_say "Done: {old} → {new}. Open a new agent session in {path} (the old session's working directory is gone)." old="$OLD" new="$NEW" path="$NEW_PATH"
