# Idea sandboxes: "projects for exploring" the assistant's ideas

> **10-25 update**: names are no longer limited to `idea-<n>`: an idea uses a human-readable code from the start (e.g.
> room-3d), with the same rules as ordinary project names; whether something is an idea sandbox depends only on
> `type: idea` in the registry (decided by the owner, the assistant's m10251646). Where `idea-<n>` appears below, it is
> the design at the time.

> **Status**: on 10-24 the owner confirmed A–F as proposed; phase one (read-only) is implemented. Phase two (work mode)
> is not done yet; it waits for the owner to ask for it.

> 10-24 19:54 the assistant read this and replied (`m10241954-b179`): same direction, no gaps. What it does on its side
> has been written into the sections below, marked "confirmed by the assistant".

Source: the assistant's request `m10241851-a45a` (10-24 18:51); the design on the assistant's side is sections C and D
of the muninn repo's `docs/design/new-domains-and-projects.md`, and the interface is
`Sandboxes.create(name, purpose) -> str | None` in `src/muninn/core/ports.py` (already on main and deployed, currently
wired to `NoSandboxes`/the local version).

## In one sentence

An idea sandbox = **a normally onboarded project, but with no GitHub remote, marked `type: idea` in the registry**.
It is created through lease-runner's new `POST /v1/sandboxes`; once created it automatically appears in the lease's
attachable list, and leases /attach to it through the existing flow. Upgrading to a real project = changing `type`
and adding a GitHub remote (renaming along the way if wanted).

## Against the assistant's five requirements

| # | Requirement | How | Existing / new |
|---|---|---|---|
| 1 | Lightweight, registered project with a purpose | `onboard-project.sh --no-remote` + registry gets `type: idea`, `purpose` | new: a wrapper script; two registry fields |
| 2 | Read-only by default; writing files/running tools needs the owner's permission | phase one: only today's discuss (read-only). Phase two: leases get a `work` mode | phase two is new (see decision D) |
| 3 | Appears in attachable, can /attach | attachable is already "every registered project", so registering is enough | no change |
| 4 | Upgrading only changes the type; history and files stay | `bin/upgrade-idea.py`: change type, create the GitHub repo + push, register the mirror, call `rename-project.sh` if renaming | new: one script (rename confirmed to handle projects with no origin) |
| 5 | An entry point for the assistant | lease-runner `POST /v1/sandboxes`, same token, same contract | new: one endpoint |

## Decisions for you to make

**A. Where: `~/workspace/idea-<n>` (flat), not `~/workspace/ideas/<n>`** (proposed)
- Flat means the existing cross-project deny rules (`render-project-settings.desired()`) and the transcript↔project
  mapping (`hublib.py`) need not handle nested paths. "Being an idea" is expressed by `type: idea` in the registry, not
  by the folder.
- The name is given by the assistant (`idea-<number>`); the platform only validates the format.

**B. Memory (`.agent-memory`) is required** (proposed)
- When a lease is detached, the handover notes are written only into `<project>/.agent-memory/memory/`
  (`lease-runner.deliver()`); for an idea, the notes are the result. So no ultra-light "just a folder" version, but
  onboarding minus the GitHub step.
- Cost: each idea adds a `~/git-origin/idea-<n>-memory.git` and `~/.memory-copies/idea-<n>`, which the librarian looks
  at every night (skipped when there are no new notes; costs almost nothing).
- There is no remote, so the nightly encrypted backup ("what the workspace has not pushed") takes the whole folder; it
  does not live only on the hub machine.

**C. What `type: idea` actually changes** (the four items below proposed)
1. Default `enforce: {sandbox: true}`: a real write block (may write only its own folder, cannot read other projects,
   network allow list), not just the prompt. Kept when upgraded to a real project; turn it off if needed.
2. Default `privacy: {reflection: stats_only}`: ideas may be very personal; the weekly report only counts, sends no
   content.
3. The `index.md` roster lists ideas as a separate group (`hubconf.py ideas`). `todo.md` is **not grouped**: its
   `### <project>` subheadings are a contract the assistant reads (`^### (\S+)\s*$`), and adding words would break the
   reading; the `idea-` prefix already sets them apart. The status page has no per-project list, so no change.
4. The lease's system prompt gets the purpose ("this is an idea sandbox, exploring: …"), so a lease knows from the
   start what it is about.
   Everything else (librarian, memory-nudge, mailbox, backup) as for ordinary projects.

**D. Whether to do the second level, "writing files, running tools", this time too** (proposed: two phases; phase two
waits for your nod)
- This is really a general lease feature, useful beyond sandboxes. What it needs: a new llm judgment point
  `lease-session-work` (tools add Edit/Write/Bash, but only reads are auto-allowed; all writes go through the
  permission relay), `lease-relay.py` sending "Edit/Write/Bash inside the working folder" to the phone to ask (allow /
  deny / allow for this lease), `POST /v1/leases` accepting `mode`, and a confirmation card on the assistant's side.
  At first only `type: idea` projects can use it.
- Once phase one is done, the assistant can "open a sandbox → read-only (discuss) lease → handover notes"; things that
  need doing go into the handover's to-dos.
- **Confirmed by the assistant**: same shape (two levels, read and write; each write sent to the phone: allow / deny /
  allow for this lease, the same kind as today's permission card); it adds a write-confirmation card on its side.

**E. A repeated call returns the same project, it does not open a new one** (proposed)
- When the assistant resends after a timeout and the same name is already registered, it gets 200 + that name (409 if
  the purpose differs), not `idea-3-2`.
- **Confirmed by the assistant**: its local version takes the same semantics (same name and purpose returns the same
  one, a taken name returns None, no suffix; `813a9ae` deployed); both project sides share contract tests, added when
  HttpSandboxes is connected. `idea-<n>` is globally unique and never reused (the problem of withdrawing the newest idea
  reusing its number is fixed too), so no request id is needed; names can only be `^idea-[0-9]+$`.

**F. Limit: at most 20 un-upgraded idea sandboxes at a time** (`hub.json` `ideas.limit`)
- Over it, `429 too_many` with two numbers, `limit` and `current` (the assistant needs to be able to say "there are
  already N").
- Archiving (**confirmed by the assistant**, not in phase one): only **dropped** ideas get archived (removed from
  attachable, history kept); **shelved** ones are not archived, they may come back. Later via
  `POST /v1/sandboxes/<name>/archive`.

## Interface: `POST /v1/sandboxes`

```
POST /v1/sandboxes   Authorization: Bearer <runner-token>
{"v": 1, "name": "idea-12", "purpose": "<the idea itself, a sentence or two>"}

201 {"v": 1, "name": "idea-12", "created": true}
200 {"v": 1, "name": "idea-12", "created": false}        same name and purpose already exist (resend)
400 {"error": "bad_name", "detail": …}                    the name is not idea-<number>
409 {"error": "exists", "detail": …}                      same name, different purpose, or a real project's name
429 {"error": "too_many", "detail": …, "limit": 20, "current": 20}
500 {"error": "create_failed", "detail": …}               onboard failed (whatever was half done has been cleaned up)
```

- Synchronous response (the local onboard takes a few seconds; the runner is multi-threaded, so other leases' streams
  are not blocked).
- The assistant's adapter: 2xx → return the name; anything else → fail with the reason (the assistant will change the
  `Sandboxes` interface to carry a reason), and tell the owner by error code: too_many → "there are already N; shelve
  or drop some old ideas first"; exists → "the name is taken"; bad_name / create_failed → "could not open it" + detail.
- What records the creation: agent-hub's registry commit (`register idea-<n>`) and lease-runner's log. No bus event
  (hubbus event types are a fixed list, and nobody would read it).

## Files phase one touches

| File | Change |
|---|---|
| `bin/create-idea-sandbox.py` (new) | validate the name → create the folder, README.md with the purpose → `onboard-project.sh --no-remote` (`ONBOARD_REGISTRY_EXTRA` puts `type`/`purpose`/`enforce`/`privacy` in the same commit as the registration) → clean up on failure; one at a time (file lock) |
| `bin/lease-runner.py` | `POST /v1/sandboxes` (calls the script above); PREAMBLE gets the purpose |
| `bin/registry.py`, `contracts/project.v1.schema.json` | add `type: project\|idea` (default project), `purpose`; `registry.py add --extra <JSON>` |
| `contracts/lease-runner.v1.schema.json` | add the `/v1/sandboxes` request/response |
| `bin/hubconf.py`, `bin/gen-index-roster.sh` | `hubconf.py ideas`; ideas as a separate group in the roster |
| `bin/upgrade-idea.py` (new) | `rename-project.sh` if needed → `gh repo create` (account from `hub.json` `github.owner`) + push → change type, origin → mirror-list; dry-run by default, only `--apply` does it |
| `tests/` | contract examples, create/resend/clash/cleanup on failure, upgrade |

## Order (so the contract check doesn't fail again)

Changing `lease-runner.v1.schema.json` makes the assistant's copy
(`contracts/vendor/records/lease-runner.v1.schema.json`) differ from the original, and that night's 01:30
contracts-check fails, just like `secrets` on 10-24. So:

1. agent-hub: registry fields + create script + upgrade script + tests (contract untouched).
2. agent-hub: endpoint + contract in one commit, **and at the same time** a letter to `muninn`: "the contract was
   updated, please sync your copy".
3. The assistant: the same day, sync the copy in `contracts/vendor/records/`, write the `HttpSandboxes` adapter, and
   switch its wiring to it (**confirmed by the assistant**; my letter must name the files to update).
4. I reply to `m10241851-a45a` and close it.

Phase two (if you say yes to D) comes after, with a separate detailed design.

## Verification record (10-24)

- Creation: run directly and through the lease-runner service (launchd environment) once each, about 1 second;
  registry, memory, no remote, sandbox settings, attachable, purpose in the system prompt all confirmed. Resend 200,
  clash 409, bad name 400, limit 429 all tested. The two test sandboxes (idea-999998, idea-999999) have been deleted.
- Upgrade: only dry-run so far (`--apply` creates a repo on GitHub; to be watched on the first real upgrade).
- Cleanup on failure (onboard failing midway): `tests/test_create_idea_sandbox.py` tests it with a fake onboard in a
  temporary hub: if the registry file is not committed yet, everything is cleaned up; if already committed, it is kept
  and the detail says to handle it by hand; a retry works after cleanup. With the cleanup code removed, all three tests
  fail.

## Phase two: work mode (design; on 10-24 the owner said to start)

**Scope**: only projects with `type: idea` and `enforce.sandbox: true` (every idea sandbox). Not open to real projects
yet.

**How to enter it**
- `POST /v1/leases` with `mode: work`, or midway through a lease `POST /v1/leases/<id>/mode {mode: work|discuss}`
  (sent only after the owner taps `允許動手` ("allow hands-on work") on the assistant's card; the runner does not judge
  whether the owner agreed, that is the assistant's card).
- Switching midway = stop the process and resume the same session with `--resume`, under the work judgment point; the
  `state` event carries `mode`.
- A registry without `enforce.work: true` → 400 `work_not_allowed` (from 10-25 this field is what counts, not type;
  written when an idea sandbox is created and carried over on upgrade). `GET /v1/policy` gains `work_projects`
  (projects that can open work).

**Permissions (two gates)**
1. **OS sandbox** (the platform already has it from phase two; `render-project-settings.py` writes it into the
   project's settings): Bash can only write its own folder, cannot read other projects, network only on the allow
   list. This is a real block, not relying on the model obeying.
2. **Every write confirmed on the phone** (`lease-relay.py`, only in work mode):
   - `Edit`/`Write`/`NotebookEdit` with a path inside this project folder → **allowed directly, no question** (decided
     by the owner on 10-25: "only ask when it touches outside the folder"; originally a `write_file` confirmation was
     sent). Outside the folder → refused directly (no question, stricter than asking).
   - `Bash` → **every command is asked** of the owner (`risk: run_command`, summary = the command); "allow for this
     lease" counts for this one only (the conservative choice for the trial; the assistant's card need not show that
     option for `run_command`).
   - Reads (Read/Glob/Grep/WebSearch) auto-allowed as before; WebFetch and questions follow the discuss rules.
   - No answer in 5 minutes = refused, as in discuss.
- Judgment point `lease-work` (contracts/llm-points.yaml): tools add Edit/Write/Bash, auto-allow is still reads only;
  every write goes through the permission relay.

**10-24 implementation and verification**
- Opening depends on "the settings file actually written" (`work_ready`): registry `type: idea`, and `sandbox.enabled`
  true in `<workspace>/.claude/settings.local.json`; the settings are rendered immediately when the sandbox is created,
  without waiting for the 15-minute schedule.
- Live test (test sandbox idea-999997, with the same `--settings` and `--strict-mcp-config` as a lease): Bash writing
  outside the folder blocked, writing inside succeeded, reading the credentials in `~/.ssh` and `~/.claude` blocked
  (`operation not permitted`).
- The relay's rules have unit tests (`tests/test_lease_relay_work.py`: outside the folder, `../`, symlinks refused
  without asking; "allow all" for file writes takes effect; commands asked every time; discuss unchanged).
- Not yet walked through once from the phone (needs the assistant's write-confirmation card).
- **10-25 live test found the OS sandbox does not block the Keychain**: Bash inside the sandbox could look up Claude's
  login credentials with `security find-generic-password` (only the exit code was looked at). So when the owner
  decided to loosen things, **only file writes inside the folder were loosened**; commands are still asked every time,
  and `Bash(security …)` was added to the lease's deny list (only a reinforcement; a different spelling gets around
  it). Loosening commands needs a way to block the Keychain at the system level first.

**Not done**: work for real projects, git push (the sandbox's network allow list has no GitHub write access anyway; idea
sandboxes have no remote either).

**Needed from the assistant**: the write-confirmation card (`write_file`/`run_command`, the same three options);
sending `mode` after "allow hands-on work" is tapped on the card; when `lease-runner.v1.schema.json` changes, sync it
the same day.
