#!/usr/bin/env bash
# For every project in the project registry (projects/*.yaml) that has a local .agent-memory, run the memory sync
# once more: the safety net for the SessionEnd/PreCompact hooks. If a session didn't end normally (crashed, or left
# open), the memory/ notes written that day stay in the project's local .agent-memory, where the librarian (which reads
# the bare repos) can't see them.
#
# Runs on the hub machine (local.sync-all-memory-mini), hourly via launchd: the manifest's paths
# (~/workspace/<name>).
# (The old copy on the laptop, local.sync-all-memory, was removed on 10-29: after every project moved to the hub
# machine on 10-15 it had nothing to do.)
# Zero LLM cost; nothing to commit, nothing done.
#
# Per project: run git-sync-memory.sh under with-lock (only adds memory/ and STATE.md, never git add -A), then push
# the commits that haven't gone out yet (e.g. the last push failed, or sync-local-setup.sh's AGENT.md commit).
set -uo pipefail

HUB="${AGENT_HUB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
source "$(dirname "${BASH_SOURCE[0]}")/hubpaths.sh"
SERVER="$(hub_server)"   # the machine that runs the hub (hub.json hosts.server); empty = this one
LOG_DIR="$HUB/.logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/sync-all-memory-$(date +%Y-%m-%d).log"

# Catch-up pushes too: memory commits/pushes never run the project's own git hooks: .agent-memory is a worktree of
# the same repo and shares core.hooksPath (e.g. the project's husky runs npx lint-staged).
# Under launchd there's no npx, so the hook failed and notes got stuck uncommitted (2026-10-01); with npx, the
# project's prettier rewrites their formatting. Memory notes aren't project code and shouldn't go through the
# project's lint/formatting.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null

log() { echo "[$(date '+%H:%M:%S')] $*" >>"$LOG"; }

# First check the hub machine is reachable. If not (the laptop is away from home, the tailnet is down; happened on
# 2026-09-30, 10-08 and 10-13), only commit locally, don't push, and log a single line for the whole round:
# otherwise every project waits for its own SSH timeout, piling up hundreds of failure lines a day. The first round
# after the connection is back pushes the commits that didn't go out.
# On the hub machine (~/git-origin is local) the memory remote is a local path; no check needed.
GIT_ORIGIN="$(hub_json_get git_origin)"; GIT_ORIGIN="${GIT_ORIGIN:-~/git-origin}"; GIT_ORIGIN="${GIT_ORIGIN/#\~/$HOME}"
if [ -z "$SERVER" ] || [ -d "$GIT_ORIGIN" ] || ssh -o ConnectTimeout=10 -o BatchMode=yes "$SERVER" true 2>/dev/null; then
  OFFLINE=""
else
  OFFLINE=1
  log "$(hub_msg "cannot reach the hub machine; this round commits locally and does not push")"
fi
export AGENT_HUB_OFFLINE="$OFFLINE"

# Since 2026-10-15 the manifest's paths are the hub machine's ~/workspace/<name>; ~/workspace is also scanned
# once, to cover folders not registered yet. Each path is done only once.
{
  "$HUB/bin/py" "$HUB/bin/hubconf.py" roster | cut -f1,2
  for d in "$HOME"/workspace/*/; do
    [ -d "$d.agent-memory" ] && printf '%s\t%s\n' "$(basename "$d")" "${d%/}"
  done
} | awk -F'\t' '!seen[$2]++' | while IFS=$'\t' read -r name path; do
  mem="$path/.agent-memory"
  [ -d "$mem" ] || continue
  (
    cd "$mem" || exit 1
    # a sandboxed project's sync runs as the project (git-sync-memory.sh hands it to project_git.py), which bundles
    # everything the bare repo lacks: no catch-up push from here (that would be git in its repo with the user's
    # authority)
    "$HUB/bin/py" "$HUB/bin/project_git.py" sandboxed "$(pwd -P)"
    case $? in
      0) sandboxed=1 ;;
      3) sandboxed="" ;;
      *) log "$(hub_msg "{name}: could not tell whether it is sandboxed; skipped" name="$name")"; exit 0 ;;
    esac
    before=$(git rev-parse HEAD)
    "$HUB/bin/with-lock.sh" "$HUB/bin/git-sync-memory.sh" >>"$LOG" 2>&1 \
      || log "$(hub_msg "{name}: git-sync-memory failed" name="$name")"
    if [ "$(git rev-parse HEAD)" != "$before" ]; then
      if [ -n "$OFFLINE" ]; then
        log "$(hub_msg "{name}: committed new memory; not pushed yet (cannot reach the hub machine)" name="$name")"
      else
        log "$(hub_msg "{name}: committed and pushed new memory" name="$name")"
      fi
    fi
    if [ -z "$OFFLINE" ] && [ -z "$sandboxed" ] && [ -n "$(git rev-list '@{u}..HEAD' 2>/dev/null)" ]; then
      "$HUB/bin/with-lock.sh" "$HUB/bin/git-safe-push.sh" >>"$LOG" 2>&1 \
        && log "$(hub_msg "{name}: pushed the commits that had not gone out" name="$name")" \
        || log "$(hub_msg "{name}: the catch-up push failed" name="$name")"
    fi
  )
done
exit 0
