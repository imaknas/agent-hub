# Agent rules

> The entry point to this memory tracking system is agent-hub's `AGENTS.md` (`agenthub doctor` prints where the hub is;
> infrastructure-level things: directory conventions, which machine does what, the scheduling/consolidating agents are
> all found there). The project's own design documents are in the project's own README/docs, not here.

## Sync discipline

- This project's git remote is a bare repo on the hub machine. Where the coding agent has session hooks, syncing is
  automatic: the platform installs hooks in the agent's local settings (not version-controlled) that run `git pull`
  at session start, and `git-sync-memory.sh` at session end and before a long session is compacted; it adds only
  `memory/` and `STATE.md`, never `git add -A`, and pushes after committing. Nothing to do by hand.
- Don't commit/push by hand, unless you are sure the hooks didn't fire (e.g. your coding agent has no session hooks).
  `sync-all-memory.sh` also runs hourly on the hub machine as a catch-up sync, so a session that didn't end normally
  doesn't leave anything stuck.
- This AGENT.md is overwritten automatically from agent-hub's template (aligned at every session start); don't edit it
  by hand.

## Memory discipline

- **After every commit or push in the project**, append to `memory/YYYY-MM-DD.md`: what you did, decisions and their
  reasons, pitfalls you hit. A long discussion with no commit also gets an entry when it reaches a conclusion. Write the
  raw record; don't spend effort summarizing: summaries and cross-project links are the job of the "librarian"
  consolidating agent, not something to do here. Notes are written in the content language that the agent-hub block
  of the project's rules file names.
- When the notes are older than the project's latest commit, a reminder appears automatically after a git commit/push
  and at session start (including after the conversation is compacted); when you see it, catch up the notes first,
  then go on with what you were doing.
- File names are always `memory/YYYY-MM-DD.md` (that day's date), and everything from the same day is appended to the
  same file. The one exception: a one-off bulk import/backfill (e.g. a batch of records moved over from another system)
  goes in its own `memory/YYYY-MM-DD-<topic>.md`, dated the day of the import, with the topic in lowercase with
  hyphens.
- Curated topic documents (not a running log: rewritten to be the latest, most accurate version) go in
  `memory/reference/<topic>.md`, with `memory/reference/README.md` kept as their index. Apart from `reference/`, no
  other subdirectories under `memory/`, and no files other than `.md`.
- Date files are a running record: only appended to, never edited afterwards. **Tidying is automatic**: agent-hub's
  nightly "looking back" (`consolidate-memory`) folds date files older than 14 days into `reference/` and then
  `git rm`s them (the history stays), and also corrects statements in `reference/` and `STATE.md` that newer records
  have overturned; every change is recorded in agent-hub's `review-log.md`. So there is no need to move old date files
  yourself in a session.
- Write what is uncertain as it is, as "planned / possibly / to be confirmed": tidying keeps it as it is and won't turn
  it into a settled fact. When the outcome is known, write "done… / confirmed…" explicitly in that day's date file, and
  the next tidying updates `reference/` from it.
- `RULES` (if present): the names of the extra rules this project uses, one per line; their content is in agent-hub's
  `templates/rules/`, and they are put into the project's rules file when a session opens. Managed by agent-hub; don't edit it by
  hand.
- Don't edit `STATE.md` yourself: it is the refined current state maintained by the consolidating agent, rewritten only
  when something substantive changes.
