#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""A project's STATE.md over time, and bringing an earlier one back (`brunna state`).

The librarian rewrites each project's STATE.md every night with a model: it is not deterministic, and a night can get
it wrong. Every version is a commit on the project's memory branch, so a wrong night is undone by going back, not by
rebuilding from the notes (a rebuild would be another model's reading, not the version that was right).

    state.py history <project> [--limit N]
        the commits that changed STATE.md, newest first: when, which, who (the librarian, a session's sync, a restore),
        lines added and removed; and how far the librarian has read the notes
    state.py restore <project> <date YYYY-MM-DD | commit> [--apply] [--keep-checkpoint]
        STATE.md as it was at the end of that day (the last change on or before it) or at that commit. Without --apply
        it only shows the difference. With it: written as a new commit in the platform's memory copy and pushed to the
        memory bare repo (history is never rewritten; a project session gets it on its next pull), with an entry in
        review-log.md. The owner's to-do keys stay exactly as they are now: a keyed line closed since is not brought
        back, one added since is kept, a kept one has today's wording (the keys belong to the registrar and the owner
        side; contracts/state-todo.v2.md). The librarian's checkpoint goes back to that day (never forward: a checkpoint
        already behind it means notes not read yet), so its next run folds the notes written since into the restored
        file; --keep-checkpoint leaves it where it is, for when those notes are what it misread. All or nothing: a
        commit or push that fails is undone, and the checkpoint and the log are untouched. Assistant handoffs the
        librarian applied since that day are not applied again.
        Known limits, being reworked: it takes no lock against the nightly jobs (run it outside the night), and a key
        written on two lines, or an owner line that had no key yet in the restored version, isn't handled exactly.

Works in the platform's own memory copy (memory_copies/<project>), never in the project's folder, so a sandboxed
project is handled the same way. Run it on the hub machine. Exit 0 done (or nothing to do), 1 refused.
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import pathlib
import re
import subprocess
import sys

BIN = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import hublib  # noqa: E402
import i18n  # noqa: E402
import todo_tokens  # noqa: E402

_ = i18n.domain(__file__)
SAFE_PUSH = hubconf.ROOT / "bin" / "git-safe-push.sh"
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RESTORE_SUBJECT = "brunna: restore STATE.md"
DESCRIPTION = "A project's STATE.md over time, and bringing an earlier one back (`brunna state`)."


def refuse(msg: str) -> int:
    print(f"state: {msg}", file=sys.stderr)
    return 1


def git(copy: pathlib.Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(copy), *hublib.NO_SYMLINKS, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"git {args[0]} failed")
    return r.stdout


def no_copy(project: str) -> int:
    """Why there is no memory copy to work in: no such project, the platform itself, or not on the hub machine."""
    if project == hubconf.hub_project():
        return refuse(_("brunna itself keeps no STATE.md; give one of your projects"))
    if project not in hubconf.projects():
        return refuse(_("there is no project {project}", project=project))
    return refuse(_("{project} has no memory copy here: run this on the hub machine", project=project))


def copy_of(project: str) -> pathlib.Path | None:
    d = hubconf.p("memory_copies", "~/.memory-copies") / project
    return d if (d / ".git").exists() else None


def checkpoint(project: str) -> pathlib.Path:
    return hubconf.STATE / f"{project}.sha"


def who(subject: str) -> str:
    if subject.startswith("librarian:"):
        return _("librarian")
    if subject.startswith(RESTORE_SUBJECT):
        return _("restore")
    if subject.endswith("sync memory/STATE.md"):
        return _("a session")
    return subject.split(":")[0][:20]


def history(project: str, limit: int) -> int:
    copy = copy_of(project)
    if not copy:
        return no_copy(project)
    hublib.pull_copy(copy, capture_output=True)
    rows = git(copy, "log", f"-{limit}", "--format=@%H%x09%cI%x09%s", "--numstat", "--", "STATE.md")
    ck = checkpoint(project).read_text().strip() if checkpoint(project).exists() else ""
    print(_("{project}: STATE.md, newest first (the librarian has read the notes up to {ck})", project=project,
            ck=ck[:7] or _("nothing yet")))
    entry = None
    for line in rows.splitlines() + ["@"]:
        if line.startswith("@"):
            if entry:
                sha, when, subject, plus, minus = entry
                print(f"  {when[:16].replace('T', ' ')}  {sha[:7]}  {who(subject):<12} +{plus} -{minus}")
            entry = (line[1:].split("\t") + ["", "", ""])[:3] + ["0", "0"] if len(line) > 1 else None
        elif line.strip() and entry:
            a, d, _f = line.split("\t", 2)
            entry[3], entry[4] = a, d
    print(_("Bring one back: brunna state restore {project} <date or commit>  (shows the difference first)",
            project=project))
    return 0


def resolve(copy: pathlib.Path, ref: str) -> str | None:
    """The commit whose STATE.md is wanted: for a date the last change on or before that day's end, else the commit."""
    if DATE.match(ref):
        day = datetime.date.fromisoformat(ref)   # ValueError for a date that doesn't exist; main() says so
        end = datetime.datetime.combine(day, datetime.time(23, 59, 59), hubconf.tz())   # the owner's day
        return git(copy, "log", "-1", "--format=%H", f"--before={end.isoformat()}", "--", "STATE.md").strip() or None
    r = subprocess.run(["git", "-C", str(copy), "rev-parse", "--verify", "-q", f"{ref}^{{commit}}"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def keep_keys(target: str, current: str, owner: str) -> tuple[str, list[str], list[str]]:
    """target with the owner's keyed to-do lines exactly as current has them: (text, keys kept that the target lacked,
    keys the target had that are gone now)."""
    P = todo_tokens.patterns(owner)

    def keyed(lines):
        out = {}
        for a, b in P.section(lines):
            for i in range(a, b):
                if (m := P.key.search(lines[i])):
                    out[m.group("key")] = i
        return out
    cur = current.splitlines()
    now = {k: cur[i] for k, i in keyed(cur).items()}
    lines = target.splitlines()
    then = keyed(lines)
    at = {i: k for k, i in then.items()}
    out = []
    for i, line in enumerate(lines):
        k = at.get(i)
        if k is None:
            out.append(line)
        elif k in now:
            out.append(now[k])   # today's wording of a key both have
        # else: a key closed since, left out
    gone = [k for k in then if k not in now]
    added = [k for k in now if k not in then]
    if added:
        secs = P.section(out)
        if secs:
            a, b = secs[0]
            while b > a and not out[b - 1].strip():
                b -= 1
            body = [l for l in out[a:b] if not P.is_none(l)]
            out[a:b] = body + [now[k] for k in added]
        else:
            out += ["", todo_tokens.tokens(hubconf.lang_content())["todo_heading"], ""] + [now[k] for k in added]
    return "\n".join(out) + ("\n" if target.endswith("\n") or not target else ""), added, gone


def restore(project: str, ref: str, apply: bool, keep_checkpoint: bool) -> int:
    copy = copy_of(project)
    if not copy:
        return no_copy(project)
    if hublib.pull_copy(copy, capture_output=True).returncode != 0:
        return refuse(_("{project}: could not bring the memory copy up to date (git pull); nothing changed",
                        project=project))
    sha = resolve(copy, ref)
    if not sha:
        return refuse(_("{project}: no STATE.md version at {ref}", project=project, ref=ref))
    try:
        target = git(copy, "show", f"{sha}:STATE.md")
    except RuntimeError:
        return refuse(_("{project}: the commit {sha} has no STATE.md", project=project, sha=sha[:7]))
    when = git(copy, "log", "-1", "--format=%cI", sha).strip()[:10]
    current = (copy / "STATE.md").read_text() if (copy / "STATE.md").exists() else ""
    text, added, gone = keep_keys(target, current, hubconf.owner_tag())
    diff = "".join(difflib.unified_diff(current.splitlines(True), text.splitlines(True), "STATE.md (now)",
                                        f"STATE.md ({when}, {sha[:7]})"))
    if not diff:
        print(_("{project}: STATE.md is already as it was at {sha} ({date})", project=project, sha=sha[:7], date=when))
        return 0
    print(diff, end="")
    for k in added:
        print(_("kept, added since: {key}", key=k))
    for k in gone:
        print(_("not brought back, closed since: {key}", key=k))
    if not apply:
        print(_("Nothing changed. Add --apply to bring this version back (as a new commit; history stays)."))
        return 0
    # all or nothing: the copy is the librarian's, and a half-done restore (a commit that didn't go out, a merge left
    # open) would make it skip the project every night. Only once the push is done do the checkpoint and the log move.
    before = git(copy, "rev-parse", "HEAD").strip()
    (copy / "STATE.md").write_text(text)
    try:
        git(copy, "add", "STATE.md")
        git(copy, "commit", "-q", "-m", f"{RESTORE_SUBJECT} to {sha[:7]} ({when})")
        push = subprocess.run([str(SAFE_PUSH)], cwd=copy, capture_output=True, text=True)
        if push.returncode != 0:
            raise RuntimeError(((push.stderr or push.stdout).strip().splitlines() or ["git-safe-push failed"])[-1])
    except RuntimeError as e:
        if (copy / ".git" / "MERGE_HEAD").exists():
            git(copy, "merge", "--abort", check=False)
        git(copy, "reset", "-q", "--hard", before, check=False)
        return refuse(_("{project}: the restore could not be committed and pushed ({error}); nothing changed",
                        project=project, error=e))
    new = git(copy, "rev-parse", "HEAD").strip()
    # back to that day, never forward: a checkpoint behind it means notes the librarian hasn't read yet
    ck = checkpoint(project).read_text().strip() if checkpoint(project).exists() else ""
    behind = bool(ck) and subprocess.run(["git", "-C", str(copy), "merge-base", "--is-ancestor", sha, ck],
                                         capture_output=True).returncode == 0
    moved = not keep_checkpoint and behind and ck != sha
    if moved:
        checkpoint(project).write_text(sha + "\n")
    label = todo_tokens.tokens(hubconf.lang_ui())["reason_label"]
    today = datetime.datetime.now(hubconf.tz()).date().isoformat()
    log = hubconf.data_file("review-log.md")
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(f"## {today} · {project}\n\n**commit**: `{new}`\n\n{label}: "
                + _("restored to {sha} ({date}) with brunna state restore", sha=sha[:7], date=when)
                + f"\n\n```diff\n{diff}\n```\n\n---\n\n")
    print(_("{project}: STATE.md is back to {sha} ({date}), commit {new}", project=project, sha=sha[:7], date=when,
            new=new[:7]))
    if moved:
        print(_("The librarian folds the notes written since {date} into it on its next run (--keep-checkpoint "
                "skips that, when those notes are what it misread).", date=when))
    return 0


def positive(text: str) -> int:
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError(_("a whole number of 1 or more"))
    return n


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="brunna state", description=_(DESCRIPTION))
    sub = ap.add_subparsers(dest="command", metavar="history | restore", required=True)
    h = sub.add_parser("history", help=_("who changed a project's STATE.md when, newest first"))
    h.add_argument("project", help=_("the project's name"))
    h.add_argument("--limit", type=positive, default=30, help=_("how many changes to list (default 30)"))
    r = sub.add_parser("restore", help=_("bring back STATE.md as it was on a day or at a commit"))
    r.add_argument("project", help=_("the project's name"))
    r.add_argument("ref", metavar="date|commit", help=_("YYYY-MM-DD (the end of that day, your time zone) or a commit "
                                                        "from history"))
    r.add_argument("--apply", action="store_true", help=_("write it back as a new commit; without it nothing changes"))
    r.add_argument("--keep-checkpoint", action="store_true",
                   help=_("don't have the librarian read the notes since that day again (when those notes are what it "
                          "misread)"))
    a = ap.parse_args(argv)
    if a.command == "history":
        return history(a.project, a.limit)
    try:
        return restore(a.project, a.ref, a.apply, a.keep_checkpoint)
    except ValueError:
        return refuse(_("{ref} is not a date (YYYY-MM-DD) that exists", ref=a.ref))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
