# Mail goes to a session first

Status: design r7, steps 1 and 2 built (records at the end). The cold reviews' blocking findings are closed; should-fix items go to each step's own review. Each revision fixes the previous one's cold review, on top of the owner's decision. The main
change since r2: delivery is per session, and a route, an ack and a fork all have an end. Replaces the delegation part of the routing in
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

1. A delegation wakes a session only if it had a hook within `CONVERSATION_WAIT_MIN` (5 minutes) and the letter is under 5
   minutes old. A session that has really gone idle isn't woken.
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

One off switch: `mailbox.wake: false` in `brunna.json` or in a project's registry entry. `manifest.update` can't change it:
`mailbox` already exists at the top level, so `PLATFORM_OWNED` needs a nested check. It covers every wake, notices included.

## The letter's state

Delivery today is one flag per letter (`INBOX_SQL` `delivered`): once any session has seen a letter, other sessions' prompt
and tool hooks don't show it, and session-start lists it in one line. That can't express "the next session should see it", so
for requests and questions state becomes:

- **Taken**, while any of these holds:
  - a reply from the receiving project to the sender (any of its names, as `own_names` and `canon` see them), or done, by anyone: for good. A question to the owner with `ref` to the
    letter (`<id>:loop`, `<id>:propose-failed`, or a handler's question) doesn't count, even for the hub's own letters;
  - an ack, while the session that acked is live: **an ack has an end.** Live means `Sessions.live_sessions()`, one `ps`, not the
    records alone (a session can run with no `.live` until its next hook). A closed session's ack no longer holds. An ack with no
    session from an MCP client holds as today; a platform run's ack isn't recorded;
  - a `resume` row, for `RESUME_TIMEOUT_MIN`: **a fork has an end.** After that the letter is open again, whether or not it was
    triaged, and `stale_resumes` goes away. A letter is forked once, so a fork that ends without closing it can't re-fire;
  - an old `handler` row: for good, so letters from before this change don't come back.

  `held` and `triage` are never taken. Only `ack`, `reply` and `done` by a session count as acting on it.
- **Told**, per session: a `message.delivered` row with that session, from a hook or `brunna inbox`. A session is told a letter
  once. A `resume` row isn't told, and the "handled in a background branch" text shows only when the fork replied or closed the
  letter. A session that sent a letter to its own project isn't told it and can't hold it.
- **Routed**: one session holds the letter. `message.routed` `{session, by: only|thread|model|recent|hook}`, in dispatch or, for
  a session told with no route, in the hook itself (`by: hook`): the first session to see a letter holds it, as today's 60
  minutes do, and nobody pre-empts it.

A route's event id is unique per row and state is the latest row by `seq`, because `publish` silently drops a repeated id.
`brunna inbox` writes a `by: hook` route too (a sandboxed session through the outbox op), so reading a letter holds it, as today.
Lease sessions are never told, never hold and are never candidates: today's guards for them stay.

**A route always ends:** by its window, by pass, by the letter being taken, or when its session is no longer live. One helper
judges this, for dispatch and for hooks alike, from the bus and the process records, so it holds where dispatch doesn't run
(`runtime.default` with no `sessions` port). Dispatch records the end as a `message.routed` with no session, and a route
judged ended never revives, even if the session is told later.

**The window** is derived, not stored: 5 minutes from the wake (`WAKE_PICKUP_MIN`) until the session is told, then 60 from the
later of the telling and the route (`TAKE_TIMEOUT_MIN`), including a `by: hook` route, which starts told. A session working for 20 minutes isn't pre-empted, and
one that never got it isn't waited on. **A busy routed session** isn't woken and its window hasn't started: on each tick, once it
is idle and still untold, dispatch wakes it (a hook skipped by `POST_TOOL_EVERY_S`, or a tool running for minutes, can't lose
the letter). A busy session's route is bounded by 60 minutes from the route.

**A hook shows a letter** to a session when it is open and not taken, that session hasn't been told, and no route points at
another session. Session-start keeps listing every open letter it isn't showing in full, in one line, as today: that list is what survives
compaction, and it reaches a session opening after the holder closed.

**`brunna inbox`** lists every open letter, marked with who holds it and whether it is taken. It marks told only for the session
listing it (a sandboxed session through a new outbox op), and not at all for a platform run (`HUB_LLM_POINT` set: the handler's
first command must not count). A sandboxed view carries the derived taken and held state, since it can't judge liveness itself. Footer and wake
notice say `brunna ack <id>` as soon as you take it, `brunna pass <id>` if it isn't yours. Someone else's ack ends the matter.

Notices (info, done, progress) keep today's one-flag behaviour: nothing routes them.

## Each tick, for a request or question that is open, not taken and not routed

1. **An MCP session uses the project**: held as today, up to `MCP_HOLD_MAX_MIN`.
2. **The letter is under 5 minutes old and a session had a hook in that time but isn't a candidate** (first turn, or mid-turn
   after a wake): wait, as today. Bounded by the letter's age, so a wandered session can't hold it.
3. **A candidate not yet tried exists**, wake is on, the budget has room and the letter is under 7 days old: pick one and route it;
   wake it if `idle` or `unknown`. Reconciliation letters (`question_check`) are never routed: they'd wake sessions at 03:00.
4. **The only candidates left are `waiting`** (a dialog is open) and the letter is under 60 minutes old: wait. Older, go on as if
   they weren't there.
5. **A reply to a closed session** whose context is small, from a sender that isn't sandboxed, under 7 days old, not forked before: today's
   background resume. It's the only way to reach a closed session, and a fork's whole context mustn't answer a sandboxed sender.
6. **A live session of a runtime with no wake port** (`runtime_ports.Sessions` routes by each session's runtime), context small,
   sender not sandboxed, under 7 days old, not forked before: today's delegation fork, that session only.
7. **Not yet triaged and under 7 days old**: triage. So an old `held` row or an old ack by a closed session doesn't come back as work.
8. **Otherwise**: nothing. It waits for a hook or a new candidate.

`hold()` applies to steps 5-7 only: it stops platform runs, not sessions. "Not forked before" is a rule from step 1 of the work on,
on both fork paths, or a dead fork re-fires every tick: `claim`'s fixed id means neither `runs_today` nor `platform_rounds` rises.

Off, budget used up, or too old: step 3 doesn't apply and the rest do, so a project whose budget another project burned still
gets triage. A `to_session` is picked first if it's a candidate of the recipient project; any other `to_session` is ignored for
routing. `message.routed` lifts the `REPLY_WAIT_MIN` filter for the session it names.

When a candidate appears after triage (a session opens, its first turn ends), the letter is routed to it: the owner's rule
applies. That's what the 7-day limit and the budget bound.

## Candidates

A session whose process record (`.live`) names this project ∩ `live_sessions()` ∩ `can_wake(sid)` (Claude: the waiter's
heartbeat is fresh), not yet routed this letter. Never MCP rows, lease sessions or the platform's own runs.

`.live` gains `project`, set when the process is first recorded and carried over whenever the record is rewritten for that
process, so a compaction after wandering into another folder doesn't move it. A record with no `project` (a session running at
the upgrade) gets it at its next hook, even with `refresh=False`. The hub's own sessions get `hub_project()`: `project_at` returns
nothing for them. Mail shown by cwd stays as it is. The wake notice names the
project (`brunna inbox --project P`, `--from P` on the reply), so a session in another folder can read it and answer as P, and
`inbox` tells it wherever it runs.

`session_status(sid)` (optional port) answers `busy|idle|waiting|unknown`; Claude: `claude agents --json`, checked live: it lists
Desktop sessions and starts no daemon (`waiting` hasn't been seen live, the docs list it). `waiting` means a dialog is open: not
woken, not counted as tried. `unknown` counts as idle. A runtime with `wake` must have `can_wake` (a contract test).

## How long a session can wait

The waiter's hook timeout goes from 1 day to 14 days (`WAIT_TIMEOUT_S`). The docs state no maximum, and the CLI's timer limit
is unverified, so the first armed waiter is watched before this ships: that shows it doesn't fire early, not that it holds
14 days. A session idle longer isn't a candidate. The status page counts open sessions with no waiter, with the reason, and
doctor gets a row for ports a runtime lacks. Agent-view background sessions can stop after about an hour unpinned (docs), so this
doesn't reach them; doctor says so.

## Picking one

1. One candidate: that one.
2. The `to_session`, or the session that last acted in this thread.
3. Otherwise `mailbox-route`.
4. No answer from it: the most recently active.

**`mailbox-route` never runs inside the dispatch loop**, which is serial and carries every project's mail. Dispatch starts it
in the background (like `run_handler`'s propose path, in its own process group), at most one per project per tick and a daily cap
per project, with a pending marker file; past the cap it picks by recency. It writes nothing to the bus. The next tick
reads the answer, or falls to recent if there is none after 2 ticks. Rereading on the next tick also closes the race with a
hook that showed the letter meanwhile.

`shape: completion`, `model_group: fast`, and two things that need code: a fixed-text failure (`contracts/llm-points.v1`, synced to the
other side the same day; `fail()` today publishes the reason, and that can echo model output, so this point publishes `job.failed`
with a fixed sentence: a broken router must show, without the text) and `max_attempts: 1` (`completion.run` retries
invalid output and rate limits today). The process's stdout isn't kept, since `llm_error` can carry model text. Its schema is one string, `session`, with no `enum` or `pattern`, so a validation error can't echo a value. The
platform checks the answer against the list and writes only a matching id, to a mode-600 file in the secrets sessions folder.
Not recorded for evals. **No text from the model is kept:** a crafted letter could otherwise have it copy a last reply and read
it back with `brunna show`. The bus gets `message.routed` with `{session, by}`.

The model reads, marked as data: the letter's sender, type and first 1000 characters, and for each candidate its id, worktree
name from `.live`, idle time, and the gated text below. No branch lookup (that would need a `project_git` op and time).

### The last reply

The owner's decision: the model may read each candidate's last reply. `.last` holds the last 500 characters of the Stop
hook's `last_assistant_message`, when present. It lives in the adapter: `hub-event wait` passes the runtime's `wait` a neutral
`keep_last` flag, computed from the project's label, and `session_summary(sid)` is the port. `session_file` learns the suffix. The
prune keeps it while the session's process record is alive and removes it at the end.

Text (the last reply, and the first lines of letters that session sent) goes only when `privacy.reflection` is `include` for both
the project at the session's cwd when it was written and the `.live` project at send time (the owner's choice of gate, 11-07; that
label wasn't written with this in mind) and the point resolves to the runtime
that session uses, with no `llm.points` override. Otherwise metadata only.

## The wake

All wakes go through `wake()`, notices included: the off switch, the budget and `<id>:woken:<sid>` apply to all. It builds the
notice from a validated sender and id, and drops either if malformed, because `--id` is free for unsandboxed senders. One wake per
session per tick, covering all its letters, counted as one. On a tick, views are written before wakes, so a sandboxed session's
`brunna inbox` is current.

`daily_wakes_per_project` (default 12) is the bound; a thread can't bound it because a woken session may omit `--reply-to`.
Sender names can be spoofed by unsandboxed projects, so there's no per-sender limit: a project can burn another's budget, and
then that project's mail goes to triage and hooks, which loses nothing. Off means no candidates, so no routes either.

A session in a prompting mode stops at the first dialog, and its status becomes `waiting`. Its window ends at 5 minutes unless
told, and the dialog stays for the owner.

## `pass`

Only by the session the letter is routed to; refused otherwise, and refused to confined runs (not in `platform_cli(mail_only)`).
Its own event, `message.passed`. Added to `WRITES`, `MAIL_COMMANDS`, `SESSION_SUBCOMMANDS`, `SESSION_COMMANDS`, the outbox's
operations (`take()`, `mail_command`), the MCP server and the help goldens. A sandboxed view gains who was asked (session ids of
its own project are already there), so the routed session can tell it's itself.

## Triage: the headless handler as last resort

- **It takes only what it finishes.** `via: triage`, not taken, so the state rules above apply. `run_handler` enforces once per
  letter. `runs_today` and `platform_rounds` count it, `PATH_OF` and `path_labels` name it.
- **`hold()` stops platform runs without taking.** At `thread_max_rounds` it asks the owner once (its event id makes that once,
  and it logs once) and the letter stays open for sessions. `held` is no longer taken; `claimed()` drops it. Routing goes on.
- **Its own rules, not all confined runs'.** `run_handler` passes `MAILBOX_LETTERS=<ids>`. When that's set, `confined_refusal`
  allows only: `done` replying to one of those letters (`info` too for an `asks` letter, which tells it to), `done <id>` for them, a
  question to the owner, and `done|drop --question` to the owner (reconciliation needs it). No other `info`, no other recipient, no
  `ack`, no `pass`. A confined run could otherwise send
  to any project and wake a session. The other `settings: platform` points (fork, event and clock jobs) keep today's rules.
- **A session and a handler don't both answer.** Refused too, if another session has taken the letter.
- **It reads only its letters.** With `MAILBOX_LETTERS` set, `inbox`, `show` and `thread` are limited to those letters and their
  threads, and bypass the `REPLY_WAIT_MIN` filter (or it gets an empty mailbox for a reply); `open-questions` is refused.
  A `--question` close needs one of its letters to name that question (`question_check`). One run per sender, so a crafted letter
  can't close another sender's letter in the same batch. A fork gets its own letter in `MAILBOX_LETTERS`.
- **It can't read what other projects' runs left.** Confined runs of the hub get `.state` and `.logs` denied, except the run's own job
  folder: a handler's cwd is the hub root, where fork logs and other projects' reports sit, and it could quote them to a sandboxed
  sender.
- **`mailbox.handler: propose` follows the same rules**: no claim, `_mailbox` accepts `done` and owner questions only, rechecks
  taken at apply time, and a `propose-failed` question closes with its letter. `mailbox.md` and `mailbox-propose.md` change.
- **The sender gets one fixed line from the platform, once**: received, waiting for a session. No `ref`, no `to_session`, so it
  neither takes the letter nor wakes anyone. It carries the letter's chain, so an event job can't loop on it. None for the owner
  side, the hub's own letters or reconciliation letters. When the budget stops routing, the same line goes out.
- **Its question to the owner stays as today.** A session answering the letter doesn't close it; the nightly reconcile-questions job
  asks about open questions, as it does now. Linking the two was tried in r5 and dropped: a question isn't an answer, a temporary
  take must not withdraw it, and a session's own question looks the same on the bus.
- **It can write its notes, and so can a fork.** Today the confined run's `deny_write` covers the hub's own project, and
  `own_state_dir` only carves out `<state>/agent-jobs/<job>`. Both gain the project's `.agent-memory/memory/` by absolute path, with its
  allow rule and `--add-dir`, since a fork of a hub session may run in a linked worktree with no link to it. The hub's notes aren't committed (`memory: false`),
  so the prompt stops saying the platform commits them there. The prompt also says: state an action only after its tool succeeded.
- **No repeats and no noise.** `dispatch`'s "treating it as not taken" log fires once per letter.

## What else reads this

- `contracts/hubbus.v1.schema.json`: `message.routed`, `message.passed`. `bin/registry.py` `MAILBOX_WORK` refuses them as triggers,
  and `message.woken`, so a job can't loop on them. `prune` removes these and `message.woken` with the letter.
- `wake-misses` and doctor: a pass or a lapsed window isn't a miss. A miss is a wake that didn't lead to the session being told.
- `cmd_status` and `gen-status-page.py`: a "waiting" category for routed, triaged and budget-stopped letters, apart from `stuck`.
- `letter_path` shows routed, passed and triage. `VIEW_SQL` carries routed and passed rows.
- `bin/evals/e3.py` runs `mailbox-escalation` with `MAILBOX_LETTERS`, or it stops testing the rules that matter.
- Stale text: the mailbox paragraph in `AGENTS.md`, the `hubbus` header, `contracts/llm-points.yaml` (`stale_resumes`),
  `templates/agent-instructions.md` ("an agent handles it right away"), `bin/mcp_server.py` (inbox "marks them as seen"),
  `mailbox-resume.md` and `mailbox-delegate.md`.

## Tests

Characterization of today's routing first, committed before any change. Then, tick by tick with hooks in between: an idle session
woken for a request at any context size; the second candidate told and marked after the first passed; a busy candidate that goes
idle untold, then woken; a route lapsing with no next candidate, then any hook shows it; a route to a closed session ending; budget
used up, then triage; triage, then a session opening later; a handler's inbox marks nothing; a fork that dies, before and after
triage, counting processes started (one); a closed session's ack; `<id>:loop` on a letter to the hub stays open; `held` leaves it open; compaction keeps the list; `to_session` outside the candidates;
wandering and compaction; `waiting` skipped; `pass` rules; the off switch and budget on notices; confined rules for a handler, and
unchanged for the other points, with reconciliation; the fixed line neither takes nor wakes and carries the chain; the route call in
the background, its timeout, a bad answer, nothing it wrote reaching the bus, a failed call publishing no `job.failed`; `.last`
surviving an idle prune; gates on text; views before wakes; goldens in both languages. A live run in Desktop before merge.

## Steps, each standing on its own

1. **State**: per-session telling, the taken rules, `by: hook` routes, platform runs marking nothing, the status category, `claimed()`,
   one fork per letter and the 7-day limit on today's routing, so a dead fork can't re-fire and an old closed-session ack doesn't
   come back as a headless run.
   Today's routing on top, reading the new state: the first session to see a letter holds it as today, and the window replaces the
   restart of 60 minutes at each sight. A letter a route no longer holds reaches other sessions' hooks.
2. **Triage**: handler rules, fixed line, notes writable, `hold()`. Fixes the false reports. Letters it leaves are now really open.
3. **The session record**: waiter lifetime, `project` in `.live`, `.last`, `can_wake`, `session_status`, prune rule, doctor's row.
4. **Routing**: the tick above with pick by thread then recency, window, `pass`, wake rules, budget, off switch. The fork for wake-capable
   runtimes goes here. The owner's behaviour starts here.
5. **`mailbox-route`**.

The separate work on sandboxed reads (`denyRead`) is independent and goes on its own.

## Step records

### Step 1: where it differs from the text above, and why

- **A telling with no route row holds by that telling** (`route_of`). Two kinds of letter have one: told by a hook before
  this step, and told by an `inbox` with no session (an MCP client carries none, `docs/mcp.md`; a null `session` on a route
  would read as an ended route). Read literally, the first would reach every other session's hook at the upgrade, and the
  second would stop holding what it held. So the first telling acts as a route from its time, ended like any other; with no
  session it ends by the window alone. No row is written for it until it ends.
- **A session whose runtime has no sessions port counts as live** (`Live`, `Sessions.judges`), as does a failed lookup. That
  is the feature being off, not the session gone: its route ends by the window, its ack holds.
- **`held` until step 2**: `hold()` is unchanged and writes its row and question as before. Dispatch skips a letter with a
  `held` row (no platform run, nothing logged, counted as waiting), which keeps hold() from running every tick now that the row
  doesn't take the letter. Hooks show it. `bin/propose_apply.py`'s held row isn't that case: `run_handler` writes a `handler`
  row first, so its letter is taken for good until step 2, as it was.
- **The 7-day check comes before the loop guard** as well as the forks and the handler, so an old thread doesn't get a new
  `<id>:loop` question.
- **The end of a route for a letter taken for good isn't recorded**: it can't come back, and it would write a row for every
  answered letter still open. Taken for now (an ack, a fork) is recorded, so the route doesn't revive when that take ends.
- **`waiting` also says `held` and `old`** (`why`: routed, triaged, held, old, budget). `old` is only for a letter past 7 days
  that something already tried (a telling, a fork, an ack). One nobody has seen stays `stuck` however old, with or without
  budget: the first version of this step quieted it, and an alert that stops at 7 days loses the letter. A route that ended
  with nothing started and no such reason is stuck after `STUCK_HOURS`.
- **A hook within 30 minutes (`SEEN_ALIVE_MIN`) counts as evidence of life, unless that session's end hook has run.** The
  design says live is `live_sessions()`, but a headless session a person started has no process record and its id is on no
  command line, so its ack would drop on the next tick while it worked. The price: a holder killed without its end hook keeps
  a letter up to 30 minutes longer; one that exits normally frees it at once.
- **A failed process lookup isn't "no sessions"**: `live_sessions` raises `SessionsUnknown` and has a timeout. Dispatch then
  starts and ends nothing that tick, hooks treat every session as can't-tell, the wake-address prune removes nothing.
- **Before an end is recorded as `closed`, dispatch looks at the process table again**, and hooks record the session before
  telling it: the tick's one look can predate a session's first hook.
- **A lease sees only what no session has seen and no route has had.** It is never told, so per-session state can't quiet a
  letter for it; a letter free again for sessions stays quiet for it, as on main.
- **What a platform run sends carries its `point`**, in a letter's payload and a done's, so `fork_result` credits a fork only
  with its own reply or close, from the receiving project. Never from an outbox: anything in the sandbox writes those files.
  So a sandboxed project's fork, and a fork from before this step, get no credit: the session isn't told the "handled" text.
  The letter is closed or answered all the same.
- **A session in a linked worktree goes through `hub-event worktree`** from `worktree-memory.sh`, outside any sandbox, for its
  process record and its end. That script's `end` hangs off both SessionEnd and PreCompact and the installed user-level
  settings change only with the owner, so the runtime's `session_ending` reads which it was.
- **The outbox op is `told`.** It carries requests and questions only (a notice's flag stays its hooks', as before), re-judges
  against the bus when taken in, since the view can be a minute old, and does nothing for a session the hooks haven't seen in
  this project.
- **A fork that dies still gets one handler**, as `stale_resumes` gave it: forks 1, handler at most 1. Step 2 makes that run a
  triage.

### Step 2: where it differs from the text above, and why

- **A fork isn't a handler** (decided 11-07, after the cold review). It gets its own letter in `MAILBOX_LETTERS`, so it reads and closes
  only that letter and sends to nobody but its sender and the owner; it has no `ack` and no `open-questions`. But it has the
  session's context, so its reply is any ordinary type, a question back to the sender included. Handlers keep `done` (`info` to
  an `asks` letter). They are told apart by the point (`run_point() == FORK_POINT`). Event and clock jobs carry no letters and
  are unchanged.
- **The rules bind only the agent, which has a point** (`run_letters`): `llm.py` and `run-agent-job.sh` around it inherit the
  list and have none, or `job_failed`'s `publish` would be refused and a run's failure never recorded. The agent's Bash reaches
  no `publish`, by the point's allow rules and by `run_refusal` alike.
- **`--question` refuses `--reply-to`, for everyone** (`send_letter`, so an outbox file too): it would carry that letter's handoff
  and take it, a forged answer that reaches the assistant.
- **A question to the owner has no `--reply-to` to judge "taken" by**, and on an `asks` letter one would make it count as the
  answer. So it is refused when all the run's letters are taken, and no letters at all count as taken. Propose's `ask_owner`
  the same. A fork is under this too.
- **A start that failed spends nothing**: `run_handler` starts first, then writes the triage row and the fixed line. A failure
  is a `job.failed` with `ref` to the letter (`mailbox-start:<project>`, id `<id>:start-failed:<n>`), which `gen-status-page`
  alerts on, as it does for a mailbox run's own failure. Tried `START_TRIES` times, `START_RETRY_MIN` apart; then the letter
  shows as stuck and hooks still get it. The price of that order: a dispatch killed between the start and the row can start
  a second run for the letter. A run that dies without `llm.py` exiting leaves `status.json` at `running`, as before this step,
  which holds the project's next handler back.
- **A propose reply carries what a session's does** (`reply_fields`): thread, asking session, handoff, or an answer to an `asks`
  letter doesn't reach the owner as the answer.
- **`MAILBOX_LETTERS` is a JSON list**, not comma-separated: `--id` is free text for an unsandboxed sender, so `a,b` would hand
  a run the letter `b`. A value that can't be read is no letters: refused, never unlimited. Propose's agent gets it too, for its
  reads (its writes are refused already).
- **The rules live in `run_refusal`, called from `mail_command`, not `confined_refusal`**: they need the bus (or the view), and
  `confined_refusal` runs before either is open. `confined_refusal` stays as it was, for every confined run. They apply
  to an agent with the variable, with or without `CONFINED`.
- **"Taken by another" leaves out the run's own acts** (`taken_by_another`), or `send` then `done` would refuse the second. A
  letter is triaged once and forked once, so a reply or close with this run's point is its own. From an outbox there is no
  point: there, one with no session is its own, so a close by a person at a terminal or an MCP client in a sandboxed project
  looks like the run's own. A sandboxed run's view can also be a minute old, so it can miss a take. The worst case of both is
  the double answer this rule cuts down, not a wrong sender. The check isn't made again when an outbox file is taken in: a file
  can't prove it came from a confined run.
- **The view gains `fork_only`**, so a sandboxed project's fork isn't refused for its own `resume` row.
- **The `delivered` flag leaves out `triage` and `held` rows** (`INBOX_SQL`): it says a session has seen the letter, which they
  don't. So today's wake and MCP hold apply to a triaged letter too, the status page's "not yet in a session" counts it, and a
  discuss lease sees it.
- **`hold()` moved behind the waits**, with the 7-day limit and the `held` guard: a letter just come, past its rounds, with
  a session in use, waits and wakes as any other, and `hold()` comes only when the platform would start something. A triaged
  letter with nothing left to start isn't held, or a letter triaged as a thread's last round would ask the owner about a loop
  when nothing is looping.
- **A fork can come after triage** (tick steps 5-6 are before 7, and the tests list "before and after triage"). In step 2 that
  is today's fork paths; a transcript is read once a tick per session, since open letters now stay in the loop.
- **One sender per tick per project**: `status.json` says `running` only after `run-agent-job.sh` has detached, so a second start
  in the same tick would race it. `run_handler` takes the first sender's letters; the next goes next tick. This spends more of
  `daily_runs_per_project` than one batch did (live replay below).
- **The budget line goes out now**: when `run_handler` is stopped by `daily_runs_per_project` the sender gets the fixed line, and
  the log goes through `log_once` (it fired every minute; it says it again when the number of letters waiting changes). Step 4's
  wake budget should reuse `received_line`.
- **The fixed line is an `info` from the hub.** "The hub's own letters" reads as sent by the hub; a letter *to* the hub from
  a project gets the line. A project's letter to itself gets none. A malformed id shows as `?`. It stays open in the sender's
  inbox like any notice until someone closes it.
- **The own job folder is carved out only when the run's working directory is it** (`own_state_dir`, as for writes). A handler's
  working directory is the hub root, so all of `.state` is denied to it: it never reads its job folder (`llm.py` reads the prompt,
  `run-agent-job.sh` writes the report). `mailbox-propose` isn't `settings: platform`, so the deny isn't there; its working
  directory is a throwaway copy that holds no `.state`.
- **A notes folder that is a link, or holds one at `memory`, gets no absolute rule** (`policy.notes_dir`): a project's links don't
  decide what a platform run may write. `_linked_rules` still follows `.agent-memory` for a worktree, and it would follow a
  `memory` link too; it did before this step, and needs its own fix.
- **The prompt names the notes by absolute path** (`{{notes}}`), or a fork in a worktree with no link would write beside itself.
- **Propose and a plain notes folder**: a project whose `.agent-memory` has no checkout (the hub, `memory: false`) has nothing
  in the throwaway copy that lists a note, so what the agent wrote would vanish while its answer said it was written. `run()`
  now looks there directly, through the same allow rules.
- **`close_failed_questions` is a step of `dispatch_locked`, not of the `done` paths**: a letter closes through the CLI, an
  outbox, propose or the librarian. A reply closes it too (`answered`), as it takes the letter for good. A loop question stays.
- **`mailbox-propose`'s schema drops `question` from reply types**: it goes in `ask_owner`.
- **e3 sets `MAILBOX_PROJECT` as well**: the mailbox scenarios had none, and the runtime refuses to run without it.

Found while here, not changed: `worktree-memory.sh` in a sandboxed project's worktree re-runs itself as the project, where the bus
isn't reachable, so its `hubbus hook` tells nothing, silently (checked in the fixture: the letter stays untold, the same hook
tells it in an unsandboxed project). `hub-event` does tell it there, from the settings `render-project-settings --worktree`
writes during that session's start hook; whether a session already starting picks up hooks from that file wasn't checked live.
So a worktree session of a sandboxed project may get no mail until it restarts or the file is read. The fix is the mail
through `hub-event worktree`, before the re-run. Step 4 needs this settled, since telling is what ends a wake's window.
