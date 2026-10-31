#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The platform's git work in project repos (docs/design/projects-and-repos.md section 4, "Platform git in project
repos"): the platform runs git only in repos it owns (the memory bare repos, the librarian's memory copies, its own
checkout); in a project's repo every git command runs as that project, through project-git-job.py started by
isolation.run_as (sandboxed when the project is). What comes back is data: JSON parsed here with types checked, or a
bundle fetched into a repo the platform owns with transfer.fsckObjects on. Nothing derived from it is used for a write
outside the project's own job.

  job(project, cwd, op, *args, **kw) → CompletedProcess     one op of project-git-job.py as the project
  data(project, cwd, op, *args) → object                    its JSON output; raises Unavailable (sandbox refused,
                                                            git failed, malformed output)
  fetch_bundle(bare, bundle, branch) → None | why not       fast-forward <bare>'s branch to the bundle's HEAD
  memory_project(dir) → (name, doc) | None                  whose .agent-memory this is
  sync_memory(name, doc, memdir) → None | why not           a sandboxed project's memory sync (commit + merge + bundle
                                                            as the project; the platform fetches the bundle)

CLI (git-sync-memory.sh, sync-all-memory.sh):
  project_git.py sandboxed <dir>      exit 0 when <dir> is the .agent-memory of a sandboxed project, 3 when not (any
                                      other exit: it couldn't tell)
  project_git.py sync-memory <dir>    sync_memory for that project (exit 1 with the reason on stderr)
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

BIN = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import isolation  # noqa: E402
import policy  # noqa: E402

JOB = BIN / "project-git-job.py"
MAX_JSON = 8 << 20
INCOMING = "refs/agent-hub/incoming"
NOT_SANDBOXED = 3
EX_SANDBOX = 78   # sandboxed-run refused (no sandbox on this machine): the work is not done, never done unsandboxed


class Unavailable(Exception):
    pass


def job(project: str | None, cwd, op: str, *args, **kw) -> subprocess.CompletedProcess:
    return isolation.run_as(project, cwd, [*hubconf.launch(JOB), op, *map(str, args)], **kw)


def data(project: str | None, cwd, op: str, *args):
    r = job(project, cwd, op, *args, capture_output=True, text=True)
    if r.returncode == EX_SANDBOX:
        raise Unavailable(f"{project}: the sandbox refused ({r.stderr.strip()[-200:]})")
    if r.returncode != 0:
        raise Unavailable(f"{project}: {op} failed ({r.stderr.strip()[-200:]})")
    if len(r.stdout) > MAX_JSON:
        raise Unavailable(f"{project}: {op} output too large")
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


def sync_memory(name: str, doc: dict, memdir, branch: str = "agent-memory") -> str | None:
    bare = policy.memory_bare(name, doc)
    if not (bare / "objects").is_dir():
        return f"{name}: no local memory bare repo at {bare}"
    with tempfile.TemporaryDirectory(prefix="hub-memory-") as tmp:
        bundle = pathlib.Path(tmp) / "memory.bundle"
        with bundle.open("wb") as fh:
            r = job(name, memdir, "memory-sync", memdir, bare, branch, stdout=fh, stderr=subprocess.PIPE)
        err = r.stderr.decode(errors="replace").strip()[-300:]
        if r.returncode == EX_SANDBOX:
            return f"{name}: the sandbox refused; memory not synced ({err})"
        if r.returncode != 0:
            return f"{name}: the memory sync job failed ({err})"
        if bundle.stat().st_size == 0:
            return None
        why = fetch_bundle(bare, bundle, branch)
        return f"{name}: {why}" if why else None


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("sandboxed", "sync-memory"):
        print("usage: project_git.py sandboxed|sync-memory <dir>", file=sys.stderr)
        return 2
    found = memory_project(argv[1])
    if argv[0] == "sandboxed":
        return 0 if found and isolation.sandboxed(found[1]) else NOT_SANDBOXED
    if not found:
        print(f"{argv[1]} is no registered project's .agent-memory", file=sys.stderr)
        return 1
    why = sync_memory(*found, argv[1])
    if why:
        print(why, file=sys.stderr)
    return 1 if why else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
