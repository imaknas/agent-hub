"""專案信箱的分流(hubbus dispatch,docs/design/mailbox-routing.md):對話 / 交辦 / 等 hook / 認領 / 防循環。

暫存的匯流排資料庫;執行環境(哪些 session 活著、context 多大)用假的模組;派人跟背景接續換成只記錄
(認領照真的記,這樣「不重派」「輪數」才測得到),不會叫起任何 agent。

    python3 -m unittest discover -s tests
"""
import datetime
import importlib.machinery
import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_hubbus():
    loader = importlib.machinery.SourceFileLoader("hubbus_mod", str(ROOT / "bin" / "hubbus"))
    spec = importlib.util.spec_from_loader("hubbus_mod", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


class FakeRuntime:
    def __init__(self):
        self.live, self.tokens = set(), {}

    def live_sessions(self):
        return set(self.live)

    def context_size(self, sid):
        return self.tokens.get(sid)

    def resume_args(self, sid):
        return ["--resume", sid, "--fork-session"]


class Dispatch(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        m = self.m = load_hubbus()
        m.DB = self.tmp / "bus.db"
        self.ws = self.tmp / "proj"
        self.ws.mkdir()
        self.hubws = self.tmp / "hub"
        self.hubws.mkdir()
        hub = m.hubconf.hub_project()
        m.projects = lambda: {"proj": self.ws, "other": self.tmp, hub: self.hubws}
        m.lease_ids = lambda: self.leases
        m.log = lambda msg: None
        self.leases: set = set()
        self.handled, self.resumed = [], []

        def run_handler(c, proj, ws, rows):
            c.execute("BEGIN IMMEDIATE")
            m.claim(c, rows, "handler", run=f"h{len(self.handled)}")
            c.execute("COMMIT")
            self.handled.append((proj, [r["id"] for r in rows]))

        def resume(c, r, sid, cwd, rt, ws=None):
            c.execute("BEGIN IMMEDIATE")
            m.claim(c, [r], "resume", session=sid, run=f"r{len(self.resumed)}")
            c.execute("COMMIT")
            self.resumed.append((r["id"], sid))

        m.run_handler, m.resume = run_handler, resume
        self.rt = FakeRuntime()
        self.c = m.db()

    def send(self, mid, to, typ, *, src="other", to_session=None, reply_to=None, minutes_ago=0):
        p = {"type": typ, "text": f"text {mid}"}
        if to_session:
            p["to_session"] = to_session
        if reply_to:
            p["reply_to"] = reply_to
        self.m.publish(self.c, mid, "message", src, to, reply_to, p)
        ts = (datetime.datetime.now().astimezone() - datetime.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
        self.c.execute("UPDATE events SET ts=? WHERE id=?", (ts, mid))

    def seen(self, sid, proj="proj", minutes_ago=0):
        ts = (datetime.datetime.now().astimezone() - datetime.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
        self.c.execute("INSERT OR REPLACE INTO presence(session, project, cwd, seen) VALUES(?,?,?,?)",
                       (sid, proj, str(self.ws), ts))

    def dispatch(self):
        return self.m.dispatch(self.c, self.rt)

    # --- 交辦 ---
    def test_delegation_nobody_using_dispatches_now(self):
        self.send("m1", "proj", "request")
        self.dispatch()
        self.assertEqual(self.handled, [("proj", ["m1"])])

    def test_delegation_waits_for_active_session_then_dispatches(self):
        self.rt.live.add("S1")
        self.seen("S1")
        self.send("m1", "proj", "request")
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual(self.handled, [])
        self.c.execute("UPDATE events SET ts=? WHERE id='m1'",
                       ((datetime.datetime.now().astimezone() - datetime.timedelta(minutes=10)).isoformat(),))
        self.dispatch()
        self.assertEqual(self.handled, [("proj", ["m1"])])

    def test_paused_session_does_not_block(self):
        self.rt.live.add("S1")
        self.seen("S1", minutes_ago=120)  # 開著但兩小時沒動作 = 沒人在用
        self.send("m1", "proj", "question")
        self.dispatch()
        self.assertEqual(len(self.handled), 1)

    def test_notifications_not_dispatched(self):
        for i, t in enumerate(("info", "done", "progress")):
            self.send(f"m{i}", "proj", t)
        self.dispatch()
        self.assertEqual(self.handled, [])

    def test_delivered_by_hook_not_dispatched(self):
        self.send("m1", "proj", "request")
        self.m.mark_delivered(self.c, [self.m.get_message(self.c, "m1")], "S1", "prompt")
        self.dispatch()
        self.assertEqual(self.handled, [])

    def test_claimed_not_redispatched(self):
        self.send("m1", "proj", "request")
        self.dispatch()
        self.dispatch()
        self.assertEqual(len(self.handled), 1)

    def test_hub_delegation_left_for_its_own_session(self):
        hub = self.m.hubconf.hub_project()
        self.send("m1", hub, "request")
        self.dispatch()
        self.assertEqual(self.handled, [])

    # --- 對話 ---
    def test_conversation_live_session_waits(self):
        self.rt.live.add("A")
        self.send("m1", "proj", "question", to_session="A")
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual(self.resumed + self.handled, [])

    def test_conversation_idle_small_context_resumes(self):
        self.rt.live.add("A")
        self.rt.tokens["A"] = 40_000
        self.send("m1", "proj", "question", to_session="A", minutes_ago=10)
        self.dispatch()
        self.assertEqual(self.resumed, [("m1", "A")])

    def test_conversation_large_context_becomes_delegation(self):
        self.rt.tokens["A"] = 500_000
        self.send("m1", "proj", "question", to_session="A")
        self.dispatch()
        self.assertEqual(self.resumed, [])
        self.assertEqual(self.handled, [("proj", ["m1"])])

    def test_conversation_to_lease_never_resumed(self):
        self.leases = {"L1"}
        self.rt.live.add("L1")
        self.rt.tokens["L1"] = 1_000
        self.send("m1", "proj", "request", to_session="L1", minutes_ago=10)
        self.dispatch()
        self.assertEqual(self.resumed, [])
        self.assertEqual(len(self.handled), 1)

    def test_conversation_to_hub_session_resumes(self):
        hub = self.m.hubconf.hub_project()
        self.rt.tokens["H"] = 10_000
        self.send("m1", hub, "question", to_session="H")
        self.dispatch()
        self.assertEqual(self.resumed, [("m1", "H")])

    def test_failed_resume_is_redelegated(self):
        self.rt.tokens["A"] = 1_000
        self.send("m1", "proj", "question", to_session="A", minutes_ago=60)
        self.dispatch()
        self.assertEqual(self.resumed, [("m1", "A")])
        old = (datetime.datetime.now().astimezone() - datetime.timedelta(minutes=40)).isoformat(timespec="seconds")
        self.c.execute("UPDATE events SET ts=? WHERE id='m1:delivered:resume'", (old,))
        self.dispatch()
        self.assertEqual(self.handled, [("proj", ["m1"])], "接續 35 分鐘沒結案要改派")
        self.dispatch()
        self.assertEqual(len(self.handled), 1, "改派過就不再派")

    def test_resumed_letter_shown_once_to_its_session(self):
        self.rt.tokens["A"] = 1_000
        self.send("m1", "proj", "question", to_session="A", minutes_ago=60)
        self.dispatch()
        seen = []
        orig = self.m.mark_delivered
        self.m.mark_delivered = lambda c, rows, sess, via: (seen.extend(r["id"] for r in rows), orig(c, rows, sess, via))
        import json as _j, io, sys
        for _ in range(2):
            sys.stdin = io.StringIO(_j.dumps({"session_id": "A", "cwd": str(self.ws)}))
            self.m.hook_input = lambda: {"session_id": "A", "cwd": str(self.ws)}
            self.m.project_of = lambda cwd: "proj"
            self.m.cmd_hook("prompt")
        self.assertEqual(seen.count("m1"), 1)

    # --- 防循環 ---
    def test_loop_guard_holds_and_asks_owner(self):
        prev = None
        for i in range(self.m.THREAD_MAX_ROUNDS):
            self.send(f"t{i}", "proj", "question", reply_to=prev)
            self.m.claim(self.c, [self.m.get_message(self.c, f"t{i}")], "handler", run=f"x{i}")
            prev = f"t{i}"
        self.send("t-next", "proj", "question", reply_to=prev)
        self.dispatch()
        self.assertEqual(self.handled, [])
        self.assertIn("t-next", self.m.claimed(self.c, ["t-next"]))
        q = self.c.execute("SELECT * FROM events WHERE id='t-next:loop'").fetchone()
        self.assertEqual(q["to_project"], self.m.KNAS)

    # --- hook ---
    def test_hook_does_not_hand_claimed_letter_to_session(self):
        self.send("m1", "proj", "request")
        self.dispatch()
        rows = self.m.inbox(self.c, "proj", "S9", only_new=True)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
