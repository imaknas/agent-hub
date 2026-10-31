#!/usr/bin/env bash
# For the SessionEnd hook of "native" projects (working directly in the project root, not a separate .agent-memory
# worktree). The difference from git-sync-push.sh, and the reason this exists: it adds/commits only memory/ and
# STATE.md, agent-hub's own two files, and never does `git add -A`, which would bundle in the user's other code changes
# that are not ready to commit.
#
# Nothing to commit: exit right away, no empty commit, no needless push.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Memory commits/pushes never run the project's own git hooks: .agent-memory is a worktree of the same repo and shares
# core.hooksPath (e.g. the project's husky running npx lint-staged). Under launchd there is no npx, so the hook failed
# and notes got stuck uncommitted (2026-10-01); with npx, the project's prettier rewrites their format. Memory notes
# are not project code and should not go through the project's lint/formatting.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null

git add memory/ STATE.md 2>/dev/null

if git diff --cached --quiet; then
  exit 0
fi

git commit -q -m "agent-hub: sync memory/STATE.md"
# when the hub machine cannot be reached (the caller has checked), commit locally only; a later round with a
# connection pushes it
[ -n "${AGENT_HUB_OFFLINE:-}" ] && exit 0
"$SCRIPT_DIR/git-safe-push.sh"
