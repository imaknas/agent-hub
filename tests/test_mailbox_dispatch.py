"""Routing of the project mailbox (hubbus dispatch, docs/design/mailbox-routing.md): conversation / request / waiting
for the hook / claiming / loop prevention.

A temporary bus database; the runtime (which sessions are alive, how big their context is) is a fake module;
dispatching and background resumption only record (claims are recorded for real, so "no re-dispatch" and "rounds"
can be tested); no agent is ever started.

    bin/py -m unittest discover -s tests
"""
import datetime
import importlib.machinery
import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest
import unittest.mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import i18n  # noqa: E402


def zh(msgid, **kw):
    """hubbus's message in zh-Hant (the tests that assert one run the code in zh-Hant)"""
    return i18n.translate("hubbus", msgid, kw, "zh-Hant")


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
        # nothing a test runs reaches the live files: the bus log, and what log_once and a fork keep in the state folder
        m.LOG = self.tmp / "hubbus.log"
        m.AGENT_JOBS = self.tmp / "agent-jobs"
        state = unittest.mock.patch.object(m.hubconf, "STATE", self.tmp / "state")
        state.start()
        self.addCleanup(state.stop)
        (self.tmp / "state").mkdir()
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
        m.sessions_port = lambda: self.rt   # hooks and status ask the same fake which sessions run
        m.real_resume = m.resume            # a test that counts the processes it starts
        m.real_run_handler = m.run_handler
        # MCP server processes taken as running (the real check reads the process table: tests.test_mcp_server)
        self.running: set = set()
        m.mcp_process = lambda sid: sid in self.running
        self.logged: list = []
        m.log = self.logged.append

        def run_handler(c, proj, ws, rows):
            c.execute("BEGIN IMMEDIATE")
            m.claim(c, rows, "triage", run=f"h{len(self.handled)}")
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
        self.addCleanup(self.c.close)

    def send(self, mid, to, typ, *, src="other", to_session=None, reply_to=None, minutes_ago=0):
        p = {"type": typ, "text": f"text {mid}"}
        if to_session:
            p["to_session"] = to_session
        if reply_to:
            p["reply_to"] = reply_to
        self.m.publish(self.c, mid, "message", src, to, reply_to, p)
        ts = (datetime.datetime.now().astimezone() - datetime.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
        self.c.execute("UPDATE events SET ts=? WHERE id=?", (ts, mid))

    def seen(self, sid, proj="proj", minutes_ago=0, via=None, runtime=None):
        ts = (datetime.datetime.now().astimezone() - datetime.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
        self.c.execute("INSERT OR REPLACE INTO presence(session, project, cwd, seen, runtime, via) VALUES(?,?,?,?,?,?)",
                       (sid, proj, str(self.ws), ts, runtime, via))

    def dispatch(self):
        return self.m.dispatch(self.c, self.rt)

    # --- requests ---
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
        self.seen("S1", minutes_ago=120)  # open but idle for two hours = nobody using it
        self.send("m1", "proj", "question")
        self.dispatch()
        self.assertEqual(len(self.handled), 1)

    def test_notifications_not_dispatched(self):
        for i, t in enumerate(("info", "done", "progress")):
            self.send(f"m{i}", "proj", t)
        self.dispatch()
        self.assertEqual(self.handled, [])

    def test_delivered_by_hook_not_dispatched(self):
        self.rt.live.add("S1")
        self.send("m1", "proj", "request")
        self.m.mark_delivered(self.c, [self.m.get_message(self.c, "m1")], "S1", "prompt")
        self.dispatch()
        self.assertEqual(self.handled, [])

    # --- delivered into a session only counts as seen (10-27, m10270201-921a) ---
    def seen_by_session(self, mid, minutes_ago, via="prompt"):
        self.rt.live.add("S9")   # a route to a closed session ends at once: tests.test_mailbox_state
        self.m.mark_delivered(self.c, [self.m.get_message(self.c, mid)], "S9", via)
        ts = (datetime.datetime.now().astimezone() - datetime.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
        self.c.execute("UPDATE events SET ts=? WHERE type='message.delivered' AND ref=?", (ts, mid))

    def test_seen_but_untouched_is_redispatched_after_timeout(self):
        self.send("m1", "proj", "request", minutes_ago=90)
        self.seen_by_session("m1", minutes_ago=self.m.TAKE_TIMEOUT_MIN + 5)
        self.dispatch()
        self.assertEqual(self.handled, [("proj", ["m1"])])

    def test_recently_seen_waits(self):
        self.send("m1", "proj", "request", minutes_ago=20)
        self.seen_by_session("m1", minutes_ago=10)
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual(self.handled, [])

    def test_acked_is_not_redispatched(self):
        self.send("m1", "proj", "request", minutes_ago=300)
        self.seen_by_session("m1", minutes_ago=200)
        self.m.publish(self.c, "m1:ack:S9", "message.delivered", "proj", "proj", "m1", {"session": "S9", "via": "ack"})
        self.dispatch()
        self.assertEqual(self.handled, [])

    def test_replied_is_not_redispatched(self):
        self.send("m1", "proj", "question", minutes_ago=300)
        self.seen_by_session("m1", minutes_ago=200)
        self.m.publish(self.c, "r1", "message", "proj", "other", "m1", {"type": "info", "text": "replied", "reply_to": "m1"})
        self.dispatch()
        self.assertEqual(self.handled, [])

    def test_ack_after_hook_delivery_is_recorded(self):
        # ack and hook delivery used to share one event id: after the hook delivered, ack recorded nothing
        import io, sys
        from contextlib import redirect_stdout
        self.send("m1", "proj", "request")
        self.seen_by_session("m1", minutes_ago=0)
        self.m.db = lambda *a, **k: self.c
        import runtime_ports
        with unittest.mock.patch.object(sys, "argv", ["hubbus", "ack", "m1"]), redirect_stdout(io.StringIO()), \
                unittest.mock.patch.object(runtime_ports, "current_session", lambda env=None: None):
            self.m.main()
        st = self.m.letter_state(self.c, self.m.get_message(self.c, "m1"), self.m.Live(self.rt, {"S9"}))
        self.assertEqual(st.taken, "good", "an ack with no session has nothing to outlive")

    def test_claimed_not_redispatched(self):
        self.send("m1", "proj", "request")
        self.dispatch()
        self.dispatch()
        self.assertEqual(len(self.handled), 1)

    def test_hub_letters_are_handled_too(self):
        # agent-hub's letters used to be left for its dedicated session: with nobody typing, nobody took
        # them (m10261706)
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

    # --- requests: branch from the most recent session (trial since 10-26) ---
    def test_delegation_forks_the_recent_live_session(self):
        self.rt.live.add("S1")
        self.rt.tokens["S1"] = 80_000
        self.seen("S1", minutes_ago=40)
        self.send("m1", "proj", "request")
        self.dispatch()
        self.assertEqual((self.resumed, self.handled), ([("m1", "S1")], []))
        self.assertIs(self.prompts[0], self.m.DELEGATE_PROMPT)

    def test_branch_prompts_carry_the_letter_as_is(self):
        """templates/prompts/mailbox-resume.md / mailbox-delegate.md, filled per letter: the letter's id where the
        commands need it, and braces in a letter stay text (the old .format() of the filled text raised on them)"""
        self.m.publish(self.c, "m1", "message", "other", "proj", None, {"type": "request", "text": "set {x} and {{owner}}"})
        r = self.m.get_message(self.c, "m1")
        for name in (self.m.RESUME_PROMPT, self.m.DELEGATE_PROMPT):
            text = self.m.branch_prompt(r, name)
            self.assertIn("set {x} and {{owner}}", text)
            self.assertIn("brunna done m1", text)
            self.assertIn("not inside a script together with other commands", text)
            self.assertNotIn("{{", text.replace("{{owner}}", ""))
        self.assertIn("--reply-to m1", self.m.branch_prompt(r, self.m.DELEGATE_PROMPT))

    def test_delegation_waits_for_typing_then_forks(self):
        self.rt.live.add("S1")
        self.rt.tokens["S1"] = 80_000
        self.seen("S1")
        self.send("m1", "proj", "request")
        self.assertEqual(self.dispatch(), 1)   # someone is typing: wait for its hook first
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

    # --- conversation ---
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
        self.assertEqual(self.handled, [("proj", ["m1"])], "a resumption not closed after 35 minutes is re-dispatched")
        self.dispatch()
        self.assertEqual(len(self.handled), 1, "once re-dispatched, never dispatched again")

    def test_resumed_letter_shown_once_to_its_session(self):
        self.rt.tokens["A"] = 1_000
        self.send("m1", "proj", "question", to_session="A", minutes_ago=60)
        self.dispatch()
        # the branch is done
        self.m.publish(self.c, "m1:done", "message.done", "proj", "proj", "m1", {"note": "the branch handled it",
                                                                                  "point": self.m.FORK_POINT})
        seen = []
        orig = self.m.mark_delivered
        self.m.mark_delivered = lambda c, rows, sess, via: (seen.extend(r["id"] for r in rows), orig(c, rows, sess, via))
        import json as _j, io, sys
        for _ in range(2):
            sys.stdin = io.StringIO(_j.dumps({"session_id": "A", "cwd": str(self.ws)}))
            self.m.hook_input = lambda: {"session_id": "A", "cwd": str(self.ws)}
            self.m.project_of = lambda cwd: "proj"
            # the same connection (setUp's cleanup closes it), not a new unclosed one each time
            self.m.db = lambda timeout=None: self.c
            self.m.cmd_hook("prompt")
        self.assertEqual(seen.count("m1"), 1)

    # --- loop prevention ---
    def test_loop_guard_holds_and_asks_owner(self):
        prev = None
        for i in range(self.m.THREAD_MAX_ROUNDS):
            self.send(f"t{i}", "proj", "question", reply_to=prev)
            self.m.claim(self.c, [self.m.get_message(self.c, f"t{i}")], "handler", run=f"x{i}")
            prev = f"t{i}"
        self.send("t-next", "proj", "question", reply_to=prev)
        self.dispatch()
        self.assertEqual(self.handled, [])
        self.assertTrue(self.m.has_via(self.c, "t-next", "held"))
        q = self.c.execute("SELECT * FROM events WHERE id='t-next:loop'").fetchone()
        self.assertEqual(q["to_project"], self.m.OWNER_ADDRESS)

    # --- what dispatch does today with presence it can't see and with MCP-shaped ids (characterization, pinned
    # before docs/design/mailbox-runtimes.md step 1, A3: MCP presence holding dispatch back) ---
    def test_presence_the_runtime_does_not_report_holds_nothing(self):
        self.seen("mcp:123:456")   # fresh, but no runtime reports it live
        self.send("m1", "proj", "request")
        self.assertEqual(self.dispatch(), 0)
        self.assertEqual(self.handled, [("proj", ["m1"])])

    def test_seen_letter_past_the_timeout_is_dispatched_while_the_project_is_in_use(self):
        self.rt.live.add("S1")
        self.seen("S1")
        self.send("m1", "proj", "request", minutes_ago=90)
        self.seen_by_session("m1", minutes_ago=self.m.TAKE_TIMEOUT_MIN + 5)
        self.dispatch()
        self.assertEqual(self.handled, [("proj", ["m1"])])

    def test_recent_session_never_takes_an_mcp_presence(self):
        # A3 (step 1): an MCP server's id is no session to fork, even if something reported it live
        self.rt.live.add("mcp:123:456")
        self.rt.tokens["mcp:123:456"] = 1_000
        self.seen("mcp:123:456", minutes_ago=40)
        self.send("m1", "proj", "request")
        self.dispatch()
        self.assertEqual((self.resumed, self.handled), ([], [("proj", ["m1"])]))

    def test_outbox_session_is_a_presence_of_the_project_but_never_an_mcp_one(self):
        # A3 (step 1): an mcp: id is never a sending session (a reply would try to resume it)
        self.seen("mcp:123:456")
        self.seen("S1")
        self.assertIsNone(self.m.own_session(self.c, "proj", "mcp:123:456"))
        self.assertEqual(self.m.own_session(self.c, "proj", "S1"), "S1")
        self.assertIsNone(self.m.own_session(self.c, "other", "S1"))

    def test_outbox_presence_operation_is_checked(self):
        # A3 (step 1): the outbox has a presence operation now, refused when malformed
        with self.assertRaises(self.m.Refused):
            self.m.take(self.c, "proj", {"v": 1, "op": "presence", "session": "mcp:123:456"})

    def test_status_lists_an_unclaimed_letter_as_stuck_while_the_project_is_in_use(self):
        import io
        import json as _j
        from contextlib import redirect_stdout
        self.rt.live.add("S1")
        self.seen("S1")
        self.seen("mcp:123:456")
        self.send("q1", "proj", "question", minutes_ago=90)
        self.m.db = lambda *a, **k: self.c
        with redirect_stdout(io.StringIO()) as out:
            self.m.cmd_status(True)
        st = _j.loads(out.getvalue())
        self.assertEqual([s["id"] for s in st["stuck"]], ["q1"])
        # A3 (step 1): held letters are listed apart (none here: no MCP server is running)
        self.assertEqual(sorted(st), ["corrupt", "held", "job_failed_24h", "letters", "no_wake", "projects",
                                    "record_conflicts", "stuck", "to_muninn", "waiting"])
        self.assertEqual(st["held"], [])

    # --- hook ---
    def test_hook_does_not_hand_a_fork_a_letter_while_it_runs(self):
        self.send("m1", "proj", "request")
        self.m.claim(self.c, [self.m.get_message(self.c, "m1")], "resume", session="S1", run="r0")
        rows = self.m.inbox(self.c, "proj", "S9", only_new=True)
        self.assertEqual(rows, [])

    def test_hook_hands_a_triaged_letter_to_a_session(self):
        self.send("m1", "proj", "request")
        self.dispatch()
        rows = self.m.inbox(self.c, "proj", "S9", only_new=True)
        self.assertEqual([r["id"] for r in rows], ["m1"])


class ForkMerge(unittest.TestCase):
    """When a branch is done, the original session gets what the branch said on its next action (m10261706: not just
    "handled"). Borrows Dispatch's setup and helpers without inheriting its tests (otherwise they would run twice)."""
    setUp, send, seen, dispatch = Dispatch.setUp, Dispatch.send, Dispatch.seen, Dispatch.dispatch

    def hook(self, sid="S1"):
        from unittest import mock
        # the branch's record is in the temp directory
        with mock.patch.object(self.m.hubconf, "STATE", self.tmp / "state"):
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

    @unittest.mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "zh-Hant"})   # asserts the zh-Hant text
    def test_finished_fork_reply_and_letters_are_carried_over(self):
        self.forked()
        log = self.tmp / "state" / "mailbox"
        log.mkdir(parents=True)
        (log / "resume-m1.log").write_text('stderr noise\n{"type": "result", "result": "checked production, yesterday\'s import failed 3 rows"}\n')
        self.m.publish(self.c, "r1", "message", "proj", "other", "m1",
                       {"type": "info", "text": "reply: 3 rows failed", "reply_to": "m1", "point": self.m.FORK_POINT})
        text = self.hook()
        self.assertIn(zh("The branch's reply: {text}", text="checked production, yesterday's import failed 3 rows"), text)
        self.assertIn(zh("Letter the branch sent [{id}] → {to}: {text}", id="r1", to="other", text="reply: 3 rows failed"),
                      text)
        self.assertNotIn("m1", self.hook(), "mentioned only once")


class McpPresence(unittest.TestCase):
    """docs/design/mailbox-runtimes.md step 1 (A3): a session that reaches the mailbox only through the platform's MCP
    server records presence as mcp:<pid>:<start>; while that server runs and used an agenthub tool within
    MCP_PRESENCE_MIN (only initialized: CONVERSATION_WAIT_MIN), a request/question not yet delivered to any session
    is held, up to MCP_HOLD_MAX_MIN. Borrows Dispatch's setup and helpers (self.running = the servers taken as
    running)."""
    setUp, send, seen, dispatch, seen_by_session = (Dispatch.setUp, Dispatch.send, Dispatch.seen, Dispatch.dispatch,
                                                    Dispatch.seen_by_session)
    SID = "mcp:4242:1700000000"

    def using(self, minutes_ago=0, via="tool", running=True):
        self.seen(self.SID, minutes_ago=minutes_ago, via=via, runtime="mcp:codex")
        if running:
            self.running.add(self.SID)

    def status(self):
        import io
        import json as _j
        from contextlib import redirect_stdout
        self.m.db = lambda *a, **k: self.c
        with redirect_stdout(io.StringIO()) as out:
            self.m.cmd_status(True)
        return _j.loads(out.getvalue())

    def test_an_undelivered_delegation_is_held_while_the_session_uses_the_project(self):
        self.using(minutes_ago=20)
        self.send("m1", "proj", "request", minutes_ago=30)   # well past the 5 minutes a hook gets
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual((self.handled, self.resumed), ([], []))
        self.assertEqual(self.m.claimed(self.c, ["m1"]), set(), "holding writes nothing: the letter stays unclaimed")

    def test_held_at_most_mcp_hold_max_min(self):
        self.using()
        self.send("m1", "proj", "request", minutes_ago=self.m.MCP_HOLD_MAX_MIN + 5)
        self.dispatch()
        self.assertEqual(self.handled, [("proj", ["m1"])])
        self.assertTrue(any("m1" in line for line in self.logged), "held and then dispatched anyway is logged")

    def test_a_tool_call_counts_for_mcp_presence_min(self):
        self.using(minutes_ago=self.m.MCP_PRESENCE_MIN + 5)
        self.send("m1", "proj", "request", minutes_ago=30)
        self.dispatch()
        self.assertEqual(self.handled, [("proj", ["m1"])])

    def test_only_initialized_counts_for_conversation_wait_min(self):
        self.using(minutes_ago=self.m.CONVERSATION_WAIT_MIN - 2, via="initialize")
        self.send("m1", "proj", "request", minutes_ago=30)
        self.assertEqual(self.dispatch(), 1)
        self.c.execute("UPDATE presence SET seen=? WHERE session=?", (
            (datetime.datetime.now().astimezone() - datetime.timedelta(minutes=self.m.CONVERSATION_WAIT_MIN + 2))
            .isoformat(timespec="seconds"), self.SID))
        self.dispatch()
        self.assertEqual(self.handled, [("proj", ["m1"])])

    def test_a_server_no_longer_running_holds_nothing(self):
        self.using(running=False)
        self.send("m1", "proj", "request", minutes_ago=30)
        self.dispatch()
        self.assertEqual(self.handled, [("proj", ["m1"])])

    def test_presence_in_another_project_holds_nothing_here(self):
        self.seen(self.SID, proj="other", via="tool")
        self.running.add(self.SID)
        self.send("m1", "proj", "request", minutes_ago=30)
        self.dispatch()
        self.assertEqual(self.handled, [("proj", ["m1"])])

    def test_a_delivered_letter_is_never_held_again(self):
        self.using()
        self.send("m1", "proj", "request", minutes_ago=90)
        self.seen_by_session("m1", minutes_ago=self.m.TAKE_TIMEOUT_MIN + 5, via="inbox")
        self.send("m2", "proj", "request", minutes_ago=30)
        self.seen_by_session("m2", minutes_ago=10, via="inbox")
        self.assertEqual(self.dispatch(), 1, "m2: seen 10 minutes ago, the seen rule gives it time")
        self.assertEqual(self.handled, [("proj", ["m1"])], "m1: seen over an hour ago with nothing done")

    def test_a_fresh_letter_waits_and_nothing_is_forked_from_the_mcp_row(self):
        self.using()
        self.rt.live.add("S1")
        self.rt.tokens["S1"] = 1_000
        self.seen("S1", minutes_ago=40)   # a Claude-like session used the project earlier
        self.send("m1", "proj", "request")
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual((self.resumed, self.handled), ([], []))
        self.running.clear()   # the MCP session ends: today's routing (fork the recent live session)
        self.dispatch()
        self.assertEqual(self.resumed, [("m1", "S1")])

    def test_a_conversation_is_routed_as_before(self):
        self.using()
        self.rt.tokens["A"] = 1_000
        self.send("m1", "proj", "question", to_session="A", minutes_ago=10)
        self.dispatch()
        self.assertEqual(self.resumed, [("m1", "A")])

    def test_status_lists_held_letters_apart_from_unclaimed(self):
        self.using()
        self.send("q1", "proj", "question", minutes_ago=90)
        self.send("q2", "proj", "question", minutes_ago=self.m.MCP_HOLD_MAX_MIN + 30)
        st = self.status()
        self.assertEqual([(h["id"], h["to"], h["by"]) for h in st["held"]], [("q1", "proj", ["mcp:codex"])])
        self.assertEqual([s["id"] for s in st["stuck"]], ["q2"])
        self.running.clear()
        st = self.status()
        self.assertEqual((st["held"], [s["id"] for s in st["stuck"]]), ([], ["q1", "q2"]))

    # --- the outbox's presence operation (a sandboxed project's MCP server) ---
    def entry(self, **kw):
        return {"v": 1, "op": "presence", "session": self.SID, "cwd": str(self.ws), "runtime": "mcp:codex",
                "via": "tool", **kw}

    def presence(self):
        return [dict(r) for r in self.c.execute("SELECT session, project, cwd, runtime, via FROM presence")]

    def test_outbox_presence_of_a_running_server_is_recorded(self):
        self.running.add(self.SID)
        patch = unittest.mock.patch.object(self.m.hubconf, "workspace", lambda p: self.ws if p == "proj" else None)
        patch.start()
        self.addCleanup(patch.stop)
        self.m.take(self.c, "proj", self.entry(cwd=str(self.ws / "sub" / ".." / "sub")))
        self.m.take(self.c, "proj", {**self.entry(cwd="/etc"), "runtime": "evil name;rm"})
        self.assertEqual(self.presence(), [{"session": self.SID, "project": "proj", "cwd": str(self.ws),
                                            "runtime": "mcp:evil-name-rm", "via": "tool"}],
                         "a cwd outside the project is recorded as the project's folder")

    def test_outbox_presence_of_a_server_not_running_is_dropped_quietly(self):
        self.m.take(self.c, "proj", self.entry())
        self.assertEqual(self.presence(), [])

    def test_outbox_presence_is_refused_when_it_is_not_an_mcp_server_of_this_project(self):
        self.running.update({self.SID, "S1"})
        for bad in (self.entry(project="other"), self.entry(session="S1"), self.entry(via="hook"),
                    self.entry(session="mcp:1:2:3"), self.entry(cwd=["x"])):
            with self.assertRaises(self.m.Refused, msg=str(bad)):
                self.m.take(self.c, "proj", bad)
        self.assertEqual(self.presence(), [])

    def test_an_mcp_id_is_never_a_sending_session(self):
        self.using()
        self.m.take(self.c, "proj", {"v": 1, "op": "send", "id": "m11011200-0123456789ab", "to": "other",
                                     "type": "question", "text": "q", "session": self.SID})
        import argparse
        a = argparse.Namespace(cmd="send", sender="proj", id=None, to="other", type="question", text="q2",
                               reply_to=None, task=None, question=None, due=None, context=None, new_reason=None,
                               inferred=False)
        mid = self.m.send_letter(self.c, a, "proj", self.SID, "proj")
        for i in ("m11011200-0123456789ab", mid):
            self.assertIsNone(self.m.payload(self.m.get_message(self.c, i)).get("from_session"))

    def test_old_mcp_rows_are_pruned_with_closed_letters(self):
        self.using(minutes_ago=60 * (self.m.MCP_PRUNE_H + 1))
        self.seen("S-old", minutes_ago=60 * (self.m.MCP_PRUNE_H + 1))
        self.seen("mcp:1:2", via="tool")
        self.m.prune(self.c)
        self.assertEqual(sorted(r["session"] for r in self.presence()), ["S-old", "mcp:1:2"])


class McpProcess(unittest.TestCase):
    """The real process check behind an MCP presence row (no fakes): pid, start time and command line."""

    def setUp(self):
        self.m = load_hubbus()

    def test_own_start_time_is_stable_and_a_process_that_is_no_mcp_server_does_not_count(self):
        start = self.m.proc_start(os.getpid())
        self.assertRegex(start or "", r"^\d+$")
        self.assertEqual(self.m.proc_start(os.getpid()), start)
        self.assertFalse(self.m.mcp_process(f"mcp:{os.getpid()}:{start}"), "this test runner is not the MCP server")

    def test_the_server_counts_only_while_it_runs_and_only_with_its_own_start_time(self):
        import subprocess
        import time as _t
        # sleeps under the server's own path: the command line names it, as `agenthub mcp` does
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", str(self.m.MCP_SERVER)])
        self.addCleanup(p.kill)
        for _ in range(50):
            if str(self.m.MCP_SERVER) in self.m.proc_cmdline(p.pid):
                break
            _t.sleep(0.1)
        start = self.m.proc_start(p.pid)
        self.assertTrue(self.m.mcp_process(f"mcp:{p.pid}:{start}"))
        self.assertFalse(self.m.mcp_process(f"mcp:{p.pid}:{int(start) - 100}"), "a reused pid has another start time")
        p.kill()
        p.wait()
        self.assertFalse(self.m.mcp_process(f"mcp:{p.pid}:{start}"))

    def test_an_old_bus_gets_the_new_presence_columns(self):
        import sqlite3
        d = pathlib.Path(tempfile.mkdtemp())
        old = sqlite3.connect(d / "bus.db")
        old.execute("CREATE TABLE presence(session TEXT PRIMARY KEY, project TEXT NOT NULL, cwd TEXT, seen TEXT NOT NULL)")
        old.execute("INSERT INTO presence VALUES('S1', 'proj', '/x', '2026-11-01T10:00:00+08:00')")
        old.commit()
        old.close()
        self.m.DB = d / "bus.db"
        c = self.m.db()
        self.addCleanup(c.close)
        self.assertEqual([r[1] for r in c.execute("PRAGMA table_info(presence)")],
                         ["session", "project", "cwd", "seen", "runtime", "via"])
        self.assertEqual([tuple(r) for r in c.execute("SELECT session, via FROM presence")], [("S1", None)])


if __name__ == "__main__":
    unittest.main()


class DispatchWithoutAgent(Dispatch):
    """No coding agent (runtime.default: none): no agent dispatched, the letter stays, the reason logged once."""

    @unittest.mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "zh-Hant"})   # asserts the zh-Hant text
    def test_letters_stay_and_the_reason_is_logged_once(self):
        logged = []
        self.m.log_once = lambda key, msg: logged.append((key, msg))
        import runtime_ports
        self.send("m1", "proj", "request", minutes_ago=30)
        with unittest.mock.patch.object(runtime_ports, "default_name", lambda: "none"):
            self.assertEqual(self.m.dispatch(self.c), 0)
        self.assertEqual(self.handled, [])
        self.assertEqual(self.resumed, [])
        self.assertEqual(logged[0][0], "dispatch-unsupported")
        self.assertIn(zh("mailbox delegation dispatches no agent: {e}", e=""), logged[0][1])
        row = self.c.execute("SELECT id FROM events WHERE id='m1'").fetchone()
        self.assertIsNotNone(row)
