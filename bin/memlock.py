#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The writer lock of a memory copy: one writer at a time in `memory_copies/<project>` (docs/design/state-restore.md,
"The lock").

    with memlock.hold(copy, "librarian", timeout=memlock.night()) as got:
        if not got: ...   # busy (memlock.holder(copy) says who and since when), or no such copy

    memlock.py hold <copy> --who <name> [--timeout s] -- <command…>
        for the shell scripts (macOS has no flock(1)): runs the command while holding the lock, exits with its code;
        exit 75 (EX_TEMPFAIL) when busy, saying who holds it

Who takes it: the librarian (its whole run of a project), the registrar (reading STATE.md to its push and `hubbus
done`), consolidate (the fold's base to its checkpoint check), `brunna state restore --apply`, rename and
`uninstall --purge` (around moving or removing a copy). Always before `with-lock.sh`'s op lock, never after; no hook and
no `with-lock.sh` user takes it (a librarian run's agent fires the project's hooks inside the copy); not re-entrant.

`<copy>/.git/brunna-writer.lock`, opened O_CREAT | O_RDWR | O_CLOEXEC without truncating (a detached child of the
holder doesn't inherit it), flocked, then checked to be still the file at that path (a copy renamed or re-cloned while
waiting is locked anew), then the holder's name and start time are written into it for messages. A flock dies with its
holder: a crash never leaves a copy locked. Standard library only (the platform's i18n for the CLI's message).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import os
import pathlib
import subprocess
import sys
import time

NAME = "brunna-writer.lock"
BUSY = 75
NIGHT = 60.0   # the nightly writers wait this long, then leave the project for the next night


def night() -> float:
    """How long a nightly writer waits (seconds); the tests shorten it."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import names
    try:
        return float(names.env("MEMLOCK_WAIT") or NIGHT)
    except ValueError:
        return NIGHT


def path(copy) -> pathlib.Path:
    return pathlib.Path(copy) / ".git" / NAME


def flock(fd: int, timeout: float) -> bool:
    """An exclusive flock on fd, waiting up to timeout seconds (never forever)."""
    deadline = None   # the clock is read only when there is something to wait for
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            deadline = deadline if deadline is not None else time.monotonic() + timeout
            if timeout <= 0 or time.monotonic() >= deadline:
                return False
            time.sleep(0.2)


_HELD: set[str] = set()   # paths this process holds: a second hold fails instead of deadlocking on itself


@contextlib.contextmanager
def hold(copy, who: str, timeout: float = 0):
    """Yields True while holding the copy's writer lock, False when it is busy after timeout seconds or there is no
    such copy."""
    f = path(copy)
    if str(f) in _HELD:
        yield False
        return
    while True:
        try:
            fd = os.open(f, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o644)
        except FileNotFoundError:   # the copy is gone (or was never there)
            yield False
            return
        if not flock(fd, timeout):
            os.close(fd)
            yield False
            return
        try:
            same = os.fstat(fd).st_ino == os.stat(f).st_ino
        except FileNotFoundError:
            same = False
        if same:
            break
        os.close(fd)   # moved or re-cloned while waiting: the lock of the copy that is there now
    _HELD.add(str(f))
    try:
        os.ftruncate(fd, 0)
        now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        os.pwrite(fd, f"{who}\t{now}\t{os.getpid()}\n".encode(), 0)
        yield True
    finally:
        _HELD.discard(str(f))
        os.close(fd)


def holder(copy) -> tuple[str, str]:
    """(who, since) as the last holder wrote it; meaningful while the lock is busy."""
    try:
        who, since, *_rest = path(copy).read_text().split("\t") + ["", ""]
        return who.strip(), since.strip()
    except OSError:
        return "", ""


def busy_message(copy) -> str:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import i18n
    _ = i18n.domain(__file__)
    who, since = holder(copy)
    return _("{copy} is being written by {who} (since {since}); try again when it is done", copy=copy,
             who=who or "?", since=since or "?")


def main(argv: list[str]) -> int:
    cut = argv.index("--") if "--" in argv else len(argv)
    ap = argparse.ArgumentParser(prog="memlock.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    h = sub.add_parser("hold", usage="memlock.py hold <copy> --who <name> [--timeout s] -- <command…>")
    h.add_argument("copy")
    h.add_argument("--who", required=True)
    h.add_argument("--timeout", type=float, default=0)
    a, cmd = ap.parse_args(argv[:cut]), argv[cut + 1:]
    if not cmd:
        ap.error("hold needs a command after --")
    if not path(a.copy).parent.is_dir():
        print(f"memlock.py: {a.copy}: not a memory copy (no .git)", file=sys.stderr)
        return 1
    with hold(a.copy, a.who, a.timeout) as got:
        if not got:
            print(busy_message(a.copy), file=sys.stderr)
            return BUSY
        return subprocess.run(cmd).returncode

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
