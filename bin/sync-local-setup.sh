#!/usr/bin/env bash
# Align an onboarded project's "local-only" setup with agent-hub's current version. Idempotent; never changes or
# commits any tracked file in the project repo:
#
#   1. In each installed runtime's rules file (`hub-runtime instructions-files`; for Claude Code, CLAUDE.local.md), the
#      block
#      between <!-- agent-hub:begin/end --> is regenerated whole from templates/agent-instructions.md plus the
#      templates/rules/*.md listed in .agent-memory/RULES (prompts.agent_rules: the names, the content language and
#      the to-do tokens filled in from hub.json): change the template and every project catches up at its next
#      session, without anyone having to remember to patch it.
#      Content outside the block is untouched. An old-style pointer section, appended directly without markers, is
#      replaced with the marked block.
#   2. hooks (installed by the runtime: `hub-event install`; the platform's are replaced, the user's own are kept)
#   2b. .agent-memory/AGENT.md aligned with templates/AGENT.md: this is the exception: it's on the memory branch and
#      gets committed+pushed (only AGENT.md is committed); the project repo is untouched
#   3. .agent-memory/ and the runtime's local-only files (`hub-runtime local-ignores`), if not ignored, are written
#      into .git/info/exclude: a local-only ignore; the committed .gitignore, shared by the project, is not changed.
#
#   4. If committed files contain this machine's paths (~/agent-hub/…, /Users/<user>/…), print a reminder (also
#      with --quiet). Since 10-27 agent-hub and the assistant are both going open source, so merely naming them isn't a
#      leak; paths and personal data are
#
# Called automatically by the project's SessionStart hook (--quiet), and by onboard-project.sh and
# recover-project.sh. The SessionStart hook's stdout goes into the session context, so with --quiet only item 4's
# reminder is printed (when there's a violation), nothing else.
#
# Usage: sync-local-setup.sh [--quiet] [project path, default the current directory]
set -uo pipefail

AGENT_HUB="${AGENT_HUB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
source "$(dirname "${BASH_SOURCE[0]}")/hubpaths.sh"
QUIET=false
[ "${1:-}" = "--quiet" ] && { QUIET=true; shift; }
# Progress lines print only without --quiet ($QUIET || …): the SessionStart hook's stdout goes into the session, and the
# quiet path must not spawn anything. Warnings meant for that session print either way.

cd "${1:-.}" || exit 1
[ -d .agent-memory ] || {
  $QUIET || hub_say "sync-local-setup: {dir} has no .agent-memory, not an onboarded project; skipping" dir="$(pwd)"; exit 0; }

# --- 0. this folder has moved to the hub machine (the marker migrate-to-mini.py leaves) ---
# Printed with --quiet too: SessionStart's stdout goes into the context, so the reminder comes the moment a session
# opens in the old copy on the laptop, without relying on anyone remembering. With sessions open on both sides, the
# same day's memory note pushes would conflict.
# The common git dir is read from the .git pointer files (bin/gitdirs.py), not asked of git.
moved=$("$AGENT_HUB/bin/py" "$AGENT_HUB/bin/gitdirs.py" "$PWD" 2>/dev/null | cut -f3)
moved=${moved:+$moved/agent-hub-migrated-to-mini}
if [ -n "$moved" ] && [ -f "$moved" ]; then
  hub_say "⚠️ This project has moved to the hub machine ({target}, moved from the laptop on {date}). This is the old copy kept on the laptop as a backup;
do not work here: ask the user to close this session and open the folder on the hub machine over SSH ({server}) from their coding agent instead." \
    target="$(cut -f1 "$moved")" date="$(cut -f2 "$moved")" server="$(hub_server)"
  exit 0
fi

# --- 0b. the memory remote's name: older checkouts called it mini (the hub machine's nickname) ---
# Renamed only when it is unmistakably the memory bare repo (URL ends in -memory.git) and the new name is free; git
# moves the agent-memory branch's upstream with it.
old_url=$(git remote get-url mini 2>/dev/null) || old_url=""
if [ -n "$old_url" ] && [ "${old_url%-memory.git}" != "$old_url" ] && ! git remote get-url "$MEMORY_REMOTE" >/dev/null 2>&1; then
  git remote rename mini "$MEMORY_REMOTE" \
    && { $QUIET || hub_say "sync-local-setup: the memory remote is now called {name} (it was mini)" name="$MEMORY_REMOTE"; }
fi

# --- 1. the rules file's managed block ---
# Which file it goes to is the runtime's call (runtime_ports' instructions; for Claude Code, CLAUDE.local.md), one block
# for each installed runtime that has a rules file (hub.json runtime.installed; docs/design/mailbox-runtimes.md A1). If
# it can't be asked, stop; don't guess a default file name: that would hard-code Claude back in.
# Exit code 3 = no installed runtime has a rules file (e.g. runtime.default is none): skipped explicitly, the other
# steps run as usual.
INSTRS=$("$AGENT_HUB/bin/py" "$AGENT_HUB/bin/hub-runtime" instructions-files "$PWD" 2>/dev/null); rc=$?
if [ "$rc" = 3 ]; then
  $QUIET || hub_say "sync-local-setup: this runtime has no rules file (hub.json runtime.default); the managed block is not written"
elif [ "$rc" != 0 ] || [ -z "$INSTRS" ]; then
  hub_say "sync-local-setup: hub-runtime could not say where the rules file is; the managed block was not updated" >&2; exit 1
else
while IFS= read -r INSTR; do
# If the project lists extra rules in .agent-memory/RULES on the memory branch (one name per line, e.g. engineering),
# append templates/rules/<name>.md after the block. The list lives on the memory branch: it survives a recover on a
# new machine; the rule content lives in agent-hub: change it once and every project that lists it catches up.
# stdout: "<name>\t<path>" for each listed rule whose file is missing (reported below, in the UI language). A temp file,
# not $(…): bash 3.2 misparses a heredoc inside a command substitution
missing=$(mktemp) || exit 1
"$AGENT_HUB/bin/py" - "$AGENT_HUB/bin" "$INSTR" .agent-memory/RULES <<'PY' >"$missing"
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import hubconf
import names
import prompts

path, rules_file = Path(sys.argv[2]), Path(sys.argv[3])
listed = rules_file.read_text().split() if rules_file.exists() else []
# rules decided by the registry (not via RULES): commits: conventional → the merge gate checks titles, so the rules
# file must carry the explanation
here = Path.cwd().resolve()
proj = next((d for n, d in hubconf.projects().items()
             if (ws := hubconf.workspace(n)) and ws.resolve() == here), {})
if proj.get("commits") == "conventional" and "conventional-commits" not in listed:
    listed.append("conventional-commits")
body, absent = prompts.agent_rules(listed)   # the template minus its title, the listed rules, filled
for name in absent:
    print(f"{name}\t{hubconf.ROOT / 'templates' / 'rules' / f'{name}.md'}")
old = path.read_text() if path.exists() else ""
# the block in either spelling becomes the current one (docs/design/rename-brunna.md); none yet: it goes after what the
# file has
new = names.put_block(old, body)

if new != old:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(new)
    tmp.replace(path)
PY
rc=$?
while IFS=$'\t' read -r name rule; do
  hub_say "sync-local-setup: RULES lists {name}, but {rule} is not there" name="$name" rule="$rule" >&2
done <"$missing"
rm -f "$missing"
[ "$rc" = 0 ] && { $QUIET || hub_say "sync-local-setup: {file} aligned" file="$INSTR"; }
done <<<"$INSTRS"
INSTR=${INSTRS%%$'\n'*}   # the first rules file: the reminder in step 4 names it
fi

# --- 2. hook ---
# installed by the runtime (runtime_ports' lifecycle): every platform hook becomes `hub-event <event>`, the user's own
# are kept
# (run as a sandboxed project, HUB_AS_PROJECT: its settings file is the platform's to write, render-project-settings
# puts the platform's hooks there; the project's own process can't)
if [ -n "${HUB_AS_PROJECT:-}" ]; then rc=4; else
"$AGENT_HUB/bin/py" "$AGENT_HUB/bin/hub-event" install "$PWD" 2>/dev/null; rc=$?
fi
[ "$rc" = 0 ] && { $QUIET || hub_say "sync-local-setup: hooks aligned"; }
[ "$rc" = 3 ] && { $QUIET || hub_say "sync-local-setup: this runtime has no session events (hooks), so none were installed"; }

# --- 2b. .agent-memory/AGENT.md aligned with templates/AGENT.md ---
# AGENT.md is on the memory branch (committed, synced to the hub machine), not a local file; git-sync-memory.sh only
# picks up memory/ and STATE.md, so this commits+pushes by itself, and commits only the one file AGENT.md. It runs in
# parallel with SessionStart's git pull, using the same with-lock lock so they don't collide.
# It compares against the version in HEAD, not the working tree: if last time it copied but the commit/push failed,
# the next run tries again. With AGENT_HUB_OFFLINE (a sandboxed project's hooks run this as the project, and its sandbox
# can't write the bare repo) it only commits; the memory sync's bundle carries it.
if ! git -C .agent-memory show HEAD:AGENT.md 2>/dev/null | cmp -s "$AGENT_HUB/templates/AGENT.md" -; then
  # don't run the project's own git hooks (see the explanation in git-sync-memory.sh)
  ( cd .agent-memory \
    && export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null \
    && cp "$AGENT_HUB/templates/AGENT.md" AGENT.md \
    && "$AGENT_HUB/bin/with-lock.sh" sh -c \
         'git commit -q -m "agent-hub: sync AGENT.md from template" -- AGENT.md \
          && { [ -n "${AGENT_HUB_OFFLINE:-}" ] || "$0/bin/git-safe-push.sh"; }' "$AGENT_HUB" ) >/dev/null 2>&1 \
    && { $QUIET || hub_say "sync-local-setup: AGENT.md aligned with the template and pushed"; } \
    || { $QUIET || hub_say "sync-local-setup: ⚠️ AGENT.md updated, but the commit/push failed; the next session start tries again"; }
fi

# --- 3. local files must be ignored ---
exclude=$(git rev-parse --git-path info/exclude 2>/dev/null) || exit 0
# .agent-memory is the platform's own; the rest are the runtime's local-only files (rules file, local settings), as
# it reports them
for f in .agent-memory $("$AGENT_HUB/bin/py" "$AGENT_HUB/bin/hub-runtime" local-ignores 2>/dev/null); do
  if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    # already committed into the repo: ignoring can't fix that; that project's own session has to handle it
    $QUIET || hub_say "sync-local-setup: ⚠️ {file} is tracked by this repo (it was committed), so ignoring it does nothing; the project's own session has to handle it" file="$f"
  else
    # always write it into exclude, whether or not .gitignore has it: that line in .gitignore will be removed later
    # (it leaks the name itself), so it can't be relied on.
    [ "$f" = .agent-memory ] && pat=".agent-memory/" || pat="$f"
    if ! grep -qxF "$pat" "$exclude" 2>/dev/null; then
      mkdir -p "$(dirname "$exclude")"
      printf '%s\n' "$pat" >> "$exclude"
      $QUIET || hub_say "sync-local-setup: {pattern} added to {exclude}" pattern="$pat" exclude="$exclude"
    fi
  fi
done
# --- 3b/3c. the memory-behind reminder and the project mailbox: no longer here ---
# At SessionStart, `hub-event session_start` calls them in one place (it runs this script as "setup only"; doing it
# in both would deliver twice).

# --- 4. committed files must not contain this machine's paths ---
# Whether a repo goes public later is undecided, so it's never allowed: remind whenever a local path of agent-hub, the
# instance or the workspace appears in committed content (naming agent-hub is fine since 10-27). This part deliberately prints with --quiet too: the SessionStart
# hook's stdout goes into the session context, so every session start hands the list of things to fix to the
# project's own session, without relying on anyone remembering to check. No violation, nothing printed.
leak_re='~/agent-hub/|/Users/[A-Za-z0-9._-]+/(agent-hub|workspace|\.agent-hub|services|git-origin)'
leaks=$(git grep -l -I -E "$leak_re" HEAD -- . 2>/dev/null | sed 's#^HEAD:##' | head -10)
if [ -n "$leaks" ]; then
  hub_say "⚠️ Files committed in this repo contain this machine's paths (the repo may become public, and local paths must not be committed; naming agent-hub is fine):
{files}
Please handle it in this project: move what is needed to operate it into {file} (outside the agent-hub:begin/end block, not version-controlled),
and in committed files write it without local paths or delete it, then commit. For details:
{command}" files="$(echo "$leaks" | sed 's/^/  - /')" file="$INSTR" command="git grep -n -E '$leak_re'"
fi
exit 0
