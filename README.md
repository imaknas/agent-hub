# agent-hub

A personal, cross-project platform for coding agents. Each project gets its own memory: notes the agent writes, plus a
curated `STATE.md`. Every night the platform consolidates that memory, writes a cross-project digest and to-do list, and
runs your scheduled jobs and background services. Projects (and you) talk to each other through a mailbox.

It is **tool-agnostic**: the platform defines its own interfaces, and Claude Code is one implementation of them, not a
requirement. Any CLI agent with a headless mode can be plugged in through a command template (tested with Codex and
Antigravity), any agent that speaks the [Agent Client Protocol](https://agentclientprotocol.com/) through the ACP
adapter, and the platform's own judgment calls can go to Anthropic, any OpenAI-compatible API, a local Ollama, or the
coding agent's own login. When a capability is missing, it is switched off explicitly and `agenthub doctor` says why.

> **Status.** Built for, and run every day by, one person on one always-on Mac mini. It is single-user by design. An instance
> picks the language the platform shows you and the language its agents and models write in (`language.ui` and
> `language.content` in `hub.json`: `en` or `zh-Hant`). Agents working in this repo read [`AGENTS.md`](AGENTS.md).

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
- **The owner's to-dos, by an open protocol.** What only you can do (a `STATE.md` to-do line tagged with your name) is
  registered with your personal assistant or the built-in owner inbox and kept in step both ways, following
  [Principal Handoff](https://doi.org/10.5281/zenodo.23852711), a published spec with conformance tests.
- **Sandboxed projects.** A project with `enforce.sandbox` gets a policy computed by the platform: its agents, services and
  jobs can write only its own folders, can't read other projects, credentials or the Keychain, listen only on a
  declared port, and mail only as themselves (Seatbelt on macOS, Landlock and seccomp on Linux; an OS without a working
  adapter doesn't run them at all). Programs the sandbox blocks, such as a GPU renderer, run through the platform
  (`agenthub cap-run`).
- **Tests and the merge gate.** Each project declares its test tiers in the registry. `agenthub test` runs them on an
  exported copy of the code with fresh environments and reuses a recorded pass for the same tree; commits on a branch
  run the fast tiers, `agenthub merge-branch` rebases, runs the merge tiers, optionally checks Conventional Commits
  titles, and fast-forwards `main`. Every project's tests also run nightly.
- **Event-triggered jobs.** A job can run when something happens on the bus (a letter, a merge, a failed job), with loop
  protection, a daily cap and a confined environment.
- **Model calls for projects.** A project's own code asks for a model by tier (`agenthub complete`); the platform picks
  the provider, holds the keys and records the cost.
- **Code anchors in `STATE.md`.** The librarian links statements to the code they describe and checks every night that
  the code is still there, so a stale statement gets flagged instead of trusted.
- **Status page.** Failed jobs, reports that need attention, and upcoming runs, served on `127.0.0.1:18790`.
- **Optional modules:** encrypted cloud backup (restic), GitHub mirrors, weekly activity reflection, memory consolidation,
  a sandbox capability runner, Claude Code upkeep (update and login checks), a built-in owner inbox, and an external
  personal-assistant bridge.

## Requirements

- macOS (launchd) or Linux (systemd user units). Project sandboxes use Seatbelt on macOS and Landlock on Linux (a
  declared listening port needs Landlock ABI 4, kernel 6.7 or later). The capability runner, which runs programs a
  sandbox blocks, uses Seatbelt, so it is macOS only.
- `git`, `jq` and [`uv`](https://docs.astral.sh/uv/). `uv` builds the platform's own Python 3.13 venv, so platform scripts
  don't depend on whichever `python3` is on PATH.
- Optional:
  - a coding agent, such as [Claude Code](https://claude.com/claude-code) or any headless CLI agent. Without one, set
    `runtime.default: none`; agent features are then off, and everything else works.
  - `gh`, to create GitHub repos on onboarding;
  - `restic`, for the backup module;
  - an alert channel: Discord, ntfy, email or macOS notifications;
  - Docker, for `bin/linux-test`.

## Quick start

```bash
git clone <this repo URL> agent-hub && cd agent-hub   # any folder; hooks name it where it is
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
agenthub --version              # the release this checkout is at
```

Inside a project session, agents use `agenthub send / inbox / done …` for the mailbox, `agenthub test` to run the
project's declared tests, and `agenthub merge-branch` to land their branch. The rules they follow are written into each project's instructions file from `templates/`.

## Docs

- [`AGENTS.md`](AGENTS.md): the index for anyone, human or agent, working on the platform
- [`CHANGELOG.md`](CHANGELOG.md): what changed in each version
- [`docs/design/`](docs/design): design notes, one per part: tool-agnosticism, runtimes (including ACP), LLM providers,
  mailbox routing and the bus sender, sandboxes and capabilities, the test system, event-triggered jobs, code anchors,
  idea sandboxes, i18n, Principal Handoff and new-user setup
- [`docs/mcp.md`](docs/mcp.md): registering the mailbox MCP server with an agent
- [`contracts/`](contracts): versioned interfaces (JSON Schema plus examples) shared with other repos
- [`known-issues.md`](known-issues.md): pitfalls hit so far, and the operating principles for automation

## Development

```bash
bin/run-tests                          # all tests, in parallel, with the platform Python
bin/lint                               # ruff, pyflakes rules only
bin/linux-test                         # the test suite in a Linux container (Docker)
```

Changes go in through the platform's own gate: work in a git worktree and merge with `agenthub merge-branch`. The
platform installs a `pre-commit` and `commit-msg` hook in the repo's git dir (`agenthub land`, run after every merge, and
the 15-minute applier); a commit on a branch runs the `lint` tier, a merge the `lint` and `full` tiers (`bin/run-tests`:
every test in parallel under a throwaway `HOME`), through `agenthub test`. Don't set `core.hooksPath`: the platform unsets
it so its hooks apply. Commit titles follow [Conventional Commits](https://www.conventionalcommits.org/), and release
notes are generated from them with [git-cliff](https://git-cliff.org/) (`cliff.toml`).

How to contribute: [CONTRIBUTING.md](CONTRIBUTING.md). Security reports: [SECURITY.md](SECURITY.md).

## License

[Apache-2.0](LICENSE). This covers the contracts in `contracts/`, including copies vendored into other repos. There is no
NOTICE file.
