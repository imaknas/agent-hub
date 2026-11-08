# Bringing back an earlier STATE.md: the to-do list and the lock

Status: design, fourth version (2026-11-09), after three rounds of blind review (round 1: the rule against the registrar
and the Principal Handoff spec, locking and failure, scope; rounds 2 and 3: the rule against `acts_md` and the registrar,
concurrency, recovery and the git mechanics, tried in throwaway repos). Replaces the key handling and adds the locking of `bin/state.py restore`
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
ledger (`registrar.Ledger(project)`). Sections and to-do sections are exactly what `acts_md.Doc` reads (`_sections`,
made public for this: fenced and commented lines skipped, a section ends at the next `#`/`##` heading), the reader the
registrar uses; never `todo_tokens.Patterns.section`, which ignores fences.

1. **Refuse** when T, C or the result R has an unclosed code fence or HTML comment (the registrar leaves such a file
   alone), or when T or C carries the marker of a bound key L calls open outside its to-do sections (`Doc.mentioned`:
   a displaced item; the owner moves it back first). Markers of closed keys in an archive or in prose don't matter.
   Nothing is guessed.
2. **R is T with its to-do sections replaced by C's**: every to-do section of C, whole and verbatim (all its items, the
   agent's and undecided ones too, ticks, continuation lines, groups, the empty marker), in C's order, at the place of
   T's first to-do section, or at the end when T has none. Every other section is T's.
   **When C has no to-do section at all** (likely the bad night itself: the model dropped it), R takes T's to-do sections
   instead, with the owner entries of keys L calls settled or withdrawn removed (whole entries, as `acts_md.apply`
   strikes them).
3. **One addition from L:** a bound key (raised by the registrar) that L calls open, that is on an owner entry of T's
   to-do sections, and whose marker appears nowhere in C (deleted between T and today), gets one line from L's latest
   terms appended to R's first to-do section, exactly as the registrar's reopen step writes it (`acts_md.apply`: a
   labelled line after the section's last non-blank line, content language). Not when C has an owner entry whose
   normalised text equals that key's latest terms (the key was only dropped from the line: the registrar relinks it).
   Each re-added line is named in the output, so the owner sees it if it duplicates a rewritten item.
4. **Said, not done:** restore lists what it doesn't bring back, with L's view of each: owner items in T's to-do
   sections that are not in C and that L calls closed (by key) or doesn't know (no key: their text, for the owner to
   re-add by hand), and open keys a project raised itself (known keys) whose line is gone (the registrar doesn't re-add
   those either).

**What the owner can predict, for the docs:** "your to-do list stays exactly as it is today and everything else goes back
to that day; an item you were waiting on that was in the list that day and was deleted since, still open, comes back.
Anything else restore leaves out, it lists."

The cost is deliberate: a bad night's edit to the agent's or undecided items inside the to-do sections is not undone by
restore (it wasn't the owner's list that went wrong, and those items are the agents' to rewrite).

**Invariants** (pinned by tests): R's to-do sections are C's plus rule 3's lines (or, when C has none, T's minus closed
owner entries); R's other sections are T's; every bound key L calls open and C mentions is in R exactly where C has it;
no key is on two lines that C didn't already have; R has no unclosed fence or comment.

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

**The order, all inside the copy's lock** (restore `--apply`). Files of a run live in `.state/restore/<project>/`.

1. **Finish or drop a previous run** whose record is there (below). Remove a temporary index left by a crash.
2. **Check the copy:** clean worktree, no `MERGE_HEAD`, and its HEAD an ancestor of the bare repo's `agent-memory` (a copy
   that diverged, or that a killed librarian left dirty, makes the next plain `git pull` fail and lets the registrar
   merge its stale file over the restore). Otherwise refuse, naming the state. Read C from the bare repo; if `--from`
   doesn't match it, refuse: "STATE.md changed since the preview; preview again".
3. **Build R and its commit without touching any worktree:** in the bare repo, a temporary index
   (`GIT_INDEX_FILE` under the run folder) read from C's tree, R's blob written, `git commit-tree` with C's commit as the
   only parent. C, its commit and the new commit are the one C read in step 2.
4. **Write the record** (atomically): the restored commit, C's commit, the new commit, the steps still to do.
5. **Publish:** `git --git-dir=<bare> update-ref refs/heads/agent-memory <new> <C's commit>`, the compare-and-swap
   `project_git.fetch_bundle` already uses. If the ref moved (a session's push, or a sandboxed project's bundle),
   nothing is published: drop the record, refuse: "STATE.md changed meanwhile; preview again". Never `git-safe-push.sh`,
   which merges.
6. **The copy:** a fast-forward pull (step 2 made it an ancestor, the lock kept it so).
7. **The checkpoint:** moved back to the restored commit when that is an ancestor of the current checkpoint;
   `--keep-checkpoint` skips this; no checkpoint file means nothing to move. Until this step is done the librarian skips
   the project ("a restore didn't finish; run brunna state restore <project> to finish it"), so no librarian run can
   read past the restore first.
8. **The review-log entry,** one `os.write`, unless an entry naming the new commit is already there.
9. **The notes** (above; already-sent ones are skipped by id).
10. **Remove the record.**

**Finishing a previous run (step 1):** if the bare repo's tip is the record's new commit or descends from it, the
publish happened: do steps 6-10 that the record says are still to do. If the tip is still the record's C commit, nothing
was published: drop the record (the owner previews again). Otherwise (someone else moved it and the new commit was never
published): drop the record. `brunna state restore <project>` with no date finishes a stopped run and does nothing else.

**The lock.** `bin/memlock.py`, one lock per memory copy, on the same `fcntl.flock` code as the scheduler's `state_lock`
(`bin/scheduler.py`): `<copy>/.git/brunna-writer.lock`, opened `O_CREAT | O_RDWR | O_CLOEXEC` without truncating (a
detached child of a holder must not inherit it), flocked, then checked to be still the file at that path (same inode; a copy renamed or re-cloned while waiting means
retry on the new one), then the holder's name and start time written for messages. A flock dies with its holder, so a
crash never leaves a copy locked. `hold(copy, who, timeout)` yields whether it got it; a
CLI for the shell scripts (`memlock.py hold <copy> --who <name> [--timeout s] -- <command>`; macOS has no `flock(1)`).

| writer | holds it | if busy |
|---|---|---|
| librarian, `process(proj)` | the whole run: pull, model call, commit, push, checkpoint (it tells who committed by comparing HEAD before and after the call) | waits up to 60 s, then leaves the project for tomorrow night |
| registrar, `run_project` | from reading STATE.md to its push and `hubbus done` | the same (it runs in the foreground: a long wait would block the tick) |
| consolidate, one project | from the fold's base to its checkpoint check after the push | the same |
| restore `--apply` | steps 1-10 | refuses at once, naming the holder and since when |
| rename / recover / onboard of an existing copy, `uninstall --purge` | around moving, removing or recreating the copy; rename also refuses while a restore record exists | refuses at once |

Rules: the writer lock is always taken before `with-lock.sh`'s op lock, never after; no hook and no `with-lock.sh` user
takes it (a librarian run's agent fires the project's hooks inside the copy); it is not re-entrant, so no path takes it
twice; a script that removes or re-clones a copy holds it while doing so, and the new copy's lock file is a new file
(nothing else holds the old one by then).

A project the registrar skips for a night is harmless: the records it didn't mark done are applied the next night and
its claim ids stop duplicates; todo.md shows that project's items as of the night before. A night consolidate skips for
the lock doesn't count as its weekly turn for that project.

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
- `--from` stale, a session pushing between read and publish, a sandboxed bundle landing meanwhile → refused, the bare
  repo and the copy unchanged;
- a diverged, dirty or mid-merge copy → refused before anything is written;
- C without a to-do section → T's, minus closed owner entries;
- a crash after each step (simulated by stopping there) then a rerun → the same end state as an uninterrupted run; the
  librarian skips the project while step 7 is pending; a duplicate log entry is never written;
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
