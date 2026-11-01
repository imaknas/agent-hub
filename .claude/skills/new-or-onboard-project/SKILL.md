---
name: new-or-onboard-project
description: Create a brand-new project or bring an existing folder into the agent-hub pipeline, end to end. Use whenever the user asks to "start/create a new project" or "onboard/connect this folder/project" — handles location, scaffolding, and visibility decisions, then delegates everything mechanical to one command.
---

# New or Onboard Project

Two commands do the mechanical work, so this skill covers only the decisions that need judgment. Don't redo by hand
what they already do (no `git init` first, no `gh repo create`):

- `agenthub connect <folder>`: existing folders. It works out the layout (one folder, one repo, a monorepo, or a folder
  holding several repos), says what connecting does, asks per layout, and calls `onboard-project.sh` for each project.
- `agenthub onboard <path>` (= `bin/onboard-project.sh`): one project, new or existing. It does the git init, the first
  commit and the GitHub repo.

## Decision 1: a brand-new project, or existing folders?

- **Brand-new project** (the user says "create a new project for me"): the directory usually doesn't exist yet or is
  empty. Go to decisions 2, 3 and 4, then run `agenthub onboard`; it creates the directory itself.
- **Existing folders** (the user says "connect this" or points at a path that already has things in it): skip decision 3,
  look at decision 4, then run `agenthub connect <folder>`. A repo with unsaved changes is skipped and the reason
  printed: **don't clean it up for the user and re-run**. Pass the message on unchanged and let them decide whether to
  commit or discard those changes. This protects their work that isn't ready yet.

## Decision 2: which path does the project go in?

- **Default** for a new project: under the workspace root (`hub.json` `workspace_root`),
  `<workspace root>/<name>`, whatever the language and whether or not it is a code project.
- Never inside the memory copies or the services root (`hub.json` `memory_copies`, `services_root`): those are the
  platform's.
- If the user names a path themselves, what they say goes; don't override it. Existing folders stay where they are.

## Decision 3: does a brand-new project need language scaffolding?

If the user named a language or framework ("create a Rust project", "use Python"), first run the matching init command
in the directory (`cargo init`, `uv init`, `npm init` etc.), **then** onboard it: the first commit is then the real
scaffolding, not just a placeholder README.md.

If the user named no language and just wants a place to keep things (research notes and the like), no scaffolding: the
placeholder README.md is enough.

## Decision 4: should the GitHub repo be public or private?

**Private by default**: that is also the command's default, no flag needed.

Add `--public` only when the user explicitly says this project is meant to be public (a portfolio, open source). If
unsure, ask; don't guess.

`--no-remote` (no GitHub repo) only when the user says the project doesn't need one for now, or `hub.json` has no
`github.owner`. Tell them the trade-off: without a remote there is no copy beyond this machine (the platform's
encrypted backup, if on, still covers it). `agenthub connect` uses `--no-remote` unless given `--remote`.

## Run

```bash
agenthub connect <folder> [--remote]                 # existing folders
agenthub onboard [--public] [--no-remote] <path>     # one project
```

Both leave nothing half-done when something fails, and carry on when run again after an interruption. When it
finishes, give the user its output as is (including the GitHub URL, and whether `--public` was used); then tell them
to start a new session in the project's folder, since the project's setup loads when a session starts.
