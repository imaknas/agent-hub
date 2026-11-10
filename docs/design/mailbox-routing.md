# Project mailbox: routed and handled mechanically by the platform

The headless handler's part is replaced by `docs/design/mailbox-session-first.md` ("Triage"): it takes nothing and acts only on
its letters. What this document says about it taking a letter describes letters from before that.

> **Status**: confirmed by the owner and implemented on 10-24 (first step: the notes capability).
> `mailbox.handle: work` is not done yet. (10-31: the setting is now `mailbox.handler: agent | propose`, in hub.json;
> neither lets the handler change code, so a work mode is still not done.)

## The problem

Letters are addressed to a "project", but today they are only seen when that project happens to have a session awake
and active (a session opened, typing, using a tool); an idle session does not wake up to read mail, unclaimed letters
wait 12 hours before a headless agent takes over, and that agent can only write notes.
What actually happened on 10-24: the assistant's letter from 21:30 was only seen on agent-hub's side when the owner
typed something.

The owner does not want to manage the communication layer. And there are two very different cases that cannot be
handled the same way:

| | Conversation | Delegation |
|---|---|---|
| Example | sessions of two projects going back and forth on a design | "get this done" |
| Addressee | **a particular session in the receiving project that already has context** | the **project**; anyone can do it |
| Needs | that session (or its context) to carry on replying | someone responsible for finishing it |

## Principles

1. **Only one way to send**: agents always use `hubbus send` and do not have to judge which channel to use; CLAUDE.md
   does not teach them to choose.
2. **Routing is a rule in the program, not an agent's judgment**: the platform decides from facts already recorded.
3. **Everything tied to the runtime (Claude Code) is in one replaceable module** (`bin/runtime_claude.py`): "is this
   session still alive" and "continue this session in the background". Switching tools, or Claude Code shipping an
   official way to wake a session, changes only this one file. Agents no longer use Claude Code's cross-session
   messages to pass things between projects.

## Already there (nothing to redo)

- `hubbus send` records the sending session automatically from `CLAUDE_CODE_SESSION_ID` (`from_session`).
- A reply (`--reply-to`) is automatically addressed to the session that asked (`to_session`); the mailbox keeps it for
  that session for 30 minutes first.
- Every delivery of a letter into a session has a `message.delivered` record (the hook gets the `session_id`).
- The hook delivers new letters into the context when a session starts, when the user types, and after a tool is used
  (at most once every 2 minutes).
- Verified on 10-24: every Claude Code session's process arguments carry its id (`--resume=<id>` or
  `--session-id=<id>`), and it is the same as the `session_id` the hook records and `CLAUDE_CODE_SESSION_ID`: "is this
  session still alive" can be decided mechanically.

## The new routing rules (all inside hubbus, decided mechanically)

Each letter is classified when sent:
- **Conversation**: it has a `to_session` (a reply to a letter some session sent).
- **Delegation**: any other `request` / `question` / `task`.
- **Notification**: `info` / `progress` / `done` etc.: nothing to handle; seen as usual the next time a session is open.

How a **conversation** is delivered:
1. The session is still alive (`runtime_claude.alive(sid)`: a Claude Code process's arguments carry this session id) →
   wait for its hook to deliver it, at most `CONVERSATION_WAIT` (5 minutes proposed; nearly instant while it is in use).
2. Not delivered within 5 minutes (idle), or the session no longer exists → continue it in the background
   (`runtime_claude.resume_background`): carry on with its full context in the background; replies still go through
   `hubbus send`.
   - **Through a judgment point**: this is an unattended claude call, so it also goes through `bin/llm.py`, declaring a
     new judgment point `mailbox-conversation` (same permissions as today's mailbox-escalation: dontAsk, may only write
     memory notes and `hubbus`; uses the headless token). The original session's permission mode is **not reused** (it
     may be acceptEdits/auto, which cannot be used with nobody watching).
   - It runs in the session's **recorded cwd** (it may be in a worktree), not the project's main folder.
   - **A lease's session is never continued**: when `to_session` is a lease-runner lease (a letter sent from a deep
     talk), it is handed to the runner to deliver into that lease; if the lease has already been detached it is handled
     as a delegation. Continuing a lease outside the runner would bypass its permission relay and sandbox settings.
   **Cost**: while the session is still open, Claude Code opens a separate copy (with the same context), so when the
   owner returns to the original session in Desktop he does not see this part directly; he only gets a "it has been
   handled" letter the next time there is activity; the status page lists it too.

**Claiming** (so the same letter is not done twice): when the platform dispatches someone for a letter it records a
`message.delivered` (`via: handler`); afterwards, when the hook delivers letters to an open session, a claimed letter
is listed as a single line "the platform is handling it", not as something for the session to do. Conversely, the
platform dispatches no one for a letter the hook has already delivered into some session.

**Presence records**: every hook run records "this session is in this project, its cwd is here, it was active at this
time" (a new `session.seen` event or a small table), used below for "is anyone using this project" and for the cwd of a
background continuation.

How a **delegation** is delivered (replacing the 12-hour escalation):
- The receiving project **has a session in use** (alive, and with hook activity within the last `CONVERSATION_WAIT`) →
  first wait for its hook to deliver it, at most `CONVERSATION_WAIT`; once delivered it is that session's, and the
  platform dispatches no one.
- Nobody using it, or still unclaimed after waiting → immediately dispatch a headless agent in that project.
- Only one at a time per project; letters arriving together are handled together.
- The capability follows the project registry's `mailbox.handle`:
  - `notes` (default): answer, write memory notes, turn things that need a decision into questions to the owner: the
    capability of today's 12-hour escalation.
  - `work` (enabled project by project, second step): change code in its own worktree, OS sandbox, run tests, merge
    back into main through the merge gate (no push).
- Anything that needs the owner's judgment, is irreversible or external (push, deploy, spending money, messaging a
  person) is always turned into a question for the owner (into the assistant), never done.

**Trigger**: launchd `local.agent-hub-mailbox-dispatch` runs `hubbus dispatch` every minute (at most a minute of delay;
"wait 5 minutes" naturally falls across a few consecutive runs), and the 15-minute bridge runs it once too as a safety
net. **Not triggered from the sending session**: the sending session may be in an OS sandbox, and a background process
started from there cannot write the platform's state or start an agent.
The assistant's letters are pulled in by the bridge from the assistant, so they reach the bus within 15 minutes at the
latest; for faster, ask the assistant to run `hubbus bridge` once right after queueing a handoff (same machine).

## Safety and cost

- Letters are sent by other agents: treated only as "requests", never as "authorisation"; permissions follow only the
  project registry and the sandbox.
- Stopping two agents from replying to each other forever: the platform handles one thread (reply_to chain) for at most
  N rounds (4 proposed); beyond that it is turned into a question for the owner.
- A per-project daily cap on handling count/cost; over it, it stops and the status page turns red.
- Channel-lease sessions (read-only (discuss) leases) still do not count as recipients.

## What changes for agents and the owner

- Agents: no new rules. CLAUDE.md only changes "unclaimed for 12 hours gets escalated" to "after sending, the platform
  handles it; no need to wait for the other side to be open".
- The owner: only gets questions in the assistant that really need his decision; the status page shows, under the
  mailbox table, how each request and question of the last day went (woken, hook, background copy, agent dispatched,
  held; done or not).

## Decisions for you to make

1. **For conversation replies to an idle session, "continue the original session" or "dispatch a new one"?**
   - Continue (as written above): full context, but a very long session (like this agent-hub one, two thousand-plus
     messages) has to be reread in full on every continuation: expensive.
   - Dispatch a new one (as for delegations): cheap, but it only sees the letters of this thread, not the details that
     session discussed before.
   - Middle way: continue when the conversation is not long, dispatch a new one with the whole thread attached when it
     is too long.
   **Decided by the owner (10-24)**: the middle way, judged by "the context a continuation would load" (the usage of the
   last reply in the conversation log; if compacted, the size after compaction): continue under 150k tokens, dispatch a
   new one above (`hub.json` `mailbox.resume_max_tokens`). This agent-hub session was at 550k then; one continuation
   would reread the whole thing.

## Out of scope this time

- A letter already delivered into some session that never handles it (no done) has no follow-up today, and this change
  does not handle that either. The status page flags only letters no session took (unclaimed for over an hour); one
  delivered and left open shows only in its project's row of the mailbox table (open letters, how many not yet in a
  session, the oldest).

## Steps

1. `runtime_claude.py` (alive, resume_background through llm.py) + test live how `--bg --resume` behaves with the
   session open/closed.
2. hubbus: classify on send, trigger dispatch on send, "wait 5 minutes → continue in the background" for
   conversations, "dispatch headless (notes) at once" for delegations, round and cost caps; remove the 12-hour
   escalation. Tests use a fake runtime module.
3. Status page, CLAUDE.md, known-issues.
4. (Later) enable `mailbox.handle: work` project by project.

## Implementation record (10-24)

- `bin/runtime_claude.py`: `live_sessions` (process arguments), `context_tokens` (the conversation log's usage),
  `resume_args` (`--resume <id> --fork-session`; tested live: the fork carries the context, gets a new id, and not one
  line of the original conversation log changed).
- `hubbus`: presence records (a `presence` table, updated by every hook run), claiming (`message.delivered` with
  via: handler/resume/held), `dispatch`, `thread`; the 12-hour escalation removed. Judgment point
  `mailbox-conversation` (dontAsk, writes only memory notes and hubbus).
- Only request/question get dispatched or continued; reply kinds (done/info/progress) still wait for the hook to
  deliver them, so nobody else's session is continued for a single "it's done".
- Tests: `tests/test_mailbox_dispatch.py` (14: delegation dispatched at once / waits for a session in use / a paused
  session does not block, notifications not dispatched, nothing dispatched for what the hook delivered, no
  re-dispatch, agent-hub's delegations left to itself, conversation waits / continues / dispatches new when too long /
  leases not continued, loop prevention, the hook does not treat claimed letters as work to do).
- At go-live there were no pending request/question, so the first run dispatched no one.
- **Live test (test sandbox idea-999996, through the every-minute launchd service)**: a conversation reply was
  continued in the background about 30 seconds after being sent; the forked copy replied with a passphrase only the
  original session knew and closed it, and the original session's conversation log was not touched at all; a
  delegation dispatched a headless agent in about 30 seconds, which wrote a memory note, replied and closed it.
  `--resume` from another folder also works (found by session id, independent of cwd).
- Added after the advisor's review: if a background continuation cannot start, it is immediately re-dispatched as a
  delegation, and also when still not closed after 35 minutes (a letter never stays stuck at "the platform is handling
  it"); the bridge and the every-minute service share one lock (the two never dispatch at the same time); the
  background copy gets `--strict-mcp-config` (the project's MCP servers are not loaded); for a reply handled in the
  background while the session was idle, that session sees a one-line explanation the next time it is active.

## Still to confirm

- Background continuation uses `llm.py run` instead (`claude -p --resume <id> --fork-session`, exits when done), not
  `--bg`; whether the forked copy shows up in Desktop's session list is not confirmed yet (waits for the first real
  continuation).
- `CONVERSATION_WAIT` 5 minutes, at most 4 conversation rounds, the daily cap: proposed values first; adjust after
  looking at the status page.

## 10-26 trial: delegations also use a session's context (m10261706, the owner: "OK, trial it first")

- **Delegation**: someone is typing in the project → wait for its hook 5 minutes as before. After that, if the project
  has a session active within 24 hours (`hub.json mailbox.fork_recent_hours`) and still alive, handle it in a
  background fork with that session's context (the same path as continuing a conversation: `--resume --fork-session`,
  the same notes capability, round and daily caps), so it can answer about what that session has in hand (e.g. the
  assistant asking about production data a project's open session was looking at); none, context over the cap, or
  today's quota used up → a headless agent (as before).
- **agent-hub's letters** are no longer reserved for its dedicated session (with nobody typing, nobody picked them up):
  forked or dispatched headless like any other project.
- **Merging back into the original session**: the fork is a copy, and what it says does not enter the original
  session's context. So once the fork is done (a final reply, the letter closed, or timed out), the next time the
  original session is active, that letter carries the fork's final reply (up to 800 characters) and the letters it sent
  for it; while the fork is still running nothing is said. No "real context merge": the runtime has no such operation,
  and two sides moving forward at once would overwrite each other; a summary in the next prompt has the same effect.
- **info does not count as stuck**: "stuck" in `hubbus status` counts only request/question; the sessions' instructions
  say to use request/question when the other side has to do something.

## 11-01: an MCP session holds a delegation back (`docs/design/mailbox-runtimes.md` step 1, A3)

A coding agent that reaches the mailbox only through the platform's MCP server (`agenthub mcp`, e.g. Codex) has no hook,
so before this the platform saw nobody in the project and handled a request in a background copy or with a headless
agent while the user was working there. Now:

- **Presence**: the MCP server writes a `presence` row at `initialize` and at every tool call, with session
  `mcp:<server pid>:<start time>`, `runtime` = `mcp:<clientInfo.name>` and `via` = `initialize` / `tool` (two columns
  added to the table; the hooks leave them empty). A sandboxed project's server writes it through its outbox (operation
  `presence`), taken in only for the outbox's own project and only while that server process runs. Not written under
  `HUB_LLM_POINT` / `HUBBUS_LEASE`, nor by a server whose project comes from `AGENTHUB_PROJECT` while its working
  directory is outside it.
- **Live by itself** (`hubbus mcp_sessions`, no runtime asked): the pid runs, its start time is the one in the id, its
  command line is this platform's `mcp_server.py`, and `seen` is within `MCP_PRESENCE_MIN` (`mailbox.mcp_presence_min`,
  30) of a tool call, or `CONVERSATION_WAIT_MIN` (5) of only an `initialize`. `active_sessions` includes these rows;
  `recent_session` never picks one (nothing to fork), and an `mcp:` id is never a `from_session` (so never a
  `to_session`).
- **Hold**: a delegation not yet delivered to any session, to a project with such a row, is held while it stays live, up
  to `MCP_HOLD_MAX_MIN` (`mailbox.mcp_hold_max_min`, 120) from the letter's timestamp; then it is routed as before (a
  log line says it was held). Holding writes nothing on the bus; a letter already delivered is under the 60-minute seen
  rule and is never held. Conversations are routed as before. A Claude Code session's presence keeps the 5 minutes.
- **The session is told**: every tool result except `inbox`'s ends with the number of open letters.
- **Status**: `hubbus status --json` has `held` (`id`, `to`, `ts`, `by` = the runtimes), and those letters are not in
  `stuck`; the status page shows "held: <id> (<project> in use by …)".
- MCP rows not seen for 24 hours are pruned with closed letters (bridge).

## 11-01: waking a live session (`docs/design/mailbox-runtimes.md` step 2, B1)

- **The port**: `wake(sid, text) -> bool` in `bin/runtime_ports.py`, its own optional port (`PORTS["wake"]`,
  `OPTIONAL`), Unsupported by default. A runtime may also give `wake_address(env)` (what a hook's environment says about
  reaching the session) and `unsupported(port)` (why it lacks one, shown in doctor's feature table).
- **Dispatch** (`hubbus wake`): when a request/question **not yet delivered** waits for a live session (a conversation
  to a live `to_session`, or a delegation while a session is active in the project: the live one most recently seen,
  never an `mcp:` row), the platform calls `wake` once per letter with only a notice ("📬 1 letter from <project>:
  <id>. Run agenthub inbox."), and records `message.woken` (id `<letter>:woken`, payload `session`, `via: wake`, `ok`,
  `error` when it raised). The letter stays on the bus; the hook delivers and claims it. Whatever `wake` answers, the
  wait and the routing after it are unchanged; a runtime that can't wake that session records nothing.
- **Status**: `hubbus status --json` has `letters`: the requests/questions to projects of the last 24 hours, each with
  its path (`woken` / `wake-failed`, `hook`, `inbox`, `ack`, `resume`, `handler`, `held`) and `done`; the status page
  shows them under the mailbox table.
- **Wake addresses**: the SessionStart hook (`hub-event` handler `wake_address`) keeps what the runtime's
  `wake_address` returns in `hubconf.session_address_file(sid)` = `secrets_dir()/sessions/<sid>.json`, mode 600 (the
  folder 700); only for a session in a project, not under `HUB_LLM_POINT` / `HUBBUS_LEASE`. SessionEnd removes it, and
  the bridge's prune removes those of sessions no longer live (10 minutes' grace). The secrets directory is in every
  sandbox's `deny_read`; the folder is excluded from the nightly backup (`backup.py`: `--exclude`, `covers()`) and from
  check-secrets' undeclared scan.
- **Claude Code**: `wake` is not implemented: its docs give only the inbox socket's auth line, not the message format
  (see mailbox-runtimes.md step 2). `wake_address` already keeps the socket and token, so `wake` is the only piece to
  add. Doctor: "an idle open session is woken when a letter arrives for it: off (… Claude Code documents only the auth
  line …)", as an information line (`·`, counted as neither a problem nor a reminder: a letter still arrives at the
  next hook).

## 11-01: more than one coding agent (`docs/design/mailbox-runtimes.md` step 3, A1)

- **Settings**: hub.json `runtime.installed` (every agent the user opens sessions with; default `[runtime.default]`) and
  `runtime.bare_ids` (whose session ids stay bare; default `claude`), in `contracts/hub.v1.schema.json`; config check
  refuses an unregistered name. `runtime.default` stays the agent the platform starts.
- **Ids**: `runtime_ports.qualify` / `runtime_of`: the bare runtime's ids as they are, every other runtime's
  `<runtime>:<id>`, `mcp:` ids A3's. Nothing on the bus is migrated.
- **Hooks**: the installed commands are `hub-event --runtime <name> <event>`; `hub-event` and `hubbus hook [--runtime]`
  parse with that runtime's lifecycle (none given: `runtime.default`'s, as the hooks written before), record presence
  with the qualified id and the `runtime` column, and deliver to that id. `hub-event install` installs every installed
  runtime's hooks.
- **Dispatch**: `runtime_ports.Sessions` is the sessions port over every installed runtime (live = their union, plus
  A3's MCP rows), `context_size` / `alive` / `wake` routed by `runtime_of(sid)`. A background resume or fork runs the
  `mailbox-conversation` point, which runs on `runtime.default`: only that runtime's sessions are resumed or forked
  (`can_resume`); a conversation to another runtime's idle session is handled as a delegation, and `recent_session`
  skips such sessions.
- **current_session**: one installed runtime answers from the environment as before; with several, the nearest
  ancestor process a runtime claims (`session_process(command)`) decides, and none claiming one is unknown.
- **Rules files**: `hub-runtime instructions-files` lists each installed runtime's rules file; sync-local-setup writes the
  managed block into each. `local-ignores` is their union.
- **Sandboxed projects**: `policy.protect()` is the union of the installed runtimes' control files; render-project-
  settings installs another runtime's hooks there only if it has `control` and `confine`, and `hub-event` does nothing
  for a runtime without them in a sandboxed project (logged `refused:<runtime>`).
- **Doctor**: with more than one installed runtime, each feature row has a cell per runtime, plus each runtime's rules
  file (or that its sessions get the rules from the MCP server's instructions) and the runtimes refused in sandboxed
  projects. With one, the output is as before.
- Still on the default runtime: headless runs, leases, and the
  older `hubbus hook` entry points called without `--runtime` (worktree-memory.sh, user-level hooks). (The session
  statistics, memory gaps and the weekly review, no longer depend on any runtime since 11-03: `bin/sessions.py`.)

## 11-02: Claude Code's wake, an asyncRewake waiter (`docs/design/mailbox-runtimes.md` step 2, B1 as built)

- **The waiter**: every Stop arms one, `hub-event --runtime claude wait` → `runtime_claude.wait` (an `asyncRewake: true`
  hook, `timeout: 86400`, its command `[ -x …/hub-event ] && exec … wait; exit 0`: no `|| true`, which would turn the
  wake's 2 into 0). It records itself in `secrets_dir()/sessions/<sid>.waiter` (newest wins: an older one that finds
  another's token there exits 0), heartbeats that file each poll (2 s), and takes each `<sid>.wake-<nonce>` request by
  renaming it to a dot name no lookup matches, then unlinking it: one request wakes once. It prints the notices to
  stderr and exits 2; it exits 0 when its record is gone (SessionEnd removes all of a session's files there, the
  bridge's prune removes a record whose heartbeat stopped), when `CLAUDE_PID` is gone, and a minute before its timeout.
  No bus access in the loop.
- **`runtime_claude.wake(sid, text)`**: writes a request (tmp + rename, mode 600) only while the session's waiter
  heartbeats (10 s); otherwise False, recorded as `message.woken` with `ok: false` ("wake failed" on the status page),
  and the routing goes on as before. A session is wakeable once its first turn has ended.
- **Not armed**: platform runs (`HUB_LLM_POINT`, `HUBBUS_LEASE`, checked by hub-event); `-p` runs, told from the
  session process's own arguments (`CLAUDE_PID`'s argv: `-p`, `--print`, `--print=…`); SDK runs
  (`CLAUDE_CODE_ENTRYPOINT` sdk-cli / sdk-ts / sdk-py, a second signal only: a `claude -p` started inside another
  session, e.g. from an agent's Bash, inherits that session's `claude-desktop` or `cli`); and a session whose process
  is unknown or unreadable (fail safe: a missed wake is fine, a hung run isn't). Observed 11-02 on 2.1.316: `claude -p`
  **waits** for an asyncRewake Stop hook to exit before it exits (the docs say it kills background hooks at teardown),
  so a waiter armed in a `-p` run hung it for minutes, up to the hook's day.
- **The notice**: "📬 agent-hub: 1 letter from <project> (<id>). Run agenthub inbox." Claude Code shows a woken hook's
  stderr as `Stop hook blocking error from command …: <stderr>`, so the text names agent-hub.
- **Seeing failures**: a letter whose wake reached a waiter but that the session didn't take (hook, inbox or ack)
  within `CONVERSATION_WAIT_MIN` shows `woken, not picked up` (`woken-missed`) on the status page; `hubbus wake-misses`
  lists those of the last 7 days, and doctor reminds when there are 2 or more. Computed from the bus events; nothing new
  is recorded.
- **Socket token**: no longer kept. `runtime_claude.wake_address` is gone: the waiter needs no token, and an unused
  credential for posting into a session isn't worth holding. The generic address handler stays for other runtimes.

## 11-02: the handlers are confined (`docs/design/event-jobs.md` 3.2, section 11)

Since 11-10 the handlers have no shell: their mail is the platform's MCP server, attached to the run
(`docs/design/confined-mail-tools.md`). What follows about Bash and the mail subcommands is how it was until then.

**The gap.** A request or question to a project, from anyone who can write the bus, starts a handler there. Until
now the handlers loaded the project's saved settings: every allow rule a person had approved in that project's
sessions (`Bash(npm:*)`, `Bash(git push:*)`…), the user's, the project's hooks, and in a sandboxed project the CLI's
default `autoAllowBashIfSandboxed: true`. `dontAsk` refuses what no rule allows, but those rules were part of the
allow set, so the letter's author reached the project with everything ever approved there. Event agent jobs were
closed against this already (`agent-job-event`, `settings: platform`); this applies the same pattern to the rest.

**The pattern** (`contracts/llm-points.yaml`, `runtime_claude._flags` / `platform_settings`): `settings: platform` and
`project_env` name the project; the runtime loads no user, project or local settings file, only one the platform
writes from the policy (the sandbox when the registry enforces it, with `autoAllowBashIfSandboxed: false`; the deny
list; the mail subcommands). `dontAsk` + `prompts: none`, so the point's rules plus those are the whole boundary. A
point that declares `tools` gets exactly those; one that doesn't (an `allow:` job) gets reading, Bash for the mail
commands and the tools its rules use, plus Write when a rule names Edit (Claude Code's Edit rules govern every
file-writing tool; a job that rewrites its state file needs Write, live probe 11-02). A runtime without the `confine` port refuses such a point with the reason; it is
never run unconfined. `llm.py` sets `AGENT_HUB_CONFINED`, so `hubbus` acts only as and for the run's own project; `drop --question` is let
through (withdrawing a question the project asked the owner, which `send` already checks is its own: the handler's
answer to reconcile-questions), `drop` of an owner's to-do (`--task`) is not.
For Claude Code: `--restricted --settings <platform file> --tools <…>` (2.1.316). `--restricted` also confines the
file tools to the working directories, checked where a path resolves: a worktree's `.agent-memory` is a link to the
main checkout's, so for a `./` rule whose fixed part resolves outside the folder the runtime adds that directory
(`--add-dir`) and the same rule on the resolved path (live probe 11-02: without it the write is refused; with it the
note is written and nothing else under `.agent-memory` is).

| point | started by | what it needs | declared |
|---|---|---|---|
| `mailbox-escalation` | `hubbus dispatch` → `run-agent-job.sh` (`MAILBOX_PROJECT`) | read the project, write `.agent-memory/memory/`, read/send/close its letters, ask the owner | `settings: platform`; tools `Read,Edit,Write,Glob,Grep,Bash`; rules `Edit(./.agent-memory/memory/**)` + the `agenthub` mail subcommands (Write is covered by the Edit rule: a new day's file) |
| `mailbox-conversation` | `hubbus dispatch` resume / fork of a recent session (`MAILBOX_PROJECT`); the prompts `mailbox-resume.md` and `mailbox-delegate.md` both run here | the same, with the session's context; the session's own mode and saved permissions are not carried over | the same |
| `mailbox-propose` | `propose_apply.py` (`mailbox.handler: propose`) | read the project, propose notes and letters | unchanged: no Bash, `--strict-mcp-config`, a throwaway worktree, the platform adopts only `.agent-memory/memory/**` and sends only replies to the letters it was given, owner questions and their closes. Its guarantee is the platform's, not the tool's settings, so any runtime can run it; it is deliberately not `settings: platform` (that needs the `confine` port) |
| `agent-job-confined` (new) | `run-agent-job.sh` for a registry clock job with `allow:` (`AGENT_JOB_PROJECT`, set by `registry.py`) | the job's `allow:` | as `agent-job-event`; `agent-job` no longer takes an allow list, so a job with one never runs on the unconfined point, whatever `AGENT_JOB_POINT` says |
| `agent-job` | `run-agent-job.sh` for a job without `allow:` | whatever its prompt asks | unchanged: `auto`, the project's settings and hooks; listed on the status page. Not reachable through the bus (its prompt is the owner's file) |

**Bash.** It stays in the two mail handlers only because the mail is a command (`agenthub send/inbox/done…`): the
platform settings allow nothing else, the rules name nothing else (the raw `hubbus` path, still in the rules of
unconfined runs, is dropped for confined ones), and `dontAsk` refuses the rest. Taking it out entirely means giving
confined runs the mail through the platform's MCP server (`agenthub mcp` has inbox/thread/send/done/ask_owner/note),
which today refuses every platform-started run (`HUB_LLM_POINT`); proposed separately.

**What no longer happens for these runs**: the project's hooks (SessionStart delivery, SessionEnd's memory commit):
the prompt says to run `agenthub inbox` first, and their notes are committed by the hourly `sync-all-memory`, as for
event jobs.

Tests: `tests/test_handler_confinement.py` (no saved project rule reaches the command or the settings file; Bash rules
are only the mail subcommands; declared tools without Bash give none; no project → refused; a runtime without
`confine` refuses; the linked `.agent-memory`; dispatch and the registry name the project);
`tests/test_run_agent_job_args.py` (a job with `allow:` runs on `agent-job-confined`).
