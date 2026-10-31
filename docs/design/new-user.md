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
(`type`/`enforce`/`network`/`allow_write`/`privacy`/`metered_models`) can still only be changed by a person (as today). This is version
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
  systemd is ever wanted, those two get one more implementation. **No Linux for now.** (10-29: Linux is supported
  through systemd user units, `bin/systemd_units.py`; see tool-agnostic.md.)

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

## 4. Steps (each goes live on its own)

> **Progress**: step 0 done 10-26 (librarian split into core + 5 jobs, digest in sections, paths from hubconf, README,
> `hub.example.json`).
> Step 1 done 10-26: `tests/test_personal_boundary.py` + `tests/fixtures/personal-baseline.json` (personal names in 76
> files, 499 places; `.secrets` literals in 7 files, 11 places).
> Different from 3.5: no split between code and comments; one total per file that may only go down (classifying would
> mean guessing, and a total ratchet blocks increases just as well).
> Step 1.5 done 10-26: `bin/config-check` (+ `contracts/hub.v1.schema.json`), `bin/appliers.py` +
> `applier_scheduler.py`, render-services / render-project-settings wrapped as appliers (42 output files byte for byte
> the same before and after the refactor), `tests/test_appliers.py`, `bin/hub config check/plan/apply`,
> `config.applied` (Agent Hub session 597883a), the status page showing the config commit in effect, githooks
> pre-commit/post-commit covering config. **Not done yet**: the sync-local-setup applier (the one run at session
> start; aligning AGENT.md has to be split into an EXTERNAL applier); it already aligns at every session start, and
> without plan only the preview is missing, so it is left for step 4's template changes.
> Step 2 done 10-26: only one role, `hosts.server` (the machine running the hub; empty = one machine does everything;
> unset falls back to `memory_host`), `hubconf.host("server")` / `hubconf.py host server`, read with `plutil` in shell
> (the remote machine may not have the platform's Python). The 7 hard-coded ssh names replaced. The status page,
> mailbox dispatch and memory sync moved into `services:` in `projects/agent-hub.yaml` (look first with
> `hub config plan`, then apply). `launchd/` keeps only the scheduler, the watchdog (installed by `hub init` in step 5)
> and the remote machine's two. Personal names 499 → 479.
> Step 3 done 10-26: a module = `modules/<name>.yaml` (same format as the registry; `registry.load_modules()` puts them
> into `index["modules"]`); the scheduler and render-services read projects + modules; `before:`, the cycle check
> (`registry.job_cycles`), jobs of modules that are off do not exist.
> Moved into modules: assistant (knas-tasks, reconcile-questions, lease-runner), backup, consolidate, mirror, reflect,
> sandbox (cap-runner), carrying their scheduling state with `legacy_name`, and others' `after:` still match through
> aliases (muninn's `after: agent-hub/weekly-reflect`).
> **Different from 3.2, left in the core**: hubbus-bridge (besides the assistant's handoffs it also clears letters
> closed over 60 days ago; splitting it in two is hubbus's side), render-settings (the fallback when enforce is turned
> off, and the merge gate's hook, are also it), the modules' credential declarations (still in
> `projects/agent-hub.yaml`'s `secrets:`), the `consumes:` of the vendor/muninn contracts (contracts-check only reads
> project registries).
> Step 4 done 10-26 (the platform side): `templates/CLAUDE.md` → `templates/agent-instructions.md`, people's names
> written as `{{owner}}`/`{{owner_alias}}`/`{{assistant}}`, filled by sync-local-setup from hub.json
> (`hubconf.fill_names`; the output for 24 projects byte for byte the same); `hub.json` gains `assistant.name`; the tags
> and prompts of gen-todo, librarian, consolidate, weekly reflect and trajectory are read from settings (this machine's
> prompts identical word for word). Personal names 479 → 407. **Handed to the Agent Hub session**: the strings in
> hubbus, moving `agent-jobs/mailbox.md` to `templates/prompts/`. **Not done yet**: knas-tasks.py's file name/job name
> (renaming has to be considered together with the assistant's contract and the to-do key markers already stamped into
> STATE.md files; left for now).
> Step 5 done 10-26: `bin/setup` (checks commands + setup-python, installs nothing itself), `bin/hub`'s init (writes
> hub.json only when there is none; asks name/time zone/project location/modules; for features left off, their
> settings section is removed too; the scheduler and watchdog are installed from the templates in `launchd/`, left
> alone if identical), doctor (uses the runtime's `auth.credentials()`; the scheduler must be **this** hub's), modules,
> and onboard/recover/rename/status/jobs/run wrappers. The plists in `launchd/` become `{{hub}}` templates
> (recover-service fills them with sed). Without alerts configured, the watchdog only logs and the status page only
> reminds, no longer red forever. `tests/test_new_user.py` = the 3.6 acceptance (empty instance + fake HOME: init →
> config-check → plan → doctor). **For step 6 to handle**: the platform's own registry `projects/agent-hub.yaml` is
> still in the user's registry folder (new users need it too); it must move back to the platform like modules/.
> Step 6 **code done 10-26, the move not yet done** (waiting for the owner, see section 7, question 9). What was settled
> (where it differs from 3.1):
> - **Switched by one pointer file**: `<hub>/.hub-home` holds the instance directory →
>   `<instance>/{config,data,secrets}`; no such file = the old layout (today's). Written last during the move; deleting
>   it rolls back. Overrides: `AGENT_HUB_HOME`, `AGENT_HUB_CONFIG`/`DATA`/`SECRETS` (the config repo's pre-commit only
>   points `AGENT_HUB_CONFIG` at the exported staging area). Python:
>   `hubconf.config_dir()/data_dir()/secrets_dir()/config_file()/data_file()`; shell: `bin/hubpaths.sh`
>   (`$HUB_CONFIG`/`$HUB_DATA`/`$HUB_SECRETS`/`hub_server`, plutil only); the watchdog has its own copy (it does not
>   import the platform). `tests/test_instance_layout.py` checks the three agree.
> - **`.state` and `.logs` do not move**: runtime state, one per machine (like bus_dir and the platform's Python). The
>   `.logs` path is written into every service's plist, so moving it means restarting every service; `.state` holds
>   the status page, lease-runner, test-gate and the registry cache, all belonging to this machine.
> - Relative paths in hub.json are relative to the config directory hub.json is in; `.secrets/<name>` always means the
>   secrets directory.
> - **The platform's own registry** is fixed at `projects/agent-hub.yaml` in the platform repo; the registry reads it
>   wherever registry_dir is.
> - `commit-hub-files.sh`: in the new layout it commits config only inside the config repo, and simply skips data
>   (data does not go into git).
> - New users start in the new layout: `hub init` creates
>   `~/.agent-hub/{config (git repo, hooksPath = githooks/config),data,secrets}` and writes the pointer file last
>   (`tests/test_new_user.py`, including the config repo's gate blocking an invalid hub.json).
> - The ratchet gains an `instance-path` rule (bin/ building config/data file paths itself).
> - This machine: 42 output files byte for byte the same, `hub config plan` empty, `hub doctor` fine.
> **Not done yet**: other sessions' files (lease-relay, lease-runner, upgrade-idea's `.secrets`/mirror-list; policy.py's
> `.secrets` deny: during the move render-settings rewrites 24 projects' settings) have been sent to them; the actual
> move.
> **10-26 23:40 the move is done** (`hub move-instance --to ~/.agent-hub --apply`, rehearsed twice in an isolated
> environment first, which caught two bugs): snapshot + rollback script
> (`~/.agent-hub/secrets/agent-hub-move-snapshot-*/rollback.sh`, tested on a moved isolated copy) → stop the scheduler
> → move → `hub config plan` found the old `.secrets` still there, and once the deny switched nothing would block it →
> the sandbox design session changed policy to block both (20e6467) → apply (the 3 sandbox projects' settings only
> gained the new path) → restart lease-runner and cap-runner → scheduler back on; all writes went to ~/.agent-hub, old
> files untouched.
> **Not done yet (tomorrow)**: confirm the 03:00 nightly flow is fine; the assistant changes its config
> (m10262338-f805); only then `git rm` the old files in the platform repo and delete `~/agent-hub/.secrets` (policy
> removes that deny automatically); once the old files are deleted, the platform repo's pre-commit should validate only
> the platform's own registry.
> The status page's three pages (section 7, question 7, chose (a)) done 10-26: `digest.html` (last 7 days),
> `todo.html`, `reflect/` (linking to data's reflect/), read from the data directory.

| Step | Content | Files touched | Estimate |
|---|---|---|---|
| **0** | librarian split into core + jobs (`after:`; knas-tasks and reconcile-questions as independent jobs first, moved into assistant in step 3); paths from hubconf; `hub.example.json` + a root README (the new user's path; commands that don't exist yet marked "planned") | `librarian.sh`, `projects/agent-hub.yaml`, `hublib.py` (digest_append), `check-memory-gaps.py`, new README, `hub.example.json` | 0.5 day |
| 1 | personal-name ratchet test + current baseline (including tests/, agent-jobs/) | new `tests/test_personal_boundary.py` | 0.25 |
| **1.5 (r3)** | config pipeline: `config check` (including `hub.v1.schema.json`), the applier interface + render-services / render-project-settings wrapped as appliers, `plan`/`apply`/`verify`, the `config.applied` event, the status page showing the commit in effect. Config is still in the platform repo, so pre-commit hangs on today's `githooks/` for now (runs check only when config files change, not all tests) | new `bin/config-check`, `bin/appliers.py` (registry + interface) + contract tests, `render-services.py`, `render-project-settings.py`, `sync-local-setup.sh` (AGENT.md alignment split out), `gen-status-page.py`, one line added to `githooks/pre-commit` + `post-commit` covering config (agreed by the Agent Hub session) | 1.5 |
| 2 | machine roles: `hosts`, `hubconf.host()`; replace the 7 hard-coded ssh names; platform plists generated (except scheduler and watchdog) | `hubconf.py`, `minilib.sh` and the other 7 scripts, `render-services.py` (ask the sandbox design session before touching it), `launchd/` | 1 |
| 3 | modules: `modules/<name>/module.yaml`, the scheduler reads modules, `before:` (cycle detection, modules that are off treated as nonexistent); assistant (including lease-runner, idea sandboxes), alerts, sandbox | `scheduler.py`, `registry.py` (validate_enforce untouched), `gen-status-page.py`, new `modules/` | 1.5 |
| 4 | owner name from settings: the to-do tag, names in prompts; rename `templates/CLAUDE.md` → `agent-instructions.md` (keeping the two sections added 10-26) and the names in it; move `mailbox.md` to `templates/prompts/`. The wording in hubbus is changed together with the Agent Hub session at an agreed time | `gen-todo.py`, `librarian.sh`, `consolidate-memory.py`, `reflect_llm.py`, `trajectory.py`, `templates/`, `sync-local-setup.sh` (the line reading the template) | 0.5 |
| 5 | `bin/hub` + `init` (including installing the scheduler's and watchdog's plists) + `doctor` | new `bin/hub` | 1 |
| 6 | first clear `schedule.yaml` to 0 entries; instance directory `config/` + `data/` + `secrets/` (3.1): `hubconf.config()`/`data()`, scripts that read and write switch to them; `commit-hub-files.sh` commits only the config repo, scripts writing data no longer commit; `index.md` split into roster + append-only notes; the design documents in `.state/` moved to `docs/design/`; secrets through `hubconf.secret()`; **backup**: add the new paths to include → **run `backup.py restore` once into a temporary directory; it counts only when `config/` and `data/` are actually restored** (not waiting for the monthly backup-verify) → switch exclude to the new paths (confirming along the way that `backup-staging` is not backed up twice) → `check-secrets`' `covers()` in the same commit as the `.secrets` move; external readers (the three paths in Muninn's config.yaml) changed in the same step or a symlink left behind; **move during a window with no jobs running** (avoiding 02:00–04:00 librarian, 05:45 backup), or stop the scheduler first and kickstart it after the move; the owner's data moved over; add the 3.6 empty-instance acceptance test | nearly every script that writes output, `backup.py`/`hub.json`'s `backup`, the 8 scripts that read secrets | 2.5–3 |
| **7 (r3)** | **Carefully rewrite the platform repo's history before open source** (chosen by the owner on 10-26, see section 7, question 8), only after step 6 is done and the tree no longer holds user data: ① first make a complete `--mirror` backup (left untouched; restore from it if anything goes wrong) ② run `git filter-repo` on a fresh mirror clone with an explicit path list (`digest/`, `reflect/`, `index.md`, `todo.md`, `review-log.md`, `projects/`, `hub.json`, `schedule.yaml`, `mirror-list.txt`, `services.tsv`, `legacy-paths.tsv`, `openclaw-migration-status.md`, the user prompts in `agent-jobs/`, whatever of `.state/` got into version control by mistake) ③ scan the content again (personal names, accounts, token-like strings; the same rules as the ratchet + a secret-scanning tool), deciding hit by hit whether to rewrite or delete ④ acceptance: the rewritten HEAD's tree is exactly the pre-rewrite HEAD minus those paths, all tests pass ⑤ keep filter-repo's commit map (so old commit ids cited in docs and memory notes can be looked up) ⑥ before switching, stop the scheduler and notify every session; the hub machine's `~/agent-hub`, the laptop's copy, mirror-backup and every worktree are all re-cloned; the force push is confirmed by the owner personally | the platform repo's history (no code changes) | 1 |

Why this order: 0 and 1 touch nobody else's files and are useful at once; 2–4 are what new users really run into; 6
has the largest scope and needs the owner to decide how data is laid out, so it comes last, and since every earlier
step has already routed paths through hubconf, 6 is only changing hubconf's defaults plus moving files.

## 5. Boundaries with other sessions

- **Runtime abstraction** (`runtime_*.py`, `llm.py`, `hub-event`) and **leases** (`lease-*`, `policy.py`, channel
  leasing contract v2, `lease-show-file.md`) are being done by another session; none of this document's steps touch
  those files.
- **(r2) The Agent Hub session's files are not touched by this document either**: `bin/hubbus` (dispatch /
  reconciliation / fork merging still on trial; the hints for agents are changed by it), `bin/py`,
  `bin/setup-python`, `bin/test-gate`, `githooks/`, `tests/test_python_floor.py`, `bin/upgrade-idea.py`, `registry.py`'s
  `validate_enforce`.
- **(r2)** `scheduler.py`, `render-services.py` and `gen-status-page.py` are not being worked on by anyone, so this
  document can take them; render-services was last changed by the sandbox design session, so ask it before touching it.
- **(r2)** Renaming `templates/CLAUDE.md` belongs to this document (step 4); the runtime side no longer does it.
- The structure of `hub.json` (adding `hosts`, `modules`) is in steps 2 and 3; other sessions read it live, so fields
  are only added, existing fields keep their meaning, per the rule "adding fields needs no version bump". Step 0 does
  not touch `hub.json`.

## 6. Out of scope

- Linux / systemd (launchd only marked as the boundary).
- A second real agent runtime (the runtime-abstraction document already says not to).
- Multiple users on one machine, graphical installation.
- Making Muninn itself a general personal assistant: this document only makes "no assistant" work; the assistant
  module's interface is the contracts Muninn defines.

## 7. For the owner to confirm

1. The overall direction: instance directory + modules (reusing the registry/`after:`, adding `before:`) + machine
   roles + `bin/hub` + the ratchet test.
2. The order of steps, and that step 0 can go first (it is itself the "split the librarian" discussed in the previous
   round).
3. ~~How instance data is laid out~~ **(r3)** settled with the owner on 10-26: config as code, config in a private
   config repo; data not in git, relying on backup; "what was done when" from the bus and the records themselves
   (3.0).
> **Decided by the owner on 10-26: 6 agreed; 7 chose (a), three more status pages; 8 chose (a), carefully rewrite the
> history (how: step 7 in section 4).**
> **Decided by the owner on 10-26 (question 9): (a) the config repo stays a local git repo with no remote (relying on
> the nightly backup); (b) the time of the move and (c) the assistant's settings as proposed: first confirm the first
> nightly flow after the split at 10-27 03:00 (librarian + module jobs) is fine, then pick a window avoiding
> 02:00–04:00 and 05:45, stop everything briefly and move; for the three paths in Muninn's config.yaml, send a letter on
> the day of the move asking the muninn session to switch them at the same time.**

6. **(r3) Does step 1.5 (config pipeline) come before modules (step 3)?** (agreed by the Agent Hub session)
   Steps 2, 3 and 6 all change the **structure** of config: `hub.json` gains `hosts` and `modules`,
   `modules/*/module.yaml` appears, and finally everything moves into `config/`. Without a pipeline, the cost of a
   mistake is "you only find out when something breaks":
   - **Example A (a typo)**: enabling a module, `hub.json` gets `"modules": {"assitant": true}`. Today's hubconf does
     not validate `hub.json`, so the key is simply ignored: the assistant module is not on, knas-tasks and bridge are
     not scheduled. No error message at all; days later it turns out the assistant has received no new owner
     tasks. With `config check`: blocked at commit, "`modules.assitant`: no such module (there are: assistant,
     alerts…)".
   - **Example B (impact not visible)**: `modules.assistant` changed from false to true. With `config plan`, before
     committing you see:
     ```
     scheduler   + assistant/knas-tasks       03:30 (after agent-hub/librarian-core, before agent-hub/todo)
     scheduler   + assistant/bridge           every 15 minutes
     services    + local.agent-hub.lease-runner   (127.0.0.1:18797)
     ```
     Without plan, all you can do is commit and wait 15 minutes to look at the status page.
   - **Example C (happens today)**: editing `projects/<project>.yaml` by hand with a wrong path in `run:`. Today it is
     only found by `registry.py` at the next tick, the status page turns red and the last copy stays in use; with
     `config-check` in pre-commit it never gets into a commit.
   Conversely, doing modules first and the pipeline later, step 3's changes to the scheduler and adding `before:` could
   only be verified by "waiting for the next tick to see the result".
9. **(Step 6) For the owner to decide before the move**: (a) whether the config repo gets a remote: a new private
   GitHub repo (`gh repo create`, external) or local git only; (b) the time of the move: everything must stop briefly
   (the scheduler, lease-runner, cap-runner, mailbox dispatch and the status page all hold the paths), not around
   librarian 02:00–04:00 or backup 05:45; (c) the three paths in Muninn's config.yaml (todo, reflect, runner token) are
   changed by the muninn session (by letter), switched in the same window.
7. **(r3) Once digest and todo are out of git, where does the owner read them?**
   - **How it is read today**: the status page (a URL on the tailnet) has a single page, `index.html`: job success
     and failure, 🔔 reports, when things run next; **no** digest, todo or weekly review. These three can today only be
     read by opening the files in the repo (in a session, or opening `reflect/index.html` after pulling on the laptop;
     the owner does not read them on GitHub).
   - **Once out of git**: these three exist only in `data/` on the hub machine, and the laptop can no longer pull
     them.
   - **Options**:
     - (a) **Three more status pages**: `/digest/` (the last few days, markdown converted to HTML), `/todo`,
       `/reflect/` (today's `reflect/index.html` as is). Example: in the morning open the status page on the phone → the
       "today's digest" tab at the top → see the "## <project> …" the librarian wrote last night; the "to-do" tab is
       today's `todo.md`, with the owner's items at the top. gen-status-page regenerates on every tick anyway, so
       three more pages cost very little; reachable only on the tailnet, as today.
     - (b) **Only through Muninn**: Muninn already reads `todo.md` and `reflect/` (by path) and could show them in its
       PWA; it does not read the digest today.
     - (c) **Not provided**: SSH into the hub machine to read them.
   - I propose (a): no dependency on one more project, and new users without Muninn can see them too.
8. **(r3) If the platform repo goes public, what about the past git history?** Moving files does not make history
   disappear: the digest, todo, STATE.md quotes, registry and `hub.json` are all in past commits. Two approaches: (a)
   rewrite history (`git filter-repo` removing those paths; every clone has to be re-pulled); (b) start open source from
   a new repo (the first commit being the then-clean tree), keeping this repo as the owner's own private history.
   I propose (b): less likely to miss something, and it does not affect the current mirror and clones.
4. Whether the decision records in comments (the owner's name + "decided") should all be rewritten before open
   source, or the ratchet clearing them slowly is enough.
5. ~~Whether `contracts/vendor/muninn/` counts as platform or assistant module~~ **(r2)** settled with the Agent Hub
   session: it belongs to the assistant module (see 3.2).

## 8. Discussion record

**10-26 the Agent Hub session reviewed r1** (direction agreed: instance directory + modules reusing jobs/after +
roles + bin/hub + ratchet). Adopted:
- Boundaries now include its files (section 5); the hints for agents in hubbus are changed by it, coordinated with step
  4's name changes.
- `before:` needs cycle detection, and pointers to modules that are off count as nonexistent, with tests.
- The assistant module includes lease-runner, idea sandboxes, show_file, vendor/muninn and the lease v2 contract; the
  code stays in the platform repo.
- The sandbox module's scope; validation of the `enforce` field stays in the core; mailbox dispatch is core.
- `agent-jobs/mailbox.md` is the platform's prompt and stays out of the instance.
- The platform's Python and the bus are "one per machine" and stay out of the instance.
- The watchdog's and scheduler's plists do not go through render-services, they are installed by `bin/hub init`; the
  watchdog keeps the system python3.
- Add the "an empty instance runs" acceptance test (3.6), more direct than the ratchet.
- The ratchet's scope adds `tests/` and `agent-jobs/`.
- Before the move, `schedule.yaml` is cleared to 0 entries first.
- Renaming `templates/CLAUDE.md` belongs to step 4 of this document.

**10-26 the Agent Hub session re-reviewed r3** (1.5 before modules, agreed). Adopted:
- `legacy-paths.tsv` is data: records, not config; the bus is backed up (CLAUDE.md's statement is outdated).
- The 7 kinds of things in `.state/` that cannot be rebuilt, or are expensive to rebuild, listed explicitly in the
  backup; the design documents in the wrong place, the `.bak` files.
- External readers read by path: Muninn's config.yaml (I found a third one separately: `lease_runner_token_file`),
  contracts-check, the owner reading reports on the laptop.
- History that only git has (todo, index notes) becomes coarser: accepted deliberately; index-notes becomes
  append-only.
- Appliers written the same way as runtime_ports: registry + mechanical check + three contract tests; the scheduler
  job list's apply is a no-op; sync-local-setup takes a scope argument, and AGENT.md alignment is split into an applier
  with an external action.
- `bin/config-check` standalone, not a branch of test-gate; validates the staging area; post-commit covers config too.
- Backup: after adding include, verify by actually restoring; switch exclude paths; `covers()` in the same commit as
  the secrets move; secrets through `hubconf.secret()` (added to the ratchet); move during a window with no jobs.
- The git history before open source (section 7, question 8).
