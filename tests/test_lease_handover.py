"""The handover when a discussion is taken back (10-25, the assistant's m10251539: 2 fallbacks out of 8).

- Lease 34c84c53: the session died at the start with no transcript, and the handover tried to --resume a conversation
  that did not exist → reported as "the model did not write a valid handover". With no transcript it returns "nothing
  to hand over", which is not a failure.
- Lease 9af483fb: the platform's own error (the resident runner loaded an old module) → the reason is told as it is,
  not blamed on the model.
- The platform's code changed and no lease is running: the runner exits itself so launchd restarts it; with a lease
  running it stays put.

Never really starts claude or sends letters: the subprocess in run_handover and deliver are both faked.

    bin/py -m unittest discover -s tests
"""
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))


def T(msgid, **kw):
    """lease-runner's message in zh-Hant (the Handover class runs in zh-Hant and asserts those texts)"""
    import i18n
    return i18n.translate("lease-runner", msgid, kw, "zh-Hant")


MODEL_FAILED = "the model did not produce a valid handover"


def load_runner():
    spec = importlib.util.spec_from_file_location("lease_runner_h", ROOT / "bin" / "lease-runner.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "zh-Hant"})   # the assertions are the zh-Hant texts
class Handover(unittest.TestCase):
    def setUp(self):
        self.m = m = load_runner()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        m.DIR = self.tmp
        self.delivered = []
        for name, value in [("deny_settings", lambda project: self.tmp / "deny.json"), ("env_for", lambda lease: {}),
                            ("deliver", lambda lease, h, n, earlier=None: self.delivered.append((n, h))),
                            ("earlier_tasks", lambda project: [])]:
            p = mock.patch.object(m, name, value)
            p.start()
            self.addCleanup(p.stop)
        import runtime_claude
        self.has_transcript = True
        p = mock.patch.object(runtime_claude, "transcript",
                              lambda sid: (self.tmp / "t.jsonl") if self.has_transcript else None)
        p.start()
        self.addCleanup(p.stop)

    def lease(self):
        l = self.m.Lease({"lease_id": "44444444-4444-4444-8444-444444444444", "project": "p", "cwd": str(self.tmp)})
        l.state = "idle_stopped"
        self.events = []
        l.emit = lambda t, **kw: self.events.append((t, kw))
        l.set_state = lambda s, **kw: self.events.append(("state", {"state": s, **kw}))
        return l

    def model(self, *outputs):
        """llm.py's outputs (in order)."""
        it = iter(outputs)
        return mock.patch.object(self.m.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
            returncode=0, stdout=json.dumps(next(it)), stderr=""))

    def handover(self):
        return [kw["handover"] for t, kw in self.events if t == "handover"][0]

    def test_no_conversation_is_not_a_failure(self):
        self.has_transcript = False
        with mock.patch.object(self.m.subprocess, "run", side_effect=AssertionError("must not call the model")):
            self.m.detach(self.lease(), "user")
        h = self.handover()
        self.assertFalse(h["fallback"])
        self.assertIn(T("This lease never started a conversation (the session did not start, or it was detached before "
                        "anything was said); there is nothing to hand over."), h["summary"])
        self.assertNotIn("handover_failed", [kw.get("error") for t, kw in self.events if t == "error"])

    def test_good_handover(self):
        so = {"summary": "talked about A", "decisions": [{"what": "x", "why": "y"}], "open_questions": [], "tasks_for_owner": [],
              "notes_markdown": "n"}
        with self.model({"structured_output": so}):
            self.m.detach(self.lease(), "user")
        self.assertEqual((self.handover()["fallback"], self.handover()["summary"]), (False, "talked about A"))

    def test_model_failure_says_why(self):
        bad = {"is_error": True, "structured_output": None, "llm_error": "output does not match the schema: summary is missing"}
        with self.model(bad, bad):
            self.m.detach(self.lease(), "idle")
        h = self.handover()
        self.assertTrue(h["fallback"])
        self.assertIn(T("Handover failed ({failed}); the conversation is still there and can be attached again",
                        failed=T(MODEL_FAILED)), h["summary"])
        errs = [kw["detail"] for t, kw in self.events if t == "error" and kw.get("error") == "handover_failed"]
        self.assertIn("summary is missing", errs[0])

    def test_platform_error_is_not_blamed_on_model(self):
        with mock.patch.object(self.m, "deny_settings", side_effect=AttributeError("no attribute 'settings'")):
            self.m.detach(self.lease(), "idle")
        h = self.handover()
        self.assertTrue(h["fallback"])
        self.assertIn(T("a platform error, not the model's"), h["summary"])
        self.assertNotIn(T(MODEL_FAILED), h["summary"])


class HandoverIds(unittest.TestCase):
    """10-26 m10262322: the same discussion resumed and taken back again keeps its lease id, so the message ids made
        from the handover must carry the handover count, or they collide and get swallowed as a resend."""

    def setUp(self):
        self.m = load_runner()
        self.m.DIR = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.m.DIR, ignore_errors=True)
        self.sent = []
        p = mock.patch.object(self.m, "hubbus", lambda *a: (self.sent.append(a), types.SimpleNamespace(returncode=0))[1])
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(self.m.subprocess, "Popen", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)

    def ids(self):
        return [a[a.index("--id") + 1] for a in self.sent if "--id" in a]

    def test_second_handover_does_not_collide(self):
        lease = self.m.Lease({"lease_id": "44444444-4444-4444-8444-444444444444", "project": "p", "cwd": "/nonexistent"})
        h = {"summary": "s", "tasks_for_owner": [{"text": "t"}], "open_questions": ["q"], "notes_markdown": ""}
        self.m.deliver(lease, dict(h), 1)
        first = self.ids()
        self.sent.clear()
        self.m.deliver(lease, dict(h), 2)
        second = self.ids()
        self.assertEqual(set(first) & set(second), set(), "two handovers' ids must not repeat")
        self.assertIn(f"lease-{lease.id}-2-task-0", second)
        self.assertTrue(all(i.startswith(f"lease-{lease.id}-") for i in first + second))
        self.sent.clear()
        self.m.deliver(lease, dict(h), 2)
        self.assertEqual(self.ids(), second, "resending the same handover gets the same ids")

    def test_count_survives_restart_and_reattach(self):
        lease = self.m.Lease({"lease_id": "55555555-5555-4555-8555-555555555555", "project": "p", "cwd": "/tmp"})
        lease.handovers = 3
        again = self.m.Lease(lease.dump())
        self.assertEqual(again.handovers, 3)
        self.assertEqual(self.m.Lease({"lease_id": "x", "project": "p", "cwd": "/tmp"}).handovers, 0,
                         "an old record without this field = 0")

    def test_count_never_below_logged_handovers(self):
        """A lease from before the change: the count is taken from the event log, so ids used for a late send never
        collide with a real handover later."""
        sid = "66666666-6666-4666-8666-666666666666"
        d = self.m.DIR / sid
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text("".join(json.dumps({"type": t}) + "\n"
                                                for t in ("state", "handover", "assistant", "handover")))
        self.assertEqual(self.m.Lease({"lease_id": sid, "project": "p", "cwd": "/tmp"}).handovers, 2)
        self.assertEqual(self.m.Lease({"lease_id": sid, "project": "p", "cwd": "/tmp", "handovers": 5}).handovers, 5)


class EarlierTasks(HandoverIds):
    """11-01: every handover raised each still-open task again under a new key (lease-<id>-<n>-task-<i>; one project
    had the same question to the owner open three times). The handover now sees what is already waiting and names it
    by ref: the same item keeps its key, and an item the conversation shows has ended is claimed or withdrawn."""

    EARLIER = [{"ref": "T1", "key": "lease-old-1-task-0", "text": "measure the desk", "bound": False},
               {"ref": "T2", "key": "task-p-0123456789", "text": "renew the domain", "bound": True},
               {"ref": "T3", "key": "lease-old-1-task-1", "text": "photo of the shelf", "bound": False},
               {"ref": "T4", "key": "task-p-9876543210", "text": "sign the lease", "bound": True}]

    def sends(self, type_):
        return [a for a in self.sent if a[:1] == ("send",) and a[a.index("--type") + 1] == type_]

    def test_prompt_lists_earlier_items_only_when_there_are_some(self):
        base = self.m.handover_prompt()
        self.assertNotIn("T1", base)
        with_items = self.m.handover_prompt(self.EARLIER)
        self.assertTrue(with_items.startswith(base))
        self.assertIn("- T1: measure the desk", with_items)
        self.assertIn("- T4: sign the lease", with_items)

    def test_same_item_keeps_its_key(self):
        lease = self.m.Lease({"lease_id": "77777777-7777-4777-8777-777777777777", "project": "p", "cwd": "/nonexistent"})
        h = {"summary": "s", "open_questions": [], "notes_markdown": "",
             "tasks_for_owner": [{"text": "measure the desk and chair", "ref": "T1"}, {"text": "renew it", "ref": "T2"},
                                 {"text": "something new"}, {"text": "again", "ref": "T1"}, {"text": "x", "ref": "T9"}],
             "ended": [{"ref": "T3", "outcome": "done", "basis": "sent the photo"},
                       {"ref": "T4", "outcome": "not_needed", "basis": "moved"},
                       {"ref": "T3", "outcome": "not_needed", "basis": "no"}, {"ref": "T8", "outcome": "done", "basis": "?"}]}
        self.m.deliver(lease, h, 2, self.EARLIER)
        tasks = self.sends("task")
        self.assertEqual([a[a.index("--id") + 1] for a in tasks],
                         ["lease-old-1-task-0", f"lease-{lease.id}-2-task-2", f"lease-{lease.id}-2-task-3",
                          f"lease-{lease.id}-2-task-4"],
                         "T1 keeps its key; the STATE.md item (T2) is not sent; a ref used twice or unknown is new")
        self.assertEqual([t["key"] for t in h["tasks_for_owner"]][:2], ["lease-old-1-task-0", "task-p-0123456789"],
                         "the handover lists the earlier keys, so the assistant keeps them open")
        self.assertTrue(all("ref" not in t for t in h["tasks_for_owner"]))
        done, drop = self.sends("done"), self.sends("drop")
        self.assertEqual([(a[a.index("--task") + 1], a[a.index("--text") + 1]) for a in done],
                         [("lease-old-1-task-1", "sent the photo")])
        self.assertEqual([a[a.index("--task") + 1] for a in drop], ["lease-old-1-task-1"],
                         "a STATE.md item (T4) is not withdrawn from here; an unknown ref is ignored")
        self.assertEqual([(x["key"], x["outcome"]) for x in h["ended"]],
                         [("lease-old-1-task-1", "done"), ("task-p-9876543210", "not_needed"),
                          ("lease-old-1-task-1", "not_needed")])


class RestartOnCodeChange(unittest.TestCase):
    def setUp(self):
        self.m = load_runner()
        self.m.DIR = pathlib.Path(tempfile.mkdtemp())  # log() writes in DIR: must not write into the real runner.log
        self.addCleanup(shutil.rmtree, self.m.DIR, ignore_errors=True)
        self.m.CODE_AT_START = 100.0
        self.exited = []
        p = mock.patch.object(self.m.os, "_exit", lambda code: self.exited.append(code))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.m.LEASES.clear)

    def add(self, state):
        l = self.m.Lease({"lease_id": f"5555555{len(self.m.LEASES)}-5555-4555-8555-555555555555", "project": "p",
                          "cwd": "/tmp"})
        l.state = state
        self.m.LEASES[l.id] = l

    def check(self, stamp, now=10_000.0):
        with mock.patch.object(self.m.hubconf, "code_stamp", return_value=stamp), \
                mock.patch.object(self.m.time, "time", return_value=now):
            self.m.restart_if_code_changed()

    def test_still_being_edited_waits(self):
        self.add("idle_stopped")
        self.check(200.0, now=200.0 + self.m.CODE_SETTLE_S - 1)
        self.assertEqual(self.exited, [])
        self.check(200.0, now=200.0 + self.m.CODE_SETTLE_S + 1)
        self.assertEqual(self.exited, [0])

    def test_unchanged_code_keeps_running(self):
        self.check(100.0)
        self.assertEqual(self.exited, [])

    def test_changed_and_idle_restarts(self):
        self.add("idle_stopped")
        self.add("detached")
        self.check(200.0)
        self.assertEqual(self.exited, [0])

    def test_changed_but_busy_waits(self):
        for state in self.m.BUSY:
            with self.subTest(state):
                self.m.LEASES.clear()
                self.add(state)
                self.check(200.0)
                self.assertEqual(self.exited, [])


class CodeStamp(unittest.TestCase):
    def test_changes_when_a_bin_file_changes(self):
        import hubconf
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "bin").mkdir()
        f = tmp / "bin" / "x.py"
        f.write_text("1")
        with mock.patch.object(hubconf, "ROOT", tmp):
            before = hubconf.code_stamp()
            subprocess.run(["touch", "-t", "203001010000", str(f)], check=True)
            self.assertGreater(hubconf.code_stamp(), before)


if __name__ == "__main__":
    unittest.main()
