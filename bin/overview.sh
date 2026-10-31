#!/usr/bin/env bash
# A central overview for agents: each project's path/origin, the services bound to it (whether they are running, if
# launchctl is reachable on this machine), and its current STATE.md.
#
# Deliberately not another hand-maintained file that drifts from the truth -- the project registry
# (projects/*.yaml), services.tsv and each project's own .agent-memory/STATE.md are already three independent,
# individually accurate sources of truth; this script only joins the three live on every run and stores no state of
# its own.
#
# Usage: overview.sh [project name] -- without an argument it lists the overview of every project.
set -uo pipefail

AGENT_HUB="${AGENT_HUB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
source "$(dirname "$0")/hubpaths.sh"
SERVICES="$HUB_CONFIG/services.tsv"


FILTER="${1:-}"
# the same for every project: asked once, not once per project
SERVICES_HEADING="$(hub_msg "-- bound services --")"
NO_SERVICES="$(hub_msg "(no services registered)")"

"$AGENT_HUB/bin/py" "$AGENT_HUB/bin/hubconf.py" roster | while IFS=$'\t' read -r name local_path origin memory_bare_repo; do
  [ -z "$name" ] && continue
  [ -n "$FILTER" ] && [ "$name" != "$FILTER" ] && continue

  echo "=========================================="
  hub_say "Project: {name}
Path: {path}
origin: {origin}" name="$name" path="$local_path" origin="$origin"
  echo

  echo "$SERVICES_HEADING"
  if [ -f "$SERVICES" ]; then
    matched=$(awk -F'\t' -v p="$name" '$2==p{print}' "$SERVICES")
    if [ -z "$matched" ]; then
      echo "$NO_SERVICES"
    else
      echo "$matched" | while IFS=$'\t' read -r label _project owner _plist purpose _notes; do
        if command -v launchctl >/dev/null 2>&1; then
          if launchctl list 2>/dev/null | awk '{print $3}' | grep -qxF "$label"; then
            running="running"
          else
            running="NOT running"
          fi
        else
          running="$(hub_msg "(launchctl status cannot be checked on this machine, or it is not managed by launchd)")"
        fi
        echo "  - $label [$running] ($owner) — $purpose"
      done
    fi
  fi
  echo

  echo "-- STATE.md --"
  # local_path is ~/workspace/<name> on the hub machine (since 2026-10-15); it does not exist when run on the
  # laptop, so then read the memory-only clone that onboard-project.sh step 9 created on the hub machine
  # (~/.memory-copies/<name>/STATE.md) -- both paths are tried, whichever machine this runs on.
  state_dir="$local_path/.agent-memory"
  state_file="$state_dir/STATE.md"
  _mc="$(hub_json_get memory_copies)"; _mc="${_mc:-~/.memory-copies}"
  mini_clone_state_file="${_mc/#\~/$HOME}/$name/STATE.md"
  # .agent-memory is normally pulled only when the project itself opens a new session (SessionStart hook) -- this
  # script may be called at any time from another folder to look at another project and never triggers that hook,
  # so reading the local file directly could give old content the librarian already pushed but this machine has not
  # pulled yet. Pull once before reading; if that fails (offline, the hub machine not reachable yet), carry on as if
  # nothing happened rather than fail the whole script.
  if [ -d "$state_dir" ]; then
    git -C "$state_dir" pull -q 2>/dev/null || true
  fi
  if [ -f "$state_file" ]; then
    cat "$state_file"
  elif [ -f "$mini_clone_state_file" ]; then
    hub_say "(read from the hub machine's memory-only clone: {file})" file="$mini_clone_state_file"
    cat "$mini_clone_state_file"
  else
    hub_say "(no STATE.md readable on this machine: tried {state} and {copy}, neither exists)" \
      state="$state_file" copy="$mini_clone_state_file"
  fi
  echo
done
