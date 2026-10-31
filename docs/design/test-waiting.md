# Waiting on tests: where agent sessions wait, the floor, and what to change

> agent-hub session · 2026-10-31 · r1. Owner's observation: in the projects being worked on now, the bottleneck is not the
> agents' output but waiting for tests; the owner wants everything besides model input/output at its theoretical minimum.
> This doc measures first, then ranks changes. **Nothing here is implemented yet**; the last section asks which to build.

## Summary

- One week (10-24 → 10-31) of the three active projects' sessions: **about 58% of agent time is the model, 26–28% is
  waiting on tests**, the rest other tools. Test waits were 18.8 hours that week across the three.
- The waiting is not mostly "a slow suite". It is, per change, **the same suites run two or three times** (the agent runs
  the full suite, then the commit or merge gate runs it again), **browser end-to-end suites** (62% of muninn's waits, 65%
  of the-crucible's), **gates that block the agent although it has other work** (memory note, report), and **a shared
  machine under load from things that are not tests at all** (39 leaked database containers from another project, on
  top of other sessions' suites: the same suite took 15 s at one moment and 45–95 s at another).
- Ranked recommendation (details and numbers below):
  1. Clean up leaked test infrastructure, and have the platform keep it clean (no risk, small).
  2. A content-keyed result cache that every gate consults, plus `agenthub test` so the agent's own run counts (low risk, small).
  3. Per-project quick wins the projects make themselves: parallel unit tests in muninn, a registered `runtime.test` in
     the-crucible, agents not pre-running the suite the gate will run (no risk, small).
  4. An asynchronous merge gate (merge queue): `merge-branch` returns at once, the platform tests and fast-forwards on green
     (moderate risk, medium).
  5. Test-impact selection for agent-hub's commit gate, full suite after (moderate risk, medium): replaying last week, a
     median commit would run 26% of the suite's test-seconds (about 44% of its wall time, estimated); 17% of commits fall
     back to everything.
  6. Cheaper critical-path tests in agent-hub; a machine-wide limit on concurrent heavy suites (later).
  Another machine is not worth it now (see 3.7).

## 1. How this was measured

Everything below is measured on the hub machine (Apple M4 Pro, 10 performance + 4 efficiency cores) unless marked
**assumed**. Reproducible with throwaway scripts (kept out of the repo: they parse one runtime's transcript format, which
belongs in a runtime adapter; see 3.9).

**Session transcripts** (the runtime's transcript directory, one folder per project; top-level sessions only, because a
subagent's tool calls already show up as the parent's wait). Window: 2026-10-24 00:00 to 2026-10-31 ~18:00 (+08:00).
Each gap between consecutive entries of a session is attributed to what ended it: an assistant entry → model; a tool
result → that tool call; a person's message → not counted (the owner reading or away). Gaps are capped at 20 minutes.
Bash commands are sorted into: *gate* (`merge-branch`; in agent-hub also `git commit`, which runs the commit gate),
*e2e* (Playwright, `pytest -m e2e`, `npm run test:e2e`), *full* (the whole unit suite: `pytest -q`, `unittest discover`,
`bin/run-tests`, `npm test`, `tsc --noEmit`), *targeted* (any other test command: one file, `-k`, one module). One command
can be several kinds (`pytest && pytest -m e2e && git commit && merge-branch`); its wait is listed under the combination.
Commands sent to the background are not waits.

**Suites**, timed today with each project's HEAD exported to a scratch directory (not the project's checkout), with
`-p no:cacheprovider`. The machine was not quiet: Docker's VM used about 3.7 cores throughout (see 2.1) and other sessions
were working. Where a number moved a lot between runs, all runs are given.

**Coverage map** (agent-hub only): every test module run once under coverage with subprocess measurement on, so scripts
the tests start are included; then last week's commits replayed against it (3.2).

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

**Contention.** 41% of all test-wait time (every project, not only these three) ran while another session's test run was
also running; 24% of the wall-clock time with any test running had two or more. Separately:

- **39 leaked `postgres:16-alpine` test containers** (label `org.testcontainers`, Node testcontainers, created 10-07 to
  10-29) from one project that uses testcontainers. Together about 1.4 cores inside the containers; Docker's VM process
  sat at 3.7 cores for the whole measurement.
- The same the-crucible backend suite (513 tests, 19–22 CPU-s every time), twelve runs within about an hour: six at
  **14.4–16.7 s**, one at 21.5 s, five at **44.7–85.6 s**, a different test stalling each time (a test that takes 1.3 s
  within its module took 28.5 s in one full run). Denying network made no difference (68.8 s), so it is the machine, not
  a remote call. Load average reached 48 on 14 cores during one window. muninn's first unit run was 28.5 s, then 19.7 s.

So part of what looks like "slow tests" is a shared machine running leaked infrastructure plus several sessions' suites at
once.

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
  again. Since `bin/run-tests` is now as fast as the gate, these runs are pure duplication.
- Most agent-hub sessions commit straight to main in the shared checkout, so the commit gate is the only gate; `merge-branch`
  was used 50 times.

### 2.3 muninn

- `runtime.test` is `uv run pytest -q` (unit + API, 1142 tests); `merge-branch` runs it after the rebase. CONTRIBUTING says
  all three suites (unit, e2e, deploy) pass before a merge; agents run e2e themselves (no gate runs it).
- Unit suite, measured: **serial 18.5–20.5 s wall, ~15 CPU-s**; `-n 4` 9.4 s; `-n 8` 8.6 s; `-n auto` (14) 9.4 s with 72
  CPU-s. Collection 1.9 s; sum of per-test durations 13.1 s; slowest test 0.51 s (flat). `pytest-xdist` is a dependency but
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
- Backend: 513 tests, 14–16 s on a quiet moment (see 2.1 for the loaded runs), ~20 CPU-s. `pytest-xdist` is not installed;
  a parallel floor is **assumed**, not measured.
- e2e: Playwright with `workers: 1`, `fullyParallel: false` by design (one shared backend and one fixture database), a
  `next dev` frontend that compiles routes on first visit. Not timed here (a scratch copy's `next dev` failed on the linked `node_modules`). From transcripts: 53 runs, avg 93 s, up to 5
  minutes; 65% of the project's test waiting.
- Commits by area: 36 of 74 touched only the backend (or backend and docs), 18 only the frontend and/or e2e tests.

## 3. Options

Each: what it removes, the saving with numbers, what failure it could let through, effort.

### 3.1 Content-keyed result cache everywhere

A pass is recorded under a key = the tree (`git write-tree` of what was tested) + what the result depends on outside it
(interpreter version, lockfile is already in the tree, browser build, the private-names list for agent-hub). Every gate
consults it: the commit gate (done for agent-hub), `merge-branch` (not yet), and a new `agenthub test` that runs the
registry's `runtime.test` on the current tree and records the key, so **the agent's own last run counts** and the gate
does not repeat it. A merge whose rebase was a no-op then costs nothing.

Projects may also declare paths no test reads (`docs/**`, `CHANGELOG.md`); a change touching only those is a pass by
definition.

- Saving: the identical-tree reruns, at most 52 / 35 / 108 min a week (2.1, upper bounds). muninn docs-only merges: 31 a
  week × ~20 s. The agent's pre-gate full run disappears whenever it ran through `agenthub test`.
- Risk: a key that misses an input (an env var, a tool version outside the tree) reuses a pass that would now fail. A
  flaky test that passed once stays passed for that tree. A wrong "no test reads this" path skips a test that did.
- Effort: small. The key logic exists in `bin/test-gate`; move it to a shared helper, add `agenthub test`, consult it in
  `merge-branch`.

### 3.2 Test-impact selection

Run only the tests whose inputs changed; the full suite still runs before main moves (3.3/3.4), so a missed dependency
costs time, not correctness.

agent-hub, measured with the coverage map (each module's executed Python files, subprocesses included), replaying the
week's code commits:

- 266 code commits (10-24 → 10-31). Every module ran under coverage with subprocesses measured (71 of 78 Python files in
  `bin/` were executed by at least one module, `bin/librarian.py` and `bin/hubbus` through the subprocesses the flow tests
  start, so the hook did reach children).
- **17% of commits (45) must run everything**: they change a file no coverage edge reaches and no test names —
  `contracts/*.json` (30 files), `templates/*.md` (19), `githooks/` (12), shell scripts (5), others (5).
- Selected share of the suite's test-seconds per commit: **median 0.26, mean 0.41, p75 0.97**. The other 83% of commits run
  a quarter of the suite on average.
- Wall time is not proportional: the slowest tests are often still selected, because `bin/hubconf.py` and `bin/i18n.py` are
  executed by 86–87 of the 99 modules, so touching either selects everything. Estimated with max(slowest selected item,
  selected seconds / 14 workers): **median 0.44 of a full run, mean 0.47, p75 0.96** — about 31 s → 14 s for the median
  commit. The estimate is a model (**assumed**), the selection is measured.
- Not measured: how often a selected-only run would have passed where the full run failed (a missed dependency).

muninn: the unit suite is 9 s in parallel, so selection buys little there. e2e covers the whole app through the browser;
path-level selection only helps the 31 docs-only merges (14%), which need no tests at all, and 8 that change only tests. Finer selection
needs coverage of the server during e2e — not measured.

the-crucible: path-level selection fits its layout (frontend vs backend): 36 of 74 commits touched only the backend; if
the API surface the frontend uses is unchanged, e2e is not needed. Needs the project's judgement on what e2e covers.

- Risk: coverage does not see data files, shell scripts, templates or dynamic imports; those fall back to everything (and
  the fallback rate is what bounds the saving). A test that depends on a file only through a subprocess the coverage hook
  missed would be skipped at commit and caught at merge/push.
- Effort: medium (map maintenance: rebuild nightly or when tests change; the fallback rules).

### 3.3 Tiered gates

Fast check where the agent waits, full suite where it does not. For worktree projects this already exists in shape:
commit has no gate, `merge-branch` runs `runtime.test`. agent-hub is the case to change: its commit is its merge (shared
checkout, straight to main). Tier it as: commit = lint + selected modules (3.2) + cache; full suite asynchronously right
after the commit and before any push, failure reported to the committing session and blocking the push.

- Saving: agent-hub's commit wait drops from ~31 s to an estimated ~14 s for the median commit (3.2); 17% still run everything.
- Risk: main can be red for the ~30 s until the full run reports; another session can commit on top of it in that window.
- Effort: medium; depends on 3.2 and on how failures reach a session (3.4).

### 3.4 Asynchronous gate (merge queue)

`merge-branch --queue` does the quick checks (clean tree, rebase, commit titles), then returns. A platform worker runs
`runtime.test` on the rebased tree, fast-forwards main when green, re-tests if main moved meanwhile, and reports
`merge.done` / `merge.failed` on the bus — which already reaches the session (hook on its next action, or the mailbox
dispatcher resumes it if it is idle). The lock is held only for rebase and fast-forward, not during tests.

- Saving: every gate wait leaves the critical path, **if** the agent has other work for that long. After a merge it always
  does: the memory note and the report it writes are model time. muninn's gate waits were 82 min (+ the gate share of
  combined commands); the lock no longer serializes the 22% of merges that overlapped.
- Risk: the agent learns about a failure later, maybe after it moved on or ended its turn; work stacked on an unmerged
  branch has to be rebased if the merge fails. Two queued merges that each pass alone can fail together — the worker tests
  the combined tree before the second fast-forward.
- Effort: medium: a queue, a worker (the scheduler's 15-minute tick is too slow; a small service), the re-test rule.

### 3.5 Cheaper slow tests

- muninn unit with `-n 4` in `addopts`: −10 s per run (measured 19.7 → 9.4 s) × ~640 runs a week ≈ **110 min/week** at no
  risk the suite has not already shown (it passes at `-n auto`). e2e default `-n 8` over `-n 4`: −5 s per run.
- the-crucible: `pytest-xdist` for the backend (saving **assumed** ~2/3 of 15 s, by analogy with muninn); e2e with a backend
  and fixture database per worker, and a built frontend instead of `next dev` — the biggest item there (93 s avg), but a
  project change, not a flag.
- agent-hub: the plateau (~30 s) is the slowest single tests stretched by load. Moving the librarian/consolidate goldens and
  `test_registrar_flow` from subprocess runs to in-process calls (or splitting each into independent cases) lowers the
  floor toward the next slowest item; how far is **not measured** — measure each test's in-process cost first.

### 3.6 Sharing one run between parallel sessions

Overlapping runs are on different worktrees with different trees, so there is nothing to share except through the cache
(3.1): when two sessions test the same tree (a rebase that made them equal, a re-run after a refused commit message), the
second gets the first's result. The real problem with overlap is contention (2.1). A machine-wide limit on concurrent heavy
suites (e.g. at most two e2e runs, queued) would cut the tail at the cost of queueing.

- Saving: the stretched tail (15 s suites taking 45–95 s), after 3.8 is done; not quantified separately from the leaks.
- Risk: a queue in front of tests adds wait when it is too strict. Effort: medium.

### 3.7 Another machine

Test CPU is small: agent-hub 210 CPU-s per full run, muninn unit ~15 CPU-s, muninn e2e ~37 CPU-s serial. The measured
contention comes from leaked containers and siblings, not from the suites outgrowing 14 cores. The remote client is a
laptop that is sometimes off; cloud CI adds minutes of queue and setup, worse than local for anything the agent waits on.
Fine for an asynchronous tier (3.3/3.4) later; not worth it now. Re-measure after 3.8.

### 3.8 Leaked test infrastructure

Remove the 39 containers (that project's call: a request goes to it) and have the platform guard against it: a nightly
platform job, like `prune-worktrees`, that removes containers labelled `org.testcontainers` older than a day whose session
is gone, reports what it removed, and shows a count on the status page.

- Saving: ~3.7 cores back on a 14-core machine, and the 3–6× stretch measured in 2.1 for whatever ran beside them.
- Risk: removing a container a long test still uses — the age limit and label make that very unlikely. Effort: small.

### 3.9 Weekly waiting report

The measurement in 1 as a platform report (via the runtime adapter that already reads transcripts for memory gaps and the
weekly reflection): model vs test waiting per project, suites per change, reruns. Keeps this measurable after changes.
Effort: small.

## 4. The floor

What cannot be removed: the tests the agent needs to see to keep working (its targeted runs, and e2e while it builds UI),
each at the fastest the machine runs them. Everything else (gates, pre-gate full runs, identical reruns) can be taken off
the critical path (3.1, 3.4) or shrunk (3.2).

Measured per week, today's waits vs that floor:

| | muninn | the-crucible | agent-hub |
|---|---|---|---|
| waiting now (one week) | 447 min | 139 min | 541 min |
| targeted runs (kept) | 303 runs, 29 min | 88 runs, 29 min | 433 runs, 57 min |
| e2e runs at the measured parallel floor (kept, if UI work needs them) | 425 × 28.7 s = 203 min | **assumed** half of 90 min | — |
| floor | **~30–230 min** | **~30–75 min** | **~57 min** |

What is measured and what is assumed in that floor:

- Measured: run counts and durations of targeted runs (transcripts); muninn e2e at `-n 8` (28.7 s).
- Assumed: that every gate and every pre-gate full run can be asynchronous (3.4) or skipped by the cache (3.1) — true when
  the agent has at least that much model work after it, which held for the merges looked at (memory note, report) but was
  not measured per merge; that targeted runs do not get faster (they could, under less load); for the-crucible, that a
  parallel e2e halves it.
- The muninn range is the question of how many of its 425 e2e runs the agent really needs while it works: the low end
  keeps none (gate only, asynchronous), the high end keeps all at parallel speed. The transcripts cannot tell which runs
  were inner-loop.

Not counted: the machine's contention tax (2.1), which inflates every number in the "now" row; clearing it lowers both.

## 5. Recommendation and what to decide

In order of saving per effort and risk:

1. **Leaked test infrastructure (3.8)** — platform nightly cleanup + a request to the project that leaks.
2. **Result cache everywhere + `agenthub test` (3.1)**, with a line in the project rules: run targeted tests while working;
   `agenthub test` (or the gate) for the full suite; do not run the full suite right before the gate.
3. **Project quick wins (3.5)**, sent as requests: muninn `-n 4` (unit) and `-n 8` (e2e) by default; the-crucible a
   `runtime.test` in its registry (backend + jest + tsc; e2e at least when frontend files change) and `pytest-xdist`.
4. **Asynchronous merge queue (3.4)**, lock released during tests.
5. **agent-hub tiered commit gate with test-impact selection (3.2 + 3.3)**: about 31 s → 14 s for the median commit
   (estimated), 17% of commits unchanged.
6. Later: cheaper critical-path tests in agent-hub (3.5), a machine-wide limit on heavy suites (3.6), the weekly waiting
   report (3.9). Not now: another machine (3.7).

To decide (owner): which of 1–5 to build, and for 4, whether an agent may end its turn before its merge is green (the
failure then comes back as a message to that session).
