#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""agenthub test: the platform's test runner (docs/design/test-waiting.md B4). Run in a checkout of a registered
project's repo (its main checkout or a linked worktree), as whoever runs it: a session runs it as itself, a platform
job as the project (isolation.wrap). It never touches the branch: no rebase, no lock.

  agenthub test [--tier T]... [--when commit|merge|nightly|deploy] [--force] [--rev R | --index]

  1. Exports what it tests into a run folder under the repo's git common dir (agent-hub/test/): by default the
     worktree's content (HEAD plus uncommitted and untracked, through a temporary index), --index what is staged,
     --rev a commit. The exported tree's id is what results are keyed on.
  2. The project's test declaration: project v2 (bin/tiers.py; a v1 entry is upgraded as it is read). The hub's own
     entry is read from the exported tree, validated in a child whose AGENT_HUB_ROOT is the export, so the merge that
     changes agent-hub's tiers is tested by the tiers it brings.
  3. Which tiers: --tier names them; otherwise tiers.affected() for the moment (default merge), against the merge
     base with main (nightly, deploy and --rev without a merge moment: every tier of the moment).
  4. Per tier: its key (tiers.key); a recorded pass in the repo's store is a hit (--force runs anyway). Otherwise a
     fresh environment (uv sync --frozen / npm ci from the repo's own tool caches, policy.cache_env), a fixed tier
     environment (the platform's PATH, a fresh HOME and TMPDIR, the cache variables, declared env: and config:
     inputs; nothing else from the caller), worker slots from the machine's budget, the command in its own process
     group (stopped with TERM, a grace period, then KILL), its complete output in the worktree's git dir
     (agent-hub/test-output/), the result recorded.
  Exit 0 when every selected tier passed (or was a hit), 1 when one failed, 2 when the tests could not be set up.

Worker slots (B5): one lock file per slot in <state>/test-slots, opened read-only without following links and
without blocking; held by an advisory lock the kernel drops when the process ends. After SLOT_WAIT seconds a run
starts with the slots it holds, or with one worker and none.
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import hashlib
import json
import os
import pathlib
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

BIN = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import i18n  # noqa: E402
import policy  # noqa: E402
import tiers  # noqa: E402

_ = i18n.domain(__file__)

RUNNER_VERSION = 1
TIMEOUT = 1800
GRACE = 10
SLOT_WAIT = 60
KEEP_OUTPUTS = 50


class SetupError(Exception):
    pass


def git(cwd, *args, env=None) -> str:
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                       env={**os.environ, **(env or {})})
    if r.returncode != 0:
        raise SetupError(_("git {args}: {output}", args=" ".join(args), output=(r.stderr or r.stdout).strip()))
    return r.stdout.strip()


def repo_dirs(cwd: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    out = git(cwd, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-dir", "--git-common-dir")
    top, git_dir, common = (pathlib.Path(x).resolve() for x in out.splitlines())
    return top, git_dir, common


def project_of(main: pathlib.Path) -> tuple[str, dict]:
    projects = hubconf.projects()
    for name, doc in projects.items():
        ws = hubconf.workspace(name)
        if ws and ws.resolve() == main and "v" in doc:
            return name, doc
    if main == hubconf.MAIN.resolve():
        return hubconf.hub_project(), projects.get(hubconf.hub_project()) or {}
    raise SetupError(_("this repo ({path}) is not a registered project's", path=main))


def export(top: pathlib.Path, source: str, rev: str | None, run: pathlib.Path) -> str:
    """Writes the tested content to run/tree; returns its tree id."""
    idx = {"GIT_INDEX_FILE": str(run / "index")}
    if source == "rev":
        tree = git(top, "rev-parse", f"{rev}^{{tree}}")
    elif source == "index":
        tree = git(top, "write-tree")
    else:
        git(top, "read-tree", "HEAD", env=idx)
        git(top, "add", "-A", env=idx)
        tree = git(top, "write-tree", env=idx)
    git(top, "read-tree", tree, env=idx)
    git(top, "checkout-index", "-a", f"--prefix={run / 'tree'}/", env=idx)
    return tree


def declaration(name: str, doc: dict, tree: pathlib.Path, run: pathlib.Path) -> dict:
    if name != hubconf.hub_project():
        return tiers.upgrade(doc)
    entry = tree / "projects" / f"{name}.yaml"
    code = ("import json, sys, yaml, registry, tiers\n"
            "doc = tiers.upgrade(yaml.safe_load(open(sys.argv[1], encoding='utf-8')))\n"
            "print(json.dumps({'doc': doc, 'errors': registry.validate_v2(doc, sys.argv[2])}, default=str))\n")
    env = {**os.environ, "AGENT_HUB_ROOT": str(tree), "AGENT_HUB_STATE": str(run / "state"),
           "PYTHONPATH": str(BIN)}
    r = subprocess.run([sys.executable, "-c", code, str(entry), name], env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise SetupError(_("reading {path}: {output}", path=entry, output=(r.stderr or r.stdout).strip()[-800:]))
    out = json.loads(r.stdout)
    if out["errors"]:
        raise SetupError(_("{path} is not a valid project v2 entry: {errors}", path=entry,
                           errors="; ".join(out["errors"])))
    return out["doc"]


def base_env(main: pathlib.Path, run: pathlib.Path, hub: bool) -> tuple[dict, dict]:
    """The fixed tier environment, and the part of it a result depends on (for the key)."""
    uv = shutil.which("uv", path=f"{os.environ.get('PATH', '')}:{hubconf.LAUNCHD_PATH}")
    path = ":".join([*([str(pathlib.Path(uv).parent)] if uv else []), hubconf.LAUNCHD_PATH])
    caches = policy.cache_env(main)
    caches["PLAYWRIGHT_BROWSERS_PATH"] = str(main / ".git" / "agent-hub" / "cache" / "playwright")
    (run / "home").mkdir()
    (run / "tmp").mkdir()
    env = {"PATH": path, "LANG": os.environ.get("LANG") or "en_US.UTF-8", "HOME": str(run / "home"),
           "TMPDIR": str(run / "tmp"), "GIT_CEILING_DIRECTORIES": str(run), "PYTHONDONTWRITEBYTECODE": "1", **caches}
    if hub:
        env["AGENT_HUB_PYTHON"] = sys.executable
    return env, {"PATH": path, "runner": str(RUNNER_VERSION)}


def inputs(doc: dict, tree: pathlib.Path, env: dict) -> tuple[list[list[str]], dict]:
    """Each input's value as a digest, and the variables it adds to the tier's environment."""
    listed = {f.name: (var, f) for var, f in hubconf.test_config_inputs()}
    values, extra = [], {}
    for item in doc.get("inputs") or []:
        kind, _sep, value = item.partition(":")
        if kind == "file":
            f = tree / value
            digest = hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() and not f.is_symlink() else "absent"
        elif kind == "config":
            if value not in listed:
                digest = "absent"
            else:
                var, f = listed[value]
                if not f.is_file():
                    raise SetupError(_("{file} is listed in hub.json tests.config_inputs but missing", file=f))
                digest = hashlib.sha256(f.read_bytes()).hexdigest()
                extra[var] = str(f)
        elif kind == "env":
            v = os.environ.get(value)
            digest = "unset" if v is None else hashlib.sha256(v.encode()).hexdigest()
            if v is not None:
                extra[value] = v
        else:   # cmd: its output, in the fixed environment
            r = subprocess.run(["/bin/sh", "-c", value], cwd=tree, env=env, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=120)
            digest = hashlib.sha256(f"{r.returncode}\n{r.stdout}".encode()).hexdigest()
        values.append([item, digest])
    return values, extra


def environment(tree: pathlib.Path, run: pathlib.Path, env: dict, log) -> dict:
    """A fresh environment for this run from the lockfiles, built from the repo's own caches."""
    env = dict(env)
    if (tree / "uv.lock").is_file():
        venv = run / "venv"
        r = subprocess.run(["uv", "sync", "--frozen", "--quiet"], cwd=tree, env={**env, "UV_PROJECT_ENVIRONMENT": str(venv)},
                           stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            raise SetupError(_("uv sync --frozen failed (see the output file)"))
        env.update(VIRTUAL_ENV=str(venv), PATH=f"{venv / 'bin'}:{env['PATH']}", UV_PROJECT_ENVIRONMENT=str(venv))
    if (tree / "package-lock.json").is_file():
        r = subprocess.run(["npm", "ci", "--no-audit", "--no-fund", "--loglevel=error"], cwd=tree, env=env, stdout=log,
                           stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            raise SetupError(_("npm ci failed (see the output file)"))
    return env


class Slots:
    def __init__(self, folder: pathlib.Path):
        self.folder, self.fds = folder, []
        n = os.cpu_count() or 4
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for i in range(n):
                f = folder / f"slot-{i:02d}"
                if not os.path.lexists(f):
                    f.touch()
        except OSError:   # a sandboxed caller cannot create them: it uses what is there, or one worker
            pass

    def _try(self, f: pathlib.Path) -> int | None:
        try:
            fd = os.open(f, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return None
        try:
            if stat.S_ISREG(os.fstat(fd).st_mode):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
        except OSError:
            pass
        os.close(fd)
        return None

    def take(self, want: int, wait: float = SLOT_WAIT) -> int:
        deadline = time.monotonic() + wait
        files = sorted(self.folder.glob("slot-*")) if self.folder.is_dir() else []
        while True:
            for f in files:
                if len(self.fds) >= want:
                    break
                if (fd := self._try(f)) is not None:
                    self.fds.append(fd)
            if len(self.fds) >= want or time.monotonic() >= deadline or not files:
                return max(len(self.fds), 1)
            time.sleep(0.5)

    def release(self) -> None:
        for fd in self.fds:
            os.close(fd)
        self.fds = []


def stop(p: subprocess.Popen) -> None:
    """The whole process group: TERM, a grace period, KILL (children that outlived the command included)."""
    for sig, wait in ((signal.SIGTERM, GRACE), (signal.SIGKILL, 0)):
        try:
            os.killpg(p.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        end = time.monotonic() + wait
        while time.monotonic() < end:
            try:
                os.killpg(p.pid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.2)


def run_tier(tier: dict, tree: pathlib.Path, env: dict, slots: Slots, log) -> bool:
    decl = tier.get("jobs") or {}
    parallel = "{jobs}" in tier["run"] or decl.get("env")
    cpus = os.cpu_count() or 4
    n = slots.take(min(decl.get("max") or cpus, cpus) if parallel else 1)
    try:
        env = dict(env)
        if decl.get("env"):
            env[decl["env"]] = str(n)
        cmd = tier["run"].replace("{jobs}", str(n))
        log.write(f"$ {cmd}\n".encode())
        log.flush()
        p = subprocess.Popen(["/bin/sh", "-c", cmd], cwd=tree, env=env, stdout=log, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            rc = p.wait(timeout=TIMEOUT)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            stop(p)
            p.wait()
            log.write(_("(stopped: over {minutes} minutes or interrupted)\n", minutes=TIMEOUT // 60).encode())
            return False
        stop(p)   # what the command left running in its group
        return rc == 0
    finally:
        slots.release()


def tail(path: pathlib.Path, n: int = 30) -> str:
    return "\n".join(path.read_text(errors="replace").splitlines()[-n:])


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="agenthub test")
    ap.add_argument("--tier", action="append", default=[])
    ap.add_argument("--when", choices=tiers.MOMENTS, default="merge")
    ap.add_argument("--force", action="store_true")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--rev")
    src.add_argument("--index", action="store_true")
    a = ap.parse_args(argv)
    try:
        top, git_dir, common = repo_dirs(pathlib.Path.cwd())
        main_dir = common.parent if common.name == ".git" else top
        name, doc = project_of(main_dir)
        (common / "agent-hub" / "test").mkdir(parents=True, exist_ok=True)
        run = pathlib.Path(tempfile.mkdtemp(prefix="run.", dir=common / "agent-hub" / "test"))
    except SetupError as e:
        print(f"agenthub test: {e}", file=sys.stderr)
        return 2
    outdir = git_dir / "agent-hub" / "test-output"
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    failed = False
    try:
        tree_id = export(top, "rev" if a.rev else "index" if a.index else "worktree", a.rev, run)
        tree = run / "tree"
        decl = declaration(name, doc, tree, run)
        if a.tier:
            chosen = [(name, t) for t in decl.get("tests") or [] if t.get("tier") in a.tier]
            if missing := set(a.tier) - {t["tier"] for _n, t in chosen}:
                raise SetupError(_("no such tier: {tiers}", tiers=", ".join(sorted(missing))))
        else:
            base = None
            if a.when in ("commit", "merge"):
                main_branch = next((b for b in ("main", "master")
                                    if subprocess.run(["git", "-C", str(top), "rev-parse", "--verify", "-q", b],
                                                      capture_output=True).returncode == 0), None)
                if main_branch:
                    base = git(top, "merge-base", main_branch, a.rev or "HEAD")
            chosen = tiers.affected(top, base, tree_id, a.when, {name: decl})
        if not chosen:
            print(_("agenthub test: nothing to run for {project} ({when}): no declared tier is affected",
                    project=name, when=a.when))
            return 0
        env, identity = base_env(main_dir, run, name == hubconf.hub_project())
        values, extra = inputs(decl, tree, env)
        store = tiers.Store(common)
        slots = Slots(hubconf.STATE / "test-slots")
        built = None
        for project, tier in chosen:
            k = tiers.key(tree_id, tier, values, identity)
            label = f"{project}/{tier['tier']}"
            if not a.force and store.passed(k):
                print(_("✓ {tier}: this tree already passed ({key})", tier=label, key=k[:12]))
                continue
            out = outdir / f"{stamp}-{project}-{tier['tier']}.log"
            t0 = time.monotonic()
            with open(out, "wb") as log:
                try:
                    if built is None:
                        built = environment(tree, run, {**env, **extra}, log)
                    ok = run_tier(tier, tree, built, slots, log)
                except SetupError as e:
                    log.write(f"{e}\n".encode())
                    ok = False
            secs = round(time.monotonic() - t0, 1)
            store.record(k, project, tier["tier"], ok, a.when, str(out))
            if ok:
                print(_("✓ {tier} passed in {secs}s; output: {path}", tier=label, secs=secs, path=out))
            else:
                failed = True
                print(_("✗ {tier} failed in {secs}s; complete output: {path}", tier=label, secs=secs, path=out))
                print(tail(out))
        for old in sorted(outdir.glob("*.log"))[:-KEEP_OUTPUTS]:
            old.unlink(missing_ok=True)
        return 1 if failed else 0
    except SetupError as e:
        print(f"agenthub test: {e}", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(run, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
