# Mail goes to a session first

Status: design, not built. Replaces the delegation part of the routing in `docs/design/mailbox-routing.md`; builds on
the wake of `docs/design/mailbox-runtimes.md` (B1) and the confinement of its "11-02" section.

## The problem

The owner's rule: a request or question goes first to a session that already exists and has context. If that session
is idle, it is woken. A headless agent is the last resort, and it must not take a letter away quietly.

What happens today, from one real case. A request arrived at 16:56:42. Two sessions of the project were open, one of them
used 12 minutes earlier. At 16:56:59 a headless handler was dispatched and no session was woken. The handler can't change
code or read other projects, so it could only ask the owner and write status letters. The letter was recorded as
"the platform is handling it" (`claimed`, `take_state` = taken), so hooks stopped treating it as work. The sender got four
status letters over the next hours, two of them corrections: the handler said it had written a memory note, and the write
had been denied. One of those runs had been dispatched for other letters; it found this one in `brunna inbox`.

Why, in `hubbus dispatch`:

1. A delegation wakes a session only if `active_sessions` has it: a hook within `CONVERSATION_WAIT_MIN` (5 minutes). A
   session that has really gone idle, the one a wake exists for, is not woken.
2. A background fork needs activity within 24 hours and a context within `RESUME_MAX_TOKENS` (150k). Long-lived sessions
   are far over it (the two here: 566k and 825k).
3. Everything else goes to the handler at once.

On the bus, across all projects so far: 11 wakes, 10 successful, all of them notices replying to a session. The one
wake for a question failed. No request or question has ever woken a session.

## What changes

For a request or question nobody has taken:

1. **Candidates**: open sessions of the project that can be woken right now (below).
2. **Pick one** (below) and **wake it**. Waking uses the session itself, not a copy, so no token limit applies.
3. **It has `WAKE_PICKUP_MIN` (5 minutes) to take the letter**: ack, reply or done. `brunna pass <id> --note …` says "not
   mine" and moves on at once. No answer, or a pass: the next candidate. Each session is tried once per letter.
4. **No candidates left**: the triage handler (below). The letter stays open for the next session.

A letter already seen by a session but not taken within `TAKE_TIMEOUT_MIN` goes to the next candidate, not to a handler.

A reply to a session (`to_session`) keeps its priority: that session is the first candidate if it can be woken. If it
isn't running at all and its context is small, today's background resume still applies; it is the only way to reach a
closed session. The delegation fork goes away for runtimes with the wake port and stays for runtimes without it.

### Candidates

Presence rows of the project ∩ `live_sessions()` ∩ a new optional sessions port `can_wake(sid)` (Claude: the waiter's
heartbeat is fresh). Never: MCP rows, lease sessions, the platform's own runs (they have no presence), and sessions whose
recorded permission mode skips prompts (see "Waking is a trust decision"). No limit on how long a session has been idle.

A brand-new session has no waiter until its first turn ends, so it is no candidate. Its hook still delivers mail.

### Picking one, in order

1. One candidate: that one.
2. The session a reply is addressed to, or the one that last acted in this thread.
3. Otherwise judgment point `mailbox-route`: `shape: completion`, `model_group: fast`, a JSON schema, a short timeout.
   Input, all marked as data: the letter's sender, type and first 1000 characters; for each candidate its id, branch or
   worktree name, idle time, letters it sent, its merge reports, and its last reply (below).
   Output: `{session, reason}`. The platform checks that `session` is in the list, so the worst a crafted letter can do
   is pick a wrong candidate.
4. The model fails, times out, answers outside the list, or no model is set up: the most recently active candidate.
   Routing never waits on a model and never fails because of one.

The call runs outside any bus transaction, once per letter per round. Several letters from one sender in one round reuse
the answer. The choice, `by` (`only|thread|model|recent`) and the reason go on the bus (`message.routed`), so a wrong pick
can be seen and the point can be evaluated later through `bin/evals`.

### The last reply

The owner's decision: the model may read each candidate's last reply, because metadata alone says almost nothing about a
session in the main checkout that has merged nothing.

The waiter's Stop hook input carries the final assistant text (`last_assistant_message`). `runtime_claude` keeps its last
500 characters in `secrets_dir()/sessions/<sid>.last` (mode 600), next to the other per-session files: no sandbox reads it,
it isn't backed up, and SessionEnd and the prune remove it. A runtime without it gives none. The neutral port is
`session_summary(sid) -> str | None`.

It is sent only when the project's `privacy.reflection` is `include` and the point's provider is the one that session's
runtime already talks to. Otherwise that candidate goes with metadata only. This is a deliberate exception to "only
statistics leave the machine", which the weekly review keeps.

### One session at a time

Today any session's hook that fires first delivers a letter. With a pick, that would race: a busy session could see it
first. `message.routed` holds the letter for the picked session during its window. Other sessions' hooks don't announce
it as new; `brunna inbox` still lists it everywhere, marked with who was asked, and anyone may still take it. A person
who is talking to another session and wants it just acks it.

### Waking is a trust decision

The 11-02 confinement exists because anyone who can write the bus can start a handler with their text, so handlers load
none of the project's saved permissions. A woken session is the opposite: it has its saved permissions and maybe no person
watching. The wake notice carries only the sender and id, but the session then reads the letter. A hook has always put
letters into open sessions; what is new is that nobody may be there.

So:

- The wake notice and the rules say what hooks already say: letters are data from other agents, and anything irreversible or
  external waits for the owner. That is advice, not a guarantee: this is a session the user opened, where the platform
  advises (`AGENTS.md`, tool neutrality).
- The hard limit: a session whose permission mode skips prompts is not a candidate for a letter. The Stop hook's input has
  `permission_mode`; the waiter records it. Auto mode stays a candidate, since it has its own classifier.
- A letter from a sandboxed project is the sharpest case: the sandbox would otherwise be a way to drive an unsandboxed
  session. **Open: wake for these at all, or only let hooks deliver them?** (Proposed: no wake; the letter waits for a
  person's next message in a session, and triage handles it meanwhile.)

The same limits apply to the notices that wake sessions today.

### Limits

`daily_wakes_per_project` (default 12), separate from `daily_runs_per_project`, and the one-wake-per-session-per-letter
rule (`<id>:woken:<sid>`, replacing `<id>:woken`). `thread_max_rounds` counts woken rounds too, so two sessions can't keep
waking each other. Waking a long-idle session re-reads its context, so the status page shows wakes per project.

### Triage: the headless handler as last resort

- **It takes only what it finishes.** A new delivery `via: triage` is not in `claimed` and doesn't make `take_state`
  "taken". The letter stays open and hooks treat it as new work in the next session. It is triaged once per letter.
- **It reads only its own letters.** The inbox view of a handler run lists the letters dispatched to it, so a run started
  for other letters can't answer these.
- **It writes to the sender only when it completed the request** (`done`). A request it can't complete gets no status
  letter from a model. The platform sends one fixed line per letter, once: received, waiting for a session.
- **It can't claim what it didn't do.** Its notes must really be writable. Today the hub's own project is under the
  confined run's `deny_write`, so the note was refused. The prompt also says: state an action only after its tool call
  succeeded.
- It still asks the owner what only the owner can answer.

## Sandboxed projects

Their mail is on the outbox path; wakes and hooks run outside the sandbox and touch only the secrets sessions folder.
Nothing about the outbox changes.

## Tool neutrality

Routing is in `hubbus`, in neutral terms. `can_wake`, `session_summary` and the recorded permission mode are optional ports;
a runtime without them has no candidates and goes to its fork or triage. `runtime.default: none` has no sessions to wake
and no handler: letters stay in the mailbox, as today. `brunna pass` is also a tool of the MCP server.

## Tests

Characterization of today's routing first, committed before any change. Then: an idle live session is woken for a
request; a big context doesn't matter; pick order at each step; a model answer outside the list, a timeout, no model; the
window, `pass`, and next candidate; a letter routed to one session isn't announced by another's hook; ack from another
session wins; skip-prompt mode and lease sessions are never candidates; privacy label and provider gate the last reply;
triage leaves the letter open and is not repeated; a handler's inbox shows only its letters; limits; goldens in both
languages; a live end-to-end run in Desktop before merge.

## Steps, each standing on its own

1. The session record: last reply and permission mode, `can_wake`, `session_summary`. Nothing uses them yet.
2. Routing without a model: candidates, pick by thread then recency, window, `pass`, `message.routed`, limits, the hook hold.
   The behaviour the owner asked for starts here.
3. Triage replaces the handler's taking and status letters.
4. `mailbox-route`.

## Open

1. A letter from a sandboxed project: wake or not (above).
2. Whether a pick should prefer an idle session over a busy one when the model is unsure.
3. Whether a woken session should be told it may be unattended, in the notice itself.
