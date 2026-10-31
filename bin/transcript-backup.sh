#!/usr/bin/env bash
# Back up the full session transcripts of Claude Code's "personal" profile
# (~/.claude-personal/projects/) to the hub machine: pure backup, no processing, zero LLM cost,
# completely separate from the librarian's summary pipeline (the value of raw process data shouldn't be sacrificed by
# "compressing it into a summary first"; it should be kept whole, cheaply, and mined later when there's a concrete
# need).
#
# Deliberately touches only the "personal" profile (~/.claude-personal), never ~/.claude (the work profile, which holds
# company project content): this boundary was drawn explicitly by the user; don't widen the scope for convenience.
#
# Uses rsync -a (no --delete): the copy on the hub machine only grows, never shrinks, so even if the local Claude Code
# cache is cleared, the hub machine still has it.
#
# This script runs on the laptop (the machine the local data comes from), not on the hub machine: the opposite
# direction from git mirror-backup, because the hub machine can't connect into the laptop to read files; it can only
# wait for the laptop to push the data over itself while it's awake.
set -uo pipefail
source "$(dirname "$0")/hubpaths.sh"

SRC="$HOME/.claude-personal/projects/"
DEST_HOST="$(hub_server)"   # the machine that runs the hub (hub.json hosts.server)
[ -n "$DEST_HOST" ] || {
  echo "transcript-backup: hub.json has no hosts.server (with one machine doing everything this script isn't needed)" >&2
  exit 0
}
DEST_PATH="transcript-backups/claude-personal-projects/"
LOG_DIR="$HOME/agent-hub/.logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/transcript-backup-$(date +%Y-%m-%d).log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

log "=== transcript-backup run start ==="

ssh "$DEST_HOST" "mkdir -p $DEST_PATH" 2>>"$LOG"

if rsync -az --stats "$SRC" "$DEST_HOST:$DEST_PATH" >>"$LOG" 2>&1; then
  log "transcript backup done"
else
  log "transcript backup failed, see the log for the reason"
fi

log "=== transcript-backup run end ==="
