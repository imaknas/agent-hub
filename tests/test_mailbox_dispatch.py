"""專案信箱的分流(hubbus dispatch,docs/design/mailbox-routing.md):對話 / 交辦 / 等 hook / 認領 / 防循環。

暫存的匯流排資料庫;執行環境(哪些 session 活著、context 多大)用假的模組;派人跟背景接續換成只記錄
(認領照真的記,這樣「不重派」「輪數」才測得到),不會叫起任何 agent。

    bin/py -m unittest discover -s tests
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
        self.handled, self.resumed, self.prompts = [], [], []

        def run_handler(c, proj, ws, rows):
            c.execute("BEGIN IMMEDIATE")
            m.claim(c, rows, "handler", run=f"h{len(self.handled)}")
            c.execute("COMMIT")
            self.handled.append((proj, [r["id"] for r in rows]))

        def resume(c, r, sid, cwd, rt, ws=None, prompt=None):
            c.execute("BEGIN IMMEDIATE")
            m.claim(c, [r], "resume", session=sid, run=f"r{len(self.resumed)}")
            c.execute("COMMIT")
            self.resumed.append((r["id"], sid))
            self.prompts.append(prompt)

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

    def test_hub_letters_are_handled_too(self):
        # 以前 agent-hub 的信留給它專用的 session,沒人打字就沒人收(m10261706)
        hub = self.m.hubconf.hub_project()
        self.send("m1", hub, "request")
        self.dispatch()
        self.assertEqual(self.handled, [(hub, ["m1"])])

    def test_hub_letter_forks_its_recent_session(self):
        hub = self.m.hubconf.hub_project()
        self.rt.live.add("H")
        self.rt.tokens["H"] = 50_000
        self.seen("H", proj=hub, minutes_ago=30)
        self.send("m1", hub, "question")
        self.dispatch()
        self.assertEqual((self.resumed, self.handled), ([("m1", "H")], []))

    # --- 交辦:用最近的 session 分支(10-26 試行)---
    def test_delegation_forks_the_recent_live_session(self):
        self.rt.live.add("S1")
        self.rt.tokens["S1"] = 80_000
        self.seen("S1", minutes_ago=40)
        self.send("m1", "proj", "request")
        self.dispatch()
        self.assertEqual((self.resumed, self.handled), ([("m1", "S1")], []))
        self.assertIs(self.prompts[0], self.m.DELEGATE_PROMPT)

    def test_delegation_waits_for_typing_then_forks(self):
        self.rt.live.add("S1")
        self.rt.tokens["S1"] = 80_000
        self.seen("S1")
        self.send("m1", "proj", "request")
        self.assertEqual(self.dispatch(), 1)   # 有人在打字:先等它的 hook
        self.send("m2", "proj", "request", minutes_ago=10)
        self.c.execute("DELETE FROM events WHERE id='m1'")
        self.dispatch()
        self.assertEqual(self.resumed, [("m2", "S1")])

    def test_delegation_without_usable_session_gets_a_handler(self):
        for sid, tokens, mins in (("dead", 1_000, 10), ("big", 900_000, 10), ("old", 1_000, 60 * 30)):
            if sid != "dead":
                self.rt.live.add(sid)
            self.rt.tokens[sid] = tokens
            self.seen(sid, minutes_ago=mins)
        self.send("m1", "proj", "request")
        self.dispatch()
        self.assertEqual((self.resumed, self.handled), ([], [("proj", ["m1"])]))

    def test_status_ignores_notifications(self):
        self.send("i1", "proj", "info", minutes_ago=180)
        self.send("q1", "proj", "question", minutes_ago=180)
        import io
        from contextlib import redirect_stdout
        self.m.db = lambda *a, **k: self.c
        with redirect_stdout(io.StringIO()) as out:
            self.m.cmd_status(True)
        import json as _j
        self.assertEqual([s["id"] for s in _j.loads(out.getvalue())["stuck"]], ["q1"])

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
        self.m.publish(self.c, "m1:done", "message.done", "proj", "proj", "m1", {"note": "分支處理了"})  # 分支做完
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


class ForkMerge(unittest.TestCase):
    """分支做完,原本的 session 下次有動作時收到分支說了什麼(m10261706:不要只說「處理了」)。
    借 Dispatch 的設定跟小工具,不繼承它的測試(不然那些測試會跑兩次)。"""
    setUp, send, seen, dispatch = Dispatch.setUp, Dispatch.send, Dispatch.seen, Dispatch.dispatch

    def hook(self, sid="S1"):
        from unittest import mock
        with mock.patch.object(self.m.hubconf, "STATE", self.tmp / "state"):   # 分支的紀錄在暫存目錄
            self.m.project_of = lambda cwd: "proj"
            self.m.db = lambda *a, **k: self.c
            return self.m.mail_text("prompt", sid, str(self.ws)) or ""

    def forked(self):
        self.rt.live.add("S1")
        self.rt.tokens["S1"] = 1_000
        self.seen("S1", minutes_ago=40)
        self.send("m1", "proj", "request")
        self.dispatch()
        self.assertEqual(self.resumed, [("m1", "S1")])

    def test_unfinished_fork_not_mentioned_yet(self):
        self.forked()
        self.assertNotIn("m1", self.hook())

    def test_finished_fork_reply_and_letters_are_carried_over(self):
        self.forked()
        log = self.tmp / "state" / "mailbox"
        log.mkdir(parents=True)
        (log / "resume-m1.log").write_text('stderr noise\n{"type": "result", "result": "查了 production,昨天的匯入失敗 3 筆"}\n')
        self.m.publish(self.c, "r1", "message", "proj", "other", "m1",
                       {"type": "info", "text": "回覆:失敗的是 3 筆", "reply_to": "m1"})
        text = self.hook()
        self.assertIn("分支的回覆:查了 production", text)
        self.assertIn("[r1] → other:回覆:失敗的是 3 筆", text)
        self.assertNotIn("m1", self.hook(), "只提一次")


if __name__ == "__main__":
    unittest.main()
