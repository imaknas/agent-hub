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
- **Old names are reserved.** No project may be registered as `agent-hub`; registry validation refuses it. Otherwise a
  new project could take letters addressed to the old name.
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
  `hubconf.former_names`/`current_name` consult the table first, so the hub's rename needs no state and no migration
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
| env vars | `AGENT_HUB_*`, `AGENTHUB_*` | `BRUNNA_*` | services, units, tests, MCP settings (`AGENTHUB_PROJECT`), registry `run:` lines, shell scripts | `names.env(X)` and its shell twin read either; given both with different values, the command refuses and says which (no silent precedence). **Variables the platform exports** (`EVENTS`, `JOB`, `JOB_CHAIN`, `JOB_DEPTH`, the paths it hands child processes) are exported in **both** spellings with the same value until v0.9: project command jobs, sandboxed ones included, read them by their documented names (event-jobs.md), and that is a contract (AGENTS.md: both versions side by side until readers switch). The projects are told by letter. Tests and `cap-runner.py` strip both prefixes |
| unit labels | `local.agent-hub-scheduler`, `…-watchdog`, `local.agent-hub-mailbox-dispatch`, `local.agent-hub.<service>` (incl. `lease-runner`, `cap-runner`), `local.agent-hub-run-now.*` | `local.brunna-…` | launchd / systemd user units | the core installer is pairwise: install `current`, then unload and remove the *other* spelling's unit when it is this hub's (`ours()`), holding the scheduler's lock (it waits up to a tick's length, else fails and says rerun). render-services removes a relabelled service *before* adding its new label, so a fixed port (lease-runner's) never clashes |
| rules-file markers | `<!-- agent-hub:begin/end -->` | `<!-- brunna:begin/end -->` | every project's rules file | sync-local-setup, uninstall and prompts read both; each project's next session start rewrites the block with `current` |
| git-hook marker | `# agent-hub:gate` | `# brunna:gate` | every repo's managed hooks | render-project-settings recognises both as its own and rewrites with `current` at the next apply |
| leak check | `~/agent-hub`, `.agent-hub` | both pairs | `sync-local-setup.sh` | checks both |
| the platform's project name | `agent-hub` | `brunna` | `projects/agent-hub.yaml` (in the repo), `hub_project()`, bus addresses, scheduler job names `agent-hub/<job>` (14 jobs, 3 services), module `before:`/`after:`/`legacy_name`, Muninn's letters | `hub_project()` = `current`; `former_names` includes the pair constant. Everywhere a project's name is matched, the mailbox matches `own_names`: inbox, the dispatch queries, `apply-updates`, `records()`. `valid_address` accepts former names and `send_letter` routes them to the current one. The scheduler adopts every state suffix (`name`, `.last_ok`, `.attempts`, cursors) from former names, and resolves `after:`/`before:` through them. The test runner finds the hub's entry in the tree by `type`, not by name, so the commit that renames the yaml passes the gate |
| Principal Handoff party ids | `agent-hub:registrar`, `agent-hub:owner-side` | `brunna:registrar`, `brunna:owner-side` | records on the wire, Muninn's register | a class of its own in `names.py`, not derived from `hub_project()`. Today's `party()` builds ids from `hub_project()`, so without that they would flip by themselves at v0.7. The same holds for the requester id `project:agent-hub` and the records' `project` field. Every exact match accepts either spelling, forever (history): the registrar's `bound()` and hubbus's `need["by"] == party("registrar")`. New records carry `current`. **The switch waits for Muninn**: it must accept a party-id change for the same registrar first. Agent-hub asks by letter with v0.6, covering party ids, the requester id, the project field and the optional rekey. Until Muninn confirms, this class's `current` stays old |
| per-repo paths | `.git/agent-hub/` (outbox, `inbox.json`, cache, test output, tier cache), `test-*.jsonl`, `refs/agent-hub/*`, the `agent-hub-op.lock` dir, the `agent-hub-deps-pruned` and `agent-hub-migrated-to-mini` markers | `.git/brunna/…`, `refs/brunna/*`, `brunna-op.lock`, … | every project's repo and worktrees readers take both. A migration op in `project-git-job.py` runs as each project, sandboxed when the project is:
- it walks the common git dir and every `.git/worktrees/*`;
- it moves each item, and **merges** into a `brunna` directory that already exists (a send under v0.7 may have created one);
- it renames the refs;
- it deletes the tool caches instead of moving them, since they are rebuilt anyway;
- it is idempotent and atomic per item.

Before it moves anything, the project's settings are rendered for v0.7, and v0.6's sandbox profiles already allow both cache paths, so a running session never loses its writable cache.

Locks: during the window, writers take **both** lock directories in a fixed order, each with its own stale-lock break.

Outbox files moved between dispatch's read and its delete can be sent twice. The bus dedups on the file's id (`INSERT OR IGNORE`), and a test pins that |
| per-machine paths | the venv `~/.local/share/agent-hub/python`, `~/git-origin/agent-hub.git` (the checkout's origin), this machine's `bus_dir` setting | `~/.local/share/brunna/python`, `~/git-origin/brunna.git`, a new `bus_dir` | the machine | the venv: `setup-python` builds the new one and `bin/py` falls back to the old until removed. The origin: the bare repo is renamed and the checkout's remote URL updated. The bus is moved only when `hub.json` names an `agent-hub` path: dispatch is stopped, the database copied with SQLite's backup API, the setting rewritten, dispatch restarted. The code default `.state/bus` is unaffected |
| config file | `hub.json` | `brunna.json` | the config repo | **not version-fixed**: `hubconf.config_path()` resolves to whichever file exists. Every reader, writer and "is this instance set up" check uses it: `schedulectl set-model`, `agenthub` init/llm/agent, `connect.py`, the callers of `commit-hub-files.sh`, `hub_json_get`. A rolled-back v0.6 therefore keeps writing the file v0.7 renamed. Both existing and different: refused. The migration `git mv`s it and commits. The schema `contracts/hub.v1.schema.json` is a contract: per the contract rules, `brunna.v1.schema.json` is added beside it with the same content and both stay until every reader (config-check, tests, goldens) has moved |
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
   - scheduler adoption;
   - the test runner finding the hub by `type`;
   - the env rule (refuse on conflict, never export both);
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
     5. the platform's own `run:` lines;
     6. the MCP re-registration offer (terminal only).

     `upgrade` runs all of it from its step list. On the owner's machine code arrives by `merge-branch` → `land`, and
     there `land` runs only steps 1, 2 and 4, the ones that must follow the code at once. It prints that steps 3, 5 and
     6 wait for `brunna migrate`, and doctor keeps saying so until they have run.
   - `upgrade.py` refuses `--to` anything older than v0.6.0, and its go-back advice names v0.6.0.
   - The migration steps stay in every later release's step list, so a jump from v0.5 or v0.6 straight to a later
     version works.
3. **Party ids switch** (in v0.7.0 if Muninn has confirmed by then, otherwise a v0.7.x).
4. **Outside the repo**, in the owner's hands or by letter:
   - rename the GitHub repo, then update the description and social preview;
   - tell Muninn about its README link and its `handoff-version` example;
   - tell the other projects that mention agent-hub: the Principal Handoff spec repo and the owner's site.
5. **v0.9.0: old spellings no longer accepted.** Upgrading to v0.9 refuses while doctor lists any project or path
   not yet migrated, and names the command to run (`brunna migrate`). The migration code itself still recognises old
   spellings, so a late project can always be migrated.
   - Dropped: the `agenthub` forwarder and permission rules, the old env vars, the old markers, the old labels, the old
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
- the task is still bound, under its relinked key;
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
- **A project nobody opens.** Its per-repo paths and markers migrate at its next session start; until then readers
  take both. `brunna migrate --projects` does them all at once. `doctor` lists projects still on old paths.
- **Search and links.** GitHub redirects; the README says "formerly agent-hub".

## Open

- The formal trademark search, before any commercial use (the owner's).
- Muninn's answer on party-id changes.
