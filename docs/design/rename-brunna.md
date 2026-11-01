# Renaming agent-hub to brunna

Status: design, 11-02. Nothing built. Decided by the owner on 11-02: the name changes, and the new name is **brunna**
(from Old Norse *brunnr*, a well: every project's notes run into one well that is tended each night).

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

Three kinds of occurrence, handled differently:

1. **Text** (docs, messages, comments, catalog entries, the logo): replaced. No compatibility needed.
2. **Names the platform writes onto the user's machine or into other systems**: the new name is written from the
   switch on, and the old one is still **read** for a stated period. These are listed in the table below.
3. **Recorded history**: never rewritten. This covers bus events, Principal Handoff records, task keys such as
   `task-agent-hub-…` in STATE.md lines, git history and digests. Old names in it stay valid forever, as opaque
   identifiers or through an alias.

| Name | Where it lives | New | Compatibility |
|---|---|---|---|
| CLI `agenthub` | users' PATH (`~/.local/bin/agenthub`), rules files, docs, habits, other agents' prompts | `brunna` | `bin/agenthub` stays as a forwarder (as `bin/hub` does today) for two minor releases; `install-cli` writes both wrappers until then |
| repo `imaknas/agent-hub` | clones, install.sh URL, README links, Muninn's README | `imaknas/brunna` | GitHub redirects the old URL and old clones keep fetching; install.sh's default `AGENT_HUB_REPO` changes |
| default checkout folder `~/agent-hub` | install.sh | `~/brunna` for new installs | an existing checkout is never moved by the rename; moving it is a separate, optional `brunna move-checkout` (hooks and units already follow a move through `config apply`) |
| default instance folder `~/.agent-hub` | `.hub-home` points to it | `~/.brunna` for new installs | existing instances keep their folder: `.hub-home` names it explicitly, so nothing moves |
| env vars `AGENT_HUB_*` (ROOT, HOME, CONFIG, STATE, DATA, SECRETS, LANG_UI, LANG_CONTENT, PYTHON, EVENTS, JOB, JOB_CHAIN…) | services, units, tests, users' shells, child processes | `BRUNNA_*` | one helper in hubconf reads `BRUNNA_X`, then `AGENT_HUB_X`; the platform sets both for child processes until the old names are dropped |
| launchd labels / systemd units `local.agent-hub-scheduler`, `…-watchdog`, `local.agent-hub.<service>` | `~/Library/LaunchAgents`, `~/.config/systemd/user` | `local.brunna-scheduler`, `local.brunna-scheduler-watchdog`, `local.brunna.<service>` | an upgrade step unloads and removes the old unit only once the new one is loaded and is this hub's (the same ownership check uninstall uses); render-services' record maps old labels to new; the watchdog and doctor look for both until the step has run |
| rules-file markers `<!-- agent-hub:begin/end -->` | every project's `CLAUDE.local.md` (and other runtimes' rules files) | `<!-- brunna:begin/end -->` | sync-local-setup and uninstall recognise both; the first sync after the switch rewrites the block with the new markers (the content is regenerated anyway) |
| hook commands `…/agent-hub/bin/hub-event` | projects' `.claude/settings.local.json`, user-level worktree hooks | the checkout's real path | `runtime_claude.MANAGED` matches the platform's hooks by `/bin/hub-event`; its `/agent-hub/bin/` fallback becomes "this hub's bin" (hubconf), so a renamed checkout is still recognised |
| MCP server name `agenthub` | users' `~/.claude.json`, `~/.codex/config.toml` | `brunna` | the upgrade offers to re-register under the new name (a terminal yes, as for registration); `agenthub mcp` and `brunna mcp` both start the same server; uninstall removes both names |
| the platform's own project name `agent-hub` | `projects/agent-hub.yaml`, `hubconf.hub_project()`, bus addresses (`--to agent-hub`), memory bare repo `agent-hub-memory.git`, Muninn's letters, the agent rules | `brunna` | the registry gains `aliases: [agent-hub]`. hubbus resolves a letter to an alias as one to the project, and keeps the address it was sent to. `rename-project.sh` moves the memory repo and copies with history. Records already on the bus keep `agent-hub` |
| task keys `task-agent-hub-…` | STATE.md lines, Muninn's task register | new keys as the key generator makes them | old keys are opaque and never rewritten |
| `hub.json` | the instance's config | unchanged | it is the file's name, not the product's |
| GitHub description, social preview, logo wordmark | GitHub, `assets/logo/` | new wordmark and preview | none |

Internal Python names (`hubconf`, `hubbus`, `hub-event`, `hub-run`) don't carry the product name and stay as they are.
A later cleanup may rename them, but the rename doesn't depend on it.

## Steps

Each step is its own branch, with the full suite green, landed by `merge-branch`. Nothing user-visible changes until
step 3.

1. **Compatibility layer, no visible change.** These all read both names, with tests pinning each one:
   - the env-var helper, and every read of `AGENT_HUB_*` moved onto it (a ratchet test forbids new direct reads);
   - both rules-file markers in sync-local-setup and uninstall;
   - `MANAGED` matching this hub's bin;
   - both MCP names in uninstall;
   - registry `aliases` and hubbus address resolution;
   - both unit labels in the watchdog, doctor and uninstall.
2. **The new names, written in parallel.**
   - `bin/brunna` becomes the CLI and `bin/agenthub` the forwarder.
   - `install-cli` writes both wrappers.
   - The platform sets `BRUNNA_*` beside `AGENT_HUB_*` for child processes.
   - Still no visible change.
3. **The switch** (a minor release, v0.5.0, with `BREAKING CHANGE:` notes even though old names keep working). Text
   changes everywhere:
   - README, docs, catalog, messages;
   - the logo wordmark and social preview;
   - install.sh defaults (repo URL, `~/brunna`, `~/.brunna`).

   Two more pieces in the same release:
   - **New labels and markers.** New installs get the new labels, markers and MCP name.
   - **`brunna upgrade` migration step**, run once, idempotent. It:
     - loads the new units, then unloads and removes the old ones;
     - rewrites markers at each project's next sync;
     - offers MCP re-registration at the terminal;
     - renames the platform's own project through `rename-project.sh`, which adds the alias.
4. **Outside the repo**, in the owner's hands or by letter:
   - rename the GitHub repo, then update the description and social preview;
   - write to Muninn: its README link, and the `handoff-version` example that names agent-hub;
   - write to the other projects that mention agent-hub (the Principal Handoff spec repo, the owner's site);
   - the Zenodo record of Principal Handoff names agent-hub only as an implementation, and that stays historical.
5. **Dropping the old names**, two minor releases later (v0.7.0):
   - the `agenthub` forwarder, `AGENT_HUB_*` reads, the old markers, the old-label checks.
   - Not dropped: the registry alias, task keys or history.

   The CHANGELOG of step 3 announces this.

## This machine

The owner's instance migrates by `brunna upgrade` like any other.

- The checkout stays at `~/agent-hub` unless the owner chooses to move it.
- `.hub-home` keeps pointing at `~/.agent-hub`.
- The scheduler's units are swapped by the step above.
- The old `agenthub` keeps working in every open session until the next session start, which loads rules naming
  `brunna`.

Muninn's lease-runner client, the bus and the memory repos keep working throughout, through the aliases.

## Risks

- **A unit swap that leaves no scheduler.** Load the new unit first and remove the old one only after the new one
  runs. A failed load leaves the old unit in place and the step reports it.
- **Two schedulers.** Old and new labels must not both run ticks. The scheduler's lock already makes a second tick
  refuse, and the step removes the old unit in the same run.
- **A project session started before the switch** keeps old rules naming `agenthub` until its next start. The
  forwarder covers it.
- **Letters addressed to `agent-hub`** from Muninn or projects during and after the switch. The alias covers them.
  A test sends one to the alias and checks the hub receives it.
- **Search and links.** GitHub redirects the repo, and the README says "formerly agent-hub" for a while so people who
  search the old name find it.

## Open

- Whether to rename the internal `hub*` modules later (not needed for the rename).
- The formal trademark search, before any commercial use (the owner's).
