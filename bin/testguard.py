"""The platform a test run sees: what bin/run-tests sets up for its tests, and the same for a test run started by hand.

A test run must never reach the machine's live platform. Run in a linked worktree, hubconf finds the main checkout and takes
its .state and .hub-home (the live state and instance), and many modules fix a path under them at import (alert.SENT,
cap-runner's DIR…), so a test that patches only hubconf.STATE still writes live (11-14: a fake conformance result that
doctor then quoted). And a test that leaves a temp folder behind leaves it in the user's own temp folder.

So a run gets, as bin/run-tests gives every module:
  - the tree under test as the platform (an explicit root: no lookup of the main checkout, so not its .state or
    instance). Only the root: HOME and STATE would leak into the fake hubs the tests build
    (docs/design/test-waiting.md A1)
  - a fresh HOME (no nvm there, so hubconf.tool and nvm_bin find none) and a recording `claude` first on PATH: no test
    reaches the real coding agent; a call that lands there fails the run
  - a secrets directory of its own, and a temp folder of its own (in the instance's private temp area, never the
    system's on an install: every sandboxed project can write that one), both removed afterwards
  - and at the end, what a test left in HOME, the secrets' temp area or the temp folder fails the run

`bin/py -m unittest …` (any form: tests.<module>, discover, or a test file run as a script) is such a run too: hubconf's
import calls reexec() when the entry point is unittest or a file in this tree's tests/ and no runner set things up; it
runs the same command again as a child under this environment, cleans up and exits with the child's code. A child, not
this process patched: the temp folder and HOME may already have been read, and the private temp area is found through
the live instance before the root is pointed elsewhere.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import names  # noqa: E402

TREE = pathlib.Path(__file__).resolve().parents[1]
MARK = "TEST_GUARD"   # set for a guarded run's child (bin/run-tests sets TEST_RUNNER): its own imports start nothing again


class Guard:
    """A run's throwaway HOME, PATH stand-ins and secrets (in the tree's .state, not the temp area: the sandbox tests need
    a HOME the sandbox can't write; test_cap_profile skips otherwise) and its temp folder."""

    def __init__(self, tree: pathlib.Path = TREE):
        state = tree / ".state"
        state.mkdir(parents=True, exist_ok=True)
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="run-tests-guard-", dir=state))
        self.home = self.dir / "home"
        (self.home / "workspace").mkdir(parents=True)
        (self.dir / "bin").mkdir()
        self.calls = self.dir / "calls.log"
        claude = self.dir / "bin" / "claude"
        claude.write_text(f'#!/bin/sh\necho "$PWD :: $*" >> "{self.calls}"\n'
                          'echo "run-tests: no test may start a real claude" >&2\nexit 97\n')
        claude.chmod(0o755)
        self.secrets = self.dir / "secrets"
        try:
            self.tmp = private_temp()
        except OSError:
            shutil.rmtree(self.dir, ignore_errors=True)
            raise

    def env(self, base: dict[str, str] | None = None, tree: pathlib.Path = TREE) -> dict[str, str]:
        env = {**(os.environ if base is None else base), "HOME": str(self.home),
               "PATH": f"{self.dir / 'bin'}{os.pathsep}{(os.environ if base is None else base).get('PATH', '')}",
               "TMPDIR": str(self.tmp)}
        names.env_set(env, "SECRETS", str(self.secrets))   # this run's own: what is left there is this run's tests'
        if not names.env("PYTHON", environ=env):
            names.env_set(env, "PYTHON", sys.executable)   # bin/py looks for it under HOME
        # no bytecode written, as the test runner's tiers write none: an interpreter's own cache can live under HOME
        # (macOS's python3 keeps its .pyc in ~/Library/Caches/com.apple.python), and what is left in HOME is a leak
        env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        names.env_set(env, "ROOT", names.env("ROOT", environ=env) or str(tree))
        return env

    def leaks(self) -> list[str]:
        """What the run's tests left where a real run would write real files."""
        try:
            out = [f"HOME: {p.relative_to(self.home)}" for p in self.home.rglob("*") if not p.is_dir()]
            out += [f"private temp: {p.name}" for p in (self.secrets / "tmp").glob("*")]
            out += [f"temp folder: {p.name}" for p in sorted(self.tmp.iterdir())]
        except OSError as e:
            out = [f"the leak check could not run: {e}"]
        return out

    def agent_calls(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def remove(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        shutil.rmtree(self.tmp, ignore_errors=True)


def private_temp() -> pathlib.Path:
    """The run's own temp folder. The tests run as the user and execute what they write there, and the system's temp folder
    is every sandboxed project's to write: an install's run takes one in its private temp area (hubconf.test_tmp). A clone
    that isn't an install (no instance, no settings file: CI, a fresh checkout) has no projects to race it: a folder of its
    own in the system's. Raises OSError for an install with no private folder outside every repo (the old layout's, in the
    hub root), which the test runner's tiers refuse too."""
    import hubconf
    import isolation
    if isolation.in_system_tmp(tempfile.gettempdir()) and (hubconf.instance_home() is not None or hubconf.config_path().exists()):
        return hubconf.test_tmp("run-tests-")
    return pathlib.Path(tempfile.mkdtemp(prefix="run-tests-"))


def guarded() -> bool:
    """A runner already set this process up (bin/run-tests, or reexec's child)."""
    return bool(names.env("TEST_RUNNER") or names.env(MARK))


def bare_test_run(tree: pathlib.Path = TREE) -> bool:
    """This process is a test run nobody set up: started as `python -m unittest …` or as a file in the tree's tests/."""
    if guarded():
        return False
    main = sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    if spec is not None and spec.name in ("unittest", "unittest.__main__"):
        return True
    f = getattr(main, "__file__", None)
    return bool(f) and pathlib.Path(f).resolve().parent == tree / "tests"


def reexec():
    """Run this process's command again as a guarded child, then clean up and leave with its exit code (1 when a test
    left something behind or reached for the real agent). Leaves with os._exit: this runs inside an import, which
    unittest's discovery would otherwise turn into an import error per module."""
    try:
        g = Guard()
    except OSError as e:
        print(f"tests: no private temp folder for this run ({e.strerror or e}); tests don't run in the system's, which every "
              f"sandboxed project can write. A hub in the old layout moves with {names.current('cli')} move-instance",
              file=sys.stderr, flush=True)
        os._exit(2)
    env = g.env()
    names.env_set(env, MARK, "1")
    code = 1
    try:
        code = subprocess.run([sys.executable, *sys.orig_argv[1:]], env=env).returncode
    except KeyboardInterrupt:
        code = 130
    finally:
        leaks, calls = g.leaks(), g.agent_calls()
        g.remove()
    if leaks:
        print("tests left files behind (removed with this run's throwaway folders; clean them up in the test):\n  "
              + "\n  ".join(leaks[:20]), file=sys.stderr)
        code = code or 1
    if calls:
        print("a test reached for the real coding agent; give it a stand-in:\n  " + "\n  ".join(calls[:20]), file=sys.stderr)
        code = code or 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code if code >= 0 else 128 - code)
