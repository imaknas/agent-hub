# Updating agent-hub

```bash
agenthub upgrade --check        # what would change: exit 0 up to date, 10 an upgrade is there
agenthub upgrade                # the newest release: shows the changelog, asks, moves the checkout, brings everything in step
agenthub upgrade --to v0.2.0    # a given release; going back is the same command with the older tag
agenthub upgrade --main         # follow main (development) instead of releases
```

`agenthub upgrade` first shows the changelog between your release and the target, breaking changes first, and asks
(`--yes` skips the question). It then moves the checkout (a detached checkout of the tag, or a fast-forward of main)
and runs the new version's own commands, in order: `bin/setup-python` (a no-op when the pinned packages are unchanged),
`registry.py check`, `agenthub config apply` (services, scheduled jobs, project settings), the scheduler and watchdog
units, `agenthub land` (the hub's git hooks), `agenthub install-cli` (when your `agenthub` is its wrapper) and last
`agenthub doctor`. If a step fails, the rest don't run; it says which step and how to go back
(`agenthub upgrade --to <previous tag>`, which runs the same steps on the old code). If you moved the checkout yourself
(`git pull`, `git checkout`), `agenthub upgrade` sees that the steps have not run for it and runs them.

Which checkouts it moves: one at a release tag follows releases; one on `main` follows `main` (`--to <tag>` switches it to
releases). Anything else is treated as a development checkout and refused, never moved: another branch, commits that
are not on the remote, uncommitted changes to tracked files, or a linked worktree. Update those with git.

`agenthub doctor` says when a newer release is out. It asks the remote at most once a day (`git ls-remote`, 5 seconds at
most, no prompts) and otherwise reads what the last check recorded.

Your data is in the instance directory, not in the repo, so moving the code does not touch it. Data formats are
versioned contracts, and newer code reads the older versions. Going back across a contract version is different: the
older code may not read data the newer version wrote. The changelog marks such changes as breaking.

It refuses while a scheduler tick is running, and holds the scheduler's lock while it moves the checkout, so no tick
starts halfway. A long background job that started earlier (the nightly librarian, say) still finishes on the code it
had loaded, so upgrade outside the nightly jobs' hours.

v0.1.0 has no `upgrade` command. From it, move the checkout yourself once
(`git fetch --tags && git checkout --detach v0.2.0` in the hub folder), then run `agenthub upgrade`.
