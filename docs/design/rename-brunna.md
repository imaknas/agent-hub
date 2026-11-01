# Renaming agent-hub to brunna

Status: design, 11-02. Revised twice after cold reviews. On 11-02 the owner widened the scope from "the names users see"
to every name the platform writes, as a test of how well the platform migrates itself. Nothing built.

The owner decided the rename on 11-02, and the new name is **brunna**: from Old Norse *brunnr*, a well. Every
project's notes run into one well, and it is tended each night.

## Why

"agent-hub" and "agenthub" are crowded:

- A live US trademark, AGENTHUB, is registered in class 42 (Reg. 7707945).
- Andrej Karpathy's AgentHub (March 2026), an agent-coordination hub built on git and a message board, owns the search
  results.
- At least one local hub, NicolasHubner/agenthub, lets Claude Code, Codex and Cursor talk to each other, which overlaps
  directly with the mailbox.
- Several unrelated repos are named agent-hub.

brunna was screened on 11-02:

- PyPI, npm, Homebrew and crates.io have no package by that name.
- brunna.dev is unregistered; brunna.com is parked for sale.
- No software product uses the name; web results are a person's name and a small Swedish town.
- No trademark appeared in a web search. A formal search (USPTO, EUIPO, TIPO) has not been done, and before any
  commercial use that is the owner's call.

## Scope

Every name the platform writes that carries the product's name changes. There are four exceptions.

- **History is never rewritten.** This covers:
  - bus events (an append-only log);
  - git history;
  - Principal Handoff records already sent;
  - published documents (the Zenodo record);
  - digests and review logs;
  - **task keys** already raised (`task-agent-hub-…`): opaque ids in STATE.md lines and Muninn's register, filed by
    the registrar under the name they were raised with (`Ledger.filed`). New keys are `task-brunna-…` because the key
    takes the current project name. Rekeying open tasks would need the protocol's `replaced_by` flow and Muninn's
    agreement; it is offered to Muninn in the same letter as the party ids, and only done if it agrees. Nothing
    depends on it.

  History stays readable through the name pairs below, which never expire for reading.
- **`hub.json`** becomes `brunna.json`. The internal module names `hubconf`, `hubbus`, `hub-event` and `hub-run` say
  "hub", not "agent-hub", so they are out of scope.
- **Both names are reserved** from v0.6 on: no project may be registered as `agent-hub` or `brunna` (the hub's own
  entry passes by `type: platform`). Otherwise a project could take letters addressed to the old name, or collide with
  the hub at the switch. If a user project named `brunna` already exists, the migration refuses and says how to rename
  it.
- **Reading keeps working.** A name pair stops being *written* in its old spelling at the switch. The old spelling of
  every reader-facing pair (CLI, env vars, markers) stops being *accepted* only after the deprecation window.

## The mechanism: one table of name pairs, in code

`bin/names.py` (standard library only), with a shell twin in `bin/hubpaths.sh` that reads the same table through
`bin/py`, holds every pair as data: class, old spelling, new spelling. The shell twin has a fallback copy for the
scripts that run before the platform's Python exists: `install.sh` and `bin/py` itself.

- **Writers** use `names.current(cls)`. It is fixed by the code's version, never by a state file. A release before the
  switch writes the old spelling; the switch release writes the new one. So no ordering between "the code moved" and "a
  migration ran" can make a writer and a reader disagree.
- **Readers** use `names.all(cls)`: both spellings, in every release from v0.6 on, in both directions. A v0.6 that is
  rolled back to still reads what v0.7 wrote.
- **The hub's own former name is a constant in this table**, not a `.state/project-renames.json` entry.
  `hubconf.former_names` and hubbus's `current_name` (both read `project-renames.json` today) consult the table
  first, so the hub's rename needs no state and no migration
  step to take effect. Other projects' renames keep using the state file as today.
- **Copies that can't import it.** Three places can't import `names.py`:
  - `install.sh`, which runs from `curl | sh` before the repo exists;
  - the shell twin's fallback, used on a remote machine without the platform's Python, and by `bin/py` and
    `setup-python`;
  - the scheduler watchdog, which runs on the system python3 and imports no platform module.

  Each carries an inline copy of the pairs it uses.
- **A test (`tests/test_name_pairs.py`) fails if code outside `names.py` spells either side of a pair as a literal**, with
  fixtures exempt. It also **compares each inline copy with `names.py`**, so a copy can't drift. This ratchet is how
  "every name" stays true as the code changes.
- **Old spellings stay migration input forever.** The migration code (`bin/migrate_names.py`) recognises every old
  spelling in every release, even after v0.9 stops accepting them in normal use. It needs that to migrate a project
  nobody opened, or an instance that jumps several releases at once.

## The pairs

| Class | Old | New | Where | How readers take both / the migration |
|---|---|---|---|---|
| product name in text | agent-hub | brunna | docs, messages, catalog, logo, README ("formerly agent-hub" for a while) | text only |
| CLI | `agenthub` | `brunna` | PATH wrapper, rules files, permission rules, prompts | both commands exist (one forwards); `install-cli` writes both wrappers; `upgrade.py` finds a wrapper by either name and either mark; the permission rules (`policy.platform_cli`, the runtime's allow rules) allow both spellings until the deprecation window ends |
| env vars | `AGENT_HUB_*`, `AGENTHUB_*` | `BRUNNA_*` | services, units, tests, MCP settings (`AGENTHUB_PROJECT`), registry `run:` lines, shell scripts | `names.env(X)` and its shell twin read either; given both with different values, the command refuses and says which (no silent precedence). **Variables the platform exports** (`EVENTS`, `JOB`, `JOB_CHAIN`, `JOB_DEPTH`, the paths it hands child processes) are exported in **both** spellings with the same value until v0.9: project command jobs, sandboxed ones included, read them by their documented names (event-jobs.md), and that is a contract (AGENTS.md: both versions side by side until readers switch). The projects are told by letter. Code that overrides a variable for a child process (`test-runner.py`, `agenthub` doctor, `config-check`, the lint tier's `run:` line) uses `names.env_set()`, which sets every spelling, so an override never conflicts with an inherited spelling. The ratchet requires it. Tests and `cap-runner.py` strip both prefixes |
| unit labels | `local.agent-hub-scheduler`, `…-watchdog`, `local.agent-hub-mailbox-dispatch`, `local.agent-hub.<service>` (incl. `lease-runner`, `cap-runner`), `local.agent-hub-run-now.*` | `local.brunna-…` | launchd / systemd user units | the core installer is pairwise: install `current`, then unload and remove the *other* spelling's unit when it is this hub's (`ours()`), holding the scheduler's lock (it waits up to a tick's length, else fails and says rerun). render-services removes a relabelled service *before* adding its new label, so a fixed port (lease-runner's) never clashes |
| rules-file markers | `<!-- agent-hub:begin/end -->` | `<!-- brunna:begin/end -->` | every project's rules file | sync-local-setup, uninstall and prompts read both; each project's next session start rewrites the block with `current` |
| git-hook marker | `# agent-hub:gate` | `# brunna:gate` | every repo's managed hooks | render-project-settings recognises both as its own and rewrites with `current` at the next apply |
| leak check | `~/agent-hub`, `.agent-hub` | both pairs | `sync-local-setup.sh` | checks both |
| the platform's project name | `agent-hub` | `brunna` | `projects/agent-hub.yaml` (in the repo), `hub_project()`, bus addresses, scheduler job names `agent-hub/<job>` (14 jobs, 3 services), module `before:`/`after:`/`legacy_name`, Muninn's letters | `hub_project()` = `current`. For the hub, `former_names`, `own_names` and hubbus's `current_name` use the table **in both directions** (`names.all` minus current) in every release from v0.6, so a v0.6 reached by rollback still sees letters and records filed under `brunna`. Everywhere a project's name is matched, the mailbox matches `own_names`: inbox, the dispatch queries, `apply-updates`, `records()`. `valid_address` accepts former names and `send_letter` routes them to the current one. The scheduler adopts every state suffix (`name`, `.last_ok`, `.attempts`, cursors) and `scheduler-failing.json` entries from former names **newest-wins**, as `adopt_cursor` does, then drops the other key. A background librarian finishing after the switch, or a rollback to v0.6, then can't run a daily job twice. It also resolves `after:`/`before:` through former names. Modules' `legacy_name: agent-hub/…` values are past state keys and are left as they are. Event jobs match on `own_names`, and chain loop detection compares through the pairs. The hub's entry gets `type: platform`, added to the validator and the project contract in v0.6, and every `stem == hub_project()` check and the test runner find it by that type. So the commit that renames the yaml passes the gate |
| Principal Handoff party ids | `agent-hub:registrar`, `agent-hub:owner-side` | `brunna:registrar`, `brunna:owner-side` | records on the wire, Muninn's register | a class of its own in `names.py`, not derived from `hub_project()`. Today's `party()` builds ids from `hub_project()`, so without that they would flip by themselves at v0.7. The same holds for the requester id `project:agent-hub` and the records' `project` field. Every exact match accepts either spelling, forever (history): the registrar's `bound()` and hubbus's `need["by"] == party("registrar")`. New records carry `current`. **The switch waits for Muninn**: it must accept a party-id change for the same registrar first. Agent-hub asks by letter with v0.6, covering party ids, the requester id, the project field and the optional rekey. Until Muninn confirms, this class's `current` stays old |
| per-repo paths | `.git/agent-hub/` (outbox, `inbox.json`, cache, test output, tier cache), `test-*.jsonl`, `refs/agent-hub/*`, the `agent-hub-op.lock` dir, the `agent-hub-deps-pruned` and `agent-hub-migrated-to-mini` markers | `.git/brunna/…`, `refs/brunna/*`, `brunna-op.lock`, … | every project's repo and worktrees readers take both. A migration op in `project-git-job.py` runs as each project, sandboxed when the project is:
- it walks the common git dir and every `.git/worktrees/*`;
- it moves each item, and **merges** into a `brunna` directory that already exists (a send under v0.7 may have created one);
- it renames the refs;
- it deletes the tool caches instead of moving them, since they are rebuilt anyway;
- it is idempotent and atomic per item.

Before it moves anything, the project's settings are rendered for v0.7, and v0.6's sandbox profiles already allow both cache paths, so a running session never loses its writable cache.

Locks are transient and are **never moved**: moving a held lock would let its holder's release remove another writer's lock. During the window, writers take **both** lock directories in a fixed order, each with its own stale-lock break.

Outbox files moved between dispatch's read and its delete can be sent twice. The bus dedups on the file's id (`INSERT OR IGNORE`), and a test pins that |
| per-machine paths | the venv `~/.local/share/agent-hub/python`, `~/git-origin/agent-hub.git` (the checkout's origin), this machine's `bus_dir` setting | `~/.local/share/brunna/python`, `~/git-origin/brunna.git`, a new `bus_dir` | the machine | the venv: `setup-python` builds the new one, and `bin/py` falls back to the old. **The old venv is never removed during an upgrade run**: the moving release's `upgrade.py` execs the target's steps with its own `sys.executable`. So from v0.6, `--steps` first re-execs itself through the target's own `bin/py`. `brunna migrate --cleanup` removes the old venv after doctor confirms nothing runs from it; v0.9's doctor reminds. The origin: the bare repo is renamed and the checkout's remote URL updated. The bus moves only when the config names an `agent-hub` path. That is most installs, since `hub.example.json` sets one. Many processes write the bus:
- short-lived ones (hooks, `send`, merge-branch) read `bus_dir` when they start;
- long-lived ones read it once at import (`hubconf.BUS_DIR`, hubbus's `DB`) and keep writing the old path until they
  restart, running whatever code they started with, v0.5 included: an open session's MCP server, a running
  librarian, the bridge.

So the move:

1. holds the scheduler lock and stops dispatch and lease-runner;
2. copies the old database with SQLite's backup API to `bus.db.tmp` in the new directory, then renames it into place.
   The copy keeps the old `meta.generation`, so event-job cursors stay valid;
3. **then** rewrites the setting, so every new writer goes to the new path;
4. sweeps the old database once at once. Whatever was written there between the copy and the rewrite is caught by
   this sweep;
5. **leaves the old database in place**; nothing deletes it.

After that:
- **The old database is never unlinked.** hubbus opens a connection per call, so an old process holds no fd between
  calls. `lsof` would see nothing, and its next `send` would create a fresh file at the old path.
- Every scheduler tick, from v0.6 on and through v0.9, sweeps any database at the old path into the new one. The
  sweep copies `events` rows verbatim with `INSERT OR IGNORE` on the id. It never goes through `publish()`, which
  would restamp the time and the tick's chain. The migration code keeps that sweep forever.
- At v0.9, after one last sweep, the old file is renamed to a tombstone and the old directory is made read-only. A
  stale writer then fails loudly instead of writing into a file nobody reads.
- From v0.6, `db()` and every other user of the bus path (the scheduler's `first_sight`, `hubconf.BUS_DIR`'s callers)
  resolve `bus_dir` on every call, so v0.6+ processes follow the move at their next call.
- The sandbox policy denies reads of both bus paths for good.
- The new `bus_dir` may never be `.state/bus`. `hubconf.bus_db()` in old code treats that as an old location and would
  move the new bus away.

The migration test keeps an MCP server open across the move and checks that its sends arrive. The code default `.state/bus` is unaffected |
| config file | `hub.json` | `brunna.json` | the config repo | **not version-fixed**: `hubconf.config_path()` resolves to whichever file exists. Every reader, writer and "is this instance set up" check uses it: `schedulectl set-model`, `agenthub` init/llm/agent, `connect.py`, the callers of `commit-hub-files.sh`, `hub_json_get`. A rolled-back v0.6 therefore keeps writing the file v0.7 renamed. Both existing and different: refused. **Neither existing, in an instance that has a `.hub-home`: refused outright** (by a check in the commands, not at
import, so `upgrade --steps` and `migrate` can always start), never "use the defaults": defaults would silently point the bus at a new empty `.state/bus`, and render-services would tear down services. That holds in every release from v0.6 on, and in v0.9, where an old spelling is no longer accepted in normal use, it names `brunna migrate`. The migration `git mv`s it and commits. The schema `contracts/hub.v1.schema.json` is a contract: per the contract rules, `brunna.v1.schema.json` is added beside it with the same content and both stay until every reader (config-check, tests, goldens) has moved |
| defaults for new installs | `~/agent-hub`, `~/.agent-hub`, repo URL | `~/brunna`, `~/.brunna`, `imaknas/brunna` | install.sh | install.sh notices an existing `~/agent-hub` install and resumes or upgrades it instead of cloning a second one. An existing checkout and instance folder keep their paths (they are the user's choice of place, recorded in `.hub-home` and the units); `brunna move-checkout` and `move-instance` move them on request |
| hard-coded names in code | `parse("agent-hub", …)` in `gen-todo.py`, `weekly-reflect.py`, `evals/e3.py`, registry `run:` lines and MCP settings in users' config | through `hub_project()` / `names` | code and the user's config | the ratchet catches the code; the migration rewrites `run:` lines in the platform's own registry entry, and doctor lists users' registry entries and MCP settings that still use old names |
| MCP server name | `agenthub` | `brunna` | users' agent settings | the upgrade offers re-registration on a terminal yes; uninstall removes both |
| repo | `imaknas/agent-hub` | `imaknas/brunna` | GitHub | renamed by the owner; GitHub redirects |

## Releases

1. **v0.6.0: both spellings everywhere, `current` = old for every class.**
   This release changes nothing visible. It is the release a rollback lands on, so it must read anything v0.7 or later
   writes:
   - `names.py` and its shell twin, the ratchet test;
   - every reader on `names.all`;
   - the pairwise unit installer;
   - the mailbox on `own_names`;
   - scheduler adoption at run time, through `names.all`, not `former_names`, and kept in every release (the hub's
     former name is kept for good). v0.6's `hub_project()` is still `agent-hub` and can't know `brunna` as a former
     name, so this is how a rollback still finds v0.7's keys and a jump still finds v0.5's;
   - the old-bus sweep (below), so a rollback to v0.6 keeps sweeping;
   - render-services pairwise like the core installer: on a rollback it removes `local.brunna.<service>` before it
     adds the old label, so a fixed port doesn't clash;
   - the test runner finding the hub by `type`;
   - the env rule (refuse a conflict on input, `env_set` for overrides);
   - both lock directories;
   - `bin/brunna`;
   - permission rules for both commands;
   - the reserved name (the hub's own entry, still `projects/agent-hub.yaml` here, passes by `type`);
   - `config_path()` everywhere;
   - exporting both env spellings;
   - the watchdog's inline copy;
   - the migration ops, built but not run.

   The letter to Muninn asking it to accept a party-id change goes out with this release.
2. **v0.7.0: the switch.**
   - `current` = new for every class, except party ids if Muninn hasn't confirmed.
   - Text everywhere, install.sh defaults.
   - The migration is `brunna migrate`. Each step is idempotent and resumable, and `doctor` verifies its result. A
     step that fails stops the rest and says how to rerun it:
     1. units (through `init --scheduler-only`'s pairwise installer);
     2. config file;
     3. per-machine paths (venv, origin, bus);
     4. **every** project's per-repo paths and markers, eagerly (not waiting for a session start);
     5. the MCP re-registration offer (terminal only).

     The platform's own registry entry, with its `run:` lines, is in the repo. It changes in the v0.7 commit itself,
     not at migrate time, because rewriting a tracked file would dirty the checkout.

     `upgrade` runs all of it from its step list. `migrate` is the **first** in-step command, before `registry.py
     check` and `config apply`.
     - From v0.6 on, the mover keeps the scheduler lock across the exec and through the steps, so no tick runs new code
       against an unmigrated instance:
       - the fd is made inheritable before `execv`;
       - its number goes in `BRUNNA_SCHEDULER_LOCK_FD`;
       - `run_steps` passes it with `pass_fds`;
       - every lock-taker (the core unit installer, the bus move, the scheduler itself) reuses that fd when the variable
         is set, instead of opening the lock file again. Opening it again would conflict with the held lock and
         deadlock;
       - every intermediate process passes the fd on, and a lock-taker checks with `fstat` that the fd is the lock
         file's, refusing otherwise;
       - the mover touches the scheduler's heartbeat while it holds the lock, so a long `setup-python` and per-repo
         pass don't trip the watchdog.
     - A v0.5 mover can't: it closes the lock right after the checkout move, then runs the target's `setup-python`
       (minutes) before migrate. For that window there is a **`.state/names-migrated` marker**:
       - it records the release that wrote it, and is written once step 4 (every project) has completed. Steps 3 and
         5 can wait and don't gate ticking. `land` writes it after its three steps, and a fresh v0.7+ install writes
         it at init;
       - the gate compares the release, so a marker left by v0.7 before a rollback to v0.6 doesn't let v0.9 run before
         its own migrate. v0.6's steps also remove it;
       - from v0.7 on, the scheduler's tick, dispatch and the hooks do nothing on an instance that has no marker, and
         say so;
       - `upgrade --steps`, `migrate` and doctor are exempt, and no check happens at import.

       The migration test covers the window.
     - The unit installer takes the "other" labels from `migrate_names`, which keeps the old spellings forever. So
       even v0.9 removes a v0.5 scheduler's units on a jump.
     - Sessions already open keep old code in memory. Upgrade and doctor say to restart them, since their mailbox tools
       may fail until then.

     On the owner's machine, code arrives by `merge-branch` → `land`. There, `land` runs only steps 1, 2 and 4, the
     ones that must follow the code at once. It prints that steps 3 and 5 wait for `brunna migrate`, and doctor keeps
     saying so until they have run.
   - `upgrade.py` refuses `--to` anything older than v0.6.0, and its go-back advice names v0.6.0.
   - The migration steps stay in every later release's step list, so a jump from v0.5 or v0.6 straight to a later
     version works.
3. **Party ids switch** (in v0.7.0 if Muninn has confirmed by then, otherwise a v0.7.x).
4. **Outside the repo**, in the owner's hands or by letter:
   - rename the GitHub repo, then update the description and social preview;
   - tell Muninn about its README link and its `handoff-version` example;
   - tell the other projects that mention agent-hub: the Principal Handoff spec repo and the owner's site.
5. **v0.9.0: old spellings no longer accepted in normal use.** Its upgrade step list runs `migrate` first, as every
   release since v0.7 does. This also works for a jump from v0.5, whose `upgrade.py` moves the checkout and then runs
   the target's steps, and stops with what to rerun only if a step fails. The migration code itself always recognises
   old spellings, so a late project can be migrated at any time.
   - Dropped: the `agenthub` permission rules (the `~/.local/bin/agenthub` wrapper stays, execing `bin/brunna`, until
     doctor sees no MCP registration naming it: those change only on a terminal yes), the old env vars, the old markers, the old labels, the old
     lock directory, `hub.json` as a name, the venv fallback.
   - Kept for good: reading history (party ids and the hub's former name in records and letters) and the reserved
     name.

## Proving it: the migration test

A test builds a **v0.5 instance** in a fake HOME and upgrades it. The instance is what `install.sh` + `init` + a
connected project + a sandboxed project produce at tag v0.5.0, and it holds:

- open letters to `agent-hub`;
- a task raised by the registrar with a key in a STATE.md line;
- scheduler state from today;
- a running fake scheduler unit;
- a lease service on a port.

It is upgraded along two paths:

- v0.5 → v0.6 → v0.7 → v0.9, one release at a time;
- v0.5 → v0.9 in one jump.

After each path, the result is compared with a fresh v0.9 install plus the same data:

- no daily job ran twice;
- every open letter reached the hub;
- the task is still bound, under its original key;
- exactly one scheduler unit is loaded;
- no file or path in the old spelling remains outside history, except the checkout and instance folders the user
  placed (listed in the test);
- `doctor` is clean.

The test also checks a rollback, v0.7 → v0.6, with the same checks against v0.6. Where a step needs launchd or
systemd, stand-ins are used (no real unit is touched), as in the uninstall tests.

The owner's machine is the last and real run, after the test passes: `brunna upgrade`, then `doctor`, then a night of
jobs checked on the status page.

## Risks

- **Two schedulers, or none.** The pairwise installer and the version-fixed `current` mean that at any moment exactly
  one spelling is written, and the other's unit is removed under the lock. Checked by the migration test's rollback.
- **Muninn.** Party ids and task keys touch Muninn's register. Both changes wait for its confirmation, and the relink
  uses an operation the protocol already defines.
- **A project nobody opens.** `migrate` does every project's per-repo paths and markers eagerly. One it couldn't
  reach (its folder missing) is listed by doctor and migrates whenever it is run again.
- **Search and links.** GitHub redirects; the README says "formerly agent-hub".

## Open

- The formal trademark search, before any commercial use (the owner's).
- Muninn's answer on party-id changes.
