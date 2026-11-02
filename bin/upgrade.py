#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""`brunna upgrade`: move the hub checkout to a release (or the remote's main) and bring this machine in step with it.

    brunna upgrade [--to <tag> | --main] [--check] [--yes]

Which checkouts it moves. The way HEAD sits is the channel the checkout follows; nothing else is recorded:
  - detached HEAD (a clone checked out at a release tag): follows releases. Default target: the newest release tag
    (vX.Y.Z by version order; pre-releases such as v1.0.0-rc.1 only with --to)
  - on the branch main: follows main. Default target: the remote's main, fast-forwarded (= --main). Going to a tag from
    here would move the checkout back, so it is never the default; `--to <tag>` switches the checkout to releases
  - anything else is a development checkout and is refused, never moved: another branch, a linked worktree, tracked
    files with uncommitted changes, or commits that are on neither the remote's branches nor a release tag (checked
    after the fetch). The author's checkout develops on main and is ahead of its remote, so it is refused
  The remote: main's upstream (branch.main.remote), else origin, else the only remote.

What it does: refuse an unsafe tree → `git fetch --tags` → show the CHANGELOG.md sections between this checkout's
release and the target (breaking entries first; for main also the conventional titles not released yet) → ask (--yes
skips; with no terminal and no --yes it stops, exit 4) → move the checkout (a detached checkout of the tag; for main a
fast-forward) while holding the scheduler's lock, so no tick starts halfway (a tick running right now: refused) →
run the target's bin/setup-python → hand over to the target's own step list (below).

The in-step commands, each from the target's files, in this order; the first that fails stops the rest and says how to
go back (`brunna upgrade --to <previous>`, which runs the same steps on the old code):
  registry.py check · brunna config apply · brunna init --scheduler-only · brunna land ·
  brunna install-cli --dir <where the wrapper is> (only when the brunna on PATH is install-cli's wrapper) ·
  brunna doctor (the report: its problems are shown, they do not fail the upgrade)
When they all pass, .state/upgrade.json records the commit they ran for (brunna init writes it too, for a fresh
install). The checkout already at the target with no such record for it (a manual `git pull` or checkout) gets
setup-python and the steps without a move; going back then means to the commit that record last named.

Hand-over between versions (a contract from v0.2.0 on: keep it): after moving the checkout and running the target's
bin/setup-python, this process execs the target's `bin/upgrade.py --steps --from <ref>`, where <ref> is what to go back
to: a release tag, a commit, or `main`. The target runs its own step list. A target without bin/upgrade.py (v0.1.0)
gets this version's list, run from the target's files, without the scheduler step (its init has no --scheduler-only).

Exit codes: 0 done, or nothing to do · 10 (--check) `brunna upgrade` would move the checkout · 1 the fetch, the move
or a step failed · 2 usage · 3 refused: not a checkout to move (says why and what to do) · 4 not confirmed.

doctor: doctor_lines() says which release this is and whether a newer one is known, from .state/upgrade-check.json
(written by every fetch here, and by doctor itself). Doctor touches the network only when that record is older than a
day: one `git ls-remote --tags` with a 5-second limit, no prompts; a failure is recorded too, so an offline machine
waits once a day, not every run.
"""
from __future__ import annotations

import datetime
import fcntl
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys

BIN = pathlib.Path(__file__).resolve().parent
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import names  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)

ROOT = hubconf.ROOT
AVAILABLE, FAILED, USAGE, REFUSED, UNCONFIRMED = 10, 1, 2, 3, 4
CHECK_EXPIRY = datetime.timedelta(hours=24)
LS_REMOTE_TIMEOUT = 5
RELEASE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")
SECTION = re.compile(r"^## (v\d+\.\d+\.\d+\S*)")
SHOWN_TYPES = re.compile(r"^(feat|fix|perf|revert)(\([^)]*\))?!?: ")   # what cliff.toml lists
BREAKING_TITLE = re.compile(r"^[a-z]+(\([^)]*\))?!: ")
WRAPPER_MARKS = tuple(f"written by {n} install-cli" for n in names.all("cli"))   # either name wrote it
# install-cli's exec line: through bin/py to bin/agenthub (until v0.7), or bin/brunna directly
WRAPPER_EXEC = re.compile(r'^exec (?:"[^"\n]*/bin/py" "[^"\n]*/bin/(?:agenthub|brunna)"|"[^"\n]*/bin/brunna") "\$@"$', re.M)

USAGE_TEXT = """usage: brunna upgrade [--to <tag> | --main] [--check] [--yes]
  (no option)   the newest release; a checkout on main follows main instead
  --to <tag>    that release (going back, too); a commit id also works
  --main        the remote's main, for following development
  --check       only say what would change: exit 0 up to date, 10 an upgrade is there, 3 the checkout is not one to move
  --yes         do not ask before moving the checkout
"""


# ---------- git ----------

def git(*args, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL)


def out(*args) -> str | None:
    r = git(*args)
    return r.stdout.strip() if r.returncode == 0 else None


def is_checkout() -> bool:
    """The hub root is the top of a git checkout (a tarball or a copy is not)."""
    top = out("rev-parse", "--show-toplevel")
    return bool(top) and pathlib.Path(top).resolve() == ROOT.resolve()


def describe() -> str:
    """The release this checkout is at (= agenthub --version): `git describe` against the v* tags; an untagged commit
    reads <tag>-<n>-g<sha>, a checkout with no release yet its short sha, a tree outside git `unknown`."""
    return out("describe", "--tags", "--match", "v[0-9]*", "--always", "--dirty") or "unknown"


def nearest_release(rev: str = "HEAD") -> str | None:
    return out("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", rev)


def version_key(tag: str | None) -> tuple | None:
    m = RELEASE.match(tag or "")
    if not m:
        return None
    return int(m[1]), int(m[2]), int(m[3]), 0 if m[4] else 1, m[4] or ""


def newest(tags) -> str | None:
    """The newest release by version order; pre-releases are never the default."""
    final = [t for t in tags if version_key(t) and not RELEASE.match(t)[4]]
    return max(final, key=version_key, default=None)


def remote_name() -> str | None:
    remotes = (out("remote") or "").split()
    up = out("config", "--get", "branch.main.remote")
    if up in remotes:
        return up
    if "origin" in remotes:
        return "origin"
    return remotes[0] if len(remotes) == 1 else None


def sha(rev: str) -> str | None:
    return out("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")


def branch() -> str | None:
    return out("symbolic-ref", "--quiet", "--short", "HEAD")


def is_ancestor(a: str, b: str) -> bool:
    return git("merge-base", "--is-ancestor", a, b).returncode == 0


def linked_worktree() -> bool:
    gd, cd = out("rev-parse", "--absolute-git-dir"), out("rev-parse", "--path-format=absolute", "--git-common-dir")
    return bool(gd and cd) and pathlib.Path(gd).resolve() != pathlib.Path(cd).resolve()


def local_commits(rev: str, remote: str) -> int:
    """Commits of rev that are on none of the remote's branches and no release tag."""
    n = out("rev-list", "--count", rev, "--not", f"--remotes={remote}", "--tags=v[0-9]*")
    return int(n) if n and n.isdigit() else 0


# ---------- records (.state) ----------

def check_file() -> pathlib.Path:
    return hubconf.STATE / "upgrade-check.json"


def steps_file() -> pathlib.Path:
    return hubconf.STATE / "upgrade.json"


def read_json(f: pathlib.Path) -> dict | None:
    try:
        d = json.loads(f.read_text())
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def write_json(f: pathlib.Path, d: dict) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n")
    tmp.replace(f)


def now() -> datetime.datetime:
    return datetime.datetime.now().astimezone()


def record_check(remote: str, newest_tag: str | None, error: str | None) -> dict:
    d = {"checked": now().isoformat(timespec="seconds"), "remote": remote, "newest": newest_tag, "error": error}
    write_json(check_file(), d)
    return d


def record_in_step() -> None:
    """The in-step commands have run for this commit (agenthub upgrade after its steps; agenthub init after a fresh
    setup). A development checkout is not recorded: it moves by merges, and doctor would nag after every one."""
    if not is_checkout() or local_mode()[0] == "dev":
        return
    write_json(steps_file(), {"commit": sha("HEAD"), "version": describe(), "ref": here_ref(),
                              "at": now().isoformat(timespec="seconds")})


def local_mode() -> tuple[str, str]:
    """('release' | 'main' | 'dev', why it is dev) from what is on disk, without the network: the remote's refs are as
    the last fetch left them."""
    if linked_worktree():
        return "dev", _("{root} is a linked worktree of another checkout", root=ROOT)
    b = branch()
    if b and b != "main":
        return "dev", _("HEAD is on the branch {branch}: a development checkout", branch=b)
    remote = remote_name()
    if remote is None:
        return "dev", _("the checkout has no remote to upgrade from")
    n = local_commits("HEAD", remote)
    if n:
        return "dev", _("{n} {n:commit|commits} here {n:is|are} on no branch of {remote} and no release tag: a development "
                        "checkout", n=n, remote=remote)
    return ("main" if b == "main" else "release"), ""


# ---------- what changes ----------

def changelog(rev: str) -> str:
    return out("show", f"{rev}:CHANGELOG.md") or ""


def sections(text: str) -> list[tuple[str, str]]:
    """(tag, the section's text) of each `## vX.Y.Z …` section, in file order."""
    found: list[tuple[str, list[str]]] = []
    cur = None
    for line in text.splitlines():
        m = SECTION.match(line)
        if m:
            cur = (m[1], [line])
            found.append(cur)
        elif line.startswith("## "):
            cur = None
        elif cur:
            cur[1].append(line)
    return [(tag, "\n".join(lines).rstrip()) for tag, lines in found]


def between(text: str, low: str | None, high: str | None) -> list[tuple[str, str]]:
    """The sections after release `low` up to and including `high` (None: no bound)."""
    lo, hi = version_key(low), version_key(high)
    return [(t, s) for t, s in sections(text) if version_key(t)
            and (lo is None or version_key(t) > lo) and (hi is None or version_key(t) <= hi)]


def bullets(section: str) -> list[str]:
    """The `- …` entries of a section, continuation lines joined."""
    items: list[str] = []
    open_ = False
    for line in section.splitlines():
        if line.startswith("- "):
            items.append(line[2:].strip())
            open_ = True
        elif open_ and line.strip() and not line.startswith("#"):
            items[-1] += " " + line.strip()
        else:
            open_ = False
    return items


def unreleased(tip: str, *exclude: str) -> list[tuple[str, bool]]:
    """(title, breaking) of the commits in tip but not in exclude that a changelog would list."""
    r = git("log", "--format=%s%x1f%b%x1e", tip, "--not", *exclude)
    titles = []
    for rec in (r.stdout.split("\x1e") if r.returncode == 0 else []):
        title, _sep, body = rec.strip().partition("\x1f")
        breaking = bool(BREAKING_TITLE.match(title)) or "BREAKING CHANGE:" in body
        if title and (SHOWN_TYPES.match(title) or breaking):
            titles.append((title, breaking))
    return titles


def changes(target: str) -> tuple[bool, list[tuple[str, str]], list[tuple[str, bool]]]:
    """(forward, changelog sections, unreleased titles) between HEAD and target: forward lists what arrives (from the
    target's CHANGELOG.md), back what is undone (from this checkout's)."""
    cur_rel, tgt_rel = nearest_release("HEAD"), nearest_release(target)
    forward = is_ancestor("HEAD", target) or (not is_ancestor(target, "HEAD")
                                               and (version_key(tgt_rel) or ()) >= (version_key(cur_rel) or ()))
    if forward:
        secs = between(changelog(target), cur_rel, tgt_rel)
        titles = unreleased(target, "HEAD", *([tgt_rel] if tgt_rel else []))
    else:
        secs = between(changelog("HEAD"), tgt_rel, cur_rel)
        titles = unreleased("HEAD", target, *([cur_rel] if cur_rel else []))
    return forward, secs, titles


def show_changes(current: str, label: str, target: str) -> None:
    forward, secs, titles = changes(target)
    print(_("brunna {current} → {target}", current=current, target=label))
    breaking = [(t, b) for t, s in secs for b in bullets(s) if "**breaking:**" in b] + \
               [(_("not released yet"), t) for t, br in titles if br]
    if breaking:
        print("\n" + (_("⚠️ Breaking changes (read these first):") if forward else
                      _("⚠️ Breaking changes this undoes (read these first):")))
        for where, text in breaking:
            print(f"  {where}: {text}")
    if not forward:
        print("\n" + _("Going back undoes these changes. Data a newer version wrote in a newer contract version may not "
                       "be readable by the older code (CHANGELOG.md marks such changes as breaking)."))
    for _tag, text in secs:
        print("\n" + text)
    if titles:
        print("\n" + (_("Not released yet (on {target}):", target=label) if forward else
                      _("Not released yet, undone too:")))
        for t, br in titles:
            print(f"- {t}" + ("  ⚠️" if br else ""))
    if not secs and not titles:
        print("\n" + _("(no changelog entries between these two)"))


# ---------- the steps ----------

def py_step(*argv) -> list[str]:
    return [sys.executable, *map(str, argv)]


def cli_wrapper() -> pathlib.Path | None:
    """The CLI on PATH, under either name (docs/design/rename-brunna.md), when it is install-cli's wrapper (never
    someone else's program of that name)."""
    for name in names.all("cli"):
        w = shutil.which(name)
        if not w:
            continue
        try:
            with open(w, "rb") as f:
                head = f.read(1024).decode(errors="ignore")
        except OSError:
            continue
        # the mark, or the wrapper's exec line itself: a wrapper written before the mark was in English (or in another
        # interface language) has only the line
        if any(m in head for m in WRAPPER_MARKS) or WRAPPER_EXEC.search(head):
            return pathlib.Path(w)
    return None


def step_list(with_scheduler: bool) -> list[tuple[str, list[str]]]:
    b = ROOT / "bin"
    steps = [("registry", py_step(b / "registry.py", "check")),
             ("config apply", py_step(b / "agenthub", "config", "apply"))]
    if with_scheduler:
        steps.append(("scheduler", py_step(b / "agenthub", "init", "--scheduler-only")))
    steps.append(("land", py_step(b / "agenthub", "land")))
    w = cli_wrapper()
    if w:
        steps.append(("install-cli", py_step(b / "agenthub", "install-cli", "--dir", w.parent)))
    return steps


# the oldest release this one can go back to: everything older can't read what the rename to brunna writes (job state
# under brunna/…, brunna.json, the new labels, letters to brunna; docs/design/rename-brunna.md). v0.6.0 reads the
# spellings but not letters addressed to the new name; v0.6.1 does
OLDEST_BACK = "v0.6.1"


def too_old(ref: str) -> bool:
    """A release before OLDEST_BACK, or a commit that comes before it (in a repo that has OLDEST_BACK)."""
    if not sha(OLDEST_BACK):
        return False
    if version_key(ref):
        return version_key(ref) < version_key(OLDEST_BACK)
    c = sha(ref)
    return bool(c) and c != sha(OLDEST_BACK) and git("merge-base", "--is-ancestor", c, OLDEST_BACK).returncode == 0


def go_back(ref: str) -> str:
    """How to go back to ref; one older than OLDEST_BACK (an upgrade that jumped from there) goes back to that."""
    if ref != "main" and too_old(ref):
        ref = OLDEST_BACK
    cli = names.current("cli")   # a new install has only this one
    return f"{cli} upgrade --main" if ref == "main" else f"{cli} upgrade --to {ref}"


def here_ref() -> str:
    """What `--from` / going back names for where HEAD is: main, a release tag, or the commit."""
    if branch() == "main":
        return "main"
    return out("describe", "--tags", "--exact-match", "--match", "v[0-9]*", "HEAD") or sha("HEAD") or "HEAD"


def failed(name: str, rc: int, back: str | None, manual: bool = False) -> int:
    print("\n" + _("✗ step {name} failed (exit {rc}); the steps after it did not run. The checkout is at {version} now.",
                   name=name, rc=rc, version=describe()), file=sys.stderr)
    if back is None:   # nothing moved, and this machine has no record of an earlier version in step
        print(_("  Fix what it says, then run brunna upgrade again (it reruns the steps)"), file=sys.stderr)
    elif manual:   # the target has no upgrade command: back by hand first
        how = f"git -C {ROOT} checkout main" if back == "main" else f"git -C {ROOT} checkout --detach {back}"
        print(_("  Fix what it says (this release has no brunna upgrade to rerun the steps), or go back: {checkout}, "
                "then {upgrade}", checkout=how, upgrade=go_back(back)), file=sys.stderr)
    else:
        print(_("  Fix what it says, then run brunna upgrade again (it reruns the steps), or go back: {how}",
                how=go_back(back)), file=sys.stderr)
    return FAILED


def run_steps(back: str | None, with_scheduler: bool = True, manual_back: bool = False) -> int:
    """The in-step commands from the files now on disk; the first failure stops the rest."""
    for name, argv in step_list(with_scheduler):
        print(f"\n→ {name}", flush=True)
        rc = subprocess.run(argv, cwd=ROOT).returncode
        if rc != 0:
            return failed(name, rc, back, manual_back)
    record_in_step()
    print("\n→ doctor", flush=True)
    rc = subprocess.run(py_step(ROOT / "bin" / "agenthub", "doctor"), cwd=ROOT).returncode
    print("\n" + _("✓ brunna is at {version}", version=describe())
          + ("" if rc == 0 else _(" (doctor found problems, above; the upgrade itself is done)")))
    return 0


# ---------- the command ----------

def refuse(why: str, what: str) -> int:
    print(_("brunna upgrade: refused, nothing changed: {why}", why=why), file=sys.stderr)
    print("  " + what, file=sys.stderr)
    return REFUSED


def scheduler_lock():
    """Take the scheduler's lock without waiting (the tick holds it for its whole run): the fd, or None while a tick
    runs."""
    f = hubconf.STATE / "scheduler.lock"
    f.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(f, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None


def confirm(question: str, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        print(_("not confirmed: there is no terminal to ask in; run it again with --yes to go ahead (nothing changed)"),
              file=sys.stderr)
        return False
    try:
        return input(_("{question} [y/N] ", question=question)).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def parse(argv: list[str]) -> dict | None:
    a = {"to": None, "main": False, "check": False, "yes": False, "steps": False, "from": None}
    it = iter(argv)
    for x in it:
        if x in ("--to", "--from"):
            a[x[2:]] = next(it, None)
            if not a[x[2:]]:
                return None
        elif x in ("--main", "--check", "--yes", "--steps"):
            a[x[2:]] = True
        else:
            return None
    if a["to"] and a["main"] or a["steps"] and not a["from"]:
        return None
    return a


def main(argv: list[str]) -> int:
    if argv[:1] in (["-h"], ["--help"]):
        print(_(USAGE_TEXT), end="")
        return 0
    a = parse(argv)
    if a is None:
        print(_(USAGE_TEXT), end="", file=sys.stderr)
        return USAGE
    if a["steps"]:   # the hand-over from the version that moved the checkout (see the header)
        return run_steps(a["from"])
    if not is_checkout():
        print(_("brunna upgrade: {root} is not a git checkout, so there is nothing to upgrade from (install by git "
                "clone; see README.md)", root=ROOT), file=sys.stderr)
        return FAILED

    # 1. refuse an unsafe tree (what can be known before the fetch)
    if linked_worktree():
        return refuse(_("{root} is a linked worktree of another checkout", root=ROOT),
                      _("run it in the main checkout, or update this worktree with git yourself"))
    b = branch()
    if b and b != "main":
        return refuse(_("HEAD is on the branch {branch}: a development checkout", branch=b),
                      _("update it with git yourself; brunna upgrade moves only a checkout at a release or on main"))
    if out("status", "--porcelain", "--untracked-files=no"):
        return refuse(_("tracked files have uncommitted changes"),
                      _("commit them on a branch, or drop them (git -C {root} stash), then run it again", root=ROOT))
    remote = remote_name()
    if remote is None:
        return refuse(_("the checkout has no remote to upgrade from"),
                      _("add the repo you installed from: git -C {root} remote add origin <url>", root=ROOT))

    # 2. fetch
    print(_("fetching from {remote} …", remote=remote), flush=True)
    r = git("fetch", "--quiet", "--tags", remote, f"+refs/heads/main:refs/remotes/{remote}/main")
    if r.returncode != 0:
        print(_("brunna upgrade: the fetch from {remote} failed: {error}", remote=remote,
                error=r.stderr.strip()[-400:]), file=sys.stderr)
        return FAILED
    n = local_commits("HEAD", remote)
    if n:
        return refuse(_("{n} {n:commit|commits} here {n:is|are} on no branch of {remote} and no release tag: a development "
                        "checkout", n=n, remote=remote),
                      _("push them or move them to a branch; brunna upgrade never moves a checkout with work of its own"))
    tags = (out("tag", "--list", "v[0-9]*") or "").split()
    latest = newest(tags)
    record_check(remote, latest, None)

    # the target
    if a["to"]:
        if a["to"] in tags and version_key(a["to"]):
            kind, target, label = "tag", a["to"], a["to"]
        elif sha(a["to"]) and re.fullmatch(r"[0-9a-f]{7,40}", a["to"]):
            kind, target, label = "commit", sha(a["to"]), a["to"]
        else:
            print(_("brunna upgrade: {remote} has no release {tag} (releases: {tags})", remote=remote, tag=a["to"],
                    tags=", ".join(sorted((t for t in tags if version_key(t)), key=version_key)) or "-"),
                  file=sys.stderr)
            return USAGE
    elif a["main"] or b == "main":
        kind, target, label = "main", f"{remote}/main", f"{remote}/main"
        if not a["main"]:
            print(_("this checkout is on main, so it follows main (to follow releases instead: brunna upgrade --to "
                    "{tag})", tag=latest or "<tag>"))
    elif latest:
        kind, target, label = "tag", latest, latest
    else:
        print(_("brunna upgrade: {remote} has no release yet; brunna upgrade --main follows its main",
                remote=remote), file=sys.stderr)
        return FAILED
    if a["to"] and too_old(target):
        return refuse(_("{ref} is older than {oldest}, which can't read what this version has written (it was renamed "
                        "to brunna in v0.7)", ref=a["to"], oldest=OLDEST_BACK),
                      _("the oldest release to go back to is {oldest}: brunna upgrade --to {oldest}", oldest=OLDEST_BACK))
    if kind == "main" and b != "main" and git("rev-parse", "--verify", "--quiet", "refs/heads/main").returncode == 0 \
            and local_commits("main", remote):
        return refuse(_("the local branch main has commits that are not on {remote}", remote=remote),
                      _("push them, or move them to another branch, before following main"))

    head, tgt = sha("HEAD"), sha(target)
    same = head == tgt and (b == "main") == (kind == "main")
    rec = read_json(steps_file()) or {}
    pending = rec.get("commit") != head
    current = describe()
    back = here_ref()

    # 3. what changes
    if same:
        print(_("brunna {version} is up to date ({target})", version=current, target=label))
        if pending:
            print(_("the in-step commands have not run for this commit on this machine (a git pull or checkout by "
                    "hand?): brunna upgrade runs them"))
        if a["check"]:
            return 0
        if not pending:
            return 0
        if not confirm(_("run the in-step commands now?"), a["yes"]):
            return UNCONFIRMED
        # going back = to where the steps last passed, if this machine knows (not where HEAD is)
        return bring_in_step(rec.get("ref") if rec.get("commit") else None, moved=False)
    show_changes(current, label, target)
    if a["check"]:
        print("\n" + _("brunna upgrade{opt} installs it", opt=f" --to {a['to']}" if a["to"] else
                       " --main" if a["main"] else ""))
        return AVAILABLE
    if not confirm(_("move this checkout from {current} to {target}?", current=current, target=label), a["yes"]):
        return UNCONFIRMED

    # 4. move the checkout, holding the scheduler's lock so no tick starts halfway
    fd = scheduler_lock()
    if fd is None:
        return refuse(_("a scheduler tick is running right now"), _("run brunna upgrade again in a minute or two"))
    try:
        if kind == "main" and b == "main":
            r = git("merge", "--ff-only", "--quiet", target)
        elif kind == "main":
            r = git("checkout", "--quiet", "-B", "main", target)
            if r.returncode == 0:
                git("branch", "--quiet", f"--set-upstream-to={remote}/main", "main")
        else:
            r = git("checkout", "--quiet", "--detach", tgt)
    finally:
        os.close(fd)
    if r.returncode != 0:
        print(_("brunna upgrade: moving the checkout failed, it stays at {version}: {error}", version=current,
                error=r.stderr.strip()[-400:]), file=sys.stderr)
        return FAILED
    print("\n" + _("✓ the checkout is at {version}", version=describe()))

    # 5. bring everything in step, from the target's code
    return bring_in_step(back, moved=True)


def bring_in_step(back: str | None, moved: bool) -> int:
    """setup-python from the files on disk, then the step list: after a move the target's own (its upgrade.py, a new
    process), with nothing moved this process's (it is the target's code)."""
    mine = ROOT / "bin" / "upgrade.py"
    print("\n→ setup-python", flush=True)
    rc = subprocess.run([str(ROOT / "bin" / "setup-python")], cwd=ROOT).returncode
    if rc != 0:
        return failed("setup-python", rc, back, manual=not mine.exists())
    if not moved:
        return run_steps(back)
    if mine.exists():
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(sys.executable, [sys.executable, str(mine), "--steps", "--from", back])
    # a target older than this command (v0.1.0): this version's list, run from its files
    return run_steps(back, with_scheduler=False, manual_back=True)


# ---------- doctor ----------

def ls_remote_newest(remote: str) -> tuple[str | None, str | None]:
    """(newest release tag on the remote, error): one `git ls-remote`, no prompts (its own session, so ssh cannot ask
    on the terminal), killed after LS_REMOTE_TIMEOUT seconds."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if not os.environ.get("GIT_SSH_COMMAND") and not out("config", "--get", "core.sshCommand"):
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    p = subprocess.Popen(["git", "-C", str(ROOT), "ls-remote", "--tags", "--refs", remote], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True, env=env, start_new_session=True)
    try:
        o, e = p.communicate(timeout=LS_REMOTE_TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.communicate()
        return None, _("no answer from {remote} within {n} seconds", remote=remote, n=LS_REMOTE_TIMEOUT)
    if p.returncode != 0:
        return None, (e.strip().splitlines() or [f"git ls-remote: exit {p.returncode}"])[-1][:200]
    return newest(line.split("refs/tags/", 1)[1] for line in o.splitlines() if "refs/tags/" in line), None


def doctor_lines() -> list[tuple[str, str, str]]:
    """(ok|warn, what, how to fix) for agenthub doctor: the release, a newer one when known, and a checkout that moved
    without its in-step commands. Network only when the record is older than CHECK_EXPIRY (see the header)."""
    if not is_checkout():
        return []
    version, rel, remote = describe(), nearest_release(), remote_name()
    rec = read_json(check_file())
    try:
        age = now() - datetime.datetime.fromisoformat(rec["checked"]) if rec else None
    except (KeyError, TypeError, ValueError):
        age = None
    if remote and (age is None or age > CHECK_EXPIRY or age < datetime.timedelta(0) or rec.get("remote") != remote):
        rec = record_check(remote, *ls_remote_newest(remote))
    lines = []
    latest = (rec or {}).get("newest")
    when = (rec or {}).get("checked", "")[:16].replace("T", " ")
    if not remote:
        lines.append(("ok", _("brunna {version} (no remote: not checked for newer releases)", version=version), ""))
    elif latest and (version_key(rel) is None or version_key(latest) > version_key(rel)):
        lines.append(("warn", _("brunna {latest} is out; this checkout is at {version} (checked {when})",
                                latest=latest, version=version, when=when),
                      _("brunna upgrade --check shows what changes; brunna upgrade installs it")))
    elif latest:
        lines.append(("ok", _("brunna {version}; the newest release is {latest} (checked {when})", version=version,
                              latest=latest, when=when), ""))
    else:
        lines.append(("ok", _("brunna {version} (could not check for newer releases at {when}: {error}; doctor tries "
                              "again after a day)", version=version, when=when,
                              error=(rec or {}).get("error") or _("no release tags")), ""))
    done = read_json(steps_file())
    if done and done.get("commit") and done["commit"] != sha("HEAD") and local_mode()[0] != "dev":
        lines.append(("warn", _("the checkout moved to {version} after brunna upgrade last brought this machine in step "
                                "(at {old}): the Python packages, services, scheduler and hooks may be stale",
                                version=version, old=done.get("version") or done["commit"][:12]),
                      f"{names.current('cli')} upgrade"))
    return lines


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
