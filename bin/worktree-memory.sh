#!/usr/bin/env bash
# 讓 git worktree 裡的 session 也接上 memory 追蹤。
#
# 問題:Claude Code 開在 worktree 的 session(例:agent 用 worktree 模式叫出來的
# `<專案>/.claude/worktrees/<名稱>`)只有 git 追蹤的檔案——沒有 .agent-memory、沒有
# 執行環境的本機專用檔(規則檔、放 hook 的本機設定;都不進版控)。
# 結果 hook 不跑、筆記不同步、session 也沒拿到 memory 跟跨專案規則。
#
# 做法:這支掛在**使用者層級**的 settings(不是專案層級,worktree 裡沒有專案的
# local settings),由 merge-hook-settings.py --worktree 安裝。在主 checkout 裡
# 什麼都不做(專案自己的 hook 會處理,避免重複);在 linked worktree 裡:
#   start  把主 checkout 的 .agent-memory、執行環境的本機專用檔(`hub-runtime local-ignores`)用 symlink 接進來,
#          pull memory,第一次接上時把規則檔(`hub-runtime instructions-file`)印進 context(session 開始時它還不存在),
#          memory 落後就提醒
#   end    (SessionEnd / PreCompact)同步 memory 筆記,跟主 checkout 的 hook 同一支腳本
#   nudge  (PostToolUse)git commit/push 之後筆記落後就提醒;專案信箱有新信就送進來(hubbus,限流)
#   start 也會把專案信箱還沒結案的信送進 context
#   prompt (UserPromptSubmit)專案信箱有新信就送進 context(使用者層級的 hook,Knas 核准才裝:U4)
# symlink 指向同一份 .agent-memory,with-lock 用的是同一個 git dir,不會跟主 checkout 互撞。
# 刪掉 worktree 只會刪 symlink,不會動到主 checkout 的 memory。
set -uo pipefail

HUB="$HOME/agent-hub"
event="${1:-}"

git_dir=$(git rev-parse --path-format=absolute --git-dir 2>/dev/null) || exit 0
common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || exit 0
[ "$git_dir" = "$common" ] && exit 0          # 主 checkout:專案自己的 hook 負責
main=$(dirname "$common")                      # <主 checkout>/.git → <主 checkout>
[ -d "$main/.agent-memory" ] || exit 0         # 不是 onboard 過的專案
top=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$top" || exit 0
# hook JSON(stdin)只能讀一次,memory-nudge 跟 hubbus 都要用
hook_json=""
[ -t 0 ] || IFS= read -r -t 1 hook_json || true

case "$event" in
  start)
    linked=""
    if [ ! -e .agent-memory ]; then
      ln -s "$main/.agent-memory" .agent-memory && linked=1
    fi
    # 執行環境的本機專用檔(規則檔、本機設定;`hub-runtime local-ignores`)都在主 checkout,worktree 沒有
    # (gitignored):接上 symlink、加進共用的 info/exclude。Claude Code 會即時重新載入設定(第二階段的 sandbox/權限規則)
    for f in $("$HUB/bin/py" "$HUB/bin/hub-runtime" local-ignores 2>/dev/null); do
      if [ ! -e "$f" ] && [ -f "$main/$f" ]; then
        mkdir -p "$(dirname "$f")" && ln -s "$main/$f" "$f"
        grep -qxF "$f" "$common/info/exclude" 2>/dev/null || echo "$f" >>"$common/info/exclude"
      fi
    done
    # .agent-memory/ 這條規則只比對目錄,symlink 要另外排除(info/exclude 所有 worktree 共用)
    grep -qxF ".agent-memory" "$common/info/exclude" 2>/dev/null || echo ".agent-memory" >>"$common/info/exclude"
    (cd .agent-memory && "$HUB/bin/with-lock.sh" git pull -q) >/dev/null 2>&1
    instr=$("$HUB/bin/py" "$HUB/bin/hub-runtime" instructions-file "$PWD" 2>/dev/null)
    if [ -n "$linked" ] && [ -n "$instr" ] && [ -f "$instr" ]; then
      echo "(這個 session 在 $main 的 git worktree 裡。memory 已經接上主 checkout 的 .agent-memory;"
      echo " $instr 在 session 開始時還不存在,內容如下,照它做:)"
      cat "$instr"
    fi
    "$HUB/bin/py" "$HUB/bin/memory-nudge.py" --session-start 2>/dev/null
    # 每晚的 worktree 清理(bin/worktrees.py auto_prune)刪過這裡的依賴:提醒怎麼重建,免得第一次跑測試時一頭霧水
    if [ -f "$git_dir/agent-hub-deps-pruned" ]; then
      echo "(這個 worktree 閒置期間,每晚的清理刪掉了重建得回來的依賴以節省空間;要跑程式或測試前先重建:)"
      sed 's/^/  /' "$git_dir/agent-hub-deps-pruned"
      echo "  重建好之後刪掉標記:rm \"$git_dir/agent-hub-deps-pruned\""
    fi
    [ -n "$hook_json" ] && printf '%s' "$hook_json" | "$HUB/bin/py" "$HUB/bin/hubbus" hook session-start 2>/dev/null
    ;;
  end)
    [ -e .agent-memory ] && cd .agent-memory && "$HUB/bin/with-lock.sh" "$HUB/bin/git-sync-memory.sh"
    ;;
  prompt)
    # UserPromptSubmit:對話中有新信就送進來(主 checkout 有專案自己的 hook,這裡只管 worktree)
    [ -n "$hook_json" ] && printf '%s' "$hook_json" | "$HUB/bin/py" "$HUB/bin/hubbus" hook prompt 2>/dev/null
    ;;
  nudge)
    [ -e .agent-memory ] && printf '%s' "$hook_json" | "$HUB/bin/py" "$HUB/bin/memory-nudge.py" --post-tool
    [ -n "$hook_json" ] && printf '%s' "$hook_json" | "$HUB/bin/py" "$HUB/bin/hubbus" hook post-tool 2>/dev/null
    ;;
esac
exit 0
