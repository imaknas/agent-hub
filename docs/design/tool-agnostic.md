# Tool-agnostic (before open source): any coding agent, any LLM, any key

2026-10-29 (second version: revised after blind review). The owner: "maximise tool agnosticism; to attract users when
open-sourced, it can't have too many restrictions."

The overview; the details of models and API keys are in `llm-providers.md` (second version), the interface layers in
`runtime-abstraction.md`, neutral permissions in `capabilities.md`. This document decides **where the line between the
platform and the tools goes**.

The five points the owner agreed on 10-29 (the first version's "to decide"): principle 1 as a hard rule, Gemini CLI as
the first non-Claude proof, leasing moves to ACP, a minimal built-in owner side, Linux in the first version. After the
blind review the owner confirmed again (10-29): principle 1 narrowed, leasing over ACP postponed, the CLI renamed
`agenthub`; and **Muninn written explicitly as an optional tool** (the owner side can be swapped, see "Not tools, but
limits all the same").

## The problem

agent-hub depends on Claude Code in two ways:

- **Using it to do work** (judgment points, mailbox delegation, leasing): can be swapped for another agent or a direct
  API call.
- **The platform's guarantees rely on its features**: write limits rely on Claude's permission rules, letters on
  Claude's hooks, notes on `CLAUDE.local.md`, statistics on Claude's conversation logs. Switch tools and these do not
  get worse, they **silently disappear**.

Only the second is an obstacle to open source.

## The core: the agent proposes, the platform applies

The first version wanted to wrap any agent in an operating-system sandbox so that "can't write means can't write". The
blind review pointed out that this cannot work: Seatbelt cannot filter the network by domain (the agent has to reach
its own model API, so once the network is open an allow list is decoration), the agent must be able to read its own
credentials, the state directory must be writable (it holds every project's conversation logs), subscription login
needs the Keychain, and **sandboxes cannot be stacked** (known-issues.md:222): Claude Code, Codex and Gemini CLI all use
Seatbelt for their own sandbox on macOS, and with another layer around them their own layer cannot be applied; on the
owner's machine Bash simply fails.

So the guarantees are made to hold **structurally**, not through a sandbox:

- **Judgments** (completion): the model returns a list of edits, the platform checks them and the platform writes the
  files (`llm-providers.md` second version).
- **Work the platform starts that touches files** (mailbox delegation, agent jobs, consolidate): any agent runs in a
  **throwaway worktree**; afterwards the platform checks `git diff`, **applies only the allowed paths**, and discards
  and records the rest. Whatever the agent does in the worktree does not matter: it cannot touch the real checkout.
- **Letters to send, things to close**: the agent says so in structured output, and **the platform sends them on its
  behalf** (the identity is the one the platform gave at start, not what the agent claims).
- **Integrity of the bus**: today `policy.write` lets agents write the bus directory and `hubbus` runs in the agent's
  own process, so an agent can write SQLite directly and impersonate a sender. It becomes a platform service + unix
  socket (identity given at start), and the bus directory is read-only to agents.

This holds on Linux just the same. The sandbox is demoted to **defence in depth** (blocking reads of other projects,
blocking exfiltration), used only where it is not nested (the platform's own cap-runner; an agent the platform starts is
wrapped only if that tool's own sandbox is off).

**The scope of principle 1** (the version to write into CLAUDE.md): **work the platform starts and the integrity of the
platform's data (bus, registry, STATE.md, notes) are guaranteed by the platform, not by any tool's features. Sessions
users open themselves (terminal, IDE, Desktop SSH) only have the tool's native protection; the platform advises and
does not guarantee**: doctor and the status page say so truthfully. The owner's own main work is interactive sessions;
that kind relies on Claude's settings today (render-project-settings), unchanged.

## Principles

1. As above: work the platform starts and the platform's data are guaranteed structurally.
2. **Use the most general interface**: files and the platform's command line > MCP > each tool's native hooks/settings
   (written only in adapters).
3. **Capabilities are derived from which interfaces an adapter implements** (the 7 interfaces of `runtime_ports`, no
   separate list); each platform feature declares which interfaces it needs; missing = the feature is explicitly off,
   with the reason in doctor's "tool × feature" table. `load()` already checks only the requested interfaces, so an
   adapter can implement just a part.
4. **New tools can be connected without writing code**, but honestly: a zero-code adapter can only do completion and
   "run once in a throwaway worktree".
5. The platform's own settings use only neutral vocabulary (ratchet, 1186f65).
6. What the platform can derive does not rely on agents reporting it.

## Where the platform touches tools

### 1. Judgments (completion)

`llm-providers.md` second version: the librarian's three points and the weekly report move to completion; any API key,
a local model, or any agent run with its tools off.

### 2. Agent work the platform starts

| Adapter | What it can do |
|---|---|
| `command` (a command template in settings) | completion; **or** run once in a throwaway worktree (prompt on stdin, output through the tool's own "last message / structured output" flags, e.g. `codex exec --output-last-message --output-schema`, without parsing an event stream that changes). On timeout the whole process group is killed; no cost reported (only tokens, if the tool gives them). **The template may not be written as "auto-allow everything" and run directly in the real checkout**: in a worktree that problem does not exist, which is why it is only allowed in worktrees |
| `acp` | the same, plus multiple turns and an event stream (the platform keeps its own neutral conversation log, no parser per tool). Resuming (`session/load`) is an optional capability |
| native (`runtime_claude`) | every capability (today's behaviour) |

Mailbox delegation (only writing notes, replying, asking the owner) only ever needed "read the project + say in a
structured way which notes to write and which letters to send": once it becomes propose → the platform applies, **any
adapter can run it**. That is the first non-Claude proof (Gemini CLI).

### 3. Letters: the bus guarantees nothing is lost; push only lets people see them sooner

- **The guarantee "a letter always gets received" is dispatch** (unclaimed request/question are handled by an agent
  the platform dispatches, already there), not any push channel.
- Push (letting a session see it sooner), from best to worst: the tool's native hooks (connected if the adapter has
  them; Claude, Codex and Gemini CLI all have SessionStart/UserPromptSubmit, with different semantics) > an MCP tool
  (receive mail) > the rule file asking the agent to check mail once at start > git post-commit (**only as a
  fallback**: discussion sessions don't commit, commit output in IDEs is shown to people, husky/lefthook's
  `core.hooksPath` would conflict, `.agent-memory` worktrees share hooks and would be noisy, and it records no
  `message.delivered`, which throws off dispatch's waiting decisions; so it comes later, and only prints without
  recording delivery).

### 4. The platform's command line and MCP

- The platform's CLI goes on the PATH and is **renamed**: `hub` clashes with GitHub's old `hub` CLI (still common on
  Homebrew, often aliased as git). Candidate: `agenthub`.
- Rule files name only this command, no local paths.
- **MCP server**: receive mail, send mail, close, write notes, ask the owner; the MCP server's `instructions` field can
  carry the platform's rules, delivering them into tools that support MCP **without changing any project file**.
  Adapters register it in the tool's MCP settings; when they can't, doctor gives manual steps. A stdio MCP inherits the
  agent's environment; remote HTTP MCP and claude.ai connectors are outside the platform's control (stated).

### 5. Rule files: don't change files the user commits

The blind review checked: Codex's `AGENTS.override.md` **replaces** the `AGENTS.md` in the same directory (it would
hide the user's rules); Gemini's `contextFileName` is in `.gemini/settings.json` (often committed); Cursor's
`.cursor/rules` are often committed; a symlink also changes a committed file. So:

- The platform's rule content is short, with no local paths (command names, MCP tool names).
- Ways to get it into the tool, from best to worst: MCP `instructions` (fully general, touches no file) > a local file
  the adapter knows **overrides nothing** (Claude's `CLAUDE.local.md`, as today) > doctor gives one line for users to
  decide whether to add it to their `AGENTS.md`.
- Rule files are kept in line today by SessionStart's `sync-local-setup.sh`; for tools with no hooks a platform job
  regenerates them (the every-15-minutes batch).

### 6. The platform's inputs

- memory-gaps catches "conversations, no commits, no notes" (one project with 88 conversations and 0 notes): that
  needs conversation logs and cannot become git-only. It is merged with memory-nudge (commits without notes,
  tool-neutral) into one script; the "conversations" half is the `transcripts` capability, and without it only the
  commit half runs, explicitly.
- The weekly report's working hours, late-night ratio and human messages need conversation logs: without them those
  sections are explicitly off; the commit, note and letter statistics still run.
- For ACP sessions the platform starts itself, the platform keeps its own neutral log (not relying on the tool's
  format).

## Not tools, but limits all the same

- **The owner side (Principal Handoff 0.3's principal side) can be swapped; Muninn is an optional tool**, on the same
  level as coding agents and LLM providers:
  - The platform is the **requester side**: the registrar writes `〔owner〕` to-dos as `raised` records and projects'
    questions as decisions, and applies the `settled`/`decided` that come back. This half **belongs to the core** and
    runs whichever owner side is connected. **Today the registrar (`assistant/knas-tasks`) sits in the assistant
    module and goes off with it when there is no Muninn: it must move back to the core**; the assistant module keeps
    only what is specific to Muninn (the HTTP part of the bridge, reconcile-questions, channel leasing).
  - **Owner-side implementations** (`hub.json` `owner_side`, default `builtin`):
    | Implementation | What it is | Capabilities |
    |---|---|---|
    | `builtin` (built in, default) | the platform's own minimal principal side: reads the records for the owner directly from the local bus | shows them (`todo.md` + `agenthub owner list`), notifies (notification adapters), closes (`agenthub owner done/drop <key>`, `answer <id> "…"` → a `settled`/`decided` record, authority `principal`) |
    | `muninn` (optional, another open-source project) | a personal assistant: bridge v2 exchanges records over HTTP (`POST/GET /api/records`, ack) | all of the above, plus phone conversations, reminder timing, inferring completion from conversation (`attested`), deep talks, **channel leasing** |
    | others | any system implementing the principal side of Principal Handoff 0.3 | the interface the spec defines is the connector |
  - The docs, `hub init` and doctor all describe Muninn as "optional: install it if you want a personal assistant on
    your phone", the same way as "install Codex if you want agent work done with Codex". Channel leasing and question
    reconciliation say they "need Muninn (or another owner side that supports leasing)".
  - **This is also the second independent implementation of the Principal Handoff spec**: `builtin` is written strictly
    to the public spec and is interchangeable with Muninn, proving the spec lets two unrelated systems connect, not
    just a private format between agent-hub and Muninn.
  - The owner's machine: `owner_side: muninn`, behaviour unchanged.
  - **Without an owner side, open-source users' `〔owner〕` to-dos and questions have nowhere to go**: so `builtin`
    is in the first version, ahead of any agent adapter.
- **Notifications**: `alert.py` becomes adapters (Discord, ntfy, email, Slack).
- **Linux**: scheduling and services are the launchd → systemd user unit / cron layer (the scheduler and
  render-services are already platform code; what changes is the generated files and the load commands); on Linux the
  sandbox is marked "not provided" at first (propose → apply does not need it).

## Scope of the first open-source version (the blind review's suggestion, adopted)

1. ✅ (63fb2f2) **Capability table + `runtime_none`**: capabilities derived from interfaces, doctor's "tool × feature"
   table; Claude-only jobs moved into modules; claude made optional in `bin/setup`.
2. ✅ (27359ce) **completion + providers**: librarian, to-dos, index, weekly report with any API key. Interface, three
   providers, uniform handling, keys and budget are done; reflect-week is already completion (runs on a local Ollama).
   Moving the librarian's three points to completion is a separate step (shadow mode, `llm-providers.md` step 4).
3. ✅ (e2a4bd3) **The owner side can be swapped**: registrar moved back to the core, `owner_side: builtin | muninn`, a
   minimal built-in owner side + notification adapters; Muninn written as an optional tool.
4. ✅ **Platform CLI renamed and on the PATH** (2d77f1d), local paths removed from the rule content, ✅ MCP server
   (10-29, `agenthub mcp`, `bin/mcp_server.py`, docs/mcp.md: inbox/show/thread/send/ack/done + ask_owner, note;
   `instructions` carries the rules and the number of open letters; agents started by the platform are always
   refused). Live run:
   Codex (registered temporarily with `-c mcp_servers.…`, without changing its settings), in the read-only sandbox,
   received mail, read the README to reply, closed it and wrote a note, all landing in the bus and memory. Registering
   in each tool's settings is up to the user (the platform only prints the snippet). A propose agent running
   `agenthub send/done/ack` itself (or any hubbus subcommand that changes the bus) is always blocked by hubbus: it
   recognises the environment variable propose_apply sets, and also looks for propose_apply.py among the ancestor
   processes (clearing the variable or changing the working directory does not escape it; 10-29). Deliberately leaving
   the process tree (e.g. handing it to another resident process to send) can only be blocked by the platform's own
   sandbox.
5. ✅ **Propose → the platform applies** (ba9649d): throwaway worktree, applying with a path allow list, the platform
   sending letters on the agent's behalf. ✅ 10-29 the first non-Claude live run: Gemini CLI stopped serving personal
   accounts from June, so its successor **Antigravity CLI (`agy`)** was used instead, connected with a
   `runtime: command` template (`{prompt}` as an argument, `{cwd}` restricting read_file/write_file to the worktree,
   `command(*)` refused); Gemini 4 Argon handled a real delegation: only the note was adopted, the platform sent the
   reply and closed it, the note was pushed to the memory repo. Flash miscopied a long temporary path → read outside the
   worktree, blocked → `agy` exited 0 with no output; the platform now treats "no output" as a failure, with the reason
   from stderr. The same day **Codex CLI** (logged in with a ChatGPT account) also ran through, with no code change: it
   took the two paths `agy` did not, stdin + `-o {output_file}`, and its own Seatbelt sandbox (`workspace-write`)
   stacked with the platform's throwaway worktree without trouble. Note: Codex's sandbox only limits writes, not reads
   (it can read the secrets directory), while `agy`'s `read_file({cwd})` limits both; the read scope is unified only
   once the platform has its own sandbox (the Linux step).
6. **Linux**. ✅ First part (10-29): the platform runs on Linux: the platform always calls its scripts through the
   interpreter (`hubconf.launch()`, `bin/py`, `hubconf.sh_py()` for hook strings; the first line is only a macOS
   convenience, and `test_python_floor` checks mechanically that nothing relies on it), process working directories go
   through `hubconf.process_cwds()` (lsof or /proc), show_file's fd path uses /proc/self/fd on Linux, and when
   `hubconf.service_manager()` finds no service manager, init/doctor/schedulectl/status page/render-services say
   explicitly "not connected yet" (doctor shows red). `bin/linux-test` (docker) runs all tests in a Linux container:
   all 515 pass. The output on macOS did not change by a single character (hook strings, worktree lists, each
   project's agent settings). ✅ Second part (10-29): **systemd user units**: `hubconf.service_manager()` recognises
   `systemctl --user` on Linux; `bin/systemd_units.py` translates the same plists the platform generates
   (render-services' render(), the scheduler and watchdog templates in launchd/) into .service (+ a .timer when there is
   an interval); init installs the scheduler and watchdog, render-services applies project services, and doctor / the
   status page / schedulectl (status, start, stop, run = systemd-run) / the watchdog's diagnostics all recognise
   systemd; jobs use `hubconf.login_shell()` (/bin/zsh on macOS as before, bash where there is no zsh). The macOS
   plists are identical to before, word for word. **Verified on a real machine**: a lima Ubuntu VM (real systemd) from
   scratch, `bin/setup` → `agenthub init --yes`: the timers of the scheduler, watchdog and mailbox dispatch are all
   scheduled, the scheduler's first tick ran every job, the status page service answers 200, doctor shows both timers
   green. The sandbox (cap-runner's Seatbelt) is not provided on Linux.
   To clean up before release (new users hit these on any system, not just Linux): old pre-move copies in the hub root
   (hub.json, todo.md, digest/…, which init takes for the old layout); the Discord / B2 / backup password / lease token
   declared in the platform registry `projects/agent-hub.yaml` and Muninn's contracts must move into their own modules
   (today a new user's secrets and contracts-check jobs are red from the start).
7. **The bus becomes a platform service + socket** (data integrity; can come after 4).

What the first version can honestly say: "judgment points with any LLM, any key; agent work the platform starts with
any CLI agent; one owner side built in, and if you want a personal assistant on your phone you can connect Muninn (or
any implementation of Principal Handoff); for integrating interactive sessions and conversation statistics only Claude
Code has a complete adapter today, and leasing needs Claude Code + Muninn."

Postponed: leasing over ACP, the platform sandbox wrapping agents, post-commit push, an `agenthub agent` launcher,
conversation-log parsers for other tools.

## Steps (nothing changes on the owner's machine at any step)

0. ✅ ratchet (1186f65).
1–7 per the scope above, each going live on its own. Verification for each step: the nightly flow on the owner's
machine runs as usual (shadow mode, golden files); in an isolated HOME, a full nightly run + one mailbox delegation
with settings that have only an API key / only Gemini CLI.

## Confirmed (10-29)

1. Principle 1 narrowed to "work the platform starts + the platform's data"; sessions users open themselves only get
   advice.
2. Leasing over ACP postponed; the first version stays native Claude (leasing needs Claude Code + Muninn).
3. The platform CLI renamed `agenthub`.
4. Muninn is an optional tool; the owner side is built in by default, and the registrar moves back to the core.
