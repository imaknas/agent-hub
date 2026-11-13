# Known issues

> Maintained by hand. Pitfalls that automation (schedules, headless coding agents, the librarian, watchdogs) has run
> into and may run into again, and the operating principles learned from them. Check here first when you see a symptom;
> read it through before designing new automation too.
> This file is general; a deployment's own incident records (machine names, projects, dates) go in its instance
> directory's `config/known-issues.local.md`.
> "The hub machine" = the machine that runs the scheduler and the services; "the remote machine" = a machine that opens
> the hub machine's folders with the coding agent's remote feature (optional).

## Scheduled/automated Claude Code gets 401

- **Cause**: on macOS, Claude Code's Keychain credentials and `~/.claude/.credentials.json` can get out of sync, and the
  headless run picks up the stale one.
- **Fix**: when scheduled or automated Claude Code gets a 401, first check that the two credentials match, before
  suspecting an expired token or anything else. The platform's headless calls use `~/.claude/headless-oauth-token`
  (`CLAUDE_CODE_OAUTH_TOKEN`) by default; confirm that one is still valid too.

## "Invalid signature in thinking block"

- **Cause**: extended thinking's thinking blocks carry an encrypted signature bound to the conversation; truncating or
  modifying them during compaction (e.g. `truncateAfterCompaction: true`) gets the next call rejected.
- **Fix**: long-running automation either drops thinking blocks entirely or keeps them untouched; **never truncate or
  edit them**.

## Tailscale funnel / serve

- **Each funnel target can have only one owner** (recorded in `services.tsv`). One node can open several funnel ports
  at once (443/8443/10000), without sudo.
- **In a non-interactive environment (launchd, systemd), `tailscale funnel` must always get `--yes`**: without it,
  off+on fails silently and falls back to an expired old target.
- **curl to a tailnet-only serve port from the machine that has the funnel open fails with TLS**
  (curl exit 35 / `SSL_ERROR_SYSCALL`): locally `<host>.<tailnet>.ts.net` resolves to the funnel's public address, not
  the tailnet 100.x, and the tailnet-only port refuses. The service itself is fine.
  For local tests use `curl --resolve <host>.<tailnet>.ts.net:<port>:$(tailscale ip -4) https://…`;
  other machines with Tailscale on go through MagicDNS and are not affected

## Watchdogs / scheduled jobs

- **Verify after fixing**, and record failures separately: a repair script once could not find tailscale on launchd's
  PATH (no `/opt/homebrew/bin`), yet recorded "fixed" every time. Use absolute paths under background services.
- **Put watchdogs on the service manager** (launchd / systemd), not in some agent's internal cron (that one once
  disappeared without a sound).
- **A missing input file must fail loudly**: after an input file was moved, a cron depending on it was silently broken
  for weeks.
- **Reminder jobs must understand "done / postponed"**: one once kept nagging about things long done, or that the owner
  had already postponed.
- **Nightly tidy-up jobs write dated files**; a memory file that is loaded every time is changed only when something
  substantive changed (appending to an always-loaded memory file every day bloats every session's context).
- **No LLM in schedules that only execute**.
- **Two schedules touching git in the same checkout can collide on `.git/index.lock`**: e.g. one service checkout with a
  daily job (commit+push) and an hourly sync (`git pull`), neither wrapped in a lock. Either wrap them in
  `bin/with-lock.sh` or stagger the times.
- **The scheduler runs jobs in order, and the service manager does not start a new tick while the previous one is still
  running**: a job that runs for tens of minutes makes all the other on-time jobs wait. A job that will run longer than
  a few minutes gets `background: true`; a background job may touch git at the same time as other jobs in the same
  checkout (the index.lock item above): when there is an order, add `after:`.
- **Nobody notices when the scheduler stops**: it is the only trigger for every job, and it also generates the status
  page. `scheduler-watchdog.py` watches it independently from the service manager; when the watchdog itself is not
  watching, the status page shows red.

- **In a shell script, backticks in a double-quoted prompt are executed as commands**: a format example in the prompt
  (a to-do line between backticks) was eaten by bash (the log showed only `-: command not found`), and the model never saw it
  for weeks. Backticks in a prompt must be written `\``, or use a single-quoted heredoc; after the change, expand the
  prompt and look at it once (the platform's prompts are now all assembled in Python).
- **To have a headless agent change only one file, restrict it with permission rules, not just the prompt**: Claude
  Code's `--permission-mode acceptEdits` allows every file in the directory (a small model went and changed other files
  anyway); use `--permission-mode dontAsk --allowedTools "Edit(./STATE.md)"` instead (`Edit(...)` covers Write/Edit).
  Better still: have the model only return an answer and let the platform write the file (a completion judgment point).

- **An empty bash array under `set -u` is an unbound variable on bash < 4.4**, and macOS's `/bin/bash` is 3.2: a
  script that passes optional arguments as `"${args[@]}"` dies there when the array is empty, while the same script
  passes on a newer bash in a terminal. `run-agent-job.sh`'s background run re-executes itself with `/bin/bash`, so a
  connectors job (no sealing argument) died at the llm.py line every day and left its status at "running", which
  nothing reports. Write `${args[@]+"${args[@]}"}`, and test shell scripts with `/bin/bash`
  (`tests/test_run_agent_job_args.py`).
- **awk reading an "exclusion list" file with `NR==FNR` filters out all the data when the list is an empty file**: with
  no line read from the empty file, `NR==FNR` holds for every line of the second input and `next` eats them all; the
  roster in index.md was once committed empty because of this. Read the list with `getline` in `BEGIN` instead; tests
  must include the "list is empty" case (`tests/test_index_roster.py`).

## Settings, installs, credentials

- **Changing a settings file ≠ the running process has loaded it**: check the log to confirm the reload, or just
  restart.
- **One install source per tool**: with one copy of the same CLI from Homebrew and one from nvm, the versions drift
  apart and the protocol becomes incompatible. After upgrading, check with `which -a <tool>`.
- **Platform scripts do not find python through PATH** (`bin/py` → the platform's own venv, built by
  `bin/setup-python`): a project that needs packages uses a uv venv (`uv run`), never installs into the global python.
  Background services use the platform's PATH, which does not include paths added by the user's shell configuration.
- **grep the settings before removing a local model**: after a model was removed, the features using it kept getting
  404.
- **Tests / a second instance must start with a clean environment** (`env -u <TOKEN> …`): a test gateway inherited the
  production bot token from the shell and fought production for the same session.
- **A temp instance run from the live checkout's code still sees the live root**: `.state`, `.logs` and `.hub-home`
  come from the hub root, not from `AGENT_HUB_HOME`/`AGENT_HUB_CONFIG`. A throwaway world that pointed only the
  instance and `bus_dir` at temp dirs would have had `hubconf.bus_db()` repoint the live `.logs/hubbus.log` at its temp
  bus. Code that changes the root's live layout checks first that the process is on the root's own instance
  (`hubconf._own_instance()`, `_migrates()`); a throwaway world also sets `AGENT_HUB_ROOT` to a temp copy.
- **Don't hand-edit installed packages or production settings to demo/test an unreleased feature**: this once caused a
  crash loop of 20+ restarts. Use a separate test account/environment; to recover, go back to the last good settings
  and reinstall the unmodified files.
- **node (and the claude installed under it) are managed only by nvm**: the autoremove that `brew install <something
  else>` runs along the way deletes a Homebrew `node` that "was installed as a dependency of another package and is no
  longer depended on", and services pointing at `/opt/homebrew/bin/node` stopped, unnoticed for days.
  Background services and schedules have no shell nvm setup, so the platform uses `hubconf.nvm_bin()` to work out the
  full path of the nvm default version (first in `LAUNCHD_PATH`; a service's env writes `$PLATFORM_PATH`, `llm.py` uses
  `hubconf.tool("claude")`), and render-services follows when nvm is upgraded.
  **Don't install node with Homebrew or install global CLIs with its npm** (that copy of claude stayed on an old version
  and never updated once node was deleted).
- **Read the whole output of `brew install/uninstall`**: it autoremoves dependencies along the way, and `| tail -2`
  hides the "Uninstalling node…". Afterwards run `brew missing` to confirm nothing else broke; to keep a formula,
  `brew install` it once (marked installed on request, so autoremove won't delete it).
- **launchctl bootout is asynchronous**: bootstrapping right away gives `5: Input/output error` and the service stays
  unloaded. render-services waits for it to disappear from launchd before bootstrapping and retries on failure; a
  manual reload has to wait too.
- **render-services' safety net**: when a command cannot be resolved it leaves the currently working one alone; a failed
  bootstrap, a keepalive service not running, or something that should be loaded and isn't all send an alert (re-sent
  every 24 hours while unresolved, and once more on recovery) and fail the job (status page red). With only the status
  page red, nobody looked and it stayed down for a day and a half. When the alert itself cannot be sent (expired token)
  the status page shows red too; it is never silently dropped.

## The memory worktree inherits the project's git hooks

- **Symptom**: a project's memory notes stay stuck uncommitted, and the `sync-all-memory` log has
  `git-sync-memory failed`, `npx: command not found`, `husky - pre-commit script failed`.
- **Cause**: `.agent-memory` is a worktree of the same repo and shares `core.hooksPath`. The project's husky pre-commit
  runs `npx lint-staged`; under a background service PATH has no npx and it fails. In an interactive session it
  succeeds, but the project's prettier rewrites the formatting of the memory notes along the way.
- **Fix**: `git-sync-memory.sh`, `sync-all-memory.sh` and the AGENT.md commit in `sync-local-setup.sh` all set
  `core.hooksPath` to `/dev/null` through `GIT_CONFIG_*` environment variables, so memory commits/pushes don't run the
  project's hooks. Any new memory commit/push must do the same.

## When the remote machine is off, the SSH sessions it opened cannot write

- **Symptom**: asking a session from the phone (Remote Control) to change files or wrap up fails every time with
  `PreToolUse hook did not respond before its timeout (host client may be unreachable)`; reading files and looking
  things up work.
- **Cause**: the session's claude runs on the hub machine, but it was opened over SSH by the remote machine's desktop
  app. The permission check before writes (the PreToolUse hook the desktop app installs) and the app's own tools are
  all answered by the app on the remote machine; with the remote machine off or asleep, the program on the hub machine
  is still alive, so it can read and chat, but every write times out. It is not something missing on the hub machine.
- **Fix**: to be able to write while the remote machine is off, open a local session directly on the hub machine (use
  Remote Control from the phone), or keep the remote machine from sleeping. When asking it to write something from the
  phone gets no response, first check that the remote machine is on.

## The OS sandbox cannot block the Keychain

- **Found**: Bash under the sandbox running `security find-generic-password -s 'Claude Code-credentials'` returns 0
  (found); adding `-w` prints Claude's login credentials. File-level blocks (denyRead, Read deny rules) do nothing
  against it: the Keychain is accessed through a system service, not by reading files. The same sandbox does block
  reading `~/.ssh`, the credential files in `~/.claude`, and `~/services/*/.env`.
- **Impact**: you cannot let an unattended agent run commands freely in the sandbox and assume the credentials are
  safe. Because of this, the idea sandboxes' work mode stays at "ask the owner for every command"; the lease's deny list
  has `Bash(security …)` added, but that is only reinforcement (`sh -c` or python gets around it).
- **Before loosening commands**: first find a way to block the Keychain at the system level (e.g. a sandbox profile
  that blocks securityd's mach-lookup, or have those jobs use credentials that are not in the Keychain), and test it
  for real.
- **The platform's own profile does block it**: `bin/seatbelt.py` blocks mach-lookup of `com.apple.SecurityServer` and
  others, and calling the Security framework directly (not through the `security` program) finds nothing either;
  `tests/test_cap_profile.py` tests it every time. For now only the platform runner (`hub-run`) uses this profile; a
  session's own Bash is still in the coding agent's own sandbox.

## macOS: claude under SSH is "not logged in"

- **Symptom**: SSH into the hub machine and `claude auth status` shows `loggedIn: false`, and `claude -p` cannot use
  claude.ai connectors, even though you did log in on that machine.
- **Cause**: the login credentials are in the login keychain, which an SSH session cannot read (`security` returns
  `User interaction is not allowed`); launchd's gui domain (the scheduler, `schedulectl run`) can.
- **Fix**: to test a scheduled job that needs the Claude login, always use `schedulectl run <name>` (goes through
  launchd), don't SSH in and run the script directly. The platform's headless calls use
  `~/.claude/headless-oauth-token` instead and are not affected, but that token keeps connectors from loading, so jobs
  with `connectors: true` in the registry deliberately don't use it.
- **Other CLIs are the same**: gh stores its token in the keychain by default, which an SSH session (including sessions
  the desktop app opens over SSH) cannot read, so `gh auth status` says the token is invalid. To use gh under SSH, run
  `gh auth login -h github.com --git-protocol ssh --web --insecure-storage` (stored in `~/.config/gh/hosts.yml`,
  readable only by you). vercel stores a file anyway; one `vercel login` is enough. `migrate-to-mini.py` checks both.
  A gh token from logging in again does not necessarily have the `workflow` scope: to change `.github/workflows/`
  through gh/HTTPS, run `gh auth refresh -s workflow`; git push over an SSH key is not affected.
- **Repos whose origin is HTTPS**: git takes HTTPS credentials from the keychain by default, and under SSH gives
  `could not read Username`. Run `gh auth setup-git` once, and github.com HTTPS uses the token gh stores in a file.
- **Three credentials, each of which can break**: the keychain (the subscription login, under launchd: only registry
  jobs with `connectors: true` use it; the assistant and leases use the headless token),
  `~/.claude/.credentials.json` (used under SSH when the keychain cannot be read; the refresh token lasts about 28 days
  and is extended when used), `~/.claude/headless-oauth-token` (`claude setup-token`, one year; with
  `CLAUDE_CODE_OAUTH_TOKEN` set, claude.ai connectors don't load). The platform's `claude -p` always uses the headless
  token by default (handled in one place in `llm.py`; callers must not set it themselves); only jobs with
  `connectors: true` in the registry use the subscription login. The claude module runs `claude-login` hourly to check
  them and alerts when one breaks; the keychain login only while some job declares `connectors: true` (with none it
  says it was not checked). One problem is one alert: a changed error text or count is not sent again as new.
- **Another pitfall**: when a launchd job ends, it kills the processes in the same process group, and `nohup ... &`
  does not prevent it; to keep running in the background takes a double fork + setsid (what `run-agent-job.sh` does).

## Transcripts that must not go to the cloud

- Some transcripts (e.g. a work profile) may only be read by a local model. Anything that calls a cloud model (weekly
  report, librarian, agent job prompts) must not read those directories; `hublib.TRANSCRIPT_ROOTS` lists only the ones
  that may be sent. **Masking people's names is not enough**: client names, project code names, code, internal URLs and
  business terms are still identifiable; a directory name can itself carry a client/repo name. For the cloud to see it,
  a local model must first rewrite it into a de-identified abstract summary, the cloud sees only the summary, and the
  confidentiality terms must be confirmed to allow it first.

## Phase-two sandbox / permission rules (Claude Code)

- **A permission deny overrides the sandbox's allowWrite**: with the project denying `Edit(<hub>/**)`, even an
  allowWrite of `<hub>/.state/bus` in the sandbox cannot write (`Operation not permitted`). That is why the bus lives
  outside the hub directory (`hub.json` `bus_dir`). Anything sandboxed projects need to write in future goes on a path
  that is not denied.
- **allow cannot open an exception to a deny** (deny → ask → allow, the first deny wins): "may read only the other
  projects' STATE.md" is impossible; read `~/.memory-copies/<p>/STATE.md` instead.
- **Hooks run outside the sandbox**: memory sync and the mailbox hook are not affected.
- **A project's own MCP of the same name overrides the claude.ai connector**: when a project has its own MCP called
  `upwork` (needs OAuth), a headless job run in that folder does not get claude.ai's Upwork tools. Agent jobs run in
  their own state directory with `cwd: state`.
- **Under dontAsk, tool names must be exactly right**: claude.ai connector tools are called
  `mcp__claude_ai_<name>__<name>__<tool>` (one extra segment). A wrong name means everything is refused, and the report
  says "blocked by permissions".
- **Turning MCP tools off doesn't stop their servers**: neither `--tools ""` nor deny rules keep a server from being
  started and connected; a deny on the whole server (`mcp__<server>`) only keeps its tools from being offered or
  called. Only `--strict-mcp-config` keeps them from loading. A connectors job (it needs the account's claude.ai
  connectors, which strict MCP drops too) therefore starts every server configured on the machine, a production
  database's included, and is held back by the deny the platform computes for each run (`tool_servers:
  allowed_only`, docs/design/evaluators.md step 0). The built-in MCP resource tools (`ListMcpResourcesTool`,
  `ReadMcpResourceTool`, `ReadMcpResourceDirTool`) reach any connected server's resources by name past those rules,
  so they are denied too.
- **The account's claude.ai connectors are on no disk**: they are fetched from the account at start-up (only with the
  subscription login). `~/.claude.json` `claudeAiMcpEverConnected` is history, not configuration. `claude mcp list`
  run in the job's folder with the job's login is the one place that lists them (along with plugins' servers, project
  and local ones); it health-checks each server while doing so.
- **At user level, deny sessions using Edit on `~/workspace/**/.claude/settings.local.json`** (the recommended setting):
  to change a project's settings, change its registry entry and let render-project-settings write them; platform
  scripts don't write with the Edit tool and are not affected.

## Principles for working with agents

- **Don't trust an agent's/subagent's own report** ("done", "the tool didn't respond"); look at objective records:
  git HEAD/diff/CI, the scheduler's and each job's logs, the actual tool calls and permission_denials in the transcript.
  Agents make up reasons and progress.
- **Irreversible or credential-related steps stay in the main session**, confirmed directly by the owner; a relayed
  "they already agreed" does not count. When the owner pastes a password, an agent never types it on their behalf; the
  owner enters it themselves.
- **Browser automation: stop and ask after the same step fails 3 times** (one button was once retried 30+ times, costing
  about $30).
- **Set a time limit for infrastructure debugging first (about 30 minutes)**; past it, write down a workaround and move
  on.
- **Truncating a transcript is not compaction**: cutting by line count loses progress; use a summary. Don't reset
  long-lived sessions with a short idle timer (a 120-minute idle reset lost an entire stretch of conversation).
- **Personal reflections don't go into shared agent memory.**
- **Search before asserting where a concept comes from** (an agent once said "harness" was Anthropic's own term, when
  OpenAI had written about it too).
- **Sandboxes cannot be stacked**: running `sandbox-exec` inside Claude Code's sandbox always gives `sandbox_apply:
  Operation not permitted`. To run something under different restrictions (e.g. GPU needs Metal, which Claude Code's
  sandbox blocks with no setting to open it), it can only be handed to a process outside the sandbox to apply: the
  platform runner `bin/cap-runner.py` + `hub-run` (docs/design/capabilities.md).
- **`excludedCommands` only works for a command on its own**: an excluded command run on its own leaves the sandbox;
  chained with other commands (`&&`, `;`) the whole chain stays in the sandbox. When testing, don't append
  `; echo rc=$?`, or you'll think it doesn't work. The platform does not use it.
- **Seatbelt's `(deny signal (target others))` does not block** sending signals to other processes; write
  `(deny signal)(allow signal (target same-sandbox))` instead, and likewise for `process-info*`.
- **A long-running service runs the code loaded when it started**: changing a module it imports mixes old and new
  modules and breaks it midway (work gets stuck, handovers fail). lease-runner and cap-runner record
  `hubconf.code_stamp()` (the latest modification time in bin/) at startup; when it changes and has been stable for 60
  seconds (a burst of edits restarts only once), they exit on their own at a moment when no lease/job is running, and
  the service manager (KeepAlive) restarts them with the new code: no manual restart is needed after changing code, but
  while a lease is running it waits until it is idle. New long-running services do the same.
- **Look carefully at the index before committing**: in a shared working tree, a bulk `git add` commits what another
  session staged along with yours; use `git commit -- <paths>`.
