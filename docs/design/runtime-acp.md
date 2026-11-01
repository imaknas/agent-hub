# An Agent Client Protocol runtime (`runtime_acp.py`)

2026-11-01. Item 2 of "Build" in `competitive-gaps.md`. Read with `runtime-abstraction.md` (the ports),
`tool-agnostic.md` (where the platform's guarantees come from) and `lease-contract-neutral.md` (kinds, approvals).

## Why

Many coding agents now speak the Agent Client Protocol (ACP): OpenHands, Goose, OpenClaw, Hermes, Gemini CLI, and
adapters exist for Codex (`@agentclientprotocol/codex-acp`) and Claude (`@agentclientprotocol/claude-agent-acp`). One
adapter that speaks ACP lets "any coding agent" be tested against several real agents (see "Live results"), with
no per-tool output parser (`runtime_command` reads only a tool's final text).

## The protocol, as read from the primary source

Source: `agentclientprotocol/agent-client-protocol`, `docs/protocol/v1/*.mdx` and `schema/v1/meta.json`, commit
`b4c72a8` (2026-10-31, release 1.14.0). Facts this design rests on:

- JSON-RPC 2.0 over the agent's stdin/stdout, one message per line. The client starts the agent as a subprocess.
  Both sides send requests: the agent asks the client for permission, files, terminals and elicitation in the middle
  of a turn, and the client must answer by id.
- **Stable v1 methods** (`meta.json`): `initialize`, `authenticate`, `session/new`, `session/load`, `session/resume`,
  `session/close`, `session/list`, `session/delete`, `session/prompt`, `session/cancel`, `session/set_mode`,
  `session/set_config_option`, `logout`, `$/cancel_request`; agent → client: `session/update`, `session/request_permission`, `elicitation/create`,
  `elicitation/complete`, `fs/read_text_file`, `fs/write_text_file`, `terminal/*`.
- **`session/fork` is only in `meta.unstable.json`.** So are `session/status` and `auth/status`.
- `initialize` carries one integer `protocolVersion` (1). Capabilities omitted on either side are unsupported. An
  agent **must not** call `fs/*` or `terminal/*` unless the client advertised them, and **must not** send
  `elicitation/create` in a mode the client did not advertise.
- `session/new {cwd, mcpServers}` → `{sessionId, configOptions?, modes?}`. `cwd` must be absolute. `session/load`
  (if `loadSession`) replays the history as `session/update`s; `session/resume` (if `sessionCapabilities.resume`)
  restores without replay. `mcpServers` lists servers the agent **should connect to**: it adds to whatever the agent
  loads from its own configuration and blocks nothing.
- `session/prompt {sessionId, prompt: ContentBlock[]}` → `{stopReason}` with `end_turn | max_tokens |
  max_turn_requests | refusal | cancelled`. Everything in between arrives as `session/update` notifications:
  `agent_message_chunk` (optional `messageId` groups chunks into messages), `agent_thought_chunk`, `tool_call`,
  `tool_call_update`, `plan`, `usage_update {used, size, cost?: {amount, currency}}` and others.
- Tool calls are announced by a `tool_call` update and patched by `tool_call_update`s (only `toolCallId` is required
  in an update). They carry `kind`: `read | edit | delete | move | search | execute | think | fetch | switch_mode | other`,
  plus `title` (agent-authored), `locations`, `rawInput`, and `content` (which can be a `diff` with its `path`).
- `session/request_permission {sessionId, toolCall, options: [{optionId, name, kind}]}` with option kinds
  `allow_once | allow_always | reject_once | reject_always`; the client answers `{outcome: {outcome: "selected",
  optionId}}` or `{outcome: {outcome: "cancelled"}}`; `cancelled` means the turn was cancelled. **The agent decides
  when to ask.** An agent that runs a tool without asking cannot be stopped by the client. "The Agent owns
  remembered decisions" (`allow_always`/`reject_always`): their scope and lifetime are the agent's, and automatic
  approval "SHOULD NOT rely only on title or option `name`".
- Elicitation (stable since 2026-07-24): `elicitation/create` in `form` mode (a flat JSON-schema object of
  primitives and enums) or `url` mode, answered with `accept | decline | cancel` (+ `content`). Session-scoped or
  request-scoped.
- Login: `initialize` returns `authMethods`. For a method of type `agent` (the default) the client calls
  `authenticate {methodId}` and the agent logs in itself; a `terminal` method needs an interactive terminal. A
  request that needs a login fails with JSON-RPC error **`-32000`** ("Authentication required"): a protocol-level
  login signal, independent of any tool's wording.
- Model choice is not a stable method: an agent may offer it as a session config option with `category: "model"`
  (`session/set_config_option`).

## Decisions

### 1. Which ports ACP fills

| Port | ACP | How |
|---|---|---|
| `agent` (run once) | **yes** | one process per run: `initialize` → `session/new` → one `session/prompt` → collect → kill |
| `auth` | **yes** | the agent keeps its own login; optionally a key the platform hands it (below); `-32000` = login error |
| `stream` (long-running lease session) | **no, `Unsupported`** in this step | see 4 |
| `sessions` (alive, live, context size, current session) | **no, `Unsupported`** | see 4 |
| `completion` | **no, `Unsupported`** | ACP has no way to turn an agent's tools off; an approximation would silently read files |
| `lifecycle` (hooks) | **no** | ACP has no hooks into sessions a person opens in their own editor |
| `instructions` (rules file) | **no** | ACP names no rules file; the platform's rules reach ACP agents through the MCP server's `instructions` (`docs/mcp.md`) |
| `transcripts` | **no** | the platform only sees sessions it starts itself; counting only those would make memory-gaps and the weekly report wrong without saying so |

Fork is not offered: `session/fork` is unstable. The adapter speaks only stable v1.

### 2. Headless run (`run`, `describe`)

- **Where a run may happen.** The adapter runs a Task only in the throwaway copy `propose_apply.py` made, which it
  marks by setting `HUB_THROWAWAY_COPY` to that directory in the run's environment; the adapter compares real
  paths. `task.cwd` None means the process's own directory (the librarian and consolidate set it through the
  subprocess, not `--cwd`) and is checked the same way, never swapped for another directory. The points that run
  there are listed once, in `runtime_ports.THROWAWAY_POINTS` (today `mailbox-propose`); the adapter declares
  `THROWAWAY_ONLY = True`. All three must hold: the mark, the point being one of those, and the place looking like
  the platform's copy (`<temp dir>/hub-propose-<project>-…/ws`, judged by path: the platform runs no git in a
  project's repository except as the project).

  Anything else is refused before the agent starts, with the reason: "the ACP runtime runs only in the platform's
  throwaway worktree". Config check already refuses pinning `acp` on any other point and refuses it as the default
  (section 6); should a run reach the adapter anyway, the agent-shaped points that work in a real checkout (the
  librarian's agent points, agent jobs, mailbox escalation and conversation, consolidate) fail with that reason and
  record `job.failed`; they never run with their per-point limits (`allowed_tools`, `allow:`) silently dropped.
  A Task with `extra` (runtime arguments from the caller, e.g. continuing an earlier conversation) is refused,
  naming the arguments.
- **What the place guarantees, and what it does not.** The throwaway copy and the copy back are
  `propose_apply.py`'s and `project_git.py`'s (`projects-and-repos.md` section 4: the platform runs git in a
  project's repository only as that project): a detached worktree made as the project, its changes listed and the
  allowed paths copied back by the project's own job, a symlink the agent leaves refused. This work adds, for every
  runtime: never applied whatever the allow list says, a path with a dot component (`.gitignore`, `.gitattributes`:
  the platform's own memory sync would follow them); and the run is **all or nothing**: a run that failed, was
  cancelled or left incomplete, or ended without the structured answer (ACP has no structured output, so prose
  instead of the JSON is a real case; the platform acts only on the structured answer), gets nothing copied back
  and hands back no output (no replies sent, no letters closed). The mailbox caller then records `job.failed`,
  holds the letters (they stay claimed, so nothing is dispatched twice and nothing loops) and asks the owner about
  each, with the reason and anything already copied back; any other break in the mailbox run does the same, and so
  does a letter the agent neither answered, closed nor deferred. Deferring is explicit in the propose contract
  (`deferred: [{id, why}]`, the prompt's two cases: waits for a code change, or for the owner, asked in
  `ask_owner`): such a letter stays open and claimed, as before, with no failure recorded. The unattended rules also
  refuse writes under `.git`, as advice.
  All of this holds for every runtime. It does **not** contain writes
  by absolute path or through a shell outside the copy: those are held only by the agent's own mode or sandbox,
  which is the standing `tool-agnostic.md` accepted for `runtime_command` on 10-29 (Codex's `workspace-write`, agy's
  `write_file({cwd})`). The Claude propose run has no shell at all (its `tools:` list); an ACP client cannot take
  the agent's shell away. So the deployment configures each agent's `mode` (section 5): when set, the adapter
  checks that the agent offers it (a config option of category `mode` first, since the spec has clients that use
  config options use them exclusively; else `session/new`'s `modes.availableModes`), applies it with
  `session/set_config_option` / `session/set_mode` before the prompt, and refuses the run when it is not offered.
  **Held settings.** The mode, and the model whenever the agent reports a model option offering it (set through
  that option, or given in the command), are held for the whole run; an option the adapter set is tracked by its
  id as well (the spec makes `category` a UX hint only), and where the agent has a mode config option, a mode it
  does not offer there refuses the run (no falling back to the legacy modes). The model is set
  first, then the mode (a dependent change, e.g. a model that resets the mode, happens before the mode is set). The
  adapter tracks the agent's current value of each from every report of it: `session/new`, the complete config
  state each `set_config_option` answer carries, `config_option_update` (e.g. a fallback to another model on rate
  limits) and `current_mode_update` (the agent switching its own mode). A held setting that moves away before the
  prompt refuses the run; during the turn it sends `session/cancel` and fails the run with the reason ("the agent
  changed its model from X to Y"). After the platform cancels, every further permission request is answered
  `cancelled`; without it, the agent's default mode applies, and doctor cannot see an agent's mode. Wrapping
  platform-started agents in the platform's own sandbox, which would make this structural, is the item
  `tool-agnostic.md` postponed (the agent must read its own login, sandboxes do not stack, the network stays open):
  an owner decision, not taken here.
- **Start**: the configured command (5) in that directory, its own process group, stderr to a temporary file (a
  single reader on stdout, so a full stderr pipe can never block the agent).
- **`initialize`** with `protocolVersion: 1`, `clientInfo {name: "agent-hub", version}`, and **no client
  capabilities**: no
  `fs`, no `terminal`, no `elicitation`, no `auth.terminal`. The platform does not become the agent's file system or
  shell, so every read, write and command stays the agent's own (and is held as described above). An
  answer with another `protocolVersion` fails the run loudly. If the agent's config names an `auth_method`, the
  adapter then calls `authenticate` with it.
- **`session/new`** with `cwd` = the absolute, resolved working directory and `mcpServers: []`. The platform gives
  the agent no MCP servers, but that does not stop the agent loading its own configured ones (the judgment points'
  "strict MCP" intent: the global servers can include production databases). Where an agent has a flag for "no
  configured MCP servers", the deployment puts it in the agent's `command`; where it has none, its own servers load,
  and this is the agent's configuration to keep clean, stated here.
- **Model**: `task.model` goes into the command template's `{model}`. With no model, the argument holding `{model}`
  is dropped, and so is the flag right before it when that argument is not itself a flag (`--model {model}` and
  `-c model={model}` both go as a pair). If the template has no `{model}` and a model is wanted, the adapter looks in
  `session/new`'s `configOptions` for a select option with `category: "model"` that offers that value (grouped
  options flattened) and sets it with
  `session/set_config_option`. Neither possible → the run fails with the reason ("this agent offers no model X"),
  never runs on another model.
- **`session/prompt`** with one text block: the prompt, plus the output schema appended as `runtime_command` does
  (ACP has no structured output). The answer is parsed with `completion.parse_json`; a failed parse leaves
  `structured_output` None for `llm.py` to judge.
- **Text**: the last agent message of the turn (the structured answer is parsed from the newest message that holds
  one: an agent may reply, make one more call and say "done"): chunks sharing the last `messageId`, or, when the agent gives no ids,
  the chunks after the last `tool_call` (a later status update of an earlier call does not start a new message).
  Thought chunks are not the answer.
- **Permission requests** in a headless run follow the neutral rule set `policy.APPROVALS["unattended"]` (new,
  in `policy.py`), applied by kind:

  | ACP kind | platform kind | unattended rule |
  |---|---|---|
  | read | read | inside (allow when every location resolves inside `cwd`) |
  | search | search | inside |
  | edit, delete, move | write_file | inside |
  | execute | run_command | deny |
  | fetch | open_web | deny |
  | think, switch_mode, other | other | deny |

  The request's `toolCall` usually carries only its id, so the adapter keeps every tool call's state from the
  `tool_call`/`tool_call_update`s, merges the request into it, and only then classifies. "Inside" uses every path
  it knows: `locations`, `diff` contents' `path`, and in `rawInput` (walked through lists and nested objects) every
  value under a key that names a place (`path`, `file_path`, `files`, `paths`, `dir`, `cwd`, `uri`, `to`,
  `destination`…) and, for a read or search, any string that looks like a place (absolute, `~`, `$`, `file:`, `..`);
  a write's content is never taken for a path. Each is resolved to a real path, and all of them must be inside (a
  move needs both ends inside; `~`/`$` paths and any `.git` component, compared case-insensitively, are never
  inside). A read or search that names no place works where it was started (cwd); a write that names no target is
  refused. Allowing selects an option of kind `allow_once`, never
  `allow_always` (a remembered decision is the agent's, with a scope and lifetime the platform cannot see); denying
  selects `reject_once`, or `reject_always` when that is all the agent offers (refusing for good is as safe in a
  one-prompt session). No option of the wanted kind = the adapter sends `session/cancel`
  and then answers `cancelled` (the spec's meaning of that outcome); the run is not ok whatever stop reason follows,
  with the platform's reason ("the agent
  offered no allow_once option for …; the platform cancelled the turn"), and `denials` names the request. Every
  denial is recorded in `TaskResult.denials` as `{kind, summary}`.
  `Task.spec`'s Claude fields (`allowed_tools`, `permission_mode`, `tools`, `extra_args`) are not read; the place
  (above) is what limits the run.
- Requests the client did not advertise (`fs/*`, `terminal/*`, `elicitation/create`) are answered with `-32601`;
  any other unknown request likewise. Unknown notifications are ignored.
- **Stop reasons** → `TaskResult`: `end_turn` ok; `max_tokens`, `max_turn_requests`, `refusal` ok + `incomplete`
  (`stop` says which; the caller decides); `cancelled` or a JSON-RPC error → not ok, with the
  error's message; a missing or unknown stop reason → not ok, naming it. A message that does not follow the
  protocol (a missing `sessionId`, a non-object where one is required) → not ok, "does not follow ACP", never a
  traceback. `usage_update` without `cost` keeps the cost already reported (it is cumulative). `-32000` anywhere → not ok, error starting with `auth_required:` and the agent's `authMethods`
  names (a person has to log the agent in).
- **Reported**: `session_id` = ACP `sessionId`; `cost_usd` from the last `usage_update.cost` only when `currency ==
  "USD"` (else None); `turns` None (ACP does not count model requests). Nothing is made up.
- **Timeout**: every write to the agent and every wait (including for its exit after it closes stdout) is bounded
  by the run's deadline, and so is cleanup: a pipe still held by a process that left the agent's group is left to
  its daemon thread rather than closed (closing would wait). On timeout: `session/cancel`, two seconds' grace, then the whole process group is killed;
  not ok. The client
  answers each permission request at once in its single read loop, so none is pending when it cancels (the spec's
  "respond to all pending requests with `cancelled`" has nothing to answer). After a
  normal end the adapter sends `session/close` if the agent advertised `sessionCapabilities.close`, then kills the
  process group.
- `describe(task)` = the argv that would start the agent, followed by the ACP calls it would make.
- `runtime_ports.models("acp")` reads the module's `MODELS`, which `runtime_acp` provides through a module
  `__getattr__` from the selected agent's `models`.

### 3. Login (`auth`)

ACP agents keep their own login (Codex's ChatGPT login, Claude's, a provider key in their own config). The adapter
says so and does not pretend to know more:

- `credentials()`: one `Credential` for the selected agent (the one runs go to): `mode: "token"`, `present` = the configured key file
  exists, or True when no key is configured ("the agent keeps its own login; only a real request confirms"),
  `renew` = the configured `login` hint (e.g. "run `codex login`").
- `auth_env("token", env)`: env with the adapter's variables cleared, plus, if the agent config names one,
  `credential: {env: <variable name>, file: <file in the secrets directory>}`, that variable read from that file;
  the file missing → `(None, reason)`, never a fallback. `auth_env("subscription", env)`: the cleared env (the
  agent's own interactive login). The config names the variable and the file, never the value.
- `clean_env(env)`: removes the configured credential variable.
- `is_login_error(text)`: the error starts with the adapter's own `auth_required:` prefix (from `-32000`). Not
  "contains": other errors quote the agent's own text. No guessing from wording.
- `auth_method` (optional, per agent): an `authMethods` id of type `agent` the adapter `authenticate`s with after
  `initialize`, for agents that log in themselves once asked. Checked against what the agent offers: an id it does
  not offer, or a `terminal` method (no terminal unattended), refuses the run with the offered ids.
- A key file counts as present only when it is non-empty, for doctor and for runs alike.
- The key file named in `credential.file` is a machine-level credential: the deployment declares it under the
  `secrets:` of the platform registry (or the module that uses it), so `check-secrets` covers existence, backup and
  expiry like any other.
- The real request check (`llm.py run login-check`, hourly in the claude module) checks the default runtime's
  logins only. An ACP agent that is not the default is checked by its own runs: a lost login fails them with
  `auth_required:` and `job.failed`, which alerts. A login check that walks every runtime in use belongs to the
  claude module's planned rename to `runtime-login` (`runtime-abstraction.md` layer 6), not here.

### 4. Long-running sessions and leases: not in this step, and what they need

The `stream` port reads "one line of the runtime's native output". ACP is two-way: mid-turn the agent asks for
permission and elicitation and waits for the client's answer. And `lease-runner.py` still builds Claude's flags,
settings file and MCP config itself (`start_proc`, `deny_settings`, `mcp_config`, `work_ready`, `terminal_open`,
`run_handover --resume`). Filling the six `stream` functions for ACP would give lease-runner something it cannot
drive. So **`stream` and `sessions` are `Unsupported` for ACP now**; lease-runner already answers
`runtime_unsupported` for a runtime without them, and doctor shows the lease feature off with the reason.

What a lease over ACP needs, decided here so the follow-up only builds it (`runtime-abstraction.md` step 4):

- **The interface.** A long-running session port the runner drives through calls, not lines:
  `open_session(spec) -> handle`, `send(handle, blocks)`, `events(handle) -> Iterator[StreamEvent]`,
  `cancel(handle)`, `close(handle)`, `resume(sid, spec)`, with the spec carrying cwd, mode, preamble, max turns and
  **a permission callback** `decide(kind, target, question) -> decision` the runtime calls whenever its agent asks.
  Claude's adapter wraps its stdio stream-json and its `--permission-prompt-tool` MCP in the same calls; ACP's
  implementation is the client loop of this adapter kept open. The runner owns lease state, idle, quota, handover.
- **Permission requests → the relay.** `lease-relay.py`'s decisions (`policy.approvals`: ask on the phone, `inside`,
  `listed` domains, `no_session_domains`) move into a neutral module that takes `(kind, target, question)` and
  returns `allow | allow_session | deny` + a message; the Claude MCP tool and the ACP client both call it. Toward
  an ACP agent the answer is always `allow_once` or `reject_once`: the spec leaves a remembered decision's scope and
  lifetime to the agent (it may outlive the lease) and says automatic approval should not rest on an option's
  `name`. "Allow for this lease" is kept in the relay's own memory, as `lease-relay.py` already does for domains,
  and still never applies to commands (`ask_once`).
- **Elicitation → questions.** Lease sessions advertise `elicitation: {form: {}}`, never `url` (the phone cannot
  give a URL page the secure context the spec requires). A form maps onto the neutral question structure
  (`runtime_claude.neutral_question`'s shape): each `enum`/`oneOf` property is a question with options, an array of
  enums is `multi`, a free string/number/boolean property is a question with no options. The owner's answer comes
  back as `accept` + `content`; no answer in 5 minutes → `cancel`. Headless runs advertise no elicitation (above).
- **The phase-two deny list: decided in part, open in part.** For Claude it is a settings file the tool enforces
  (`runtime_claude.lease_settings`: deny reading credentials, other projects, writing outside). ACP has no portable
  settings file, and an agent may read without asking. Decided: the relay refuses any permission request whose
  target falls under `policy.deny_read` / `deny_write`, which covers only what the agent asks about. Open, and to be
  settled in the follow-up's own design before leases over ACP are switched on:
  - covering what the agent does without asking needs the platform's sandbox around the agent process
    (`isolation.py`). But the agent must read its own login (its config directory, on macOS the Keychain), so the
    profile needs per-agent credential exemptions the policy does not have today;
  - discuss mode is read-only for Claude through its tool list; for ACP it would need a sandbox profile with the
    workspace read-only, which `seatbelt.service_profile` does not provide (it allows writes to the project);
  - the network is not enforced at that layer (Seatbelt cannot filter by domain, `tool-agnostic.md`), so the
    `listed` domains rule holds only for requests the agent asks about;
  - where the agent's own sandbox would be nested (macOS Seatbelt cannot stack), it may have to run with its own
    sandbox off.

  Until those are settled, leases over ACP stay off (`Unsupported`), which is what this step ships.
- **Resume.** `session/resume` when advertised, else `session/load` (replayed updates are swallowed, not
  re-emitted); neither → the runner starts a new conversation and says so, as today. No fork.
- **The platform's own log.** The runner already keeps every lease event; that is the transcript for leases over ACP
  (`tool-agnostic.md` "the platform keeps its own neutral log").

### 5. Configuration

`hub.json`:

```json
"runtime": {
  "default": "claude",
  "acp": {
    "agent": "codex",
    "agents": {
      "codex": {"command": ["npx", "-y", "@agentclientprotocol/codex-acp@2.5.0", "-c", "model={model}"],
                "login": "run `codex login` once on the hub machine",
                "models": {"standard": "…", "strong": "…"}},
      "claude": {"command": ["npx", "-y", "@agentclientprotocol/claude-agent-acp@0.99.1"],
                 "credential": {"env": "<the agent's key variable>", "file": "<file in the secrets directory>"}}
    }
  }
}
```

- `agents.<name>.command`: argv, no shell. Placeholders: `{model}` (with no model, dropped together with the flag
  before it, section 2) and `{cwd}`.
  Pin package versions: an unpinned `npx -y` fetches new code on every run.
- `agents.<name>.env`: extra variables (no values of credentials).
- `agents.<name>.models`: this agent's group → model (`runtime_ports.models("acp")` reads the selected agent's
  table; the module has no static `MODELS`).
- `agents.<name>.mode`: the session mode to run in (section 2); not offered by the agent = refused.
- `agents.<name>.credential`, `.auth_method`, `.login`: section 3.
- The keys are in `contracts/hub.v1.schema.json` under `runtime.acp`, closed (`additionalProperties: false` on
  `acp`, each agent and `credential`), so a misspelled `credential` is an error, not a silent fallback to the agent's
  own login. `agenthub config check` also runs the adapter's own `config_errors`: `agent` must name an entry; an
  agent's `env` may not set `PATH`, `HUB_*`, `LLM_*` or its credential variable, nor hold a value that looks like a
  key; and `acp` used (as the default or on a point) without `runtime.acp` is an error. It also checks that
  `runtime.default` is a registered runtime. Doctor shows the login of the default runtime and of every runtime a
  point is pinned to.
- `agent`: which entry the `acp` runtime uses. One ACP agent per deployment at a time; switching is one setting.
  Running two ACP agents side by side would need runtime names with a parameter (`acp:<agent>`) in `runtime_ports`;
  not needed yet.
- Choosing ACP for one judgment point: `hub.json` `llm.points.<point>.runtime: "acp"` (new, next to the existing
  `.model`; `llm.point_runtime` reads it before the declaration's own `runtime`). `agenthub config check` rejects a
  name that is not a registered runtime, a runtime pin on a completion point (those go to `llm.providers`), on a
  streamed point or one resumed with the default runtime's own arguments (`runtime_ports.RUNTIME_ARG_POINTS`: a pin
  would drop them), and a copy-only runtime (`acp`) on a point not in `runtime_ports.THROWAWAY_POINTS`. The first use is the propose mailbox handler:
  `"llm": {"points": {"mailbox-propose": {"runtime": "acp"}}}`, with `mailbox.handler: propose`: the one agent job
  already confined to a throwaway worktree. `runtime.default: "acp"` is refused by config check: as the default it
  would run nothing (section 6).

### 6. What is off, and what doctor shows

`runtime_ports.capabilities("acp") == {"agent", "auth"}`. Everything else is `Unsupported` with the port's name as
the reason. ACP is used only pinned on single points, so doctor's feature table (which describes the default
runtime) stays the default runtime's; what doctor adds for ACP:

| Doctor line | When |
|---|---|
| agent login (ACP agent X, its own login / key file): present or missing | any point is pinned to `acp` (doctor walks the default runtime and every pinned one) |
| "the ACP agent X has no mode set: what it writes outside the throwaway copy … is held only by its own default mode" (warn) | `acp` is pinned and the selected agent has no `mode` |

Why ACP cannot be the default: mailbox dispatch (`hubbus dispatch`) loads the default runtime's `agent` **and**
`sessions` before it delegates anything (it has to know which sessions are live), and every agent-shaped point
other than `mailbox-propose` is refused outside the throwaway copy. As the default, ACP would run nothing, so
`agenthub config check` refuses `runtime.default: "acp"` with that reason (a runtime declaring `THROWAWAY_ONLY`).
If it is set anyway (the check skipped), the refusals still name the reason, doctor warns under the feature table,
and the claude module's hourly login check prints "not checked" and leaves its alert untouched rather than
alerting on refusals.

The feature table itself was wrong for every runtime without sessions: `runtime_ports.features()` listed mailbox
tasks as needing only `agent` + `auth`, so doctor claimed the feature for `runtime_command` while dispatch refused
it. This step corrects the row to `agent`, `auth`, `sessions`, matching the code.

`llm.py exec` for an ACP point says "this runtime does not support long-running streaming sessions" (already
there). The librarian's `librarian-state`, `librarian-index` and `librarian-todo-format` are agent-shaped; only its
`*-edits` points and `reflect-week` are completion points, which go through `llm.providers` and cannot be pinned to
a runtime.

### 7. Neutrality

All ACP syntax (method names, field names, stop reasons, option kinds, error codes) lives in `runtime_acp.py`. The
only additions elsewhere are neutral: `policy.APPROVALS["unattended"]`, `"acp"` in `runtime_ports.RUNTIMES`, the
`HUB_THROWAWAY_COPY` mark and the all-or-nothing and hold-and-ask rules in `propose_apply.py`, the login check's "not checked" line, `runtime_ports.THROWAWAY_POINTS` /
`throwaway_only`, `llm.point_runtime` + its config checks, config-check calling a runtime's `config_errors`, the
doctor line, the schema keys.
`runtime_acp.py` imports no other runtime adapter.

## Tests

- **Unit**: `tests/fixtures/fake_acp_agent.py`, a fake agent speaking JSON-RPC over stdio, scripted by environment:
  a normal turn (chunks with `messageId`, a tool call, a permission request, `usage_update` in USD, `end_turn`),
  `max_turn_requests`, `refusal`, a failure (stderr + exit), `-32000` (and `authenticate` clearing it), a protocol
  version mismatch, an `fs/read_text_file` request (must get `-32601`), permission requests outside cwd or with no
  path, only an `allow_always` option, no stop reason, a malformed answer, a cost-less usage update, a hang
  (timeout kills the group), a model via a grouped config option. Plus: a working directory not marked by
  propose_apply is refused before the agent starts, also when it is the process's own; `extra` is refused;
  `llm.points.<point>.runtime` routing and its config checks; `runtime.acp` checks; a configured `mode` set before
  the prompt (or refused); propose_apply's copy sharing no git settings while the real repository moves on. The fake checks what it
  receives (absolute cwd, no client capabilities, `allow_once` chosen) and fails loudly otherwise.
- **Contract**: an `AcpFixture` in `tests/test_runtime_contracts.py` runs the agent and auth contracts.
- **Live, opt-in** (`ACP_LIVE=1`, never in the gate): one small read-and-answer task with structured output against
  each configured agent in a throwaway directory: `claude-agent-acp` and at least one non-Claude agent
  (`codex-acp`).

## Steps

1. This document + blind review.
2. `runtime_acp.py`, `policy.APPROVALS["unattended"]`, registration, fake agent, contract fixture, unit tests.
3. Live runs; results recorded below.
4. Follow-up (its own task): the long-running session port and lease-runner on it (section 4).

## Review

- Round 1 (blind, 11-01): one blocking finding: the first draft let agent-shaped points run with `runtime.default:
  acp` in real checkouts, their per-point limits silently dropped. Fixed by refusing any run the platform does not
  confine (section 2). Should-fix findings folded in: `mcpServers` is additive, not strict; the `{model}` flag-drop
  rule for `-c model={model}`; permission requests classified from the tool call's merged state, `diff` paths, both
  ends of a move; never selecting `allow_always`/`reject_always`, in leases too; the lease deny list's open
  questions stated as open; `authenticate` with a configured method; credentials for the selected agent only, the
  key file declared as a secret; per-point runtime in hub.json instead of editing the platform's declarations; the
  cancel and `cancelled`-outcome semantics; nits (method lists, `clientInfo.version`, `extra`, `cwd` None,
  `refusal` as incomplete, grouped model options, `MODELS`).

- Round 2 (blind, 11-01): two blocking findings, both fixed: a missing `--cwd` made the run happen in an empty
  temporary directory (the librarian and consolidate pass the directory through the subprocess), now the process's
  directory under the same check; and `acp` could be pinned where it can never run, with doctor silent, now refused
  by config check (`THROWAWAY_POINTS`) and named by doctor. Should-fix, fixed: the worktree's shared `.git` (snapshot
  and restore in propose_apply); bounded writes and waits; malformed messages fail cleanly; a cost-less usage update
  keeps the cost; closed schema and `env` checks; the "no option" cancel says it was the platform; the `extra`
  message; unknown stop reasons are not success; `initialize` inside the login-error handling.

- Round 3 (blind, 11-01): two blocking findings, both fixed: the shared-git snapshot of round 2 failed normal
  propose runs whenever the platform's own memory sync or a session moved a ref (and could revert others' config),
  replaced by independent clones, which share nothing; and §2 claimed the place contained what an agent with a
  shell writes by absolute path, now stated as the agent's own mode (checked when configured), with the platform
  sandbox named as the postponed owner decision. Should-fix, fixed: bounded cleanup; `session/cancel` before
  `cancelled`, not ok whenever the platform cancelled; `~`/`$` paths and any path-like write target; config check
  and doctor cover a runtime pinned to single points; the hourly login check says "not checked" instead of a false
  alert; `is_login_error` matches the prefix only.

- Round 4 (blind, 11-01): two blocking findings, both fixed: the copy's `origin` pointed at the real repository,
  so a push from it landed there (now no remote, no tags); and the agent could leave the configured mode mid-turn
  unnoticed (now cancelled and failed). Should-fix, fixed: `acp` as the default is refused by config check (it
  would run nothing); doctor warns when the pinned agent has no `mode`; the copy brings content filters, LFS and
  git-crypt keys so the agent reads what the real checkout shows. Nits, fixed: write targets judged by key names
  (never content), a late tool status update no longer splits the answer, `auth_method` checked against the offer,
  an empty key file is missing for doctor too, and the login check no longer sends a recovery it did not check.

- Round 5 (blind, 11-01): one blocking finding, fixed: setting the model through a config option could reset the
  mode as a dependent change reported only in that call's answer, which the adapter did not read; now the model is
  set first, every answer and update is read, and the mode and model are held for the whole run (a mid-turn model
  fallback fails it too). Should-fix, fixed: permission requests after the platform cancels are answered
  `cancelled`; propose applies nothing from a failed or cancelled run; git-crypt keys copied, not linked, and the
  shared LFS store stated; doctor names an `acp` pin that never runs (mailbox handler not propose); confinement
  also checks the point and the place, not only the variable; doctor's hints come from the adapter; path keys
  matched by exact name or suffix, never content.

- Round 6 (blind, 11-01): one blocking finding, fixed: after the run propose_apply ran `git diff`/`git status` in
  the agent's copy, so an `fsmonitor` or filter the agent wrote into the copy's `.git/config` would have run as the
  platform; now no git runs in the copy after the agent, changes are found by comparing files with the base tree
  read from the real repository. Should-fix, fixed: a model given in the command is held when the agent reports
  it; symlinks are never applied. Noted: everything so far is tested against the fake agent; the live runs below
  are what checks real agents.

- Round 7 (blind, 11-01): one blocking finding, fixed: round 6's file-hash comparison misjudged content filters,
  line endings, modes and symlinks (untouched files reported, mode changes missed); replaced by git status on the
  real repository's metadata with a throwaway index and the copy as work tree. Should-fix and low, fixed: no file is
  read whole; deletions applied first; the copy's remote removed only after checkout (LFS); the structured answer
  found in an earlier message; and a pre-existing gap closed: the copy starts at HEAD, so a note file with
  uncommitted lines in the workspace used to be overwritten by the agent's version. Now an applied file the
  workspace also changed since the base is merged (both appended: both kept; otherwise `git merge-file`) and a real
  conflict is refused and listed under `conflicts`, never overwritten.

- Round 8 (blind, 11-01): one blocking finding, fixed: `git status` entered a submodule the agent initialised in
  its copy and ran a filter from that submodule's config (reproduced; now `--ignore-submodules=all`, with a test
  that fails without it). Should-fix and low, fixed: today's note created on both sides is merged, not refused; a
  failed or conflicting run hands back no output, so no letter is closed and no reply sent while nothing was
  applied; applied files are written in one step and read without following symlinks; file names that are not
  UTF-8; a failure to make the copy fails the run with the reason; the production layout (`.agent-memory` as a
  linked worktree, untracked cache on) is tested to leave the real `.git` byte for byte unchanged.

- Round 9 (blind, 11-01): two blocking findings, fixed: a failed propose run left its letter claimed by the
  handler for good, so it was never dispatched again although the code said so (pre-existing; now released once,
  then held with a question to the owner, `job.failed` always, and claims are one event per run); and dot files
  under the allowed memory path (`.gitignore`, `.gitattributes`) would have been applied and then followed by the
  platform's own `git add`. Low, fixed: nothing under a symlinked directory is applied; a file a session writes
  while the others are being merged is merged again (or left as the workspace has it and listed); timeouts and
  status errors fail the run cleanly.

- Round 10 (blind, 11-01): two blocking findings, both from round 9's `released` event re-entering dispatch (a
  released letter with a resume claim forked every minute; a released-then-resumed letter was never dispatched
  again). Resolved by removing the release: hubbus is unchanged from before this work, and a failed propose run
  holds its letters and asks the owner (with `job.failed`), as does any break in the mailbox run or a write error
  during the apply. Low, fixed: read targets judged by place keys too; a denial may select `reject_always` when
  that is all the agent offers.

- Round 11 (blind, 11-01): one blocking finding, fixed: a propose run that ended ok without the structured answer
  (prose instead of JSON, a real case with ACP) left its letters claimed with no word; now that is a failed run
  (held, owner asked), as is a letter the agent neither answered nor closed. Should-fix and low, fixed: runtime pins
  refused on streamed and resumed points; options held by id; a mode config option is used exclusively; errors in
  the apply's re-merge report what was applied; an apply-time conflict is told to the owner; non-USD usage keeps
  the last USD cost; `env` values typed as strings.

- Round 12 (blind, 11-01): one blocking finding, fixed: round 11's "neither answered nor closed" rule also caught
  the letters the prompt tells the agent to leave open (owner judgment, code change), recording a failure and
  asking the owner twice; deferral is now explicit in the contract (`deferred`). Minor, fixed: the apply's
  temporary files are made outside the work tree; a read or search naming no place works in cwd (a write must
  name its target); only a JSON object counts as the structured answer; an error while recording the result rolls
  back and holds the letters with the reason. Noted: questions from `hold_and_ask` come from agent-hub, as
  `hubbus.hold()`'s do.

- Round 13 (blind, 11-01): **no blocking finding.** Taken anyway: an apply-time conflict asks the owner (a
  question naming the letters, not a notice); the rollback is guarded; the copy's remote name is explicit; a run the
  platform cancelled reports that reason even when the agent ignores the cancel. Left as follow-ups (not blocking):
  a propose process killed outright leaves its letters claimed (hubbus does not time out a `running` status;
  pre-existing); a doctor warning for an agent without a "no configured MCP servers" flag; a proactive login check
  for runtimes pinned on points; the e2 propose eval scoring `deferred`.

- Rebase onto main (11-01): meanwhile main moved every git command in a project's repository to run as the project
  (`project_git.py`, `projects-and-repos.md`), with its own copy, change listing and copy back. That design
  supersedes the clone, status and merge mechanics rounds 3–9 built here, which are dropped; kept from this work are
  the mailbox-level rules (all or nothing, the structured answer, hold and ask, `deferred`, dot paths) and the ACP
  adapter. The place check now judges the copy by its path. "confined" in this work was renamed "throwaway copy"
  (`THROWAWAY_POINTS`, `throwaway_only`, `HUB_THROWAWAY_COPY`): main's `confine` port means something else
  (a runtime honouring a point's declared permissions), which ACP does not have, so main's "unattended agent jobs
  with allow:" row is off for it too. Gaps from the earlier rounds that the superseded mechanics had closed and
  main's do not (to be judged against main's design, not here): a note a session also changed since the base is
  overwritten by the copy back; nothing under a symlinked directory is checked; the agent's copy is a linked
  worktree sharing the project's `.git`.

- Round 14 (blind, 11-01, on the rebased commit): **no blocking finding.** Taken: the commit message describes
  what the commit does (it had kept the superseded clone/status/merge text); the permission classifier walks lists
  and nested objects, knows more place keys and `file:` URIs, and judges a read or search by any place-like string
  it carries; `.git` is compared case-insensitively; an incomplete run's stop reason reaches the owner; deferral
  reasons are in the run's result; a model given in the command is held whenever the agent reports a model option.

## Live results

**11-01, both passed**, run by the owner on the hub machine (the sessions were not allowed to fetch and run the
packages with `npx`). The task read `secret-word.txt` in the throwaway copy and answered with the schema's
structured output, with no denials and stop `end_turn`:

| agent | result | time | cost reported |
|---|---|---|---|
| `@agentclientprotocol/claude-agent-acp@0.99.1` | ok, `{"word": "periwinkle"}` | 18.1 s | $0.12 |
| `@agentclientprotocol/codex-acp@2.5.0` (Codex's ChatGPT login) | ok, `{"word":"periwinkle"}` | 16.3 s | none (`cost_usd` null) |

This checks a read-and-answer task end to end through `runtime_acp.py` with two real agents from different vendors.
It does not yet check write paths, permission requests that need the relay, session modes or model options against a
real agent; those are still tested only against `tests/fixtures/fake_acp_agent.py`.

To run them on the hub machine (opt-in, never in the gate):

```
ACP_LIVE=1 ACP_LIVE_COMMAND='["npx","-y","@agentclientprotocol/codex-acp@2.5.0"]' bin/py -m unittest tests.test_runtime_acp.Live
ACP_LIVE=1 ACP_LIVE_COMMAND='["npx","-y","@agentclientprotocol/claude-agent-acp@0.99.1"]' bin/py -m unittest tests.test_runtime_acp.Live
```

The first uses Codex's own ChatGPT login; the second needs a Claude login readable where it runs (under SSH the
Keychain is not, so a login error there is a result to record, not a bug in the adapter). Record here what passed.
