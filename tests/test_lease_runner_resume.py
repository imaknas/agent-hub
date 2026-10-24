"""lease-runner 的接續判斷(10-25 Knas 遇到:切換模式後深談永遠掛掉,lease 34c84c53)。

- 第一輪還沒跑完(沒有對話紀錄)就不能 --resume,要用 --session-id
- 啟動就失敗的 is_error、num_turns=0 result 不能把 crashes 歸零(不然永遠不會自動收回)

不會真的開 claude:Popen 換成記錄參數的假物件。

    /opt/homebrew/bin/python3 -m unittest discover -s tests
"""
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest

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
        # m.subprocess 就是全域的 subprocess 模組:換掉之後一定要在 tearDown 還原,不然後面的測試全部拿到假物件
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
        """切換模式/閒置停掉/重啟時第一輪還沒跑完:不能 --resume。"""
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
        lease.proc = object()  # 不是這個 proc → 讀完後不走崩潰處理,只看 result 的效果
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
