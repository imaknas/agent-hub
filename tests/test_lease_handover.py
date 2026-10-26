"""深談收回的交接(10-25 Muninn m10251539:8 次裡 2 次 fallback)。

- 租約 34c84c53:session 一開始就掛掉、沒有對話紀錄,交接去 --resume 一段不存在的對話 → 被說成「模型沒寫出合格交接」。
  沒有對話紀錄就回「沒有內容要交接」,不算失敗。
- 租約 9af483fb:平台自己的錯(常駐的 runner 載入舊模組)→ 原因要照實說,不能說是模型的問題。
- 平台的程式改過、沒有租約在跑:runner 自己結束讓 launchd 重啟;有租約在跑就不動。

不會真的開 claude、不會真的寄信:run_handover 裡的 subprocess、deliver 都換成假的。

    bin/py -m unittest discover -s tests
"""
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))


def load_runner():
    spec = importlib.util.spec_from_file_location("lease_runner_h", ROOT / "bin" / "lease-runner.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Handover(unittest.TestCase):
    def setUp(self):
        self.m = m = load_runner()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        m.DIR = self.tmp
        self.delivered = []
        for name, value in [("deny_settings", lambda project: self.tmp / "deny.json"), ("env_for", lambda lease: {}),
                            ("deliver", lambda lease, h, n: self.delivered.append((n, h)))]:
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
        """llm.py 的輸出(依序)。"""
        it = iter(outputs)
        return mock.patch.object(self.m.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
            returncode=0, stdout=json.dumps(next(it)), stderr=""))

    def handover(self):
        return [kw["handover"] for t, kw in self.events if t == "handover"][0]

    def test_no_conversation_is_not_a_failure(self):
        self.has_transcript = False
        with mock.patch.object(self.m.subprocess, "run", side_effect=AssertionError("不該叫模型")):
            self.m.detach(self.lease(), "user")
        h = self.handover()
        self.assertFalse(h["fallback"])
        self.assertIn("沒有開始對話", h["summary"])
        self.assertNotIn("handover_failed", [kw.get("error") for t, kw in self.events if t == "error"])

    def test_good_handover(self):
        so = {"summary": "談了 A", "decisions": [{"what": "x", "why": "y"}], "open_questions": [], "tasks_for_owner": [],
              "notes_markdown": "n"}
        with self.model({"structured_output": so}):
            self.m.detach(self.lease(), "user")
        self.assertEqual((self.handover()["fallback"], self.handover()["summary"]), (False, "談了 A"))

    def test_model_failure_says_why(self):
        bad = {"is_error": True, "structured_output": None, "llm_error": "輸出不合 schema:少了 summary"}
        with self.model(bad, bad):
            self.m.detach(self.lease(), "idle")
        h = self.handover()
        self.assertTrue(h["fallback"])
        self.assertIn("模型", h["summary"])
        errs = [kw["detail"] for t, kw in self.events if t == "error" and kw.get("error") == "handover_failed"]
        self.assertIn("少了 summary", errs[0])

    def test_platform_error_is_not_blamed_on_model(self):
        with mock.patch.object(self.m, "deny_settings", side_effect=AttributeError("no attribute 'settings'")):
            self.m.detach(self.lease(), "idle")
        h = self.handover()
        self.assertTrue(h["fallback"])
        self.assertIn("平台出錯", h["summary"])
        self.assertNotIn("模型沒能", h["summary"])


class HandoverIds(unittest.TestCase):
    """10-26 m10262322:同一段深談接回再收回,lease id 沿用——交接轉成的訊息 id 要帶第幾次交接,不然撞號被當重送吞掉。"""

    def setUp(self):
        self.m = load_runner()
        self.m.DIR = pathlib.Path(tempfile.mkdtemp())
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
        self.assertEqual(set(first) & set(second), set(), "兩次交接的 id 不能重複")
        self.assertIn(f"lease-{lease.id}-2-task-0", second)
        self.assertTrue(all(i.startswith(f"lease-{lease.id}-") for i in first + second))
        self.sent.clear()
        self.m.deliver(lease, dict(h), 2)
        self.assertEqual(self.ids(), second, "同一次交接重送拿到同一組 id")

    def test_count_survives_restart_and_reattach(self):
        lease = self.m.Lease({"lease_id": "55555555-5555-4555-8555-555555555555", "project": "p", "cwd": "/tmp"})
        lease.handovers = 3
        again = self.m.Lease(lease.dump())
        self.assertEqual(again.handovers, 3)
        self.assertEqual(self.m.Lease({"lease_id": "x", "project": "p", "cwd": "/tmp"}).handovers, 0, "舊紀錄沒有這欄 = 0")

    def test_count_never_below_logged_handovers(self):
        """改版前的租約:次數從事件紀錄算,補送用的 id 之後不會被真的交接撞到。"""
        sid = "66666666-6666-4666-8666-666666666666"
        d = self.m.DIR / sid
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text("".join(json.dumps({"type": t}) + "\n"
                                                for t in ("state", "handover", "assistant", "handover")))
        self.assertEqual(self.m.Lease({"lease_id": sid, "project": "p", "cwd": "/tmp"}).handovers, 2)
        self.assertEqual(self.m.Lease({"lease_id": sid, "project": "p", "cwd": "/tmp", "handovers": 5}).handovers, 5)


class RestartOnCodeChange(unittest.TestCase):
    def setUp(self):
        self.m = load_runner()
        self.m.DIR = pathlib.Path(tempfile.mkdtemp())  # log() 寫在 DIR:不能寫進正式的 runner.log
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
        (tmp / "bin").mkdir()
        f = tmp / "bin" / "x.py"
        f.write_text("1")
        with mock.patch.object(hubconf, "ROOT", tmp):
            before = hubconf.code_stamp()
            subprocess.run(["touch", "-t", "203001010000", str(f)], check=True)
            self.assertGreater(hubconf.code_stamp(), before)


if __name__ == "__main__":
    unittest.main()
