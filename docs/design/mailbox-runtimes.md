# The mailbox with more than one coding agent, and next to native session messaging (11-01, r3)

This design answers two questions from the owner. Target: v0.2.0, except the Codex adapter, which comes in the next
version.

1. A user runs Claude Code and Codex side by side. What does the mailbox do for each?
2. A user runs only Claude Code, which has its own way for sessions to message each other. Is the mailbox then a step
   back?

Status: step 1 (A3) implemented on 11-01 (record in `docs/design/mailbox-routing.md`, "11-01: an MCP session holds a
delegation back"); step 2 (B1) implemented on 11-01 with Claude's `wake` Unsupported (record in mailbox-routing.md,
"11-01: waking a live session"), and Claude's `wake` built on 11-02 as an asyncRewake waiter (record in
mailbox-routing.md, "11-02: Claude Code's wake"); step 3 (A1) implemented on 11-01 (record in mailbox-routing.md, "11-01: more than one
coding agent"); step 4 is the next version.

r2 followed two cold reviews: one against the code, one against the tools' documentation. r3 fixes a third review of
r2: the messaging tokens move to the secrets directory, the hold covers only letters not yet delivered, and presence
checks the start time and process as well as the pid. Bare session ids are pinned to one runtime. What r2 changed from
r1:
- A3 now works on its own.
- Presence holds a letter back instead of only delaying it.
- B1 uses Claude Code's documented inbox socket.
- B2 (sessions nudging each other) is dropped.
- A1 keeps today's session ids and routes resume calls per runtime.

## Where things stand (checked against the code and the docs on 11-01)

The platform has one coding agent, `runtime.default` in hub.json (default `claude`). Everything session-shaped goes
through that runtime's ports in `bin/runtime_ports.py`:
- `lifecycle`: the hooks that put letters into a session;
- `instructions`: the rules file;
- `sessions`: `alive`, `live_sessions`, `context_size`, `current_session`;
- `transcripts`;
- `agent`: headless runs and resuming a session in the background;
- `control` / `confine`: what a sandboxed project's sessions load and how they are confined.

The hook entry points (`hub-event`, `hubbus hook`) load the default runtime's `lifecycle` to parse events and inject
text. Any agent that speaks MCP can also reach the mailbox through `agenthub mcp` (`docs/mcp.md`).

Routing (`bin/hubbus` `dispatch`, every minute; constants in `hub.json` `mailbox`):

- **A conversation** is a letter with a `to_session`.
  1. While that session is live (in the default runtime's `live_sessions`), the dispatcher waits `CONVERSATION_WAIT_MIN`
     (5 minutes, counted from the letter's timestamp) for the session's hook to deliver it.
  2. Then it resumes the session in the background, if the context is at most `RESUME_MAX_TOKENS` and the project's
     `DAILY_RUNS` aren't used up.
  3. Otherwise the letter is handled as a delegation.
  4. A reply is hidden from the project's other sessions for `REPLY_WAIT_MIN` (30 minutes) while it waits for the
     session that asked.
- **A delegation** is a request or question addressed to a project.
  1. If a `presence` row for the project is fresh (within `CONVERSATION_WAIT_MIN`) and its session is in `live`, the
     dispatcher waits, again for 5 minutes from the letter's timestamp.
  2. Then, if a live session was active in the project within `FORK_RECENT_H` (24 hours), the letter is handled in a
     background fork of that session.
  3. Otherwise a headless agent handles it. That agent only writes notes and replies; it never changes code.
- **"Seen" letters.** A letter a hook or an MCP `inbox` call delivered counts as seen. If no ack, reply or done follows
  within `TAKE_TIMEOUT_MIN` (60 minutes), it is dispatched anyway.

`presence` (session, project, cwd, seen) is written only by the Claude hook. `live` comes from the default runtime
alone.

### What a Codex session gets today (MCP only)

| | Claude Code session | Codex session (MCP) |
|---|---|---|
| Platform rules | Managed block in `CLAUDE.local.md` | Only the MCP server's `instructions` at `initialize` (Codex reads them) |
| Letters arrive | Pushed by hooks (📬) | Only when the agent calls `inbox` |
| Replies to the asking session | Yes | No. MCP over stdio has no session id, so replies land in the project mailbox |
| The platform sees it working | Yes (presence + `alive`) | No |
| Memory-note nudge after a commit | Hook | None |
| Memory gaps, weekly statistics | From Claude transcripts | Not counted |

What happens: while the user works in Codex, the platform sees nobody in the project. After 5 minutes it handles a
request in a background fork of the last Claude session, or with a headless agent. The handler only writes notes and
replies, but the project's own agent and the platform then answer the same request separately, and the user's Codex
session never learns of it.

### Claude Code's native session messaging

- **Sessions message each other on the same machine, and an idle receiver is woken.** "When the receiving session is
  idle, Claude Code starts a new turn with the message" (code.claude.com/docs/en/cross-session-messaging).
- **There is a documented way in from outside a session**: each session binds a per-session inbox socket, which the docs
  describe for "a script or hook to post into a session". `CLAUDE_CODE_MESSAGING_SOCKET` and
  `CLAUDE_CODE_MESSAGING_TOKEN` are set for hooks before any hook runs, SessionStart included. The auth line
  (`{"type":"auth","token":…}`) is documented. The message payload format is not.
- **A sender that isn't one of the session's own processes can be held for approval.** If the receiver is in a mode that
  skips permission prompts, the message waits for a dialog in the terminal, or expires after 5 minutes under `-p`. It
  goes straight in when the receiver prompts for permissions, or when `crossSessionInbound: accept` applies.
- **A second documented wake**: an `asyncRewake` hook that exits with code 2 "wakes Claude immediately even when the
  session is idle".
- **Reading session state from outside**: `claude agents --json` gives the pid, session id, cwd and busy/waiting/idle
  status of each session. The docs call it the supported way to do this.

For a Claude-only user the mailbox is worse at exactly one thing: getting a letter into an open session *now*. A letter
waits for that session's next hook, which comes with the user's next message or a tool run. An idle open session isn't
woken. After 5 minutes the platform answers from a background copy, and the session the owner is looking at never sees
it. Native messaging is a better transport. The mailbox is the record and the router: letters addressed to projects,
durable on the bus, threads, done/drop, dispatch when nobody is there, owner questions only the owner side closes, the
librarian applying letters at night, and any tool. The fix is for the mailbox to use the native transport (Part B), not
to choose between them.

## Principles

- The platform guarantees delivery and dispatch. A tool's native feature is reinforcement: if it fails, letters still
  arrive (later) and are still dispatched.
- Tool syntax lives only in adapters. Codex specifics go in `runtime_codex.py`, Claude's in `runtime_claude.py`.
- A missing capability is switched off explicitly with its reason (`Unsupported`, doctor's feature table).
- Across projects, only letters. `bin/guard.py` keeps sessions from opening sessions in other projects. This design
  doesn't loosen that: no session is asked to message another project's session.

## Tiers: per-tool code only where it adds, never where it is needed

The owner's concern (11-01): special cases per tool never end. So the design is tiered. Only the top tier is per-tool.

| Tier | Which agents | Per-tool code | What they get |
|---|---|---|---|
| 0 | any agent that can run a shell command | none | `agenthub send/inbox/done` |
| 1 | any MCP-capable agent (Codex, Cursor, Gemini CLI, Antigravity…) | none (MCP is the standard) | tier 0 + the platform's rules (MCP `instructions`) + presence that holds dispatch back (A3) |
| 2 | tools worth a deep integration | one adapter per tool | letters pushed into the conversation, waking an idle session, replies to the asking session, transcript statistics |

Agents the platform starts go through ACP (`runtime_acp.py`): any ACP agent, no per-tool code.

At tier 1 no letter is lost, and dispatch is held back while the agent is working through agenthub's tools. Tier 2
adds what tier 1 can't see: a session that is coding and never calls an agenthub tool. Through MCP the platform only
hears an agent when it calls one of agenthub's tools, so it cannot tell such a session apart from no session. Only a
hook can. Tier 2 also makes delivery faster.
A new tool that speaks MCP is fully usable with no code written for it. Tier 2 exists because no cross-tool standard
covers "push into a live conversation" or "is this conversation alive" yet. MCP and ACP stop at the session boundary,
and every tool's hooks differ. That is the same gap as the ACP RFD on questions that outlive the session. If a standard
fills it, tier 2 collapses into one generic adapter.

## A3: MCP sessions hold dispatch back (tier 1; first, stands alone)

1. **The MCP server records presence.** On `initialize` and on every tool call it writes a presence row:
   - `runtime` = `mcp:<clientInfo.name>`. MCP requires `clientInfo` in `initialize`.
   - session = `mcp:<server pid>:<start time>`. stdio MCP has no session id, so the server makes one up per process.
   - cwd, and `seen` = now.

   It does **not** write presence:
   - under `HUB_LLM_POINT` or `HUBBUS_LEASE` (platform-started agents and leases, the same exemptions `mail_text` uses);
   - when the project came only from `AGENTHUB_PROJECT` and the server's cwd is not inside that project. One global
     server in an IDE must not pin a project forever.
2. **An MCP presence row counts as live by itself**, without asking any runtime, when both hold:
   - its server process is alive (the pid in the id, checked with `os.kill(pid, 0)`; the MCP server runs on the hub
     machine);
   - the process is still the same one: the start time in the id matches the process's start time, so a reused pid
     doesn't count. A presence entry that came through a sandboxed project's outbox counts only if that pid is an
     `agenthub mcp` process, checked from its command line;
   - its `seen` is within `MCP_PRESENCE_MIN` (new, default 30 minutes) of an agenthub tool call. A presence written
     only at `initialize` counts for `CONVERSATION_WAIT_MIN` (5 minutes), as a Claude session does. Only tool calls
     extend it, not the open connection, so a session that doesn't use the mailbox stops counting quickly instead of
     holding letters for half an hour with nobody told.

   `active_sessions` and `recent_session` take these rows as live. That is the one change A3 makes to the dispatcher's
   input.
3. **Presence holds a delegation back, not just for 5 minutes.** A delegation **not yet delivered to any session** to
   a project with a live MCP presence is held while that presence stays live, up to `MCP_HOLD_MAX_MIN` (new, default
   120 minutes from the letter's timestamp). A letter that was already delivered (a hook or an `inbox` call) is under
   the 60-minute seen rule (`take_state`, checked first in dispatch) and is never held again. The status page lists
   held letters as "held: <project> in use", not as unclaimed (`STUCK_HOURS`).
   Then it is dispatched as today. A Claude presence keeps today's 5 minutes: its hook delivers within them, and the
   60-minute seen rule takes over. Holding is safe because the MCP session is told about the letter. The server puts
   the count of open letters in `instructions`, and with A3 it also tells the agent at each agenthub tool call, through
   the tool result: "📬 N letters, call inbox". A sandboxed project's server counts them from its view, as `unread()`
   does. Once the agent calls `inbox`, the 60-minute seen rule applies as for any session.
4. **A synthetic MCP id is never a session to resume or reply to.**
   - If `current_session()` returns a real session id in the server's process (the server inherits its client's
     environment, e.g. Claude Code's), that id is used and no synthetic id is made.
   - An `mcp:` id is never written as `from_session` or `to_session`.
   - `recent_session` skips `mcp:` rows, since there is nothing to fork.
5. **Sandboxed projects.** Their MCP server reads a view, not the bus, so presence goes through the project's outbox as
   a new `presence` operation. The platform's outbox reader checks that it names the outbox's own project, and checks
   the pid as in 2.
6. **Where it doesn't reach.** An MCP client on the remote machine (the laptop) runs its own `agenthub mcp`, whose
   presence never reaches the hub's bus. Behaviour there stays as today, which is safe: nothing is held wrongly.

Codex surfacing MCP server notifications (e.g. `notifications/resources/list_changed`) is not documented, so A3 doesn't
rely on them. The tool-result notice in step 3 works with any client.

## B1: waking a live session (tier 2; the transport for Claude-only users)

A new optional port, `sessions.wake(sid, text) -> bool` (`Unsupported` by default). Dispatch uses it in two places:
- **Conversation, session live**: when the letter is first seen.
- **Delegation, project in use by a live session that has `wake`**: likewise.

In both cases the letter is recorded on the bus as today, then `wake(sid, "📬 1 letter from <project>: <id>. Run
agenthub inbox.")` is called **once per letter**, recorded as a `message.woken` event (`via: wake`). Then the usual
wait applies. The session's own hook delivers the letter and claims it.

- The letter itself never travels through `wake`. Only the notice does.
- If `wake` fails, is held for approval, or is `Unsupported`, today's behaviour is unchanged.
- The status page's mailbox section shows per letter: woken / hook / background copy / dispatched.

**Claude implementation** (`runtime_claude.wake`):
- The SessionStart hook runs with `CLAUDE_CODE_MESSAGING_SOCKET` and `CLAUDE_CODE_MESSAGING_TOKEN` set. Hooks run
  outside the sandbox with the user's authority, so the hook can write platform state. It saves both to
  `secrets_dir()/sessions/<sid>.json` (mode 600). The secrets directory is in every sandboxed session's `deny_read`.
  Neither the bus nor `.state` will do: anyone holding a token can post into that session, sandboxed sessions read
  bus views, and `.state` under the hub root is readable from inside the sandbox. The file is removed at SessionEnd,
  and stale ones are pruned with the presence rows.
  `sessions/` is excluded from the nightly backup: a token only works while its session runs, and there is no reason
  to copy it to the cloud. check-secrets' undeclared-file scan skips the folder.
- `wake` connects, sends the documented auth line, then the message.

Before building, check:
- the message payload format, which isn't documented (read the docs again; if there is still no format, ask Anthropic
  or wait rather than reverse-engineer it);
- what a receiver in a permission-skipping mode does. The notice may sit in an approval dialog. That is acceptable,
  because `wake` is reinforcement, but doctor should say "wake may need approval in sessions that skip permission
  prompts".

The `asyncRewake` hook is the fallback if the socket can't be used. A background hook started at SessionStart polls the
bus for this session's letters and exits 2 when one arrives. That costs a long-lived process per session, so it comes
second. (As built on 11-02, the fallback is what Claude uses, armed on Stop rather than SessionStart: step 2 below.)

**Why B2 (r1) is dropped.** r1 let a sending session nudge the receiving project's session itself. Reviewers found that
it gives other projects session ids and cwds, bypasses the outbox's sender checks for sandboxed projects, and loosens
"across projects, only letters". With B1 the platform does the waking, so B2 is no longer needed. The rules text stays
as it is.

## A1: more than one runtime the user opens sessions with

`runtime.default` stays as the agent the platform starts (headless dispatch, background resume, agent jobs, leases).
The new `runtime.installed: [claude, codex, …]` (default `[runtime.default]`; it and `runtime.bare_ids` are added to
`contracts/hub.v1.schema.json`)
lists every agent the user opens sessions with.

- **Session ids keep today's form.** A bare id means the runtime recorded once in hub.json as `runtime.bare_ids`
  (written the first time A1 runs: the `runtime.default` of that moment, `claude` on every existing instance). It
  doesn't follow later changes to `runtime.default`, so changing the default never reroutes old ids. Every other
  runtime's ids are written `<runtime>:<id>`, and `mcp:` ids are A3's. Existing bus data, `leases.json` keys, delivered-event ids
  (`{id}:delivered:{session}`) and presence keys need no migration. One helper, `runtime_of(sid)`, is used at every
  comparison and call site.
- **Hooks carry their runtime.** The installed hook command passes `--runtime <name>`. `hub-event` and `hubbus hook`
  load that runtime's `lifecycle` for `parse_event` / `inject`, not the default's. `install_lifecycle` runs for each
  installed runtime that has it.
- **Calls about a session go to that session's runtime.** `context_size`, `resume_args` and `alive` are routed by
  `runtime_of(sid)`.
  - Background resume and forks only use sessions whose runtime has the `agent` port.
  - Otherwise the conversation is handled as a delegation, and `recent_session` skips such sessions.
  - `live` is the union over installed runtimes, plus A3's MCP rows.
- **`current_session`**: the nearest ancestor process that belongs to a runtime decides, not the first runtime that
  answers. Otherwise Codex started inside a Claude session would see the inherited `CLAUDE_CODE_SESSION_ID` and record
  the wrong session.
- **Rules files**: one managed block for each installed runtime that has a local rules file. A runtime with none, like
  Codex (below), gets the rules through its SessionStart hook output or MCP `instructions`. Doctor says which.
- **Sandboxed projects**: a runtime's hooks are installed only if that runtime has `control` and `confine`, so its hook
  and config files are in the project's `control_paths` and its sessions are confined. A runtime without them is
  refused in sandboxed projects with the reason (fail closed). Otherwise a sandboxed session could rewrite its own hooks.
- **`presence` gets a `runtime` column**, and doctor's feature table gets one column per installed runtime.

About 30 call sites use `runtime_ports.load()` / `default_name()`. Only the session-serving ones change: hooks, the
sessions ports in dispatch, rules files, transcripts. Headless runs stay on the default runtime.

## Codex adapter (tier 2 example; next version)

Owner, 11-01: after open-sourcing, the agent-hub session writes this adapter's own design, has it cold-reviewed, then
implements it, port by port, saying the rest is unsupported. What the reviews found so far, for that design:

- **lifecycle**: Codex has hooks (SessionStart, UserPromptSubmit, PostToolUse, Stop and more). `additionalContext`
  (and stdout for SessionStart/UserPromptSubmit) "is added as extra developer context". Project hooks live in
  `<repo>/.codex/hooks.json` or `config.toml` and can be kept untracked through `.git/info/exclude`. They load only in a
  trusted project, and trust is per hook hash, so changing a hook command means the user trusts it again.
  sync-local-setup must keep the command stable.
- **instructions**: `AGENTS.override.md` *replaces* `AGENTS.md` (one file per directory), so it can't hold the managed
  block. Use SessionStart `additionalContext` instead, or MCP `instructions`.
- **sessions**: hook input has `session_id`, `transcript_path` and `cwd`. `codex agents` has no machine-readable output.
  The app-server's thread list is experimental.
- **transcripts**: rollout JSONL under `~/.codex/sessions/`. The docs say the format "isn't a stable interface", and a
  migration to SQLite thread history exists. Treat it as fragile and keep it optional.
- **agent / resume**: `codex exec fork <id>` copies a saved session into a new one with no terminal UI, suitable for
  background resume.
- **wake**: `codex queue --thread <id> --message <text>` queues a message for an existing session. Whether it starts a
  turn in an idle session is unknown. `wake_on_agent_messages` is under development.

## Order of work (each step stands on its own)

1. **A3**: MCP presence, holding dispatch back, the tool-result notice. Characterization tests on today's dispatch
   first. **Done 11-01.** As built: `presence` gained the `runtime` and `via` (initialize / tool) columns now, not in
   A1 (the 5- vs 30-minute rule needs `via`; A1 only fills `runtime` for hook rows). The command-line check (this
   platform's `mcp_server.py`) applies to every `mcp:` row, not only to outbox ones. An outbox `presence` entry from a
   server that has exited by intake is dropped without telling the project, not refused (a server restarting would
   otherwise send it a letter each time); a malformed one is refused as usual.
2. **B1**: the `wake` port, its once-per-letter use in dispatch, the delivery column, doctor's row. The Claude socket
   implementation follows if the payload format checks out; otherwise the port ships `Unsupported` with that reason.
   **Done 11-01, Claude's `wake` Unsupported.** The docs were read again on 11-01
   (code.claude.com/docs/en/cross-session-messaging, "The session's inbox socket"; code.claude.com/docs/en/env-vars,
   `CLAUDE_CODE_MESSAGING_SOCKET` / `CLAUDE_CODE_MESSAGING_TOKEN`): they document the socket, the token and the first
   line `{"type":"auth","token":…}` (optional on macOS/Linux, required on Windows), the 30-second limit for a complete
   line, and the own-child delivery rule, but not what a message after the auth line looks like. Nothing was
   reverse-engineered. As built, and where it differs from the text above:
   - `wake` is a port of its own (`PORTS["wake"] = ("wake",)`), not a function added to `sessions`: a port is all or
     nothing, and `sessions` with an optional function would make every runtime without it fail to load. It is in
     `runtime_ports.OPTIONAL`, so `load()` without `ports=` doesn't ask for it.
   - Doctor's row is an information line (`·`), neither a problem nor a reminder, with the runtime's own reason
     (`unsupported(port)`, a new optional adapter function) after the port's name. The "may need approval" line waits
     for the socket `wake` itself.
   - The delegation's wake goes to the live session most recently seen in the project, one per letter, not to every
     active session.
   - Everything around `wake` is built and tested: the SessionStart hook keeps the socket and token
     (`runtime_claude.wake_address`) in `secrets_dir()/sessions/<sid>.json`, SessionEnd and the bridge's prune remove
     them, and the backup and check-secrets skip the folder. Once the format is documented, `runtime_claude.wake` is
     the only piece to add.
   - The `asyncRewake` fallback is documented (code.claude.com/docs/en/hooks: `asyncRewake: true` "runs in the
     background and wakes Claude on exit code 2", even when the session is idle), but not built: the docs also say
     its `timeout` is enforced (600 seconds by default) and a hook process ends when it wakes, so one SessionStart
     poller gives at most one wake within its timeout. It needs a respawn design of its own (re-armed at the end of each
     turn, one poller per session), which no review has seen. Left to the owner.

   **Claude's `wake` built 11-02, as an asyncRewake waiter re-armed at every Stop.** A live spike that day (Claude
   Desktop, its bundled CLI 2.1.309, a throwaway folder) settled what the docs left open:
   1. A Stop hook with `asyncRewake: true` that exits 2 wakes an idle session about 1 s later and starts a turn. Its
      stderr reaches Claude as a system reminder reading `Stop hook blocking error from command "Stop": <stderr>`, so
      the notice names agent-hub and says what to run.
   2. A large timeout holds: with `timeout: 86400`, a waiter armed at 02:06:42 still woke the session at 02:18:39.
   3. A waiter on SessionStart blocks the session's first reply (Desktop waits for SessionStart hooks; 15 minutes in the
      spike). So it is armed only on Stop, at every turn's end, and a new session is wakeable only once its first turn
      has ended.
   4. Each Stop arms a new waiter, so a request any waiter can see again becomes a wake loop (the spike's glob matched
      its own taken files and woke the session about 10 times for one request). A request is taken exactly once.
   5. Desktop runs its own CLI binary (not nvm's `claude`), with `CLAUDE_PID` in the hook environment; nothing here
      looks for the session's process by name.
   6. (The live end-to-end check, 11-02, CLI 2.1.316.) `claude -p` waits for an asyncRewake Stop hook to exit before it
      exits, contrary to the docs' "killed at teardown", and a `-p` run started inside another session inherits that
      session's `CLAUDE_CODE_ENTRYPOINT` (`claude-desktop`, `cli`) rather than getting `sdk-cli`. So whether a session
      is a `-p` run is read from its own process's arguments (`CLAUDE_PID`), the entrypoint is only a second signal, and
      a session whose process can't be read arms nothing. The same check showed the wake itself working: a turn about
      1 s after `wake()`, the request consumed once, a new waiter armed after it, `superseded` for an older one.

   As built (details in mailbox-routing.md, "11-02"), and where it differs from the text above:
   - The waiter is `hub-event --runtime claude wait` → `runtime_claude.wait`, the one hub-event entry point that doesn't
     always exit 0. Its files are in `secrets_dir()/sessions/` beside the addresses: `<sid>.waiter` (newest wins,
     heartbeat by mtime) and `<sid>.wake-<nonce>` requests, taken by an atomic rename to a dot name. It reads no bus:
     the request file is the only signal. It doesn't poll the bus as the text above says; `wake()` writes the request.
   - `wake()` is True only while the session's waiter heartbeats; False otherwise, recorded as `ok: false`.
   - The waiter's liveness is its heartbeat, not `live_sessions()`: a Desktop session started with `--resume=<id>
     --fork-session` runs under a new id that is not on its command line, so `live_sessions()` would miss it and prune
     its record. Dispatch itself asks `live_sessions()` before it wakes (`to_s in live`, `wake_target`) and when it
     holds back (`active_sessions`); that gap, the sessions port's, was closed the same day (below).
   - The socket token is no longer kept (`runtime_claude.wake_address` removed): the waiter needs none.
   - "Woken, not picked up" (the session didn't take the letter within `CONVERSATION_WAIT_MIN` of the wake) is a step on
     the status page, and doctor reminds when it happens twice in 7 days (`hubbus wake-misses`), so a change in how
     Claude Code handles the wake shows the next day.
   - Hooks: each project's at its next session start (sync-local-setup → `hub-event install`), a sandboxed project's by
     render-project-settings (`platform_hooks()`). A worktree session gets the project's hooks only once its
     `.claude/settings.local.json` link exists (worktree-memory.sh `start`); for its first session the user-level
     worktree hooks carry a Stop waiter too (`merge-hook-settings.py --worktree`, `worktree-memory.sh wait`), which,
     like every user-level change, is installed only when the owner says so.

   **Forked sessions in `live_sessions()` (11-02).** A session started as a fork (Desktop's `--resume=<old>
   --fork-session`, its branch action, `/branch` in the terminal) runs under a new id that is on no command line, so
   dispatch never woke it and could dispatch a background agent while the owner was in it. Every hook already knows the
   real id (its input) and the session's process (the runtime's new optional `session_pid(env)`; Claude: `CLAUDE_PID`,
   Desktop's CLI or the terminal's, never looked for by name). As built:
   - hub-event's `session_record` handler (last on `session_start`, `prompt`, `tool_used`; only in a project, never
     for platform runs) writes `<session>.live` = `{runtime, pid, start, cwd}` through `runtime_ports.record_session`,
     tmp + rename, mode 600. At `session_start` it is always rewritten (a resumed session has a new process); after
     that only when the pid differs, so a tool call costs no process lookup. A headless run gets none, by the waiter's
     rules (the adapter's optional `person_session(pid, env)`: not an SDK entrypoint, not `-p` among the process's own
     arguments, arguments readable), asked only when it would write: a person isn't in it, so letters shouldn't wait
     for it.
   - Where: `secrets_dir()/sessions/`, beside the waiter and its requests, not the bus `presence` table. It is written
     by the hook outside any sandbox (`session_record` is not in `AS_PROJECT`), lies in every sandbox's `deny_read`
     and outside its `allowWrite`, is not backed up, and goes at `SessionEnd` with the session's other files
     (`hubconf.session_files`). The presence table also takes rows from sandboxed projects' outboxes (MCP presence),
     and `live_sessions()` is the runtime's port, which shouldn't read the bus schema.
   - Identity: the pid with its start time, the rule MCP presence already used; `hubbus.proc_start` moved to
     `runtime_ports.process_start(s)` (one `ps` for all records) and the two share it. A dead pid or another start
     time (a reused pid) doesn't count.
   - One process runs one session: writing a record drops any other naming the same process. So whichever id a fork's
     `SessionStart` carries (its `source` is `fork`; the CLI's hook schema has the value, but which id it carries
     wasn't observed: no live session was started for this), the next prompt or tool hook with the new id replaces it.
   - `runtime_claude.live_sessions()` = the command-line ids (unchanged) ∪ `runtime_ports.recorded_sessions("claude")`;
     dispatch (`wake_target`, `active_sessions`, `recent_session`, the conversation's `to_s in live`) needed no change.
   - `prune_wake_addresses` removes a record when its process is gone, however long the session has been idle; never
     by age or by `live` (which reads the records).

   Left: whether Claude Code picks up hooks from a settings file that appears mid-session (the worktree link); whether
   a woken session in a permission-skipping mode behaves the same; a session idle longer than the timeout (a day, 14 days
   since docs/design/mailbox-session-first.md step 3) isn't
   wakeable until its next turn; a fork is live only from its first hook (a session that ran none since this shipped
   is still missed); a fork's command line still reads as its parent (`--resume=<old> --fork-session` puts the old id
   in `live`, as before: that process runs another id; left as it was because the platform's own background resumes
   run that way, and dropping it would change how a second letter to that session waits).
3. **A1**: `runtime.installed`, runtime-routed calls, hooks carrying their runtime, the presence column, doctor's
   columns. **Done 11-01.** Characterization tests of a Claude-only machine first (`tests/test_runtime_installed.py`
   ClaudeOnlyToday, through the real Claude adapter); with `runtime.installed` absent or `[claude]` every golden is
   unchanged. The only visible difference there: the installed hook command gains `--runtime claude` (each project's
   hook settings are rewritten once at its next session start, a sandboxed project's by render-project-settings), and
   hook presence rows now carry `runtime`. As built, and where it differs from the text above:
   - `runtime.bare_ids` is not written by the platform. Unset it means `claude` (`runtime_ports.DEFAULT`), not "the
     `runtime.default` of the moment A1 first runs": every bare id on any bus was written by the Claude adapter (the
     only runtime with hooks before A1), so the two are the same on every existing instance, and the constant needs no
     first-run write into hub.json (no applier, no commit) and cannot drift with `runtime.default`. Setting it in
     hub.json still pins it.
   - Background resume and forks use only sessions of `runtime.default` (when it has the `agent` port), not of any
     runtime with the port: they run the `mailbox-conversation` point, which runs on `runtime.default` and can't be
     pinned elsewhere (`RUNTIME_ARG_POINTS`). Resuming another runtime's session comes with that runtime's adapter.
   - `current_session` with several installed runtimes asks each adapter's new optional `session_process(command)`
     about the ancestor processes; with one, it reads the environment as before.
   - Doctor's per-runtime columns appear only with more than one installed runtime.
   - Not moved: the older
     `hubbus hook` entry points without `--runtime` (worktree-memory.sh) parse as `runtime.default`. (The transcript
     statistics moved off runtimes entirely on 11-03: `bin/sessions.py` reads every agent's sessions.)
4. **Codex adapter**: next version (above).

v0.2.0: steps 1–3. If the release has to be tight, step 1 alone is the floor. It stops dispatch from colliding with
MCP sessions that use the mailbox. A session that never calls an agenthub tool stays invisible until tier 2.

## Open questions

- `MCP_PRESENCE_MIN` (30) and `MCP_HOLD_MAX_MIN` (120): tune after a week of real use. The status page shows how often a
  letter was held and then dispatched anyway.
- With two live sessions of different runtimes in one project, a delegation waits for any of them. The first hook or
  `inbox` that takes the letter claims it.
- Should `runtime.default` become per project (a Codex-only project gets Codex dispatch)? Left out here. It changes who
  the platform starts, not who it serves.
