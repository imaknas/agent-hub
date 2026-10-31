#!/usr/bin/env bash
# Wrap git operations in a lock so that several Claude Code sessions (SessionStart/SessionEnd hooks firing at the
# same time) do not interfere with each other on the same repo/worktree -- without the lock, two sessions committing
# and pushing at once collide on git's own .git/index.lock, and one side's sync may fail silently.
#
# `git rev-parse --git-dir` finds the real git directory: for a worktree that is .git/worktrees/<name> (a real
# directory), not the .git in the working directory (in a worktree that is actually a file, which cannot be
# mkdir'd into); this way git status does not see the lock directory and git add -A does not pick it up by mistake.
#
# If the lock still cannot be had after the timeout, give up rather than wait forever -- better to skip this sync than
# to hang the session's whole hook.
#
# Usage: with-lock.sh <command...>
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/hubpaths.sh"

[ $# -ge 1 ] || { hub_say "usage: with-lock.sh <command...>" >&2; exit 1; }

GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || { hub_say "with-lock: not in a git repo" >&2; exit 1; }
LOCK_DIR="$GIT_DIR/agent-hub-op.lock"
MAX_WAIT=30
waited=0

while ! mkdir "$LOCK_DIR" 2>/dev/null; do
  if [ "$waited" -ge "$MAX_WAIT" ]; then
    hub_say "with-lock: still no lock after waiting over {seconds}s (another session may be stuck); giving up this time" \
      seconds="$MAX_WAIT" >&2
    exit 1
  fi
  sleep 1
  waited=$((waited + 1))
done

trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT

"$@"
