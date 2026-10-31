"""lease-relay.py's work mode (phase two): writes outside the folder are refused without asking, writes inside are
allowed directly (decided by the owner on 10-25), commands are asked about every time.

Sending to the phone (ask_owner) and reporting to the runner (tell_runner) are both faked; no request is made.

    bin/py -m unittest discover -s tests
"""
import importlib.util
import os
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_relay():
    spec = importlib.util.spec_from_file_location("lease_relay", ROOT / "bin" / "lease-relay.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class WorkMode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = os.path.realpath(os.path.join(self.tmp, "idea-1"))
        os.makedirs(self.ws)
        self.m = load_relay()
        self.m.MODE, self.m.WS = "work", self.ws
        self.asked: list[tuple[str, str]] = []
        self.answer: dict = {"decision": "allow"}
        self.m.ask_owner = lambda rid, tool, summary, risk, q: (self.asked.append((tool, risk)), self.answer)[1]
        self.m.tell_runner = lambda *a, **k: None

    def call(self, tool, **inp):
        return self.m.decide({"tool_name": tool, "input": inp, "tool_use_id": "t1"})

    def test_write_inside_allowed_without_asking(self):
        r = self.call("Write", file_path=os.path.join(self.ws, "notes.md"), content="x")
        self.assertEqual(r["behavior"], "allow")
        self.assertEqual(self.asked, [], "a write inside the folder is not sent to ask")

    def test_relative_path_resolves_against_workspace(self):
        r = self.call("Edit", file_path="src/a.py", old_string="a", new_string="b")
        self.assertEqual(r["behavior"], "allow")

    def test_outside_denied_without_asking(self):
        for p in ("/etc/hosts", os.path.join(self.ws, "..", "other", "x"), os.path.expanduser("~/.zshrc")):
            r = self.call("Write", file_path=p, content="x")
            self.assertEqual(r["behavior"], "deny", p)
        self.assertEqual(self.asked, [], "outside the folder must not be sent to ask")

    def test_symlink_pointing_out_denied(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        os.symlink(outside, os.path.join(self.ws, "link"))
        r = self.call("Write", file_path=os.path.join(self.ws, "link", "x"), content="x")
        self.assertEqual(r["behavior"], "deny")
        self.assertEqual(self.asked, [])


    def test_bash_asks_every_time_even_after_allow_session(self):
        self.answer = {"decision": "allow_session"}
        self.assertEqual(self.call("Bash", command="ls")["behavior"], "allow")
        self.call("Bash", command="ls")
        self.assertEqual(self.asked, [("Bash", "run_command"), ("Bash", "run_command")])

    def test_bash_deny_and_timeout(self):
        self.answer = {"decision": "deny"}
        self.assertEqual(self.call("Bash", command="make")["behavior"], "deny")
        self.answer = {}
        self.assertEqual(self.call("Bash", command="rm -rf build")["behavior"], "deny")

    def test_discuss_mode_unchanged(self):
        self.m.MODE = "discuss"
        r = self.call("Write", file_path=os.path.join(self.ws, "a"), content="x")
        self.assertEqual(r["behavior"], "deny")
        self.assertEqual(self.call("Bash", command="ls")["behavior"], "deny")
        self.assertEqual(self.asked, [])


if __name__ == "__main__":
    unittest.main()
