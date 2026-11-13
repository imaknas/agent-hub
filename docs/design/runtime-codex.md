# The Codex runtime adapter (`bin/runtime_codex.py`)

Status (11-14): step 0 (fail closed) built 11-08; **Step 2 built 11-14** (`bin/runtime_codex.py`, lifecycle + sessions;
"Step 2: progress (11-14)" below); steps 1, 3, 4 not built. Step 1 follows `runtime-versions.md` steps 1–4 (it is that
doc's step 5). The Codex preset now leaves `runtime.default` at `none`, not `command` as "Where it stands" below says.
Design, 11-08. Owner, 11-01: the adapter is designed, cold-reviewed, then built port by port, each port that
isn't there said to be off (`Unsupported`, doctor). Owner, 11-08: "start the Codex enhancements" after the sandbox gaps.

## Where it stands (read 11-08)

Codex today runs only through `runtime_command` (the generic command-template runtime): `modules/codex.yaml`'s preset sets
`runtime.default: command` with a `codex exec --sandbox workspace-write … -o {output_file} -` template. That runtime has two ports,
`agent` (one run) and `completion`. Everything a session-serving runtime has is off: hooks (letters seen live, notes reminders),
the rules block, session state (so mailbox dispatch never runs: `hubbus` needs `agent` + `sessions`), wake, resume, confined runs,
login state, tool-server lists. `bin/sessions_codex.py` reads Codex's session files for the weekly statistics only.

What's inconsistent today, independent of the adapter (C1 below):

- doctor's feature table asks for `auth` for judgment points, so with Codex it says they are off, while `llm.py` runs them and doctor
  also says "judgment points go to the runtime command". The "mailbox tasks" row asks for `auth`, which dispatch doesn't use.
- `modules/codex.yaml` and the README promise background work and that Codex "picks up the project's setup when the session starts";
  agent jobs with `allow:` and mailbox escalation are refused (`tool_servers: allowed_only`), and nothing fires at session start.
- The completion port isn't tool-free with Codex: `codex exec --sandbox workspace-write` in an empty folder still has a shell and
  reads anything (the port's contract: "no tools").
- No model names: the template has no `{model}`, `runtime_command` no `MODELS`, so `model_group` is ignored.
- doctor names the runtime `command`, never Codex; `runtime_command` says it should run only in the throwaway worktree but nothing
  holds it there (`THROWAWAY_ONLY` isn't set).
- `mailbox-runtimes.md` shows `runtime.installed: [claude, codex]`, which `config-check` rejects (no `codex` runtime).

## What Codex offers (its docs and source, 11-08; codex-cli 0.165 here, 0.167 the latest stable)

- **Hooks**: `hooks.json` or `[hooks]` in `config.toml`, user-level (`$CODEX_HOME`, default `~/.codex`) and project-level
  (`<repo>/.codex/`, loaded only in a trusted project); every layer loads. Events include SessionStart, UserPromptSubmit, PostToolUse,
  Stop, SessionEnd, PreCompact. Input on stdin: `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `turn_id`, per-event fields
  (`prompt`, `tool_name`/`tool_input`, `last_assistant_message`). Output: `hookSpecificOutput.additionalContext` is added as developer
  context; `decision: "block"` on Stop continues the turn with the reason. **Trust is per hook, by the hash of its definition**: a new
  or changed hook is skipped until the user reviews it (`/hooks`). `async: true` runs a hook in the background, but "finishing a
  background hook doesn't start a new turn": no equivalent of Claude's `asyncRewake`.
- **Instructions**: `AGENTS.md` per directory, git root down to the cwd, one file each (`AGENTS.override.md` *replaces* `AGENTS.md`
  in that directory), 32 KiB combined. No untracked local file.
- **Sessions**: rollout JSONL under `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<time>-<uuid>.jsonl`; the format isn't documented as an
  interface. `codex exec resume <id>` and `codex exec fork <id> [prompt]` continue a saved session without a terminal UI.
- **Queue**: `codex queue --thread <id> --message <text> [--resume]` (marked stable) adds a message to a thread through the app-server;
  its tests show a message queued on an idle loaded thread starts a turn at once, one queued while a turn runs waits for it, and one
  queued on a thread no process has loaded is sent when it's resumed. The interactive UI attaches to a shared local daemon by default.
- **Sandbox**: Seatbelt (macOS) / bwrap + seccomp (Linux); `--sandbox read-only|workspace-write`; permission profiles
  (`[permissions.<name>].filesystem.<path or glob> = "read"|"write"|"deny"`) can deny reading paths; per-project trust
  (`[projects."<path>"].trust_level`) decides whether a repo's `.codex/` config and hooks load at all.
- **exec**: `--output-schema <file>`, `--json` events (usage on `turn.completed`), `-m`, `-c key=value`, `--ephemeral`,
  `--ignore-user-config`, `-C`; no documented tool-free mode (nearest: `features.shell_tool=false`, web search off, no MCP servers,
  `--sandbox read-only`, approval `never`).
- **Login**: `codex login status` (exit 0 when logged in); `$CODEX_HOME/auth.json` or the keyring.

## Found by the 11-08 blind review (blocking, all taken)

- **The platform's Codex runs ignore every limit a judgment point declares (live in released versions).** `runtime_command` drops a
  point's `tools`, `allowed_tools`, `permission_mode` and `extra_args`, and passes the whole environment. So with the Codex preset every
  agent point has a shell and reads anything (`workspace-write` limits writes only; M1 showed `read-only` doesn't stop `cat`):
  `librarian-state` reading a note a sandboxed project planted could copy the secrets folder into `STATE.md`; `mailbox-propose` takes
  anyone's letter; `brunna complete` from a sandboxed project runs through it. This machine's default is Claude, so it isn't hit here.
- doctor's feature table can't be fixed by trading `auth` for `sessions`: mailbox escalation and conversation declare
  `settings: platform` and `tool_servers: allowed_only`, so they need `confine` and `tool_servers` too (`llm.py`); jobs with `allow:` need
  `confine` + `tool_servers`; follow-up needs `confine`, and resuming only works on the default runtime.
- `runtime.default: codex` would break Claude's worktree sessions: `worktree-memory.sh` calls `hub-event wait|worktree|worktree-scope`
  without `--runtime`, and `hub-event` falls back to the default runtime.
- A shared app-server daemon may be every Codex session's process: `record_session` keeps one `.live` record per (pid, start), so at
  most one Codex session would count as live, and `alive` would be true for all of them while the daemon runs.

## The plan (revised)

**Step 0, now, before the adapter: fail closed.** `runtime_command` becomes `THROWAWAY_ONLY` (only `mailbox-propose`, in the platform's
throwaway copy, where it can't reach the real checkout and the platform applies only allowed paths), and its completion port refuses
too, since it can't be made tool-free generically. Config check then refuses `runtime.default: command`; an instance that has it (the
Codex preset until step 1) gets an upgrade step that says so and points to step 1's runtime, and doctor says judgment points are off
on it and why. Before this, the worktree hooks name their runtime (`--runtime claude`), so a default other than Claude can't take them.
Built 11-08: `runtime_command` is `THROWAWAY_ONLY` with no completion port (`run()` refuses itself); config check refuses it as the
default, and `brunna config migrate` (run by upgrade before config apply, one-way) moves off it `runtime.default: command`, its pins on
other points and `impl: command` providers; the worktree hooks name `--runtime claude` and an unnamed hook is Claude's; doctor's feature
table is per the second finding (the librarian's answer-only fallback counted), with a row for the residual of a pinned `mailbox-propose`.

**Step 1: `runtime_codex` with agent + completion + auth, confined.** Every platform run of Codex is built from the point's spec and
`policy.py`, never from the user's or the project's Codex config:
- `--ignore-user-config` and no project config: measured (M5) that `codex exec` loads no `<repo>/.codex/` config, hooks or MCP servers
  that way, or else the run's working folder is one with none (as Claude's `--restricted` does); refused if neither holds.
- A point with no tools (`tools: ""`, completion): shell off is not enough on 0.167 (M1 again, below: the models are code-mode only
  and their `exec` tool still reaches `apply_patch`, web and MCP resources). The run sets `features.shell_tool=false`,
  `features.unified_exec=false`, `features.apps=false`, `features.plugins=false`, `features.goals=false`, `features.multi_agent=false`,
  `web_search="disabled"`, `default_permissions=":read-only"`, `approval_policy=never`, in a platform home with no MCP servers; what is
  left is a clock and MCP resource tools with no server behind them. A test pins the list the run sees (the model asked for
  `ALL_TOOLS`), so a Codex release that adds a tool fails it instead of widening the run.
  **Not tool-free on the default model** (runtime-versions.md, V-M1, 11-09). A model whose catalog entry has
  `multi_agent_version: v2` (`gpt-6-luna`, the default) still gets a `collaboration` namespace (`spawn_agent`, `send_message`,
  …) with every flag above, `features.multi_agent_v2=false` included. `gpt-5.6-luna` (v1) has none. So each point kind's model
  is pinned, and the pinned list covers the namespaces as well as `ALL_TOOLS`. It is checked per run: `codex debug prompt-input`
  before, the rollout's `additional_tools` after.
  A point with tools gets them only through a permission profile rendered from the policy (M8): writes to its declared folders, reads
  denied for what `policy.py` denies every sandbox (`deny_read`, credentials, `platform_state`), `approval_policy=never`. Paths and
  globs in a profile must be absolute (a relative glob stops the run at load, nothing runs).
- **Codex 0.167.0 or later**: 0.165.0 replaces a linked `auth.json` with a file on refresh (M9); doctor and the login check refuse an
  older one and say why.
- `--json` doesn't list commands a code-mode model runs through `exec`; what a run did is read from its rollout in the platform home.
- The environment is the platform's minimal one (as Claude's runs: the runtime's own auth and PATH), not `os.environ`.
- `MODELS` per group; `--output-schema` measured with the platform's real schemas (M1 used a strict one; 8 of 11 aren't strict).
- `auth`: `codex login status`. `RULES_FILE_NAMES = ("AGENTS.md", "AGENTS.override.md")`. `control_paths`: `.codex` (so a sandboxed
  project can't plant hooks or config that a trusted Codex later loads). Credentials: `~/.codex` (`auth.json`), write-denied
  `config.toml` / `hooks.json` as user-level control files.
- doctor's feature table rewritten per the second finding above; rows name "Codex", not the runtime id.
- `modules/codex.yaml`'s preset sets `runtime.default: codex`; a login-check job like Claude's; the README says what works.

**Step 2: lifecycle + instructions + sessions** (letters seen live, notes reminders, the platform's rules, dispatch serving Codex
sessions). User-level hooks (D1), a fixed command (`brunna` on PATH, `hook --runtime codex <event>`), so its hash doesn't change across
upgrades; doctor flags a Codex process with no record (a hook skipped as untrusted). Rules by SessionStart `additionalContext`, the
`instructions` port left absent so nothing writes the tracked `AGENTS.md`; the feature row says rules arrive by injection. The session id
for a shell (`current_session`) comes from what Codex puts in a tool shell's environment (M6), else Codex sessions send without a
sender session. Records keyed by Codex's session id, not by process: every thread, Desktop's and the TUIs', runs under one shared
daemon (M7).

**Step 3: wake** (`codex queue --thread <id>`, the id checked against the record's format first), **status / summary** (the rollout
reader shared with `sessions_codex.py`, the same privacy gate as Claude's last reply), **resume / fork** (`codex exec fork`).

**Step 4: confine + control + tool_servers for sandboxed projects and `allow:` jobs**, with a "sessions confined" check separate from
the platform's own runs (a sandboxed project's Codex session the owner opens is confined only if D2 says so); render-project-settings
gets a Codex renderer beside Claude's. `FOLDER_GIT_OFF` stays unknown (Codex runs git in the folder) until measured.

## Implementation plan (11-14), reviewed blind once (three blocking, folded into its r2 below)

The owner's goal now (11-14): use Codex as a side session beside Claude Code. What that needs first is runtime-codex.md's
**Step 2** (a Codex session the owner opens is served: letters seen live, notes reminders, the platform's rules, session
state for dispatch), then **Step 4** (a sandboxed project's Codex sessions confined, D2 both layers), then **Step 1** (the
platform's own runs on Codex, on runtime-versions' store; it is that doc's step 5), then **Step 3** (status, summary,
resume/fork; wake stays off under D4 until M11). The owner's rule (11-01): built port by port, each port not there said
to be off (`Unsupported`, doctor). Reordering is allowed by it; this plan asks the review whether Step 2 can stand
without Step 1.

### Step 2 first: `bin/runtime_codex.py` with lifecycle + instructions + sessions

1. The adapter, registered in `runtime_ports.RUNTIMES` as `codex`, `MODELS` empty for now, ports: `lifecycle`
   (`parse_event` / `unusable` for Codex's hook input; `inject` → its additionalContext output; `deny`; `install_lifecycle`
   → see 2), `sessions` (`alive`, `live_sessions`, `context_size`, `current_session` from `CODEX_THREAD_ID` (M6)),
   `instructions` **absent** (the tracked `AGENTS.md` is never written; rules arrive by SessionStart injection, the feature
   row says so). `agent`, `completion`, `auth`, `confine`, `control`, `stream`, `wake`, `session_status`, `session_summary`,
   `distribution` absent (Unsupported; doctor's table names each).
2. **User-level hooks (D1)**: `~/.codex/hooks.json` with the platform's handlers for SessionStart, UserPromptSubmit, PostToolUse,
   Stop/SessionEnd (whatever events Codex has, mapped to the platform's EVENTS), each command the hub's absolute path
   `<hub>/bin/hub-event --runtime codex <event>`; their trust written into `~/.codex/config.toml`
   `[hooks.state."<file>:<event>:<group>:<handler>"] trusted_hash` only after checking the definition under that key is the
   platform's own (never from `hooks/list`'s current hash). This is a user-level change: **only with the owner's direct yes,
   in a session** (AGENTS.md); the platform ships `brunna user-settings --install --runtime codex` (and `--update` for
   upgrade's `user_refresh`), and nothing installs it automatically. Doctor reads `hooks/list` (app-server) for a hook that is
   `modified` or `untrusted` and says so.
3. `hub-event` for Codex: `handle()` returns at once on a platform run (`HUB_LLM_POINT` set), since Codex's platform runs
   can't skip user-level hooks (M5); records keyed by Codex's session id, not the process (M7: all threads share one daemon);
   `record_session` must not drop other records with the same pid for Codex (M7).
4. The rules: the managed block that Claude gets in `CLAUDE.local.md` is injected by the SessionStart hook's
   `additionalContext` for Codex (M2: it reaches the model).
5. `runtime.installed: [claude, codex]` becomes valid (config check accepts a registered name), so dispatch routes letters
   to Codex sessions; `can_wake` is false for Codex (D4), so letters to a Codex session go the way of a runtime with no wake
   port (shown at its next hook; a background fork stays on Claude, the default; then triage).
6. `sessions_codex.py` already reads Codex's sessions for statistics; nothing changes there.
Tests: parse_event/unusable on Codex's hook payloads (fixtures from M2/M7's logged inputs), inject's output shape, the hooks
file and trust entries rendered (to a scratch home, never `~/.codex`), the trust refused when the definition differs,
handle() a no-op under HUB_LLM_POINT, records by session id with two sessions on one pid, runtime.installed with codex in
config check, dispatch routing to a Codex session without waking it.

### Step 4: a sandboxed project's Codex sessions (D2, both layers)

1. Layer 1 (the agent's own sandbox): the platform renders the project's `.codex/config.toml` permission profile from
   `policy.py` (reads denied for credentials, platform state, other projects; writes to its own folders; network off or the
   allowed domains) — Codex's renderer beside Claude's in render-project-settings; `.codex/` protected from the project's
   own writes (the adapter's `control` port: `.codex/config.toml`, `.codex/hooks.json`); the project's trust entry in the
   user's `config.toml` (user-level: installed with the hooks above, owner's yes). Doctor and the status page show a
   sandboxed project whose rendered config is missing or changed.
2. Layer 2 (any agent): `brunna session <project> [--agent <name>]` starts the agent's terminal UI under the project's
   service profile (`bin/sandboxed-run`), with the agent's state folder writable for that run. macOS first; Linux after M10.
3. `CAGED_NEEDS` becomes a "sessions confined" check per runtime (control + confine for Claude; control + the rendered
   profile for Codex), in hub-event and render-project-settings.
4. Measurements first: M8's profile under a TUI session (not only `exec`), and V-M6 for Codex.

### Step 1: platform runs on Codex (runtime-versions step 5)

`distribution` (npm `@openai/codex`, the platform packages' integrity), `conformance_codex.py` (M1, M8, M9's cases, the per-run
tool list V-M1), the store copy and a platform `CODEX_HOME` in the store (`config.toml` with the tool-free flags and the
updater off, `hooks.json` none, `auth.json` a link to the user's (M9), only `sessions/` writable), `agent` + `completion` +
`auth` + `confine` (the permission profile, M8), `MODELS` pinned per point kind (a v1 model, V-M1), section 3a's login rule
for candidates (the linked auth.json only after the credential check says no refresh can fall due), and `modules/codex.yaml`
offering `runtime.default: codex`. D3 (the default runtime) stays the owner's.

### Step 3: status, summary, resume/fork

`session_status` / `session_summary` from the rollout reader shared with `sessions_codex.py`; resume/fork with `codex exec
fork`; wake off (D4) until M11.

### r2 (11-14), after the blind review: three blocking, folded in; order Step 2 → Step 4 layer 1 → Step 1 → Step 4 layer 2 → Step 3

- **B1** `install_lifecycle(ws)` for Codex is a per-project no-op returning False (it is called by `hub-event install` at every
  session start and by render-project-settings every 15 minutes): the user-level hooks and their trust are written only by
  `brunna user-settings --install --runtime codex`, run by the owner. Tested.
- **B2** A Codex session's life: alive from its SessionStart until its SessionEnd, or until its presence is stale (no hook of
  it for 2 hours: a TUI that quits fires no SessionEnd, M7, and the shared daemon's pid lives for days); `thread/loaded/list`
  replaces the staleness cutoff once M11's framing is measured. An ack by a session that is no longer alive frees the letter
  as for any closed session. Tested: TUI quit, daemon alive → the letter goes back after the cutoff.
- **B3** Step 4's layer 2 waits: it needs Step 1's platform `CODEX_HOME` (never a writable `~/.codex`), and a measurement
  of whether a sandboxed TUI's tools run in the shared daemon (then only the client would be confined). Layer 1 alone comes
  after Step 2.
- Step 2's text corrected: a Codex session is **not routed to or forked** (dispatch's candidates need `can_wake`, a fork needs
  `can_resume` of the default runtime); its letters reach it only through its hooks, then triage on Claude. A Codex-only
  instance (`runtime.default: none`) has no dispatch; doctor says so.
- Event map: SessionStart, UserPromptSubmit, PostToolUse, PreCompact → `pre_compact`, SessionEnd → `session_end`; **Stop is
  not mapped** (it fires every turn). `open_session_attempt` isn't mapped (no such tool).
- `current_session` for Codex: `session_process` from `CODEX_THREAD_ID`, taking precedence over a Claude id in the same
  environment (the daemon's environment can carry one, M6/M7). Tested with both ids present.
- `~/.codex` stays denied in `CREDENTIALS` whichever runtimes are installed; `$CODEX_HOME` added through the adapter.
- A sandboxed project's Codex session before Step 4: hub-event refuses service (`CAGED_NEEDS`), and doctor's per-project row
  "which agents' sessions here are confined" ships with Step 2. Tested with `--runtime codex` in a sandboxed folder.
- Doctor keeps "a Codex session with no record" (an untrusted or modified hook is skipped silently, M2); `hooks/list` only once
  its framing is measured.
- The trust hash: computed from Codex's source definition of the canonical form and checked against a scratch home before
  use; the owner's `config.toml` edited by a minimal TOML writer that keeps the rest of the file byte for byte.
- Hook command: `<hub>/bin/py <hub>/bin/hub-event --runtime codex <event>` (no shebang reliance).
- Letters: injected only on SessionStart until UserPromptSubmit's and PostToolUse's `additionalContext` are measured to reach
  the model; marked told only where it reaches. `additionalContext` is developer-role in Codex: letters are framed as data
  (a notice and the `brunna inbox` pointer, never a letter's body), and the rules say to use the MCP tools where the
  workspace-write sandbox blocks `brunna send`.
- **Measure on 0.169 before building** (scratch `CODEX_HOME`s only, never `~/.codex`): M2 (hook payloads, trust key and hash,
  additionalContext per event), M6 (`CODEX_THREAD_ID`), M7 (the daemon as parent, records per thread), PostToolUse for
  code-mode `exec_command` (`tool_name`/`tool_input`), SessionStart after compaction and on resume. The payloads become
  test fixtures.
- Step 1 names its dependency: runtime-versions merge B, step 4 and step 3 first; V-M1, M1 and M9 re-measured on 0.169;
  `MIN_VERSION` at least 0.167. Step 3: forking a user's thread needs the user's `~/.codex` rollout (hooks.json loads there,
  M5), `can_resume` stays the default runtime's, section 5's session-side rule applies.

### Step 2: progress (11-14)

Built as r2 says, measured first on codex-cli 0.169.0 (below, "Measured 11-14"). What each piece became:

- `bin/runtime_codex.py`, registered as `codex`. Ports: `lifecycle` (`parse_event`, `unusable`, `inject` always in the JSON
  form, `deny` empty, `install_lifecycle` / `remove_lifecycle` per-project no-ops returning False) and `sessions` (`alive`,
  `live_sessions` = the hooks' records, `context_size` from the rollout's last `token_count`, `current_session` from
  `CODEX_THREAD_ID`). Every other port absent; `unsupported()` gives doctor the reason for `instructions`, `wake`, `agent`,
  `completion`. Declarations the neutral code reads (documented in `runtime_ports`' header): `EVENTS_MAPPED` (no
  `open_session_attempt`; Stop isn't mapped), `TELLS_ON = ("session_start",)`, `LETTERS_AS_NOTICE`, `RULES_AT_START`,
  `PLATFORM_RUNS_FIRE_HOOKS`, `SESSION_STALE_S = 2 h`, `END_BUDGET_S = 3`, `USER_LEVEL_HOOKS`. `RULES_FILE_NAMES =
  ("AGENTS.md", "AGENTS.override.md")` (a memory branch holds neither).
- Records: `runtime_ports.record_session(stale_s=)` for a shared process: keyed by Codex's session id, no record drops another
  of the same pid or lends it its project, a hook of the same process touches the record, and `running_records` (so
  `open_sessions`, `recorded_sessions`, dispatch's `opened`) counts it only while its last hook is younger than `stale_s`. The
  record's process is the nearest Codex ancestor of the hook (`session_pid`); `codex exec` is headless and gets none:
  `person_session` reads `session_meta.originator` (`codex_exec`) from the rollout the hook input's `transcript_path` names,
  and only when that can't be read the command line (`codex [options] exec|e`, an option's value skipped). A session that
  ends on record leaves `<session>.ended` (`runtime_ports.mark_ended`, kept past the end like `.version`,
  `hubconf.KEPT_SUFFIXES`), for doctor's unrecorded check.
- `current_session` with Claude and Codex installed: the ancestor walk (`session_process` claims a process whose executable,
  argv[0], is `codex`, never one that only names it in an argument): a Codex process is nearer than any Claude session that started it, so a Claude id the daemon inherited
  doesn't win. Whether `ps` works inside Codex's tool sandbox (the walk needs it) is unmeasured: no model call.
- `hub-event`: `handle()` / `worktree()` return at once under `HUB_LLM_POINT` for a runtime with `PLATFORM_RUNS_FIRE_HOOKS`
  (Claude's behaviour unchanged, leases untouched); the `mail` handler asks `hubbus.mail_text(show=)`: `"none"` at events not
  in `TELLS_ON` (presence recorded, nothing told or held), `"notice"` for `LETTERS_AS_NOTICE` (`hubbus.letters_notice`: ids and
  senders as a wake's notice, the `brunna inbox --project` pointer, never a letter's text; told and held as any SessionStart
  telling); a new `rules` handler (MEMORY_ONLY, after `local_setup`) gives a `RULES_AT_START` runtime the managed block's text
  plus `rules_note()` (use the brunna MCP server's tools where Codex's sandbox can't write the mailbox), the listed rules read
  from the memory checkout a linked worktree shares (`prompts.listed_rules`, now also what `sync-local-setup.sh` uses);
  `memory_sync` at a session's end is detached for a runtime with `END_BUDGET_S`; `CAGED_NEEDS` is
  `runtime_ports.CONFINED_SESSION_PORTS`, so a sandboxed project's Codex session gets nothing (fail closed).
- Dispatch: a Codex session is **neither routed to nor forked** (candidates need `can_wake`; a fork needs the default
  runtime's `can_resume`), and since its hooks tell nothing after its start, a letter doesn't wait for it as for a session
  using the project (`runtime_ports.tells_later`, `hubbus.active_sessions`): a letter reaches it at its next SessionStart or
  through `brunna inbox`, else a wakeable session, else triage on Claude. **One wait is deliberate**: a reply addressed to a
  live Codex session (`to_session`) waits `CONVERSATION_WAIT_MIN` (5 minutes) for it as for any session (dispatch's `asked`),
  since the rules tell a Codex session to run `brunna inbox` itself while it waits for a reply. A Codex session told at its
  start holds a request only `NOTICE_HOLD_MIN` (10 minutes, hubbus) from the telling, not `TAKE_TIMEOUT_MIN` (60): it saw a
  notice, not the letter, and a wakeable Claude session in the project would otherwise wait an hour; its `brunna ack` takes
  the letter as anyone's does. An ack by a Codex session that is no longer live (2 h without a hook) frees the letter as
  any closed session's does. A Codex-only instance (`runtime.default: none`, `runtime.installed: [codex]`) has no dispatch;
  doctor says so and lists Codex's features.
- `runtime.installed: [claude, codex]` passes config check (a registered name). `~/.codex` stays in `policy.CREDENTIALS`;
  `credential_paths()` adds `$CODEX_HOME`.
- Doctor: Codex's column in the feature table (the rules row says they arrive by the session-start hook); per sandboxed
  project, whose sessions are confined (`doctor_confined`); "the platform's Codex hooks aren't installed / some aren't
  trusted" (`user_missing`); "N sessions of the last hour ran without the platform's hooks" (`unrecorded`: a rollout of today
  or yesterday changed within the hour, not `codex exec`'s, its `cwd` in an onboarded project that isn't sandboxed (the
  hooks record no other), with no record, version or end mark); one runtime's failure in these is said and the next is still
  checked; a shell whose `CODEX_HOME` (or Claude Code's `CLAUDE_CONFIG_DIR`) moves the login away from the place the policy
  denies, not named in `credentials.dirs` / `credentials.files` (`credential_override`, `doctor_credential_homes`). The
  per-project confined row shows whenever some installed agent isn't confined, a Codex-only instance included.
  `hooks/list` waits for M11's framing.
- The user-level hooks: `brunna user-settings --install --runtime <name>` (the adapter's `user_install`), `--update` through
  `user_refresh` (only where the owner installed them, so upgrade adds nothing), `user_cleanup` for uninstall. `hooks.json`:
  the platform's handlers rewritten in place, a missing one appended as its own group after the user's, so no hook of the
  user's moves (keys are positional); a link or a shape Codex wouldn't read is refused. Handlers: `<hub>/bin/py
  <hub>/bin/hub-event --runtime codex <event>`, `additionalContextLimit: 0` on the three context events (the rules are about
  16 KB), `timeout: 3` on SessionEnd, matcher `Bash` on PostToolUse. Trust: `trust_entries` hashes each platform handler read
  back from the file at its position (never a user's handler, never Codex's report); a group holding the platform's handler
  and one of the user's is refused (its matcher is theirs too). `set_trust` edits config.toml by lines (a
  `[hooks.state."<key>"]` table's `trusted_hash` replaced or added, a missing table appended, in the file's own line ends;
  a header's key read by `tomllib`, escapes included), checks that the parsed result equals the parsed input with only those
  values set (compared as TOML values: datetimes kept, a NaN equal to itself), and refuses a key set inline or dotted;
  anything unforeseen there or in reading the files is `Refused`, never a traceback. Both files are read and written with
  their line ends as they are (CRLF stays), the temp file is synced before it replaces the original, and each is written
  only if it still holds what the plan read; when hooks.json landed and config.toml didn't, the error says so (the hooks
  are there untrusted, and doctor says it). Rule names read from RULES must be `[a-z0-9-]+` (`prompts.listed_rules`; others
  are left out and said on stderr). **Nothing in the platform runs the install** (a test greps `bin/`); it wasn't run
  against the real `~/.codex`.
- Tests: `tests/test_runtime_codex.py` (r2's list: payloads, inject, the hooks file and trust in a scratch home, a definition
  that isn't the platform's gets no trust, the TOML writer, handle() under `HUB_LLM_POINT`, two sessions on one pid, the 2 h
  cutoff and the freed ack, both ids present, config check, dispatch, the sandboxed refusal, the rules from a worktree, the
  detached end sync, doctor's confined row); `tests/test_runtime_contracts.py` runs the contract on Codex (a fixture's
  `EVENTS_MAPPED` and `USER_LEVEL_HOOKS` decide the parts that differ); fixtures in `tests/fixtures/codex/`.

Departures and known limits:
- The handler command is the bare `<hub>/bin/py <hub>/bin/hub-event …`, without Claude's `[ -x … ] && … || true` guard: a hub
  that moved or was removed makes Codex show the hook as failed, which is the visible failure wanted; `--update` rewrites it.
- The install doesn't re-check the hash against Codex at run time (that needs a model-less Codex run or `hooks/list`): a
  release that changes the canonical form shows as doctor's "sessions of the last hour ran without the platform's hooks".
- A letter named in a SessionStart notice counts as told and is held by that session (10 minutes, `NOTICE_HOLD_MIN`, or
  until the session isn't live), even if the session can't run `brunna inbox` (no MCP server registered); a notice letter
  counts as delivered. `rules_note()` tells a Codex session to run `brunna inbox` itself when it waits for a reply, since nothing
  shows it letters mid-session.
- A Codex session in a linked worktree gets the rules (from the main checkout's memory, `project_git.scope_of`), but no
  `.agent-memory` link beside it: that link is made by Claude Code's user-level worktree hooks (`worktree-memory.sh`). Its
  notes go to the main checkout's `.agent-memory` by path until Codex gets a worktree hook of its own.
- After a session's start, a hook of a shared-process runtime only touches its record (`runtime_ports.touch_record`): no
  process lookup per prompt or tool call. A second SessionStart keeps the project its own record names.
- `context_size` reads 0.165's `token_count` shape; 0.169 wrote none without a model call. It matters only for a fork, which
  a Codex session never gets.
- `$CODEX_HOME` reaches the policy only where the process computing it has the variable; the scheduler's environment
  doesn't. No setting was added for it: doctor says when the shell's points elsewhere, and `credentials.dirs` is the fix.
- An executable path with a space in it isn't recognised as Codex (`session_process` reads argv[0] up to the first space).

What waits on the owner: **his yes** for `brunna user-settings --install --runtime codex` (writes `~/.codex/hooks.json` and
the trust in `~/.codex/config.toml`) and `runtime.installed: [claude, codex]` in brunna.json; and **his login** for what needs
a model call, measured on 0.169 by nobody yet (scratch homes only, no login used): whether UserPromptSubmit's and PostToolUse's
additionalContext is read by the model (until then letters are told only at SessionStart; the commit nudge on PostToolUse is
best-effort), whether PostToolUse fires for code mode's `exec` → `exec_command` and with what `tool_input`, SessionStart's
`source` after a compaction and on resume (so whether the rules come back after compaction), `CODEX_THREAD_ID` in a tool shell
(0.169's source sets it), and whether `ps` runs inside Codex's tool sandbox.

## Found by the second blind review (11-08), taken into the steps

- **Step 1 ships completion and tool-free points only.** Codex has no file-only tools: the shell is how it reads, and nothing yet
  shows a permission profile's `deny` stops `cat` or a write to `.git/config` under `exec -c approval_policy=never` (M1 measured
  `tools: ""` only; `read-only` doesn't stop reads). Points with tools (librarian-state, todo-format, consolidate: file tools only on
  Claude) stay refused on Codex until M8 passes: a profile denying reads and writes, `.git` included, with MCP off, under `exec`.
  **M8 passed 11-08 (0.167.0)**, below; those points open in Step 1 behind the profile.
- **Platform runs and `brunna session` use a platform-owned `CODEX_HOME`**, never the user's: `~/.codex/hooks.json` loads even under
  `--ignore-user-config` (M5), and a writable `~/.codex` lets a sandboxed agent plant a hook and its trust for the owner's next
  unsandboxed session. Its `config.toml` and `hooks.json` are the platform's and read-only to the run; only its `sessions/` is
  writable. How the login is shared with it (auth.json and token refresh, the keyring) is M9, which blocks Step 1's agent runs and D2
  layer 2. **M9 done 11-08**: the platform home's `auth.json` is a link to the user's (below).
- **Step 0 also** makes an unnamed hook's runtime `claude` (every hook written before runtimes were named is Claude's), not the
  default; `runtime_command.run` refuses itself, not only config check.
- **Step 2:** the hooks' `handle()` returns at once on a platform run (`HUB_LLM_POINT`); Claude's platform runs load no hooks at all
  (`--restricted`), which Codex can't do, so this is new, not "as Claude's". The command in the trusted hook is the hub's absolute path
  (a bare `brunna` can be shadowed on PATH, and the Dock-started app's PATH lacks `~/.local/bin`); the platform writes `trusted_hash`
  only after checking the definition under that key is its own, never from `hooks/list`'s current hash; doctor says when the owner's
  edits moved a hook's key (keys are positional). M7 blocks it: `record_session` drops other records with the same pid.
- **Environment:** "minimal, as Claude's" was wrong (Claude's runs pass `os.environ`). Codex's runs get a minimal one that keeps
  `HUB_LLM_POINT`, the confinement markers, the hub's PATH, `HOME` and the platform's `CODEX_HOME`.
- **Credentials:** `~/.codex` stays denied whole to every sandbox (it holds every project's conversations, not only `auth.json`),
  whichever runtimes are installed.
- **Step 4:** the "sessions confined" check replaces `CAGED_NEEDS` in `hub-event` and `render-project-settings`; fallback triage
  stays off on Codex until then. **Linux:** Codex's bwrap can't start where unprivileged user namespaces are refused (Ubuntu 24.04), and
  a nested sandbox is unmeasured (M10), so Linux gets neither layer until measured. `codex queue` uses `--thread=<uuid>` and a fixed
  message (it arrives as the user's words).

Measurements and what they block: M7 (done for an SSH project) → Step 2; M3 (done for the TUI) and M11 (is a client attached) →
Step 3; M8 and M9 (both done 11-08) and the platform's own schemas → Step 1's agent runs, so only the schemas are left, plus
which model each point kind pins and what removes v2's `collaboration` tools (runtime-versions.md, V-M1); Step 1 now follows
runtime-versions.md's steps 1-4 (its step 5); M10 (nested
sandbox) → D2 layer 2; M8 → Step 4.

## Decisions for the owner

- **D1, where the hooks live. Owner, 11-08: user-level.** (a) User-level `~/.codex/hooks.json`, once per machine: one trust review; the hook works out its project
  from the cwd; installed by the owner and kept current by upgrade (`user_refresh`, as Claude's worktree hooks). (b) Project-level
  `<repo>/.codex/hooks.json`, untracked: one review per project, and only in trusted projects. (a) recommended. Platform runs must not
  fire them (M5).
- **D2, a sandboxed project's sessions on Codex. Owner, 11-08, asked back: a user who has only Codex (or another agent) must get the
  sandbox too.** So "Claude-only" isn't the answer; the options are in "Sandboxed sessions on any agent" below.
  The earlier text: The platform confines its own Codex runs (steps 1, 4). A Codex session the owner opens
  in a sandboxed project would load a permission profile from the project's `.codex/config.toml` only if the project is trusted, and the
  platform can't hold it to that the way Claude's settings are enforced. Recommended: sandboxed projects stay Claude-only for sessions;
  doctor says so.
- **D3, the default runtime.** With Claude as the default, Codex sessions are served (hooks, letters, wake) but the platform's
  background runs (judgment points, forks, leases) stay on Claude; leases can't move (`RUNTIME_ARG_POINTS`). A per-project default is
  left out.
- **D4, waking a Codex thread nobody may be watching (M7). Owner, 11-08: (a), never woken.** `codex queue` runs a turn on any
  loaded thread, including one whose TUI quit, with that thread's permissions. The owner's "a woken session keeps its permissions"
  assumed a session someone has open. Until M11 shows a client is attached, a Codex thread is never woken: its letters go the way of
  a runtime with no wake port (a background fork under the platform's settings, then triage). The rejected (b) was to wake it anyway,
  as Claude's, accepting a turn nobody watches. Revisited only when M11 gives a reliable "a client is attached".

## Sandboxed sessions on any agent (for D2)

The rule today (AGENTS.md, tool neutrality): the platform guarantees what *it* starts; a session the owner opens has only the tool's
own protection, which the platform sets up and checks but can't force. With Claude that protection is strong (its settings file holds
an OS sandbox that the session can't turn off, and the platform rewrites it if changed). For other agents, two layers:

1. **The agent's own sandbox, set up by the platform (per adapter).** For Codex: the platform writes the project's `.codex/config.toml`
   with a permission profile rendered from `policy.py` (reads denied for credentials, platform state, other projects; writes to its
   own folders; network off or the allowed domains if Codex can name them), protects `.codex/` from the project's own writes (the
   control map), and, because Codex loads a project's config only when the project is trusted, adds the project's trust entry to the
   user's config (a user-level change: installed once by the owner, kept current by `brunna upgrade`, as the hooks). The owner can still
   start Codex with a flag that overrides it, as with any agent; doctor and the status page show a sandboxed project whose rendered
   config is missing or changed. An agent with no sandbox of its own gets nothing here, and doctor says so.
2. **The platform's own sandbox around the agent (any agent).** `brunna session <project> [--agent <name>]` starts the agent's
   terminal UI under the same OS profile the project's services run in (Seatbelt / Landlock, `bin/sandboxed-run`), so what it may read
   and write doesn't depend on the agent at all. Limits: an app the owner opens from the Dock (Codex's or Claude's desktop app) isn't
   started through it; the OS profiles can't allow outbound traffic by domain the way Claude's proxy does (all or nothing, plus local
   ports), and the agent's own state folder (`~/.codex`) has to be writable for it, which the profile then allows for that run only.

**Owner, 11-08: both.** Recommended was: both — (1) for Codex now, as each adapter's job; (2) as the general way for any agent with no sandbox of its own, and for
owners who want the guarantee rather than the agent's word. Until then doctor names, per sandboxed project, which agents' sessions
there are confined and which aren't.

## Measurements (scratch folders; `--ephemeral` where possible; nothing in `~/.codex/config.toml` changed)

Still to do: M2 (hook trust, project vs user level; user level only with the owner's yes), M3 (`codex queue` to an idle TUI and to
a Desktop-held thread), M5 (`--ignore-user-config` and project `.codex/` config, hooks and MCP servers in `codex exec`), M6 (a tool
shell's environment: session id?), M7 (the process a hook sees with two TUIs and the Desktop app), and `--output-schema` with the
platform's own schemas.

- M1: `codex exec -c features.shell_tool=false … --output-schema s.json` returns schema-shaped output and runs no shell command when
  the prompt asks for one (C1.2, C2.1).
- M2: a project-level hook in an untrusted vs trusted scratch repo: is it skipped, does trust persist per hash, what the review looks
  like non-interactively (C2.2, D1). User-level hook measurement only with the owner's yes (it writes `~/.codex`).
- M3: `codex queue` to a TUI session idle in a terminal starts a turn; to one the Desktop app holds (C2.4).
- M4: the rollout lines this version writes for a short session: where `session_id`, the last reply and turn state are (C2.5).

### Measured 11-08 (codex-cli 0.165.0, scratch folders, nothing in `~/.codex` changed)

- **M1, done.** `codex exec --ephemeral --skip-git-repo-check --sandbox read-only -c approval_policy=never -c features.shell_tool=false
  -c tools.web_search=false --ignore-user-config --output-schema schema.json -o out.txt -`, asked to run `ls /` and `cat /etc/hosts`:
  exit 0, `out.txt` schema-shaped JSON (`{"answer":"no shell","files":[]}`); with `--json` the events are `thread.started`,
  `turn.started`, one `agent_message` item, `turn.completed` with usage, and no `command_execution` or other tool item. The same run
  without `features.shell_tool=false` ran `/bin/zsh -lc 'ls / && cat /etc/hosts'` and read the root folder: **`--sandbox read-only`
  doesn't stop reads; the setting is what makes completion tool-free.** No user MCP servers exist on this machine, so whether
  `--ignore-user-config` drops them is unmeasured (C2.8 needs it).
- **M4, done.** A rollout is `sessions/YYYY/MM/DD/rollout-<local time>-<session uuid>.jsonl`; each line is
  `{timestamp, ordinal, type, payload[, metadata]}`. `session_meta` (first) has `session_id`, `cwd`, `originator`, `source`,
  `cli_version`; a turn runs from `event_msg/task_started` (`turn_id`) to `event_msg/task_complete` (`last_agent_message`,
  `completed_at`, `duration_ms`); the person's text is the `user`-role `response_item` whose
  `internal_chat_message_metadata_passthrough.content_item_kinds` has `user.text` (other `user`-role items carry injected context);
  the final reply is the `assistant` item with `phase: "final_answer"`; `token_usage_record` and `event_msg/token_count` hold usage.
  So session_status (a `task_started` with no `task_complete` after it = busy) and session_summary (`last_agent_message`) are readable
  (C2.5), with the format's own caveat.
- **M2, done.** An unreviewed hook is skipped silently by `codex exec` (only the interactive UI says "hooks need review"); in an
  untrusted project the project's `.codex/` doesn't load at all, and `-c 'projects."<path>".trust_level=…'` on the command line is
  ignored (trust is read only from `config.toml`). **Trust can be written without interaction**: `[hooks.state."<hooks file>:<event in
  snake_case>:<group>:<handler>"] trusted_hash = "sha256:<hex>"` in `config.toml`; the app-server's `hooks/list` gives each hook's key,
  current hash and trust status (managed / untrusted / trusted / modified). So the platform can install its user-level hooks *and*
  their trust in one owner-approved step, and upgrade can rewrite both (`user_refresh`); doctor reads `hooks/list` to see a hook
  that's `modified` or `untrusted`. A user-level SessionStart hook's `additionalContext` reaches the model (it answered the planted word).
- **M5, done.** `--ignore-user-config` skips `config.toml` (so trust and hook trust are gone: no project config, no hooks needing
  review) but **not `~/.codex/hooks.json`**: with trust bypassed, user-level hooks still fired. The platform's own runs therefore
  set `HUB_LLM_POINT` (it reaches the hook's environment, measured) and the hooks do nothing under it, as Claude's do. No flag ignores
  a trusted project's config; a working folder that isn't a trusted path loads none (trust is by absolute path), which covers the
  throwaway copy and memory copies. A project MCP server entry loads in a trusted repo.
- **M6, done.** A tool shell sees `CODEX_THREAD_ID` (= `CODEX_SESSION_ID`), `CODEX_TOOL_CALL_ID`, `CODEX_SANDBOX`: the sessions port's
  `current_session(env)` reads it. **The caller's whole environment reaches the tool shell** (a run started from a Claude session saw
  its `CLAUDE_CODE_*`): the platform's runs pass a minimal environment. Hook stdin has `session_id`, `turn_id`, `transcript_path`,
  `permission_mode`; a hook's parent is the native `codex exec` process.
- **M3, done for the terminal UI.** A TUI session (`codex --sandbox read-only`, 0.165.0) that had answered once and sat idle; from
  outside, `codex queue --thread <its id> --message …` (no `--resume`) printed "Queued message … for thread …", and the session
  started a turn at once: its rollout gained a second `task_started` with `turn_trigger: "queue"`, and the UI showed the message and
  the reply. The TUI was attached to the shared app-server daemon (`~/.codex/packages/app-server-daemon/…/codex app-server --listen
  unix://`, 0.167.0, which the CLI runs itself). So **wake works without any hook**, the opposite of Claude (whose wake is its Stop
  hook). A thread the Desktop app holds is M7, below. Nothing in `~/.codex/config.toml` changed (same sha256).
- **M7, done for an SSH project (11-08).** The Desktop app on the remote machine, with an SSH project on a folder of the hub machine,
  and two TUIs in the same folder on the hub machine; a temporary user-level logging hook on all five events, trusted in
  `config.toml` (both restored afterwards, same sha256).
  - **A Desktop SSH thread runs on the hub machine**: its hooks run there, from the hub's `~/.codex/hooks.json` and trust, with the
    hub folder as `cwd`. So a remote-machine Desktop user is served like a TUI on the hub; nothing runs on the remote machine.
  - **Every thread runs under one process**: the hook's parent was the shared daemon (`codex app-server --listen unix:// …
    --managed-daemon`, started two days earlier) for both Desktop threads and both TUIs. The hook's environment is the daemon's (the
    TUIs' hooks carried the same `SSH_CONNECTION` as the Desktop's). Neither the process nor the environment tells sessions or
    clients apart: records are keyed by the stdin `session_id`, and "alive" can't be a process check (B4 is real).
  - **SessionEnd fires when a Desktop thread is archived** (`reason: "other"`). **Quitting a TUI doesn't end its thread**: it prints
    "Disconnected from this task. Any running work continues" and no SessionEnd fires; the thread stays loaded in the daemon.
  - **`codex queue` starts a turn at once on an idle Desktop thread** (UserPromptSubmit and Stop fired, the reply was the asked word),
    **and on a TUI's thread after the TUI quit**: a turn nobody watches, with that thread's permissions. So a wake to a Codex thread
    is a person's open session only while a client is attached, and nothing measured yet says whether one is.
  - The daemon has `thread/loaded/list` and `thread/read` (status `notLoaded | idle | active | systemError`); neither says whether a
    client is attached. `codex app-server proxy` didn't answer newline-delimited JSON-RPC within 6 s; its framing is unmeasured (M11).
  - The local case (Desktop and TUIs on one machine, without SSH) is unmeasured; doctor's row for it says so until it is.
  - What it changes: Step 2 keys records by session id and ends one on SessionEnd or when the thread is no longer loaded; a TUI that
    quit leaves a record that still looks live. Step 3 can't wake a Codex thread as "the person's open session" until M11 shows a
    client is attached; until then a Codex thread gets what a session of a runtime with no wake port gets (D4, decided 11-08).
- **M9, done (11-08): the platform's own `CODEX_HOME` shares the login by a link.** Codex's source (main) and the binaries checked:
  the file store writes `auth.json` as a temp file renamed over the path, after following a link to its target
  (`resolve_auth_file`, "too many credential symlinks"). That string is in 0.167.0 and not in 0.165.0: on 0.165.0 a refresh would
  replace the link with a file, and refresh tokens rotate (`refresh_token_reused`), so the two homes would log each other out. A copy
  of `auth.json` fails the same way and isn't used. Run with 0.167.0, `env -i` (HOME, a bare PATH, `CODEX_HOME`), a home holding only
  `auth.json -> ~/.codex/auth.json`: `codex login status` said "Logged in using ChatGPT", `codex exec` answered, the user's `auth.json`
  kept its mtime and size (no refresh fell in the window; it was never read by us), and `~/.codex/config.toml` kept its sha256. Codex
  filled the platform home itself (sqlite state, `models_cache.json`, `cache/`, `plugins/`, `skills/`, `.sandbox_migration`).
  - Residual: the lock around a refresh's write is `auth.lock` in each home, so the user's daemon and a platform run aren't locked
    against each other; both reload from disk before refreshing and on a 401, as two processes on one home already do. Not forced
    (a refresh can't be triggered without touching `auth.json`); doctor shows a failed platform login with the login check's alert.
  - The keyring store keys by the home's path, so the link works only with the file store; doctor says so when `config.toml` sets
    `cli_auth_credentials_store` to anything but `file` (this machine sets none: file).
  - Other ways, for other plans: `codex login --with-access-token` into the platform home (Codex access tokens are for ChatGPT Business
    and Enterprise workspaces), or `--with-api-key` (billed per use: `metered_models`). A second interactive login into the platform
    home is not measured and not needed.
- **M8, done (11-08, 0.167.0): a permission profile stops reads and writes of the shell and of `exec`.** Platform home as in M9,
  `default_permissions = "m8"`, `[permissions.m8] extends = ":workspace"`, filesystem: a workspace file `deny`, a folder outside
  `deny`, `<work>/.git` `read`, `<work>/**/*.env` `deny`, `~/.codex` `deny`. Under `codex sandbox -- /bin/sh -c …` (no model):
  reading the denied file, the outside folder (also listing it), `a.env` and `sub/b.env`, `~/.codex/config.toml`, writing
  `.git/config`, a `git commit` (its `index.lock`), writing `~/m8-probe`, creating a new `c.env` and reading it, a symlink to the denied
  file, a hard link to it, renaming it, and Python's `open()` were all "Operation not permitted"; reading and appending to an allowed
  workspace file worked. With a model (`codex exec`, approval never): the commands ran through code mode's `exec` →
  `tools.exec_command`, the denied ones returned exit 1 with "Operation not permitted", the allowed ones ran, no denied text appeared
  in the output or the rollout, and `.git/config` was unchanged. A relative glob (`**/*.env`) stops the run at load with "must be
  absolute", before anything runs.
### Measured 11-14 (codex-cli 0.169.0, for Step 2)

0.169.0 from npm into a scratch npm prefix, scratch `CODEX_HOME`s only, no login (the model call fails with 401 after the
hooks around it ran); `~/.codex` not read. Source read at tag `rust-v0.169.0` (`codex-rs/hooks`, `codex-rs/config`).

- **Hooks without a login, under `codex exec`** (stdin closed; with it open exec waits for "additional input"): SessionStart
  (`source: "startup"`), UserPromptSubmit (`turn_id`, `prompt`) and SessionEnd (`reason: "other"`) fired; Stop didn't (no turn
  completed); PostToolUse and PreCompact need a model call. Payloads in `tests/fixtures/codex/` (paths replaced). Every input
  has `session_id`, `transcript_path`, `cwd`, `hook_event_name`; SessionStart and UserPromptSubmit add `model` and
  `permission_mode`. From the source: PostToolUse for exec_command and the shell tools has `tool_name: "Bash"` and
  `tool_input: {"command": …}`; PreCompact has `trigger`.
- **The hook's process and environment**: its parent was the native `codex` binary (Codex runs the command with `$SHELL -lc`,
  which execs it); the environment was the caller's: `CODEX_HOME`, `HUB_LLM_POINT` when set, and the Claude session's
  `CLAUDE_CODE_SESSION_ID` that started it; no `CODEX_THREAD_ID` (the source injects it into tool shells only).
- **Trust, measured**: key `"<hooks.json's real path>:<event in snake_case>:<group>:<handler>"` (a key naming a linked
  `CODEX_HOME` by its link didn't count; by its target it did); hash `sha256:` + hex sha256 of the compact, key-sorted JSON of
  `{event_name, matcher?, hooks: [{type, command, timeout, async, statusMessage?, additionalContextLimit?}]}` with the timeout
  default filled in (600 s; SessionEnd 1 s, at most 3). With it the hooks fired; with no entry, or one hex digit changed, none
  fired and nothing was said. A matcher (`startup` on SessionStart) is part of the hash and fired. Pinned in `tests/test_runtime_codex.py`
  (`TrustHash`): two handlers printing a marker, one with `additionalContextLimit: 0`, one with matcher `startup`, trusted with
  `sha256:dc10fd98…6184e62` and `sha256:d6efab4b…4a019c212`; both markers landed in the thread. `user_install` itself,
  run against a scratch home with a stand-in hub and a user's own config.toml (comments, another hook's state) and hooks.json
  (a Stop hook), fired SessionStart, UserPromptSubmit and SessionEnd through the stand-in, kept the user's text byte for byte
  ahead of its tables, and a second run changed nothing.
- **additionalContext**: the JSON form on SessionStart and UserPromptSubmit each landed in the thread as a `developer`-role
  message (`content_item_kinds: hooks.additional_context`) before the request; whether the model acts on it on 0.169 needs a
  login. Over 2,500 tokens it is cut to a preview with "Full hook output saved to: <temp file>" unless the handler sets
  `additionalContextLimit: 0` (then whole; measured).
- **M5 changed**: on 0.169 `--ignore-user-config` loads no user-level `hooks.json` either, trusted or not (0.165 fired them).
  The `HUB_LLM_POINT` guard in hub-event stays all the same: it costs nothing and doesn't rest on one version's flag.

- **M1 again on 0.167.0: shell off is no longer tool-free.** Every model in the catalog has `tool_mode: code_mode_only`, which
  `features.*` can't change; its `exec` tool (a JS host with no `fs` import, no `require`, no `fetch`) calls the other tools. With
  `features.shell_tool=false` only, `exec` still reached `apply_patch`, goal tools, the ChatGPT apps connectors (`mcp__codex_apps__*`),
  MCP resources and `web__run`. `tools.web_search=false` and `include_apply_patch_tool=false` removed neither; `web_search="disabled"`
  removed the web tool, `features.apps=false` the connectors, and `default_permissions=":read-only"` made `apply_patch` fail ("writing
  is blocked by read-only sandbox"). The set Step 1 uses is listed there.
