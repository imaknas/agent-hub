# Waiting on tests: where agent sessions wait, the floor, and what to change

> agent-hub session · 2026-10-31 · r4. Owner's observation: in the projects being worked on now, the bottleneck is not the
> agents' output but waiting for tests; the owner wants everything besides model input/output at its theoretical minimum.
> This doc measures first, then proposes changes. **Nothing here is implemented yet.** Owner's rule for building it:
> **the final architecture first, no workarounds.**
>
> History: r2 split the changes into **A. agent-hub itself**, **B. the platform's test system** and **C. each
> project's own suite**. r3 after a first blind review (four reviewers): measurements deduplicated, deploy scripts and
> subagents counted, facts about the code corrected. r4 after a second blind review (three reviewers: code facts,
> architecture, a hunt for workarounds): the platform's test pieces become **one test system** — one declaration, one
> selection function, one result store, one runner (section 4) — instead of a cache, tiers and rules added separately,
> and the order is rebuilt so every step only adds to the last (7). All reviewers were Claude models (no other provider
> is configured), so their errors may be correlated.

## Summary

- One week (10-24 → 10-31 18:00) of the three active projects' top-level sessions: **26–31% of agent time is waiting
  on tests** (18.2 hours across the three), about 58% is the model. Subagents waited another 6.1 hours on tests in the
  background.
- The waiting is mostly **the same suites run several times per change**: in muninn the unit suite ~2.6 times and the
  e2e suite ~2.4 times per merge — the agent, then `merge-branch`, then `bin/deploy.sh`, which re-tests the exact commit
  `merge-branch` just tested (128 min a week). Then **browser e2e suites**, **gates that block the agent**, and **a
  shared machine under load** (leaked test containers, since removed, plus other sessions' suites: the same suite took
  13 s at one moment and 45–86 s at another).
- The plan (7): first the foundations that are final as built — the instance's state out of the hub root, the test
  declaration (which also lets the-crucible declare tests), the runner, the machine's worker budget; then agent-hub on
  worktrees; then the projects-and-repos resolver; then on it the selection function and the result store, which turn
  most merges and deploys into lookups.

## 1. How this was measured

Everything below is measured on the hub machine (Apple M4 Pro, 14 cores) unless marked **assumed**. The scripts were
throwaway and are not in the repo (they parse one runtime's transcript format, which belongs in a runtime adapter); the
method is described here in enough detail to rebuild them (B7).

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
background gate could hide (B8). r1 measured 89% of muninn's gates (median 5.3× the gate) and 56% of agent-hub's
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
  on 14 cores. A single slow test stretches with that load (the librarian golden: 16.6 s at 7–14 workers, 31 s at 28),
  though one whole gate measured about the same at 14 and 28 workers (32 s / 31 s, `e1d6540`).

### 2.2 agent-hub

- Gate: lint + whole suite under a fake HOME on every code commit (`bin/test-gate`). `855fb30` and `e1d6540` (10-31) took
  a full gate from ~170 s to ~31 s (one measurement by the session that made them, 14 workers) and skip a tree that
  already passed (the last 200 keys). Most of the week's gate minutes predate that.
- The ~30 s plateau is the slowest single tests stretched by load, not cores: in the recorded durations the i18n
  goldens are 54–59% of the suite's module-seconds and their second, English pass (`@en`) 27–29% (two snapshots); the
  largest single modules are `test_sandboxed_services`, `test_registrar_flow` and `test_librarian_flow`.
- Agents run the full suite before committing (125 runs, 99 min, plus runs chained into commit commands). One reason
  the review found: on a failure the gate prints the `ERROR:`/`FAIL:` lines and the last 30 lines, which cuts the
  failing modules' full output, so an agent re-runs to see it.
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
  runtime directory" (`registry.py` `dirs()`/`validate()`), and the project has none. It waits for the test declaration
  (section 4).
- Backend (at the time of measurement): 513 tests, 13–16 s at best, ~20 CPU-s; since then parallel (section 5).
- e2e: Playwright, `workers: 1` by design (one backend, one fixture database). From transcripts 45 runs, avg 88 s.
- Its subagents spent 63 min on tests, more than half of its top-level figure.

## 3. A. agent-hub itself

### A1. Work in worktrees and merge through `merge-branch` (decided)

**Why, beyond tests:** the shared checkout is the running platform (2.2); with worktrees the main checkout only holds
merged, tested commits. It also removes the class of incidents where one session's commit carries another's files
(09-25, 10-26, 10-31).

**Prerequisites:**

1. **The instance's state leaves the hub root; the pointer resolves from any checkout.** Without `.hub-home`
   (untracked), a worktree falls back to the old layout with everything in the worktree itself: its own `.state`
   (no registry cache — `merge-branch` then cannot find the project), no config, no private-names list, so `test-gate`
   silently skips the private-project-names check. Final form:
   - `hubconf` and `bin/hubpaths.sh` (and the other readers of `.hub-home`: `commit-hub-files.sh`, `agenthub`,
     `scheduler-watchdog.py`) find the pointer through the git common dir, i.e. the main checkout.
   - Instance state moves from `<hub>/.state` into the instance directory (`hubconf.state_dir()`): registry cache,
     scheduler and agent-job state, alerts, backup staging, cap-runner, applied config, the gate's bypass log, test
     durations, the test result store (4). AGENTS.md already says the hub root holds nothing of the user's; this
     finishes it, and replaces the literal `$HUB/.state` paths in `test-gate` and `githooks/post-commit`.
   - Scripts that write instance state (registry refresh, config apply, scheduler) refuse to run from a linked worktree,
     where they would rebuild the live registry index from a half-edited copy.
   - hub.json declares whether the deployment has a private-names list; `test-gate` fails, instead of skipping, when one
     is declared and cannot be found. A deployment that declares none skips the check, as AGENTS.md says.
2. **Commits in a linked worktree run lint and the config check only; the full suite runs at merge** through the test
   system (4). `githooks/post-commit` logs a bypass only when a commit skipped what its location requires (lint in a
   worktree, the full gate in the main checkout).
3. **agent-hub's test declaration** (4): its full tier is today's gate run (lint + suite under a fake HOME); its `code`
   list is today's `CODE` pattern, kept in one place.
4. **The main checkout:** a fast-forward is blocked only by uncommitted files the merge changes; the sessions working
   there finish before the switch.

**Costs:** a fix is live only after it is merged. After the switch, agent-hub's ~31 s suite runs inside `merge-branch`
under the repo's merge lock, so parallel sessions' merges queue behind each other until the result store (B3) makes
most merges a lookup.

### A2. Commit-time test selection with a coverage map — deferred

Replaying the window's 266 code commits against a coverage map: 17% must run everything; the rest select a median 26%
of the suite's test-seconds, an **assumed** ~44% of its wall time. Once A1 makes worktree commits lint-only, selection
at commit only shortens what the agent runs itself, at the cost of a nightly map and an unmeasured miss rate. Deferred
until B7's numbers say commit-time waits still matter; if built, it is a source of paths for a tier (B2), not a separate
rule.

### A3. Cheaper slow tests

- Profile and split the largest modules: `test_sandboxed_services`, `test_registrar_flow`, `test_librarian_flow`
  (suite work, independent of how tests are selected).
- The English pass of the i18n goldens (`@en`, 27–29% of module-seconds) becomes its own tier whose `paths` are i18n
  code, prompts, templates and golden fixtures (B1), so it runs at merge only when those change and always at night.
  First check whether `@en` ever failed alone on a commit touching none of those paths.

### A4. No full-suite run before the gate

agent-hub's rules: targeted tests while working; the gate runs the full suite. The reason agents re-run — the gate cuts
the failure output — goes away with the runner's complete-output file (B4).

## 4. B. The platform's test system

The platform does not make a project's suite faster (that is C). It decides **what a project's tests are** (B1), **which
of them a change needs** (B2), **whether a run already happened** (B3), **how and where they run, for every caller**
(B4), **the machine they run on** (B5), **what agents are told** (B6) and **how we know it worked** (B7). One system: one
declaration, one selection function, one result store, one runner. Later, on the numbers: whether the agent waits at
all (B8).

### B1. The test declaration

A top-level `tests:` block in the project's registry entry, independent of `runtime:` (which keeps meaning "has a
deployment"), so a project without services can declare tests:

```yaml
tests:
  code: ["src/**", "tests/**", "pyproject.toml", "uv.lock"]   # what counts as code; default: everything in its scopes
  inputs: [env:PYTHON_VERSION]                                 # outside inputs that change results
  tiers:
    full: {cmd: "uv run pytest -q -n auto", setup: "uv sync --frozen", when: [merge, nightly]}
    e2e:  {cmd: "uv run pytest -q -m e2e -n auto", when: [merge, nightly], paths: ["src/*/web/**"]}
```

- **Tiers** have a command, an optional `setup` (bring the untracked environment in line with the in-tree lockfiles;
  run when their hashes changed since the last setup in that checkout), `when` (`commit` only in repos with an opt-in
  commit gate; `merge`; `nightly` — every tier runs nightly), optional `paths` (the tier runs at merge only when they
  change; default: any change to the project's code), and, for a project with several scopes, the `scope` it runs in.
  The working folder is the scope folder.
- **`runtime.test`** (a string, today's form) is read by the same function as `full: {cmd: …, when: [merge, nightly]}`.
  Nothing new uses it; existing entries keep working.
- **Contract:** additive to project v1 (`projects-and-repos.md` section 3); a project changes its own declaration with
  `manifest.update`.
- **Commands use `-n auto`** (or the runner's equivalent); the worker count comes from the platform's budget (B5).

### B2. Which tests a change needs: `affected(repo, base, head)`

One function in the resolver: the changed paths → the projects they affect (`projects-and-repos.md` rule 5: owners,
ancestors whose runners collect nested scopes, everyone for root-owner files, the `depends_on` closure) → kept only if a
changed path matches the project's `code` → the tiers whose `paths` match. Every caller asks it: the commit gate,
`githooks/post-commit`, `merge-branch`, `agenthub test`, the deploy check, the nightly run, B7's classification. The two
copies of "what counts as code" that exist today (in `test-gate` and `post-commit`, already different) are replaced by
it.

### B3. The result store

One store for the machine, in the instance's state (so a deployment clone sees passes recorded in a worktree). A
result's key is a hash of: the tier's command and setup; the content its tests depend on at the tested commit (the tree
hash of each folder from B2's dependency set — for an ancestor, including nested scopes); the declared inputs. A result
is recorded only when the tier's paths are clean (no uncommitted changes), so the key describes what ran. Entries
expire by age (30 days), not count. It replaces `test-gate`'s `test-gate-passed-trees` file; one force flag for the
platform replaces `TEST_GATE_FORCE`.

Limits, stated: files a test reads that are neither in the tree nor declared inputs are not in the key; a flaky pass
stays recorded for that content. The nightly run (B4) is the net for both.

### B4. The runner and its callers

`agenthub test [--tier T] [--commit C]` runs tiers: by default the pairs `affected()` gives against the merge base with
main, skipping those with a recorded pass; it runs `setup` if needed, then the command in the scope folder with the
worker budget, writes the **complete output to a file and prints its path** with a summary, and records the result. It
takes no lock and does not rebase. Callers:

- **`merge-branch`:** after its rebase, the affected merge tiers through the runner; a recorded pass from the agent's own
  `agenthub test` makes the merge a lookup (209 of muninn's 227 merges last week had a no-op rebase).
- **Commit gate** (opt-in repos): the commit tiers; in a linked worktree, lint only (A1).
- **Deploy:** a project's deploy script calls `agenthub test --commit HEAD` instead of running its suites, so a deploy
  right after a merge is a lookup (muninn: 128 min a week today). Requested from projects when the runner exists.
- **Nightly:** every project's main, all tiers, the force flag, in a throwaway worktree (not the main checkout), after
  the container cleanup (B5); a failing test is re-run once and both results reported, so a flaky test is visible
  without paging anyone.

### B5. The machine

- A nightly job removes containers labelled `org.testcontainers` older than a day; the count is on the status page.
- Check what Spotlight indexes on the hub machine; exclude workspaces, worktrees and scratch directories if it is them.
- **A machine-wide worker budget:** the runner takes worker tokens from a small pool in the instance's state and passes
  the count to the suite (`TEST_JOBS` for agent-hub's `run-tests`, `PYTEST_XDIST_AUTO_NUM_WORKERS` for pytest-xdist), so
  two suites at once no longer start 2 × 14 workers. Projects then declare `-n auto` (B1).
- Another machine: not now (test CPU is small: agent-hub ~210 CPU-s per full run, muninn unit ~15 CPU-s).

### B6. What every project's agents are told

In `templates/agent-instructions.md`, now: run targeted tests while working; write the memory note and the report
before calling `merge-branch` (work agents already do after a gate, so the gate overlaps it). With the runner: run
`agenthub test` for the full check; a failure's complete output is in the file it names; leave a tier to the merge when
the project declares it `when: merge`.

### B7. The waiting report

The measurement in section 1 as a platform report: the runtime's transcript port reports each session's folder, which
`project_at` turns into a project; runs are classified by the projects' declared tier commands (patterns only for
commands no project declares); deduplicated; deploy runs and subagents counted. It also reports time the owner actually
waited on a session that was waiting on tests. Its first use, before any e2e tier is declared `when: merge`: classify
the agents' e2e runs as safety runs (followed by `merge-branch`) or inner-loop work (followed by an edit) — if most are
inner-loop, e2e at merge would add ~30 s to ~94 muninn merges a week instead of removing runs.

### B8. Asynchronous merge (merge queue) — on the numbers

`merge-branch` returns after the quick checks; a worker runs the tests, fast-forwards on green, reports on the bus. Its
bound is the lower "after the gate" numbers (2.1). Decided after B7 has run for a week, once the result store has
removed what it can.

## 5. C. Each project's own suite (requests sent 10-31)

- **muninn (done 10-31, `1c7f474`, measured by the project):** `-n 4` on the full-suite commands only (deploy, CI,
  docs; `runtime.test` sent as a manifest.update), not in `addopts`: unit ~20 s → ~8 s, three runs each. e2e at `-n 8`
  in deploy and docs: ~30 s → ~19 s. Nothing turned flaky.
- **the-crucible (done 10-31, `b5e0d07`, measured by the project):** tests made parallel-safe and `pytest-xdist` added
  (530 backend tests ~16 s → ~10 s at `-n 4`); e2e against a production build (4.2 → 3.7 min with the build, the first-
  test compile timeouts gone). Its test command (17 s end to end) was rejected by the registry gap (2.4) and waits for
  B1. Not done: a backend per e2e worker; it waits for its e2e tier's `when`.
- **Next, when the platform has them:** `-n auto` instead of `-n 4` once the worker budget exists (B5) — the fixed
  count is the projects' interim choice, not the platform's final form; deploy scripts call `agenthub test` (B4).
- **The project that leaked test containers:** fixed 10-31.

## 6. The floor

What cannot be removed: the tests the agent needs to see while working, and **one test run per merge** that something
must wait on unless an asynchronous merge (B8) hides it. Top-level, deduplicated, per week:

| | muninn | the-crucible | agent-hub |
|---|---|---|---|
| waiting now | 502 min | 103 min | 485 min |
| targeted runs (kept) | 27 min | 25 min (+16 in subagents) | 51 min |
| one full run per merge/commit, at today's measured speed | 227 × ~8 s ≈ 30 min | ~79 × 17 s ≈ 22 min once it can declare tests | 266 × ~31 s ≈ 137 min, less after A3 |
| e2e the agent needs while building UI (unknown share of 350 runs, at ~19 s) | 0–110 min | **assumed** 0–35 min | — |
| floor without B8 | **~57–170 min** | **~47–82 min** | **~190 min** |
| floor with every merge run hidden (B8) | ~27–140 min | ~25–60 min | ~51 min |

- Measured: run counts and durations (transcripts, deduplicated); the projects' own suite timings.
- Assumed: the-crucible's e2e share; exactly one full run per merge (a recorded pass makes it zero, a failure more); for
  agent-hub, one run per code commit.
- Not counted: contention, which inflates the "now" row.

## 7. Order of work

**Decided 10-31 (owner): the final architecture first, no workarounds.** Nothing is built in a form a later step
replaces; the reviewers' alternative (a whole-tree cache now, scopes later) is not taken. Each step only adds to what
the steps before it built.

1. **Foundations** (final as built, independent of how projects map to repos):
   - A1 prerequisite 1: instance state out of the hub root, the pointer through the git common dir, refusing writers
     in linked worktrees, the declared private-names list.
   - B1, the test declaration, and B4's runner core (setup, worker budget, complete-output file); `merge-branch` runs
     the declared merge tiers through it (all of them, until B2 exists). the-crucible declares its tests then.
   - B5: the container cleanup, the Spotlight check, the worker budget.
   - B6's rules that hold now; A3's module splits.
2. **A1:** agent-hub on worktrees and `merge-branch`.
3. **`projects-and-repos.md` steps 1–3**, in a worktree: characterization tests, the resolver and registry, the callers.
4. **On the resolver:** B2 (`affected`), B3 (the result store), the runner's remaining callers (`agenthub test`, the
   deploy check, the commit gate, the nightly run), A3's `@en` tier, B6's remaining rules, and B7 — whose baseline is
   taken with section 1's method just before this step starts, and whose e2e classification decides each project's e2e
   `when`.
5. **`projects-and-repos.md` steps 5–8:** memory, backups and deployment per repo, runtime capabilities, onboarding,
   the end-to-end monorepo.
6. **After a week of B7:** decide B8 (merge queue) and A2 (commit-time selection).

Costs of this order, so they are a known choice: the savings the result store brings (deploy re-runs, merges a lookup,
agent-hub's gate) arrive at step 4; until then muninn's deploys keep re-testing (128 min a week) and, after step 2,
agent-hub's merges queue behind each other's suite under the lock; the-crucible can declare tests at step 1.
