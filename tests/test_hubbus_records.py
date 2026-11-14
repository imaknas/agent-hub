"""Principal Handoff 0.3 records on the bus (stage 3, docs/design/principal-handoff-0.3-migration.md).

Event type record, id rec:<ext>:<project>:<record id> (a resend is a no-op); `hubbus records` is for the registrar to
query; `record-publish` is for programs to send; done works on records too; the inbox shows records from the assistant
(who decided, under what authority); dispatch does not treat a record as a letter to dispatch someone for.

    bin/py -m unittest discover -s tests
"""
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TS = "2026-10-27T09:00:00+08:00"


def load():
    loader = importlib.machinery.SourceFileLoader("hubbus_rec", str(ROOT / "bin" / "hubbus"))
    spec = importlib.util.spec_from_loader("hubbus_rec", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def settled(key="abc123", rid="s:abc123:1"):
    return {"ph": "0.3", "ext": "acts/0.3", "id": rid, "type": "settled", "origin": "principal", "by": "assistant",
            "key": key, "project": "proj", "ts": TS, "outcome": "done", "terms_rev": 1,
            "authority": {"kind": "principal", "decider": "person:owner"}}


def raised(key="abc123"):
    return {"ph": "0.3", "ext": "acts/0.3", "id": f"r:{key}", "type": "raised", "origin": "requester", "by": "project:proj",
            "key": key, "project": "proj", "ts": TS, "terms": {"text": "Pay the property tax"}, "terms_rev": 1}


class Records(unittest.TestCase):
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

    def cli(self, *argv, stdin=""):
        with mock.patch.object(sys, "argv", ["hubbus", *argv]), mock.patch.object(sys, "stdin", io.StringIO(stdin)), \
                redirect_stdout(io.StringIO()) as out:
            self.m.main()
        return out.getvalue().strip()

    def test_event_id_and_resend_is_noop(self):
        eid, new = self.m.publish_record(self.c, settled(), self.owner, "proj")
        self.assertEqual(eid, "rec:acts/0.3:proj:s:abc123:1")
        self.assertTrue(new)
        self.assertFalse(self.m.publish_record(self.c, settled(), self.owner, "proj")[1])

    def test_records_query_filters_and_pending(self):
        self.m.publish_record(self.c, settled("k1", "s:k1:1"), self.owner, "proj")
        self.m.publish_record(self.c, {**settled("k2", "o:k2:1"), "type": "reopened", "supersedes": ["s:k2:1"],
                                       "restore_text": "x"}, self.owner, "proj")
        self.m.publish_record(self.c, settled("k3", "s:k3:1"), self.owner, "other")
        out = json.loads(self.cli("records", "--project", "proj", "--type", "settled,reopened", "--pending"))
        self.assertEqual([o["record"]["key"] for o in out], ["k1", "k2"])
        self.cli("done", out[0]["id"])
        out = json.loads(self.cli("records", "--project", "proj", "--pending"))
        self.assertEqual([o["record"]["key"] for o in out], ["k2"])
        done = json.loads(self.cli("records", "--project", "proj"))
        self.assertEqual([o["done"] for o in done], [True, False])

    def test_record_publish_for_programs(self):
        out = json.loads(self.cli("record-publish", "--json", "-", stdin=json.dumps([raised()])))
        self.assertEqual(out, [{"id": "r:abc123", "event": "rec:acts/0.3:proj:r:abc123", "status": "accepted"}])
        again = json.loads(self.cli("record-publish", "--json", "-", stdin=json.dumps([raised()])))
        self.assertEqual(again[0]["status"], "duplicate")
        ev = self.c.execute("SELECT * FROM events WHERE id=?", (out[0]["event"],)).fetchone()
        self.assertEqual((ev["type"], ev["source"], ev["to_project"]), ("record", "proj", self.owner))

    def test_record_publish_checks_each_record_on_its_own(self):
        batch = [{"ph": "0.3"}, {**raised(), "ph": "0.2"}, settled(), {**raised(), "project": "nope"}, raised("good1")]
        with mock.patch.object(sys, "argv", ["hubbus", "record-publish", "--json", "-"]), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(batch))), redirect_stdout(io.StringIO()) as out, \
                self.assertRaises(SystemExit):
            self.m.main()
        res = json.loads(out.getvalue())
        self.assertEqual([r["status"] for r in res], ["invalid"] * 4 + ["accepted"])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM events WHERE type='record'").fetchone()[0], 1)

    def test_records_lists_both_directions(self):
        self.cli("record-publish", "--json", "-", stdin=json.dumps([raised("k1")]))
        self.m.publish_record(self.c, settled("k1", "s:k1:1"), self.owner, "proj")
        out = json.loads(self.cli("records", "--project", "proj"))
        self.assertEqual([(o["direction"], o["record"]["type"]) for o in out], [("out", "raised"), ("in", "settled")])
        only_in = json.loads(self.cli("records", "--project", "proj", "--direction", "in"))
        self.assertEqual([o["record"]["type"] for o in only_in], ["settled"])

    def test_a_record_never_displaces_one_of_another_type(self):
        """core §10 (0.5): the same id on another type or origin is a record of its own, kept beside the first"""
        first = self.m.publish_record(self.c, settled(), self.owner, "proj")
        note = {**settled(), "type": "note", "about": {"key": "abc123"}, "note": "x"}
        for k in ("outcome", "terms_rev", "authority"):
            del note[k]
        eid, new = self.m.publish_record(self.c, note, self.owner, "proj")
        self.assertEqual((eid, new), (f"{first[0]}:principal:note", True))
        self.assertFalse(self.m.publish_record(self.c, note, self.owner, "proj")[1], "and a resend is still a no-op")
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM events WHERE type='record'").fetchone()[0], 2)


    def test_record_publish_reads_0_5_and_refuses_a_proof(self):
        """0.5 records go out; evidence.proof is reserved, senders never emit it (core §5.2)"""
        r05 = {**raised("k5"), "ph": "0.5", "ext": "acts/0.5"}
        claim = {**raised("k6"), "ph": "0.5", "ext": "acts/0.5", "id": "c:k6:1", "type": "claimed", "origin": "agent",
                 "outcome": "done", "evidence": {"basis": "paid", "proof": "eyJ..c2ln"}}
        with mock.patch.object(sys, "argv", ["hubbus", "record-publish", "--json", "-"]), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps([r05, claim]))), \
                redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            self.m.main()
        self.assertEqual([r["status"] for r in json.loads(out.getvalue())], ["accepted", "invalid"])

    def test_record_publish_reads_0_6_and_refuses_a_member_named_twice(self):
        """core §4: the text is read strictly, record by record; a 0.6 record goes out (converted down to what the
        principal side reads, outgoing)"""
        r06 = json.dumps({**raised("k7"), "ph": "0.6", "ext": "acts/0.6"})
        dup = json.dumps(raised("k8"))[:-1] + ', "key": "k9"}'
        with mock.patch.object(sys, "argv", ["hubbus", "record-publish", "--json", "-"]), \
                mock.patch.object(sys, "stdin", io.StringIO(f"[{r06}, {dup}]")), \
                redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            self.m.main()
        res = json.loads(out.getvalue())
        self.assertEqual([r["status"] for r in res], ["accepted", "invalid"])
        self.assertIn("I-JSON", res[1]["errors"][0])

    def test_outgoing_converts_down_to_the_send_version(self):
        """core §14: a record made in 0.5 goes to a principal side that speaks 0.4 as 0.4"""
        r05 = {**raised("k5"), "ph": "0.5", "ext": "acts/0.5"}
        with mock.patch.object(self.m.ph_wire.hubconf, "get", return_value=None):
            self.assertEqual((self.m.outgoing(r05)["ph"], self.m.outgoing(r05)["ext"]), ("0.4", "acts/0.4"))
            self.assertEqual(self.m.outgoing(raised())["ext"], "acts/0.3", "older ones go as they are")
        with mock.patch.object(self.m.ph_wire.hubconf, "get", return_value="0.5"):
            self.assertEqual(self.m.outgoing(r05), r05)

    def test_outgoing_after_the_switch_to_0_6(self):
        """core 0.6 §14 item 2: a converting party sends N to a counterpart configured for N, so a 0.5 record made
        before the switch and forwarded after it goes as 0.6; 0.3 and 0.4 ones go as they are"""
        r05 = {**raised("k5"), "ph": "0.5", "ext": "acts/0.5"}
        with mock.patch.object(self.m.ph_wire, "send_version", return_value="0.6"):
            self.assertEqual(self.m.outgoing(r05), {**r05, "ph": "0.6", "ext": "acts/0.6"})
            self.assertEqual(self.m.outgoing(raised()), raised())

    def test_inbox_shows_who_decided(self):
        self.m.publish_record(self.c, settled(), self.owner, "proj")
        text = self.cli("inbox", "--project", "proj")
        self.assertIn("acts settled", text)
        self.assertIn("principal", text)
        self.assertIn("person:owner", text)

    def test_dispatch_does_not_route_records(self):
        self.m.publish_record(self.c, settled(), self.owner, "proj")
        handled = []
        self.m.run_handler = lambda c, proj, ws, rows: handled.append(rows)

        class RT:
            def live_sessions(self): return set()
            def context_size(self, sid): return None
        self.m.dispatch(self.c, RT())
        self.assertEqual(handled, [])


PRINCIPAL = "person:owner"


class InboxText(unittest.TestCase):
    def test_a_settlement_is_shown_whatever_its_answer_is(self):
        """An Acts settlement's answer has no shape of its own, so it may be a number: the inbox must still render it. A
        record with an ext that isn't a string is rendered too."""
        m = load()
        for rec in ({**settled(), "answer": 1}, {**settled(), "answer": "Yes"}, {**settled(), "answer": {"text": "Yes"}},
                    {**settled(), "ext": 1}):
            with self.subTest(rec=rec.get("answer", rec["ext"])):
                row = {"type": "record", "id": "e1", "ts": "2026-11-07T10:00:00+08:00", "source": "owner",
                       "to_project": "proj", "ref": None, "payload": json.dumps({"record": rec})}
                self.assertIsInstance(m.fmt_record(row), str)


def question(text="Which one?", **kw):
    return {"ph": "0.5", "ext": "decisions/0.5", "id": "r:q1", "type": "raised", "origin": "requester",
            "by": "project:proj", "key": "q1", "project": "proj", "ts": TS, "terms": {"text": text}, "terms_rev": 1,
            **kw}


def answer(text, need=None, **kw):
    import ph_wire
    return {"ph": "0.5", "ext": "decisions/0.5", "id": "s:q1:1", "type": "settled", "origin": "principal",
            "by": "assistant", "key": "q1", "project": "proj", "ts": "2026-10-28T09:00:00+08:00", "outcome": "decided",
            "answer": {"text": text}, "terms_rev": 1, "terms_digest": ph_wire.terms_digest(need or question()),
            "authority": {"kind": "principal", "decider": PRINCIPAL}, **kw}


class ReusedIds(unittest.TestCase):
    """core §10 (0.5): one record id sent with different valid content keeps the record whose RFC 8785 serialisation
    is greater, whatever the arrival order; an identical record is a duplicate; an invalid one never displaces or
    blocks a valid one; the conflict is reported (hubbus status, the status page)"""

    def setUp(self):
        m = self.m = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        m.log = lambda s: None
        m.projects = lambda: {"proj": self.tmp}
        self.owner = m.hubconf.owner_address()
        self.cfg = m.ph_records.Config(PRINCIPAL)
        patcher = mock.patch.object(m.ph_records, "config", return_value=self.cfg)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.real_db, self.n = m.db, 0

    def bus(self):
        """a fresh, empty bus"""
        self.n += 1
        self.m.DB = self.tmp / f"bus{self.n}.db"
        c = self.real_db()
        self.addCleanup(c.close)
        self.m.db = lambda *a, **k: c
        return c

    def count(self, c) -> int:
        return c.execute("SELECT COUNT(*) FROM events WHERE type='record'").fetchone()[0]

    def deliver(self, *answers, asked=None):
        """a bus that holds the question (sent by the project) and the answers from the principal side, in this order"""
        c = self.bus()
        for q in asked or [question()]:
            self.m.publish_record(c, q, "proj", self.owner)
        for a in answers:
            self.m.publish_record(c, a, self.owner, "proj")
        return c

    def test_the_records_are_valid(self):
        for r in (question(), question("Which two?"), answer("yes"), answer("no")):
            self.assertEqual(self.m.ph_records.validate(r, self.cfg)[1], [], r)

    def test_both_orders_hold_the_same_winner_and_view(self):
        yes, no = answer("yes"), answer("no")
        seen = []
        for order in ((yes, no), (no, yes)):
            c = self.deliver(*order)
            self.assertEqual(self.count(c), 3, "the second content is stored, not dropped")
            got = self.m.records(c, "proj")
            recs = [r["record"] for r in got]
            settled_ = [r for r in recs if r["type"] == "settled"]
            view = self.m.ph_records.settlement_view(recs, self.cfg)
            inbox = [self.m.payload(r)["record"] for r in self.m.inbox(c, "proj")]
            seen.append((settled_, view, inbox, self.m.open_questions(c)))
        self.assertEqual(seen[0], seen[1])
        settled_, view, inbox, open_ = seen[0]
        self.assertEqual(settled_, [yes], "the greater serialisation: {\"text\":\"yes\"} > {\"text\":\"no\"}")
        self.assertEqual((view["state"], view["answer"]), ("settled", {"text": "yes"}))
        self.assertEqual(inbox, [yes], "the inbox shows the record that prevails, once")
        self.assertEqual(open_, [])

    def test_a_question_sent_twice_with_other_terms(self):
        """the project's own records too: open_questions and question_asked read the raised that prevails"""
        one, two = question("Which one?"), question("Which two?")
        texts = set()
        for order in ((one, two), (two, one)):
            c = self.deliver(asked=order)
            self.assertEqual(self.count(c), 2)
            [q] = self.m.open_questions(c)
            texts.add((q["text"], self.m.question_asked(c, "q1")["text"],
                       self.m.need_state(c, "proj", "q1", "decisions")["terms"]["text"]))
        self.assertEqual(texts, {("Which two?",) * 3})

    def test_an_identical_record_is_a_no_op(self):
        c = self.deliver(answer("yes"), answer("no"))
        self.assertFalse(self.m.publish_record(c, question(), "proj", self.owner)[1])
        for again in (answer("yes"), answer("no")):
            self.assertFalse(self.m.publish_record(c, again, self.owner, "proj")[1])
        self.assertEqual(self.count(c), 3)
        reordered = dict(reversed(list(answer("no").items())))   # member order is not content
        self.assertFalse(self.m.publish_record(c, reordered, self.owner, "proj")[1])

    def test_an_invalid_record_never_displaces_or_blocks_a_valid_one(self):
        bad = answer("zzz", authority={"kind": "principal", "decider": "person:someone-else"})   # not the principal
        self.assertTrue(self.m.ph_records.validate(bad, self.cfg)[1])
        for order in ((answer("yes"), bad), (bad, answer("yes"))):
            c = self.deliver(*order)
            self.assertEqual(self.count(c), 3)
            recs = [r["record"] for r in self.m.records(c, "proj")]
            self.assertEqual([r["answer"]["text"] for r in recs if r["type"] == "settled"], ["yes"])
            self.assertEqual(self.m.ph_records.settlement_view(recs, self.cfg)["answer"], {"text": "yes"})
            self.assertEqual(self.m.record_conflicts(c), [], "an invalid record is no conflict")

    def test_the_conflict_is_reported(self):
        c = self.deliver(answer("no"), answer("yes"))
        [x] = self.m.record_conflicts(c)
        first = "rec:decisions/0.5:proj:s:q1:1"
        self.assertEqual({k: x[k] for k in ("project", "ext", "origin", "type", "id")},
                         {"project": "proj", "ext": "decisions", "origin": "principal", "type": "settled", "id": "s:q1:1"})
        self.assertEqual(x["events"][0], first)
        self.assertTrue(x["events"][1].startswith(first + "~"))
        self.assertEqual(x["kept"], x["events"][1], "the second one, {\"text\":\"yes\"}, prevails")
        with mock.patch.object(sys, "argv", ["hubbus", "status", "--json"]), redirect_stdout(io.StringIO()) as out:
            self.m.main()
        self.assertEqual(json.loads(out.getvalue())["record_conflicts"], [x])
        self.assertEqual(self.m.record_conflicts(c, "2999-01-01"), [], "only recent ones")

    def test_a_record_re_created_with_another_ts(self):
        """kept as any other content, the greater (here the later ts) prevailing in both orders, both directions, and
        not reported (core §10: a record re-created after a crash)"""
        later = answer("yes", ts="2026-10-28T10:00:00+08:00")
        asked_later = question(ts="2026-10-29T09:00:00+08:00")
        for answers, asked in (((answer("yes"), later), (question(), asked_later)),
                               ((later, answer("yes")), (asked_later, question()))):
            c = self.deliver(*answers, asked=asked)
            self.assertEqual(self.count(c), 4)
            got = {r["record"]["type"]: r["record"]["ts"] for r in self.m.records(c, "proj")}
            self.assertEqual(got, {"settled": later["ts"], "raised": asked_later["ts"]})
            self.assertEqual(self.m.record_conflicts(c), [])

    def test_prune_keeps_every_content_of_a_reused_id(self):
        """prune removes records closed long ago; removing the one that prevails would let the other prevail, so a
        record id holding two contents stays whole"""
        c = self.deliver(answer("no"), answer("yes"))
        alone = {**answer("ok"), "id": "s:q2:1", "key": "q2"}
        self.m.publish_record(c, alone, self.owner, "proj")
        kept = {r["record"]["id"]: r["id"] for r in self.m.records(c, "proj", types={"settled"})}
        for eid in kept.values():
            self.m.publish(c, f"{eid}:done", "message.done", "proj", "proj", eid, {"note": "applied"})
        self.m.set_cursor(c, self.m.BRIDGE, 10 ** 9)
        self.m.PRUNE_DAYS = -1
        self.m.prune_wake_addresses = lambda *a, **k: []
        self.m.prune(c)
        self.assertEqual([r["record"]["answer"]["text"] for r in self.m.records(c, "proj", types={"settled"})], ["yes"])
        self.assertIsNone(c.execute("SELECT 1 FROM events WHERE id=?", (kept["s:q2:1"],)).fetchone(),
                          "a record alone under its id is pruned as before")

    def test_record_publish_accepts_other_content(self):
        c = self.bus()
        mine = [raised("k3"), {**raised("k3"), "terms": {"text": "Pay the property tax by Friday"}}]
        with mock.patch.object(sys, "argv", ["hubbus", "record-publish", "--json", "-"]), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(mine))), redirect_stdout(io.StringIO()) as out:
            self.m.main()
        res = json.loads(out.getvalue())
        self.assertEqual([r["status"] for r in res], ["accepted", "accepted"])
        self.assertEqual(res[0]["event"], "rec:acts/0.3:proj:r:k3")
        self.assertTrue(res[1]["event"].startswith("rec:acts/0.3:proj:r:k3~"))
        self.assertEqual(self.count(c), 2)

    def test_another_type_is_still_kept_apart(self):
        """the 11-01 rule holds beside this one: a note reusing a settlement's id is no conflict, and its own
        variants are stored under its own event id"""
        c = self.deliver(answer("yes"))
        note = {k: v for k, v in answer("yes").items() if k not in ("outcome", "answer", "terms_rev", "terms_digest",
                                                                       "authority")}
        note |= {"type": "note", "about": {"key": "q1"}, "note": "a"}
        e1, _n = self.m.publish_record(c, note, self.owner, "proj")
        e2, new = self.m.publish_record(c, {**note, "note": "b"}, self.owner, "proj")
        self.assertEqual(e1, "rec:decisions/0.5:proj:s:q1:1:principal:note")
        self.assertTrue(new and e2.startswith(e1 + "~"))
        self.assertEqual([r["record"]["note"] for r in self.m.records(c, "proj", types={"note"})], ["b"])
        self.assertEqual([x["type"] for x in self.m.record_conflicts(c)], ["note"])


if __name__ == "__main__":
    unittest.main()
