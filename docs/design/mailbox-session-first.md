# Mail goes to a session first

Status: design r2, not built. r2 follows two cold reviews of r1 (one against the code, one on safety and on what the tool
documents) and the owner's decision on permissions. Replaces the delegation part of the routing in
`docs/design/mailbox-routing.md`; builds on the wake in `docs/design/mailbox-runtimes.md` (B1).

## The problem

The owner's rule: a request or question goes first to a session that already exists and has context. If that session is
idle, it is woken. A headless agent is the last resort, and it must not take a letter away quietly.

What happens today, from one real case. A request arrived at 16:56:42. Two sessions of the project were open, one used 12
minutes earlier. At 16:56:59 a headless handler was dispatched and no session was woken. The handler can't change code or
read other projects, so it could only ask the owner and write status letters. The letter was recorded as "the platform is
handling it", so hooks stopped treating it as work. The sender got four status letters over the next hours, two of them
corrections: the handler said it had written a memory note, and the write had been denied. One of those runs had been
started for other letters and found this one in `brunna inbox`.

Why, in `hubbus dispatch`:

1. A delegation wakes a session only if `active_sessions` has it: a hook within `CONVERSATION_WAIT_MIN` (5 minutes). A
   session that has really gone idle isn't woken.
2. A background fork needs activity within 24 hours and a context within `RESUME_MAX_TOKENS` (150k). Long-lived sessions
   are far over it (566k and 825k here).
3. Everything else goes to the handler at once.
4. The waiter that makes a wake possible leaves after 24 hours and can't re-arm without a turn. At the time of writing,
   3 or 4 of 12 open sessions had one.

When a waiter is alive the wake works for requests too: one request was woken, seen 2 seconds later, acked after 12 and
closed in under 3 minutes.

## The owner's decision: a woken session keeps its permissions

A wake starts a turn from a letter, in a session with its saved permissions and maybe nobody watching. Any project that can
write the bus can cause one, and text a project took from outside can reach it. The reviewers asked for a confined turn or a
per-project opt-in; the owner chose (11-07): **a session keeps whatever permissions it has.** No mode check, no confined
turn, no trust list.

What this means, so it is on record:

- This is a line the platform doesn't guarantee. The rules tell the session that letters are data from other agents and
  that anything irreversible or external waits for the owner; that is advice. The risk is accepted, including a letter
  reaching a session that skips prompts.
- A session in a mode that prompts (Manual) stops at the first dialog until the owner returns, since the platform adds no
  rules for it. That is what the mode means. It holds the letter until the seen limit below.
- A sender's name isn't authenticated for unsandboxed projects, and it decides nothing here.
- Unknown, not probed: whether auto mode's classifier counts hook-delivered text as user intent.

There is one off switch: `mailbox.wake: false` in `brunna.json`, or in a project's registry entry (a security field,
which `manifest.update` can't change). Off means no wake for that project; its mail goes to triage.

## What changes

For a request or question nobody has taken, in this order:

1. **An MCP session uses the project**: held as today, up to `MCP_HOLD_MAX_MIN` (design A3 stays).
2. **A session was active in the last 5 minutes but has no waiter** (in its first turn, or mid-turn after a wake): wait, as
   today. Its hook delivers the letter.
3. **Candidates exist**: pick one (below). If it is idle, wake it. If it is busy, don't wake it: its hook delivers.
4. **The picked session has its window** (below). `brunna pass <id>` or no answer moves to the next candidate. Each session
   is tried once per letter.
5. **None left**: triage. A project with the wake port never uses the delegation fork. Runtimes without the port keep the
   fork, then triage. `runtime.default: none` has neither: letters stay in the mailbox, as today.

A reply to a session (`to_session`) follows the same order, with that session first if it is among the recipient project's
candidates; a `to_session` outside them is ignored. If that session isn't running and its context is small, today's
background resume applies: it is the only way to reach a closed session.

### Candidates

Presence rows of the project ∩ `live_sessions()` ∩ `can_wake(sid)` (optional sessions port; Claude: the waiter's heartbeat
is fresh). Never MCP rows, lease sessions or the platform's own runs. A session belongs to a project by where its process
record says it is (`.live` cwd), so a session that wandered into another project's folder isn't that project's candidate.

### How long a session can wait

The waiter's hook timeout goes from 1 day to 14 days (`WAIT_TIMEOUT_S`). That needs a live check: a timer that overflows
could fire at once, so 14 days stays well under Node's 24.8-day timer limit, and the first armed waiter is watched before
this ships. A session idle longer isn't a candidate; the status page counts open sessions with no waiter and says why, so
this is never silent. Doctor gets a row for the ports a runtime lacks.

### Picking one

1. One candidate: that one.
2. The `to_session`, or the session that last acted in this thread.
3. Otherwise judgment point `mailbox-route`: `shape: completion`, `model_group: fast`, a JSON schema with one field,
   `session`. The platform checks that it is in the list. **No free text from the model is kept anywhere:** there is no
   reason field, because a crafted letter could have the model copy a last reply into it and read it back with `brunna show`.
4. The model fails, times out, answers outside the list, or none is set up: the most recently active candidate.

The call has a 15-second timeout and the dispatch tick spends at most 30 seconds on them, then falls back to step 4: the
dispatch loop is serial and also carries every project's mail and the sandboxed outboxes. It is not recorded for evals.
What goes on the bus is `message.routed`: `{session, by: only|thread|model|recent, round}`.

What the model reads, all marked as data: the letter's sender, type and first 1000 characters, and for each candidate its
id, branch or worktree name, idle time, plus the gated text below.

### The last reply

The owner's decision: the model may read each candidate's last reply, since metadata says little about a session in the
main checkout. The text (the last reply, and the first lines of letters it sent and its merge reports) goes only when:

- the project's `privacy.reflection` is `include` (the owner's choice of gate, 11-07; that label was not written with this
  in mind), and
- the point resolves to the runtime that session already uses, with no `llm.points` override to another provider.

Otherwise that candidate goes with metadata only. `.last` holds the last 500 characters of `last_assistant_message`, and
isn't written at all for a project whose label isn't `include`. `session_file` learns the suffix. The prune keeps `.last`
while the session's process record is alive (today it would delete it after 10 minutes), and removes it when the session
ends. Nothing about it reaches the bus.

### The window

- **Not seen within `WAKE_PICKUP_MIN` (5 minutes)**: next candidate.
- **Seen by the picked session**: it holds the letter until `TAKE_TIMEOUT_MIN` (60 minutes), so a session working for 20
  minutes isn't pre-empted. Then next candidate.
- **Acked, replied or done**: taken. The wake notice says so: `brunna ack <id>` if it's yours, `brunna pass <id>` if it isn't.
- **`pass`**: its own event, `message.passed`, never a `message.delivered`.

While a letter is routed to one session, other sessions' hooks don't announce it as new. `brunna inbox` lists it with
who was asked, and listing it there doesn't mark it delivered. Anyone may still ack it: a person talking to another session
who wants it just takes it. `message.routed` to a session lifts the `REPLY_WAIT_MIN` filter for that session, so the next
candidate of a `to_session` letter can see it.

### The wake

`wake()` builds the notice from the validated sender and id only, and drops either if it doesn't have the expected shape.
The waiter prints it as it is. One wake per session per tick, covering all its letters, counted as one; the event id
becomes `<id>:woken:<sid>`.

Is the session busy? `session_status(sid)` (optional port; Claude: `claude agents --json`) answers `busy|idle|unknown`, and
unknown counts as idle. Whether Desktop sessions appear in that list is checked before this is relied on.

### Limits

`daily_wakes_per_project` (default 12) and `daily_wakes_per_sender` (6 per sender per recipient), so one project can't spend
another's budget. Budget used up: no more wakes today, no triage for it either. The letter stays open and waits for a hook,
and the status page says so. `thread_max_rounds` doesn't count wakes. A separate `thread_max_wakes` stops waking in a
thread, and the letter stays open for hooks: nothing is held and no question goes to the owner, since a person may be in
that conversation. A woken session that omits `--reply-to` starts a new thread, so the daily limits are the real bound.

### Triage: the headless handler as last resort

- **It takes only what it finishes.** A new `via: triage`. `INBOX_SQL`'s `delivered` excludes it, so hooks treat the letter
  as new; it joins `SEEN_VIA`, so `take_state` isn't "taken". `claimed` doesn't list it. `run_handler` enforces once per
  letter. The status page's `stuck` shows these as "waiting for a session", without the hourly alert.
- **Readers of `via` are updated together**: `runs_today` and `platform_rounds` count it, `stale_resumes` excludes it,
  `PATH_OF` and `path_labels` name it.
- **A session and a handler can't both answer.** A confined `send` or `done` for a letter a session has taken is refused.
- **It reads only its own letters.** The inbox view of a handler run lists the letters dispatched to it.
- **The boundary is the platform's, not the prompt's.** `confined_refusal` allows only: `done` replying to one of its own
  letters, a question to the owner, and closing its own letters. No other recipient and no `info`. This also closes a
  way around 11-02: a confined run could otherwise send to any project and so wake a session. `mailbox.handler: propose`
  follows the same rule: `hold_and_ask` no longer claims, and `mailbox-propose.md` changes.
- **The sender gets one fixed line, from the platform, once**: received, waiting for a session. No `ref` and no `to_session`,
  so it neither marks the letter taken nor wakes anyone. None for the owner side, the hub's own letters or reconciliation
  letters.
- **It can write its notes.** The confined run's `deny_write` covers the hub's own project, so `platform_settings`' `keep`
  and `write_except` carve out its `.agent-memory/memory/`. The prompt says: state an action only after its tool call
  succeeded.
- **No repeats and no noise.** A triaged letter isn't retried or logged every tick.

## Sandboxed projects

`pass` joins the outbox's operations (`take()`, `mail_command`), `MAIL_SUBCOMMANDS`, `SESSION_SUBCOMMANDS`, `SESSION_COMMANDS`
and the help goldens. `VIEW_SQL` gains a yes/no "another session was asked", with no session id or other text. Wakes run
outside the sandbox and touch only the secrets sessions folder.

## What else reads this

- `contracts/hubbus.v1.schema.json`: `message.routed` and `message.passed` join the enum; `contracts-check` and
  `registry.py` (`trigger.events`) read it. `prune` removes these rows with the letter.
- `wake-misses` and doctor: a pass isn't a miss. "Picked up" means one thing everywhere: ack, reply, done, or a delivery seen
  by the picked session.
- `letter_path` and the status page show routed, passed and triage.
- `brunna pass` is also a tool of the MCP server.

## A gap found on the way, separate from this

`evals_dir` isn't in `policy.deny_read`, so a sandboxed session could read recorded judgment-point inputs. This is live
today; it goes in first as its own fix.

## Tests

Characterization of today's routing first, committed before any change. Then: an idle live session is woken for a request,
whatever its context size; the order (MCP hold, active without waiter, candidates, triage); pick order; a model answer
outside the list, a timeout, the tick budget, no model; nothing the model wrote reaches the bus; the window's three cases;
`pass`; another session's listing doesn't mark a held letter; an ack from another session wins; `to_session` outside the
candidates; a session wandered into another project; the off switch; limits, including budget used up; `.last` survives an
idle prune and is gone at session end; the gates on text; triage leaves a letter new for hooks and isn't repeated; the
confined `send` rules, both handlers; the fixed line neither takes nor wakes; `pass` through the outbox; goldens in both
languages; a live run in Desktop before merge, including a 14-day waiter that doesn't fire early.

## Steps, each standing on its own

1. The `evals_dir` fix.
2. Triage: letters stop being taken quietly, the confined `send` rules, the fixed line, notes writable. This alone fixes the
   false reports, and it goes first because "no candidates" must already be honest when routing starts.
3. The session record: the waiter's lifetime, `.last`, `can_wake`, `session_status`, the prune rule, doctor's row.
4. Routing without a model: order, pick by thread then recency, window, `pass`, hold, limits, off switch.
5. `mailbox-route`.
