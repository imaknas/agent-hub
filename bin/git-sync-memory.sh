#!/usr/bin/env bash
# For the SessionEnd hook of "native" projects (working directly in the project root, not a separate .agent-memory
# worktree). Why this exists: it adds/commits only memory/ and STATE.md, agent-hub's own two files, and never does
# `git add -A`, which would bundle in the user's other code changes that are not ready to commit.
#
# Nothing to commit: exit right away, no empty commit, no needless push.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Memory commits/pushes never run the project's own git hooks: .agent-memory is a worktree of the same repo and shares
# core.hooksPath (e.g. the project's husky running npx lint-staged). Under launchd there is no npx, so the hook failed
# and notes got stuck uncommitted (2026-10-01); with npx, the project's prettier rewrites their format. Memory notes
# are not project code and should not go through the project's lint/formatting.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null

# A sandboxed project's .git is its own to write (config, hooks, alternates), so its memory is synced as the project:
# its job commits, merges the bare repo's branch and hands back a bundle, which the platform fetches into the bare repo
# (project_git.py; docs/design/projects-and-repos.md section 4). Exit 3 = not a sandboxed project's memory; anything
# else that isn't 0 means it couldn't tell, and then nothing is run here.
"$SCRIPT_DIR/py" "$SCRIPT_DIR/project_git.py" sandboxed "$(pwd -P)"
case $? in
  0) exec "$SCRIPT_DIR/py" "$SCRIPT_DIR/project_git.py" sync-memory "$(pwd -P)" ;;
  3) ;;
  *) echo "git-sync-memory: could not tell whether $(pwd -P) belongs to a sandboxed project; not synced" >&2; exit 1 ;;
esac

# Only in a checkout of its own (a project's .agent-memory worktree, or a native project's root). A plain folder inside
# another repo (agent-hub's own, memory: false) would make every git command below act on that repo: on 10-31 a bare
# `git commit` here committed and pushed another session's staged files as "sync memory/STATE.md".
[ "$(git rev-parse --show-toplevel 2>/dev/null)" = "$(pwd -P)" ] || exit 0

# each path on its own: one that does not exist (no STATE.md yet) makes `git add a b` add nothing at all; -A picks up
# deletions of tracked files too
for p in memory STATE.md; do
  if [ -e "$p" ] || git ls-files --error-unmatch -- "$p" >/dev/null 2>&1; then git add -A -- "$p" 2>/dev/null; fi
done

# commit only these two paths, whatever else is staged (bash 3.2: no mapfile)
files=()
while IFS= read -r f; do files+=("$f"); done < <(git diff --cached --name-only -- memory/ STATE.md)
[ ${#files[@]} -eq 0 ] && exit 0

git commit -q -m "brunna: sync memory/STATE.md" -- "${files[@]}"
# when the hub machine cannot be reached (the caller has checked), commit locally only; a later round with a
# connection pushes it
[ -n "${AGENT_HUB_OFFLINE:-}" ] && exit 0
"$SCRIPT_DIR/git-safe-push.sh"
