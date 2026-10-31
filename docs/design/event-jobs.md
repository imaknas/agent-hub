# Event-triggered jobs

> agent-hub session · 2026-11-01 · r5. Item 1 of "Build" in `competitive-gaps.md`: a registry job can start when
> matching bus events appear, not only on a clock or `after:` another job. External sources (CI finished, a webhook)
> are out of scope: they will arrive later as bus events from one receiver, so jobs only ever react to bus events.
>
> r2 after a first blind review (two reviewers: code facts; loops, security, exactly-once). Changed: the field is
> `trigger:`, not `on:` (YAML 1.1 reads a bare `on` as true); the trust model of the bus is stated and an agent with
> the platform's authority never reacts to other projects' events (3.2); the cursor is tied to a bus generation, moves
> only past events a run actually received, survives a rename, and is written under a lock shared with `schedulectl`
> (4); relays don't stamp their chain (7); held-back events are counted and shown; `schedulectl skip` for a batch that
> keeps failing; `sources` filter; `record` refused.
>
> r3 after a second blind review (one reviewer, all lenses). Changed: the platform's event jobs can't be agent jobs at
> all, and a project's agent event job must declare `allow:` (3.2); `message.delivered` / `message.done` are refused
> like `record` (3.5); a project's job matches only events addressed to it, never by source (3.2); loops are held back
> by depth, carried separately from the chain's membership (7); a prompt the batch can't be put into fails the run
> (6); the batch sits between random-token delimiters; lock waits are bounded (4); no cursor while there is no bus
> file; a former name now used by another project is not "self".
>
> r4 after a third blind review: an agent event job must run in the workspace, where the project's sandbox and rules
> apply (3.2); smaller fixes (the bus generation is written only when missing, an unreadable `ts` can't break the page,
> a rename during a background run keeps its advance, `schedulectl run` takes its mark back if the start fails), and
> the owner-answer loop is named (7).
>
> r5 after a fourth review (of r4's fix): running in the workspace also loaded the project's accumulated tool
> permissions (every "always allow" a person clicked there), so `allow:` was not the whole boundary. Agent event jobs
> now run under their own judgment point, `agent-job-event`, which loads **only** the platform's settings for the
> project (3.2); verified against the installed CLI. A renamed job doesn't start while its old name still runs.

## 1. What exists

- `bin/scheduler.py` ticks every 15 minutes. A job is due by clock (`time`), by interval (`every_minutes`), filtered
  by days/months/`end_date`/`between`, ordered by `after:` (`before:` is turned into the target's `after:` in
  `registry.scheduler_jobs`). State lives in `.state/scheduler-last-run.json`: `<job>` = the date of the last success,
  `<job>.attempts` = `{date, count}`, `<job>.last_ok` for interval jobs. Background jobs write `.state/runs/<job>.json`
  and are merged back by `reconcile()` on the next tick.
- `bin/hubbus` is an append-only SQLite log: `events(seq, id, ts, type, source, to_project, ref, payload)`. Writers:
  letters and their bookkeeping (`message`, `message.delivered`, `message.done`); `job.failed` (scheduler, backup,
  contracts-check, llm, and the bridge's own rejects); `merge.done` / `merge.failed` / `session.report`
  (merge-branch); `config.applied` (`agenthub config apply`); `session.report` (lease runner); `record`. The event
  types are the enum in `contracts/hubbus.v1.schema.json`. `ts` is timezone-aware (`hubbus.iso()`); the scheduler
  works in naive local time.
- **The bus is a peer log, not an authenticated one.** Every project's sandbox may write the bus directory
  (`policy.py`: the bus is how a session sends a letter), `hubbus publish` takes any `--source`/`--to`, and `send`
  takes `--from`. An event's `source`, `to_project` and payload are what its writer claims. Any project can already
  make an agent run in another project with text it wrote: a request letter, which dispatch hands to a handler there.
- `bin/run-agent-job.sh` detaches itself (double fork) and returns 0 at once; the run's outcome lands in
  `.state/agent-jobs/<name>/status.json`, never in the exit code the scheduler sees. With
  `RUN_AGENT_JOB_FOREGROUND=1` it runs in place (and today still exits 0 when the run failed).
- `bin/hub-event` is unrelated despite the name: it is the entry point for a coding tool's hooks, not a bus writer.

## 2. The field

A registry job gets a third trigger, `trigger:`, next to `at` and `every`. **A job has exactly one of `at`, `every`,
`trigger`.** Mixing a clock with events would make "ran today" mean two things; a project that wants both declares two
jobs. (Not `on:`: PyYAML reads a bare `on` key as the boolean `true`, and the registry is rewritten by `safe_dump`,
which drops any quoting.)

```yaml
jobs:
  - name: review-merge
    trigger:
      events: [merge.done, merge.failed]   # required: one or more bus event types (the hubbus contract's enum)
      projects: [self]                     # optional, default [self]: whose events (3.2)
      sources: ["*"]                       # optional: the writers to accept (globs), default any (3.3)
      where:                               # optional: payload filters, all must hold (3.3)
        branch: "claude/*"
      debounce: 10m                        # optional, default 0m: quiet period before a run (5)
    kind: agent
    allow: ["Read", "Bash(git log:*)"]     # required for an agent event job (3.2)
    max_attempts_per_day: 6                # optional for event jobs, default 12 (5)
```

Every other job field keeps its meaning: `run` / `kind: agent` + `prompt`, `cwd`, `days`, `days_of_month`, `months`,
`until`, `between`, `background`, `after`, `before`, `max_attempts_per_day`, `allow`, `connectors`. `legacy_name` is
refused with `trigger` (schedule.yaml has no event jobs to migrate from); schedule.yaml does not get `trigger`.

Registry validation (`bin/registry.py`); every error makes the file invalid like any other job error:

- `trigger` is a mapping with only `events`, `projects`, `sources`, `where`, `debounce`;
- `events` is a non-empty list, each in the `type` enum of `contracts/hubbus.v1.schema.json`, and not `record`,
  `message.delivered` or `message.done` (3.5). The enum is the one list: `hubbus publish`'s choices are it minus
  letters and records (a test keeps them equal). A new event type goes into the contract first;
- `projects` is a non-empty list of `self`, a project name or `*`. An ordinary project may only name itself; the
  platform project and modules may name others or `*`;
- the platform project's and modules' event jobs can't be `kind: agent`; a project's agent event job must declare
  `allow:` and run in the workspace (`cwd: workspace`, explicit when the project has a runtime) (3.2);
- `sources` is a non-empty list of strings;
- `where` maps payload keys (dots for nesting: `record.type`) to a scalar or a non-empty list of scalars;
- `message` in `events` needs `where.type` limited to `info`, `progress`, `done` (3.5);
- `debounce` matches the `every` syntax (`15m`, `1h`) or is `0m`.

`registry.scheduler_jobs` turns it into the scheduler's job dict: `trigger = {events, projects, sources, where,
debounce_minutes, platform}`, where `self` becomes the project's name **plus the names it had before a rename**
(`hubconf.former_names`: old events keep the old name), except a former name another registered project now uses. `max_attempts_per_day` defaults to 12. An agent event job is
always a scheduler background job run with `RUN_AGENT_JOB_FOREGROUND=1`, and `run-agent-job.sh` in foreground mode
exits non-zero when the run failed: the scheduler owns the detachment and sees the real outcome, which section 4
needs (a self-detaching run would look successful at launch, consume its events before it ran, and let a second copy
start while the first is working). Clock agent jobs keep detaching themselves as today. `applier_scheduler.KEYS`
gains `trigger`, so `agenthub config plan` reports a changed trigger.

## 3. Matching

### 3.1 Event type

An event matches when its `type` is in `trigger.events`.

### 3.2 Project, and the trust model

An event **belongs to** `to_project` when that is set, else to `source`:

| event | belongs to |
|---|---|
| `merge.done`, `merge.failed`, `session.report` | the merged / reporting project (both fields are the project) |
| `message`, `message.delivered`, `message.done` | the recipient |
| `config.applied` | the platform project (source) |
| `job.failed` from the scheduler | the job's project: the scheduler now sets `to_project` for registry jobs |
| `job.failed` from elsewhere | its source (`llm`, `backup`, `contracts-check`, `scheduler` for schedule.yaml jobs): platform jobs listening to `*` see them |

An event matches when the project it belongs to is in `trigger.projects` (`*` = any). For an ordinary project's job
only `to_project` counts, never `source`: platform writers (`scheduler`, `llm`, `backup`, …) are sources, and a
project registered under such a name must not see their failures.

The trust model (1: the bus is a peer log) decides what an event job may do with an event:

- **An ordinary project's job only sees events addressed to its project**, and runs with that project's authority
  (sandboxed when the project is).
- **Event jobs do add reach, and it is gated.** A request letter only ever starts a mailbox handler, whose
  permissions are fixed and narrow (`mailbox-escalation`: memory notes and the mail commands). An event agent job runs
  with its own `allow:`, and anyone who can write the bus can start it with a forged event addressed to the project.
  So a project's agent event job **must declare `allow:`** (no unattended `auto` mode), and it runs under the
  judgment point `agent-job-event`, declared `settings: platform`: the run loads none of the user's, the project's or
  local tool settings, only a settings file the platform writes from the policy (`policy.for_project`): the
  project's sandbox when it is enforced, the deny list, and the platform's own commands. So the boundary is exactly
  `allow:` plus the platform's settings; permissions a person accumulated in the project's settings, and the
  project's hooks, don't apply (memory notes the job writes are committed by the hourly memory sync). The sandbox
  comes from the policy, not from whatever is rendered in the workspace at that moment. For Claude Code the adapter
  translates this to `--restricted --settings <file> --tools <the tools the rules use>` (`runtime_claude`; checked on
  2.1.316: a project-level allow no longer applies, a `--settings` one does). The job also **must run in the
  workspace** (`cwd: workspace`), so its files and rules files are the project's. A command job runs only its own code; it reads
  the payload as claims (e.g. re-check a merge in git before acting on it).
- **The platform's event jobs are never agents**, whatever their `projects`: even an event "from" the platform
  project can be forged by any project, and an agent with the platform's authority must not take instructions from
  it. A platform command job may listen to other projects or `*`.
- The batch an agent job receives is presented in its prompt as **data written by others, not instructions**
  (`templates/prompts/event-batch.md`).
- Bus writes stay unauthenticated in this step. Making the bus stamp the writer's identity (a broker that every write
  goes through, so the sandbox no longer writes `bus.db` itself) would protect letters as much as events; it is a task
  of its own, proposed separately.

### 3.3 Sources and payload filters

`sources`, when given, is a list of globs; the event's `source` must match one. It lets a job ignore writers it
doesn't want (an `info` letter flood from one project), within the trust model above: `source` is a claim.

Each `where` key is looked up in the payload (dots descend into nested objects). A missing key never matches. A
scalar value matches by equality, with no type coercion (`1` is not `"1"` and not `true`); a string value containing
`*`, `?` or `[` matches as a shell glob (`fnmatch`, case-sensitive); a list matches if any item matches. All keys must
match.

### 3.4 Loop protection

Before an event counts as a match: if its chain contains this job, or its depth is already `MAX_DEPTH`, it is **held
back** (section 7).

### 3.5 Mailbox work

A request or question letter belongs to the mailbox: a session's hook takes it, or `hubbus dispatch` hands it to a
handler. `record` events addressed to a project are inbox items too (the inbox query includes them). An event job on
either would be a second handler for the same work. `message.delivered` and `message.done` are the mailbox's
bookkeeping, mostly written by dispatch, a relay that carries no chain (7), so a job reacting to them could loop
unseen. So a job may react to `message` only for notices (`where: {type: [info, progress, done]}`), and never to
`record`, `message.delivered` or `message.done`; validation enforces it. An event job never marks a letter delivered
or done.

## 4. Consuming each event once

Each event job has a cursor in scheduler state: `<job>.event_seq` = the highest bus `seq` it has consumed, and
`<job>.event_gen` = the bus file's generation the cursor was taken in. `hubbus db()` writes a random `generation` into
a `meta` table when it creates a bus file.

- **First sight.** A job with no cursor sets it to the bus head and does not run (while there is no bus file at all,
  it takes no cursor: one taken then couldn't tell a bus moved in later from history): a new job starts with events from
  now on, never with history. This runs every tick before the date filters, so a job added on a day outside its
  `days` still starts counting at once. A job of a renamed project first takes the cursor of its old name
  (`<old>/<job>.event_seq`, from `hubconf.former_names`), so a rename loses nothing.
- **Pending.** Each tick reads, in one read transaction on a read-only connection to the bus file's path (not
  `hubconf.bus_db()`, which may migrate), the head `max(seq)`, the generation and `events WHERE seq > cursor AND seq
  <= head AND type IN (trigger.events) ORDER BY seq`, and keeps the matches (3.2–3.4).
- **The batch** is the oldest pending events whose JSON fits in 64 KB (measured in UTF-8 bytes; at least one event,
  whose payload is replaced by `{"truncated": true}` if it alone is too big). `upto` = the head when every pending
  event fits, else the `seq` of the last event in the batch: the rest form the next run.
- **Success moves the cursor to `upto`** (never back). Foreground: after the run. Background: `upto` is in the run
  file and `reconcile()` applies it; the run file is deleted only after the state is saved.
- **Failure leaves the cursor**: the next due tick runs again with the same events plus new ones. A batch that keeps
  failing uses the day's attempts and tries again the next day; the scheduler's job alert reports a failure lasting
  over an hour, and `schedulectl skip <job>` moves the cursor to the head when the owner decides the batch is not
  worth retrying.
- **The guarantee**: every matching event reaches a successful run, and normally exactly one. It reaches a second
  successful run only if the scheduler dies between a foreground run finishing and the state write (milliseconds).
  Delivery is therefore at least once; a job that must not act twice keys its work on the event `id`.
- **In flight**: a background run is not started twice (existing rule); events arriving meanwhile have `seq > upto`.
- **Nothing pending**: the cursor moves to the head on the spot (the held-back events it passes are counted, 7).
- **Out of window** (`days`, `days_of_month`, `months`, `between`) or **capped**: events stay pending.
- **Dropped from the list** (an invalid registry file with no last valid version, a module turned off): the cursor
  stays; when the job comes back it takes the backlog, in batches.
- **A new bus file.** If the generation differs from the cursor's, or the head is below the cursor (a corrupt file
  moved aside, a restore from backup), the cursor is reset to the head with a log line. Events written to the new file
  before that tick are not run; the corruption already lost letters, accepted on 2026-10-21.
- **Prune** deletes closed letters and `job.failed` older than 60 days; an event job only misses them if its batch has
  failed for 60 days.
- **One writer.** The tick holds `.state/scheduler.lock` (flock) for its whole run; `schedulectl`'s state writes
  (`run`, `skip`) take the same lock and write atomically. Waits are bounded: a tick waits up to a minute for a
  `schedulectl` write, then gives up without a heartbeat (a stuck tick must not look alive); `schedulectl` waits 30
  seconds, then changes nothing and says so (`schedulectl run` records "ran today" before it starts the job, so it
  runs nothing either). A run that calls `schedulectl` while its own tick holds the lock therefore fails instead of
  hanging. A background run closes its inherited copy of the lock at once. So a cursor never moves back to a stale
  copy, and overlapping ticks can't hand one batch to two runs.

## 5. Debounce and rate limits

- **Times.** Event `ts` is converted to naive local time before comparison; a `ts` in the future counts as now.
- **Debounce** (default `0m`): a job with pending events is due when the newest is at least `debounce` old (a quiet
  period), **or** the oldest is at least `4 × debounce` old (a stream can't starve it). Three merges in five minutes
  with `debounce: 10m` make one run with three events.
- **Daily cap**: `max_attempts_per_day`, meaning what it means for interval jobs: at most this many runs a day,
  successes included. Event jobs default to 12.
- **Tick**: reaction latency is up to 15 minutes plus `debounce`, like every other job; waking the scheduler on
  publish is a later change.
- **Window**: `between` applies to event jobs too.

## 6. What the job receives

Every run the scheduler starts (clock, interval, event) gets neutral environment variables:

- `AGENT_HUB_JOB`: the job's full name;
- `AGENT_HUB_JOB_CHAIN`: the run's chain, a JSON list, and `AGENT_HUB_JOB_DEPTH`, its depth (7);
- `AGENT_HUB_EVENTS`, event jobs only: `{"events": [...]}`, each `{seq, id, ts, type, source, to_project, ref,
  payload}`, oldest first, at most 64 KB (4).

Command jobs read `AGENT_HUB_EVENTS` themselves; sandboxed ones get it too (`sandboxed-run` execs with the inherited
environment). For agent jobs `run-agent-job.sh` appends the batch to the prompt through
`templates/prompts/event-batch.md` (filled by `prompts.py`), which marks it as data written by others and puts it
between delimiter lines with a random token (JSON keeps every payload string on one line, and the token can't be
guessed, so a payload can't fake the end of the list). If the batch can't be put into the prompt (unreadable
`AGENT_HUB_EVENTS`, a template error), the run fails without calling the model: running without its events would
consume them unseen. A job that has stopped itself (`STOP`, `run-agent-job.sh`) exits 0 and so consumes its events;
the status page already shows it as stopped.

`schedulectl run <job>` gives the run the same variables: an event job gets `{"events": []}` and its cursor stays (a
manual run is a test, not consumption); the chain continues the caller's when `schedulectl run` is itself called from
a run.

## 7. Loop protection

A job that reacts to `merge.done` and merges, or to `job.failed` and fails, would trigger itself; two jobs can do it
to each other. Events carry a **causal chain**:

- A scheduled run's chain is `[job]` for a clock or interval job; for an event job, the batch's chains joined (order
  kept, duplicates dropped) followed by the job. Its **depth** is 1 for a clock or interval job, else one more than
  the deepest event in the batch. The chain answers "was this job involved"; the depth answers "how many generations
  deep": a batch joining three unrelated chains is wide, not deep.
- `hubbus publish()`, the function every event write goes through, adds `chain` and `depth` from the environment to
  the payload (joined with a chain the payload already has; the larger depth). Processes the run starts (merge-branch, `llm.py`, an agent's
  tools, `agenthub send`, `schedulectl run`) inherit the variable.
- The scheduler's own `job.failed` for a run carries that run's chain and `to_project`; a background run keeps both in
  its run file for the "vanished" case.
- **Relays don't stamp.** `hubbus bridge`, `dispatch` and `reconcile-questions` carry other parties' events (owner
  answers, claims, the assistant's records); they drop the variable, so a scheduled relay job doesn't add itself to
  every owner answer. Dispatch passes each letter's own chain to the handler it starts (`run_handler`, `resume`, the
  propose handler), so what a handler publishes stays in the chain of the run that sent the letter.
- **Holding back**: an event whose chain contains the job, or whose depth is `MAX_DEPTH = 4` (a root and three
  generations; an event without `depth` counts its chain's length), never triggers it. The cursor passes it like a non-match, but it is counted: `<job>.event_held` =
  `{date, count, last}` in state, a log line, the status page and `schedulectl events` show them.

Consequences, stated: a job doesn't react to the consequences of its own runs, including a reply to a letter it sent
(request and reply belong to the mailbox and a session, not to an event job); and one chained event in a batch puts
the job in the chain of everything that run publishes. A chain can be dropped on purpose (unset the variable, write
the bus directly); that only lets a project loop on its own jobs, bounded by the daily cap and debounce. Hops the
platform doesn't drive are outside the chain: a person's session acting on an event, and the owner side answering a
letter (the bridge is a relay, so an owner answer comes back without a chain; a job reacting to notices could go round
that way once per answer, bounded by the cap).

`chain` and `depth` are new optional payload fields; they keep the hubbus contract at v1.

## 8. `background:`, `after:`, `before:`

- `background: true` works as for any job; agent event jobs are always background (2).
- An event job may have `after: [X]`: while X is due today but unfinished, its events wait (`waiting_on`).
- A job waiting on an event job (`after:`, or the event job's `before:`) waits only while it is running, like an
  interval job (`waiting_on` treats `trigger` like `every_minutes`; it would crash on the missing `time` otherwise).
- Cycles through event jobs are refused by the existing `job_cycles` check.
- Jobs run in list order within a tick; an event job whose trigger an earlier job published in the same tick sees it
  in that tick.

## 9. Status page

Each event job is a row in the jobs table:

- **When**: "on merge.done, merge.failed", with "(quiet 10 min; from any project; …)" as configured, and the
  cap/until suffixes.
- **Next**: "on the next event", or "3 events waiting since 11/01 14:02", plus "; today's 12 runs are used up" when
  capped.
- **Detail** under the name: the last batch ("last run: 2 events, 14:30") and the events held back today.
- Alert (`notify`): an event job has an event waiting for over a day (capped, out of window, after a job that keeps
  failing, a batch that keeps failing).

The page reads the bus through `event_jobs`, the same functions the scheduler uses.

## 10. `schedulectl`

- `list` shows event jobs with the same "when" phrase as the page.
- `run <event job>`: section 6.
- `events <job>`: the cursor, the head, what is waiting and what the chain holds back (seq, time, type, belongs to,
  chain).
- `skip <job>`: move the cursor to the head, read and written under the lock (4).

## 11. Out of scope, recorded

- The receiver for external sources and its auth.
- Authenticated bus writes (3.2).
- Waking the scheduler on publish.

## 12. Files

- `bin/event_jobs.py` (new): matching, reading, batch, chain, the phrases.
- `bin/registry.py`: `trigger` validation and conversion, header. `bin/applier_scheduler.py`: `trigger` in `KEYS`.
- `bin/scheduler.py`: event jobs in the tick, cursor, lock, env for every run, `job.failed` with `to_project` and
  chain, `waiting_on`, reconcile saves before deleting, header.
- `bin/hubbus`: `meta.generation`; `publish()` adds `chain`; relays drop it; dispatch passes letters' chains; publish
  choices from the contract; header.
- `bin/run-agent-job.sh` + `templates/prompts/event-batch.md`.
- `bin/gen-status-page.py`, `bin/schedulectl`.
- `contracts/hubbus.v1.schema.json` (`chain`, `depth`), `contracts/project.v1.schema.json` (`trigger`).
- `locales/zh-Hant/*.json`.
- Tests: the characterization tests of the tick (committed first), then event jobs end to end through the tick.
