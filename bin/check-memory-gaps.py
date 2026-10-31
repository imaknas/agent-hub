#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Find projects that "are being talked to but whose memory is not being kept" -- a mechanical check at zero LLM
cost.

Memory notes depend on the agent writing them. If a session was opened before onboarding (so it never read the
instructions), or the agent was too busy to write, memory stays empty, the librarian has nothing to work with, and
nobody notices. A project once went exactly like that: 88 messages after onboarding, 0 memory notes.

How: for each onboarded project, count the messages the user sent in that folder in the last WINDOW hours (sessions
in worktrees under it included), then count the commits the agent made to memory/ in the same period. Messages >=
THRESHOLD with 0 note commits: list it.

Note commits are read from the project's memory bare repo (a session's hook and the hourly sync push them there;
~/.memory-copies waits for the librarian's pull; the workspace's .agent-memory is the project's repo, where the
platform runs no git: docs/design/projects-and-repos.md section 4), and the platform's own commits
do not count: consolidate folding old date files into reference/, what the librarian writes, the onboarding skeleton
(init:), merges during sync -- they all touch memory/, and counting them would hide an agent that wrote nothing.

Runs on the hub machine (after the librarian); transcripts are read from the runtime's transcript_roots (the hub
machine's own, plus those backed up from the remote machine by transcript-backup.sh). While the remote machine's
backup cannot connect, conversations there are counted only by the next check after it recovers.

Usage: check-memory-gaps.py [--window-hours 72] [--threshold 10] [--apply]
Output: one markdown line per project when there are gaps, nothing at all otherwise.
--apply (platform job agent-hub/memory-gaps, nightly after todo): the result is written into todo.md's fixed block
  and the memory-gaps section of today's digest (both removed when there are no gaps), and only a one-line summary
  is printed -- printed whatever the result, so "0 gaps" can be told apart from "did not run at all".
  todo.md is regenerated as a whole by gen-todo.py, so this job has to be scheduled after todo.
"""
import argparse
import datetime
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import hublib  # noqa: E402
import i18n  # noqa: E402
import runtime_ports  # noqa: E402  transcripts are read only through the interface (Claude Code is one implementation)

_ = i18n.domain(__file__)

# commits the platform itself makes to memory/ (not agent notes); merges are excluded separately with --no-merges
PLATFORM_SUBJECTS = ("^init:", "^consolidate", "^librarian")


def human_messages_since(paths: list[str], since: datetime.datetime) -> int:
    """Count only messages the owner actually sent (before 2026-10-03, messages from other sessions and background
    notifications were counted too)."""
    rt = runtime_ports.load(ports=("transcripts",))
    return sum(1 for t in rt.turns(paths, since, worktrees=True) if t.role == "human")


def notes_repo(name: str) -> pathlib.Path | None:
    """Where the project's notes are counted: its memory bare repo, which the platform owns and the hourly sync keeps
    current. Not the project's .agent-memory: that is a worktree of the project's repo, whose .git config a sandboxed
    project writes (a `git log` there can run its gpg.program; docs/design/projects-and-repos.md section 4)."""
    import policy
    doc = hubconf.projects().get(name)
    return policy.memory_bare(name, doc) if doc else None


def note_commits_since(repo, since: datetime.datetime, branch: str = "agent-memory") -> int:
    """How many agent note commits the memory repo's branch has since `since`; -1 when there is no memory repo."""
    if not repo or not pathlib.Path(repo).is_dir():
        return -1
    r = subprocess.run(
        ["git", "-C", str(repo), "log", branch, f"--since={since.isoformat()}", "--format=%h", "--no-merges",
         "--invert-grep", *(f"--grep={g}" for g in PLATFORM_SUBJECTS), "--", "memory/"],
        capture_output=True, text=True,
    )
    return len(r.stdout.split()) if r.returncode == 0 else -1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-hours", type=int, default=72)
    ap.add_argument("--threshold", type=int, default=10)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=args.window_hours)

    try:
        runtime_ports.load(ports=("transcripts",))
    except runtime_ports.Unsupported as e:
        # this check needs "there are conversations": without transcripts it explicitly does not check (not "no gaps")
        msg = _("memory gap check skipped: {reason}", reason=e)
        print(msg)
        if args.apply:
            write_todo_block(hubconf.data_file("todo.md"), [])
            hublib.digest_append("memory-gaps", f"## {heading()}\n\n{msg}")
        return
    roster = hubconf.roster()
    gaps = []
    for name, paths in hublib.project_paths().items():
        if name not in roster or not roster[name].get("workspace"):
            continue  # the platform itself and projects without a workspace have no memory
        msgs = human_messages_since(paths, since)
        if msgs < args.threshold:
            continue
        commits = note_commits_since(notes_repo(name), since)
        if commits == 0:
            # `**{project}**` is read back below (the names in the summary line): translations keep it
            gaps.append("- [ ] " + _("**{project}**: {n} messages in the last {hours} hours, but no new memory notes; "
                                     "ask {project}'s session to write the important decisions and progress into "
                                     "`.agent-memory/memory/`", project=name, hours=args.window_hours, n=msgs))
    if not args.apply:
        if gaps:
            print("\n".join(gaps))
        return
    write_todo_block(hubconf.data_file("todo.md"), gaps)
    hublib.digest_append("memory-gaps", f"## {heading()}\n\n" + "\n".join(gaps) if gaps else "")
    names = " ".join(re.findall(r"\*\*([^*]+)\*\*", "\n".join(gaps)))
    print(_("memory gap check: {n} {n:project|projects} with conversations but no memory notes", n=len(gaps))
          + (_(" ({names})", names=names) if names else ""))


def heading() -> str:
    """the heading of the digest section and the todo.md block (looked up per call: the language is read at run time)"""
    return _("Projects whose memory is not being kept")


def write_todo_block(todo: pathlib.Path, gaps: list[str]) -> None:
    """todo.md's <!-- memory-gaps:begin/end --> block: put at the end of the file when there are gaps, removed when
    there are none. Nothing is done when todo.md does not exist yet."""
    if not todo.exists():
        return
    s = re.sub(r"\n*<!-- memory-gaps:begin -->.*?<!-- memory-gaps:end -->\n?", "\n", todo.read_text(), flags=re.S).rstrip() + "\n"
    if gaps:
        s += ("\n<!-- memory-gaps:begin -->\n## " + heading() + _(" (mechanical check)") + "\n\n"
              + _("Conversations, but no new memory notes: the librarian has nothing to consolidate, and STATE.md keeps "
                  "getting staler.") + "\n\n"
              + "\n".join(gaps) + "\n<!-- memory-gaps:end -->\n")
    todo.write_text(s)


if __name__ == "__main__":
    main()
