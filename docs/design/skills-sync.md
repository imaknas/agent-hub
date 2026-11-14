# A project's skills, reviewed by the owner

Status: built 11-15 (`bin/skills_sync.py`, `brunna skills`, the platform job `brunna/skills-pending`), and fixed the
same day after a cold review ("After its cold review" below). Not yet run on a real project; see "Not verified".

## Why

Since 11-14 every sandbox keeps a sandboxed project's own processes off the coding agents' control folders, at any
depth: Claude Code's `.claude` entries, Codex's `.codex` and `.agents` (the adapters' `control_paths`,
`policy.protect()`; `runtime-codex.md`, "Step 4 layer 1"). A skill (`SKILL.md` and its files) is instructions a session
loads by itself, so something inside the sandbox (a session misled by hostile input) must not be able to plant or change
one for later sessions. A sandboxed project that develops its own skills can then no longer change them, and
`render-project-settings` reports its tracked copies as foreign control files every 15 minutes.

The owner's decision (11-15): **skills are developed in an ordinary folder `skills/<name>/` in the project, and
installed into the folders agents load them from only when the owner has reviewed and confirmed them; the platform
reminds the owner when changes wait, so the owner never has to remember.** An automatic copy would defeat the
protection, so there is none: what is installed is always what the owner said yes to.

## What is built

**`brunna skills sync <project> [--adopt] [--yes=<code>]`** (owner-only; not a session command, so no permission rule
allows it in a project session):

- Source: `<workspace>/skills/<name>/`, each with a `SKILL.md`. A folder without one is left out (said); a file beside
  the skill folders is ignored.
- Destinations: each installed runtime's optional `SKILL_DIRS` (its adapter), in each of its session folders: for Claude
  Code `.claude/skills` in the workspace and in each `.claude/worktrees/<name>`, for Codex `.agents/skills` in the
  workspace. A destination sync installed into before keeps being kept while its folder is there.
- It shows, per destination, each skill added / changed / removed, then the executables, the media files it shows only
  as a size and a hash, and what an adapter flags (a Claude skill whose frontmatter declares `hooks`, which its sessions
  run outside the sandbox, or `allowed-tools`; the frontmatter read as YAML and line by line, however a key is quoted
  or spaced), then the diffs.
- **What a diff is against.** Only the manifest says what the owner approved. A skill sync installed and that is still
  exactly as installed is diffed against that copy; when it also equals the new tree it is "same" and nothing is asked.
  Anything else is shown in full, whatever is on disk now: a new skill, an entry sync didn't install (`--adopt`), and a
  copy changed since sync installed it (said, then installed again), even when it already equals the source. Every file
  is shown as text: one that isn't valid UTF-8 or holds a NUL is decoded with escapes and said; only media files (by
  name, not UTF-8, not executable) are a size and a hash. Every character Python doesn't count as printable is escaped
  (controls, bidirectional and zero-width marks, tags, separators), so a file can't hide anything from the owner.
- Then it asks for a typed `yes` on a terminal. It also prints an approval code naming exactly that review (each step's
  place, kind, base and new tree); `--yes=<code>` approves that review and nothing else, and is the only way without a
  terminal. A bare `--yes` is refused.
- Refused inside a sandbox (`isolation.confined_now`, or the platform's state unreadable, which every project sandbox
  does, Linux's without sandboxed-run's mark too), inside a coding agent's session (`runtime_ports.current_session`: an
  agent must not be able to approve), and from a linked worktree of the hub (it writes the live state).
- Session folders other than the workspace count only when they are checkouts (a `.git` entry), at most 64. Skill names
  match case-folded, and removals go first, so a case-only rename on a case-insensitive volume ends with the new name.
- Refused while the project's git still tracks files in the destination folders (read as the project, `project_git`'s
  `tracked`): the install would leave its checkout changed. Each destination goes into the repo's `info/exclude` (as
  the project, shared by its worktrees), so an installed skill never shows as a change.

**How it copies.** The source and the destinations are in the project's folder, where any of its processes can swap a
folder for a link at any moment (B-1, `git-broker.md`), and on Linux nothing keeps them off the control folders either.

- The source is read once into memory: every folder opened from the one above it with `O_NOFOLLOW`, every file opened
  without following a link or blocking on a FIFO, a regular file with one link only, under caps (2 MiB a file, 64 MiB
  and 4000 files in all, 20000 folder entries, 12 levels). A link, a hard link, a special file, a control name inside a skill (`.claude`,
  `.agents`…, case-folded: it would load for a session started below it), or a name that can't be printed refuses the whole sync,
  listed. The diff, the hashes, the question and the install all use that snapshot: a change to `skills/` after the
  review waits for its own review.
- Each skill is written into a new folder next to its place, through descriptors held from the workspace down (new files
  `O_EXCL`, the mode set with `fchmod`: executable bits kept, nothing else), and renamed into place; the entry it
  replaces is renamed aside and removed without following anything (a link is unlinked, never followed). A destination
  folder that is a link is never written through; once a folder is open, swapping its path can't redirect a write
  (the writes land wherever the project moved it, which it could write anyway).
- Removed only when sync installed it. A same-named entry sync didn't install (a project's old copy, the link to it) is
  left alone and refuses the sync; `--adopt` replaces it after the owner saw it in the diff.
- The manifest is in the platform's state (`.state/skills-sync/<project>.json`), never in the project: what was approved
  (the change set's hash) and, per destination and skill, every file's sha256 and executable bit.

**The reminder** (`brunna/skills-pending`, every 15 minutes; `skills_sync.py remind`): for each sandboxed project, and
any sync has installed into, `skills/` read the same way and hashed (its problems included). When that differs from
the approved set: a "notify" row on the status page and doctor, and one owner to-do through the bus (`--type task` from
the platform, one key per review period); a later change set while it waits re-sends the same key (the owner
side takes it as new only when its text changed: the count of skills), so there is one to-do; a sync closes it
(`done`); `skills/` back at what was approved for an hour withdraws it (`drop`), and a change within that hour keeps
the same to-do, so a revert and redo doesn't churn the owner's list. A to-do whose letter couldn't be sent stays in the
manifest and is sent again. It never installs anything, a pending review never fails the job, and a project whose
lock is held (a review waiting at its question) keeps its last row and is looked at the next time, never waited on.

**Foreign control files.** `render-project-settings` passes `skills_sync.owned(project, ws)` to each adapter's
`foreign_controls(ws, owned)`: an installed skill is the platform's only while its tree is exactly the manifest's (the
same files, hashes and executable bits, no link or special file). Codex's check now looks into the top `.agents` entry by entry;
everything else there, and every nested `.agents`/`.codex`, stays a finding. Claude's skips the hooks check only for an
intact installed skill.

## Where each tool loads a project's skills

- Claude Code: `.claude/skills/<name>/SKILL.md` in the project (its docs, "skills"; it is in the adapter's control list:
  `.claude/skills`).
- Codex: `.agents/skills/<name>/SKILL.md`, the repo scope, measured 11-14 with the owner's login (`runtime-codex.md`,
  Step 4 r2, and the note at `runtime_codex.CONTROL`). Its user scope is `~/.agents/skills`, which every sandbox
  already denies (`user_control_paths`).

## Moving a project's skills

For a sandboxed project that tracks its skills in a control folder today (one tracks 44 files under `.agents/skills/`
with `.claude/skills/*` links to them). Measured 11-15 in a scratch repo under the platform's service profile (the
sandbox its services and jobs run in, with the real protect list), not a session's own sandbox:

1. In the project's session, in its worktree branch (copying out of a control folder is a read, allowed):

       mkdir -p skills && cp -R .agents/skills/. skills/
       git rm -r -q --cached .agents/skills .claude/skills      # the index only: allowed
       printf '/.agents/skills/\n/.claude/skills/\n' >> .gitignore
       git add skills .gitignore && git commit -m "refactor(skills): develop skills in skills/, installed by the owner"

   `git mv .agents/skills skills` does not work there (the sandbox can't remove the old copies), and neither can any
   edit of them.
2. `brunna merge-branch` refuses this commit (step 1b: it changes tracked files in a control folder, deletions
   included). The session asks the owner to land it.
3. The owner, in a terminal, with the project's sessions and services stopped (doctor's checkout-race warning, B-1 in
   `git-broker.md`: any of its running processes can race a checkout into writing anywhere the owner can) and doctor's
   git config fence row green for the project (its repo holds nothing that makes the owner's git run code,
   `git-config-fence.md`): in the main checkout, `git merge --ff-only <branch>`. As the owner the old tracked copies are removed from the checkout.
   (Measured: the same fast-forward from inside the sandbox goes through but leaves the old copies in place, untracked
   and ignored, since the sandbox can't delete them.)
4. The owner: `brunna skills sync <project>`. Old copies left in place (the migration's own worktree keeps them, untracked
   and ignored; so does a fast-forward made inside the sandbox, or `git rm --cached` run in the main checkout) show as
   entries sync didn't install and refuse the sync: look at the diff and run it with `--adopt`. A worktree checked out
   before the move still tracks the old copies: sync leaves it out and says so (installing there would change its
   checkout).

Measured end to end 11-15 in a scratch repo (steps 1 and 2 under the service profile, then the owner's fast-forward and
the built `sync`): the plain sync refused on the migration worktree's old link, `--adopt` installed into the workspace's
two folders and that worktree, every checkout stayed clean, and the worktree from before the move was left out. The
foreign check then still named the old `.agents` copies inside both worktrees (Codex loads a nested one for a session
started there): the status row stays red until those worktrees are removed or their old copies deleted by the owner.

From then on the project edits `skills/` like any other file, and the owner gets a to-do when there is something to
review.

doctor says, for a sandboxed project that tracks files in a skill folder, that skills move to `skills/` and points
here.

## After its cold review (11-15)

A blind review found the core claim, "what is installed is always what the owner said yes to", broken twice, and both
are fixed: a file with a stray non-UTF-8 byte or a NUL was shown only as "binary" (B1), and the diff and the "same"
check used what was on disk instead of the manifest, so `--adopt` over an entry identical to the source installed it
unseen, and an installed copy changed to match a changed source was recorded as approved without a question (B2).
Also from it: the printable check (S1), the frontmatter read as YAML and `allowed-tools` (S2), the session refusal and
the approval code (S3), the reminder's non-blocking lock (S4) and its hour before a drop (S5), the fence precondition of
migration step 3 (S6), the case-only rename, the entry and folder caps, a cached `owned`, and a to-do kept until its
close is sent. The reviewer's two mutations (folders opened following links, all of them or all but the last) each fail
a swap test now.

## Limits

- A worktree made after a sync has no skills until the next sync (`brunna skills sync` shows them as added there).
  Whether Claude Code or Codex finds the main checkout's skills from a session in a worktree isn't measured.
- The per-skill swap is two renames: for a moment the skill is absent from that folder.
- Codex's skills go only into the workspace's `.agents/skills` (its adapter names no worktrees as session folders);
  an old `.agents` left in a worktree stays a finding.
- Nothing stops an unsandboxed process (the owner's own, or a project's on Linux) from changing an installed skill; the
  foreign check reports it until the next sync puts it back.

## Not verified

- A real `brunna skills sync` on a project, and the migration above on the project that tracks 44 skills.
- The migration steps inside a session's own sandbox (Claude Code's or Codex's) rather than the service profile.
