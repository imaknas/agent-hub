# Runtime versions and compatibility

Status: design, 11-08. Not built. Owner, 11-08: "brunna has two adapters but no way to fix their versions: one day Claude Code or
the Codex app has a breaking change, the user updates without meaning to, and brunna may just break". Owner: design this first,
before Codex Step 1 (`docs/design/runtime-codex.md`), which builds the platform's own Codex home and `update-codex`.

## What can change under the platform

Three surfaces, and only one of them is the platform's to choose:

- **A. The platform's own runs** (judgment points, completions, mailbox forks and triage, leases, `brunna complete`). The platform
  picks the binary. Today that is whatever `hubconf.tool("claude")` finds (bin/hubconf.py:391), in practice nvm's global copy, the
  same one the owner runs in a terminal; Codex runs only as a `runtime_command` template resolved on PATH.
- **B. The user's own sessions** (hooks, wake, session status, transcripts, rules injection, the sandbox settings of a sandboxed
  project's session). The user's tool picks the binary and updates it itself: Claude Desktop's SSH sessions run its own
  `~/.claude/remote/ccd-cli/<version>` (2.1.308, .309, .317 on this machine), Claude Code auto-updates, and Codex's shared daemon
  updates itself under `~/.codex/packages/app-server-daemon/releases/` (0.166.0 and 0.167.0 here, while npm's CLI was still 0.165.0).
  The platform can't pin these and shouldn't try.
- **C. The provider's servers.** The model catalog, model aliases and the tools a model gets change with no binary change: on 11-08
  every Codex model became `tool_mode: code_mode_only`, which made "shell off" stop meaning "no tools" (runtime-codex.md, M1 again).
  Pinning a binary doesn't stop this.

So the answer is not "pin the version". It is: **the platform's runs use a copy that passed a check, every run re-checks what can
change from the server, and the user's sessions are watched so a break is said, by version, instead of going quiet.** This is the
tool-neutrality rule (AGENTS.md: "when a capability is missing, turn the feature off explicitly and say why") applied over time.

## What exists today (survey 11-08)

- `bin/update-claude.py` (nightly 01:15, claude module): when npm has a newer Claude Code, a real request on the current version,
  `npm install -g …@latest`, then `--version`, the same request and one real completion with the completion flags
  (`--restricted`) on the new one; any failure reinstalls the old one and alerts (`claude-version`). Separately, every version found
  in nvm's copy gets the folder-git probe (`claude-git-filter`) once, recorded in `.state/claude-filter-probe.json`.
- `bin/check-claude-login.py` (hourly): a real request per credential, expiry dates.
- No minimum versions, no version gating, no doctor row with a runtime's version. "Tested on 2.1.316" lives only in comments.
- Hooks (`bin/hub-event`): a payload that isn't JSON, lacks `session_id` / `cwd`, or names an unknown event returns before the log
  line (hub-event:336-338), so a changed payload is a silent no-op; the header's "every run logs one line" doesn't hold. A wrongly
  typed payload raises and is logged to `.logs/hub-event.log`, which nothing reads.
- Session readers (`sessions_claude.py`, `sessions_codex.py`) fail only when a whole file has no known line type; unknown line types
  and malformed entries are skipped, and neither reads the tool's version (Claude's lines carry `version`, Codex's `session_meta`
  carries `cli_version`).
- No test runs a real `claude` or `codex`. `tests/fixtures/llm-golden.json` pins what the platform sends, not how the tool answers.
- The contract surface with Claude Code is wide: ~25 CLI flags, `claude agents --json` and the text of `claude mcp list`, four
  environment variables (`CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS` carries a security property), `-p` JSON result fields, the
  stream-json event types, hook events, matchers, input fields and output shapes, settings keys (`sandbox.*`, `permissions.*`, rule
  syntax), transcript paths and their line format. Codex's will be as wide.

## The design

### 1. Conformance checks, per port, owned by the adapter

A neutral runner, `bin/conformance.py`, runs an adapter's checks against **a given binary** and records the result. The adapter
declares them (`runtime_claude.CONFORMANCE`, later `runtime_codex.CONFORMANCE`), each tagged with the port it guards and whether it
is a **safety** check (a confinement property) or a **function** check:

| Port | Checks (Claude today's; Codex's from runtime-codex.md) | Kind |
|---|---|---|
| completion | one completion per schema in `contracts/llm/` returns schema-valid output; the run had no tools (Claude: `--tools ""` asked to run a command, no tool use in the result; Codex: the tool list a run sees equals the pinned list) | function + safety |
| agent | a run's result fields parse (`result`, `is_error`, usage); `--model` aliases in `MODELS` resolve | function |
| confine | the sandbox profile denies what the policy denies (Claude: settings `sandbox` + `permissions.deny`; Codex: the permission profile, M8's cases: read, write, `.git`, links, rename); folder git off (`NO_FOLDER_GIT`, today's filter probe) | safety |
| lifecycle | a headless run in a throwaway home with the platform's hook set fires each event the platform installs, and each payload has the fields the adapter reads (`parse_event`'s inputs); hook output is honoured (additionalContext reaches the model; a PreToolUse deny stops the tool) | function (deny: safety) |
| sessions | that run's transcript / rollout parses with the reader, with no skipped lines and its version read; `claude agents --json` parses | function |
| stream | a short stream-json exchange parses (leases) | function |
| tool_servers | `claude mcp list` parses with no server and with one | function |
| auth | the login check | function |

Checks run in a throwaway home and folder (as update-claude's probes and the M8 runs did), never the user's. Each needs a model
call or two; a full pass is a few dozen calls, run only when a version is new (below). A test (`tests/test_conformance_cover.py`)
fails when an adapter implements a port with no check for it, so a new port can't ship unguarded.

Results: `.state/runtime-conformance/<runtime>/<version>.json`: per check pass / fail / error with the reason, the binary's path and
digest, and the time. `<version>` is what `--version` prints, plus the digest, since two installs can report the same version.

### 2. The platform's runs use their own copy, promoted only after a pass

- Each runtime's platform copy lives in the instance: `<instance>/runtimes/<runtime>/<version>/` (an `npm install --prefix` there,
  or the tool's own installer), with `current` and `previous` links. `hubconf.tool` stops resolving runtimes for platform runs; the
  adapter's `binary()` returns `current`'s executable. The user's own copy (nvm's, Desktop's, the daemon's) is never what the
  platform runs.
- `update-runtimes` (neutral platform job, replacing `update-claude` with `legacy_name`): for each runtime in use, when the tool's
  source has a newer version, install it beside `current`, run the full conformance, and move `current` only if every safety check
  and every function check of a port the instance uses passes. Otherwise `current` stays, the failed checks go to an alert
  (`alert.once`, by runtime and version), and the status page shows which version is held back and why. Keep `current` and
  `previous`; delete older ones (a Claude copy is ~300 MB, Codex's ~350 MB).
- A minimum version per adapter (`MIN_VERSION`, e.g. Codex 0.167.0 for the linked login, M9): doctor and the runner refuse below it.
- `brunna runtimes` shows each runtime's platform copy (version, when promoted, held-back version and its failed checks) and the
  versions seen in the user's sessions (section 4). `brunna runtimes rollback <runtime>` moves `current` back to `previous`.
- First install (init / `brunna llm setup`): the same path, so a new instance starts on a checked copy.

### 3. What the server can change is re-checked on every run, cheaply

Promotion checks a binary once; surface C changes without one. So a run asserts the invariants that hold its confinement, and fails
closed (`Refused`, with the reason) instead of running wider:

- Codex: the tool list the run sees equals the pinned list for that point kind (completion: none but clock and server-less MCP
  resources; tool points: the profile's). How to read it without a model call is a measurement (V-M1: whether `codex debug
  prompt-input` lists code mode's nested tools); if it can't be read without one, the check runs once a day per model, not per run,
  and a mismatch turns the points off until it passes.
- Claude: a `-p` result with tool use on a `--tools ""` run, or a `permission_denials` entry on a point that declared none, is a
  refusal and an alert, not an answer.
- A model alias that stopped resolving is a loud failure (it already is: the run fails), named in the alert as "the provider dropped
  model X", so it isn't read as a login problem.
- A daily conformance subset (completion + the tool list) on `current` even with no new version, since the catalog moves.

### 4. The user's sessions: watched by version, never silently degraded

- **Record the version.** The hook reads it once per session (Claude: the `version` field of the transcript's last lines, the path is
  in the hook input; Codex: `session_meta.cli_version` from the rollout) and writes it into the session's record; the session readers
  return it per session.
- **Check the versions in use.** The nightly job also runs the session-side checks (lifecycle, sessions, confine for sandboxed
  projects) against each version seen in the last 7 days that has no result yet, using that binary where it is on disk (Desktop's
  `ccd-cli/<version>`, the daemon's `releases/<version>`, nvm's), and records it like any other.
- **Say it, by feature and version.** doctor and the status page show, per feature that rides on user sessions (letters seen live,
  wake, rules, notes reminders, a sandboxed project's session confinement): on, off with the failed check, or unverified (a version
  seen but not checked, e.g. no binary on disk). A **safety** check failing on a version the owner's sessions use (a sandboxed
  project's session not confined) alerts at once; a function check alerts once per version.
- **Hooks stop failing silently.** `hub-event` logs every payload it can't use (`unusable=<reason>` with the runtime and version),
  as the header already promises; a nightly count per version above zero is a doctor row and, for the events letters depend on, an
  alert. The worktree hooks' one-line `read -t 1` stays (it reads a path only).
- **Readers count what they skip.** Per file: lines skipped as unknown type or malformed. A version whose files skip more than a few
  percent is a doctor row ("the weekly statistics may undercount sessions of Claude Code X") instead of a quiet undercount.

### 5. The user's own copy

The owner asked on 10-24 that the terminal Claude not fall behind (that is why `update-claude` exists). With the platform's copy
separate, the user's copy is the user's: decision DV2 below says whether the platform still updates it, and to what.

## Steps

1. **Conformance runner + Claude's checks** (port update-claude's three probes in; add completion with every schema, lifecycle,
   sessions, stream, tool_servers), the results store, `brunna runtimes`, the coverage test. `hub-event` logs unusable payloads;
   readers count skips and read the version. No behaviour change for runs yet.
2. **Platform copies + `update-runtimes`** for Claude (promotion, hold-back, rollback, `MIN_VERSION`, doctor rows); `update-claude`
   retired by `legacy_name`. `hubconf.tool("claude")` callers for platform runs move to `binary()`.
3. **User-session versions**: recorded per session, checked nightly, doctor / status page / alerts by feature and version.
4. **Codex Step 1** builds on it: `runtime_codex.CONFORMANCE` (the M1/M8/M9 cases), its platform copy and home, the per-run tool-list
   invariant (V-M1).

## Decisions for the owner

- **DV1, the platform's own copy.** (a) A copy per runtime in the instance, promoted only after a pass; the user's copy never
  affects platform runs (recommended; ~650 MB on disk for both runtimes with `previous` kept). (b) Keep sharing the user's copy
  and only check it (a user's manual update still changes the platform's runs before any check).
- **DV2, the user's copy.** (a) The platform updates nvm's copy only to a version that passed, the same night it passes
  (recommended: the terminal doesn't fall behind, and doesn't get ahead of what's checked). (b) Leave it to the tool's own updater
  and only watch it. (c) As today, latest every night. Desktop's and the Codex daemon's copies update themselves in any case; they
  are watched (section 4), not controlled.
- **DV3, cost.** A full conformance pass is a few dozen model calls per new version (Claude Code ships several a week), plus a
  daily subset of a few calls. On the subscription; nothing billed per use unless `metered_models` says so.

## Measurements

- V-M1: does `codex debug prompt-input` (no model call) show code mode's nested tool list, for the per-run invariant?
- V-M2: does a `claude -p` run with `--settings` carrying the platform's hooks fire SessionStart, UserPromptSubmit, PostToolUse,
  Stop and SessionEnd, with the same payload fields an interactive session sends (so lifecycle can be checked headlessly)?
- V-M3: can Desktop's `ccd-cli/<version>` binary be run headlessly in a throwaway home (for checking the versions in use)?
- V-M4: the hook can read the session's version cheaply (the transcript's last lines) at SessionStart, before the first message.
