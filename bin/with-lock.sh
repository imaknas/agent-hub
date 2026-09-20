#!/usr/bin/env bash
# 用一個 lock 包住 git 操作,避免同一個 repo/worktree 被多個 Claude Code
# session(SessionStart/SessionEnd hook 同時觸發)互相干擾——沒鎖的話兩個
# session 同時 commit+push,會撞 git 自己的 .git/index.lock,某一邊的同步
# 可能靜默失敗。
#
# 用 `git rev-parse --git-dir` 找真正的 git 目錄:worktree 的情況下這是
# .git/worktrees/<name>(一個真的目錄),不是工作目錄裡那個 .git(worktree
# 底下那其實是個檔案,不能 mkdir 進去);這樣鎖目錄不會被 git status 看到、
# 也不會被 git add -A 誤加。
#
# 逾時還是拿不到鎖就放棄,不會無限等——寧可這次同步沒做到,也不要卡死整個
# session 的 hook。
#
# 用法:with-lock.sh <command...>
set -uo pipefail

[ $# -ge 1 ] || { echo "用法: with-lock.sh <command...>" >&2; exit 1; }

GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || { echo "with-lock: 不在 git repo 裡" >&2; exit 1; }
LOCK_DIR="$GIT_DIR/agent-hub-op.lock"
MAX_WAIT=30
waited=0

while ! mkdir "$LOCK_DIR" 2>/dev/null; do
  if [ "$waited" -ge "$MAX_WAIT" ]; then
    echo "with-lock: 等超過 ${MAX_WAIT}s 還是拿不到鎖(可能有其他 session 卡住),放棄這次操作" >&2
    exit 1
  fi
  sleep 1
  waited=$((waited + 1))
done

trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT

"$@"
