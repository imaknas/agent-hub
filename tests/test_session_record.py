"""The session record (docs/design/mailbox-session-first.md, step 3): what a session's files say about it, before any routing
reads them.

A temporary secrets folder, bus, log and state; the process table holds `sleep`s this test starts as the "session"
processes, and the agents listing is a fake command. No Claude session is started, nothing is sent, nothing reaches a live
file.

    bin/run-tests        (running this module directly in a worktree reads the real config through .hub-home)
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import runtime_ports  # noqa: E402
import test_bus_sender as tbs  # noqa: E402
import test_claude_session_records as tcsr  # noqa: E402
import test_mailbox_triage as tmt  # noqa: E402
import test_mailbox_wake as tmw  # noqa: E402

A, B = tcsr.A, tcsr.B
LONG = "x" * 700


class CharacterizeRecord(tcsr.SecretsDir):
    """A session's process record as hub-event's session_record leaves it today."""

    def test_its_fields(self):
        p = tcsr.session_process(self)
        runtime_ports.record_session(B, "claude", p.pid, str(self.tmp))
        rec = json.loads(hubconf.session_file(B, "live").read_text())
        self.assertEqual(rec, {"runtime": "claude", "pid": p.pid, "start": runtime_ports.process_start(p.pid),
                               "cwd": str(self.tmp)})

    def test_after_the_start_it_is_left_as_it_is_at_no_cost(self):
        p = tcsr.session_process(self)
        runtime_ports.record_session(B, "claude", p.pid, str(self.tmp))
        with mock.patch.object(runtime_ports, "process_starts", side_effect=AssertionError("looked up")):
            self.assertFalse(runtime_ports.record_session(B, "claude", p.pid, "/elsewhere", refresh=False))
        self.assertEqual(json.loads(hubconf.session_file(B, "live").read_text())["cwd"], str(self.tmp))

    def test_a_refresh_rewrites_the_folder_it_saw(self):
        p = tcsr.session_process(self)
        runtime_ports.record_session(B, "claude", p.pid, str(self.tmp))
        self.assertTrue(runtime_ports.record_session(B, "claude", p.pid, "/elsewhere"))
        self.assertEqual(json.loads(hubconf.session_file(B, "live").read_text())["cwd"], "/elsewhere")


class CharacterizeWaiter(tmw.ClaudeSessionsDir):
    """The waiter as it reads the Stop hook's input."""

    def hub_event(self):
        m = tmw.load_file("hub_event_record", BIN / "hub-event")
        m.LOG = self.tmp / "hub-event.log"
        return m

    def stdin(self, text):
        """A stdin as a hook's: not a terminal, readable at once, larger than a pipe's buffer if it wants to be."""
        f = self.tmp / "stdin"
        f.write_text(text)
        fh = open(f)
        self.addCleanup(fh.close)
        return mock.patch.object(sys, "stdin", fh)

    def test_a_headless_run_reads_its_input_and_arms_nothing(self):
        m = self.hub_event()
        raw = json.dumps({"session_id": self.SID, "cwd": str(self.tmp), "hook_event_name": "Stop",
                          "last_assistant_message": "y" * 200000})
        env = {"CLAUDE_PID": str(os.getpid()), "CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}
        with self.stdin(raw), mock.patch.dict(os.environ, env):
            for var in ("HUB_LLM_POINT", "HUBBUS_LEASE"):
                os.environ.pop(var, None)
            self.assertEqual(m.main(["--runtime", "claude", "wait"]), 0)
        self.assertEqual(self.files(), [], "nothing armed, nothing kept")
        self.assertIn("wait - ran=headless", m.LOG.read_text())

    def test_a_platform_run_reads_its_input_and_does_nothing(self):
        m = self.hub_event()
        with self.stdin(self.raw()), mock.patch.dict(os.environ, {"HUB_LLM_POINT": "librarian"}):
            self.assertEqual(m.main(["--runtime", "claude", "wait"]), 0)
        self.assertEqual(self.files(), [])
        self.assertFalse(m.LOG.exists())

    def test_the_hook_declares_the_waiters_own_timeout(self):
        self.assertEqual(self.rc.wait_hook()["timeout"], self.rc.WAIT_TIMEOUT_S)


class CharacterizeToday(tmw.ClaudeSessionsDir):
    """What step 3 changes, as it is before it (each flips in the commit that changes it)."""

    def test_the_hook_timeout_is_a_day(self):
        self.assertEqual(self.rc.wait_hook()["timeout"], 86400)

    def test_a_last_reply_in_the_hook_input_is_not_kept(self):
        raw = json.dumps({"session_id": self.SID, "cwd": str(self.tmp), "last_assistant_message": "said this"})
        with mock.patch.object(self.rc, "_argv", lambda pid: self.DESKTOP_ARGV), \
                mock.patch.dict(os.environ, {"CLAUDE_PID": str(os.getpid())}):
            self.request("wake")
            self.assertEqual(self.rc.wait(raw, os.environ, poll=0)[0], 2)
        self.assertEqual(self.files(), [])
        self.assertIsNone(hubconf.session_file(self.SID, "last"), "no such file of a session yet")

    def test_the_prune_treats_an_unknown_suffix_as_nobodys(self):
        hb = tcsr.tmd.load_hubbus()
        d = hubconf.session_addresses_dir()
        d.mkdir(parents=True)
        f = d / f"{self.SID}.last"
        f.write_text("x")
        old = time.time() - hb.ADDRESS_GRACE_S - 60
        os.utime(f, (old, old))
        self.assertEqual(hb.prune_wake_addresses(live=set()), [], "not one of a session's files")
        self.assertFalse(f.exists())


class CharacterizeWorktreeMail(tbs.WorktreeFixture):
    """A linked worktree's mail: told in an open project; in a sandboxed one its re-run can't reach the bus."""

    def told(self, text):
        [letter] = [r["id"] for r in self.rows("type='message'") if json.loads(r["payload"]).get("text") == text]
        return [json.loads(r["payload"]).get("session") for r in self.rows("type='message.delivered' AND ref=?", (letter,))]

    def hook_out(self, what, wt, name):
        payload = json.dumps({"session_id": self.SID, "cwd": str(wt), "hook_event_name": name, "prompt": "hello"})
        r = subprocess.run(["bash", str(BIN / "worktree-memory.sh"), what], cwd=wt, input=payload + "\n",
                           capture_output=True, text=True, timeout=120, env=self.hook_env())
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_an_open_project_is_told(self):
        wt = self.tree("open")
        self.letter_to("open", "for the open project", typ="request")
        out = self.hook_out("prompt", wt, "UserPromptSubmit")
        self.assertIn("for the open project", out)
        self.assertEqual(self.told("for the open project"), [self.SID])

    @unittest.skipUnless(tbs.seatbelt.can_sandbox() is None, "needs Seatbelt")
    def test_a_sandboxed_project_is_told_nothing_silently(self):
        wt = self.tree("box")
        self.letter_to("box", "for the box", typ="request")
        out = self.hook_out("prompt", wt, "UserPromptSubmit")
        self.assertNotIn("for the box", out)
        self.assertEqual(self.told("for the box"), [])


class CharacterizeDetach(unittest.TestCase):
    """run-agent-job.sh when its detach step can't run."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.hub = self.tmp / "hub"
        (self.hub / "bin").mkdir(parents=True)
        (self.hub / "agent-jobs").mkdir()
        (self.hub / "agent-jobs" / "job.md").write_text("do it\n")
        for f in ("run-agent-job.sh", "hubpaths.sh"):
            (self.hub / "bin" / f).write_text((BIN / f).read_text())
        py = self.hub / "bin" / "py"
        py.write_text("#!/bin/sh\nexit 1\n")
        py.chmod(0o755)
        (self.hub / "bin" / "run-agent-job.sh").chmod(0o755)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("BRUNNA_", "AGENT_HUB_"))}
        self.env.update(AGENT_HUB_ROOT=str(self.hub), AGENT_HUB_STATE=str(self.tmp / "state"), AGENT_HUB_LANG_UI="en")

    def run_job(self):
        return subprocess.run([str(self.hub / "bin" / "run-agent-job.sh"), "job", str(self.tmp)], env=self.env,
                              capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)

    def test_a_failing_python_reads_as_started(self):
        r = self.run_job()
        self.assertEqual(r.returncode, 0)
        self.assertIn("started in the background", r.stdout)


class CharacterizeDispatchErrors(tmt.Base):
    def test_a_prompt_that_cannot_be_rendered_stops_dispatch_for_later_projects(self):
        self.real_handler()
        self.send("m1", "proj", "request")
        self.send("m2", self.hub, "request")
        with mock.patch.object(self.m.prompts, "render", side_effect=ValueError("bad template")), \
                self.assertRaises(ValueError):
            self.dispatch()
        self.assertEqual(self.started, [], "nothing started, the later project never reached")


class CharacterizeOwnerQuestion(tmt.Base):
    def test_a_run_s_question_on_an_asks_letter_carries_its_handoff(self):
        self.m.publish(self.c, "asks", "message", self.owner, "proj", None,
                       {"type": "question", "text": "x", "effect": "asks", "handoff_id": 7})
        self.cli("send", "--to", self.owner, "--reply-to", "asks", "--type", "question", "--text", "which one?",
                 env=tmt.with_letters(tmt.ESCALATION, "asks"))
        [q] = self.rows("type='message' AND source='proj'")
        self.assertEqual(self.m.to_assistant(self.c, q)["handoff_id"], 7, "said as the project's answer")


if __name__ == "__main__":
    unittest.main()
