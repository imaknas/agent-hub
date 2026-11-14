"""hubbus send turned into Principal Handoff 0.3 Acts records (stage 3, docs/design/principal-handoff-0.3-migration.md,
"direction one").

Switched by hub.json mailbox.records (off by default): when off, exactly as before (v1 letters); when on, a task/request
to the owner → raised, done --task → claimed, drop --task → withdrawn (no longer needed), and no v1 letter is sent.
--inferred is sent by the registrar itself (it has to count reopened), so send does not convert it and blocks it when
on. A question to the owner → Decisions raised (key = the letter id), done --question → claimed with the answer +
withdrawn (settled), drop --question → withdrawn (not needed); a question replying in an asks thread stays v1 (asks
is outside 0.3). info / progress stay v1.

    bin/py -m unittest discover -s tests
"""
import importlib.machinery
import importlib.util
import io
import pathlib
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
# what every kind of record must have (core + the acts schema's required)
REQUIRED = {"raised": ("terms", "terms_rev"), "claimed": ("outcome", "evidence"), "withdrawn": ("reason",)}


def load():
    loader = importlib.machinery.SourceFileLoader("hubbus_sendrec", str(ROOT / "bin" / "hubbus"))
    spec = importlib.util.spec_from_loader("hubbus_sendrec", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


class SendRecords(unittest.TestCase):
    def setUp(self):
        m = self.m = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        m.DB = self.tmp / "bus.db"
        m.log = lambda s: None
        m.projects = lambda: {"proj": self.tmp, "other": self.tmp}
        self.c = m.db()
        m.db = lambda *a, **k: self.c
        self.owner = m.hubconf.owner_address()

    def send(self, *argv, records=True):
        self.m.RECORDS = records
        with mock.patch.object(sys, "argv", ["hubbus", "send", "--from", "proj", "--to", self.owner, *argv]), \
                redirect_stdout(io.StringIO()) as out:
            self.m.main()
        return out.getvalue().strip()

    def events(self, etype):
        return [(r["id"], self.m.payload(r)) for r in self.c.execute("SELECT * FROM events WHERE type=? ORDER BY seq",
                                                                      (etype,))]

    def rec(self):
        recs = self.events("record")
        self.assertEqual(len(recs), 1, recs)
        r = recs[0][1]["record"]
        self.assertEqual(self.m.record_errors(r), [])
        self.assertRegex(r["ts"], TS)
        for k in REQUIRED[r["type"]]:
            self.assertIn(k, r)
        self.assertEqual((r["ph"], r["ext"], r["project"]), ("0.4", "acts/0.4", "proj"))
        return recs[0][0], r

    # --- switch off: same as before ---
    def test_off_is_v1_unchanged(self):
        mid = self.send("--type", "task", "--id", "task-proj-0000000001", "--text", "Pay the property tax", "--due", "2026-11-30",
                        records=False)
        self.assertEqual(mid, "task-proj-0000000001")
        msgs = self.events("message")
        self.assertEqual([(i, p["type"], p["text"], p["due"]) for i, p in msgs],
                         [("task-proj-0000000001", "task", "Pay the property tax", "2026-11-30")])
        self.assertEqual(self.events("record"), [])

    # --- switched on ---
    def test_task_with_key_becomes_raised(self):
        out = self.send("--type", "task", "--id", "task-proj-0000000001", "--text", "Pay the property tax", "--due", "2026-11-30",
                        "--context", "migration", "--new-reason", "last year's was another building")
        eid, r = self.rec()
        self.assertEqual(out, eid)
        self.assertEqual(eid, "rec:acts/0.4:proj:r:task-proj-0000000001")
        self.assertEqual((r["type"], r["id"], r["key"], r["origin"], r["by"], r["terms_rev"]),
                         ("raised", "r:task-proj-0000000001", "task-proj-0000000001", "requester", "project:proj", 1))
        self.assertEqual(r["terms"], {"text": "Pay the property tax", "due": "2026-11-30"})
        self.assertEqual((r["context"], r["note"]), ("migration", "last year's was another building"))
        self.assertEqual(self.events("message"), [])   # no more v1 letter

    def test_resend_same_key_is_noop(self):
        self.send("--type", "task", "--id", "task-proj-0000000001", "--text", "Pay the property tax")
        self.send("--type", "task", "--id", "task-proj-0000000001", "--text", "Pay the property tax")
        self.rec()

    def test_resend_with_other_terms_amends(self):
        """the same item (its key) asked differently now: amended, the terms_rev after the latest, superseding the
        current need record (core §6); the same terms again: nothing new"""
        k = "lease-x-1-task-0"
        self.send("--type", "task", "--id", k, "--text", "measure the desk")
        self.send("--type", "task", "--id", k, "--text", "measure the desk and the chair")
        self.send("--type", "task", "--id", k, "--text", "measure  the desk and the chair")
        self.send("--type", "task", "--id", k, "--text", "measure the desk and the chair", "--due", "2026-11-30")
        recs = [p["record"] for _i, p in self.events("record")]
        self.assertEqual([(r["type"], r["id"], r.get("terms_rev"), r.get("supersedes")) for r in recs],
                         [("raised", f"r:{k}", 1, None), ("amended", f"a:{k}:2", 2, [f"r:{k}"]),
                          ("amended", f"a:{k}:3", 3, [f"a:{k}:2"])])
        self.assertEqual(recs[2]["terms"], {"text": "measure the desk and the chair", "due": "2026-11-30"})
        for r in recs:
            self.assertEqual(self.m.record_errors(r), [])

    def test_drop_names_the_need_it_replaces(self):
        self.send("--type", "task", "--id", "task-proj-0000000001", "--text", "buy parts")
        self.send("--type", "drop", "--task", "task-proj-0000000001", "--text", "not needed")
        w = self.events("record")[-1][1]["record"]
        self.assertEqual((w["type"], w["supersedes"]), ("withdrawn", ["r:task-proj-0000000001"]))

    def test_task_without_key_is_not_the_registrars(self):
        """same text, same key, but never the registrar's `task-` prefix (acts 0.4 §6.1)"""
        self.assertEqual(self.m.task_key("proj", "  café-résumé "), self.m.task_key("proj", "café-résumé"))
        self.send("--type", "task", "--text", "  café-résumé ")
        _, r = self.rec()
        self.assertEqual(r["key"], self.m.task_key("proj", "café-résumé"))
        self.assertTrue(r["key"].startswith("ask-proj-"))
        self.assertEqual(r["terms"], {"text": "café-résumé"})

    def test_a_state_md_item_is_not_amended_from_a_session(self):
        """acts 0.4 §4: a key the registrar raised from a STATE.md line changes only through that line"""
        k = "task-proj-0123456789"
        r = self.m.hubconf.party("registrar")
        self.m.publish_record(self.c, {"ph": "0.3", "ext": "acts/0.3", "id": f"r:{k}", "type": "raised",
                                       "origin": "requester", "by": r, "key": k, "project": "proj",
                                       "ts": "2026-11-01T00:00:00Z", "terms": {"text": "renew"}, "terms_rev": 1},
                              "proj", self.owner)
        with self.assertRaises((SystemExit, self.m.Refused)):
            self.send("--type", "task", "--id", k, "--text", "renew for two years")
        self.assertEqual(len(self.events("record")), 1)
        self.send("--type", "task", "--id", k, "--text", "renew")   # the same terms: nothing new, no refusal
        self.assertEqual(len(self.events("record")), 1)

    def test_request_to_owner_raises_too(self):
        self.send("--type", "request", "--text", "Sign the contract for me")
        _, r = self.rec()
        self.assertEqual((r["type"], r["key"]), ("raised", self.m.task_key("proj", "Sign the contract for me")))

    def test_done_task_becomes_claimed(self):
        out = self.send("--type", "done", "--task", "task-proj-0000000001", "--text", "Paid already, receipt in docs/")
        eid, r = self.rec()
        self.assertEqual(out, eid)
        self.assertEqual((r["type"], r["key"], r["origin"], r["by"], r["outcome"]),
                         ("claimed", "task-proj-0000000001", "agent", "project:proj", "done"))
        self.assertEqual(r["evidence"], {"basis": "Paid already, receipt in docs/"})
        self.assertTrue(r["id"].startswith("c:task-proj-0000000001:"))
        self.assertNotRegex(r["id"], r":r\d+$")   # kept apart from the registrar's c:<key>:r<n>
        self.assertEqual(self.events("message"), [])

    def test_two_claims_get_distinct_ids(self):
        self.send("--type", "done", "--task", "task-proj-0000000001", "--text", "Done it")
        self.send("--type", "done", "--task", "task-proj-0000000001", "--text", "Really done it")
        self.assertEqual(len({i for i, _ in self.events("record")}), 2)

    def test_drop_task_becomes_withdrawn_not_needed(self):
        self.send("--type", "drop", "--task", "task-proj-0000000001", "--text", "Went another way, no need to buy it")
        _, r = self.rec()
        self.assertEqual((r["type"], r["key"], r["origin"], r["by"], r["reason"], r["note"]),
                         ("withdrawn", "task-proj-0000000001", "requester", "project:proj", "not_needed",
                          "Went another way, no need to buy it"))

    def test_inferred_is_the_registrars_and_is_rejected(self):
        with self.assertRaises(SystemExit) as e:
            self.send("--type", "done", "--task", "task-proj-0000000001", "--inferred", "--text", "deleted from STATE.md")
        self.assertIn("record-publish", str(e.exception.code))
        self.assertEqual(self.events("record") + self.events("message"), [])

    def test_info_and_progress_stay_v1(self):
        self.send("--type", "info", "--text", "deployed")
        self.send("--type", "progress", "--text", "halfway")
        self.assertEqual([p["type"] for _, p in self.events("message")], ["info", "progress"])
        self.assertEqual(self.events("record"), [])

    def test_done_without_task_to_owner_stays_v1(self):
        self.send("--type", "done", "--text", "that thing is done")
        self.assertEqual([p["type"] for _, p in self.events("message")], ["done"])

    def test_send_works_where_the_sandbox_forbids_ps(self):
        # the project's sandbox forbids ps: no view of the parent process = not work the platform sends on someone's
        # behalf, so send as usual (on 10-29 resume's send failed entirely because of this)
        real = self.m.subprocess.run
        def no_ps(cmd, *a, **k):
            if cmd and cmd[0] == "ps":
                raise PermissionError(1, "Operation not permitted", "ps")
            return real(cmd, *a, **k)
        with mock.patch.dict(self.m.os.environ, {}, clear=False), mock.patch.object(self.m.subprocess, "run", no_ps):
            self.m.os.environ.pop(self.m.PLATFORM_MAILS_ENV, None)
            self.assertIsNone(self.m.platform_mails())
            self.send("--type", "info", "--text", "deployed")
        self.assertEqual([p["type"] for _, p in self.events("message")], ["info"])

    def test_v1_forward_never_posts_records(self):
        self.send("--type", "task", "--text", "Pay the property tax")
        ev = self.c.execute("SELECT * FROM events WHERE type='record'").fetchone()
        self.assertIsNone(self.m.to_assistant(self.c, ev))


# what every Decisions record must have (core + the decisions schema's required, decisions/schemas/*.schema.json)
DEC_REQUIRED = {"raised": ("terms", "terms_rev"), "claimed": ("outcome", "evidence", "answer"), "withdrawn": ("reason",)}


class SendDecisions(unittest.TestCase):
    setUp, send, events = SendRecords.setUp, SendRecords.send, SendRecords.events

    def recs(self):
        out = []
        for _eid, p in self.events("record"):
            r = p["record"]
            self.assertEqual(self.m.record_errors(r), [])
            self.assertEqual((r["ph"], r["ext"], r["project"]), ("0.4", "decisions/0.4", "proj"))
            for k in DEC_REQUIRED[r["type"]]:
                self.assertIn(k, r)
            out.append(r)
        return out

    def ask(self, *extra):
        return self.send("--type", "question", "--id", "m1031-q1", "--text", "Which one?\n- A\n- B", *extra)

    def test_question_is_raised_keyed_by_the_letter_id(self):
        self.ask("--context", "lease:abc")
        self.assertEqual(self.events("message"), [])
        [r] = self.recs()
        self.assertEqual((r["type"], r["id"], r["key"], r["origin"], r["terms"], r["terms_rev"], r["context"]),
                         ("raised", "r:m1031-q1", "m1031-q1", "requester", {"text": "Which one?\n- A\n- B"}, 1, "lease:abc"))
        [q] = self.m.open_questions(self.c, "proj")
        self.assertEqual((q["id"], q["text"]), ("m1031-q1", "Which one?\n- A\n- B"))

    def test_done_question_claims_the_answer_then_withdraws(self):
        self.ask()
        self.send("--type", "done", "--question", "m1031-q1", "--text", "Pick A")
        _raised, claimed, withdrawn = self.recs()
        self.assertEqual((claimed["type"], claimed["origin"], claimed["outcome"], claimed["answer"]),
                         ("claimed", "agent", "decided", {"text": "Pick A"}))
        self.assertEqual((withdrawn["type"], withdrawn["origin"], withdrawn["reason"]), ("withdrawn", "requester", "settled"))
        self.assertEqual(withdrawn["supersedes"], ["r:m1031-q1"])
        self.assertEqual(self.m.open_questions(self.c), [])

    def test_drop_question_is_withdrawn_not_needed(self):
        self.ask()
        self.send("--type", "drop", "--question", "m1031-q1", "--text", "another way now")
        r = self.recs()[-1]
        self.assertEqual((r["type"], r["reason"], r["note"]), ("withdrawn", "not_needed", "another way now"))
        self.assertEqual(self.m.open_questions(self.c), [])

    def test_a_v1_question_is_closed_with_records_under_its_letter_id(self):
        self.m.publish(self.c, "m1029-old", "message", "proj", self.owner, None, {"type": "question", "text": "old one?"})
        self.send("--type", "drop", "--question", "m1029-old", "--text", "not needed")
        self.assertEqual(self.recs()[0]["key"], "m1029-old")
        self.assertEqual(self.m.open_questions(self.c), [])

    def test_only_this_projects_own_question(self):
        self.m.publish(self.c, "m-theirs", "message", "other", self.owner, None, {"type": "question", "text": "?"})
        with self.assertRaises(SystemExit):
            self.send("--type", "done", "--question", "m-theirs", "--text", "x")
        with self.assertRaises(SystemExit):
            self.send("--type", "done", "--question", "m-none", "--text", "x")

    def test_a_question_in_an_asks_thread_stays_v1(self):
        self.m.publish(self.c, "h-asks", "message", self.owner, "proj", None,
                       {"type": "question", "text": "how far along?", "handoff_id": 72, "effect": "asks"})
        self.send("--type", "question", "--reply-to", "h-asks", "--text", "which one do you mean?")
        self.assertEqual(self.events("record"), [])
        [(_mid, p)] = [e for e in self.events("message") if e[0] != "h-asks"]
        self.assertEqual((p["type"], p["handoff_id"]), ("question", 72))


class SendVersion(unittest.TestCase):
    """hub.json principal_handoff.send_version decides the version of what send makes (core §14)"""
    setUp, send, events = SendRecords.setUp, SendRecords.send, SendRecords.events

    def test_0_5(self):
        with mock.patch.object(self.m.ph_wire, "send_version", return_value="0.5"):
            self.send("--type", "task", "--id", "ask-proj-0000000001", "--text", "Pay the property tax")
            self.send("--type", "question", "--id", "m1031-q1", "--text", "Which one?")
        recs = [p["record"] for _eid, p in self.events("record")]
        self.assertEqual([(r["ph"], r["ext"]) for r in recs], [("0.5", "acts/0.5"), ("0.5", "decisions/0.5")])
        self.assertEqual([self.m.record_errors(r) for r in recs], [[], []])


if __name__ == "__main__":
    unittest.main()
