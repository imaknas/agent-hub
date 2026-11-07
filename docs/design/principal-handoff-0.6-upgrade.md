# Principal Handoff 0.6 upgrade (11-06)

agent-ops-specs published Principal Handoff 0.6 (tag `principal-handoff-0.6`, 9e69648, release-2026-11-05) and 0.6.1
(tag `principal-handoff-0.6.1`, a8950e4, release-2026-11-05b: the same text and schemas, 705 more fixture cases,
`core/conformance/raw.json` and `OPEN-READINGS.md`). Its requests to brunna are m11051656-41cf and the follow-up in the
same thread. This document is what brunna did and the three reports they asked for. The earlier versions are in
[principal-handoff-0.3-migration.md](principal-handoff-0.3-migration.md).

## What brunna does now

- **Reads 0.3 to 0.6 and folds in 0.6** (`bin/ph_wire.py` READ, LATEST). brunna implements 0.5 and 0.6 both, so in
  core §14's terms it is a converting party configured for 0.6, whatever it sends: `principal_handoff.send_version` is
  the version its counterpart, the principal side, is configured for.
- **Held records (core §6)** (`bin/ph_records.py` `held_form`, `received`). A received record is held when it is in a
  version brunna implements, valid by that version's own rules as it was sent (0.6 by 0.6's, 0.5 by 0.5's even where 0.6
  only relaxes a rule, 0.4 by 0.4's), and has a 0.6 form; it is held in that form, and core §10's identity and
  keep-the-greater rule compare the 0.6 forms. The records brunna made itself (origins requester, agent and registrar,
  and principal when the owner side is the built-in one: `Config.own`) are held as their 0.6 form, validated in 0.6;
  their 0.5 forms exist only on the wire. 0.3 and 0.4 are outside core 0.6 (§14 covers N−1 and N); brunna keeps its own
  reading of them, converting one version at a time.
- **Conversion** (`ph_wire.convert`, one version at a time): 0.5 ↔ 0.6 from the record alone (`ph`, `ext`, a relation's
  `target.ext`, `evidence.proof` dropped and `evidence` with it when left empty); a 0.5 relation whose target's
  extension name or project does not fit 0.6 has no 0.6 form and is not held. 0.4 ↔ 0.5 still replaces a settlement's
  digest by that of the held need record, and 0.3 → 0.4 adds it.
- **Relations** (`ph_records.relations`, `replacement`): identified by (kind, extension name, project, key) or (kind,
  uri); the latest record decides; `same_as`/`derived_from` naming the item itself have no effect. brunna does not act
  on relations (it is a requester); this is what the convert `held` cases and `relations.json` check.
- **Versions brunna does not implement are kept** (core §4, §6): the bridge takes such a record from the principal side
  into the bus and acks it (`hubbus incoming_errors`); nothing acts on it (`ph_wire.family` is empty for it, so the
  registrar, `open-questions` and the answer routing skip it), and the inbox shows it as "a version brunna does not
  read: kept, not acted on". Once brunna implements that version it is held, since it is on the bus as it came.
  Before this upgrade such a record was refused, and the pull for that project stopped at it.
- **I-JSON at the text** (`ph_wire.loads`, `parse`): what brunna reads (`record-publish`'s stdin, the principal side's
  HTTP bodies) marks an object that names a member twice, and that record alone is refused (the bus could not keep it
  as it came); NaN and Infinity are not JSON; a fraction, an exponent or an unpaired surrogate makes the record invalid
  when it is validated. From the principal side, a refused record stops that project's pull at it, logged, as every
  refused record does (the bridge never moves past a record it did not take, so nothing is lost unseen).
- **Sending 0.6 waits for the principal side** (below, report 3).

`send_version` stays as it was: the default in code is `"0.4"` and this instance has `"0.5"`. Neither was changed.
(The request to this work said "the default stays 0.5"; the code's default is 0.4, as before. Moving it is a separate
decision: a new install whose principal side reads 0.5 would then not need the setting.)

## Report 1: the 0.6.1 suites

brunna is a requester (bin/hubbus send, the registrar), the Acts registrar and a converting party. Its built-in owner
side (`bin/owner.py`) makes settlements but computes no principal-side state (claims, conflicts, ignored), so the
principal-side comparisons of `lifecycle.json` are not claimed. Every applicable suite runs in the test suite
(`tests/test_ph_records.py`, `tests/test_ph_wire.py`, `tests/test_acts_conformance.py`), in the fixtures' order and
reversed where order could matter.

| Suite | Cases | Passed |
|---|---|---|
| acts/conformance/records.json (incl. 1 `valid: null`) | 262 | 262 |
| decisions/conformance/records.json (incl. 1 `valid: null`) | 279 | 279 |
| acts/conformance/lifecycle.json, settlement view | 73 | 73 |
| decisions/conformance/lifecycle.json, settlement view | 78 | 78 |
| acts/conformance/relations.json | 15 | 15 |
| decisions/conformance/relations.json | 15 | 15 |
| core/conformance/raw.json (both extensions) | 50 | 50 |
| core/conformance/terms-digest.json | 7 | 7 |
| core/conformance/convert.json cases (the 2 Criteria cases are for parties that implement Criteria) | 28 | 28 |
| core/conformance/convert.json held | 12 | 12 |
| acts-markdown: parse, keys, register, relink, amend, apply, reconcile | 27, 8, 13, 14, 10, 34, 17 | all |

The binding cases run twice, in the case's own profile and in brunna's reading (both token sets). 0.5's and 0.4's
suites keep running by their own rules, and `tests/test_ph_characterization.py` pins, from before the upgrade, what
every 0.5 and 0.4 lifecycle case holds, ignores and folds to: unchanged.

No case fails, and none is recorded as an expected failure. **Corrected 11-07:** this was first reported with the harness
counting "kept" as "invalid", which hid `without-ext` (both extensions) and `an-empty-object`: a record with no `ext` was
judged kept, and core §4 makes it invalid. The strict counts then were 261/262, 278/279 and 49/50. Fixed with the harness
tightened, so these numbers now hold. Cases that failed on the way, all decided as "the implementation changes":

- `type-not-a-word`, `terms-not-an-object`, `authority-not-an-object`, `evidence-not-an-object`,
  `note-about-not-an-object`, `relation-not-an-object` (both extensions' records.json) and
  `record-with-a-non-string-type-is-ignored` (both lifecycle.json): the validator raised an exception instead of
  calling the record invalid, and a type that is a list stopped the bus's deduplication. The validator now stops at the
  schema's errors before reading members (as the reference does), for 0.4, 0.5 and 0.6 alike; a malformed record from
  the principal side would have stopped the registrar before this.
- `approved-is-a-listed-pair` (decisions/records.json): brunna named `approved` as the schema; core §4 says the
  extension assigns a module pair no schema of its own, and the fixture's `schema: null` agrees. Changed for 0.6 (0.5's
  records.json named the type, and still passes).

## Report 2: the open readings, as brunna actually does them

Each row's record was fed to brunna (`ph_wire.parse` where the row is about text, `ph_records.validate`, `received`,
`settlement_view`), without changing code for any reading.

### Validity

| Case | brunna |
|---|---|
| `decisions/0.5` with `ph: "0.6"` | Invalid: brunna reads the version from `ext`, validates by 0.5's rules, and 0.5's schema wants `ph` "0.5". Not held; it stays on the bus. |
| `ext` of `decisions/0.6.1`, or `decisions` | Kept, not acted on: neither names a version brunna implements. |
| `note` with a non-date `terms.due`; a member on a type that doesn't list it | Validated, for members the core record schema defines: `terms.due` of `2026-02-30` or `tomorrow` on a note makes it invalid. A member only an extension's other type defines (`answer: 1` on a note) is not checked: valid. |
| `option.label: 1` | Invalid. |
| `terms_rev: 0` on `withdrawn`, `reopened` or `related` | Invalid. |
| `evidence.presented` off-pattern or not a string | Invalid. |
| Malformed `terms_digest` | Invalid. |
| An id containing whitespace in `supersedes` or `adopts` | Invalid. |
| `-0` | Valid (read as the integer 0). |
| `dismissed` carrying `answer` | Valid, and the settlement view shows the answer. |
| `reopened` with `supersedes: []` | Valid; it is the current settlement and the item is open. |
| `:60` at a minute other than 23:59 UTC, or on a day with no leap second | Valid. |
| A byte order mark at the start of the text | Two answers inside brunna: rejected when the text comes as a string (`record-publish` from a project), ignored when it comes as bytes over HTTP from the principal side (Python's JSON reader strips a UTF-8 BOM from bytes). |
| U+2028 in Acts `terms.text` | Allowed (only LF and CR break the single line). |

### State

| Case | brunna |
|---|---|
| A `withdrawn` with `terms_rev: 9` over a settlement at 1 | Not stale: only held `raised` and `amended` count. |
| `:60.5` against `:59.999999` | Equal; the greater id decides. |
| A record listing itself in `supersedes`, when a record of another type has the same id | It names only itself: the other record stays a head, so there are two heads and a conflict; the current one is the later (here the `amended`), in either order. |
| A record of an older version in a `lifecycle.json` case | Acted on for the versions brunna implements (0.3 to 0.5); a version it does not implement (0.7) is kept and not acted on. |

The two "suite format" rows don't arise: as a requester brunna compares neither `claims` nor `ignored`.

## Report 3: ending a transition (core §15)

### How brunna stops sending 0.5

Every record is made in `send_version` at the moment it is made (send, the registrar, the built-in owner side), so the
switch is the setting. It is one edit of the settings, made once the principal side's release says it reads 0.6:

    "principal_handoff": {"send_version": "0.6", "principal_side_reads": ["0.5", "0.6"]}

`principal_side_reads` is the guard: the versions the owner confirmed the principal side reads (not set: 0.4 and 0.5).
config-check refuses a `send_version` that is not among them, and so does `ph_wire.send_version()` at run time (loudly:
nothing falls back to another version). With the built-in owner side the guard is met by itself, since that side is
brunna and reads what brunna reads.

Why a configured confirmation and not something seen on the wire: core §14 makes each party's version configuration,
set out of band like roles, and there is no honest in-band signal before the switch. Muninn's API says nothing about
versions; it tells each project in the version configured for that project (first seeded from what the project spoke),
so its first 0.6 record can only come after brunna switches; and the only probe would be sending a 0.6 record, which
Muninn 0.9 answers `unsupported` and does not keep. So the owner, who runs both sides, states it, and brunna holds them
to it: if the principal side still answers `unsupported` to a record in the version brunna sends, the bridge keeps the
whole batch and sends it again (the accepted ones as duplicates) until `send_version` names a version it reads,
instead of letting those records go; the batch's other answers (an invalid record's job.failed) are acted on when it
finally goes.

brunna does not stop reading 0.5: it stays a converting party for as long as anything in 0.5 can arrive or is held.

### What brunna does with the 0.5 records it holds

On the bus at the upgrade: 206 records, 140 in 0.5 (118 Acts and 14 Decisions sent by brunna's projects and its
registrar, 8 Acts from the principal side), 65 in 0.3 (49 sent, 16 received), 1 in 0.4 (sent), none in 0.6.

- **Received** 0.5 records stay on the bus as they came (the bus is append-only). At each fold they are validated by
  0.5's rules and held in their 0.6 form; nothing is converted in storage.
- **Sent** 0.5 records stay as sent. brunna holds their 0.6 forms, validated in 0.6. It does not send them again in
  0.6: the registrar skips any (origin, type, id) a project already sent, in whatever version, and the principal side
  deduplicates across versions. Later records refer to them by id (`supersedes`), which no version changes, and the
  terms digest is the same in 0.5 and 0.6, so settlements made against 0.5 need records are not stale for that.
- **0.3 and 0.4** records (66) are read as before, outside core 0.6: they are what "N−1 records after a transition"
  becomes once a transition has ended. Nothing is deleted or rewritten.

### Records in flight at the switch

- **brunna to the principal side.** The bridge forwards every minute, and converts each record to the version it
  sends at the moment it forwards it (`hubbus outgoing`): a 0.5 record made before the switch and forwarded after it
  goes as 0.6 (core §14 item 2: a converting party sends N to a counterpart configured for N; 0.5 → 0.6 is
  record-local), and if the setting goes back from 0.6 to 0.5, a 0.6 record not yet forwarded goes as 0.5 (every 0.6
  record has a 0.5 form). 0.3 and 0.4 records go as they are. A batch that fails is sent again whole, and one the
  principal side answers `unsupported` is kept (above).
- **The principal side to brunna.** brunna reads both versions whenever they come, so nothing waits on the order of the
  two switches. A record in a version brunna does not implement is now kept, not refused.
- On the bus, one record sent in two versions is two events (the event id names the `ext`); readers compare the 0.6
  forms and take it once.

### Problems met

1. Before this upgrade, a record of a version brunna did not read stopped the pull for its project for good (the
   bridge never moves past a record it did not take). Had the principal side moved to 0.6 first, every project's
   inbound records would have stalled. 0.6's "keep" rule fixes a real hazard; the principal side needs it as much
   (Muninn 0.9 consumes an unknown version without keeping it).
2. There is no way to learn the counterpart's version without sending, so the guard is the owner's confirmation. A
   version advertisement, or a status that says "kept, not read yet", would let a party check before it switches.
3. "Configured for" was ambiguous here: brunna implements both versions and folds in 0.6 (configured for 0.6, in §14's
   terms) while it sends 0.5. Reading `send_version` as the counterpart's configured version resolves it.
4. §6 holds "those it emitted" with no validity condition. brunna still validates its own records' 0.6 forms and
   ignores (and logs) one that fails, which it never makes on purpose.
5. Records two versions back are outside §14, yet a party that has lived through two transitions holds them (66 here).
   brunna keeps converting them one version at a time; 0.4 → 0.5 needs the held need record, so that step is not
   record-local.
6. A byte order mark is read differently depending on whether the text arrives as a string or as bytes (report 2).
   And a record from the principal side that names a member twice still stops its project's pull (it cannot be stored
   as it came), the same hazard class as problem 1, kept deliberately: it is malformed, not merely newer.
7. The validators of 0.4 and 0.5 raised exceptions on malformed shapes (report 1); found only by 0.6.1's new cases.

## Live read-only check

Against a snapshot of the bus (opened `mode=ro`, copied with SQLite's backup API), the config, data and `.state`, the
code of main and of this branch ran `registrar.py --dry-run`, `hubbus open-questions --json` and `hubbus records
--project P` for every project name on the bus and in the roster (29). Every output, error output and exit code is
byte-identical: no difference to explain. The one warning the dry run prints (a project's line carrying a key its own
session raised) is the same on both.
