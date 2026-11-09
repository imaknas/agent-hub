#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""A project's STATE.md over time, and bringing an earlier one back (`brunna state`; docs/design/state-restore.md).

The librarian rewrites each project's STATE.md every night with a model: it is not deterministic, and a night can get
it wrong. Every version is a commit on the project's memory branch, so a wrong night is undone by going back, not by
rebuilding from the notes (a rebuild would be another model's reading, not the version that was right).

    state.py history <project> [--limit N]
        the commits that changed STATE.md, newest first: when, which, who (the librarian, a session's sync, a restore),
        lines added and removed; and how far the librarian has read the notes
    state.py restore <project> <date YYYY-MM-DD | commit> [--apply [--from <blob>]] [--keep-checkpoint]
        STATE.md as it was at the end of that day (the last change on or before it, the owner's time zone) or at that
        commit, with the to-do list as it is today: every to-do section of today's version, whole and verbatim, in
        place of that day's. An item the owner was waiting on (a key the registrar raised, still open on the bus) that
        was in that day's list and is gone from today's comes back, from its latest terms. Anything else that day's
        list had and today's doesn't, restore lists and leaves out. When today's version has no to-do section at all,
        that day's comes back without the owner's items closed since. A displaced key (an open item's marker outside
        the to-do list) or an unclosed code fence or comment in either version: refused, nothing guessed.
        Without --apply it only shows the difference, and the apply command to run, which names the version it was
        computed from (--from): if STATE.md changes before then, the apply is refused.
        With --apply: a new commit on the memory bare repo (history is never rewritten; a project session gets it on
        its next pull), published only if nobody moved the branch meanwhile; the platform's copy fast-forwarded; the
        librarian's checkpoint moved back to that day (never forward), so its next run folds the notes written since
        into the restored file (--keep-checkpoint leaves it, for when those notes are what it misread); an entry in
        review-log.md; and for an item brought back that the registrar had claimed done, a note to the owner side that
        the claim came from a wrong edit.
    state.py restore <project>
        finishes a restore that was stopped (the computer slept, a crash), and does nothing else

Reads the memory bare repo directly (never pulls in the copy), and writes holding the copy's writer lock
(bin/memlock.py): a nightly job running there makes it refuse at once. A run's progress is recorded in
.state/restore/<project>/ before it publishes, so a stopped run is finished (or, if it never published, dropped) by
the next; the librarian leaves the project alone until it is. Run it on the hub machine. Exit 0 done (or nothing to
do), 1 refused.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import difflib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

BIN = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
import acts_md  # noqa: E402
import hubconf  # noqa: E402
import hublib  # noqa: E402
import i18n  # noqa: E402
import memlock  # noqa: E402
import names  # noqa: E402
import todo_tokens  # noqa: E402

_ = i18n.domain(__file__)
_c = i18n.domain(__file__, content=True)   # the note the owner side reads: content
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BRANCH = "refs/heads/agent-memory"
RESTORE_SUBJECT = "brunna: restore STATE.md"
DESCRIPTION = "A project's STATE.md over time, and bringing an earlier one back (`brunna state`)."
STEPS = ["pull", "checkpoint", "log", "notes"]   # what is left once the commit is built (the record's "todo")


class Refused(Exception):
    pass


def refuse(msg: str) -> int:
    print(f"state: {msg}", file=sys.stderr)
    return 1


def git(*args: str, cwd: pathlib.Path | None = None, bare: pathlib.Path | None = None, check: bool = True,
        **kw) -> str:
    cmd = ["git", *(["--git-dir", str(bare)] if bare else ["-C", str(cwd)] if cwd else []), *hublib.NO_SYMLINKS, *args]
    if isinstance(kw.get("input"), str):
        kw["input"] = kw["input"].encode()
    r = subprocess.run(cmd, capture_output=True, **kw)   # bytes: a CRLF file stays CRLF (text mode would fold it)
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace").strip() or f"git {args[0]} failed")
    return r.stdout.decode(errors="replace")


def ancestor(bare: pathlib.Path, a: str, b: str) -> bool:
    return subprocess.run(["git", "--git-dir", str(bare), "merge-base", "--is-ancestor", a, b],
                          capture_output=True).returncode == 0


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


def bare_of(copy: pathlib.Path) -> pathlib.Path | None:
    """The memory bare repo the copy pulls from (its origin), when it is a repo on this machine."""
    url = git("remote", "get-url", "origin", cwd=copy, check=False).strip()
    url = url.removeprefix("file://")
    p = (copy / pathlib.Path(url).expanduser()).resolve() if url and ":" not in url.split("/")[0] else None
    return p if p and git("rev-parse", "--verify", "-q", BRANCH, bare=p, check=False).strip() else None


def checkpoint(project: str) -> pathlib.Path:
    return hubconf.STATE / f"{project}.sha"


def run_dir(project: str) -> pathlib.Path:
    return hubconf.STATE / "restore" / project


def who(subject: str) -> str:
    if subject.startswith("librarian:"):
        return _("librarian")
    if subject.startswith(RESTORE_SUBJECT):
        return _("restore")
    if subject.endswith("sync memory/STATE.md"):
        return _("a session")
    return subject.split(":")[0][:20]


# --- the rule ---

@dataclasses.dataclass
class Book:
    """What the registrar's ledger says about the project's items: open keys it raised from lines (bound), keys settled
    or withdrawn, open keys the project raised itself, and the latest terms of the open bound keys."""
    open_bound: set
    closed: set
    raised_here: set
    terms: dict

    @classmethod
    def of(cls, led) -> "Book":
        open_ = set(led.open())
        bound = {k for k in open_ if led.bound(k)}
        return cls(bound, {k for k in led.known() if led.settled(k) or led.withdrawn(k)}, open_ - bound,
                   {k: t for k in bound if (t := led.terms(k)[0])})


@dataclasses.dataclass
class Rebuilt:
    text: str | None                 # None: refused
    refused: str | None = None
    readded: list = dataclasses.field(default_factory=list)   # keys brought back
    left: list = dataclasses.field(default_factory=list)      # (why, key, text): closed, project, unknown, nokey


def _ranges(d: acts_md.Doc) -> list[tuple[int, int]]:
    """the to-do sections as line ranges, heading included"""
    return [(s - 1, e) for s, e in d.sections]


def _outside(d: acts_md.Doc):
    """(key, line) for every marker on a line outside the to-do sections"""
    inside = {i for a, b in _ranges(d) for i in range(a, b)}
    for i, line in enumerate(d.lines):
        if i not in inside:
            for rx in d.rx:
                for m in rx.mention.finditer(line):
                    yield m.group(1), i


def rebuild(t: str, c: str, book: Book, read, write) -> Rebuilt:
    """R: T (that day's version) with its to-do sections replaced by C's (today's), plus the open items deleted since;
    or a refusal (docs/design/state-restore.md, "The rule")."""
    dt, dc = acts_md.Doc(t, read), acts_md.Doc(c, read)
    for d, then in ((dt, True), (dc, False)):
        if d.unclosed is not None:
            return Rebuilt(None, _("that day's STATE.md has an unclosed code fence or comment (line {line})", line=d.unclosed + 1)
                           if then else _("today's STATE.md has an unclosed code fence or comment (line {line}); fix "
                                          "it first", line=d.unclosed + 1))
        for k, i in _outside(d):
            if k in book.open_bound:
                return Rebuilt(None, _("{key}, an open item, is outside the to-do list on line {line} of that day's "
                                       "STATE.md", key=k, line=i + 1) if then else
                               _("{key}, an open item, is outside the to-do list on line {line} of today's STATE.md; "
                                 "move it back into the to-do list first", key=k, line=i + 1))
    T = dt.lines
    if dc.sections:
        block: list[str] = []
        for a, b in _ranges(dc):
            if block and block[-1].strip():
                block.append("")
            block += dc.lines[a:b]
        skip = {i for a, b in _ranges(dt) for i in range(a, b)}
        out: list[str] = []
        if not skip:
            out = T + ([""] if T and T[-1].strip() else []) + block
        else:
            placed = False
            for i, line in enumerate(T):
                if i in skip:
                    if not placed:
                        out += block
                        placed = True
                    continue
                if i - 1 in skip and out and out[-1].strip() and line.strip():
                    out.append("")
                out.append(line)
        text = "".join(line + dt.eol for line in out)
    else:   # today's has no to-do section: that day's, without the owner items closed since
        text = t
        closed = {k for e in dt.entries if e.owner == "principal" and (k := dt.carries(e.i)) in book.closed}
        for k in sorted(closed):
            text, _changed = acts_md.apply(text, {"origin": "principal", "type": "settled", "key": k}, read, write)
    out_ = Rebuilt(text)
    c_texts = {acts_md.norm(e.text) for e in dc.tasks() if e.owner == "principal"}
    seen_c = dc.mentioned()
    then = {}   # that day's open owner entries: key (or ("", text)) -> text, first of each; a ticked box was done
    for e in dt.entries:
        if e.owner == "principal" and not acts_md.TICKED.match(dt.lines[e.i]):
            then.setdefault(dt.carries(e.i) or ("", acts_md.norm(e.text)), e.text)
    # an owner line that day without its key yet (the registrar stamps it later that night) is the open item whose
    # latest terms it says
    by_text = {acts_md.norm(t): k for k, t in book.terms.items() if k in book.open_bound and k not in seen_c}
    for k in [k for k in then if isinstance(k, tuple) and k[1] in by_text]:
        then.setdefault(by_text[k[1]], then.pop(k))
    for k in sorted(k for k in then if isinstance(k, str) and k in book.open_bound and k in book.terms):
        if k in seen_c or acts_md.norm(book.terms[k]) in c_texts or k in acts_md.Doc(out_.text, read).mentioned():
            continue   # today's has it, or asks it without the key (the registrar relinks it)
        out_.text, changed = acts_md.apply(out_.text, {"origin": "principal", "type": "reopened", "key": k}, read,
                                           write, {"text": book.terms[k]})
        if changed:
            out_.readded.append(k)
    r = acts_md.Doc(out_.text, read)
    if r.unclosed is not None:
        return Rebuilt(None, _("the result would have an unclosed code fence or comment"))
    in_r, r_texts = r.mentioned(), {acts_md.norm(e.text) for e in r.tasks() if e.owner == "principal"}
    for k, text in then.items():
        if isinstance(k, tuple):
            if k[1] not in r_texts:
                out_.left.append(("nokey", "", text))
        elif k not in in_r and k not in book.open_bound:
            out_.left.append(("closed" if k in book.closed else "project" if k in book.raised_here else "unknown",
                              k, text))
    ticked = {dt.carries(e.i) for e in dt.entries if acts_md.TICKED.match(dt.lines[e.i])}
    for k in sorted(book.open_bound - set(in_r) - set(then) - ticked):   # open, in neither that day's list nor R
        out_.left.append(("open", k, book.terms.get(k, "")))
    return out_


# --- reading ---

def resolve(bare: pathlib.Path, ref: str, tip: str) -> str | None:
    """The commit whose STATE.md is wanted: for a date the last change on or before that day's end (the owner's day),
    else the commit; on the memory branch either way."""
    if DATE.match(ref):
        day = datetime.date.fromisoformat(ref)   # ValueError for a date that doesn't exist; main() says so
        end = datetime.datetime.combine(day, datetime.time(23, 59, 59), hubconf.tz())
        return git("log", "-1", "--format=%H", f"--before={end.isoformat()}", tip, "--", "STATE.md",
                   bare=bare).strip() or None
    sha = git("rev-parse", "--verify", "-q", f"{ref}^{{commit}}", bare=bare, check=False).strip()
    return sha if sha and ancestor(bare, sha, tip) else None


def history(project: str, limit: int) -> int:
    copy = copy_of(project)
    if not copy:
        return no_copy(project)
    bare = bare_of(copy)
    if not bare:
        return refuse(_("{project}: cannot read the memory repo", project=project))
    rows = git("log", f"-{limit}", "--format=@%H%x09%cI%x09%s", "--numstat", BRANCH, "--", "STATE.md", bare=bare)
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


@dataclasses.dataclass
class Plan:
    sha: str        # the version brought back
    when: str
    base: str       # today's commit, the new one's parent
    blob: str       # today's STATE.md blob (--from)
    current: str
    rebuilt: Rebuilt
    diff: str


def plan(project: str, bare: pathlib.Path, ref: str) -> Plan:
    base = git("rev-parse", BRANCH, bare=bare).strip()
    sha = resolve(bare, ref, base)
    if not sha:
        raise Refused(_("{project}: no STATE.md version at {ref}", project=project, ref=ref))
    try:
        target = git("show", f"{sha}:STATE.md", bare=bare)
    except RuntimeError:
        raise Refused(_("{project}: the commit {sha} has no STATE.md", project=project, sha=sha[:7]))
    when = git("log", "-1", "--format=%cI", sha, bare=bare).strip()[:10]
    blob = git("rev-parse", "--verify", "-q", f"{base}:STATE.md", bare=bare, check=False).strip()
    current = git("show", f"{base}:STATE.md", bare=bare) if blob else ""
    led = ledger(project)
    tag = hubconf.owner_tag()
    rebuilt = rebuild(target, current, Book.of(led), acts_md.profiles(tag),
                      acts_md.profile(hubconf.lang_content(), tag))
    if rebuilt.text is None:
        raise Refused(_("{project}: {why}; nothing changed", project=project, why=rebuilt.refused))
    diff = "".join(difflib.unified_diff(current.splitlines(True), rebuilt.text.splitlines(True), "STATE.md (now)",
                                        f"STATE.md ({when}, {sha[:7]})"))
    return Plan(sha, when, base, blob, current, rebuilt, diff)


def ledger(project: str):
    import registrar
    try:
        return registrar.Ledger(project)
    except SystemExit as e:   # the bus can't be read
        raise Refused(_("{project}: cannot read the to-do records on the bus: {error}", project=project, error=e))


def show(project: str, p: Plan) -> None:
    print(p.diff, end="")
    for k in p.rebuilt.readded:
        print(_("brought back, still open: {key}", key=k))
    for why, k, text in p.rebuilt.left:
        print({"closed": _("not brought back, closed since: {key} {text}", key=k, text=text),
               "project": _("not brought back, raised by the project itself (it re-adds it): {key} {text}", key=k,
                            text=text),
               "unknown": _("not brought back, a key the bus doesn't know: {key} {text}", key=k, text=text),
               "open": _("still open on the bus but in neither list (add it again by hand if still wanted): {key} "
                         "{text}", key=k, text=text),
               "nokey": _("not brought back, an owner item without a key (add it again by hand if still wanted): "
                          "{text}", text=text)}[why])


# --- writing ---

def stop(step: str) -> None:
    """The tests stop a run after a step, as a crash would."""
    if names.env("RESTORE_CRASH_AFTER") == step:
        os._exit(3)


def load(project: str) -> dict | None:
    try:
        return json.loads((run_dir(project) / "record.json").read_text())
    except (OSError, ValueError):
        return None


def save(project: str, rec: dict) -> None:
    f = run_dir(project) / "record.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1))
    tmp.replace(f)


def pending(project: str) -> bool:
    """A restore stopped before its checkpoint step: the librarian leaves the project alone until it is finished."""
    rec = load(project)
    if not rec or "checkpoint" not in rec.get("todo", []):
        return False
    copy = copy_of(project)
    bare = bare_of(copy) if copy else None
    tip = git("rev-parse", BRANCH, bare=bare, check=False).strip() if bare else ""
    return not tip or tip == rec["new"] or ancestor(bare, rec["new"], tip)   # unpublished: the next run drops it


def build(project: str, bare: pathlib.Path, p: Plan) -> str:
    """the new commit, built in the bare repo with a temporary index: no worktree touched"""
    d = run_dir(project)
    env = {**os.environ, "GIT_INDEX_FILE": str(d / "index")}
    git("read-tree", p.base, bare=bare, env=env)
    blob = git("hash-object", "-w", "--stdin", bare=bare, input=p.rebuilt.text).strip()
    mode = (git("ls-tree", p.base, "--", "STATE.md", bare=bare).split() or ["100644"])[0]
    mode = mode if mode in ("100644", "100755") else "100644"   # a plain file, whatever was there
    git("update-index", "--add", "--cacheinfo", f"{mode},{blob},STATE.md", bare=bare, env=env)
    tree = git("write-tree", bare=bare, env=env).strip()
    (d / "index").unlink(missing_ok=True)
    return git("commit-tree", tree, "-p", p.base, "-m", f"{RESTORE_SUBJECT} to {p.sha[:7]} ({p.when})",
               bare=bare).strip()


def check_copy(project: str, copy: pathlib.Path, bare: pathlib.Path, tip: str) -> None:
    """Step 2: a copy whose next fast-forward could fail (changes, a merge left open, commits the bare repo hasn't)
    would let the registrar merge its stale file over the restore."""
    if git("status", "--porcelain", "--untracked-files=no", cwd=copy).strip():
        raise Refused(_("{project}: the memory copy has uncommitted changes (a stopped nightly run?); nothing changed",
                        project=project))
    g = copy / ".git"
    if any((g / f).exists() for f in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply",
                                       "index.lock")) \
            or git("symbolic-ref", "-q", "HEAD", cwd=copy, check=False).strip() != BRANCH:
        raise Refused(_("{project}: the memory copy is in the middle of a git operation, or not on its branch (a "
                        "stopped nightly run?); nothing changed", project=project))
    head = git("rev-parse", "HEAD", cwd=copy).strip()
    coming = set(git("diff", "--name-only", head, tip, bare=bare, check=False).split("\n")) | {"STATE.md"}
    if coming & set(git("ls-files", "--others", "--exclude-standard", cwd=copy).split("\n")) - {""}:
        raise Refused(_("{project}: the memory copy has untracked files the fast-forward would overwrite; nothing "
                        "changed", project=project))
    if not ancestor(bare, head, tip):
        raise Refused(_("{project}: the memory copy has commits the memory repo doesn't (it diverged); nothing changed",
                        project=project))


def apply_(project: str, copy: pathlib.Path, bare: pathlib.Path, ref: str, frm: str | None,
           keep_checkpoint: bool) -> int:
    check_copy(project, copy, bare, git("rev-parse", BRANCH, bare=bare).strip())
    p = plan(project, bare, ref)
    if frm and frm != p.blob:
        raise Refused(_("{project}: STATE.md changed since the preview; preview again", project=project))
    if not p.diff:
        print(_("{project}: STATE.md is already as it was at {sha} ({date})", project=project, sha=p.sha[:7],
                date=p.when))
        return 0
    show(project, p)
    run_dir(project).mkdir(parents=True, exist_ok=True)
    new = build(project, bare, p)
    label = todo_tokens.tokens(hubconf.lang_ui())["reason_label"]
    today = datetime.datetime.now(hubconf.tz()).date().isoformat()
    entry = (f"## {today} · {project}\n\n**commit**: `{new}`\n\n{label}: "
             + _("restored to {sha} ({date}) with brunna state restore", sha=p.sha[:7], date=p.when)
             + f"\n\n```diff\n{p.diff}\n```\n\n---\n\n")
    rec = {"restored": p.sha, "date": p.when, "base": p.base, "new": new, "todo": list(STEPS),
           "keep_checkpoint": keep_checkpoint, "log": entry}
    save(project, rec)
    stop("record")
    if subprocess.run(["git", "--git-dir", str(bare), "update-ref", BRANCH, new, p.base],
                      capture_output=True).returncode != 0:
        shutil.rmtree(run_dir(project), ignore_errors=True)
        raise Refused(_("{project}: STATE.md changed meanwhile; preview again", project=project))
    stop("publish")
    return finish_steps(project, copy, bare, rec)


def finish_steps(project: str, copy: pathlib.Path, bare: pathlib.Path, rec: dict) -> int:
    """Steps 6-10, each recorded as done before the next."""
    def done(step):
        stop(step)   # a crash here, the step done and not recorded: the next run does it again, harmlessly
        rec["todo"].remove(step)
        save(project, rec)
    if "pull" in rec["todo"]:
        hublib.harden_copy(copy)
        r = subprocess.run(["git", *hublib.NO_SYMLINKS, "pull", "--ff-only", "-q"], cwd=copy, capture_output=True,
                           text=True)
        if r.returncode != 0:
            raise Refused(_("{project}: the memory copy could not be fast-forwarded ({error}); the restore is "
                            "published, run brunna state restore {project} to finish it", project=project,
                            error=r.stderr.strip()[-200:]))
        done("pull")
    moved = False
    if "checkpoint" in rec["todo"]:
        ck = checkpoint(project).read_text().strip() if checkpoint(project).exists() else ""
        # back to that day, never forward: a checkpoint behind it means notes the librarian hasn't read yet
        moved = (not rec["keep_checkpoint"] and bool(ck) and ck != rec["restored"]
                 and ancestor(bare, rec["restored"], ck))
        if moved:
            checkpoint(project).write_text(rec["restored"] + "\n")
        done("checkpoint")
    if "log" in rec["todo"]:
        log = hubconf.data_file("review-log.md")
        log.parent.mkdir(parents=True, exist_ok=True)
        if f"`{rec['new']}`" not in (log.read_text() if log.exists() else ""):
            fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                os.write(fd, rec["log"].encode())
            finally:
                os.close(fd)
        done("log")
    if "notes" in rec["todo"]:
        if not notes(project, git("show", f"{rec['new']}:STATE.md", bare=bare),
                     git("show", f"{rec['base']}:STATE.md", bare=bare, check=False)):
            raise Refused(_("{project}: STATE.md is restored, but a note to {assistant} could not be sent; run brunna "
                            "state restore {project} to send it", project=project,
                            assistant=hubconf.assistant_name()))
        done("notes")
    shutil.rmtree(run_dir(project), ignore_errors=True)
    print(_("{project}: STATE.md is back to {sha} ({date}), commit {new}", project=project, sha=rec["restored"][:7],
            date=rec["date"], new=rec["new"][:7]))
    if moved:
        print(_("The librarian folds the notes written since {date} into it on its next run (--keep-checkpoint "
                "skips that, when those notes are what it misread).", date=rec["date"]))
    return 0


def notes(project: str, text: str, before: str) -> bool:
    """An item restore brought back (in the restored file's first to-do list, not in the file it replaced) that the
    registrar had claimed done: one note per claim, decided from the ledger and the two files (so a rerun sends what
    a stopped run didn't, and nothing twice)."""
    import registrar
    led = ledger(project)
    read = acts_md.profiles(hubconf.owner_tag())
    book, d, was = Book.of(led), acts_md.Doc(text, read), acts_md.Doc(before, read).mentioned()
    recs = []
    for k in sorted(book.open_bound):
        n = led.reopens(k)
        if k in was or not d.holding(k) or ("registrar", "claimed", f"c:{k}:r{n}") not in led.sent:
            continue
        recs.append(registrar.record(
            led.filed(k), "note", k, f"x:{k}:r{n}", origin="registrar", about={"key": k},
            note=_c("This to-do is back in STATE.md's to-do list: brunna state restore brought back an earlier day's "
                    "file, after a nightly edit deleted its line, and the registrar's claim that it was done "
                    "(c:{key}:r{n}) came from that deletion. If it really is done, settle it as usual.", key=k, n=n)))
    res = registrar.publish(recs, led.sent)
    return all(res.get(r["id"]) in ("accepted", "duplicate") for r in recs)


def finish(project: str, copy: pathlib.Path, bare: pathlib.Path) -> int | None:
    """Step 1: a previous run's record. Published (the branch is at its commit or past it): its remaining steps.
    Not published: dropped. None when there is none."""
    rec = load(project)
    if rec is None:
        shutil.rmtree(run_dir(project), ignore_errors=True)
        return None
    tip = git("rev-parse", BRANCH, bare=bare).strip()
    if tip == rec["new"] or ancestor(bare, rec["new"], tip):
        print(_("{project}: finishing the restore that was stopped", project=project))
        return finish_steps(project, copy, bare, rec)
    shutil.rmtree(run_dir(project), ignore_errors=True)
    print(_("{project}: a restore stopped before it was published; it was dropped (preview again)", project=project))
    return None


def restore(project: str, ref: str | None, apply: bool, keep_checkpoint: bool, frm: str | None) -> int:
    copy = copy_of(project)
    if not copy:
        return no_copy(project)
    bare = bare_of(copy)
    if not bare:
        return refuse(_("{project}: cannot read the memory repo; nothing changed", project=project))
    try:
        if ref is not None and not apply:
            p = plan(project, bare, ref)
            if not p.diff:
                print(_("{project}: STATE.md is already as it was at {sha} ({date})", project=project, sha=p.sha[:7],
                        date=p.when))
                return 0
            show(project, p)
            print(_("Nothing changed. To bring this version back (as a new commit; history stays):"))
            print(f"  brunna state restore {project} {ref} --apply" + (f" --from {p.blob}" if p.blob else "")
                  + (" --keep-checkpoint" if keep_checkpoint else ""))
            return 0
        with memlock.hold(copy, "restore") as got:
            if not got:
                return refuse(memlock.busy_message(copy))
            r = finish(project, copy, bare)
            if ref is None:
                if r is None:
                    print(_("{project}: no stopped restore to finish", project=project))
                return r or 0
            return apply_(project, copy, bare, ref, frm, keep_checkpoint)
    except Refused as e:
        return refuse(str(e))
    except RuntimeError as e:   # git itself failed
        return refuse(_("{project}: git failed ({error}); run brunna state restore {project} to finish it", project=project,
                        error=str(e)[-300:]) if load(project) else
                      _("{project}: git failed ({error}); nothing changed", project=project, error=str(e)[-300:]))


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
    r.add_argument("ref", metavar="date|commit", nargs="?",
                   help=_("YYYY-MM-DD (the end of that day, your time zone) or a commit from history; without it, "
                          "finish a restore that was stopped"))
    r.add_argument("--apply", action="store_true", help=_("write it back as a new commit; without it nothing changes"))
    r.add_argument("--from", dest="frm", metavar="blob",
                   help=_("the version of today's STATE.md the preview was computed from (the preview prints it)"))
    r.add_argument("--keep-checkpoint", action="store_true",
                   help=_("don't have the librarian read the notes since that day again (when those notes are what it "
                          "misread)"))
    a = ap.parse_args(argv)
    if a.command == "history":
        return history(a.project, a.limit)
    try:
        return restore(a.project, a.ref, a.apply, a.keep_checkpoint, a.frm)
    except ValueError:
        return refuse(_("{ref} is not a date (YYYY-MM-DD) that exists", ref=a.ref))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
