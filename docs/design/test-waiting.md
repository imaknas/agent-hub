# Waiting on tests: where agent sessions wait, the floor, and what to change

> agent-hub session · 2026-10-31 · r6. Owner's observation: in the projects being worked on now, the bottleneck is not the
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

## Summary

- One week (10-24 → 10-31 18:00) of the three active projects' top-level sessions: **26–31% of agent time is waiting
  on tests** (18.2 hours across the three), about 58% is the model. Subagents waited another 6.1 hours on tests in the
  background.
- The waiting is mostly **the same suites run several times per change**: in muninn the unit suite ~2.6 times and the
  e2e suite ~2.4 times per merge — the agent, then `merge-branch`, then `bin/deploy.sh`, which re-tests the exact commit
  `merge-branch` just tested (128 min a week). Then **browser e2e suites**, **gates that block the agent**, and **a
  shared machine under load** (leaked test containers, since removed, plus other sessions' suites: the same suite took
  13 s at one moment and 45–86 s at another).
- The plan (7): first the foundations that are final as built — the instance's state out of the hub root, project v2
  with a `tests` list independent of `runtime` (which lets the-crucible declare tests), one result cache per repo,
  tests always in an exported tree with environments cached by lockfile hash, the machine's worker budget; then
  agent-hub on worktrees; then the projects-and-repos resolver, onto which selection and keys extend.

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
     `.logs` from there. `bin/hubpaths.sh` and the other readers of `.hub-home` (`commit-hub-files.sh`, `agenthub`) do
     the same through one shell helper; the scheduler watchdog is always started from the main checkout and keeps its
     own copy.
   - An explicit `AGENT_HUB_ROOT`, `AGENT_HUB_HOME` or `AGENT_HUB_STATE` skips the lookup — the commit gate's exported
     tree (no `.git`, under `GIT_CEILING_DIRECTORIES`) and every test set them; `bin/run-tests` always points them at
     temporary folders, so no test can reach the live instance through a worktree.
   - The platform's own files are taken from the main checkout too: the resolver defines the hub repo as the main
     checkout of the running copy's git common dir (`merge-branch`'s `project_of`, `platform_registry()`, `LOGS`).
   - Scripts that rebuild instance state from the platform's code (registry refresh, config apply, the scheduler) refuse
     when **the script itself** lives in a linked worktree, where they would rebuild the live index from a half-edited
     copy. The test runner and its per-checkout files are not such writers.
   - hub.json declares whether the deployment has a private-names list (a new key in `contracts/hub.v1.schema.json`);
     `test-gate` fails, instead of skipping, when one is declared and cannot be found.
2. **What a commit runs depends on where it lands, not where it is made:** a commit on a branch runs the project's
   `commit` tiers; a commit onto main in the main checkout is a merge and runs the `merge` tiers. For agent-hub: a `lint`
   tier (lint and the config check; `when: [commit, merge, nightly]`) and a `full` tier (`when: [merge, nightly]`), whose
   command is the export + fake HOME + `run-tests` sequence taken out of `test-gate`. `test-gate` itself becomes a thin
   caller of the runner (B4), so there is no loop. `githooks/post-commit` logs a bypass when a commit skipped its tiers.
3. **The commit gate is opted into in the registry** (a repo-level `commit_gate: true`), not implicitly through the
   repo's own `core.hooksPath`. agent-hub opts in. Its main is protected by this gate, not by `gate: strict`, which the
   platform cannot install in a repo that has its own `core.hooksPath`.
4. **The main checkout:** a fast-forward is blocked only by uncommitted files the merge changes; the sessions working
   there finish before the switch.

**Costs:** a fix is live only after it is merged. After the switch, agent-hub's suite runs inside `merge-branch` under
the repo's merge lock, so parallel merges queue; a merge whose rebase was a no-op is a cache hit (B3).

### A2. Commit-time test selection with a coverage map — deferred

Replaying the window's 266 code commits against a coverage map: 17% must run everything; the rest select a median 26%
of the suite's test-seconds, an **assumed** ~44% of its wall time. With A1, branch commits run only the `lint` tier, so
selection would only shorten what the agent runs itself, at the cost of a nightly map and an unmeasured miss rate.
Deferred until B7's numbers say it matters; if built, it is a source of paths for a tier (B1), not a separate rule.

### A3. Cheaper slow tests

- Profile and split the largest modules: `test_sandboxed_services`, `test_registrar_flow`, `test_librarian_flow`
  (suite work, independent of how tests are selected).
- The English pass of the i18n goldens (`@en`, 27–29% of module-seconds) becomes its own tier whose `paths` are i18n
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
inputs: ["file:uv.lock", "file:package-lock.json", "cmd:python3 --version"]
tests:
- {tier: full, run: "uv run pytest -q -n {jobs}", when: [merge, nightly, deploy]}
- {tier: e2e,  run: "uv run pytest -q -m e2e -n {jobs}", paths: ["src/*/web/**"], when: [merge, nightly, deploy],
   jobs: {max: 8}}
```

- **Patterns** (`code`, `paths`, `file:` inputs) are relative to the project's folder (its scope folder later).
- **`code`** says which changed files count as code for this project; `is_code(project, paths)` is the one function that
  answers it, used by `test-gate` and `githooks/post-commit` from step 1 instead of their two `CODE` patterns (which
  already differ: one has `ruff.toml`). agent-hub's list goes into `projects/agent-hub.yaml`.
- **`inputs`** are what changes results without being code: `file:<path>` (in the repo), `config:<name>` (a file in the
  instance's config, e.g. the private-names list — symbolic, so a public registry entry names no path), `env:<NAME>`,
  `cmd:<command>` (its output, e.g. an interpreter's version). agent-hub declares its Python version and
  `config:private-names.txt`, today's two extra key parts.
- **Tiers** `{tier, run, paths?, when, jobs?}`. `when` is required: any of `commit` (repos with `commit_gate`), `merge`,
  `nightly`, `deploy`. A tier runs when a change touches the project's code and, if it has `paths`, one of them. `jobs`
  says how the tier takes a worker count — a `{jobs}` placeholder in `run`, or `jobs: {env: NAME}` — and its `max`
  (e.g. 1 for a suite that cannot run in parallel); the runner fills it from the machine's budget (B5).
- **Contract:** renaming `runtime.test` and changing what `runtime` means bumps project to **v2**. All registry files
  are in the instance's config repo and are rewritten in one change; `contracts/project.v2.schema.json`, `registry.py`
  (known fields, `dirs()`, `default_cwd`, validation, the header) and the readers of `runtime` (`merge-branch`,
  `check-secrets.py`, `lease-runner.py`, `render-services.py`) change together. Later additions (`scopes`,
  `depends_on`, a tier's `repo:` for multi-repo projects) are additive to v2. `session.report`'s `tests` field becomes
  a list of `{project, tier, key, ok, output}` in the same change; nothing reads it today.
- **Security:** `tests` and `inputs` stay changeable by the project (`manifest.update`): they are its own. That is safe
  only because every run the platform starts is sandboxed like the project's jobs (B4).

### B2. Which tests a change needs: `affected(repo, base, head, moment)`

One function: each changed path triggers its **owning** project if the path matches that project's `code`; then every
project that depends on a triggered one follows — under `projects-and-repos.md` rule 5, ancestors whose runners collect
nested scopes, everyone in the repo for root-owner files, the `depends_on` closure — without being filtered by its own
`code` (a dependent's tests depend on the dependency's code). Of each project, the tiers whose `when` includes the
caller's moment and whose `paths` match. Until the resolver exists the repo has one project and this is the same
function with one answer. Every caller asks it: the commit gate, `post-commit`, `merge-branch`, `agenthub test`, the
nightly run, B7's classification.

### B3. One result cache

**Decided by the owner (10-31):** one shared module, one key function, **one store per repo, in the git common dir**,
where `test-gate`'s store already lives and which every session of the repo already writes (commits do).

- `key(project, tier, tree)` = the content the tier's tests depend on in the tested tree + the resolved command + the
  contents of `inputs`. The content is the tree hash of the project's folder — for an ancestor including nested scopes,
  since its runner collects them (the relayed text's "scope residual" is unsound there, found in the second review; this
  doc keeps the sound form and the owner can overrule it) — plus, for a dependent, the dependencies' folders, and, in a
  shared repo, the root owner's own files (the hash of its owned `(path, blob)` list, computed from `git ls-tree`).
- A result is recorded only when the key's folders are **clean**: `git status --porcelain`, untracked non-ignored files
  included, shows nothing there — so what ran is exactly what the key names.
- `test-gate`'s passed-trees file (`e1d6540`) is this cache's case for agent-hub's one scope `/`: its logic moves into
  the module and gains the resolved command; nothing is thrown away. Entries expire by age; one force flag replaces
  `TEST_GATE_FORCE`.
- **A nightly failure overrides** the recorded pass for that key, so a flaky pass does not stay green for merges and
  deploys.
- Trust: any session of a repo can record a result for any project in that repo — the same trust domain that already
  shares the repo's hooks (`projects-and-repos.md` 2.1).

### B4. Where tests run, the runner and its callers

**Decided by the owner (10-31): always in an exported clean tree**, as `test-gate` does today. The environment is
rebuilt from the lockfiles and **cached by the lockfiles' hash** (a venv, `node_modules`) in the tool caches every
sandbox may already write, so it is built once per lockfile version. Cost, stated: an environment layer per ecosystem
(uv and npm first); a project whose ecosystem has none yet runs uncached, said explicitly.

`agenthub test [--tier T]` exports the worktree's current content (through a temporary index, uncommitted changes
included), runs the affected tiers in the export with the cached environment and a worker count from the budget, writes
the **complete output to a file in the worktree's git dir and prints its path** with a summary, records the results
(B3), and kills each tier's whole process group when it ends (the leaked containers came from killed processes whose
children survived). It never touches the branch: no rebase, no lock.

**Every run the platform starts is sandboxed like the project's jobs:** for a project with `enforce.sandbox`, the tier
command goes through `isolation.wrap` (as the scheduler does for jobs), and the platform records the result outside the
wrapped command. Callers:

- **`merge-branch`** (in the session): after its rebase, the affected `merge` tiers through the runner; after a no-op
  rebase the tree is unchanged and the agent's own `agenthub test` result is a hit (209 of muninn's 227 merges).
- **Commit gate** (repos with `commit_gate`): the `commit` tiers of a branch commit; a commit onto main runs the merge
  tiers (A1).
- **Deploy:** the deploy script runs `agenthub test --when deploy`, which covers **every** deploy tier for HEAD's key —
  a lookup when the merge already ran it, a run otherwise (a commit made directly on main is not deployed untested). It
  runs from the deployment clone, which the resolver recognizes as its repo's deployment and which reads that repo's
  store in the main checkout's git common dir. Requested from projects once the runner exists.
- **Nightly:** every project's main, all tiers, the force flag, in a platform-managed worktree inside the repo's folder
  (excluded, removed after the run, not listed by the worktree cleanup), after the container cleanup (B5); a failing
  test is re-run once and both results reported.

### B5. The machine

- **A worker budget for the machine:** worker tokens in a small pool next to the bus (which every sandbox may write by
  design), each token held with the runner's process id so a crashed runner's tokens are reclaimed. The runner passes the
  count into the tier as declared (B1); two suites at once no longer start 2 × 14 workers.
- Test containers: the runner's process-group cleanup removes the cause; a nightly job that removes containers labelled
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
contract tests, is its own step (7). The report attributes sessions with `project_at`, classifies runs by the declared
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

**Decided 10-31 (owner): the final architecture first, no workarounds.** Nothing is built in a form a later step
replaces; each step only adds to the ones before it.

1. **In the shared checkout, small:** characterization tests for everything step 2 changes (`test-gate`, `post-commit`,
   `merge-branch`, registry validation of `runtime`, `hubconf`'s layout); then A1 prerequisite 1 (a linked worktree
   finds the main checkout's instance and state; explicit overrides; the declared private-names list). From here on,
   agent-hub's own work happens in linked worktrees: their commits run today's full gate (correct, slower) and land on
   main by fast-forward, so the running platform is never half-edited.
2. **Foundations, in worktrees (final as built):**
   - B1: project v2 (`tests`, `code`, `inputs`; `runtime` = `deploy`), every registry file rewritten in one change;
     `is_code()` replacing the two `CODE` patterns.
   - B3 and B4: the cache module (`test-gate`'s store becomes it), the runner (exported tree, uv and npm environment
     layers, worker counts, complete-output file, process-group cleanup, `isolation.wrap` for sandboxed projects),
     `agenthub test`, `affected()` answering with today's one project per repo; callers: `test-gate` (thin),
     `post-commit`, `merge-branch`, deploy, the nightly run.
   - A1 prerequisites 2–3 (commit tiers by where a commit lands; `commit_gate` in the registry).
   - B5: the worker pool, the container module, the Spotlight check.
   - The transcript port extension B7 needs, in every runtime adapter, with contract tests.
   - B6's proposed rules that hold now; A3's module splits.
   - Requests: the-crucible declares its tests; muninn's deploy runs its deploy tiers through the runner.
3. **A1:** agent-hub's sessions on worktrees and `merge-branch`; branch commits run the `lint` tier.
4. **`projects-and-repos.md` steps 1–3**, in worktrees: characterization tests, the resolver and registry, the callers
   (backup and deployment per repo already here).
5. **B7**, the waiting report, on the resolver; its first report is the baseline for what follows, and its e2e
   classification decides each project's e2e `when`.
6. **On the resolver:** `affected()` and the cache key extended to scopes and `depends_on` (rule 5); A3's `@en` tier;
   B6's remaining rules.
7. **`projects-and-repos.md` steps 5–8:** memory, runtime capabilities, onboarding, the end-to-end monorepo.
8. **After a week of B7:** decide B8 (merge queue) and A2 (commit-time selection).

Costs of this order, so they are a known choice: step 2 is large (a contract bump with every registry file, the cache
module, the runner with its environment layers, a port extension). Until its deploy caller lands, muninn's deploys keep
re-testing (128 min a week). During step 2 agent-hub's worktree commits run the full gate each (as today, but in a
worktree). After step 3, agent-hub's merges run its suite under the merge lock, so parallel merges queue; a no-op rebase
is a cache hit. Steps 4 and 7 (the identity refactor) bring nothing to the three measured projects, which are one repo
each; they are the price of the general architecture.
