# The platform's MCP server: the project mailbox for any coding agent

`agenthub mcp` is a stdio MCP server (`bin/mcp_server.py`). Register it with the coding agent you use, and the sessions
that agent opens in a project folder can:

| Tool | What it does |
|---|---|
| `inbox` | This project's letters that are not closed yet (and marks them as seen) |
| `show` / `thread` | Read one letter, or a whole thread |
| `send` | Send a letter to another project, or reply (`reply_to`) |
| `ack` | Claim: will handle it, but not now |
| `done` | Close (a note may be attached) |
| `ask_owner` | Ask the owner something they have to decide |
| `note` | Add an entry to this project's memory note for today |

It is the same implementation as the command line `agenthub inbox|show|thread|send|ack|done` (`bin/hubbus`). On
`initialize` the server puts the platform's rules and the number of letters not yet closed into `instructions`: the
rules reach the agent without changing any file in the project.

## The platform holds a request while you use the mailbox

A request or question to a project that no session takes is handled by the platform (`hubbus dispatch`,
`docs/design/mailbox-routing.md`). So that it doesn't send another agent onto a project you are working in through
this server, the server records presence (`docs/design/mailbox-runtimes.md` A3):

- At `initialize` and at every tool call it writes a presence row for its project: session `mcp:<server pid>:<start
  time>` (stdio MCP has no session id), runtime `mcp:<clientInfo.name>`. If the server's environment carries a session
  id from the coding agent's runtime (the server inherits its client's environment), that id is used instead, in the
  platform's form (`<runtime>:<id>` unless it is `runtime.bare_ids`'s; with several runtimes in hub.json
  `runtime.installed`, the nearest ancestor process a runtime claims decides whose, `docs/design/mailbox-runtimes.md`
  A1), with that runtime in the row. A sandboxed project's server writes it through the project's outbox (operation
  `presence`).
- Dispatch counts that row as the project in use, without asking any runtime, while the server process still runs (its
  pid and start time match, and it is this platform's MCP server) and for `mailbox.mcp_presence_min` (30) minutes
  after the last tool call; a server that was only initialized counts for `mailbox.conversation_wait_min` (5) minutes.
- While it counts, a request/question to the project that no session has been given yet is **held**, at most
  `mailbox.mcp_hold_max_min` (120) minutes from the letter's time; then it is dispatched as usual. A letter `inbox` has
  shown is under the 60-minute "seen" rule instead and is never held again. The status page lists held letters as
  "held", not as unclaimed.
- So the session knows, every tool result except `inbox`'s ends with "📬 N letters are still open; look with inbox
  first" when there are open letters (a sandboxed project's count comes from its mailbox view).
- No presence is recorded for agents the platform starts (`HUB_LLM_POINT`), lease sessions (`HUBBUS_LEASE`), or a
  server whose project comes from `AGENTHUB_PROJECT` while its working directory is outside that project (one global
  server in a tool must not keep a project in use for good).
- The `mcp:` id is never a sending session: a reply goes to the project's mailbox, and no background copy is made
  from it.

## Which project

The registered project the working directory belongs to (a git worktree or `.agent-memory` counts as the main
project). If the tool does not start the MCP server inside the project folder, add `AGENTHUB_PROJECT=<project name>` to
the env in its settings. When the project cannot be recognised, the project tools return an error saying how to set it,
and the server offers two others instead:

- `status`: the connected projects and the folders you named for projects;
- `connect_project(path)`: connects a folder, the same as `agenthub connect <folder> --yes`, but only an existing
  folder strictly inside one of those folders (hub.json `connect.roots`, plus `workspace_root`), named without
  symlinks. Add a folder with `agenthub connect --add-root <folder>`. It asks you first when the tool supports MCP
  elicitation, and never creates a GitHub repo.

## Registering

`agenthub init` offers to register it for your user in the tools it finds (Claude Code, Codex), so a session in any
folder, the desktop apps' too, can reach it; it does so only on your yes at the terminal. Later:
`agenthub init --register-mcp`. Otherwise the platform does not change your tool's settings for you:
`agenthub mcp config` prints the snippet to use (with absolute paths: a tool opened from the GUI may not have your
PATH).

- **Codex CLI**: `codex mcp add agenthub -- ~/.local/bin/agenthub mcp`
- **Antigravity CLI**: `agy mcp add agenthub ~/.local/bin/agenthub mcp`
- **Claude Code**: the platform already delivers letters into the session with hooks, so there is no need to register;
  if you want to, `claude mcp add agenthub -- ~/.local/bin/agenthub mcp`
- **Other tools that support MCP** (Cursor, Gemini CLI…): put the snippet `agenthub mcp config` prints into its MCP
  settings, in that tool's format

## Known limitations

- **Agents the platform starts cannot use it** (environment variable `HUB_LLM_POINT`): that kind of work writes the
  letters it wants sent into its output, and the platform sends them.
- The MCP server is a process separate from the agent, so **the agent's own sandbox does not restrict it** (e.g. with
  Codex `--sandbox read-only`, `note` can still write to memory). It only writes this project's memory notes and the
  bus.
- stdio only; HTTP transport and `roots/list` are not done yet.
- If the client retries `send`, two letters are sent (the letter id is generated by the platform).
- Reading a letter only counts as "seen", without knowing which session (MCP carries no session id): a request/question
  with no ack, reply or done within 60 minutes is still dispatched by the platform to someone else.
- In a sandboxed project, `inbox` through this server does not mark letters seen (that project's letters are marked by
  its hooks, outside the sandbox): for an agent without those hooks only `ack`, a reply or `done` claims a letter, and a
  held one is dispatched after `mailbox.mcp_hold_max_min`. Its tool-result count follows the mailbox view, which the
  platform rewrites every minute, so it can lag a `done` by up to a minute.
- The platform only hears the session when it calls one of these tools: a session that is coding without touching
  the mailbox stops holding anything 30 minutes after its last call. An MCP server on another machine (the remote
  machine) records presence in that machine's bus, which dispatch on the hub machine never reads.
- An MCP-only session is never woken when a letter arrives (`docs/design/mailbox-runtimes.md` B1 wakes only sessions
  whose runtime has the `wake` port, through that runtime): it learns of letters from the count at its next agenthub
  tool call.
