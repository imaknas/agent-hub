---
name: new-or-onboard-project
description: Create a brand-new project or bring an existing folder into the agent-hub pipeline, end to end. Use whenever the user asks to "start/create a new project" or "onboard this folder/project" — handles location, scaffolding, and visibility decisions, then delegates everything mechanical to onboard-project.sh in one call.
---

# New or Onboard Project

`bin/onboard-project.sh` now handles git init, the first commit and creating the GitHub repo automatically: one call does
the whole set. This skill only covers the parts it cannot do, the ones that need judgment. Don't redo by hand what is
already mechanized (no need to `git init` first or run `gh repo create` by hand: the script does it).

## Decision 1: a brand-new project, or onboarding an existing folder?

- **Brand-new project** (the user says "create a new project for me"): the directory usually doesn't exist yet or is
  empty. Go to decisions 2, 3 and 4, and once they are made call the script directly; the script creates the directory
  itself.
- **Onboarding an existing folder** (the user says "onboard this" or points at a path that already has code): skip
  decision 3 (language scaffolding: things are already there), look at decision 4, then call the script. If that folder
  is already a git repo with a dirty working tree, the script fails right away and prints why: **don't try to clean it
  up for the user and re-run**; pass the message on to the user unchanged and let them decide whether to commit or
  discard those changes. This is a deliberate safety mechanism protecting the user's real work that isn't ready yet.

## Decision 2: which path does the project go in?

Since 2026-10-15 every project lives in **the hub machine's `~/workspace/<name>`** (the laptop is only a remote, using
Claude Desktop to open sessions on the hub machine over SSH). This skill and `onboard-project.sh` both run on the hub
machine:

- **Default**: `~/workspace/<name>`, whatever the language and whether or not it is a code project.
- Not in `~/.memory-copies/` (the librarian's memory copies) or `~/services/` (deployed copies of production services).
- If the user names a path themselves, what the user says goes; don't override it, but remind them: outside
  `~/workspace/`, the `sync-all-memory.sh` scan and some conventions (folder name = project name) won't line up.

## Decision 3: does a brand-new project need language scaffolding?

If the user named a language/framework ("create a Rust project for me", "use Python"), first run the matching init
command in the directory (`cargo init`, `uv init`, `npm init` etc.) to produce real initial files, **then** call
`onboard-project.sh`: that way the first commit the script makes automatically is the real scaffolding, not just a blank
README.md placeholder.

If the user named no language and just wants a place to keep things (a notes-style project like research notes), no
scaffolding: the README.md placeholder the script adds itself is enough.

## Decision 4: should the GitHub repo be public or private?

**Always private by default**: that is also the script's own default, no flag needed.

Add `--public` only when the user explicitly says this is a portfolio/open-source project meant to be public. If
unsure, ask the user; don't guess "this looks fine to make public".

Only if the user explicitly says the project doesn't need GitHub for now (rare: today every native project was turned
into a hosted one, because native = no backup beyond the local machine) add `--no-remote`, and confirm with the user
that they know the trade-off.

## Run

With decisions 2-4 made, one command does the whole set:

```bash
~/agent-hub/bin/onboard-project.sh [--public] [--no-remote] <path>
```

The script itself handles the hub machine being unreachable (it does nothing at all, leaving no half-done state) and
resuming after an interruption: neither needs the skill to step in, and the script prints clear messages itself. When
the script finishes, paste its output (including the GitHub URL and whether --public was used) to the user as is; no
separate summary needed.
