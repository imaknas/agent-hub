# The Codex runtime adapter (`bin/runtime_codex.py`)

Status (11-14): step 0 (fail closed) built 11-08; **Step 2 built 11-14** (`bin/runtime_codex.py`, lifecycle + sessions;
"Step 2: progress (11-14)" below); steps 1, 3, 4 not built. Step 4 layer 1 is planned ("Step 4 layer 1: plan (11-14)"),
on measurements with the owner's login ("Measured 11-14 (with the owner's login)"), and revised after a cold review
("r2 (11-14), after the blind review"). **It can't ship as "confined"**: Codex runs git as the user in the session's
folder, and a repo filter ran from it (B1). r2's steps 1–2 are built (11-14): `.codex` and `.agents` protected in every
sandbox, and a per-version probe whose B1 check fails on 0.167. The path to "confined" is the owner's (D9). **Step 1
is built** on its branch through r2's order step 5 ("Step 1 as built (11-14)"); step 6, the login list and a first
promoted copy, waits for the owner. D11–D14 are provisional as recommended, the owner's. Step 1 follows `runtime-versions.md`
steps 1–4 (it is that doc's step 5). The Codex preset now leaves
`runtime.default` at `none`, not `command` as "Where it stands" below says.
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
(0.169's source sets it), and whether `ps` runs inside Codex's tool sandbox. **Measured 11-14 with his login** (below,
"Measured 11-14 (with the owner's login)"): all of these answered; what they change for Step 2 is listed at the end of that
section. The hooks and `runtime.installed: [claude, codex]` were in place by then.

### Step 4 layer 1: plan (11-14)

Not built. Goal: a sandboxed project's Codex session that the owner opens (TUI, `codex exec`, on the shared daemon or
embedded) runs under a permission profile the platform renders from `policy.py`. The session can't change that profile or
the platform's hooks, and hub-event serves it only while both hold. Built on "Measured 11-14 (with the owner's login)"
below; the items marked *measure first* come before the code that rests on them.

**1. What render-project-settings writes (the Codex renderer).** `runtime_codex.session_config(pol, folder) -> str` (TOML)
holds every Codex key; render-project-settings and `policy.py` stay neutral. The text goes to `<folder>/.codex/config.toml`
for the workspace and each session folder (its worktrees). Every path is absolute and resolved, since a relative one stops
Codex at load (M8). Contents:
- A first-line comment saying the platform manages the file and writes changes back; `default_permissions = "<PROFILE>"` (a
  constant in the adapter); `approval_policy = "never"`; `approvals_reviewer = "user"`. `never` is the counterpart of
  Claude's `allowUnsandboxedCommands: false`. Measured: under it the model can't ask to run outside the sandbox. Under the
  default (on-request) the owner's "yes" runs the command unsandboxed, and "don't ask again" saves a rule.
- `[permissions.<PROFILE>] extends = ":workspace"`. Filesystem entries:
  - `deny`: `other_projects`, `deny_read` (the notes glob, secrets, the bus for an outbox project), `credentials` and
    `credential_files` (`~/.codex` among them, so no session reads another project's rollouts), `platform_state`. This is
    the same list as Claude's `denyRead`.
  - `write`: `pol["write"]` (its runtime folder, `allow_write`) and `<folder>/.git` (or a worktree's git dir and the common
    dir). `.git` must be named `write`: naming only `.git/config` and `.git/hooks` as `read` made the whole `.git`
    read-only, which stopped the outbox and commits (measured).
  - `read`: `<folder>/.git/config` and `<folder>/.git/hooks` (plus a worktree's `config.worktree`); every control name in
    `policy.protect()` (every installed agent's: `.codex`, `.claude/settings*.json`, `.mcp.json`, …); `":tmpdir"` and
    `":slash_tmp"`. The control names are written at the folder's root and, by an absolute glob, at any depth (*measure
    first*: a `read` glob over a subtree, M8 measured only `deny` globs). The temp folders are needed because `:workspace`
    leaves `$TMPDIR` and `/tmp` writable, a channel between projects (measured).
- `[permissions.<PROFILE>.network] enabled = false`. Codex names domains only through its network proxy
  (`features.network_proxy`, *measure first*). Until that is measured, a sandboxed project's Codex sessions have no network,
  stricter than Claude's allowlist, and doctor's confined row says so.
- `[shell_environment_policy] set`: Claude's `session_env` (the tool caches in the repo's git dir), plus `TMPDIR` pointed into
  `<folder>/.git/brunna/tmp`, since the shared temp folders are read-only. *Measure first*: whether a project's file may set it.
- Never `[hooks]`, `features.hooks`, `mcp_servers`, `[projects]` or `hooks.state`. A project's `[features] hooks = false`
  turned off every hook, the platform's user-level ones included (measured).

**2. How it is written, checked and removed.** A new optional port, `session_settings`, takes `session_files(pol, folder) ->
{relative path: text}`. render-project-settings runs it for each installed runtime that has it. Claude keeps its own
`compose` path in layer 1; moving Claude onto the port is a later cleanup.
- Written with `nofollow.replace`, as Claude's file is. The record is `.state/rendered-settings/<project>.<runtime>.json`
  (path → sha256 of what was written). `.codex/config.toml` goes into `.git/info/exclude` (it names machine paths).
- Drift (the file differs from the record, is missing, or isn't a plain file) is written back, printed, and makes the job
  exit 1, so the status page shows red, as for Claude.
- A foreign control is anything in `.codex/` that the platform didn't write (`hooks.json`, `rules/`, any other entry), or a
  `.codex/config.toml` the repo tracks. It is never deleted. It counts as drift (red), is listed by the adapter's
  `foreign_controls`, and the confined check (5) fails while it is there. A tracked config means the platform writes nothing
  for that project and says why.
- enforce turned off, or Codex no longer in `runtime.installed`: `unapply` removes the rendered file only if it still holds
  what the record says (otherwise it leaves the file and says so), then removes the record. Nothing else in `.codex/` is
  touched.

**3. The control port for Codex.** `control_paths() = [(".codex", True)]`. The whole folder counts: measured, it decides
the profile, the approval policy, whether hooks run at all, MCP servers, and (by Codex's docs) execpolicy `rules/`.
`.agents` is added if Codex's repo skills are confirmed to load from there (*measure first*). `control_root() =
(".codex", ())`, so nothing in it stays writable for the project's services and jobs. `session_folders` and
`foreign_controls` as above. Through `policy.protect()` / `protect_roots()` this one declaration protects `.codex` in
these sandboxes:
- The project's services and jobs get it from the Seatbelt/Landlock profiles (`protect_denies`, `protect_roots`).
- The Codex profile names Claude's control files `read` (1).
- Claude's sandboxed sessions would get `Edit` denies on `**/.codex` from `settings()`, but those hold Claude's file tools
  only. (Corrected in r2, S1: today they get none, since `policy.protect()` skips a runtime without the control port, and
  Codex has none. Nothing protects `.codex` in a sandboxed project now.)

**A gap to close in the same change.** Claude's own sandbox doesn't keep its Bash off `.codex`:
- `settings()` turns protect names into `Edit` rules;
- its `sandbox.filesystem.denyWrite` lists only the entries present in `.claude` (`present_controls`);
- Claude Code's built-in protection covers its own settings only.

So a sandboxed Claude session's Bash could rewrite `.codex/config.toml` (a wider profile, `features.hooks = false`), and
the next Codex session would load it before the 15-minute drift check. Claude's renderer therefore adds every other
installed runtime's control paths, in the workspace and each worktree, to `denyWrite`, with a test. Once that is in, each
agent's sandbox protects every agent's control files.

**4. The project's trust entry in the owner's `~/.codex/config.toml`.** Codex loads a folder's `.codex/config.toml` only when
`[projects."<folder>"] trust_level = "trusted"`. An untrusted folder runs with Codex's defaults, which read everything,
`~/.codex` included (measured). A trust entry for a project is *new* content in a user-level file, not "keeping current
what the owner already put there", so under AGENTS.md it needs the owner's direct yes. Proposal:
- The platform never writes it on its own: not render-project-settings, not a job or hook, not `user_refresh` / `brunna
  upgrade`. A test greps `bin/` for code writing a `[projects.` table, as Step 2 greps for the hooks install.
- **(a)** Codex's own prompt. The owner opens the sandboxed project in Codex once and chooses "Trust and continue"; measured,
  Codex writes the entry itself. This is the owner's act in a session.
- **(b)** `brunna user-settings --install --runtime codex` also lists the sandboxed projects and adds their trust entries,
  saying which. `brunna user-settings --trust <project> --runtime codex` covers a project sandboxed later. Both are run only
  by the owner. `--update` keeps existing entries and adds none. Whether (b) is wanted is the owner's decision (below).
- Before the entry exists (or with `trust_level = "untrusted"`), the confined check fails, so hub-event refuses the session
  and doctor's row says "not confined: the folder isn't trusted in Codex".
- A linked worktree: whether Codex takes the main checkout's trust is *measure first*. If it doesn't, each worktree needs
  the owner's trust as in (a) or (b), and until then it is refused.
- Nested `.codex/` folders are *measure first*: does a `.codex/config.toml` in a subfolder load when Codex starts there,
  and can it redefine the profile? If so, the renderer's `read` entries and the static check (5) cover `.codex` at any
  depth, not only the folder's root.

**5. The "sessions confined" check, replacing `CAGED_NEEDS`.** `runtime_ports.session_confinement(name, project, folder) ->
str | None` returns None when confined, otherwise the reason for people. It comes from an adapter function
`confinement(ws, folder)`, and `CONFINED_SESSION_PORTS` remains as its port part. Callers: `hub-event` `handle()` /
`worktree()`, render-project-settings' `other_lifecycles`, and doctor.
- Claude: the `control` and `confine` ports (today's check) plus `sandbox_rendered(ws)` (the file is the fact, not the
  registry's intention).
- Codex, all of these:
  - the `control` port;
  - the rendered config present and equal to its record;
  - no foreign control in `.codex/`;
  - the folder trusted in `$CODEX_HOME/config.toml` (read with `tomllib`, never written);
  - the platform's user-level hooks installed and trusted (`user_missing`). The `confine` port is Step 1's and isn't needed
    here.
- **Per session, for Codex.** From each hook after the session's first turn, hub-event reads the last `turn_context` in the
  rollout that the hook input's `transcript_path` names. It reads as the user, outside Codex's sandbox, and only paths under
  `$CODEX_HOME/sessions` are accepted. The session counts as confined only with:
  - `active_permission_profile.id == PROFILE`, and the turn's `permission_profile` entries equal to the rendered ones (a
    name alone would pass any profile called that);
  - `approval_policy == "never"`;
  - `sandbox_policy.type` not `danger-full-access`.

  Measured: `--sandbox …` and `--dangerously-bypass-approvals-and-sandbox` remove `active_permission_profile`. The hooks'
  `permission_mode` can't tell the difference (`bypassPermissions` under every exec). A session that fails gets an
  `.unconfined` mark beside its record; its presence is dropped and letters are withheld. It is told once, then gets
  nothing more.
- At SessionStart there is no `turn_context` yet, so only the static check decides. For a sandboxed project the session gets
  the rules at its start, and letters (the notice) from its first prompt on, after the per-session check: `TELLS_ON` gains
  `prompt` and `tool_used` (measured to reach the model). *Measure first*: whether a turn's own `turn_context` is in the
  rollout by its UserPromptSubmit hook. If it isn't, the first telling moves to PostToolUse or the next prompt.
- A refused Codex session gets one notice line at its start rather than nothing: "this session isn't under the platform's
  profile for <project> (<reason>); open it again after …". This departs from Claude's silent refusal. The notice is the
  only way the owner sees why mail and rules are missing.

**6. How hub-event serves a confined Codex session.** As it serves Claude's sandboxed sessions. Codex runs hooks as the
user, outside its tool sandbox (measured, both exec and the daemon), so:
- `session_record`, `mail`, `end_mark` and `wake_address` read and write the bus from outside, as today;
- `memory_pull`, `local_setup` and `nudge` run as the project through `hub-event as-project`;
- `rules` joins `AS_PROJECT`. It reads the project's `RULES`, which the project writes, and today a caged session never
  gets rules (`memory_ok` is false). Claude's sandboxed sessions read them from `CLAUDE.local.md` instead.

The session's own mail follows `bus-sender.md`:
- `brunna send` writes `.git/brunna/outbox/`, which the profile keeps writable (measured);
- `brunna inbox` reads `.git/brunna/inbox.json`;
- the bus is denied (`deny_read`);
- `rules_note()` drops the MCP-server advice for a sandboxed project.

`current_session` inside Codex's sandbox: `ps` is refused there (measured), so the ancestor walk finds nothing. When the
walk can't run and `CODEX_SANDBOX` is set, Codex's `CODEX_THREAD_ID` wins.

**7. Doctor and the status page.**
- `doctor_confined` becomes per project and per installed agent: "confined", or the reason from (5). Reasons include no
  rendered config, changed, a foreign `.codex` entry, not trusted in Codex, hooks not installed or not trusted, and
  "network off for Codex sessions".
- `doctor_control` lists `.codex` with the other control names.
- The status page goes red through render-project-settings' exit code, as for Claude, and lists each rendered record.
- doctor counts "N Codex sessions of the last day ran unconfined in a sandboxed project" from the `.unconfined` marks.

**8. Tests** (scratch `CODEX_HOME` and folders under a temp dir only, never `~/.codex`):
- the renderer from a fixture policy, parsed back with `tomllib`: paths absolute, the protect names, the temp folders read,
  `.git` write with `config`/`hooks` read, no hooks / features / MCP / projects keys;
- drift detected and written back, the record kept, `unapply` only when unchanged, a foreign `.codex/hooks.json` gives drift
  and a refusal but is never deleted, a tracked config refused;
- the trust read from scratch config files: trusted, untrusted, missing, and the form the TUI writes;
- the confined check's matrix, and hub-event's refusal and notice;
- the per-session check with `turn_context` fixtures from these measurements (the profile, `--sandbox danger-full-access`,
  `--sandbox read-only`, the bypass flag);
- `rules` as the project;
- `current_session` with `ps` refused;
- `policy.protect()` with `.codex`, Claude's deny rules covering it, and the Seatbelt service profile denying it;
- a grep that nothing in `bin/` writes trust.

A conformance probe (`codex sandbox -- <canaries>` in a scratch trusted folder holding a rendered config; the B column
below; no model) runs on each new Codex version. The `.git` trap and the temp folders are version behaviour. It belongs in
Step 1's `conformance_codex.py`, or runs earlier as a probe of its own.

**9. What layer 1 does not guarantee.**
- **The owner's own choices.** The owner can always override: `--sandbox …`, `--dangerously-bypass-approvals-and-sandbox`
  (both measured), `/permissions` → Full Access, or switching a hook off in `/hooks`; presumably `-c default_permissions=…`
  too (not measured). The platform only notices (5) and stops serving that session.
- **An untrusted open.** Codex's defaults read everything, the profile isn't loaded, and the platform refuses service but
  can't stop the session.
- **The Desktop app.** Not measured locally; its worktrees live under `~/.codex`, which the profile denies.
- **Reads are denied by path only.** The Keychain isn't a path (as with Claude), and Codex's profile has no command deny
  list; execpolicy rules could add one later.
- **The shared daemon updates itself.** It runs every session's tools and hooks, and it updates itself hourly through its
  own channel (it ran 0.169 while the npm copy was 0.167). The version the platform's conformance checks doesn't decide
  what runs. Fixing that is Step 1's store and later work.
- **No network**, until the proxy is measured.
- **Linux gets nothing** (M10).

**Order:**
1. The *measure first* items: the `read` glob, `shell_environment_policy` from a project file, worktree trust,
   `turn_context` timing, the network proxy, `.agents`.
2. Then the control port, the renderer and render-project-settings.
3. Then the confined check and hub-event.
4. Then doctor and the status page.
5. Then the docs.

Each step ships with its tests.

**For the owner to decide:**
- **D5, trust entries.** Option (a), Codex's own prompt only, or also (b), the owner-run `user-settings --install` /
  `--trust`? (a) alone keeps the platform out of the owner's file entirely. Recommended: both, with (b) only ever run by the
  owner.
- **D6, a session the owner overrode by flag.** Stop serving it (recommended: fail closed, as for any unconfined session),
  or serve it with a warning?
- **D7, the approval policy.** `approval_policy = "never"` means no escalation even with the owner's yes (as Claude's
  setting). The alternative is on-request, where the owner approves each escalation. Recommended: never.
- **D8, the network.** No network for Codex sessions in sandboxed projects until the proxy is measured. Recommended: accept;
  measure it first in the build.

#### r2 (11-14), after the blind review

A cold review of the plan above found two blocking points (B1, B2), thirteen should-fix items (S1–S13) and some nits. The
measurements it asked for were taken the same day; they are under "Measured 11-14 for r2", at the end of this doc. Where
r2 and the plan above disagree, r2 holds.

**The owner's decisions (11-14, directly in the session):**
- **D5:** (b) recommended, and (a) checked: as revised under S2.
- **D6:** a session the owner overrode by flag is not served.
- **D7:** `approval_policy = "never"`.
- **D8, reversed: network on.** In the owner's words, a Codex session in a sandboxed project without network is about half
  useless. Where Codex's sandbox proxy can name domains (`features.network_proxy`, `network.domains`), the profile gets the
  project's allowed domains (the policy's `network`, as Claude's allowlist); that is measured before steps 3–5. Where it
  can't, the profile gets full network, and doctor says plainly that this is wider than Claude's allowlist.
- **Measure first for steps 3–5, from D8:** with the network on under the rendered profile, can a tool shell reach the
  daemon's unix socket (`codex queue`, a raw connect)? It was measured only with the network off, where both were refused.
  If it can, the profile denies the socket's folder (`/tmp/codex-daemon-<uid>/`, and whatever `~/.codex/app-server-control/`
  links to), or the session could drive the daemon as the owner.
- D9 stays open. D10 is provisional: steps 1–2 are built (below), steps 3–5 wait for D9.

**B1 decides the outcome: layer 1 does not ship as "confined".**
- Codex runs git itself in the session's folder, as the user and outside its tool sandbox. Measured from `codex exec`
  (0.167) and from the shared daemon (0.169) through a logging `git` first on PATH:
  - the calls are `rev-parse`, `remote get-url` / `remote -v`, `config --get core.fsmonitor` and `status --porcelain`, at a
    session's start and around each turn, and `/diff`;
  - each runs with `-c safe.bareRepository=explicit -c core.hooksPath=/dev/null -c core.fsmonitor=false` and
    `GIT_OPTIONAL_LOCKS=0`.
- So fsmonitor and hooks are neutralised, but **filters are not**. A clean filter named in the repo's `.git/config` (with
  `.gitattributes` selecting a file whose stat changed) **ran as the user, unsandboxed**, from `git status --porcelain`.
  This happened under both exec and the daemon; the canary's parent was `codex app-server`. `diff.external`, `gpg.program`
  and the hooks didn't run.
- Codex has no measured switch for its own git. The binaries hold no "git off" setting; `features.ghost_snapshot` is a
  different thing. Claude's `NO_FOLDER_GIT` has no counterpart here.
- What can make git read a filter: `.git/config`, and every other config source git follows from the folder. A profile
  can make `.git/config` and `.git/hooks` read-only to the Codex session itself. But measured under the rendered profile,
  the session can still:
  - write `.git/worktrees/<n>/config.worktree` and `commondir`;
  - write `.git/modules/<x>/config` (submodule recursion);
  - write `.git/info/attributes`;
  - make a linked worktree inside the project with `git worktree add`.
- More importantly, a sandboxed project's Claude sessions and services may write their own `.git` today, by design
  (runtime-versions.md r3; projects-and-repos.md, "Platform git in project repos"). The platform's own rule is that it never
  runs git as the user in a project's repo (`project_git`). Codex breaks that rule for every session the owner opens in a
  sandboxed project, and nothing in Codex's profile can prevent it.
- So the confined check (5) can't pass for Codex while this holds. It returns "not confined: Codex runs git in the folder
  as you, and the folder's git config is the project's to write". Doctor says the same, and hub-event goes on refusing a
  sandboxed project's Codex sessions as it does today.
- Ways to "confined", for the owner to choose (D9 below):
  - **(i)** Layer 2 with `--no-daemon`. Codex's CLI has the flag, so the TUI and its git run inside the platform's OS
    sandbox as the project. Untested: M10, and whether `--no-daemon` keeps every thread, MCP server and git call in that
    process.
  - **(ii)** A platform-wide fence on a sandboxed project's git config sources, for every writer (sessions of any agent,
    services, jobs). That means `.git/config`, `config.worktree`, `worktrees/*`, `modules/*/config`, `info/`, and the
    gitdir pointers. It conflicts with the decision that sessions write their own `.git` (worktrees, submodules), and
    projects-and-repos.md calls such a fence cosmetic within a shared repo.
  - **(iii)** Wait for Codex to offer a switch. The conformance probe below watches for it.
  - Rejected: a `git` shim first on Codex's PATH. The daemon's PATH is fixed when it starts (by the Desktop app or a
    terminal), and the platform doesn't own it.
- What layer 1 is worth building anyway, as hardening rather than a guarantee: S1/S3 (`.codex` protected in every sandbox,
  which is a hole today whether or not Codex is used), the renderer (Codex's own tools fenced), the checks and doctor rows
  that say why it isn't confined, and the version gate. hub-event keeps refusing.

**B2, canaries through `apply_patch` and the other targets** (measured; profile B plus `deny` globs):
- `apply_patch` obeys the profile: root `.codex/`, `.git/config` and `.git/hooks/` were refused with "rejected as outside
  the project", and an allowed file was written.
- A new nested `sub/.codex/config.toml` **was written** under the root-only entries. **It loads**: a session started in
  `sub/` got the nested profile (here a wide one) in place of the root's. It read the denied folder and `~/.codex`, wrote
  `$TMPDIR` and wrote the root's `.codex/`.
- `read` entries can't be globs ("only supports `deny` access"). `deny` globs `<ws>/**/.codex` and `<ws>/**/.codex/**` did
  refuse creating a nested `.codex` by `mkdir`, `cp -r`, `mv` and a symlink, and they cover the root's `.codex` too (`**`
  matches no folder). So the renderer writes those two `deny` globs. The root's exact `read` entry goes; the tool can't read
  `.codex` then, which nothing needs.
- `git worktree add wt` from inside the session: allowed. `wt/.codex` was refused by the glob. Writable:
  `.git/worktrees/wt/commondir` and `config.worktree`, `.git/modules/x/config`, `.git/info/attributes`, new files in `.git`.
  `mv .git g` and writing a `.git` file were refused (the read-only entries inside `.git` hold the folder in place). A
  linked worktree's own `.git` file inside the project is writable unless named. All of this matters only through B1, and
  goes to the residual list.
- S4 (linked worktrees): a session folder that is a linked worktree gets its own entries for its `.git` file (`read`) and
  for the common dir's `config`, `hooks` and `worktrees/<n>/` (`read`). Still not enough while B1 holds.

**The other measurements** (S13, S9):
- **MCP servers.** The daemon starts a project's MCP server once per thread (two threads gave two processes), in that
  thread's own folder (`cwd` = the session's folder, `sub/` for one started there), outside the profile (`ps` works, no
  `CODEX_SANDBOX`), with no `CODEX_THREAD_ID`. User-level servers weren't added (that would change the owner's file); the
  same spawner is assumed. So `mcp_server.py`'s project from its working directory is right, and a sandboxed project's
  server uses its outbox and view (it already does). But every MCP server runs with the user's authority, beside the
  profile (S8).
- **The daemon's socket** (`/tmp/codex-daemon-<uid>/<hash>`, linked from `~/.codex/app-server-control/`): from a tool shell
  under the rendered profile, listing the folder and connecting by its full path were both "Operation not permitted", and
  `codex queue` couldn't read its config. Outside the sandbox the same connect worked. Measured with the network off;
  with the network on it is unmeasured.
- **Collaboration sub-threads.** A `spawn_agent` sub-thread (rollout `thread_source: "subagent"`, `parent_thread_id`) ran
  under the same `active_permission_profile`, and its canaries were refused as the parent's were.
- **`.agents`.** `.agents/hooks.json` and `[[hooks.*]]` / `[mcp_servers.*]` in `.agents/config.toml` didn't load (`/hooks`
  and `/mcp` showed none). `.agents/skills/<n>/SKILL.md` **is** loaded as a skill into the model's instructions. So
  `.agents` is like Claude's `.claude/skills`: a control path (tree), protected the same way, `deny` globs included.
- **`brunna send` / `brunna inbox`.** Under the rendered profile the CLI starts. In an unregistered folder `brunna inbox`
  tries the bus and fails with a raw `sqlite3.OperationalError` traceback (the bus is unwritable; a clean message would be
  better). The end-to-end path through a registered sandboxed project's outbox and view wasn't measured: no such project
  could be used.
- **The version** in `session_meta.cli_version` is the version of the process that created the thread: `0.169.0` for TUI
  threads on the daemon behind a `0.167.0` client (`originator: "codex-tui"`, `source: "vscode"`), `0.167.0` for exec. A
  resumed rollout gets no new `session_meta`, so a daemon that updated itself since isn't seen there (review B1): the
  running process's version is read first (as built, below).

**The should-fix items, and how each is folded in:**
- **S1.** Today no sandbox protects `.codex` (the correction in section 3). The fix comes first:
  - the Codex adapter's `control` port (`.codex` and `.agents`, both trees);
  - a Bash canary in Claude's control check (`conformance_claude`): `sh -c` writes to `.codex/config.toml`, to a new
    `sub/.codex/`, and to `.codex/` in a new worktree, all expected refused;
  - `denyWrite` entries only as a second layer.
- **S2 + D5.** No sandboxed project gets a trust entry until the control port and the renderer are deployed.
  - `brunna user-settings --trust <project> --runtime codex` refuses unless the project's `.codex/` holds exactly the
    recorded file and nothing else.
  - Doctor flags any trust entry for a sandboxed project's folder (or a folder inside one) whose `.codex/` isn't exactly
    the record. This also catches one added through Codex's own prompt, which the platform can't prevent.
  - The confined check requires `.codex/` to equal the record at every hook, not only at start.
  - **D5 now reads: (b) recommended; (a) can't be stopped, so it is checked.**
- **S3.** `.codex` and `.agents` are protected in every sandbox whichever runtimes are installed, as `~/.codex` stays in
  `CREDENTIALS`. `policy.protect()` takes them from a list of known agents' control names (each declared by its adapter,
  installed or not), not only from installed runtimes. `unapply` never removes the rendered file while a trust entry for
  that folder remains; it says so instead.
- **S4.** Worktree entries, as under B2.
- **S5.** `rules` in `AS_PROJECT` needs the runtime: `_as_project` passes `--runtime <name>` through to `hub-event
  as-project`, so the inner run loads the right adapter (and `rules_note()`).
- **S6.** `_memory_sync_as_project` at a sandboxed session's end is detached for a runtime with `END_BUDGET_S`, as the
  plain `memory_sync` already is.
- **S7.** The per-session check reads only the `turn_context` whose `turn_id` is the hook's own. If that isn't in the rollout
  yet, the hook tells nothing and records nothing for that turn.
- **S8.** The renderer also writes `web_search = "disabled"` and `[features] apps = false` (the network itself is D8's). Doctor's row says plainly that
  user-level MCP servers (the brunna MCP server included) run outside the profile, with the user's authority. "Network off"
  is about the session's tools only.
- **S9.** A version gate:
  - `session_version` for Codex is the running daemon's (or exec's) version, read from its process's files, not from the
    client's; the rollout's `session_meta.cli_version` (the thread's creator) only when that can't be read, marked so
    (corrected after the code review, B1);
  - the conformance probe (`codex sandbox` canaries, no model, B1's filter canary included) runs in Order step 2 for every
    version the store checks;
  - in a sandboxed project, a version without a passing probe counts as not confined.
  - The daemon's self-updates go through no store, so a daemon version without a recorded pass is unconfined until the
    probe has run on it.
- **S10.** Every other installed runtime's home is denied whole, declared by its adapter (`~/.claude` from Claude's, as
  `~/.codex` is from Codex's); today a confined Codex session could read `~/.claude/projects/**`. Same as `CREDENTIALS`.
- **S11.** A `.codex/config.toml` found with no record is foreign. It is never overwritten; it counts as drift and the
  project gets no rendered file until the owner removes it.
- **S12.**
  - `turn_context` parsing (profile id, entries, approval, sandbox type) and the `CODEX_SANDBOX` / `CODEX_THREAD_ID`
    precedence are adapter functions; hub-event and `runtime_ports` call them neutrally.
  - `tests/test_runtime_boundary.py`'s ratchet gains `\.codex\b`, `CODEX_[A-Z]+`, `turn_context` and `\[permissions\.`,
    and hub-event and render-project-settings join `NEUTRAL` where they can.
  - The facts read from a project's repo (is `.codex/config.toml` tracked, the worktrees' git dirs) go through
    `project_git` as the project.
- **S13.** The measurements above.
- **Nits, each taken:**
  - `unrecorded()` covers sandboxed folders too, counting them as "unconfined" rather than "no hooks".
  - Presence is recorded only after a passing check. SessionStart `mail` tells a sandboxed session nothing, the
    open-questions line included.
  - `:tmpdir` read-only holds Codex's tools only; the project's services can still write the shared temp folders (their
    own profile's business).
  - One name for the renderer port: `session_settings` with `session_files(pol, folder)`.
  - On Linux the confined check returns "not confined" (no Codex layer measured, M10).
  - `user_cleanup` removes the trust entries the platform wrote (listed in its record), and only those.

**r2's order of work** (each step with its tests, nothing marked confined until B1 is resolved):
1. S1 and S3: the Codex control port (`.codex`, `.agents`) protected in every sandbox, plus the Bash canary in Claude's
   control check. This closes today's hole and doesn't wait for anything else.
2. S9: the version gate and the no-model conformance probe, B1's filter canary included (today it fails, which is what
   keeps "confined" off).
3. The renderer as hardening: deny globs, `.git` entries, the temp folders, `web_search` and apps off, `approval_policy =
   never`. render-project-settings with S11's foreign rule and S3's no-removal-while-trusted rule.
4. The confined check (static plus per turn, S7, S12), whose answer while B1 holds is "not confined", and the hub-event
   pieces that would serve a confined session (S5, S6, presence after the check), tested but reached only by a passing
   check.
5. Doctor and status rows, trust checks (S2), `user-settings --trust` (refusing as S2 says), and `user_cleanup`.
6. Then the owner's D9 path to "confined".

**For the owner to decide (new):**
- **D9, the path to a confined Codex session in a sandboxed project.** Option (i), layer 2 with `--no-daemon` (recommended:
  it puts Codex's own git inside the OS sandbox and depends on no agent setting); option (ii), a platform-wide fence on git
  config sources; or option (iii), wait for Codex.
- **D10, build steps 3–5 now as hardening, or stop after steps 1–2 until D9?** Recommended: steps 1–2 now (they close real
  holes), and steps 3–5 with D9's path. Provisionally taken: steps 1–2 are built.

**Steps 1–2 as built (11-14), and after two cold code reviews of them (the same day).**
- **Step 1 (S1, S3).**
  - `runtime_codex` has the `control` port: `CONTROL = (".codex", tree), (".agents", tree)`; `control_root() = (".codex",
    ())`; `session_folders` (the folder itself). `foreign_controls` walks the folder for `.codex` and `.agents` at any depth,
    in every spelling the volume folds to them, without following links and past `.git`, `node_modules` and virtualenvs; the
    top `.codex`'s entries are named one by one (review S5).
  - Adapters that declare `ALWAYS_PROTECTED` (Codex and Claude) are protected whatever is installed
    (`runtime_ports.protected_runtimes`; an adapter that can't be loaded is left out and said on stderr, review N2).
    `policy.protect()` and `protect_roots()` read that list, so:
    - **Claude's sessions.** Edit denies on `**/.codex` and `**/.agents`, which V-M9 measured the sandbox takes in for Bash:
      - under the workspace;
      - under every other place a session may write (its runtime folder, `allow_write`, the temp area as given and resolved,
        and the renderer's own temp folder), so that a tree staged there and moved in carries none (review A-B2);
      - in each spelling the volume folds to the name (`.agentſ`, review A-C1).

      A `denyWrite` on each folder at the top of every session folder is the second layer.
    - **Services and jobs.** The Seatbelt profile denies writing the control names wherever they are made, not only under
      the workspace: Seatbelt checks a rename at its two ends only, so a name made in the temp area and moved in nested used
      to land (review A-B2, the reviewer's probe). Codex's tool folder is one rule with nothing writable.
    - **The confined runs** get the same rules.
    - **Linux:** Landlock enforces none of this (it can only allow, and the project's folder is writable). doctor's control
      row says so, as for Claude's names (review N1).
  - `user_control_paths`: what an agent loads for every session from the user's home (Codex's `~/.agents`, Claude's own
    user-level list). It goes into every sandbox's `deny_write` (`runtime_ports.user_controls`; review S1).
  - `~/.codex` moved from policy's `CREDENTIALS` to Codex's `credential_paths`, which is asked for every protected runtime:
    still denied whatever is installed, and the neutral files carry no Codex syntax (review N3).
  - **Tracked control files (review S3).**
    - A project that tracks files under `.codex/` or `.agents/` (at any depth) can no longer change them from inside its
      sandbox.
    - merge-branch's up-front check holds a merge that would change one (`policy.protected_changes` with
      `policy.is_control`, now test-runner's too; review S2).
    - doctor names them per sandboxed project, read as the project (project-git-job `tracked`).
    - render-project-settings reports every protected runtime's `foreign_controls` as drift, so such a project's status row
      stays red until the files go. That is meant: they are what its sessions would load.
  - **Claude's `control` check: nine canaries for other agents' control folders.**
    - `.codex/canary` and `.agents/canary`, through Edit and Bash.
    - `sub/.codex/canary` and `sub/.agents/canary` (Bash).
    - `.codex` in a worktree made after the settings were rendered, so no second-layer entry names it (Bash; review S4).
    - A tree built in `$TMPDIR` and moved into the project (review A-B2).
    - `.agentſ/canary` (Write).

    Each has its positive control.
    - Every Bash canary makes its folder first (`mkdir -p … && printf …`). It counts as refused only when the Bash result
      shows the sandbox's refusal ("Operation not permitted"), never because a file is absent (review A-B1: on the real
      2.1.323 the earlier canaries "tested nothing").
    - The stand-in tool behaves like the tool:
      - Write makes folders, Bash only what its command makes;
      - a rename is checked at its two ends;
      - the rules don't fold the long s.
    - **Run against the real Claude 2.1.323 (11-14, `--only control --report`): pass**, all 18 canaries refused through
      the rendered settings and written with their rules left out, the staged move and `.agentſ` included;
      confine-sandbox passes too. The first run found the positive controls testing nothing for `.agents`: Claude's
      sandbox folds the long s in the rules merged into it, so `Edit(…/.agentſ)` and the `.agentſ` `denyWrite` entry
      refused a Bash write to `.agents` with the literal rules left out. The controls now leave out what covers the
      target as the sandbox reads it (case-folded); the file tools' matching is unchanged.
  - Doctor's per-project row names each unconfined agent's reason (`runtime_ports.unconfined_reason`). Codex's comes from the
    newest recorded probe once there is one, else from what was measured by hand (review N4).
- **Step 2 (S9).**
  - **`bin/conformance_codex.py`: three checks, all session-side.** No model, no login, a `CODEX_HOME` of the world's:
    - **session-folder-git** (safety).
      - Four drivers, one per config source git reads in a repo's folder: the repo's config, a file it includes,
        `config.worktree` (`extensions.worktreeConfig`), and a `process` driver. Each file a driver is chosen for has its
        timestamp moved with its content as staged.
      - `codex debug prompt-input` runs Codex's own `git status`. It also opens an unauthenticated connection to the
        model provider, which carries nothing of the user's.
      - A logging `git` (not exec'ing the real one) records each call's folder, arguments, exit code and completion.
      - It **passes** only when a `status` (the subcommand word, after git's global options) ran to the end in the
        project's folder and no driver ran. It **errors** when no such status ran, and **fails** naming the drivers that
        ran (review B2).
    - **control-globs** (safety). `codex sandbox -P` with `runtime_codex.profile_control_entries` (the deny globs, written
      as TOML strings) refuses six probes, each run on its own with its folders made by its command:
      - `.codex` at the top and nested;
      - an `.agents` skill;
      - a folder moved to the top `.codex`;
      - a folder moved to a nested `.agents`;
      - a link named `.codex`.

      An allowed write in the same run proves the command ran. With a profile lacking the entries every probe lands;
      otherwise it errors.
    - **lifecycle-hooks**: not checkable (it needs a model call).
  - **Run against 0.167.0 in a scratch world:** session-folder-git **fails**, all four drivers ran; control-globs
    **passes**, all six probes refused and landing without the entries.
  - **`conformance.py run`** takes a runtime with no distribution port that says its version and has only session-side
    checks (`versioned`). Its result goes to `<version>.session.json` (`record_any`), and a record that fails is said, not
    a traceback.
    - The binary is PATH's `codex` unless `--binary` names another. To probe the daemon's version, point `--binary` at the
      daemon's package (`<codex home>/packages/app-server-daemon/releases/<version>-<target>/bin/codex`). The recorded
      digest is of the file `--binary` names (for PATH's npm `codex`, the shim).
  - **What reads the result.**
    - `runtime_ports.gated` reads it, but only fails gate a session. A sandboxed project's Codex session is refused today
      anyway (no confine port), so the gate isn't reachable end to end yet.
    - "A version without a passing probe isn't confined" belongs to step 4's confined check (steps 3–5), not to `gated`.
  - **Nothing runs the probe on a schedule yet:** `session_versions.py` serves runtimes with the distribution port (Step 1).
  - **A Codex session's version is the running process's.**
    - `session_pid` gives the daemon under the Desktop app and the TUIs, the native `codex` under exec. `version_at` reads
      the daemon's own release folder or nvm's package, so a daemon that updated itself under a resumed thread is seen
      (review B1).
    - Only when that can't be read, the rollout's `session_meta.cli_version`, marked build `rollout`. It is read only
      from a rollout in the user's Codex home (both sides resolved, so a linked sessions folder works and
      `sessions/../..` doesn't), whose session id is the hook's, with a first line of up to 16 MiB.
    - One version grammar for all of it (`runtime_ports.checkable_version`): a pre-release isn't recorded.
    - hub-event hands the hook's input to a `session_version` that takes it, in a worktree's hooks too.
  - Tests: `tests/test_conformance_codex.py`, with a stand-in `codex` whose sandbox is Seatbelt (macOS).
- **S12's ratchet.**
  - `tests/test_runtime_boundary.py` counts Codex's syntax too (`\.codex`, `CODEX_*`, `turn_context`, `[permissions.`),
    with `runtime_codex.py` and `modules/codex.yaml` exempt.
  - The neutral files are checked against Codex's patterns as well; hub-event and render-project-settings.py are among
    them.
  - The baseline went down: policy.py carries none now.
- **S10 is left for steps 3–5.** It only matters once a Codex session has a rendered profile (Claude's sessions already
  deny `~/.codex`, now through Codex's `credential_paths`). The renderer then denies each other installed runtime's home,
  declared by its adapter. Claude's declares only its credential file today, so it gains a home declaration.
- **Left for steps 3–5:**
  - the renderer and render-project-settings (S3's no-removal-while-trusted, S11);
  - the static and per-turn confined check (S7, and "no passing probe = not confined") and the hub-event pieces (S5, S6);
  - doctor's trust rows, `user-settings --trust` and `user_cleanup` (S2);
  - D8's network (the proxy, the socket);
  - S10;
  - running the probe on a schedule.

### Step 1: plan (11-14)

Not built. Goal: the platform's own runs (completions, then the agent points that work in a memory copy) can run on
Codex, on a checked copy in runtime-versions.md's store, in a Codex home of the platform's, each run's tools pinned and
checked. It is runtime-versions.md's step 5; that doc's steps 1–4 (the store, `update-runtimes`, `checked_copy`, the
conformance runner, session versions) are on main. Measured for it without a login on 0.169.0 (npm's latest) and 0.167.0:
"Measured 11-14 for Step 1 (no login)" at the end of this doc. What needs a model call is listed under (9).

**What the measurements change in the earlier Step 1 text** (where this plan and the text above disagree, this plan
holds):
- **The tool-free switch is `[agents] enabled = false`**, with `features.multi_agent_v2 = false`. On 0.167 and 0.169 it
  removes the `collaboration` namespace from v2 models (`gpt-6-luna`, the default, included), and the deferred
  `multi_agent_v1` namespace that a v1 model announces in a developer `<tools>` message. `features.multi_agent` is
  deprecated (Codex says so on every run) and isn't written. So no model is pinned to keep a run tool-free. Models are
  still pinned per group, for strength and for the per-run check.
- **No `--ignore-user-config`.** It drops `$CODEX_HOME/config.toml`, which is the platform's own file here, and `codex debug
  prompt-input` doesn't take it, so the check before a run and the run itself would differ.
- **"Only `sessions/` writable" can't hold.** A run writes sqlite state, thread history, `installation_id`,
  `skills/.system`, `tmp/` and locks into its home. So each run gets a fresh home built from a template (2).
- **`confine` isn't a Step 1 port** (1e).
- **`--output-schema` is checked by Codex only for being JSON**; whether the server takes a non-strict schema is a
  login measurement (9). The fallback rule is in (1c).
- **`codex exec` has no updater** and starts no daemon (measured with a newer version published); the pins stay as
  reinforcement, and the digest check is what holds the copy.

**1. The ports `runtime_codex.py` gains.** Codex's syntax stays in the adapter; the neutral files get only the small
additions named here.

*a. distribution* (`latest`, `install`, `version_of`, `pin_env`, `MIN_VERSION`), as Claude's:
- `PACKAGE = "@openai/codex"`. `latest()` is `npm view … version`. `published()` keeps only `X.Y.Z`: the package lists
  about 5,900 versions, most of them platform-suffixed builds (`0.169.0-darwin-arm64`) and alphas.
- `install(version, folder)`: `npm install --prefix <folder> @openai/codex@<version> --save-exact`. It runs in a clean
  environment (no caller's `npm_config_*`, no `OPENAI_*` or `CODEX_*` variables, `HOME` the folder, the store's npm
  cache). The package has no install scripts. Its platform builds are npm aliases in `optionalDependencies`
  (`@openai/codex-darwin-arm64` → `npm:@openai/codex@<version>-darwin-arm64`), and the lockfile carries a sha512
  integrity for the wrapper and for all six of them. Any resolved entry without one refuses the install, as for Claude.
- **It returns the native binary**, `node_modules/@openai/codex-<os>-<arch>/vendor/<triple>/bin/codex`. The path is read
  from `codex-package.json` beside it, whose `version` and `target` must match. It is not `.bin/codex`, which is a node
  shim that sets `CODEX_MANAGED_BY_NPM` and execs the binary, so its digest would say nothing.
- **The digest covers every executable of the copy**: `bin/codex`, `bin/codex-code-mode-host` (code mode's JS host),
  and `codex-path/rg`. `runtime_store.verify` gains an optional adapter list, `executables(entry)`. The size, mtime and
  real path of each are checked every run, the full sha256 once a day.
- `version_of` exists. `version_at` learns the store's layout (`<runtimes>/codex/<version>/…`), as Claude's did.
- `pin_env(folder)` is `npm_config_prefix` at `<store>/codex/.npm-sink`. The home's config is the `pin_config`:
  `check_for_update_on_startup = false`, `features.daemon_auto_start = false`, `features.in_app_updates = false`.
- **`MIN_VERSION = "0.169.0"`**, Claude's precedent: the version the platform-side measurements were taken on, and the
  one the login list (9) is measured on. 0.167.0 passed the model-free ones too, and M9's link-following is in both.
- `install_global` / `user_copy` (section 8) only with D12's yes.

*b. The copy a run uses.* `checked_copy()`, `_run_copy()` and `binary()` as Claude's: the store's `current`, held and
verified, or `NoCheckedCopy`. **`binary()` loses its PATH fallback.** It is used today by the session-side checks;
`conformance.py run codex --binary <path>` still names any binary for those.

*c. completion* (`complete`, `health`):
- **Where.** A fresh empty folder under `hubconf.private_tmp()`. No `.git` may be above it (checked as files, before the
  run).
- **How.** `exec --strict-config --skip-git-repo-check --json -m <model> -o <file> -`, with the prompt on stdin.
  - `req.system` goes in as `-c developer_instructions=…` (measured: a developer message).
  - The config comes from the run's home (2). One function builds the `-c` list for the run and for its check.
- **Environment.** Minimal, never `os.environ` (M6): `HOME`, `LANG`, a private `TMPDIR`, `CODEX_HOME` (the run's home),
  `HUB_LLM_POINT`, `pin_env`, and a PATH of the system folders. The native binary needs no node.
- **Before the run:** `codex debug prompt-input` with the same flags in the same folder (0.16 s, no model call). Its
  shape is compared with the pinned one for that model:
  - the `additional_tools` tree: `functions.exec`, whose nested tools are `apply_patch`, `list_mcp_resource_templates`,
    `list_mcp_resources`, `read_mcp_resource`, and `clock__curr_time` on GPT-6 models; and `functions.wait`;
  - the kinds of developer messages: the model's base instructions, permissions, and on GPT-6 models the collaboration
    mode; no `<tools>` (deferred namespaces), no skills, no AGENTS.md, no `multi_agent_role`.

  A mismatch refuses the run. In a fresh home `prompt-input` sees the copy's bundled catalog (there is no cached one, and
  it makes no request), so this check holds the binary's side only. The server's side (surface C) is caught by the check
  after the run, or closed by pinning the catalog (4).
- **After the run**, from the rollout in the run's home:
  - `additional_tools` as above;
  - `turn_context`: the model, `multi_agent_version == "disabled"`, `approval_policy == "never"`, `sandbox_policy.type
    == "read-only"`, `active_permission_profile.id == ":read-only"`;
  - `world_state.top_level_tools` keys equal to the namespaces seen.

  A mismatch refuses the result and alerts per point, as Claude's `_tools_alert` does.
- **A model whose list `prompt-input` can't show is refused.** For `gpt-5.5` (no `tool_mode` in the catalog) it shows no
  tool list at all, even with the shell on. Only models where a positive control shows a tool are pinned (4).
- **Output.** The `-o` file. Usage comes from `turn.completed`. A login error is `is_login_error`'s (a 401, "Not logged
  in").
- **Schemas.** `--output-schema` for a schema the version's `completion-schemas` check recorded as taken. Any other
  schema goes into the prompt, as `provider_openai`'s `structured: false` does. `completion.py` validates either way. Two of
  the eleven schemas in `contracts/llm/` are strict (`first-look`, `mailbox-route`).
- `health(conf)`: a checked copy, and the login link (2) in place; no request.

*d. agent* (`run`, `describe`). One table maps a point's declaration to Codex. A field or value it doesn't know refuses
the run with the reason, never dropped (step 0's bug). A test walks `contracts/llm-points.yaml`: every point maps fully
or is refused, and a new field fails it.
- **Where (B1).** Codex runs git as you in the run's folder, so the agent port starts a run only in a folder whose git
  config the platform owns:
  - a memory copy under `hubconf`'s memory-copies root, after `memory_paths.folder_problems` (llm.py's `works_in: memory`
    check; `.gitattributes` and anything else beside the notes is refused there);
  - a folder the run made under `private_tmp`.

  A project's workspace, its worktrees and the throwaway copy are refused ("Codex runs git as you in the folder, and
  its git config is the project's"). `FOLDER_GIT_OFF` stays False, so doctor's "folder git unknown" row stays. A future
  version whose `session-folder-git` passes is a reason to revisit, as Claude's `NO_FOLDER_GIT` was.
- **`tools`:**
  - `""` gives the completion's home and checks.
  - `Read,Write,Edit,Glob,Grep` (any subset) turns the shell on with `-c features.shell_tool=true -c
    features.unified_exec=true`, under a per-run permission profile. Codex has no file-only tools: its shell is how it
    reads.
  - Any other tool name is refused.
- **The per-run profile** (`brunna-run`, all of it in `-c`, measured to arrive whole in `turn_context`). The shell must
  read no wider than the point needs, which is the copy: its prompt says so, and its allow list is `Edit(./…)` only.
  `:read-only` alone reads the whole disk. So:
  - `extends = ":read-only"` (the system's own files stay readable, so a shell starts);
  - `deny` on the user's home folder whole, and on the temp roots (`/tmp`, `/private/tmp`, `/var/folders`), which
    covers every project, the hub, the instance, the memory copies, credentials and the store;
  - `read` on the copy itself, and `write` on each path in `allowed_tools`. The more specific entry wins over the deny
    around it (measured);
  - as a second layer, `deny` for every path the policy denies a sandbox (credentials, other projects, `platform_state`,
    `deny_read`), from the same lists that render Claude's settings;
  - `network.enabled = false`.

  `:minimal` can't be extended ("unsupported built-in"), and a profile with no base can't start `/bin/sh` (measured).
  Measured with `codex sandbox -P`: the copy readable and `STATE.md` writable; `AGENT.md`, a new file in `memory/`, a
  sibling of the copy, its parent folder, `$TMPDIR` and the network refused; `/etc/hosts` readable. Whether the
  model's shell finds anything it needs outside the copy is on the login list (9). If this boundary can't be held, the
  memory-copy points stay off on Codex (they have their `-edits` completions).
- **`allowed_tools`:** only `Edit(./<path>)` and `Edit(./<dir>/**)`, resolved under the folder and never through a link.
  Any other rule is refused.
- **`permission_mode`:** `dontAsk` and `acceptEdits` become `approval_policy = "never"`; anything else is refused.
- **`extra_args`:** `--strict-mcp-config` and `--no-session-persistence` are what the home always does (no MCP servers;
  the rollout goes with the run's home). Anything else is refused.
- **`restricted`** always holds: the folder isn't trusted in the platform's home, so no `.codex/` of it loads (measured).
- **Refused:** `settings: platform`, `allow_from_env`, `extra_allow_env`, `tool_servers`, mail. llm.py refuses most of
  them first (no `confine`, `rule_check`, `tool_servers` or `run_server` port).
- **Per run**, as the completion's, with the shell's nested tools (`exec_command`, `write_stdin`) in the pinned list.
  After the run, `turn_context.permission_profile` must equal the rendered entries.
- **A new optional adapter function, `point_refusal(point, spec) -> str | None`.** `llm_ready.unsupported` and llm.py
  read it, so the librarian takes its `-edits` completion for a part Codex refuses, instead of failing on every item.
  This is what `THROWAWAY_ONLY` does for whole runtimes.

*e. `confine` is left out of Step 1.*
- `runtime_ports.sessions_confined()` and `CAGED_NEEDS` (hub-event, render-project-settings) are `{"control", "confine"}
  <= capabilities`, and Codex already has `control`. Adding `platform_settings` would make hub-event serve a sandboxed
  project's Codex sessions while B1 holds.
- None of the points Step 1 opens needs `confine`: a memory copy's `restricted` is held by the folder check and the
  untrusted folder.
- `confine` comes with Step 4 r2's `confinement()` (item 5). That function replaces the port test in both callers, so an
  adapter's own word decides, and Codex's says "not confined" while B1 holds.

*f. auth* (`auth_env`, `clean_env`, `credentials`, `is_login_error`, `billing`):
- `auth_env("token")` is the run's home with the login link (2). `"subscription"` is the same login: Codex has one
  ChatGPT login, and no connectors run through it.
- `clean_env` drops `OPENAI_API_KEY`, `CODEX_API_KEY` and `CODEX_*`.
- `credentials()`:
  - the user's `auth.json` is present;
  - the user's config uses the file store (`cli_auth_credentials_store` absent or `"file"`; the keyring is keyed by the
    home's path, M9);
  - `auth_mode`, and the age of `last_refresh`.

  The file is read for those fields only; no token is printed or kept.
- `billing`: `subscription` for a ChatGPT login with no API key in the environment; `metered` for an API key (which
  needs the owner's `metered_models`); `unknown` otherwise.

*g.* `unsupported()` loses `agent` and `completion`, and names `confine` (Step 4), `stream`, `tool_servers`,
`rule_check` and `run_server`. Leases and the confined points stay on Claude.

**2. The platform's Codex home.**
- **Template.** `<runtimes>/codex/home/` holds `config.toml` only. It isn't a version folder, so prune leaves it.
  `install` and `update-runtimes` render it from an adapter constant and record its digest.
- **Per run.** Each run gets `<runtimes>/codex/runs/<random>/` (mode 700):
  - `config.toml`, copied after its digest matched the record (else refused: "the platform's Codex config changed after
    it was written");
  - `auth.json`, a link to the user's login file (`$CODEX_HOME` or `~/.codex`, resolved when the home is built);
  - nothing else. Codex fills the rest.

  The home is removed when the run ends, after the rollout is read. A home left by a crash goes after a day, as llm.py's
  run folders do.
- **Why one home per run.**
  - The runs' sqlite files, thread history and rollouts would otherwise build up, in one folder, the text of every
    project the platform reads.
  - Runs at the same time don't share a state database.
  - A home that someone changed can't carry over into the next run.
- **`config.toml`** (accepted under `--strict-config` on 0.167.0 and 0.169.0):
  - top level: `check_for_update_on_startup = false`, `cli_auth_credentials_store = "file"`, `web_search = "disabled"`,
    `default_permissions = ":read-only"`, `approval_policy = "never"`, `project_doc_max_bytes = 0`;
  - `[features]`: `shell_tool`, `unified_exec`, `apps`, `plugins`, `goals`, `multi_agent_v2`, `daemon_auto_start`,
    `in_app_updates`, `view_image`, `sleep_tool`, `memories` and `hooks`, all `false`;
  - `[agents] enabled = false`, `[tools.experimental_request_user_input] enabled = false`;
  - `[skills] include_instructions = false`: the user's `~/.agents/skills` is otherwise listed in a platform run's
    instructions, whatever `CODEX_HOME` is (measured);
  - `[analytics] enabled = false`.

  Never `[hooks]`, `hooks.json`, `[projects]`, `mcp_servers` or a profile wider than the run asks for. Each widening is a
  per-run `-c` (1d), so the file is the tightest form.
- **Why each matters.**
  - With `plugins` on, a run in a fresh home cloned a plugin marketplace's git repo into the home (measured).
  - `features.hooks = false` and the `HUB_LLM_POINT` guard both stand. Measured: a trusted hook in `$HOME/.codex` didn't
    fire for a run whose `CODEX_HOME` was elsewhere, even with `--dangerously-bypass-hook-trust`; its positive control
    (no `CODEX_HOME`) fired.
  - `--strict-config` on every run: a key a new version no longer knows stops the run instead of being ignored. It
    rejected a misspelt `features.shell_tol` (measured).
- **Never passed:** `--trust` (writes trust), `--dangerously-bypass-*`, `--ephemeral` (the rollout is the after-run check),
  `-p` profiles.
- **Never run with a platform home:** `codex login` and `codex logout`. A login through the link rewrote the user's file
  (measured with dummy keys).
- **Admin layers.** `/etc/codex/{config,requirements,managed_config}.toml` and a managed-preferences domain can override
  the home. None exists here. Doctor says when one does, since an administrator can lock the updater setting or the
  sandbox.
- **Denied to every sandbox.** `<runtimes>/codex` joins Codex's `credential_paths()`: the template, the run homes (which
  hold the login link and the rollouts) and the store's copies.
- **The login, M9 and section 3a.**
  - The link is the only way the login is shared.
  - The keyring store would need the same home path, so doctor refuses it (the user's config, read with `tomllib`).
  - Narrowed residual: a write through the link takes `auth.json.lock` beside the target. With that lock held by
    another process, the write waited 5 s and failed (measured). Whether a token refresh takes the same lock is on the
    login list (9).
  - A candidate's model checks get the real link only after its model-free checks pass (`auth-link-write` among them,
    with dummy keys in the world), and only when the credential check says no refresh can fall due during the checks.
    That is `last_refresh` against Codex's refresh interval, read from that version's source (9). Otherwise the checks
    wait for the next night.
- **The login-check job** (`codex/codex-login`, hourly, `bin/check-codex-login.py`, model-free):
  - the user's file and the link target are there;
  - `codex login status` through a fresh platform home exits 0;
  - the file store is in use;
  - `last_refresh` isn't older than a limit.

  A real request is the daily checks' completion (3). A platform run's login error alerts at once, as Claude's do. One
  alert per problem, by a stable id, as `check-claude-login` does.

**3. `conformance_codex.py`: the platform side**, beside the three session-side checks. Each check runs in the runner's
world. Names are shared with Claude's where they mean the same, so `update-runtimes`' `DAILY` (`agent-result`,
`completion-no-tools`, `completion-schemas`) picks them up; `home-flags` joins `DAILY`.
- **`distribution-integrity`** (function): the version; a lockfile integrity for every resolved tarball;
  `codex-package.json` matching the version and target.
- **`home-flags`** (safety, no model):
  - the rendered `config.toml` loads under `--strict-config`;
  - `prompt-input` gives the pinned shape for each model in `MODELS`;
  - positive controls: `-c features.shell_tool=true` shows `exec_command` (measured), and `agents.enabled=true` shows
    `collaboration` on a v2 model; either missing is "tests nothing".
- **`completion-no-tools`** (safety, login): a real completion asked to run a command:
  - its rollout and `turn_context` are as pinned;
  - no `exec` call in the rollout;
  - positive control: the same run with the shell on calls `exec_command`.
- **`completion-schemas`** (function, login): one completion per schema in `contracts/llm/`. Each schema is recorded as
  taken by `--output-schema` or not; one not taken goes into the prompt, and the check passes when that answer validates.
- **`agent-result`** and **`agent-models`** (function, login): `--json` events parse (`thread.started`, `turn.completed`
  usage, the final message, `-o`); each model in `MODELS` is in the catalog the run used and answers.
- **`agent-profile`** (safety, no model). `codex sandbox -P` with the per-run profile rendered for a memory-copy-shaped
  world. Canaries, as M8's:
  - a file beside the copy, a file in the home folder, a credential canary, another project, platform state, the store's
    Codex folder;
  - a write to `AGENT.md`, `RULES`, `.git/config`, a new `memory/` file, `$TMPDIR`;
  - a link and a rename;
  - the network.

  `STATE.md` must stay writable. Each canary has its positive control (the profile without its entry).
- **`auth-link-write`** (safety, no model). A dummy API key logged into the world's "user" home, and a platform-shaped
  home linking it. A write through the link must land in the target and keep the link. Positive control: a home holding
  a copy instead leaves the target unchanged.
- **`auth-login-error`** (function, no model): a run with no login is a login error to `is_login_error` (the 401
  measured).
- **`updater-off`**: not-checkable. `exec` has no update path: 0.167.0, with 0.169.0 published, wrote no `version.json`
  or `packages/`, left the npm sink empty, and kept its digest. The store's digest check is what holds the copy.
- **`lifecycle-hooks`** becomes checkable under section 3a: the platform's hooks installed by `user_install` into the
  world's home, and a marker in `additionalContext` that reaches the model. It needs the login, so it runs only once
  Codex runs platform work (D11); while Codex is only installed for sessions it stays not-checkable.
- `session-folder-git` and `control-globs` stay. `test_conformance_cover` holds every port to a check or a named
  not-checkable entry.

**The promotion rule needs one change.** `conformance.verdict()` fails on any safety fail. `session-folder-git` failed
on 0.167 (the probe) and Codex's git ran a filter on 0.169's daemon (by hand, B1), so no Codex copy would ever be
promoted.
- `Check` gains an optional `holds = "sessions"`, set only on `session-folder-git`. Its fail still gates that version's
  sandboxed sessions (`runtime_ports.gated`).
- Its alert is keyed by check and reason, not by version, as `update-runtimes`' are. Otherwise every self-update of the
  daemon would alert again about the same known failure, which doctor's confined row already names.
- It doesn't hold back the platform's copy, because the agent port starts no run in a folder whose git config the
  platform doesn't own (1d, tested). Promotion reads `verdict(doc, for_promotion=True)`.
- Claude's checks keep the default. The rule is the owner's to confirm (D13).

**`update-runtimes` and `session-versions` for Codex.** Both start on Codex the day it has the distribution port, since
both read `runtime.installed`, which here holds `codex`:
- `session-versions` installs the versions the user's Codex sessions run (the daemon's own updates included) as session
  copies (about 370 MB each, at most 5) and runs the session-side checks: model-free, but for `lifecycle-hooks`.
- `update-runtimes` installs a platform copy, runs every check (the login ones under 3a), promotes, and raises the user's
  npm `codex` (section 8).

So the distribution port ships behind D11: until the owner says otherwise, a runtime only in `runtime.installed` gets
session checks and no platform copy, and no check uses the owner's login. `in_use()` splits into "runs platform work"
(`runtime.default`, `llm.points` pins, providers whose impl is a runtime) and "sessions only".

**4. MODELS, and which points run on Codex first.**
- **`MODELS = {"fast": "gpt-6-luna", "standard": "gpt-6.1-sol", "strong": "gpt-6-astra"}`**, from 0.169's bundled
  catalog ("fast and affordable", "workhorse", "frontier"). All are `code_mode_only` and v2, which `agents.enabled =
  false` handles.
  - `gpt-5.5` (legacy, no `tool_mode`) is never pinned: its tools can't be seen before a run.
  - Whether the live catalog says the same, and whether a ChatGPT plan serves each model, is on the login list.
  - Pinning the catalog itself (`model_catalog_json` = the copy's bundled catalog, written into the template at
    promotion) would take surface C's tool-shape changes out of the platform's runs. It was taken by `prompt-input` with
    no login; whether a logged-in run keeps it over the server's catalog is on the login list. Until then the per-run
    check holds.
- **First, the completion points:** `librarian-state-edits`, `librarian-index-edits`, `librarian-todo-format-edits`,
  `first-look`, `reflect-week`, `mailbox-route`. They run on Codex through `llm.points.<point>.runtime: codex`, a
  provider whose `impl` is `codex`, or `runtime.default: codex`.
- **Then the agent points in a memory copy:** `librarian-state`, `librarian-todo-format`, `consolidate`, behind
  `agent-profile`.
- **Refused on Codex, said by `point_refusal` and doctor:**
  - `librarian-index` (`acceptEdits` with no allow list, in the data folder);
  - `agent-job`, `agent-job-confined`, `agent-job-event`, `project-agent`, `mailbox-escalation` and
    `mailbox-conversation` (they need `confine`, `rule_check`, `tool_servers`, `run_server`, or a project folder);
  - `mailbox-propose` (the throwaway copy, B1);
  - `lease-*` (`stream`);
  - `login-check` (Claude's point; Codex has its job).

**5. `modules/codex.yaml`.**
- **The preset** is for an instance that picks Codex at init (a Codex-only user): `runtime.default: codex`, `runtime.installed:
  [codex]`. On this machine `runtime.default` stays `claude`: D3 is the owner's.
- **`runtime.command` (the `codex exec` template for `mailbox-propose`) leaves the preset.** Codex's own runtime replaces
  it. And if propose's throwaway copy shares the project's git config (a linked worktree; to check), that template's run
  is B1's case today. An instance that has it keeps it until the owner removes it; doctor says what it leaves.
- **Jobs:** `codex-login` (hourly, 2). Secrets: the user's `auth.json` declared by path (no backup: a new login replaces
  it), so `brunna/secrets` checks it's there.
- `description` and the README say what works: Codex sessions served (Step 2), completions and memory-copy judgment
  points on a checked Codex copy, nothing confined in sandboxed projects (B1).

**6. D9 (i), layer 2.** Layer 2 needs a Codex the platform holds and a home that isn't the user's (B3). Step 1 builds
both, so layer 2 adds a config, not a mechanism:
- the store copy, digest-checked, started with the top-level `--no-daemon` (accepted there; `exec` rejects it and needs
  none);
- the home builder takes its config as a parameter. Layer 2 renders a session config: tools on, the project's profile,
  the updater off, which matters there, since the TUI is what updates itself. It keeps a home per project under the
  store, so `sessions/` and resume persist.
- The login is the same link.

**7. Tests** (scratch homes and a stand-in `codex` only, never `~/.codex`):
- `install` from a fake npm: the native path from `codex-package.json`, a missing integrity refused, the wrong target
  refused; `published()` filtering.
- `verify` over every executable.
- The home builder:
  - the template's digest checked;
  - the link made to the resolved user file;
  - a changed template refused;
  - the run home removed on every way out;
  - crash leftovers pruned.
- The argv and `-c` list from one function for the run and its check; no `--ignore-user-config`, `--trust`,
  `--ephemeral` or `--dangerously-*` ever.
- The before and after checks against fixtures from these measurements (a v2 tree, a v1 `<tools>` message, a
  `gpt-5.5` empty list, a `turn_context` with `multi_agent_version` `v2`), each refusing.
- The declaration table over every point in `contracts/llm-points.yaml`, and an unknown field refused.
- The cwd rule: a workspace, a worktree, the throwaway copy and a folder below a `.git` refused; a memory copy and a run
  folder taken.
- `point_refusal` read by `llm_ready` (the librarian falls back).
- `CAGED_NEEDS` unchanged for Codex (no `confine`, a test says so until Step 4).
- `verdict(for_promotion=True)` with a `holds: sessions` fail.
- `in_use()`'s split.
- `credentials()` from fixture files (never printing a token).
- The `config.toml` text against the measured keys.
- `test_runtime_boundary`'s ratchet unchanged (all syntax in the adapter); `test_llm_golden` with a Codex fake store.
- The conformance checks against a stand-in `codex` that honours or ignores each property (pass, fail, tests nothing).

**8. Order of work** (each with its tests; one cold review of the code before merging):
1. Distribution and the store's Codex layout (`executables`, `version_at`), `checked_copy`, `binary()` without the PATH
   fallback, `in_use()`'s split. With D11's default ("sessions only") no platform copy is installed and no login is
   used, but `session-versions` starts installing the versions the user's Codex sessions run (session copies, about
   370 MB each, at most 5) and runs their model-free checks. That is wanted (the per-version gate needs it), and it is
   said in the merge's note.
2. The home template and builder, the config, `credentials()`, `codex-login`, `auth-link-write`, `home-flags`,
   `agent-profile`, `distribution-integrity`.
3. The completion port with its two checks; `completion-no-tools`, `completion-schemas`; the promotion rule (D13).
4. The agent port with the declaration table, the cwd rule, `point_refusal`; `agent-result`, `agent-models`.
5. `modules/codex.yaml`, doctor's rows (the Codex column's agent and completion rows, the admin layers, the keyring),
   the README.
6. With the owner's login and yes (D11): the login list (9), then one `update-runtimes --runtime codex` from main, and
   its result recorded here.

**9. What needs the owner's login to measure** (0.169.0, a platform-shaped home linking the user's login, scratch
folders):
- the live catalog: the same models, `tool_mode` and `multi_agent_version`; whether `model_catalog_json` holds over a
  catalog refresh;
- a real completion's rollout equal to `prompt-input`'s list with the home's flags, and `ALL_TOOLS` from inside `exec`
  (the nested list is a floor in its description);
- whether `apply_patch` still fails under `:read-only` on 0.169 (M1 again was 0.167);
- `--output-schema` with each of the eleven schemas: taken, refused (and how), or loosened;
- whether a ChatGPT plan serves `gpt-6-luna`, `gpt-6.1-sol` and `gpt-6-astra` through `exec`;
- an agent run in a memory-copy-shaped folder under the per-run profile: the canaries through the model (M8's set) and
  `turn_context.permission_profile`;
- whether a token refresh takes `auth.json.lock` beside the target, and Codex's refresh interval (`last_refresh`), read
  from the 0.169 source and seen once;
- usage in `turn.completed`, and the error text of a lost login;
- `lifecycle-hooks` in the world (additionalContext reaching the model);
- the time a completion takes (the librarian's nightly budget), and what a logged-in run fetches into a fresh home
  (the catalog, plugins, skills) and how long that adds.

**Model-free, at build** (not measured for this plan):
- Codex's own git calls (a logging `git` first on PATH) under `exec` with the platform home, in the completion's empty
  folder and in a memory-copy-shaped clone.
- The existing `conformance.py run codex` against 0.169.0. It wasn't run here: from this worktree its world would have
  been made under the live instance's private folder.

**10. Risks.**
- **Surface C.** The server can change the tool shape (11-08's code mode did). The per-run check refuses rather than
  widens. The residual is one run's wider tools under `:read-only` and no shell, with no MCP servers, until the after-run
  check refuses its result.
- **Deferred tools.** `exec` describes deferred nested tools only sometimes; the developer `<tools>` message names
  deferred namespaces. A version that defers something without saying so is caught only after the run. `world_state`'s
  tool hashes help, but don't list deferred ones.
- **B1 reaches the platform's own runs** wherever the folder's git config isn't the platform's. The cwd rule is the whole
  defence, so it is tested mechanically.
- **The shared login.** A refresh by a platform run rotates the user's tokens. Linked, both homes see the result. The
  residual is a refresh racing the user's daemon, narrowed (not closed) by the lock measured above.
- **Disk.** About 370 MB per copy: `current`, `previous`, up to 5 session copies, and a candidate.
- **The user's npm `codex`.** Its TUI offers "Update now" itself (11-14's slip); section 8's raise is D12.
- **Linux.** Codex's bwrap and M10: no copy is promoted where the profile check fails, as runtime-versions.md says.

**For the owner to decide:**
- **D11, the platform's Codex copy and your login.** A runtime that is only in `runtime.installed` (sessions) gets
  session checks and no platform copy, and no check uses your ChatGPT login. The platform installs a Codex copy and runs
  login checks (section 3a's rule) only once Codex runs platform work: a point pinned to it, a provider with `impl: codex`,
  or `runtime.default: codex`. Recommended: yes.
- **D12, your own npm `codex`.** Raise it to the promoted version after each promotion (section 8, as DV2 (a) for
  Claude), only while Codex runs platform work. Recommended: yes; the Desktop app and the daemon update themselves
  anyway.
- **D13, promotion with a sessions-only safety fail.** `session-folder-git` (B1) gates the sandboxed sessions of a
  version, but not the platform's copy, whose runs never start where it matters. Recommended: yes; without it no Codex
  copy can be promoted.
- **D3 stays as it is.** The default runtime is yours; Step 1 needs a pin or a provider, not the default.

#### r2 (11-14), after the blind review

A cold review of the plan above found one blocking point (B1 below, not runtime-codex's older B1), seven should-fix items
(S1–S7) and eight nits. The reviewer measured on 0.169 with the plan's own template. Where r2 and the plan above
disagree, r2 holds. Not built.

**D9, redefined by the owner (11-14).** In a sandboxed project nothing inside the sandbox may write git config that can
execute code: the platform writes that config, outside the sandbox. Then any tool the owner opens normally is safe, and
`brunna session` becomes an optional extra. The design is `git-config-fence.md` (another session's). What Step 1 rests
on it for:
- **Nothing in Step 1 itself.** Its runs start only in folders whose git config the platform owns (S1, S7), with or
  without the fence.
- **Opening Codex platform runs in a project's folder** (agent jobs, `mailbox-escalation`, `mailbox-conversation`,
  `mailbox-propose`) waits for the fence. Once it holds, Codex's own git there runs only config the platform wrote.
- **D13 is re-decided** when the fence lands or before Step 3's fork and resume, whichever comes first (S7).
- **Step 4 layer 2's `--no-daemon` home** (section 6 above) is now optional, as `brunna session` is.

**B1 (blocking): Codex runs the user's login shell outside any sandbox, and keeps what it exported.**
- What the reviewer measured on 0.169, with exactly the plan's template:
  - `exec` sourced `.zshenv`, `.zprofile` and `.zshrc`;
  - a line in an rc file wrote outside the sandbox;
  - `$CODEX_HOME/shell_snapshots/` held the shell's exports, a `GITHUB_TOKEN` and a `SECRET_CANARY` from `.zshrc` among
    them;
  - `prompt-input` does the same.
- `-c features.shell_snapshot=false` stops all of it. `--strict-config` takes it, and it changes no tool.
- Folded in:
  - The template gets `features.shell_snapshot = false`.
  - **The run's `HOME` is a folder of the run's own** (`<run home>/home`, empty, mode 700), not the user's. That also
    drops `~/.agents/skills`, `~/.gitconfig` (for Codex's own git) and every rc file. The login link is made before the
    run from the real home (2 above), so nothing of the user's is needed by name during it. `[skills]
    include_instructions = false` stays as a second layer.
  - **`home-flags` gains a canary:** an rc file (`.zshenv`, `.zprofile`, `.zshrc`) in the world's home, each writing a
    marker and exporting a canary variable. With the template, no marker may appear, and no snapshot may hold the
    variable. The positive control is the same run with `features.shell_snapshot = true` and the world's home as
    `HOME`: the markers appear, or the check "tests nothing".
  - Login list (9): an export from the user's `.zshrc` is absent from the model's shell environment in a real run.

**S1. The cwd rule refused `consolidate`'s fresh clone.** It is made under `private_tmp`, where propose's throwaway
copies live too.
- The rule now goes by files, all read before the run, never by running git:
  - the folder holds a real `.git` directory (a linked worktree's `.git` file is refused);
  - no `.git` in any folder above it;
  - its `.git/config` is one the platform wrote: its only remote is a bare repo under `git_origin` (a memory repo), and
    it names no `filter`, `include`, `includeIf`, `core.fsmonitor`, `core.hooksPath`, `diff.external` or other
    program-running key;
  - no `.git/info/attributes`, and no `config.worktree`.
- Folders under the memory-copies root are still known by name. A throwaway copy fails the rule (its `.git` is a file),
  and so is refused.
- `memory/reference/` is made before the run when a point's `allowed_tools` names it and it is missing. The profile's
  `write` on a path that doesn't exist can't create it.

**S2. Completions get the agent profile's narrowing**, not plain `:read-only`:
- the user's home and the temp roots denied;
- `read` back only on the run's empty folder and the running version's executables (S3);
- no `write` anywhere;
- the network off.

A tool the server widened would otherwise read files, and those reads reach the provider before the check after the run
can see them.

**S3. The profile reads the running version's own executables.**
- `codex-path/rg` and `codex-code-mode-host` live in `<instance>/runtimes/codex/<version>/`. In production that is under
  the home deny; in the check world it isn't.
- The profile gets an explicit `read` on that version's folder of executables (the platform package's `vendor/<triple>/`).
  The store's `home/`, `runs/` and every other version stay denied.
- `agent-profile` and `home-flags` render their profile with the world's store under a denied world home, so the check
  meets the shape production has.

**S4. The runner learns the login order** (neutral additions, named with the others below):
- `Check` gains an optional `login: bool`.
- `conformance.run` orders the login checks last.
- It withholds them (`error`, "withheld: a safety check failed first") after any safety fail in the same run, and when
  the adapter's credential check says a refresh could fall due during them.
- The credential check is an optional adapter function, `login_window(seconds) -> str | None` (None: no refresh can fall
  due that soon; else why not).

**S5. A run killed mid-refresh could spend the user's rotating refresh token.**
- The completion and agent ports end a run with SIGTERM, then SIGKILL after a grace period (10 s). Claude's `complete`
  goes straight to `kill()`; Codex's doesn't.
- The atomic rename of `auth.json` is measured fine, so the risk is the network half: a refresh sent and its answer
  never written.
- **The refresh-due rule for every run, taken in part.** Candidates and checks wait when a refresh could fall due (S4),
  as before.
  - For ordinary runs, refusing whenever a refresh is due would stop platform work whenever the owner hasn't opened
    Codex within the refresh interval. That is a failure of its own, which the owner may not see for days.
  - So an ordinary run may refresh, under the grace kill.
  - If the login list shows a refresh can land between SIGTERM and the write, every run refuses while a refresh is due,
    and doctor and an alert say "open Codex once to refresh its login". This is D14.
- Login list (9): a run killed with SIGTERM during a forced refresh (an expired access token in a copy of the home):
  does the new token pair reach `auth.json`?

**S6. Session copies gate nothing yet, and would alert on every daemon version.**
- A sandboxed project's Codex sessions are refused whatever their version (B1 of Step 4, `CAGED_NEEDS`). So a session
  copy of a Codex version serves nothing until those sessions can be served.
- Meanwhile `session-versions` would install one per daemon update (about 370 MB) and alert per version on
  `session-folder-git`.
- Taken:
  - `session-versions` skips a runtime whose sessions can't be confined (`runtime_ports.unconfined_reason` not None).
    This goes under D11's rule: no installs and no login for Codex until it runs platform work or its sessions can be
    confined.
  - The alert re-key (check and reason, not version) ships in order step 1, not later.
- Order step 1's note about session copies is withdrawn: with D11 provisional, step 1 installs nothing.

**S7. `holds = "sessions"` is sound only while no Codex platform run starts in a project folder.**
- The invariant is tested, not just stated. Every path into Codex's agent and completion ports goes through one cwd
  check (S1), and a test runs each declared point, and each caller's folder kind (a workspace, a worktree, a throwaway
  copy, a session folder, a memory copy, a fresh clone, a run folder), through it. Only the last three pass.
- `point_refusal` refuses the folder-bound points before a run is tried.
- A `holds = "sessions"` check that ends in `error` (the probe couldn't run) **blocks promotion**, as any safety error
  does. Only a recorded fail of that one property is let through, so a broken probe never counts as a known failure.
- D13 is re-decided before Step 3's fork and resume (a fork of a user's thread runs in the user's folder), or when the
  fence lands (above).

**Nits, each taken:**
- **`mailbox-propose` today is not exploitable.** It needs the owner's opt-in (`mailbox.handler: propose`), and
  `propose_apply.unsupported()` refuses sandboxed projects. The "to check" in section 5 is withdrawn, and so is the
  reason "B1" for dropping the template from the preset: Codex's own runtime replaces it, nothing more.
  - **Where I disagree, in part:** "refusing propose on Codex for B1 is more than needed" is right for unsandboxed
    projects, whose git config is the owner's own. Step 1 still keeps `mailbox-propose` off on Codex's own runtime. Its
    copy is a project folder, and letting one kind of project folder in would split S7's single invariant.
  - Opening it for unsandboxed projects is a separate change, after the fence.
- **`.gitattributes` made mid-run under a writable subtree.** Each `write` subtree of a profile gets `deny` globs on
  `<subtree>/**/.git*` (`.gitattributes`, `.gitmodules`, a nested `.git`), measured form as `profile_control_entries`.
  `agent-profile` gets a canary for it.
- **The pre-check can't take "the same flags".**
  - `prompt-input` rejects `-m` and `--strict-config`, so the model goes in as `-c model=…` in both, from one function.
  - The pre-check isn't strict. It is fail-closed: a shape it doesn't expect refuses the run, and the run itself is
    strict.
- **Doctor's admin-layer row** adds Codex's `cloud_requirements` and `cloud_managed_config` (the account-delivered
  layers) beside the files and the managed-preferences domain. How to read them without a login is to measure; until
  then the row says they can't be seen.
- **Catalog pinning:** the login list (9) measures `features.remote_models = false` beside `model_catalog_json`.
- **`update-runtimes.DAILY` adds `agent-models`.**
- **The login-check job** is neutral: no `bin/check-codex-login.py`.
  - The logic is an adapter function, `runtime_codex.login_check() -> list[problem]`.
  - A neutral `bin/check-runtime-login.py --runtime <name>` runs it, so the ratchet's exempt set doesn't grow.
  - Claude's `check-claude-login.py` moving onto it is later cleanup.
- **`agent-profile` targets the run's own `$CODEX_HOME/auth.json` link**, read and write, as canaries. The run's home is
  denied to its tools whole; the check proves it.

**The neutral-file changes, all of them** (the rest stays in `runtime_codex.py` / `conformance_codex.py`):
- `runtime_store.verify`: an optional `executables(entry)`.
- `conformance`:
  - `Check.holds` and `Check.login`;
  - login checks ordered last and withheld (S4);
  - `verdict(doc, for_promotion=True)`;
  - the session alert keyed by check and reason (S6).
- `update-runtimes`: `in_use()` split; `DAILY` + `home-flags`, `agent-models`.
- `session_versions`: skips unconfinable runtimes (S6).
- `llm_ready` / `llm.py`: `point_refusal`.
- `runtime_ports`: `login_window` named as an optional function.
- `bin/check-runtime-login.py`.
- doctor's rows.

**New for the owner:**
- **D14, a refresh due during an ordinary run (S5).** Provisionally: allowed, under the grace kill. If the login
  measurement shows the grace isn't enough, refuse and ask the owner to open Codex once.

**Order of work, revised:**
1. Distribution, the store's Codex layout, `checked_copy`, `binary()`, `in_use()`'s split, `session-versions` skipping
   Codex (S6), and the alert re-key (S6). Nothing is installed for Codex after this step while D11 holds.
2. The home template and builder (with `shell_snapshot` off and the run's own `HOME`), `credentials()`, `login_window`,
   `login_check` and the neutral job, `auth-link-write`, `home-flags` (with B1's rc canary), `agent-profile` (S2, S3, the
   `.git*` globs, the auth link), `distribution-integrity`, and the runner's login order (S4).
3. The completion port: the narrowed profile (S2), `-c model=`, the grace kill (S5), and the checks before and after a
   run. `completion-no-tools`, `completion-schemas`, the promotion rule with S7's error rule.
4. The agent port: the declaration table, the cwd check by files (S1) with S7's test over every folder kind,
   `point_refusal`, `memory/reference/` made first. `agent-result`, `agent-models`.
5. `modules/codex.yaml`, doctor's rows (the admin layers with the cloud ones), the README.
6. With the owner's login and yes (D11): the login list, B1's and S5's items included, then one `update-runtimes
   --runtime codex` from main, its result recorded here.

#### Step 1 as built (11-14), r2's order steps 1–5

Built on its branch, not merged. r2's order steps 1–5 are in; step 6 (the login list, then a first `update-runtimes
--runtime codex` from main) waits for the owner. With D11 provisional, **nothing changes on an instance that has Codex
only in `runtime.installed`** (this machine), and a test holds that:
- no store install;
- no session copies;
- no login use;
- no alert.

**The neutral pieces:**
- **`runtime_ports.platform_work()`**: the default, `llm.points` pins, and providers whose impl is a runtime. It is never
  `runtime.installed` alone.
  - `update-runtimes`' `in_use()` reads it, so a sessions-only runtime gets no platform copy and no raise of the user's
    own copy (D11, D12).
  - `session_versions.runtimes()` skips a runtime whose sessions can't be confined (`unconfined_reason`) unless the
    platform runs work on it (S6).
- **The session-check alert** is one per runtime, `session-versions-<runtime>`. Each problem is a check and reason,
  naming the versions; the per-version alerts of before end once.
- **`Check.login`**:
  - the runner orders login checks last;
  - after a safety fail it records them as `error` ("withheld");
  - it asks the adapter's `login_window(seconds)` before each one;
  - `update-runtimes.promotable` refuses a result whose login checks didn't run (`conformance.login_unrun`).
- **`Check.holds = "sessions"`**: `conformance.verdict(doc, for_promotion=True)` lets that check's fail through and
  never its error (S7).
- **`DAILY`** adds `agent-models` and `home-flags`. On Claude that is one more model call per model each day.
- **`runtime_store`**: a distribution port's optional `executables(exe)` goes into `installed.json` (`also`). Promotion
  records each one's real path, size, time and sha256, and `verify` checks them as it checks the executable.
- **`point_refusal(point, spec)`**, an optional adapter function, is read by `llm_ready.unsupported` and `llm.py`. The
  librarian takes its `-edits` completion for a part Codex refuses.
- **`bin/check-runtime-login.py --runtime <name>`** is neutral. It returns at once, saying so and with no alert, for a
  runtime the platform runs no work on. Otherwise it alerts once per runtime on the adapter's `login_check()` problems,
  by their ids.
- **Doctor** shows an adapter's own `doctor_notes()` rows: Codex's admin layers, its keyring store, and the cloud
  layers it can't see.

**`bin/runtime_codex.py`**: the ports `distribution`, `completion`, `agent` and `auth`. `confine` is left out (1e).
- **Distribution:**
  - `latest`, and `published` (only `X.Y.Z`);
  - `install` (npm into a prefix, then `copy_problems`: the lockfile's integrity, and `codex-package.json`'s version
    and target). It returns the native binary. Its `--version` runs with a throwaway home.
  - `executables` (the binary, `codex-code-mode-host`, `rg`);
  - `pin_env` (the npm sink), `MIN_VERSION = "0.169.0"`;
  - `user_copy` and `install_global` (D12; only reached for a runtime in platform work).
  - `binary()` no longer falls back to PATH; `version_at` knows the store's layout.
- **The run home.** `run_home()` makes `<runtimes>/codex/runs/<random>/` holding:
  - `config.toml`, written from `HOME_CONFIG` for every run. There is no template file to drift, which departs from
    r2's digest-checked template: nothing can change the file between its writing and the run;
  - `auth.json`, a link to the user's resolved file;
  - `home/`, the run's `HOME`.

  `HOME_CONFIG` is the plan's config plus `features.shell_snapshot = false` (B1). `run_env` keeps only the language,
  the user name, `HUB_LLM_POINT` and the confinement marker, then sets `HOME`, `CODEX_HOME`, a `TMPDIR` in the home,
  `PATH=/usr/bin:/bin` and the npm sink. Homes older than a day are pruned when the next one is made.
- **`profile_entries` + `run_args`** build one `-c` list for `prompt-input` and the run:
  - `-c model=` (never `-m`) and `-c developer_instructions=`;
  - the shell flags for agent runs;
  - the `brunna-run` profile:
    - `extends :read-only`;
    - the user's home, the store's Codex folder, the temp roots and every credential path the policy knows: `deny`;
    - the folder and the running version's vendor folder: `read`;
    - the written paths: `write`, with `<subtree>/**/.git*` `deny` for a folder;
  - network off.
- **The checks around a run (`_one_run`):**
  - Before: `prompt-input`, checked by `shape_problems`. The namespaces must equal `{"functions": ["exec", "wait"]}`.
    The nested tools may only be the pinned ones (plus the shell's two when asked). There may be no `<tools>`,
    `<skills_instructions>`, `<multi_agent_*>` or AGENTS.md context. A model with no list at all is refused.
  - After: the rollout (exactly one in the home), checked by `rollout_problems`:
    - the same shape;
    - per `turn_context`: approval `never`, sub-agents `disabled`, profile `brunna-run`, and permission entries equal
      to the rendered ones, beside the root read and Codex's own read of its `tmp/arg0*`;
    - `world_state`'s top-level tools.

    A mismatch refuses the result even of a run that failed, since what it was sent is the safety fact, and alerts per
    point (`completion-tools:codex:<point>`).
  - A timeout sends SIGTERM, then SIGKILL after `GRACE_S = 10` (S5).
- **Completion:**
  - a fresh folder under `private_tmp` with no `.git` above it;
  - the narrowed profile (S2);
  - the schema in the prompt (`SCHEMA_ASK`), not `--output-schema`, until the login list says the server takes the
    platform's schemas; `completion.py` validates either way;
  - usage from `turn.completed`.
- **Agent:**
  - `point_refusal`'s table (`KNOWN_FIELDS`; file tools; `dontAsk`/`acceptEdits`; `Edit(./…)` rules; the two
    held arguments). Over `llm-points.yaml`, Codex runs exactly `librarian-state`, `librarian-todo-format` and
    `consolidate`, and every completion point.
  - `folder_refusal`, read as files: a real `.git` directory, nothing above it, a config of only plain-clone keys whose
    remote is a bare `.git` directly under `git_origin`, and no `config.worktree`, `info/attributes` or `commondir`.
  - A folder rule's folder is made first; `describe` makes nothing.
  - The answer is parsed as JSON when the point has a schema.
- **Auth:**
  - `auth_env` gives the same login for both modes, and says when the file is missing;
  - `clean_env` drops `OPENAI_API_KEY`, `CODEX_API_KEY` and `CODEX_*`;
  - `billing` (an API key, or the file's `auth_mode`);
  - `credentials` (the file, and a note for a keyring store);
  - `login_window`: `last_refresh` against `REFRESH_INTERVAL_S`, 7 days, a guess short of Codex's own until the login
    list reads it, plus an hour's margin;
  - `login_check` (the file, its store, `codex login status` through a run home);
  - `doctor_notes`. Only `auth_mode` and `last_refresh` are read from the login file.
- **`unconfined()`** reads the newest of all of a version's results, full and session.

**`bin/conformance_codex.py`, the platform side:**
- `distribution-integrity` (function);
- `updater-off` (not-checkable: `exec` has no update path);
- `home-flags` (safety). It covers:
  - every model in `MODELS` through `prompt-input`;
  - one unauthenticated `exec --strict-config` whose rollout is checked;
  - rc files planted in the world's home that must not run, nor reach a snapshot;
  - positive controls: the shell, sub-agents, and the rc files with the snapshot on and the world's `HOME`;
- `agent-profile` (safety): 16 probes through `codex sandbox -c … -P brunna-run`, the copy under the world's home, each
  refused probe with a `:workspace` control;
- `auth-link-write` (safety; dummy API keys);
- `auth-login-error` (function, a 401);
- login checks, all not-checkable: `completion-no-tools`, `completion-schemas`, `agent-result`, `agent-models` and
  `lifecycle-hooks`;
- `session-folder-git` gets `holds="sessions"`.

**Run against the real 0.169.0** (a scratch npm prefix; the platform's config, state, data, secrets, instance and `HOME`
pointed at a scratch instance; `--report`; no login):
- pass: `distribution-integrity`, `home-flags` (its rc control saw the rc files run with the snapshot on and the user's
  `HOME`, so B1's finding holds on 0.169), `agent-profile`, `auth-link-write`, `auth-login-error`, `control-globs`;
- fail: `session-folder-git` (all four drivers ran: B1 on 0.169, now by the probe too);
- not-checkable: `updater-off` and the five login checks.

So `verdict` is fail, `verdict(for_promotion=True)` pass, and `login_unrun` names the five: not promoted.

**`modules/codex.yaml`:**
- the preset is `runtime.default: codex`, `runtime.installed: [codex]`, and no command template;
- the job `codex-login` is hourly, `bin/py bin/check-runtime-login.py --runtime codex`;
- the secret `codex-login` is `~/.codex/auth.json`, with no backup.

The module is off on this machine.

**Tests:**
- `tests/test_runtime_codex_runs.py` (29) with the stand-in `tests/codex_fake.py`:
  - nothing new while only installed;
  - distribution;
  - the run home and its config;
  - the shapes;
  - completion (clean, refused before, refused after, no login);
  - every declared point;
  - every folder kind;
  - the agent run;
  - auth;
- `test_runtime_contracts` runs the run and auth contracts on Codex (a memory-copy point, a stand-in, the login as a
  file);
- the runner's login order, the promotion rules and the store's `also` in their own tests.

**Left for the owner (step 6) and later:**
- the login list (r2 (9), B1's and S5's items), after which:
  - the login checks become real checks;
  - `--output-schema` per schema;
  - `REFRESH_INTERVAL_S` from Codex's source;
- a first `update-runtimes --runtime codex` from main once D11 brings Codex into platform work;
- D14's measurement.

Nothing points a judgment point at Codex yet.

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

### Measured 11-14 (with the owner's login)

**Setup.**
- The owner's login and `~/.codex`, with the owner's yes in the session. Versions: codex-cli 0.167.0 (the npm copy on
  PATH), and 0.169.0 from a scratch npm prefix pointed at the same home.
- The shared app-server daemon had already updated itself to 0.169.0 with its own hourly updater
  (`~/.codex/packages/app-server-daemon`). So a TUI on the "Local background server" ran its tools and hooks in 0.169
  behind a 0.167 client.
- Scratch git folders under `$TMPDIR`. Their trust entries, and the trust of their hooks
  (`[hooks.state."<scratch>/.codex/hooks.json:…"]`), were added to `~/.codex/config.toml` for the measurement and removed
  after; the file was byte-identical to its copy from before (`cmp`). `~/.codex/hooks.json` wasn't touched; `auth.json` was
  neither read nor linked.
- The TUI was driven through a pty.
- Each canary is one shell command run under the session's sandbox. It was judged from the file system and the rollout's
  tool output, never from the model's reply.
- About twenty small model calls.

One slip: on the first TUI start Codex showed its update prompt, and the scripted Enter chose "Update now". That ran
`npm install -g @openai/codex` (0.167 → 0.169). The npm copy was reinstalled at 0.167.0 at once, and later runs answered
the prompt with Esc (skip). Codex's own `version.json` (its update check) changed.

**For Step 2's open items:**
- **Hook context reaches the model at every event.** A project-level hook on SessionStart, UserPromptSubmit and
  PostToolUse each returned `additionalContext` with its own marker word. `codex exec` was asked to run `echo hi` and then
  list the markers, and named all three, on 0.167 and on 0.169.
- **PostToolUse fires for code mode's `exec_command`**, with `tool_name: "Bash"`, `tool_input: {"command": "echo hi"}`,
  `tool_response` and `tool_use_id: "exec-<uuid>"`, on both versions. The platform's matcher `Bash` holds.
- **SessionStart again:**
  - `codex exec resume <id>` fires it with `source: "resume"` and the same `session_id`.
  - `/compact` in a TUI (daemon 0.169) fires PreCompact (`trigger: "manual"`), then SessionStart with `source: "compact"`
    before the next turn. It doesn't fire right away: a TUI stopped just after compacting got none. So the rules come back
    after a compaction.
  - A TUI stopped with SIGTERM got its SessionEnd (`reason: "other"`) only later, from the daemon, while another session
    ran.
  - Auto compaction: not measured.
- **`CODEX_THREAD_ID`** is in a tool shell's environment (TUI on the daemon), equal to the id in the rollout's name, with
  `CODEX_SANDBOX=seatbelt`. It is not in a hook's environment.
- **`ps` is refused inside Codex's tool sandbox** under `read-only`, under the plain `:workspace` profile and under the
  rendered one (`ps -o pid= -p $$` fails). So `runtime_ports.current_session`'s ancestor walk finds nothing in a Codex tool
  shell; with a Claude id also in the environment, the sender is unknown.
- **End to end in this brunna worktree** (`codex exec -s read-only`, 0.167, the installed user-level hooks):
  - hub-event logged session_start with `hub_pull, memory_pull, local_setup, rules, nudge, session_record, mail,
    wake_address`, and prompt and session_end after it;
  - the thread got one developer message (`hooks.additional_context`) holding the letters notice and the open-questions line;
  - asked on resume which letter id it named, the model answered with it;
  - the trust that `user_install` wrote in 0.169's canonical form holds on 0.167.
  - **No rules came.** The hub's `.agent-memory` is a plain folder, so the rules handler's `_memory(ws)` is false. A project
    with a memory checkout gets them (Step 2's scratch test).
  - The first question (quote the rules' first line) got a refusal ("can't verify the injected hook context"): model
    behaviour, not delivery.

**For Step 4 layer 1:**
- **Hooks run outside the tool sandbox**, both under exec (a profile with `$TMPDIR` read-only) and in the TUI on the daemon.
  The hook wrote its log into a `$TMPDIR` folder the profile denied, ran `ps`, and had no `CODEX_SANDBOX`. Its parent was the
  native `codex` (exec) or the daemon.
- **An untrusted folder's profile is ignored.** `codex sandbox` and `codex debug prompt-input` in an untrusted git folder
  holding a profile in `.codex/config.toml` got Codex's default: read-only, `:root` readable, approval on-request. Reads of
  the canary outside the folder and of `~/.codex/config.toml` succeeded; every write and the network were refused.
- **The TUI's first open of an untrusted folder** asks: "Trust this folder? Codex can read, edit, and run files here, subject
  to your permission settings. Folder settings can run code automatically, even without a model request. … Your trust
  decision will be saved. 1. Trust and continue 2. Back". Choosing 1 wrote `[projects."<folder>"] trust_level = "trusted"`
  into `~/.codex/config.toml` itself, after the other project tables. `codex exec` asks nothing (M2).
- **The profile applies in the TUI.** In a trusted folder, a TUI started with no flags (on the shared daemon) showed in
  `/status`: "Permissions: Profile brunna (workspace, Ask for approval)". With `approval_policy = "never"` in the project's
  file it showed "(workspace, never)": the project's file may set the approval policy. `/permissions` offered "Ask for
  approval", "Approve for me", "Full Access", "Read Only" and "brunna (current)".
- **The canaries** (through the model in the TUI, from the rollout's tool output). Profile A is `extends = ":workspace"`,
  `network.enabled = false`, the outside folder `deny`, `~/.codex` `deny`. Profile B adds `<ws>/.codex` `read`, `<ws>/.git`
  `write`, `<ws>/.git/config` and `<ws>/.git/hooks` `read`, `":tmpdir"` and `":slash_tmp"` `read`.

  | canary | A | B |
  | --- | --- | --- |
  | read the denied folder outside | refused | refused |
  | read `~/.codex/config.toml` | refused | refused |
  | write a new file in `~` | refused | refused |
  | write a sibling folder in `$TMPDIR` | **allowed** | refused |
  | write `/tmp` | **allowed** | refused |
  | write its own file | allowed | allowed |
  | write `.codex/` (a new file, `config.toml`, `hooks.json`) | **allowed** | refused |
  | write `.git/config`, `.git/hooks/` | **allowed** | refused |
  | write `.git/brunna/outbox/` | allowed | allowed |
  | `git commit` | allowed | allowed |
  | network (`curl`) | refused | refused |
  | `ps` | refused | refused |

  - `:workspace` protects neither `.codex/`, `.git` nor the shared temp folders.
  - The trap: naming only `.git/config` and `.git/hooks` as `read` (without `.git` `write`) made the whole `.git` read-only,
    which refused the outbox and commits.
  - B gave the same results everywhere the canaries ran: `codex sandbox -- sh probe.sh` (no model) on 0.167 and 0.169, and
    through the model in the TUI (daemon 0.169). Under `codex exec` (0.167 and 0.169) only `echo hi` ran, and the rollout
    showed profile B active.
  - `codex sandbox` in a trusted folder applies the folder's `default_permissions`, which gives a check without a model.
- **What the rollout says.** Every `turn_context` has `approval_policy`, `sandbox_policy`, `permission_profile` (its entries)
  and `active_permission_profile: {"id": "brunna", "extends": ":workspace"}`.
  - With `--sandbox danger-full-access`, `--dangerously-bypass-approvals-and-sandbox` or `--sandbox read-only` on the command
    line, `active_permission_profile` was absent and `sandbox_policy.type` was the flag's.
  - The hooks' `permission_mode` was `bypassPermissions` in every exec case and in the TUI under `never`. It seems to follow
    the approval policy: with `-c approval_policy="on-request"` the turn still said `never`, but the project's file also set
    `never` then, so which one won is open. Either way it can't tell a confined session from an overridden one; the rollout
    can.
- **Escalation.**
  - Under `approval_policy = "never"`, the model asked to write with `require_escalated` answered that the session's policy
    forbids it; nothing was written.
  - Under on-request it asked: "Would you like to run the following command? Environment: local … 1. Yes, proceed 2. Yes,
    and don't ask again for commands that start with … 3. No". Declined; nothing was written.
  - So the model doesn't leave the profile without the owner.
- **Project-level hooks and config.**
  - A trusted folder's `.codex/hooks.json` loads once its hooks are trusted. With the trust in the user's file, the folder's
    hooks ran on 0.167 and 0.169, outside the sandbox, with the user's authority. Trust entries written in the folder's own
    `.codex/config.toml` didn't make them fire, but that was one run, and it also had a `-c` override that the positive run
    lacked. Take it as likely, not settled; the design doesn't rest on it, since `.codex/` is protected whole.
  - Inline `[[hooks.<Event>]]` in the folder's `config.toml` load too (`/hooks`: "1 hook needs review").
  - **`[features] hooks = false` in the folder's `config.toml` stopped every hook, the platform's user-level ones included.**
    hub-event logged nothing for that session.
  - The folder's file also sets `approval_policy` and `default_permissions` (above) and MCP servers (M5).
  - So `.codex/` is a control path whole. Codex's docs add execpolicy `rules/` to what an untrusted folder skips; not
    measured.
- 0.169's `/hooks` detail view ("Turn hooks on or off. Your changes are saved automatically") lets the owner switch a hook
  off. That is the owner's act, and doctor's "sessions without the platform's hooks" row shows it.
- **Not measured:**
  - the Desktop app (local) under a profile;
  - a linked worktree's trust;
  - `network.domains` with `features.network_proxy`;
  - a user-level `sandbox_mode` against a project's `default_permissions`;
  - a `read` glob over a subtree;
  - `shell_environment_policy.set` from a project's file;
  - whether a turn's `turn_context` is in the rollout by its UserPromptSubmit hook;
  - Codex's repo skills folder;
  - nested `.codex/` folders below a project's root;
  - `-c default_permissions=…` as an owner override;
  - the rules block reaching the model end to end (the hub's own session got none; Step 2's scratch test showed the hook
    gives it);
  - auto compaction.

What changes for Step 2, a follow-up apart from Step 4:
- `TELLS_ON` can add `prompt` and `tool_used`. Letters still go as a notice, since the context is developer-role.
- `current_session` gives Codex's id when the walk can't run and `CODEX_SANDBOX` is set.
- Whether the hub's own Codex sessions should get the managed block.

### Measured 11-14 for r2 (with the owner's login)

**Setup.**
- The same permissions and versions as above.
- One scratch git repo under `$TMPDIR` with profile B, trusted for the run. `~/.codex/config.toml` was copied first and
  copied back after; it was then byte-identical to that copy and to the one from before the first round. `hooks.json` and
  `auth.json` weren't touched or read.
- Every TUI start answered Codex's update prompt deliberately with Esc ("skip"). The driver checks the screen first and
  never sends Enter while that prompt shows. `codex --version` was 0.167.0 at the end.
- The git canaries in the repo's `.git/config` and `.gitattributes`: `core.fsmonitor`, a clean/smudge filter on `*.txt`,
  `diff.external`, `gpg.program` with `commit.gpgsign`, and hooks (`post-index-change`, `reference-transaction`,
  `pre-commit`, `post-checkout`). Each canary logged its parent process, whether `ps` worked and `CODEX_SANDBOX`. It wrote
  to a folder outside the profile, falling back to one inside, so a sandboxed run would still have left a line. Checked
  first with a plain `git status` and `git diff` from a shell, where fsmonitor, the clean filter and `post-index-change`
  fired.
- A logging `git` wrapper first on PATH, for exec and the embedded TUI.
- About eight small model calls.

**Results.**
- **Codex's own git** (exec 0.167; daemon 0.169 by the canary's parent):
  - Calls: `rev-parse --git-dir`, `rev-parse HEAD`, `rev-parse --abbrev-ref HEAD`, `remote get-url origin`, `remote -v`,
    `config --null --get core.fsmonitor` (and with `--type=bool`), and `status --porcelain`. They ran at the session's
    start and around a turn, and `/diff` ran git too.
  - Every call was outside the sandbox, as the user, with `-c safe.bareRepository=explicit -c core.hooksPath=/dev/null -c
    core.fsmonitor=false` and `GIT_OPTIONAL_LOCKS=0`; the `config --get` calls had only the first `-c`.
  - **The clean filter ran from `status --porcelain`** once a tracked file's stat changed with its content as staged. Its
    parent was `git … status --porcelain`, and the git's parent was the native `codex` (exec) or `codex app-server
    --listen unix://` (daemon 0.169).
  - fsmonitor, the hooks, `diff.external` and `gpg.program` never ran from Codex.
  - The 0.167 and 0.169 binaries carry `--no-ext-diff`, `--no-textconv`, `core.fsmonitor=false`, `core.hooksPath=/dev/null`
    and `GIT_CONFIG_NOSYSTEM`, and no setting that turns Codex's git off.
- **`apply_patch` under profile B**:
  - root `.codex/ap.txt`, `.git/config` and `.git/hooks/ap`: refused ("rejected as outside the project");
  - `a.txt`: written;
  - a new `sub/.codex/config.toml`: **written**.
- **The nested config loads.** `codex debug prompt-input` started in `sub/` showed the nested profile's entries (a wide
  `:workspace`, the temp folders writable). `codex sandbox` from `sub/` then read the denied folder and `~/.codex`, wrote
  `$TMPDIR` and the root's `.codex/`.
- **Globs.** A `read` glob stops Codex at load: "filesystem glob path … only supports `deny` access; use an exact path or
  trailing `/**` for `read` subtree access". With `deny` on `<ws>/**/.codex` and `<ws>/**/.codex/**`:
  - creating a nested `.codex` by `mkdir`, `cp -r`, `mv` and `ln -s` was refused;
  - the root's `.codex` was refused for reads and writes;
  - other writes in the workspace worked.
- **Inside the sandbox (shell)**:
  - `git worktree add wt -b wtb`: ok;
  - `wt/.codex/`: refused;
  - `.git/worktrees/wt/commondir` and `config.worktree`, `.git/modules/x/config`, `.git/info/attributes`, a new file in
    `.git`: ok;
  - `mv .git g` and writing a `.git` file: refused.
- **MCP servers.** A project MCP server, with two TUIs at once on the daemon (one started in the root, one in `sub/`), gave
  two processes. Each ran in its thread's folder, with parent `codex app-server`, `ps` working, no `CODEX_SANDBOX` and no
  `CODEX_THREAD_ID`.
- **The daemon's socket** from the sandbox (network off): listing `/tmp/codex-daemon-<uid>/` and connecting to both sockets
  by full path were refused, and `codex queue` failed reading `~/.codex/config.toml`. Unsandboxed, both connects succeeded,
  and `codex queue` to a nonexistent thread reached the daemon ("no rollout found").
- **`spawn_agent`.** The sub-thread's rollout has `thread_source: "subagent"`, `parent_thread_id` and the same
  `active_permission_profile`, and its canaries were refused as the parent's were.
- **`.agents`.** Its `hooks.json` and its `config.toml` hooks and MCP server didn't load. A `SKILL.md` under
  `.agents/skills/` appeared in the model's skills list (`codex debug prompt-input`).
- **`brunna inbox`** under the profile in an unregistered folder: a `sqlite3.OperationalError` traceback (the bus isn't
  writable there).
- **`session_meta.cli_version`** is the version of the process that created the thread: 0.169.0 for daemon threads behind
  a 0.167.0 client. A resumed thread keeps it.

### Measured 11-14 for Step 1 (no login)

**Setup.**
- codex-cli 0.169.0 (npm's latest that day) and 0.167.0, each installed from npm into a scratch prefix.
- Every run had a scratch `HOME` and `CODEX_HOME` and a bare PATH, under `env -i`.
- No login. A model request failed with 401 after the client had built it, so the rollout still shows what would have
  been sent.
- `~/.codex` was neither read nor linked. One `codex --version` of the scratch 0.167.0 ran with the real environment by
  mistake; `--version` makes a `tmp/` entry in its home, so it may have left one under `~/.codex/tmp`.

**The package.**
- `@openai/codex@0.169.0`: a 13.5 KB wrapper (`bin/codex.js`, a node shim) and no install scripts.
- Six platform builds as npm aliases in `optionalDependencies` (`@openai/codex-darwin-arm64` →
  `npm:@openai/codex@0.169.0-darwin-arm64`). The lockfile has a sha512 integrity for the wrapper and for all six; npm
  installs only the one that fits. The registry also lists provenance attestations.
- The installed copy is 371 MB: `vendor/aarch64-apple-darwin/bin/codex` (Mach-O, 281 MB),
  `bin/codex-code-mode-host` (70 MB), `codex-path/rg`, and `codex-package.json` (`version`, `target`, `entrypoint`).
- `codex --version` gives `codex-cli 0.169.0` through the shim and from the native binary alike. The native binary
  needs no node.
- The shim sets `CODEX_MANAGED_BY_NPM=1` and `CODEX_MANAGED_PACKAGE_ROOT`, then execs the binary.
- `npm view @openai/codex versions` lists about 5,900 versions, mostly platform-suffixed builds and alphas.

**exec, the daemon and the updater.**
- `codex exec` ran in process: no `packages/`, `app-server-control/` or `version.json` in its home, and no process left
  after it. That held with and without the updater settings.
- Positive-control attempt: 0.167.0 through npm's shim, with 0.169.0 published, a default home and npm's global prefix
  pointed at an empty sink. No `version.json`, no `packages/`, nothing in the sink, the binary's digest unchanged. exec
  has no update path; the update prompt is the TUI's.
- `--no-daemon` is a top-level flag: `codex --no-daemon exec` is accepted, and `codex exec --no-daemon` is an error.
- A fresh home with the default features cloned a plugin marketplace's git repo into `$CODEX_HOME/.tmp/plugins`;
  `features.plugins = false` stops it.
- What a run writes into its home: sqlite files (`state_5`, `logs_2`, `goals_1`, `memories_1`, `queue_1`,
  `thread_history_1`, each with `-wal`/`-shm`), `installation_id`, `.sandbox_migration`, `skills/.system/…`,
  `shell_snapshots/`, `thread-writer-locks/`, `tmp/`, `sessions/…/rollout-*.jsonl`. `config.toml` was left as it was.

**The tools a run gets** (`codex debug prompt-input`, no model call, 0.16 s; the same on 0.167.0).

| config | `gpt-6-luna` (v2, the default with no login) | `gpt-5.6-luna` (v1) |
| --- | --- | --- |
| none | exec{apply_patch, exec_command, list_mcp_*, read_mcp_resource, view_image, write_stdin, clock__curr_time}, wait, request_user_input(_async), clock.sleep, collaboration.* | (not run) |
| Step 1's earlier flags (shell, unified_exec, apps, plugins, goals, multi_agent, multi_agent_v2 off; web search off; `:read-only`; never) | exec{apply_patch, list_mcp_*, read_mcp_resource, view_image, clock__curr_time}, wait, request_user_input(_async), clock.sleep, **collaboration.*** | exec{apply_patch, list_mcp_*, read_mcp_resource, view_image}, wait, request_user_input; **a developer `<tools>` message: "Deferred tool namespaces: multi_agent_v1"** |
| + `features.view_image`, `features.sleep_tool`, `tools.experimental_request_user_input.enabled` off | exec{apply_patch, list_mcp_*, read_mcp_resource, clock__curr_time}, wait, **collaboration.*** | exec{apply_patch, list_mcp_*, read_mcp_resource}, wait; the `<tools>` message |
| + `[agents] enabled = false` (Step 1's home) | exec{apply_patch, list_mcp_*, read_mcp_resource, clock__curr_time}, wait | exec{apply_patch, list_mcp_*, read_mcp_resource}, wait |

- `agents.max_depth = 0` and `features.deferred_tool_world_state = false` each removed v1's deferred announcement, and
  neither removed v2's `collaboration`. `features.tool_search = false` removed neither (tried on v1).
  `agents.max_concurrent_threads_per_session = 0` is refused at load.
- Codex itself says: "`[features].multi_agent` is deprecated … `[agents].enabled = false` disables subagent tools even
  when the model selects a subagent version, unless `[features].multi_agent_v2` is enabled."
- With the home's flags, `gpt-6.1-sol` and `gpt-6-astra` gave `gpt-6-luna`'s list. A positive control, `-c
  features.shell_tool=true -c features.unified_exec=true`, added `exec_command` and `write_stdin`.
- `gpt-5.5` (no `tool_mode` in the catalog) shows no tool list in `prompt-input` at all, with the shell on or off. It
  isn't code mode, so its tools go in the request, not in the input list.
- The catalog without a login is the bundled one (`codex debug models` equals `--bundled`):
  - v2: `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, and the
    hidden `gpt-daybreak-*`;
  - v1: `gpt-5.6-luna`, and the hidden `codex-auto-review`;
  - neither: `gpt-5.5`;
  - all but `gpt-5.5` are `code_mode_only`.
- `model_catalog_json` (a config key) pointed at an edited copy of the bundled catalog (`gpt-6-luna` as v1) was used by
  `prompt-input`.
- `project_doc_max_bytes = 0` kept a folder's `AGENTS.md` out.
- A skill in the user's `$HOME/.agents/skills` was listed in a run's instructions with `CODEX_HOME` elsewhere, unless
  `[skills] include_instructions = false`.
- `-c developer_instructions=…` arrives as a developer message.

**The rollout of a run with no login** (`exec --json`, the home's flags, `gpt-5.6-luna`):
- `session_meta` (`originator: codex_exec`, `cli_version`);
- `additional_tools` with exactly the tree above;
- the developer and user messages;
- `world_state`, with `top_level_tools` (each namespace and tool by hash) and a hash of `model_catalog`;
- `turn_context`: `model`, `multi_agent_version: "disabled"`, `approval_policy: "never"`, `sandbox_policy: read-only`,
  `active_permission_profile: {"id": ":read-only"}`.

**Config.**
- `--strict-config` refused a misspelt `features.shell_tol` and took every key of Step 1's home, `[analytics] enabled =
  false` included, on 0.167.0 and 0.169.0.
- `--ignore-user-config` is documented as "Do not load `$CODEX_HOME/config.toml`; auth still uses `CODEX_HOME`".
- exec also has `--trust` ("Save trust for this project … Persists even with --ephemeral"),
  `--dangerously-bypass-hook-trust` and `--ignore-rules`.
- A per-run permission profile passed whole by `-c` arrived in `turn_context.permission_profile`: `permissions.<name>.extends`,
  a `filesystem` table of absolute paths, `network.enabled`, and `default_permissions`.
- The binary names system layers `/etc/codex/config.toml`, `requirements.toml` and `managed_config.toml`, and a managed
  preferences domain. None exists here.

**`--output-schema`.** Codex only checks that the file is JSON: a file that wasn't stopped the run before any request.
`mailbox-route` (strict), `librarian-state-edits` and `reflect-week` (not strict), and a schema with an invalid type all
went on to the request (401). Strict by OpenAI's rules (`additionalProperties: false` everywhere, every property
required, no `pattern` or `min`/`max`): `first-look` and `mailbox-route`, two of eleven. The rollout doesn't record the
schema.

**Hooks and a project's config.**
- A scratch `$HOME/.codex` held a SessionStart hook trusted by `trust_hash`. It fired for a run with no `CODEX_HOME`
  (the positive control). It didn't fire for a run whose `CODEX_HOME` was Step 1's home, nor with
  `--dangerously-bypass-hook-trust`.
- A `.codex/config.toml` turning the shell and agents on, in a run's untrusted folder (not a git repo), didn't load
  (`prompt-input`). A `.codex/hooks.json` there didn't fire under `exec` with a home whose hooks were on. Its control,
  the same hook as a user-level `hooks.json` in that home with `--dangerously-bypass-hook-trust`, fired.

**The login link (M9 without a real login).**
- Setup: dummy API keys and the file store, in a scratch "user" home. `codex login --with-api-key` wrote its
  `auth.json` (`auth_mode`, `OPENAI_API_KEY`). A platform-shaped home held `auth.json` as a link to it.
- `login status` through the link read it.
- A second `login --with-api-key` through the platform home wrote the user's file and kept the link.
- `logout` through the platform home removed only the link.
- A write through the link takes `auth.json.lock` beside the target: with that file locked by another process the write
  waited 5 s and failed; with the user home's `auth.lock` locked instead it went through at once.

**Permission profiles for a run in a memory copy** (`codex sandbox -P`, no model).
- `:minimal` can't be extended ("cannot extend unsupported built-in profile"). A profile with no `extends` can't start
  `/bin/sh`.
- `extends = ":read-only"` with the copy `read` and its `STATE.md` `write`:
  - `STATE.md` written;
  - `AGENT.md`, a new file in `memory/` and `$TMPDIR` refused;
  - the network refused;
  - a folder outside and the home readable, so the denies come from the policy.
- `:workspace` with the same entries wrote everything in the folder and `$TMPDIR`.
- `extends = ":read-only"` with the folder holding the copy `deny`, the copy `read` and its `STATE.md` `write`: the copy
  read and listed, `STATE.md` written; `AGENT.md`, a sibling folder's file and the parent's listing refused; `/etc/hosts`
  read. The more specific entry wins over the deny around it.
