# Runtime abstraction: every layer of the platform is an interface, Claude Code is just one implementation

> **Status (11-13)**: steps 1, 2, 3, 5 and 6 built. Since the 10-25 note below: `templates/CLAUDE.md` is renamed
> (`templates/agent-instructions.md`), layer 5 moved to `bin/sessions.py` (sessions from every agent, not a runtime
> port), the lease runner no longer unsets the token, and `classify_exit` uses `is_login_error`. Still open: step 4 (the
> long-running session port; the lease runner still builds Claude's flags), judgment points declared neutrally
> (`platform_tools`), the job renames (`claude-login`, `update-claude`), `lease-resume` on the login port, and the
> runtime-boundary ratchet at zero.

> **Progress** (10-25): steps 1 (skeleton), 2 (layer 5, conversation logs), 3 (layer 1, "run once"), 5 (layer 3 events
> `hub-event`, layer 4 rule files `hub-runtime`) and 6 (layer 6 login, interface `auth`) are done; all 24 projects
> switched to the new hooks. **Not done yet**: layer 1's long-running session (scheduled after the other session's
> lease neutralisation, see `lease-contract-neutral.md`), neutralising the judgment point declarations (waits for
> layer 2's approvals; adding `platform_tools` along the way), renaming `templates/CLAUDE.md`, renaming jobs
> (`runtime-login`, `update-runtime`).
> **Claude names layer 6 still has**: `lease-resume` (reads the token file directly and calls `claude --resume`;
> switched together with the long-running session's `resume_command`), the `unset/remove CLAUDE_CODE_OAUTH_TOKEN` in
> `run-agent-job.sh` and `lease-runner.py` (an extra safety layer; llm.py already goes through `auth_env`; can be
> deleted), `classify_exit`'s own copy of the login-error check (to switch to `is_login_error` later).
> **Acceptance for every step**: `bin/test-gate` (the commit gate) and the golden files: before refactoring, the
> externally visible behaviour is fixed as tests (layer 6: `tests/test_check_login.py` compares even the alert
> wording, and `tests/fixtures/llm-golden.json` was not regenerated).
> **One place still using the old entry points** (output identical to before, still working): the user-level worktree
> hook (U4, which the owner must change personally). agent-hub's own committed `.claude/settings.json` went on 11-01:
> its sessions get the same `hub-event` hooks as every project, installed in its local settings by the agent-settings
> applier, and `hub-event session_start` pulls the hub's main checkout (`hub_pull`). Every hook names the hub where it
> is when written (`hubconf.hub_bin`); after the hub moves, `agenthub config apply` writes them all again.

## Goal

The owner on 10-25: "all of these must become interfaces; Claude Code is just one implementation, and every layer
needs this kind of abstraction and generalisation."

Today agent-hub's coupling to Claude Code is spread over six places (inventory in each layer). The goal: the
platform's code depends only on **interfaces the platform defines itself**, and every detail tied to Claude Code is
gathered into `ClaudeCode*` implementations; switching tools, or using a second one alongside (e.g. the assistant's
local models, other CLIs), means writing one more set of implementations, with no change to the rest of the platform.

## Principles

1. **Ports & adapters is the structure; Strategy is how the implementation is chosen.** One small interface per layer
   (a port, a Python `Protocol`, written the same way as Muninn's `core/ports.py`); implementations (adapters) are
   interchangeable Strategies, chosen by settings, and callers only get the interface.
   Not every layer looks like a Strategy: the hook layer is events (the platform defines events, each runtime
   connects to them), the permission layer is translation.
2. **Interfaces are designed down from what the platform needs, not abstracted up from Claude Code's features.** The
   test: do the interface's nouns and parameters still make sense for a completely different tool? If not (e.g.
   `allowedTools`, `--resume`, jsonl appear), the implementation's shape has leaked into the interface.
3. **At least two implementations: Claude Code + a fake one.** Each interface has a set of contract tests, and both
   implementations must pass them. An interface with only one implementation inevitably grows into that
   implementation's shape; the fake is also the test double for the rest of the platform (today's mailbox routing
   tests already use it that way). When a tool is really switched, a new implementation counts as a replacement only
   after passing the same contract tests.
4. **Switch layer by layer (strangler), no rewrite in one go.** For each layer: define the interface → wrap the
   existing code as a `ClaudeCode*` implementation → callers depend on the interface → add contract tests → delete the
   Claude details scattered around. Every step can go live and be rolled back on its own.
5. **One implementation = one module; the Strategy chooses a module** (as `bin/` is written today, module-level
   functions, no forced classes): the Claude Code implementation is `bin/runtime_claude.py` (shared by all layers;
   layer 2's translation functions are here too, see below), the fake one is `bin/runtime_fake.py`. The interfaces
   are in `bin/runtime_ports.py` (`Protocol`, only describing which functions a module must provide). Which one is
   used is written in `hub.json`'s `runtime.default` (currently `claude`), overridable by the project registry or a
   judgment point when needed; platform code gets the module with `runtime_ports.load(name)` and does not
   `import runtime_claude` directly.

6. **Conforming to the interface is checked mechanically, not by convention**: `runtime_ports.load(name)` checks that
   the module provides every function the interfaces require, and refuses to load it otherwise; the contract tests
   find every implementation from the registry automatically rather than a hand-written `[claude, fake]`: a third
   implementation cannot be added without passing the contract tests.
7. **Contract tests check semantics, not just whether something was called**: e.g. after a forked continuation the
   original conversation's context size and log are completely unchanged, `alive` is False after a conversation ends,
   `context_size` returns None for an unknown id, every `send` has exactly one `turn_end`. So the Fake has to really
   keep state. The Fake can also be chosen in settings (`runtime.default: fake` in a temporary hub): librarian and
   mailbox routing can run end to end without spending money on models.
8. **The whole system has one set of "action kinds"**: read, search, ask a person, open web, write file, run command.
   Layer 1's judgment point policies, layer 2's `approvals` and the permission contract with the assistant all use the
   same names, none naming them separately.
9. **A session carries which runtime it belongs to**: `SessionHandle = (runtime, id)`; the mailbox's presence records
   and the lease records get a `runtime` column; `hubbus send` gets the sending session from
   `runtime_ports.current_session()` (today it reads `CLAUDE_CODE_SESSION_ID` directly). Several runtimes can be
   registered at once and each session uses its own: "side by side, chosen per session", not "replace the whole
   thing"; old conversation logs remain readable.
10. **Failure policy stays above the interface**: `AgentRuntime.run` returns only a neutral `TaskResult` (success /
   failure, structured output, cost, tokens); `on_failure`, `job.failed` and cost accounting stay in `llm.py`, rather
   than each runtime implementing its own.

## Layers

Files: `bin/runtime_ports.py` (interfaces + `load`), `bin/runtime_claude.py` (the Claude Code implementation, all
layers), `bin/runtime_fake.py` (the fake implementation), `tests/test_runtime_contracts.py` (contract tests, run on both
modules). The interfaces below are written as classes only for readability; the implementations are functions of the
same names in a module.

### 1. AgentRuntime: having an agent do work

What the platform needs: run a judgment point once (give a prompt, working directory, policy and output format, get
the result), open a long-running conversation (a lease), continue an existing conversation, fork a continuation
without touching the original conversation, whether this conversation is still alive, how much context a
continuation would load, and the conversation's event stream.

```python
class AgentRuntime(Protocol):
    def run(self, task: Task) -> TaskResult: ...                 # once: prompt, cwd, policy, output_schema, timeout
    def open_session(self, spec: SessionSpec) -> SessionHandle: ...   # long-lived: id, cwd, policy, preamble, may ask
    def send(self, h: SessionHandle, msg: Message) -> None: ...  # text + attachments
    def events(self, h: SessionHandle) -> Iterator[Event]: ...   # neutral: text, tool_use, tool_result, turn_end, error
    def resume(self, sid: str, *, fork: bool, spec: SessionSpec) -> SessionHandle: ...
    def alive(self, sid: str) -> bool: ...
    def context_size(self, sid: str) -> int | None: ...
```

- Where it is today: `llm.py` (`claude -p` flags, output format), `lease-runner.py` (building flags, parsing
  stream-json), `runtime_claude.py`, `run-agent-job.sh`, `check-claude-login.py`/`update-claude.py` calling `claude`.
- `llm.py` becomes the "judgment point → Task" layer: it reads `llm-points.yaml`, builds a `Task` and hands it to
  `AgentRuntime.run`, no longer building Claude flags itself. The Claude-specific fields in `llm-points.yaml`
  (`allowed_tools`, `permission_mode`, `tools`) become a neutral policy (translated by layer 2); a judgment point can
  declare `runtime` (optional, default from `hub.json`): this changes the shared `llm-points.v1.schema.json`, synced
  with Muninn the same day.
- "May it ask a person" (permission relay, questions) is part of `SessionSpec`: the platform provides a neutral "ask a
  person" service (what is behind today's lease-relay: send to the assistant, wait for the answer), and each runtime's
  implementation connects it to its own permission mechanism (for Claude Code, `--permission-prompt-tool`).
- The event stream uses neutral event types; lease-runner already turns Claude's format into its own events
  (`assistant`, `tool_use`, `text_delta`), and that conversion moves into `runtime_claude.events`.
- **For the long-running session (lease) this document only defines the interface, no implementation**: the
  lease contract's neutralisation (the assistant's m10251446: a tool → kind translation table, events, quota, error
  codes, the permission relay deciding by kind) spans this layer and layer 2, and which session does it is not decided
  yet; until it is, neither side touches lease-runner's flags, stream-json or lease-relay.

### 2. PolicyCompiler: permission policy (owned by another session)

The neutral policy translated into each runtime's settings. Design and implementation are owned by the "sandbox design
and Claude Code coupling" session, see `docs/design/capabilities.md` (the format may change until the owner confirms).
This document only references it, defining nothing separately:

- **Neutral policy**: `policy.policy(name, index) -> dict`, fields `workspace`, `write`, `deny_write`, `deny_read`,
  `credentials`, `network`, `platform_commands`, `capabilities`.
- **Translation (= this layer's Claude Code implementation, module-level functions)**:
  `runtime_claude.settings(policy)` (the project's Claude settings), `runtime_claude.lease_settings(policy)` (the
  lease's settings), `runtime_claude.sandbox_rendered(...)` (the condition for opening work mode). The platform's own
  Seatbelt `seatbelt.profile(policy, cap)` is runtime-independent.
- **"Which actions need asking a person"** (`approvals`, keyed by action kind: read, search, ask a person, open web,
  write file, run command) is not settled yet; it will be defined in the m10251446 design, and layer 1's `SessionSpec`
  connects to it once it is.
- `AgentRuntime`'s `Task`/`SessionSpec` carry the neutral policy, converted to its settings by the corresponding
  runtime's translation functions.

### 3. LifecycleEvents: the platform's events, and how each runtime connects to them

The moments the platform needs to know about: session start (including restart after compaction), session end, about
to compact, a tool finished, the user sent a message; and at these moments, "put a piece of text into the agent's
context" (mailbox, memory reminders).

```python
class LifecycleBinding(Protocol):
    def install(self, workspace: Path) -> None: ...        # connect the runtime's native hooks to `hub event <event>`
    def parse(self, event: str, raw: bytes) -> HubEvent: ...   # native input → neutral HubEvent (session, cwd, event)
    def inject(self, event: str, text: str) -> bytes: ...  # neutral "put into context" → native output format
```

- On the platform side there is one entry point, `bin/hub-event <event>`: it reads the input with `parse`, calls the
  platform's handling per event (memory sync, mailbox, memory reminders, cross-project write guard, presence records),
  and outputs with `inject`. The handling code (hubbus hook, memory-nudge, guard, git-sync-memory) no longer knows
  names like `hookSpecificOutput` or `session_id`.
- Where it is today: `merge-hook-settings.py` (writes Claude's hook settings), `sync-local-setup.sh`,
  `worktree-memory.sh`, `hubbus hook`, `memory-nudge.py`, `guard.py`, `CLAUDE_CODE_SESSION_ID`.
- Mapping: Claude Code's SessionStart/SessionEnd/PreCompact/PostToolUse/UserPromptSubmit/PreToolUse.
- **Transition**: `hubbus hook …` stays as an adapter for now (calling `hub-event` internally), removed only after every
  project has rerun `sync-local-setup`: hook error output is discarded, and removing the old command directly would
  break memory sync silently.
- **The worktree hook is at user level** (`~/.claude/settings.json`, U4): per CLAUDE.md, changing that file needs the
  owner's direct confirmation in that session, so when layer 3 goes live this step is his to do. A runtime lacking
  some moment (e.g. no "before compaction") declares it unsupported in its implementation, and the platform handles it
  as unsupported (e.g. switching to timed sync) rather than pretending.

### 4. InstructionsChannel: how rules and memory instructions reach the agent

```python
class InstructionsChannel(Protocol):
    def write(self, workspace: Path, block: str) -> None: ...   # managed block, where the runtime reads it; untracked
    def ignore_paths(self) -> list[str]: ...                     # files to add to .git/info/exclude
```

- Where it is today: `sync-local-setup.sh` (the managed block in `CLAUDE.local.md`), the ignores in
  `onboard/recover/migrate`.
- The block's **content** (`templates/CLAUDE.md`, `templates/rules/`) belongs to the platform, is runtime-independent,
  and is renamed `templates/agent-instructions.md`; only "which file, what format" is the implementation's business
  (Claude Code: `CLAUDE.local.md`; another tool might use `AGENTS.md`).

### 5. TranscriptSource: reading conversation logs

```python
class TranscriptSource(Protocol):
    def sessions(self, since: datetime | None = None) -> Iterator[SessionInfo]: ...  # id, working dir, start/last time
    def turns(self, sid: str) -> Iterator[Turn]: ...    # who said it (human/assistant/tool/system), time, text, tokens
    def last_activity(self, workspace: Path) -> datetime | None: ...
```

- Where it is today: `hublib.py` (`~/.claude/projects/<slug>/*.jsonl`, `origin.kind == human`, the slug rule),
  `weekly-reflect.py`, `check-memory-gaps.py`, `trajectory.py`, `worktrees.py` (idle days), `migrate-to-mini.py`,
  `runtime_claude.context_tokens`.
- This layer is **the most likely to break silently**: Claude Code's conversation logs are an undocumented internal
  format. `ClaudeCodeTranscripts` checks the format as it reads (an unknown version → explicit failure, red on the
  status page), so statistics are never silently wrong.

### 6. AuthProvider: login state

```python
class AuthProvider(Protocol):
    def check(self, mode: str) -> AuthStatus: ...   # usable or not, when it expires; mode = unattended / subscription
    def env(self, mode: str) -> dict: ...           # the environment handed to AgentRuntime (values never printed)
```

**The shape as implemented** (10-25, `runtime_ports`' `auth`): `auth_env(mode, env) -> (env | None, reason)`,
`clean_env(env)`, `credentials() -> [Credential]`, `is_login_error(text)`. The difference from the draft above: "is it
usable" is not the runtime's answer: actually making a request (`llm.py run login-check`) is the platform's check; the
runtime only describes which logins exist, expiry, thresholds, how to log in again (`Credential`), and tells whether an
error is a login problem. mode keeps llm-points' `token`/`subscription` (a declaration shared with Muninn; renamed
when neutralised). `check-claude-login.py` rebuilds its original sentences from `Credential` (alerts are deduplicated
by wording, so word for word); one deliberate change: "claude not found" on the token run also counts as needing a
person.

- Where it is today: `llm.py auth_env` (headless token / subscription login), `check-claude-login.py`,
  `update-claude.py`, `lease-resume`.
- The platform's jobs (`agent-hub/claude-login`, `update-claude`) become "check every runtime the settings use", renamed
  `runtime-login`, `update-runtime` (with the registry's `legacy_name`, so their state moves along).

## Out of scope

- **Claude Desktop's session management** (listing sessions, archiving, "tidy sessions"): those are Desktop's tools for
  sessions, not something platform code calls; on the platform side only `AgentRuntime.alive/resume` needs the
  concept.
- **Writing a second real runtime now**: not done. The Fake proves the interfaces don't leak; a real second one (the
  assistant's local models, other CLIs) is written when needed.
- **The logic of the mailbox, memory and librarian themselves**: they already belong to the platform; only how they
  get runtime information changes, through the interfaces.

## Steps (each goes live on its own)

1. **Skeleton**: `runtime_ports.py` (interfaces, `load`'s mechanical check, `current_session`, `runtime.default` in
   `hub.json`), the contract test framework, `runtime_fake.py` (stateful). This step touches no other file and can be
   done right away.
   **`runtime_claude.py`, `render-project-settings.py` and `lease-runner.py` are touched only after the other
   session's first layer-2 step (`policy.py`, moving the settings translation into `runtime_claude.py`) is
   committed**: it changes those three at the same time.
2. **Layer 5 TranscriptSource** (the most likely to break silently, and the smallest; the Claude implementation goes
   into `runtime_claude.py`: the other session's step 1 is committed (2568776), so it can be touched now; it will
   later change `lease-runner.py`'s preamble, so tell it before touching that one): `hublib`'s conversation-log
   parsing, `runtime_claude.context_tokens` and `worktrees`' idle days go through the interface; add the format check.
3. **Layer 1 AgentRuntime "run once"**: `llm.py` builds a `Task` and hands it to the interface; every scheduled job
   follows automatically without changes. Neutralising `llm-points` is done together with layer 2 (it waits for that
   session's policy format).
4. **Layer 1 long-running session**: lease-runner goes through `open_session/send/events/resume` (confirmed together
   with the layer 2 session).
5. **Layers 3 and 4**: the `hub-event` entry point, `LifecycleBinding`, `InstructionsChannel`; each project's hooks and
   managed block switch to the new ones the next time a session opens (`sync-local-setup.sh` already does that).
6. **Layer 6 AuthProvider**, renaming the platform jobs.
7. **Wrap-up**: `grep` finds no `claude`, `.claude/` or Claude flags outside `runtime_claude.py` (added to the nightly
   contracts-check as a lint, the same kind of check as today's "no direct claude -p in bin/").

## For the owner to confirm

0. (This document and the other session have aligned the boundary: layer 2, capabilities and the lease contract's
   neutralisation are that session's or undecided; until it commits step 1, this work does not touch
   `runtime_claude.py`, `render-project-settings.py` or `lease-runner.py`.)

1. The overall approach (ports & adapters + Strategy choosing implementations + Claude Code / Fake implementations
   passing the same contract tests + switching layer by layer).
2. The order (layer 5 first, then layer 1).
3. `llm-points.v1.schema.json` gains `runtime`, and the Claude-specific fields are replaced by a neutral policy: this
   touches the format shared with Muninn, so both sides must sync the same day.
4. When layer 3 goes live, the worktree hook (user-level settings) must be changed by you personally.
5. When renaming `templates/CLAUDE.md` → `templates/agent-instructions.md`, the skills and the two CLAUDE.md files that
   reference it change together.
