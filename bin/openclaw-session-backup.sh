#!/usr/bin/env bash
# Back up OpenClaw's full session transcripts on the hub machine (~/.openclaw/agents/*/sessions/) to the laptop:
# pure backup, no processing, zero LLM cost; completely separate from cross-channel-memory's approach of "summarize
# first, then write into memory/log": the raw conversations shouldn't be sacrificed to summaries.
#
# The opposite direction from transcript-backup.sh: that one pushes the laptop's data to the hub machine (because the
# hub machine is up 24/7 and the laptop sleeps); this one's data source is on the hub machine to begin with
# (OpenClaw's session files), and backing it up only makes sense somewhere that "isn't the hub machine itself", so
# it's reversed: the laptop (while awake) pulls from the hub machine. This runs on the laptop, not on the hub
# machine: same as transcript-backup.sh, just the other direction.
#
# Uses rsync -a (no --delete): the copy on the laptop only grows, never shrinks, so even if OpenClaw's session files
# on the hub machine are cleared later, the laptop still has them.
#
# Why back up a system that's being retired: some projects' cron jobs still run on OpenClaw (not migrated yet,
# deliberately left for last), so the session files keep growing and aren't purely historical data; and even once
# OpenClaw is fully retired, these transcripts are valuable in themselves (not just the distilled versions the
# librarian summarized into memory/log).
#
# 2026-09-22: it used to run on a daily schedule (launchd); the user confirmed that isn't needed: what's really wanted
# is settling the session content into project memory that agent-hub can manage (see notes like
# openclaw-raw-transcript-backfill under each project's .agent-memory/memory/), not continuously mirroring the raw
# files themselves. The schedule has been removed; this script stays for manual runs only: there's already a
# 2026-09-22 snapshot in ~/openclaw-session-backups/, and if a "last snapshot" is needed later (e.g. right before
# OpenClaw is shut down for good), just run it by hand, no redesign needed.
set -uo pipefail
source "$(dirname "$0")/hubpaths.sh"

SRC_HOST="$(hub_server)"   # the machine that runs the hub (hub.json hosts.server)
SRC_PATH="~/.openclaw/agents/"
DEST="$HOME/openclaw-session-backups/agents/"
LOG_DIR="$HOME/agent-hub/.logs"
mkdir -p "$LOG_DIR" "$DEST"
LOG="$LOG_DIR/openclaw-session-backup-$(date +%Y-%m-%d).log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

log "=== openclaw-session-backup run start ==="

if rsync -az --stats -e ssh "$SRC_HOST:$SRC_PATH" "$DEST" >>"$LOG" 2>&1; then
  log "$(hub_msg "openclaw session backup done")"
else
  log "$(hub_msg "openclaw session backup failed; the log says why")"
fi

log "=== openclaw-session-backup run end ==="
