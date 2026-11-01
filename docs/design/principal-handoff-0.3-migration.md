# Moving agent-hub and Muninn to Principal Handoff 0.3: a contract v2 proposal

> agent-hub session · 2026-10-27 · the owner on 10-27: "start moving agent-hub and Muninn to 0.3; coordinate contract v2
> with Muninn first". The spec is in `~/workspace/agent-ops-specs/specs/principal-handoff/` (Core 0.3, Acts 0.3,
> Decisions 0.3, commit 2fe74dc). This is a **proposal**: Muninn's contracts (`messages.v1`, `handoffs.v1`) are defined
> by Muninn, and what v2 looks like is Muninn's decision; the changes on agent-hub's side (bridge, task registration,
> librarian) follow the outcome of the coordination.

## Why v2

v1 grew separately in the two systems; spec 0.3 was abstracted from them, but conversely v1 has a few places 0.3
considers defects (most already sent to Muninn, m10270639-ba51, m10270459-89db):

1. **Who decided is not recorded**: a handoff only has an effect, no decider or kind of authority; a settlement matched
   by the model is recorded as `by=user`.
2. **Claims and settlements are mixed up**: a project's `done --task` and Muninn's settlement go through the same
   `done`; the first message of `asks` is treated as the answer whether it is one or not.
3. **Answers and cancellations are delivered incompletely**: rescheduling, merging and cancellation after sending are
   not told to the project.
4. **Same-machine assumption**: handoffs can only be fetched with the local command `muninn handoffs --json`, and the
   record relies on reading file paths.

## Mapping: v1 → 0.3 records

Direction one: project (agent-hub bridge) → Muninn, today `POST /api/messages` (messages.v1)

| v1 message | 0.3 record |
|---|---|
| `task` (registered by knas-tasks, id = key) | Acts `raised`, id `r:<key>`, `by` = that project's registrar, `terms {text, due}`, `terms_rev: 1` |
| `task` with `new_reason` | `raised` + `note` (explaining why it is new; Muninn does not merge it because of this) |
| `question` | Decisions `raised`, id `r:<message id>`, `terms {text}` (options/default may be added later) |
| `done` / `drop` `--task` (said by the project) | Acts `claimed`, `outcome: done/dropped`, `evidence.basis` |
| same, `inferred: true` (inferred by the librarian) | Acts `claimed`, origin `registrar`, id `c:<key>:r<n>` |
| `drop --task` (this project no longer needs it) | Acts `withdrawn`, `reason: not_needed` (keep policy: Muninn keeps it and asks the owner; other projects' needs are unaffected) |
| `done --question` (answered in the project session) | Decisions `claimed` (with the answer) + `withdrawn`, `reason: settled` |
| `drop --question` | Decisions `withdrawn`, `reason: not_needed` |
| `progress`, `info` | `note` |

Direction two: Muninn → project, today `muninn handoffs --json` (handoffs.v1)

| v1 handoff | 0.3 record |
|---|---|
| `done` / `dropped` / `already_done` (task) | Acts `settled`, `outcome`, `authority` per the table below; `already_done` carries `related same_as` pointing to the original |
| `reopened` | Acts `reopened`, `supersedes` the previous settlement, `restore_text` |
| `answers` | Decisions `settled`, `outcome: decided`, `answer {text}` |
| "no need to answer" (today answers + a fixed text) | Decisions `settled`, `outcome: dismissed`, `note` says why |
| `resolves` / `changes` / `relevant` (about a to-do with no key) | `note`, `about {quote: <the project's own words>}`; **does not settle** (0.3 says a text reference can only be a note) |
| `resolves` about a registered task | Acts `settled`, authority `attested` (model match) or `confirmed` after asking the owner |
| `changes` with `new_due` | `note` (the premise changed); whether to change the terms is the project's call (only the requester can amend) |
| Muninn merges duplicate tasks | one `related same_as` per project; on settlement one `settled` for each |
| `asks` (the owner asking a project) | **outside 0.3** (core §15): goes through letters as today; a project asking the owner back uses Decisions `raised` + `derived_from {uri: <the asks id>}` |

Kinds of authority (Muninn's existing `DecidedBy` maps almost one to one):

| Muninn | 0.3 `authority.kind` | `decider` |
|---|---|---|
| `USER` (the owner said/pressed it himself) | `principal` | principal identifier |
| `INFERRED_UNSURE` → asked the owner, he confirmed | `confirmed` (`evidence.proposal` = Muninn's proposal) | principal identifier |
| `INFERRED_SURE` (evidence) | `attested` (`evidence.basis`) | Muninn |
| answering on the owner's behalf from his standing preferences (AnswerQuestion kind) | `delegated` (`under` = reference to the preference or standing instruction, `chain` = [principal, Muninn]) | Muninn |
| `AGENT` (said by the project) | this is a claim; when Muninn adopts it, `attested` + `adopts` | Muninn |

`OUTWARD_HELD` (can be withdrawn before sending) = core §10 "a record not yet sent can be withdrawn"; after sending it
can only be replaced by a new record.

## Transport

0.3 does not define transport. Proposal: v2 uses HTTP in both directions, dropping the local-command assumption:

- Project → Muninn: `POST /api/records` (replacing `/api/messages`), the body an array of 0.3 records, the same bearer
  token.
- Muninn → project: pull with `GET /api/records?project=…&after=<cursor>`, confirm with `POST /api/records/ack`
  (replacing `muninn handoffs --json` / `--delivered`). The bridge pulls once a minute (which also solves the speed
  problem of asks).
- The principal identifier and each project's requester identity are set on both sides (core R6): agent-hub in
  `hub.json`, Muninn in its settings.

## Phases (both versions side by side until the readers have switched: the rule for agent-hub contracts)

1. **Coordinate**: Muninn reviews this mapping and fixes the v2 schema (it can vendor the spec's schema directly, or
   wrap it).
2. **Muninn receives and sends v2 first**: `/api/records` and the pull endpoint go live, v1 keeps running; inside
   Muninn, `DecidedBy` maps to authority.
3. **agent-hub switches**: the bridge sends and receives v2; `knas-tasks.py` becomes the Acts registrar (relink, amend,
   claim, withdraw); the librarian applies settlements and judges notes; vendor spec 0.3's conformance test data, and
   agent-hub runs the Acts suite directly (no longer through an adapter).
4. **Cutover**: once both sides have run v2 for a week without trouble, v1 is retired.

## For Muninn to decide

1. Should v2's schema use spec 0.3's records directly, or should Muninn wrap them in its own envelope?
2. Should handoffs become HTTP pull + ack, or keep the local command and add an HTTP version?
3. Is `AnswerQuestion` (writing the answer in the owner's words) `principal` (the owner answered in the conversation,
   Muninn relays it) or `delegated`? My understanding: the owner said the answer in the conversation → `principal`
   (`evidence.basis` points to that part of the conversation); Muninn answers by itself from preferences →
   `delegated`.
4. Should projects be able to object to a merge (same_as) (today's `new_reason`)? 0.3's way: the project sends a
   `note`, Muninn withdraws the relation.
5. When can phase 2 start?

## Muninn's reply (10-27 08:28, 08:48; m10270828-e2bb, m10270848-a373)

- **Settled**: v2 uses spec 0.3's records directly, no extra envelope; asks stay outside 0.3 as in v1; handoffs move to
  HTTP pull + ack, the local command stays during the transition; AnswerQuestion: the owner said it in the conversation
  → `principal` (`evidence.basis` points to that sentence), answering from preferences → `delegated` (Muninn has no such
  feature today, so it will not occur); objecting to a merge: the project sends a note, Muninn withdraws same_as.
- **A rule Muninn added itself**: in a record sent to a project, the text (note, quote, evidence) only carries what that
  project may hear; content from a lease or another project only goes back to the project it came from (Muninn's
  existing only_to/via). Spec core §13 does not cover this direction; it could be written into the spec later.
- **Phase 2 is live** (Muninn 9ef9442, format `contracts/records.v2.md`, the spec's schema and conformance cases
  vendored in `contracts/vendor/principal-handoff/`): `POST /api/records` answers
  accepted/duplicate/unsupported/invalid per record; `GET /api/records?project=&after=` + `POST /api/records/ack`. An
  ack marks the corresponding handoff delivered (it disappears from `muninn handoffs`): while both transports coexist,
  whoever acks first takes it; send the ack only once agent-hub has written it durably. When switching from v1, start
  from the cursor read the first time, not from 0.
- **Principal identifier**: `person:knas`, Muninn itself is `muninn`; agent-hub's hub.json must use the same.
- **Behaviour change (in effect)**: v1's `drop --task`, like v2 acts `withdrawn`, no longer settles it directly when
  only this project wanted it; the owner is asked instead.
- **What phase 3 (agent-hub) has to do**: the bridge switches to v2 (sending records, pull + ack, cursor from the first
  read); `knas-tasks.py` becomes the Acts registrar; the librarian applies settlements and judges notes; questions from
  a lease put `lease:<id>` in `context`; hub.json gets the principal identifier; in agent-instructions the
  explanation of `drop --task` becomes "no longer needed, Muninn will ask the owner". Timing waits for the owner to
  schedule.

## Phase 3 design (10-27, the owner: "start phase 3")

**Approach B: 0.3 records end to end.** Not having the bridge translate at the boundary only (agent-hub would still use
the old vocabulary internally, and decider and kind of authority would have to be squeezed back into the old letter
format, which amounts to patching), but the bus carrying 0.3 records directly, agent-hub's task registration
implemented as the Acts registrar, and the bridge only moving things.

The interface on the bus (internal to agent-hub; both sides write to this):

- New event type `record`: `payload = {"v": 1, "record": <0.3 record>}`. Going to Muninn, `to_project` = the owner
  address (`knas`); coming from Muninn, `to_project` = the record's `project`, `source` = the owner address. Event id =
  `rec:<ext>:<project>:<record id>`, so a resend is a no-op (core §10's deduplication scope).
- Query: `hubbus records --project P [--ext acts/0.3] [--type settled,reopened] [--pending]` prints a JSON array
  (replacing `open-handoffs` for the registrar); `--pending` = those this project has not `hubbus done` yet. After
  applying, `hubbus done <event id>` as before.
- The convenience command `hubbus send` for sessions stays as it is (`--type task/question/done/drop/progress`); hubbus
  translates it into a record (mapping table in "Direction one" above), so sessions learn no new syntax; only the
  meaning explained in agent-instructions changes.
- Bridge (v2): outwards, `record` events with `to_project = owner` are batched into `POST /api/records` (status checked
  per record; invalid ones are recorded, not retried); inwards, `GET /api/records?project=&after=` pulled every minute
  and written as `record` events, and **only after the write to the bus is committed** `POST /api/records/ack`; the
  cursor starts from the first read (not 0). v1's messages/handoffs forwarding coexists until the cutover.

Split of work (proposal):

| Part | Who |
|---|---|
| `bin/hubbus`: `record` event, `records` query, `send` translated into records, how inbox shows records, bridge v2 | the Agent Hub session (hubbus is its) |
| `hub.json` gets `principal` (`person:knas`), `contracts/hub.v1.schema.json` | this session |
| `knas-tasks.py` → Acts registrar (relink, raise, amend, reconcile→claim, withdrawn without claim, `r:`/`a:`/`c:` ids, terms_rev) | this session |
| librarian: settlements applied mechanically by the registrar; notes and Decisions settled judged by the model (prompt changes) | this session (after switching to librarian.py) |
| agent-instructions: `drop --task` = no longer needed, Muninn will ask the owner; explanation of Decisions replies | this session |
| vendor spec 0.3's Acts suite, run directly by the registrar (replacing the HTH 0.1 adapter) | this session |

Order: first write characterization tests that lock in today's knas-tasks behaviour (tests-before-rewrite), then
change it; define the bus `record` interface first so both sides can work in parallel; run the whole set through the
nightly flow in an isolated environment (a fake Muninn API + copies of the bus/STATE.md) before going live; v1 and v2
coexist for a week.

## Cutover (10-27 14:49)

The owner: "if the isolated rehearsal runs clean once more, cut over before 03:00 tonight (the job switches to
registrar.py + mailbox.records on)". The Agent Hub session reran the isolated rehearsal (real data copied afresh,
including 0fe4bda) and it was clean: on the cutover night the registrar sends only 1 record (one project's hand-made
`task-<project>-funnel-f588`, with no record on the bus, going to Muninn for the first time), and 0 on the second run.

- `modules/assistant.yaml`: `assistant/knas-tasks` now runs `bin/py bin/registrar.py` (8fecd6a; the job name and
  scheduling state carry over)
- config `hub.json`: `mailbox.records: true` (c265a2b); bridge v2 ran for the first time at 14:49:26 and set 28
  starting points (including the old name idea-1)
- Tonight at 03:00 `librarian.py` and `registrar.py` go live together for the first time; to check tomorrow morning:
  the registrar's log (should have only 1 raised), whether the librarian has v2 notes/answers, whether the bridge has
  errors outwards or inwards.
- After a week side by side (around 11-03): remove `knas-tasks.py`, the registrar's `v1_compat`, `librarian.sh` (this
  one can go tomorrow), the HTH 0.1 vendor copy and `tests/test_hth_conformance.py`; Decisions (questions) are still on
  v1, the Agent Hub session does that next.
  Done so far: `librarian.sh`; `knas-tasks.py` on 11-01 (i18n step 7: nothing scheduled or called it any more; the HTH
  0.1 key and apply vectors now run against `registrar.py`). Left: `v1_compat` (the v1 letters on the bus) and the HTH
  0.1 copy with its test.

## Decisions switched over (10-31)

Questions now go the same way as tasks once `mailbox.records` is on (it is on here):

- `send --type question` to the owner → decisions `raised`, `key` = the letter id (the id `--question` takes later,
  and the id the assistant keeps the question under); `done --question` → `claimed` with the answer, then `withdrawn`
  (`settled`); `drop --question` → `withdrawn` (`not_needed`). A question asked before the switch is closed the same
  way under its letter id. A question replying in an asks thread stays a v1 letter (asks is outside 0.3; the assistant
  ties it back by `handoff_id`). info / progress stay v1.
- The assistant's decisions `settled` come in as records (no longer rewritten into a v1 answer letter), with the asking
  session (`to_session`) and the question beside the record on the event; v1 `answers` handoffs are no longer imported.
  The session carries on with the answer and marks it done; otherwise tonight's librarian applies it (`load_records`).
- `open_questions` (session start ❓, `open-questions`, `reconcile-questions`, `owner.py list`) reads both v1 letters and
  records: closed by an answer (v1 letter or `settled`) or by the asking project (v1 done/drop or `claimed`/`withdrawn`),
  opened again by `reopened`.
- The built-in owner side's `owner.py answer` writes decisions `settled` (authority `principal`) like the assistant.

## Acts 0.4 (11-01)

agent-ops-specs published Principal Handoff 0.4 (tag `principal-handoff-0.4`, 81a0486), which answers agent-hub's
m11010359-3de9 (reply m11010550-9894). What agent-hub does now:

- **The document layer is 0.4.** `bin/acts_md.py` is the Markdown binding, after the spec's reference registrar, but it
  reads a file in both token sets at once (state-todo v2 allows a mixed file) and writes the content language's set.
  The registrar and gen-todo read through it. In practice:
  - the registrar works only on **bound keys**, the ones it raised from a line;
  - a key whose marker is anywhere in the file is present; if it is off the owner lines it is **displaced** (no claim,
    one note `d:<key>:r<n>` per open period);
  - a ticked owner box is claimed done;
  - new to-dos are raised only from the first to-do section, and every section is read;
  - fences and HTML comments are not read, and an unclosed one leaves the file alone;
  - a settlement that is stale, or whose line was edited, strikes nothing.

  The conformance fixtures of 0.4 run in the spec's profile and in agent-hub's two-set reading
  (`tests/test_acts_conformance.py`). The 0.3 and HTH 0.1 copies are retired.
- **Records: read 0.3 and 0.4, send 0.4** (`bin/ph_wire.py`). The assistant took 0.4 the same day and answers each
  project in the version it last sent (m11011416-bcb1). The bridge also stopped acking a page that held a record it
  refused, since that record would otherwise be lost. Every `settled` the built-in owner side writes carries a
  `terms_digest` (core §5.3), checked against the spec's vectors. Decisions 0.4 approvals are not implemented: the
  reference deployment leaves approvals to the assistant alone (Knas, 11-01), and `owner.py` has no approval command
  and settles only when no assistant is configured.
- **The `en` set keeps the profile's default `principal_tag`, `owner`**, whoever the owner is. An English STATE.md then
  reads the same on every instance. The zh-Hant set's tag stays the configured owner tag.
- **Keys a project assigns itself** (`send --type task` without `--id`) are now `ask-<project>-<hash>`. The registrar's
  `task-` prefix is its own (acts §6.1). Before this, a project's key could be copied onto a STATE.md line and pass for
  a bound one.
- **A STATE.md item changes only through its line**: `send --type task --id <a registrar key>` with other terms is
  refused (acts §4).

The live dry run of the registrar, main against this branch (both against the real bus and state), differed in two
places, both 0.4's intent:

- One project's key (the one in `registrar-warned.json`) gets a note where it used to get nothing. The key sits on
  a to-be-decided line, which makes it displaced.
- Another project gets a new `raised`. Its line carried a key the project's own session had raised (`task-` prefix,
  from before the `ask-` change), which an agent then copied onto the line. That key is not bound, so the line is
  raised under the key derived from its text. The project's own item stays open at the assistant with the same text.
  The assistant can relate the two (`same_as`), or the owner settles one as `already_done`.
