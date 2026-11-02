#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The test system's declarations and results (docs/design/test-waiting.md B1–B3): one module for what a project's
tests are, which of them a change needs, and whether a run already happened. Standard library only; the runner
(bin/test-runner.py) is its one caller that runs anything.

  upgrade(doc) → doc                     a v1 registry document as v2: runtime.test becomes a `full` tier with
                                         when [merge, nightly, deploy]; runtime keeps only deploy (an emptied runtime
                                         stays {}: its presence still means <services_root>/<name>); no `code` means
                                         every file. A v2 document comes back as it is.
  is_code(doc, paths) → bool             does a change to these paths (relative to the project's folder) count as
                                         its code
  selected(doc, changed, moment) → [tier]
                                         the tiers a change needs at a moment (commit | merge | nightly | deploy);
                                         changed=None = every tier of that moment (nightly, deploy)
  affected(repo, base, head, moment, docs) → [(project, tier)]
                                         the same for every project of the repo (today the repo has one; scopes and
                                         depends_on extend it, projects-and-repos.md); base=None = every tier
  key(tree, tier, inputs, env) → str     what a result depends on: the tested tree, the declared run and jobs.max (not
                                         the worker count), the inputs' values, the tier environment, KEY_FORMAT
  Store(common_dir)                      one store per repo, in its git common dir (brunna/test-results.jsonl):
    .record(key, project, tier, ok, moment, output)
    .passed(key) → bool                  a pass not older than MAX_AGE_DAYS, unless the latest nightly run of the key
                                         failed (a flaky pass does not stay green for merges and deploys)
  RunLog(common_dir)                     the waiting report's measurements (test-waiting.md B7), beside the store
                                         (brunna/test-runs.jsonl): one line per tier per invocation, hits included,
                                         with how long it took. Measurement only: what counts as a pass is the store's
                                         alone, so a hit written here never extends a recorded pass's life.
    .record(**fields)                    {at, run, project, tier, moment, caller, source, result: hit|pass|fail,
                                         secs (the tier), total (from the invocation's start to this result), tree}

The store is read and written by whoever runs the tests, as itself: a session's runner, or a project's own job. The
platform never opens it directly (test-waiting B4); it reads results through a project_git job.
"""
from __future__ import annotations

import copy
import datetime
import hashlib
import json
import os
import pathlib
import sys
import stat
import subprocess

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import names  # noqa: E402  (standard library only, like this file)

KEY_FORMAT = 1           # bump when what a recorded result means changes (not on every platform commit)
MOMENTS = ("commit", "merge", "nightly", "deploy")
MAX_AGE_DAYS = 30
KEEP_LINES = 5000


def upgrade(doc: dict) -> dict:
    if not isinstance(doc, dict) or doc.get("v") != 1:
        return doc
    new = copy.deepcopy(doc)
    new["v"] = 2
    rt = new.get("runtime")
    if isinstance(rt, dict) and "test" in rt:
        test = rt.pop("test")
        if test:
            new["tests"] = [{"tier": "full", "run": test, "when": ["merge", "nightly", "deploy"]}]
    return new


def match(path: str, pattern: str) -> bool:
    return pathlib.PurePosixPath(path).full_match(pattern)


def is_code(doc: dict, paths: list[str]) -> bool:
    code = doc.get("code")
    if code is None:
        return bool(paths)
    return any(match(p, c) for p in paths for c in code)


def selected(doc: dict, changed: list[str] | None, moment: str) -> list[dict]:
    tiers = [t for t in (doc.get("tests") or []) if moment in (t.get("when") or [])]
    if changed is None:
        return tiers
    if not is_code(doc, changed):
        return []
    return [t for t in tiers if not t.get("paths") or any(match(p, g) for p in changed for g in t["paths"])]


def changed_paths(repo: pathlib.Path, base: str, head: str) -> list[str]:
    """Paths that differ between two commits or trees (the runner passes the merge base and the exported tree)."""
    r = subprocess.run(["git", "-C", str(repo), "diff", "--name-only", "--no-renames", base, head],
                       capture_output=True, text=True, check=True)
    return [line for line in r.stdout.splitlines() if line]


def affected(repo: pathlib.Path, base: str | None, head: str, moment: str,
             docs: dict[str, dict]) -> list[tuple[str, dict]]:
    """docs: the repo's projects (name → v2 document). Today a repo has one project, whose folder is the repo's root,
    so its paths are the repo's paths."""
    changed = None if base is None else changed_paths(repo, base, head)
    return [(name, t) for name, doc in sorted(docs.items()) for t in selected(doc, changed, moment)]


def key(tree: str, tier: dict, inputs: list[list[str]], env: dict[str, str]) -> str:
    blob = [KEY_FORMAT, tree, tier.get("run"), (tier.get("jobs") or {}).get("max"), inputs, sorted(env.items())]
    return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()


def _open(path: pathlib.Path, flags: int) -> int:
    """Without following a link and without blocking on a FIFO; a regular file or nothing."""
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError(f"{path} is not a regular file")
    return fd


def read_jsonl(path: pathlib.Path) -> list[dict]:
    try:
        fd = _open(path, os.O_RDONLY)
    except OSError:
        return []
    with os.fdopen(fd, encoding="utf-8", errors="replace") as f:
        out = []
        for line in f:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if isinstance(o, dict):
                out.append(o)
        return out


def append_jsonl(path: pathlib.Path, entry: dict) -> None:
    """One line appended (the file kept to KEEP_LINES: past it, the newer half stays)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = _open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    recs = read_jsonl(path)
    if len(recs) > KEEP_LINES:
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs[-KEEP_LINES // 2:]))
        tmp.replace(path)


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


class RunLog:
    def __init__(self, common_dir: pathlib.Path):
        self.path = names.repo_dir(common_dir) / "test-runs.jsonl"

    def record(self, **fields) -> None:
        append_jsonl(self.path, {"at": now_iso(), **fields})

    def records(self) -> list[dict]:
        return read_jsonl(self.path)


class Store:
    def __init__(self, common_dir: pathlib.Path):
        self.path = names.repo_dir(common_dir) / "test-results.jsonl"

    def records(self) -> list[dict]:
        return read_jsonl(self.path)

    def record(self, key_: str, project: str, tier: str, ok: bool, moment: str, output: str = "") -> None:
        append_jsonl(self.path, {"key": key_, "project": project, "tier": tier, "ok": ok, "moment": moment,
                                 "output": output, "at": now_iso()})

    def passed(self, key_: str, now: datetime.datetime | None = None) -> bool:
        mine = [r for r in self.records() if r.get("key") == key_]
        nightly = [r for r in mine if r.get("moment") == "nightly"]
        if nightly and not nightly[-1].get("ok"):
            return False
        oks = [r for r in mine if r.get("ok")]
        if not oks:
            return False
        try:
            at = datetime.datetime.fromisoformat(oks[-1]["at"])
        except (KeyError, ValueError):
            return False
        now = now or datetime.datetime.now().astimezone()
        return (now - at).days < MAX_AGE_DAYS and mine[-1].get("ok", False)
