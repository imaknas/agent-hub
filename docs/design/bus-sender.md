# Who sent a letter: the bus sender a sandboxed project can't forge

Status: proposal (11-01), for the owner's decision. Found by the fifth blind review of `projects-and-repos.md` (section 9,
"Holes that exist today"); the other holes on that list are closed in code, this one needs a decision first.

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

## Decisions needed from the owner

1. A (outbox, recommended) or B (broker)?
2. Until it lands, sandboxed projects keep a writable bus: is that acceptable for the current sandboxed projects (idea
   sandboxes), or should `manifest.update` from them be held for the owner's approval in the meantime? (A hold is a
   stopgap, so it is only offered, not built.)
