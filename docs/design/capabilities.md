# Extra capabilities for idea sandboxes + declarations independent of the runtime

> **Status**: on 10-25 the owner confirmed A–F as proposed; implemented and verified the same day (see "10-25
> implementation and verification" at the end).

Source: the assistant's request `m10251437-1f92` (10-25 14:37). The direction the owner agreed: the platform maintains
a fixed set of "capabilities", projects declare which they use, and sessions in a sandbox run things under the
corresponding limits through one platform command. The first capability is `gpu`.
The owner also specified on 10-25: **capabilities in the registry are declared in runtime-independent vocabulary, with
the Claude-specific translation gathered in one place**.

The draft and live tests were done by another agent-hub session ("testing"), handed over on 10-25 to the session of
this design, which from then on owns the design, implementation and commits. The profile prototype and the two escape
tests are copied in `.state/cap-prototype/` (not in version control).

## Why

`idea-1` (rebuilding a room in 3D from photos) needs Blender to render. On the Mac, Blender only supports Metal, and
Claude Code's sandbox blocks it from detecting the GPU, so it crashes at start; today the owner has to run it himself
in a terminal. Claude Code's sandbox has no setting to open GPU/IOKit/Metal, and no custom Seatbelt profile.

What "no hands-on work from the owner" means (confirmed by the owner in the "testing" session on 10-25): **he does not
have to operate Blender himself**. Confirmation on the phone stays as it is: in work mode `hub-run …` is a Bash
command, sent to the phone every time for him to allow. That human check does not change.

## 10-25 live tests (the "testing" session; all in temporary directories or test sandboxes, deleted afterwards)

1. **The platform's own macOS sandbox rules (Seatbelt) let Blender use the GPU**: the same render script, unrestricted
   and under the `gpu` rules, produced two preview images, a `.blend` and a `.glb` in about 1 second both times.
2. **The same rules block 16 escapes**: writing outside the folder, reading `~/.ssh`, reading claude credentials,
   reading another project, reading a service's `.env`, reading platform secrets, **the Keychain**, network
   connections (including 127.0.0.1), `open` launching an app, `osascript` sending Apple Events, `launchctl`,
   `crontab`; writing/reading its own folder and the temporary area work. The Keychain is something Claude Code's
   sandbox cannot block (known-issues.md); the platform's rules can (blocking `securityd`'s mach-lookup directly).
   `open` and `osascript` were allowed at first (Calculator really was launched outside the sandbox); only adding
   `(deny lsopen)`, `(deny appleevent-send)` and a process-exec deny blocked them.
3. **Sandboxes cannot be stacked**: running `sandbox-exec` inside Claude Code's sandbox always gives
   `sandbox_apply: Operation not permitted`. So the step that applies the platform's rules cannot happen inside the
   session's sandbox.
4. **Verified in the real location**: the test folder was put in `~/workspace/<probe>` (the rules first block all of
   `~/workspace`, then allow its own folder, and the two really overlap there). In a temporary directory this overlap
   cannot be tested.
5. **Claude Code's `excludedCommands` works, but only for "standalone" commands**: running the excluded wrapper
   command alone leaves Claude Code's sandbox; chained with other commands (`… && …`, `…; …`), the whole chain stays in
   the sandbox, and escapes chained after it are blocked. The result is the same with or without the lease's
   `--settings`.

## Three layers: declaration, translation, enforcement

```
project registry      ── bin/policy.py ──┬─ bin/runtime_claude.py → .claude/settings.local.json, lease --settings
(neutral vocabulary)                     │   (how Claude Code does it; the only place in the platform
  enforce.sandbox                        │    that knows Claude's settings format)
  enforce.capabilities                   │
  network, allow_write                   └─ bin/seatbelt.py → the platform's Seatbelt profile, for cap-runner
                                             (how macOS blocks it; goes through no agent)
```

- **`bin/policy.py` (new)**: computes a project's policy from the registry, in neutral words only:
  `{workspace, write: [paths], deny_write: [paths], deny_read: [paths], credentials: [paths], network: [domains],
  platform_commands: [paths], capabilities: [names]}`. The content is what `render-project-settings.desired()` computes
  today, with Claude's syntax (`Read(...)`, `Edit(...)`, `Bash(... *)`) removed. `credentials` (`~/.ssh`,
  `~/.claude*`, `~/.aws`, `~/.config/gh`) is today written only in `lease-runner.deny_settings()`; it moves here into
  the same policy, with the locations in `hub.json`'s `credentials` (no runtime paths in the code). The Keychain is not
  a file; `seatbelt.py` handles it itself. Paths all come from `hubconf` (the `~/agent-hub` hard-coded in `desired()`
  today is fixed along the way).
- **`bin/runtime_claude.py` (extended)**: its header already says "switching tools changes only this file", but so far
  only mailbox routing follows that. These two move in:
  - `settings(policy)` → the `sandbox` block + the platform's deny/allow rules (the Claude half of today's
    `desired()`), `settings_path(workspace)`, `sandbox_rendered(workspace)` (`lease-runner.work_ready` reads
    `settings.local.json` itself today)
  - `lease_settings(policy)` → the lease's `--settings` file (today's `lease-runner.deny_settings()`, including the
    rules blocking `security`)
  `render-project-settings.py` keeps only the mechanism "write the file, record what was written last, detect drift";
  what to write it asks `runtime_claude`.
- **`bin/seatbelt.py` (new)**: `profile(policy, cap)` → SBPL text. The **read** lists (`deny_read`, `credentials`) use
  the same `policy` as Claude's, not written by hand separately (the prototype's read list was hand-written; that
  changes now). **Writes** deliberately do not follow `policy.write`: only the project folder + the temporary area. Work
  run on a project's behalf has no reason to write the bus directly (that would bypass `hubbus`'s checks) or the shared
  package caches.

**Not moved this time, left to m10251446 (see the last section)**: the lease's `claude` flags, stream-json parsing,
tool names, rate limit, `--resume`, the Claude tool rules in `lease-relay.py`, the tool lists in
`contracts/llm-points.yaml`. Once that is done, knowledge of Claude Code is only in `runtime_claude.py` (translation),
`llm.py` (the only place that runs `claude -p`), plus the transport layer of `lease-relay.py`: it is itself Claude's
`--permission-prompt-tool` (MCP, answering `behavior`/`updatedInput`); its rules will decide by kind, but the process
and the return format stay Claude-specific.

**Boundary test** (`tests/test_runtime_boundary.py`, new): the neutral files touched this time (`policy.py`,
`seatbelt.py`, `cap-runner.py`, `hub-run`, the capability section of `registry.py`) may not contain Claude's words
(`.claude`, `Read(`/`Edit(`/`Bash(`, `claude`). `lease-runner.py` is added once m10251446 is done.

## Registry

```yaml
type: idea
enforce:
  sandbox: true
  capabilities: [gpu]     # new; only capability names the platform has are accepted
network: [...]            # existing, already neutral
allow_write: [...]        # existing
```

- `registry.py` validates: every name in `capabilities` must be in the platform's capability list;
  `enforce.sandbox: true` is required; only `type: idea` is accepted this time.
- **Capability list**: `hub.json`'s `capabilities: {gpu: {risk: low, default_for: [idea], description}}` +
  `capabilities/<name>.sb` (that capability's profile fragment). A new capability later = one more list entry + one
  fragment file, with no change to the entry point or the projects.
- **`gpu` is given to idea sandboxes by default**: `create-idea-sandbox.py` **writes it explicitly** into the registry
  at creation per `default_for` (the registry shows what a project has, rather than it being implied by `type`);
  `idea-1` gets one line added. Capabilities with `risk: high` are never added automatically; the owner agrees and the
  registry is changed in the agent-hub session.

**The registry's security fields can only be changed by the platform** (fixing an existing hole): today
`registry.py apply-updates` only checks that "the sending project changes its own registry". So a session in a
sandbox could send a `manifest.update` that turns `enforce.sandbox` off, or widens `allow_write`/`network`; with
`capabilities` added it could grant itself capabilities; changing `type` to `idea` would get it work mode and
capabilities; removing `stats_only` from `privacy` would put an idea's content into the weekly report's cloud call.
Changed to: if `type`, `enforce`, `allow_write`, `network` or `privacy` differ from the current registry, the whole
update is rejected, with an explanation that the agent-hub session must make the change (the owner confirming). No
project has ever changed these fields through manifest.update (git log shows only one update, from the assistant, not
touching them), so existing flows are unaffected.

## The entry point: `hub-run` (the platform runs it on the project's behalf)

**Two approaches**

- **A. Exclude + wrap (what the assistant originally proposed)**: put `hub-run` into the project's
  `excludedCommands`; once outside Claude Code's sandbox it applies the platform's rules itself with `sandbox-exec`.
  Tested and works (point 5 above). Drawbacks: security relies on Claude Code judging chained commands correctly, and
  if its judgment ever has a gap, the commands chained after it leave the sandbox wholesale; it has to be written into
  Claude's settings, so switching tools means finding the equivalent mechanism; and the escaped `hub-run` cannot
  reliably know which project it belongs to (`cd` into another project and run it, and it can impersonate).
- **B. The platform runs it (proposed)**: `hub-run` stays in the session's sandbox and only writes the request into
  **its own project folder**'s `.hub-run/requests/`; a resident platform service outside the sandbox picks the request
  up, checks it, runs it under the platform's rules only then, and writes the output and exit code back.
  The only requirement on the runtime is "can write its own folder", which any agent can do; not one line has to be
  added to Claude's settings. A is kept as a fallback.

**How a request proves which project sent it**: the sandbox only lets each project write its own folder; other
projects cannot write into it. The service only scans `<workspace>/.hub-run/requests/` of projects that declare
capabilities in the registry, and **the folder a request is found in is the project it belongs to**; the project
folder used for running comes from the registry, not from anything in the request.
(Not in the shared bus directory: every project can write there, so project A could impersonate B.) `.hub-run/` is
written into `.git/info/exclude`.

**Request format**: `{"v": 1, "id", "cap": "gpu", "argv": [...], "cwd": "<relative to the project folder>",
"timeout_s"}`. `argv` is a JSON list; the service directly execs `sandbox-exec -f <profile> -D WS=… -D HOME=… <argv>`,
with no shell string in between.

**Checks when the service receives a request** (any failure writes back `rejected` with the reason, nothing runs):
- `.hub-run/`, `requests/` and the request file itself must all be inside the project folder after resolving to real
  paths, and be regular files/directories: if `.hub-run` is replaced by a symlink to another project, the result files
  would be written there, so this level must be blocked too (the prototype draft only checked `cwd`). Results are
  written back opening files without following symlinks.
- `id` may only be a plain file name; `cwd`, resolved to a real path, must be inside the project folder (a symlink
  inside the folder pointing out is blocked too).
- `cap` must be in this project's `enforce.capabilities` in the registry; the request file is at most 64 KB; requests
  that are too old or already answered are ignored.
- **The profile is not kept in the project folder**: computed from the registry on every run and written to
  `.state/cap-runner/<project>/<id>/profile.sb` (the session cannot write there). The prototype draft put it in
  `<workspace>/.hub-run/profiles/`: a place the session itself can change, where changing one line amounts to no
  sandbox.

**Service**: `local.agent-hub.cap-runner` (`bin/cap-runner.py`, resident, `KeepAlive`, scanning once a second), written
in agent-hub's registry `services:` (like lease-runner, its plist is generated by `render-services.py`, a Discord alert
is sent when it is not running, and `recover-service.sh` can rebuild it), also registered in `services.tsv`. Not put
into lease-runner: restarting the runner must not kill running work.
- Only `PATH`, `HOME`, `TMPDIR`, `LANG` are passed as environment variables, everything else is cleared (no tokens, no
  `HUBBUS_*`).
- Timeout 10 minutes by default, 30 minutes at most (`hub.json`'s `cap_runner`); on timeout the whole process group is
  killed; at most 2 running at once, 1 per project.
- `stdout`, `stderr`, `exit` are written next to the request; each job writes one line to
  `.state/cap-runner/log.jsonl` (project, capability, argv, exit code, time).
- The service not running: if `hub-run` sees no service pick the request up within 30 seconds, it withdraws the request
  (the service restarts itself when updated, and launchd waits 10 seconds), prints that the platform's run service is
  not running, and exits with 125.
- Any start failure answers `rejected` + the reason (a client is never left waiting); unfinished jobs are marked `lost`
  when the service restarts.

**`hub-run` (the client)**: a small standard-library script, `hub-run --cap gpu [--timeout seconds] -- <command…>`. It
prints the job id first, prints output while waiting, and exits with the same exit code. A session's Bash call has its
own timeout (2 minutes by default, 10 at most); what gets killed is `hub-run`, not the job, so `hub-run --wait <id>` can
reattach. Without `--cap` nothing is accepted (a capability is an authorisation; which one must be stated). The request
file is written to a temporary file first and then renamed (the service never reads half-written JSON); if nobody
picks it up in 30 seconds the request is deleted before exiting, so a late-starting service never runs a job nobody is
waiting for.

## What the `gpu` profile really is (stated clearly)

- The rules **allow everything first, then block the dangerous parts** (a deny list): writes only to its own folder and
  the temporary area; no reading `~/workspace` or `~/services` (except its own folder), memory copies, platform
  secrets, credentials, the Keychain; no network at all (**including 127.0.0.1**, so a job cannot reach lease-runner
  or the assistant); the Keychain's system service blocked; "asking another system service to start a program for it"
  blocked (opening apps, Apple Events, `launchctl`, `crontab`, `at`). **Everything not listed is allowed, Metal
  included**.
- So this time the `gpu` fragment is empty: Metal was never on the deny list. Here the capability's role is
  **authorisation** (who may use the platform runner), not opening another hole. If this later becomes "block
  everything by default, open item by item" (finding out one by one what Blender needs; not done this time), the `gpu`
  fragment becomes the Metal/IOKit rules.
- Compared with Claude Code's sandbox: reads are stricter (all of `~/workspace` and `~/services` blocked), network is
  stricter (fully closed), and it additionally blocks the Keychain and launching programs; writes lose the bus
  directory and package caches, but gain **the whole system temporary area** (`/private/tmp`, `/private/var/folders`,
  where other programs' temporary files are too). Accepted for the trial; if a capability needs tightening, each job
  gets its own `.hub-run/tmp` as `TMPDIR` and the shared temporary area is removed.
- Blocking "looking at other processes' information, sending signals to other processes" will be added too, tested
  live first.
- **The real check is still the phone confirmation**: the rules are a reinforcement; tested to block what is listed
  above; nothing beyond that is guaranteed.

## Leases and the assistant

- **The lease contract does not change**: the command shape is the one the assistant proposed
  (`hub-run --cap gpu -- <command>`), and in work mode it is just a Bash command that needs confirming.
- When the project declares capabilities, work mode's system prompt gets one line: "run programs that need the GPU
  with `<hub>/bin/hub-run --cap gpu -- …` (reattach long jobs with `--wait <id>`)", with the path built from `hubconf`
  (the lease's PATH does not include agent-hub's bin).
- Interactive sessions opened over SSH can use it too (their sandbox also lets them write their own folder).
- The phone confirmation is not loosened. The platform profile does block the Keychain, so "`hub-run` without
  confirmation" can be discussed again later, but it is the owner's decision, not part of this.

## Decisions for you to make

**A. Have the platform run it (B), not excludedCommands (A)** (proposed): reasons above; A tested and works, kept as a
fallback.

**B. `gpu` written explicitly into the registry at creation** (proposed): not implied by `type: idea`; `idea-1` gets
one line `capabilities: [gpu]`.

**C. `type`, `enforce`, `allow_write`, `network`, `privacy` can only be changed by the platform** (proposed): a
`manifest.update` touching them is rejected. This hole exists today, not only for capabilities.

**D. Where the translation layer sits and its scope** (proposed): the neutral `policy.py`; Claude's translation in
`runtime_claude.py`; the platform profile in `seatbelt.py`. This time only "how the settings are written" moves; the
lease's flags, events and tool names are left to m10251446.

**E. The `gpu` profile uses "allow first, then block" during the trial** (proposed): including the known looseness of
the shared temporary area, listed clearly above.

**F. m10251446 handled separately** (proposed): see the next section.

## Order (one commit per step, adding only its own files)

1. `policy.py` + `runtime_claude.settings()`/`lease_settings()`; `render-project-settings.py` and `lease-runner.py`
   switch to them. **Behaviour unchanged**: before and after, the settings computed for every project are byte for
   byte the same as `.state/rendered-settings/`, and the lease's `--settings` too. Add the boundary test.
2. Registry: `enforce.capabilities`, the capability list, `apply-updates` blocking security fields;
   `contracts/project.v1.schema.json` gains the fields (adding fields needs no version bump).
3. `seatbelt.py` + `capabilities/gpu.sb` + `tests/test_cap_profile.py` (the prototype's 16 items; the test folder is
   created under `workspace_root` so the overlap is tested, skipped when not on the hub machine; if the `open` item is
   not blocked it really launches Calculator, so teardown must close it).
4. `cap-runner.py` + `hub-run` + plist + `services.tsv` + a heartbeat-check job + tests (requests in another project's
   folder, `.hub-run` as a symlink, `cwd` escaping, undeclared capability, timeout: all rejected or killed).
5. `create-idea-sandbox.py` writes capabilities per `default_for`; one line added to `idea-1`'s registry; one line added
   to work mode's system prompt.
6. A real run: in `idea-1`, in a session opened with its real settings, run
   `hub-run --cap gpu -- blender -b … -P tools/blender_render.py …` and get images. Add "Sandboxes cannot be stacked"
   and "`excludedCommands` only works for a command on its own" to `known-issues.md`.
7. Reply to `muninn` (`m10251437-1f92`): what was done, how to use the command, how it differs from the proposal (the
   platform runs it instead of excluding it), then `hubbus done`.

## A separate letter: m10251446 (contract independent of the runtime)

On 10-25 at 14:46 the assistant separately asked that `contracts/lease-runner.v1.schema.json` stop using Claude Code's
vocabulary: tool names in events become generic categories
(`kind: ask_user|read|search|write_file|run_command|open_web|other` + `summary`), questions use a generic structure,
rate limit becomes `windows: [{id, label, used, resets_at}]`, `/terminal` returns `{command, hint}`, error code `auth`
becomes `agent_login`.

It is the same principle as this document, and its translation table goes into the same `runtime_claude.py` (tool
names → kind, stream-json → neutral events, AskUserQuestion ↔ generic questions, rate limit → windows, the resume
command, error classification; `lease-relay.py`'s rules decide by kind). But it needs a parallel-running deadline agreed
with the assistant and involves the `permission-requests` contract the assistant owns, so **it gets a separate
design**, not confirmed together with this one; it starts after this one is done.

## 10-25 implementation and verification

Commits: `2568776` (policy.py, Claude settings translation moved into runtime_claude) → `c3583ea` (registry
capabilities, locking security fields) → `61ff364` (seatbelt.py) → `92b2898` (cap-runner, hub-run) → `5416817`
(default gpu, idea-1, prompt) → `fa95f4a` (start failures must answer, known-issues).

- **Behaviour unchanged**: before and after the move, the Claude settings computed for 24 projects and the lease's
  `--settings` are byte for byte the same; `render-project-settings.py --report` shows no drift.
- **Where it differs from the design**: the service became a registry service (the "Service" section above), with no
  hand-written plist and no separate heartbeat job; the capability-list lookup is in `hubconf.capabilities()`
  (standard library, so the sandbox-creation script can use it too); the git exclude for `.hub-run/` is written by
  cap-runner the first time it sees a request.
- **Found in live tests, fixed**: `(deny signal (target others))` did not block sending signals to other processes;
  changed to "block everything, then allow same-sandbox"; the resident cap-runner was not restarted after
  `hubconf.py` changed, and mixing old and new modules left one job stuck in queued: now every start failure answers
  rejected, and known-issues.md records it ("A long-running service runs the code loaded when it started").
- **Tests**: `test_cap_profile.py` (real sandbox-exec, under workspace_root; files, Keychain, network including local
  and unix sockets, opening apps, Apple Events, launchctl, crontab, signals), `test_cap_runner.py` (14: the various
  rejections, symlinked .hub-run/jobs/requests, timeout killing the whole process group, queueing, restart, the
  client), `test_registry_capabilities.py`, `test_runtime_boundary.py`. All 114 tests pass.
- **A real run**: 10-25 15:26 in idea-1, in a Claude Code session opened with its real sandbox settings (`claude -p`,
  only hub-run allowed), ran
  `hub-run --cap gpu -- blender -b --factory-startup -P tools/blender_render.py -- results/scene-v2.json <output>`:
  2.1 seconds, exit 0, two pngs, .blend, .glb (only Blender writing thumbnails to `~/.thumbnails` was blocked, no
  effect).
- **Not verified yet**: no real lease has run since lease-runner restarted; the extra line work mode adds to the prompt
  has only been seen in `preamble()`'s output, never walked through from the phone.
- Reply to `muninn` `m10251527-a8e0`, `m10251437-1f92` closed.
