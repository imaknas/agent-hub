# Everyday commands

```bash
agenthub status [project]       # project state, services, STATE.md
agenthub config plan            # what a config change would do; `agenthub config apply` applies it
agenthub modules                # optional features; `agenthub modules enable <name>`
agenthub llm setup              # which provider/model the platform's judgment calls use
agenthub jobs                   # all scheduled jobs; `agenthub run <job>` runs one now
agenthub recover <project>      # new machine or lost folder (never re-onboard an onboarded project)
agenthub rename <old> <new>     # dry-run first, then --apply
agenthub --version              # the release this checkout is at
agenthub upgrade --check        # is there a newer release, and what changes; `agenthub upgrade` installs it
```

Inside a project session, agents use `agenthub send / inbox / done …` for the mailbox, `agenthub test` to run the
project's declared tests, and `agenthub merge-branch` to land their branch. The rules they follow are written into each project's instructions file from `templates/`.
