"""bridge v2:匯流排的 0.3 record ↔ 助理的 /api/records(助理那邊的契約:records.v2.md)。

開關 mailbox.records(跟 send 翻 record 同一個);關著就完全不碰 v2。每分鐘的 dispatch:
- 往外:寄給 owner 的 record 批次 POST {"records": […]},逐筆看 status;invalid 記 job.failed、不重試;送不出去整批下次重送。
- 往內:每個專案 GET ?project=&after=,寫成 record 事件、commit 之後才 ack;第一次讀只定起點(v1 已經送過的不再收一次);
  ack 失敗下次補;不合格的(不是助理寄的、專案不對)跳過不寫;改名前的名稱也拉,寫到現在的名稱。
HTTP 換成假的,不碰真的助理。

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
    """/api/records 的假的:out[project] 是助理要給的 record(cursor = 第幾筆,從 1 開始)。"""

    def __init__(self, test):
        self.t, self.calls, self.out, self.down, self.ack_down = test, [], {}, False, False
        self.received, self.invalid = [], set()

    def __call__(self, method, path, body=None, query=None):
        self.calls.append((method, path, body, query))
        if self.down:
            return None, None, "connection refused"
        if method == "POST" and path == "/api/records":
            self.received += body["records"]
            return 200, {"ok": True, "results": [
                {"id": r["id"], "status": "invalid", "errors": ["bad"]} if r["id"] in self.invalid
                else {"id": r["id"], "status": "accepted"} for r in body["records"]]}, None
        if method == "GET" and path == "/api/records":
            recs = self.out.get(query["project"], [])
            after, limit = int(query["after"]), int(query.get("limit", 200))
            page = recs[after:after + limit]
            return 200, {"records": page, "cursor": after + len(page)}, None
        if method == "POST" and path == "/api/records/ack":
            if self.ack_down:
                return None, None, "timeout"
            # ack 一定在 record 寫進匯流排(commit)之後
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
        c = sqlite3.connect(self.m.DB)   # 另一條連線:只看得到已經 commit 的
        c.row_factory = sqlite3.Row
        return c

    def incoming(self, project="proj"):
        return [r["record"]["id"] for r in self.m.records(self.c, project, direction="in")]

    # --- 往外 ---
    def test_outbound_posts_owner_records_once_and_marks_invalid(self):
        self.m.publish_record(self.c, raised("k1"), "proj", self.owner)
        self.m.publish_record(self.c, raised("k2"), "proj", self.owner)
        self.m.publish(self.c, "x1", "message", "proj", self.owner, None, {"type": "info", "text": "v1 的不走這裡"})
        self.m.publish_record(self.c, settled("k9"), self.owner, "proj")   # 往內的不送回去
        self.fake.invalid = {"r:k2"}
        self.m.forward_records(self.c)
        self.assertEqual([r["id"] for r in self.fake.received], ["r:k1", "r:k2"])
        failed = self.c.execute("SELECT * FROM events WHERE type='job.failed'").fetchall()
        self.assertEqual(len(failed), 1)
        self.assertIn("r:k2", self.m.payload(failed[0])["detail"])
        self.m.forward_records(self.c)
        self.assertEqual(len(self.fake.received), 2)   # 不重送、invalid 也不重試

    def test_outbound_catches_up_a_long_bus_in_one_run(self):
        for i in range(1200):   # 開關打開那一刻,匯流排上已經有一大堆別的事件
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

    # --- 往內 ---
    def test_first_read_only_sets_the_starting_point(self):
        self.fake.out["proj"] = [settled(f"old{i}") for i in range(250)]   # v1 已經送過的
        self.m.pull_records(self.c)
        self.assertEqual(self.incoming(), [])
        self.assertEqual(self.fake.acks(), [])
        self.fake.out["proj"].append(settled("new"))
        self.m.pull_records(self.c)
        self.assertEqual(self.incoming(), ["s:new"])
        self.assertIn("250", [q["after"] for q in self.fake.gets() if q["project"] == "proj"])   # 從起點接著拉

    def test_records_are_committed_before_ack_and_resend_is_noop(self):
        self.m.pull_records(self.c)   # 起點
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

    def test_bad_incoming_records_are_skipped(self):
        self.m.pull_records(self.c)
        self.fake.out["proj"] = [raised("forged"), settled("k1", project="other"), {"id": "junk"}, settled("ok")]
        self.m.pull_records(self.c)
        self.assertEqual(self.incoming(), ["s:ok"])
        self.assertEqual(self.incoming("other"), [])
        self.assertTrue(any("forged" in l or "r:forged" in l for l in self.logs))

    def test_starting_point_is_only_for_the_switch_on_not_for_later_names(self):
        self.m.pull_records(self.c)   # 開關打開:起點
        self.m.projects = lambda: {"proj": self.tmp, "other": self.tmp, "newbie": self.tmp}   # 之後才 onboard 的專案
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
    """v1 跟 v2 並存:有 record 的轉交只走 v2(助理 ack 之後 v1 就不列;v1 先拿走的 v2 還是會再給一次),
    v1 只拿沒有 record 的(asks、沒 key 的、不認得的)跟 answers(Decisions 這邊還沒換);v2 的 decisions 結案寫成
    跟 v1 同一個 id 的回答信,誰先到都只有一封。"""

    HANDOFFS = [
        {"id": 70, "status": "pending", "effect": "done", "project": "proj", "reply_to": "task-proj-aaaaaaaaaa", "item": "x"},
        {"id": 71, "status": "pending", "effect": "relevant", "project": "proj", "fact": "下週出國", "item": None},
        {"id": 72, "status": "pending", "effect": "asks", "project": "proj", "fact": "查到哪了?"},
        {"id": 73, "status": "pending", "effect": "answers", "project": "proj", "reply_to": "q1", "fact": "選 A", "item": "要哪家?"},
        {"id": 74, "status": "pending", "effect": "done", "project": "proj", "fact": "沒有 key 的結案"},
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
        # proj 問過 owner 的問題 q1
        m.publish(self.c, "q1", "message", "proj", self.owner, None, {"type": "question", "text": "要哪家?",
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
        self.assertEqual(sorted(self.letters()), [f"{p}72", f"{p}73", f"{p}74", f"{p}75"])
        self.assertEqual(sorted(self.delivered), ["72", "73", "74", "75"])   # 70、71 留給 v2 的 ack

    def answer_record(self, hid=73, **kw):
        return {"ph": "0.3", "ext": "decisions/0.3", "id": f"s:q1:h{hid}", "type": "settled", "origin": "principal",
                "by": "assistant", "key": "q1", "project": "proj", "ts": TS, "outcome": "decided", "terms_rev": 1,
                "answer": {"text": "選 A"}, "authority": {"kind": "principal", "decider": "person:owner"}, **kw}

    def test_v2_answer_becomes_the_same_letter_as_v1(self):
        self.m.RECORDS = True
        fake = self.m.records_http = FakeAssistant(self)
        self.acked_seen = []
        self.reader = lambda: self.c
        self.m.pull_records(self.c)   # 起點
        fake.out["proj"] = [self.answer_record()]
        self.m.pull_records(self.c)
        letters = self.letters()
        mid = f"{self.m.HANDOFF_ID_PREFIX}73"
        self.assertEqual(list(letters), [mid])
        p = letters[mid]
        self.assertEqual((p["type"], p["effect"], p["reply_to"], p["to_session"], p["handoff_id"]),
                         ("handoff", "answers", "q1", "sess-1", 73))
        self.assertIn("選 A", p["text"])
        self.assertIn("要哪家?", p["text"])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM events WHERE type='record'").fetchone()[0], 0)
        self.m.import_handoffs(self.c)      # v1 也拿到同一題:還是一封
        self.assertEqual(sum(1 for i in self.letters() if i == mid), 1)

    def test_v2_dismissal_says_no_answer_needed(self):
        self.m.RECORDS = True
        fake = self.m.records_http = FakeAssistant(self)
        self.acked_seen = []
        self.reader = lambda: self.c
        self.m.pull_records(self.c)
        rec = self.answer_record(hid=80, outcome="dismissed", note="他說不用回答")
        rec.pop("answer")
        fake.out["proj"] = [rec]
        self.m.pull_records(self.c)
        p = self.letters()[f"{self.m.HANDOFF_ID_PREFIX}80"]
        self.assertIn("不用回答", p["text"])


if __name__ == "__main__":
    unittest.main()
