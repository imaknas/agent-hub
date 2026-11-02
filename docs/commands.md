# Everyday commands

```bash
brunna status [project]       # project state, services, STATE.md
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
