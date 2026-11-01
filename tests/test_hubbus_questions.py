"""Questions a project asks the owner: once resolved in the project's session they can be closed (--question); open
ones get reminders and periodic reconciliation (m10252237).
A temporary bus database; the project list is faked; nothing is sent to the assistant.

    bin/py -m unittest discover -s tests
"""
import datetime
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def load_hubbus():
    loader = importlib.machinery.SourceFileLoader("hubbus_q", str(ROOT / "bin" / "hubbus"))
    spec = importlib.util.spec_from_loader("hubbus_q", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


class Questions(unittest.TestCase):
    def setUp(self):
        m = self.m = load_hubbus()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        m.DB = self.tmp / "bus.db"
        m.log = lambda msg: None
        m.RECORDS = False   # v1 letters (an install without mailbox.records); the records side is QuestionRecords
        m.projects = lambda: {"proj": self.tmp / "proj", "other": self.tmp / "other", "agent-hub": self.tmp}
        # hubconf is a shared module: always swap it back
        hp = mock.patch.object(m.hubconf, "hub_project", lambda: "agent-hub")
        hp.start()
        self.addCleanup(hp.stop)
        self.c = m.db()
        self.owner = m.OWNER_ADDRESS

    def ask(self, qid, project="proj", days_ago=5, text="Which plan should we use?", kind="question"):
        ts = (NOW - datetime.timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
        self.c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                       (qid, ts, "message", project, self.owner, None, json.dumps({"type": kind, "text": text})))

    def open_ids(self, project=None):
        return [r["id"] for r in self.m.open_questions(self.c, project)]

    def send(self, *argv):
        with mock.patch.object(sys, "argv", ["hubbus", "send", "--from", "proj", *argv]), \
                redirect_stdout(io.StringIO()) as out:
            self.m.main()
        return out.getvalue().strip()

    def test_open_means_not_answered_not_closed_not_a_handover(self):
        self.ask("q-open")
        self.ask("q-answered")
        self.m.publish(self.c, "ans-1", "message", self.owner, "proj", "q-answered", {"type": "info", "text": "Use A"})
        self.ask("lease-abc-q-0")          # a question from a discussion handover: the assistant handles it
        self.ask("p-1", kind="progress")   # not a question
        self.ask("q-other", project="other")
        self.assertEqual(self.open_ids(), ["q-open", "q-other"])
        self.assertEqual(self.open_ids("proj"), ["q-open"])

    def test_question_closed_with_done_and_forwarded(self):
        self.ask("q-1")
        mid = self.send("--to", "owner", "--type", "done", "--question", "q-1", "--text", "picked A in a session")
        self.assertEqual(self.open_ids(), [])
        ev = self.c.execute("SELECT * FROM events WHERE id=?", (mid,)).fetchone()
        item = self.m.to_assistant(self.c, ev)
        self.assertEqual((item["type"], item["question"], item["project"]), ("done", "q-1", "proj"))

    def test_drop_with_question_is_allowed(self):
        self.ask("q-1")
        self.send("--to", "owner", "--type", "drop", "--question", "q-1", "--text", "not needed any more")
        self.assertEqual(self.open_ids(), [])

    def test_question_must_be_this_projects_question_to_knas(self):
        self.ask("q-theirs", project="other")
        self.ask("p-1", kind="progress")
        for argv in (("--to", "owner", "--type", "done", "--question", "q-theirs", "--text", "x"),
                     ("--to", "owner", "--type", "done", "--question", "p-1", "--text", "x"),
                     ("--to", "owner", "--type", "info", "--question", "q-theirs", "--text", "x"),
                     ("--to", "owner", "--type", "done", "--question", "q-theirs", "--task", "k", "--text", "x")):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                self.send(*argv)
        self.assertEqual(sorted(self.open_ids()), ["q-theirs"])

    def test_session_start_note_lists_open_questions(self):
        self.ask("q-1", text="Run the pilot or not?")
        note = self.m.questions_note(self.c, "proj")
        self.assertIn("q-1", note)
        self.assertIn("--question <id>", note)
        self.assertIsNone(self.m.questions_note(self.c, "other"))

    def test_reconcile_asks_once_per_week_after_three_days(self):
        self.ask("q-new", days_ago=1)
        self.ask("q-old", days_ago=4)
        self.ask("lease-x-q-1", days_ago=20)
        sent = self.m.reconcile_questions(self.c, now=NOW)
        self.assertEqual(sent, ["qcheck-q-old-0"])
        letter = self.c.execute("SELECT * FROM events WHERE id='qcheck-q-old-0'").fetchone()
        self.assertEqual((letter["source"], letter["to_project"], self.m.payload(letter)["type"]),
                         ("agent-hub", "proj", "request"))
        self.assertIn("--question q-old", self.m.payload(letter)["text"])
        self.assertEqual(self.m.reconcile_questions(self.c, now=NOW), [], "a rerun in the same week does not resend")
        later = NOW + datetime.timedelta(days=7)
        self.assertEqual(self.m.reconcile_questions(self.c, now=later), ["qcheck-q-new-0", "qcheck-q-old-1"])

    def test_reconcile_skips_closed_and_unknown_projects(self):
        self.ask("q-gone", project="renamed-away")
        self.ask("q-closed")
        self.send("--to", "owner", "--type", "done", "--question", "q-closed", "--text", "solved")
        self.assertEqual(self.m.reconcile_questions(self.c, now=NOW), [])


class QuestionRecords(Questions):
    """The same readers over Decisions records (mailbox.records on): asked as raised, closed by the asking project's
    claimed/withdrawn or the owner's settled, opened again by reopened."""

    def setUp(self):
        super().setUp()
        self.m.RECORDS = True

    def ask(self, qid, project="proj", days_ago=5, text="Which plan?", kind="question"):
        if kind != "question":
            return super().ask(qid, project, days_ago, text, kind)
        ts = (NOW - datetime.timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
        rec = {"ph": "0.3", "ext": "decisions/0.3", "id": f"r:{qid}", "type": "raised", "origin": "requester",
               "by": f"project:{project}", "key": qid, "project": project, "ts": ts, "terms": {"text": text}, "terms_rev": 1}
        self.m.publish_record(self.c, rec, project, self.owner)
        self.c.execute("UPDATE events SET ts=? WHERE id=?", (ts, self.m.record_event_id(rec)))

    def settle(self, qid, kind="settled", n=1):
        rec = {"ph": "0.3", "ext": "decisions/0.3", "id": f"{kind[0]}:{qid}:{n}", "type": kind, "origin": "principal",
               "by": "assistant", "key": qid, "project": "proj", "ts": "2026-10-25T12:00:00Z", "outcome": "decided",
               "answer": {"text": "use A"}, "terms_rev": 1, "authority": {"kind": "principal", "decider": "person:owner"}}
        self.m.publish_record(self.c, rec, self.owner, "proj")

    def test_open_means_not_answered_not_closed_not_a_handover(self):
        self.ask("q-open")
        self.ask("q-answered")
        self.settle("q-answered")
        self.ask("lease-abc-q-0")
        self.ask("q-other", project="other")
        self.assertEqual(self.open_ids(), ["q-open", "q-other"])
        self.assertEqual(self.open_ids("proj"), ["q-open"])

    def test_reopened_is_open_again(self):
        self.ask("q-1")
        self.settle("q-1")
        self.assertEqual(self.open_ids(), [])
        self.settle("q-1", "reopened", 2)
        self.assertEqual(self.open_ids(), ["q-1"])

    def test_question_closed_with_done_and_forwarded(self):
        self.ask("q-1")
        self.send("--to", "owner", "--type", "done", "--question", "q-1", "--text", "picked A in a session")
        self.assertEqual(self.open_ids(), [])
        kinds = [self.m.payload(r)["record"]["type"] for r in self.c.execute(
            "SELECT * FROM events WHERE type='record' ORDER BY seq")]
        self.assertEqual(kinds, ["raised", "claimed", "withdrawn"])


if __name__ == "__main__":
    unittest.main()
