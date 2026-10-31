#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Remind the agent to write its memory notes: speaks up only when "the project has new commits but the notes are
older".

Memory depends on the agent writing it, which easily lapses when busy: once a project's conversation was compacted,
the note-writing habit didn't make it into the summary, and for the next 2.5 days, about 45 commits, nothing more was
recorded. The instruction itself was still there; what was missing was a reminder at the right moment.

Two modes (both run in the project root):
  --post-tool     PostToolUse(Bash) hook: stdin is the hook JSON. If the command is git commit/push
                  (not inside .agent-memory) and the notes are stale, output additionalContext
                  for the model to see.
  --session-start called by sync-local-setup.sh at SessionStart (including after compaction, source=compact):
                  if the notes are stale, print one line of plain text; SessionStart's stdout goes into context.

"Behind" = since the latest note (the mtime of the files under .agent-memory/memory/; memory syncs only hourly, so
commits aren't used) or since the moment of onboarding, commits that represent project work haven't been recorded:
2 or more have piled up, or the earliest is over 30 minutes old. If every commit gets recorded you're never bothered.
Commits produced automatically by schedules and commits touching only files agent-hub manages don't count.
In every other case, no output and exit code 0; nothing is ever blocked.
"""
from __future__ import annotations  # the system python3 (3.9, the one hooks use) must be able to import this too

import datetime
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from hublib import NON_WORK_PATHS, is_work_subject  # noqa: E402  commits that aren't project work, defined in hublib
import i18n  # noqa: E402  no I/O until a message is shown: the silent path stays as cheap as before

_ = i18n.domain(__file__)
GIT_WRITE = re.compile(r"\bgit\b(?:\s+-C\s+\S+)?[^|;&]*\b(commit|push)\b")
MIN_UNRECORDED = 2        # remind when >= 2 commits behind
MAX_AGE_MINUTES = 30      # or the earliest unrecorded commit is over 30 minutes old


def unrecorded_commits(root: pathlib.Path):
    """Return the times of commits representing project work since the latest note (or the moment of onboarding);
    None when not applicable."""
    mem = root / ".agent-memory" / "memory"
    if not mem.is_dir():
        return None
    notes = [p.stat().st_mtime for p in mem.rglob("*.md")]
    # the baseline is at least the moment of onboarding (the memory branch's first commit): work before onboarding
    # owes no notes
    onboarded = subprocess.run(["git", "-C", str(root / ".agent-memory"), "log", "--reverse",
                                "--format=%ct", "agent-memory"],
                               capture_output=True, text=True).stdout.split()
    baseline = max(notes + ([int(onboarded[0])] if onboarded else []), default=0)
    out = subprocess.run(["git", "-C", str(root), "log", f"--since={int(baseline)}",
                          "--format=%ct%x09%s", "--", ".", *NON_WORK_PATHS],
                         capture_output=True, text=True).stdout
    times = []
    for line in out.splitlines():
        ts, _sep, subject = line.partition("\t")
        if ts.isdigit() and int(ts) > baseline and is_work_subject(subject):
            times.append(int(ts))
    return times


def should_nudge(times) -> bool:
    if not times:
        return False
    oldest_age = (datetime.datetime.now().timestamp() - min(times)) / 60
    return len(times) >= MIN_UNRECORDED or oldest_age >= MAX_AGE_MINUTES


def message(times) -> str:
    today = datetime.date.today().isoformat()
    since = datetime.datetime.fromtimestamp(min(times)).strftime("%m-%d %H:%M")
    return _("memory notes are behind: since {since} this project has {n} {n:commit|commits} not yet recorded in "
             ".agent-memory/memory/. Add a section to .agent-memory/memory/{today}.md on what was done, "
             "what was decided and what went wrong (raw notes are fine, no summary needed), then carry on.",
             since=since, n=len(times), today=today)


def nudge_text(root, command: str | None = None) -> str | None:
    """The platform's logic: return the reminder text if the memory notes are older than the latest commit (None if
    not behind). Independent of the runtime.
    command given = a command just finished (only git commit/push and the like trigger a reminder); not given = the
    session just started."""
    root = pathlib.Path(root)
    if command is not None and (not GIT_WRITE.search(command) or ".agent-memory" in command
                                or ".agent-memory" in root.parts):
        return None
    times = unrecorded_commits(root)
    return message(times) if times and should_nudge(times) else None


def main() -> None:
    """The old entry point: worktrees' user-level hooks and projects not yet on the new settings still call it (it must
    keep working until the owner changes the user-level settings).
    --post-tool reads the hook input and outputs through the runtime's inject, word for word as before; --session-start
    prints text directly."""
    import runtime_ports
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--post-tool":
        try:
            rt = runtime_ports.load(ports=("lifecycle",))
        except runtime_ports.Unsupported:   # runtimes without hooks never get here; run by hand, it exits quietly
            return
        ev = rt.parse_event("tool_used", sys.stdin.read())
        if ev is None:
            return
        text = nudge_text(ev.cwd or pathlib.Path.cwd(), ev.command or "")
        if text:
            print(rt.inject("tool_used", text))
    elif mode == "--session-start":
        text = nudge_text(pathlib.Path.cwd())
        if text:
            print(text)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # a failed reminder must not affect the session
        pass
