# AGENTS.md

This file is for any coding agent (Claude Code, Codex, Cursor, Gemini CLI…) working in the brunna repo.
`CLAUDE.md` is a single line, `@AGENTS.md` (Claude Code reads this file through the reference); write nothing else in
CLAUDE.md.

**The facts of this machine** (which machine runs the hub, whether there is a remote, who the owner and the assistant
are, the names of external services, decisions made for this deployment) are in the instance directory's
`config/AGENTS.local.md` (the instance directory is where `<hub>/.hub-home` points, default `~/.brunna`;
`bin/hubconf.py dir config` prints the config directory). **Read it first if it exists.** This file holds only the
general rules and an index; on a conflict, this file wins for the general rules and that one wins for the facts of the
machine.

## What this is

brunna is a cross-project system for memory tracking + scheduling + a registry of projects and services. This file
gives any session working here a starting index; the details are in each script's header comment and are not repeated
here. Design documents are in `docs/design/`.

What a deployment looks like: **one machine runs the hub** (called "the hub machine" below: the scheduler, the
services, the memory bare repos and the projects' development copies are all on it; brunna.json `hosts.server` is its ssh
name, left empty when one machine does everything), optionally plus **a remote machine** (e.g. a laptop that uses the
coding agent's remote feature to open folders on the hub machine and runs only a few things itself; see
`bin/minilib.sh`).

## Tool neutrality (hard rule; design in docs/design/tool-agnostic.md)

brunna is meant for anyone, with any coding agent, any LLM, any key.

- **Work the platform starts and the platform's data (bus, registry, STATE.md, notes) are guaranteed by the platform,
  not by any tool's features.** What has to hold is done with the platform's own mechanisms: a judgment point has the
  model return an answer and the platform writes the file (completion); agent work that changes files runs in a
  throwaway worktree and the platform applies only the allowed paths. A tool's native features (permission settings,
  hooks) are only reinforcement: removing them does not affect correctness. A session the user opens themselves has only
  the tool's native protection; there the platform advises and does not guarantee.
- **Tool syntax lives only in adapters**: `runtime_<name>.py`, `provider_<name>.py`, `modules/<tool>.yaml`. Everywhere
  else the platform uses only neutral terms (the `runtime_ports` interfaces, paths, commands, tiers, event names).
- **When a capability is missing, turn the feature off explicitly and say why** (`runtime_ports.Unsupported`, doctor's
  feature table); never run on with empty data.
- Mechanical checks (the commit gate runs them): `tests/test_runtime_boundary.py` (Claude syntax and model names may
  only go down), `tests/test_tool_agnostic.py` (core judgment points must run with any provider; files that import the
  Claude adapter directly may only go down), `tests/test_runtime_none_flow.py` (with no coding agent, every place says
  "not supported").

## Rules for new code

- **Paths, names and the owner always come from `bin/hubconf.py`** (settings in `brunna.json`; the hub root is derived from
  the script's location, `BRUNNA_ROOT` overrides it). Never hard-code `~/brunna`, `~/services`, project names or
  the owner's name.
- Identifiers in protocols are English (the owner's hubbus address is `owner`; aliases are read from brunna.json); the
  user's identity (the owner's tag in to-do lines, `owner.tag`) is read from settings.
- Never put personal data in code, tests or public docs (`tests/test_personal_boundary.py` checks mechanically); what is
  specific to this deployment goes in the instance directory's `config/AGENTS.local.md`. **Not a single private project
  name may appear in the platform repo** (examples use generic names); the list is in the instance's
  `config/private-names.txt`, listed in brunna.json `tests.config_inputs`, and the commit gate passes it in for the check
  (a deployment that lists none skips it; a listed file that is missing fails the gate).
- **Python 3.13 minimum, always the platform's Python `bin/py`**: a uv-managed venv (3.13 + PyYAML; on a new machine run
  `bin/setup-python`, `--check` only verifies). A script's first line is
  `#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"` (macOS only); shell scripts, services and registry `run:`
  call `bin/py <script>`, Python starts another script with `hubconf.launch()` (= `sys.executable` + the script), and
  shell strings such as hooks use `hubconf.sh_py()`. Never hard-code an interpreter, never rely on `python3` on PATH,
  **and never rely on the first line** (it only works on macOS; Linux runs `bin/linux-test`, all tests in a docker
  container). Tests: `bin/py -m unittest discover -s tests`, or the parallel `bin/run-tests` (the gate uses it, about 1
  minute). `tests/test_python_floor.py` checks mechanically; the only deliberate exceptions are `scheduler-watchdog.py`
  (it must still send alerts when the platform's Python is broken: the system python3, standard library only) and
  `migrate-to-mini.py`, which runs on the remote machine

## Look here first, don't guess

- **Where the user's things live**: `hubconf.config_dir()` (brunna.json, registry, schedule.yaml…), `data_dir()` (digest,
  todo, review-log, reflect…), `secrets_dir()`; shell uses `bin/hubpaths.sh`. `<hub>/.hub-home` points to the
  instance directory (default `~/.brunna`: config is a local git repo, data, secrets); the hub root holds nothing of
  the user's. New code always goes through these and never builds paths itself (the ratchet blocks it)
- **The platform's CLI is `brunna`** (`bin/hub` is the old name and forwards to it). `brunna install-cli` puts it on
  PATH; project sessions use `brunna send/inbox/done/…`, `brunna merge-branch`, `brunna cap-run` (rules files name
  only these, never local paths); the same mailbox is also an MCP server, `brunna mcp` (`bin/mcp_server.py`, for any
  agent that supports MCP; how to register it: docs/mcp.md)
- **What commands the platform has**: `bin/brunna --help` (init, doctor, config, llm, modules, onboard, recover,
  rename, status, jobs, run); which provider's model makes the platform's judgments and where the key lives:
  `brunna llm setup` (init asks too); when something is broken, run `bin/brunna doctor` first. New machine:
  `bin/setup` → `brunna init`
- **Updating an install**: `brunna upgrade` (`bin/upgrade.py`: moves a checkout at a release or on main to the newest
  release / the remote's main, then runs the target's own in-step commands; the rule for which checkouts it refuses,
  the exit codes and the hand-over between versions are in its header). A development checkout (another branch, or
  commits not on the remote, like the author's main) is refused, never moved. `bin/upgrade.py --steps --from <ref>` is
  a contract between versions: an older version execs the new one's after moving the checkout; keep it working

- **A project's current state, which services it is bound to, what its STATE.md says**:
  `bin/overview.sh [project name]` (no argument lists all of them)
- **Which projects have been onboarded**: the project registry (`projects/<name>.yaml` in config; `paths.workspace` is
  its location on the hub machine; `bin/hubconf.py roster` lists one project per line with name/path/origin/memory bare
  repo, which is how shell scripts read it)
- **Which background services run on the hub machine**: `services.tsv` in config (a hand-written list, including ones
  the platform does not manage)
- **Which GitHub repos are mirrored as backup**: `mirror-list.txt` in config
- **What is still open across projects (and who can do it)**: `todo.md` in data (regenerated every night by the
  `brunna/todo` job after the librarian)
- **What happened in each project this week, working hours, how work with AI went**: `reflect/index.html` in data (the
  page with every week's analysis report, data in `reflect/YYYY-Www.json`; produced every Monday, reflect module)
- **Odd errors from automation/scheduling/watchdogs, or designing new automation**: check `known-issues.md` first
  (concrete pitfalls + operating principles)

## Create / recover

- **Start a new project, or connect an existing folder to this system**:
  `bin/onboard-project.sh [--public] [--no-remote] ~/workspace/<name>` (run on the hub machine): fully automatic (it
  does the git init, adds the first commit, creates the GitHub repo); details in its header comment and
  `.claude/skills/new-or-onboard-project/SKILL.md`
- **New machine, or a project's local folder is gone**: `bin/recover-project.sh <name>`
  — **never** re-run `onboard-project.sh` for a project that was already onboarded: it creates a brand-new memory cut
  off from the old history instead of recovering it. Details in `.claude/skills/recover-projects/SKILL.md`
- **Idea sandboxes** (exploration projects the assistant opens for an idea; registry `type: idea`,
  `~/workspace/idea-<n>`, no GitHub remote): the assistant opens one through lease-runner's `POST /v1/sandboxes`
  (→ `bin/create-idea-sandbox.py`); to upgrade one to a real project use
  `bin/upgrade-idea.py <idea-n> [--rename <new name>] [--public]` (dry-run first, then add `--apply`). Design in
  `docs/design/idea-sandboxes.md`
- **Renaming a project** (after the GitHub repo has been renamed):
  `bin/rename-project.sh <old> <new> [--path <new path>]` — registration, memory bare repo / `~/.memory-copies` copy /
  checkpoint / mirror and the local folder all change in one go, memory history kept. Dry-run first, then add `--apply`
- **Moving a project's development environment from the remote machine to the hub machine**:
  `bin/migrate-to-mini.py <name>`, run on the remote machine: preflight checks (nothing uncommitted/unpushed, no
  session open), recover into `~/workspace/<name>` on the hub machine, copy untracked state files, auto memory, the
  project's MCP settings, `.git/info/exclude`, rebuild dependencies. Dry-run first, then add `--apply`; the original
  folder is left alone
- **Scripts such as onboard/rename that touch the hub machine**: run on the hub machine they work locally; only run on
  the remote machine do they go through ssh (the shared logic is in `bin/minilib.sh`)
- **A background service needs rebuilding on a new machine**: `bin/recover-service.sh <label>`
  — only entries in `services.tsv` whose owner is brunna can be rebuilt fully automatically; for the others
  (Homebrew, GitHub Actions, things other tools manage) it only prints the rebuild steps and never guesses its way into
  touching someone else's things

## Project mailbox / event bus

- `bin/hubbus`: letters across projects (and to the owner). The recipient is a **project** (or `owner` = delivered to
  the owner side: the assistant, or the built-in owner side), not a session. It is an append-only event log (`brunna.json
  bus_dir`, default `~/.local/state/brunna-bus/bus.db`, one per machine, not in version control; the nightly
  encrypted backup takes it separately with the SQLite backup API), and the mailbox is a projection of it. Project
  sessions use `brunna send/inbox/done` (described in `templates/agent-instructions.md`); letters reach a session
  through hooks at session start, when the user sends a message and after a tool runs (runtimes with hooks), or are read
  with the MCP server's `inbox`. **A sandboxed project's mail never touches the bus** (it is in none of its sandboxes):
  its commands write into its own outbox (`<workspace>/.git/brunna/outbox/`) and read the view of its own letters
  (`.git/brunna/inbox.json`); `hubbus dispatch` sends each outbox file as that project (a sender named in the file
  is never read) and writes the views back (design `docs/design/bus-sender.md`). `hubbus-bridge` (every 15 minutes) brings the assistant's handoffs into the mailboxes
  and sends letters for the owner and done for handoffs to the assistant. **A request/question nobody picks up is
  handled by the platform** (`hubbus dispatch`, service `local.brunna-mailbox-dispatch`, every minute; design in
  `docs/design/mailbox-routing.md`): for a reply to a session's conversation, if that session is open its hook gets 5
  minutes first; if it is idle, the reply continues in the background with its context (a branched copy, the runtime
  must support it; a context too large is dispatched fresh instead). For a request, if someone is using the project it
  waits 5 minutes, then a live session of this project with activity in the last 24 hours handles it on a background
  branch (it can answer about what it has in hand); only when there is no such session is a headless agent dispatched
  (`templates/prompts/mailbox.md`: it only writes notes/replies/asks the owner, never changes code; with
  `mailbox.handler: propose` it runs in a throwaway worktree instead and the platform sends for it,
  `bin/propose_apply.py`). brunna's own letters work the same way. Anyone who can write the bus can start these
  handlers, so the background branch and the headless agent load only the platform's settings for the project, never
  its saved permissions (`settings: platform`, as event agent jobs; per point in mailbox-routing.md "11-02").
  When the branch is done, the original session receives the branch's reply and the letters it sent on its next
  activity. Only request/question is handled; info is a notice (one that replies to a live session wakes it, nothing more).
  A dispatched letter is recorded as "the platform is handling it", so an open session does not redo it; at most 4
  rounds per thread and 6 dispatches per project per day (brunna.json `mailbox`). Everything specific to Claude Code is
  only in `bin/runtime_claude.py`. Handoffs do not go through this.
  When a project's question to the owner is resolved inside the project session:
  `brunna send --to owner --type done|drop --question <id>`, and the owner side closes it; opening a session lists the
  questions still open for that project (❓), and every night after the librarian the `assistant/reconcile-questions`
  job (`modules/assistant.yaml`) asks about those open more than 3 days whether they are still wanted (questions from
  discuss-lease handoffs are the assistant's own to manage and are not asked about).
  The assistant's handoffs are read from the mailbox by the librarian every night (`hubbus open-handoffs`) and applied
  directly to STATE.md, with `hubbus done` only when the result is reliable (the bridge then reports the close to the
  assistant); a session that sees one during the day treats it as reference only; the owner's answers are handled by
  the session that asked, and unhandled ones are also applied at night. Interface and failure modes: `bin/hubbus`
  header
- **The owner to-do registrar** (`bin/registrar.py`, a core platform job, nightly after the librarian and before
  todo, zero LLM): sends the owner's to-do lines (state-todo contract) of every STATE.md to the owner side (the assistant, or the built-in
  `bin/owner.py`) as Principal Handoff Acts records (read in 0.3 to 0.5, sent in the version of brunna.json
  `principal_handoff.send_version`), and applies the closes it gets back by key

## Channel leases (the assistant lends its conversation to a project session; assistant module)

- `bin/lease-runner.py` (service `local.brunna.lease-runner`, 127.0.0.1:18797, token `runner-token` in the
  platform's secrets directory): the assistant opens leases, relays messages, receives events over SSE, and detaches;
  each lease is one long-running session of `llm.py exec lease-session` (read-only tools, with the phase-two deny list,
  `HUBBUS_LEASE=1`). Idle for 10 minutes the process stops; every 30 minutes it asks once (detached 10 minutes after
  the assistant reports warned). On detach the `lease-handover` judgment point writes a structured handover → bus,
  project mailbox, task/question/progress for the owner, memory note. API and events:
  `contracts/lease-runner.v2.schema.json`, handover: `contracts/lease-handover.v1.schema.json`; design in the header.
  Without a runtime that supports long-running streaming, opening a lease returns `runtime_unsupported`
- `bin/lease-relay.py`: the permission relay for lease sessions (MCP): questions and WebFetch to domains outside the
  registry go to the assistant (phone), everything else is refused; the domains in `brunna.json`
  `lease.no_session_domains` may never be "allowed for the whole lease"
- `bin/lease-resume <id>`: continue a lease in the terminal

## Called by hooks/other scripts: don't run these by hand

- `bin/git-safe-push.sh`: on a diverged push, pulls and retries automatically; only a real content conflict fails for a
  person to look at, and it never uses any strategy to guess which side to keep
- `bin/git-sync-memory.sh`: for the session-end / pre-compaction hooks; commits **only** `memory/` and `STATE.md` and
  never touches the project's real code. This hook must never be changed to `git add -A` in any project (it would
  commit the user's real work that is not ready yet along with it)
- `bin/with-lock.sh`: keeps several sessions from colliding on the same git repo
- `bin/gen-index-roster.sh`: mechanically regenerates the project roster block of `index.md` (between
  `roster:begin/end`) from the project registry; called on every librarian run and when `onboard-project.sh`
  registers. The roster is not left to LLM judgment; the LLM only writes notes outside the block
- `bin/sync-local-setup.sh`: brings a project's local-only setup (the `brunna:begin/end` block in the runtime's
  rules file — for Claude Code, `CLAUDE.local.md` —, `.agent-memory/AGENT.md` (it commits+pushes this one file), the
  hook settings, and the ignores for both) in line with the current `templates/`. Runs automatically at every project
  session start, and onboard/recover call it too: **after changing `templates/agent-instructions.md` there is no need to
  patch each project by hand**; it takes effect at the next session. It only touches untracked files; the ignores go in
  `.git/info/exclude`, never `.gitignore`
- `bin/memory-nudge.py`: reminds the agent to write notes when the memory notes are older than the project's latest
  commit: after git commit/push and at session start, including after compaction (called by sync-local-setup.sh).
  Prints nothing at all when the notes are not behind
- `bin/worktree-memory.sh`: a session in a git worktree (e.g. `<project>/.claude/worktrees/…` created by an agent in
  worktree mode) has no `.agent-memory`, rules file or project hooks; this script symlinks the main checkout's two of
  them in and handles syncing/reminders. It hangs off the **user-level** tool settings, installed with
  `merge-hook-settings.py --worktree <file>`; a new machine needs it installed once too. In the main checkout it does
  nothing
- `bin/merge-hook-settings.py`: merges (never overwrites) the hook settings in a project's
  `.claude/settings.local.json`; `onboard-project.sh` / `recover-project.sh` call it themselves, no need to run it by
  hand. The hooks are deliberately not in the shared `settings.json`: the commands contain the hub's local path, and
  committing them to the repo would publish this machine's paths

## Scheduling / nightly automation (runs on the hub machine)

- `bin/scheduler.py` + `schedule.yaml` + `bin/schedulectl`: the scheduler, triggered every 15 minutes; `schedulectl` is
  the CLI for viewing/adjusting the schedule (e.g. `schedulectl models` shows the model each judgment point uses,
  `schedulectl run <name>` runs one right now). **All schedules live only here**, including ones that need model
  judgment (don't use a desktop app's own scheduling: it only runs while the computer is awake and the app is open):
  registry jobs (`<project>/<job>`) or a command `bin/run-agent-job.sh <name> [working directory]`, with reports and
  state in `.state/agent-jobs/<name>/`
- **Event-triggered jobs**: a registry job with `trigger:` (instead of `at`/`every`) runs when matching bus events
  wait: `trigger: {events: [merge.done], projects?, sources?, where?, debounce?}` (the key is `trigger`, never `on`:
  YAML reads a bare `on` as true). Event types come from the hubbus contract's enum; a project's job sees only events
  addressed to it. Each job keeps a cursor in the scheduler's state (tied to the bus file's generation); a run gets
  the oldest waiting events in `BRUNNA_EVENTS` (agent jobs: appended to the prompt as data), and only a success
  moves the cursor. Every scheduled run gets `BRUNNA_JOB` and the causal chain (`BRUNNA_JOB_CHAIN` /
  `_DEPTH`), which hubbus stamps on what it publishes, so a job never triggers itself. Bus events are their writers'
  claims (any unsandboxed project can write the bus; a sandboxed one only its own merge events, through its outbox):
  the platform's event jobs are never agents, and a project's agent event job
  must declare `allow:` and runs under judgment point `agent-job-event`, which loads only the platform's settings for
  the project (so `allow:` is its whole boundary). `schedulectl events <job>` shows what waits and why, `schedulectl skip <job>` drops a batch
  that keeps failing. Logic in `bin/event_jobs.py`, design in `docs/design/event-jobs.md`
- **Long jobs get `background: true`**: the scheduler runs jobs one at a time in order, so a job that takes minutes or
  more (e.g. the librarian) without it makes everything after it wait. A background job's result is merged back into
  the state on the next tick; details in the `scheduler.py` header. When background jobs have an order between them
  (they touch the same thing), add `after: [job]`; don't rely on staggered times alone
- **The scheduler watchdog** `bin/scheduler-watchdog.py` (`local.brunna-scheduler-watchdog`, every 5 minutes):
  sends Discord when the scheduler has not ticked for more than 35 minutes. It deliberately depends on neither the
  scheduler nor the platform's Python; the Discord settings are in the secrets directory (not in version control), and
  when they are missing the status page shows red
- **The schedule status page** (service `local.status-page`, by default listening only on 127.0.0.1:18790; to view it
  from another machine, expose it yourself with a VPN / tunnel): `bin/gen-status-page.py` regenerates it every tick:
  which jobs failed, which reports need attention (🔔), when things run next, the full text of agent job reports
- `bin/librarian.py`: the "librarian". Reads what is new in each project's `memory/`, decides whether and how to update
  `STATE.md`, produces the cross-project `digest/YYYY-MM-DD.md` + updates `index.md`. Models: a judgment point declares
  how strong a model it needs (`model_group`); the actual model is computed only by `llm.model_for` (the runtime's
  group table → overridden by brunna.json `llm.points`; view with `schedulectl models`, change with `set-model`). Shadow
  mode: the completion version of the judgment points runs alongside, comparing only, never writing
  (`bin/librarian-shadow-report.py`). The mechanical steps after it are each their own platform job with
  `after: librarian` (`projects/brunna.yaml`): registrar, reconcile-questions, todo, memory-gaps,
  `librarian-publish` (roster + commits only digest/index/review-log/todo); the digest is written in sections
  (`hublib.py digest-append <section>`), each job replacing only its own section
- `bin/consolidate-memory.py`: "looking back" (consolidate module), nightly at 02:00 (before the librarian): folds
  memory date files older than 14 days into `reference/` and then git rm's them, corrects statements that were
  overturned (keeping "planned/possibly" wording, mechanically checked); at most 3 projects a night and each project at
  most once a week, judgment point `consolidate` (`model_group: strong`). `--list` shows the state,
  `--dry-run --project <name>` only produces the proposed diff. Changes are recorded in `review-log.md`; details in
  the header comment
- `bin/gen-todo.py`: platform job `brunna/todo` (nightly after the librarian and the registrar): collects the
  to-do section of every project's STATE.md (fixed format, `contracts/state-todo.v2.md`, maintained by the librarian;
  both token sets, zh-Hant and English, are defined once in `bin/todo_tokens.py`: readers take either, writers use the
  content language's) and mechanically produces `todo.md`, no LLM; projects with a malformed section are listed at the
  end of the file
- `bin/weekly-reflect.sh` (→ `weekly-reflect.py` + `reflect_llm.py`, page template `templates/reflect.html`; reflect
  module): every Monday at 07:00, an analysis of the previous week's cross-project activity. The statistics (messages/
  sessions/commits/notes per project, time of day, late-night share, longest continuous stretch of work, how work was
  delegated) use zero LLM; "themes and observations on working with AI" is one model call a week, **sending only the
  statistics, never conversation text to the cloud**; for a project whose registry `privacy.reflection` is not include,
  not even a summary is sent
- `bin/evals/`: evals for prompt and rule changes (docs/design/i18n.md). `e2.py` freezes judgment-point inputs (the
  librarian records each night's into `hubconf.evals_dir()`), runs prompt versions through `llm.py` and scores them;
  `e3.py` runs the project rules in a throwaway project. Runs use their own config copy and bus
- `bin/hublib.py`: shared logic: real human messages, transcript ↔ project mapping, which commits count as project
  work. check-memory-gaps / memory-nudge / weekly-reflect all use it
- `bin/backup.py`: **encrypted cloud backup** (backup module; restic, destination and retention in `brunna.json`
  `backup`). `backup/backup` nightly at 05:45: SQLite copied with the backup API, the memory bare repos bundled, each
  workspace's unpushed/uncommitted work and `.env`, plus `.state` / secrets / service data / transcripts; retention
  applied on Sundays (14 days, 8 weeks, 12 months). `backup/backup-verify` restores into a temporary directory and
  verifies on the 1st of every month. The password and keys are in the secrets directory (`backup-password`,
  `b2-env`; **keep another copy of the password somewhere else**: the copy inside the backup cannot unlock itself).
  Restore: `bin/backup.py restore <destination> [--include <path>]`
- `bin/mirror-backup.sh`: reads `mirror-list.txt` and keeps a read-only `--mirror` clone of each GitHub repo, purely
  for disaster recovery, entirely independent of the librarian's summary flow, zero LLM cost (mirror module)
- `bin/transcript-backup.sh`: hourly backup of conversation transcripts from the remote machine → the hub machine (this
  script runs on the remote machine; it is only needed for sessions opened **locally** on the remote machine, since a
  session opened on the hub machine over SSH already has its transcript on the hub machine)
- `bin/check-memory-gaps.py`: platform job `brunna/memory-gaps` (`--apply`, nightly after todo): projects with
  conversations in the last 72 hours but no new memory notes are listed in a fixed block of `todo.md` and in the digest
  (zero LLM cost)
- `bin/check-state-anchors.py`: platform job `brunna/state-anchors` (`--apply`, nightly after the librarian and
  todo): STATE.md statements marked with a code anchor (⚓ + a code span: `path` or `path#symbol`) whose path or name
  is gone from the project's main branch are listed in a fixed block of `todo.md` and in the digest; the librarian is
  told about them on its next run for that project (`bin/state_anchors.py`, design in `docs/design/state-anchors.md`;
  zero LLM cost)
- `bin/test-waiting-report.py`: platform job `brunna/test-waiting` (nightly at 04:45): where agent sessions waited on
  tests in the last 7 days, per project (transcripts through the port; the test runner's run logs, read as each
  project) → `test-waiting/<day>.json` in data and a status page section (docs/design/test-waiting.md B7; zero LLM)
- `bin/sync-all-memory.sh`: hourly, re-runs `git-sync-memory.sh` + the push for every project's `.agent-memory` (the
  safety net for the hooks; service `local.brunna.sync-all-memory`)

## Path conventions on the hub machine

Four look alike but serve entirely different purposes; don't mix them up (locations in brunna.json: `git_origin`,
`memory_copies`, `services_root`, `workspace_root`):

- `~/git-origin/<name>-memory.git`: a bare repo with only the `agent-memory` branch (created by `onboard-project.sh`)
- `~/.memory-copies/<name>/`: a **memory-only** copy for the librarian (a hidden directory, not for people to open
  sessions in) (only AGENT.md/STATE.md/memory/, not the project's real code; cloned by `onboard-project.sh`)
- `~/services/<name>/`: a full git clone of the **real code**, for projects that need a long-running process on the hub
  machine (e.g. a webhook receiver). The difference from `~/.memory-copies/`: this is what commands like `node`/`python`
  actually execute, with machine-local state such as `.env`/`data/`, not something the librarian reads memory from.
  **Every `~/services/` project must have its own `bin/deploy.sh`**, run from the development copy, deploying in one
  command (git pull + install dependencies/rebuild + restart the service if needed + health check). Not rsync, not
  `onboard-project.sh`, and not ssh-ing in to pull by hand
- `~/workspace/<name>/`: **the development copies of all projects** (including the `.agent-memory` worktree; this is
  the registry's `paths.workspace`); sessions are opened here. New projects are created here with `onboard-project.sh`
  and recovered with `recover-project.sh`; the memory remote points straight at the local bare repo. Don't run services
  here (that is `~/services/`), and don't use `~/.memory-copies/` as a development copy

Only a new project that needs a long-running service on the hub machine uses `~/services/`; a project that only needs
memory tracking and runs nothing long-lived (most projects) needs nothing under `~/services/`.

## Directories

- `bin/`: all the scripts
- `launchd/` (Linux: the same templates are turned into systemd user units by `bin/systemd_units.py`;
  `hubconf.service_manager()` decides which): only the scheduler and the watchdog are left (deliberately not through
  render-services: a broken scheduler is exactly what the watchdog has to report), plus the remote machine's transcript
  backup (`local.transcript-backup`). The status page, mailbox dispatch and memory sync are in the `services:` of
  `projects/brunna.yaml`, generated by render-services. The hub machine's ssh name is in `brunna.json` `hosts.server`
  (`hubconf.host("server")`; shell uses `hub_server` from `hubpaths.sh`, since the remote machine may not have the
  platform's Python).
  **Projects' long-running services are not here**: they are in the project registry's `services:`, and
  `bin/render-services.py` (platform job, every 15 minutes) generates the launchd plist / systemd unit and reloads only
  on change; when a command cannot be found it leaves the working one alone, and it alerts when a service is not
  running.
  **node (and claude) are managed only by nvm**: services and schedules use the nvm default version
  (`hubconf.nvm_bin()`, `$PLATFORM_PATH` in env); don't install node with Homebrew (the reason is in known-issues.md
  "Settings, installs, credentials").
  The claude module (`modules/claude.yaml`): `brunna/update-claude` (nightly, `bin/update-claude.py`) makes sure
  claude does not silently fall behind; `claude-login` (hourly, `bin/check-claude-login.py`) actually makes a request
  and checks expiry dates, alerting when it is broken or about to expire; the platform's `claude -p` (`llm.py`) uses the
  headless token by default, and only a job whose registry entry says `connectors: true` (it needs claude.ai
  connectors) uses the subscription login
- **The merge gate for parallel sessions** `bin/merge-branch` (run in the session's own worktree): rebase → the
  registry's `runtime.test` → fast-forward main (the main checkout) → push only when the registry says
  `push_on_merge: true`; the bus records `merge.done`/`merge.failed`/`session.report`. The status page lists each
  project's worktrees, two branches changing the same file, and the number of commits made directly on main without
  the gate (information only). `bin/prune-worktrees [--yes]` cleans up merged, clean worktrees (with their branches,
  **run by a person**). Separately, the platform job `brunna/prune-worktrees` (nightly at 04:30, `--auto`)
  automatically cleans up orphan worktrees that **no session has open** (with a session open it never touches them,
  even when idle): idle ≥ 2 days, it deletes only dependencies that can be rebuilt (the folder stays, and session start
  reminds to rebuild); only at ≥ 14 days does it remove the worktree (the branch stays); rules in the `bin/worktrees.py`
  header
- **config pipeline** (`docs/design/new-user.md` 3.7): config = `brunna.json`, the registry, `mirror-list.txt`,
  `services.tsv`, `schedule.yaml`. `brunna config check` (= `bin/config-check`, run by the config repo's pre-commit on
  the index and by brunna's `lint` tier; `brunna.json` against `contracts/hub.v1.schema.json`, unknown keys are rejected), `plan` (what would change,
  touches nothing), `apply` (check → apply → plan again must be empty → records `.state/config-applied.json` + bus
  `config.applied`; the status page title shows the config commit in effect). Appliers are registered in
  `bin/appliers.py` (the scheduled job list, render-services, render-project-settings), contract test
  `tests/test_appliers.py`; the two every-15-minute jobs keep running as a safety net. To make a registry change take
  effect right away, run `brunna config apply`
- **Phase-two enforcement**: for a project whose registry entry has `enforce: {sandbox: true}`,
  `bin/render-project-settings.py` (platform job, every 15 minutes) writes the sandbox (may write only its own
  project/runtime/cache, cannot read other projects or the bus, network allowlist) and deny rules into its tool settings
  (Claude Code: `.claude/settings.local.json`); if they are changed it writes them back and the status page shows red;
  when `enforce` is turned off it removes them. `--report` shows what it would write for each project. What to write is
  computed by `bin/policy.py` (the neutral policy, no runtime syntax allowed, checked by
  `tests/test_runtime_boundary.py`) and translated into Claude's format by `runtime_claude.settings()`.
  **Capabilities** (`enforce.capabilities`, e.g. `gpu` for idea sandboxes; the list is in `brunna.json` `capabilities` +
  `capabilities/<name>.sb`): a program the sandbox blocks runs with `bin/hub-run --cap <capability> -- …`, executed by
  the platform's runner service `bin/cap-runner.py` under the platform's own Seatbelt profile (`bin/seatbelt.py`, which
  can block the Keychain; macOS only). Design in `docs/design/capabilities.md`.
  `type`/`enforce`/`network`/`allow_write`/`privacy`/`metered_models`/`paths` are security fields that `manifest.update` cannot
  change.
  **The platform runs git only in repos it owns** (memory bare repos, memory copies, its own checkout): in a
  project's repo every git command (status page facts, worktree cleanup, backup, propose, memory sync, gate hooks) goes
  through `bin/project_git.py` → `bin/project-git-job.py` as the project (`isolation.run_as`, sandboxed when it is), and
  only its JSON or bundle comes back (docs/design/projects-and-repos.md section 4). New code that needs git facts from a
  project's repo adds an op there; it never runs `git -C <workspace>` itself.
  Such a project's own services and command jobs run sandboxed as well (`bin/sandboxed-run` → `bin/isolation.py`, one
  adapter per OS: `seatbelt.service_profile` on macOS, `bin/landlock.py` (Landlock + seccomp) on Linux; never
  unsandboxed: no working adapter means not run; each adapter declares its guarantees, `isolation.REQUIRED` must be
  among them, and the `Conformance` tests run one probe through whichever adapter the machine has; `brunna.json` `service_sandbox_exempt` holds a project back temporarily).
  Every model call goes through `bin/llm.py` (judgment points declared in `contracts/llm-points.yaml`, linted
  nightly); an unattended agent job that declares `allow:` in the registry does not stop to ask
- **User-level tool settings** (e.g. Claude Code's `~/.claude/settings.json`) **are changed only when the owner confirms
  directly in that session**; consent relayed by someone else does not count
- `contracts/`: **versioned contracts**: the interfaces brunna defines (hubbus, todo-md, state-todo, reflect-week,
  project, llm-points, lease-runner) and vendored copies of other projects' contracts (`contracts/vendor/<project>/`).
  The registry's (and modules') `provides`/`consumes` declare who defines and who reads; `tests/test_contracts.py`
  validates the examples, and `bin/contracts-check.py` (nightly at 01:30) compares the copies with the originals and
  validates real data. Adding a field keeps the version; renaming/changing a meaning bumps it, with both versions side
  by side until the readers have switched
- `integrations/`: **clients for other apps** (docs/design/herdr.md), one folder per app, named for it, not to be
  confused with `modules/`. Each is laid out as its app requires: herdr shares a folder like a herdr home, so
  `integrations/herdr/` holds `RECIPE.md` and `preview.png` with the mod in `integrations/herdr/mods/brunna/`
  (`main.js`, `mod.json`, its images; herdr finds it only one level down, so it stays exactly there), and the repo
  carries herdr's topic `herdr-mod`. A client reads only the platform's neutral output (`brunna glance --json`, the
  glance.v1 contract) and runs only the `brunna` command; it never reads the bus, the instance or a project directly.
  `tests/test_mods.py` checks them
- `modules/`: **features that can stay off** (docs/design/new-user.md 3.2): `<name>.yaml` in the same
  jobs/services/secrets/consumes format as the registry (`type: module`, cwd always hub), adopted only with `brunna.json`
  `modules.<name>: true`; their jobs are called `<module>/<job>` in the schedule. Current: assistant
  (reconcile-questions, lease-runner), backup, claude, consolidate, mirror, owner, reflect, sandbox (cap-runner).
  A module inserts itself into the core flow with `before: [<project>/<job>]` (the core does not need to know about
  it); when it is off it is as if it did not exist (someone else's after: pointing at it does not wait either); cycles
  in `after:`/`before:` are rejected by the registry. A job moved into a module keeps its schedule state with
  `legacy_name: <old full name>`
- **Project registry** (`projects/` in config, one `<name>.yaml` per project; the directory is set by `brunna.json`
  `registry_dir`; the platform repo's `projects/` holds only the platform's own `brunna.yaml`):
  paths, jobs, services, privacy labels, test command. Validation and format: the `bin/registry.py` header;
  `registry.py check` validates. What the scheduler runs = `schedule.yaml` (old entries during the transition) +
  registry jobs (`<project>/<job>`)
- `templates/`: skeleton files (`AGENT.md`, copied as is onto every project's memory branch, so it has no
  placeholders; `agent-instructions.md`: the instructions for each project's agent, with people's names written as
  `{{owner}}`/`{{owner_alias}}`/`{{assistant}}` and the content language as `{{content_language}}`, filled from brunna.json
  by sync-local-setup (`prompts.agent_rules`; every template under `templates/` is filled by `bin/prompts.py`)); the
  hook settings are not here but in `bin/merge-hook-settings.py`.
  `templates/rules/<name>.md` are optional project rules, added to a project's rules file only when the project lists
  the name in `.agent-memory/RULES` (onboarding a new project lists `engineering` and `multi-agent-review`
  automatically)
- `.claude/skills/`: `new-or-onboard-project`, `recover-projects`
- `digest/`, `index.md` (roster block generated by `gen-index-roster.sh`), `review-log.md`, `todo.md` in the data
  directory: the librarian's output; don't edit them by hand: edits get overwritten on the next run or cause confusion

## Who owns credentials

- **Three tiers**: machine-level ones (the coding agent's login/token, alert channels, backup keys and password, gh)
  **are managed by the platform**, declared in the module that uses them (`secrets:` in `modules/<name>.yaml`, not
  checked when the module is off; the other parties' contracts the assistant module reads are also in the module's
  `consumes:`), and the keys for alerts and LLM providers are derived from brunna.json by check-secrets; keys for a
  project's own services (LINE, Google OAuth, IG…) **are managed by the project**, declared in its own registry entry
  (the project changes it with manifest.update); issuing new ones at the external service, revoking and replacing
  them **only the owner can do** (an owner to-do)
- **A registry `secrets:` entry holds only the name, location and expiry, never the value** (format in the
  `bin/registry.py` header); the platform job `brunna/secrets` (daily at 08:00, `bin/check-secrets.py`) checks that
  each exists, whether it is backed up (`backup.covers()`, the same rules as the nightly backup) and whether it is about
  to expire, and alerts on problems; it also lists files whose names look like credentials but are not declared
  (reminder only). Neither this script nor `check-claude-login.py` prints values
- **Alerts** (`bin/alert.py`; channels: Discord, ntfy, email, macOS notifications, configured in brunna.json `alerts`): a
  new problem is sent at once, an unresolved one is re-sent every 24 hours, and a recovery is sent when it is resolved;
  **an alert that cannot be sent is recorded in `.state/alerts-failed.json` and the status page shows red**. The
  secrets job first checks every day that the alert channels still work (without sending a message). The scheduler
  watchdog deliberately uses its own copy and does not depend on this script
- How projects handle it (where values live, how code reads them, no silent degradation, revoking whatever entered the
  history before open-sourcing) is in the "Credentials" section of `templates/agent-instructions.md`, which is in every
  project's rules file

## Safety properties (don't bypass)

- In every project, the session-end / pre-compaction hooks are scoped to `memory/`/`STATE.md` only; don't change them
  to `git add -A`: that would accidentally commit the user's real work that is not ready yet
- The hub machine is never the origin of any project's main branch; memory tracking goes only through the separate
  `.agent-memory` worktree. This safety property must not be relaxed because "this project happens to have no external
  remote yet"

## Child sessions

When a child session opened by an brunna session is done and its result has been checked, archive it; don't leave it
idle. The same rule for each project is in `templates/agent-instructions.md` (which goes into every project's rules
file).

## Who commits/pushes brunna

- **The commit gate and the test system** (docs/design/test-waiting.md): brunna's registry entry declares its tests
  (project v2: `code`, `inputs`, tiers `lint` and `full`) and `commit_gate: true`; the platform's hook installer
  (`render-project-settings`, also run by `brunna land` after every merge) puts a `pre-commit` in the repo's git dir.
  A commit on a branch runs the `lint` tier (`bin/lint`: ruff with only the F rules, rules in `ruff.toml`, ruff in the
  platform's venv by `bin/setup-python`; and the config check), `merge-branch` the `merge` tiers (`lint` and `full`:
  all tests through `bin/run-tests`, one process per module, in parallel, the English goldens too, under a fresh HOME
  that no test may write into). Both go through the main checkout's runner, `brunna test` (`bin/test-runner.py`):
  the staged or merged content exported, the entry read from that tree, a recorded pass for the same tree a hit
  (`bin/tiers.py`, the store in the repo's git common dir), the complete output in a file whose path it prints. A
  change to files outside `code` runs nothing. Commits on main that didn't come through `merge-branch` are counted on
  the status page. Behavior visible from outside is pinned with golden files before and after a refactor (e.g.
  `tests/fixtures/llm-golden.json`); don't rely on comparing by hand
- **Commit titles follow Conventional Commits** (`<type>[(<scope>)][!]: <description>`, type: feat / fix / perf /
  refactor / test / docs / build / ci / chore / revert; the scope is the area's name, e.g. `fix(hubbus): …`; a breaking
  change adds `!` and says `BREAKING CHANGE:` in the body). The platform's `commit-msg` hook (every repo with
  `commits: conventional`) rejects malformed titles (`bin/commit_title.py`); `CHANGELOG.md` and each release's notes are generated from the commits when a version is
  tagged, with `cliff.toml` (git-cliff, the same flow as the assistant's repo; nothing in it is written by hand; steps in
  CONTRIBUTING.md, "Releasing"). Other projects: with `commits: conventional` in the
  registry, `merge-branch` checks before merging and the rules file carries the explanation automatically
- **Manual edits (scripts, docs)**: made only by brunna's own sessions on the hub machine, **each in a linked
  worktree of the hub, merged with `brunna merge-branch`** (docs/design/test-waiting.md A1). The main checkout is the
  running platform (the scheduler, services and hooks execute its files), so it holds only merged, tested commits and
  no session edits in it. In a worktree the platform's code is the branch's, while `.hub-home`, `.state` and `.logs`
  are the main checkout's (`hubconf`, `hubpaths.sh`); from there the scheduler and `brunna config apply` refuse and
  `registry.refresh` writes nothing. Branch commits run the full commit gate; `merge-branch` runs it again on the
  rebased tree (brunna's `runtime.test`; a rebase that changed nothing is a hit); push only when the owner says.
  A session of another project that finds a problem in brunna only reports it (`brunna send --to brunna`, or
  tells the user) and does not change it directly. Commit only your own paths (`git commit -- <paths>`), never
  `git add -A`
- **Version-controlled files written by scripts** (the config repo's `projects/<name>.yaml`
  (onboard/rename/manifest.update), `mirror-list.txt`, `schedule.yaml`, `brunna.json` (schedulectl set-model)): the script
  that writes them commits only those files itself with `bin/commit-hub-files.sh`, so `onboard-project.sh` is safe to
  run from any session; nothing in the data directory goes into git
