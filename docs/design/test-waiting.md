# Waiting on tests: where agent sessions wait, the floor, and what to change

> agent-hub session · 2026-10-31, r9 2026-11-01. Owner's observation: in the projects being worked on now, the bottleneck is not the
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
>
> r5 (same day): the owner's decisions on the two blocking points, relayed from the agent-hub i18n session — tests
> leave `runtime` in a project v2 contract (B1); one result cache per repo (B3); tests always in an exported tree with
> environments cached by lockfile hash (B4). One part of the relayed text (a residual-hash key for ancestors) conflicts
> with a verified review finding; B3 keeps the sound form and says so.
>
> r6: after a third blind review (code facts, architecture, workarounds). Two security holes closed — every run the
> platform starts is sandboxed like the project's jobs (B4); memory clones join the sandbox's paths
> (`projects-and-repos.md`). `.state` stays in the hub root as documented, found from worktrees through the git common
> dir (A1); commit tiers follow where a commit lands; one `is_code()`; input kinds; the cache's clean rule and
> nightly override; the transcript port extension scheduled; the order starts with characterization tests and moves
> agent-hub's own work into worktrees before the large step.
>
> r7 (11-01): after a fourth blind review (security, code facts, order and workarounds). The owner's bar for starting:
> no blocking findings. Fixed: **who runs a test with what authority** — the r6 rule could not work (sandboxes cannot
> be nested, so a session cannot wrap its own runs) and left project-declared `inputs` evaluated outside any sandbox;
> now everything a run evaluates carries exactly the authority of the project it tests (B4). Environments are cached
> per repo, not in the shared tool caches (B4). agent-hub's merges test from step 1 (section 7). The key is the
> exported tree, so the clean rule is gone (B3). Main moves only to a tree with a recorded pass (A1).
>
> r8 (11-01): after a fifth review (security; order and facts). Step 2 reordered so nothing is built twice and
> agent-hub's merges never break (section 7); v1 registry files are upgraded on read, permanently, instead of a
> reader removed per instance (B1); environments are a fresh per-run `uv sync` from a per-repo uv cache — measured at
> 0.07 s warm — so no environment and no package cache is shared across repos or between runs (B4); the runner
> exports a revision, the index or the worktree (B4); the main guard is an accident guard with stated limits (A1);
> the platform runs git in a project's repo only as that project (`projects-and-repos.md` section 4).
>
> r9 (11-01): a sixth review found 10 blocking points. Three refute r7/r8 mechanisms that were written without a test
> (the absolute `core.hooksPath`, `run-tests`' temporary HOME/STATE, a bundle handed over as a file); several are
> holes in today's sandbox, now given to a separate fix. **Not settled; building does not start.** The refuted
> sentences are marked open below (section 7, "Open after round 6") instead of being replaced by new guesses.
>
> r10 (11-01, after the owner paused reviews until the sandbox fix landed, which it has): the design's security claim
> is scoped to "adds no path by which a project exceeds its sandbox" (B1); round 6's open points are decided, each in
> its section (section 7, "Settled after round 6"), mostly by reusing what the 11-01 fix built (per-repo tool caches
> with their own interpreters, `project_git.py`); step 2.3's switching merge is rehearsed in a throwaway hub before it
> lands.

## Summary

- One week (10-24 → 10-31 18:00) of the three active projects' top-level sessions: **26–31% of agent time is waiting
  on tests** (18.2 hours across the three), about 58% is the model. Subagents waited another 6.1 hours on tests in the
  background.
- The waiting is mostly **the same suites run several times per change**: in muninn the unit suite ~2.6 times and the
  e2e suite ~2.4 times per merge — the agent, then `merge-branch`, then `bin/deploy.sh`, which re-tests the exact commit
  `merge-branch` just tested (128 min a week). Then **browser e2e suites**, **gates that block the agent**, and **a
  shared machine under load** (leaked test containers, since removed, plus other sessions' suites: the same suite took
  13 s at one moment and 45–86 s at another).
- The plan (7): agent-hub's own work moves into worktrees first (they find the main checkout's instance; merges run
  the full gate); then the foundations that are final as built — project v2 with a `tests` list independent of
  `runtime` (which lets the-crucible declare tests), one result cache per repo, tests always in an exported tree with
  per-run environments from a per-repo package cache, the machine's worker budget; then the projects-and-repos resolver,
  onto which selection and keys extend.

## 1. How this was measured

Everything below is measured on the hub machine (Apple M4 Pro, 14 cores) unless marked **assumed**. The scripts were
throwaway and are not in the repo (they parse one runtime's transcript format, which belongs in a runtime adapter); the
method is described here in enough detail to rebuild them (B7).

**Which projects.** muninn and the-crucible have the most `merge.done` events since 10-24 (230 and 28); agent-hub has
almost none because its sessions commit straight to main, and was taken for its sessions and commits (31.8 hours of
agent time, 266 code commits in the window).

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
  goldens are 46–59% of the suite's module-seconds and their second, English pass (`@en`) 23–29% (three snapshots); the
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

At the time of measurement (before its `1c7f474` of 10-31, section 5):

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

1. **A linked worktree finds the same instance and state as the main checkout.** Without `.hub-home` (untracked), a
   worktree falls back to the old layout with everything in the worktree itself: its own `.state` (no registry cache, so
   `merge-branch` cannot find the project), no config, no private-names list — and `test-gate` then silently skips the
   private-project-names check. Final form, keeping the documented decision that `.state` and `.logs` are per-machine
   run state in the hub root (`hubconf.py` header, `docs/design/new-user.md`):
   - `hubconf` finds the hub root of the **main checkout** by reading the worktree's `.git` and `commondir` files (no
     git subprocess at import; `hubconf` is also imported by the system Python), and takes `.hub-home`, `.state` and
     `.logs` from there. The shell readers of `.hub-home` (`bin/hubpaths.sh`, `commit-hub-files.sh`) do the same
     through one shell helper, `agenthub` (Python) through `hubconf`; the scheduler watchdog is always started from the
     main checkout and keeps its own copy.
   - An explicit `AGENT_HUB_ROOT`, `AGENT_HUB_HOME` or `AGENT_HUB_STATE` skips the lookup — the commit gate's exported
     tree (no `.git`, under `GIT_CEILING_DIRECTORIES`) and every test set them. `bin/run-tests` sets none of them today
     (only `PYTHONPATH` and `AGENT_HUB_TEST_RUNNER`); in the same change it sets `AGENT_HUB_ROOT` to the tree it tests
     when unset, **and nothing else**: that is how `test-gate` runs the suite on every commit today (`AGENT_HUB_ROOT`
     = the exported tree), and an explicit root skips the lookup, so a run in a worktree sees that worktree's own empty
     layout, never the live instance. Setting `AGENT_HUB_HOME` or `AGENT_HUB_STATE` as well fails 7 modules, whose
     fake hubs would inherit them (round 6, reproduced). The shell scripts that
     build `.state` paths themselves (`test-gate`'s durations, `post-commit`'s bypass log) take the state folder from
     `hubpaths.sh`.
   - The platform's own files are taken from the main checkout too: the resolver defines the hub repo as the main
     checkout of the running copy's git common dir (`project_of` in `merge-branch`; `platform_registry()` and `LOGS` in
     `hubconf`).
   - Scripts that rebuild instance state from the platform's code (registry refresh, config apply, the scheduler) refuse
     when **the script itself** lives in a linked worktree **and found the live state by the lookup**, where they would
     rebuild the live index from a half-edited copy. With an explicit `AGENT_HUB_STATE` they run: `config-check
     --staged` validates a staged registry file that way, into a temporary state, from a worktree's pre-commit. The
     test runner and its per-checkout files are not such writers.
   - hub.json lists the instance files tests may use as `config:` inputs (`tests.config_inputs`, a new key in
     `contracts/hub.v1.schema.json`; B1 uses the same key); `test-gate` fails, instead of skipping, when the
     private-names list is listed and cannot be found.
   - The hooks stay as they are in step 1: `githooks/pre-commit` runs the committing checkout's own `test-gate`, so a
     branch's commits and its merge are gated by the branch's gate scripts — what the shared checkout does today,
     where the working tree's copy runs. Gate code taken from the main checkout comes with the runner (prerequisite 2,
     step 2.3).
   - **agent-hub's merges test from the first worktree merge:** its registry entry declares `runtime: {test: bin/test-gate
     --all}` (valid for the hub's entry, whose `dirs()` is the hub root). `merge-branch`'s rebase runs no pre-commit
     and its fast-forward no gate (of the hooks, only `post-merge` and `reference-transaction`, none installed today;
     checked with git 2.50), so without this the rebased tree would reach the running platform untested. It is a data
     value in agent-hub's registry entry, replaced in step 2.3 by that entry's v2 tiers. `test-gate`'s tree store makes
     a no-op rebase a hit.
2. **What a commit runs depends on where it lands, not where it is made:** a commit on a branch runs the project's
   `commit` tiers; a commit onto main in the main checkout is a merge and runs the `merge` tiers. For agent-hub: a `lint`
   tier (lint and the config check; `when: [commit, merge, nightly]`) and a `full` tier (`when: [merge, nightly]`), whose
   command is `run-tests` plus the HOME-leak check taken out of `test-gate` (the runner already exports and gives every
   tier a fresh `HOME`, B4; the private-names list comes in as a `config:` input). `test-gate` itself becomes a thin
   caller of the runner (B4), so there is no loop.
   **Gate code from main, declaration from the tree under test (decided after round 6):** every caller runs the
   runner of the hub's main checkout (the `agenthub` on PATH execs it), never a copy inside the tree being tested, so
   a branch cannot certify its own gate; the runner tests the branch's runner code like any other code. The test
   declaration of the hub repo's own entry is read from the tree under test (`projects/agent-hub.yaml` in the
   export), every other project's from the registry, so the merge that changes agent-hub's tiers is tested by the
   tiers it brings. This is what makes step 2.3's switching merge work: the old `merge-branch` runs agent-hub's v1
   `runtime.test` (`bin/test-gate --all`) in the worktree, that is the branch's thin `test-gate`, which calls main's
   runner (landed in step 2.2) with the branch's v2 tiers; none of them is `test-gate`, so nothing recurses. agent-hub's `code` covers `projects/`, `modules/` and
   `hub.example.json` as well as code, so the config check in the `lint` tier still runs on registry-only commits.
   **Main moves only to a tree that passed (decided after round 6):** the check is `merge-branch`'s own, before it
   fast-forwards: it refuses unless every affected `merge` tier has a recorded pass for the rebased tree, and a refusal
   applies nothing. A move of main by any other path (a hand `merge --ff-only`, `cherry-pick`, `--no-verify`) is
   **detected, not blocked**: a `reference-transaction` hook in its `committed` state logs it and the status page shows
   red. Blocking it there would leave the main checkout half-applied (tested in round 6: a refused fast-forward kept
   the ref but left the new files in the working tree and index), which for agent-hub means untested files live in
   the running platform. This replaces `githooks/post-commit`'s bypass log; it only reports when main's value changes
   to a different commit (`pack-refs` and same-value updates are not reported). It is an accident guard, not a security
   boundary (any session can set `core.hooksPath`; `git-sync-memory.sh` does). In a `commit_gate` repo nothing else
   moves main: `git-safe-push.sh` (whose `pull --no-rebase` becomes a fetch) and the pulls in the hub checkout
   (`librarian.py`, `consolidate-memory.py`, `librarian-publish.sh`, `weekly-reflect.sh`) only fetch, and commits
   that reach origin some other way enter main through a branch and `merge-branch`.
3. **The commit gate is opted into in the registry** (a repo-level `commit_gate: true`, among v2's fields; in a one-repo
   project's own file, in a repo file once a repo has several projects), not implicitly through the repo's own
   `core.hooksPath`. It means: the platform installs its `pre-commit` (the `commit` tiers, or the `merge` tiers on main)
   and `reference-transaction` hooks into the repo's git dir the way it installs `gate: strict`'s hooks
   (`pre-commit`, `pre-merge-commit`), not under a repo's own `core.hooksPath` (reported instead, as `gate: strict` is
   today). **agent-hub gets no exception:** in step 2.3 its gate moves from the tracked `githooks/` into its git dir
   like every other repo's, its `core.hooksPath` is unset, and `agenthub init` and doctor change with it (both expect
   `githooks` today). `gate: strict` and `commit_gate` share the one platform `pre-commit`; `strict` wins on main (no
   commit there at all). Hooks are installed without following symlinks (closed 11-01 by the sandbox fix). agent-hub
   opts in.
4. **The main checkout:** a fast-forward is blocked only by uncommitted files the merge changes; the sessions working
   there finish before the switch.

**Costs:** a fix is live only after it is merged. After the switch, agent-hub's suite runs inside `merge-branch` under
the repo's merge lock, so parallel merges queue; a merge whose rebase was a no-op is a cache hit (B3). From step 1 to
step 2.3, `post-commit` logs every commit a rebase replays as a bypass (information only; the merge tests the result),
a hand fast-forward of main is not reported, as it is not today, and a branch's gate scripts gate that branch, as the
working tree's copy does today.

### A2. Commit-time test selection with a coverage map — deferred

Replaying the window's 266 code commits against a coverage map: 17% must run everything; the rest select a median 26%
of the suite's test-seconds, an **assumed** ~44% of its wall time. With A1, branch commits run only the `lint` tier, so
selection would only shorten what the agent runs itself, at the cost of a nightly map and an unmeasured miss rate.
Deferred until B7's numbers say it matters; if built, it is a source of paths for a tier (B1), not a separate rule.

### A3. Cheaper slow tests

- Profile and split the largest modules: `test_sandboxed_services`, `test_registrar_flow`, `test_librarian_flow`
  (suite work, independent of how tests are selected).
- The English pass of the i18n goldens (`@en`, 23–29% of module-seconds) becomes its own tier whose `paths` are i18n
  code, prompts, templates and golden fixtures (B1), so it runs at merge only when those change and always at night;
  `bin/run-tests` gains a switch to leave it out of `full`. First check whether `@en` ever failed alone on a commit
  touching none of those paths.

### A4. No full-suite run before the gate (proposed rule)

A rule for agent-hub's sessions, not written anywhere yet: targeted tests while working; `agenthub test` (B4) for a full
check; the merge runs the rest. The reason agents re-run today — the gate cuts the failure output — goes away with the
runner's complete-output file (B4).

## 4. B. The platform's test system

The platform does not make a project's suite faster (that is C). It decides **what a project's tests are** (B1), **which
of them a change needs** (B2), **whether a run already happened** (B3), **how and where they run, for every caller**
(B4), **the machine they run on** (B5), **what agents are told** (B6) and **how we know it worked** (B7). One system: one
declaration, one selection function, one result cache, one runner. Later, on the numbers: whether the agent waits at
all (B8).

### B1. The test declaration (project v2)

**Decided by the owner (10-31, relayed from the agent-hub i18n session):** tests leave `runtime:`. Today
`runtime: {deploy, test}` means "has `<services_root>/<name>`", which tied tests to a deployment folder — the reason
the-crucible cannot declare any. Every earlier shape is dropped. The final form, a **project v2** contract:

```yaml
v: 2
runtime: {deploy: bin/deploy.sh}               # only deployment is left in runtime
code: ["src/**", "tests/**", "pyproject.toml", "uv.lock", "package*.json"]
inputs: ["cmd:python3 --version"]               # lockfiles are code: in the tree hash
tests:
- {tier: full, run: "uv run pytest -q -n {jobs}", when: [merge, nightly, deploy]}
- {tier: e2e,  run: "uv run pytest -q -m e2e -n {jobs}", paths: ["src/*/web/**"], when: [merge, nightly, deploy],
   jobs: {max: 8}}
```

- **Patterns** (`code`, `paths`, `file:` inputs) are relative to the scope folder the tier runs in — the project's
  folder until scopes exist; for a project in several repos, the folder of the tier's `repo:`.
- **`code`** says which changed files count as code for this project; `is_code(project, paths)` is the one function that
  answers it, used by `test-gate` and the commit hooks from step 2.3 instead of their two `CODE` patterns (which already
  differ: one has `ruff.toml`). agent-hub's list goes into `projects/agent-hub.yaml`.
- **`inputs`** are what changes results without being code and is not already in the tested tree: `file:<path>` (a
  file in the repo outside the project's folders, resolved by real path and invalid if it leaves the repo; a file
  inside them adds nothing, it is already in the key's tree hash), `config:<name>` (a bare file name the platform
  lists in hub.json `tests.config_inputs` (A1) — today only the private-names list, symbolic so a public registry entry names no path; the platform makes
  each listed file readable in sandboxes, and any other name is invalid), `env:<NAME>` (also passed into the tier,
  B4), `cmd:<command>` (its output, e.g. an interpreter's version, run in the tier's fixed environment). Inputs are
  evaluated by the runner with the authority of the run (B4), never by the platform on its own. agent-hub declares its Python version and
  `config:private-names.txt`, today's two extra key parts.
- **Tiers** `{tier, run, paths?, when, jobs?, repo?}`. `when` is required: any of `commit` (repos with `commit_gate`),
  `merge`, `nightly`, `deploy`. A tier runs when a change touches the project's code and, if it has `paths`, one of
  them. `jobs` says how the tier takes a worker count — a `{jobs}` placeholder in `run`, or `jobs: {env: NAME}` — and
  its `max` (e.g. 1 for a suite that cannot run in parallel); the runner fills it from the machine's budget (B5). `repo`
  names the repo a multi-repo project's tier runs in (added with scopes).
- **Repo-level fields** v2 knows from the start: today's (`paths.origin`, `push_on_merge`, `commits`, `gate`, `enforce`,
  `network`, `allow_write`, `privacy`) plus `commit_gate` (A1).
- **Contract:** renaming `runtime.test` and changing what `runtime` means bumps project to **v2**. The files live in two
  repos — the platform's own entry in the platform repo (`platform_registry()`), every other in the instance's config
  repo, whose pre-commit validates with the live platform's `registry.py` (which today refuses any `v` but 1) — so the
  cutover is a sequence, not one commit. **A v1 document is upgraded to v2 when it is read**, permanently: by
  `registry.py` on load, by `apply-updates` for a `manifest.update` written in v1, by `config-check`; this is the one
  upgrade function, kept as long as any deployment may hold v1 files (a release note decides when it goes, never one
  instance's state). The upgrade: `runtime.test` becomes a `full` tier with `when: [merge, nightly, deploy]`; an absent
  `code` means every file in the project's folder (so an upgraded project is never under-tested); a `runtime` that held
  only `test` stays as `{}` (validation guarantees its folder exists, and `runtime`'s presence still means that folder).
  `agenthub config apply` rewrites the files in the new form afterwards. The upgrade sits where documents enter the
  index (`registry.refresh`, including its last-valid fallback, which serves a cached document through the same
  upgrade); the index records the schema version it was built with, and `hubconf.projects()` — which reads the index
  without PyYAML — treats an index older than its code as missing and refreshes it; `merge-branch` refuses a
  document that is not v2 instead of reading it as "no tests". Changing with it: `contracts/project.v2.schema.json`,
  `registry.py` (known fields, `dirs()`, `default_cwd`, validation, the header) and the one reader of `runtime.test`,
  `merge-branch`; the readers of whether `runtime` exists (`check-secrets.py`, `lease-runner.py`, `hub-run`,
  `render-services.py` through `default_cwd`, and those through `dirs()`) keep their meaning. Later additions
  (`scopes`, `depends_on`, a tier's `repo`) are additive to v2. `session.report`'s `tests` field becomes a list of
  `{project, tier, key, ok, output}` and `session.report`'s payload goes to `v: 2` (the bus contract's rule for a
  change of meaning); no code reads the field, but `contracts/lease-handover.v1.schema.json` pins it as an object (or
  null) and muninn vendors that contract, so lease-handover goes to v2 beside v1 until muninn has switched (a request
  in step 2.7).
- **Security — what this design claims (decided after round 6):** it adds no path by which a project exceeds its
  sandbox. Whether the sandbox itself holds is the sandbox's own scope (`projects-and-repos.md` section 9; the 11-01
  fix; `bus-sender.md`), not this design's. Within that claim: `tests` and `inputs` stay changeable by the project
  (`manifest.update`): they are its own. That is safe because nothing a project declares is ever evaluated with more
  authority than the project's own (B4), and because the fields that set that authority (`enforce`, `network`,
  `allow_write`, `paths`, later `scopes`) are platform-owned. A project without a sandbox (or held back by `service_sandbox_exempt`) has the user's authority by
  definition, so there is no boundary for it to cross. It assumes a
  `manifest.update` comes from the project it names; today a letter's sender is chosen freely (`hubbus send --from`)
  and the bus file is writable in every sandbox — a gap that predates this design and applies to every
  `manifest.update` field, so it is fixed in the bus (`bus-sender.md`, waiting for the owner).

### B2. Which tests a change needs: `affected(repo, base, head, moment)`

One function: each changed path triggers its **owning** project if the path matches that project's `code`; then every
project that depends on a triggered one follows — under `projects-and-repos.md` rule 5, ancestors whose runners collect
nested scopes, every scope nested under the one owning the file (test runners load configuration from parent folders:
`conftest.py`, `pyproject.toml`; the root owner's files, triggering everyone in the repo, are the case of scope `/`),
the `depends_on` closure — without being filtered by its own `code` (a dependent's tests depend on the dependency's
code). Of each project, the tiers whose `when` includes the caller's moment and whose `paths` match. Until the
resolver exists the repo has one project and this is the same function with one answer. Every caller asks it: the
commit gate and the `reference-transaction` check, `merge-branch`, `agenthub test`, the nightly run, B7's
classification. A caller runs only the tiers that run in its own repo; a pair in another repo (a cross-repo
`depends_on`) is reported as pending and left to that repo's nightly run, whose key misses because the dependency's
folder hash changed. Validation rejects a tier `repo:` in which the project has no scope.

### B3. One result cache

**Decided by the owner (10-31):** one shared module, one key function, **one store per repo, in the git common dir**,
where `test-gate`'s store already lives and which every session of the repo already writes (commits do).

- `key(project, tier, tree)` = the content the tier's tests depend on in the **exported** tree + the tier's declared
  `run` text and `jobs.max` (not the worker count filled in, which follows the machine's load) + the values of
  `inputs` + the runner's version and its fixed tier environment (B4). The tree is the one the run exported (the `write-tree` of its temporary index, B4), so what ran is exactly
  what the key names, uncommitted changes included, and no clean-checkout rule is needed. The content is the tree hash
  of the project's folder — for an ancestor including nested scopes, since its runner collects them (the relayed
  text's "scope residual" is unsound there, found in the second review; this doc keeps the sound form and the owner
  can overrule it) — plus the own files of every ancestor scope on the path from the repo root (the root owner's in a
  shared repo; each the hash of its owned `(path, blob)` list, from `git ls-tree`), plus, for a dependent, the
  dependencies' folders.
- `test-gate`'s passed-trees file (`e1d6540`) is this cache's case for agent-hub's one scope `/` — it already keys the
  `write-tree` of the index it exports: its logic moves into the module and gains the declared command; nothing is
  thrown away. Entries expire by age; one force flag replaces `TEST_GATE_FORCE`.
- **A nightly failure overrides** the recorded pass for that key, so a flaky pass does not stay green for merges and
  deploys.
- Trust: any session of a repo can record a result for any project in that repo — the same trust domain that already
  shares the repo's hooks (`projects-and-repos.md` 2.1).

### B4. Where tests run, the runner and its callers

**Decided by the owner (10-31): always in an exported clean tree**, as `test-gate` does today. `agenthub test` joins
the session commands (`SESSION_COMMANDS`, `policy.platform_cli()`), so a sandboxed session may run it.

`agenthub test [--tier T] [--when M] [--force] [--rev R | --index]` exports what it tests — by default the worktree's
current content (through a temporary index, uncommitted changes included); `--index` what is staged (the commit gate);
`--rev` a commit (deploy and the nightly run) — runs the affected tiers in the export with a fresh environment and a
worker count from the budget, writes the **complete output to a file in the worktree's git dir and prints its path** with a summary,
records the results (B3), and ends each tier by stopping its whole process group — a terminate signal, a grace period,
then kill, so a test server's own shutdown still runs (the leaked containers came from a server killed outright, whose
children survived; containers are outside the group, so B5's cleanup module stays). It never touches the branch: no
rebase, no lock.

- **Where:** the export goes under the repo's git common dir (`agent-hub/test/`, beside the tool caches the 11-01 fix
  put in `agent-hub/cache/`), the one place every caller can
  write — a session of the repo, and a project's sandbox, which includes its repo's `.git` — and not a temporary
  directory (test-gate's reason: sandbox profiles allow writing those, so the tests that catch writes outside a folder
  could not) nor the hub's `.state` (denied to sandboxes). Git is fenced off from the enclosing repo with
  `GIT_CEILING_DIRECTORIES`, as test-gate does.
- **Environment (r8):** **a fresh environment per run, built from the lockfile into the run's directory, from the
  repo's own tool caches** — the ones the 11-01 sandbox fix already gives every project in its git dir
  (`agent-hub/cache/<tool>`, set through `policy.env`: `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, `PIP_CACHE_DIR`,
  `npm_config_cache`, `COREPACK_HOME`), so interpreters as well as packages are per repo and no sandbox can write
  another repo's; the runner sets the same variables for every project, sandboxed or not, and adds the browser
  cache tiers need (`PLAYWRIGHT_BROWSERS_PATH`) to that list. Nothing
  ready-to-run is shared: a run cannot change what a later run sees, and no other repo can change this repo's
  packages. The shared tool caches are not used, because every sandbox may write them and a package changed there is
  linked unchecked into the next environment built from it (shown in review 5 with uv). Measured with muninn's
  lockfile (uv 0.11, APFS): `uv sync --frozen` into a new folder took 4.9 s with a cold cache and **0.07 s warm**
  (120 packages, linked as copy-on-write clones), and the project imported from the export. The project and its
  workspace members are installed from the export each run, so nothing stale is carried. npm follows the same shape
  (`npm ci` from the per-repo cache), measured when it is built; an ecosystem without a layer yet runs in the session's
  own environment, said explicitly. Cost: each repo downloads its packages once on its own.
- **What a tier sees:** the tool variables above, so a fresh `HOME` hides nothing a tier needs (round 6: with a fresh
  `HOME`, uv found no managed interpreter and Playwright no browsers), and a fixed environment (the platform's `PATH`,
  which has uv and nvm's node; `AGENT_HUB_PYTHON` for agent-hub's tiers; locale; the
  platform's variables; the run environment's `bin`) plus its declared `env:`
  inputs; nothing else from the caller, so a session's `PYTEST_ADDOPTS` or `SKIP_E2E` cannot turn a pass into a false
  one; `GIT_*` cleared; `HOME` and `TMPDIR` fresh folders inside the run's directory.

**Authority (r7).** Everything a run evaluates — the git export (clean filters and fsmonitor come from the repo's own
config), the `inputs`, the tier — runs with the authority of the project it tests, never more:

- **Runs a session starts** (`agenthub test`, `merge-branch`, the commit gate, a deploy script) run as that session:
  they execute only what the agent could run directly, inside whatever sandbox the session has. They add no wrap of
  their own: sandboxes cannot be nested (`seatbelt.py`, tested 10-25: `sandbox_apply: Operation not permitted`), and
  these sessions have unsandboxed commands switched off. This is AGENTS.md's rule for sessions a person opens: the tool's
  protection, advised but not guaranteed by the platform.
- **Runs the platform starts** are command jobs of the project in the registry (the nightly run, B8's worker later), so
  the whole `agenthub test` invocation goes through `isolation.wrap` like every command job: sandboxed for a project
  with `enforce.sandbox` (not run when the sandbox cannot be applied, as `sandboxed-run` does today), and with the
  user's authority for a project without one, which has no boundary to cross. The platform reads the results from the store as data;
  it never evaluates a project's commands or inputs itself.

Callers:

- **`merge-branch`** (in the session): after its rebase, the affected `merge` tiers through the runner; after a no-op
  rebase the tree is unchanged and the agent's own `agenthub test` result is a hit (209 of muninn's 227 merges).
- **Commit gate** (repos with `commit_gate`, `--index`): the `commit` tiers of a branch commit; a commit onto main runs the merge
  tiers; the `reference-transaction` check keeps main on tested trees (A1).
- **Deploy:** the deploy script runs `agenthub test --when deploy`, which covers **every** deploy tier for HEAD's key —
  a lookup when the merge already ran it, a run otherwise (a commit made directly on main is not deployed untested). It
  runs in the main checkout, where deploy scripts run today (AGENTS.md: from the development copy) and where the store
  is local; it tests `--rev HEAD` and refuses when the checkout has uncommitted changes, since it deploys HEAD.
  Requested from projects once the runner exists.
- **Nightly:** a platform-created command job per project: `agenthub test --when nightly --force --rev <main>`,
  exported like every other run (no worktree), after the container cleanup (B5); a failing test is re-run once and both
  results reported.

### B5. The machine

- **A worker budget for the machine:** one lock file per worker slot next to the bus (which every sandbox can reach by
  design); a runner holds a slot by an advisory lock on its file, which the kernel releases when the process ends, so
  a crashed runner frees its slots and nothing has to be reclaimed. The runner passes the count into the tier as
  declared (B1); two suites at once no longer start 2 × 14 workers. Since any sandboxed project can take locks, a wait
  is bounded: after a minute without enough slots a run starts with the slots it holds (at least one), and the status
  page names the projects holding slots — a project can slow others, never stop them.
- Test containers: the runner's process-group stop removes the usual cause; a nightly job that removes containers labelled
  `org.testcontainers` older than a day is a **module** (it is specific to Docker and one library), with a count on the
  status page.
- Check what Spotlight indexes on the hub machine; exclude workspaces, worktrees and scratch folders if it is them.
- Another machine: not now (test CPU is small: agent-hub ~210 CPU-s per full run, muninn unit ~15 CPU-s).

### B6. What every project's agents are told (proposed rules)

Nothing in `templates/agent-instructions.md` says how to run tests today. Proposed now: run targeted tests while
working; write the memory note and the report before calling `merge-branch` (work agents already do after a gate, so
the gate overlaps it). With the runner: `agenthub test` for the full check; a failure's complete output is in the file
it names; tiers declared `when: merge` are left to the merge.

### B7. The waiting report

The measurement in section 1 as a platform report. It needs the transcript port to report, per session, its folder and
per tool call the command text, duration, entry ids (for deduplication) and whether it came from a subagent — today the
port takes folders as input and its `Turn` has none of these. Extending the port, in every runtime adapter with its
contract tests, is its own step (section 7, step 2.5). The report attributes sessions with `project_at`, classifies runs by the declared
tiers (patterns only for undeclared commands), deduplicates, counts deploy runs and subagents, and also reports the time
the owner actually waited on a session that was waiting on tests. Its first report is the baseline for the steps after
it (section 2.1 is the baseline for the steps before). Its first use, before any e2e tier is declared `when: merge`:
classify the agents' e2e runs as safety runs (followed by `merge-branch`) or inner-loop work (followed by an edit).

### B8. Asynchronous merge (merge queue) — on the numbers

`merge-branch` returns after the quick checks; a worker runs the tests, fast-forwards on green, reports on the bus. Its
bound is the lower "after the gate" numbers (2.1). Decided after B7 has run for a week; it reuses the runner and cache.

## 5. C. Each project's own suite (requests sent 10-31)

- **muninn (done 10-31, `1c7f474`, measured by the project):** `-n 4` on the full-suite commands only (deploy, CI,
  docs; `runtime.test` sent as a manifest.update), not in `addopts`: unit ~20 s → ~8 s, three runs each. e2e at `-n 8`
  in deploy and docs: ~30 s → ~19 s. Nothing turned flaky.
- **the-crucible (done 10-31, `b5e0d07`, measured by the project):** tests made parallel-safe and `pytest-xdist` added
  (530 backend tests ~16 s → ~10 s at `-n 4`); e2e against a production build (4.2 → 3.7 min with the build, the first-
  test compile timeouts gone). Its test command (17 s end to end) was rejected by the registry gap (2.4) and waits for
  B1. Not done: a backend per e2e worker; it waits for its e2e tier's `when`.
- **Next, when the platform has them (step 2):** declare tests in project v2 with the runner's worker placeholder
  (`-n {jobs}`, B1) instead of a fixed `-n 4` in tier commands — the fixed count is the projects' choice until the
  budget exists; deploy scripts run their `deploy` tiers through `agenthub test` (B4).
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

**Settled after round 6 (11-01)**, each where it belongs: `bin/run-tests` sets only `AGENT_HUB_ROOT` (A1
prerequisite 1; checked against the suite: it is how `test-gate` already runs it); hooks unchanged in step 1, gate code
from the main checkout and the hub's declaration from the tree under test from step 2.3, agent-hub's hooks in its git
dir with no exception (A1 prerequisites 1–3); the main guard in `merge-branch`, hand moves detected, not blocked (A1
prerequisite 2); the v1 upgrade at the index, with a schema version (B1); interpreters and tool caches per repo, from
the 11-01 fix (B4); 2.1 not wired into live validation (below); the full list of pulls (A1). **Step 2.3's switching
merge is rehearsed in a throwaway hub (the fake-hub pattern the tests already use) before it merges into the real
one** — the one step whose behaviour prose review could not settle, and which cannot be rehearsed before 2.1 and 2.2
exist.

**Decided 10-31 (owner): the final architecture first, no workarounds.** Nothing is built in a form a later step
replaces; each step only adds to the ones before it.

0. **Before anything here (done 11-01, except what waits for the owner):** the separate fix of the holes that exist
   today (`projects-and-repos.md` section 9): the platform's git in project repos (`project_git.py`), per-repo tool
   caches, memory reads, `paths` as a security field, hooks. Still open there, outside this design: the letter sender
   (`bus-sender.md`), runtime settings files a sandboxed job can write, the scheduler's per-job timeout.
1. **A1, in the shared checkout, small:** characterization tests for everything step 2 changes (`test-gate`,
   `post-commit`, `merge-branch`, registry validation of `runtime`, `hubconf`'s layout); then A1 prerequisite 1 (a
   linked worktree finds the main checkout's instance and state; explicit overrides; `bin/run-tests` pointing them at
   temporary folders; `tests.config_inputs`; the absolute `core.hooksPath`; agent-hub's `runtime.test`). Then every
   agent-hub session moves to a linked worktree and merges with `merge-branch` (the rule goes into AGENTS.md;
   prerequisite 4): branch
   commits run today's full gate (correct, slower), merges run it again on the rebased tree (a hit when the rebase
   changed nothing), and the running platform is never half-edited.
2. **Foundations, in worktrees (final as built), in this order:**
   1. B1's schema: `contracts/project.v2.schema.json` (`tests`, `code`, `inputs`, `commit_gate`; `runtime` =
      `deploy`) and its validation, unit-tested only: the live validators (`apply-updates`, the config repo's
      pre-commit, `registry.refresh`) keep refusing v2 until 2.3, so no v2 file can land while the old `merge-branch`
      would read it as untested.
   2. B3, B4 and B5's slots: the cache module (`test-gate`'s store becomes it), the runner (export of worktree, index or
      revision; per-run environments for uv and npm; the fixed tier environment; worker slots; complete-output file;
      process-group stop), `agenthub test` (also a session command), `affected()` answering with today's one project
      per repo. The runner reads the hub's own declaration from the tree under test (A1 prerequisite 2); with no v2
      declaration anywhere yet, `agenthub test` elsewhere says "no tests declared". The store's module lands here and
      `test-gate` keeps its own file until 2.3 makes it thin.
   3. **One merge** switching the readers and callers: the read-time upgrade of v1 documents (B1) and `is_code()`;
      `merge-branch` on the runner; agent-hub's entry rewritten by hand as v2 with
      the `lint` and `full` tiers taken out of `test-gate` (its step-1 `runtime` removed); `test-gate` thin; A1
      prerequisites 2–3 (commit tiers by where a commit lands, the `reference-transaction` check, `commit_gate`, the
      shared `pre-commit`, fetch-only pulls); `session.report` v2 and lease-handover v2 beside v1. Then `agenthub config
      apply` rewrites the other registry files. Then deploy and the nightly job.
   4. B5's container module and the Spotlight check.
   5. The transcript port extension B7 needs, in every runtime adapter, with contract tests.
   6. B6's proposed rules that hold now; A3's module splits.
   7. Requests: the-crucible declares its tests; muninn's deploy runs its deploy tiers through the runner; muninn
      moves its vendored lease-handover to v2.
3. **`projects-and-repos.md` steps 1–3**, in worktrees: characterization tests, the resolver and registry, the callers
   (backup and deployment per repo already here).
4. **B7**, the waiting report, on the resolver; its first report is the baseline for what follows, and its e2e
   classification decides each project's e2e `when`.
5. **On the resolver:** `affected()` and the cache key extended to scopes and `depends_on` (rule 5); A3's `@en` tier;
   B6's remaining rules.
6. **`projects-and-repos.md` steps 5–8:** memory, runtime capabilities, onboarding, the end-to-end monorepo.
7. **After a week of B7:** decide B8 (merge queue) and A2 (commit-time selection).

Costs of this order, so they are a known choice: step 2 is large (a contract bump with every registry file, the cache
module, the runner with its environment layers, a port extension). Until its deploy caller lands, muninn's deploys keep
re-testing (128 min a week). From step 1 until step 2.3, agent-hub runs its full suite twice per change (branch commit
and merge; the second is a hit when the rebase changed nothing) and `post-commit` logs replayed commits as bypasses
(A1 "Costs"). After step 2.3, agent-hub's merges run its suite under the merge lock, so parallel merges queue. Steps 3
and 6 (the identity refactor) bring nothing to the three measured projects, which are one repo each; they are the
price of the general architecture.
