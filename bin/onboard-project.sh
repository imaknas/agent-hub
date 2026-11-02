#!/usr/bin/env bash
# Connect any project to agent-hub's memory-tracking pipeline. One approach for all, native or hosted, "brand-new"
# or "existing": assume every repo will sooner or later have a real external remote (GitHub), so the hub machine is
# never the origin of any project's main branch, and memory tracking always uses a fully isolated orphan worktree:
# this safety property shouldn't rest on a premise that expires, like "this project happens to have no external
# history yet".
#
# Purely mechanical: every step is a fixed command, nothing anywhere needs judgment. Preconditions that can be filled
# in automatically (not a git repo yet, no commits yet, no GitHub remote yet) always are; the user doesn't have to do
# them by hand before running this script. There's only one case it won't handle and exits with a failure:
# **there's commit history, but the working tree is dirty**: that means the user has real work not ready to commit,
# and the script won't guess and clear it away.
#
# Usage: onboard-project.sh [--public] [--no-remote] <local project path>
#   run on the hub machine, new projects go in ~/workspace/<name> (since 2026-10-15 every project is on the hub
#   machine)
#   --public     make the GitHub repo public when creating it (default private)
#   --no-remote  don't create a GitHub remote automatically, stay purely local (native): only use this when you're
#                really sure the project doesn't need a backup on GitHub
#
# What it does (in order):
#   1. Precondition checks and automatic fill-in:
#        - create the directory if it doesn't exist
#        - git init if it's not a git repo
#        - no commits at all: commit the existing content (possibly empty; a minimal README.md is added) as the
#          first commit: in this case there's no "work the user isn't ready with" to protect, so it can be done
#          automatically
#        - commits exist but the working tree is dirty: fail right away, no guessing, no clearing
#        - no origin and no --no-remote: create a GitHub repo automatically (private by default) and push what was
#          just committed
#   2. git worktree add --orphan -b agent-memory .agent-memory
#   3. copy the AGENT.md/STATE.md skeleton, create an empty memory/, commit on the agent-memory branch
#   4. write .agent-memory/ into the local .git/info/exclude (the committed .gitignore is untouched)
#   5. merge (not overwrite) the hook settings into the outer .claude/settings.local.json (steps 5-6 are both
#      handed to sync-local-setup.sh):
#      deliberately not the shared settings.json: the hook commands contain the hub's path on this machine, and
#      writing them into a committed file puts a personal tool's name/paths into the repo history
#      (happened in 2026-09 in the public sycophancy-eval and the-crucible).
#      On a machine change, recover-project.sh regenerates this local file.
#   6. write the agent-hub block into the local CLAUDE.local.md (gitignored; Claude Code loads it together with
#      CLAUDE.md): the project's shared CLAUDE.md is untouched. The block is marked, and every SessionStart after
#      that regenerates it from templates/agent-instructions.md
#   7. commit nothing to the outer repo (old versions committed .gitignore)
#   8. create a bare repo on the hub machine and push the agent-memory branch there (no external remote touched)
#   9. clone a working copy on the hub machine for the librarian
#   10. if there's an external origin, add it to the mirror-backup list; otherwise skip
#   11. register in agent-hub's own project registry projects/<name>.yaml (native or hosted, always registered):
#       on a new machine or after a local folder is lost, this is the only place to find "which projects were ever
#       onboarded here", independent of any single project's own repo content
set -euo pipefail

AGENT_HUB="${AGENT_HUB_ROOT:-${BRUNNA_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}}"
# when running on the hub machine (the norm since 2026-10-15: new projects open in the hub machine's
# ~/workspace/<name>) the hub machine's steps run locally
. "$AGENT_HUB/bin/minilib.sh"
TEMPLATES="$AGENT_HUB/templates"

fail() { echo "onboard-project: $1" >&2; exit 1; }

VISIBILITY="--private"
NO_REMOTE=false
while [ $# -gt 0 ]; do
  case "$1" in
    --public) VISIBILITY="--public"; shift ;;
    --no-remote) NO_REMOTE=true; shift ;;
    --) shift; break ;;
    -*) fail "$(hub_msg "unknown argument: {arg}" arg="$1")" ;;
    *) break ;;
  esac
done
[ $# -eq 1 ] || fail "$(hub_msg "usage: onboard-project.sh [--public] [--no-remote] <local project path>")"

# --- 0. Check the connection to the hub machine, first of all: before any local/GitHub side effect.
# Later this script must create a bare repo on the hub machine; if it can't connect, the safe thing is that nothing
# else happens: no git init, no GitHub repo, no half-done state left behind.
if ! on_mini && ! ssh -o ConnectTimeout=8 -o BatchMode=yes "$MINI_HOST" true 2>/dev/null; then
  fail "$(hub_msg "cannot reach the hub machine ({host}). This script creates a bare repo there, so it stopped before doing anything (this run had no side effects): run it again once the hub machine is reachable." host="$MINI_HOST")"
fi

# asked from an agent session (the MCP server's connect_project, docs/design/newcomer-path.md B5): the folder must already
# exist and, on its real path, be strictly inside a root the owner named; checked again here, where it is used, so a
# folder swapped for a symlink after the caller's check is still refused
if [ -n "${AGENTHUB_CONNECT_ROOTS:-${BRUNNA_CONNECT_ROOTS:-}}" ]; then
  [ -d "$1" ] || fail "$(hub_msg "{path} is not an existing folder" path="$1")"
  _real="$(cd "$1" && pwd -P)"; _inside=false
  IFS=: read -r -a _roots <<<"${AGENTHUB_CONNECT_ROOTS:-${BRUNNA_CONNECT_ROOTS:-}}"
  for _r in "${_roots[@]}"; do case "$_real" in "$_r"/?*) _inside=true ;; esac; done
  $_inside || fail "$(hub_msg "{path} is not inside a folder the owner named for projects" path="$_real")"
fi
mkdir -p "$1"
# the real path (-P): a folder given through a symlink is onboarded where it really is (docs/design/newcomer-path.md B6)
PROJ_PATH="$(cd "$1" && pwd -P)"
# the folder isn't trusted yet: every git call in it runs with its own hooks and fsmonitor off (B6)
sgit() { git -c core.hooksPath=/dev/null -c core.fsmonitor= "$@"; }
# where the memory bare repos live on the hub machine (hub.json git_origin), as a path its shell expands
GO_REMOTE="$(hub_json_get git_origin)"; GO_REMOTE="${GO_REMOTE:-~/git-origin}"
GO_SH="${GO_REMOTE/#\~/\$HOME}"
PROJ_NAME="$(basename "$PROJ_PATH")"
cd "$PROJ_PATH"

# --- 0b. Guard: an already onboarded project can't be onboarded again (checked before any side effect).
# Running again would create a brand-new agent-memory branch cut off from the old history: not a rescue, but a
# second memory. The most common case is "cloned again from GitHub (no .agent-memory), then called this", so looking
# only at whether .agent-memory exists locally isn't enough. The only exception is resume (local steps done, the hub
# machine's steps not yet), see below.
ROSTER=$("$AGENT_HUB/bin/py" "$AGENT_HUB/bin/hubconf.py" roster) || fail "$(hub_msg "cannot read the project registry ({path})" path="$AGENT_HUB/projects")"
RECOVER_HINT="$(hub_msg "To reconnect an existing project (new machine, folder gone, fresh clone) use bin/recover-project.sh {name} [path]" name="$PROJ_NAME")"
if awk -F'\t' -v n="$PROJ_NAME" '$1==n{f=1} END{exit !f}' <<<"$ROSTER"; then
  fail "$(hub_msg "'{name}' is already in the project registry (projects/{name}.yaml): it cannot be onboarded again. {hint}" name="$PROJ_NAME" hint="$RECOVER_HINT")"
fi
if [ ! -e .agent-memory ] && mini_sh "git --git-dir=\"$GO_SH/${PROJ_NAME}-memory.git\" show-ref --verify --quiet refs/heads/agent-memory" 2>/dev/null; then
  fail "$(hub_msg "the hub machine already has {name}-memory.git (with agent-memory history): this name was onboarded before. {hint}" name="$PROJ_NAME" hint="$RECOVER_HINT")"
fi
if [ -d .git ] && existing_origin=$(sgit remote get-url origin 2>/dev/null); then
  same=$(awk -F'\t' -v o="$existing_origin" -v n="$PROJ_NAME" '$3==o && $1!=n{print $1}' <<<"$ROSTER")
  [ -z "$same" ] || fail "$(hub_msg "this repo (origin {origin}) was onboarded as '{name}' (under another folder name). Reconnect it with bin/recover-project.sh {name} [path]; to rename it, use bin/rename-project.sh" origin="$existing_origin" name="$same")"
fi

# --- 1. precondition checks and automatic fill-in ---
if [ ! -d .git ]; then
  sgit init -q
fi

origin_url=$(sgit remote get-url origin 2>/dev/null || true)
# the hub machine as the ssh host of origin (only when hosts.server is set: an empty name would match every URL), or a
# memory bare repo path
on_hub=false
case "$origin_url" in */git-origin/*) on_hub=true ;; esac
if [ -n "$MINI_HOST" ]; then
  case "$origin_url" in "$MINI_HOST":*|*@"$MINI_HOST":*|ssh://"$MINI_HOST"[:/]*|ssh://*@"$MINI_HOST"[:/]*) on_hub=true ;; esac
fi
$on_hub && fail "$(hub_msg "origin points at the hub machine, which the current design never does: this project is probably left over from the old onboarding; deal with it by hand first")"

if sgit rev-parse HEAD >/dev/null 2>&1; then
  # There's commit history: this is the case where the user's real work needs protecting; the working tree must be
  # clean, no guessing, no automatic clearing.
  [ -z "$(sgit status --porcelain)" ] || fail "$(hub_msg "the working tree is not clean (uncommitted changes or untracked files): clean it up yourself, then run again")"
else
  # A brand-new repo with no commits at all: there's no "work the user isn't ready with" to protect here, so the
  # existing content (possibly empty) is committed as the initial content.
  # Check for emptiness with find, excluding .git itself: after git init .git always exists, and `ls -A` counts it,
  # so ls -A alone can never tell "empty".
  if [ -z "$(find . -mindepth 1 -not -path './.git' -not -path './.git/*')" ]; then
    printf '# %s\n' "$PROJ_NAME" > README.md
  fi
  sgit add -A
  sgit commit -q -m "chore: initial commit"
fi

RESUME=false
if [ -e .agent-memory ]; then
  # Checking whether the bare repo's directory exists isn't enough: `git init --bare` creates the directory by
  # itself, which doesn't mean the agent-memory branch was ever pushed. Check whether that ref exists.
  if mini_sh "cd $GO_SH/${PROJ_NAME}-memory.git 2>/dev/null && git show-ref --verify --quiet refs/heads/agent-memory" 2>/dev/null; then
    fail "$(hub_msg ".agent-memory already exists and the hub machine already has its bare repo (agent-memory branch pushed): this project should already be onboarded")"
  fi
  # .agent-memory exists locally, but the hub machine has no matching bare repo with real content: exactly the
  # trace of "the hub machine was unreachable, so the last onboarding got stuck halfway" (the local steps all done,
  # the hub machine's steps never got to run, or failed halfway). Not treated as an error: continue from the hub
  # machine's steps.
  RESUME=true
  hub_say "(The local steps are done, but the hub machine has no bare repo with content for this project: it looks like the last run was interrupted; continuing with the hub machine's steps, without redoing what is done)"
elif git show-ref --verify --quiet refs/heads/agent-memory; then
  fail "$(hub_msg "the agent-memory branch exists but the .agent-memory worktree does not: probably a run that failed halfway; clean up by hand first (git branch -D agent-memory), then run again")"
fi

if [ -z "$origin_url" ] && [ "$NO_REMOTE" != true ]; then
  # the account is read from hub.json's github.owner, not hard-coded (CLAUDE.md, "rules for writing new code")
  GH_OWNER=$("$AGENT_HUB/bin/py" "$AGENT_HUB/bin/hubconf.py" get github.owner) || fail "$(hub_msg "{settings} has no github.owner: cannot create the GitHub repo")"
  gh repo create "$GH_OWNER/$PROJ_NAME" $VISIBILITY --source=. --remote=origin
  sgit remote set-url origin "git@github.com:${GH_OWNER}/${PROJ_NAME}.git"
  outer_branch=$(sgit branch --show-current)
  sgit push -u origin "$outer_branch"
  origin_url=$(sgit remote get-url origin)
fi

if [ -n "$origin_url" ]; then
  hub_say "[1/11] Preconditions OK: {name} (external remote: {origin})" name="$PROJ_NAME" origin="$origin_url"
else
  hub_say "[1/11] Preconditions OK: {name} (--no-remote, stays local only)" name="$PROJ_NAME"
fi

if [ "$RESUME" != true ]; then
  # --- 2. create the orphan worktree ---
  sgit worktree add --orphan -b agent-memory .agent-memory -q
  hub_say "[2/11] Created the .agent-memory worktree (agent-memory branch, fully separate from the main branch)"

  # --- 3. copy the skeleton, commit ---
  cp "$TEMPLATES/AGENT.md" .agent-memory/AGENT.md
  # the placeholder is content (the content language); the librarian rewrites it
  { printf '# STATE\n\n'; hub_content "> Maintained by the cross-project agent (the librarian), rewritten only when something substantive changes. It
> has not started running yet, so for now this file is only a placeholder."; echo; } > .agent-memory/STATE.md
  mkdir -p .agent-memory/memory
  touch .agent-memory/memory/.gitkeep
  # Rules enabled for every project (content in templates/rules/, put into CLAUDE.local.md by sync-local-setup.sh).
  # New projects get engineering registered by default since 2026-09-27 and multi-agent-review since 2026-10-27;
  # existing projects were all given them the same day.
  printf 'engineering\nmulti-agent-review\n' > .agent-memory/RULES
  ( cd .agent-memory && sgit add -A && sgit commit -q -m "init: brunna memory skeleton (AGENT.md/STATE.md/memory)" )
  hub_say "[3/11] Skeleton files in place and committed on the agent-memory branch"

  # --- 4. ignore .agent-memory/ ---
  # Written into the local .git/info/exclude, not .gitignore: .gitignore gets committed, and not even the name
  # `.agent-memory/` should go into the repo (whether it goes public later is undecided).
  # sync-local-setup.sh (steps 5-6, every SessionStart, recover) also makes sure of this.
  "$AGENT_HUB/bin/sync-local-setup.sh" --quiet . >/dev/null
  sgit check-ignore -q .agent-memory || fail "$(hub_msg ".agent-memory is not ignored, stopping (otherwise git add -A would add it as an embedded repo)")"
  hub_say "[4/11] .agent-memory/ is excluded in .git/info/exclude (.gitignore untouched)"

  # --- 5-6. Local-only setup: the hooks (.claude/settings.local.json) and the agent-hub block in CLAUDE.local.md:
  # neither goes into the repo (they contain the hub's path and names, which a public repo would leak).
  # Handed to sync-local-setup.sh, the same logic as recover and every SessionStart.
  "$AGENT_HUB/bin/sync-local-setup.sh" .
  # say only what this runtime really has (with no coding agent there is neither; sync-local-setup said why)
  rt_has() { "$AGENT_HUB/bin/py" "$AGENT_HUB/bin/hub-runtime" has "$1" >/dev/null 2>&1; }
  if rt_has instructions && rt_has lifecycle; then
    hub_say "[5-6/11] Hooks and the rules-file block are in place (not version-controlled)"
  elif rt_has instructions; then
    hub_say "[5-6/11] The rules-file block is in place (not version-controlled); this runtime has no hooks"
  elif rt_has lifecycle; then
    hub_say "[5-6/11] Hooks are in place (not version-controlled); this runtime has no rules file"
  else
    hub_say "[5-6/11] No hooks and no rules-file block: this runtime has neither ({settings} runtime.default)"
  fi

  # --- 7. commit nothing to the outer repo ---
  # This used to commit .gitignore (with the tool's name in the message, even); now the local setup is all in
  # ignored/excluded files, and this machinery never shows up in the project repo's history.
  hub_say "[7/11] No changes to the outer repo, nothing committed"
else
  hub_say "[2-7/11] Resume mode: done by the previous run, skipped"
fi

# --- 8. create the bare repo on the hub machine, push the agent-memory branch ---
mini_sh "mkdir -p $GO_SH && git init --bare -q $GO_SH/${PROJ_NAME}-memory.git"
# Note: the remote branch is deliberately named agent-memory like the local one, not main: a different name makes
# branch.agent-memory.merge point at refs/heads/main, which works (it's the only ref on the remote anyway) but looks
# like a misconfiguration, and people (or other agents) easily mistake it for a bug and go digging.
# remote add isn't idempotent: in resume mode the memory remote may already be set from last time (before the hub
# machine's steps failed); if it exists, change its URL, only add it if it doesn't.
( cd .agent-memory && \
  if sgit remote get-url "$MEMORY_REMOTE" >/dev/null 2>&1; then
    sgit remote set-url "$MEMORY_REMOTE" "$(mini_memory_url "$PROJ_NAME")"
  else
    sgit remote add "$MEMORY_REMOTE" "$(mini_memory_url "$PROJ_NAME")"
  fi && \
  sgit push -u "$MEMORY_REMOTE" agent-memory:agent-memory -q )
# The hub machine's git init.defaultBranch is main, so a new bare repo's HEAD points at refs/heads/main by default;
# but only agent-memory is ever pushed here, main never exists, HEAD dangles, and the plain git clone in the next
# step gets nothing. Point HEAD at the right branch explicitly.
mini_sh "git --git-dir=\"$GO_SH/${PROJ_NAME}-memory.git\" symbolic-ref HEAD refs/heads/agent-memory"
if on_mini; then   # this machine is the hub machine: say where, not "on the hub machine"
  hub_say "[8/11] Memory bare repo created ({path}), agent-memory branch pushed" path="$GO_REMOTE/$PROJ_NAME-memory.git"
else
  hub_say "[8/11] Bare repo created on the hub machine, agent-memory branch pushed"
fi

# --- 9. clone a working copy on the hub machine for the librarian ---
# without symlinks (core.symlinks=false): the librarian reads its files, and a committed link must not point it elsewhere
mini_sh "mkdir -p ~/.memory-copies && git clone -q --config core.symlinks=false $GO_SH/${PROJ_NAME}-memory.git ~/.memory-copies/${PROJ_NAME}"
if on_mini; then
  hub_say "[9/11] Cloned the librarian's memory copy to ~/.memory-copies/{name}" name="$PROJ_NAME"
else
  hub_say "[9/11] Cloned the librarian's memory copy on the hub machine to ~/.memory-copies/{name}" name="$PROJ_NAME"
fi

# --- 10. add to the mirror-backup list only when there's an external origin ---
if [ -n "$origin_url" ]; then
  source "$AGENT_HUB/bin/hubpaths.sh"
  touch "$HUB_CONFIG/mirror-list.txt"
  if ! grep -qxF "$origin_url" "$HUB_CONFIG/mirror-list.txt"; then
    echo "$origin_url" >> "$HUB_CONFIG/mirror-list.txt"
  fi
  hub_say "[10/11] Added to the mirror-backup list"
else
  hub_say "[10/11] No external remote, not added to the mirror-backup list"
fi

# --- 11. register in agent-hub's project registry projects/<name>.yaml (always, origin or not) ---
manifest_origin="${origin_url:-(native)}"
# ONBOARD_REGISTRY_EXTRA (JSON): fields the caller wants registered too (create-idea-sandbox.sh's type: idea etc.),
# in the same commit as the registration, so there's no window of "registered but the fields not filled in yet"
"$AGENT_HUB/bin/py" "$AGENT_HUB/bin/registry.py" add "$PROJ_NAME" "$PROJ_PATH" "$manifest_origin" \
    "$GO_REMOTE/${PROJ_NAME}-memory.git" ${ONBOARD_REGISTRY_EXTRA:+--extra "$ONBOARD_REGISTRY_EXTRA"} \
  || fail "$(hub_msg "adding to the project registry failed (projects/{name}.yaml)" name="$PROJ_NAME")"
# Commit+push agent-hub right after registering: it used to only write the file without committing, so the
# librarian/mirror-backup on the hub machine only saw a new project once someone happened to commit by hand (a
# project once got stuck locally that way). Only these three files are committed; nothing else uncommitted in
# agent-hub is touched.
"$AGENT_HUB/bin/gen-index-roster.sh"
if "$AGENT_HUB/bin/commit-hub-files.sh" "register $PROJ_NAME (onboard-project.sh)" \
    "projects/$PROJ_NAME.yaml" mirror-list.txt index.md; then
  hub_say "[11/11] Registered in brunna's project registry (projects/{name}.yaml); brunna committed and pushed" name="$PROJ_NAME"
else
  hub_say "Warning: committing/pushing brunna's registry files failed; do it by hand (the registration itself is written)" >&2
  hub_say "[11/11] Registered in brunna's project registry (projects/{name}.yaml); committing/pushing brunna failed (see the warning)" name="$PROJ_NAME"
fi

echo
hub_say "Done: {name} is connected to the brunna pipeline." name="$PROJ_NAME"
