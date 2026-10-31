# Waiting on tests: where agent sessions wait, the floor, and what to change

> agent-hub session · 2026-10-31 · r2. Owner's observation: in the projects being worked on now, the bottleneck is not the
> agents' output but waiting for tests; the owner wants everything besides model input/output at its theoretical minimum.
> This doc measures first, then proposes changes. **Nothing here is implemented yet.**
>
> r2 (same day, after talking it through with the owner): the changes are split into **A. agent-hub itself**, **B. the
> platform** (how, when and whether every project's tests run) and **C. each project's own suite** (requests sent to it).
> Direction agreed: **agent-hub moves to worktree + `merge-branch` like every other project** (A1). The leaked test
> containers found in r1 were removed by the project that left them, and suites were re-timed (2.1).

## Summary

- One week (10-24 → 10-31) of the three active projects' sessions: **about 58% of agent time is the model, 26–28% is
  waiting on tests**, the rest other tools. Test waits were 18.8 hours that week across the three.
- The waiting is not mostly "a slow suite". It is, per change, **the same suites run two or three times** (the agent runs
  the full suite, then the commit or merge gate runs it again), **browser end-to-end suites** (62% of muninn's waits, 65%
  of the-crucible's), **gates that block the agent although it has other work** (memory note, report), and **a shared
  machine under load** (leaked test containers, since removed, plus other sessions' suites: the same suite took 13 s at
  one moment and 45–95 s at another).
- **A. agent-hub itself:** move to worktree + `merge-branch` (A1) with a tiered commit gate (A2: lint + the tests the change
  reaches, ~31 s → ~14 s for the median commit, estimated); cheaper critical-path tests (A3); no full-suite run before
  the gate (A4).
- **B. the platform:** a result cache every gate consults and `agenthub test` (B1); test tiers and ignored paths in the
  registry (B2); a nightly full run on every project's main as the safety net (B3); machine hygiene (B4); test rules for
  every project's agents (B5); a weekly waiting report (B6). Later, after measuring: an asynchronous merge queue (B7).
- **C. each project:** muninn runs its suites in parallel by default (~110 min/week); the-crucible registers a
  `runtime.test` (today its merges test nothing) and parallelizes its backend tests.

## 1. How this was measured

Everything below is measured on the hub machine (Apple M4 Pro, 10 performance + 4 efficiency cores) unless marked
**assumed**. The scripts were throwaway and are not in the repo (they parse one runtime's transcript format, which
belongs in a runtime adapter); the method is described here in enough detail to rebuild them (B6).

**Which projects.** The three with the most `merge.done` / `session.report` bus events since 10-24 and sessions seen
today: muninn (226 merges), the-crucible (22 merges, plus commits made without the merge gate) and agent-hub itself
(commits straight to main). Others had a handful of merges that week.

**Session transcripts** (the runtime's transcript directory, one folder per project; top-level sessions only, because a
subagent's tool calls already show up as the parent's wait). Window: 2026-10-24 00:00 to 2026-10-31 ~18:00 (+08:00).
Each gap between consecutive entries of a session is attributed to what ended it: an assistant entry → model; a tool
result → that tool call; a person's message → not counted (the owner reading or away). Gaps are capped at 20 minutes.
Bash commands are sorted into: *gate* (`merge-branch`; in agent-hub also `git commit`, which runs the commit gate),
*e2e* (Playwright, `pytest -m e2e`, `npm run test:e2e`), *full* (the whole unit suite: `pytest -q`, `unittest discover`,
`bin/run-tests`, `npm test`, `tsc --noEmit`), *targeted* (any other test command: one file, `-k`, one module). One command
can be several kinds (`pytest && pytest -m e2e && git commit && merge-branch`); its wait is listed under the combination.
Commands sent to the background are not waits.

**Suites**, timed with each project's HEAD exported to a scratch directory (not the project's checkout), with
`-p no:cacheprovider`. The machine was never quiet (2.1); where a number moved a lot between runs, all runs are given.

**Coverage map** (agent-hub only): every test module run once under coverage with subprocess measurement on, so scripts
the tests start are included; then last week's commits replayed against it (A2).

## 2. Findings

### 2.1 Across the platform

| one week | muninn | the-crucible | agent-hub |
|---|---|---|---|
| agent time (sum over sessions) | 28.3 h | 8.5 h | 31.8 h |
| model | 59% | 58% | 57% |
| waiting on tests | 447 min (26%) | 139 min (27%) | 541 min (28%) |
| …of which e2e | 278 min | 90 min | 3 min |
| …gate (`merge-branch` / commit gate) | 82 min (+ combos) | 3 min | 364 min |
| …full suite run by the agent | 58 min | 16 min | 117 min |
| …targeted runs | 29 min | 29 min | 57 min |
| changes | 227 merges | 74 commits | 257 code commits |

(e2e and gate rows include the combined commands; muninn's e2e row is e2e alone 189, e2e + full 52, gate + e2e + full 22,
gate + e2e 15.)

**Suites run per change.** muninn: 640 unit-suite runs (374 by agents, 266 by `merge-branch`) and 425 e2e runs for 227
merges — **2.8 unit and 1.9 e2e runs per merge**. agent-hub: about 2.1 commit-gate runs per code commit plus 148
full-suite runs the agents started themselves — **about 2.7 full runs per commit** (most of the week before today's gate
speedups). the-crucible: 56 e2e and 66 backend-suite runs for 74 commits.

**Reruns with nothing edited in between** (a suite run whose previous run of the same suite, in the same session, passed,
with no Edit/Write/`sed -i`/git rebase between): muninn 13% of suite time (52 min), the-crucible 35% (35 min), agent-hub
23% (108 min). This is an **upper bound** on reruns of an identical tree: another session's merge can move main in
between, and some reruns are deliberate flake checks. agent-hub's commit gate has skipped identical trees since today
(`e1d6540`), so part of its 23% is already gone for gate→gate reruns — but not for an agent's own run followed by the gate,
which the cache does not see.

**After the gate.** For each gate that took over 10 s, the time the agent then spent on non-test work (model and other
tools) before its next test command or the owner's next message: **muninn had at least the gate's length after 189 of 213
gates (89%), median 5.3× the gate; agent-hub 129 of 229 (56%), median 1.2×.** This bounds what running a gate in the
background can hide (B7).

**Contention.** 41% of all test-wait time (every project, not only these three) ran while another session's test run was
also running; 24% of the wall-clock time with any test running had two or more.

- **Leaked test containers** (found in r1, fixed the same day): 65 `postgres:16-alpine` containers labelled
  `org.testcontainers`, 39 of them running (created 10-07 to 10-29), left by one project's end-to-end tests. Its test
  server was killed with SIGKILL at the end of every run, so its shutdown never removed the container, and the project had
  turned off testcontainers' own reaper (Ryuk does not start under Colima). Together ~1.4 cores and 1.1 GB inside the
  containers; Docker's VM sat at 3.7 cores. The project fixed its shutdown and now removes its own containers older than
  two hours before each run; after that, 0 containers and the VM idle.
- The same the-crucible backend suite (513 tests, 19–22 CPU-s every time). **With the leak:** twelve runs within about an
  hour, six at 14.4–16.7 s, one at 21.5 s, five at 44.7–85.6 s, a different test stalling each time (a test that takes
  1.3 s within its module took 28.5 s in one full run); denying network made no difference (68.8 s). **After the leak was
  removed:** 12.7, 12.8, 25.2, 26.9, 29.8 s at load averages 19–29, while other sessions ran browser tests (headless
  Chromium and a `next dev` server ~1.9 cores) and Spotlight indexing used ~1.2 cores (`mds_stores` + `mds`; what it was
  indexing was not checked). muninn's unit suite in the same minutes: 17.3, 21.9, 29.3 s (19.7 s earlier).

So the leak caused the worst stretch (up to 6×), but a ~2× spread remains from sessions running heavy suites at the same
time and from the machine's own background work.

### 2.2 agent-hub

- Gate: lint + whole suite under a fake HOME on every code commit (`bin/test-gate`). Today `855fb30` and `e1d6540` took a
  full gate from about 170 s to about 31 s (14 workers; 210 CPU-s per run, user 118, sys 91 — measured by the session that
  made them) and skip an identical tree in 0.3 s. Most of the week's 364 gate minutes predate that.
- Last week's gate runs were slower than today's: 87 plain `git commit` calls that ran the gate averaged 75 s; 271
  commands combining a test run and a commit averaged 53 s. At today's 31 s, one gate run per code commit would be
  257 × 31 s ≈ **130 min a week instead of 364**, before anything in this doc.
- The 30 s plateau is set by the slowest single tests under load (librarian golden `test_run` 12 s alone, 16.6 s at 7–14
  workers, 31 s at 28; consolidate golden 11 s; `test_registrar_flow` second run ~9 s), not by cores. sys time is 43% of
  CPU: these tests start many subprocesses and touch many files.
- Agents also run the full suite themselves just before committing (148 runs, avg 47 s, 117 min) — the gate then runs it
  again.
- Most agent-hub sessions commit straight to main in the shared checkout, so the commit gate is the only gate; `merge-branch`
  was used 50 times. The registry entry has no `runtime.test`.
- The shared checkout is also **the running platform**: the scheduler, services and hooks execute its scripts. While this
  was written it held 14 files other sessions had modified and not committed (among them `bin/lease-runner.py`), live
  without having passed any gate.

### 2.3 muninn

- `runtime.test` is `uv run pytest -q` (unit + API, 1142 tests); `merge-branch` runs it after the rebase. CONTRIBUTING says
  all three suites (unit, e2e, deploy) pass before a merge; agents run e2e themselves (no gate runs it).
- Unit suite: **serial 18.5–20.5 s wall, ~15 CPU-s**; `-n 4` 9.4 s; `-n 8` 8.6 s; `-n auto` (14) 9.4 s with 72 CPU-s.
  Collection 1.9 s; sum of per-test durations 13.1 s; slowest test 0.51 s (flat). `pytest-xdist` is a dependency but
  `addopts` does not use it, so every agent run and every merge gate runs serially. The ~6 s left at `-n 8` is per-worker
  start-up and import; not isolated further.
- e2e (69 browser tests): **serial 81.7 s, `-n 4` 34.2 s, `-n 8` 28.7 s**; slowest test 4.8 s. CONTRIBUTING says `-n 4`;
  the transcripts show both serial and `-n 4` runs.
- Merges: 227 last week. By changed files: 94 touch web assets, 94 other code, **31 only docs, 8 only tests**. Of all 413
  commits, 202 touched only docs (CHANGELOG, `docs/`, `*.md`).
- `merge-branch` holds the repo's merge lock **while the tests run**: 59 of 266 `merge-branch` calls overlapped another
  session's, so one waited for the other's test run.

### 2.4 the-crucible

- **No `runtime.test` in its registry**, so `merge-branch` merges without running anything (its merges take 1–2 s). All
  testing is up to the agent; nothing guarantees main passes.
- Backend: 513 tests, 13–16 s at best (2.1), ~20 CPU-s. `pytest-xdist` is not installed; a parallel floor is **assumed**,
  not measured.
- e2e: Playwright with `workers: 1`, `fullyParallel: false` by design (one shared backend and one fixture database), a
  `next dev` frontend that compiles routes on first visit. Not timed here (a scratch copy's `next dev` failed on the
  linked `node_modules`). From transcripts: 53 runs, avg 93 s, up to 5 minutes; 65% of the project's test waiting.
- Commits by area: 36 of 74 touched only the backend (or backend and docs), 18 only the frontend and/or e2e tests.

## 3. A. agent-hub itself

Changes only agent-hub benefits from directly: its own workflow, its own gate, its own tests.

### A1. Work in worktrees and merge through `merge-branch` (direction agreed)

agent-hub is the one project whose parallel sessions share one working tree and commit straight to main. Moving it to the
same rule as every other project:

- **Why, beyond tests.** The shared checkout is the running platform (2.2): today a half-finished edit is live the moment
  it is saved. With worktrees, the main checkout only ever holds merged, tested commits, and that is what the scheduler
  and services run. The same change removes a class of incidents that the current rules only work around: sessions'
  commits sweeping each other's half-done files (09-25, 10-26), the "always `git commit -- <paths>`" rule, and the commit
  gate having to export the index because the working tree mixes several sessions' work.
- **For waiting on tests.** Commits in a worktree no longer need the full suite (A2); the full suite runs once at merge,
  and the result cache (B1) lets a merge reuse a pass for the same tree. Later the merge itself can run in the background
  (B7), though agent-hub sessions had enough other work after only 56% of gates.
- **Prerequisites, in this order:**
  1. A2's tiered commit gate. Without it, a worktree commit still runs the full suite (the hooks are shared), and the
     merge runs it again: twice the waiting, not less.
  2. A `runtime.test` in agent-hub's registry entry (`bin/test-gate --all`, which already uses the tree cache), or
     `merge-branch` will test nothing — the-crucible's situation.
  3. A worktree has no `.hub-home` (it is not tracked), so `hubconf` falls back to the default instance directory. Right on
     this machine, wrong on any deployment whose instance lives elsewhere: `hubconf` should read the main checkout's
     `.hub-home` (via the git common dir).
  4. Switch-over: the main checkout must be clean for `merge-branch` to fast-forward. The sessions with uncommitted work
     in it finish first; then the rules (AGENTS.md, the instance notes) say worktrees.
- **Costs.** A fix is not live until it is merged; trying a change against the real instance from a worktree still works
  (running its scripts reads the same instance), but the scheduler and services pick it up only after the merge — with a
  cached pass that is seconds. Rebase conflicts appear at merge instead of being silently interleaved. The owner's own
  interactive session may stay in the main checkout (the rules already allow one); what it changes should be committed
  promptly, or it blocks fast-forwards.

### A2. Tiered commit gate with test-impact selection

In a worktree, a commit runs lint + the test modules the change reaches; the full suite runs at merge. A commit made
directly on main in the main checkout (the owner's session) keeps the full gate.

Measured with the coverage map (each module's executed Python files, subprocesses included), replaying the week's code
commits:

- 266 code commits (10-24 → 10-31). 71 of 78 Python files in `bin/` were executed by at least one module,
  `bin/librarian.py` and `bin/hubbus` through the subprocesses the flow tests start, so the hook did reach children.
- **17% of commits (45) must run everything**: they change a file no coverage edge reaches and no test names —
  `contracts/*.json` (30 files), `templates/*.md` (19), `githooks/` (12), shell scripts (5), others (5).
- Selected share of the suite's test-seconds per commit: **median 0.26, mean 0.41, p75 0.97**.
- Wall time is not proportional: the slowest tests are often still selected, because `bin/hubconf.py` and `bin/i18n.py` are
  executed by 86–87 of the 99 modules, so touching either selects everything. Estimated with max(slowest selected item,
  selected seconds / 14 workers): **median 0.44 of a full run, mean 0.47, p75 0.96** — about 31 s → 14 s for the median
  commit. The estimate is a model (**assumed**), the selection is measured.
- Not measured: how often a selected-only run would have passed where the full run failed (a missed dependency).
- Risk: coverage does not see data files, shell scripts, templates or dynamic imports; those fall back to everything. A
  dependency the map misses is caught at merge, by the full suite — it costs time, not correctness.
- Cost: the map has to be rebuilt (nightly, and when tests change: a few minutes of CPU), and the fallback rules kept.

### A3. Cheaper critical-path tests

The ~30 s plateau is the slowest single tests stretched by load (2.2). Moving the librarian/consolidate goldens and
`test_registrar_flow` from subprocess runs to in-process calls (or splitting each into independent cases) lowers the floor
toward the next slowest item; how far is **not measured** — measure each test's in-process cost first. Cost: the goldens
exist to pin behaviour through the real entry points, so an in-process version must still go through them.

### A4. No full-suite run before the gate

agent-hub's own rules: run targeted tests while working; the gate (or `agenthub test`, B1) runs the full suite. Removes the
148 pre-commit full runs a week (117 min last week, at older speeds). Cost: none.

## 4. B. The platform: how, when and whether every project's tests run

The platform does not make any project's suite faster — that is C. It decides **whether a run is needed** (B1, B2), **which
tests run where** (B2), **that nothing slips through** (B3), **the machine they run on** (B4), **what agents are told** (B5),
and later **whether the agent waits** (B7). These are written once in agent-hub and apply to every project that uses
`merge-branch` — agent-hub itself included, after A1.

### B1. Result cache every gate consults, and `agenthub test`

A pass is recorded under a key = the tree (`git write-tree` of what was tested) + what the result depends on outside it
(interpreter version, browser build, the private-names list for agent-hub; the lockfile is already in the tree). Every gate
consults it: the commit gate (done for agent-hub), `merge-branch` (not yet), and a new `agenthub test` that runs the
registry's tests on the committed tree and records the key, so **the agent's own last run counts** and the gate does not
repeat it. A merge whose rebase was a no-op then costs nothing. `agenthub test` tests what is committed (or staged), not
unsaved working-tree edits, so the key is exact.

- Saving: the identical-tree reruns, at most 52 / 35 / 108 min a week (2.1, upper bounds); the agent's pre-gate full run
  whenever it went through `agenthub test`.
- Risk: a key that misses an input (an env var, a tool version outside the tree) reuses a pass that would now fail; a
  flaky test that passed once stays passed for that tree. B3 is the net for both.
- Cost: small. The key logic exists in `bin/test-gate`; move it to a shared helper.

### B2. Test tiers and ignored paths in the registry

Today the registry has one `runtime.test`, run by `merge-branch`. Proposed (adding fields, so no contract version bump):

```yaml
runtime:
  test:
    fast: <cmd>                 # what the agent runs while working / at commit
    full: <cmd>                 # what merge-branch runs
    e2e: {cmd: <cmd>, paths: [<glob>…]}   # merge-branch also runs this when a changed file matches
    ignore: [docs/**, "*.md"]   # a change touching only these needs no tests
```

A plain string keeps meaning `full`. This turns e2e from "the agent decides whether to run it" into "the platform runs it
when it can matter", which is what can bring muninn's 425 e2e runs a week down: the agent no longer needs to run e2e
before merging to be safe. `ignore` passes muninn's 31 docs-only merges a week (14%) without tests.

- Risk: a wrong `paths` or `ignore` declaration skips a run that was needed, silently. B3 is the net.
- Cost: each project maintains its declaration; out-of-date globs do not fail loudly.

### B3. Nightly full run on every project's main

Every project with a registered test: all tiers (full and e2e) on main, no cache, no path rules; failure raises an alert
and a task for the project. The safety net for B1 (key misses, flaky passes), B2 (wrong paths) and A2 (missed
dependencies). Cost: machine time at night; a slip is found the next morning at the latest.

### B4. Machine hygiene and contention

- A nightly platform job, like `prune-worktrees`, that removes containers labelled `org.testcontainers` older than a day,
  reports what it removed and shows a count on the status page. The project that leaked has fixed its own side; this keeps
  the next project from doing it again.
- Check what Spotlight is indexing on the hub machine (~1.2 cores during the re-timing) and exclude workspaces, worktrees,
  test scratch directories and `.state/` if it is them.
- If the ~2× spread remains after that: a machine-wide limit on concurrent heavy suites (e.g. at most two e2e runs,
  queued). Overlapping runs are on different trees, so there is nothing to share between them except through B1.
- Cost: small for the first two; the limit adds queueing when it is too strict, so only with a measured reason.
- Another machine: not now. Test CPU is small (agent-hub 210 CPU-s per full run, muninn unit ~15 CPU-s, e2e ~37 CPU-s
  serial); contention comes from leaks and siblings, not from the suites outgrowing 14 cores. The remote client is a
  laptop that is sometimes off; cloud CI adds minutes of queue and setup. Possibly for B7's background runs later.

### B5. Test rules for every project's agents

In `templates/agent-instructions.md` (reaches every project's rules file): run targeted tests while working; run the
`fast` tier before committing; leave `full` and e2e to `merge-branch` or run them through `agenthub test` so the result
is reused; do not run the full suite right before the gate.

### B6. Weekly waiting report

The measurement in section 1 as a platform report, through the runtime adapter that already reads transcripts for memory
gaps and the weekly reflection: model vs test waiting per project, suites per change, reruns, work after gates. This is
how to know whether A and B1–B5 worked, and whether B7 is worth building.

### B7. Asynchronous merge gate (merge queue) — decide after measuring

`merge-branch --queue` does the quick checks (clean tree, rebase, commit titles), then returns. A platform worker runs the
tests on the rebased tree, fast-forwards main when green, re-tests if main moved meanwhile, and reports `merge.done` /
`merge.failed` on the bus — which reaches the session (its hook on the next action where the runtime has hooks, the
mailbox otherwise; the dispatcher resumes an idle one). The lock is held only for rebase and fast-forward, not during
tests.

- Saving: bounded by work after gates (2.1): most of muninn's gate waits (89% of gates), about half of agent-hub's; the
  22% of muninn merges that overlapped no longer wait for each other's tests.
- Risk: main stays green (the worker only fast-forwards on a pass). What changes is that the agent learns about a failure
  later, maybe after it ended its turn, and work stacked on an unmerged branch must be rebased when it fails. Two queued
  merges that each pass alone can fail together — the worker tests the combined tree before the second fast-forward.
- Cost: the largest item here: a new long-running service (the scheduler's 15-minute tick is too slow), more merge states
  ("queued", "testing", "re-testing"), a queue view on the status page; "done" in a session's report becomes "queued" vs
  "merged". Agents that no longer wait may also run more suites at once (B4).
- Why later: B1 + B2 remove part of the same waiting at a fraction of the cost. Measure a week after them (B6), then decide.

## 5. C. Each project's own suite (requests to the projects)

The platform does not edit other projects; these go to their mailboxes once the owner says so.

- **muninn:** `-n 4` in `addopts` for the unit suite: −10 s per run (19.7 → 9.4 s measured) × ~640 runs a week ≈
  **110 min/week**; the suite already passes at `-n auto`. e2e at `-n 8` instead of `-n 4`: −5 s per run. When B2 exists,
  declare its tiers (`fast`/`full`/e2e with its web paths, `ignore` for docs and CHANGELOG).
- **the-crucible:** register a `runtime.test` (backend pytest + jest + `tsc --noEmit`; e2e when frontend files change,
  once B2 exists) — today `merge-branch` tests nothing. Add `pytest-xdist` to the backend (saving **assumed** ~2/3 of
  13–16 s, by analogy with muninn). The biggest item there is e2e (93 s avg, one worker by design): a backend and fixture
  database per worker, and a built frontend instead of `next dev` — a project change, not a flag.
- **The project that leaked test containers:** done 10-31 (its commit `befb20e`); nothing left.

## 6. The floor

What cannot be removed: the tests the agent needs to see to keep working (its targeted runs, and e2e while it builds UI),
each at the fastest the machine runs them. Everything else (gates, pre-gate full runs, identical reruns) can be skipped
(B1, B2), shrunk (A2) or taken off the critical path (B7).

| one week | muninn | the-crucible | agent-hub |
|---|---|---|---|
| waiting now | 447 min | 139 min | 541 min |
| targeted runs (kept) | 303 runs, 29 min | 88 runs, 29 min | 433 runs, 57 min |
| e2e runs at the measured parallel floor (kept, if UI work needs them) | 425 × 28.7 s = 203 min | **assumed** half of 90 min | — |
| floor | **~30–230 min** | **~30–75 min** | **~57 min** |

- Measured: run counts and durations of targeted runs (transcripts); muninn e2e at `-n 8` (28.7 s); work after gates.
- Assumed: that every gate and every pre-gate full run can be skipped by the cache (B1, B2) or hidden in the background
  (B7). The agent had at least the gate's length of other work after 89% of muninn's gates and 56% of agent-hub's, so
  agent-hub's floor needs A2 and B1, not asynchrony alone. Also assumed: that targeted runs do not get faster (they could,
  under less load); for the-crucible, that a parallel e2e halves it.
- The muninn range is how many of its 425 e2e runs the agent really needs while it works: the low end keeps none (the
  gate runs e2e, B2), the high end keeps all at parallel speed. The transcripts cannot tell which runs were inner-loop.
- Not counted: contention (2.1), which inflates every number in the "now" row; B4 lowers both rows.

## 7. Order and what is left to decide

Decided 10-31 (owner): **B1 and B2 are built on scopes** (`projects-and-repos.md`), not on whole trees first. So the
order is:

1. **Now, independent of scopes:** B3 (nightly full run on every project's main), B4's container cleanup job and
   Spotlight check, B5 (test rules for every project's agents), A4 (agent-hub sessions do not pre-run the full suite).
   The C requests were sent 10-31 to muninn and the-crucible; the-crucible answered with a `runtime.test` in a
   manifest.update.
2. **`projects-and-repos.md` steps 1–3:** characterization tests, the resolver and registry fields, the callers moved
   onto it.
3. **On the resolver (its step 4):** B1 (cache keyed by scope hashes, `agenthub test`), B2 (test tiers per project, the
   scope as the default paths), A2 (agent-hub's tiered commit gate with test-impact selection).
4. **A1:** agent-hub's `runtime.test`, `.hub-home` for worktrees, the main checkout cleared, then agent-hub on worktrees.
5. **B6** for a week, then decide **B7** (merge queue) and **A3** (cheaper critical-path tests) on the numbers.

Still open: B7 after the measured week, including whether an agent may end its turn before its merge is green.
