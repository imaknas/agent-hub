# agent-hub

A personal, cross-project platform for coding agents. Each project gets its own memory: notes the agent writes, plus a
curated `STATE.md`. Every night the platform consolidates that memory, writes a cross-project digest and to-do list, and
runs your scheduled jobs and background services. Projects (and you) talk to each other through a mailbox.

It is **tool-agnostic**: the platform defines its own interfaces, and Claude Code is one implementation of them, not a
requirement. Any CLI agent with a headless mode can be plugged in through a command template (tested with Codex and
Antigravity), and the platform's own judgment calls can go to Anthropic, any OpenAI-compatible API, a local Ollama, or the
coding agent's own login. When a capability is missing, it is switched off explicitly and `agenthub doctor` says why.

> **Status.** Built for, and run every day by, one person on one always-on Mac mini. It is single-user by design. The code
> comments and design docs are written in Traditional Chinese; this README and the CLI's commands are the English entry
> points. Agents working in this repo read [`AGENTS.md`](AGENTS.md).

## What you get

- **Project memory.** Each project has a `.agent-memory/` worktree on its own branch, backed by a local bare repo, so
  memory sync never touches your real code. Session hooks commit only `memory/` and `STATE.md`.
- **Nightly librarian.** It reads new notes, updates each `STATE.md` (including a fixed-format to-do section), and writes a
  cross-project `digest/<date>.md` and `todo.md`. Older notes are folded into reference files (consolidate module).
- **Scheduler and services.** A 15-minute scheduler for every job, a watchdog that alerts when the scheduler stops, and
  per-project background services declared in the registry and rendered to launchd or systemd units.
- **Mailbox.** Letters between projects and to the owner; the recipient is a project, not a session. Unclaimed requests are
  dispatched by the platform: to an idle session's context, or to a headless agent in a throwaway worktree. The mailbox is
  also an MCP server (`agenthub mcp`) for agents without hook support.
- **Merge gate for parallel sessions.** `agenthub merge-branch` rebases a session's branch, runs the project's tests,
  optionally checks Conventional Commits titles, and fast-forwards `main`.
- **Status page.** Failed jobs, reports that need attention, and upcoming runs, served on `127.0.0.1:18790`.
- **Optional modules:** encrypted cloud backup (restic), GitHub mirrors, weekly activity reflection, memory consolidation,
  a sandbox capability runner, Claude Code upkeep (update and login checks), a built-in owner inbox, and an external
  personal-assistant bridge.

## Requirements

- macOS (launchd) or Linux (systemd user units). The capability runner, which runs sandboxed helper programs, uses
  Seatbelt, so it is macOS only.
- `git`, `jq` and [`uv`](https://docs.astral.sh/uv/). `uv` builds the platform's own Python 3.13 venv, so platform scripts
  don't depend on whichever `python3` is on PATH.
- Optional:
  - a coding agent, such as [Claude Code](https://claude.com/claude-code) or any headless CLI agent. Without one, set
    `runtime.default: none`; agent features are then off, and everything else works.
  - `gh`, to create GitHub repos on onboarding;
  - `restic`, for the backup module;
  - an alert channel: Discord, ntfy, email or macOS notifications.

## Quick start

```bash
git clone <this repo URL> ~/agent-hub && cd ~/agent-hub
bin/setup                            # check required tools, build the platform Python, put `agenthub` on PATH
agenthub init                        # owner name, timezone, workspace, modules, which LLM makes judgment calls
agenthub doctor                      # everything that is missing or broken, each with how to fix it
agenthub onboard ~/workspace/my-app  # connect a project (--no-remote: don't create a GitHub repo)
```

The next morning there is a digest and a `todo.md` in the instance's data directory.

## Where things live

The repo holds no user data. `agenthub init` creates an **instance directory**, `~/.agent-hub` by default, and
`<repo>/.hub-home` points to it:

| | What | Notes |
|---|---|---|
| `config/` | `hub.json`, the project registry (`projects/<name>.yaml`), `schedule.yaml` | A local git repo. `agenthub config check / plan / apply` |
| `data/` | digests, `todo.md`, review log, weekly reflections | Generated; not in git |
| `secrets/` | API keys, tokens, backup password | Never printed; the registry records names and locations only |

Projects live wherever you choose, `~/workspace/<name>` by default. Their memory bare repos and the librarian's read-only
copies sit beside them; the paths are set in `hub.json`.

## Everyday commands

```bash
agenthub status [project]       # project state, services, STATE.md
agenthub config plan            # what a config change would do; `agenthub config apply` applies it
agenthub modules                # optional features; `agenthub modules enable <name>`
agenthub llm setup              # which provider/model the platform's judgment calls use
agenthub jobs                   # all scheduled jobs; `agenthub run <job>` runs one now
agenthub recover <project>      # new machine or lost folder (never re-onboard an onboarded project)
agenthub rename <old> <new>     # dry-run first, then --apply
```

Inside a project session, agents use `agenthub send / inbox / done …` for the mailbox and `agenthub merge-branch` to land
their branch. The rules they follow are written into each project's instructions file from `templates/`.

## Docs

- [`AGENTS.md`](AGENTS.md): the index for anyone, human or agent, working on the platform
- [`docs/design/`](docs/design): design notes on tool-agnosticism, runtimes, LLM providers, mailbox routing, capabilities,
  idea sandboxes and new-user setup
- [`docs/mcp.md`](docs/mcp.md): registering the mailbox MCP server with an agent
- [`contracts/`](contracts): versioned interfaces (JSON Schema plus examples) shared with other repos
- [`known-issues.md`](known-issues.md): pitfalls hit so far, and the operating principles for automation

## Development

```bash
bin/py -m unittest discover -s tests   # all tests, with the platform Python
bin/lint                               # ruff, pyflakes rules only
bin/linux-test                         # the test suite in a Linux container (Docker)
```

The commit gate (`git config core.hooksPath githooks`, which `agenthub init` sets) lints the staged tree, then runs every
test with a throwaway `HOME`. Commit titles follow [Conventional Commits](https://www.conventionalcommits.org/), and release
notes are generated from them with [git-cliff](https://git-cliff.org/) (`cliff.toml`).

## License

[Apache-2.0](LICENSE). This covers the contracts in `contracts/`, including copies vendored into other repos. There is no
NOTICE file.
