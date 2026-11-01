# Who sent a letter: the bus sender a sandboxed project can't forge

Status: built (11-01), option A (the owner's choice, 11-01); how it was built is in "As built" at the end. Found by
the fifth blind review of `projects-and-repos.md` (section 9, "Holes that exist today").

## The problem

A letter's sender is the `source` column of its row in the bus (`hubbus`, one SQLite file in `hub.json` `bus_dir`).
`hubbus send --from <project>` sets it freely, and, more to the point, **the bus file is writable in every sandbox**
(`policy.py` puts `BUS_DIR` in `write`, so `agenthub send` works from a sandboxed session, service or job). A
sandboxed project can therefore insert any row it likes, with any `source`.

What that reaches today:

- `registry.py apply-updates` (every 15 minutes) applies a `manifest.update` to the registry entry of **the sender**.
  The security fields are refused, but `services:` and `jobs:` (their `run:` commands) are not. A sandboxed project
  that sends a `manifest.update` as an **unsandboxed** project gets its command run as that project's service, i.e.
  with the user's authority: a full escape within one platform tick.
- Any other consumer that trusts `source`: dispatch (which project's agent handles a request, with what context), the
  owner side (who asked a question), `done`/`drop` of another project's tasks and questions, `merge.done` events the
  status page counts.
- Because the whole file is writable and readable, a sandbox can also read every project's letters and rewrite or
  delete rows (the log is append-only only by convention).

No check inside `hubbus` can fix this: the attacker doesn't run `hubbus`, it writes the file.

## What any fix has to do

1. Take the bus file out of every sandbox (neither readable nor writable): authority over `source` must come from a
   channel the project can't write directly.
2. Bind the sender to something the sandbox already enforces per project: its own folders (only it can write them)
   or a secret only its sandbox can read.
3. Keep `agenthub send/inbox/done` working from sessions, services and command jobs, on macOS and Linux, without
   unix-domain sockets (Linux's adapter blocks `socket(AF_UNIX)`; `landlock.py`).
4. Unsandboxed projects keep the user's authority and may stay on today's path; nothing changes for them.

## Options

**A. A per-project outbox (recommended).** A sandboxed project's `hubbus send/done/ack` writes one JSON file per
letter into its own outbox (in its repo's git dir, `<workspace>/.git/agent-hub/outbox/`, like the tool caches: only
it can write there, git ignores it). The platform's mailbox service (already running every minute:
`hubbus dispatch`) moves each file into the bus with `source` = the outbox's project, validates it like `send` does,
and deletes it; the sender is never read from the file. For reading, the same service writes the project's own
mailbox projection (its letters only) into `<workspace>/.git/agent-hub/inbox.json`, and `inbox/show/thread/
open-questions` read that file inside a sandbox. Cost: sends from a sandbox are delivered within a minute instead of
at once (the hooks that deliver letters to sessions already work on that scale); the id is assigned by the client
(it already can be: `--id`). No network, no new service, works the same on both OSes.

**B. A local broker.** A platform service on `127.0.0.1:<port>` owns the bus file and authenticates each request by a
per-project token stored in the project's own folder (only its sandbox can read it). Keeps `send` synchronous, but
adds a service every send depends on, a port each runtime's network allowlist must open, and token rotation.

**C. Signed rows.** Each project gets a key only its sandbox can read; rows carry a MAC that consumers verify.
Rejected: the file stays writable and readable, so letters can still be deleted, rewritten or read across projects,
and every consumer has to verify.

## Decisions (owner, 11-01)

1. A, the per-project outbox.
2. No interim hold on `manifest.update`: A was built instead.

## As built (11-01)

Which projects: those whose sessions are sandboxed, `enforce.sandbox: true` (`policy.mail_outbox`; the
services-only `service_sandbox_exempt` doesn't change it, since their sessions are sandboxed all the same). Everything
else keeps the direct path.

- **Policy.** For those projects `policy.write` no longer has the bus directory and `policy.deny_read` has it, so every
  layer closes it: the session sandbox (`denyRead`, no `allowWrite`), Seatbelt and Landlock (both deny it and used to
  allow it again only through `policy.write`). The conformance probe in `tests/test_sandboxed_services.py` checks that
  the bus can be neither written nor read under this machine's adapter.
- **Writing.** `hubbus send/ack/done/publish` run in such a project's folder (or naming it from outside any project)
  write one JSON file per command into `<workspace>/.git/agent-hub/outbox/` (`{"v": 1, "op", …}`, renamed into place
  whole) after the same checks `send` makes, run against the project's view. The client picks the letter id in a shape
  no other writer uses (`m<MMDDhhmm>-<12 hex>`), so a project can't take an id another writer will use. `publish` is
  only `merge.done`/`merge.failed`/`session.report` (what `merge-branch` publishes from a session), source and
  recipient the project itself; the platform keeps them under `outbox:<project>:<id>`. The MCP server does the same:
  it runs `hubbus` in the project's folder.
- **Taking them in.** `hubbus dispatch` (every minute) first reads each outbox with `nofollow` (no link followed, no
  FIFO waited on, at most 512 KiB, at most 100 files a run, oldest name first) and applies each file in one
  transaction as the outbox's project: `send` through the same function as the CLI, with the sender forced to the
  project; a reply only in its own threads; `ack`/`done` only of letters addressed to it; a `session` only when the
  hooks saw that session in this project (`presence`; it decides which session a reply resumes in). A sender named in
  the file is never read. The file is then deleted; one that is malformed or refused is rolled back, reported to the
  project in an `info` letter from agent-hub (`outbox_refused` in its payload, which reaches its sessions through the
  hooks), and deleted too. A busy bus leaves the file for the next run.
- **Reading.** After dispatching, the same run writes each project's view, `<workspace>/.git/agent-hub/inbox.json`:
  the letters and records to or from it, with their delivered/done events, other projects' session ids left out; it
  is replaced with `nofollow.replace` only when it changed. In the project's folder `inbox/show/thread/open-questions`
  load it into an in-memory copy of the bus's tables and run the usual code on it; `inbox` doesn't mark anything
  delivered there (the hooks do).
- **Hooks.** Mail delivery stays outside the sandbox with the user's authority, reading and writing only the bus
  (presence, delivery); it is not one of `hub-event`'s `AS_PROJECT` handlers, which could not reach the bus. Its
  project is the one whose folder the session is in by path: `hubbus.project_of` no longer follows a sandboxed
  project's `.git` pointer files, where a `gitdir:` naming another project's repo used to deliver that project's mail
  (found while building this).
- **Tests.** `tests/test_bus_sender.py`: the attacks (a `manifest.update` claiming another project through `send
  --from`, through a forged outbox file and straight into the SQLite file under the real Seatbelt profile; reading
  another project's letters through the CLI, the hooks and the file) failed on the code before; then the outbox, the
  view, refusals, links, FIFOs, forged sessions, ids, `merge-branch`'s events, the MCP server and the unsandboxed path.

Left as it was: a sandboxed project's `.git/agent-hub/outbox` can be written by anything that runs as the project, so
the narrowing of a confined run (`AGENT_HUB_CONFINED`) and of an agent the platform sends letters for
(`propose_apply.py`) stay checks in the client, as they were while the bus was writable; what changed is that nothing
can send as another project. Letters from a sandboxed project arrive within a minute instead of at once.
