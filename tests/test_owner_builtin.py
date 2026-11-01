"""The built-in minimal owner side (bin/owner.py): closes, answers and notifies per Principal Handoff 0.3; another
implementation of the same interface as the assistant.

Isolated config + bus (real commands, real record validation); notifications use a stand-in channel.

    bin/py -m unittest discover -s tests
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "bin"))
import ph_wire  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"


class Builtin(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        for d in ("home", "config/projects", "data", "secrets", "bus"):
            (self.tmp / d).mkdir(parents=True)
        self.cfg = {"owner": {"name": "alex", "tag": "Alex", "principal_id": "person:alex"}, "owner_side": "builtin",
                    "bus_dir": str(self.tmp / "bus"), "registry_dir": "projects"}
        self.write_cfg()
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith("AGENT_HUB_") or k == "AGENT_HUB_PYTHON"},
                    "HOME": str(self.tmp / "home"), "AGENT_HUB_CONFIG": str(self.tmp / "config"),
                    "AGENT_HUB_DATA": str(self.tmp / "data"), "AGENT_HUB_SECRETS": str(self.tmp / "secrets")}

    def write_cfg(self):
        (self.tmp / "config" / "hub.json").write_text(json.dumps(self.cfg))

    def run_(self, script, *args, stdin=""):
        return subprocess.run([sys.executable, str(BIN / script), *args], input=stdin, env=self.env, cwd=self.tmp,
                              capture_output=True, text=True, timeout=120)

    def raise_task(self, key="task-agent-hub-0001", text="Renew café-résumé.example"):
        rec = {"ph": "0.3", "ext": "acts/0.3", "id": f"r:{key}", "type": "raised", "origin": "requester",
               "by": "agent-hub:registrar", "key": key, "project": "agent-hub", "ts": "2026-10-29T01:00:00Z",
               "terms": {"text": text}, "terms_rev": 1}
        r = self.run_("hubbus", "record-publish", "--json", "-", stdin=json.dumps(rec, ensure_ascii=False))
        self.assertEqual(r.returncode, 0, r.stderr)

    def records(self):
        r = self.run_("hubbus", "records", "--project", "agent-hub")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_list_then_settle(self):
        self.raise_task()
        r = self.run_("owner.py", "list")
        self.assertIn("task-agent-hub-0001", r.stdout)
        self.assertIn("Renew café-résumé.example", r.stdout)
        r = self.run_("owner.py", "done", "task-agent-hub-0001", "--note", "renewed on Friday")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        settled = [e for e in self.records() if e["record"]["type"] == "settled"]
        self.assertEqual(len(settled), 1)
        rec = settled[0]["record"]
        self.assertEqual(settled[0]["direction"], "in",
                         "from the owner side: the same direction as what the assistant sends")
        self.assertEqual((rec["origin"], rec["outcome"], rec["authority"]),
                         ("principal", "done", {"kind": "principal", "decider": "person:alex"}))
        self.assertEqual(rec["by"], "agent-hub:owner-side")
        need = next(e["record"] for e in self.records() if e["record"]["type"] == "raised")
        self.assertEqual(rec["terms_digest"], ph_wire.terms_digest(need, "0.4"), "core §4: what it was settled against, as 0.4 digests it")
        self.assertEqual((rec["ph"], rec["ext"]), ("0.4", "acts/0.4"), "hub.json principal_handoff.send_version, 0.4 when not set")
        self.assertNotIn("task-agent-hub-0001", self.run_("owner.py", "list").stdout, "once closed, no longer listed")

    def test_resettle_supersedes_the_earlier_one(self):
        self.raise_task()
        self.assertEqual(self.run_("owner.py", "done", "task-agent-hub-0001").returncode, 0)
        self.assertEqual(self.run_("owner.py", "drop", "task-agent-hub-0001").returncode, 0)
        recs = [e["record"] for e in self.records() if e["record"]["type"] == "settled"]
        self.assertEqual(recs[1]["supersedes"], [recs[0]["id"]])
        self.assertEqual(recs[1]["outcome"], "dropped")

    def test_unknown_key(self):
        r = self.run_("owner.py", "done", "task-nope")
        self.assertEqual(r.returncode, 1)

    def test_assistant_side_only_lists(self):
        self.cfg["owner_side"] = "assistant"
        self.write_cfg()
        self.raise_task()
        self.assertEqual(self.run_("owner.py", "done", "task-agent-hub-0001").returncode, 2)
        self.assertEqual(self.run_("owner.py", "list").returncode, 0)

    def test_answer_closes_the_question(self):
        r = self.run_("hubbus", "send", "--from", "agent-hub", "--to", "owner", "--type", "question", "--text", "Which cache?")
        self.assertEqual(r.returncode, 0, r.stderr)
        qid = r.stdout.strip().split()[-1]
        self.assertIn("Which cache", self.run_("owner.py", "list").stdout)
        r = self.run_("owner.py", "answer", qid, "Use SQLite")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("Which cache", self.run_("owner.py", "list").stdout)
        inbox = self.run_("hubbus", "inbox", "--project", "agent-hub").stdout
        self.assertIn("Use SQLite", inbox)

    def test_answer_with_records_on(self):
        self.cfg["mailbox"] = {"records": True}
        self.write_cfg()
        self.test_answer_closes_the_question()
        [ans] = [e for e in self.records() if e["record"]["type"] == "settled"]
        self.assertEqual((ans["record"]["ext"], ans["record"]["answer"], ans["question"]),
                         ("decisions/0.4", {"text": "Use SQLite"}, "Which cache?"))
        self.assertTrue(ans["record"]["terms_digest"].startswith("sha-256:"))


class Notify(unittest.TestCase):
    def test_only_new_items_and_failures_are_recorded(self):
        spec = importlib.util.spec_from_file_location("owner_t", BIN / "owner.py")
        owner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(owner)
        import alert
        tmp = pathlib.Path(tempfile.mkdtemp())
        sent, failed = [], []
        items = [{"kind": "task", "project": "p", "key": "k1", "text": "Do A"}]
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(owner, "SEEN", tmp / "seen.json"), \
             mock.patch.object(owner, "open_items", lambda only=None: list(items)), \
             mock.patch.object(alert, "configured", lambda: ["ntfy"]), \
             mock.patch.object(alert, "send", lambda text: sent.append(text) or True), \
             mock.patch.object(alert, "clear_failure", lambda k: None), \
             mock.patch.object(alert, "record_failure", lambda k, t: failed.append(k)):
            self.assertEqual(owner.notify(), 0)
            self.assertEqual(owner.notify(), 0)
            self.assertEqual(len(sent), 1, "the same item is not notified twice")
            items.append({"kind": "question", "project": "p", "id": "q1", "text": "Which one?"})
            owner.notify()
            self.assertEqual(len(sent), 2)
            self.assertIn("Which one", sent[1])
            self.assertNotIn("Do A", sent[1])
            items.append({"kind": "task", "project": "p", "key": "k2", "text": "Do B"})
            with mock.patch.object(alert, "send", lambda text: False):
                self.assertEqual(owner.notify(), 1)
            self.assertEqual(failed, ["owner-notify"])

    def test_no_channel_at_all(self):
        """a newcomer without alerts set up"""
        spec = importlib.util.spec_from_file_location("owner_t2", BIN / "owner.py")
        owner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(owner)
        import alert
        tmp = pathlib.Path(tempfile.mkdtemp())
        failed = []
        items = [{"kind": "task", "project": "p", "key": "k1", "text": "Do A"}]
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
             mock.patch.object(owner, "SEEN", tmp / "seen.json"), \
             mock.patch.object(owner, "open_items", lambda only=None: list(items)), \
             mock.patch.object(alert, "configured", lambda: []), \
             mock.patch.object(alert, "send", lambda text: False), \
             mock.patch.object(alert, "clear_failure", lambda k: None), \
             mock.patch.object(alert, "record_failure", lambda k, t: failed.append(k)):
            self.assertEqual(owner.notify(), 0)
        self.assertEqual(failed, [], "nothing to send on is not a failed send")
        self.assertFalse((tmp / "seen.json").exists(), "the items stay new for a channel set up later")


if __name__ == "__main__":
    unittest.main()
