"""Routing a letter to a live session and waking it (docs/design/mailbox-session-first.md, "Each tick", "Candidates", "Picking
one", "The wake", "`pass`"), tick by tick with hooks in between. A temporary bus and a fake runtime that really remembers what
it was asked; no agent or session is started, and no live file is touched.

    bin/py -m unittest tests.test_mailbox_route
"""
import json
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "bin"))
import test_bus_sender as tbs  # noqa: E402
import test_mailbox_triage as tmt  # noqa: E402


class Pass(tmt.Base):
    """Only the session a letter is routed to. Here the route is a hook's: the first session told holds it."""

    def setUp(self):
        super().setUp()
        self.rt.live |= {"S1", "S2"}
        self.send("m1", "proj", "request")
        self.hook("prompt", "S1")

    def test_the_holder_gives_it_back_and_the_next_session_gets_it(self):
        self.cli("pass", "m1", session="S1")
        self.assertEqual(self.routes("m1"), ["S1", None], "the route ended with it")
        self.assertEqual(self.payloads("type='message.routed' AND ref=?", "m1")[-1]["ended"], "passed")
        self.assertEqual(self.payloads("type='message.passed' AND ref=?", "m1")[0]["session"], "S1")
        self.assertFalse(self.state("m1").held)
        self.assertIn("[m1]", self.hook("prompt", "S2"), "the next session is told")
        self.assertEqual(self.state("m1").holder, "S2")

    def test_the_session_that_passed_isnt_told_again(self):
        self.cli("pass", "m1", session="S1")
        self.assertEqual(self.hook("prompt", "S1"), "")
        self.assertEqual(self.routes("m1"), ["S1", None])
        self.cli("inbox", session="S1")
        self.assertEqual(self.routes("m1"), ["S1", None], "reading it holds nothing for it")
        self.assertEqual(self.told("m1"), ["S1"])

    def not_routed(self, mid):
        return self.m._("hubbus: {id} is not routed to this session", id=mid)

    def test_refused_for_another_session_and_with_no_session(self):
        self.assertEqual(self.refused("pass", "m1", session="S2"), self.not_routed("m1"))
        self.assertEqual(self.refused("pass", "m1", session=None),
                         self.m._("hubbus: {id}: pass needs a session, and this command isn't in one", id="m1"))
        self.assertEqual(self.rows("type='message.passed'"), [])
        self.assertEqual(self.state("m1").holder, "S1")

    def test_refused_for_a_letter_nobody_is_asked_about_and_one_taken(self):
        self.send("m2", "proj", "request")
        self.assertEqual(self.refused("pass", "m2", session="S1"), self.not_routed("m2"))
        self.ack("m1", "S1")
        self.assertEqual(self.refused("pass", "m1", session="S1"), self.not_routed("m1"), "an ack ends the matter")

    def test_refused_for_a_notice(self):
        self.send("m2", "proj", "info")
        self.assertEqual(self.refused("pass", "m2", session="S1"),
                         self.m._("hubbus: {id} is not a request or question: there is nothing to pass", id="m2"))

    def test_all_or_none(self):
        self.send("m2", "proj", "request")
        self.refused("pass", "m1", "m2", session="S1")
        self.assertEqual(self.rows("type='message.passed'"), [])
        self.assertEqual(self.state("m1").holder, "S1", "the second was refused: the first stands")

    def test_a_platform_run_cant_a_fork_included(self):
        env_list = (tmt.with_letters(tmt.ESCALATION, "m1"), tmt.with_letters(tmt.FORK, "m1"), tmt.EVENT_JOB, tmt.CLOCK_JOB,
                    {"HUB_LLM_POINT": "lease-session", "HUBBUS_LEASE": "1"})
        for env in env_list:
            with self.subTest(env=env.get("HUB_LLM_POINT")):
                self.refused("pass", "m1", session="S1", env=env)   # even as the session it's routed to
        self.assertEqual(self.rows("type='message.passed'"), [])

    def test_it_stops_nothing_but_the_route(self):
        self.cli("pass", "m1", session="S1")
        self.assertEqual(self.state("m1").taken, "")
        self.assertEqual(self.m.is_done(self.c, "m1"), False)

    def test_it_goes_when_the_letter_goes(self):
        self.cli("pass", "m1", session="S1")
        self.m.publish(self.c, "m1:done", "message.done", "proj", "proj", "m1", {"note": "x"})
        self.c.execute("UPDATE events SET ts='2020-01-01T00:00:00+08:00' WHERE id='m1:done'")
        with mock.patch.object(self.m, "cursor", lambda c, consumer: 1 << 40):
            self.m.prune(self.c)
        self.assertEqual(self.rows("ref='m1'"), [])


class PassLists(unittest.TestCase):
    def test_a_session_has_it_and_a_mailbox_agent_doesnt(self):
        import policy
        self.assertIn("pass", policy.SESSION_SUBCOMMANDS)
        self.assertNotIn("pass", policy.MAIL_SUBCOMMANDS)
        self.assertFalse(any(c.endswith(" pass") for c in policy.platform_cli(mail_only=True)))
        self.assertTrue(any(c.endswith(" pass") for c in policy.platform_cli()))

    def test_no_event_job_can_loop_on_it(self):
        import registry
        self.assertIn("message.passed", registry.MAILBOX_WORK)


class PassFromASandbox(tbs.Fixture):
    """A sandboxed session's pass goes through its outbox: judged against its view there, and again against the bus when taken
    in, for the session the hooks saw in that project."""

    def setUp(self):
        super().setUp()
        import types
        self.m = tbs.load_hubbus()
        self.m.DB = self.tmp / "bus" / "bus.db"
        self.m.LOG = self.tmp / "in-process.log"
        self.m.sessions_port = lambda: types.SimpleNamespace(live_sessions=lambda: {"s-box", "s-other"})
        self.c = self.m.db()
        self.addCleanup(self.c.close)
        self.mid = self.letter_id(self.hubbus("send", "--to", "box", "--type", "request", "--text", "ping",
                                              cwd=self.ws["open"]).stdout)
        with mock.patch.object(self.m.hubconf, "projects", lambda: self.docs), \
                mock.patch.object(self.m.hubconf, "workspace", lambda p: self.ws.get(p)):
            self.m.mail_text("prompt", "s-box", str(self.ws["box"]))

    def run_in_box(self, *args, session="s-box"):
        import contextlib
        import io
        import runtime_ports
        m = tbs.load_hubbus()
        m.DB = self.tmp / "bus-not-for-the-sandbox.db"
        m.LOG = self.tmp / "in-process.log"
        out = io.StringIO()
        with mock.patch.object(m.hubconf, "projects", lambda: self.docs), \
                mock.patch.object(m.hubconf, "workspace", lambda p: self.ws.get(p)), \
                mock.patch.object(m, "project_of", lambda cwd: "box"), \
                mock.patch.object(runtime_ports, "current_session", lambda env=None: session), \
                mock.patch.object(sys, "argv", ["hubbus", *args]), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            m.main()
        return out.getvalue()

    def take(self):
        with mock.patch.object(self.m.hubconf, "projects", lambda: self.docs):
            self.m.take_outboxes(self.c)

    def test_the_holder_through_its_outbox(self):
        self.run_in_box("pass", self.mid)
        self.assertEqual(self.rows("type='message.passed'"), [], "only in the outbox yet")
        self.take()
        self.assertEqual(self.refusals(), [])
        self.assertEqual([json.loads(r["payload"])["session"] for r in self.rows("type='message.passed'")], ["s-box"])

    def test_another_session_is_refused_by_its_view(self):
        with self.assertRaises(SystemExit):
            self.run_in_box("pass", self.mid, session="s-other")
        self.assertFalse(self.outbox().exists() and list(self.outbox().iterdir()), "nothing queued")

    def test_a_file_naming_someone_else_is_refused_when_taken_in(self):
        self.drop({"v": 1, "op": "pass", "ids": [self.mid], "session": "s-other"})
        self.take()
        self.assertEqual(self.rows("type='message.passed'"), [])
        self.assertEqual(len(self.refusals()), 1)

    def test_a_file_for_another_projects_letter_is_refused(self):
        theirs = self.letter_id(self.hubbus("send", "--to", "open", "--from", "brunna", "--type", "request", "--text", "x").stdout)
        self.drop({"v": 1, "op": "pass", "ids": [theirs], "session": "s-box"})
        self.take()
        self.assertEqual(self.rows("type='message.passed'"), [])


if __name__ == "__main__":
    unittest.main()
