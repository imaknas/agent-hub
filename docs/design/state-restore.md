# Bringing back an earlier STATE.md: the to-do list and the lock

Status: design, third version (2026-11-09), after two rounds of blind review (round 1: the rule against the registrar and
the Principal Handoff spec, locking and failure, scope; round 2: whether the rule is implementable and correct against
`acts_md`, concurrency and recovery). Replaces the key handling and adds the locking of `bin/state.py restore`
(shipped in v0.9.0, hardened in v0.10.0).

## The problem

`brunna state restore <project> <date|commit>` writes an earlier version of a project's STATE.md back as a new commit, to
undo a night when the librarian's model got the file wrong. The file's to-do sections hold the owner's items, each
stamped with a key (`(waiting on owner: task-…)`, or the zh-Hant set's marker; `bin/todo_tokens.py`) that ties it to a
Principal Handoff item on the bus, which the registrar keeps the file in step with every night (`bin/registrar.py`,
steps 1-5). Restoring the old text naively revives items closed since and loses items opened since.

v0.9.0 decided from the text alone (a key missing from today's file = closed) and failed the case restore exists for:
the librarian's edit check allows deleting a whole keyed line (`bin/edits.py`), the registrar then claims the item done,
and restore called it closed. With the built-in owner side (`bin/owner.py`: done, drop, already-done) nothing can bring it
back. There was no lock against the nightly jobs either.

Round 2 showed that moving the owner's entries one by one between the two versions keeps meeting structure the registrar
depends on (several to-do sections, of which the registrar reads only the first; continuation lines; the grouped form;
ticked boxes; markers moved elsewhere). So the to-do sections now move whole.

## The rule: the to-do list stays as it is today, everything else goes back

Let T be the restored version, C today's version (both as the memory bare repo holds them, below), L the registrar's
ledger (`registrar.Ledger(project)`). Sections are the file's `## ` sections; a to-do section is one whose heading is
either set's to-do heading (`todo_tokens.patterns().section`, the reader `acts_md` and the registrar use).

1. **Refuse** when T or C has an unclosed code fence or HTML comment (the registrar leaves such a file alone; reading past
   it would drop lines), or when C has a key marker outside its to-do sections (a displaced item: the registrar notes it
   every night; the owner moves it back first). Nothing is guessed in either case.
2. **R is T with its to-do sections replaced by C's**: every to-do section of C, whole and verbatim (all its items, the
   agent's and undecided ones too, ticks, continuation lines, groups, the empty marker), in C's order, at the place of
   T's first to-do section, or at the end when T has none. Every other section is T's.
3. **One addition from L:** a key the registrar raised itself (a bound key) that L calls open, and whose marker appears
   nowhere in C, gets one line from L's latest terms in R's first to-do section, written as the registrar's reopen step
   writes it (`acts_md` writer, content language; under the owner's group in the grouped form). This is the line the
   librarian deleted.
4. **Said, not done:** restore lists what it doesn't bring back, with L's view of each: owner items in T's to-do
   sections that are not in C (closed since: by key; no key: their text), open keys a project raised itself whose line
   is gone (the registrar doesn't re-add those either), bound keys settled since by a claim from a deleted line (the owner
   reopens them on their side if the deletion was wrong; the registrar then re-adds the line).

**What the owner can predict, one sentence for the docs:** "your to-do list stays exactly as it is today and everything
else goes back to that day; an item of yours still open whose line was deleted comes back."

The cost is deliberate: a bad night's edit to the agent's or undecided items inside the to-do sections is not undone by
restore (it wasn't the owner's list that went wrong, and those items are the agents' to rewrite).

**Invariants** (pinned by tests): R's to-do sections are C's plus rule 3's lines; R's other sections are T's; every bound
key L calls open and C mentions is in R exactly where C has it; no key is on two lines that C didn't already have.

### The claim the registrar already sent

When an open bound key's line vanished, the registrar claimed the item done that same night (step 5, `claimed`, id
`c:<key>:r<reopens>`). Principal Handoff can't withdraw a claim (`claimed` changes no register; `supersedes` names
records of the same register only: core R3); a `note` from the registrar is the only means. Restore sends one note
(origin registrar, project `led.filed(key)`, id `x:<key>:r<n>`, about the key) for **every** bound key that L calls open,
that is on an open (unticked) entry of R, whose `c:<key>:r<n>` the project sent, and whose `x:<key>:r<n>` it hasn't:
decided from the ledger and R, not from what this run changed, so a run after a crash still sends it, and a second run
doesn't send it again.

The note is advisory: it tells the owner side the claim came from a wrong nightly edit. An assistant reading it should
not accept that claim; the built-in owner side doesn't read claims or notes at all (`bin/owner.py`), so there the item
simply stays open. If the claim is accepted anyway, the registrar strikes the line the next night, and the owner reopens
the item on their side. Restore never sends settlements or reopenings.

## Reading

`history` and a preview (restore without `--apply`) read the memory bare repo directly (`git --git-dir <bare> log` /
`show agent-memory:STATE.md`); they never fetch or pull in the copy (that would race the librarian's pull on the copy's
refs, or move HEAD under its model call) and take no lock. The preview ends with the exact apply command, carrying the id
of the C it was computed from: `brunna state restore garden-app 2026-11-01 --apply --from <blob>`.

## Writing

**The order, all inside the copy's lock** (restore `--apply`):

1. Finish a previous run first, if its record says it stopped halfway (below).
2. Read C from the bare repo. If `--from` doesn't match it, refuse: "STATE.md changed since the preview; preview again".
3. Build R, and the commit for it **without touching the copy's worktree**: a temporary index (`GIT_INDEX_FILE`) on top
   of the bare repo's `agent-memory`, `git commit-tree`. A crash here leaves at most an unreferenced commit object.
4. Push it to the bare repo **fast-forward only**, as a compare-and-swap on the ref (`git push <bare> <commit>:refs/heads/
   agent-memory` with `--force-with-lease=agent-memory:<C's commit>`). Rejected (a session pushed meanwhile): nothing
   changed; refuse: "STATE.md changed meanwhile; preview again". Never `git-safe-push.sh`, which merges.
5. Record the run: `.state/restore/<project>.json` (the restore commit, the restored commit, what is still to do),
   written atomically.
6. Bring the copy to it with a fast-forward pull (the copy is behind the bare repo now; if the copy has local commits of
   its own, leave it: the next nightly pull merges, as for any session's push).
7. The checkpoint: moved back to the restored commit only if that is an ancestor of the current checkpoint **and** the
   current checkpoint is not already a descendant of the restore commit (a librarian run since the restore has read past
   it: moving it back again would make it fold notes twice). `--keep-checkpoint` skips this.
8. The review-log entry, appended with one `os.write`.
9. The notes (above).
10. Remove the record. Each of 7-9 is marked done in the record as it completes, so step 1 of a later run finishes only
    what is missing.

**The lock.** `bin/memlock.py`, one lock per memory copy, on the same `fcntl.flock` code as the scheduler's `state_lock`
(`bin/scheduler.py`): `<copy>/.git/brunna-writer.lock`, opened `O_CREAT | O_RDWR | O_CLOEXEC` without truncating (a
detached child of a holder must not inherit it), flocked, then the holder's name and start time written for messages. A
flock dies with its holder, so a crash never leaves a copy locked. `hold(copy, who, timeout)` yields whether it got it; a
CLI for the shell scripts (`memlock.py hold <copy> --who <name> [--timeout s] -- <command>`; macOS has no `flock(1)`).

| writer | holds it | if busy |
|---|---|---|
| librarian, `process(proj)` | the whole run: pull, model call, commit, push, checkpoint (it tells who committed by comparing HEAD before and after the call) | waits up to 60 s, then leaves the project for tomorrow night |
| registrar, `run_project` | from reading STATE.md to its push and `hubbus done` | the same (it runs in the foreground: a long wait would block the tick) |
| consolidate, one project | from the fold's base to its checkpoint check after the push | the same |
| restore `--apply` | steps 1-10 | refuses at once, naming the holder and since when |
| rename / recover / onboard of an existing copy | around moving, removing or recreating the copy | refuses at once |

Rules: the writer lock is always taken before `with-lock.sh`'s op lock, never after; no hook and no `with-lock.sh` user
takes it (a librarian run's agent fires the project's hooks inside the copy); it is not re-entrant, so no path takes it
twice; a script that removes or re-clones a copy holds it while doing so, and the new copy's lock file is a new file
(nothing else holds the old one by then).

A project the registrar skips for a night is harmless: the records it didn't mark done are applied the next night and
its claim ids stop duplicates; todo.md shows that project's items as of the night before.

## Tests (pinned first, per tests-before-rewrite)

A temp instance (memory bare repo, the copy, a fake `hubbus records` answer for the `Ledger`), each case asserting the
invariants:

- the librarian deleted an open bound key's line → re-added from L's terms; one note when its claim was sent and not
  answered; a rerun sends none; a crash after the push then a rerun sends it;
- owner items closed since, on one line, on two, ticked, in the grouped form, in a second to-do section → as C has them;
- agent and undecided items, continuation lines, groups → C's;
- C with a displaced marker, T or C with an unclosed fence → refused, nothing written;
- an unkeyed owner line in T that C lacks, a known key whose line is gone, a key settled since → listed, not restored;
- zh-Hant, English and mixed token sets;
- `--from` stale, a session pushing between read and push → refused, the bare repo and the copy unchanged;
- a crash at each step (simulated by stopping after it) then a rerun → the same end state as an uninterrupted run, the
  checkpoint moved back once at most;
- the lock: restore refuses while held; librarian, registrar and consolidate wait 60 s then skip; a killed holder
  releases it; a child of the holder doesn't keep it; rename refuses while held;
- `history` and a preview leave the copy (refs, HEAD, worktree) untouched.

The 11-03 end-to-end run (a clean container, the real registrar, `brunna owner done`, a session-start pull) is repeated,
plus a librarian-deleted line and its claim.

## Settled in review

- A claim can't be withdrawn under the spec: an advisory note is the only means; the built-in owner side doesn't read it
  and doesn't need to (it doesn't act on claims).
- Restore doesn't reopen a settled item on the bus: settlements are the owner side's.
- The librarian holds the lock for its whole run: locking only its write would bring back the HEAD-comparison race.
- The to-do sections move whole: moving entries one by one breaks what the registrar reads from section 0, displaced
  markers, continuation lines and ticks.
- The platform's own `openclaw-migration-status.md` (the registrar's other file) is out of scope.
