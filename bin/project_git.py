#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The platform's git work in project repos (docs/design/projects-and-repos.md section 4, "Platform git in project
repos"): the platform runs git only in repos it owns (the memory bare repos, the librarian's memory copies, its own
checkout); in a project's repo every git command runs as that project, through project-git-job.py started with
isolation.as_project (sandboxed when the project is). What comes back is data on its stdout (never a file in the
project's folder, which could be a symlink or a FIFO): JSON parsed here with types checked, or a bundle fetched into a
repo the platform owns with transfer.fsckObjects on. Every job has a timeout (its whole process group is killed: a
`[include] path=<fifo>` in the repo's config hangs any git) and an output cap. Nothing derived from the output is used
for a write outside the project's own job.

  job(project, cwd, op, *args, input, out, timeout, cap) → Result
                                                            one op of project-git-job.py as the project: returncode,
                                                            stdout (bytes; streamed into `out` when given), stderr
  data(project, cwd, op, *args) → object                    its JSON output; raises Unavailable (sandbox refused,
                                                            git failed, malformed output)
  fetch_bundle(bare, bundle, branch) → None | why not       fast-forward <bare>'s branch to the bundle's HEAD
  memory_project(dir) → (name, doc) | None                  whose .agent-memory this is
  project_at(dir) → (name, doc) | None                      whose workspace holds this folder (by path, no git)
  run(project, cwd, argv, …) → Result                       any argv as the project, bounded like job
  sync_memory(name, doc, memdir) → None | why not           a sandboxed project's memory sync (commit + merge + bundle
                                                            as the project; the platform fetches the bundle)

CLI (git-sync-memory.sh, sync-all-memory.sh):
  project_git.py sandboxed <dir>      exit 0 when <dir> is the .agent-memory of a sandboxed project, 3 when not (any
                                      other exit: it couldn't tell)
  project_git.py run-as <dir> -- <argv…>
                                      a shell hook's work as the project <dir> belongs to (HUB_AS_PROJECT=1 set, stdin
                                      and output passed through); exit 3 when it isn't sandboxed
  project_git.py sync-memory [--lock] <dir>
                                      sync_memory for that project (exit 1 with the reason on stderr); --lock: the job
                                      takes with-lock.sh's lock (a caller under with-lock.sh holds it already)
"""
from __future__ import annotations

import json
import os
import pathlib
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from typing import NamedTuple

BIN = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import isolation  # noqa: E402
import policy  # noqa: E402

JOB = BIN / "project-git-job.py"
MAX_JSON = 8 << 20          # facts
MAX_BUNDLE = 4 << 30        # a bundle of unpushed work or memory
TIMEOUT_S = 120
BUNDLE_TIMEOUT_S = 1800
TIMED_OUT, TOO_BIG = 124, 125
INCOMING = "refs/agent-hub/incoming"
NOT_SANDBOXED = 3
EX_SANDBOX = 78   # sandboxed-run refused (no sandbox on this machine): the work is not done, never done unsandboxed


class Unavailable(Exception):
    pass


class Result(NamedTuple):
    returncode: int
    stdout: bytes
    stderr: str


def job(project: str | None, cwd, op: str, *args, **kw) -> Result:
    """One op of project-git-job.py as the project (run's keywords)."""
    return run(project, cwd, [*hubconf.launch(JOB), op, *map(str, args)], **kw)


def run(project: str | None, cwd, argv: list[str], input: str | bytes | None = None, out=None, timeout: float = TIMEOUT_S,
        cap: int | None = None, env: dict | None = None) -> Result:
    """Run argv as the project. Over `timeout` seconds or `cap` bytes of stdout (default MAX_JSON), the whole process
    group is killed (returncode TIMED_OUT / TOO_BIG); stdout goes into the binary file `out` when given."""
    cap = MAX_JSON if cap is None else cap
    argv = isolation.as_project(project, cwd, argv)
    p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                         env={**os.environ, **env} if env else None)
    if input is not None:
        p.stdin.write(input if isinstance(input, bytes) else input.encode())
        p.stdin.close()
    sel = selectors.DefaultSelector()
    sel.register(p.stdout, selectors.EVENT_READ, "out")
    sel.register(p.stderr, selectors.EVENT_READ, "err")
    got, err, n, why = [], bytearray(), 0, None
    deadline = time.monotonic() + timeout
    while sel.get_map() and why is None:
        left = deadline - time.monotonic()
        if left <= 0:
            why = (TIMED_OUT, f"timed out after {timeout:g}s")
            break
        for key, _mask in sel.select(left):
            chunk = os.read(key.fileobj.fileno(), 1 << 16)
            if not chunk:
                sel.unregister(key.fileobj)
            elif key.data == "err":
                err += chunk[:max(0, 65536 - len(err))]
            else:
                n += len(chunk)
                if n > cap:
                    why = (TOO_BIG, f"output over {cap} bytes")
                    break
                if out is not None:
                    out.write(chunk)
                else:
                    got.append(chunk)
    if why is None:
        try:   # stdout closed is not the end: a process left behind may still run
            rc = p.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            why = (TIMED_OUT, f"timed out after {timeout:g}s")
    if why:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        rc = p.wait()
    for fo in (p.stdout, p.stderr):
        fo.close()
    sel.close()
    return Result(why[0] if why else rc, b"".join(got), why[1] if why else err.decode(errors="replace"))


def data(project: str | None, cwd, op: str, *args):
    r = job(project, cwd, op, *args)
    sys.stderr.write(f"DIAG data {op} rc={r.returncode} stdout={r.stdout[:500]!r}\nDIAG stderr: {r.stderr}\n")
    if r.returncode == EX_SANDBOX:
        raise Unavailable(f"{project}: the sandbox refused ({r.stderr.strip()[-200:]})")
    if r.returncode != 0:
        raise Unavailable(f"{project}: {op} failed ({r.stderr.strip()[-200:]})")
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise Unavailable(f"{project}: {op} output is not JSON") from None


def strs(x) -> list[str]:
    return [s for s in x if isinstance(s, str)] if isinstance(x, list) else []


def owned_git(repo, *args) -> subprocess.CompletedProcess:
    """git in a repo the platform owns."""
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def fetch_bundle(bare: pathlib.Path, bundle: pathlib.Path, branch: str) -> str | None:
    """Fast-forward <bare>'s refs/heads/<branch> to the bundle's HEAD. The bundle is untrusted data: objects are
    checked (fsck), and anything but a fast-forward is refused (the next round merges first)."""
    fsck = ["-c", "transfer.fsckObjects=true", "-c", "fetch.fsckObjects=true"]
    r = owned_git(bare, *fsck, "fetch", "-q", "--no-tags", "--no-write-fetch-head", str(bundle), f"+HEAD:{INCOMING}")
    if r.returncode != 0:
        return f"fetching the bundle failed: {r.stderr.strip()[-300:]}"
    try:
        new = owned_git(bare, "rev-parse", "--verify", "-q", INCOMING).stdout.strip()
        old = owned_git(bare, "rev-parse", "--verify", "-q", f"refs/heads/{branch}").stdout.strip()
        if old and owned_git(bare, "merge-base", "--is-ancestor", old, new).returncode != 0:
            return f"not a fast-forward of {branch} (the bare repo moved on; the next round merges it first)"
        r = owned_git(bare, "update-ref", f"refs/heads/{branch}", new, old)
        return None if r.returncode == 0 else f"update-ref failed: {r.stderr.strip()[-300:]}"
    finally:
        owned_git(bare, "update-ref", "-d", INCOMING)


def memory_project(d) -> tuple[str, dict] | None:
    real = pathlib.Path(d).resolve()
    for name, doc in hubconf.projects().items():
        ws = hubconf.path((doc.get("paths") or {}).get("workspace"))
        if ws and (ws / ".agent-memory").resolve() == real:
            return name, {**doc, "name": name}
    return None


def project_at(d) -> tuple[str, dict] | None:
    """The registered project whose workspace holds this folder (its worktrees under it included); by path only, no
    git (a repo's config can hang even rev-parse)."""
    real = pathlib.Path(d).resolve()
    best = None
    for name, doc in hubconf.projects().items():
        ws = hubconf.path((doc.get("paths") or {}).get("workspace"))
        if ws and real.is_relative_to(ws.resolve()) and (best is None or len(ws.parts) > len(best[2].parts)):
            best = (name, {**doc, "name": name}, ws)
    return best[:2] if best else None


def sync_memory(name: str, doc: dict, memdir, branch: str = "agent-memory", lock: bool = False) -> str | None:
    """lock: the job takes with-lock's lock itself (the hourly sync; a hook's call already holds it)."""
    bare = policy.memory_bare(name, doc)
    if not (bare / "objects").is_dir():
        return f"{name}: no local memory bare repo at {bare}"
    with tempfile.TemporaryDirectory(prefix="hub-memory-") as tmp:
        bundle = pathlib.Path(tmp) / "memory.bundle"
        with bundle.open("wb") as fh:
            r = job(name, memdir, "memory-sync", memdir, bare, branch, *(["--lock"] if lock else []), out=fh,
                    timeout=BUNDLE_TIMEOUT_S, cap=MAX_BUNDLE)
        err = r.stderr.strip()[-300:]
        if r.returncode == EX_SANDBOX:
            return f"{name}: the sandbox refused; memory not synced ({err})"
        if r.returncode != 0:
            return f"{name}: the memory sync job failed ({err})"
        if bundle.stat().st_size == 0:
            return None
        why = fetch_bundle(bare, bundle, branch)
        return f"{name}: {why}" if why else None


def run_as_cli(d: str, argv: list[str]) -> int:
    """A shell hook's work as the project of the folder d: stdin passed in, its stdout and stderr passed out. Exit 3
    when d isn't a sandboxed project's (the caller goes on itself)."""
    found = project_at(d)
    if not (found and isolation.sandboxed(found[1])):
        return NOT_SANDBOXED
    stdin = "" if sys.stdin.isatty() else sys.stdin.read()
    r = run(found[0], d, argv, input=stdin, env={"HUB_AS_PROJECT": "1"})
    sys.stdout.buffer.write(r.stdout)
    sys.stderr.write(r.stderr)
    return r.returncode


def main(argv: list[str]) -> int:
    if argv[:1] == ["run-as"] and len(argv) >= 4 and argv[2] == "--":
        return run_as_cli(argv[1], argv[3:])
    lock = "--lock" in argv
    argv = [a for a in argv if a != "--lock"]
    if len(argv) != 2 or argv[0] not in ("sandboxed", "sync-memory"):
        print("usage: project_git.py sandboxed <dir> | sync-memory [--lock] <dir>", file=sys.stderr)
        return 2
    found = memory_project(argv[1])
    if argv[0] == "sandboxed":
        return 0 if found and isolation.sandboxed(found[1]) else NOT_SANDBOXED
    if not found:
        print(f"{argv[1]} is no registered project's .agent-memory", file=sys.stderr)
        return 1
    why = sync_memory(*found, argv[1], lock=lock)
    if why:
        print(why, file=sys.stderr)
    return 1 if why else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
