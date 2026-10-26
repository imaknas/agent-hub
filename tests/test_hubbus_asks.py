"""助理替 owner 問專案(轉交 effect="asks",m10261731-817c):寄成 question、照 dispatch 處理、回覆帶 handoff_id 回去、
每分鐘就拉得到。暫存的匯流排;助理的 CLI 換成假的,不碰真的助理。

    bin/py -m unittest discover -s tests
"""
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
ASKS = {"id": 41, "status": "pending", "effect": "asks", "project": "proj", "fact": "project-06 的匯入查到哪了?",
        "quote": "去問一下", "ts": "2026-10-27T10:00:00+08:00"}
FACT = {"id": 42, "status": "pending", "effect": "relevant", "project": "proj", "fact": "下週出國", "item": None}


def load():
    loader = importlib.machinery.SourceFileLoader("hubbus_asks", str(ROOT / "bin" / "hubbus"))
    spec = importlib.util.spec_from_loader("hubbus_asks", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


class Asks(unittest.TestCase):
    def setUp(self):
        m = self.m = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        m.DB = self.tmp / "bus.db"
        m.log = lambda s: None
        m.projects = lambda: {"proj": self.tmp, "other": self.tmp}
        m.ASSISTANT_CLI = ROOT / "bin" / "hubbus"   # 存在就好;CLI 換成假的
        self.calls = []

        def fake_cli(*args):
            self.calls.append(args)
            out = json.dumps([ASKS, FACT]) if args == ("--json",) else ""
            return subprocess.CompletedProcess(args, 0, out, "")
        m.assistant_cli = fake_cli
        self.c = m.db()
        m.db = lambda *a, **k: self.c

    def letter(self, hid):
        return self.c.execute("SELECT * FROM events WHERE id=?", (f"{self.m.HANDOFF_ID_PREFIX}{hid}",)).fetchone()

    def test_asks_becomes_a_question_with_its_handoff_id(self):
        self.m.import_handoffs(self.c)
        p = self.m.payload(self.letter(41))
        self.assertEqual((p["type"], p["handoff_id"], p["effect"]), ("question", 41, "asks"))
        self.assertIn("project-06 的匯入查到哪了?", p["text"])
        self.assertIn("第一則回覆就要是答案本身", p["text"])
        self.assertEqual(self.m.payload(self.letter(42))["type"], "handoff")   # 其他 effect 照舊

    def test_librarian_does_not_apply_asks(self):
        self.m.import_handoffs(self.c)
        with redirect_stdout(io.StringIO()) as out:
            self.m.cmd_open_handoffs()
        self.assertEqual([h["handoff_id"] for h in json.loads(out.getvalue())], [42])

    def test_dispatch_routes_it_like_any_question(self):
        self.m.import_handoffs(self.c)
        handled = []
        self.m.run_handler = lambda c, proj, ws, rows: handled.append([r["id"] for r in rows])

        class RT:
            def live_sessions(self): return set()
            def context_size(self, sid): return None
        self.m.dispatch(self.c, RT())
        self.assertEqual(handled, [[f"{self.m.HANDOFF_ID_PREFIX}41"]])

    def test_reply_carries_the_handoff_id_back(self):
        self.m.import_handoffs(self.c)
        with mock.patch.object(sys, "argv", ["hubbus", "send", "--from", "proj", "--to", self.m.OWNER_ALIAS, "--type", "info",
                                             "--reply-to", f"{self.m.HANDOFF_ID_PREFIX}41", "--text", "查到 9/11 那筆了"]), \
                redirect_stdout(io.StringIO()) as out:
            self.m.main()
        ev = self.c.execute("SELECT * FROM events WHERE id=?", (out.getvalue().strip(),)).fetchone()
        item = self.m.to_assistant(self.c, ev)
        self.assertEqual((item["handoff_id"], item["text"]), (41, "查到 9/11 那筆了"))

    def test_minute_dispatch_pulls_handoffs_but_bridge_path_does_not_double(self):
        class RT:
            def live_sessions(self): return set()
            def context_size(self, sid): return None
        self.m.run_handler = lambda *a: None
        with mock.patch.dict(sys.modules, {"runtime_ports": mock.Mock(load=lambda *a, **k: RT())}):
            self.m.dispatch_locked(self.c, pull=True)
        self.assertIn(("--json",), self.calls)
        self.assertIsNotNone(self.letter(41))
        self.calls.clear()
        with mock.patch.dict(sys.modules, {"runtime_ports": mock.Mock(load=lambda *a, **k: RT())}):
            self.m.dispatch_locked(self.c)          # bridge 呼叫的那條:它自己已經拉過
        self.assertNotIn(("--json",), self.calls)

class RenamedProjects(unittest.TestCase):
    """轉交寄給改名前的專案名稱(例:idea-1 → project-33):照改名紀錄送到新名稱;真的不認得的只在變化時記一次 log。"""

    def setUp(self):
        m = self.m = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        m.DB = self.tmp / "bus.db"
        self.logs = []
        m.log = self.logs.append
        m.projects = lambda: {"project-33": self.tmp, "other": self.tmp}
        m.ASSISTANT_CLI = ROOT / "bin" / "hubbus"
        state = self.tmp / "state"
        state.mkdir()
        (state / "project-renames.json").write_text(json.dumps({"idea-1": {"new": "room-x", "stage": "done"},
                                                                "room-x": {"new": "project-33", "stage": "done"}}))
        st = mock.patch.object(m.hubconf, "STATE", state)   # hubconf 是共用的模組:一定要換回來
        st.start()
        self.addCleanup(st.stop)
        self.items = [{"id": 35, "status": "pending", "effect": "relevant", "project": "idea-1", "fact": "f"},
                      {"id": 36, "status": "pending", "effect": "relevant", "project": "gone", "fact": "g"}]
        m.assistant_cli = lambda *a: subprocess.CompletedProcess(a, 0, json.dumps(self.items) if a == ("--json",) else "", "")
        self.c = m.db()

    def test_follows_renames_to_the_current_name(self):
        self.m.import_handoffs(self.c)
        r = self.c.execute("SELECT to_project FROM events WHERE id=?", (f"{self.m.HANDOFF_ID_PREFIX}35",)).fetchone()
        self.assertEqual(r["to_project"], "project-33")

    def test_unknown_project_logged_only_when_it_changes(self):
        for _ in range(3):
            self.m.import_handoffs(self.c)
        self.assertEqual(sum("#36" in l for l in self.logs), 1)


if __name__ == "__main__":
    unittest.main()
