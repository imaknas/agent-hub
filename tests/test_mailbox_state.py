"""A letter's state, tick by tick with hooks in between (docs/design/mailbox-session-first.md, "The letter's state").

Characterization: what the routing does before step 1 of that design, pinned so the commit that changes it shows, in
its diff of these expectations, exactly what changed. A temporary bus and tests.test_mailbox_dispatch's fake runtime;
no agent is started.

    bin/py -m unittest tests.test_mailbox_state
"""
import contextlib
import datetime
import io
import json
import os
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "bin"))
import runtime_ports  # noqa: E402
import test_mailbox_dispatch as tmd  # noqa: E402


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
                contextlib.redirect_stdout(out):
            self.m.main()
        return out.getvalue()

    def age(self, eid, minutes):
        self.c.execute("UPDATE events SET ts=? WHERE id=?", (ago(minutes), eid))

    def status(self):
        self.m.db = lambda *a, **k: self.c
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.m.cmd_status(True)
        return json.loads(out.getvalue())

    def rows(self, where, *args):
        return self.c.execute(f"SELECT * FROM events WHERE {where} ORDER BY seq", args).fetchall()


class Today(Harness):
    def test_one_flag_a_letter_told_to_one_session_reaches_no_other(self):
        self.send("m1", "proj", "request")
        self.assertIn("[m1]", self.hook("prompt", "S1"))
        self.assertEqual(self.hook("prompt", "S2"), "")
        start = self.hook("session-start", "S2")
        self.assertIn("[m1]", start, "listed in one line")
        self.assertNotIn("  text m1", start, "not in full")

    def test_an_ack_holds_for_good_whoever_it_was(self):
        self.send("m1", "proj", "request", minutes_ago=300)
        self.m.publish(self.c, "m1:ack:S9", "message.delivered", "proj", "proj", "m1", {"session": "S9", "via": "ack"})
        self.dispatch()
        self.assertEqual(self.m.take_state(self.c, "m1", "proj"), "taken")
        self.assertEqual(self.handled, [])

    def test_an_ack_with_no_session_holds(self):
        self.send("m1", "proj", "request", minutes_ago=300)
        self.m.publish(self.c, "m1:ack:cli", "message.delivered", "proj", "proj", "m1", {"session": None, "via": "ack"})
        self.dispatch()
        self.assertEqual(self.handled, [])

    def test_the_loop_question_on_a_letter_to_the_hub_counts_as_a_reply(self):
        hub = self.m.hubconf.hub_project()
        self.send("m1", hub, "request")
        self.m.publish(self.c, "m1:loop", "message", hub, self.m.OWNER_ADDRESS, "m1", {"type": "question", "text": "q"})
        self.assertEqual(self.m.take_state(self.c, "m1", hub), "taken")

    def test_a_letter_to_a_third_project_with_ref_counts_as_a_reply(self):
        self.send("m1", "proj", "request")
        self.m.publish(self.c, "r1", "message", "proj", "third", "m1", {"type": "info", "text": "fyi", "reply_to": "m1"})
        self.assertEqual(self.m.take_state(self.c, "m1", "proj"), "taken")

    def test_a_platform_runs_inbox_and_ack_are_recorded(self):
        self.send("m1", "proj", "request")
        self.send("m2", "proj", "request")
        self.cli("inbox", session="H1", env={"HUB_LLM_POINT": "mailbox-escalation"})
        self.cli("ack", "m2", session="H1", env={"HUB_LLM_POINT": "mailbox-escalation"})
        self.assertEqual({r["id"] for r in self.rows("type='message.delivered'")},
                         {"m1:delivered:H1", "m2:delivered:H1", "m2:ack:H1"})

    def test_a_leases_inbox_is_recorded_though_its_hooks_are_not(self):
        lease = {"HUB_LLM_POINT": "lease-session", "HUBBUS_LEASE": "1"}
        self.send("m1", "proj", "request")
        self.assertIn("[m1]", self.hook("session-start", "L1", lease))
        self.assertEqual(self.rows("type='message.delivered'"), [])
        self.cli("inbox", session="L1", env=lease)
        self.assertEqual([r["id"] for r in self.rows("type='message.delivered'")], ["m1:delivered:L1"])

    def test_a_held_letter_counts_as_taken_and_hooks_leave_it(self):
        self.send("m1", "proj", "request")
        self.c.execute("BEGIN IMMEDIATE")
        self.m.claim(self.c, [self.m.get_message(self.c, "m1")], "held")
        self.c.execute("COMMIT")
        self.assertEqual(self.m.claimed(self.c, ["m1"]), {"m1"})
        self.assertEqual(self.hook("prompt", "S1"), "")
        self.assertIn(self.m._(" (the platform is handling it, nothing to do)"), self.hook("session-start", "S1"))
        self.dispatch()
        self.assertEqual(self.handled, [])

    def test_a_resume_that_expired_counts_as_finished(self):
        self.send("m1", "proj", "request")
        self.m.claim(self.c, [self.m.get_message(self.c, "m1")], "resume", session="S1", run="r0")
        self.age("m1:delivered:resume", self.m.RESUME_TIMEOUT_MIN + 1)
        with mock.patch.object(self.m.hubconf, "STATE", self.tmp / "state"):
            self.assertEqual(self.m.fork_result(self.c, self.m.get_message(self.c, "m1")), (True, ""))

    def test_a_fork_that_dies_gets_one_handler(self):
        self.rt.tokens["A"] = 1_000
        self.send("m1", "proj", "question", to_session="A", minutes_ago=60)
        self.dispatch()
        self.age("m1:delivered:resume", self.m.RESUME_TIMEOUT_MIN + 1)
        for _ in range(3):
            self.dispatch()
        self.assertEqual((len(self.resumed), self.handled), (1, [("proj", ["m1"])]))

    def test_any_age_gets_a_handler(self):
        self.send("m1", "proj", "request", minutes_ago=60 * 24 * 30)
        self.dispatch()
        self.assertEqual(self.handled, [("proj", ["m1"])])

    def test_a_seen_letter_is_not_listed_as_stuck_however_old(self):
        self.send("m1", "proj", "request", minutes_ago=600)
        self.hook("prompt", "S1")
        self.c.execute("UPDATE events SET ts=? WHERE type='message.delivered'", (ago(500),))
        self.assertEqual(self.status()["stuck"], [])

    def test_a_notice_keeps_the_one_flag(self):
        self.send("n1", "proj", "info")
        self.assertIn("[n1]", self.hook("prompt", "S1"))
        self.assertEqual(self.hook("prompt", "S2"), "")
        self.assertEqual([r["id"] for r in self.rows("type='message.delivered'")], ["n1:delivered:S1"])

    def test_a_session_is_told_its_own_letter_to_its_own_project(self):
        self.m.publish(self.c, "m1", "message", "proj", "proj", None, {"type": "request", "text": "note to self",
                                                                     "from_session": "S1"})
        self.assertIn("[m1]", self.hook("prompt", "S1"))


if __name__ == "__main__":
    unittest.main()
