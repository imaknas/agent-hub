"""hub-event stops failing in silence (docs/design/runtime-versions.md section 6, "Hooks stop failing silently"): an input
that gives no event is logged as unusable=<reason> with its runtime, except an empty one, which onboard, recover and
sync-local-setup send on purpose; an event name hub-event doesn't know is logged too. Nothing else changes: no handler
runs and the output is empty."""
import json
import pathlib
import sys
import tempfile
import unittest

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_mailbox_wake as tmw  # noqa: E402


class Unusable(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.m = tmw.load_file("hub_event_unusable", BIN / "hub-event")
        self.m.LOG = self.tmp / "hub-event.log"

    def lines(self):
        return self.m.LOG.read_text().splitlines() if self.m.LOG.exists() else []

    def test_each_kind_of_input(self):
        for raw, want in (("", None),
                          ("   \n", None),
                          ("not json", "unusable=not_json runtime=claude"),
                          ("[1, 2]", "unusable=not_an_object runtime=claude"),
                          (json.dumps({"cwd": "/x"}), "unusable=no_session_id runtime=claude"),
                          (json.dumps({"session_id": 5, "cwd": "/x"}), "unusable=session_id_not_text runtime=claude"),
                          (json.dumps({"session_id": "s"}), "unusable=no_cwd runtime=claude"),
                          (json.dumps({"session_id": "s", "cwd": ["/x"]}), "unusable=cwd_not_text runtime=claude")):
            with self.subTest(raw=raw):
                before = len(self.lines())
                self.assertEqual(self.m.handle("prompt", raw, name="claude"), "")
                new = self.lines()[before:]
                if want is None:
                    self.assertEqual(new, [], "empty input is no session: nothing logged")
                else:
                    self.assertEqual(len(new), 1, new)
                    self.assertTrue(new[0].endswith(want), new[0])
                    self.assertIn(" prompt - ran=- failed=- ", new[0])

    def test_an_unknown_event_is_logged(self):
        raw = json.dumps({"session_id": "s", "cwd": "/x"})
        self.assertEqual(self.m.handle("no_such_event", raw, name="claude"), "")
        self.assertTrue(self.lines()[-1].endswith("no_such_event - ran=- failed=- unusable=unknown_event runtime=claude"))

    def test_a_tool_input_of_the_wrong_type_is_not_a_crash(self):
        import runtime_claude
        ev = runtime_claude.parse_event("tool_used", json.dumps({"session_id": "s", "cwd": "/x", "tool_input": "x"}))
        self.assertEqual((ev.session, ev.cwd, ev.command), ("s", "/x", ""))

    def test_the_worktree_entry_logs_too(self):
        self.m.worktree("start", "not json")
        self.assertTrue(self.lines()[-1].endswith("session_start - ran=- failed=- unusable=not_json runtime=claude"))
        before = len(self.lines())
        self.m.worktree("start", "")
        self.assertEqual(len(self.lines()), before)


if __name__ == "__main__":
    unittest.main()
