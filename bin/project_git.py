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
  project_git.py sandboxed-project <name>   exit 0 when this registered project is sandboxed, 3 when not (any
                                      other exit: it couldn't tell)
  project_git.py run-as <name> <dir> -- <argv…>
                                      a shell hook's work as the project <dir> belongs to (HUB_AS_PROJECT=1 set, stdin
                                      and output passed through); exit 3 when it isn't sandboxed
  project_git.py sync-memory [--lock] <name>
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
import gitdirs  # noqa: E402
import hubconf  # noqa: E402
import names  # noqa: E402
import isolation  # noqa: E402
import policy  # noqa: E402
import registry  # noqa: E402

JOB = BIN / "project-git-job.py"
MAX_JSON = 8 << 20          # facts
MAX_BUNDLE = 4 << 30        # a bundle of unpushed work or memory
TIMEOUT_S = 120
BUNDLE_TIMEOUT_S = 1800
TIMED_OUT, TOO_BIG = 124, 125
INCOMING = names.repo_ref("incoming")
NOT_SANDBOXED = 3
COULD_NOT_TELL = 5   # the name isn't in the registry's index (a stale one, say): it may be sandboxed, so nothing runs as the user
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


def data(project: str | None, cwd, op: str, *args, **kw):
    """One op's JSON (run's keywords: input, timeout…); Unavailable when it failed or printed something else."""
    r = job(project, cwd, op, *args, **kw)
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


def sandbox_of(cwd) -> tuple[str, dict] | None:
    """The sandboxed project this folder is under, the nearest, as a hook's cwd names it or as it resolves (gitdirs.holder). As
    named comes first: a folder a sandboxed project owns can be swapped for a link after the runtime took it as the session's
    folder, and a hook's input still names it, so what it resolves to says nothing about whose session this is."""
    docs = {name: {**doc, "name": name} for name, doc in hubconf.projects().items()}
    roots = {name: ws for name, doc in docs.items()
             if (ws := hubconf.path((doc.get("paths") or {}).get("workspace"))) and isolation.sandboxed(doc)}
    name = gitdirs.holder(cwd, roots)
    return (name, docs[name]) if name else None


def holds(doc: dict, folder) -> bool:
    """Whether this project's workspace really holds the folder, resolved (a sandbox_of by name may not)."""
    ws = hubconf.path((doc.get("paths") or {}).get("workspace"))
    return ws is None or gitdirs.holder(pathlib.Path(folder).resolve(), {"": ws}) is not None


def registered_main(main) -> bool:
    """Whether this folder is the checkout of the hub or of a registered project that isn't sandboxed."""
    m = pathlib.Path(main).resolve()
    return m == hubconf.MAIN.resolve() or any(
        (ws := hubconf.path((d.get("paths") or {}).get("workspace"))) and ws.resolve() == m and not isolation.sandboxed({**d, "name": n})
        for n, d in hubconf.projects().items())


class Scope(NamedTuple):
    memory_ok: bool           # git in its project's memory, as the user
    folder_ok: bool           # git in this very folder, as the user
    memdir: pathlib.Path      # that memory, from the same look


def scope_of(folder) -> Scope:
    """What a hook may run as the user in this folder, from one look at its pointers: looking twice lets them change in between.

    memory_ok: git reads the repo's config and runs what it names, so yes only for what isn't a stranger's: a registered project that
    isn't sandboxed, the hub, or a linked worktree of one of those that it confirms (a repo a stranger made confirms itself, so its
    being registered is what counts). Everything else is no, not a list of bad cases.
    folder_ok: memory_ok, and a folder only its owner can write, since a .git file and all beside it can change between a look and
    the git. In nothing a sandboxed project can write; then a registered project's, or a linked worktree under its main checkout's
    folder or under workspace_root (no sandbox writes there but its own folder, policy.py). Anywhere else, the temp folder
    included, it is no.
    memdir: a confirmed linked worktree's memory is its main checkout's, whatever sits beside it, others their own."""
    found, at = gitdirs.dirs(folder), project_at(folder)
    is_confirmed = bool(found) and gitdirs.confirmed(found)
    memdir = found.common.parent / ".agent-memory" if is_confirmed else pathlib.Path(folder) / ".agent-memory"
    if at:
        memory_ok = not isolation.sandboxed(at[1])
    else:
        memory_ok = bool(found) and (found.top == hubconf.MAIN.resolve() or (is_confirmed and registered_main(found.common.parent)))
    folder_ok = False
    if memory_ok and not sandbox_of(folder) and not gitdirs.holder(pathlib.Path(folder).resolve(), sandbox_writes()):
        folder_ok = bool(at) or (bool(found) and gitdirs.holder(found.top, {"main": found.common.parent,
                                                                            "root": hubconf.p("workspace_root", "~/workspace")}) is not None)
    return Scope(memory_ok, folder_ok, memdir)


def user_git_ok(folder) -> bool:
    return scope_of(folder).memory_ok


def sandbox_writes() -> dict:
    """What the sandboxed projects' own processes can write besides the temp folder: {path: path}, for gitdirs.holder."""
    out = {}
    for name, doc in hubconf.projects().items():
        doc = {**doc, "name": name}
        if isolation.sandboxed(doc):
            paths = doc.get("paths") or {}
            d = registry.dirs(doc) if paths.get("workspace") else {}
            out.update({str(p): p for p in (d.get("workspace"), d.get("runtime"), *map(hubconf.path, doc.get("allow_write") or [])) if p})
    return out


def folder_git_ok(folder) -> bool:
    return scope_of(folder).folder_ok


def memory_dir(folder) -> pathlib.Path:
    return scope_of(folder).memdir


def sync_memory_of(name: str, ws) -> None:
    """A platform script's sync of this project's memory: as the project when it is sandboxed (with-lock.sh's own rev-parse reads the
    repo's config, which can hang on a FIFO), otherwise the hook's path. Failures are the next hourly sync's, and so is a name the
    registry's index doesn't have."""
    doc = hubconf.projects().get(name)
    if doc is None:
        print(f"{name} is not in the registry's index; whether it is sandboxed can't be told, memory not synced", file=sys.stderr)
        return
    mem = pathlib.Path(ws) / ".agent-memory"
    if isolation.sandboxed({**doc, "name": name}):
        sync_memory(name, doc, mem, lock=True)
        return
    bin_dir = pathlib.Path(__file__).resolve().parent
    subprocess.run([str(bin_dir / "with-lock.sh"), str(bin_dir / "git-sync-memory.sh")], cwd=mem, capture_output=True)


def sync_memory(name: str, doc: dict, memdir, branch: str = "agent-memory", lock: bool = False) -> str | None:
    """lock: the job takes with-lock's lock itself (the hourly sync; a hook's call already holds it)."""
    bare = policy.memory_bare(name, doc)
    if not (bare / "objects").is_dir():
        return f"{name}: no local memory bare repo at {bare}"
    try:
        tmp = tempfile.TemporaryDirectory(prefix="hub-memory-", dir=hubconf.private_tmp(), ignore_cleanup_errors=True)
    except OSError as e:
        return f"{name}: no folder for the bundle ({e})"
    with tmp as d:
        bundle = pathlib.Path(d) / "memory.bundle"
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


def by_name(project: str) -> tuple[str, dict] | None:
    doc = hubconf.projects().get(project)
    return (project, {**doc, "name": project}) if doc else None


def run_as_cli(project: str, d: str, argv: list[str]) -> int:
    """A shell hook's work as this project in the folder d, by the name the hook already has, never asked again of a folder that can
    have changed: stdin passed in, its stdout and stderr passed out. Exit 3 when it isn't sandboxed (the caller goes on itself), and
    COULD_NOT_TELL for a name the index doesn't have (the caller goes no further)."""
    found = by_name(project)
    if not found:
        return _not_in_index(project)
    if not isolation.sandboxed(found[1]):
        return NOT_SANDBOXED
    stdin = "" if sys.stdin.isatty() else sys.stdin.read()
    r = run(found[0], d, argv, input=stdin, env={"HUB_AS_PROJECT": "1"})
    sys.stdout.buffer.write(r.stdout)
    sys.stderr.write(r.stderr)
    return r.returncode


UNCONFIRMED = 4   # worktree_scope: its .git names a registered repo that doesn't list it (moved by hand)


def worktree_scope(top: str, main: str, cwd: str | None = None) -> int:
    """Whether a linked worktree's hook goes on as the user (worktree-memory.sh, through `hub-event worktree-scope`). cwd: what the
    hook's input names; top and main are read from the folder, and pointer files under top say main, so neither decides alone.
    0: a sandboxed project's, as the session's folder names it, or as top or main is (a repo it writes is never run as the
    user, whoever names it); its name and its folder, resolved, are printed, as its registry entry says.
    NOT_SANDBOXED: the hook goes on in main's memory, so main has to be what it said when it was read: the top may pass now with
    a main read a moment ago from a pointer since rewritten. UNCONFIRMED, or 1: nothing runs."""
    cage = (sandbox_of(cwd) if cwd else None) or sandbox_of(top)
    at_main = None if cage else memory_project(pathlib.Path(main) / ".agent-memory")
    cage = cage or (at_main if at_main and isolation.sandboxed(at_main[1]) else None)
    if cage:
        print(f"{cage[0]}\t{hubconf.path((cage[1].get('paths') or {}).get('workspace')).resolve()}")
        return 0
    if user_git_ok(top):
        m, at_top = pathlib.Path(main).resolve(), project_at(top)
        at_m = project_at(m)
        return NOT_SANDBOXED if registered_main(m) or (at_top and at_m and at_top[0] == at_m[0]) else 1
    found = gitdirs.dirs(top)
    return UNCONFIRMED if found and registered_main(found.common.parent) and not gitdirs.confirmed(found) else 1


def _not_in_index(name: str) -> int:
    print(f"{name} is not in the registry's index, so whether it is sandboxed can't be told", file=sys.stderr)
    return COULD_NOT_TELL


def main(argv: list[str]) -> int:
    if argv[:1] == ["run-as"] and len(argv) >= 5 and argv[3] == "--":
        return run_as_cli(argv[1], argv[2], argv[4:])
    lock = "--lock" in argv
    argv = [a for a in argv if a != "--lock"]
    if len(argv) != 2 or argv[0] not in ("sandboxed-project", "sync-memory"):
        print("usage: project_git.py sandboxed-project <name> | sync-memory [--lock] <name>", file=sys.stderr)
        return 2
    found = by_name(argv[1])
    if not found:
        return _not_in_index(argv[1])
    if argv[0] == "sandboxed-project":
        return 0 if isolation.sandboxed(found[1]) else NOT_SANDBOXED
    if not isolation.sandboxed(found[1]):   # this path is a sandboxed project's job: anything else, the caller's
        print(f"{argv[1]} is no sandboxed registered project", file=sys.stderr)
        return NOT_SANDBOXED
    why = sync_memory(*found, hubconf.path((found[1].get("paths") or {}).get("workspace")) / ".agent-memory", lock=lock)
    if why:
        print(why, file=sys.stderr)
    return 1 if why else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
