"""匯流排上的 Principal Handoff 0.3 record(階段 3,docs/design/principal-handoff-0.3-migration.md)。

事件 type record、id rec:<ext>:<project>:<record id>(重送 no-op);`hubbus records` 給 registrar 查;`record-publish` 給程式寄;
done 對 record 也有效;收件匣看得到從助理來的 record(誰決定、什麼授權);dispatch 不把 record 當成要派人處理的信。

    bin/py -m unittest discover -s tests
"""
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
            "key": key, "project": "proj", "ts": TS, "terms": {"text": "繳房屋稅"}, "terms_rev": 1}


class Records(unittest.TestCase):
    def setUp(self):
        m = self.m = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
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
        self.assertEqual(out, [{"id": "rec:acts/0.3:proj:r:abc123", "status": "accepted"}])
        again = json.loads(self.cli("record-publish", "--json", "-", stdin=json.dumps([raised()])))
        self.assertEqual(again[0]["status"], "duplicate")
        ev = self.c.execute("SELECT * FROM events WHERE id=?", (out[0]["id"],)).fetchone()
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


if __name__ == "__main__":
    unittest.main()
