# The Codex runtime adapter (`bin/runtime_codex.py`)

Status: design, 11-08. Not built. Owner, 11-01: the adapter is designed, cold-reviewed, then built port by port, each port that
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

**Step 1: `runtime_codex` with agent + completion + auth, confined.** Every platform run of Codex is built from the point's spec and
`policy.py`, never from the user's or the project's Codex config:
- `--ignore-user-config` and no project config: measured (M5) that `codex exec` loads no `<repo>/.codex/` config, hooks or MCP servers
  that way, or else the run's working folder is one with none (as Claude's `--restricted` does); refused if neither holds.
- A point with no tools (`tools: ""`, completion): `-c features.shell_tool=false`, web search off, no MCP servers (M1: no command runs).
  A point with tools gets them only through a permission profile rendered from the policy: writes to its declared folders, reads denied
  for what `policy.py` denies every sandbox (`deny_read`, credentials, `platform_state`), `approval_policy=never`.
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
sender session. Records keyed by Codex's session id, not by process, if the daemon is shared (M7).

**Step 3: wake** (`codex queue --thread <id>`, the id checked against the record's format first), **status / summary** (the rollout
reader shared with `sessions_codex.py`, the same privacy gate as Claude's last reply), **resume / fork** (`codex exec fork`).

**Step 4: confine + control + tool_servers for sandboxed projects and `allow:` jobs**, with a "sessions confined" check separate from
the platform's own runs (a sandboxed project's Codex session the owner opens is confined only if D2 says so); render-project-settings
gets a Codex renderer beside Claude's. `FOLDER_GIT_OFF` stays unknown (Codex runs git in the folder) until measured.

## Decisions for the owner

- **D1, where the hooks live.** (a) User-level `~/.codex/hooks.json`, once per machine: one trust review; the hook works out its project
  from the cwd; installed by the owner and kept current by upgrade (`user_refresh`, as Claude's worktree hooks). (b) Project-level
  `<repo>/.codex/hooks.json`, untracked: one review per project, and only in trusted projects. (a) recommended. Platform runs must not
  fire them (M5).
- **D2, a sandboxed project's sessions on Codex.** The platform confines its own Codex runs (steps 1, 4). A Codex session the owner opens
  in a sandboxed project would load a permission profile from the project's `.codex/config.toml` only if the project is trusted, and the
  platform can't hold it to that the way Claude's settings are enforced. Recommended: sandboxed projects stay Claude-only for sessions;
  doctor says so.
- **D3, the default runtime.** With Claude as the default, Codex sessions are served (hooks, letters, wake) but the platform's
  background runs (judgment points, forks, leases) stay on Claude; leases can't move (`RUNTIME_ARG_POINTS`). A per-project default is
  left out.

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
