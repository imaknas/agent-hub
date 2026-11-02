#!/usr/bin/env bash
# On a new machine, or after the local folder is lost, reattach a project that "was already onboarded".
# Not the same thing as onboard-project.sh; neither can replace the other:
#
#   onboard-project.sh assumes the project was never attached to agent-hub, and creates a brand-new orphan
#   agent-memory branch + a brand-new bare repo on the hub machine. Running onboard-project.sh on a project that
#   "was actually onboarded, only its local folder is gone" creates a brand-new, blank agent-memory branch completely
#   cut off from the old history, which orphans the real memory history already on the hub machine: not a rescue,
#   but a second, unrelated memory.
#
#   recover-project.sh assumes the agent-memory branch history on the hub machine is still there (memory_bare looked
#   up in the project registry projects/<name>.yaml), and only reattaches the local working copy to that existing
#   branch, creating no new branch/bare repo.
#
# If a precondition isn't met it fails and exits right away; it doesn't guess.
#
# Usage: recover-project.sh <name> [target path, default: the local_path recorded in the manifest]
set -euo pipefail

AGENT_HUB="${AGENT_HUB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
# the machine that runs the hub (hub.json hosts.server; read with plutil, since this script may also run on a
# machine without the platform's Python)
source "$(dirname "${BASH_SOURCE[0]}")/hubpaths.sh"
MINI_HOST="$(hub_server)"
TEMPLATES="$AGENT_HUB/templates"
_rd="$(hub_json_get registry_dir)"; _rd="${_rd:-projects}"; _rd="${_rd/#\~/$HOME}"
# hub.json registry_dir, relative to the config directory
REGISTRY="$([ "${_rd#/}" != "$_rd" ] && echo "$_rd" || echo "$HUB_CONFIG/$_rd")"

fail() { echo "recover-project: $1" >&2; exit 1; }

[ $# -ge 1 ] || fail "$(hub_msg "usage: recover-project.sh <name> [target path]")"
NAME="$1"

[ -d "$REGISTRY" ] || fail "$(hub_msg "{path} not found: first make sure brunna itself is cloned" path="$REGISTRY")"

row=$("$AGENT_HUB/bin/py" "$AGENT_HUB/bin/hubconf.py" roster | awk -F'\t' -v n="$NAME" '$1==n{print; found=1} END{exit !found}') \
  || fail "$(hub_msg "'{name}' is not in the project registry ({path}): check the spelling, or the project was never registered" name="$NAME" path="$REGISTRY")"

origin_url=$(echo "$row" | cut -f3)
memory_bare_repo=$(echo "$row" | cut -f4)
manifest_path=$(echo "$row" | cut -f2)
TARGET_PATH="${2:-$manifest_path}"

[ "$origin_url" != "(native)" ] || fail "$(hub_msg "'{name}' was a native project (no external GitHub remote).
So its code was never backed up anywhere but the original machine: the registry entry only has
memory_bare_repo ({memory_bare}), which is the history of memory tracking, not code.
If the original machine's disk is still there, rescue the code from it first, then decide by hand how to attach
{memory_bare}; this script will not guess at code." name="$NAME" memory_bare="$memory_bare_repo")"

[ ! -e "$TARGET_PATH" ] || fail "$(hub_msg "{path} already exists, not overwriting: sort it out by hand, then run again" path="$TARGET_PATH")"

hub_say "[1/5] Preconditions OK: {name} (origin={origin}, restoring to {path})" name="$NAME" origin="$origin_url" path="$TARGET_PATH"

# --- 2. clone the real code ---
git clone -q "$origin_url" "$TARGET_PATH"
cd "$TARGET_PATH"
hub_say "[2/5] Cloned the code from {origin}" origin="$origin_url"

# --- 3. attach the existing agent-memory branch (not a new one) ---
# When restoring locally on the hub machine (working copy in ~/workspace/<name>), the bare repo is on this machine:
# the hub machine can't resolve its own tailnet name, so use the local path; memory pulls/pushes then skip ssh too
bare_rel="${memory_bare_repo#*:~/}"
if [ "$bare_rel" != "$memory_bare_repo" ] && git -C "$HOME/$bare_rel" rev-parse --is-bare-repository >/dev/null 2>&1; then
  memory_bare_repo="$HOME/$bare_rel"
fi
git remote add "$MEMORY_REMOTE" "$memory_bare_repo"
git fetch -q "$MEMORY_REMOTE" agent-memory
git worktree add -b agent-memory .agent-memory "$MEMORY_REMOTE/agent-memory" -q
hub_say "[3/5] .agent-memory is on the existing agent-memory branch of {memory_bare} (full history, not a new branch)" memory_bare="$memory_bare_repo"

# --- 4-5. Local-only setup: the agent-hub block in CLAUDE.local.md, the .claude/settings.local.json hooks, and
#         making sure both are ignored (if .gitignore doesn't exclude them, they go into .git/info/exclude).
#         Neither goes into the outer repo: they contain the hub's path and names, which some public repos
#         deliberately don't take (see adaptive-iteration). Same script as onboard and every SessionStart.
"$AGENT_HUB/bin/sync-local-setup.sh" .
hub_say "[4-5/5] Local setup is aligned (rules-file block / hooks / ignores)"

echo
hub_say "Done: {name} is restored to {path}, with the full agent-memory history." name="$NAME" path="$TARGET_PATH"
