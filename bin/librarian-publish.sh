#!/usr/bin/env bash
# The last step of the librarian's chain of nightly jobs (platform job agent-hub/librarian-publish, after:
# librarian, registrar, todo, memory-gaps, state-anchors): regenerate index.md's project roster, then commit only the
# files this chain wrote (today's digest, index.md, review-log.md, todo.md). This used to be the end of librarian.sh;
# split out, a deployment without an assistant runs in the same order.
# The roster is regenerated after the model has changed index.md, so even if the model touched the block by mistake
# it is overwritten with the correct content.
# Commit only the listed files, never git add -A -- the working tree may hold half-done changes by others (other
# sessions, manual edits).
set -uo pipefail
HUB="${AGENT_HUB_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
source "$(dirname "${BASH_SOURCE[0]}")/hubpaths.sh"
TODAY=$(date +%Y-%m-%d)
cd "$HUB" || exit 1
git fetch -q || true   # agent-hub's main moves only through merge-branch (commit_gate): fetch, never pull

rc=0
"$HUB/bin/gen-index-roster.sh" || { hub_say "gen-index-roster failed"; rc=1; }
files=(index.md review-log.md todo.md)
[ -f "digest/$TODAY.md" ] && files+=("digest/$TODAY.md")
if "$HUB/bin/commit-hub-files.sh" "librarian: digest / index / review-log / todo $TODAY" "${files[@]}"; then
  hub_say "committed (if anything changed) and pushed: {files}" files="${files[*]}"
else
  hub_say "commit/push failed: {files}" files="${files[*]}"
  rc=1
fi
exit $rc
