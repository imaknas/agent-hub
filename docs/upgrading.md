# Updating brunna

```bash
brunna upgrade --check        # what would change: exit 0 up to date, 10 an upgrade is there
brunna upgrade                # the newest release: shows the changelog, asks, moves the checkout, brings everything in step
brunna upgrade --to v0.2.0    # a given release; going back is the same command with the older tag
brunna upgrade --main         # follow main (development) instead of releases
```

`brunna upgrade` first shows the changelog between your release and the target, breaking changes first, and asks
(`--yes` skips the question). It then moves the checkout (a detached checkout of the tag, or a fast-forward of main)
and runs the new version's own commands, in order: `bin/setup-python` (a no-op when the pinned packages are unchanged),
`registry.py check`, `brunna config apply` (services, scheduled jobs, project settings), the scheduler and watchdog
units, `brunna land` (the hub's git hooks), `brunna install-cli` (when your `brunna` is its wrapper) and last
`brunna doctor`. If a step fails, the rest don't run; it says which step and how to go back
(`brunna upgrade --to <previous tag>`, which runs the same steps on the old code). If you moved the checkout yourself
(`git pull`, `git checkout`), `brunna upgrade` sees that the steps have not run for it and runs them.

Which checkouts it moves: one at a release tag follows releases; one on `main` follows `main` (`--to <tag>` switches it to
releases). Anything else is treated as a development checkout and refused, never moved: another branch, commits that
are not on the remote, uncommitted changes to tracked files, or a linked worktree. Update those with git.

`brunna doctor` says when a newer release is out. It asks the remote at most once a day (`git ls-remote`, 5 seconds at
most, no prompts) and otherwise reads what the last check recorded.

Your data is in the instance directory, not in the repo, so moving the code does not touch it. Data formats are
versioned contracts, and newer code reads the older versions. Going back across a contract version is different: the
older code may not read data the newer version wrote. The changelog marks such changes as breaking.

It refuses while a scheduler tick is running, and holds the scheduler's lock while it moves the checkout, so no tick
starts halfway. A long background job that started earlier (the nightly librarian, say) still finishes on the code it
had loaded, so upgrade outside the nightly jobs' hours.

v0.1.0 has no `upgrade` command. From it, move the checkout yourself once
(`git fetch --tags && git checkout --detach v0.2.0` in the hub folder), then run `brunna upgrade`.

## From agent-hub

brunna was called agent-hub until v0.7. Upgrading an agent-hub install renames what can be renamed safely and leaves
the rest where it is:

- **Renamed:** the command (`brunna`; `agenthub` keeps working, both run the same code), the scheduler, watchdog and
  service units (`local.brunna-…`), the platform's project and job names (`brunna`, `brunna/<job>`; each job's state
  carries over, so nothing runs twice), the block in each project's rules file (at that project's next session), and
  the messages.
- **Kept:** the checkout folder (`~/agent-hub`), the instance folder (`~/.agent-hub`), the settings file (`hub.json`),
  the event bus folder, the platform's Python and each existing project's `.git/agent-hub/`. Running processes use
  these, so nothing moves under them; the platform reads both spellings. Messages name your settings file as it is.
- **Your assistant:** items it already holds under the project `agent-hub` stay filed there; new ones come from
  `brunna`. Its party ids are `brunna:registrar` and `brunna:owner-side` from now on.
- **MCP:** a registration under `agenthub` keeps working through the old command. `brunna init --register-mcp`
  registers the new name.

Going back is to v0.6.0 or later (`brunna upgrade --to v0.6.0`): v0.6 reads everything v0.7 writes. Older releases
can't, so `upgrade` refuses them.

