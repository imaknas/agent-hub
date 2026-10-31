#!/usr/bin/env bash
# Hook sessions in a git worktree up to memory tracking too.
#
# The problem: a Claude Code session opened in a worktree (e.g. `<project>/.claude/worktrees/<name>`, started by an
# agent in worktree mode) has only the git-tracked files: no .agent-memory, none of the runtime's local-only files
# (the rules file, the local settings holding the hooks; none are in version control).
# So hooks don't run, notes don't sync, and the session doesn't get memory or the cross-project rules.
#
# The approach: this script hangs off the **user-level** settings (not project level: a worktree has no project local
# settings), installed by merge-hook-settings.py --worktree. In the main checkout it does nothing (the project's own
# hooks handle it, no duplication); in a linked worktree:
#   start  symlink in the main checkout's .agent-memory and the runtime's local-only files
#          (`hub-runtime local-ignores`), pull memory, the first time it links print the rules file
#          (`hub-runtime instructions-file`) into the context (it didn't exist yet when the session started),
#          remind when memory is behind
#   end    (SessionEnd / PreCompact) sync the memory notes, same script as the main checkout's hook
#   nudge  (PostToolUse) remind when notes are behind after a git commit/push; deliver new letters from the project
#          mailbox (hubbus, rate-limited)
#   start also delivers the project mailbox's letters that aren't closed yet into the context
#   prompt (UserPromptSubmit) deliver new letters from the project mailbox into the context (a user-level hook,
#          installed only with the owner's approval: U4)
# The symlink points at the same .agent-memory, and with-lock uses the same git dir, so it doesn't collide with the
# main checkout. Deleting the worktree only deletes the symlink, never the main checkout's memory.
set -uo pipefail

HUB="${AGENT_HUB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
source "$(dirname "${BASH_SOURCE[0]}")/hubpaths.sh"
event="${1:-}"

git_dir=$(git rev-parse --path-format=absolute --git-dir 2>/dev/null) || exit 0
common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || exit 0
[ "$git_dir" = "$common" ] && exit 0          # main checkout: the project's own hooks handle it
main=$(dirname "$common")                      # <main checkout>/.git → <main checkout>
[ -d "$main/.agent-memory" ] || exit 0         # not an onboarded project
top=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$top" || exit 0
# the hook JSON (stdin) can be read only once, and both memory-nudge and hubbus need it
hook_json=""
[ -t 0 ] || IFS= read -r -t 1 hook_json || true

# Hooks run outside the agent's sandbox, with the user's authority. For a sandboxed project, everything below (git in
# its repo, files it can plant, such as the marker or the rules file that get printed into the session) runs as the
# project instead (project_git.py run-as → sandboxed-run); its memory sync is the platform's bundle path. Only the
# rev-parse calls above run here: at worst a FIFO in its config hangs its own session's hook.
if [ -z "${HUB_AS_PROJECT:-}" ]; then
  "$HUB/bin/py" "$HUB/bin/project_git.py" sandboxed "$main/.agent-memory"
  case $? in
    0) [ "$event" = end ] && exec "$HUB/bin/py" "$HUB/bin/project_git.py" sync-memory --lock "$main/.agent-memory"
       printf '%s' "$hook_json" | AGENT_HUB_OFFLINE=1 "$HUB/bin/py" "$HUB/bin/project_git.py" run-as "$top" -- \
         bash "$HUB/bin/worktree-memory.sh" "$event"
       exit 0 ;;
    3) ;;
    *) exit 0 ;;
  esac
fi

case "$event" in
  start)
    linked=""
    if [ ! -e .agent-memory ]; then
      ln -s "$main/.agent-memory" .agent-memory && linked=1
    fi
    # the runtime's local-only files (rules file, local settings; `hub-runtime local-ignores`) are all in the main
    # checkout, not in the worktree (gitignored): symlink them in, add them to the shared info/exclude. Claude Code
    # reloads settings live (phase two's sandbox/permission rules)
    for f in $("$HUB/bin/py" "$HUB/bin/hub-runtime" local-ignores 2>/dev/null); do
      if [ ! -e "$f" ] && [ -f "$main/$f" ]; then
        mkdir -p "$(dirname "$f")" && ln -s "$main/$f" "$f"
        grep -qxF "$f" "$common/info/exclude" 2>/dev/null || echo "$f" >>"$common/info/exclude"
      fi
    done
    # the .agent-memory/ rule only matches a directory; the symlink needs its own exclude (info/exclude is shared by
    # all worktrees)
    grep -qxF ".agent-memory" "$common/info/exclude" 2>/dev/null || echo ".agent-memory" >>"$common/info/exclude"
    (cd .agent-memory && "$HUB/bin/with-lock.sh" git pull -q) >/dev/null 2>&1
    instr=$("$HUB/bin/py" "$HUB/bin/hub-runtime" instructions-file "$PWD" 2>/dev/null)
    if [ -n "$linked" ] && [ -n "$instr" ] && [ -f "$instr" ]; then
      hub_say "(this session is in a git worktree of {main}. Memory is linked to the main checkout's .agent-memory;
 {file} did not exist yet when the session started, so here it is; follow it:)" main="$main" file="$instr"
      cat "$instr"
    fi
    "$HUB/bin/py" "$HUB/bin/memory-nudge.py" --session-start 2>/dev/null
    # the nightly worktree cleanup (bin/worktrees.py auto_prune) deleted dependencies here: say how to rebuild them,
    # so the first test run isn't a puzzle
    if [ -f "$git_dir/agent-hub-deps-pruned" ]; then
      hub_say "(while this worktree sat idle, the nightly cleanup removed dependencies that can be rebuilt, to save space; rebuild them before running code or tests:)"
      sed 's/^/  /' "$git_dir/agent-hub-deps-pruned"
      hub_say "  once rebuilt, remove the marker: rm \"{marker}\"" marker="$git_dir/agent-hub-deps-pruned"
    fi
    [ -n "$hook_json" ] && printf '%s' "$hook_json" | "$HUB/bin/py" "$HUB/bin/hubbus" hook session-start 2>/dev/null
    ;;
  end)
    [ -e .agent-memory ] && cd .agent-memory && "$HUB/bin/with-lock.sh" "$HUB/bin/git-sync-memory.sh"
    ;;
  prompt)
    # UserPromptSubmit: deliver new letters mid-conversation (the main checkout has the project's own hook; this only
    # handles worktrees)
    [ -n "$hook_json" ] && printf '%s' "$hook_json" | "$HUB/bin/py" "$HUB/bin/hubbus" hook prompt 2>/dev/null
    ;;
  nudge)
    [ -e .agent-memory ] && printf '%s' "$hook_json" | "$HUB/bin/py" "$HUB/bin/memory-nudge.py" --post-tool
    [ -n "$hook_json" ] && printf '%s' "$hook_json" | "$HUB/bin/py" "$HUB/bin/hubbus" hook post-tool 2>/dev/null
    ;;
esac
exit 0
