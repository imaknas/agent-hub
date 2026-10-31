# New users: from zero to the first nightly tidy-up, and the architecture changes in that direction

> agent-hub session · 2026-10-26 · design only; nothing is done before the owner confirms (except step 0, see
> "Steps"). Continues `.state/phase1-design.md` ("no project names in the platform's **code**") and
> `runtime-abstraction.md` ("Claude Code is just one implementation"), without reopening what those two settled
> (central registry, `registry_dir`, `push_on_merge` false by default, ports & adapters).
> **r2 (10-26)**: revised per the Agent Hub session's review (direction agreed; boundaries, module scope, watchdog,
> acceptance test); changes are marked **(r2)**, discussion record in section 8.
> **r3 (10-26, already re-reviewed by the Agent Hub session, comments merged, see section 8)**: settled with the owner
> the classification code / config / data / secret and "git holds only code and config" (3.0); the instance directory
> is therefore split into a config repo + a data directory (3.1 rewritten); a config pipeline added (3.7) with the
> corresponding steps.

## Goal

The owner on 10-26: "imagine a brand-new user who wants to use this platform in the future: what is the most
convenient and most extensible way for them?"

Phase one's acceptance was "no **project** names in the code". This document extends it to: **no names of people,
assistants or machines in the code, and no assumption of two machines**.
A new user clones it, answers a few questions (name, where projects live, whether they want a personal assistant /
alerts / cloud backup), and it runs on one Mac;
adding features later means **registering one more module**, not changing a big script like the librarian.

Three separations, one path, one self-check:

1. **Code vs the user's data**: the platform repo holds only programs, templates, contracts and examples; the user's
   settings, registry and outputs live in their own instance directory.
2. **Core vs modules**: the core is what everyone needs (registry, scheduling, memory sync, the librarian's STATE.md
   tidy-up, mailbox, status page); the rest (personal assistant, alerts, cloud backup, GitHub mirror, weekly review,
   consolidation, sandbox enforcement) are modules that can stay off.
3. **Roles vs machines**: programs only know roles ("the machine memory lives on", "the machine running the
   schedule"); which machine is which is a setting; by default everything is local.
4. **One path**: `git clone` → `bin/setup` → `bin/hub init` → `bin/hub doctor` → `bin/onboard-project.sh` → read the
   digest the next morning.
5. **Self-check**: a mechanical check like `tests/test_runtime_boundary.py` scans `bin/` for personal names; they may
   only go down (ratchet), so code other sessions write cannot regress.

## 1. Where starting from zero gets stuck today (inventory 2026-10-26)

In the order a new user would go; `bin/` + `templates/` + `launchd/`, 88 files in all.

| # | Where it gets stuck | Where |
|---|---|---|
| 1 | No README, no idea where to start; 75 scripts, entry points only in `CLAUDE.md` (written for agents, full of history about the hub machine and the laptop) | repo root |
| 2 | No example settings: `hub.json` is the owner's own settings (`owner.tag` = the owner's name, `assistant.project: muninn`, `backup.repo: b2:<bucket>:…`, `memory_host: <ssh name of the hub machine>`, `github.owner`) | `hub.json` |
| 3 | Code and data mixed in one repo: cloning it gets you the owner's `projects/*.yaml`, `digest/`, `reflect/`, `index.md`, `todo.md`, `review-log.md`, `schedule.yaml`, `mirror-list.txt`, `services.tsv`, `legacy-paths.tsv`, `openclaw-migration-status.md`, `agent-jobs/` | repo root |
| 4 | The platform's launchd plists hard-code the home directory paths of two machines | `launchd/*.plist` (7 files) |
| 5 | The second machine's ssh name hard-coded | `minilib.sh:9`, `recover-project.sh:21`, `sync-all-memory.sh:34`, `transcript-backup.sh:20`, `trajectory.py:36`, `migrate-to-mini.py:42`, `openclaw-session-backup.sh:28` |
| 6 | Hub location and memory copy location hard-coded | `librarian.sh:22,104,111`, `commit-hub-files.sh:13`, `gen-index-roster.sh:20`, `hubbus:281` (hints for agents; **(r2)** hubbus's HEADER/FOOTER, questions_note, qcheck's letters, RESUME/DELEGATE_PROMPT are changed by the Agent Hub session) |
| 7 | The librarian's tail always runs 5 steps in a fixed order, of which `knas-tasks.py` and `reconcile-questions` (:384–391) are personal-assistant-only, yet run without an assistant too | `librarian.sh:375–420` |
| 8 | The to-do owner tag is hard-coded to the owner's name | `gen-todo.py:38`, the prompt at `librarian.sh:57`, the prompt at `consolidate-memory.py:258` |
| 9 | The weekly review and trajectory analysis prompts hard-code the owner's name | `reflect_llm.py:43`, `trajectory.py:134` |
| 10 | The instructions for every project agent hard-code the owner's and the assistant's names | `templates/CLAUDE.md:40,44` |
| 11 | Alerts are Discord only; when unset it is "status page red", not "this module is off" | `alert.py`, `scheduler-watchdog.py` |
| 12 | Personal names scattered through programs and comments: the owner's name in 36 files / 194 lines, the assistant's in 25 files / 155 lines (mostly decision records in comments) | `bin/` |

Already done, and new users benefit directly: `hubconf.py` (the hub root derived from the script's location,
`AGENT_HUB_ROOT`), `bin/py` + `bin/setup-python`, the central registry + fully automatic `onboard-project.sh`,
`github.owner` read from settings, runtime interfaces (Claude replaceable), contracts + contract tests.

## 2. The new user's path (the goal)

```
git clone <agent-hub> ~/agent-hub && cd ~/agent-hub
bin/setup            # = setup-python + check git/gh/claude/jq; prints how to install what is missing, installs nothing
bin/hub init         # asks: your name (to-do tag), time zone, where projects live (~/workspace), where instance data
                     # lives, which modules to enable
                     # → writes <instance>/hub.json (from hub.example.json), an empty projects/, installs the
                     #   scheduler's launchd plist
bin/hub doctor       # everything at a glance: Python, claude login, registry, whether the schedule ticks, each module's
                     # credentials; says how to fix what is broken
bin/onboard-project.sh ~/workspace/my-app
# the next morning: <instance>/digest/<date>.md, the status page
```

- **One machine is the default**. Doing it like the owner ("a Mac mini runs it, the laptop controls it remotely") is an
  option in `hub init` (or a later change to `hosts`), not a prerequisite.
- **The personal assistant is optional**. Without the `assistant` module: no knas-tasks, no hubbus-bridge, letters
  `--to owner` only go to the status page and digest, and channel leasing and idea sandboxes do not exist.
- **Alerts, cloud backup and mirror are all optional**; off means those jobs do not exist, and the status page does
  not turn red.

## 3. Architecture direction

### 3.0 code / config / data / secret (r3)

In today's repo git does three jobs at once: **version control** (code), **recording "what was done when"**
(librarian, consolidate and reflect auto-commit digest/todo/review-log every night; 57 of the last 200 commits are this
kind), and **transport** (each project's `.agent-memory` syncs between the workspace and the librarian's copy through a
bare repo). The second is the wrong tool: "what was done when" should be answered by the records' own timestamps and
the bus's events.

**Rule: git holds only code and config; data never goes into git.** The only exception is memory (see the table).

| Category | Definition | Where it lives | How it is tracked | Backup |
|---|---|---|---|---|
| **code** | written deliberately, the same for every user | the platform repo | git; every commit is an intentional change, through `test-gate` | GitHub (+ mirror) |
| **config** | the user's declaration of "how the platform works for me"; the desired state | the user's config repo (private; whether to push is their call) | git (config as code): every change is a commit, including those made by automatic flows; goes through the 3.7 pipeline; the bus records "which commit was applied" | nightly encrypted backup (a remote is optional) |
| **data: records** | append-only history | the data directory | records carry their own time; events on the bus | nightly encrypted backup |
| **data: derived** | can be regenerated from other things at any time | the data directory | not tracked | not backed up (rerun to get it) |
| **data: runtime state** | checkpoints, schedule state, locks, logs | the data directory | not tracked | only what cannot be rebuilt is backed up |
| **secret** | keys, tokens | the secrets directory | the registry records only name, location, expiry (`check-secrets.py`) | nightly encrypted backup |

**Exception: each project's memory** (`.agent-memory` notes, STATE.md) is data: records, but uses git as
**transport**: sessions and the librarian don't run at the same time and may not be on the same machine, and git gives
conflict handling and offline commits. It lives in each project's own memory repo (the `agent-memory` branch of the
bare repo), not mixed with code or config. This document does not change it; changing the transport is a separate
matter.

**What today's things are**:

| Where it is today | Category |
|---|---|
| `bin/`, `templates/`, `contracts/` (including vendored contracts of others), `capabilities/`, `tests/`, `docs/`, `githooks/`, the templates in `launchd/`, `agent-jobs/mailbox.md`, `known-issues.md`, `CLAUDE.md` | code |
| `hub.json`, `models.env`, `projects/*.yaml` + the prompts of users' jobs, `mirror-list.txt`, `services.tsv` (the hand-written list of services, including non-platform ones), `schedule.yaml` (the old format during the transition; deleted once down to 0 entries) | config |
| `digest/`, `review-log.md`, `reflect/*.json`, `openclaw-migration-status.md`, the LLM-written notes in `index.md`, **(r3)** `legacy-paths.tsv` (the history of "where it used to be", written automatically by rename-project.sh, not a desired state) | data: records |
| the bus (`bus_dir`) | data: records, **one per machine, not in the instance**; backed up (`backup.py` takes `hubconf.bus_db()` separately with the SQLite backup API, not through include: CLAUDE.md's "not backed up" is outdated) |
| `todo.md`, the roster block in `index.md`, `reflect/index.html`, the status page, the `.state/registry/` cache | data: derived |
| the rest of `.state/`, `.logs/` | data: runtime state |
| `.secrets/` | secret |
| **(r3) in the wrong place**: `.state/phase1-design.md`, `phase2-design.md`, `backup-design.md`, `lease-design.md` | code (docs): moved to `docs/design/` before the move |
| **(r3)** the `.bak` files of zprofile/zshenv/claude-settings in `.state/` | secret level: put in `secrets/` or delete |

`index.md` is one file mixing two categories (a mechanically generated roster + LLM-written notes); it is split into two
files.

**(r3) Derived does not mean the location can change freely**: `todo.md` (contract todo-md v1) and `reflect/`
(reflect-week v1) have external readers that read them by path; moving them must happen in the same step as the
readers (see 3.1).

**(r3) What in `.state/` cannot be rebuilt, or is expensive to rebuild, must be listed in the backup explicitly**:

| File | What happens if it is lost |
|---|---|
| `<project>.sha` (librarian checkpoint) | the librarian rereads all memory: cost + duplicated digest |
| `scheduler-last-run.json` | due-time decisions rerun: backup and agent jobs run an extra round, costing money |
| `idea-upgrades.json`, `project-renames.json` | half-done progress; a retry cannot pick it up |
| `knas-tasks-inferred.json` | inferred dones sent again |
| `lease-runner/` (`leases.json` + the files each lease has shown) | the assistant would GET old file ids |
| `alerts-sent.json` | alerts sent again (minor) |
| the reports in `agent-jobs/` | reports gone |

**(r3) History that only git has will disappear**: `todo.md` is overwritten whole every day, as are the LLM notes in
`index.md`; their past versions are now only in git log. Once out of git, the granularity of history becomes the
backup's retention rules (daily for 14 days, weekly for 8 weeks, monthly for 12 months). This granularity is **accepted
deliberately**; only `index-notes.md` becomes append-only (each section dated), so the notes' history stays in the
file.

**Automatic flows changing config** (onboard, rename, `manifest.update`, upgrade-idea): once validated they commit
directly to the config repo, without waiting for a person to approve; the security fields
(`type`/`enforce`/`network`/`allow_write`/`privacy`) can still only be changed by a person (as today). This is version
control, not an activity log: what is committed is "the desired state changed".

**"What was done when"** is answered uniformly by bus events and the records themselves (the digest's date,
review-log's entries); git log no longer carries that job.

### 3.1 The instance directory (rewritten in r3)

- `hubconf` gains one concept: the **instance directory** (`AGENT_HUB_HOME`, default = the hub root, i.e. how things
  are today, so nothing changes before the switch), with three parts under it, each of which a setting can point
  elsewhere:

  ```
  <AGENT_HUB_HOME>/
    config/     ← the config repo (git; `hub init` runs git init; without git it runs too, the platform doesn't commit)
      hub.json  models.env  mirror-list.txt  services.tsv  legacy-paths.tsv
      projects/<name>.yaml  projects/<name>/jobs/*.md
    data/       ← not in git; backed up nightly (except derived data)
      digest/  reflect/  review-log.md  todo.md  index.md  index-notes.md  .state/  .logs/
    secrets/    ← not in git; nightly encrypted backup
  ```

  `hubconf.config(<name>)` and `hubconf.data(<name>)` replace each script building `$HUB/digest` or `$HUB/projects`
  itself.
- The platform repo keeps: `bin/`, `templates/` (including `templates/prompts/mailbox.md`), `contracts/`,
  `capabilities/`, `tests/`, `docs/`, `hub.example.json`, `projects.example/`, README.
- `commit-hub-files.sh` commits only files in the **config repo**; the librarian, consolidate, reflect and gen-todo,
  which write data, no longer commit (the digest/todo/index/review-log/reflect they commit today are all data).
- **The backup scope must change accordingly**: today `hub.json`'s `backup.include` has only `.state` and `.secrets`,
  because the registry and digests are in git and pushing to GitHub counts as another copy. Once data leaves git, the
  backup must include the whole `data/` (except derived) and `config/`, or the registry and records have no backup at
  all.
- `known-issues.md` is platform knowledge and stays in the platform repo, but is rewritten without the "the hub
  machine / the laptop" framing; `openclaw-migration-status.md` and `legacy-paths.tsv` (both data: records) are the
  owner's and move into the instance's `data/`.
- **(r3) External readers read by path; moving files must happen in the same step as them** (or leave a symlink at the
  old location during the transition):
  - Muninn's `~/services/muninn/data/config.yaml`: `external_todos: ~/agent-hub/todo.md`,
    `weekly_stats: ~/agent-hub/reflect`, `lease_runner_token_file: <hub>/.secrets/runner-token` (`project_states`
    reads `~/.memory-copies`, unaffected). The contracts (todo-md, reflect-week) do not change, only the paths.
  - `contracts-check` reads `todo.md` and the latest reflect json: switched to `hubconf.data()`.
  - The owner reading reports on the laptop: the laptop's agent-hub only pulls, so once out of git it no longer sees
    digest and reflect; switch to the status page (see section 7, question 7).
- **(r3) Things that judge by git history**: the comment at `gen-todo.py:107` ("to know which day it was generated,
  look at git log" → look at mtime or the digest instead); weekly-reflect counts each project repo's commits, and
  agent-hub's count becomes more accurate without the librarian's automatic commits, so `hublib`'s rule for "which
  commits count as project work" can be simplified along the way. hubbus and leases do not read `index.md`;
  knas-tasks reads review-log as a file, not through git.
- **(r3) The secrets path**: 8 programs build `hubconf.ROOT / ".secrets"` directly (alert, backup, check-secrets,
  gen-status-page, lease-runner, lease-relay, policy, scheduler-watchdog). Before the move they all switch to
  `hubconf.secret(<name>)`, or they break after it; the ratchet test gains a rule: no `".secrets"` literal in `bin/`
  (the watchdog uses the system python3 and does not import hubconf, so its copy is listed as an exception and reads
  hub.json itself).
- **(r2)** `agent-jobs/` does not move into the instance as a whole: `mailbox.md` is the platform's prompt (used by
  mailbox dispatch) and moves to `templates/prompts/`; the instance holds only the prompts of the user's own jobs
  (phase one G already said they go with the registry: `projects/<name>/jobs/*.md`).
- **(r2) Things that exist "one per machine" stay out of the instance**: the platform's Python
  (`~/.local/share/agent-hub/python`, deliberately not in hub.json: hubconf needs Python before it can read it) and the
  bus (`bus_dir` = `~/.local/state/agent-hub-bus`, deliberately outside the hub so the sandbox can write it).
- **(r2)** Before the move, `schedule.yaml` (old entries during the transition) is cleared down to 0 entries, all moved
  into the registry's `jobs:`, so the instance does not carry one more format to maintain long term.

### 3.2 Modules (core vs optional)

**No new plugin framework**: the platform already has two composition mechanisms, the registry's `jobs:`/`services:`
and the scheduler's `after:`. A module is "a declaration in the same format as a project registry + the settings
section it needs":

```yaml
# modules/assistant/module.yaml (in the platform repo; adopted only when hub.json's modules.assistant is on)
v: 1
name: assistant
requires_config: [assistant.api, assistant.ingest_token]
jobs:
  - {name: bridge, every: 15m, run: bin/hubbus bridge}
  - {name: knas-tasks, at: "03:30", after: [agent-hub/librarian-core], before: [agent-hub/todo], run: bin/knas-tasks.py}
  - {name: reconcile-questions, at: "03:30", after: [agent-hub/librarian-core], run: bin/hubbus reconcile-questions}
services: []
status_sections: [to_owner_backlog]      # extra sections on the status page (gen-status-page looks them up by name)
```

- The scheduler's job sources become: project registries + enabled modules. Job names are `<module>/<job>`; `after:`
  can still point across modules.
- `before:` is new (today there is only `after:`): it lets a module insert itself into the middle of the core flow
  without the core having to know about it. In the implementation the scheduler turns `before: [X]` into an `after:` on
  X. **(r2)** There must be cycle detection (blocked at validation, status page red); an `after:`/`before:` pointing
  to a job of a **module that is off** is treated as nonexistent: core jobs must not wait forever just because the
  assistant is off. Both cases go into tests.
- **The librarian is split**: `librarian-core` (tidying each STATE.md + the digest + the index judgment) stays in the
  core; `todo` (gen-todo), `memory-gaps`, `index-roster`, `commit` become independent core jobs; `knas-tasks` and
  `reconcile-questions` go into the assistant module.
  memory-gaps currently writes into the librarian's digest of the day; after the split the digest is opened by
  `librarian-core` and other jobs append their own section with a small function
  (`hublib.digest_append(<section name>, text)`, a section of the same name is overwritten, so reruns are idempotent).
- The first modules: `assistant` (Muninn), `alerts` (Discord; the interface = `notify(level, title, body)`, which
  `alert.py` already looks like), `backup` (restic; the target set by settings), `mirror` (GitHub mirror), `reflect`
  (weekly review, trajectories), `consolidate`, `sandbox` (phase-two enforcement).
- **(r2) Module scope**:
  - `assistant` = knas-tasks, reconcile-questions, hubbus-bridge, **the lease-runner service, idea sandboxes**
    (create-idea-sandbox / upgrade-idea / project-rename endpoints), show_file, `contracts/vendor/muninn/` and
    `contracts/lease-runner.v2`: their only reason to exist is the assistant. The contracts themselves are neutral
    (lease v2 no longer has runtime vocabulary), so the runner's code stays in the platform repo, and the module only
    decides whether to start the service and schedule the jobs.
  - `sandbox` = rendering `enforce.sandbox` (render-project-settings) + capabilities (cap-runner, `capabilities/`,
    hub.json `capabilities`) + the meaning of `enforce.work`. **Validation of the registry's `enforce` field stays in
    the core**: the registry must be readable with the module off, and the whole registry must not fail because a
    field is not recognised.
  - **Mailbox dispatch is core** (headless agents, forked continuation): letters between projects need someone to
    receive them even without an assistant. With assistant off, only letters `--to owner` go to the status page and
    the digest instead.
- The difference between modules and runtimes: a runtime is "the same capability, a different implementation"
  (Strategy); a module is "whether to have this capability" (a switch). Alerts are both: the `alerts` module is on, and
  `alerts.backend: discord` chooses the implementation.

### 3.3 Machine roles

- `hub.json`'s `hosts`: `{"memory": "local", "scheduler": "local", "remote_control": null}`; the values are `local` or
  an ssh host name. `hubconf.host(role)` replaces every hard-coded ssh name; `minilib.sh`'s "on the hub machine do it
  locally, otherwise ssh" decides by role instead.
- `memory_host`, `hosts.mbp_home` and `hosts.mini_home` are merged into this; `hublib.MBP_HOME/MINI_HOME` remain only
  for reading old conversation logs (`legacy-paths.tsv`).
- The platform's own launchd plists are generated from templates by `render-services.py` like project services (paths
  from hubconf), and the `launchd/` directory keeps only templates. **(r2) Exception: the scheduler's and the
  watchdog's plists are installed by `bin/hub init`/`bin/setup`**, not through render-services: render-services is a
  job the scheduler runs, and the scheduler breaking is exactly what the watchdog has to report. The watchdog keeps
  using the system's built-in python3, not the platform's Python (`test_python_floor`'s EXEMPT and its 3.9 check stay).
- launchd is the boundary: scheduling and services both go through `render-services.py` and `scheduler.py`, so if
  systemd is ever wanted, those two get one more implementation. **No Linux for now.**

### 3.4 A single entry point

`bin/hub <command>`: a thin dispatch layer, no rewriting of existing scripts. `init`, `doctor`, `onboard`, `recover`,
`rename`, `status`, `run <job>`, `jobs` (what `schedulectl` does today), `modules [enable|disable] <name>`. `hub --help`
lists them all, one line each: new users and new sessions both start looking here, and `CLAUDE.md`'s index shrinks to
"run `bin/hub --help` first".

### 3.5 Mechanical check for personal names (ratchet)

`tests/test_personal_boundary.py`: scans `bin/`, `templates/`, `launchd/`, **(r2)** `tests/`, `agent-jobs/` (test data
has them too, and before open source that is a leak just the same); the rules are the owner's and the assistant's
names, machine names, the home directory paths of the two machines, account names (the actual patterns are in the
test).
- In programs (strings, identifiers) an occurrence fails, with exceptions in an allowlist (e.g. `hubbus` accepting the
  `knas` alias is part of the protocol, but the value should be read from `owner.aliases`, so the allowlist should soon
  be empty).
- Decision records in comments ("decided by the owner on 10-24", written with the name) first get a baseline of the
  current state: the count per file **may only go down**. Rewriting them as "the owner decided" or moving them into
  decision records in docs/ is gradual, not cleared in one go.
- Written the same way as the runtime check (`test_runtime_boundary.py`); the nightly contracts-check runs it too.

### 3.6 Acceptance test for a brand-new user (r2)

The ratchet only looks at words; the real acceptance is "a brand-new instance runs". After step 6 a test is added,
reusing the fake HOME + staging copy `bin/test-gate` already has: `AGENT_HUB_HOME` points to an empty instance with
only `hub.example.json`, `bin/hub doctor` and one scheduler tick run, and no personal name or hard-coded path is used
(the fake HOME has no `~/workspace` or `~/git-origin`, so using one fails). Before that, each step is accepted with the
existing tests.

### 3.7 The config pipeline (r3)

Config as code needs a pipeline. Most of the parts exist today, but scattered:

| Stage | What exists | What is missing |
|---|---|---|
| Validate | `registry.py` validates on every load (every scheduler tick), keeps the last valid copy on failure, status page red; `manifest.update` writes only after validating | no validation at commit (`test-gate` deliberately skips files like the registry, so a hand edit is only found wrong at the next tick); `hub.json`, `models.env`, `mirror-list.txt` are not validated |
| Preview | `render-services.py --dry-run` and `render-project-settings.py --report` can each be looked at | no single view of "what this change does to the schedule, services and project settings" |
| Apply | the scheduler reads the registry every tick; `render-services` and `render-project-settings` every 15 minutes; `sync-local-setup` at session start | spread over several separately timed jobs; up to 15 minutes' wait after a change; no "apply now" |
| Drift detection | `render-project-settings` writes back when changed, status page red; `render-services` alerts when a service is not running; `contracts-check` nightly | no knowledge of which config version is in effect |
| Rollback | each project has a "last valid" cache | no explicit rollback; only a manual revert and waiting for the next round |

**The goal** (mostly connecting what exists, `bin/hub config <subcommand>`):

1. **check**: one entry point validating every config file: registry (`registry.py check`), `hub.json` (a new
   `contracts/hub.v1.schema.json`), `models.env`, `mirror-list.txt`, `services.tsv`, module declarations (cycles,
   pointers to jobs that do not exist). The config repo's pre-commit runs it; loading still runs it again (on failure
   keep the last valid copy, behaviour unchanged).
2. **plan**: ask every applier to report with a dry-run what it would change, and print it all together. An applier =
   a module implementing a small interface: `plan(config) -> [changes]`, `apply(config) -> [changes]` (today's
   render-services, render-project-settings, the scheduler's job list and sync-local-setup's managed block are one
   each). A new module that needs to generate something = one more applier.
   **(r3) Written the same way as runtime_ports**: an `APPLIERS` registry `{name: module}`, `load()` mechanically
   checking the functions exist; `Change` is a neutral NamedTuple `(op: add|update|remove, target, detail)`. One
   contract test runs on every applier: ① plan has no side effects (fake HOME + file snapshots identical before and
   after) ② what apply returns == what plan just said ③ planning again after apply gives nothing (idempotent). An
   applier that fails the contract tests cannot be added.
   - **The scheduler's job list**: it reads the registry itself every tick, so `apply` is a no-op and `plan` reports
     jobs added and removed.
   - **sync-local-setup**: its scope is "one workspace" (`--workspace X`; the session-start hook =
     `apply --only local-setup --workspace <this one>`). It has an external action (commit+push AGENT.md to the memory
     repo) that plan cannot do: "aligning AGENT.md" is split into another applier, registered with `external: true`
     (plan only reports, apply pushes).
3. **apply**: run every applier once, immediately; the every-15-minutes round stays as a safety net (it is idempotent
   anyway).
4. **verify**: after applying, check service health and that managed files have not drifted (the existing checks
   gathered into one).
5. **record**: the bus records `config.applied {commit, list of changes}`; the status page shows the config commit in
   effect, yellow when it differs from the config repo's HEAD (changed but not applied).

**Rollback** = `git revert` in the config repo + `hub config apply`. **Automatic flows** (onboard, `manifest.update`…)
writing config go through the same path: check → commit → apply, no separate one.

**(r3) Commit gate**: a new `bin/config-check` (not a branch of test-gate: test-gate only handles "code passes the
tests"). `githooks/pre-commit` calls `test-gate` and `config-check` in order. Requirements: ① config-check validates
the **staged** content (exported with `checkout-index` as test-gate does), not the working tree: a shared working tree
may hold someone else's half-done changes; ② the post-commit record of "skipped the gate" must cover config paths too
(config-check writes its own marker file when it passes). Once config moves to its own repo, that repo has its own
hooksPath, set by `hub init`.

## 4. 步驟(每一步單獨上線)

> **進度**:第 0 步 10-26 完成(librarian 拆成核心 + 5 個 job、digest 分段、路徑從 hubconf、README、`hub.example.json`)。
> 第 1 步 10-26 完成:`tests/test_personal_boundary.py` + `tests/fixtures/personal-baseline.json`(個人名稱 76 個檔 499 處、`.secrets` 字面值 7 個檔 11 處)。
> 跟 3.5 不同:不分程式/註解,每個檔一個總數只准變少(分類要靠猜,總數 ratchet 一樣擋得住變多)。
> 第 1.5 步 10-26 完成:`bin/config-check`(+ `contracts/hub.v1.schema.json`)、`bin/appliers.py` + `applier_scheduler.py`、render-services / render-project-settings 包成套用者(重構前後 42 個產出檔逐位元組相同)、`tests/test_appliers.py`、`bin/hub config check/plan/apply`、`config.applied`(Agent Hub session 597883a)、狀態頁顯示生效的 config commit、githooks pre-commit/post-commit 涵蓋 config。**還沒做**:sync-local-setup 的套用者(session 開始時跑的那支,AGENT.md 對齊要拆成 EXTERNAL 的套用者)——它本來就在每個 session 開始時對齊,沒有 plan 只是看不到預覽,留到第 4 步改樣板時一起做。
> 第 2 步 10-26 完成:只用一個角色 `hosts.server`(跑 hub 的那台;空 = 一台機器全包;沒寫沿用 `memory_host`),`hubconf.host("server")` / `hubconf.py host server`,shell 用 `plutil` 讀(遙控那台不一定有平台的 Python)。7 處寫死的 ssh 名稱換掉。狀態頁、信箱 dispatch、memory 同步搬進 `projects/agent-hub.yaml` 的 `services:`(用 `hub config plan` 先看、再 apply)。`launchd/` 只剩排程器、看門狗(第 5 步由 `hub init` 裝)跟遙控那台的兩份。個人名稱 499 → 479。
> 第 3 步 10-26 完成:模組 = `modules/<名稱>.yaml`(跟登錄同格式,`registry.load_modules()` 放進 `index["modules"]`),
> 排程器跟 render-services 讀專案 + 模組;`before:`、繞圈檢查(`registry.job_cycles`)、沒開的模組的 job 不存在。
> 搬進模組:assistant(knas-tasks、reconcile-questions、lease-runner)、backup、consolidate、mirror、reflect、sandbox(cap-runner),
> 用 `legacy_name` 帶著排程狀態、別人的 `after:` 經 alias 對得上(muninn 的 `after: agent-hub/weekly-reflect`)。
> **跟 3.2 不同、留在核心的**:hubbus-bridge(除了 Muninn 的轉交,還清 60 天前結案的信——要拆成兩半是 hubbus 那邊的事)、
> render-settings(enforce 關掉時的退回、合併閘門的 hook 也是它)、各模組的憑證宣告(還在 `projects/agent-hub.yaml` 的
> `secrets:`)、vendor/muninn 契約的 `consumes:`(contracts-check 只讀專案登錄)。
> 第 4 步 10-26 完成(平台這邊):`templates/CLAUDE.md` → `templates/agent-instructions.md`,人名寫成 `{{owner}}`/`{{owner_alias}}`/`{{assistant}}`,sync-local-setup 照 hub.json 填(`hubconf.fill_names`;24 個專案的輸出逐位元組相同);`hub.json` 加 `assistant.name`;gen-todo、librarian、consolidate、weekly reflect、trajectory 的標籤跟 prompt 改從設定讀(這台的 prompt 逐字相同)。個人名稱 479 → 407。**交給 Agent Hub session**:hubbus 裡的字串、`agent-jobs/mailbox.md` 搬到 `templates/prompts/`。**還沒做**:knas-tasks.py 的檔名/job 名(改名要跟 Muninn 的契約、STATE.md 裡已經蓋上的「等 Knas:」key 一起考慮,先留著)。
> 第 5 步 10-26 完成:`bin/setup`(檢查指令 + setup-python,不自己裝)、`bin/hub` 的 init(沒有 hub.json 才寫、問名字/時區/專案位置/模組;沒開的功能連設定段一起拿掉;排程器跟看門狗從 `launchd/` 的樣板裝,內容一樣就不動)、doctor(用 runtime 的 `auth.credentials()`,排程器要是**這份** hub 的)、modules、onboard/recover/rename/status/jobs/run 包裝。`launchd/` 的 plist 改成 `{{hub}}` 樣板(recover-service 用 sed 填)。沒設定 alerts 的話,看門狗只記錄、狀態頁只提醒,不再永遠紅。`tests/test_new_user.py` = 3.6 的驗收(空實例 + 假 HOME:init → config-check → plan → doctor)。**第 6 步要處理**:平台自己的登錄 `projects/agent-hub.yaml` 還在使用者的登錄資料夾裡(新使用者也需要它),要跟 modules/ 一樣搬回平台。
> 第 6 步 10-26 **程式碼完成、搬家還沒做**(等 Knas,見第 7 節第 9 題)。定下來的(跟 3.1 不同的地方):
> - **一個指標檔切換**:`<hub>/.hub-home` 寫著實例目錄 → `<實例>/{config,data,secrets}`;沒有這個檔 = 舊的擺法(今天的樣子)。
>   搬家時最後才寫,刪掉就退回。覆蓋:`AGENT_HUB_HOME`、`AGENT_HUB_CONFIG`/`DATA`/`SECRETS`(config repo 的 pre-commit 只把
>   `AGENT_HUB_CONFIG` 指到匯出的暫存區)。Python:`hubconf.config_dir()/data_dir()/secrets_dir()/config_file()/data_file()`;
>   shell:`bin/hubpaths.sh`(`$HUB_CONFIG`/`$HUB_DATA`/`$HUB_SECRETS`/`hub_server`,只用 plutil);看門狗自己一份(不 import 平台)。
>   三份一致由 `tests/test_instance_layout.py` 檢查。
> - **`.state`、`.logs` 不搬**:每台機器一份的執行狀態(跟 bus_dir、平台 Python 一樣)。`.logs` 的路徑寫在每個服務的 plist 裡,
>   搬它等於重啟所有服務;`.state` 裡有狀態頁、lease-runner、test-gate、registry 快取,都是這台機器的。
> - hub.json 裡的相對路徑以 hub.json 所在的 config 目錄為準;`.secrets/<名稱>` 一律指 secrets 目錄。
> - **平台自己的登錄**固定在平台 repo 的 `projects/agent-hub.yaml`,registry 不管 registry_dir 在哪都讀它。
> - `commit-hub-files.sh`:新擺法只在 config repo 裡 commit config,data 直接略過(data 不進 git)。
> - 新使用者一開始就是新擺法:`hub init` 建 `~/.agent-hub/{config(git repo,hooksPath = githooks/config),data,secrets}`,
>   最後寫指標檔(`tests/test_new_user.py`,含 config repo 的閘門擋下不合格的 hub.json)。
> - ratchet 多一條 `instance-path`(bin/ 裡自己拼 config/data 檔的路徑)。
> - 這台:42 個產出檔逐位元組相同、`hub config plan` 空、`hub doctor` 沒問題。
> **還沒做**:別的 session 的檔(lease-relay、lease-runner、upgrade-idea 的 `.secrets`/mirror-list;policy.py 的 `.secrets` deny——搬家時
> render-settings 會重寫 24 個專案的設定)已寄給它們;實際搬家。
> **10-26 23:40 搬家完成**(`hub move-instance --to ~/.agent-hub --apply`,先在隔離環境演練兩次、抓到兩個 bug 才做):
> 快照 + 退回腳本(`~/.agent-hub/secrets/agent-hub-move-snapshot-*/rollback.sh`,在搬過的隔離副本上實測過)→ 停排程器 → 搬 →
> `hub config plan` 發現舊的 `.secrets` 還在、deny 一換就沒人擋 → 沙盒設計 session 改 policy 兩個都擋(20e6467)→ apply(3 個 sandbox
> 專案的設定只多了新路徑)→ 重啟 lease-runner、cap-runner → 開回排程器;寫入都到 ~/.agent-hub、舊檔沒被碰。
> **還沒做(明天)**:確認 03:00 的夜間流程正常;Muninn 改 config(m10262338-f805);之後才 `git rm` 平台 repo 裡的舊檔、刪 `~/agent-hub/.secrets`
> (policy 會自動拿掉那條 deny);平台 repo 的 pre-commit 在舊檔刪掉之後要改成只驗平台自己的登錄。
> 狀態頁的三頁(第 7 節第 7 題選 (a))10-26 完成:`digest.html`(最近 7 天)、`todo.html`、`reflect/`(連到 data 的 reflect/),從 data 目錄讀。

| 步 | 內容 | 動到的檔案 | 估計 |
|---|---|---|---|
| **0** | librarian 拆成核心 + job(`after:`;knas-tasks 跟 reconcile-questions 先當成獨立 job,第 3 步再歸進 assistant);路徑改從 hubconf;`hub.example.json` + 根目錄 README(新使用者路徑,還沒有的指令寫「規劃中」) | `librarian.sh`、`projects/agent-hub.yaml`、`hublib.py`(digest_append)、`check-memory-gaps.py`、新增 README、`hub.example.json` | 0.5 天 |
| 1 | 個人名稱 ratchet 測試 + 現況基準(含 tests/、agent-jobs/) | 新增 `tests/test_personal_boundary.py` | 0.25 |
| **1.5 (r3)** | config pipeline:`config check`(含 `hub.v1.schema.json`)、套用者介面 + 把 render-services / render-project-settings 包成套用者、`plan`/`apply`/`verify`、`config.applied` 事件、狀態頁顯示生效的 commit。config 還在平台 repo 裡,pre-commit 先掛在現在的 `githooks/`(只在改到 config 檔時跑 check,不跑全部測試) | 新增 `bin/config-check`、`bin/appliers.py`(登記表 + 介面)+ 契約測試、`render-services.py`、`render-project-settings.py`、`sync-local-setup.sh`(拆出 AGENT.md 對齊)、`gen-status-page.py`、`githooks/pre-commit` 加一行 + `post-commit` 涵蓋 config(Agent Hub session 同意) | 1.5 |
| 2 | 機器角色:`hosts`、`hubconf.host()`;換掉 7 處寫死的 ssh 名稱;平台 plist 改成產生(排程器、看門狗除外) | `hubconf.py`、`minilib.sh` 等 7 支、`render-services.py`(動之前先問沙盒設計 session)、`launchd/` | 1 |
| 3 | 模組:`modules/<名稱>/module.yaml`、排程器讀模組、`before:`(循環偵測、沒開的模組當不存在);assistant(含 lease-runner、想法沙盒)、alerts、sandbox | `scheduler.py`、`registry.py`(不動 validate_enforce)、`gen-status-page.py`、新增 `modules/` | 1.5 |
| 4 | owner 名稱從設定讀:待辦標籤、prompt 裡的名字;`templates/CLAUDE.md` → `agent-instructions.md` 改名(保留 10-26 加的兩段)跟裡面的名字;`mailbox.md` 搬到 `templates/prompts/`。hubbus 裡的字樣跟 Agent Hub session 約時間一起改 | `gen-todo.py`、`librarian.sh`、`consolidate-memory.py`、`reflect_llm.py`、`trajectory.py`、`templates/`、`sync-local-setup.sh`(讀樣板那行) | 0.5 |
| 5 | `bin/hub` + `init`(含裝排程器、看門狗的 plist)+ `doctor` | 新增 `bin/hub` | 1 |
| 6 | 先把 `schedule.yaml` 清到 0 項;實例目錄 `config/` + `data/` + `secrets/`(3.1):`hubconf.config()`/`data()`、讀寫的腳本改用它;`commit-hub-files.sh` 只 commit config repo,寫 data 的腳本不再 commit;`index.md` 拆成名冊 + 附加式註記;`.state/` 的設計文件移到 `docs/design/`;secrets 經過 `hubconf.secret()`;**備份**:新路徑加進 include → **跑一次 `backup.py restore` 到暫存目錄,實際還原出 `config/`、`data/` 才算數**(不等每月的 backup-verify)→ exclude 換成新路徑(順便確認 `backup-staging` 不會被備份兩次)→ `check-secrets` 的 `covers()` 跟 `.secrets` 搬家同一個 commit;外部讀者(Muninn 的 config.yaml 三個路徑)同一步改或留 symlink;**在沒有 job 跑的時段搬**(避開 02:00–04:00 librarian、05:45 backup),或先停排程器、搬完 kickstart;Knas 的資料搬過去;加 3.6 的空實例驗收測試 | 幾乎所有寫產出的腳本、`backup.py`/`hub.json` 的 `backup`、讀 secrets 的 8 支 | 2.5–3 |
| **7 (r3)** | **開源前小心地重寫平台 repo 的歷史**(Knas 10-26 選的,見第 7 節第 8 題),只在第 6 步完成、tree 裡已經沒有使用者資料之後做:① 先做一份完整的 `--mirror` 備份(不動,出事就從這裡還原)② 在新的 mirror clone 上跑 `git filter-repo`,路徑清單明列(`digest/`、`reflect/`、`index.md`、`todo.md`、`review-log.md`、`projects/`、`hub.json`、`schedule.yaml`、`mirror-list.txt`、`services.tsv`、`legacy-paths.tsv`、`openclaw-migration-status.md`、`agent-jobs/` 的使用者 prompt、`.state/` 誤進版控的)③ 再掃一次內容(個人名稱、帳號、token 的字樣;用 ratchet 同一份規則 + 一個 secret 掃描工具),有命中就逐筆決定改寫或刪 ④ 驗收:重寫後 HEAD 的 tree 跟重寫前 HEAD 減掉那些路徑完全一樣、測試全過 ⑤ 保留 filter-repo 的 commit 對照表(文件、memory 筆記裡引用的舊 commit 編號查得到新的)⑥ 換掉之前停排程器、通知所有 session;mini 的 `~/agent-hub`、MBP 的副本、mirror-backup、各 worktree 全部重新 clone;force push 由 Knas 親自確認 | 平台 repo 的歷史(不改程式) | 1 |

順序的理由:0、1 不碰別人的檔、馬上有用;2–4 是新使用者真的會撞到的;6 範圍最大、要 Knas 決定資料怎麼放,放最後,
而且前面每一步都已經把路徑收進 hubconf,6 就只是改 hubconf 的預設值加搬檔。

## 5. 跟其他 session 的邊界

- **runtime 抽象化**(`runtime_*.py`、`llm.py`、`hub-event`)跟**租約**(`lease-*`、`policy.py`、通道租借的契約 v2、
  `lease-show-file.md`)是另一個 session 正在做的;這份的步驟都不動那些檔。
- **(r2) Agent Hub session 的檔,這份也不動**:`bin/hubbus`(dispatch/對帳/分支合併還在試行;給 agent 看的提示由它改)、
  `bin/py`、`bin/setup-python`、`bin/test-gate`、`githooks/`、`tests/test_python_floor.py`、`bin/upgrade-idea.py`、
  `registry.py` 的 `validate_enforce`。
- **(r2)** `scheduler.py`、`render-services.py`、`gen-status-page.py` 沒人在動,這份可以拿;render-services 最近是沙盒設計
  session 改的,動之前先問它。
- **(r2)** `templates/CLAUDE.md` 的改名歸這份(第 4 步),runtime 那邊不做了。
- `hub.json` 的結構(加 `hosts`、`modules`)在第 2、3 步;別的 session 都即時讀它,只加欄位、不改既有欄位的意思,
  照「加欄位不升版」的規則。第 0 步不動 `hub.json`。

## 6. 不在範圍

- Linux / systemd(launchd 只標成邊界)。
- 第二個真的 agent runtime(runtime-abstraction 那份已經說不做)。
- 一台機器多個使用者、圖形化安裝。
- 把 Muninn 本身做成通用的個人助理——這份只讓「沒有助理」也能跑;assistant 模組的介面是 Muninn 定義的那幾份契約。

## 7. 要 Knas 確認的

1. 整體方向:實例目錄 + 模組(沿用登錄/`after:`,加 `before:`)+ 機器角色 + `bin/hub` + ratchet 測試。
2. 步驟順序,以及第 0 步可以先做(它本身就是上一輪談的「librarian 拆開」)。
3. ~~實例資料怎麼放~~ **(r3)** 10-26 跟 Knas 定了:config as code,config 放 private config repo;data 不進 git、靠備份;
   「什麼時候做了什麼」看匯流排跟紀錄本身(3.0)。
> **10-26 Knas 決定:6 同意;7 選 (a) 狀態頁多三頁;8 選 (a) 小心地重寫歷史(做法見第 4 步的第 7 步)。**
> **10-26 Knas 決定(第 9 題):(a) config repo 只留本機 git、不設 remote(靠每晚備份);(b) 搬家時間跟 (c) Muninn 設定照建議:先確認 10-27 03:00 拆開後的第一次夜間流程(librarian + 模組的 job)正常,再挑避開 02:00–04:00、05:45 的時段短暫全停後搬;Muninn config.yaml 的三個路徑在搬家當天寄信請 muninn session 一起換。**

6. **(r3) 1.5 步(config pipeline)排在模組(第 3 步)之前嗎?**(Agent Hub session 同意)
   第 2、3、6 步都在改 config 的**結構**:`hub.json` 加 `hosts`、`modules`,多出 `modules/*/module.yaml`,最後整批搬到
   `config/`。沒有 pipeline 的話,改錯的代價是「要等出事才知道」:
   - **範例 A(打錯字)**:開模組時在 `hub.json` 寫成 `"modules": {"assitant": true}`。今天的 hubconf 不驗證 `hub.json`,
     這個鍵只是被忽略——assistant 模組沒開,knas-tasks、bridge 都不排程。沒有任何錯誤訊息;幾天後才發現 Muninn 沒收到
     新的〔Knas〕任務。有 `config check`:commit 時就被擋,「`modules.assitant`:沒有這個模組(有:assistant、alerts…)」。
   - **範例 B(看不到影響)**:`modules.assistant` 從 false 改成 true。有 `config plan` 時,commit 前先看到:
     ```
     scheduler   + assistant/knas-tasks       03:30(after agent-hub/librarian-core,before agent-hub/todo)
     scheduler   + assistant/bridge           每 15 分鐘
     services    + local.agent-hub.lease-runner   (127.0.0.1:18797)
     ```
     沒有 plan 的話,只能 commit 之後等 15 分鐘看狀態頁。
   - **範例 C(今天就會發生的)**:手改 `projects/<專案>.yaml`,`run:` 打錯路徑。現在要等下一個 tick 才被
     `registry.py` 發現,狀態頁變紅、沿用上一份;有 pre-commit 的 `config-check` 就進不了 commit。
   反過來說,如果先做模組再做 pipeline,第 3 步改排程器、加 `before:` 的時候,就只能靠「等下一個 tick 看結果」來驗證。
9. **(第 6 步) 搬家前要 Knas 決定的**:(a) config repo 要不要 remote——新開一個 private GitHub repo(`gh repo create`,對外)
   或只留本機 git;(b) 搬家的時間:要短暫全停(排程器、lease-runner、cap-runner、信箱 dispatch、狀態頁都拿著路徑),
   不要在 librarian 02:00–04:00、backup 05:45 前後;(c) Muninn 的 config.yaml 三個路徑(todo、reflect、runner token)
   由 muninn session 改(寄信),同一個時段換。
7. **(r3) digest、todo 不進 git 之後,Knas 在哪裡看?**
   - **現在怎麼看**:狀態頁(tailnet 上的網址)只有一頁 `index.html`——job 成敗、🔔 報告、下次
     什麼時候跑;**沒有** digest、todo、每週回顧。這三樣現在只能在 repo 裡打開檔案看(session 裡,或 MBP pull 之後開
     `reflect/index.html`;Knas 沒有用 GitHub 看)。
   - **搬出 git 之後**:這三樣只在 mini 的 `data/` 裡,MBP pull 不到了。
   - **選項**:
     - (a) **狀態頁多三頁**:`/digest/`(最近幾天,markdown 轉成 HTML)、`/todo`、`/reflect/`(直接放現在的 `reflect/index.html`)。
       範例:早上在手機開狀態頁 → 上方的「今日 digest」分頁 → 看到昨晚 librarian 寫的「## <專案> …」;「待辦」分頁就是
       今天的 `todo.md`,〔Knas〕的項目排在最上面。gen-status-page 每個 tick 本來就在重生,多產生三頁的成本很小;
       只在 tailnet 打得開,跟現在一樣。
     - (b) **只看 Muninn**:Muninn 已經讀 `todo.md` 跟 `reflect/`(用路徑),可以在它的 PWA 裡顯示;digest 它現在不讀。
     - (c) **不提供**:要看就 SSH 進 mini。
   - 我建議 (a):不多依賴一個專案,新使用者沒有 Muninn 也看得到。
8. **(r3) 平台 repo 要公開的話,過去的 git 歷史怎麼辦?** 搬檔不會讓歷史消失——digest、todo、STATE.md 的引文、登錄、
   `hub.json` 都在過去的 commit 裡。兩個做法:(a) 重寫歷史(`git filter-repo` 拿掉這些路徑,所有 clone 要重拉);
   (b) 開源時從一個新的 repo 開始(第一個 commit 是當時乾淨的 tree),現在這個 repo 留著當 Knas 自己的私有歷史。
   我建議 (b):比較不會漏,也不影響現在的 mirror 跟 clone。
4. 註解裡的決策紀錄(「Knas 決定」)要不要在開源前全部改寫,還是 ratchet 慢慢清就好。
5. ~~`contracts/vendor/muninn/` 算平台還是 assistant 模組~~ **(r2)** 跟 Agent Hub session 談定:歸 assistant 模組(見 3.2)。

## 8. 討論紀錄

**10-26 Agent Hub session 審查 r1**(方向同意:實例目錄 + 模組沿用 jobs/after + 角色 + bin/hub + ratchet)。採納的:
- 邊界補上它的檔(第 5 節);hubbus 裡給 agent 看的提示由它改,第 4 步改名字時約一起。
- `before:` 要有循環偵測、指到沒開的模組當不存在,進測試。
- assistant 模組包含 lease-runner、想法沙盒、show_file、vendor/muninn、租約 v2 契約;程式碼留平台 repo。
- sandbox 模組的範圍;`enforce` 欄位驗證留核心;信箱 dispatch 是核心。
- `agent-jobs/mailbox.md` 是平台的 prompt,不進實例。
- 平台 Python、匯流排是「每台機器一份」,不進實例。
- 看門狗跟排程器的 plist 不經過 render-services,由 `bin/hub init` 裝;看門狗維持系統 python3。
- 加「空實例跑得起來」的驗收測試(3.6),比 ratchet 直接。
- ratchet 範圍加 `tests/`、`agent-jobs/`。
- 搬家前 `schedule.yaml` 先清到 0 項。
- `templates/CLAUDE.md` 改名歸這份第 4 步。

**10-26 Agent Hub session 複審 r3**(1.5 排在模組之前,同意)。採納的:
- `legacy-paths.tsv` 是 data:紀錄,不是 config;匯流排有備份(CLAUDE.md 的說法是舊的)。
- `.state/` 裡重建不回來、或重建很貴的 7 類明列進備份;放錯地方的設計文件、`.bak` 檔。
- 外部讀者用路徑讀:Muninn 的 config.yaml(我另外找到第三個:`lease_runner_token_file`)、contracts-check、Knas 在 MBP 看報告。
- 只有 git 有的歷史(todo、index 註記)會變粗:刻意接受,index-notes 改成附加式。
- 套用者照 runtime_ports 的寫法:登記表 + 機械檢查 + 三條契約測試;排程器 job 清單 apply 是 no-op;sync-local-setup 帶
  範圍參數,AGENT.md 對齊拆成有對外動作的套用者。
- `bin/config-check` 獨立,不是 test-gate 的分支;驗暫存區;post-commit 也涵蓋 config。
- 備份:加 include 後實際還原驗證、exclude 換路徑、`covers()` 跟 secrets 搬家同一個 commit、secrets 經過
  `hubconf.secret()`(加進 ratchet)、在沒有 job 的時段搬。
- 開源前的 git 歷史(第 7 節第 8 題)。
