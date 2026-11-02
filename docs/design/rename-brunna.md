# Renaming agent-hub to brunna

Status: design, 11-02. Nothing built yet. This is the fourth version: it was rewritten from scratch after seven
cold-review rounds of the earlier versions, then reviewed three more times; the last round found nothing blocking.

Decided by the owner on 11-02:

- The new name is **brunna**, from Old Norse *brunnr*, a well. Every project's notes run into one well, and it is
  tended each night.
- **Goal: everything a new user sees after installing is brunna.** On a machine that already runs agent-hub, whatever
  can't be changed reliably stays as it is.

## Why

"agent-hub" and "agenthub" are crowded:

- A live US trademark, AGENTHUB, is registered in class 42 (Reg. 7707945).
- Andrej Karpathy's AgentHub (March 2026), an agent-coordination hub, owns the search results.
- At least one local hub, NicolasHubner/agenthub, lets Claude Code, Codex and Cursor talk to each other, which overlaps
  directly with the mailbox.

brunna was screened on 11-02:

- PyPI, npm, Homebrew and crates.io have no package by that name.
- brunna.dev is unregistered; brunna.com is parked for sale.
- No software product uses the name.
- No trademark appeared in a web search. A formal search (USPTO, EUIPO, TIPO) is the owner's call before any
  commercial use.

## What the earlier versions taught

The earlier versions tried to migrate every name on existing machines. Seven review rounds kept finding blocking
problems in the same three places:

- moving the bus;
- jumping from v0.5, whose `upgrade.py` can't be changed;
- the window in which new code runs before a migration finishes.

Every one of them came from moving something that old, unchangeable processes still use. This version moves nothing
that a running process might be using.

## The rule

Every name the platform writes goes through **one table of name pairs** (`bin/names.py`): class, old spelling, new
spelling. Three ways to use it, chosen per class:

- **resolve**, for paths and files: use whichever spelling **exists**; when neither does, create the one this release
  writes (`current`: old in v0.6, new from v0.7). So no release creates a name the release before it can't read.
  - A fresh install therefore has only new spellings.
  - An existing instance keeps using what it has, so nothing moves under a running process.
  - Both spellings existing and differing is refused, never guessed.
- **both**, for names that are recognised: readers accept both spellings, writers write the new one.
  - Used where rewriting is safe: unit labels, rules-file markers, hook markers, bus addresses, job names.
  - Readers accept both in every release from v0.6 on. A rollback to v0.6 therefore still reads what v0.7 wrote.
- **gated**, for party ids: the switch waits for Muninn's agreement (see below).

Some places can't import `names.py`. Each carries an inline copy of the pairs it uses:

- `install.sh`, run from `curl | sh` before the repo exists;
- the fallback in `bin/hubpaths.sh`, for a remote machine without the platform's Python, `bin/py` and `setup-python`;
- the scheduler watchdog, which runs on the system python3.

A test (`tests/test_name_pairs.py`) does two things:
- it fails when code spells either side of a pair as a literal outside the table and its copies (fixtures exempt);
- it compares every copy with the table.

## The pairs

| Class | Old | New | How |
|---|---|---|---|
| product name in text | agent-hub | brunna | text: README ("formerly agent-hub" for a while), docs, messages, catalog, logo, social preview |
| CLI | `agenthub` | `brunna` | both: both commands exist and one forwards. `install-cli` writes `brunna` on a fresh install, and on an existing one also keeps `agenthub`. `upgrade.py` finds a wrapper by either name and either mark. Permission rules (`policy.platform_cli`, the runtime's allow rules) allow both |
| env vars | `AGENT_HUB_*`, `AGENTHUB_*` | `BRUNNA_*` | both, with a fixed precedence. The platform exports `BRUNNA_*`. The project-facing job variables (`EVENTS`, `JOB`, `JOB_CHAIN`, `JOB_DEPTH`) are exported in both spellings with the same value, because project code reads them by their documented names (event-jobs.md); they are never overridden. `names.env(X)` reads the **old spelling when it is set, else the new one**. Any override still written in the old spelling, in a shell line, a `run:` line or a user's script, is therefore an explicit override and wins over the inherited `BRUNNA_*`. A new user who overrides with `BRUNNA_X` has no old spelling set, so theirs is used. When both are set and differ, it logs which one it used and never refuses. The platform's own code overrides through `names.env_set()`, which writes the new spelling and **removes** the old ones (except the four project-facing job variables, which keep both), and clears through `names.env_unset()`, which removes every spelling. An old spelling in a child's environment is then always one that something outside the platform set, so "old wins" means "an explicit override wins". The ratchet flags any single-spelling assignment, pop, delete or filter in the repo: hubbus's `handler_env` strip, the test runner, the evals, `config-check`, and the hub's own lint tier, whose `run:` line uses `BRUNNA_*` from v0.7. Tests and `cap-runner.py` strip both prefixes |
| unit labels | `local.agent-hub-scheduler`, `…-watchdog`, `local.agent-hub-mailbox-dispatch`, `local.agent-hub.<service>` (incl. lease-runner, cap-runner), `local.agent-hub-run-now.*` | `local.brunna-…` | both, pairwise installer: install the new label, then remove the other spelling's unit when it is this hub's (`ours()`). render-services removes a relabelled service before adding the new label, so a fixed port doesn't clash. A rollback to v0.6 does the reverse with the same code. Two schedulers can't tick at once: the scheduler's lock is per checkout, whatever the label |
| rules-file markers | `<!-- agent-hub:begin/end -->` | `<!-- brunna:begin/end -->` | both: sync-local-setup, uninstall and prompts read both; each project's next session start rewrites its block in the new spelling. A project nobody opens keeps the old marker, which is still read |
| git-hook marker | `# agent-hub:gate` | `# brunna:gate` | both: render-project-settings recognises both as its own and rewrites at the next apply |
| leak check | `~/agent-hub`, `.agent-hub` | `~/brunna`, `.brunna` | `sync-local-setup.sh` checks both |
| the platform's project name | `agent-hub` | `brunna` | both: `hub_project()` returns `brunna`; the yaml is renamed in the commit; the entry gets `type: platform` (added to the validator and the project contract in v0.6), and the test runner and every `stem == hub_project()` check find it by type. For the hub, `former_names`, `own_names` and hubbus's `current_name` use the table in both directions (all spellings but the current one), from v0.6 on. That covers `valid_address`, `send_letter`, the inbox, the dispatch queries, `apply-updates`, `records()`, event-job matching, and every project-name field in registry entries (`consumes.from`, which the owner's registry uses for the hub; `trigger.projects`/`sources`), so letters to either name reach the hub in any release from v0.6. Both names are reserved for other projects from v0.6. The hub is identified only by `type: platform` in the repo's `projects/`, never by stem: `roster()`, `projects()`, `workspace()`, `registry.dirs`. If a user project named `brunna` exists, its name wins, and the hub keeps `agent-hub` as its current name, decided at run time; doctor says so. A v0.5→v0.7 jump therefore can't make a user's project vanish |
| scheduler job names | `agent-hub/<job>` | `brunna/<job>` | both: the scheduler adopts state (`name`, `.last_ok`, `.attempts`, cursors, `scheduler-failing.json` entries, and alert state keyed by job name, so an open alert still gets its recovery message) at run time from the other spelling, newest-wins as `adopt_cursor` does, then drops the other key. This is in every release from v0.6, so a daily job never runs twice, on upgrade, rollback or jump. `after:`/`before:` resolve through both. Modules' `legacy_name: agent-hub/…` values are past keys and stay |
| Principal Handoff party ids | `agent-hub:registrar`, `agent-hub:owner-side`, requester `project:agent-hub`, the records' `project` field | `brunna:…` | gated: a class of its own in the table, not derived from `hub_project()`. Readers accept both spellings for good (history), including `bound()` and hubbus's `need["by"]` check. Writers switch only after Muninn agrees, asked by letter with v0.6 |
| task keys | `task-agent-hub-…` | `task-brunna-…` | history: open keys stay (opaque ids, filed under the name they were raised with). New keys follow the project name |
| per-repo paths | `.git/agent-hub/` (outbox, `inbox.json`, cache, test output, tier cache, run logs), `refs/agent-hub/*`, the `agent-hub-deps-pruned` and `agent-hub-migrated-to-mini` markers | `.git/brunna/…`, … | resolve: a project connected after the switch gets `brunna`; an existing project keeps `agent-hub`. hubbus, dispatch, the test runner, policy (the sandbox's writable cache paths, at render time) and worktrees resolve per repo. These all persist, so "whichever exists" is stable. When neither exists, resolve creates the current spelling (old in v0.6, new from v0.7), so v0.6 leaves nothing v0.5 can't read. Both directories present: resolve takes the one holding an outbox or `inbox.json`. One holding only cache is a stray left by a session with old settings; doctor deletes it, since caches rebuild |
| per-repo lock | the `agent-hub-op.lock` directory | **none: fixed for good** | a mkdir lock that exists only while it is held, so "whichever exists" can't decide it, and old and new code must use the same name to exclude each other. It keeps its name in every release. It lives inside `.git` for seconds at a time, and is listed as an exception in the fresh-install test |
| platform venv | `~/.local/share/agent-hub/python` | `~/.local/share/brunna/python` | resolve: `bin/py` and `setup-python` use whichever exists; a fresh install builds the new one |
| bus directory | `hub.json` `bus_dir` = `~/.local/state/agent-hub-bus` | `~/.local/state/brunna-bus` | resolve: it is a setting. `hub.example.json` gives new installs the new path; existing instances keep theirs. Nothing moves |
| config file | `hub.json` | `brunna.json` | resolve: `hubconf.config_path()` everywhere (readers, writers, "is this instance set up" checks, `commit-hub-files.sh` callers, `hub_json_get`). `init` writes `brunna.json` on a fresh install. The schema gets `brunna.v1.schema.json` beside `hub.v1.schema.json` (same content, both kept per the contract rules) |
| checkout's origin | `~/git-origin/agent-hub.git` | the new default for a fresh install | resolve: the checkout's remote is whatever it is; nothing renames it |
| default folders | `~/agent-hub` (checkout), `~/.agent-hub` (instance) | `~/brunna`, `~/.brunna` | install.sh defaults for new installs; an existing `~/agent-hub` install is found and upgraded rather than cloned a second time |
| MCP server name | `agenthub` | `brunna` | both: a fresh init registers `brunna`. On an existing machine, upgrade offers to re-register (only on a terminal yes, so an unattended upgrade leaves the old registration, which keeps working through the wrapper). Uninstall removes both |
| repo | `imaknas/agent-hub` | `imaknas/brunna` | renamed by the owner on GitHub, which redirects old URLs |
| hard-coded literals | `gen-todo.py`, `weekly-reflect.py`, `evals/e3.py`, the platform's registry `run:` lines | through `hub_project()` / `names` | caught by the ratchet; the platform's own registry entry changes in the switch commit |

Out of scope: the internal module names `hubconf`, `hubbus`, `hub-event`, `hub-run`, which say "hub", not
"agent-hub". History is never rewritten: bus events, git history, records already sent, the Zenodo record, digests.

## On an existing machine after the switch

What changes: the CLI (both names work), unit labels, rules-file blocks (at each project's next session start), the
hub's project name and job names, the docs and messages, and (on a yes) the MCP registration.

What stays named agent-hub, on purpose:

- the checkout folder `~/agent-hub` and the instance folder `~/.agent-hub`;
- the bus directory;
- the platform's venv;
- `hub.json`;
- each existing project's `.git/agent-hub/`;
- the checkout's origin.

These are paths that running processes and old code use. They can be moved later, one at a time, with their own
commands if ever wanted, and none of that is part of this rename.

On the owner's machine, code arrives by `merge-branch` → `land`, not `upgrade`. Today `land` runs only `registry.py
check` and the hub's own settings. From v0.6 on, `land` also runs, after every merge:

- `install-cli`, which writes the `brunna` wrapper and keeps `agenthub`;
- `init --scheduler-only`, the pairwise core-unit installer: the scheduler and watchdog aren't installed by
  `config apply`;
- `config apply`, which re-renders the services pairwise.

Each is idempotent and leaves an unchanged thing alone, so an ordinary merge costs a few file comparisons. Because the
wrapper exists from v0.6, a rules block that names `brunna` (written from v0.7) always finds it.

The MCP re-registration is offered as `brunna init --register-mcp`.

## Releases

1. **v0.6.0: readers of both, no visible change.** This is the release a rollback lands on.
   - Everything in the table that says "both" or "resolve", on the read side. Writers keep the old spellings.
   - The pairwise unit installer and render-services.
   - Scheduler adoption.
   - The hub's names in both directions.
   - `type: platform`.
   - The reserved names.
   - `config_path()`.
   - The env rule and `env_set`.
   - The inline copies and the ratchet.
   - `bin/brunna`.
   - Permission rules for both commands.
   - `land` running `install-cli`, `init --scheduler-only` and `config apply`.

   The letter to Muninn about party ids goes with it.
2. **v0.7.0: the switch.** Writers use the new spelling: labels, markers, the project name, job names, the CLI, fresh
   installs' paths and files. Text everywhere, install.sh defaults, the new logo. Party ids too, if Muninn has agreed.
   `upgrade.py` refuses `--to` anything older than v0.6.0. When a jump came from older than v0.6, its go-back advice
   names v0.6.0, not the from-ref: v0.5 can't read job state already adopted under `brunna/…`.
3. **Outside the repo** (the owner, or by letter):
   - rename the GitHub repo, then update the description and social preview;
   - Muninn: its README link and its `handoff-version` example;
   - the Principal Handoff spec repo and the owner's site.
4. **Later, when wanted:** drop the old spellings from the *writers'* fallbacks. Readers keep accepting them as long as
   an instance may still carry them; that is cheap and safe.

## Proving it

A test installs **v0.5.0** into a fake HOME. The instance has:

- a connected project and a sandboxed one;
- open letters to `agent-hub`;
- a registrar task;
- today's scheduler state;
- stand-in units;
- a lease service on a port.

It then upgrades along three paths:

- v0.5 → v0.6 → v0.7, one release at a time;
- v0.5 → v0.7 in one jump;
- v0.7 → v0.6, a rollback.

After each path:

- no daily job runs twice;
- every open letter reaches the hub;
- the task is still bound;
- exactly one scheduler unit is loaded;
- the CLI works by both names;
- `doctor` is clean.

A second test does a **fresh v0.7 install** and checks the **instance, the units, the paths it created and the messages
it printed** (not the repo's source, which has to know the old spellings to read them). None of them may contain
`agent-hub` or `agenthub`, outside these exceptions:
- the per-repo lock while it is held;
- the four project-facing job variables, which every job's environment carries in both spellings until project code
  has moved.

State keyed by the hub's name: the tier cache (`{project}-{tier}`) resets on the switch, which is fine for a cache,
and the test checks it rebuilds. Agent-job state folders such as `mailbox-<project>/status.json` are the dispatcher's
one-at-a-time guard, so they are read through both names, like job state. That is the owner's
goal, as a test.

The owner's machine is the last, real run.

## To settle while building (from the last review, none blocking)

- **Unit swap.** `init --scheduler-only` takes `scheduler.lock` around the swap, waiting up to `TICK_LOCK_WAIT`, so
  `land` never boots out a scheduler mid-tick.
- **`AGENTHUB_TIER_CACHE`.** Project `run:` lines use it, so it is exported in both spellings from v0.6, like the four
  job variables.
- **`land`'s `install-cli`** finds the existing wrapper the way `upgrade.py` does, so it never leaves a second one.
- **`recover-service.sh` and `services.tsv`.** Template paths there resolve both spellings, and `services.tsv` joins
  the table. Done in v0.6 (11-02): a row is found by either label, its location may start with either name, a template
  filed under the other spelling is used, and the unit gets the label the row names (services.tsv is the owner's file:
  the platform never rewrites it). `consumes: from:` naming the platform in either spelling finds the `type: platform`
  entry.
- **The ratchet is defined per class and only for code.** Docs, locales and `CHANGELOG.md` name the product freely,
  and `hub.json` is a resolved file name.
- ~~No going back below v0.6 once a new spelling exists.~~ Not needed: resolve creates the current spelling, so v0.6
  creates only old ones (decided while building, 11-02).
- **The index keys the hub by `type: platform`**, never by stem. A user project named `brunna` keeps
  `projects/brunna.yaml` and its stem; the hub's entry is the one under the repo's `projects/`.
- **Run-now units** (`local.*-run-now.*`) are recognised in both spellings but never swept by the pairwise remover:
  that would kill a `schedulectl run` still going.

## Open

- Muninn's answer on party ids.
- The formal trademark search, before any commercial use (the owner's).
