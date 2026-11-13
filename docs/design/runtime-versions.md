# Runtime versions and compatibility

Status: design, 11-08, revised after the blind reviews the same day (their findings are at the end); DV1–DV4 decided 11-09; the measurements done 11-09; DV5 decided 11-09 ((c); V-M11 done).
Not built. Owner, 11-08: "brunna has two adapters but no way to fix their versions: one day Claude Code or the Codex app has a
breaking change, the user updates without meaning to, and brunna may just break". Owner: design this first, before Codex Step 1
(`docs/design/runtime-codex.md`), which builds the platform's own Codex home and `update-codex`.

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

A and B also meet: some calls the platform makes with its own binary read what the user's binary wrote (`claude agents --json` over
the user's sessions, a fork of a user's transcript, the linked Codex login). Those are the **session-side calls** (section 5).

So the answer is not "pin the version". It is: **the platform's runs use a copy that passed a check and can't change after; what the
server can change is re-checked as runs go; every version the user's sessions run is checked when first seen and said by version,
and a sandboxed project's session on a version that failed a safety check gets no work from the platform.** Three rules over all of
it:
- **A missing or failed check never removes a protection; it only withholds work.** Deny rules, control paths, the hooks the platform
  keeps and the guard stay whatever a check says.
- **A check has three outcomes: pass, fail, not-checkable.** A port whose check can't be built yet (it waits on a measurement) is
  `not-checkable`: treated as today (gated per runtime, as `CAGED_NEEDS` does), never per version, never a promotion blocker, shown in
  doctor as an info row naming the measurement that would make it checkable.
- **Per-version gating is narrow**: only a sandboxed project's sessions, only on a **failed safety** check (never pending, never
  not-checkable, never a function check), only the handlers that start work. Everything else about a version is recorded, shown and
  alerted on, not gated.

This is the tool-neutrality rule
(AGENTS.md: work the platform starts is guaranteed by the platform; "when a capability is missing, turn the feature off explicitly
and say why") applied over time.

## What exists today (survey 11-08)

- `bin/update-claude.py` (nightly 01:15, claude module): when npm has a newer Claude Code, a real request on the current version,
  `npm install -g …@latest`, then `--version`, the same request and one real completion with the completion flags
  (`--restricted`) on the new one; any failure reinstalls the old one and alerts (`claude-version`). Every version found in nvm's
  copy, including ones Claude Code's own updater brought, gets the folder-git probe once (`claude-git-filter`), which has a positive
  control: it refuses to pass when the unguarded run doesn't trigger the filter (update-claude.py:158-166).
- `bin/check-claude-login.py` (hourly): a real request per credential, expiry dates.
- No minimum versions, no version gating, no doctor row with a runtime's version. "Tested on 2.1.316" lives only in comments.
  ACP's protocol version is the only version the platform checks (runtime_acp.py:55, :461).
- Hooks (`bin/hub-event`): a payload that isn't JSON, lacks `session_id` / `cwd`, or names an unknown event returns before the log
  line (hub-event:336-338), so a changed payload is a silent no-op; the header's "every run logs one line" doesn't hold. Empty stdin
  is the same path, on purpose (onboard, recover and sync-local-setup call it with none). A wrongly typed payload raises and is
  logged to `.logs/hub-event.log`, which nothing reads.
- Gating today is per runtime, not per version: `CAGED_NEEDS <= capabilities(runtime)` in hub-event:330-341 and
  render-project-settings.py:359, :379.
- Session readers (`sessions_claude.py`, `sessions_codex.py`) fail only when a whole file has no known line type; unknown line types
  and malformed entries are skipped, and neither reads the tool's version.
- `session_statuses` maps a failed `claude agents --json` to every session `unknown` (runtime_claude.py:451-453), and `unknown`
  counts as idle (runtime_ports.py:341): a listing broken by a version change makes letters wake busy sessions.
- No test runs a real `claude` or `codex`. `tests/fixtures/llm-golden.json` pins what the platform sends, not how the tool answers.
- The contract surface with Claude Code is wide: ~25 CLI flags, `claude agents --json` and the text of `claude mcp list`, four
  environment variables (`CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS` carries a security property), `-p` JSON result fields, the
  stream-json event types, hook events, matchers, input fields and output shapes, settings keys (`sandbox.*`, `permissions.*`, rule
  syntax), transcript paths and their line format, the files it loads as control (`USER_CONTROL`, runtime_claude.py:180).

## 1. A distribution port, so the job stays neutral

Getting, installing and pinning a tool is tool syntax, so it lives in the adapter as a new port, `distribution`:
`latest()` (the newest version the vendor publishes), `install(version, folder)` (the exact version from the vendor's source, its
integrity checked as the vendor publishes it: npm's `dist.integrity`), `version(binary)`, `pin_env()` / `pin_config()` (what turns
the tool's own updater off for a run of this copy: Claude `DISABLE_AUTOUPDATER=1` plus `npm_config_prefix` at a sink folder of
the store's own beside the copies (`<store>/claude/.npm-sink`), since its updater writes npm's global prefix, not the running copy; Codex `check_for_update_on_startup`, `features.daemon_auto_start`
and `features.in_app_updates` off in the platform home; V-M5), and `MIN_VERSION`. The neutral job and runner only call these. A runtime without the port (ACP, a
command template) keeps today's behaviour and doctor says it is unversioned.

## 2. Conformance checks, one or more per port

A neutral runner, `bin/conformance.py`, runs an adapter's checks (`conformance_claude.CHECKS`, later `conformance_codex.CHECKS`: each tool's checks in an adapter of their own)
against **a binary in the platform's store** and records the result. Each check guards a port and is a **safety** check (it holds a
confinement property) or a **function** check.

How every check runs:
- **In a throwaway world**: a throwaway project folder, tool home and config dir, and the platform's own state, data, secrets and bus
  pointed at temp folders (`AGENT_HUB_STATE`, `bus_dir`…), so a real `hub-event` firing in a lifecycle check can't touch the live
  bus or mark a letter told. All of it under `hubconf.private_tmp()` (hubconf.py:195), never the system temp folder, which every
  sandboxed project can write as the same user (a planted `.claude/settings.json` there would run as the user; update-claude.py:136
  learned this). Hooks are installed the way the platform installs them (`install_lifecycle` into the throwaway
  project, the worktree set into the throwaway home), not through `--settings`.
- **Safety checks have a positive control**, as the folder-git probe does: the same case without the guard must show the effect (the
  unguarded run reads the canary; a run with tools does use one), or the check fails as "tests nothing". **Model-free where it can
  be** (M8 used `codex sandbox -- /bin/sh -c …`; Claude's sandbox can be driven the same way through a Bash call the check scripts).
- **Canaries, never real secrets**: canary files at paths that go through the same policy → settings translation as real ones
  (a canary credential path, a canary `.git/config`, a canary control file), so a broken sandbox leaks a canary. Results keep
  pass / fail / error and the reason, never tool output or file contents.
- **Logins** (section 3, "a candidate never refreshes the user's login").

The checks (Claude's now, Codex's from runtime-codex.md):

| Port | Checks | Kind |
|---|---|---|
| completion | one completion per schema in `contracts/llm/` returns schema-valid output; the run has no tools (Claude: a positive-control run with tools shows the signal V-M7 names, the `--tools ""` run doesn't; Codex: the tool list equals the pinned one) | function + safety |
| agent | a run's result fields parse (`result`, `is_error`, usage, `permission_denials`); `MODELS` aliases resolve | function |
| confine | the sandbox denies the canaries the policy denies: read, write, `.git`, links, rename, a new file; folder git off (`NO_FOLDER_GIT`, today's probe) | safety |
| control | a canary write at each enumerated control path (`CONTROL`, `USER_CONTROL`, in a throwaway project and home), through the real policy → settings translation, is denied; positive control: the same write with that rule left out succeeds. (The one-rule form, section 7, replaces it in step 4.) | safety |
| lifecycle | each installed event fires with the fields `parse_event` reads; additionalContext reaches the model; a PreToolUse deny stops the tool | function (deny: safety) |
| instructions | a marker in the rules file the adapter writes (`CLAUDE.local.md`) reaches the model | function |
| sessions | the run's transcript / rollout parses with no skipped lines and its version read | function |
| session_status | `claude agents --json` lists a live throwaway interactive session (V-M6's pty driver) as `idle` after its turn | function |
| session_summary | the last reply is read from the throwaway session | function |
| wake | an idle throwaway interactive session (V-M6's pty driver; a `-p` or stream-json session never arms the waiter, runtime_claude.py:546-549, :588-592) takes a turn after `wake()`: its transcript gains the Stop-hook feedback and a reply | function |
| stream | a short stream-json exchange parses (leases) | function |
| tool_servers | `claude mcp list` parses with no server and with one | function |
| auth | the login check | function |
| distribution | `version()` of the installed binary is the version asked for; the install's lockfile has an integrity hash for every tarball it resolved (the wrapper and the platform package holding the executable, `optionalDependencies` included), each verified on install | function |

`tests/test_conformance_cover.py` fails when an adapter implements a port with neither a check nor a named `not-checkable` entry
with its measurement, so a new port can't ship unguarded or unaccounted for.

Results: `.state/runtime-conformance/<runtime>/<version>.json`: per check pass / fail / not-checkable / error with the reason, the
binary's path and digest, the time. A function check that fails is run once more before it counts (a model's answer can miss a schema once); a safety
check counts at once.

## 3. The platform's runs: their own copy, promoted only after a pass, fixed after

- **The store**: `<instance>/runtimes/<runtime>/<version>/`, installed by `distribution.install`; `current` and `previous` are links
  swapped by rename (atomic). A run resolves `current` once at its start, records the version and digest it ran with, and holds that
  path to its end, so a swap mid-run doesn't change what a running job or lease executes. A version folder is removed only when no
  process runs from it (the store keeps a lock per version a run holds); a held-back candidate is removed after 14 days.
- **Fixed after promotion**: every platform run of the copy carries `pin_env()` / `pin_config()` (its updater off), and `binary()`
  compares the executable with the digest recorded at promotion before the run (size and mtime each run, the full digest once a
  day), refusing on a mismatch with "the platform's Claude Code changed after it was checked". The same holds for every store copy a
  platform run executes, not only `current` (the copies of user versions that run forks and `lease-resume`, section 5). The digest check is the safety
  mechanism, not a conformance check: a pinned updater can't be shown to stay off without a newer version to update to, but any
  change it made is caught before the next run.
- **No checked copy, no run**: when there is no `current` (a fresh install, the vendor unreachable on first install, a version that
  never passed, a restore), the ports that **start platform runs** (agent, completion, stream, auth, and confine as the platform's
  own runs use it) are `Unsupported` with that reason and doctor is red; never a fallback to the PATH copy. The ports that **describe
  the user's sessions** stay: control (what `policy.protect()` denies, policy.py:134-139), lifecycle (the hooks
  render-project-settings keeps, :340-343) and the settings translation, so a sandboxed project keeps every deny with no copy in the
  store. `runtime_ports.load` gets the split as a parameter, not a second meaning of `Unsupported`. The no-copy refusal lives only in
  `binary()` and that `load` parameter: `runtime_ports.capabilities()` keeps reporting which ports the adapter implements, so
  `CAGED_NEEDS` (hub-event:342, :424; render-project-settings.py:379), which reads it, doesn't turn every sandboxed project's hooks
  off on a fresh install or a restore. doctor and llm.py read the no-copy state on its own. Mailbox dispatch splits its gate
  the same way: today it loads `("agent", "sessions")` and returns before any hold, route or wake when that fails (hubbus:2898-2906);
  routing, holds and wakes need only `sessions` (and `wake`), and only forks, resumes and triage need `agent` from a checked copy, so
  letters keep reaching live sessions with no copy in the store. `hubconf.tool` is no longer used
  for runtimes; `_env` puts the store copy's bin first on PATH and nvm's after it (plugin hooks need nvm's `node`,
  runtime_claude.py:666-680), so nothing a run starts finds the user's `claude` by name; `agenthub.agent_found` reports the store copy.
- **First install** (init, `brunna llm setup`, a restore): install the latest version into the store, log in (section 3a), run the
  checks, promote; until then the features say "no checked copy of Claude Code yet" and init says it is running the checks.
  init's agent question (`choose_agent`, bin/agenthub:517) keeps its options and stays the one place the runtime is chosen; what
  follows it changes. Today it looks for the user's copy and, on the owner's consent, installs one at user level, which the
  platform then runs. After: the store copy is installed and checked whether or not the user has one (it touches nothing of the
  user's, so it needs no consent, but init says first what it downloads, ~300 MB, and that the checks take minutes); the user-level
  install becomes an offer for the user's own use only, no longer needed for the platform. The login steps init already runs
  (Claude's headless token, Codex's linked `auth.json`) now serve the store copy. `brunna init --agent <other>` installs and checks
  the other runtime's store copy; the one no longer chosen stops updating (`update-runtimes` does nothing for a runtime not in
  use) and is removed after 14 days. Choosing `none` installs nothing; the judgment points can still use a provider's API.
- **`update-runtimes`** (a neutral platform job replacing `update-claude` by `legacy_name`, doing nothing for a runtime not in use):
  when `latest()` is newer than `current`, install it beside `current`, run every check, and promote only when every safety check and
  every function check of a port the instance uses passes. Otherwise `current` stays (held back) and the result goes to the status
  page and an alert keyed by **check and reason**, not by version (the next version failing the same way doesn't re-alert). Held
  back 7 days or more escalates once ("Claude Code has been held back at X since …"). A version below `MIN_VERSION` is never promoted.
- **Rollback**: `brunna runtimes rollback <runtime>` moves `current` to `previous` only after the safety checks pass on it again and
  it is at least `MIN_VERSION`.
- **Daily, with no new version**: the completion and agent checks on `current`, since the server side moves (section 4).
- **Backup**: the store is left out of the backup (it is rebuilt from the vendor; ~300 MB per Claude copy, ~350 MB per Codex copy, and
  OS-specific); a restore runs first install.
- **Linux**: Codex's bwrap is refused where unprivileged user namespaces are (Ubuntu 24.04), so its confine checks fail and no copy is
  promoted there: fail-closed, and doctor says why.

### 3a. A candidate never refreshes the user's login

The platform's copy needs a login, and a candidate is unchecked code. Rotating refresh tokens mean a candidate that refreshes wrongly
logs the owner out everywhere (Codex 0.165.0 would have, M9).

- **Claude**: platform runs already use the headless token (`CLAUDE_CODE_OAUTH_TOKEN`, `auth_env`), which doesn't refresh; a
  candidate gets the same and nothing else. On Linux the throwaway home has no `.credentials.json`. On macOS the subscription login
  is in the Keychain, and the platform's Seatbelt profile can't wrap the checks: a second
  Seatbelt layer can't start inside one (seatbelt.py:9-11), so Claude's own sandbox, which the confine checks exercise, wouldn't run,
  and that profile also turns the network off. So no wrapping.
  - V-M10 and V-M11 (11-09): a run on the shared config dir reads the user's login item through `security` on PATH, and doesn't
    write it.
  - A run with its own `CLAUDE_CONFIG_DIR` looks up only that dir's items (`Claude Code-credentials-<hash>`), so a throwaway
    config dir does hide the login, by naming.
  - DV5 (c) adds a refusing `security` first on PATH, which `-p`, stream, lease-shaped and interactive runs all work with.
  Residual either way: the promoted copy's refresh of the subscription login (jobs with `connectors: true`) is not checked before
  promotion; `check-claude-login` (hourly, a real request) is what catches it after.
- **Codex**: a Business / Enterprise workspace's access token when the instance has one (doesn't refresh). Otherwise the candidate's
  model checks run with the linked `auth.json` only after its model-free checks pass, including that its file store follows the link
  (`resolve_auth_file`), and only when the adapter's credential check (it reads the expiry and `last_refresh`, never the tokens) says
  no refresh can fall due during the checks; otherwise they wait for the next night. Accepted residual, as M9 says: the user's daemon
  and a platform run aren't locked against each other's refresh (`auth.lock` is per home).

## 4. What the server can change is checked as runs go

Promotion checks a binary once; surface C changes without one. Each run asserts what holds its confinement and refuses rather than
running wider:

- **Codex** (V-M1): the tool list a run sees equals the pinned list for its point kind and model, namespaces included (the
  server's `multi_agent_version` adds `collaboration`, which the feature flags don't remove).
  - Before the run, `codex debug prompt-input` with the run's flags, which makes no model call, reads the cached catalog. A
    mismatch refuses the run.
  - After it, the rollout's `additional_tools` holds what was actually sent, so a catalog the run refreshed itself is caught too.
    A mismatch refuses the result and alerts.
  - The residual is one run's wider tool list after a catalog change, bounded by the run's permission profile (M8), which doesn't
    come from the server.
- **Claude** (V-M7): completions and `tools: ""` points read stream-json.
  - The `init` event lists the run's tools. Any tool beyond the pinned set (`StructuredOutput` with a schema, nothing without)
    ends the run when it is read. Whether that is always before the first model request goes out wasn't measured.
  - A `tool_use` of any other name refuses the result and alerts.
  - What guarantees anything is the `tool_use` check plus the run's sandbox and settings. Ending on `init` only shortens the
    window.
- **Models**: an alias that stops resolving fails the run (as now), named "the provider dropped model X", not read as a login problem.

## 5. Session-side calls use matching versions

Calls the platform makes about the user's sessions run the user's version's behaviour, or are refused:

- Each adapter call is classed platform-side (a run of its own) or session-side: Claude's `agents --json` (status), `--resume
  --fork-session` of a user's transcript, `lease-resume` and `run-agent-job.sh`'s resume of a platform session in a terminal; Codex's
  `queue`, the daemon's status calls, the linked login.
- A session-side call runs with **the store's copy of the session's own version** (installed from the vendor when first seen,
  section 6), or with `current` only when the pair (`current`, that version) passed the session-side checks (a transcript written by
  that version forked by `current`; a session of that version listed by `current`). Otherwise it is refused, as a missing port is.
  A fork is a platform run, so the copy that runs it must also have passed the safety checks and the agent check, not only the
  session-side set.
- **A listing that failed is not `unknown`.** `unknown` keeps its meaning (the runtime has no status port, the session isn't in
  the listing, the tick's 10 s budget ran out; hubbus:2555, :2606-2616) and keeps counting as idle where it does today
  (hubbus:2964, :3008). A new state, `unlisted`, is a listing that errored or a session whose version no checked copy can list: the
  letter is routed (its hook shows it at the next turn) but the session isn't woken. One listing per live version per tick, inside the
  same 10 s budget; a version whose listing doesn't fit is `unlisted` for that tick.
- `lease-resume` resumes with the store's copy of the version that wrote the session, not `claude` on PATH; the resume hint
  `run-agent-job.sh` prints (:178) names that copy's path.

## 6. The user's sessions: checked when first seen, said by version

- **Record the version at session start.** The transcript isn't written before the first turn (runtime_claude.py:1503), so the
  hook reads it from the session process's executable: its path (Desktop's `ccd-cli/<version>/`, the daemon's
  `releases/<version>-<arch>/`) or the package metadata beside it (nvm's `package.json`), read as files, never run (V-M4). A version
  that can't be read (no `CLAUDE_PID`, runtime_claude.py:80-82; an unknown layout) is recorded as `unreadable`, which counts as
  pending. It goes into the session's record, which `hub-event` writes in the platform's own state; the readers also return the
  version per session from the transcript.
- **Check it then.** A platform job polls the session records each scheduler tick (15 minutes; event jobs trigger only on bus events,
  which any unsandboxed project could write) and takes each version with no result: it validates it against the vendor's published
  list, installs **that exact version from the vendor** into the store (never runs the binary in the user's folders, which a
  sandboxed session might have written) and runs the session-side checks on it: lifecycle, instructions, sessions, confine and
  control, session_status and wake (V-M6's pty driver). They run under section 3a's login rule, since they too run
  unchecked vendor code with a login. At most 3 installs a day. Desktop's builds are on npm (2.1.308, .309 and .317, the ones on
  this machine, are all published) and so are the daemon's (0.166.0, 0.167.0); a version the vendor doesn't publish or can't be
  installed is `not-installable`: treated as pending, a doctor row naming it, tried again once a week.
- **Gating, narrow.** Only for a **sandboxed project's** session, and only when its version **failed a safety check** (confine,
  control, a lifecycle deny): the handlers that start work are off for it (letters shown into the session, wake, routing to it,
  forks and resumes of it), the session is told once at start ("this Claude Code version failed the platform's sandbox check; the
  platform starts nothing in this session"), and the owner gets an alert. Everything else runs as today for it: reading and recording
  the version, the session record and its end (`session_record`, `mail_end`), memory sync, the deny handlers (`guard`). Wake is
  gated where it is decided, in hubbus, from the version in the session's process record (the `hub-event wait` entry point isn't a
  handler). The gate also covers `hub-event worktree` (a session in a git worktree, from the user-level worktree-memory.sh): today it
  checks only `CAGED_NEEDS` (hub-event:424) and then runs `mail` and `nudge` outside `handle()` (:426-428); for a failed version those
  two are off, read from the process record the same way, while the record and the session end keep running. A **pending**, `not-installable` or `not-checkable` version is as today: no gate, a doctor row. An unsandboxed
  project's session is never gated by version; a failed check there is a doctor row and an alert.
- **`CAGED_NEEDS` stays as it is**: it returns before every handler (hub-event:338-341). For a runtime without `control` (Codex
  before its Step 4) the hook files are writable by the sandboxed agent, so every payload in a sandboxed project may be forged, and
  nothing may act on one (render-project-settings.py:362-368 already doesn't install hooks there). The always-run / work-starting
  split is for the per-version gate only, which applies to runtimes that have `control`.
- **Hooks stop failing silently.** `hub-event` logs every payload `parse_event` rejects as `unusable=<reason>` with runtime and
  version, empty stdin apart (`empty`, expected). An event the tool adds isn't seen here at all (hub-event runs only for the events it
  installed); the lifecycle check is what notices one. Unusable payloads of an event letters depend on, three times for a version,
  are a doctor row and an alert keyed by event and reason.
- **Readers count what they skip** per file; a version whose files skip more than 5% of lines is a doctor row ("the weekly statistics
  may undercount Claude Code X's sessions").
- **doctor / `brunna runtimes`**: per runtime, the platform's copy (version, promoted when, a held-back version and its failed
  checks) and each version seen in the user's sessions in the last 7 days with its state (passed, pending, failed: which check),
  and which features are limited for which version.

## 7. Control paths become one rule over the tool's folder

The control check can't find a load location a new version adds: the platform's lists of control paths (`CONTROL` for the project,
`USER_CONTROL` for `~/.claude`, runtime_claude.py:180-182) are the only description, and a check against them compares the lists
with themselves. The aim is a rule the tool can't outgrow inside the project: a sandboxed session and its services create or change
nothing under the tool's folder in the project (`.claude/`, `.codex/`) except the worktrees the platform names writable.

It can't be written as one deny today: a deny wins over any allow (runtime_claude.py:831) and the policy emits
`Edit(/{ws}/**/{name}/**)` (runtime_claude.py:216), so denying `.claude/**` also denies every edit inside `.claude/worktrees/<x>/`, where
Desktop's worktree sessions work. How it can be expressed is V-M9: a deny on creating a new entry directly under the tool's folder
(Seatbelt can say it for services; whether Claude's permission rules can for sessions is the question), or rendering a deny for every
entry present except `worktrees` plus a deny on new top-level entries.

**V-M9 settled it (11-09)** (measurements below):
- **Services (Seatbelt): one rule.** `(deny file-write* (require-all (subpath "<ws>/.claude") (require-not (subpath
  "<ws>/.claude/worktrees"))))`. It denies new entries, writes, removals and renames under `.claude/`, and renaming `.claude`
  itself, while writes inside `worktrees/` go through. This replaces the enumerated control paths in the service profile.
  - Landlock (Linux) enforces none of it: it can only allow, and the project's own folder is writable, so neither the
    enumerated paths (`protect`) nor this rule hold there (landlock.py, "Can't enforce"); doctor says so.
- **Sessions (Claude's rules): not expressible as one rule.**
  - A deny wins over every allow, in the sandbox's `denyWrite`/`allowWrite` as well as in permission rules.
  - The glob matchers (node-ignore for rules, regexes for the sandbox) have no negation and match case-insensitively.
  - The only form that comes close is about 36 complement character classes. A class either engine doesn't take is silently
    ignored, and names that start with a non-ASCII character stay open to Bash.
  - That is a workaround resting on two engines' quirks, so it isn't taken.
  - So sessions keep the fixed load-location lists (`CONTROL`, `USER_CONTROL`), which deny those names whether or not they exist
    yet. Added to them: a deny (a `denyWrite` and an `Edit` deny) for every other entry present under `.claude/` except
    `worktrees`.
  - The residual is a new load-location name that is neither listed nor present: Bash through `sh -c` gets past the `Edit`
    parse and the tool's own ask (V-M9). The control check's canary names it: a throwaway session writes a new
    `.claude/<probe>` through Edit and through Bash (`sh -c`), and the result is recorded per version. For Edit, the tool's own
    "sensitive file" ask currently stops it (not in bypass mode).
  - `.claude/.cc-writes/`, which Claude Code creates itself whenever its sandbox is on, is exempt from the check.

The residuals doctor's control row names: a new load location under the tool's folder in the project, a new root-level file name, and a new
load location under the user's `~/.claude` or `~/.codex` (user-level, which the project sandbox doesn't cover). The vendor's release
notes for each promoted version are linked from `brunna runtimes` for the owner to read.

## 8. The user's own copy

The owner asked on 10-24 that the terminal Claude not fall behind (that is why `update-claude` exists). With the platform's copy
separate, the user's copy no longer runs platform work, but it still must not fall behind (DV2 (a)): after `update-runtimes`
promotes a version, it installs that same version into nvm's copy (the distribution port's `install` into the user's global
prefix, the vendor's integrity checked) when nvm's is older, never downgrading one the tool's own updater moved further. A failed
install is an alert keyed by runtime and reason, as `update-claude`'s are today. The same holds for Codex's npm CLI. Desktop's
copies and the Codex daemon update themselves and are only checked (section 6). Anything else on the machine that runs nvm's
`claude` itself (the assistant, for one) gets the checked version this way, with no change of its own.

## Steps

1. **Distribution port + conformance runner + Claude's checks** (the folder-git probe and the completion check ported in; the
   rest added), the results store, `brunna runtimes`, the coverage test. `hub-event` logs unusable payloads; readers count skips and
   read the version; `unlisted` added (section 5; `unknown` keeps its meaning). No change to which binary runs yet.
   **Progress (11-13), in this order, one commit each:**
   - Done: `hub-event` logs `unusable=<reason> runtime=<name>`, through an optional lifecycle function `unusable(event, raw)`
     rather than a change to `parse_event`'s contract. The version joins the line in step 3, which reads it.
   - Done: `Turn.version` and the session-record contract's optional `version`; `records(file, tally)` counts lines and
     skips per version; `sessions.skipped(since)`. The "skips more than 5%" row goes in `brunna runtimes`, not doctor (it reads
     a week of transcripts).
   - Done: `unlisted`, routed to but never woken, and said once in the dispatch log while it lasts.
   - Done: the distribution port (optional; `latest`, `install`, `version_of`, `pin_env`, `MIN_VERSION` 2.1.319) for Claude,
     doctor's "unversioned" row for a runtime without it, and `runtime_claude.binary()` with `check_binary()`, the only way
     to point a run at a copy under check (in process, never from the environment).
   - Next: the runner, the results store, the coverage test and `brunna runtimes`, against the fake runtime. Each check
     runs in a subprocess whose environment points state, config, data, secrets, bus, home and the tool's config dir at a
     throwaway world, and the first check proves the live bus and session records are untouched. The binary to check is
     chosen only by the runner, never from a run's environment. From a linked worktree the runner doesn't write the live
     results.
   - Then: the folder-git probe and the completion check move into the runner, and `update-claude.py` calls them (one copy).
   - Then: the model-free and cheap checks. Last: the pty-driven ones (session_status, session_summary, wake). A check
     that can't have a positive control yet is a named `not-checkable` entry.
   - Before merging: a cold review of the code, `bin/linux-test` on the new modules, one real run against the current
     binary recorded here.
   - **Done (11-14).** The checks are `bin/conformance_claude.py` (an adapter, like `sessions_<tool>.py`), with
     `prepare`/`enter` for the world's login (the headless token only; `CLAUDE_CONFIG_DIR` in the world, the refusing
     `security` first on PATH, the updater off and pinned into the world, the platform's Python named for `bin/py`). Two
     blind reviews (design, code) found five blocking bugs, all fixed: two checks that could never pass, two gate tests,
     and `enter()` clearing the environment after the isolation proof when there was no token. `bin/linux-test` on the
     new modules: 246 tests pass. **The real run on 2.1.322 (11-14, from the worktree, `--report`):** agent-result,
     agent-models, completion-schemas (all ten schemas), completion-no-tools (with the schema case), instructions-reach,
     sessions-read, stream-exchange, tool-servers-listed, auth-login-error and auth-keychain (its stub saw the lookups,
     none of the user's own item) pass. confine-folder-git found that 2.1.322 runs no git in the folder in a `-p` run even
     without the variable (the nightly probe has said so since 11-13): nothing to guard on that path, so it is
     not-checkable until a version runs git again, when the control returns. lifecycle-events: the hooks fire (seen
     once `bin/py` was told the platform's Python in the world), but the installed hooks run the main checkout's
     `hub-event`, whose logs follow an overridden state only once this step is merged; from the worktree they logged
     three lines into the live log. It is run again from main after the merge.
2. **The store + `update-runtimes`** for Claude: promotion, hold-back, the digest check, the updater pinned, no PATH fallback (with
   the ports split so protections stay), first install, rollback, `MIN_VERSION`; `update-claude` retired by `legacy_name`; callers of
   `hubconf.tool("claude")` move to `binary()`; DV3's platform config dir for the runs it covers, with `PROJECTS`, `transcript()`,
   `lease-resume` and the connectors login left on the shared dir until V-M8.
   **Implementation plan r2 (11-14), after its blind review (below). Two merges: the first fills the store on this
   machine, the second switches platform runs to it once a checked `current` exists here.**
   *Merge A (nothing changes which binary runs):*
   1. `bin/runtime_store.py` (neutral) and `hubconf.runtimes_dir()` (`<instance>/runtimes`; no instance directory = no
      store, said). Per runtime: `<version>/` (the copy, an npm prefix of its own), `promoted.json` in it (version, the
      executable's path and real path, sha256, size, mtime, the conformance result file, when), and the links `current`
      and `previous`. Writers (install, promote, rollback, remove) take `<runtime>/.writer` exclusively; an install goes into
      `.staging-<random>/` and is renamed in; `promoted.json` is fsynced before a link moves, `previous` before `current`,
      each link by `os.replace` of a fresh one. `hold(runtime)` resolves `current` once and keeps a shared flock on
      `<version>/.lock` for the run (inheritable across `exec`, so a lease keeps it; long-lived processes hold per run, never
      for their life). `verify(entry)`: size, mtime and the executable's real path on every run, the full sha256 once a day;
      a mismatch refuses ("the platform's Claude Code changed after it was checked"). `remove` skips a held version,
      `current`, `previous` and the platform config dir. Left out of the backup (`covers()` and the stage both).
   2. Distribution, cleaned: `_npm_env` a clean environment (no caller's `npm_config_*`), an install's npm cache in the
      store; whether `--ignore-scripts` still gives a working copy is measured on the first real install and recorded
      here. `install_global(version)` for section 8 (nvm's prefix, `-g`; npm checks each tarball's integrity as it
      installs; the version read back).
   3. `bin/update-runtimes.py` (neutral platform job in `projects/brunna.yaml`, nightly, `background: true`,
      `legacy_name` of `claude/update-claude`) for each runtime in use (`runtime.default`, `runtime.installed`, `llm.points`
      pins, providers whose impl is a runtime): no `current` → first install; a newer `latest()` → install beside, run
      `conformance.run`, promote when `conformance.verdict` is pass (not-checkable doesn't block, an error holds back) and the
      version is at least `MIN_VERSION`; else held back, `alert.once` keyed by check and reason, once more after 7 days.
      No new version: the daily subset on `current`. Then section 8: nvm's copy raised to the promoted version when older,
      never lowered. Unheld versions other than `current` and `previous` go after 14 days; a runtime no longer in use is
      left alone and removed after 14 days. `brunna runtimes install|rollback <runtime>`; `brunna upgrade` runs
      `update-runtimes.py --first` as an in-step command, so a machine that upgrades fills its store. `update-claude.py`
      and its job go. After merge A, this machine's store is filled from the main checkout and its result recorded here.
   *Merge B (platform runs use the store):*
   4. The no-copy state: `runtime_claude.checked_copy()` (the held entry or why not). The ports that start platform runs
      (agent, completion, stream, confine) are `Unsupported` with that reason through `runtime_ports.load(..., platform_runs=
      True)` (the default for those ports); `auth` stays loadable (it only describes and cleans). `capabilities()` is unchanged
      (`CAGED_NEEDS` keeps every sandboxed project's hooks). With no copy, `session_statuses` gives `unlisted` (routed,
      never woken; tested on runtime_claude), `health()` and `_listed_servers` say why, and llm.py, `llm_ready`, the
      librarian's completion switch, doctor and the status page read it. Refusal text comes from the adapter.
   5. `binary()` is the held, verified `current`; no fallback to PATH. A run's environment by what the run is:
      | run | config dir | refusing `security` |
      |---|---|---|
      | completions, judgment points that neither resume nor fork (token) | the platform's (`<runtimes>/claude/config`) | yes |
      | runs that resume or fork a session: `mailbox-conversation`, agent-job sessions (resumed by id), leases (token) | the shared `~/.claude` | yes |
      | the subscription login: `login-check`, `connectors: true` jobs | the shared `~/.claude` | no |
      The copy's `node_modules/.bin` goes first on PATH (then nvm's), with `pin_env`; `_home()` ignores the platform dir, so
      `credential_paths()` and `user_control()` stay on the real `~/.claude` (tested); each run records the version and digest
      it ran with (the usage row, the result's `llm_version`).
   6. Callers: `lease-resume` (the store's copy), init (install unpromoted → token → checks → promote → login check;
      `setup-token` run with the store's copy; `agent_found` reports it, so `--yes` keeps choosing Claude), doctor's rows.
      A machine with no npm says so in init and doctor.
   7. Section 4 for Claude: completions read stream-json, a run whose init event lists a tool beyond the pinned set ends,
      and a `tool_use` of another name refuses the result and alerts.
   Tests: the store from fake installs (every refusal, the writer lock under two writers, staging, the link order, the
   hold across `exec`), the promotion rules, first install from `update-runtimes`, the no-copy split on runtime_claude
   (a sandboxed project's hooks and denies unchanged, dispatch routes and doesn't wake), the environment table per run,
   `_home()` with the platform dir set, `test_llm_golden` with a fake store recording the environment's names,
   `bin/linux-test` on the store, and one real install of the current version into a scratch store before merge A.
   **Merge B built (11-14), on its branch, not merged.** Items 4–7 as planned, with these settled while building:
   - The row of the run table: the platform's config dir only for a token run whose declaration's shape isn't agent
     and that neither resumes nor forks, and every completion; every agent-shaped point (the librarian's agent parts,
     consolidate, agent jobs, leases) stays on the shared `~/.claude`, which is narrower than the first row above
     (`runtime_claude.run_kind`). A completion on the subscription login (no token) gets the shared dir and no stub.
   - In a conformance check (`check_binary`) the check's world decides the config dir, the stub and the pin
     (`conformance_claude.enter`); `_env` only puts the binary's folder first. `checked_copy()` counts the copy under
     check, so a first install's checks run with an empty store; a dry run (`llm.py --dry-run`, the folder-git probe's
     bare run) describes with no copy and starts nothing.
   - Each run resolves the copy once (`_run_copy`) for its binary, environment and recorded version.
   - `pin_env`'s `npm_config_prefix` is a sink beside the copies (`<store>/claude/.npm-sink`), never the copy's folder
     (V-M5 below); `lease-resume` runs its Python isolated and enters the lease's folder only when it execs the tool.
   - Mailbox dispatch loads its gate with `platform_runs=False` and, with no copy, routes and holds letters but starts
     no fork or triage (each letter's one chance isn't spent on a refusal); the rest of the split gate is step 3.
   - `check-claude-login` makes no request with no copy (no login failure, no recovery); the files and dates are still
     checked. `lease-runner` parses through the runtime with `platform_runs=False`; `llm.py exec` refuses.
   - Not yet run against a real binary: stream-json `complete()`, the platform config dir, `resume_exec`. Before the
     merge, a checked `current` must exist on this machine (merge A's first install), and the conformance run from main
     after it. `run-agent-job.sh`'s resume hint still names `claude` (section 5, step 3).
   **Re-review of r2 (11-14): B1–B3 resolved, nothing new blocks.** To fold in while building: the token's presence in a
   run's environment is what tells `_env` its row (named and tested); `update-runtimes.py --first` is the last in-step
   command of an upgrade and its failure is a red doctor row, never a halted upgrade; `lease-resume` (interactive, which
   updates itself, V-M5) goes through an adapter function with `pin_env`, the hold and `verify`, and the stub only on a
   token; `setup-token` never runs on an unpromoted copy (the user's own copy, or a scratch config dir); an old-layout
   machine (no instance directory) keeps its store under the hub's `.state` (already in every sandbox's `deny_write`);
   the inherited lock descriptor is opened read-only; `agent_found` also counts the user's own copy, so init's default
   doesn't flip to none on an empty store; the stub lives in the store and a token run refuses when it is missing.
   **Blind review of r1 (11-14): three blocking, folded into r2 above.**
   - B1: with no copy, `session_statuses` → `_exe()` would raise through `Tick.status` and fail dispatch for every project; with
     no checked copy the listing must give `unlisted` (routed, never woken), tested on runtime_claude, not the fake.
   - B2: the stub and the platform config dir go by auth mode, not to every run: `login-check` (subscription, hourly) and
     `connectors: true` jobs keep the shared dir and the Keychain; `mailbox-conversation` and `agent-job` sessions (resumed by id;
     `run-agent-job.sh` tells the owner to `claude --resume`) stay on the shared dir. A table per point: dir and stub.
   - B3: nothing fills the store on a running machine: `update-runtimes` with no `current` does the first install, `brunna
     upgrade` runs it as an in-step command, and the `binary()` switch lands last, in a merge of its own, after `current`
     exists here. Order: store → install/rollback → update-runtimes → the split and the readers of the no-copy state → the
     `binary()` switch → callers.
   Should-fix: the promotion rule is `conformance.verdict` (not-checkable doesn't block, error holds back); `auth` stays loadable
   (describe/clean only; the refusal lives in `binary()`); `llm_ready` and the librarian read the no-copy state; `health()` and
   `_listed_servers` handle it; `_home()` ignores the platform dir (it feeds `credential_paths()`/`user_control()`); the
   updater pin also puts the copy's `node_modules/.bin` first and `verify` compares that executable's real path; section 8
   needs a global install function (`-g`, integrity from `npm view dist.integrity`), not `install()`; a store-wide writer
   lock, installs staged then renamed, `promoted.json` fsynced before the links, `previous` before `current`; the hold across
   `exec` (leases) and its scope in long-lived processes; init's order (install unpromoted → token → checks → promote → login
   check; `setup-token` with no `claude`; `agent_found`'s default); a machine with no npm/nvm says so; each run records the
   version and digest it ran with; section 4's per-run tool check for Claude placed in a step; "in use" includes `llm.points`
   pins and providers; a hubconf accessor for the store path; removal skips the config dir; the job runs in the background;
   `test_llm_golden` gets a fake store and records the environment's names; `_npm_env` builds a clean environment.
3. **Session versions**: recorded at start, the job that checks a newly seen version, the narrow per-version gate (`CAGED_NEEDS`
   unchanged), dispatch's split gate, session-side calls on matching
   versions, the SessionStart notice.
4. **Control** (section 7, after V-M9): the one Seatbelt rule for services on macOS; for sessions, the fixed load-location lists
   as today plus a deny for every other entry present under `.claude/` except `worktrees`, and the control check's per-version
   canary. The sandbox-parity tests
   are updated to match.
   **Implementation plans for steps 4 and 3 (11-14), reviewed blind twice (r1: four blocking, r2: one, r3 below): step 4 is built before step 3.**

   Order changed: **step 4 before step 3** (r1 review S7: step 3's gate fires only on a failed safety check, and the safety
   checks it needs, control and confine-sandbox, are built in step 4).

   ## Step 4: control (section 7, V-M9), built first

   1. **Services (macOS Seatbelt): the one rule is added, nothing removed.** `seatbelt.py`'s service profile keeps
      `protect_denies` (the CONTROL names at any depth, root `.mcp.json`, nested `.claude/`) and adds the V-M9 rule
      `(deny file-write* (require-all (subpath "<ws>/<tool folder>") (require-not (subpath "<ws>/<tool folder>/worktrees"))))`,
      the workspace path resolved as `protect_denies` does. The folder names (`.claude`, `worktrees`) come from the adapter's
      control port (a new `control_root()` → (folder, exempt names)), never written in seatbelt.py/policy.py
      (test_runtime_boundary). The parity test gains a case with `protect` on (r1 NIT: it turns it off today).
      project-git-job runs under this profile: a fast-forward touching a tracked `.claude/<other>` file now fails; said by
      doctor and in the job's error.
   2. **Sessions (Claude's settings): the fixed lists, plus every present entry, in the workspace and in each worktree.**
      `CONTROL` gains `.claude/launch.json` (Desktop runs its commands); `USER_CONTROL` / the user-level denies gain
      `~/.claude/remote/ccd-cli/` (Desktop's executables, run at every Desktop start). Each entry present under the
      workspace's `.claude/` and under each `session_folders()` worktree's `.claude/` (except `worktrees` and `.cc-writes`)
      gets a `denyWrite` and an `Edit` deny. A name with a glob character (`*?[]{}!`) is not rendered as a rule (it would
      widen into every sibling, V-M9): it is denied through the sandbox's `denyWrite` path only and named by doctor. The
      present-entry denies also stop the agent's own writes there (`launch.json`, `agent-memory/`): doctor says which.
      Linux: Landlock enforces none of `protect` (landlock.py:30-33); doctor says so, not "keeps the enumerated paths".
   3. **The control and confine-sandbox checks** (conformance_claude, safety): the world gets a sandboxed throwaway project
      registered in its own registry (`enforce: {sandbox: true}`); its session settings are rendered by the real
      policy → settings path into the project. The session runs with `--permission-mode bypassPermissions` (V-M9: denies
      hold there; the sensitive-file ask doesn't, so it can't stand in for the rules) and Bash and Edit allowed, asked to write
      each canary. Canaries: each CONTROL path that exists as a write target (`.claude/settings.json`, `.claude/settings.local.json`,
      `.claude/hooks/x`, `.mcp.json`, `.claude/launch.json`; never `CLAUDE.md`, which no rule covers) and, for confine-sandbox,
      another project's folder, the world's secrets dir and the project's `.git/config`. Each canary has its own positive
      control: the same session with that rule left out of the rendered settings writes it. A canary counts only with
      evidence the write was attempted (the stream's `tool_use` for it, or its denial in `permission_denials`), never from the
      file's absence alone; no attempt is `error`. The new `.claude/<probe>` through Edit and through Bash `sh -c` is recorded
      as the version's residual (information).
   Tests: the rule rendered beside `protect_denies` (and on macOS a real `sandbox-exec` probe: a write under `.claude/` denied,
   a write of a CONTROL name under `.claude/worktrees/x/` still denied, an ordinary file under `.claude/worktrees/x/` allowed);
   present entries rendered for the workspace and a worktree; a glob-named entry not rendered as a rule; the checks against a
   stand-in claude that honours or ignores the rendered settings (pass and fail), and one with no attempt (error).

   **Progress (11-14): built on its branch, not merged.**
   - **Services:** `seatbelt.root_denies` sits beside `protect_denies` in `_isolation`, so cap-run jobs get it as well as
     services. The folder comes from `runtime_claude.control_root()`, an optional function of the control port, through
     `policy.protect_roots`.
   - **The services rule's cost:** project-git-job's write ops (copy-back) now refuse a tracked `.claude/<other>` file.
     project-git-job says so on a PermissionError, and doctor (`doctor_control`) says it too. There is no fast-forward op
     in project-git-job; merge-branch's fast-forward runs in the session, where the present-entry denies stop it the same
     way.
   - **Sessions:** `present_controls` and `control_notes` are in runtime_claude; the doctor rows come from the adapter.
   - **Present entries:** an entry's rules name its resolved folder and its own name, never what a linked entry points
     to. The platform's rendered `settings.local.json` is not counted as a present entry: it is a control name already,
     and counting it changed what was rendered on every apply (test_appliers). Backslash joins the glob characters.
   - **Doctor on Linux:** says that Landlock enforces none of it.
   - **The checks:** `control` and `confine_sandbox` are in conformance_claude. The world now points the instance
     directory into itself as well. The hub's pointer had named the live instance, which holds the world (private_tmp is
     in its secrets) and which a sandboxed project's policy denies whole.
   - **Found by the real runs:**
     - A positive control has to leave out more than the rules on the canary. A deny on a folder entry alone
       (`.claude/hooks`) keeps the folder from being made, and a **Read deny refuses a write too** (2.1.323).
     - The model once declined to overwrite the rendered settings file as "live". The prompt now says the file is a
       throwaway copy, and a run that didn't try is asked once more.
   - **The real run on 2.1.323 (11-14, from the worktree, `--report`):** confine-sandbox and control pass. The residual
     is a new `.claude/<name>`, which is written through Edit (bypass) and through Bash (`sh -c`), as V-M9 found.

   ## Step 3: the user's sessions, checked when first seen, said by version (sections 5, 6), built second

   1. **The version at session start (V-M4)**, `runtime_claude.session_version(pid)`, from the executable's real path, never
      running it: Desktop's `~/.claude/remote/ccd-cli/<version>-<hash>` (a file; parsed `^(\d+\.\d+\.\d+)-([0-9a-f]+)$`, the hash
      recorded too), a store copy `<runtimes>/claude/<version>/…`, nvm's `…/lib/node_modules/@anthropic-ai/claude-code/`
      `package.json` only when the executable resolves under nvm's prefix (a `claude` in a project's own `node_modules` is
      `unreadable`, never trusted). Anything else `unreadable`. `record_session` carries `version` on every write (start,
      a /clear takeover, the project backfill); a record missing it is read from the pid when next used, not counted as
      unreadable. A closed session's version comes from its transcript (`Turn.version`). The `unusable=` log line gets
      `version=`.
   2. **The check job** `brunna/session-versions` (every tick, background): versions from **live session records first**
      (the transcripts' versions only feed `brunna runtimes`: a sandboxed session can write `~/.claude/projects` in bypass,
      r1 S5); each version without a session result: checked against npm's published list (`published()`, a new distribution
      function), installed exactly into the store unpromoted, at most 3 installs a day per runtime, its **session-side
      checks** run (`Check.side = "session"`: lifecycle, instructions, sessions, confine-sandbox, control, session-status,
      session-summary, wake, and a new **`sessions-fork`**: a transcript written by that version, forked by `current`, reads
      back), recorded as `<results>/<runtime>/<version>.session.json`. Not published / not installable → `not-installable`,
      retried weekly. Session-version copies are kept only while a session of that version was seen in the last 7 days and
      at most 5 per runtime (a Desktop build is ~320 MB), and never one a check holds.
   3. **The narrow gate**, read at one place, `runtime_ports.Sessions.gated(sid)` (sandboxed project + its version's
      recorded session result has a safety `fail`), used by every path that starts work: hub-event's `handle()` and `worktree`
      (always-run: `session_record`, `mail_end`, memory sync, `guard`, `wake_address_drop`; off: `mail` (and with it presence,
      said), `wake_address`, `nudge`), hubbus's `candidates()`, `Tick.can_wake` (the notice and holder wakes), `active_sessions`
      and the conversation wait (a gated session is not waited for), and `can_resume` (no fork or resume of it). The session is
      told once at start ("this Claude Code version failed the platform's sandbox check: the platform delivers no letters to
      this session and wakes it for none"), the owner gets an alert per runtime, version and check. Pending, unreadable,
      not-installable, not-checkable: as today. Unsandboxed: never gated, a doctor row and an alert.
   4. **Session-side calls on matching versions (section 5).** Listing: `current` lists every live session as today; a
      session whose version's `session-status` session check **failed** is `unlisted` (pending versions keep today's listing,
      r1 S1). One `agents --json` per tick still (it lists all sessions), under a deadline passed down from the tick's budget
      (r1 S2). Forks of a session run with `current` when the session's version passed `sessions-fork` (the pair route), else
      are refused like a missing port (the letter falls to triage); a version still pending keeps today's fork (as today).
      `lease-resume`: the lease record gains the session's version; it resumes with `current` when that pair passed, else is
      refused with the reason (section 5).
   5. **Said by version**: `brunna runtimes` and doctor per version seen in 7 days (passed, pending, unreadable,
      not-installable, failed: which check; Desktop builds by hash); unusable hook payloads of an event letters depend on, three
      times for a version, a doctor row and an alert keyed by event and reason.
   Tests: each layout's version from fixture paths (Desktop's file name with hash, the store, nvm's prefix, a project's
   node_modules refused), the record keeping the version through all three writes, the job's live-first order, budget and
   not-installable path, the gate at each choke point (handlers, candidates, can_wake, active_sessions, can_resume) and its
   absence for pending and unsandboxed, the listing rule, a fork refused on a failed pair and kept on pending.

   ## r3 (11-14), after the re-review of r2 (B1–B3 resolved; B4's last piece and the should-fix items here)

   - Step 4, checks: the `.git/config` canary is dropped (no session rule denies a project's own `.git`, which sessions need
     to commit). confine-sandbox's canaries are only what a rendered rule denies: another project's folder and the world's
     secrets directory. **Every positive control goes through Edit** (bypass leaves Edit open; Claude's own sandbox keeps Bash
     off `.claude/settings*.json` and off unlisted folders whatever the rules say), so a control that can't land is a real
     `error`, not the model's choice of tool.
   - Step 4, sessions: `.claude/launch.json` is added as a tree entry (like `settings.json`), so sessions get its Edit deny.
     A glob-named present entry is **not rendered at all** (the sandbox reads paths as regexes too and drops what it can't
     take, V-M9); doctor names it.
   - Step 3, versions: the hook-recorded version is kept past the session's end in `<secrets>/sessions/<sid>.version`
     (written by `session_record`, not removed by `wake_address_drop`, pruned after 30 days); a closed session's version is
     read from it, never from the transcript (which a sandboxed session can write in bypass). No `.version` = pending.
   - Step 3, gate: the outbox import ignores `told` and `ack` from a gated session (`own_session`, hubbus), so a gated session
     can't hold a letter away from healthy ones through the CLI.
   - Step 3, retention: `runtime_store.prune` learns the session-version copies (`installed.json` kind `session`, last seen
     recorded by the check job) and keeps them while a session of that version was seen in 7 days, at most 5 per runtime;
     promoted copies keep today's rule.
   - Design text to update with step 3: section 5's fork rule becomes "with `current` when the pair passed `sessions-fork`,
     pending as today, otherwise refused".
5. **Codex Step 1** builds on all of it: `conformance_codex.CHECKS` (M1, M8, M9's cases), its store and platform home, section 3a's
   login rule, the per-run tool list (V-M1).

## Decisions for the owner

- **DV1, the platform's own copy. Owner, 11-09: (a).** A copy per runtime in the store, promoted only after a pass, fixed after; the
  user's copy never runs platform work (~650 MB for both runtimes with `previous` kept, not backed up). Only the runtime the instance
  uses gets one (init's agent question, section 3 "First install"). Runtimes without the distribution port (ACP, a command template)
  stay unversioned, and doctor says so. Not taken: (b) keep sharing the user's copy and only check it, where a manual update or the
  tool's own updater changes the platform's runs before any check.
- **DV2, the user's copy (nvm's). Owner, 11-09: (a), with the tool's own updater left on.** After each promotion the platform
  moves nvm's copy up to the promoted version, never down (section 8). Claude Code's own updater stays on: turning it off is a
  user-level setting (`DISABLE_AUTOUPDATER` in `~/.claude/settings.json`), which AGENTS.md leaves to the owner's direct yes, and the
  owner chose not to; a version it brings first is checked when a session first shows it (section 6). Not taken: (b) leave it to the
  tool's own updater alone, which brings back what `update-claude` was made for (the owner works mostly in Desktop, Claude Code
  updates itself only in interactive sessions, so nvm's copy fell weeks behind; owner, 10-24); (c) latest every night, unchecked.
  Desktop's and the Codex daemon's copies update themselves in any case; they are checked (section 6), not controlled.
- **DV3, a platform config dir for Claude's platform runs. Owner, 11-09: (a), in two stages** (completions and judgment points
  in step 2; leases, mailbox forks and `connectors: true` jobs after V-M8). V-M8 (11-09): mailbox forks of a user's transcript
  move too, by `--resume <path> --fork-session` (they read the user's file and write only the platform's dir); `lease-resume`
  and the `connectors: true` jobs stay on the shared dir. Claude's platform runs still share `~/.claude` (settings, plugins, hooks,
  `~/.claude.json`) with the user's sessions; non-`--restricted` runs (leases) load the user's `settings.json`, plugins and hooks,
  written for the user's version. (a) A platform `CLAUDE_CONFIG_DIR` (runtime_claude.py already passes one through), the token
  through `auth_env`, for the runs that neither resume nor fork a session nor need the subscription login: completions and the
  judgment points (recommended, step 2). Leases, mailbox forks of a user's transcript and `connectors: true` jobs keep sharing until
  V-M8 shows a run with its own config dir can resume a session stored in another one: `PROJECTS` is fixed to `~/.claude/projects`
  (runtime_claude.py:61), `transcript()` / `resumable()` look only there (:96-98, :1501-1504), `--resume` takes an id and looks in its
  own config dir, `lease-resume` runs a bare `claude --resume` against the user's dir, and the connectors jobs need the subscription
  login, which a token can't stand in for. The platform config dir goes only into the child run's environment, never the
  platform's own: `_home()` reads `CLAUDE_CONFIG_DIR` from `os.environ` (runtime_claude.py:161-162) and feeds `credential_paths()`
  and `user_control()`, which become every sandboxed project's denies; set in the platform's environment it would move them off the
  real `~/.claude`. Session-side calls (section 5) keep the user's config dir. Not taken: (b) keep sharing for all of them.
- **DV5, the Keychain login. Owner, 11-09: (c).**
  - V-M10/V-M11 (11-09): a token run on the shared config dir reads the user's login item (`find-generic-password` through
    `security` on PATH, no write). A run with its own config dir looks up only that dir's hashed items.
  - So the risk is a candidate that changes that naming, or a run left on the shared dir.
  - "Every token run" in (c) leaves out the runs that use the subscription login (`connectors: true` jobs), which need the
    Keychain and keep it.
  - (c) works in `-p`, stream, lease-shaped and interactive runs (V-M11). Its positive control can check names as well as
    calls: a scratch-dir run whose logged lookups name the user's unhashed `Claude Code-credentials` fails it.

  The options were:
  - (a) Run candidate and session-version checks as a dedicated macOS user made for them (its own Keychain; a one-time setup by
    the owner).
  - (b) Accept the residual: a candidate is the vendor's integrity-checked build, and the risk is a buggy refresh of the
    subscription login, which `check-claude-login` notices within the hour.
  - (c) A `security` first on PATH that refuses every call ("item not found", exit 44), for candidate and session-version checks
    and every token run of the platform's copy. A token run works with it (V-M10). It is held by a safety check with a positive
    control: the candidate, with a scratch config dir and no token, under a refusing stub that logs, must show its Keychain reads
    in the log. No logged call means the candidate reaches the Keychain some other way (the Security framework directly), and it
    isn't promoted (fail-closed, and the owner is told DV5 is open again).
    Residual: a version that calls the stub and the framework directly both. Recommended: no setup for the owner, it holds on any
    machine, and a change of method stops promotion rather than passing silently.
- **DV4, cost. Owner, 11-09: accepted as is**, the daily subset included (a server-side change such as 11-08's code mode comes
  with no new version to trigger a check). A full pass is ~40 model calls per new version (Claude Code ships a few a week: ~150 calls a week), the daily
  subset ~12 (one completion per schema), each version a user session runs ~10 more. On the subscription; nothing billed per use
  unless `metered_models` says so.

## Measurements

- V-M1: does `codex debug prompt-input` (no model call) show code mode's nested tool list, for the per-run check?
  **Done 11-09 (0.167.0, the platform home of M9, the tool-free flags plus M1b's).**
  - It does. The `additional_tools` item lists the top-level namespaces, and `exec`'s description declares each nested tool
    (`declare const tools: { apply_patch(…`). The nested names equal what M1b's real run printed from `ALL_TOOLS`: `apply_patch`,
    `clock__curr_time`, `list_mcp_resource_templates`, `list_mcp_resources`, `read_mcp_resource`.
  - The description says deferred nested tools "may be omitted from this description", so it is a floor, not the whole list.
  - It is computed locally. Under a Seatbelt profile with the network denied, the output is identical: it reads the cached
    catalog (`models_cache.json` in the home, with `fetched_at`), so it can't see a catalog change the run itself fetches.
  - The rollout records exactly what the run sent: its first `response_item` is `additional_tools` with the full tree. So the
    per-run check is two steps: `prompt-input` before the run, which refuses on a mismatch; and the rollout's `additional_tools`
    after it, which refuses the result and alerts on a mismatch. Section 4's "if not" branch (once a day per model) isn't needed.
  - **New finding: the tool-free set isn't tool-free on the default model.**
    - The catalog's `gpt-6-luna` (the default) has `multi_agent_version: v2`, and its runs get a `collaboration` namespace:
      `spawn_agent`, `send_message`, `followup_task`, `wait_agent`, `list_agents`, `interrupt_agent`.
    - This happens with `features.multi_agent=false`, and with `features.multi_agent_v2=false` too. M1b's real run had it.
    - `gpt-5.6-luna` (`multi_agent_version: v1`) with `multi_agent=false` has none. `functions.wait` and
      `request_user_input(_async)` remain on both.
    - So the server decides which collaboration tools a run gets. Codex Step 1 pins the model per point kind (a v1 model, or
      whatever setting removes v2's tools, still to find), and the pinned tool list includes the namespace names, not only the
      nested ones.
- V-M4: the version of the binary a session runs, read at SessionStart from the session process's executable path or package
  metadata (Claude: `CLAUDE_PID`'s executable; Codex: the daemon's executable; both read as files).
  **Done 11-09.**
  - Hooks get `CLAUDE_PID`, both in Desktop's sessions and in a `-p` run of nvm's copy.
  - `proc_pidpath` (libproc; Linux `/proc/<pid>/exe`) gives the real executable. Desktop's is the file
    `~/.claude/remote/ccd-cli/<version>-<hash>`: the version is in the file name, not a folder. nvm's resolves to
    `…/node_modules/@anthropic-ai/claude-code/bin/claude.exe`, a native Mach-O, with `../package.json` beside it holding the
    version. `ps -o comm=` shows the symlink as run (`…/bin/claude`), so the reader resolves it first.
  - `CLAUDE_CODE_EXECPATH` is set in Desktop's sessions (the ccd-cli path) but empty in a `-p` run of nvm's copy, so it is a
    shortcut, not the source.
  - Codex: a hook's parent chain reaches the daemon `…/app-server-daemon/releases/<version>-<arch>/bin/codex` (M7's log);
    `proc_pidpath` of the first `codex` ancestor gives it.
- V-M5: the switch that keeps each tool's updater off for one run: Claude `DISABLE_AUTOUPDATER=1` under `-p` and in a stream session;
  Codex's update and daemon-auto-start settings in the platform home. The check: the binary's digest is unchanged after a run with a
  newer version published.
  **Done 11-09.**
  - **Claude:** 2.1.317 was installed into a scratch npm prefix while 2.1.319 was the latest.
    - `-p` and stream-json sessions never start the CLI updater, with or without the flag: no `AutoUpdater` line, no npm call,
      the digest unchanged.
    - An interactive session under a pty did update itself about 25 s in, as the positive control: `Installation type:
      npm-global`, then `npm install --global @anthropic-ai/claude-code@2.1.319`.
    - With `DISABLE_AUTOUPDATER=1` the same session did nothing, and the digest and version held.
    - **The updater installs into npm's global prefix, not into the running copy's folder.** It honoured `npm_config_prefix`.
      Without that, a store copy run with nvm's npm on PATH would rewrite nvm's global packages.
    - So `pin_env()` for Claude is `DISABLE_AUTOUPDATER=1` (`DISABLE_UPDATES` and
      `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` also turn it off, untested) **and** `npm_config_prefix` set to a sink folder
      of the store's own (`<store>/claude/.npm-sink`), so even an updater that ignores the flag writes nowhere that runs.
      Not the copy's own folder, as first written: every tool and hook a run starts inherits the prefix, so an unsandboxed
      run's `npm install -g` would write into the copy, of which `verify` checks only the executable (merge B's review,
      11-14).
    - The flag matters for V-M6's interactive checks and leases, not for `-p`.
  - **Codex 0.167.0:** `codex exec` runs the app-server in process.
    - It wrote no `version.json`, made no update request, started no daemon, and left `packages/` absent.
    - The settings that exist (from the binary's strings and `features list`) are `check_for_update_on_startup` (which an
      administrator can also lock), `features.daemon_auto_start` and `features.in_app_updates`. `pin_config()` sets all three
      off in the platform home.
    - They can't be shown to hold until a newer Codex is published (no positive control today). Until then they are
      `not-checkable`, with the digest check behind them.
    - The interactive update path (`version.json`, the self-updating daemon) belongs to the TUI and the daemon, which the
      platform doesn't run.
- V-M6: a throwaway interactive session the checks can drive: Claude's stream-json session idles and takes a turn after `wake()`, and
  `claude agents --json` lists it; Codex's thread through its app-server.
  **Done 11-09 for Claude (2.1.319).** An interactive `claude` under a Python pty does all three steps with no human, in two full
  runs. So `session_status` and `wake` become checkable, and section 2's two `not-checkable` rows go.
  - **Setup.**
    - `pty.fork()`, 50x160, `TERM=xterm-256color`; a scratch config dir seeded with `hasCompletedOnboarding` and the
      project's `hasTrustDialogAccepted`. No first-run screen appeared, and the token raised no prompt.
    - The platform's hooks installed by `install_lifecycle`. The prompt is typed followed by `\r`, and `/exit` ends it.
    - A driver waits for the `❯` input line or the `session_start` hook line, not a fixed delay.
    - It passes `--permission-mode` explicitly; the session otherwise came up in auto mode.
  - **Listed:** `claude agents --json` shows it as `kind: "interactive"` with a status, and `session_statuses` returns `idle`.
    `busy` is too short to catch reliably, so the check reads transcript growth plus `idle`, never `busy`.
  - **Waiter armed:** after a turn, the `.waiter` exists, `can_wake` is true, and `hub-event wait` runs under the session. A new
    waiter is armed after every woken turn.
  - **Woken:** `wake(sid, text)` returns true. Within about 1 s, `hub-event.log` has `wait ran=woke` and then a `prompt` line, and
    the transcript gains the Stop-hook feedback user message and the reply. Two wakes in a row each took a turn.
  - **Cleanup:** SessionEnd drops the waiter, and no process is left.
  - **For section 2's throwaway world.** Besides state, secrets and `bus_dir`, the world sets the hub root (`AGENT_HUB_ROOT`)
    and the instance (`AGENT_HUB_HOME`). With the live root, `LOGS` is the main checkout's `.logs`, and `hubconf.bus_db()` would
    repoint the live `.logs/hubbus.log` link at the temp bus's log. The hooks inherit all of it from the session's environment.
    A check that wants a letter delivered also needs a throwaway registry entry in the temp config.
  - Codex's thread through its app-server is still unmeasured; it comes with Codex Step 4.
- V-M7: the signal in `claude -p --output-format json` that a run used a tool (`num_turns`, `permission_denials`, or the stream's
  `tool_use` events), with a positive control.
  **Done 11-09 (2.1.319, the completion flags `--restricted --strict-mcp-config --no-session-persistence`, a scratch config dir,
  the token).**
  - `num_turns` counts model round trips:
    - 1 with `--tools ""` and no schema, 2 with a tool call;
    - with `--json-schema` the structured output is itself a tool call (`StructuredOutput`): 2 with no tools, 3 when Bash ran.
    - So turns are a rough signal only (a schema retry adds one).
  - `permission_denials` stays empty when a tool is simply absent, so it says nothing here.
  - The exact signal is stream-json (`--output-format stream-json --verbose`). The `system`/`init` event lists the run's tools
    before the first model call (`['StructuredOutput']` with `--tools ""`, `['Bash', 'StructuredOutput']` with Bash), and each
    `assistant` event's `tool_use` blocks name the tools called.
  - So the completion check (section 4) reads the stream:
    - on an `init` tool list other than the pinned one (`StructuredOutput` only, or nothing without a schema), it ends the run
      when it reads `init`. Whether that always comes before the first model request wasn't measured, so this only shortens the
      window;
    - a `tool_use` of any other name refuses the result and alerts.
    - completions move from `--output-format json` to stream-json.
  - The positive control: the same run with `--tools Bash --allowedTools "Bash(echo:*)"` shows Bash in `init` and in a
    `tool_use`. Without the schema, the no-tools model wrote "I ran `echo m7probe`…" in plain text while calling nothing, so the
    check reads events, never the answer.
- V-M8: can a Claude run with its own `CLAUDE_CONFIG_DIR` resume or fork a session stored under `~/.claude/projects` (by id, or by a
  path or a link into its own dir)? Until it can, DV3 leaves resumes and forks on the shared dir.
  **Done 11-09 (2.1.319, two scratch config dirs, a session with a code word made in A).**
  - From B, `--resume <id>`, with or without `--fork-session`: "No conversation found".
  - `--resume <path of A's .jsonl> --fork-session` from a fresh dir E works (the answer recalls the code word), and the fork is
    written to E's `projects/`, while A is untouched.
  - The transcript copied into C's `projects/<slug>/` works too.
  - A link from D's `projects/<slug>` to A's works, but the fork lands in A through the link: never that.
  - So mailbox forks of a user's transcript can run with the platform's config dir, resuming by path: they read the user's
    file and write only the platform's. That leaves the shared dir only to `lease-resume`, which resumes in the user's own
    terminal, and to the `connectors: true` jobs.
  - `--resume <path>` becomes a session-side check, so a version that drops it fails before promotion.
  - `claude agents --json` lists the same sessions (24 live, machine-wide) with a scratch `CLAUDE_CONFIG_DIR` as with the shared
    one. A platform config dir doesn't blind section 5's status calls.
- V-M9: how "create nothing new under `.claude/` but `worktrees`" is expressed: in Claude's permission rules for sessions, and in
  Seatbelt / Landlock for services.
  **Done 11-09 (2.1.319; macOS).** Each case ran in a throwaway repo with one write per prompt, judged by the file system, with a
  positive control (no rule: the write happens).
  - **Permission rules.**
    - `Edit(...)` rules are gitignore globs (node-ignore). They are case-insensitive, and a matched directory denies everything
      beneath it: `Edit(<ws>/.claude/*)` also denies `.claude/worktrees/x/a.txt`.
    - `[!w]` and `[^w]` are taken literally, and there is no `!` negation.
    - Deny rules hold in bypass mode and cover Write of a new file as well as Edit.
    - An allow rule doesn't lift the tool's own "sensitive file" ask. That ask covers everything under `.claude/` except
      `.claude/worktrees/` and is off in bypass mode.
    - A complement of about 18 character-class rules does express "all but worktrees" for the file tools.
  - **Sandbox.**
    - The keys are `sandbox.filesystem.{allowWrite,denyWrite,allowRead,denyRead}`. `denyWrite` beats `allowWrite`, so an allow for
      `worktrees` inside a deny of `.claude` doesn't win. `Edit` rules are merged into them.
    - Class rules that the Seatbelt translation doesn't take are silently ignored. A lower-case ASCII complement works, but names
      starting with a non-ASCII character (a CJK file name) stay writable from Bash.
    - Without the sandbox, an `Edit` deny stops a parsed `touch`, but not `sh -c 'touch …'` or `python3 -c`. The sandbox is what
      governs Bash.
    - With the sandbox on, Claude Code creates `<ws>/.claude/.cc-writes/` itself on every run.
  - **Seatbelt.** The `require-all`/`require-not` profile in section 7 parses and holds:
    - **Denied:** new file, `mkdir`, append, `rm`, `mv` out, `mv .claude`, symlink and hard-link tricks.
    - **Allowed:** writes, `mkdir` and `rm` inside `worktrees/`.
    - **Gaps:** the `worktrees` entry itself can be swapped for a link, though writes through it are still checked at the real
      path; and `.claude` can't be created from inside the sandbox.
    - A create-only rule (`file-write-create` on a regex) leaves existing entries open.
  - **Landlock:** not measurable on macOS.
- V-M11: DV5 (c)'s refusing `security` in a stream-json session and a lease run (`llm.py exec lease-session`) with the token:
  does the run work, and does the logging stub see every Keychain call? Also an interactive session (V-M6's driver), which the
  session_status and wake checks use. Measured before step 2 builds the stub.
  **Done 11-09 (2.1.319, a scratch config dir each, the token, a stub first on PATH that refuses every call and logs the
  subcommand and service).** All three cases work, and the stub logged the Keychain lookups in each:
  - **stream-json:** two turns, one of them a Read.
  - **Lease-shaped:** stream-json with the lease point's tools and `--allowedTools`, a stdio MCP server standing in for
    `lease_relay` (connected), `--permission-prompt-tool` on it, and three turns. The turns used Read, WebSearch (a server tool),
    and WebFetch (asked through the relay, which denied it as told).
  - **Interactive** (V-M6's driver): listed busy then idle, the waiter armed, and woken twice, each wake taking a turn.
  - Every lookup named `Claude Code-credentials-<hash>` / `Claude Code-<hash>`, the config dir's own items, never the user's.
  - A driver note: the driver picks its session by the newest transcript, or by the `session_start` hook, never by name order.
    A reused config dir otherwise hands it an old session, which shows as `unknown` and `not_armed`.
- V-M10: does `claude` run with `CLAUDE_CODE_OAUTH_TOKEN` read or write the Keychain's Claude Code login item (macOS)? Decides DV5.
  **Done 11-09 (2.1.319); corrected the same day by V-M11.**
  - Claude Code reaches the Keychain by running `security` found on PATH. A wrapper first on PATH, which logs only the subcommand
    and the `-s` service name (a write carries the secret in its arguments), sees every call.
  - Positive control, a normal subscription run with the shared config dir: seven `find-generic-password` calls (`Claude
    Code-credentials`, and one for `Claude Code`).
  - With the token and the shared config dir: three `find-generic-password` calls on the user's items. No `add-`/`delete-`
    call, and the item's `mdat` is unchanged. So a token run on the shared dir **reads** the subscription login and doesn't
    write it.
  - **With a scratch `CLAUDE_CONFIG_DIR`, the lookups name the config dir's own items:** `Claude Code-credentials-<8 hex>` and
    `Claude Code-<8 hex>`, a different suffix per dir. None of them exists in the Keychain, so the user's login isn't read.
    The first record said the name doesn't change with the config dir. That run hadn't set the dir: zsh passed
    `--config <dir>` as one word, the helper ignored it, and the run used the shared dir.
  - So the platform's config dir (DV3) and the checks' throwaway dirs keep the user's login out of reach by naming alone. DV5
    (c) guards against a version that changes that naming.
  - With a `security` first on PATH that refuses every call (exit 44, "item not found"), a token run still succeeds.
- V-M6 is now: a driver for an interactive (non-print) throwaway session, under a pty, that the wake and status checks can use.
- V-M4 on macOS: a pid's executable path comes from `ps -o comm=` / `proc_pidpath`, not a file read; the package metadata beside
  it is then read as a file.

## Found by the blind reviews (11-08), all taken

(Where a later round changed how a finding is taken, the later section says so: pending versions are no longer gated, `CAGED_NEEDS`
isn't split, and candidates aren't wrapped in a Keychain-blocked profile. The text above is current.)

- A version whose safety checks fail or are pending still got wake, routing and forks for sandboxed sessions: now limited per version
  (section 6), as `CAGED_NEEDS` does per runtime.
- Safety checks could pass by testing nothing (the model declines to use a tool) and could leak real secrets when the sandbox is
  broken: positive controls, model-free where possible, canaries through the same translation, no output kept (section 2).
- A candidate ran with the user's live login: section 3a.
- The promoted copy could update itself: pinned updater and a digest check per run (section 3).
- No `current` fell back to the PATH copy: `Unsupported`, first install defined (section 3).
- The coverage test contradicted the table (8 of 13 ports): every port has a check; control, which no run can check, becomes a rule
  over the tool's folder (sections 2, 7).
- Platform runs mixed versions with the user's sessions (status listing, forks, resumes, the linked login), and a failed listing
  counted as idle: session-side calls (section 5).
- Running binaries from the user's folders was a new exposure, and V-M3 could leave versions unverified for good: versions are
  installed from the vendor into the store (section 6).
- The transcript isn't written at SessionStart: the version is read from the process's executable (V-M4).
- `npm install` in a neutral job broke tool neutrality: the distribution port (section 1).
- DV2(a) claimed more than it can deliver without the owner's yes for a user-level setting: rewritten.
- Should-fix taken: rename swaps, per-run version records, in-use versions kept, store not backed up, Linux fail-closed, alerts keyed
  by check and reason with a retry for function checks and an escalation after 7 days held back, empty stdin and unknown events
  counted apart, V-M7 for Claude's tool-use signal, the job idle for runtimes not in use, a platform config dir (DV3), costs (DV4).

## Found by the second blind review (11-08), all taken

- "No `current` → the runtime's ports are Unsupported" would have emptied `policy.protect()` and stopped the hooks being kept, so a
  sandboxed session could write its control files: only the ports that start platform runs go off; the rule "a missing or failed
  check never removes a protection" is stated up front (section 3).
- The per-version gate copied `CAGED_NEEDS`, which returns before every handler: an unchecked version recorded nothing (so it was
  never checked), got no notice, and lost the guard. Handlers split into always-run and work-starting; an unreadable version is
  pending; today's `CAGED_NEEDS` gate gets the same split (section 6).
- "`unknown` no longer counts as idle" would have stopped routing for every runtime without the status port and every slow tick: a
  separate `unlisted` state for a failed listing (section 5).
- DV3's platform config dir would have broken resumes, forks and the connectors login: limited to runs that do none of those until
  V-M8.
- The one-rule control deny would have denied edits inside `.claude/worktrees/`: V-M9, the enumerated lists until then, and the
  residuals (user-level included) named in doctor (section 7).
- Should-fix taken: candidates under the Keychain-blocked Seatbelt profile, forks need the safety and agent checks, a
  `not-installable` state, versions taken from the platform's own session records (not a bus event), validated and capped, the
  distribution row made a function check with lockfile integrity for every tarball, the digest check named as the safety mechanism,
  nvm's bin kept after the store's, "unknown-event" dropped (not observable), the listing budget per version, the resume hint.

## Found by the third blind review (11-08), all taken

Two of its six blocking findings came from the second round's fixes, and two shared a root the design hadn't stated: every check was
pass or fail, and one result drove promotion, per-version gating and the coverage test at once, so a check that can't be built yet
became a permanent fail. Taken at the root:
- **`not-checkable`**, a third outcome, and **narrow gating** (sandboxed projects only, failed safety checks only, work-starting
  handlers only), stated up front.
- The Steps list still said "`unknown` stops counting as idle": now "`unlisted` added".
- The control check pointed at a rule that can't be written before V-M9, so nothing could be promoted: the interim check is a canary
  write at each enumerated control path through the real translation, with a positive control; the one-rule form comes in step 4.
- With no copy in the store, dispatch's `("agent", "sessions")` load returned before any hold, route or wake: dispatch splits its
  gate (section 3).
- The wake and status checks can't run on a `-p` / stream-json session (`wait()` returns `headless`): `not-checkable` until V-M6
  names an interactive driver.
- The Keychain block by an outer Seatbelt profile can't nest with Claude's own sandbox and kills the network: no wrapping; V-M10 and
  DV5.
- Splitting `CAGED_NEEDS` would have let forged payloads from a runtime without `control` drive `session_record` and `mail_end`:
  `CAGED_NEEDS` stays; the split is for the per-version gate only.
- Should-fix taken: throwaway worlds under `private_tmp()`, the login rule for session-version checks, the platform config dir only
  in the child's environment, pin and digest for every store copy a run executes, pending as today and wake gated in hubbus, a poll
  instead of an event trigger, Desktop's and the daemon's versions found on npm, the citation fixed.

## Verification pass (11-08)

Every earlier fix checked against the code; two gaps found and closed: the no-copy refusal must not change `capabilities()` (or
`CAGED_NEEDS` would turn sandboxed projects' hooks off on a fresh install), and the per-version gate covers `hub-event worktree`.
No blocking problem left.
