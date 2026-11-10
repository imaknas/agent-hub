"""The headless handler as last resort (docs/design/mailbox-session-first.md, "Triage"), tick by tick with hooks in
between. A temporary bus, log and state folder (tests.test_mailbox_dispatch's setUp); processes are stubbed, no agent is
started.

    bin/py -m unittest tests.test_mailbox_triage
"""
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "bin"))
import hubconf  # noqa: E402
import test_handler_confinement as thc  # noqa: E402
import test_mailbox_state as tms  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFINED = {"AGENT_HUB_CONFINED": "1"}
ESCALATION = {"HUB_LLM_POINT": "mailbox-escalation", **CONFINED}
FORK = {"HUB_LLM_POINT": "mailbox-conversation", **CONFINED}
EVENT_JOB = {"HUB_LLM_POINT": "agent-job-event", **CONFINED}
CLOCK_JOB = {"HUB_LLM_POINT": "agent-job-confined", **CONFINED}


def with_letters(env, *ids):
    return {**env, "MAILBOX_LETTERS": json.dumps(ids)}


def load_propose():
    spec = importlib.util.spec_from_file_location("propose_apply_triage", ROOT / "bin" / "propose_apply.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Base(tms.Harness):
    def setUp(self):
        tms.tmd.Dispatch.setUp(self)
        self.started = []
        self.hub = self.m.hubconf.hub_project()
        self.owner = self.m.OWNER_ADDRESS
        self.m.RECORDS = False   # read from the machine's settings at import: each test says which path it means

    def real_handler(self):
        """The real run_handler, with its process stubbed, whichever handler the machine's settings name."""
        self.m.run_handler = self.m.real_run_handler
        get = self.m.hubconf.get
        settings = mock.patch.object(self.m.hubconf, "get", lambda k, d=None: {} if k == "mailbox" else get(k, d))
        settings.start()
        self.addCleanup(settings.stop)

        real = subprocess.run
        self.start_exit = 0

        def run(cmd, **kw):
            if "run-agent-job.sh" not in str(cmd[0]):   # ps, and the like
                return real(cmd, **kw)
            if self.start_exit:
                return subprocess.CompletedProcess(cmd, self.start_exit, "", "workdir does not exist")
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

    def refused(self, *args, env=None, session=None):
        """The command is refused, and its reason given."""
        with self.assertRaises(SystemExit) as e:
            self.cli(*args, env=env, session=session)
        self.assertIsInstance(e.exception.code, str, args)
        return e.exception.code

    def letters_of(self, run):
        return json.loads(run["env"]["MAILBOX_LETTERS"])


class Triage(Base):
    def test_it_leaves_the_letter_new_for_hooks_and_is_not_repeated(self):
        self.real_handler()
        self.send("m1", "proj", "request")
        self.dispatch()
        self.assertEqual(len(self.started), 1)
        self.assertEqual(self.vias("m1"), ["triage"])
        self.assertEqual(self.started[0]["env"]["AGENT_JOB_POINT"], "mailbox-escalation")
        self.assertEqual(self.state("m1").taken, "", "it takes nothing")
        self.assertEqual(self.m.claimed(self.c, ["m1"]), set())
        self.assertEqual(self.dispatch(), 1, "it waits")
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual(len(self.started), 1, "once per letter")
        self.rt.live.add("S1")
        self.assertIn("[m1]", self.hook("prompt", "S1"), "the next session is told it in full")
        self.assertEqual(self.told("m1"), ["S1"])
        self.assertNotIn(self.m._(" (the platform is handling it, nothing to do)"), self.hook("session-start", "S2"))

    def test_run_handler_itself_does_it_once(self):
        self.real_handler()
        self.send("m1", "proj", "request")
        r = self.m.get_message(self.c, "m1")
        self.m.run_handler(self.c, "proj", self.ws, [r])
        self.m.run_handler(self.c, "proj", self.ws, [r])
        self.assertEqual(len(self.started), 1)

    def test_the_readers_of_via_count_and_name_it(self):
        self.real_handler()
        self.send("m1", "proj", "request")
        self.dispatch()
        self.assertEqual(self.m.runs_today(self.c, "proj"), 1)
        self.assertEqual(self.m.platform_rounds(self.c, [self.m.get_message(self.c, "m1")]), 1)
        self.assertEqual(self.m.letter_path(self.c, self.m.get_message(self.c, "m1")), ["triage"])
        spec = importlib.util.spec_from_file_location("status_page_triage", ROOT / "bin" / "gen-status-page.py")
        page = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(page)
        self.assertIn("triage", page.path_labels())

    def test_the_budget_stops_the_run_says_so_once_and_the_sender_still_hears(self):
        self.real_handler()
        self.m.DAILY_RUNS = 0
        self.send("m1", "proj", "request")
        self.dispatch()
        self.dispatch()
        self.assertEqual((self.started, self.vias("m1")), ([], []))
        self.assertEqual(len(self.rows("id='m1:received'")), 1)
        self.assertEqual(len([x for x in self.logged if self.m.DAILY_RUNS == 0 and "proj" in x]), 1, self.logged)
        self.m.DAILY_RUNS = 6
        self.dispatch()
        self.assertEqual(self.vias("m1"), ["triage"], "with room again it goes")
        self.assertEqual(len(self.rows("id='m1:received'")), 1, "the line goes once")

    def test_a_letter_a_session_sees_after_triage_goes_on_like_any_other(self):
        self.real_handler()
        self.send("m1", "proj", "request")
        self.dispatch()
        self.rt.live.add("S1")
        self.hook("prompt", "S1")
        self.cli("ack", "m1", session="S1")
        self.assertTrue(self.state("m1").taken)
        self.cli("done", "m1", session="S1")
        self.assertEqual(self.state("m1").taken, "good")
        self.assertEqual(len(self.started), 1)

    def test_a_fork_comes_after_triage_when_a_session_can_be_forked_and_only_once(self):
        self.real_handler()
        self.send("m1", "proj", "request", minutes_ago=30)
        self.dispatch()
        self.assertEqual(len(self.started), 1)
        self.rt.live.add("S1")
        self.rt.tokens["S1"] = 1000
        self.seen("S1", minutes_ago=30)
        self.dispatch()
        self.assertEqual(self.resumed, [("m1", "S1")])
        self.age("m1:delivered:resume", self.m.RESUME_TIMEOUT_MIN + 1)  # that fork ended and didn't close it
        self.assertEqual(self.state("m1").taken, "", "open again")
        self.dispatch()
        self.assertEqual((len(self.resumed), len(self.started)), (1, 1), "nothing starts again")

    def test_a_fork_that_dies_before_triage_gets_one_triage(self):
        self.real_handler()
        self.rt.live.add("S1")
        self.rt.tokens["S1"] = 1000
        self.seen("S1", minutes_ago=30)
        self.send("m1", "proj", "request", minutes_ago=30)
        self.dispatch()
        self.assertEqual((self.resumed, self.started), ([("m1", "S1")], []))
        self.age("m1:delivered:resume", self.m.RESUME_TIMEOUT_MIN + 1)
        self.dispatch()
        self.dispatch()
        self.assertEqual((len(self.resumed), len(self.started)), (1, 1))

    def test_a_transcript_is_read_once_a_tick_however_many_letters_stay_open(self):
        calls = []
        self.rt.context_size = lambda sid: calls.append(sid) or 900_000
        self.rt.live.add("S1")
        self.seen("S1", minutes_ago=30)
        for i in range(5):
            self.send(f"m{i}", "proj", "request", minutes_ago=30)
        self.dispatch()
        self.assertEqual(calls, ["S1"])

    def test_an_mcp_hold_is_said_once(self):
        self.real_handler()
        self.running.add("mcp:1:2")
        self.seen("mcp:1:2", via="tool", runtime="mcp:codex")
        self.send("m1", "proj", "request", minutes_ago=self.m.MCP_HOLD_MAX_MIN + 1)
        for _ in range(3):
            self.dispatch()
        self.assertEqual(len([x for x in self.logged if "m1" in x and "MCP" in x]), 1, self.logged)

    def test_status_says_triaged_is_waiting(self):
        self.real_handler()
        self.send("m1", "proj", "request", minutes_ago=200)
        self.dispatch()
        st = self.status()
        self.assertEqual([(w["id"], w["why"]) for w in st["waiting"]], [("m1", "triaged")])
        self.assertEqual(st["stuck"], [])
        self.assertEqual(st["projects"]["proj"]["undelivered"], 1, "a triage isn't a session having seen it")

    def test_a_propose_run_is_started_the_same_way(self):
        self.real_handler()
        self.send("m1", "proj", "request")
        started = []

        class P:
            def __init__(self, cmd, **kw):
                started.append((cmd, kw["env"]))
        with mock.patch.object(self.m.subprocess, "Popen", P), \
                mock.patch.object(self.m.hubconf, "projects", lambda: {"proj": {}}), \
                mock.patch.object(self.m.hubconf, "get", lambda k, d=None: {"handler": "propose"} if k == "mailbox" else d):
            self.dispatch()
        cmd, env = started[0]
        self.assertIn("propose_apply.py", cmd[1])
        self.assertEqual(cmd[2:], ["mailbox", "proj", "m1"])
        self.assertEqual(self.vias("m1"), ["triage"], "no claim")
        self.assertEqual(json.loads(env["MAILBOX_LETTERS"]), ["m1"])


class ProposeRefused(Base):
    """mailbox.handler: propose for a sandboxed project, or one the index lacks: nothing starts, nothing is spent, the letter stays open
    for sessions and the reason shows."""

    def refused_with(self, docs):
        self.real_handler()
        self.send("m1", "proj", "request")
        self.send("m2", "proj", "request")
        started = []
        with mock.patch.object(self.m.subprocess, "Popen", lambda cmd, **kw: started.append(cmd)), \
                mock.patch.object(self.m.hubconf, "projects", lambda: docs), \
                mock.patch.object(self.m.hubconf, "get", lambda k, d=None: {"handler": "propose"} if k == "mailbox" else d):
            for _i in range(3):
                self.dispatch()
                self.c.execute("UPDATE events SET ts=? WHERE type='job.failed'", (tms.ago(self.m.START_RETRY_MIN + 1),))   # were it a retry, it's due
            why = load_propose().unsupported("proj")
            status = self.status()
        self.assertEqual(started, [], "no run")
        for mid in ("m1", "m2"):
            self.assertEqual(self.vias(mid), [], "no triage spent, and nobody holds it")
            self.assertEqual(self.rows("id=?", f"{mid}:received"), [], "no word to the sender about a run that isn't")
            self.assertEqual(self.rows("type='message.done' AND ref=?", mid), [], "never closed as handled")
            self.assertEqual(self.rows("type='job.failed' AND ref=?", mid), [], "not a start that failed")
        self.assertEqual(self.m.runs_today(self.c, "proj"), 0)
        failed = self.rows("type='job.failed'")
        self.assertEqual(len(failed), 1, "one a day, however many letters and ticks")
        self.assertEqual(failed[0]["id"], f"propose-refused:proj:{self.m.now():%Y%m%d}", "by the day")
        self.assertEqual(json.loads(failed[0]["payload"])["detail"], why)
        self.assertEqual({w["id"]: w["why"] for w in status["waiting"]}, {"m1": "refused", "m2": "refused"})
        self.assertEqual(status["stuck"], [], "it waits for a session: not a letter nobody handled, one red line each")
        return why

    def test_a_session_is_still_told(self):
        self.refused_with({"proj": {"enforce": {"sandbox": True}}})
        told = [r["id"] for r in self.m.inbox(self.c, "proj")]
        self.assertEqual(sorted(told), ["m1", "m2"])

    def test_the_handler_changing_lets_it_start_again(self):
        self.refused_with({"proj": {"enforce": {"sandbox": True}}})
        started = []
        with mock.patch.object(self.m.subprocess, "Popen", lambda cmd, **kw: started.append(cmd)), \
                mock.patch.object(self.m.hubconf, "projects", lambda: {"proj": {}}), \
                mock.patch.object(self.m.hubconf, "get", lambda k, d=None: {"handler": "propose"} if k == "mailbox" else d):
            self.dispatch()
        self.assertEqual(len(started), 1, "nothing was spent, so nothing stands in the way")

    def test_sandboxed(self):
        self.assertIn("proj", self.refused_with({"proj": {"enforce": {"sandbox": True}}}))

    def test_not_in_the_index(self):
        self.assertIn("proj", self.refused_with({}))


class StartFailed(Base):
    """run-agent-job.sh didn't start: the letter keeps its one triage, the budget isn't spent, the sender hears nothing
    yet, a later tick tries again, a bounded number of times, and the failure shows."""

    def failures(self, mid="m1"):
        return self.rows("type='job.failed' AND ref=?", mid)

    def retry_due(self, mid="m1"):
        self.c.execute("UPDATE events SET ts=? WHERE type='job.failed' AND ref=?", (tms.ago(self.m.START_RETRY_MIN + 1), mid))

    def test_it_spends_nothing_and_says_nothing_to_the_sender(self):
        self.real_handler()
        self.start_exit = 1
        self.send("m1", "proj", "request")
        self.dispatch()
        self.assertEqual(self.vias("m1"), [], "no triage spent")
        self.assertEqual(self.m.runs_today(self.c, "proj"), 0, "no run from the budget")
        self.assertEqual(self.rows("id='m1:received'"), [], "no line before a run has started")
        p = json.loads(self.failures()[0]["payload"])
        self.assertEqual(p["job"], "mailbox-start:proj")
        self.assertIn("workdir does not exist", p["detail"])

    def test_a_later_tick_tries_again_and_the_run_then_counts(self):
        self.real_handler()
        self.start_exit = 1
        self.send("m1", "proj", "request")
        self.dispatch()
        self.dispatch()
        self.assertEqual(len(self.failures()), 1, "not every minute")
        self.start_exit = 0
        self.dispatch()
        self.assertEqual(self.started, [], "still waiting its turn")
        self.retry_due()
        self.dispatch()
        self.assertEqual(len(self.started), 1)
        self.assertEqual(self.vias("m1"), ["triage"])
        self.assertEqual(len(self.rows("id='m1:received'")), 1)

    def test_it_stops_trying_and_the_letter_shows_as_stuck(self):
        self.real_handler()
        self.start_exit = 1
        self.send("m1", "proj", "request", minutes_ago=200)
        for _ in range(self.m.START_TRIES + 3):
            self.dispatch()
            self.retry_due()
        self.assertEqual(len(self.failures()), self.m.START_TRIES)
        self.assertEqual(self.vias("m1"), [])
        self.assertEqual([s["id"] for s in self.status()["stuck"]], ["m1"], "alerted, not quietly waiting")

    def test_one_senders_failure_does_not_stop_the_next(self):
        self.real_handler()
        self.start_exit = 1
        self.send("m1", "proj", "request", src="other")
        self.send("m2", "proj", "request", src=self.hub)
        self.dispatch()
        self.start_exit = 0
        self.dispatch()
        self.assertEqual([self.letters_of(r) for r in self.started], [["m2"]])

    def test_a_propose_run_that_cannot_start_counts_the_same(self):
        self.real_handler()
        self.send("m1", "proj", "request")

        def nope(*a, **k):
            raise OSError("no such file")
        with mock.patch.object(self.m.subprocess, "Popen", nope), mock.patch.object(self.m.hubconf, "projects", lambda: {"proj": {}}), \
                mock.patch.object(self.m.hubconf, "get", lambda k, d=None: {"handler": "propose"} if k == "mailbox" else d):
            self.dispatch()
        self.assertEqual(self.vias("m1"), [])
        self.assertEqual(self.rows("id='m1:received'"), [])
        self.assertEqual(len(self.failures()), 1)

    def test_a_failed_start_shows_on_the_status_page(self):
        spec = importlib.util.spec_from_file_location("status_page_start", ROOT / "bin" / "gen-status-page.py")
        page = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(page)
        for job in ("mailbox-start:proj", "mailbox-propose:proj", "llm:mailbox-escalation", "llm:mailbox-conversation"):
            self.assertTrue(page.mail_failure({"job": job}), job)
        self.assertFalse(page.mail_failure({"job": "llm:librarian-state"}))


class OnePerSender(Base):
    def test_one_run_per_sender(self):
        self.real_handler()
        self.send("m1", "proj", "request", src="other")
        self.send("m2", "proj", "request", src=self.hub)
        self.send("m3", "proj", "question", src="other")
        self.dispatch()
        self.assertEqual([self.letters_of(r) for r in self.started], [["m1", "m3"]])
        self.assertEqual(self.vias("m2"), [], "the next sender goes next tick")
        self.dispatch()
        self.assertEqual([self.letters_of(r) for r in self.started], [["m1", "m3"], ["m2"]])
        self.dispatch()
        self.assertEqual(len(self.started), 2)

    def test_one_at_a_time_per_project_still(self):
        self.real_handler()
        self.send("m1", "proj", "request")
        job = self.m.AGENT_JOBS / "mailbox-proj"
        job.mkdir(parents=True)
        (job / "status.json").write_text('{"state": "running"}')
        self.dispatch()
        self.assertEqual((self.started, self.vias("m1")), ([], []))

    def test_a_letter_whose_id_isnt_one_stays_for_the_sessions(self):
        """confined-mail-tools.md M3: an id a sender chose before send checked it never reaches a run's letter list (one bad
        id would refuse the whole batch) and is never routed or forked; the sender's other letters go on."""
        self.real_handler()
        self.send("m1,victim", "proj", "request")
        self.send("m2", "proj", "request")
        self.dispatch()
        self.assertEqual([self.letters_of(s) for s in self.started], [["m2"]])
        self.assertFalse(self.m.is_done(self.c, "m1,victim"))

    def test_a_fork_gets_its_own_letter(self):
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
        self.send("m2", "proj", "request", src=self.hub)
        with mock.patch.object(self.m.subprocess, "Popen", P), \
                mock.patch.object(self.m, "branch_prompt", lambda r, p: "t"):
            self.m.resume(self.c, self.m.get_message(self.c, "m1"), "S1", self.ws, self.rt)
        self.assertEqual(json.loads(seen["MAILBOX_LETTERS"]), ["m1"])


class Hold(Base):
    def thread(self, last="next"):
        prev = None
        for i in range(self.m.THREAD_MAX_ROUNDS):
            self.send(f"t{i}", "proj", "question", reply_to=prev)
            self.claim(f"t{i}", "triage", run=f"x{i}")
            prev = f"t{i}"
        self.send(last, "proj", "question", reply_to=prev)

    def test_held_leaves_it_open_and_hold_does_not_repeat(self):
        self.thread()
        self.logged.clear()
        for _ in range(3):
            self.dispatch()
        self.assertEqual(self.vias("next"), ["held"])
        self.assertEqual(len(self.rows("id='next:loop'")), 1)
        self.assertEqual(len([x for x in self.logged if "next" in x]), 1, self.logged)
        self.assertEqual(self.state("next").taken, "")
        self.assertEqual(self.handled + self.resumed, [])
        self.rt.live.add("S1")
        self.assertIn("[next]", self.hook("prompt", "S1"))

    def test_hold_called_again_says_nothing_more(self):
        self.send("m1", "proj", "request")
        r = self.m.get_message(self.c, "m1")
        self.m.hold(self.c, r)
        self.m.hold(self.c, r)
        self.assertEqual(self.vias("m1"), ["held"])
        self.assertEqual(len(self.rows("id='m1:loop'")), 1)
        self.assertEqual(len(self.logged), 1)

    def test_it_stops_platform_runs_not_a_session(self):
        """A letter just come, in a thread past its rounds, with a session in use: the session's wait goes on as for any
        letter; hold() comes only when the platform would start something."""
        self.rt.live.add("S1")
        self.seen("S1")
        self.thread()
        self.dispatch()
        self.assertEqual(self.vias("next"), [], "not held yet")
        self.age("next", self.m.CONVERSATION_WAIT_MIN + 1)
        self.dispatch()
        self.assertEqual(self.vias("next"), ["held"])
        self.dispatch()
        self.assertEqual(len(self.rows("id='next:loop'")), 1)
        self.assertEqual(self.handled + self.resumed, [])

    def test_a_letter_triaged_as_the_last_round_asks_nothing(self):
        """Its own triage row counts as a round: with nothing left to start, the loop guard has nothing to stop."""
        for i in range(self.m.THREAD_MAX_ROUNDS - 1):
            self.send(f"t{i}", "proj", "question", reply_to=f"t{i - 1}" if i else None)
            self.claim(f"t{i}", "triage", run=f"x{i}")
        self.send("last", "proj", "question", reply_to=f"t{self.m.THREAD_MAX_ROUNDS - 2}")
        self.dispatch()
        self.assertEqual(self.handled, [("proj", ["last"])])
        self.dispatch()
        self.assertEqual(self.vias("last"), ["triage"])
        self.assertEqual(self.rows("id='last:loop'"), [])

    def test_a_held_letter_from_before_keeps_starting_nothing(self):
        self.send("m1", "proj", "request", minutes_ago=30)
        self.claim("m1", "held")
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual(self.handled + self.resumed, [])

    def test_a_letter_with_an_old_handler_row_never_comes_back(self):
        self.send("m1", "proj", "request", minutes_ago=500)
        self.claim("m1", "handler", run="h0")
        self.assertEqual(self.state("m1").taken, "good")
        self.assertEqual(self.dispatch(), 0)
        self.assertEqual(self.hook("prompt", "S1"), "")


class FixedLine(Base):
    def line(self, mid="m1"):
        got = self.rows("id=?", f"{mid}:received")
        return got[0] if got else None

    def test_it_neither_takes_nor_wakes_and_carries_the_chain(self):
        self.real_handler()
        woke = []
        self.m.wake = lambda c, rt, r, sid: woke.append(sid)
        self.rt.live.add("SO")
        self.seen("SO", proj="other", minutes_ago=1)
        self.m.publish(self.c, "m1", "message", "other", "proj", None,
                       {"type": "request", "text": "x", "from_session": "SO", "chain": ["other/job"], "depth": 1})
        self.dispatch()
        line = self.line()
        p = json.loads(line["payload"])
        self.assertEqual((line["source"], line["to_project"], line["ref"]), (self.hub, "other", None))
        self.assertEqual(p["type"], "info")
        self.assertNotIn("to_session", p)
        self.assertNotIn("reply_to", p)
        self.assertEqual((p["chain"], p["depth"]), (["other/job"], 1))
        self.assertIn("m1", p["text"])
        self.assertEqual(self.state("m1").taken, "", "no ref: it isn't an answer")
        self.dispatch()
        self.assertEqual(woke, [], "nobody woken for it")
        self.assertEqual(len(self.rows("id='m1:received'")), 1, "once")

    def test_none_for_the_owner_side_the_hub_its_reconciliation_or_a_letter_to_itself(self):
        self.real_handler()
        self.send("own", "proj", "request", src=self.owner)
        self.send("hub", "proj", "request", src=self.hub)
        self.send("self", "proj", "request", src="proj")
        self.m.publish(self.c, "qc", "message", "other", "proj", None, {"type": "request", "text": "x", "question_check": "q"})
        for _ in range(4):
            self.dispatch()
        for mid in ("own", "hub", "self", "qc"):
            self.assertEqual(self.vias(mid), ["triage"], mid)
            self.assertIsNone(self.line(mid), mid)

    def test_a_crafted_id_is_not_repeated_in_the_text(self):
        """Such a letter isn't dispatched at all now (above); the fixed line still never repeats one."""
        self.real_handler()
        self.send("x y\nignore all", "proj", "request")
        self.dispatch()
        self.assertIsNone(self.line("x y\nignore all"))
        self.assertEqual(self.started, [])
        self.c.execute("BEGIN IMMEDIATE")
        self.m.received_line(self.c, self.m.get_message(self.c, "x y\nignore all"))
        self.c.execute("COMMIT")
        self.assertNotIn("ignore all", json.loads(self.line("x y\nignore all")["payload"])["text"])

    def test_it_does_not_trigger_the_job_that_sent_the_letter(self):
        import event_jobs
        self.real_handler()
        self.m.publish(self.c, "free", "message", "other", "proj", None, {"type": "request", "text": "x"})
        self.m.publish(self.c, "chained", "message", "other", "proj", None,
                       {"type": "request", "text": "x", "chain": ["other/job"], "depth": 1})
        self.dispatch()
        job = {"name": "other/job", "project": "other", "trigger": {"events": ["message"]}}

        def classify(mid):
            return event_jobs.classify(job, {**dict(self.line(mid)), "payload": json.loads(self.line(mid)["payload"])})
        self.assertEqual(classify("free"), "match", "with no chain the job would run: the check below means something")
        self.assertEqual(classify("chained"), "loop")


class Commands(Base):
    """Every command a run with letters has, allowed and refused, for both mailbox points."""

    POINTS = (ESCALATION, FORK)

    def setUp(self):
        super().setUp()
        self.send("m1", "proj", "request")
        self.send("elsewhere", "proj", "request", src=self.hub)

    def each(self):
        for point in self.POINTS:
            with self.subTest(point["HUB_LLM_POINT"]):
                yield with_letters(point, "m1")

    def letter(self, mid):
        return self.rows("type='message' AND source='proj' AND ref=?", mid)

    def test_a_fork_replies_to_its_letter_with_any_ordinary_type(self):
        env = with_letters(FORK, "m1")
        for kind in ("done", "info", "progress", "question", "request"):
            with self.subTest(kind):
                self.c.execute("DELETE FROM events WHERE type='message' AND source='proj'")
                self.cli("send", "--to", "other", "--reply-to", "m1", "--type", kind, "--text", "x", env=env)
                self.assertEqual([json.loads(r["payload"])["type"] for r in self.letter("m1")], [kind])

    def test_a_fork_stays_with_its_letter(self):
        env = with_letters(FORK, "m1")
        for args in (("--to", self.hub, "--reply-to", "m1", "--type", "question"),      # not its sender
                     ("--to", self.owner, "--reply-to", "m1", "--type", "info"),
                     ("--to", self.hub, "--reply-to", "elsewhere", "--type", "question"),  # not its letter
                     ("--to", "other", "--type", "question"),                            # no letter at all
                     ("--to", "other", "--type", "info"),
                     ("--to", self.owner, "--type", "info")):
            with self.subTest(args):
                self.refused("send", *args, "--text", "x", env=env)
        self.assertEqual(self.rows("type='message' AND source='proj'"), [])
        self.refused("done", "elsewhere", env=env)
        self.refused("show", "elsewhere", env=env)
        self.assertNotIn("[elsewhere]", self.cli("inbox", env=env))

    def test_a_handler_replies_done_only(self):
        env = with_letters(ESCALATION, "m1")
        for kind in ("info", "progress", "question", "request"):
            with self.subTest(kind):
                self.refused("send", "--to", "other", "--reply-to", "m1", "--type", kind, "--text", "x", env=env)
        self.assertEqual(self.letter("m1"), [])

    def test_a_platform_process_around_the_run_is_not_the_run(self):
        """What llm.py and run-agent-job.sh do around the agent inherits the list and has no point: only the agent has it."""
        around = {"MAILBOX_LETTERS": json.dumps(["m1"]), "AGENT_JOB_POINT": "mailbox-escalation", "MAILBOX_PROJECT": "proj"}
        self.cli("publish", "job.failed", "--source", "llm", "--payload", json.dumps({"job": "llm:x", "detail": "y"}), env=around)
        self.assertEqual(len(self.rows("type='job.failed'")), 1)
        for point in (ESCALATION, FORK, {"HUB_LLM_POINT": "mailbox-propose"}):
            with self.subTest(point["HUB_LLM_POINT"]):
                self.refused("publish", "job.failed", "--source", "llm", "--payload", "{}", env=with_letters(point, "m1"))

    def test_reply_done_to_its_letter(self):
        for env in self.each():
            self.c.execute("DELETE FROM events WHERE type='message' AND source='proj'")
            self.cli("send", "--to", "other", "--reply-to", "m1", "--type", "done", "--text", "answer", env=env)
            self.assertEqual(len(self.letter("m1")), 1)

    def test_info_only_to_an_asks_letter(self):
        self.refused("send", "--to", "other", "--reply-to", "m1", "--type", "info", "--text", "x",
                     env=with_letters(ESCALATION, "m1"))
        for i, env in enumerate(self.each()):
            asks = f"asks{i}"
            self.m.publish(self.c, asks, "message", self.owner, "proj", None,
                           {"type": "question", "text": "x", "effect": "asks", "handoff_id": i})
            self.cli("send", "--to", self.owner, "--reply-to", asks, "--type", "info", "--text", "the answer",
                     env=with_letters(env, asks))
            self.assertEqual(len(self.letter(asks)), 1)

    def test_a_question_to_the_owner(self):
        for env in self.each():
            before = len(self.rows("type='message' AND to_project=?", self.owner))
            self.cli("send", "--to", self.owner, "--type", "question", "--text", "which?", env=env)
            self.assertEqual(len(self.rows("type='message' AND to_project=?", self.owner)), before + 1)

    def test_a_question_to_the_owner_as_a_record(self):
        self.m.RECORDS = True
        for env in self.each():
            before = len(self.rows("type='record'"))
            self.cli("send", "--to", self.owner, "--type", "question", "--text", "which?", env=env)
            self.assertEqual(len(self.rows("type='record'")), before + 1)
        self.assertEqual(self.rows("type='message' AND to_project=?", self.owner), [], "no v1 letter beside it")

    def test_close_its_letter_and_no_other(self):
        for env in self.each():
            self.refused("done", "elsewhere", env=env)
            self.assertFalse(self.m.is_done(self.c, "elsewhere"))
        self.cli("done", "m1", env=with_letters(ESCALATION, "m1"))
        self.assertTrue(self.m.is_done(self.c, "m1"))

    def test_no_other_recipient_type_or_reply(self):
        for env in self.each():
            for args in (("--to", "other", "--type", "info"),                        # no notice
                         ("--to", "other", "--type", "question"),
                         ("--to", "other", "--type", "request"),
                         ("--to", "other", "--type", "done"),                        # a done with no letter
                         ("--to", self.hub, "--reply-to", "m1", "--type", "done"),   # not its sender
                         ("--to", self.hub, "--reply-to", "elsewhere", "--type", "done"),
                         ("--to", self.owner, "--type", "info"),
                         ("--to", self.owner, "--type", "progress")):
                self.refused("send", *args, "--text", "x", env=env)
            self.assertEqual(self.letter("m1"), [])
            self.assertEqual(self.rows("type='message' AND source='proj'"), [])

    def test_no_ack_no_task_no_registry_no_publish(self):
        for env in self.each():
            self.refused("ack", "m1", env=env)
            self.assertEqual(self.vias("m1"), [])
            self.refused("send", "--to", self.owner, "--type", "task", "--text", "x", env=env)
            self.refused("send", "--to", self.owner, "--type", "done", "--task", "k", "--text", "x", env=env)
            self.refused("send", "--to", self.hub, "--type", "manifest.update", "--text", "x", env=env)
            self.refused("publish", "job.failed", "--source", "proj", env=env)
            self.refused("send", "--to", "other", "--reply-to", "m1", "--type", "done", "--text", "x", "--id", "mine", env=env)
            self.refused("send", "--from", self.hub, "--to", self.owner, "--type", "question", "--text", "x", env=env)

    def test_it_reads_only_its_letters_and_their_threads(self):
        self.send("before", "proj", "request")
        self.send("m2", "proj", "question", reply_to="before")
        self.m.publish(self.c, "z", "message", "other", self.hub, None, {"type": "request", "text": "another's secret"})
        self.send("crafted", "proj", "request", reply_to="z")
        for point in self.POINTS:
            with self.subTest(point["HUB_LLM_POINT"]):
                env = with_letters(point, "m2", "crafted")
                out = self.cli("inbox", env=env)
                self.assertIn("[m2]", out)
                self.assertIn("[crafted]", out)
                self.assertNotIn("[m1]", out)
                self.assertNotIn("[elsewhere]", out)
                self.assertIn("[m2]", self.cli("show", "m2", env=env))
                self.assertIn("[before]", self.cli("show", "before", env=env), "its thread")
                self.assertIn("[before]", self.cli("thread", "m2", env=env))
                self.assertNotIn("another's secret", self.cli("thread", "crafted", env=env), "a crafted reply_to")
                self.refused("show", "m1", env=env)
                self.refused("thread", "elsewhere", env=env)
                self.refused("show", "z", env=env)
                self.refused("open-questions", env=env)
                self.refused("open-questions", "--json", env=env)

    def test_its_inbox_marks_nothing_and_sees_a_reply_despite_the_wait(self):
        self.send("r1", "proj", "question", to_session="S1", minutes_ago=2)
        for env in (with_letters(ESCALATION, "r1"), with_letters(FORK, "r1")):
            self.assertIn("[r1]", self.cli("inbox", env=env), "or it gets an empty mailbox for a reply")
        self.assertEqual(self.vias("r1"), [])
        self.assertEqual(self.routes("r1"), [])
        self.refused("inbox", env=ESCALATION)   # a confined run without letters reads no mailbox (M3)
        self.assertEqual(self.vias("r1"), [])
        self.rt.live.add("S1")
        self.assertIn("[r1]", self.hook("prompt", "S1"), "the handler's reading counted for nothing")

    def test_an_unreadable_list_is_no_letters(self):
        for raw in ("m1", "{", '{"m1": 1}', "", "[1]"):
            env = {**ESCALATION, "MAILBOX_LETTERS": raw}
            self.assertNotIn("[m1]", self.cli("inbox", env=env), raw)
            self.refused("done", "m1", env=env)

    def test_a_list_is_not_split(self):
        self.send("m1,elsewhere", "proj", "request")
        env = with_letters(ESCALATION, "m1,elsewhere")
        self.refused("done", "elsewhere", env=env)
        self.refused("done", "m1", env=env)
        self.cli("done", "m1,elsewhere", env=env)


class Taken(Base):
    """A session and a handler don't both answer."""

    def setUp(self):
        super().setUp()
        self.send("m1", "proj", "request")
        self.reply = ("send", "--to", "other", "--reply-to", "m1", "--type", "done", "--text", "mine")
        self.env = with_letters(ESCALATION, "m1")
        self.rt.live.add("S1")

    def test_a_sessions_ack(self):
        self.cli("ack", "m1", session="S1")
        self.refused(*self.reply, env=self.env)
        self.refused("done", "m1", env=self.env)
        self.assertFalse(self.m.is_done(self.c, "m1"))

    def test_once_that_sessions_ack_ends_it_is_free_again(self):
        self.cli("ack", "m1", session="S1")
        self.close("S1")
        self.cli(*self.reply, env=self.env)

    def test_a_sessions_reply_or_close(self):
        self.cli("send", "--to", "other", "--reply-to", "m1", "--type", "info", "--text", "a session's", session="S1")
        self.refused(*self.reply, env=self.env)
        self.refused("done", "m1", env=self.env)
        self.send("m2", "proj", "request")
        self.cli("done", "m2", session="S1")
        self.refused("send", "--to", "other", "--reply-to", "m2", "--type", "done", "--text", "x",
                     env=with_letters(ESCALATION, "m2"))

    def test_its_own_reply_and_close_in_either_order(self):
        self.cli(*self.reply, env=self.env)
        self.cli("done", "m1", env=self.env)
        self.send("m2", "proj", "request")
        env = with_letters(ESCALATION, "m2")
        self.cli("done", "m2", env=env)
        self.cli("send", "--to", "other", "--reply-to", "m2", "--type", "done", "--text", "and a word", env=env)

    def test_a_fork_is_another_for_the_handler_and_itself_for_a_fork(self):
        self.claim("m1", "resume", session="S1", run="r0")
        self.refused(*self.reply, env=self.env)
        fork = with_letters(FORK, "m1")
        self.cli(*self.reply, env=fork)
        self.cli("done", "m1", env=fork)

    def test_a_held_or_triaged_letter_is_free(self):
        self.claim("m1", "triage", run="h0")
        self.claim("m1", "held")
        self.cli(*self.reply, env=self.env)

    def test_a_reply_by_another_run_counts(self):
        self.m.publish(self.c, "f1", "message", "proj", "other", "m1",
                       {"type": "done", "text": "a fork", "reply_to": "m1", "point": "mailbox-conversation"})
        self.refused(*self.reply, env=self.env)


class OwnerQuestionWhenTaken(Base):
    """A question has no --reply-to (on an asks letter one would count as the answer), so it is judged by the run's letters."""

    def setUp(self):
        super().setUp()
        self.send("m1", "proj", "request")
        self.send("m2", "proj", "request")
        self.ask = ("send", "--to", self.owner, "--type", "question", "--text", "which?")
        self.rt.live.add("S1")

    def owner_letters(self):
        return self.rows("type='message' AND to_project=?", self.owner)

    def test_refused_when_all_its_letters_are_taken(self):
        self.ack("m1", "S1")
        self.ack("m2", "S1")
        self.refused(*self.ask, env=with_letters(ESCALATION, "m1", "m2"))
        self.assertEqual(self.owner_letters(), [])

    def test_allowed_while_one_is_free(self):
        self.ack("m1", "S1")
        self.cli(*self.ask, env=with_letters(ESCALATION, "m1", "m2"))
        self.assertEqual(len(self.owner_letters()), 1)

    def test_its_own_reply_does_not_stop_it(self):
        env = with_letters(ESCALATION, "m1")
        self.cli("send", "--to", "other", "--reply-to", "m1", "--type", "done", "--text", "half", env=env)
        self.cli(*self.ask, env=env)
        self.assertEqual(len(self.owner_letters()), 1)

    def test_no_letters_means_no_question(self):
        self.refused(*self.ask, env=with_letters(ESCALATION))
        self.refused(*self.ask, env={**ESCALATION, "MAILBOX_LETTERS": "not json"})
        self.assertEqual(self.owner_letters(), [])

    def test_the_question_on_an_asks_letter_is_not_the_answer(self):
        self.m.publish(self.c, "asks", "message", self.owner, "proj", None,
                       {"type": "question", "text": "x", "effect": "asks", "handoff_id": 1})
        self.cli(*self.ask, env=with_letters(ESCALATION, "asks"))
        self.assertEqual(self.state("asks").taken, "")


class OtherPoints(Base):
    """A confined run with no letters (event and clock jobs) only starts new letters (confined-mail-tools.md M3): with
    no letters of its own, reading would show the whole mailbox and done would close any letter to the project."""

    def setUp(self):
        super().setUp()
        self.send("m1", "proj", "request")
        self.send("m2", "proj", "request", src=self.hub)

    def test_a_run_without_letters_only_starts_new_letters(self):
        for env in (EVENT_JOB, CLOCK_JOB):
            with self.subTest(env["HUB_LLM_POINT"]):
                self.c.execute("DELETE FROM events WHERE type='message' AND source='proj'")
                self.cli("send", "--to", "other", "--type", "info", "--text", "not a reply", env=env)
                self.cli("send", "--to", "other", "--type", "question", "--text", "a question", env=env)
                self.cli("send", "--to", "other", "--type", "request", "--text", "a request", env=env)
                self.cli("send", "--to", self.owner, "--type", "question", "--text", "for the owner", env=env)
                self.assertEqual(len(self.rows("type='message' AND source='proj'")), 4)
                for args in (("inbox",), ("show", "m2"), ("thread", "m2"), ("open-questions",), ("done", "m2"),
                             ("ack", "m1"),
                             ("send", "--to", "other", "--type", "done", "--text", "x"),
                             ("send", "--to", "hub", "--type", "info", "--reply-to", "m2", "--text", "x"),
                             ("send", "--to", self.owner, "--type", "request", "--text", "a to-do")):
                    with self.subTest(args):
                        self.refused(*args, env=env)
        self.assertFalse(self.m.is_done(self.c, "m2"))
        self.assertEqual(self.vias("m1"), [])

    def test_a_person_at_a_terminal_is_not_limited(self):
        self.assertIn("[m2]", self.cli("inbox"))
        self.cli("send", "--to", "other", "--type", "info", "--text", "hello")
        self.cli("ack", "m1", session="S1")

    def test_today_refusals_stay(self):
        self.refused("send", "--to", self.owner, "--type", "task", "--text", "x", env=EVENT_JOB)
        self.refused("send", "--from", self.hub, "--to", "other", "--type", "info", "--text", "x", env=EVENT_JOB)
        self.refused("publish", "job.failed", "--source", "proj", env=EVENT_JOB)


class Reconciliation(Base):
    def ask(self, qid="q1"):
        self.m.publish(self.c, qid, "message", "proj", self.owner, None, {"type": "question", "text": "still?"})
        self.c.execute("UPDATE events SET ts=? WHERE id=?", (tms.ago(60 * 24 * 5), qid))

    def check_letter(self):
        self.c.execute("BEGIN IMMEDIATE")
        sent = self.m.reconcile_questions(self.c)
        self.c.execute("COMMIT")
        self.assertEqual(len(sent), 1)
        return sent[0]

    def test_a_handler_closes_the_question_its_letter_names_and_the_letter(self):
        for kind, env in (("done", ESCALATION), ("drop", FORK)):
            with self.subTest(kind):
                qid = f"q-{kind}"
                self.ask(qid)
                mid = self.check_letter()
                env = with_letters(env, mid)
                self.cli("send", "--to", self.owner, "--type", kind, "--question", qid, "--text", "settled", env=env)
                self.cli("done", mid, env=env)
                self.assertEqual(self.m.open_questions(self.c, "proj"), [])
                self.assertTrue(self.m.is_done(self.c, mid))

    def test_not_a_question_no_letter_of_its_names(self):
        self.ask("q1")
        self.ask("q2")
        self.c.execute("UPDATE events SET ts=? WHERE id='q2'", (tms.ago(5),))
        mid = self.check_letter()
        env = with_letters(ESCALATION, mid)
        self.refused("send", "--to", self.owner, "--type", "done", "--question", "q2", "--text", "x", env=env)
        self.refused("send", "--to", self.owner, "--type", "drop", "--question", "q2", "--text", "x", env=env)
        # a crafted letter can't name it for a run started for another letter
        self.m.publish(self.c, "crafted", "message", "other", "proj", None,
                       {"type": "request", "text": "x", "question_check": "q2"})
        self.refused("send", "--to", self.owner, "--type", "done", "--question", "q2", "--text", "x", env=env)
        self.assertIn("q2", [q["id"] for q in self.m.open_questions(self.c, "proj")])

    def test_a_question_close_is_not_a_reply_to_anything(self):
        """It would carry the other letter's handoff, take that letter, and reach the assistant as its answer."""
        self.ask("q1")
        mid = self.check_letter()
        self.m.publish(self.c, "owners", "message", self.owner, "proj", None,
                       {"type": "question", "text": "x", "effect": "asks", "handoff_id": 7})
        close = ("send", "--to", self.owner, "--type", "done", "--question", "q1", "--text", "forged")
        for reply_to in ("owners", mid):
            for env, session in ((with_letters(ESCALATION, mid), None), (with_letters(FORK, mid), None), (None, "S1")):
                with self.subTest(reply_to=reply_to, env=env):
                    self.refused(*close, "--reply-to", reply_to, env=env, session=session)
        self.assertEqual(self.state("owners").taken, "")
        self.assertEqual(self.rows("type='message' AND ref='owners'"), [])
        self.assertIn("q1", [q["id"] for q in self.m.open_questions(self.c, "proj")])
        self.cli(*close, env=with_letters(ESCALATION, mid))
        self.assertEqual(self.m.open_questions(self.c, "proj"), [])

    def test_closing_it_as_records(self):
        self.m.RECORDS = True
        self.m.publish(self.c, "r:q1", "message", "other", "proj", None, {"type": "info", "text": "pad"})
        self.cli("send", "--to", self.owner, "--type", "question", "--text", "still?", session="S1")
        qid = self.m.open_questions(self.c, "proj")[0]["id"]
        self.c.execute("UPDATE events SET ts=? WHERE type='record'", (tms.ago(60 * 24 * 5),))
        mid = self.check_letter()
        env = with_letters(ESCALATION, mid)
        self.cli("send", "--to", self.owner, "--type", "drop", "--question", qid, "--text", "settled", env=env)
        self.assertEqual(self.m.open_questions(self.c, "proj"), [])

    def test_a_run_without_letters_closes_no_question(self):
        """A job could otherwise close the owner's open questions of its project (M3)."""
        self.ask("q1")
        for env in (EVENT_JOB, CLOCK_JOB):
            for kind in ("drop", "done"):
                self.refused("send", "--to", self.owner, "--type", kind, "--question", "q1", "--text", "x", env=env)
        self.assertEqual(len(self.m.open_questions(self.c, "proj")), 1)

    def test_its_letter_gets_no_fixed_line_and_a_handler(self):
        self.real_handler()
        self.ask("q1")
        mid = self.check_letter()
        self.dispatch()
        self.assertEqual(self.letters_of(self.started[0]), [mid])
        self.assertEqual(self.rows("id=?", f"{mid}:received"), [])


class Propose(Base):
    """What propose_apply's mailbox does with the agent's answer: the same rules."""

    def apply(self, ids, output, ok=True):
        pa = load_propose()
        pa._hubbus = lambda: self.m
        self.m.db = lambda *a, **k: self.c
        res = {"ok": ok, "output": output if ok else None, "text": "", "applied": [], "refused": [], "error": "" if ok else "broke"}
        with mock.patch.object(pa, "run", lambda *a, **k: res), mock.patch.object(pa.hubconf, "workspace", lambda n: self.ws), \
                mock.patch("builtins.print"):
            return pa._mailbox("proj", ids)

    def out(self, replies=(), done=(), ask=(), deferred=()):
        return {"replies": list(replies), "done": list(done), "ask_owner": list(ask), "deferred": list(deferred), "summary": ""}

    def sent(self, to="other"):
        return self.payloads("type='message' AND source='proj' AND to_project=?", to)

    def test_it_accepts_done_and_owner_questions_only(self):
        self.send("m1", "proj", "request")
        self.apply(["m1"], self.out(
            replies=[{"reply_to": "m1", "type": "question", "text": "which?"},
                     {"reply_to": "m1", "type": "info", "text": "fyi"},
                     {"reply_to": "m1", "type": "done", "text": "answer"},
                     {"reply_to": "other-letter", "type": "done", "text": "no"}],
            ask=[{"text": "owner?"}]))
        self.assertEqual([p["type"] for p in self.sent()], ["done"])
        self.assertEqual([p["type"] for p in self.sent(self.owner)], ["question"])

    def test_info_to_an_asks_letter(self):
        self.m.publish(self.c, "asks", "message", self.owner, "proj", None,
                       {"type": "question", "text": "x", "effect": "asks", "handoff_id": 1})
        self.apply(["asks"], self.out(replies=[{"reply_to": "asks", "type": "info", "text": "the answer"}], done=[{"id": "asks"}]))
        reply = self.rows("type='message' AND source='proj' AND ref='asks'")
        self.assertEqual(len(reply), 1)
        p = json.loads(reply[0]["payload"])
        self.assertEqual((p["type"], p["reply_to"], p["handoff_id"]), ("info", "asks", 1))
        item = self.m.to_assistant(self.c, reply[0])
        self.assertEqual((item["handoff_id"], item["text"]), (1, "the answer"), "reaches the owner as the answer")
        closed = self.rows("type='message.done' AND ref='asks'")[0]
        self.assertIsNone(self.m.to_assistant(self.c, closed), "and the close doesn't replace it")

    def test_a_reply_goes_as_a_sessions_reply_does(self):
        self.m.publish(self.c, "m1", "message", "other", "proj", None, {"type": "question", "text": "x", "from_session": "SO"})
        self.apply(["m1"], self.out(replies=[{"reply_to": "m1", "type": "done", "text": "answer"}]))
        p = self.sent()[0]
        self.assertEqual((p["reply_to"], p["to_session"]), ("m1", "SO"))
        self.assertEqual(self.m.thread_chain(self.c, self.rows("type='message' AND source='proj'")[0])[0]["id"], "m1")

    def test_no_owner_question_when_all_its_letters_are_taken(self):
        self.send("m1", "proj", "request")
        self.rt.live.add("S1")
        self.ack("m1", "S1")
        self.apply(["m1"], self.out(ask=[{"text": "owner?"}], deferred=[{"id": "m1", "why": "x"}]))
        self.assertEqual(self.sent(self.owner), [])

    def test_an_owner_question_while_one_is_free(self):
        self.send("m1", "proj", "request")
        self.send("m2", "proj", "request")
        self.rt.live.add("S1")
        self.ack("m1", "S1")
        self.apply(["m1", "m2"], self.out(ask=[{"text": "owner?"}], deferred=[{"id": "m2", "why": "x"}]))
        self.assertEqual(len(self.sent(self.owner)), 1)

    def test_a_letter_a_session_took_meanwhile_is_rechecked_at_apply_time(self):
        self.send("m1", "proj", "request")
        self.send("m2", "proj", "request")
        self.rt.live.add("S1")
        self.ack("m2", "S1")
        self.m.publish(self.c, "s1", "message", "proj", "other", "m1", {"type": "done", "text": "a session did", "reply_to": "m1"})
        self.apply(["m1", "m2"], self.out(replies=[{"reply_to": "m1", "type": "done", "text": "me too"},
                                                   {"reply_to": "m2", "type": "done", "text": "me too"}],
                                          done=[{"id": "m1"}, {"id": "m2"}]))
        self.assertEqual([p["text"] for p in self.sent()], ["a session did"])
        self.assertFalse(self.m.is_done(self.c, "m1"))
        self.assertFalse(self.m.is_done(self.c, "m2"))
        self.assertEqual(self.rows("id LIKE '%:propose-failed'"), [], "not asked about: a session has it")

    def test_a_deferred_letter_stays_open_for_sessions(self):
        self.real_handler()
        self.send("m1", "proj", "request")
        self.dispatch()
        self.apply(["m1"], self.out(deferred=[{"id": "m1", "why": "needs a code change"}]))
        self.assertEqual(self.state("m1").taken, "")
        self.rt.live.add("S1")
        self.assertIn("[m1]", self.hook("prompt", "S1"))
        self.dispatch()
        self.assertEqual(len(self.started), 1)

    def test_a_failed_run_is_held_stays_open_and_its_question_closes_with_the_letter(self):
        self.send("m1", "proj", "request")
        self.claim("m1", "triage", run="h0")
        self.apply(["m1"], None, ok=False)
        self.assertEqual(self.vias("m1"), ["triage", "held"])
        self.assertEqual(self.state("m1").taken, "", "a question to the owner is no answer")
        self.assertIn("m1:propose-failed", [q["id"] for q in self.m.open_questions(self.c)])
        self.assertEqual(self.dispatch(), 1, "no run starts for it again")
        self.m.close_failed_questions(self.c)
        self.assertIn("m1:propose-failed", [q["id"] for q in self.m.open_questions(self.c)], "the letter is still open")
        self.rt.live.add("S1")
        self.assertIn("[m1]", self.hook("prompt", "S1"))
        self.cli("done", "m1", session="S1")
        self.m.close_failed_questions(self.c)
        self.m.close_failed_questions(self.c)
        self.assertNotIn("m1:propose-failed", [q["id"] for q in self.m.open_questions(self.c)])
        closes = self.rows("id='m1:propose-failed:closed'")
        self.assertEqual(len(closes), 1)
        self.assertEqual((closes[0]["source"], closes[0]["to_project"]), (self.hub, self.owner))

    def test_a_loop_question_stays_as_it_was(self):
        self.send("m1", "proj", "request")
        self.m.hold(self.c, self.m.get_message(self.c, "m1"))
        self.m.publish(self.c, "m1:done", "message.done", "proj", "proj", "m1", {"note": "a session did"})
        self.m.close_failed_questions(self.c)
        self.assertIn("m1:loop", [q["id"] for q in self.m.open_questions(self.c)])


class Prompts(unittest.TestCase):
    NAMES = ("mailbox", "mailbox-resume", "mailbox-delegate")

    def text(self, name):
        import prompts
        return prompts.render(name, letter="L", id="m1", notes="/abs/notes", letters="L", today="2026-11-07")

    def test_notes_are_named_by_absolute_path_and_nothing_says_the_platform_commits_them(self):
        for name in self.NAMES:
            with self.subTest(name):
                self.assertIn("/abs/notes/", self.text(name))
                self.assertNotIn("the platform commits", self.text(name))

    def test_an_action_is_stated_only_after_its_tool_succeeded(self):
        for name in (*self.NAMES, "mailbox-propose"):
            with self.subTest(name):
                self.assertIn("State an action only", " ".join(self.text(name).split()))

    def test_the_replies_they_ask_for_are_the_ones_allowed(self):
        for name in self.NAMES:
            with self.subTest(name):
                self.assertIn("--type done", self.text(name))
        self.assertNotIn("--type done|info", self.text("mailbox"))
        self.assertIn("(type done; info only", " ".join(self.text("mailbox-propose").split()))


class Settings(unittest.TestCase):
    """What a confined run of the hub, or of a project, may write and read."""

    def setUp(self):
        self.hub = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.hub, True)
        self.state = self.hub / ".state"
        self.logs = self.hub / ".logs"
        for d in (self.state / "agent-jobs" / "mailbox-other", self.state / "mailbox", self.logs, self.hub / "bin",
                  self.hub / ".claude" / "worktrees" / "wt", self.hub / ".agent-memory" / "memory"):
            d.mkdir(parents=True)
        (self.state / "scheduler-state.json").write_text("{}")
        (self.hub / ".agent-memory" / "AGENT.md").write_text("x")
        self.name = hubconf.hub_project()
        for attr, value in (("MAIN", self.hub), ("STATE", self.state), ("LOGS", self.logs)):
            p = mock.patch.object(hubconf, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def settings(self, project=None, keep=None, workspace=None):
        import runtime_claude
        project = project or self.name
        ws = workspace or self.hub
        pol = {"other_projects": [], "deny_write": [self.hub], "deny_read": [], "platform_commands": [],
               "platform_cli": ["brunna send"], "write": [], "network": [], "credential_files": [], "credentials": [],
               "workspace": str(ws)}
        doc = {"name": project, "paths": {"workspace": str(ws)}}
        with mock.patch("policy.policy", lambda name, d, i: pol), mock.patch("registry.refresh", lambda: {"projects": {project: doc}}):
            return json.loads(runtime_claude.platform_settings(project, {}, tempfile.mkdtemp(), keep).read_text())["permissions"]["deny"]

    def tilde(self, p):
        import runtime_claude
        return runtime_claude._rule_path(p)   # ~/… or //… (a single / would be the settings file's folder)

    def test_notes_are_writable_and_nothing_else_of_the_hub_is(self):
        deny = self.settings()
        self.assertNotIn(f"Edit({self.tilde(self.hub)}/**)", deny)
        self.assertNotIn(f"Edit({self.tilde(self.hub / '.agent-memory' / 'memory')}/**)", deny)
        self.assertNotIn(f"Edit({self.tilde(self.hub / '.agent-memory')}/**)", deny)
        self.assertIn(f"Edit({self.tilde(self.hub / '.agent-memory' / 'AGENT.md')})", deny)
        for kept in (self.hub / "bin", self.hub / ".claude", self.state, self.logs):
            self.assertIn(f"Edit({self.tilde(kept)}/**)", deny)

    def test_state_and_logs_are_denied_to_read(self):
        deny = self.settings()
        self.assertIn(f"Read({self.tilde(self.state)}/**)", deny)
        self.assertIn(f"Read({self.tilde(self.logs)}/**)", deny)

    def test_except_the_runs_own_job_folder(self):
        job = self.state / "agent-jobs" / "brunna-check"
        job.mkdir()
        deny = self.settings(keep=job)
        self.assertNotIn(f"Read({self.tilde(self.state)}/**)", deny)
        self.assertNotIn(f"Read({self.tilde(job)}/**)", deny)
        self.assertNotIn(f"Edit({self.tilde(job)}/**)", deny)
        self.assertIn(f"Read({self.tilde(self.logs)}/**)", deny)
        self.assertIn(f"Read({self.tilde(self.state / 'mailbox')}/**)", deny)
        self.assertIn(f"Read({self.tilde(self.state / 'agent-jobs' / 'mailbox-other')}/**)", deny)
        self.assertIn(f"Read({self.tilde(self.state / 'scheduler-state.json')})", deny)

    def test_another_projects_run_keeps_todays_reads(self):
        other = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, other, True)
        (other / ".agent-memory" / "memory").mkdir(parents=True)
        with mock.patch.object(hubconf, "workspace", lambda n: other):
            deny = self.settings("other", workspace=other)
        # the hub's state is denied whole: what the platform CLI reads is beside it, in hubconf.SHARED
        # (docs/design/capabilities.md 11-08, A1 points 1 and 7)
        self.assertIn(f"Read({self.tilde(self.state)}/**)", deny)
        self.assertIn(f"Read({self.tilde(self.logs)}/**)", deny)
        self.assertFalse([r for r in deny if str(hubconf.shared()) in r])
        self.assertIn(f"Edit({self.tilde(self.hub)}/**)", deny)
        job = self.state / "agent-jobs" / "other-check"
        job.mkdir()
        with mock.patch.object(hubconf, "workspace", lambda n: other):
            deny = self.settings("other", keep=job, workspace=other)
        # a confined job's own folder: .state is listed beside it, only in that run's settings
        self.assertNotIn(f"Read({self.tilde(self.state)}/**)", deny)
        self.assertFalse([r for r in deny if str(job) in r])
        self.assertIn(f"Read({self.tilde(self.state / 'mailbox')}/**)", deny)
        self.assertIn(f"Read({self.tilde(self.state / 'agent-jobs' / 'mailbox-other')}/**)", deny)

    def flags(self, cwd):
        return thc.flags("mailbox-escalation", cwd, {"MAILBOX_PROJECT": self.name})

    def test_a_run_gets_the_absolute_rule_and_no_extra_folder_at_the_hub_root(self):
        cmd = self.flags(self.hub)
        self.assertIn(f"Edit(/{self.hub}/.agent-memory/memory/**)", thc.allowed(cmd))
        self.assertNotIn("--add-dir", cmd)

    def test_a_fork_in_a_linked_worktree_with_no_link_gets_the_folder_too(self):
        wt = self.hub / ".claude" / "worktrees" / "wt"
        cmd = thc.flags("mailbox-conversation", wt, {"MAILBOX_PROJECT": self.name})
        self.assertIn(f"Edit(/{self.hub}/.agent-memory/memory/**)", thc.allowed(cmd))
        self.assertEqual(cmd[cmd.index("--add-dir") + 1], str(self.hub / ".agent-memory"))

    def test_with_a_link_the_folder_is_added_once(self):
        wt = self.hub / ".claude" / "worktrees" / "wt"
        (wt / ".agent-memory").symlink_to(self.hub / ".agent-memory")
        cmd = thc.flags("mailbox-conversation", wt, {"MAILBOX_PROJECT": self.name})
        self.assertEqual(cmd.count("--add-dir"), 1)
        rules = thc.allowed(cmd)
        self.assertEqual(rules.count(f"Edit(/{self.hub}/.agent-memory/memory/**)"), 1)

    def test_a_point_with_no_notes_rule_gets_none(self):
        import runtime_claude
        import runtime_ports
        spec = {"settings": "platform", "project_env": "P", "permission_mode": "dontAsk", "tools": "Read",
                "allowed_tools": ["Read(./**)"]}
        task = runtime_ports.Task("synthetic", "x", spec, cwd=str(self.hub), env={"P": self.name})
        with mock.patch.object(runtime_claude, "platform_settings", lambda p, *a, **k: pathlib.Path("/platform/p.json")):
            cmd = runtime_claude._flags(task)
        self.assertFalse([r for r in thc.allowed(cmd) if ".agent-memory" in r])

    def test_a_link_for_notes_is_not_followed(self):
        import policy
        victim = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, victim, True)
        ws = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, ws, True)
        (ws / ".agent-memory").mkdir()
        (ws / ".agent-memory" / "memory").symlink_to(victim)
        with mock.patch.object(hubconf, "workspace", lambda n: ws):
            self.assertIsNone(policy.notes_dir("other"))
        shutil.rmtree(ws / ".agent-memory")
        (ws / ".agent-memory").symlink_to(victim)
        with mock.patch.object(hubconf, "workspace", lambda n: ws):
            self.assertIsNone(policy.notes_dir("other"))


class Eval(unittest.TestCase):
    """e3's mailbox scenarios all the way to the process it would start: the real build, config, bus and letters of a run
    in a temporary folder, and the agent's process stubbed."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        cfg = self.tmp / "config"
        (cfg / "projects").mkdir(parents=True)
        (self.tmp / "workspace").mkdir()
        sys.path.insert(0, str(ROOT / "bin" / "evals"))
        import e2
        (cfg / e2.HUB_JSON).write_text(json.dumps(
            {"owner": {"name": "alex", "tag": "Alex"}, "owner_side": "builtin", "registry_dir": "projects",
             "workspace_root": str(self.tmp / "workspace"), "runtime": {"default": "claude"},
             "language": {"ui": "en", "content": "en"}}))
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("BRUNNA_", "AGENT_HUB_", "AGENTHUB_", "HUB_"))}
        self.env.update({"HOME": str(self.tmp), "AGENT_HUB_CONFIG": str(cfg), "AGENT_HUB_DATA": str(self.tmp / "data"),
                         "AGENT_HUB_SECRETS": str(self.tmp / "secrets"), "AGENT_HUB_PYTHON": sys.executable,
                         "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})

    def run_scenario(self, scenario):
        sys.path.insert(0, str(ROOT / "bin" / "evals"))
        import e3
        real, seen = subprocess.run, {}

        def run(cmd, *a, **kw):
            if "llm.py" not in " ".join(map(str, cmd)):
                return real(cmd, *a, **kw)
            seen.update(cmd=cmd, env=kw["env"])
            return subprocess.CompletedProcess(cmd, 0, "{}", "")
        with mock.patch.object(e3.subprocess, "run", run):
            e3.one(scenario, "new-en", 0, self.tmp / "run", self.env)
        out = self.tmp / "run" / scenario / "new-en-0"
        return e3, seen, json.loads((out / "files.json").read_text()), out

    def letters_seen(self, seen, point):
        """What the agent's own commands would read as their letters, in the environment e3 gives it."""
        hb = thc.load_hubbus()
        with mock.patch.dict(os.environ, {**seen["env"], "HUB_LLM_POINT": point}, clear=True):
            return hb.run_letters()

    def test_the_handler_runs_with_all_its_letters(self):
        e3, seen, files, out = self.run_scenario("mailbox")
        self.assertIn("mailbox-escalation", seen["cmd"])
        self.assertEqual(seen["env"]["MAILBOX_PROJECT"], e3.PROJECT)
        self.assertEqual(sorted(self.letters_seen(seen, "mailbox-escalation")), sorted(files["letters"].values()))
        rows = e3.bus(out)
        delivered = [e["payload"].get("via") for e in rows if e["type"] == "message.delivered"]
        self.assertEqual(set(delivered), {"triage"})
        self.assertNotIn("{{", (out / "prompt.txt").read_text())
        self.assertIn(".agent-memory/memory/", (out / "prompt.txt").read_text())
        self.assertIn("/" + e3.PROJECT + "/", (out / "prompt.txt").read_text(), "by absolute path")

    def test_a_fork_runs_with_its_own_letter(self):
        e3, seen, files, _out = self.run_scenario("delegate_question")
        self.assertIn("mailbox-conversation", seen["cmd"])
        self.assertEqual(self.letters_seen(seen, "mailbox-conversation"), [files["letter"]])


if __name__ == "__main__":
    unittest.main()
