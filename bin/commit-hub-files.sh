#!/usr/bin/env bash
# After a script writes version-controlled files in agent-hub, commit+push with this: commits only the
# paths listed explicitly, never picking up anything others (other sessions, manual edits) left
# uncommitted in the working tree. Runs entirely under with-lock.sh; pushes through git-safe-push.sh.
#
# Rule: whichever script writes a version-controlled file calls this itself to commit+push it,
# rather than leaving it for "someone happens to commit by hand later" (one project's registration got stuck
# that way).
#
# Usage: commit-hub-files.sh "<commit message>" <path>... (paths relative to agent-hub)
# If none of the listed files changed, does nothing and exits 0.
set -uo pipefail

HUB="${AGENT_HUB:-${AGENT_HUB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}}"   # default = the hub this script is in
cd "$HUB" || exit 1

if [ -z "${_COMMIT_HUB_LOCKED:-}" ]; then
  _COMMIT_HUB_LOCKED=1 exec "$HUB/bin/with-lock.sh" "$0" "$@"
fi

source "$HUB/bin/hubpaths.sh"
[ $# -ge 2 ] || { hub_say "usage: commit-hub-files.sh \"<message>\" <path>..." >&2; exit 1; }
msg="$1"; shift

# Instance directory (docs/design/new-user.md 3.0/3.1): git holds only code and config. The old layout (no .hub-home)
# works as before, committing in the hub; the new layout commits the files the config repo has, and data (digest, todo,
# review-log, reflect…) isn't in git and is skipped.
if [ "$HUB_CONFIG" != "$HUB" ]; then
  keep=()
  for f in "$@"; do
    if [ -e "$HUB_CONFIG/$f" ] || git -C "$HUB_CONFIG" ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
      keep+=("$f")
    else
      hub_say "commit-hub-files: {file} is not config (data does not go into git), skipped" file="$f"
    fi
  done
  [ ${#keep[@]} -gt 0 ] || exit 0
  git -C "$HUB_CONFIG" rev-parse --git-dir >/dev/null 2>&1 \
    || { hub_say "commit-hub-files: {dir} is not a git repo, nothing committed" dir="$HUB_CONFIG"; exit 0; }
  cd "$HUB_CONFIG" || exit 1
  set -- "${keep[@]}"
fi

# Skip paths that don't exist and were never tracked (e.g. a script didn't produce that file this time),
# or git add fails the whole commit on a pathspec that matches nothing.
paths=()
for f in "$@"; do
  if [ -e "$f" ] || git ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
    paths+=("$f")
  fi
done
[ ${#paths[@]} -gt 0 ] || { hub_say "commit-hub-files: none of these exist, nothing committed: {paths}" paths="$*"; exit 0; }
set -- "${paths[@]}"

if [ -z "$(git status --porcelain -- "$@")" ]; then
  hub_say "commit-hub-files: {paths} unchanged, nothing committed" paths="$*"
  exit 0
fi

git add -- "$@" \
  && git commit -q -m "$msg" -- "$@" \
  && { [ -z "$(git remote)" ] || "$HUB/bin/git-safe-push.sh"; } \
  && hub_say "commit-hub-files: committed and pushed: {paths}" paths="$*"
