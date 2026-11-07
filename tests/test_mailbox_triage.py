"""The headless handler as last resort (docs/design/mailbox-session-first.md, "Triage"). A temporary bus, log and state
folder (tests.test_mailbox_dispatch's setUp); processes are stubbed, no agent is started.

    bin/py -m unittest tests.test_mailbox_triage
"""
import importlib.util
import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "bin"))
import test_mailbox_state as tms  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
ESCALATION = {"HUB_LLM_POINT": "mailbox-escalation", "AGENT_HUB_CONFINED": "1"}
EVENT_JOB = {"HUB_LLM_POINT": "agent-job-event", "AGENT_HUB_CONFINED": "1"}


def load_propose():
    spec = importlib.util.spec_from_file_location("propose_apply_triage", ROOT / "bin" / "propose_apply.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Base(tms.Harness):
    def setUp(self):
        tms.tmd.Dispatch.setUp(self)
        self.started = []

    def real_handler(self):
        """The real run_handler, with its process stubbed."""
        self.m.run_handler = self.m.real_run_handler

        def run(cmd, **kw):
            self.started.append({"cmd": cmd, "env": kw.get("env") or {}})
            return subprocess.CompletedProcess(cmd, 0, "", "")
        patch = mock.patch.object(self.m.subprocess, "run", run)
        patch.start()
        self.addCleanup(patch.stop)
        render = mock.patch.object(self.m.prompts, "render", lambda *a, **k: "prompt")
        render.start()
        self.addCleanup(render.stop)

    def payloads(self, where, *args):
        return [json.loads(r["payload"]) for r in self.rows(where, *args)]

    def vias(self, mid):
        return [p.get("via") for p in self.payloads("type='message.delivered' AND ref=?", mid)]


class Today(Base):
    """What the handler paths do before this step, pinned first."""

    def test_a_dispatched_letter_is_taken_for_good_and_hooks_stop_showing_it(self):
        self.real_handler()
        self.send("m1", "proj", "request")
        self.dispatch()
        self.assertEqual(len(self.started), 1)
        self.assertEqual(self.vias("m1"), ["handler"])
        self.assertEqual(self.started[0]["env"]["AGENT_JOB_POINT"], "mailbox-escalation")
        self.assertEqual(self.state("m1").taken, "good")
        self.assertEqual(self.hook("prompt", "S1"), "")
        self.dispatch()
        self.assertEqual(len(self.started), 1)

    def test_the_propose_handler_claims_too(self):
        self.real_handler()
        self.send("m1", "proj", "request")
        started = []

        class P:
            def __init__(self, cmd, **kw):
                started.append(cmd)
        with mock.patch.object(self.m.subprocess, "Popen", P), \
                mock.patch.object(self.m.hubconf, "get", lambda k, d=None: {"handler": "propose"} if k == "mailbox" else d):
            self.dispatch()
        self.assertIn("propose_apply.py", started[0][1])
        self.assertEqual(started[0][2:], ["mailbox", "proj", "m1"])
        self.assertEqual(self.vias("m1"), ["handler"])

    def test_several_senders_go_in_one_run(self):
        self.real_handler()
        self.send("m1", "proj", "request", src="other")
        self.send("m2", "proj", "request", src=self.m.hubconf.hub_project())
        self.dispatch()
        self.assertEqual(len(self.started), 1)
        self.assertEqual((self.vias("m1"), self.vias("m2")), (["handler"], ["handler"]))

    def test_the_run_budget_claims_nothing_and_logs_every_tick(self):
        self.real_handler()
        self.m.DAILY_RUNS = 0
        self.send("m1", "proj", "request")
        self.dispatch()
        self.dispatch()
        self.assertEqual((self.started, self.vias("m1")), ([], []))
        self.assertEqual(len([x for x in self.logged if "m1" in x or "0" in x]), 2)

    def test_hold_records_once_but_logs_at_each_call(self):
        self.send("m1", "proj", "request")
        r = self.m.get_message(self.c, "m1")
        self.m.hold(self.c, r)
        self.m.hold(self.c, r)
        self.assertEqual(self.vias("m1"), ["held"])
        self.assertEqual(len(self.rows("id='m1:loop'")), 1)
        self.assertEqual(len(self.logged), 2)

    def test_a_held_letter_stops_everything_in_dispatch_even_a_wake(self):
        woke = []
        self.m.wake = lambda c, rt, r, sid: woke.append(sid)
        self.rt.live.add("S1")
        self.seen("S1")
        self.send("m1", "proj", "request")
        self.claim("m1", "held")
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual(woke, [])

    def test_a_confined_run_sends_anything_to_anyone(self):
        self.send("m1", "proj", "request")
        self.cli("send", "--to", "other", "--type", "info", "--text", "not a reply", env=ESCALATION)
        self.cli("send", "--to", "other", "--type", "question", "--text", "a question", env=ESCALATION)
        self.assertEqual(len(self.rows("type='message' AND source='proj'")), 2)

    def test_a_confined_run_reads_and_closes_every_letter_of_its_project(self):
        self.send("m1", "proj", "request")
        self.send("m2", "proj", "request", src=self.m.hubconf.hub_project())
        out = self.cli("inbox", env=ESCALATION)
        self.assertIn("[m1]", out)
        self.assertIn("[m2]", out)
        self.cli("done", "m2", env=ESCALATION)
        self.assertTrue(self.m.is_done(self.c, "m2"))

    def test_a_handlers_inbox_misses_a_reply_waiting_for_its_session(self):
        self.send("m1", "proj", "question", to_session="S1", minutes_ago=2)
        self.assertNotIn("[m1]", self.cli("inbox", env=ESCALATION))

    def test_a_handlers_ack_is_quietly_not_recorded(self):
        self.send("m1", "proj", "request")
        self.cli("ack", "m1", env=ESCALATION)
        self.assertEqual(self.vias("m1"), [])

    def test_a_fork_runs_with_no_list_of_letters(self):
        seen = {}
        self.m.resume = self.m.real_resume

        class P:
            def __init__(self, cmd, **kw):
                seen.update(kw["env"])
                self.stdin = mock.Mock()
        self.rt.live.add("S1")
        self.rt.tokens["S1"] = 1000
        self.seen("S1", minutes_ago=30)
        self.send("m1", "proj", "request")
        with mock.patch.object(self.m.subprocess, "Popen", P):
            self.dispatch()
        self.assertEqual(seen["MAILBOX_PROJECT"], "proj")
        self.assertNotIn("MAILBOX_LETTERS", seen)


class TodayPropose(Base):
    """What propose_apply's mailbox does with the agent's answer, before this step."""

    def apply(self, ids, output, ok=True):
        pa = load_propose()
        pa._hubbus = lambda: self.m
        self.m.db = lambda *a, **k: self.c
        res = {"ok": ok, "output": output if ok else None, "text": "", "applied": [], "refused": [], "error": "" if ok else "broke"}
        with mock.patch.object(pa, "run", lambda *a, **k: res), mock.patch.object(pa.hubconf, "workspace", lambda n: self.ws), \
                mock.patch("builtins.print"):
            return pa._mailbox("proj", ids)

    def test_a_reply_of_any_type_goes_to_the_sender(self):
        self.send("m1", "proj", "request")
        self.apply(["m1"], {"replies": [{"reply_to": "m1", "type": "question", "text": "which?"}], "done": [],
                            "ask_owner": [], "summary": ""})
        sent = self.payloads("type='message' AND source='proj' AND to_project='other'")
        self.assertEqual([p["type"] for p in sent], ["question"])

    def test_a_letter_someone_else_took_meanwhile_is_answered_all_the_same(self):
        self.send("m1", "proj", "request")
        self.m.publish(self.c, "s1", "message", "proj", "other", "m1", {"type": "done", "text": "a session did", "reply_to": "m1"})
        self.apply(["m1"], {"replies": [{"reply_to": "m1", "type": "done", "text": "me too"}], "done": [{"id": "m1"}],
                            "ask_owner": [], "summary": ""})
        self.assertEqual(len(self.rows("type='message' AND source='proj' AND ref='m1'")), 2)
        self.assertTrue(self.m.is_done(self.c, "m1"))

    def test_a_failed_run_is_held_and_asks_and_the_question_stays_when_the_letter_closes(self):
        self.send("m1", "proj", "request")
        self.apply(["m1"], None, ok=False)
        self.assertEqual(self.vias("m1"), ["held"])
        self.assertEqual(len(self.rows("id='m1:propose-failed'")), 1)
        self.m.publish(self.c, "m1:done", "message.done", "proj", "proj", "m1", {"note": "a session did"})
        self.assertIn("m1:propose-failed", [q["id"] for q in self.m.open_questions(self.c)])


class TodaySettings(unittest.TestCase):
    """What a confined run of the hub may write and read, before this step: the whole hub is denied, notes included."""

    def setUp(self):
        import tempfile
        self.hub = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(__import__("shutil").rmtree, self.hub, True)
        (self.hub / ".state" / "agent-jobs").mkdir(parents=True)
        (self.hub / ".logs").mkdir()
        (self.hub / ".agent-memory" / "memory").mkdir(parents=True)

    def deny(self, project="hub-name"):
        import hubconf
        import runtime_claude
        pol = {"other_projects": [], "deny_write": [self.hub], "deny_read": [], "platform_commands": [],
               "platform_cli": ["brunna send"], "write": [], "network": [], "credential_files": [], "credentials": [],
               "workspace": str(self.hub)}
        doc = {"name": project, "paths": {"workspace": str(self.hub)}}
        with mock.patch("policy.policy", lambda name, d, i: pol), mock.patch.object(hubconf, "STATE", self.hub / ".state"), \
                mock.patch("registry.refresh", lambda: {"projects": {project: doc}}):
            return json.loads(runtime_claude.platform_settings(project).read_text())["permissions"]["deny"]

    def test_the_hub_is_denied_whole_and_nothing_stops_a_read(self):
        import runtime_claude
        deny = self.deny()
        self.assertIn(f"Edit({runtime_claude._tilde(self.hub)}/**)", deny)
        self.assertFalse([r for r in deny if r.startswith("Read(") and ".state" in r])


if __name__ == "__main__":
    unittest.main()
