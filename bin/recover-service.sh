#!/usr/bin/env bash
# On a new machine / after reinstalling the hub machine, recreate a background service registered in services.tsv.
# Only plists agent-hub manages itself (plist_location starting with agent-hub/launchd/)
# are handled fully automatically; things with other owners (OpenClaw, Homebrew, GitHub Actions, etc.)
# get their recreate_notes printed for a person to judge, never rebuilt by guessing: those are not agent-hub's
# jurisdiction, and meddling could break them.
#
# Either spelling of the platform's name works (docs/design/rename-brunna.md): the label asked for or the one in
# services.tsv, the location's agent-hub/ or brunna/, a template under the other spelling of its label.
#
# Usage: recover-service.sh <label>
set -euo pipefail

AGENT_HUB="${AGENT_HUB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
source "$(dirname "$0")/hubpaths.sh"
MANIFEST="$HUB_CONFIG/services.tsv"

fail() { echo "recover-service: $1" >&2; exit 1; }

[ $# -eq 1 ] || fail "$(hub_msg "usage: recover-service.sh <label>")"
LABEL="$1"

[ -f "$MANIFEST" ] || fail "$(hub_msg "{path} not found" path="$MANIFEST")"

# the label in the platform's other spelling (empty when it isn't one of the platform's)
other_label() {
  local label="$1"
  set -- $NAMES_unit
  case "$label" in
    "$1"-*|"$1".*) echo "$2${label#"$1"}" ;;
    "$2"-*|"$2".*) echo "$1${label#"$2"}" ;;
  esac
}
OTHER=$(other_label "$LABEL")

row=$(awk -F'\t' -v n="$LABEL" -v m="$OTHER" '($1==n || (m!="" && $1==m)) && !found {print; found=1} END{exit !found}' "$MANIFEST") \
  || fail "$(hub_msg "'{label}' is not in {path}" label="$LABEL" path="$MANIFEST")"
LABEL=$(echo "$row" | cut -f1)

project=$(echo "$row" | cut -f2)
owner=$(echo "$row" | cut -f3)
plist_location=$(echo "$row" | cut -f4)
purpose=$(echo "$row" | cut -f5)
recreate_notes=$(echo "$row" | cut -f6)
# a template in this repo: <name>/launchd/… with either of the platform's names
kind=other
case "$plist_location" in projects/*) kind=registry ;; esac
for _n in $NAMES_product; do
  case "$plist_location" in "$_n"/launchd/*) kind=template; template="${plist_location#"$_n"/}" ;; esac
done

case "$kind" in
  registry)
    # a service declared in the project registry: regenerate the plist from the registry and load it (render-services
    # takes either spelling of the label)
    "$AGENT_HUB/bin/py" "$AGENT_HUB/bin/render-services.py" --only "$LABEL"
    hub_say "Rebuilt from {source}: {label}" source="$plist_location" label="$LABEL"
    hub_say "Purpose: {purpose}" purpose="$purpose"
    if [ -n "$recreate_notes" ]; then hub_say "Note: {notes}" notes="$recreate_notes"; fi
    ;;
  template)
    plist_path="$AGENT_HUB/$template"
    tmpl_label=$(basename "$plist_path" .plist)
    if [ ! -f "$plist_path" ] && [ -n "$(other_label "$tmpl_label")" ]; then
      tmpl_label=$(other_label "$tmpl_label")
      plist_path="$(dirname "$plist_path")/$tmpl_label.plist"
    fi
    [ -f "$plist_path" ] || fail "$(hub_msg "{path} does not exist: is agent-hub itself behind (not pulled)?" path="$plist_path")"
    # what's under launchd/ is a template: {{hub}} becomes this machine's hub location, and the template's own label
    # the one services.tsv names (with sed: the remote machine may not have the platform's Python)
    sed -e "s#{{hub}}#$AGENT_HUB#g" -e "s#<string>${tmpl_label//./\\.}</string>#<string>$LABEL</string>#" \
      "$plist_path" >"$HOME/Library/LaunchAgents/${LABEL}.plist"
    launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/${LABEL}.plist"
    hub_say "Rebuilt and started: {label}" label="$LABEL"
    hub_say "Purpose: {purpose}" purpose="$purpose"
    if [ -n "$recreate_notes" ]; then hub_say "Note: {notes}" notes="$recreate_notes"; fi
    ;;
  *)
    hub_say "'{label}' is not a service agent-hub manages (owner: {owner}); it cannot be rebuilt automatically." label="$LABEL" owner="$owner"
    hub_say "Purpose: {purpose}" purpose="$purpose"
    hub_say "How to rebuild: {notes}" notes="$recreate_notes"
    ;;
esac
