"""lease-runner's resume decision (hit by the owner on 10-25: after switching mode the discussion died for good,
lease 34c84c53).

- Before the first turn has finished (no transcript yet), --resume is not allowed; use --session-id
- An is_error, num_turns=0 result from failing at startup must not reset crashes to zero (or it would never be taken
  back automatically)

Never really starts claude: Popen is replaced with a fake object that records the arguments.

    bin/py -m unittest discover -s tests
"""
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))


def load_runner():
    spec = importlib.util.spec_from_file_location("lease_runner", ROOT / "bin" / "lease-runner.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class FakeProc:
    def __init__(self, lines=()):
        self.stdout = io.StringIO("".join(json.dumps(l) + "\n" for l in lines))
        self.stdin = io.StringIO()
        self.pid, self.returncode = 4242, 0

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0


class Resume(unittest.TestCase):
    def setUp(self):
        self.m = m = load_runner()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        m.DIR = self.tmp
        m.deny_settings = lambda project: self.tmp / "deny.json"
        m.mcp_config = lambda lease: self.tmp / "mcp.json"
        m.env_for = lambda lease: {}
        m.preamble = lambda project, mode="discuss": "p"
        m.reader = lambda lease, proc: None
        self.cmds = []
        # m.subprocess is the global subprocess module: once swapped it must be restored in tearDown, or every
        # later test gets the fake object
        self._popen = m.subprocess.Popen
        m.subprocess.Popen = lambda cmd, **kw: (self.cmds.append(cmd), FakeProc())[1]
        import runtime_claude
        self.rt = runtime_claude
        self.has_transcript = False
        self._orig = runtime_claude.transcript
        runtime_claude.transcript = lambda sid: (self.tmp / "t.jsonl") if self.has_transcript else None

    def tearDown(self):
        self.rt.transcript = self._orig
        self.m.subprocess.Popen = self._popen

    def lease(self, **kw):
        d = {"lease_id": "11111111-1111-4111-8111-111111111111", "project": "p", "cwd": str(self.tmp)}
        d.update(kw)
        return self.m.Lease(d)

    def flag(self):
        cmd = self.cmds[-1]
        return "--resume" if "--resume" in cmd else "--session-id" if "--session-id" in cmd else None

    def test_fresh_lease_uses_session_id(self):
        self.m.start_proc(self.lease(), resume=False)
        self.assertEqual(self.flag(), "--session-id")

    def test_started_but_no_transcript_uses_session_id(self):
        """Switching mode / stopped when idle / restarting before the first turn has finished: no --resume."""
        l = self.lease(started_once=True)
        self.m.start_proc(l, resume=True)
        self.assertEqual(self.flag(), "--session-id")

    def test_transcript_exists_resumes(self):
        self.has_transcript = True
        self.m.start_proc(self.lease(started_once=True), resume=True)
        self.assertEqual(self.flag(), "--resume")

    def test_start_does_not_mark_started(self):
        l = self.lease()
        self.m.start_proc(l, resume=False)
        self.assertFalse(l.started_once)


class Reader(unittest.TestCase):
    def setUp(self):
        self.m = m = load_runner()
        m.DIR = pathlib.Path(tempfile.mkdtemp())

    def run_reader(self, lease, lines):
        proc = FakeProc(lines)
        lease.proc = object()  # not this proc → after reading, no crash handling; only the result's effect counts
        self.m.reader(lease, proc)

    def lease(self):
        return self.m.Lease({"lease_id": "22222222-2222-4222-8222-222222222222", "project": "p", "cwd": "/tmp"})

    def test_startup_failure_result_keeps_crash_count(self):
        l = self.lease()
        l.crashes = 1
        self.run_reader(l, [{"type": "result", "is_error": True, "num_turns": 0,
                             "result": "No conversation found with session ID"}])
        self.assertEqual(l.crashes, 1)
        self.assertFalse(l.started_once)

    def test_real_turn_resets_crashes_and_marks_started(self):
        l = self.lease()
        l.crashes = 1
        self.run_reader(l, [{"type": "result", "is_error": False, "num_turns": 1, "result": "hi"}])
        self.assertEqual(l.crashes, 0)
        self.assertTrue(l.started_once)


if __name__ == "__main__":
    unittest.main()


@mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "zh-Hant"})   # the assertions are the zh-Hant texts
class Attachments(unittest.TestCase):
    """Attachments on POST /v1/leases/<id>/messages (asked for by the assistant on 10-25: sending photos in a
    discussion)."""

    def setUp(self):
        import base64
        import subprocess
        self.b64 = lambda raw: base64.b64encode(raw).decode()
        self.m = m = load_runner()
        m.DIR = pathlib.Path(tempfile.mkdtemp())
        self.ws = pathlib.Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(self.ws)], check=True)
        self.lease = m.Lease({"lease_id": "33333333-3333-4333-8333-333333333333", "project": "p", "cwd": str(self.ws)})
        self.sent = []
        proc = FakeProc()
        proc.stdin = type("W", (), {"write": lambda _s, x: self.sent.append(x), "flush": lambda _s: None})()
        proc.poll = lambda: None  # the process is still running: send_message must not restart it
        self.lease.proc = proc

    def test_rejects_bad_attachments(self):
        ok = {"name": "a.png", "media_type": "image/png", "data": self.b64(b"x")}
        self.assertIsNone(self.m.check_attachments([ok]))
        self.assertIsNotNone(self.m.check_attachments([ok] * 9))
        self.assertIsNotNone(self.m.check_attachments([{"name": "a", "media_type": "image/png", "data": "!!!"}]))
        self.assertIsNotNone(self.m.check_attachments([{"name": "a", "data": self.b64(b"x")}]))

    def test_saved_cleaned_unique_and_excluded_from_git(self):
        att = {"name": "../../房間 照片.jpg", "media_type": "image/jpeg", "data": self.b64(b"jpegbytes")}
        p1 = self.m.save_attachments(self.lease, [att])
        p2 = self.m.save_attachments(self.lease, [att])
        self.assertTrue(all(p.startswith("attachments/") and ".." not in p for p in p1 + p2))
        self.assertNotEqual(p1, p2, "the same name does not overwrite")
        self.assertEqual((self.ws / p1[0]).read_bytes(), b"jpegbytes")
        self.assertIn("attachments/", (self.ws / ".git" / "info" / "exclude").read_text())

    def test_images_become_content_blocks_and_paths_listed(self):
        img = {"name": "room.png", "media_type": "image/png", "data": self.b64(b"png")}
        pdf = {"name": "plan.pdf", "media_type": "application/pdf", "data": self.b64(b"pdf")}
        self.m.send_message(self.lease, "看一下", [img, pdf])
        content = json.loads(self.sent[-1])["message"]["content"]
        self.assertEqual([b["type"] for b in content], ["image", "text"], "only images become image blocks")
        self.assertIn("看一下", content[-1]["text"])
        self.assertIn("plan.pdf", content[-1]["text"])

    def test_image_only_message(self):
        img = {"name": "room.png", "media_type": "image/png", "data": self.b64(b"png")}
        self.m.send_message(self.lease, "", [img])
        content = json.loads(self.sent[-1])["message"]["content"]
        self.assertTrue(content[-1]["text"].startswith("附件已存到"))

    def test_plain_text_unchanged(self):
        self.m.send_message(self.lease, "hi")
        self.assertEqual(json.loads(self.sent[-1])["message"]["content"], "hi")
