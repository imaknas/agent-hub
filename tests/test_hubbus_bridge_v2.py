"""bridge v2: the bus's 0.3 records ↔ the assistant's /api/records (the assistant side's contract: records.v2.md).

Switched by mailbox.records (the same switch as send turning letters into records); when off, v2 is not touched at all.
The per-minute dispatch:
- Outbound: records addressed to the owner are POSTed in batches {"records": […]}, status checked per record; invalid
  is logged as job.failed and not retried; if the batch cannot be sent, all of it is resent next time.
- Inbound: per project GET ?project=&after=, written as record events, acked only after the commit; the first read
  only sets the starting point (what v1 already delivered is not taken again); a failed ack is made up next time;
  invalid ones (not sent by the assistant, wrong project) are skipped, not written; the pre-rename name is pulled too,
  written under the current name.
HTTP is faked; the real assistant is never touched.

    bin/py -m unittest discover -s tests
"""
import importlib.machinery
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TS = "2026-10-27T09:00:00+08:00"


def load():
    loader = importlib.machinery.SourceFileLoader("hubbus_v2", str(ROOT / "bin" / "hubbus"))
    spec = importlib.util.spec_from_loader("hubbus_v2", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def raised(key, project="proj"):
    return {"ph": "0.3", "ext": "acts/0.3", "id": f"r:{key}", "type": "raised", "origin": "requester",
            "by": f"project:{project}", "key": key, "project": project, "ts": TS, "terms": {"text": key}, "terms_rev": 1}


def settled(key, project="proj", **kw):
    return {"ph": "0.3", "ext": "acts/0.3", "id": f"s:{key}", "type": "settled", "origin": "principal", "by": "assistant",
            "key": key, "project": project, "ts": TS, "outcome": "done", "terms_rev": 1,
            "authority": {"kind": "principal", "decider": "person:owner"}, **kw}


class FakeAssistant:
    """Fake /api/records: out[project] holds the records the assistant has to give (cursor = the record's position,
    from 1)."""

    def __init__(self, test):
        self.t, self.calls, self.out, self.down, self.ack_down = test, [], {}, False, False
        self.received, self.invalid, self.unsupported = [], set(), set()

    def __call__(self, method, path, body=None, query=None):
        self.calls.append((method, path, body, query))
        if self.down:
            return None, None, "connection refused"
        if method == "POST" and path == "/api/records":
            self.received += body["records"]
            return 200, {"ok": True, "results": [
                {"id": r["id"], "status": "invalid", "errors": ["bad"]} if r["id"] in self.invalid
                else {"id": r["id"], "status": "unsupported"} if r["id"] in self.unsupported
                else {"id": r["id"], "status": "accepted"} for r in body["records"]]}, None
        if method == "GET" and path == "/api/records":
            recs = self.out.get(query["project"], [])
            after, limit = int(query["after"]), int(query.get("limit", 200))
            page = recs[after:after + limit]
            return 200, {"records": page, "cursor": after + len(page)}, None
        if method == "POST" and path == "/api/records/ack":
            if self.ack_down:
                return None, None, "timeout"
            # the ack always comes after the record is written to the bus (commit)
            n = self.t.m.records(self.t.reader(), body["project"] if body["project"] in ("proj", "other") else "proj")
            self.t.acked_seen.append((body["project"], body["through"], len(n)))
            return 200, {"ok": True, "through": body["through"]}, None
        return 404, None, "HTTP 404"

    def gets(self):
        return [q for meth, p, _, q in self.calls if meth == "GET"]

    def acks(self):
        return [b for meth, p, b, _ in self.calls if p == "/api/records/ack"]


class BridgeV2(unittest.TestCase):
    def setUp(self):
        m = self.m = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        m.DB = self.tmp / "bus.db"
        self.logs = []
        m.log = self.logs.append
        m.projects = lambda: {"proj": self.tmp, "other": self.tmp}
        m.RECORDS = True
        state = self.tmp / "state"
        state.mkdir()
        st = mock.patch.object(m.hubconf, "STATE", state)
        st.start()
        self.addCleanup(st.stop)
        self.state = state
        self.c = m.db()
        self.owner = m.hubconf.owner_address()
        self.fake = m.records_http = FakeAssistant(self)
        self.acked_seen = []

    def reader(self):
        import sqlite3
        c = sqlite3.connect(self.m.DB)   # another connection: sees only what is committed
        c.row_factory = sqlite3.Row
        return c

    def incoming(self, project="proj"):
        return [r["record"]["id"] for r in self.m.records(self.c, project, direction="in")]

    # --- outbound ---
    def test_outbound_posts_owner_records_once_and_marks_invalid(self):
        self.m.publish_record(self.c, raised("k1"), "proj", self.owner)
        self.m.publish_record(self.c, raised("k2"), "proj", self.owner)
        self.m.publish(self.c, "x1", "message", "proj", self.owner, None, {"type": "info", "text": "v1 ones do not go this way"})
        self.m.publish_record(self.c, settled("k9"), self.owner, "proj")   # inbound ones are not sent back
        self.fake.invalid = {"r:k2"}
        self.m.forward_records(self.c)
        self.assertEqual([r["id"] for r in self.fake.received], ["r:k1", "r:k2"])
        failed = self.c.execute("SELECT * FROM events WHERE type='job.failed'").fetchall()
        self.assertEqual(len(failed), 1)
        self.assertIn("r:k2", self.m.payload(failed[0])["detail"])
        self.m.forward_records(self.c)
        self.assertEqual(len(self.fake.received), 2)   # no resend, and invalid is not retried either

    def test_outbound_catches_up_a_long_bus_in_one_run(self):
        for i in range(1200):   # when the switch is turned on, the bus already holds lots of other events
            self.m.publish(self.c, f"n{i}", "message", "proj", "other", None, {"type": "info", "text": "x"})
        self.m.publish_record(self.c, raised("late"), "proj", self.owner)
        self.m.forward_records(self.c)
        self.assertEqual([r["id"] for r in self.fake.received], ["r:late"])

    def test_outbound_down_resends_next_time(self):
        self.m.publish_record(self.c, raised("k1"), "proj", self.owner)
        self.fake.down = True
        self.m.forward_records(self.c)
        self.fake.down = False
        self.m.forward_records(self.c)
        self.assertEqual([r["id"] for r in self.fake.received], ["r:k1"])

    # --- inbound ---
    def test_first_read_only_sets_the_starting_point(self):
        self.fake.out["proj"] = [settled(f"old{i}") for i in range(250)]   # already delivered by v1
        self.m.pull_records(self.c)
        self.assertEqual(self.incoming(), [])
        self.assertEqual(self.fake.acks(), [])
        self.fake.out["proj"].append(settled("new"))
        self.m.pull_records(self.c)
        self.assertEqual(self.incoming(), ["s:new"])
        # pulls on from the starting point
        self.assertIn("250", [q["after"] for q in self.fake.gets() if q["project"] == "proj"])

    def test_records_are_committed_before_ack_and_resend_is_noop(self):
        self.m.pull_records(self.c)   # starting point
        self.fake.out["proj"] = [settled("k1"), settled("k2")]
        self.m.pull_records(self.c)
        self.assertEqual(self.incoming(), ["s:k1", "s:k2"])
        self.assertIn(("proj", 2, 2), self.acked_seen)
        r = self.c.execute("SELECT * FROM events WHERE type='record' AND to_project='proj'").fetchone()
        self.assertEqual(self.m.canon(r["source"]), self.owner)
        self.m.pull_records(self.c)
        self.assertEqual(len(self.incoming()), 2)

    def test_failed_ack_is_retried(self):
        self.m.pull_records(self.c)
        self.fake.out["proj"] = [settled("k1")]
        self.fake.ack_down = True
        self.m.pull_records(self.c)
        self.fake.ack_down = False
        self.m.pull_records(self.c)
        self.assertEqual([b["through"] for b in self.fake.acks() if b["project"] == "proj"][-1], 1)
        self.assertEqual(self.incoming(), ["s:k1"])

    def test_bad_incoming_records_are_not_taken_nor_acked(self):
        """a record not taken in is never acked past: the page comes again (what was taken is deduplicated) until the
        reason is fixed, instead of being lost (the assistant's m11011416-bcb1)"""
        self.m.pull_records(self.c)
        self.fake.out["proj"] = [raised("forged"), settled("k1", project="other"), {"id": "junk"}, settled("ok")]
        self.m.pull_records(self.c)
        self.assertEqual(self.incoming(), ["s:ok"])
        self.assertEqual(self.incoming("other"), [])
        self.assertTrue(any("forged" in l or "r:forged" in l for l in self.logs))
        self.assertEqual([b for b in self.fake.acks() if b["project"] == "proj"], [], "nothing acked")
        self.m.pull_records(self.c)
        self.assertEqual(self.incoming(), ["s:ok"], "pulled again, taken once")
        self.assertEqual([q["after"] for q in self.fake.gets() if q["project"] == "proj"][-1], "0")

    def test_a_0_4_record_is_taken(self):
        self.m.pull_records(self.c)
        self.fake.out["proj"] = [settled("k1", ph="0.4", ext="acts/0.4", terms_digest="sha-256:" + "0" * 64)]
        self.m.pull_records(self.c)
        self.assertEqual(self.incoming(), ["s:k1"])
        self.assertEqual([b["through"] for b in self.fake.acks() if b["project"] == "proj"], [1])

    def test_a_record_of_a_version_not_read_is_kept_and_not_acted_on(self):
        """core 0.6 §4, §6: a record in a version brunna does not implement is taken in and kept (until 0.6 it was
        refused, and the page came again), so that it is held once brunna implements it; nothing acts on it"""
        self.m.pull_records(self.c)
        later = settled("k1", ph="0.9", ext="acts/0.9", terms_digest="sha-256:" + "0" * 64)
        self.fake.out["proj"] = [later]
        self.m.pull_records(self.c)
        self.assertEqual(self.incoming(), ["s:k1"])
        self.assertEqual([b["through"] for b in self.fake.acks() if b["project"] == "proj"], [1])
        [ev] = self.c.execute("SELECT * FROM events WHERE type='record'").fetchall()
        self.assertEqual(self.m.payload(ev)["record"], later, "kept as it came")
        self.assertEqual(self.m.fmt(ev).splitlines()[1].strip(),
                         self.m._("{ext} is a version brunna does not read: kept, not acted on", ext="acts/0.9"))

    def test_a_record_naming_a_member_twice_is_refused(self):
        """core §4: not I-JSON, and the bus could not keep it as it came"""
        self.m.pull_records(self.c)
        dup = self.m.ph_wire.loads(json.dumps(settled("k1"))[:-1] + ', "outcome": "dropped"}')
        self.assertIsInstance(dup, self.m.ph_wire.Duplicated)
        self.fake.out["proj"] = [dup]
        self.m.pull_records(self.c)
        self.assertEqual(self.incoming(), [])
        self.assertTrue(any("I-JSON" in l for l in self.logs))

    def test_a_version_the_assistant_does_not_read_keeps_the_batch(self):
        """the backstop for principal_side_reads confirmed too early: `unsupported` for a record in the version
        brunna sends lets nothing go; the batch is sent again until send_version names a version it reads"""
        self.m.publish_record(self.c, {**raised("k1"), "ph": "0.6", "ext": "acts/0.6"}, "proj", self.owner)
        self.fake.unsupported = {"r:k1"}
        with mock.patch.object(self.m.ph_wire, "send_version", return_value="0.6"):
            self.m.forward_records(self.c)
            self.m.forward_records(self.c)
        self.assertEqual([r["id"] for r in self.fake.received], ["r:k1", "r:k1"])
        self.assertTrue(any("0.6" in l and "r:k1" in l and "send_version" in l for l in self.logs))
        self.fake.unsupported = set()
        with mock.patch.object(self.m.ph_wire, "send_version", return_value="0.5"):   # the owner set 0.5 back
            self.m.forward_records(self.c)
            self.m.forward_records(self.c)
        self.assertEqual([(r["id"], r["ext"]) for r in self.fake.received],
                         [("r:k1", "acts/0.6")] * 2 + [("r:k1", "acts/0.5")], "sent once more, converted down")

    def test_starting_point_is_only_for_the_switch_on_not_for_later_names(self):
        self.m.pull_records(self.c)   # switch on: starting point
        # newbie: a project onboarded later
        self.m.projects = lambda: {"proj": self.tmp, "other": self.tmp, "newbie": self.tmp}
        self.fake.out["newbie"] = [settled("k1", project="newbie")]
        self.m.pull_records(self.c)
        self.assertEqual(self.incoming("newbie"), ["s:k1"])

    def test_renamed_projects_old_name_is_pulled_into_the_new_one(self):
        (self.state / "project-renames.json").write_text(json.dumps({"idea-9": {"new": "proj", "stage": "done"}}))
        self.m.pull_records(self.c)
        self.fake.out["idea-9"] = [settled("k1", project="idea-9")]
        self.m.pull_records(self.c)
        self.assertEqual(self.incoming(), ["s:k1"])

    def test_off_does_not_touch_v2(self):
        # only a deployment with the assistant pulls from the assistant
        side = mock.patch.object(self.m.hubconf, "owner_side", lambda: "assistant")
        side.start()
        self.addCleanup(side.stop)
        self.m.RECORDS = False
        self.m.run_handler = lambda *a: None
        self.m.import_handoffs = lambda c: None

        class RT:
            def live_sessions(self): return set()
            def context_size(self, sid): return None
        with mock.patch.dict("sys.modules", {"runtime_ports": mock.Mock(load=lambda *a, **k: RT())}):
            self.m.dispatch_locked(self.c, pull=True)
        self.assertEqual(self.fake.calls, [])
        self.m.RECORDS = True
        with mock.patch.dict("sys.modules", {"runtime_ports": mock.Mock(load=lambda *a, **k: RT())}):
            self.m.dispatch_locked(self.c, pull=True)
        self.assertTrue(self.fake.gets())


class Coexistence(unittest.TestCase):
    """v1 and v2 side by side: a handoff with a record goes only through v2 (once the assistant acks, v1 no longer
    lists it; v2 still gives again what v1 took first); v1 takes only those without a record (asks, no key,
    unrecognised). An answer (decisions settled) comes only as a record, back to the session that asked, with the
    question beside it."""

    HANDOFFS = [
        {"id": 70, "status": "pending", "effect": "done", "project": "proj", "reply_to": "task-proj-aaaaaaaaaa", "item": "x"},
        {"id": 71, "status": "pending", "effect": "relevant", "project": "proj", "fact": "abroad next week", "item": None},
        {"id": 72, "status": "pending", "effect": "asks", "project": "proj", "fact": "How far along is it?"},
        {"id": 73, "status": "pending", "effect": "answers", "project": "proj", "reply_to": "q1", "fact": "Pick A", "item": "Which one?"},
        {"id": 74, "status": "pending", "effect": "done", "project": "proj", "fact": "a closure without a key"},
        {"id": 75, "status": "pending", "effect": "something-new", "project": "proj", "fact": "?"},
    ]

    def setUp(self):
        m = self.m = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        m.DB = self.tmp / "bus.db"
        m.log = lambda s: None
        m.projects = lambda: {"proj": self.tmp, "other": self.tmp}
        m.ASSISTANT_CLI = ROOT / "bin" / "hubbus"
        state = self.tmp / "state"
        state.mkdir()
        st = mock.patch.object(m.hubconf, "STATE", state)
        st.start()
        self.addCleanup(st.stop)
        self.delivered = []

        def cli(*a):
            if a == ("--json",):
                return mock.Mock(returncode=0, stdout=json.dumps(self.HANDOFFS), stderr="")
            self.delivered += a[1:]
            return mock.Mock(returncode=0, stdout="", stderr="")
        m.assistant_cli = cli
        self.c = m.db()
        self.owner = m.hubconf.owner_address()
        # question q1 that proj asked the owner
        m.publish(self.c, "q1", "message", "proj", self.owner, None, {"type": "question", "text": "Which one?",
                                                                       "from_session": "sess-1"})

    def letters(self):
        return {r["id"]: self.m.payload(r) for r in self.c.execute("SELECT * FROM events WHERE type='message' AND id LIKE ?",
                                                                    (f"{self.m.HANDOFF_ID_PREFIX}%",))}

    def test_records_off_v1_takes_everything(self):
        self.m.RECORDS = False
        self.m.import_handoffs(self.c)
        self.assertEqual(len(self.letters()), 6)
        self.assertEqual(sorted(self.delivered), ["70", "71", "72", "73", "74", "75"])

    def test_records_on_v1_takes_only_what_has_no_record(self):
        self.m.RECORDS = True
        self.m.import_handoffs(self.c)
        p = self.m.HANDOFF_ID_PREFIX
        self.assertEqual(sorted(self.letters()), [f"{p}72", f"{p}74", f"{p}75"])
        self.assertEqual(sorted(self.delivered), ["72", "74", "75"])   # 70, 71, 73 left to v2's ack

    def answer_record(self, hid=73, **kw):
        return {"ph": "0.3", "ext": "decisions/0.3", "id": f"s:q1:h{hid}", "type": "settled", "origin": "principal",
                "by": "assistant", "key": "q1", "project": "proj", "ts": TS, "outcome": "decided", "terms_rev": 1,
                "answer": {"text": "Pick A"}, "authority": {"kind": "principal", "decider": "person:owner"}, **kw}

    def pull(self, *recs):
        self.m.RECORDS = True
        fake = self.m.records_http = FakeAssistant(self)
        self.acked_seen = []
        self.reader = lambda: self.c
        self.m.pull_records(self.c)   # starting point
        fake.out["proj"] = list(recs)
        self.m.pull_records(self.c)
        return self.c.execute("SELECT * FROM events WHERE type='record'").fetchall()

    def test_v2_answer_is_a_record_for_the_session_that_asked(self):
        [ev] = self.pull(self.answer_record())
        p = self.m.payload(ev)
        self.assertEqual((ev["to_project"], ev["ref"], p["to_session"], p["question"]), ("proj", "q1", "sess-1", "Which one?"))
        self.assertEqual(p["record"]["answer"]["text"], "Pick A")
        self.assertEqual(self.letters(), {})
        text = self.m.fmt(ev)
        self.assertIn("Which one?", text)
        self.assertIn(f"brunna done {ev['id']}", text)
        self.assertEqual(self.m.open_questions(self.c), [])
        self.m.import_handoffs(self.c)      # v1 lists the same answer: not taken
        self.assertNotIn(f"{self.m.HANDOFF_ID_PREFIX}73", self.letters())

    def test_v2_dismissal_closes_the_question_too(self):
        rec = self.answer_record(hid=80, outcome="dismissed", note="no answer needed")
        rec.pop("answer")
        [ev] = self.pull(rec)
        self.assertIn("no answer needed", self.m.fmt(ev))
        self.assertEqual(self.m.open_questions(self.c), [])


if __name__ == "__main__":
    unittest.main()
