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
#   end    (SessionEnd / PreCompact) sync the memory notes, same script as the main checkout's hook; at SessionEnd the
#          session's letters are free again
#   nudge  (PostToolUse) remind when notes are behind after a git commit/push; deliver new letters from the project
#          mailbox (rate-limited)
#   start also delivers the project mailbox's letters that aren't closed yet into the context
#   prompt (UserPromptSubmit) deliver new letters from the project mailbox into the context (a user-level hook,
#          installed only with the owner's approval: U4)
#   Every event here goes through `hub-event worktree` first, outside any sandbox: the session's record, its end, its mail
#   and the reminder (the bus is in no sandbox, and a hook's output is one JSON or none). Only what's left runs below.
#   wait   (Stop, run in the background) hand over to `hub-event wait`, the waiter that wakes the idle session when a
#          letter comes (docs/design/mailbox-runtimes.md B1); its exit code is the wake
# The symlink points at the same .agent-memory, and with-lock uses the same git dir, so it doesn't collide with the
# main checkout. Deleting the worktree only deletes the symlink, never the main checkout's memory.
set -uo pipefail

HUB="${AGENT_HUB_ROOT:-${BRUNNA_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}}"
source "$(dirname "${BASH_SOURCE[0]}")/hubpaths.sh"
event="${1:-}"

# where the repo is, from the .git pointer files (bin/gitdirs.py), not from git: this part runs as the user before it
# knows whose repo it is, and the repo's config can hang even rev-parse
IFS=$'\t' read -r top git_dir common < <("$HUB/bin/py" "$HUB/bin/gitdirs.py" "$PWD" 2>/dev/null) || exit 0
[ "$git_dir" = "$common" ] && exit 0          # main checkout: the project's own hooks handle it
main=$(dirname "$common")                      # <main checkout>/.git → <main checkout>
[ -d "$main/.agent-memory" ] || exit 0         # not an onboarded project
# wait (Stop, in the background): the runtime's waiter that wakes an idle session for a letter
# (docs/design/mailbox-runtimes.md B1). Handed over before stdin is read, never as the project (it reads only the
# platform's sessions folder, which no sandbox reaches), its exit code passed on
[ "$event" = wait ] && exec "$HUB/bin/py" "$HUB/bin/hub-event" wait
cd "$top" || exit 0
# the hook JSON (stdin) can be read only once, and both memory-nudge and hubbus need it
hook_json=""
[ -t 0 ] || IFS= read -r -t 1 hook_json || true

# The session's process record, its end and its mail, outside any sandbox (the secrets folder and the bus are in none): a
# session here is judged and told like any other (hubbus Live), and one that exits frees what it holds
if [ -z "${HUB_AS_PROJECT:-}" ] && [ -n "$hook_json" ]; then
  printf '%s' "$hook_json" | "$HUB/bin/py" "$HUB/bin/hub-event" worktree "$event" 2>/dev/null
fi

# Hooks run outside the agent's sandbox, with the user's authority. For a sandboxed project, everything below (git in
# its repo, files it can plant, such as the marker or the rules file that get printed into the session) runs as the
# project instead (project_git.py run-as → sandboxed-run); its memory sync is the platform's bundle path. What runs
# here first, finding the repo above, starts no git. $top and $main are read from the folder and from pointer files the
# project can write: whose this is is project_git.worktree_scope's call, from the folder the hook's input names first.
if [ -z "${HUB_AS_PROJECT:-}" ]; then
  memdir=$(printf '%s' "$hook_json" | "$HUB/bin/py" "$HUB/bin/hub-event" worktree-scope "$top" "$main")
  case $? in
    0) [ "$event" = end ] && exec "$HUB/bin/py" "$HUB/bin/project_git.py" sync-memory --lock "$memdir"
       # the worktree's settings (its sandbox, the platform's hooks) are the platform's to write: the project can't
       # write them there itself (the sandbox's protect list), so they are a plain file written here, not a link
       [ "$event" = start ] || exit 0   # a prompt or a tool call has nothing left to do as the project
       # a folder swapped for a link elsewhere isn't this project's to run in, nor another's to print from into this session
       case "$top/" in "$(dirname "$memdir")/"*) ;; *) exit 0 ;; esac
       "$HUB/bin/py" "$HUB/bin/render-project-settings.py" --worktree "$top" 2>/dev/null
       AGENT_HUB_OFFLINE=1 "$HUB/bin/py" "$HUB/bin/project_git.py" run-as "$top" -- \
         bash "$HUB/bin/worktree-memory.sh" "$event" </dev/null
       exit 0 ;;
    3) ;;
    4) [ "$event" = start ] && hub_say "(this folder's .git says it is a worktree of {main}, but {main} doesn't list it, so this session gets no mail or memory here. If it was moved by hand, run git worktree repair in {main}.)" main="$main"
       exit 0 ;;
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
    ;;
  end)
    [ -e .agent-memory ] && cd .agent-memory && "$HUB/bin/with-lock.sh" "$HUB/bin/git-sync-memory.sh"
    ;;
esac
exit 0
