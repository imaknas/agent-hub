# Everyday commands

```bash
brunna status [project]       # project state, services, STATE.md
brunna state history <project>   # every night's STATE.md; `brunna state restore <project> <date>` brings one back
brunna config plan            # what a config change would do; `brunna config apply` applies it
brunna modules                # optional features; `brunna modules enable <name>`
brunna llm setup              # which provider/model the platform's judgment calls use
brunna jobs                   # all scheduled jobs; `brunna run <job>` runs one now
brunna recover <project>      # new machine or lost folder (never re-onboard an onboarded project)
brunna rename <old> <new>     # dry-run first, then --apply
brunna --version              # the release this checkout is at
brunna upgrade --check        # is there a newer release, and what changes; `brunna upgrade` installs it
```

Inside a project session, agents use `brunna send / inbox / done …` for the mailbox, `brunna test` to run the
project's declared tests, and `brunna merge-branch` to land their branch. The rules they follow are written into each project's instructions file from `templates/`.

## When a night's STATE.md went wrong

The librarian rewrites each project's `STATE.md` every night with a model. A model can get it wrong, and the same notes
read again would not give back the version you had: every night's version is a commit on the project's memory branch,
so you go back instead.

```bash
brunna state history garden-app                  # who changed STATE.md when (the librarian, a session, a restore)
brunna state restore garden-app 2026-11-01       # the difference to how it was at the end of that day; nothing changes
brunna state restore garden-app 2026-11-01 --apply
```

Your to-do list stays exactly as it is today and everything else goes back to that day; an item you were waiting on
that was in the list that day and was deleted since, still open, comes back. Anything else restore leaves out, it
lists. The preview ends with the command to apply it, `--from` naming the version it was computed from: if `STATE.md`
changes before you run it, it refuses and you preview again.

`--apply` writes the result as a new commit (history is never rewritten) and logs it in `review-log.md`. The librarian
then folds the notes written since that day into the restored file on its next run; add `--keep-checkpoint` when those
notes are what it misread. While a nightly job is writing in the project's memory it refuses at once; try again later.
If a restore is stopped halfway (the machine slept), `brunna state restore garden-app` finishes it. Run it on the hub
machine.
