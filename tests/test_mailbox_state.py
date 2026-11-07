"""A letter's state, tick by tick with hooks in between (docs/design/mailbox-session-first.md, "The letter's state"):
per-session telling, the taken rules, by: hook routes and their end, platform runs marking nothing, one fork per letter,
the 7-day limit on platform runs. A temporary bus and tests.test_mailbox_dispatch's fake runtime; no agent is started.

    bin/py -m unittest tests.test_mailbox_state
"""
import contextlib
import datetime
import io
import json
import os
import pathlib
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "bin"))
import runtime_ports  # noqa: E402
import test_mailbox_dispatch as tmd  # noqa: E402

LEASE = {"HUB_LLM_POINT": "lease-session", "HUBBUS_LEASE": "1"}
HANDLER = {"HUB_LLM_POINT": "mailbox-escalation"}


def ago(minutes):
    return (datetime.datetime.now().astimezone() - datetime.timedelta(minutes=minutes)).isoformat(timespec="seconds")


class Harness(unittest.TestCase):
    setUp = tmd.Dispatch.setUp
    send, seen, dispatch = tmd.Dispatch.send, tmd.Dispatch.seen, tmd.Dispatch.dispatch

    def hook(self, event, sid, env=None):
        """One real hook of session sid in the project's folder."""
        clean = {k: v for k, v in os.environ.items() if k not in ("HUB_LLM_POINT", "HUBBUS_LEASE")}
        self.m.project_of = lambda cwd: "proj"
        self.m.db = lambda *a, **k: self.c
        with mock.patch.dict(os.environ, {**clean, **(env or {})}, clear=True), \
                mock.patch.object(self.m.hubconf, "STATE", self.tmp / "state"):
            return self.m.mail_text(event, sid, str(self.ws)) or ""

    def cli(self, *args, session=None, env=None):
        """One real mail command in the project's folder."""
        clean = {k: v for k, v in os.environ.items() if k not in ("HUB_LLM_POINT", "HUBBUS_LEASE")}
        self.m.project_of = lambda cwd: "proj"
        self.m.db = lambda *a, **k: self.c
        out = io.StringIO()
        with mock.patch.dict(os.environ, {**clean, **(env or {})}, clear=True), \
                mock.patch.object(sys, "argv", ["hubbus", *args]), \
                mock.patch.object(runtime_ports, "current_session", lambda env=None: session), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.m.main()
        return out.getvalue()

    def age(self, eid, minutes):
        self.c.execute("UPDATE events SET ts=? WHERE id=?", (ago(minutes), eid))

    def age_state(self, mid, minutes):
        """Every telling and route of this letter happened this long ago."""
        self.c.execute("UPDATE events SET ts=? WHERE ref=? AND type IN ('message.delivered','message.routed')",
                       (ago(minutes), mid))

    def status(self):
        self.m.db = lambda *a, **k: self.c
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.m.cmd_status(True)
        return json.loads(out.getvalue())

    def rows(self, where, *args):
        return self.c.execute(f"SELECT * FROM events WHERE {where} ORDER BY seq", args).fetchall()

    def routes(self, mid):
        return [json.loads(r["payload"]).get("session") for r in self.rows("type='message.routed' AND ref=?", mid)]

    def told(self, mid):
        return [json.loads(r["payload"]).get("session") for r in self.rows(
            "type='message.delivered' AND ref=? AND json_extract(payload,'$.via') IN "
            "('session-start','prompt','post-tool','inbox')", mid)]

    def state(self, mid):
        return self.m.letter_state(self.c, self.m.get_message(self.c, mid),
                                   self.m.Live(self.rt, self.rt.live_sessions(), c=self.c))

    def close(self, sid):
        """Session sid is gone: off the process table, and its last hook is too old to say it lives."""
        self.rt.live.discard(sid)
        self.c.execute("UPDATE presence SET seen=? WHERE session=?", (ago(self.m.SEEN_ALIVE_MIN + 1), sid))

    def ended(self, sid):
        """Session sid's end hook, on a connection of its own as it has in life."""
        self.m.db = lambda *a, **k: sqlite3.connect(self.tmp / "bus.db", isolation_level=None)
        self.m.session_ended(sid)

    def ack(self, mid, session, minutes=0):
        self.m.publish(self.c, f"{mid}:ack:{session}", "message.delivered", "proj", "proj", mid,
                       {"session": session, "via": "ack"})
        self.age(f"{mid}:ack:{session}", minutes)

    def claim(self, mid, via, **extra):
        self.m.claim(self.c, [self.m.get_message(self.c, mid)], via, **extra)


class Told(Harness):
    def test_told_per_session_and_the_first_holds(self):
        self.rt.live |= {"S1", "S2"}
        self.send("m1", "proj", "request")
        self.assertIn("[m1]", self.hook("prompt", "S1"))
        self.assertEqual(self.routes("m1"), ["S1"])
        self.assertEqual(self.hook("prompt", "S2"), "", "a route points at another session")
        start = self.hook("session-start", "S2")
        self.assertIn("[m1]", start, "listed in one line")
        self.assertNotIn("  text m1", start, "not in full")
        self.assertEqual(self.told("m1"), ["S1"], "a one-line listing tells nothing")
        self.assertEqual(self.hook("prompt", "S1"), "", "told once")

    def test_a_seen_untaken_letter_is_held_then_its_route_lapses_and_another_sessions_hook_shows_it(self):
        self.rt.live |= {"S1", "S2", "S3"}
        self.send("m1", "proj", "request", minutes_ago=2)
        self.assertIn("[m1]", self.hook("prompt", "S1"))
        self.assertEqual(self.dispatch(), 1)                        # held by S1
        self.assertEqual(self.hook("prompt", "S2"), "")
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual(self.handled + self.resumed, [])
        self.age_state("m1", self.m.TAKE_TIMEOUT_MIN + 1)           # S1 never acted on it
        self.assertIn("[m1]", self.hook("prompt", "S2"), "the route lapsed: the next session is told")
        self.assertEqual(self.routes("m1"), ["S1", "S2"])
        self.assertEqual(self.dispatch(), 1, "S2 holds it now")
        self.assertEqual(self.handled + self.resumed, [])
        self.assertEqual(self.hook("prompt", "S1"), "", "S1 was told once")
        self.assertEqual(self.hook("prompt", "S3"), "", "S2 holds it")

    def test_the_window_does_not_restart_at_each_sight(self):
        self.rt.live |= {"S1", "S2"}
        self.send("m1", "proj", "request", minutes_ago=50)
        self.hook("prompt", "S1")
        self.age_state("m1", 50)
        self.cli("inbox", session="S2")                             # another session reads it 50 minutes in
        self.assertEqual(self.told("m1"), ["S1", "S2"])
        self.assertEqual(self.routes("m1"), ["S1"], "reading doesn't pre-empt a holder")
        self.age_state("m1", self.m.TAKE_TIMEOUT_MIN + 1)
        self.dispatch()
        self.assertEqual(self.handled, [("proj", ["m1"])], "S2's sight gave it no more time")

    def test_route_ids_are_unique_and_the_latest_row_is_the_state(self):
        self.rt.live |= {"S1", "S2"}
        self.send("m1", "proj", "request")
        r = self.m.get_message(self.c, "m1")
        for s in ("S1", "S2", "S1"):
            self.m.routed(self.c, r, s, "hook")
        self.assertEqual(self.routes("m1"), ["S1", "S2", "S1"], "publish drops a repeated id")
        self.assertEqual(self.state("m1").holder, "S1")
        self.m.routed(self.c, r, "S2", "hook")
        self.assertEqual(self.state("m1").holder, "S2")

    def test_a_route_judged_ended_never_revives(self):
        self.rt.live.add("S1")
        self.rt.tokens = {}
        self.send("m1", "proj", "request", minutes_ago=2)
        self.hook("prompt", "S1")
        self.close("S1")
        self.m.DAILY_RUNS = 0                                       # no run to start: the letter stays open
        self.m.run_handler = lambda *a: None
        self.dispatch()
        self.assertEqual(self.routes("m1"), ["S1", None])
        self.seen("S1")
        self.rt.live.add("S1")                                      # that session runs again
        self.assertFalse(self.state("m1").held)
        self.dispatch()
        self.assertEqual(self.routes("m1"), ["S1", None], "ended once, recorded once")
        self.rt.live.add("S2")
        self.assertIn("[m1]", self.hook("prompt", "S2"))
        # told after its window: no way back either
        self.send("m2", "proj", "request", minutes_ago=200)
        self.m.routed(self.c, self.m.get_message(self.c, "m2"), "S1", "recent")
        self.c.execute("UPDATE events SET ts=? WHERE type='message.routed' AND ref='m2'", (ago(130),))
        self.m.mark_delivered(self.c, [self.m.get_message(self.c, "m2")], "S1", "prompt")
        self.assertEqual(self.state("m2").ended, "window")

    def test_a_telling_within_a_route_starts_the_window_from_the_telling(self):
        self.rt.live.add("S1")
        self.send("m1", "proj", "request", minutes_ago=200)
        self.m.routed(self.c, self.m.get_message(self.c, "m1"), "S1", "recent")
        self.c.execute("UPDATE events SET ts=? WHERE type='message.routed' AND ref='m1'", (ago(50),))
        self.assertIn("[m1]", self.hook("prompt", "S1"), "a route to this session doesn't keep it from it")
        self.c.execute("UPDATE events SET ts=? WHERE type='message.routed' AND ref='m1'", (ago(self.m.TAKE_TIMEOUT_MIN + 10),))
        self.c.execute("UPDATE events SET ts=? WHERE type='message.delivered' AND ref='m1'", (ago(self.m.TAKE_TIMEOUT_MIN - 20),))
        self.assertTrue(self.state("m1").held, "past 60 from the route, within 60 from the telling")

    def test_a_holder_closing_lets_the_next_sessions_hook_show_it(self):
        self.rt.live |= {"S1", "S2"}
        self.send("m1", "proj", "request")
        self.hook("prompt", "S1")
        self.assertEqual(self.hook("prompt", "S2"), "")
        self.close("S1")
        self.assertIn("[m1]", self.hook("prompt", "S2"))
        self.assertEqual(self.routes("m1"), ["S1", "S2"])

    def test_a_holder_closing_cleanly_ends_its_route_at_once(self):
        self.rt.live |= {"S1", "S2"}
        self.send("m1", "proj", "request")
        self.hook("prompt", "S1")
        self.rt.live.discard("S1")
        self.assertEqual(self.hook("prompt", "S2"), "", "its hook is recent: it may be a headless session at work")
        self.ended("S1")
        self.assertIn("[m1]", self.hook("prompt", "S2"), "SessionEnd says otherwise")

    def test_a_session_ended_and_back_counts_again(self):
        self.rt.live.add("S1")
        self.send("m1", "proj", "request")
        self.hook("prompt", "S1")
        self.rt.live.discard("S1")
        self.ended("S1")
        self.assertFalse(self.state("m1").held)
        self.seen_by_hook("S1")
        self.assertTrue(self.m.Live(self.rt, set(), c=self.c)("S1"), "its next hook says it runs")

    def seen_by_hook(self, sid):
        self.m.record_presence(self.c, sid, "proj", self.ws)

    def test_a_session_told_just_after_the_snapshot_is_not_judged_closed(self):
        """Dispatch reads the process table once, then a session's first hook tells it a letter: on the next line of the
        tick that session is on no list, and the letter would go to a handler too."""
        hook = self.hook
        self.send("m1", "proj", "request", minutes_ago=2)

        class Racing(tmd.FakeRuntime):
            def live_sessions(self):
                snapshot = set(self.live)
                hook("prompt", "S1")
                return snapshot

        self.rt = Racing()
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual(self.handled + self.resumed, [])
        self.assertEqual(self.routes("m1"), ["S1"])

    def test_ending_a_route_as_closed_looks_again(self):
        """No hook to go by: the first look missed it, and the route ends only if a second look does too."""
        self.send("m1", "proj", "request", minutes_ago=2)
        self.m.routed(self.c, self.m.get_message(self.c, "m1"), "S1", "hook")
        looks = []

        class Second(tmd.FakeRuntime):
            def live_sessions(self):
                looks.append(1)
                return {"S1"} if len(looks) > 1 else set()

        self.rt = Second()
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual((self.handled, self.routes("m1"), len(looks)), ([], ["S1"], 2))

    def test_a_holder_closing_with_no_other_session_goes_on_to_todays_routing_and_says_so_once(self):
        self.rt.live.add("S1")
        self.send("m1", "proj", "request", minutes_ago=2)
        self.hook("prompt", "S1")
        self.assertEqual(self.dispatch(), 1)
        self.close("S1")
        self.logged.clear()
        self.dispatch()
        self.dispatch()
        self.assertEqual(self.handled, [("proj", ["m1"])])
        self.assertEqual(self.routes("m1"), ["S1", None])
        self.assertEqual(json.loads(self.rows("type='message.routed' AND ref='m1'")[-1]["payload"])["ended"], "closed")
        self.assertEqual(len([x for x in self.logged if "m1" in x]), 1, self.logged)

    def test_the_log_of_a_lapsed_route_fires_once_per_letter(self):
        # the budget is spent, so nothing takes the letter: it would otherwise say so every minute
        self.rt.live |= {"S1", "S2"}
        self.send("m1", "proj", "request", minutes_ago=90)
        self.hook("prompt", "S1")
        self.age_state("m1", self.m.TAKE_TIMEOUT_MIN + 1)
        self.m.run_handler = lambda *a: None
        self.logged.clear()
        for _ in range(3):
            self.dispatch()
        self.assertEqual(len([x for x in self.logged if "m1" in x]), 1, self.logged)
        self.hook("prompt", "S2")
        self.age_state("m1", self.m.TAKE_TIMEOUT_MIN + 1)
        self.dispatch()
        self.assertEqual(len([x for x in self.logged if "m1" in x]), 1, "once per letter, not per route")

    def test_compaction_keeps_the_list(self):
        self.rt.live |= {"S1", "S2"}
        self.send("m1", "proj", "request")
        self.send("m2", "proj", "request")
        self.assertIn("  text m1", self.hook("session-start", "S1"))
        for _ in range(2):                                          # compacted, twice
            start = self.hook("session-start", "S1")
            self.assertIn("[m1]", start)
            self.assertIn("[m2]", start)
            self.assertNotIn("  text m1", start)
        self.ack("m1", "S1")
        self.assertIn("[m1]", self.hook("session-start", "S1"), "taken but open: still listed")
        self.assertEqual(self.hook("session-start", "S1").count("[m"), 2)
        self.assertEqual(self.told("m1"), ["S1"], "told once")

    def test_a_session_opening_after_the_holder_closed_gets_it_in_full(self):
        self.rt.live.add("S1")
        self.send("m1", "proj", "request")
        self.hook("session-start", "S1")
        self.close("S1")
        self.rt.live.add("S2")
        self.assertIn("  text m1", self.hook("session-start", "S2"))

    def test_a_session_is_not_told_its_own_letter_and_cannot_hold_it(self):
        self.rt.live |= {"S1", "S2"}
        self.m.publish(self.c, "m1", "message", "proj", "proj", None, {"type": "request", "text": "note to self",
                                                                     "from_session": "S1"})
        self.assertEqual(self.hook("prompt", "S1"), "")
        start = self.hook("session-start", "S1")
        self.assertIn("[m1]", start, "listed, as every open letter")
        self.assertNotIn("  note to self", start)
        self.assertIn("note to self", self.cli("inbox", session="S1"), "inbox lists it")
        self.assertEqual((self.told("m1"), self.routes("m1")), ([], []))
        self.assertIn("[m1]", self.hook("prompt", "S2"), "another session of the project gets it")
        self.assertEqual(self.routes("m1"), ["S2"])

    def test_a_notice_keeps_the_one_flag(self):
        self.send("n1", "proj", "info")
        self.assertIn("[n1]", self.hook("prompt", "S1"))
        self.assertEqual(self.hook("prompt", "S2"), "")
        self.assertEqual([r["id"] for r in self.rows("type='message.delivered'")], ["n1:delivered:S1"])
        self.assertEqual(self.routes("n1"), [])


class Taken(Harness):
    def test_a_closed_sessions_ack_no_longer_holds(self):
        self.rt.live |= {"S1", "S2"}
        self.send("m1", "proj", "request", minutes_ago=30)
        self.hook("prompt", "S1")
        self.ack("m1", "S1")
        self.assertEqual(self.state("m1").taken, "now")
        self.assertEqual((self.dispatch(), self.handled), (0, []), "taken while S1 lives")
        self.assertEqual(self.hook("prompt", "S2"), "")
        self.close("S1")
        self.assertEqual(self.state("m1").taken, "")
        self.assertIn("[m1]", self.hook("prompt", "S2"))

    def test_a_headless_session_a_person_started_holds_while_its_hooks_are_recent(self):
        """A `claude -p` or SDK run: no process record, its id on no command line. On main its ack was taken for good."""
        self.send("m1", "proj", "request", minutes_ago=30)
        self.send("m2", "proj", "request", minutes_ago=30)
        self.assertIn("[m1]", self.hook("prompt", "H1"))             # never in rt.live
        self.ack("m1", "H1")
        for _ in range(2):
            self.assertEqual(self.dispatch(), 1, "m2 is held by H1's telling")
        self.assertEqual(self.handled + self.resumed, [])
        self.assertEqual(self.state("m1").taken, "now")
        self.assertEqual(self.routes("m2"), ["H1"])
        self.close("H1")
        self.assertEqual(self.state("m1").taken, "", "its hooks stopped: nothing says it lives")

    def test_someone_elses_ack_ends_a_route_and_it_does_not_revive(self):
        self.rt.live |= {"S1", "S2"}
        self.send("m1", "proj", "request", minutes_ago=30)
        self.hook("prompt", "S1")
        self.ack("m1", "S2")
        self.assertEqual(self.dispatch(), 0)
        self.assertEqual(self.routes("m1"), ["S1", None])
        self.close("S2")
        self.assertFalse(self.state("m1").held, "S1's route ended when S2 took it")

    def test_an_ack_with_no_session_holds_as_today(self):
        self.send("m1", "proj", "request", minutes_ago=300)
        self.m.publish(self.c, "m1:ack:cli", "message.delivered", "proj", "proj", "m1", {"session": None, "via": "ack"})
        self.dispatch()
        self.assertEqual(self.handled, [])
        self.assertEqual(self.state("m1").taken, "good")

    def test_a_reply_to_the_sender_takes_it_for_good_under_any_of_their_names(self):
        self.send("m1", "proj", "request", src="old-other")
        self.m.publish(self.c, "m2", "message", "old-proj", "proj", None, {"type": "request", "text": "x"})
        with mock.patch.object(self.m.hubconf, "former_names",
                               lambda p: {"other": ["old-other"], "proj": ["old-proj"]}.get(p, [])), \
                mock.patch.object(self.m, "current_name", lambda p: {"old-other": "other", "old-proj": "proj"}.get(p, p)):
            self.m.publish(self.c, "r1", "message", "old-proj", "other", "m1", {"type": "info", "text": "y", "reply_to": "m1"})
            self.assertEqual(self.state("m1").taken, "good")

    def test_a_letter_to_a_third_project_with_ref_is_no_reply(self):
        self.send("m1", "proj", "request")
        self.m.publish(self.c, "r1", "message", "proj", "third", "m1", {"type": "info", "text": "fyi", "reply_to": "m1"})
        self.assertEqual(self.state("m1").taken, "")

    def test_a_done_takes_it_for_good(self):
        self.send("m1", "proj", "request")
        self.m.publish(self.c, "m1:done", "message.done", "other", "proj", "m1", {"note": "x"})
        self.assertEqual(self.state("m1").taken, "good")

    def test_the_loop_question_on_a_letter_to_the_hub_leaves_it_open(self):
        hub = self.m.hubconf.hub_project()
        self.send("m1", hub, "request")
        self.m.publish(self.c, "m1:loop", "message", hub, self.m.OWNER_ADDRESS, "m1", {"type": "question", "text": "q"})
        self.m.publish(self.c, "m1:propose-failed", "message", hub, self.m.OWNER_ADDRESS, "m1",
                       {"type": "question", "text": "q"})
        self.assertEqual(self.state("m1").taken, "")

    def test_the_loop_guard_on_a_letter_to_the_hub_leaves_it_open_tick_by_tick(self):
        hub = self.m.hubconf.hub_project()
        self.send("m1", hub, "request")
        self.m.hold(self.c, self.m.get_message(self.c, "m1"))
        for _ in range(2):
            self.assertEqual(self.dispatch(), 1, "open, and the platform starts nothing")
        self.assertEqual(self.state("m1").taken, "")
        self.assertEqual(self.handled + self.resumed, [])

    def test_a_question_to_the_owner_is_no_answer_even_to_the_owner_sides_letter(self):
        self.send("m1", "proj", "question", src=self.m.OWNER_ADDRESS)
        self.m.publish(self.c, "q1", "message", "proj", self.m.OWNER_ADDRESS, "m1", {"type": "question", "text": "q"})
        self.assertEqual(self.state("m1").taken, "")
        self.m.publish(self.c, "r1", "message", "proj", self.m.OWNER_ADDRESS, "m1", {"type": "info", "text": "a"})
        self.assertEqual(self.state("m1").taken, "good", "an answer to the owner side's own question")

    def test_a_held_letter_is_not_taken_and_stays_open_to_sessions_while_the_platform_stops(self):
        self.send("m1", "proj", "request", minutes_ago=30)
        self.claim("m1", "held")
        self.assertEqual(self.m.claimed(self.c, ["m1"]), set())
        self.assertEqual(self.state("m1").taken, "")
        self.assertEqual(self.dispatch(), 1, "the platform starts nothing more for it")
        self.assertEqual(self.handled + self.resumed, [])
        self.rt.live.add("S1")
        self.assertIn("[m1]", self.hook("prompt", "S1"))
        self.assertNotIn(self.m._(" (the platform is handling it, nothing to do)"), self.hook("session-start", "S1"))

    def test_triage_is_read_and_takes_nothing(self):
        self.send("m1", "proj", "request", minutes_ago=30)
        self.claim("m1", "triage", run="t0")
        self.assertEqual(self.state("m1").taken, "")
        self.assertEqual(self.m.claimed(self.c, ["m1"]), set())
        self.rt.live.add("S1")
        self.assertIn("[m1]", self.hook("prompt", "S1"))
        self.assertEqual(self.told("m1"), ["S1"], "a triage row isn't told")

    def test_the_loop_guard_and_held_together_ask_once_and_log_once(self):
        prev = None
        for i in range(self.m.THREAD_MAX_ROUNDS):
            self.send(f"t{i}", "proj", "question", reply_to=prev)
            self.claim(f"t{i}", "handler", run=f"x{i}")
            prev = f"t{i}"
        self.send("next", "proj", "question", reply_to=prev)
        self.logged.clear()
        for _ in range(4):
            self.dispatch()
        self.assertEqual(len([x for x in self.logged if "next" in x]), 1, self.logged)
        self.assertEqual(len(self.rows("id='next:loop'")), 1)
        self.assertEqual(self.handled, [])
        self.rt.live.add("S1")
        self.assertIn("[next]", self.hook("prompt", "S1"), "open to sessions")


class Forks(Harness):
    def started(self, tick_count, aged=True):
        """Run real resume() with a process table that counts; tick_count ticks after the fork's time ran out."""
        started = []

        class Proc:
            def __init__(self, argv, **kw):
                started.append(argv)
                self.stdin = io.StringIO()

        self.m.resume = self.m.real_resume
        with mock.patch.object(self.m.subprocess, "Popen", Proc), mock.patch.object(self.m.hubconf, "STATE", self.tmp / "state"):
            self.dispatch()
            self.assertEqual(len(started), 1)
            self.assertEqual(self.state("m1").taken, "now", "taken while it runs")
            self.age("m1:delivered:resume", self.m.RESUME_TIMEOUT_MIN + 1)
            self.assertEqual(self.state("m1").taken, "", "a fork has an end")
            for _ in range(tick_count):
                self.dispatch()
        return started

    def test_a_conversation_fork_that_dies_never_starts_again(self):
        self.rt.tokens["A"] = 1_000
        self.send("m1", "proj", "question", to_session="A", minutes_ago=60)
        self.assertEqual(len(self.started(4)), 1)
        self.assertEqual(self.handled, [("proj", ["m1"])], "then the handler, once, as today")

    def test_a_delegation_fork_that_dies_never_starts_again(self):
        self.rt.live.add("S1")
        self.rt.tokens["S1"] = 1_000
        self.seen("S1", minutes_ago=40)
        self.send("m1", "proj", "request")
        self.assertEqual(len(self.started(4)), 1)
        self.assertEqual(self.handled, [("proj", ["m1"])])

    def test_a_fork_that_dies_with_the_budget_spent_stays_for_hooks_and_starts_nothing(self):
        self.rt.live.add("S1")
        self.rt.tokens["S1"] = 1_000
        self.seen("S1", minutes_ago=40)
        self.send("m1", "proj", "request")
        ran = []
        self.m.run_handler = lambda c, p, w, rows: ran.append([r["id"] for r in rows])
        self.assertEqual(len(self.started(3)), 1)
        self.assertEqual(ran, [["m1"]] * 3, "the handler is tried and refuses (no claim): never a second fork")
        self.assertIn("[m1]", self.hook("prompt", "S1"), "open again, and a resume row isn't told")

    def test_a_resume_row_is_not_told_and_the_text_shows_only_when_it_replied_or_closed(self):
        self.rt.live.add("S1")
        self.send("m1", "proj", "request")
        self.claim("m1", "resume", session="S1", run="r0")
        self.assertEqual(self.m.claimed(self.c, ["m1"]), {"m1"})
        self.assertEqual(self.hook("prompt", "S1"), "", "being handled")
        self.age("m1:delivered:resume", self.m.RESUME_TIMEOUT_MIN + 1)
        self.assertEqual(self.m.claimed(self.c, ["m1"]), set())
        with mock.patch.object(self.m.hubconf, "STATE", self.tmp / "state"):
            self.assertEqual(self.m.fork_result(self.c, self.m.get_message(self.c, "m1")), (False, ""))
        text = self.hook("prompt", "S1")
        self.assertIn("  text m1", text, "the fork died: told as any letter")
        self.assertNotIn(self.m._("(While you were idle the platform handled this letter in a background branch with "
                                  "your context; nothing more to do.{result})", result=""), text)

    def test_the_text_shows_when_it_closed_the_letter_after_its_time(self):
        self.rt.live.add("S1")
        self.send("m1", "proj", "request")
        self.claim("m1", "resume", session="S1", run="r0")
        self.age("m1:delivered:resume", self.m.RESUME_TIMEOUT_MIN + 1)
        self.m.publish(self.c, "m1:done", "message.done", "proj", "proj", "m1", {"note": "x"})
        self.assertIn(self.m._("(While you were idle the platform handled this letter in a background branch with your "
                               "context; nothing more to do.{result})", result=self.m._(
                                   " The result is in the reply or the memory notes.")).split("{")[0],
                      self.hook("prompt", "S1"))


class SevenDays(Harness):
    OLD = 60 * 24 * (7 + 1)

    def test_no_handler_for_a_letter_past_it(self):
        self.send("m1", "proj", "request", minutes_ago=self.OLD)
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual(self.handled, [])

    def test_a_letter_inside_it_gets_a_handler(self):
        self.send("m1", "proj", "request", minutes_ago=60 * 24 * 6)
        self.dispatch()
        self.assertEqual(self.handled, [("proj", ["m1"])])

    def test_no_fork_on_either_path_past_it(self):
        self.rt.live.add("S1")
        self.rt.tokens.update({"S1": 1_000, "A": 1_000})
        self.seen("S1", minutes_ago=40)
        self.send("m1", "proj", "request", minutes_ago=self.OLD)
        self.send("m2", "proj", "question", to_session="A", minutes_ago=self.OLD)
        self.dispatch()
        self.assertEqual(self.resumed + self.handled, [])

    def test_an_old_ack_by_a_closed_session_stays_for_a_hook(self):
        self.send("m1", "proj", "request", minutes_ago=self.OLD)
        self.ack("m1", "gone", minutes=self.OLD - 10)
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual(self.handled, [])
        self.rt.live.add("S1")
        self.assertIn("[m1]", self.hook("prompt", "S1"))

    def test_no_loop_question_for_a_letter_past_it(self):
        prev = None
        for i in range(self.m.THREAD_MAX_ROUNDS):
            self.send(f"t{i}", "proj", "question", reply_to=prev, minutes_ago=self.OLD)
            self.claim(f"t{i}", "handler", run=f"x{i}")
            prev = f"t{i}"
        self.send("next", "proj", "question", reply_to=prev, minutes_ago=self.OLD)
        self.dispatch()
        self.assertEqual(self.rows("id='next:loop'"), [])


class PlatformRuns(Harness):
    def test_a_handlers_inbox_marks_nothing_and_its_ack_is_not_recorded(self):
        self.send("m1", "proj", "request")
        self.send("m2", "proj", "request")
        out = self.cli("inbox", session="H1", env=HANDLER)
        self.assertIn("[m1]", out)
        self.cli("ack", "m2", session="H1", env=HANDLER)
        self.assertEqual(self.rows("type IN ('message.delivered','message.routed')"), [])
        self.rt.live.add("S1")
        self.assertIn("[m1]", self.hook("prompt", "S1"), "the handler's first command counted for nothing")

    def test_a_lease_is_never_told_and_never_holds(self):
        self.send("m1", "proj", "request")
        self.assertIn("[m1]", self.hook("session-start", "L1", LEASE))
        self.assertIn("[m1]", self.hook("prompt", "L1", LEASE), "never told: it sees what is open each time")
        self.cli("inbox", session="L1", env=LEASE)
        self.cli("ack", "m1", session="L1", env=LEASE)
        self.assertEqual(self.rows("type IN ('message.delivered','message.routed')"), [])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM presence WHERE session='L1'").fetchone()[0], 0)
        self.rt.live.add("S1")
        self.assertIn("[m1]", self.hook("prompt", "S1"))
        self.assertEqual(self.routes("m1"), ["S1"])
        self.assertEqual(self.hook("prompt", "L1", LEASE), "", "and it respects a route")
        self.assertIn("[m1]", self.hook("session-start", "L1", LEASE), "in the list")

    def test_a_platform_hook_is_silent(self):
        self.send("m1", "proj", "request")
        self.assertEqual(self.hook("session-start", "H1", HANDLER), "")
        self.assertEqual(self.rows("type IN ('message.delivered','message.routed')"), [])


class FromBefore(Harness):
    """Letters from before the change stay as they were."""

    def test_an_old_handler_row_stays_taken(self):
        self.send("m1", "proj", "request", minutes_ago=600)
        self.claim("m1", "handler", run="h0")
        self.age("m1:delivered:handler", 500)
        self.dispatch()
        self.assertEqual(self.handled, [])
        self.rt.live.add("S1")
        self.assertEqual(self.hook("prompt", "S1"), "")
        self.assertIn(self.m._(" (the platform is handling it, nothing to do)"), self.hook("session-start", "S1"))
        self.assertEqual(self.routes("m1"), [], "nothing written for it")

    def test_told_with_no_route_is_held_by_that_telling_until_its_window_ends(self):
        self.rt.live |= {"S1", "S2"}
        self.send("m1", "proj", "request", minutes_ago=40)
        self.m.mark_delivered(self.c, [self.m.get_message(self.c, "m1")], "S1", "prompt")
        self.age_state("m1", 30)
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual(self.hook("prompt", "S2"), "", "as it was: another session isn't told")
        self.assertEqual(self.routes("m1"), [], "nothing written while it holds")
        self.age_state("m1", self.m.TAKE_TIMEOUT_MIN + 1)
        self.assertIn("[m1]", self.hook("prompt", "S2"))
        self.assertEqual(self.routes("m1"), ["S2"])

    def test_told_with_no_route_ends_as_any_route_and_is_recorded(self):
        self.rt.live.add("S1")
        self.send("m1", "proj", "request", minutes_ago=90)
        self.m.mark_delivered(self.c, [self.m.get_message(self.c, "m1")], "S1", "prompt")
        self.age_state("m1", self.m.TAKE_TIMEOUT_MIN + 1)
        self.dispatch()
        self.assertEqual(self.routes("m1"), [None])
        self.assertEqual(self.handled, [("proj", ["m1"])])

    def test_seen_and_answered_writes_nothing(self):
        self.send("m1", "proj", "request", minutes_ago=90)
        self.m.mark_delivered(self.c, [self.m.get_message(self.c, "m1")], "S1", "prompt")
        self.m.publish(self.c, "r1", "message", "proj", "other", "m1", {"type": "info", "text": "y", "reply_to": "m1"})
        self.dispatch()
        self.assertEqual(self.routes("m1"), [], "taken for good can't come back: no row for it")

    def test_listing_with_no_session_holds_by_the_window_alone(self):
        self.send("m1", "proj", "request", minutes_ago=10)
        self.cli("inbox")
        self.assertEqual(self.routes("m1"), [])
        self.assertEqual([r["id"] for r in self.rows("type='message.delivered'")], ["m1:delivered:inbox"])
        self.assertEqual(self.dispatch(), 1, "as it was: an MCP client read it and has its time")
        self.rt.live.add("S1")
        self.assertEqual(self.hook("prompt", "S1"), "")
        self.age_state("m1", self.m.TAKE_TIMEOUT_MIN + 1)
        self.assertIn("[m1]", self.hook("prompt", "S1"))


class NoSessionsPort(Harness):
    """A runtime with hooks and no sessions port (or runtime.default none): dispatch doesn't run; the same helper judges
    in hooks. Absent from live_sessions says nothing about such a session."""

    def setUp(self):
        super().setUp()
        rt = runtime_ports.Sessions.__new__(runtime_ports.Sessions)
        rt.mods = {}
        self.m.sessions_port = lambda: rt

    def test_a_route_ends_by_the_window_alone(self):
        self.send("m1", "proj", "request")
        self.assertIn("[m1]", self.hook("prompt", "S1"))
        self.assertEqual(self.hook("prompt", "S2"), "", "S1 can't be judged closed")
        self.age_state("m1", self.m.TAKE_TIMEOUT_MIN + 1)
        self.assertIn("[m1]", self.hook("prompt", "S2"), "held where dispatch doesn't run")
        self.assertEqual(self.routes("m1"), ["S1", "S2"])

    def test_an_ack_holds(self):
        self.send("m1", "proj", "request")
        self.ack("m1", "S1")
        self.assertEqual(self.hook("prompt", "S2"), "")

    def test_dispatch_says_unsupported_and_touches_nothing(self):
        self.send("m1", "proj", "request", minutes_ago=30)
        self.hook("prompt", "S1")
        self.age_state("m1", self.m.TAKE_TIMEOUT_MIN + 1)
        with mock.patch.object(runtime_ports, "default_name", lambda: "none"), mock.patch.object(self.m, "log_once"):
            self.assertEqual(self.m.dispatch(self.c), 0)
        self.assertEqual(self.routes("m1"), ["S1"], "no end recorded")
        self.assertIn("[m1]", self.hook("prompt", "S2"))

    def test_status_says_waiting_while_a_route_holds(self):
        self.send("m1", "proj", "request", minutes_ago=90)
        self.hook("prompt", "S1")
        self.assertEqual([w["why"] for w in self.status()["waiting"]], ["routed"])

    def test_a_failed_lookup_is_no_session_gone(self):
        self.m.sessions_port = lambda: (_ for _ in ()).throw(OSError("no ps"))
        self.m.log_once = lambda k, msg: None
        self.send("m1", "proj", "request")
        self.m.routed(self.c, self.m.get_message(self.c, "m1"), "S1", "hook")
        self.assertEqual(self.hook("prompt", "S2"), "")


class FailedLookup(Harness):
    """A process table that can't be read says nothing about any session. Read as "no sessions" it would end every
    route for good and send a handler for each."""

    def setUp(self):
        super().setUp()
        self.broken = False
        real = self.rt.live_sessions

        def lookup():
            if self.broken:
                raise runtime_ports.SessionsUnknown("ps failed")
            return real()

        self.rt.live_sessions = lookup
        self.once = []
        self.m.log_once = lambda key, msg: self.once.append((key, msg))

    def holding(self):
        self.rt.live.add("S1")
        self.send("m1", "proj", "request", minutes_ago=2)
        self.hook("prompt", "S1")
        self.c.execute("UPDATE presence SET seen=? WHERE session='S1'", (ago(self.m.SEEN_ALIVE_MIN + 1),))
        self.rt.live.clear()

    def test_dispatch_starts_nothing_ends_nothing_and_says_so_once(self):
        self.holding()
        self.send("m2", "proj", "request", minutes_ago=30)
        self.broken = True
        self.assertEqual(self.dispatch(), 0)
        self.assertEqual(self.handled + self.resumed, [])
        self.assertEqual(self.routes("m1"), ["S1"])
        self.assertEqual([k for k, msg in self.once if msg], ["live-sessions"])

    def test_a_hook_reads_it_as_can_not_tell(self):
        self.holding()
        self.broken = True
        self.assertEqual(self.hook("prompt", "S2"), "", "S1 isn't gone, it can't be judged")

    def test_the_prune_keeps_every_record(self):
        d = self.tmp / "secrets" / "sessions"
        d.mkdir(parents=True)
        (d / "S1.live").write_text(json.dumps({"runtime": "claude", "pid": 1, "start": "1", "cwd": "/x"}))
        (d / "S1.json").write_text("{}")
        with mock.patch.object(self.m.hubconf, "session_addresses_dir", lambda: d), \
                mock.patch.object(runtime_ports, "Sessions", lambda: mock.Mock(
                    live_sessions=mock.Mock(side_effect=runtime_ports.SessionsUnknown("ps failed")))), \
                mock.patch.object(runtime_ports, "running_records", mock.Mock(
                    side_effect=runtime_ports.SessionsUnknown("ps failed"))):
            self.assertEqual(self.m.prune_wake_addresses(), [])
        self.assertEqual(sorted(p.name for p in d.iterdir()), ["S1.json", "S1.live"])


class Status(Harness):
    def test_routed_is_waiting_not_stuck(self):
        self.rt.live.add("S1")
        self.send("m1", "proj", "request", minutes_ago=90)
        self.hook("prompt", "S1")
        st = self.status()
        self.assertEqual([(w["id"], w["why"], w["session"]) for w in st["waiting"]], [("m1", "routed", "S1")])
        self.assertEqual(st["stuck"], [])

    def test_a_route_that_ended_with_nothing_started_is_stuck(self):
        self.send("m1", "proj", "request", minutes_ago=600)
        self.hook("prompt", "S1")
        self.age_state("m1", 500)
        st = self.status()
        self.assertEqual([s["id"] for s in st["stuck"]], ["m1"])
        self.assertEqual(st["waiting"], [])

    def test_budget_held_triaged_and_too_old_are_waiting(self):
        self.send("b1", "proj", "request", minutes_ago=90)
        self.send("h1", "proj", "request", minutes_ago=90)
        self.claim("h1", "held")
        self.send("t1", "proj", "request", minutes_ago=90)
        self.claim("t1", "triage", run="t")
        self.send("o1", "proj", "request", minutes_ago=60 * 24 * 8)
        with mock.patch.object(self.m, "DAILY_RUNS", 0):
            st = self.status()
        self.assertEqual(sorted((w["id"], w["why"]) for w in st["waiting"]),
                         [("b1", "budget"), ("h1", "held"), ("o1", "old"), ("t1", "triaged")])
        self.assertEqual(st["stuck"], [])

    def test_taken_is_neither(self):
        self.rt.live.add("S1")
        self.send("m1", "proj", "request", minutes_ago=90)
        self.ack("m1", "S1")
        st = self.status()
        self.assertEqual((st["waiting"], st["stuck"]), ([], []))

    def test_text_output_names_them(self):
        self.rt.live.add("S1")
        self.send("m1", "proj", "request", minutes_ago=90)
        self.hook("prompt", "S1")
        self.m.db = lambda *a, **k: self.c
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.m.cmd_status(False)
        self.assertIn("m1→proj (routed)", out.getvalue())


class Inbox(Harness):
    def test_lists_every_open_letter_with_who_holds_it_and_tells_only_the_one_listing(self):
        self.rt.live |= {"S1", "S2"}
        self.send("free", "proj", "request")
        self.send("mine", "proj", "request")
        self.send("theirs", "proj", "request")
        self.send("taken", "proj", "request")
        self.m.publish(self.c, "theirs:route", "message.routed", "proj", "proj", "theirs", {"session": "S2longer", "by": "hook"})
        self.rt.live.add("S2longer")
        self.m.publish(self.c, "mine:route", "message.routed", "proj", "proj", "mine", {"session": "S1", "by": "hook"})
        self.ack("taken", "S2")
        out = self.cli("inbox", session="S1")
        for mid in ("free", "mine", "theirs", "taken"):
            self.assertIn(f"[{mid}]", out)
        self.assertIn(self.m._("  (held by session {session})", session="S2longer"), out)
        self.assertIn(self.m._("  (held by this session)"), out)
        self.assertIn(self.m._("  (taken)"), out)
        self.assertEqual({r["ref"] for r in self.rows("type='message.delivered' AND json_extract(payload,'$.via')='inbox'")},
                         {"free", "mine", "theirs"}, "told only what isn't taken")
        self.assertEqual(self.routes("free"), ["S1"], "reading holds it")
        self.assertEqual(len(self.routes("theirs")), 1, "and doesn't pre-empt")

    def test_a_second_listing_writes_nothing_more(self):
        self.send("m1", "proj", "request")
        self.cli("inbox", session="S1")
        n = len(self.rows("1"))
        self.cli("inbox", session="S1")
        self.assertEqual(len(self.rows("1")), n)


class Prune(Harness):
    def test_routes_and_wakes_go_with_the_letter(self):
        self.send("m1", "proj", "request")
        self.m.routed(self.c, self.m.get_message(self.c, "m1"), "S1", "hook")
        self.m.publish(self.c, "m1:woken", "message.woken", "proj", "proj", "m1", {"session": "S1", "via": "wake", "ok": True})
        self.m.publish(self.c, "m1:done", "message.done", "proj", "proj", "m1", {"note": "x"})
        self.age("m1:done", 60 * 24 * (self.m.PRUNE_DAYS + 1))
        self.m.set_cursor(self.c, self.m.BRIDGE, 1000)
        self.m.prune(self.c)
        self.assertEqual(self.rows("ref='m1' OR id='m1'"), [])


if __name__ == "__main__":
    unittest.main()
