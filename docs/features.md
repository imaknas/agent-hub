# What brunna does, in detail

- **Project memory.** Each project has a `.agent-memory/` worktree on its own branch, backed by a local bare repo, so
  memory sync never touches your real code. Session hooks commit only `memory/` and `STATE.md`.
- **Nightly librarian.** It reads new notes, updates each `STATE.md` (including a fixed-format to-do section), and writes a
  cross-project `digest/<date>.md` and `todo.md`. Older notes are folded into reference files (consolidate module).
  Every night's `STATE.md` is a commit: `brunna state restore` brings back an earlier day's when a night got it
  wrong, your to-do list kept as it is today ([`docs/commands.md`](commands.md)).
- **Scheduler and services.** A 15-minute scheduler for every job, a watchdog that alerts when the scheduler stops, and
  per-project background services declared in the registry and rendered to launchd or systemd units.
- **Mailbox.** Letters between projects and to the owner; the recipient is a project, not a session. Unclaimed requests are
  dispatched by the platform: to an idle session's context, or to a headless agent in a throwaway worktree. The mailbox is
  also an MCP server (`brunna mcp`) for agents without hook support; while such an agent works in a project, the
  platform holds requests to that project instead of dispatching another agent (up to two hours, [`docs/mcp.md`](mcp.md)).
- **The owner's to-dos, by an open protocol.** What only you can do (a `STATE.md` to-do line tagged with your name) is
  registered with your personal assistant or the built-in owner inbox and kept in step both ways, following
  [Principal Handoff](https://doi.org/10.5281/zenodo.23852711), a published spec with conformance tests.
- **Sandboxed projects.** A project with `enforce.sandbox` gets a policy computed by the platform: its agents, services and
  jobs can write only its own folders, can't read other projects, credentials or the Keychain, listen only on a
  declared port, and mail only as themselves (Seatbelt on macOS, Landlock and seccomp on Linux; an OS without a working
  adapter doesn't run them at all). Programs the sandbox blocks, such as a GPU renderer, run through the platform
  (`brunna cap-run`).
- **Tests and the merge gate.** Each project declares its test tiers in the registry. `brunna test` runs them on an
  exported copy of the code with fresh environments and reuses a recorded pass for the same tree; commits on a branch
  run the fast tiers, `brunna merge-branch` rebases, runs the merge tiers, optionally checks Conventional Commits
  titles, and fast-forwards `main`. Every project's tests also run nightly.
- **Event-triggered jobs.** A job can run when something happens on the bus (a letter, a merge, a failed job), with loop
  protection, a daily cap and a confined environment.
- **Model calls for projects.** A project's own code asks for a model by tier (`brunna complete`); the platform picks
  the provider, holds the keys and records the cost.
- **Code anchors in `STATE.md`.** The librarian links statements to the code they describe and checks every night that
  the code is still there, so a stale statement gets flagged instead of trusted.
- **Weekly review, from your sessions.** Every Monday, statistics of how you worked across projects (messages,
  sessions, daily rhythm, how work was handed off) and one model call on those numbers, never on the conversations.
  The numbers come from the sessions on the machine, whichever agent made them: Claude Code's and Codex's session files
  are read where they are, and any other tool or script can drop files in the neutral format
  ([`contracts/session-record.v1.schema.json`](../contracts/session-record.v1.schema.json)) into
  `<data>/sessions-import/`. The page's footer names the sources it read; the memory-gap check and the waiting report
  use the same sources, and `brunna doctor` says which it found.
- **Status page.** Failed jobs, reports that need attention, and upcoming runs, served on `127.0.0.1:18790`.
- **Optional modules:** encrypted cloud backup (restic), GitHub mirrors, weekly activity reflection, memory consolidation,
  a sandbox capability runner, Claude Code upkeep (update and login checks), a built-in owner inbox, an external
  personal-assistant bridge, and nightly removal of leftover test containers (testcontainers). `brunna modules` lists
  them; the reflection and consolidation modules need a model (below).
- **Any agent, any model.** The platform defines its own interfaces; Claude Code is one implementation of them, not a
  requirement. Any CLI agent with a headless mode can be plugged in through a command template (tested with Codex and
  Antigravity), any agent that speaks the [Agent Client Protocol](https://agentclientprotocol.com/) through the ACP
  adapter: neither can be held to a judgment point's limits, so they handle mailbox requests only, in the platform's
  throwaway copy, with the platform applying only the notes and letters (Codex gets an adapter of its own next,
  [docs/design/runtime-codex.md](design/runtime-codex.md)). The platform's own judgment calls can go to Anthropic, any
  OpenAI-compatible API, a local Ollama, or the coding agent's own login. When a capability is missing, it is switched off explicitly and `brunna doctor` says why.

Back to the [README](../README.md).
