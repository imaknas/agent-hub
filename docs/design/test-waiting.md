# Waiting on tests: where agent sessions wait, the floor, and what to change

> agent-hub session · 2026-10-31 · r3. Owner's observation: in the projects being worked on now, the bottleneck is not the
> agents' output but waiting for tests; the owner wants everything besides model input/output at its theoretical minimum.
> This doc measures first, then proposes changes. **Nothing here is implemented yet.**
>
> r2: changes split into **A. agent-hub itself**, **B. the platform** (how, when and whether every project's tests run)
> and **C. each project's own suite**; agent-hub moves to worktree + `merge-branch` (A1, agreed with the owner).
>
> r3: **blind review** by four independent reviewers (facts against the code, measurements, architecture, a
> contrarian), findings taken as a union and each checked against the source before use. Only Claude models were
> available on this machine (no other provider configured), so the reviewers differ by model size, not model family;
> their errors may be correlated. What changed: the measurement now deduplicates branched transcripts, counts deploy
> scripts and subagents (1, 2.1); several facts about the code were wrong (A1, B1, B2). Three of four reviewers argued
> for building the cache on whole trees before the scopes refactor; the owner decided the other way — final
> architecture first, no workarounds (7).

## Summary

- One week (10-24 → 10-31 18:00) of the three active projects' top-level sessions: **26–31% of agent time is waiting
  on tests** (18.2 hours across the three), about 58% is the model. Subagents waited another 6.1 hours on tests in the
  background.
- The waiting is mostly **the same suites run several times per change**: in muninn the unit suite ~2.6 times and the
  e2e suite ~2.4 times per merge — the agent, then `merge-branch`, then `bin/deploy.sh`, which re-tests the exact commit
  `merge-branch` just tested (128 min a week). Then **browser e2e suites**, **gates that block the agent**, and **a
  shared machine under load** (leaked test containers, since removed, plus other sessions' suites: the same suite took
  13 s at one moment and 45–86 s at another).
- What the review changed in the plan: a test cache helps most if it also covers deploy scripts; the registry today
  cannot even hold a test command for a project without a service (the-crucible hit this); agent-hub's worktree switch
  needs `hubconf` to find the instance from a worktree, or the private-names check is silently skipped. Everything is
  built toward the final architecture with no workarounds (owner, 10-31), so the cache and tiers come after the
  projects-and-repos resolver (7).

## 1. How this was measured

Everything below is measured on the hub machine (Apple M4 Pro, 14 cores) unless marked **assumed**. The scripts were
throwaway and are not in the repo (they parse one runtime's transcript format, which belongs in a runtime adapter); the
method is described here in enough detail to rebuild them (B6).

**Which projects.** The three with the most `merge.done` / `session.report` bus events since 10-24 and sessions seen
on 10-31: muninn, the-crucible and agent-hub itself.

**Session transcripts** (the runtime's transcript directory, one folder per project). Window: 10-24 00:00 to 10-31
18:00, local time. Each gap between consecutive entries of a session is attributed to what ended it: an assistant
entry → model; a tool result → that tool call; a person's message → not counted. Gaps are capped at 20 minutes.

- **Deduplicated (r3):** a branched or resumed session copies its history into a new file. r1 counted those copies
  again: 8% of muninn's and 24% of the-crucible's entries in the window were copies. Entries are now counted once by
  their id, tool calls once by theirs.
- **Subagents counted separately (r3):** r1 left them out on the premise that the parent waits on them. It does not:
  subagents run in the background (the parent's wait on them was 0–1 minute a project). Their own test waits are real
  machine time and real delay for the work they do, but not time the parent or the owner sat waiting; they are reported
  beside the top-level numbers, not added to them.
- **Commands** are sorted into: *deploy* (a project's `bin/deploy.sh`; added in r3: muninn's runs the unit and the e2e
  suite), *gate* (`merge-branch`; in agent-hub also `git commit`, which runs the commit gate), *e2e* (Playwright,
  `pytest -m e2e`, `npm run test:e2e`), *full* (the whole unit suite), *targeted* (any other test command). A combined
  command (`pytest && git commit && merge-branch`) counts once, under the first matching kind in that order; so the
  gate row of agent-hub includes the agent's own test runs chained before a commit.
- **Known inflators, not corrected:** in sessions in default permission mode, time waiting for a permission prompt is
  counted as tool time; polling loops on background tests (`until grep …; sleep`) count as other tools, not tests;
  an interactive login in a terminal tab counted as tool time (agent-hub, 92 min, not in the test numbers).

**Suites** were timed with each project's HEAD exported to a scratch directory, with `-p no:cacheprovider`. The machine
was never quiet (2.1); where a number moved a lot between runs, all runs are given, and single measurements are said
to be single.

**Coverage map** (agent-hub only): every test module run once under coverage with subprocess measurement on; then the
window's commits replayed against it (A2).

## 2. Findings

### 2.1 Across the platform

Top-level sessions, deduplicated (r3):

| one week | muninn | the-crucible | agent-hub |
|---|---|---|---|
| agent time | 26.6 h | 6.7 h | 27.8 h |
| waiting on tests | **502 min (31%)** | **103 min (26%)** | **485 min (29%)** |
| …e2e (incl. combined with a gate) | 254 min, 386 runs | 66 min, 45 runs | 3 min |
| …deploy script (unit + e2e again) | 128 min, 158 runs | — | — |
| …gate (`merge-branch` / commit) | 38 min, 157 runs | 2 min, 27 runs | 332 min, 497 commands |
| …full suite run by the agent | 55 min, 233 runs | 11 min, 41 runs | 99 min, 125 runs |
| …targeted | 27 min, 282 runs | 25 min, 78 runs | 51 min, 373 runs |
| subagents' test waits (not added above) | 26 min | 63 min | 278 min (206 of it commit gates) |
| changes in the window (git) | 227 merges, 450 commits | 79 commits | 266 code commits |

The model's share (~57–59%) is from r1, before deduplication; the reviewer who rebuilt the method found it barely moves.

**Suites run per change.** muninn: ~584 unit-suite runs (233 by agents, 193 in `merge-branch`, 158 in `deploy.sh`) and
~544 e2e runs (350 by agents, 36 combined with a merge, 158 in `deploy.sh`) for 227 merges — **about 2.6 unit and 2.4 e2e
runs per merge**. agent-hub: 497 commit/gate commands and 125 agent full runs for 266 code commits; not every commit
command ran the gate (data-only commits skip it), so a per-commit figure is not derived. the-crucible: 45 e2e and 41
backend-suite runs for 79 commits.

**Reruns with nothing edited in between** (r1, before deduplication, so too high): muninn 13% of suite time, the-crucible
35%, agent-hub 23%. An **upper bound** on reruns of an identical tree; in agent-hub's shared checkout even looser, since
another session's edit changes the tree between one session's two runs. Not counted there: muninn's deploy runs, which
re-test the commit just merged — the clearest identical-tree rerun of all.

**After the gate.** How much non-test work the agent did after a gate before it needed tests again bounds what a
background gate could hide (B7). r1 measured 89% of muninn's gates (median 5.3× the gate) and 56% of agent-hub's
(1.2×). A reviewer rebuilding it found **68–74% and 28–32%**, depending on whether messages from other sessions and task
notifications end the window; and in 40 of 201 muninn cases the work after the gate was a deploy or a push, which needs
the merge to have succeeded and cannot overlap it. Take the lower numbers.

**Contention.** r1 reported that 41% of test-wait time ran while another session's test run was also running. That
counted branched copies overlapping themselves; deduplicated, the reviewer found **28%**.

- **Leaked test containers** (found in r1, fixed the same day by the project that left them): 65 `postgres:16-alpine`
  containers labelled `org.testcontainers`, 39 running (created 10-07 to 10-29). Its test server was killed with SIGKILL
  at the end of every run, so its shutdown never removed the container, and testcontainers' own reaper was off (it does
  not start under Colima). ~1.4 cores and 1.1 GB inside the containers; Docker's VM sat at 3.7 cores. After the fix: 0
  containers, the VM idle.
- The same the-crucible backend suite (513 tests, 19–22 CPU-s every time). **With the leak:** six runs at 14.4–16.7 s,
  one at 21.5 s, five at 44.7–85.6 s, a different test stalling each time; denying network made no difference.
  **After:** 12.7, 12.8, 25.2, 26.9, 29.8 s at load averages 19–29, while other sessions ran browser tests (~1.9 cores)
  and Spotlight indexing used ~1.2 cores (what it indexed was not checked). muninn's unit suite in the same minutes:
  17.3, 21.9, 29.3 s.
- `bin/run-tests` always starts `min(16, cores)` workers, whatever else runs: two agent-hub gates at once are 28 workers
  on 14 cores, and the librarian golden test takes 16.6 s at 7–14 workers but 31 s at 28.

### 2.2 agent-hub

- Gate: lint + whole suite under a fake HOME on every code commit (`bin/test-gate`). `855fb30` and `e1d6540` (10-31) took
  a full gate from ~170 s to ~31 s (one measurement by the session that made them, 14 workers) and skip a tree that
  already passed (the last 200 keys). Most of the week's gate minutes predate that.
- The ~30 s plateau is the slowest single tests stretched by load, not cores: the i18n goldens are 59% of the suite's
  module-seconds and their second, English pass (`@en`) alone 29%; then `test_registrar_flow` and `test_librarian_flow`.
- Agents run the full suite before committing (125 runs, 99 min, plus runs chained into commit commands). One reason
  the review found: on a failure the gate prints only a tail of the output, so an agent re-runs to see the whole failure.
- Subagents' commit gates: 206 min in the week, in the background.
- Most sessions commit straight to main in the shared checkout, which **is the running platform**: the scheduler,
  services and hooks execute its scripts, so an uncommitted half-edit is live (14 such files on 10-31).
- **Incident on 10-31:** commit `757b959`, titled as a memory sync, carried 17 code files and reached `origin/main`
  without the commit gate: `bin/git-sync-memory.sh` disables git hooks for memory commits and committed without a path
  limit, so another session's staged work went with it. Fixed the same evening by another session (`1e4d8e9`: the sync
  commits only `memory/` and `STATE.md`, and only in its own checkout). The next commit passed the gate, so the content
  was tested afterwards.
- The registry entry has no `runtime.test`.

### 2.3 muninn

- `runtime.test` is `uv run pytest -q` (1142 tests); `merge-branch` runs it after the rebase. `bin/deploy.sh` runs it
  again, then `pytest -m e2e -n 4`, before deploying the commit that was just merged.
- Unit suite: **serial 18.5–20.5 s, ~15 CPU-s**; `-n 4` 9.4 s; `-n 8` 8.6 s; `-n auto` 9.4 s with 72 CPU-s (each a single
  run). Collection 1.9 s; per-test sum 13.1 s; slowest test 0.51 s. `pytest-xdist` is installed but not used by default.
- e2e (69 browser tests): **serial 81.7 s, `-n 4` 34.2 s, `-n 8` 28.7 s**; slowest test 4.8 s.
- 227 merges: 94 touch web assets, 94 other code, 31 only docs, 8 only tests.
- `merge-branch` holds the repo's merge lock while tests run; 59 of 266 `merge-branch` calls overlapped another
  session's call (how many minutes were spent waiting on the lock was not computed).
- In 209 of 227 merges the rebase was a no-op (the head commit was not rewritten; reviewer's count from the bus and git):
  the tree `merge-branch` tests is almost always the one the agent just tested.

### 2.4 the-crucible

- **No test runs before a merge, and today it cannot have one.** It has no `runtime.test`, so `merge-branch` tests
  nothing. When it sent one (10-31), validation rejected it: in the registry, any `runtime:` key means "has a service
  runtime directory" (`registry.py` `dirs()`/`validate()`), and the project has none. It is waiting for the platform fix
  (B2).
- Backend: 513 tests, 13–16 s at best, ~20 CPU-s; `pytest-xdist` not installed.
- e2e: Playwright, `workers: 1` by design (one backend, one fixture database), against `next dev`. Not timed here; from
  transcripts 45 runs, avg 88 s.
- Its subagents spent 63 min on tests, more than half of its top-level figure.

## 3. A. agent-hub itself

### A1. Work in worktrees and merge through `merge-branch` (direction agreed)

**Why, beyond tests:** the shared checkout is the running platform (2.2); with worktrees the main checkout only holds
merged, tested commits. It also removes the class of incidents where one session's commit carries another's files
(09-25, 10-26, and 10-31 above).

**Prerequisites (corrected in r3):**

1. **Commits in a worktree run lint only; the full suite runs at merge.** `bin/test-gate` can tell a linked worktree
   (`git rev-parse --git-dir` differs from `--git-common-dir`). A few lines; no test selection needed (A2 is not a
   prerequisite, as r2 said). Commits in the main checkout keep the full gate.
2. **A `runtime.test` for agent-hub:** `bin/test-gate --all` (it uses the tree cache). agent-hub is exempt from the
   runtime-directory rule (2.4), so this already validates.
3. **`hubconf` must find the instance from a worktree.** r2 said a worktree falls back to the default instance
   directory; wrong: without `.hub-home` (untracked) `hubconf` uses the old layout, everything in the hub root, which in a
   worktree is the worktree itself. Its `.state/` (registry cache) is the worktree's, and `bin/test-gate` run there finds
   no `private-names.txt`, so **the private-project-names check is silently skipped** and the cache key loses that input.
   Fix: resolve the pointer and `.state` through the git common dir (the main checkout), and make `test-gate` fail, not
   skip, when the list is missing on a deployment that has one.
4. **The main checkout:** a fast-forward is blocked only by uncommitted files the merge changes, not by any dirty file;
   still, the sessions with uncommitted work there should finish before the switch.

**Costs:** a fix is live only after it is merged (with a cached pass, seconds); rebase conflicts show up at merge. The
owner's interactive session may stay in the main checkout.

### A2. Commit-time test selection with a coverage map — deferred

Measured with a coverage map (subprocesses included), replaying the window's 266 code commits: 17% must run everything
(they change contracts, templates, git hooks or shell scripts that coverage cannot see); for the rest the median commit
selects 26% of the suite's test-seconds, an **assumed** ~44% of its wall time (~31 s → ~14 s, a model with a single 31 s
baseline that leaves out fixed overhead, so probably low).

The review showed it is close to break-even once A1 makes commits lint-only: the full run moves to merge either way, and
selection then only shortens what the agent runs itself. It costs a nightly map, fallback rules and an unmeasured miss
rate, and adds a third way to decide which tests a change needs. **Deferred** until B6 shows commit-time waits matter
after A1, A3 and B1.

### A3. Cheaper slow tests — moved up

- Run the English pass of the i18n goldens (`@en`, 29% of the suite's module-seconds) only when i18n code, prompts,
  templates or golden fixtures change — a path rule, no coverage map — and always in the nightly run (B3). First check
  whether `@en` ever failed alone on a commit that touched none of those paths.
- Profile and split `test_registrar_flow` and `test_librarian_flow`, the next slowest items.
- Lowers every full run: at commit today, at merge after A1, at night, and the load other sessions feel.

### A4. No full-suite run before the gate — with complete failure output

Rules for agent-hub's sessions: run targeted tests while working; the gate runs the full suite. Paired with a change
that removes the reason agents re-run: on failure the gate writes the failing modules' full output to a file and prints
its path.

## 4. B. The platform: how, when and whether every project's tests run

The platform does not make any project's suite faster (that is C). It decides **whether a run is needed** (B1, B2),
**which tests run where** (B2), **that nothing slips through** (B3), **the machine they run on** (B4), **what agents are
told** (B5), **how we know** (B6), and later **whether the agent waits** (B7).

### B1. Result cache every gate consults — including deploy scripts

A pass is recorded under a key: the tested tree + the **resolved test command** + the declared outside inputs
(interpreter version, browser build, the private-names list for agent-hub). Consulted by the commit gate (done for
agent-hub), `merge-branch`, and **the deploy path** — muninn's `deploy.sh` re-tests the commit `merge-branch` just
tested, 128 min a week; either deploy asks the cache, or the platform tells projects that a deploy right after a merge
may skip its tests for a tree that passed.

- `agenthub test` can start as `merge-branch --dry-run` plus the cache (it already runs the tests without
  fast-forwarding).
- `merge-branch` runs tests in the session's worktree, not in a clean export like the commit gate: untracked state
  (`.venv`, `node_modules`, `.env`, build output) is in no tree hash. Either export like `test-gate`, or the key is
  trusted only when the lockfiles are in the tree and the environment is rebuilt from them.
- Saving: deploy re-runs (≤128 min/week in muninn), the no-op-rebase merges (209 of 227 in muninn: the agent's own
  `agenthub test` pass carries over), and identical-tree reruns (upper bounds in 2.1).
- Risk: a missing input in the key reuses a stale pass; a flaky pass stays cached for that tree. B3 is the net.
- Keys keep 200 entries today; shared across projects it needs a per-repo file or more room.

### B2. Test commands in the registry: fix the gap first, then tiers

- **The gap:** a project without a service runtime cannot declare a test command (2.4). Fixed by the final test
  declaration (section 7, step 3), which does not imply a runtime directory or change jobs' default `cwd` — not by a
  stopgap field.
- **Tiers:** `runtime.test` is a string in `contracts/project.v1.schema.json` and in the validator; a mapping
  (`fast`/`full`/`e2e`) would crash today's readers (`registry.py` passes it to `shlex.split`; `merge-branch` to the
  shell). So either new keys beside it (e.g. `test_fast`, `test_e2e: {cmd, paths}`) — additive, no version bump — or a
  project v2. Readers: only `registry.py` and `merge-branch`.
- **Which paths need tests:** one rule, an allowlist of what counts as code, like `test-gate`'s `CODE` pattern — not an
  `ignore` list: `ignore: ["*.md"]` would skip agent-hub's tests although its prompt templates are `.md` files pinned
  by goldens.
- **e2e at merge — settle first:** whether moving e2e into `merge-branch` lowers muninn's e2e runs depends on how many
  of the agents' 350 runs were pre-merge safety runs (followed by `merge-branch`) and how many were inner-loop UI work
  (followed by an edit). Classify them from the transcripts before building it; if most are inner-loop, e2e-at-merge
  adds ~30 s to ~94 merges a week, under the merge lock.

### B3. Nightly full run on every project's main

All tests, no cache, no path rules, in a throwaway worktree or export (not the main checkout, where someone may be
working); agent-hub's with `TEST_GATE_FORCE=1`. Scheduled `after:` the container cleanup (B4); a flake policy (rerun a
failing test once, report both) so the alert stays meaningful. The net for B1 and B2.

### B4. Machine hygiene and contention

- Nightly cleanup of `org.testcontainers` containers older than a day; count on the status page.
- Check what Spotlight indexes on the hub machine; exclude workspaces, worktrees and test scratch directories if it is
  them.
- **Load-aware worker counts (moved up, r3):** `run-tests` (and suites the platform starts) size their pools by the
  machine's free cores or take tokens from a small machine-wide pool, instead of always 14. The measured 16.6 s → 31 s
  at 28 workers is the case this prevents.
- Another machine: not now (test CPU is small: agent-hub ~210 CPU-s per full run, muninn unit ~15 CPU-s).

### B5. Test rules for every project's agents — only what exists

In `templates/agent-instructions.md`, now: run targeted tests while working; **write the memory note and the report
before calling `merge-branch`** (the work agents already do after a gate, moved in front of it, so the gate's wait
overlaps nothing it could have overlapped anyway); a failing gate's full output is in the file it names. Rules that
depend on B1/B2 (`agenthub test`, tiers, leaving e2e to the merge) are added when those exist — telling agents to skip
e2e before a merge that does not run it would remove the only check.

### B6. Weekly waiting report — first, not last

The measurement in section 1 (deduplicated, deploy and subagents included) as a platform report through the runtime
adapter that already reads transcripts. **Built first**, so every later change has a before and after; the agent-hub
baseline in 2.1 is already stale (the gate got 5× faster on 10-31). It also reports the time the owner actually waited
on a session that was waiting on tests, beside the summed agent time: a session waiting matters most when someone waits
for it.

### B7. Asynchronous merge gate (merge queue) — later, on the numbers

`merge-branch` returns after the quick checks; a platform worker tests, fast-forwards on green, reports on the bus. Its
bound is the lower "after the gate" numbers (2.1): 68–74% of muninn's gates, 28–32% of agent-hub's, minus deploys and
pushes that need the merge. B5's "note and report before the merge" captures part of it for free. A new service and
more merge states; decided after B6 has run for a week.

## 5. C. Each project's own suite (requests sent 10-31)

- **muninn:** `-n 4` for the full unit suite, put in `runtime.test`, `deploy.sh` and the documented full-suite command
  — **not in `addopts`**, which would also slow every targeted run with worker start-up (corrected request sent
  10-31). Expected ~10 s per full run, from a single measurement. e2e already runs at `-n 4`.
- **the-crucible (done 10-31, its `b5e0d07`, measured by the project):** tests made parallel-safe (each test its own
  database, uploads, vector store, env) and `pytest-xdist` added: 530 backend tests ~16 s serial, ~10 s at `-n 4`, ~11 s
  at 14 workers, so `-n 4`. e2e against a production build instead of `next dev`: a full run 4.2 → 3.7 min including
  the build, and the first-test compile timeouts are gone. Not done: a backend and fixture database per e2e worker (the
  frontend's API URL is fixed at build time, and the live specs share one fixture database) — it waits for B2 to decide
  when e2e runs. Its `runtime.test` (backend ruff + pytest `-n 4`, frontend tsc + eslint + jest; 17 s end to end) was
  sent as a manifest.update and **rejected by the registry gap (2.4)**; it resends once B2's fix is in. Registering
  tests adds ~17 s per merge that it does not pay today; that is the price of main being tested.
- **The project that leaked test containers:** fixed 10-31.

## 6. The floor

What cannot be removed: the tests the agent needs to see while working, and **one test run per merge** that something
must wait on unless a background gate (B7) hides it. Top-level, deduplicated, per week:

| | muninn | the-crucible | agent-hub |
|---|---|---|---|
| waiting now | 502 min | 103 min | 485 min |
| targeted runs (kept) | 27 min | 25 min (+16 in subagents) | 51 min |
| one full run per merge/commit, at today's measured speed | 227 × ~9 s (`-n 4`) ≈ 34 min | ~79 × 17 s ≈ 22 min once its `runtime.test` is accepted | 266 × ~31 s ≈ 137 min, less after A3 |
| e2e the agent needs while building UI (unknown share of 350 runs, at 28.7 s) | 0–170 min | **assumed** 0–35 min | — |
| floor without B7 | **~60–230 min** | **~47–82 min** | **~190 min** |
| floor with every gate hidden (B7) | ~27–200 min | ~25–60 min | ~51 min |

- Measured: run counts and durations (transcripts, deduplicated); muninn's suite timings (single runs).
- Assumed: the-crucible's e2e share; that each merge needs exactly one full run (a cache
  hit can make it zero; a failure makes it more); for agent-hub, one gate run per code commit.
- Not counted: contention, which inflates the "now" row.

## 7. Order of work

**Decided 10-31 (owner): build toward the final architecture; no workarounds.** Nothing is built in a form that a later
step replaces. The final architecture is the one in `projects-and-repos.md` (projects, repos and scopes) with the test
pieces here built on it. So B1 and B2 are built on the resolver; the reviewers' alternative (whole trees now, scopes
later) and the middle option (a project-local path list) are not taken, since both would be rebuilt. The registry gap
that blocks the-crucible is fixed by the final test declaration, not by a stopgap field.

1. **Foundations every later step stands on** (final as built):
   - `hubconf` resolves the instance pointer and `.state` through the git common dir, so worktrees see the same
     instance; `test-gate` fails instead of skipping when a deployment's private-names list is missing (A1, prerequisite
     3; `projects-and-repos.md` step 0).
   - B6, the waiting report, so every later step has a before and after.
   - B4: the container cleanup job, the Spotlight check, load-aware worker counts.
   - A4 (complete failure output) and the B5 rules that already hold (targeted tests while working; memory note and
     report before `merge-branch`).
   - A3's test splits: profile and split `test_registrar_flow` and `test_librarian_flow` (suite work, independent of
     how tests are selected).
2. **A1:** agent-hub on worktrees and `merge-branch` (lint-only commits in worktrees; `runtime.test` = `bin/test-gate
   --all`, a plain string, which stays valid as the full tier in the final declaration).
3. **`projects-and-repos.md` steps 1–3, in a worktree:** characterization tests; the resolver and registry fields
   **including the final test declaration** (B2: tiers as additive keys, paths, an allowlist of what counts as code,
   no runtime directory implied — the-crucible resends its test command then); callers moved onto the resolver.
4. **On the resolver:** B1 (cache keyed by residual scope hashes and the resolved command, consulted by `merge-branch`,
   `agenthub test` and the deploy path), B2's tiers in `merge-branch` (e2e at merge only if the transcript
   classification in B2 shows most agent e2e runs were safety runs), B3 (nightly full run), A3's `@en` pass by B2's path
   rule.
5. **`projects-and-repos.md` steps 5–8:** memory backups and the clone migration, runtime capabilities, onboarding and
   recovery for subfolder and folder-only projects, the end-to-end monorepo.
6. **After a week of B6:** decide B7 (merge queue) and A2 (commit-time selection) on the numbers.

Cost of this order, stated so it is a known choice: the measured savings that B1/B2 bring (deploy re-runs, merges whose
rebase was a no-op, agent-hub's gate) arrive after steps 1–3, and the-crucible's merges stay untested until step 3.
