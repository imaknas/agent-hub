"""bin/hub-event: the platform's event entry point (layer 3 of the runtime abstraction). A fake runtime + stand-in
handlers; real projects and the bus are never touched.

    bin/py -m unittest discover -s tests
"""
import importlib.machinery
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import runtime_fake  # noqa: E402


def load_hub_event():
    loader = importlib.machinery.SourceFileLoader("hub_event", str(ROOT / "bin" / "hub-event"))
    spec = importlib.util.spec_from_loader("hub_event", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


class HubEvent(unittest.TestCase):
    def setUp(self):
        self.m = load_hub_event()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.m.LOG = self.tmp / "hub-event.log"
        self.m._workspace = lambda cwd: self.tmp
        # these handlers are stand-ins: the rule has tests/test_worktree_pointer.py
        p = mock.patch.object(self.m.project_git, "scope_of", lambda folder: self.m.project_git.Scope(True, True, folder / ".agent-memory"))
        p.start()
        self.addCleanup(p.stop)
        self.calls = []

    def test_a_session_is_on_record_before_its_mail_is_told(self):
        """Told before it is recorded, a session with no process record could be judged closed by the tick in between."""
        for event in ("session_start", "prompt", "tool_used"):
            order = [n for n, _f in self.m.HANDLERS[event]]
            self.assertLess(order.index("session_record"), order.index("mail"), event)

    def test_a_session_ending_tells_the_bus(self):
        self.assertIn("mail_end", [n for n, _f in self.m.HANDLERS["session_end"]])

    def raw(self, **extra):
        return json.dumps({"sid": "S1", "dir": str(self.tmp), **extra})

    def stub(self, name, out=None, boom=False):
        def fn(ev, ws):
            self.calls.append((name, ev.event))
            if boom:
                raise RuntimeError("boom")
            return out
        return fn

    def test_texts_from_several_handlers_are_combined(self):
        self.m.HANDLERS["tool_used"] = [("nudge", self.stub("nudge", "write your notes")), ("mail", self.stub("mail", "you have mail"))]
        out = json.loads(self.m.handle("tool_used", self.raw(cmd="git commit"), runtime_fake))
        self.assertEqual(out["say"], "write your notes\n\nyou have mail")

    def test_one_failure_does_not_stop_the_rest_and_is_logged(self):
        self.m.HANDLERS["session_start"] = [("memory_pull", self.stub("memory_pull", boom=True)),
                                            ("mail", self.stub("mail", "you have mail"))]
        out = json.loads(self.m.handle("session_start", self.raw(), runtime_fake))
        self.assertEqual(out["say"], "you have mail")
        log = self.m.LOG.read_text()
        self.assertIn("session_start", log)
        self.assertIn("failed=memory_pull:RuntimeError", log)

    def test_deny_wins(self):
        self.m.HANDLERS["open_session_attempt"] = [("guard", self.stub("guard", "that is another project"))]
        out = json.loads(self.m.handle("open_session_attempt", self.raw(where="/x"), runtime_fake))
        self.assertEqual(out["block"], "that is another project")

    def test_not_a_session_does_nothing(self):
        self.m.HANDLERS["prompt"] = [("mail", self.stub("mail", "you have mail"))]
        self.assertEqual(self.m.handle("prompt", json.dumps({"dir": "/x"}), runtime_fake), "")
        self.assertEqual(self.calls, [])

    def test_unknown_event_does_nothing(self):
        self.assertEqual(self.m.handle("nope", self.raw(), runtime_fake), "")

    def test_no_output_when_handlers_have_nothing(self):
        self.m.HANDLERS["prompt"] = [("mail", self.stub("mail", None))]
        self.assertEqual(self.m.handle("prompt", self.raw(), runtime_fake), "")

    def test_main_always_exits_zero(self):
        self.m.handle = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        self.assertEqual(self.m.main(["prompt"]), 0)

    def caged(self, as_project):
        """A sandboxed project's folder: the AS_PROJECT handlers come back from its sandboxed run (as_project)."""
        ws = self.tmp.resolve() / "box"
        (ws / ".agent-memory").mkdir(parents=True)
        self.m._workspace = lambda cwd: pathlib.Path(cwd)
        self.m.HANDLERS["tool_used"] = [(n, f) if n == "nudge" else (n, self.stub(n)) for n, f in self.m.HANDLERS["tool_used"]]
        for p in (mock.patch.object(self.m.project_git, "sandbox_of", lambda cwd: ("box", {"name": "box"})),
                  mock.patch.object(self.m.project_git, "holds", lambda doc, folder: True),
                  mock.patch.object(self.m.runtime_ports, "capabilities", lambda name: {"control", "confine"}),
                  mock.patch.object(self.m, "_as_project", as_project)):
            p.start()
            self.addCleanup(p.stop)
        return ws

    def tool_used(self, cwd, cmd):
        out = self.m.handle("tool_used", json.dumps({"sid": "S1", "dir": str(cwd), "cmd": cmd}), runtime_fake)
        return out, self.m.LOG.read_text().splitlines()[-1]

    def test_a_sandboxed_projects_tool_call_with_nothing_to_nudge_is_no_failure(self):
        """Seen 11-02..11-07 as failed=nudge:RuntimeError: the sandboxed run is only started for a git commit/push, and
        no answer from a run that wasn't started read as a failed one. Its folder or its memory checkout alike."""
        ws = self.caged(mock.Mock(side_effect=AssertionError("started for a call with nothing to nudge")))
        for cwd in (ws, ws / ".agent-memory"):
            with self.subTest(cwd=cwd.name):
                out, line = self.tool_used(cwd, "ls -la")
                self.assertEqual(out, "")
                self.assertIn("failed=-", line)
                self.assertIn("nudge", line.split("ran=")[1].split()[0])

    def test_a_sandboxed_projects_commit_is_nudged_as_the_project(self):
        ws = self.caged(lambda project, ws, ev: {"nudge": {"ok": True, "text": "write your notes"}})
        out, line = self.tool_used(ws, "git commit -m x")
        self.assertEqual(json.loads(out)["say"], "write your notes")
        self.assertIn("failed=-", line)

    def test_a_sandboxed_run_that_failed_still_says_so(self):
        ws = self.caged(lambda project, ws, ev: {"": {"error": "exit 78"}})
        out, line = self.tool_used(ws, "git commit -m x")
        self.assertIn("failed=nudge:RuntimeError", line)

    def test_every_platform_event_has_handlers(self):
        import runtime_ports
        self.assertEqual(set(self.m.HANDLERS), set(runtime_ports.EVENTS))


class WorktreeHooksNameTheirRuntime(unittest.TestCase):
    """worktree-memory.sh's hooks are Claude Code's (runtime_claude.worktree_hooks): with runtime.default set to another
    runtime they still read their input with Claude's adapter, never the default's (docs/design/runtime-codex.md, step 0)."""

    def setUp(self):
        self.m = load_hub_event()
        self.m.LOG = pathlib.Path(tempfile.mkdtemp()) / "hub-event.log"
        self.seen = []

        def load(name=None, ports=None):
            self.seen.append(name)
            raise self.m.runtime_ports.Unsupported("stand-in")
        patches = [mock.patch.object(self.m.runtime_ports, "load", load),
                   mock.patch.object(self.m.runtime_ports, "default_name", lambda: "fake"),
                   mock.patch.object(self.m, "_read_stdin", lambda: "{}"),
                   mock.patch.object(self.m.project_git, "worktree_scope", lambda top, main, cwd: 3)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_worktree_events(self):
        for what in ("start", "prompt", "nudge", "end"):
            self.seen.clear()
            self.assertEqual(self.m.main(["--runtime", "claude", "worktree", what]), 0)
            self.assertEqual(self.seen, ["claude"], what)

    def test_worktree_scope(self):
        self.assertEqual(self.m.main(["--runtime", "claude", "worktree-scope", "/top", "/main"]), 3)
        self.assertEqual(self.seen, ["claude"])

    def test_wait(self):
        with mock.patch.object(self.m, "platform_run", lambda env: False):
            self.assertEqual(self.m.main(["--runtime", "claude", "wait"]), 0)
        self.assertEqual(self.seen, ["claude"])

    def test_without_it_claudes_never_the_default(self):
        """Every hook written before hooks named their runtime is Claude Code's: runtime.default (here fake) never takes one."""
        with mock.patch.object(self.m, "platform_run", lambda env: False):
            for argv in (["worktree", "start"], ["worktree-scope", "/top", "/main"], ["wait"], ["prompt"]):
                self.seen.clear()
                self.m.main(argv)
                self.assertEqual(self.seen, ["claude"], argv)

    def test_the_script_names_it_on_every_call(self):
        """Every hub-event and hub-runtime call in worktree-memory.sh that reads a runtime's input or files names claude
        (local-ignores goes over every installed runtime, so it names none)."""
        calls = [line for line in (ROOT / "bin" / "worktree-memory.sh").read_text().splitlines()
                 if not line.lstrip().startswith("#") and ('bin/hub-event"' in line or 'bin/hub-runtime"' in line)]
        self.assertGreaterEqual(len(calls), 5)
        for line in calls:
            if "local-ignores" in line:
                continue
            self.assertRegex(line, r'bin/hub-(event|runtime)" --runtime claude ', line.strip())


class HubRuntimeNamesItsRuntime(unittest.TestCase):
    def test_the_rules_file_of_the_runtime_asked(self):
        loader = importlib.machinery.SourceFileLoader("hub_runtime", str(ROOT / "bin" / "hub-runtime"))
        hr = importlib.util.module_from_spec(importlib.util.spec_from_loader("hub_runtime", loader))
        loader.exec_module(hr)
        import io
        import runtime_claude
        ws = tempfile.mkdtemp()
        out = io.StringIO()
        with mock.patch.object(hr.runtime_ports, "default_name", lambda: "fake"), mock.patch("sys.stdout", out):
            self.assertEqual(hr.main(["--runtime", "claude", "instructions-file", ws]), 0)
        self.assertEqual(out.getvalue().strip(), str(runtime_claude.instructions_file(pathlib.Path(ws).resolve())
                                                     .relative_to(pathlib.Path(ws).resolve())))


if __name__ == "__main__":
    unittest.main()
