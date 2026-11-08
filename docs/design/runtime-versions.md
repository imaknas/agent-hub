# Runtime versions and compatibility

Status: design, 11-08, revised after two blind reviews the same day (their findings are in "Found by the blind reviews" at the end).
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
server can change is re-checked as runs go; every version the user's sessions run is checked when first seen, and until it passes,
what the platform starts in those sessions is limited as for a runtime without the port.** And one rule over all of it: **a missing
or failed check never removes a protection; it only withholds work.** Deny rules, control paths and the guard stay whatever a
check says. This is the tool-neutrality rule
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
the tool's own updater off for a run of this copy: Claude `DISABLE_AUTOUPDATER=1`, Codex its update and daemon-auto-start settings in
the platform home; V-M5), and `MIN_VERSION`. The neutral job and runner only call these. A runtime without the port (ACP, a
command template) keeps today's behaviour and doctor says it is unversioned.

## 2. Conformance checks, one or more per port

A neutral runner, `bin/conformance.py`, runs an adapter's checks (`runtime_claude.CONFORMANCE`, later `runtime_codex.CONFORMANCE`)
against **a binary in the platform's store** and records the result. Each check guards a port and is a **safety** check (it holds a
confinement property) or a **function** check.

How every check runs:
- **In a throwaway world**: a throwaway project folder, tool home and config dir, and the platform's own state, data, secrets and bus
  pointed at temp folders (`AGENT_HUB_STATE`, `bus_dir`…), so a real `hub-event` firing in a lifecycle check can't touch the live
  bus or mark a letter told. Hooks are installed the way the platform installs them (`install_lifecycle` into the throwaway
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
| control | the control paths a sandboxed session is denied are written as one rule over the tool's folder in the project (section 7), and a canary write there is denied | safety |
| lifecycle | each installed event fires with the fields `parse_event` reads; additionalContext reaches the model; a PreToolUse deny stops the tool | function (deny: safety) |
| instructions | a marker in the rules file the adapter writes (`CLAUDE.local.md`) reaches the model | function |
| sessions | the run's transcript / rollout parses with no skipped lines and its version read | function |
| session_status | `claude agents --json` lists a live throwaway session with a status (V-M6) | function |
| session_summary | the last reply is read from the throwaway session | function |
| wake | an idle throwaway session takes a turn after `wake()` (V-M6) | function |
| stream | a short stream-json exchange parses (leases) | function |
| tool_servers | `claude mcp list` parses with no server and with one | function |
| auth | the login check | function |
| distribution | `version()` of the installed binary is the version asked for; the install's lockfile has an integrity hash for every tarball it resolved (the wrapper and the platform package holding the executable, `optionalDependencies` included), each verified on install | function |

`tests/test_conformance_cover.py` fails when an adapter implements a port with no check, so a new port can't ship unguarded.

Results: `.state/runtime-conformance/<runtime>/<version>.json`: per check pass / fail / error with the reason, the binary's path and
digest, the time. A function check that fails is run once more before it counts (a model's answer can miss a schema once); a safety
check counts at once.

## 3. The platform's runs: their own copy, promoted only after a pass, fixed after

- **The store**: `<instance>/runtimes/<runtime>/<version>/`, installed by `distribution.install`; `current` and `previous` are links
  swapped by rename (atomic). A run resolves `current` once at its start, records the version and digest it ran with, and holds that
  path to its end, so a swap mid-run doesn't change what a running job or lease executes. A version folder is removed only when no
  process runs from it (the store keeps a lock per version a run holds); a held-back candidate is removed after 14 days.
- **Fixed after promotion**: every platform run of the copy carries `pin_env()` / `pin_config()` (its updater off), and `binary()`
  compares the executable with the digest recorded at promotion before the run (size and mtime each run, the full digest once a
  day), refusing on a mismatch with "the platform's Claude Code changed after it was checked". The digest check is the safety
  mechanism, not a conformance check: a pinned updater can't be shown to stay off without a newer version to update to, but any
  change it made is caught before the next run.
- **No checked copy, no run**: when there is no `current` (a fresh install, the vendor unreachable on first install, a version that
  never passed, a restore), the ports that **start platform runs** (agent, completion, stream, auth, and confine as the platform's
  own runs use it) are `Unsupported` with that reason and doctor is red; never a fallback to the PATH copy. The ports that **describe
  the user's sessions** stay: control (what `policy.protect()` denies, policy.py:134-139), lifecycle (the hooks
  render-project-settings keeps, :340-343) and the settings translation, so a sandboxed project keeps every deny with no copy in the
  store. `runtime_ports.load` gets the split as a parameter, not a second meaning of `Unsupported`. `hubconf.tool` is no longer used
  for runtimes; `_env` puts the store copy's bin first on PATH and nvm's after it (plugin hooks need nvm's `node`,
  runtime_claude.py:666-680), so nothing a run starts finds the user's `claude` by name; `agenthub.agent_found` reports the store copy.
- **First install** (init, `brunna llm setup`, a restore): install the latest version into the store, log in (section 3a), run the
  checks, promote; until then the features say "no checked copy of Claude Code yet" and init says it is running the checks.
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
  candidate gets the same and nothing else. On macOS the subscription login is in the Keychain, which a throwaway config dir doesn't
  hide, so candidate checks run under the platform's Seatbelt profile with the Keychain blocked (seatbelt.py, `keychain-blocked`);
  on Linux the throwaway home has no `.credentials.json`. Residual: the promoted copy's refresh of the subscription login (jobs with
  `connectors: true`) is not checked before promotion; `check-claude-login` (hourly, a real request) is what catches it after.
- **Codex**: a Business / Enterprise workspace's access token when the instance has one (doesn't refresh). Otherwise the candidate's
  model checks run with the linked `auth.json` only after its model-free checks pass, including that its file store follows the link
  (`resolve_auth_file`), and only when the adapter's credential check (it reads the expiry and `last_refresh`, never the tokens) says
  no refresh can fall due during the checks; otherwise they wait for the next night. Accepted residual, as M9 says: the user's daemon
  and a platform run aren't locked against each other's refresh (`auth.lock` is per home).

## 4. What the server can change is checked as runs go

Promotion checks a binary once; surface C changes without one. Each run asserts what holds its confinement and refuses rather than
running wider:

- **Codex**: the tool list a run sees equals the pinned list for its point kind. If it can be read with no model call (V-M1), every
  run checks it first. If not, it is checked once a day per model, and **a model's points are off until its first check passes**;
  the residual is up to a day of a wider tool list after a catalog change, bounded by the run's permission profile (M8), which
  doesn't come from the server.
- **Claude**: a completion or a `tools: ""` point whose result shows tool use (the signal V-M7 names) is a refusal and an alert. This
  is detection after the fact, not prevention: the tool already ran, inside the run's sandbox and settings, which hold regardless.
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

## 6. The user's sessions: checked when first seen, limited until they pass

- **Record the version at session start.** The transcript isn't written before the first turn (runtime_claude.py:1503), so the
  hook reads it from the session process's executable: its path (Desktop's `ccd-cli/<version>/`, the daemon's
  `releases/<version>-<arch>/`) or the package metadata beside it (nvm's `package.json`), read as files, never run (V-M4). A version
  that can't be read (no `CLAUDE_PID`, runtime_claude.py:80-82; an unknown layout) is recorded as `unreadable`, which counts as
  pending. It goes into the session's record, which `hub-event` writes in the platform's own state; the readers also return the
  version per session from the transcript.
- **Check it then.** A platform job (event-triggered on the session records changing, not on a bus event any project could write)
  takes each version with no result, validates it against the vendor's published list, installs **that exact version from the
  vendor** into the store (never runs the binary in the user's folders, which a sandboxed session might have written) and runs the
  session-side checks on it: lifecycle, instructions, sessions, session_status, wake, confine and control. Minutes, not a night. At
  most 3 installs a day. A version the vendor doesn't publish (a build only Desktop ships) or can't be installed is
  `not-installable`: limited as pending, a doctor row naming it, tried again once a week, not every night.
- **Until it passes, the version is limited for the platform's work, and only that.** The hook's handlers split in two:
  - **always run**, whatever the version's state: reading and recording the version, the session record and its end
    (`session_record`, `mail_end`), memory sync, the deny handlers (`guard`, hub-event:291-293: "cross-project: letters only" must
    hold most for the sessions trusted least), and the SessionStart notice below;
  - **work-starting**, run only when the version passed the checks of the ports they use: letters shown into the session, wake,
    routing to it, forks and resumes of it.
  For a **sandboxed project's** session on a pending, failed or not-installable version, every work-starting handler is off and the
  session is told once at start ("this Claude Code version isn't checked yet; the platform starts nothing in this session until it
  is"); the owner gets an alert when a confine or control check **fails**. An unsandboxed project's session loses only what rides on
  the failed port (a failed wake check: no wake; routed letters wait for the next hook). Today's `CAGED_NEEDS` gate returns before
  every handler (hub-event:338-341), the guard included; it gets the same split.
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
`Edit(/{ws}/**/{name}/**)` (policy.py:218-220), so denying `.claude/**` also denies every edit inside `.claude/worktrees/<x>/`, where
Desktop's worktree sessions work. How it can be expressed is V-M9: a deny on creating a new entry directly under the tool's folder
(Seatbelt can say it for services; whether Claude's permission rules can for sessions is the question), or rendering a deny for every
entry present except `worktrees` plus a deny on new top-level entries. Until V-M9 settles it, the enumerated lists stay, and doctor's
control row names the residuals: a new load location under the tool's folder in the project, a new root-level file name, and a new
load location under the user's `~/.claude` or `~/.codex` (user-level, which the project sandbox doesn't cover). The vendor's release
notes for each promoted version are linked from `brunna runtimes` for the owner to read.

## 8. The user's own copy

The owner asked on 10-24 that the terminal Claude not fall behind (that is why `update-claude` exists). With the platform's copy
separate, the user's copy is the user's: decision DV2.

## Steps

1. **Distribution port + conformance runner + Claude's checks** (the folder-git probe and the completion check ported in; the
   rest added), the results store, `brunna runtimes`, the coverage test. `hub-event` logs unusable payloads; readers count skips and
   read the version; `unknown` stops counting as idle. No change to which binary runs yet.
2. **The store + `update-runtimes`** for Claude: promotion, hold-back, the digest check, the updater pinned, no PATH fallback (with
   the ports split so protections stay), first install, rollback, `MIN_VERSION`; `update-claude` retired by `legacy_name`; callers of
   `hubconf.tool("claude")` move to `binary()`; DV3's platform config dir for the runs it covers, with `PROJECTS`, `transcript()`,
   `lease-resume` and the connectors login left on the shared dir until V-M8.
3. **Session versions**: recorded at start, the job that checks a newly seen version, the handler split and per-version limits
   (today's `CAGED_NEEDS` gate included), `unlisted`, session-side calls on matching
   versions, the SessionStart notice.
4. **Control as one rule** (section 7), once V-M9 says how it can be written, with the sandbox-parity tests updated.
5. **Codex Step 1** builds on all of it: `runtime_codex.CONFORMANCE` (M1, M8, M9's cases), its store and platform home, section 3a's
   login rule, the per-run tool list (V-M1).

## Decisions for the owner

- **DV1, the platform's own copy.** (a) A copy per runtime in the store, promoted only after a pass, fixed after; the user's copy
  never runs platform work (recommended; ~650 MB for both runtimes with `previous` kept, not backed up). (b) Keep sharing the user's
  copy and only check it: a manual update or the tool's own updater changes the platform's runs before any check.
- **DV2, the user's copy (nvm's).** (a) The platform updates it only to a version that passed, the same night. That alone doesn't
  stop Claude Code's own updater from moving it further; turning that updater off is a user-level setting (`DISABLE_AUTOUPDATER` in
  `~/.claude/settings.json`), which AGENTS.md leaves to the owner's direct yes in a session. So (a) is "updated to checked versions,
  and with the owner's yes, not past them". (b) Leave it to the tool's own updater; section 6 checks whatever version shows up
  (recommended: the platform's runs no longer depend on it, and every version the sessions use is checked within minutes). (c) As
  today, latest every night. Desktop's and the Codex daemon's copies update themselves in any case; they are checked (section 6), not
  controlled.
- **DV3, a platform config dir for Claude's platform runs.** Claude's platform runs still share `~/.claude` (settings, plugins, hooks,
  `~/.claude.json`) with the user's sessions; non-`--restricted` runs (leases) load the user's `settings.json`, plugins and hooks,
  written for the user's version. (a) A platform `CLAUDE_CONFIG_DIR` (runtime_claude.py already passes one through), the token
  through `auth_env`, for the runs that neither resume nor fork a session nor need the subscription login: completions and the
  judgment points (recommended, step 2). Leases, mailbox forks of a user's transcript and `connectors: true` jobs keep sharing until
  V-M8 shows a run with its own config dir can resume a session stored in another one: `PROJECTS` is fixed to `~/.claude/projects`
  (runtime_claude.py:61), `transcript()` / `resumable()` look only there (:96-98, :1501-1504), `--resume` takes an id and looks in its
  own config dir, `lease-resume` runs a bare `claude --resume` against the user's dir, and the connectors jobs need the subscription
  login, which a token can't stand in for. (b) Keep sharing for all of them.
- **DV4, cost.** A full pass is ~40 model calls per new version (Claude Code ships a few a week: ~150 calls a week), the daily
  subset ~12 (one completion per schema), each version a user session runs ~10 more. On the subscription; nothing billed per use
  unless `metered_models` says so.

## Measurements

- V-M1: does `codex debug prompt-input` (no model call) show code mode's nested tool list, for the per-run check?
- V-M4: the version of the binary a session runs, read at SessionStart from the session process's executable path or package
  metadata (Claude: `CLAUDE_PID`'s executable; Codex: the daemon's executable; both read as files).
- V-M5: the switch that keeps each tool's updater off for one run: Claude `DISABLE_AUTOUPDATER=1` under `-p` and in a stream session;
  Codex's update and daemon-auto-start settings in the platform home. The check: the binary's digest is unchanged after a run with a
  newer version published.
- V-M6: a throwaway interactive session the checks can drive: Claude's stream-json session idles and takes a turn after `wake()`, and
  `claude agents --json` lists it; Codex's thread through its app-server.
- V-M7: the signal in `claude -p --output-format json` that a run used a tool (`num_turns`, `permission_denials`, or the stream's
  `tool_use` events), with a positive control.
- V-M8: can a Claude run with its own `CLAUDE_CONFIG_DIR` resume or fork a session stored under `~/.claude/projects` (by id, or by a
  path or a link into its own dir)? Until it can, DV3 leaves resumes and forks on the shared dir.
- V-M9: how "create nothing new under `.claude/` but `worktrees`" is expressed: in Claude's permission rules for sessions, and in
  Seatbelt / Landlock for services.

## Found by the blind reviews (11-08), all taken

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
