#!/usr/bin/env bash
# Rename an onboarded project end to end: the agent-hub registry entry, the memory bare repo on the hub machine, the
# librarian's ~/.memory-copies copy, the librarian checkpoint, the mirror backup, the local folder (renamed along by
# default, following the ~/workspace/<name> convention), legacy-paths.tsv. When run on the hub machine (the norm
# since 2026-10-15), the hub machine's steps are done locally; when run on the laptop, they go over ssh. The memory
# history is kept in full: it is only moved, not rebuilt.
#
# Not done here: renaming the GitHub repo (do it first on GitHub / in the project's own session, and run this only
# once origin points to the new URL); renaming things in the project's code (the project's own business).
#
# Fails outright, without guessing, when a precondition is not met:
#   - <old> is in the project registry (projects/*.yaml), <new> is not
#   - the local folder exists and the main repo has no uncommitted changes to tracked files
#   - .agent-memory is clean and already pushed
#   - the local project's origin is already the new URL (or given with --origin)
#   - neither the new path nor the new path on the hub machine exists yet
#
# Claude sessions open in that project must be closed first: once the folder moves, their working directory is gone.
# With --apply, if any process still has its working directory in the old folder, it refuses outright (when a project
# was renamed on 2026-09-27 this was not blocked, and an open session had its working directory moved away).
#
# Usage: rename-project.sh <old> <new> [--path <new local path>] [--origin <new URL>] [--apply]
#   Without --apply it only prints what it would do. Without --path, a folder named after the old name is renamed
#   to the new name.
set -euo pipefail

HUB="${AGENT_HUB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
source "$(dirname "$0")/hubpaths.sh"
MIRRORS="$HUB_CONFIG/mirror-list.txt"
LEGACY="$HUB_DATA/legacy-paths.tsv"
. "$HUB/bin/minilib.sh"

fail() { echo "rename-project: $1" >&2; exit 1; }

[ $# -ge 2 ] || fail "$(hub_msg "usage: rename-project.sh <old> <new> [--path <new local path>] [--origin <new URL>] [--apply]")"
OLD="$1"; NEW="$2"; shift 2
NEW_PATH=""; NEW_ORIGIN=""; APPLY=false
while [ $# -gt 0 ]; do
  case "$1" in
    --path) NEW_PATH="$2"; shift 2 ;;
    --origin) NEW_ORIGIN="$2"; shift 2 ;;
    --apply) APPLY=true; shift ;;
    *) fail "$(hub_msg "unknown argument: {arg}" arg="$1")" ;;
  esac
done
[[ "$NEW" =~ ^[a-z0-9][a-z0-9-]*$ ]] || fail "$(hub_msg "the new name may only use lowercase letters, digits and -: {name}" name="$NEW")"

ROSTER=$("$HUB/bin/py" "$HUB/bin/hubconf.py" roster)
row=$(awk -F'\t' -v n="$OLD" '$1==n' <<<"$ROSTER")
[ -n "$row" ] || fail "$(hub_msg "'{name}' is not in the project registry" name="$OLD")"
awk -F'\t' -v n="$NEW" '$1==n{f=1} END{exit !f}' <<<"$ROSTER" && fail "$(hub_msg "'{name}' is already registered" name="$NEW")"
OLD_PATH=$(echo "$row" | cut -f2); OLD_ORIGIN=$(echo "$row" | cut -f3)
if [ -z "$NEW_PATH" ]; then
  # when the folder name is the project name (the ~/workspace/<name> convention), rename it along
  [ "$(basename "$OLD_PATH")" = "$OLD" ] && NEW_PATH="$(dirname "$OLD_PATH")/$NEW" || NEW_PATH="$OLD_PATH"
fi

[ -d "$OLD_PATH/.agent-memory" ] || fail "$(hub_msg "{path}/.agent-memory does not exist" path="$OLD_PATH")"
[ -z "$(git -C "$OLD_PATH" status --porcelain --untracked-files=no)" ] || fail "$(hub_msg "{path} has uncommitted changes to tracked files; deal with them first" path="$OLD_PATH")"
[ -z "$(git -C "$OLD_PATH/.agent-memory" status --porcelain)" ] || fail "$(hub_msg ".agent-memory has uncommitted changes; sync it first")"
git -C "$OLD_PATH/.agent-memory" fetch -q mini
[ "$(git -C "$OLD_PATH/.agent-memory" rev-list --count @{u}..HEAD)" = 0 ] || fail "$(hub_msg ".agent-memory has commits that are not pushed")"
[ "$NEW_PATH" = "$OLD_PATH" ] || [ ! -e "$NEW_PATH" ] || fail "$(hub_msg "{path} already exists" path="$NEW_PATH")"

CUR_ORIGIN=$(git -C "$OLD_PATH" remote get-url origin 2>/dev/null || echo "(native)")
NEW_ORIGIN="${NEW_ORIGIN:-$CUR_ORIGIN}"
if [ "$NEW_ORIGIN" != "(native)" ]; then
  git ls-remote -q "$NEW_ORIGIN" HEAD >/dev/null 2>&1 || fail "$(hub_msg "cannot reach {origin}" origin="$NEW_ORIGIN")"
fi
OLD_MIRROR_NAME=$(basename "$OLD_ORIGIN" .git); NEW_MIRROR_NAME=$(basename "$NEW_ORIGIN" .git)

mini_sh "[ -d ~/git-origin/$OLD-memory.git ] && [ ! -e ~/git-origin/$NEW-memory.git ] && [ ! -e ~/.memory-copies/$NEW ]" \
  || fail "$(hub_msg "on the hub machine, the old bare repo is missing or a path with the new name already exists")"

# remote=mini: the name of the git remote (a token)
if [ "$NEW_PATH" != "$OLD_PATH" ]; then folder="mv $OLD_PATH → $NEW_PATH + git worktree repair"
else folder="$(hub_msg "the local folder stays where it is")"; fi
hub_say "rename-project: {old} → {new}
  projects/<name>.yaml    name {old} → {new}
                          path {old_path} → {new_path}
                          origin {old_origin} → {new_origin}
                          memory ~/git-origin/{old}-memory.git → ~/git-origin/{new}-memory.git
  mirror-list.txt         {old_origin} → {new_origin}
  hub machine             mv ~/git-origin/{old}-memory.git → {new}-memory.git
                          mv ~/.memory-copies/{old} → ~/.memory-copies/{new} (origin points at the new bare repo)
                          mv .state/{old}.sha → {new}.sha
                          mirrors/{old_mirror}.git → {new_mirror}.git (if the name changes)
  this machine            remote {remote} → {url}
                          {folder}" \
  old="$OLD" new="$NEW" old_path="$OLD_PATH" new_path="$NEW_PATH" old_origin="$OLD_ORIGIN" new_origin="$NEW_ORIGIN" \
  old_mirror="$OLD_MIRROR_NAME" new_mirror="$NEW_MIRROR_NAME" remote=mini url="$(mini_memory_url "$NEW")" folder="$folder"
$APPLY || { hub_say "(dry-run; add --apply to carry it out)"; exit 0; }

if [ "$NEW_PATH" != "$OLD_PATH" ]; then
  # lsof exits 1 when it finds no process; with pipefail + set -e and no `|| true`, the normal "nobody is using it"
  # case would end the whole script silently (caught by the 10-25 idea-upgrade trial run)
  # Only the working directory (-d cwd), and recursively (+D): a session in a worktree has its working directory in
  # a subfolder, and +d, which looks only at the top level, misses it (10-25 review)
  # Without lsof (common on Linux) ask hubconf (reads /proc); both mean "working directory in this folder or a
  # subfolder"
  if command -v lsof >/dev/null; then
    users=$(lsof -a -d cwd +D "$OLD_PATH" 2>/dev/null | awk 'NR>1{print $1"("$2")"}' | sort -u | tr '\n' ' ' || true)
  else
    users=$("$HUB/bin/py" "$HUB/bin/hubconf.py" cwd-users "$OLD_PATH" || true)
  fi
  # a dedicated exit code (75 = EX_TEMPFAIL, "in use right now"): the caller (project-rename.py) checks the code,
  # not the wording
  [ -z "$users" ] || { hub_say "rename-project: processes still have their working directory in {path}: {users}(close that project's agent sessions / terminal tabs first, then run again)" path="$OLD_PATH" users="$users" >&2; exit 75; }
fi

# --- mini ---
mini_bash_stdin <<EOF
set -euo pipefail
mv ~/git-origin/$OLD-memory.git ~/git-origin/$NEW-memory.git
mv ~/.memory-copies/$OLD ~/.memory-copies/$NEW
git -C ~/.memory-copies/$NEW remote set-url origin ~/git-origin/$NEW-memory.git
git -C ~/.memory-copies/$NEW fetch -q origin
[ -f ~/agent-hub/.state/$OLD.sha ] && mv ~/agent-hub/.state/$OLD.sha ~/agent-hub/.state/$NEW.sha || true
EOF
# The mirror is renamed only when origin really is a URL. A project without a remote has origin "(native)": it used
# to be expanded straight into the remote script above, giving `[ -d …/(native).git ]` -> a bash syntax error, after
# the mv before it had already run, leaving things half-renamed (caught by the 10-25 idea-upgrade trial run; idea
# sandboxes have no remote)
if [ "$OLD_ORIGIN" != "(native)" ] && [ "$NEW_ORIGIN" != "(native)" ] && [ "$OLD_MIRROR_NAME" != "$NEW_MIRROR_NAME" ]; then
  mini_bash_stdin <<EOF
set -euo pipefail
if [ -d ~/git-origin/mirrors/'$OLD_MIRROR_NAME'.git ]; then
  mv ~/git-origin/mirrors/'$OLD_MIRROR_NAME'.git ~/git-origin/mirrors/'$NEW_MIRROR_NAME'.git
  git -C ~/git-origin/mirrors/'$NEW_MIRROR_NAME'.git remote set-url origin '$NEW_ORIGIN'
fi
EOF
fi
hub_say "[hub machine] bare repo / ~/.memory-copies / checkpoint / mirror renamed"

# --- local ---
git -C "$OLD_PATH" remote set-url mini "$(mini_memory_url "$NEW")"
if [ "$NEW_PATH" != "$OLD_PATH" ]; then
  mkdir -p "$(dirname "$NEW_PATH")"
  mv "$OLD_PATH" "$NEW_PATH"
  git -C "$NEW_PATH" worktree repair "$NEW_PATH/.agent-memory"
fi
git -C "$NEW_PATH/.agent-memory" fetch -q mini
[ "$(git -C "$NEW_PATH/.agent-memory" rev-parse HEAD)" = "$(git -C "$NEW_PATH/.agent-memory" rev-parse @{u})" ] \
  || fail "$(hub_msg "after the move, .agent-memory does not match the hub machine; stopping for a manual check")"
"$HUB/bin/sync-local-setup.sh" --quiet "$NEW_PATH" >/dev/null || true
# Claude Code's auto memory follows the folder path (slug); when the folder moves, carry it to the new path
if [ "$NEW_PATH" != "$OLD_PATH" ]; then
  slug() { echo "$1" | tr '/.' '--'; }
  for cfg in "$HOME/.claude" "$HOME/.claude-personal"; do
    old_mem="$cfg/projects/$(slug "$OLD_PATH")/memory"; new_mem="$cfg/projects/$(slug "$NEW_PATH")/memory"
    if [ -d "$old_mem" ] && [ -n "$(ls -A "$old_mem")" ] && [ ! -e "$new_mem" ]; then
      mkdir -p "$(dirname "$new_mem")" && cp -R "$old_mem" "$new_mem" && hub_say "[local] auto memory carried to the new path ({dir})" dir="$cfg"
    fi
  done
  hub_say "Note: if this project has project-scoped MCP servers (claude mcp list), their settings are stored under the old path; add them again in the new folder"
fi
hub_say "[local] remote / folder / worktree updated"

# --- registry ---
# the project registry is the source of truth (jobs, services, privacy labels are all in it and move together)
"$HUB/bin/py" "$HUB/bin/registry.py" rename "$OLD" "$NEW" "$NEW_PATH" "$NEW_ORIGIN" "~/git-origin/$NEW-memory.git" \
  || fail "$(hub_msg "renaming the registry entry failed (projects/{old}.yaml → {new}.yaml); the other steps are done, so fix the registry entry by hand" old="$OLD" new="$NEW")"
# transcripts under the old name count toward the new name: rename it in legacy-paths.tsv and record the old path
# that moved away too
awk -F'\t' -v OFS='\t' -v o="$OLD" -v n="$NEW" '$1==o{$1=n} {print}' "$LEGACY" > "$LEGACY.tmp" && mv "$LEGACY.tmp" "$LEGACY"
[ "$NEW_PATH" != "$OLD_PATH" ] && printf '%s\t%s\t%s\n' "$NEW" "$OLD_PATH" "$(hub_msg "where it was before the rename ({old})" old="$OLD")" >> "$LEGACY"
if [ "$OLD_ORIGIN" != "$NEW_ORIGIN" ] && grep -qxF "$OLD_ORIGIN" "$MIRRORS"; then
  grep -vxF "$OLD_ORIGIN" "$MIRRORS" > "$MIRRORS.tmp"; echo "$NEW_ORIGIN" >> "$MIRRORS.tmp"; mv "$MIRRORS.tmp" "$MIRRORS"
fi
"$HUB/bin/gen-index-roster.sh"
"$HUB/bin/commit-hub-files.sh" "rename project $OLD → $NEW (rename-project.sh)" \
    "projects/$OLD.yaml" "projects/$NEW.yaml" "projects/$OLD" "projects/$NEW" \
    legacy-paths.tsv mirror-list.txt index.md
hub_say "Done: {old} → {new}. Open a new agent session in {path} (the old session's working directory is gone)." old="$OLD" new="$NEW" path="$NEW_PATH"
