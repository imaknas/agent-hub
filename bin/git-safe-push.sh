#!/usr/bin/env bash
# Resolve "push rejected because the remote is ahead" automatically by retrying with a pull (merge).
#
# Background: repos like agent-hub get written from two sides at once -- the laptop (scripts edited by hand) and the
# hub machine (librarian/hooks writing automatically) -- so a plain `git push` is often rejected because the other
# side happened to push first, and each time a person had to pull+merge before pushing again. This script handles the
# common case of "divergent history but no real content conflict" automatically.
#
# A real content conflict (both sides changed the same part of the same file, and merge cannot combine them) is never
# settled by guessing which side to keep with some strategy -- it prints the error and exits non-zero; that case is
# meant for a person to look at.
#
# Usage: call it after cd-ing into the target repo, e.g. git-safe-push.sh or git-safe-push.sh origin main.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/hubpaths.sh"

max_attempts=5
attempt=1

while [ $attempt -le $max_attempts ]; do
  push_err=$(git push -q "$@" 2>&1) && exit 0

  if echo "$push_err" | grep -qE "rule violations|GH0[0-9]+|required_signatures|protected branch"; then
    # blocked by a branch protection rule (e.g. PRs required, signatures required), not by divergence;
    # retrying with a pull does not help and only wastes time.
    hub_say "git-safe-push: blocked by a branch protection rule, not by diverged history; retrying will not help:" >&2
    echo "$push_err" >&2
    exit 1
  fi

  if ! echo "$push_err" | grep -qE "rejected|fetch first|non-fast-forward"; then
    # not caused by divergence (e.g. network down, authentication failed); retrying is pointless.
    echo "$push_err" >&2
    exit 1
  fi

  if ! pull_err=$(git pull --no-rebase -q 2>&1); then
    hub_say "git-safe-push: the pull could not merge, possibly a real content conflict; a person has to look:" >&2
    echo "$pull_err" >&2
    exit 1
  fi

  attempt=$((attempt + 1))
done

hub_say "git-safe-push: the push still fails after {n} attempts" n="$max_attempts" >&2
exit 1
