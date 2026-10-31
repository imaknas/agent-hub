# A lease contract independent of the runtime (m10251446): design, awaiting the owner's confirmation

> **Status**: the owner confirmed A–E on 10-25 (deadline changed to at most one day) and it was finished the same day:
> `f6df0e9` (contract v2 + v1 compatibility), `2482a30` (approvals + relay). The same day the assistant switched to v2
> (Muninn `dd3bd3b`) and also issued permission requests v2; the v1 compatibility layer and the v1 schema were removed
> right away (the schema moved to `contracts/retired/`), and permission requests are sent only as v2 (the
> `lease.permission_requests_v` setting is no longer needed).

Source: the assistant's request `m10251446-318e` (10-25 14:46, direction already agreed by the owner): turn
`contracts/lease-runner.v1.schema.json` into generic categories independent of the agent implementation, with Claude
Code's names translated on the runner's side. Done when (the assistant's criteria): no Claude Code tool names or
Claude-subscription-specific fields remain in the contract; the runner has a translation table and tests; the reply
says clearly until which day the old and new formats run side by side.

Relation to the two other documents:
- `docs/design/capabilities.md` (layer 2's neutral policy `policy.py`, translated by `runtime_claude.settings()`): this
  document adds what layer 2 still lacks, "which actions need asking a person" (`approvals`).
- `docs/design/runtime-abstraction.md` (the overall design, another session): this document's "event translation" is
  the content of its layer 1 `events()`; for the split see "Split of work with the overall design" (aligned 10-25).

## Where it is tied today (against the five points the assistant listed, all checked in the code)

| # | What is tied | Where in the code |
|---|---|---|
| 1 | Event `tool_use.name` is Claude's tool name | `lease-runner.reader()`, `summarize_tool()` |
| 2 | Permission requests' `tool` (AskUserQuestion / WebFetch / Bash); `question` is AskUserQuestion's input as is; events `question`/`permission` also carry `tool` | `lease-relay.decide()`, `ask_owner()`; the runner turns the relay's reports into events unchanged |
| 3 | `rate_limit`'s `five_hour`/`seven_day` (+`_resets_at`, epoch seconds), in both the event and `/v1/status` | `reader()`'s `rate_limit_event` |
| 4 | The command `/terminal` returns; resuming relies on Claude Code's conversation log (kept 30 days), and when the log is gone it **silently** opens a new conversation | `start_proc()`'s `can_resume`, `bin/lease-resume` |
| 5 | Error code `auth` really means "Claude is not logged in" | guessed from stderr at the end of `reader()` |

Two more places the assistant did not list, but also Claude's words: the description of `/v1/policy`'s
`no_session_domains` says WebFetch; in the events reported by the relay, `kind: question|permission` duplicates the
event type (and the new format wants `kind` for the kind of action, see below).
`lease-handover.v1` was checked: no Claude words.

## One set of action kinds, shared by the whole platform

```
read | search | ask_user | open_web | write_file | run_command | other
```

- Exactly the set the assistant proposed. **The same set** is also used by layer 2's `approvals`, the judgment point
  declarations (`llm-points.yaml`), and the overall design's conversation-log statistics: the overall design's
  principle 8 already says so; this document fixes the list.
- The list lives in `runtime_ports.py` (neutral, `KINDS`); **the Claude tool name → kind table is only in
  `runtime_claude.py`**: `Read`→read; `Glob`, `Grep`, `WebSearch`→search; `WebFetch`→open_web; `Edit`, `Write`,
  `MultiEdit`, `NotebookEdit`→write_file; `Bash`→run_command; `AskUserQuestion`→ask_user; everything else (`Task`,
  `TodoWrite`, MCP tools…)→other. `hub-run` is run_command (the summary says which capability).
- Translation function: `runtime_claude.action(tool, input) -> (kind, collab, target, summary)`: target is for
  decisions (path, URL, command), summary is one sentence for people. The runner's events, the relay's permission
  decisions and the conversation-log statistics all use it rather than each writing their own.
- **Another dimension: how work is shared** (`collab`, separate from kind, defined in one place only):
  `subagent | workflow | peer_message | skill` or None. kind says "the effect of this action" (used for permissions and
  approvals), collab says "how work is divided" (used for the weekly statistics): handing to a sub-agent, running a
  workflow, messaging across sessions, using a skill are all other in kind, invisible to the statistics, so it is
  carried separately. `runtime_ports.Action` becomes `Action(kind, detail, collab=None)`; the overall design's original
  `ACTIONS` and `runtime_claude._action` are retired by that side once `action()` is done.

## Contract: v2 and v1 side by side, the assistant picks the day to switch

**Approach (proposed)**: add `contracts/lease-runner.v2.schema.json`, and change **not one character** of the v1 file.
- The assistant chooses the format with `v`: a request with `"v": 2` gets v2's shape; SSE uses
  `GET /v1/leases/<id>/events?v=2`. Without it, it is v1, byte for byte the same behaviour as today. So the assistant
  can switch over in one go, without handling "old and new fields in the same event".
- This is the CLAUDE.md rule "renaming/changing meaning bumps the version, both versions coexist until the readers have
  switched"; no fields are added to v1 to run in parallel, because `kind` already has another meaning
  (question|permission) in the `question`/`permission` events, and adding the field would clash.
- Inside the runner only v2 events are produced; v1 is a small function turning them back into the old shape
  (`v1_compat()`), deleted entirely on the deadline.
- **Deadline: deleted as soon as the assistant reports it has switched, 2026-10-26 at the latest** (the owner on 10-25:
  only he uses it, iteration should be fast, no need to wait). After that the runner only accepts v2, and the v1
  schema file moves to `contracts/retired/`.
- The nightly contract check is unaffected: the v1 file has not changed, so the assistant's copy stays consistent; v2 is
  a new file, compared only from the day the assistant adds `consumes` to its registry.

**Differences between v2 and v1** (other fields and endpoints unchanged)

| | v1 | v2 |
|---|---|---|
| Event `tool_use` | `name` (Claude tool name), `summary` | `kind`, `summary`, `detail: {tool}` (the original name, for debugging; the assistant does not read it) |
| Event `question` | the relay's `phase/tool/summary/kind/decision` as is | `phase`, `kind: ask_user`, `summary`, `question` (generic structure, see below), `decision` |
| Event `permission` | same | `phase`, `kind` (open_web / write_file / run_command…), `summary`, `target`, `decision` |
| Event `rate_limit` | `five_hour`, `seven_day`, `*_resets_at` (epoch seconds), `status` | renamed `quota`: `{status: ok\|warning\|exhausted, windows: [{id, label, used(0–1), resets_at(ISO)}]}` |
| `/v1/status` | `rate_limit: {five_hour, …}` | `quota`: same as above, plus `at` |
| `/terminal` response | `{command}` | `{command, hint}`; command built by the runtime (still `lease-resume <id>` today), hint is one sentence for people |
| Resuming | silently opens a new one when the log is gone | the `state` event carries `resumed: true\|false`; not resumed → `resumed: false` + `detail` ("The earlier conversation is gone, starting a new conversation"); the `lease` object gets `resumable` |
| Error code | `auth` | `agent_login`, with a human-readable `detail` ("Claude Code used by project sessions is not logged in or its credentials have expired; the platform sends an alert") |
| `/v1/policy` | `no_session_domains` described with WebFetch | described with open_web (field unchanged) |

- The `rate_limit` error code (quota used up) stays: the word itself is generic.
- Claude's implementation of `quota.windows`: `five_hour` → `{id: "five_hour", label: "5 hours"}`, `seven_day` →
  `{id: "seven_day", label: "7 days"}`; `status` maps Claude's allowed /
  allowed_warning / rejected to ok / warning / exhausted. The assistant only displays the label and judges by used.
- `session_id` (the UUID the assistant gives when creating a lease) stays: it is the lease's own id; "must be a UUID"
  is written as a runtime limitation, not part of the contract's meaning.

## Permission requests (the assistant's contract `permission-requests`)

This one is **defined by the assistant and vendored by us**
(`contracts/vendor/muninn/permission-requests.v1.schema.json`), so the assistant issues v2 and we send accordingly.
The proposed shape (written in the reply to it):

```
request:  {v: 2, id, lease, project, kind, summary, target?, question?, detail?: {tool}}
  kind      ask_user | open_web | write_file | run_command (the only kinds that get sent to ask)
  question  {questions: [{question, options: [{label, description?}], multi?: bool}]}
            generic structure, not AskUserQuestion's input
decision: {v: 2, decision: allow | deny | allow_session, answers?: {question: label or [label…] (multi)}}
```
- Drops `tool`, `input_summary` (renamed `summary`), `risk` (duplicates kind: question→ask_user,
  external_fetch→open_web).
- On Claude's side the answers of a multiple choice have to be joined into one string; that is `runtime_claude`'s job,
  the contract uses a list.
- The relay sends per the version the assistant gives; until the assistant has switched it sends v1 (the relay reads the
  `v` the assistant returns, or a setting).

## "Which actions need asking a person": approvals (the piece layer 2 gains)

Today this is hard-coded in `lease-relay.decide()`, decided by Claude tool names. It becomes part of the neutral policy
(`policy.approvals(name, mode)`), and the relay is left with only the adapter "Claude's permission protocol ↔ neutral
decision":

```
discuss:  read/search → allow; open_web → registered domains allow, others ask; ask_user → ask;
          write_file/run_command/other → deny
work:     same, plus write_file → allow inside the project folder, deny outside;
          run_command → ask (every time; allow_session counts for this one only)
```
- `ask` = send to the owner (the assistant → the owner's phone). The guard rails of `allow_session`
  (`no_session_domains`, a command can never be allowed for the whole lease) are written here too.
- The rules are the same as the owner's decisions of 10-25, **behaviour unchanged**; the existing
  `tests/test_lease_relay_work.py` all pass as they are.
- The `tools`/`allowed_tools` of the judgment point declarations (`lease-session`/`lease-work` in `llm-points.yaml`)
  also become lists of kinds, translated by `runtime_claude` into `--tools`/`--allowedTools`. This touches
  `llm-points.v1.schema.json`, shared with the assistant: the overall design's layer 1 was going to do that anyway; this
  document only provides the format of kinds and approvals (split below).

## Split of work with the overall design (aligned with that session 10-25)

The boundary: **this document is "translation and contract", the overall design is "process lifecycle"**.

- **This document (this session)**: `KINDS`, `StreamEvent` (the neutral conversation event, the shape of the v2 table;
  not named `Event`/`HubEvent`: `HubEvent` is already the name of layer 3's hook events), `Action` gaining `collab`, all
  three in `runtime_ports.py`; `runtime_claude.action()` (tool name → kind, collab),
  `runtime_claude.parse_stream(line) -> [StreamEvent]`, translating quota / error classification / the resume command,
  `policy.approvals()`, splitting the relay into an adapter, contract v2 + the v1 compatibility layer, coordinating with
  the assistant. It touches `lease-runner.py`'s `reader()`, event serialisation, `/terminal`, `/v1/status`, and
  `lease-relay.py`.
- **The overall design (the other session)**: layer 1's long-running session interface
  (`open_session/send/events/resume`) and lease-runner's process management (building flags, starting and stopping
  processes, deciding `--resume`), scheduled after this one, with `events()` using `parse_stream` directly;
  `SessionSpec` carries `approvals = policy.approvals(name, mode)`, which the runtime connects to its own permission
  mechanism. **Making `llm-points` neutral is also that side's work** (all judgment points switched at once, the schema
  shared with the assistant changed only once), using this document's `KINDS` and approvals format; the
  `lease-session`/`lease-work` points are scheduled after this document's relay changes (allowed_tools and the relay's
  behaviour have to be tested together). **This document does not change `llm-points.yaml`.**
- The conversation-log statistics switch to `action()`'s collab, and the `tool_used` hook event later carries kind too
  (done on that side).
- **Order of files**: that session is currently changing `runtime_ports.py` and `runtime_claude.py` (layer 3
  lifecycle); this work waits for its commit before touching those two; while this work is in progress, that side does
  not touch `lease-runner.py` or `lease-relay.py`, and is told after the commit.
- That side's `runtime-abstraction.md` adds three things from here: SessionSpec referencing approvals, v2/v1 side by
  side as here, HubEvent and StreamEvent kept apart.

## Tests

- Translation table: every known Claude tool has a kind, unknown ones are other; `action()`'s target/summary.
- Event translation: record a real stream-json (with tool_use, rate_limit_event, result, errors) → neutral events,
  compared item by item.
- **v1 compatibility**: for the same recording, the v1 output is byte for byte the same as today's runner (record
  today's output as the baseline before changing anything).
- The v2 contract's examples all pass `tests/test_contracts.py`; the boundary test adds the decision parts of
  `lease-runner.py` and `lease-relay.py` to `NEUTRAL` (Claude's words may only appear in `runtime_claude.py`, `llm.py`
  and the relay's few MCP adapter lines).
- Relay: the existing `test_lease_relay_work.py` all pass (behaviour unchanged), plus approvals tests for discuss.
- The fake runtime (`runtime_fake`) produces the same neutral events too, and the contract tests run both (overall
  design principle 3).

## For the owner to confirm

**A. v2 and v1 side by side, the assistant picks with `v`; v1 is deleted once the assistant has switched, 2026-10-26 at
the latest** (confirmed 10-25). No fields added to v1 in parallel (`kind` would clash).
**B. One set of action kinds** (the seven above), used by the contract, approvals, judgment points and the
conversation-log statistics.
**C. "Which actions need asking a person" moves into the neutral policy** (`policy.approvals`), the relay keeps only
the adapter; rules as today.
**D. The split with the overall design** (previous section, both sessions aligned): this one does translation and
contract, that one does process lifecycle and llm-points; this one goes first.
**E. Permission requests v2 are issued by the assistant** (its contract); we attach the proposed shape to the reply.

## Order (one commit per step)

1. (After the overall design's session commits layer 3) record today's runner output as the v1 baseline; `KINDS`,
   `StreamEvent`, `Action.collab`, `runtime_claude.action()`, `parse_stream()`; inside the runner produce v2 events, v1
   through the compatibility layer (v1 output byte for byte the same).
2. The v2 contract + choosing with `?v=2`/`"v": 2` + `quota`, `/terminal`'s hint, `resumed`/`resumable`, `agent_login`.
3. `policy.approvals()` + the relay split into an adapter + permission requests sent per the assistant's version; when
   done, tell the overall design's session (it then switches llm-points and builds the long-running session
   interface).
4. Reply to `muninn` (new format, deadline, proposed shape of permission requests v2); `hubbus done`.
5. Deadline: delete the v1 compatibility layer, move the v1 schema to `contracts/retired/`.
