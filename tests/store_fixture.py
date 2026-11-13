"""A stand-in store of checked copies (bin/runtime_store.py; docs/design/runtime-versions.md section 3) for tests that
need a coding agent's binary: a small executable promoted through the real store with a stand-in distribution port,
under the test's own temp folder. Never the machine's store: hubconf.runtimes_dir is patched for the test, and with
children=True the STATE variable points child processes at the same temp state (hubconf.runtimes_dir is
<STATE>/runtimes there).

    exe = store_fixture.stand_in(self)                      a claude that answers like `claude -p --output-format json`
    exe = store_fixture.stand_in(self, body="#!/bin/sh …")   its own script
    store_fixture.empty(self)                               a store with no copy in it
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

VERSION = "2.1.400"
# answers like claude -p: one result (--output-format json), or the init event (no tools) and then it (stream-json)
ANSWER = ("#!/bin/sh\ncat > /dev/null\n"
          "case \" $* \" in *\" stream-json \"*) printf '%s\\n' '{\"type\": \"system\", \"subtype\": \"init\", \"tools\": []}';; esac\n"
          "printf '%s\\n' '{\"type\": \"result\", \"result\": \"ok\", \"is_error\": false, \"session_id\": \"s\", \"num_turns\": 1}'\n")
INIT = '{"type": "system", "subtype": "init", "tools": []}'


class Dist:
    """A distribution port whose install writes the executable at node_modules/.bin/<name>, as npm lays out a copy."""

    def __init__(self, body: str, name: str = "claude"):
        self.body, self.name = body, name

    def install(self, version, folder):
        exe = pathlib.Path(folder) / "node_modules" / ".bin" / self.name
        exe.parent.mkdir(parents=True)
        exe.write_text(self.body)
        exe.chmod(0o755)
        return exe


def _state(test, children: bool) -> pathlib.Path:
    import hubconf
    import names
    d = tempfile.TemporaryDirectory()
    test.addCleanup(d.cleanup)
    state = pathlib.Path(d.name).resolve()
    p = mock.patch.object(hubconf, "runtimes_dir", lambda: state / "runtimes")
    p.start()
    test.addCleanup(p.stop)
    if children:
        env = names.env_set({}, "STATE", str(state))
        e = mock.patch.dict(os.environ, env)
        e.start()
        test.addCleanup(e.stop)
    _release(test)
    return state


def _release(test) -> None:
    """No hold from an earlier test's store carries over (runtime_store.held keeps one per runtime per process)."""
    import runtime_store
    for name in list(runtime_store._HELD):
        runtime_store.release(name)
    test.addCleanup(lambda: [runtime_store.release(n) for n in list(runtime_store._HELD)])


def empty(test, children: bool = False) -> pathlib.Path:
    """A store with nothing in it; its folder (the temp state's runtimes)."""
    return _state(test, children) / "runtimes"


def promote_into(runtimes: pathlib.Path, body: str = ANSWER, version: str = VERSION, runtime: str = "claude") -> pathlib.Path:
    """A copy installed and promoted in the store at runtimes (a fake hub's <STATE>/runtimes, which its own processes
    read), this process's store left as it was. The executable's path."""
    import hubconf
    import runtime_store
    with mock.patch.object(hubconf, "runtimes_dir", lambda: pathlib.Path(runtimes)):
        runtime_store.install(runtime, version, Dist(body))
        return runtime_store.promote(runtime, version).exe


class Store:
    """The same without a TestCase (a fixture object with its own teardown): start() patches the store at
    <root>/runtimes and promotes a copy, stop() undoes it."""

    def __init__(self, root: pathlib.Path):
        self.root = pathlib.Path(root)
        self._patch = None

    def patch(self) -> "Store":
        """The store at <root>/runtimes, with nothing in it yet."""
        import hubconf
        if self._patch is None:
            self._patch = mock.patch.object(hubconf, "runtimes_dir", lambda: self.root / "runtimes")
            self._patch.start()
        return self

    def start(self, body: str = ANSWER, version: str = VERSION, runtime: str = "claude") -> pathlib.Path:
        import runtime_store
        self.patch()
        for name in list(runtime_store._HELD):
            runtime_store.release(name)
        runtime_store.install(runtime, version, Dist(body))
        return runtime_store.promote(runtime, version).exe

    def stop(self) -> None:
        import runtime_store
        for name in list(runtime_store._HELD):
            runtime_store.release(name)
        if self._patch is not None:
            self._patch.stop()
            self._patch = None


def stand_in(test, body: str = ANSWER, version: str = VERSION, runtime: str = "claude", children: bool = False,
             name: str = "claude") -> pathlib.Path:
    """A copy installed and promoted in a fresh temp store; the executable's path."""
    import runtime_store
    _state(test, children)
    runtime_store.install(runtime, version, Dist(body, name))
    return runtime_store.promote(runtime, version).exe
