# Bringing back an earlier STATE.md: the owner's items and the lock

Status: design, revised after its first blind review (2026-11-09; three reviewers: the rule against the registrar and the
Principal Handoff spec, locking and failure, scope). Replaces the key handling and adds the locking of
`bin/state.py restore` (shipped in v0.9.0, hardened in v0.10.0).

## The problem

`brunna state restore <project> <date|commit>` writes an earlier version of a project's STATE.md back as a new commit in
the platform's memory copy, to undo a night when the librarian's model got the file wrong. STATE.md's to-do section also
holds the owner's items, each stamped with a key (`(waiting on owner: task-…)`, or the zh-Hant set's marker; `bin/todo_tokens.py`) that ties it to a
Principal Handoff item on the bus. Those items have their own source of truth, the bus, which the registrar keeps the file
in step with every night (`bin/registrar.py`, steps 1-5). Restoring the old text naively revives items the owner closed
since and loses items opened since.

v0.9.0 decided from the text alone (a key missing from today's file = closed). The blind review of 11-03 found that wrong
in five ways, the worst being the case restore exists for: when the librarian deletes an owner line by mistake (its edit
check allows deleting a whole keyed line, `bin/edits.py`), the key is missing from today's file, so restore called the
item closed. With the built-in owner side the loss is final: `bin/owner.py` can answer done, drop or already-done, never
reject a claim or reopen an item. And there was no lock against the nightly jobs.

## The rule: the owner's items come from today, the rest from the restored day

Let T be the restored version's text, C today's text (both read at the same HEAD, below), L the registrar's ledger
(`registrar.Ledger(project)`: what each key's records say, read from the bus and validated by `bin/ph_records.py`). The
owner's entries are found with the same reader the registrar uses: `acts_md.Doc`, entries whose owner is the principal,
of any kind (an open line, a ticked box, a struck line, a line of the grouped form under `### [owner]`). Restore never
decides on its own what an owner entry is.

1. **Everything that is not an owner entry comes from T**, as it was: that is the point of the restore.
2. **Every owner entry comes from C**, exactly as it is today (text, key, tick, form), in C's order, in R's first to-do
   section (under the owner's group heading in the grouped form, created in the content language's set if T has none).
   No owner entry is taken from T. So nothing the owner closed since comes back, nothing opened since is lost, a key on
   several lines keeps them, a displaced key stays where C has it, and a stale settlement (one the registrar refused to
   strike, sending an amendment instead) keeps its line.
3. **One addition from L:** a key the registrar raised itself (a bound key) that L calls open, with no entry in C, gets
   one line from L's latest terms, written as the registrar's reopen step writes it (`acts_md` writer, content
   language). This is the line the librarian deleted. Keys a project raised itself (known keys) are left alone, as the
   registrar leaves them.
4. **What the rule can't recover is said, not guessed:** an owner line in T with no key that is in no entry of C and
   matches no key's terms in any revision (an item a session wrote and the librarian deleted before the registrar ever
   stamped it) is listed in the output for the owner to re-add by hand.
5. The empty marker (`- (none)`, or the zh-Hant set's) follows: removed from a section that ends up with entries, added to one with
   none.

**Invariants** (pinned by tests): R's owner entries are C's plus the re-added ones of rule 3; every bound key L calls open
is on an open entry of R; R minus its owner entries equals T minus its owner entries.

The docs' promise becomes one sentence: "your to-do items stay exactly as they are today; one still open whose line the
librarian deleted comes back."

### The claim the registrar already sent

When an open bound key's line vanished, the registrar claimed the item done that same night (step 5, `claimed`, id
`c:<key>:r<reopens>`). Principal Handoff has no way to withdraw a claim (`claimed` changes no register; `supersedes` names
records of the same register only: core R3, `record.schema.json`); a `note` from the registrar is the only means. So when
rule 3 re-adds a key **and** L shows that claim was sent (`c:<key>:r<n>` in the project's sent records) with no
settlement after it, restore sends one `note` (origin registrar, id `x:<key>:r<n>`, about the key): "the line was
restored by brunna state restore; the claim came from a wrong nightly edit". Without it the owner side could still
accept the claim, and the registrar would strike the restored line the next night.

A key whose wrong claim the owner side already **settled** is closed in L, so rule 3 doesn't re-add it; restore says so,
and the owner reopens it on their side if it was wrong (the registrar then re-adds the line). Restore never sends
settlements or reopenings: those belong to the owner side.

## Reading without moving anything

`history` and a restore without `--apply` only read: they `git fetch` the memory copy's remote and read
`origin/agent-memory` with `git log` / `git show`, never `git pull`. A pull would move the copy's HEAD under a librarian
run (whose model may be editing STATE.md) and make the librarian take that move for a session hook's commit
(`librarian.process`: it compares HEAD before and after the call). They take no lock.

## Writing: the lock, the order, the push

**The lock.** One lock per memory copy, `bin/memlock.py`, built on the same `fcntl.flock` code as the scheduler's
`state_lock` (`bin/scheduler.py`): a file `<copy>/.git/brunna-writer.lock`, opened without truncating, flocked, then the
holder's name and start time written for messages; never unlinked. `hold(copy, who, timeout)` yields whether it got the
lock. A flock is released by the kernel when its holder dies, so a crash never leaves a copy locked. It has a CLI for the
shell scripts (`memlock.py hold <copy> --who <name> [--timeout s] -- <command>`; macOS has no `flock(1)`).

Who holds it, and for how long:

| writer | holds it | if busy |
|---|---|---|
| librarian, `process(proj)` | the whole run: pull, model call, commit, push, checkpoint (it decides who committed by comparing HEAD before and after the call) | waits up to 10 minutes, then leaves the project for tomorrow night |
| registrar, `run_project` | from reading STATE.md to its push and `hubbus done` | waits up to 10 minutes, then leaves the project for tomorrow night |
| consolidate, one project | from the fold's base to the checkpoint check after the push | the same |
| `state.py restore --apply` | the whole write | refuses at once: "the librarian is working on garden-app since 03:01; try again when it is done" |
| rename / recover / onboard on an existing copy | around moving or recreating the copy | refuses at once |

Rules that keep it free of deadlocks: the writer lock is always taken before `with-lock.sh`'s op lock, never after; no
hook and no `with-lock.sh` user takes the writer lock (a librarian run's headless agent fires the project's hooks inside
the copy while the librarian holds it); the lock is not re-entrant, so no code path takes it twice.

**The order inside the lock** (restore `--apply`): pull the copy (fast-forward only) → read C and L → build R →
commit → push **fast-forward only** → checkpoint → review-log → note.

- The push is a plain fast-forward push, never `git-safe-push.sh`: that script merges a diverged remote, and a session
  that pushed a to-do edit meanwhile (sessions push to the memory bare repo without the copy's lock) could merge cleanly
  into a file that breaks the invariants (two lines with one key). When the push is rejected, restore resets to the pulled
  HEAD, pulls again, rebuilds R from the new C and L, and tries again, up to three times; then it refuses, nothing changed.
- Any failure before the push succeeds resets the copy to the pulled HEAD: nothing changed (as v0.10.0).
- After the push: the checkpoint moves (back only, as v0.10.0), then the review-log entry is appended as one `os.write`
  (the librarian, consolidate and restore all append to it; the registrar reads it), then the note. A crash after the
  push leaves the restore published with the checkpoint or log not yet written: the output said nothing yet, and running
  the same restore again finds STATE.md already as wanted, then finishes the missing steps (checkpoint, log entry, note),
  each done only if not already done (the note by its id in L's sent records).

## The checkpoint

As v0.10.0: back to the restored commit only when it is an ancestor of the current checkpoint; `--keep-checkpoint`
leaves it. Assistant handoffs the librarian applied after that day are not applied again (they were closed on the bus);
the output says how many.

## Tests (pinned first, per tests-before-rewrite)

A temp instance: memory bare repo, the copy, a fake `hubbus records` answer for the `Ledger`. Each case asserts the
invariants:

- the librarian deleted an open bound key's line → re-added from L's terms; the note sent once, only when the claim was
  sent and unanswered; a second run sends nothing;
- an owner entry closed since, on one line, on two lines, ticked, in the grouped form → as C has it (gone, or ticked);
- a key displaced under another section in C → stays there;
- a stale settlement over an edited line → C's line kept;
- an unkeyed owner line in T lost since → listed, not restored;
- a known key with no line → not re-added;
- zh-Hant, English and mixed token sets;
- a session pushes a to-do edit between restore's pull and push → restore rebuilds and the invariants hold;
- the lock: restore refuses while held; librarian, registrar and consolidate wait and then skip; a killed holder releases
  it; rename refuses while held;
- `history` and a dry run leave the copy's HEAD and worktree untouched.

The 11-03 end-to-end run (a clean container, the real registrar, `brunna owner done`, a session-start pull) is repeated
with the new rule, plus a librarian-deleted line and its claim.

## Settled in review

- A claim can't be withdrawn under the spec: a note is the only means (reviewer reading of 0.6.2's `claimed` and core
  R3).
- Restore doesn't reopen a settled item on the bus: settlements are the owner side's.
- The librarian holds the lock for its whole run: locking only its write would bring back the HEAD-comparison race.
- The platform's own `openclaw-migration-status.md` (the registrar's other file) is out of scope: restore takes project
  names only.
