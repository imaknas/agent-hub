# Renaming agent-hub to brunna

Status: design, 11-02; revised after a cold review the same day. Nothing built. Decided by the owner on 11-02: the
name changes, and the new name is **brunna** (from Old Norse *brunnr*, a well: every project's notes run into one well
that is tended each night).

## Why

"agent-hub" and "agenthub" are crowded:

- A live US trademark, AGENTHUB, is registered in class 42 (Reg. 7707945).
- Andrej Karpathy's AgentHub (March 2026), an agent-coordination hub built on git and a message board, owns the
  search results.
- At least one local hub, NicolasHubner/agenthub, lets Claude Code, Codex and Cursor talk to each other, which overlaps
  directly with the mailbox.
- Several unrelated agent-hub repos.

The name is unsearchable and easy to confuse with those projects. The repo has been public for only a few days, so
renaming costs least now.

brunna was screened on 11-02:
- PyPI, npm, Homebrew and crates.io have no package by that name.
- brunna.dev is unregistered; brunna.com is parked for sale.
- No software product uses the name; web results are a person's name and a small Swedish town.
- No trademark appeared in a web search. A formal search (USPTO, EUIPO, TIPO) has not been done and is the owner's
  call before any commercial use.

## What changes, and what must keep working

Four kinds of occurrence, handled differently:

1. **Text** (docs, messages, comments, catalog entries, the logo): replaced. No compatibility needed.
2. **Names the user meets or types** (the CLI, env vars, unit labels, rules-file markers, the MCP name, the platform's
   project name, its job names): the new name is written from the switch on, and the old one is still **read** for a
   stated period. The table below lists them.
3. **Frozen identifiers**: names written into repos, protocols or per-machine files that nobody reads as the product's
   name. They **keep the old spelling for good**, like `hub.json`. Renaming them would cost a migration each and buy
   nothing. The second table lists them, so an implementer leaves them alone.
4. **Recorded history**: never rewritten. This covers bus events, Principal Handoff records, task keys such as
   `task-agent-hub-…` in STATE.md lines, git history and digests.

### Renamed, with the old name still read

| Name | Where it lives | New | Compatibility |
|---|---|---|---|
| CLI `agenthub` | users' PATH (`~/.local/bin/agenthub`), rules files, docs, habits, other agents' prompts | `brunna` | `bin/agenthub` stays as a forwarder (as `bin/hub` does today); `install-cli` writes both wrappers. `upgrade.py` finds the wrapper by either name and either `WRAPPER_MARK`, so an existing install gets `brunna` |
| repo `imaknas/agent-hub` | clones, install.sh URL, README links, Muninn's README | `imaknas/brunna` | GitHub redirects the old URL and old clones keep fetching; install.sh's default repo URL changes |
| default checkout folder `~/agent-hub` | install.sh | `~/brunna` for new installs | an existing checkout is never moved by the rename (hooks and units already follow a move through `config apply`) |
| default instance folder `~/.agent-hub` | `.hub-home` points to it | `~/.brunna` for new installs | existing instances keep their folder: `.hub-home` names it explicitly |
| env vars `AGENT_HUB_*` (ROOT, HOME, CONFIG, STATE, DATA, SECRETS, LANG_UI, LANG_CONTENT, PYTHON, EVENTS, JOB, JOB_CHAIN, PRIVATE_NAMES…) and `AGENTHUB_*` (`AGENTHUB_PROJECT`, which users set in their MCP settings; `AGENTHUB_TIER_CACHE` in registry `run:` lines; `AGENTHUB_CONNECT_ROOTS`) | services, units, tests, users' shells and MCP settings, child processes | `BRUNNA_*` | one helper in hubconf reads `BRUNNA_X`, then `AGENT_HUB_X` / `AGENTHUB_X`; every read moves onto it (a ratchet test forbids new direct reads); the platform sets both spellings for child processes |
| launchd labels / systemd units `local.agent-hub-scheduler`, `…-watchdog`, `local.agent-hub.<service>` | `~/Library/LaunchAgents`, `~/.config/systemd/user` | `local.brunna-scheduler`, `local.brunna-scheduler-watchdog`, `local.brunna.<service>` | the swap is done by `init --scheduler-only`, which upgrade already runs: it loads the new unit, then unloads and removes the old one, holding the scheduler's lock so a tick in progress isn't killed. It only touches an old unit that is this hub's, the same ownership check uninstall uses. Project services need nothing extra: render-services removes the labels it recorded and no longer wants, and adds the new ones. Every reader of the labels knows both until the old names are dropped: the watchdog's `diagnose()`, doctor, uninstall, the status page, `schedulectl`, `recover-service.sh` |
| rules-file markers `<!-- agent-hub:begin/end -->` | every project's `CLAUDE.local.md` (and other runtimes' rules files) | `<!-- brunna:begin/end -->` | sync-local-setup and uninstall recognise both; the first sync after the switch rewrites the block with the new markers |
| MCP server name `agenthub` | users' `~/.claude.json`, `~/.codex/config.toml` | `brunna` | the upgrade offers to re-register under the new name, which needs a terminal yes as registration does; `agenthub mcp` and `brunna mcp` start the same server; uninstall removes both names |
| the platform's own project name `agent-hub` | `projects/agent-hub.yaml` (in the repo), `hubconf.hub_project()`, bus addresses (`--to agent-hub`), Muninn's letters, the agent rules | `brunna` | in code: the constant changes and the yaml is `git mv`'d. The upgrade step records the rename the way every project rename is recorded: `.state/project-renames.json` gets `{"agent-hub": {"new": "brunna", "stage": "done"}}`. `hubconf.former_names` then carries it to the places that already read it: hubbus `own_names`/`now_named`, the registry, event-job cursors, the scheduler's cursor adoption. No `rename-project.sh`: it refuses the platform, and the platform has no memory repo to move. `~/git-origin/agent-hub.git`, the checkout's own origin, is not touched |
| scheduler job names `agent-hub/<job>` (17 jobs) | the scheduler's state (`name`, `.last_ok`, `.attempts`), users' and modules' `after:`/`before:` (`modules/consolidate.yaml`, `modules/assistant.yaml` and the owner's registry name `agent-hub/librarian`) | `brunna/<job>` | the scheduler adopts every state suffix of a job from its former names, so no daily job runs twice on upgrade day; `after:`/`before:` resolve through former names. Today an unknown dependency is skipped silently, which is how a missed name would go unnoticed. The two modules name `{hub}/librarian` through the hub's project name instead of the literal |

### Frozen: the old spelling stays

These are names written into repos, protocols and per-machine paths. Nobody reads them as the product's name, and
changing any of them would split readers from writers.

- **Principal Handoff party ids** `agent-hub:registrar` and `agent-hub:owner-side` (`hubconf.party`). They are
  protocol identities on the wire. The registrar counts a task as bound only when its `by` is the registrar's id, so a
  new id would orphan every existing task: relink, amend, reconcile and claim-done would stop. `hubconf.party` keeps
  the old prefix as a constant, separate from `hub_project()`. Muninn is told they don't change.
- **Per-repo names**:
  - `.git/agent-hub/` (outbox, `inbox.json`, cache, test output, tier cache);
  - the `test-*.jsonl` logs;
  - `refs/agent-hub/*`;
  - the `agent-hub-op.lock` directory (`with-lock.sh` and `project-git-job.py` must agree on it, or they stop locking
    each other out);
  - the `agent-hub-deps-pruned` marker.
- **Per-machine paths**:
  - the platform's venv `~/.local/share/agent-hub/python` (`bin/py`'s fallback: renaming it breaks every install);
  - the bus default `~/.local/state/agent-hub-bus`;
  - `~/git-origin/agent-hub.git`.
- **Exclude entries** such as `.agent-hub/` in the test runner's exclude list.
- **`hub.json`**, and the internal module names `hubconf`, `hubbus`, `hub-event`, `hub-run`.

A test lists these and fails if one is renamed by accident.

## Steps

Each step is its own branch, with the full suite green, landed by `merge-branch`.

1. **Compatibility layer (v0.6.0, no visible change).** Released on its own, so that any version a user might roll
   back to already reads both names. A rollback from the switch to v0.5 would otherwise put the old units back while
   the new ones still run: two schedulers.
   - The env-var helper; every read of `AGENT_HUB_*` and `AGENTHUB_*` moved onto it; the ratchet test.
   - Both rules-file markers in sync-local-setup and uninstall.
   - Both MCP names in uninstall.
   - Both unit labels in the watchdog, doctor, uninstall, the status page, `schedulectl` and `recover-service.sh`.
   - hubbus `valid_address` accepts former names and routes them with `now_named`. Today `send_letter` canonicalises
     only the recipient, so a letter to a renamed project is refused; that is already a bug for any project rename.
   - The scheduler: job state and `after:`/`before:` adopt through former names.
   - `hubconf.party` decoupled from `hub_project()`.
   - The frozen-names test.
   - `bin/brunna` added, with `bin/agenthub` as the forwarder; `install-cli` writes both; `upgrade.py` finds either
     wrapper.
   - Hard-coded `"agent-hub"` literals in `weekly-reflect.py`, `gen-todo.py` and `evals/e3.py` go through
     `hub_project()`.
   - `sync-local-setup.sh`'s leak check learns `brunna` and `.brunna`.
2. **The switch (v0.7.0, `BREAKING CHANGE:` notes, though old names keep working).**
   - Text everywhere: README ("formerly agent-hub" for a while), docs, catalog, messages, the logo wordmark, the
     social preview.
   - install.sh defaults: repo URL, `~/brunna`, `~/.brunna`.
   - `hub_project()` becomes `brunna` and the yaml is renamed.
   - `init --scheduler-only` swaps the units.
   - The migration step (idempotent, and kept in later releases' step lists, so a jump straight from v0.5 works):
     - writes the project-renames entry;
     - lets the unit swap run;
     - offers MCP re-registration at the terminal.

     Markers are rewritten at each project's next session start.
3. **Outside the repo**, in the owner's hands or by letter:
   - rename the GitHub repo, then update the description and social preview;
   - write to Muninn:
     - its README link;
     - its `handoff-version` example that names agent-hub;
     - that the party ids stay as they are;
     - that letters to `agent-hub` keep arriving;
   - write to the other projects that mention agent-hub (the Principal Handoff spec repo, the owner's site);
   - the Zenodo record of Principal Handoff names agent-hub only as an implementation, and that stays historical.
4. **Dropping the old names (v0.9.0), two minor releases after the switch.** The CHANGELOG of the switch announces it.
   - Dropped: the `agenthub` forwarder, the old env-var reads, the old markers, the old-label checks.
   - Kept for good: the former-name entry (it is how history and old letters resolve) and every frozen identifier.

## This machine

The owner's instance migrates by `brunna upgrade` like any other.

- The checkout stays at `~/agent-hub` unless the owner chooses to move it.
- `.hub-home` keeps pointing at `~/.agent-hub`.
- `init --scheduler-only` swaps the scheduler's units.
- The old `agenthub` keeps working in every open session until the next session start, which loads rules naming
  `brunna`.

Muninn's lease-runner client, the bus and the memory repos keep working throughout: through former names, and through
the frozen ids.

## Risks

- **A unit swap that leaves no scheduler, or two.** The new unit is loaded first, and the old one is removed in the
  same run, under the scheduler's lock. A failed load leaves the old unit in place and is reported. The scheduler's
  lock already makes a second tick refuse. A rollback to v0.6 reads both labels, so it doesn't leave two running.
- **Daily jobs running twice on upgrade day.** Prevented by adopting job state through former names. A test runs a
  tick before and after the rename on the same day and checks no job runs twice.
- **Letters addressed to `agent-hub`.** Covered by `valid_address` and `now_named`. A test sends one after the rename
  and checks the hub receives it.
- **The registrar's tasks.** Covered by the frozen party ids. A test checks that a task raised before the rename is
  still bound after it.
- **A project session started before the switch** keeps old rules naming `agenthub` until its next start. The
  forwarder covers it.
- **Search and links.** GitHub redirects the repo, and the README says "formerly agent-hub".

## Open

- Whether to rename the internal `hub*` modules later (not needed for the rename).
- The formal trademark search, before any commercial use (the owner's).
