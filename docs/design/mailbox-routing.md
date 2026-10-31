# Project mailbox: routed and handled mechanically by the platform

> **Status**: confirmed by the owner and implemented on 10-24 (first step: the notes capability).
> `mailbox.handle: work` is not done yet.

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
- Channel-lease sessions (read-only deep talks) still do not count as recipients.

## What changes for agents and the owner

- Agents: no new rules. CLAUDE.md only changes "unclaimed for 12 hours gets escalated" to "after sending, the platform
  handles it; no need to wait for the other side to be open".
- The owner: only gets questions in the assistant that really need his decision; the handling record is on the status
  page.

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
  does not handle that either; the status page lists letters "delivered over a day ago and not closed".

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
