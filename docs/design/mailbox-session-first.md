# Mail goes to a session first

Status: design r3, not built. r2 fixed r1's review findings on top of the owner's decision; r3 fixes r2's. The main change
in r3: delivery becomes per session, and a route has an end. Replaces the delegation part of the routing in
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

1. A delegation wakes a session only if it had a hook within `CONVERSATION_WAIT_MIN` (5 minutes). A session that has
   really gone idle isn't woken.
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

So this is a line the platform doesn't guarantee. The rules tell the session that letters are data from other agents and
that anything irreversible or external waits for the owner; that is advice. The risk is accepted, including a letter
reaching a session that skips prompts. Sender names aren't authenticated for unsandboxed projects and decide nothing about
permissions. Not probed: whether auto mode's classifier counts hook-delivered text as user intent.

One off switch: `mailbox.wake: false` in `brunna.json` or in a project's registry entry (a security field,
`manifest.update` can't change it). It covers every wake, notices included.

## The letter's state

Delivery today is one flag per letter (`INBOX_SQL` `delivered`): once any session has seen a letter, no other hook shows it.
That can't express "the next session should see it", so for requests and questions state becomes:

- **Taken**: an ack, a reply from the receiving project, or done, by anyone. `held` and `triage` are not taken. A `resume`
  is taken only until a later `triage` row (a stale fork gives it up). Old `handler` rows stay taken, so letters from before
  this change don't come back.
- **Told**, per session: a `message.delivered` row with that session. A session is told a letter once.
- **Routed**: the latest `message.routed` whose window hasn't ended. It ends by time, by pass, or by the letter being taken.
  **A route always ends**, and then nothing holds the letter.

**A hook shows a letter** to a session when it is open and not taken, that session hasn't been told, and no route points at
another session. The same rule serves a session opening later, and a letter nobody could be routed to.

**`brunna inbox`** lists every open letter, marked with who was asked and whether it is taken. It marks told only for the
session listing it, and not at all for a platform run (`HUB_LLM_POINT` set: the handler's first command must not count).

**The window** of a route: 5 minutes (`WAKE_PICKUP_MIN`) if the routed session hasn't been told, up to 60
(`TAKE_TIMEOUT_MIN`) once it has. So a session working for 20 minutes isn't pre-empted, and a session that never got it isn't
waited on. A session is told to ack first: the footer and the wake notice now say `brunna ack <id>` as soon as you take it,
`brunna pass <id>` if it isn't yours. Someone else's ack ends the matter; the inbox shows it as taken.

Notices (info, done, progress) keep today's one-flag behaviour: nothing routes them.

## Each tick, for a request or question that is open and not taken

1. **Routed**: leave it.
2. **An MCP session uses the project**: held as today, up to `MCP_HOLD_MAX_MIN`.
3. **A session had a hook in the last 5 minutes but has no candidate state** (first turn, or mid-turn after a wake): wait.
   Its hook shows the letter.
4. **A candidate not yet tried exists** and the wake budget has room: pick one, route it. If `idle`, wake it. If `busy`, don't:
   its hook shows it. A letter older than 7 days isn't routed; hooks still show it.
5. **The budget is used up**: no route and no triage. The letter stays open and the status page says why. Hooks show it.
6. **A session's runtime has no wake port** (`runtime_ports.Sessions` routes by each session's runtime): today's fork, if its
   context is small, for that session only.
7. **Otherwise, not yet triaged**: triage.
8. **Otherwise**: nothing. It waits for a hook or for a new candidate.

A reply (`to_session`) follows the same steps, with that session first if it is a candidate of the recipient project. Any
other `to_session` is ignored for routing. If that session isn't running, its context is small and the sender isn't a
sandboxed project, today's background resume applies: it is the only way to reach a closed session, and a fork's whole
context mustn't answer a sandboxed sender. `message.routed` lifts the `REPLY_WAIT_MIN` filter for the session it names.

When a candidate appears after triage (a session opens, its first turn ends), the letter is routed to it: the owner's rule
applies. That's what the 7-day limit and the budget bound.

## Candidates

A session whose process record (`.live`) names this project ∩ `live_sessions()` ∩ `can_wake(sid)` (Claude: the waiter's
heartbeat is fresh), not yet routed this letter. Never MCP rows, lease sessions or the platform's own runs.

`.live` gains `project`, set when the process is first recorded and kept for that process, so a compaction after wandering
into another folder doesn't move it. Which project is `project_at`/`_hub` at that moment. Mail shown by cwd stays as it is;
telling is per session now, so a wandered session seeing a letter no longer hides it from anyone.

`session_status(sid)` (optional port) answers `busy|idle|waiting|unknown`; Claude: `claude agents --json`, checked live: it lists
Desktop sessions and starts no daemon. `waiting` means a dialog is open, so that session is skipped this tick and isn't
counted as tried. `unknown` counts as idle. A runtime with `wake` must have `can_wake` (a contract test).

## How long a session can wait

The waiter's hook timeout goes from 1 day to 14 days (`WAIT_TIMEOUT_S`). The docs state no maximum, and the CLI's timer limit
is unverified, so the first armed waiter is watched before this ships: that shows it doesn't fire early, not that it holds
14 days. A session idle longer isn't a candidate. The status page counts open sessions with no waiter, with the reason, and
doctor gets a row for ports a runtime lacks.

## Picking one

1. One candidate: that one.
2. The `to_session`, or the session that last acted in this thread.
3. Otherwise `mailbox-route`.
4. No answer from it: the most recently active.

**`mailbox-route` never runs inside the dispatch loop**, which is serial and carries every project's mail. Dispatch starts it
in the background (like `run_handler`'s propose path, in its own process group) and writes nothing to the bus. The next tick
reads the answer, or falls to recent if there is none after 2 ticks. Rereading on the next tick also closes the race with a
hook that showed the letter meanwhile.

`shape: completion`, `model_group: fast`, `on_failure: silent`, new: no `job.failed`, since failure text can echo model output.
No retries. Its schema is one string, `session`, with no `enum` or `pattern`, so a validation error can't echo a value. The
platform checks the answer against the list and writes only a matching id, to a mode-600 file in the secrets sessions folder.
Not recorded for evals. **No text from the model is kept:** a crafted letter could otherwise have it copy a last reply and read
it back with `brunna show`. The bus gets `message.routed` with `{session, by: only|thread|model|recent, window}`.

The model reads, marked as data: the letter's sender, type and first 1000 characters, and for each candidate its id, worktree
name from `.live`, idle time, and the gated text below. No branch lookup (that would need a `project_git` op and time).

### The last reply

The owner's decision: the model may read each candidate's last reply. `.last` holds the last 500 characters of the Stop
hook's `last_assistant_message`, when present. It lives in the adapter: `hub-event wait` passes the runtime's `wait` a neutral
`keep_last` flag, computed from the project's label, and `session_summary(sid)` is the port. `session_file` learns the suffix. The
prune keeps it while the session's process record is alive and removes it at the end.

Text (the last reply, and the first lines of letters that session sent) goes only when the project's `privacy.reflection` is
`include` (the owner's choice of gate, 11-07; that label wasn't written with this in mind) and the point resolves to the runtime
that session uses, with no `llm.points` override. Otherwise metadata only.

## The wake

All wakes go through `wake()`, notices included: the off switch, the budget and `<id>:woken:<sid>` apply to all. It builds the
notice from a validated sender and id, and drops either if malformed, because `--id` is free for unsandboxed senders. One wake per
session per tick, covering all its letters, counted as one. On a tick, views are written before wakes, so a sandboxed session's
`brunna inbox` is current.

`daily_wakes_per_project` (default 12) is the bound; a thread can't bound it because a woken session may omit `--reply-to`.
Sender names can be spoofed by unsandboxed projects, so there's no per-sender limit: a project can burn another's budget, and
then mail waits for hooks, which loses nothing.

A session in a prompting mode stops at the first dialog. Its status becomes `waiting`, and it holds nothing: its window ends
at 5 minutes unless told. The dialog stays for the owner.

## `pass`

Only by the session the letter is routed to; refused otherwise, and refused to confined runs (not in `platform_cli(mail_only)`).
Its own event, `message.passed`. Added to `WRITES`, `MAIL_COMMANDS`, `SESSION_SUBCOMMANDS`, `SESSION_COMMANDS`, the outbox's
operations (`take()`, `mail_command`), the MCP server and the help goldens. A sandboxed view gains who was asked (session ids of
its own project are already there), so the routed session can tell it's itself.

## Triage: the headless handler as last resort

- **It takes only what it finishes.** `via: triage`, not taken, so the state rules above apply. `run_handler` enforces once per
  letter. `runs_today` and `platform_rounds` count it, `stale_resumes` excludes it, `PATH_OF` and `path_labels` name it.
- **`hold()` stops platform runs without taking.** At `thread_max_rounds` it asks the owner once and the letter stays open for
  sessions. `held` is no longer taken, and routing goes on.
- **Its own rules, not all confined runs'.** `run_handler` passes `MAILBOX_LETTERS=<ids>`. When that's set, `confined_refusal`
  allows only: `done` replying to one of those letters, `done <id>` for them, a question to the owner, and `done|drop --question`
  to the owner (reconciliation needs it). No `info`, no other recipient, no `ack`, no `pass`. A confined run could otherwise send
  to any project and wake a session. The other `settings: platform` points (fork, event and clock jobs) keep today's rules.
- **A session and a handler don't both answer.** Refused too, if another session has taken the letter. The inbox and `show` of
  a run with `MAILBOX_LETTERS` are limited to those letters.
- **`mailbox.handler: propose` follows the same rules**: no claim, `_mailbox` accepts `done` and owner questions only, rechecks
  taken at apply time, and a `propose-failed` question closes with its letter. `mailbox.md` and `mailbox-propose.md` change.
- **The sender gets one fixed line from the platform, once**: received, waiting for a session. No `ref`, no `to_session`, so it
  neither takes the letter nor wakes anyone. It carries the letter's chain, so an event job can't loop on it. None for the owner
  side, the hub's own letters or reconciliation letters. When the budget stops routing, the same line goes out.
- **It can write its notes.** Today the confined run's `deny_write` covers the hub's own project, and `own_state_dir` only carves
  out `<state>/agent-jobs/<job>`. It gains the project's `.agent-memory/memory/`. The hub's notes aren't committed (`memory: false`),
  so the prompt stops saying the platform commits them there. The prompt also says: state an action only after its tool succeeded.
- **No repeats and no noise.** `take_state`'s "treating it as not taken" log fires once per letter.

## What else reads this

- `contracts/hubbus.v1.schema.json`: `message.routed`, `message.passed`. `bin/registry.py` `MAILBOX_WORK` refuses them as triggers,
  so a job can't loop on them. `prune` removes these and `message.woken` with the letter.
- `wake-misses` and doctor: a pass or a lapsed window isn't a miss. A miss is a wake that didn't lead to the session being told.
- `cmd_status` and `gen-status-page.py`: a "waiting" category for routed, triaged and budget-stopped letters, apart from `stuck`.
- `letter_path` shows routed, passed and triage.

## Tests

Characterization of today's routing first, committed before any change. Then, tick by tick with hooks in between: an idle session
woken for a request at any context size; the second candidate told and marked after the first passed; a busy candidate told by its
hook; a route lapsing with no next candidate, then any hook shows it; budget used up, then hooks; triage, then a session opening
later; a handler's inbox marks nothing; resume then triage isn't taken; `held` leaves it open; `to_session` outside the candidates;
wandering and compaction; `waiting` skipped; `pass` rules; the off switch and budget on notices; confined rules for a handler, and
unchanged for the other points, with reconciliation; the fixed line neither takes nor wakes and carries the chain; the route call in
the background, its timeout, a bad answer, nothing it wrote reaching the bus, a failed call publishing no `job.failed`; `.last`
surviving an idle prune; gates on text; views before wakes; goldens in both languages. A live run in Desktop before merge.

## Steps, each standing on its own

1. **State**: per-session telling, the taken rules, routes (none created yet), platform runs marking nothing, the status category.
   Today's routing on top. With no routes, the only visible change is that a seen, untaken letter reaches other sessions' hooks.
2. **Triage**: handler rules, fixed line, notes writable, `hold()`. Fixes the false reports. Letters it leaves are now really open.
3. **The session record**: waiter lifetime, `project` in `.live`, `.last`, `can_wake`, `session_status`, prune rule, doctor's row.
4. **Routing**: the tick above with pick by thread then recency, window, `pass`, wake rules, budget, off switch. The fork for wake-capable
   runtimes goes here. The owner's behaviour starts here.
5. **`mailbox-route`**.

Step 1 of the separate work on sandboxed reads (`denyRead`) is independent and goes on its own.
