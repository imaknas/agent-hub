#!/bin/sh
# A newcomer's first minutes after install.sh, on a clean machine: init, connect two projects, a question to the owner
# from inside one, a letter between them, the status page, then `brunna glance --json` must show both. Run by CI on
# Linux and macOS after installing from the checkout, and by hand in a clean VM or container.
#   BRUNNA_DIR    where install.sh put brunna (default ~/brunna)
#   INIT_ARGS     extra `brunna init` arguments (CI: --no-launchd; a VM with launchd: empty)
# Expected: the last line is "fresh install: ok"; anything else exits non-zero at the step that broke.
set -eu
hub="${BRUNNA_DIR:-$HOME/brunna}"
step() { printf '\n== %s\n' "$*"; }
fail() { printf 'fresh install: FAILED at %s\n' "$*" >&2; exit 1; }
command -v brunna >/dev/null || fail "brunna is not on PATH"
git config --global user.name >/dev/null || git config --global user.name "Fresh Install"
git config --global user.email >/dev/null || git config --global user.email "fresh@example.com"

step init
# shellcheck disable=SC2086
brunna init --yes ${INIT_ARGS:-} || fail init
brunna doctor || true   # a fresh machine has reminders (no AI provider, no alerts); doctor's exit says so, not a failure

ws="$HOME/workspace"
for p in garden-app recipe-site; do
  mkdir -p "$ws/$p" && (cd "$ws/$p" && git init -q && echo "# $p" > README.md && git add -A && git commit -qm first)
  step "connect $p"
  brunna connect "$ws/$p" --yes || fail "connect $p"
done

step "a question to the owner, from inside garden-app"
(cd "$ws/garden-app" && brunna send --to owner --type question --text "Which host for the demo?") || fail "send to owner"
step "a letter from recipe-site to garden-app"
(cd "$ws/recipe-site" && brunna send --to garden-app --type request --text "Can you share the API?") || fail "send to a project"

step "status page"
"$hub/bin/py" "$hub/bin/gen-status-page.py" || fail "status page"

step glance
brunna glance || fail glance
brunna glance --json | "$hub/bin/py" -c '
import json, sys
g = json.load(sys.stdin)
need = [x["project"] for x in g["needs_you"]]
mail = {p["project"]: p["open"] for p in g["mail"]["projects"]}
assert g["set_up"], g
assert need == ["garden-app"], f"needs_you: {need}"
assert mail == {"garden-app": 1}, f"mail: {mail}"
' || fail "glance --json"
echo "fresh install: ok"
