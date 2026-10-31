"""The assistant asks a project on the owner's behalf (handoff effect="asks", m10261731-817c): sent as a question,
handled by dispatch, the reply goes back with the handoff_id, and is pulled within a minute. A temporary bus; the
assistant's CLI is faked, the real assistant is never touched.

    bin/py -m unittest discover -s tests
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
ASKS = {"id": 41, "status": "pending", "effect": "asks", "project": "proj", "fact": "shop-app 的匯入查到哪了?",
        "quote": "去問一下", "ts": "2026-10-27T10:00:00+08:00"}
FACT = {"id": 42, "status": "pending", "effect": "relevant", "project": "proj", "fact": "下週出國", "item": None}


def load():
    loader = importlib.machinery.SourceFileLoader("hubbus_asks", str(ROOT / "bin" / "hubbus"))
    spec = importlib.util.spec_from_loader("hubbus_asks", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


@mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "zh-Hant"})   # the assertions are the zh-Hant texts
class Asks(unittest.TestCase):
    def setUp(self):
        m = self.m = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        m.DB = self.tmp / "bus.db"
        m.log = lambda s: None
        m.projects = lambda: {"proj": self.tmp, "other": self.tmp}
        m.ASSISTANT_CLI = ROOT / "bin" / "hubbus"   # it only has to exist; the CLI is faked
        m.RECORDS = False   # v1 behaviour (how work is split when v2 runs alongside: test_hubbus_bridge_v2.Coexistence)
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
        self.assertIn("shop-app 的匯入查到哪了?", p["text"])
        self.assertIn("回覆一定要帶 --reply-to", p["text"])
        # since 10-27 the assistant relays every one back to the original conversation (m10291432)
        self.assertIn("每一則都會原樣講給", p["text"])
        self.assertNotIn("第一則回覆就要是答案本身", p["text"])
        self.assertEqual(self.m.payload(self.letter(42))["type"], "handoff")   # other effects as before

    def test_librarian_does_not_apply_asks(self):
        self.m.import_handoffs(self.c)
        with redirect_stdout(io.StringIO()) as out:
            self.m.cmd_open_handoffs()
        self.assertEqual([h["handoff_id"] for h in json.loads(out.getvalue())], [42])

    def test_open_handoff_without_raw_fields(self):
        """listed with the fields empty: the text meant for sessions is not parsed back"""
        self.m.publish(self.c, "h-old", "message", self.m.hubconf.owner_address(), "proj", None,
                       {"type": "handoff", "handoff_id": 7, "effect": "relevant", "text": "Fact: x"})
        with redirect_stdout(io.StringIO()) as out:
            self.m.cmd_open_handoffs()
        got = [(h["fact"], h["quote"], h["item"], h["why"], h["effect"]) for h in json.loads(out.getvalue())
               if h["handoff_id"] == 7]
        self.assertEqual(got, [("", None, None, "", "relevant")])

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

    # --- a closure must not be taken as the answer (the assistant tells the owner the first one carrying the handoff_id
    # as the answer) ---
    def done(self, mid, note=None):
        p = {"note": note} if note else {}
        self.m.publish(self.c, f"{mid}:done", "message.done", "proj", "proj", mid, p)
        return self.c.execute("SELECT * FROM events WHERE id=?", (f"{mid}:done",)).fetchone()

    def test_done_after_a_reply_is_not_forwarded(self):
        self.m.import_handoffs(self.c)
        mid = f"{self.m.HANDOFF_ID_PREFIX}41"
        self.m.publish(self.c, "r1", "message", "proj", self.m.hubconf.owner_address(), mid, {"type": "info", "text": "答案", "reply_to": mid,
                                                                      "handoff_id": 41})
        self.assertIsNone(self.m.to_assistant(self.c, self.done(mid, "已回")))

    def test_done_with_a_note_and_no_reply_carries_the_note_as_the_answer(self):
        self.m.import_handoffs(self.c)
        item = self.m.to_assistant(self.c, self.done(f"{self.m.HANDOFF_ID_PREFIX}41", "查到 9/11 那筆"))
        self.assertEqual((item["type"], item["text"], item["handoff_id"]), ("progress", "查到 9/11 那筆", 41))

    def test_done_without_answer_says_so_explicitly(self):
        self.m.import_handoffs(self.c)
        item = self.m.to_assistant(self.c, self.done(f"{self.m.HANDOFF_ID_PREFIX}41"))
        self.assertEqual(item["type"], "done")
        self.assertIn("沒有回答", item["text"])
        self.assertNotEqual(item["text"], "已處理")

    def test_ordinary_handoff_done_unchanged(self):
        self.m.import_handoffs(self.c)
        item = self.m.to_assistant(self.c, self.done(f"{self.m.HANDOFF_ID_PREFIX}42"))
        self.assertEqual((item["type"], item["text"], item["handoff_id"]), ("done", "已處理", 42))

    def test_minute_dispatch_pulls_handoffs_but_bridge_path_does_not_double(self):
        class RT:
            def live_sessions(self): return set()
            def context_size(self, sid): return None
        self.m.run_handler = lambda *a: None
        # only a deployment with the assistant pulls from the assistant
        side = mock.patch.object(self.m.hubconf, "owner_side", lambda: "assistant")
        side.start()
        self.addCleanup(side.stop)
        with mock.patch.dict(sys.modules, {"runtime_ports": mock.Mock(load=lambda *a, **k: RT())}):
            self.m.dispatch_locked(self.c, pull=True)
        self.assertIn(("--json",), self.calls)
        self.assertIsNotNone(self.letter(41))
        self.calls.clear()
        with mock.patch.dict(sys.modules, {"runtime_ports": mock.Mock(load=lambda *a, **k: RT())}):
            self.m.dispatch_locked(self.c)          # the path bridge calls: it has already pulled itself
        self.assertNotIn(("--json",), self.calls)

class RenamedProjects(unittest.TestCase):
    """A handoff addressed to a project's pre-rename name (e.g. idea-1 → garden-app): delivered to the new name per the
    rename record; a truly unknown one is logged once, only when it changes."""

    def setUp(self):
        m = self.m = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        m.DB = self.tmp / "bus.db"
        self.logs = []
        m.log = self.logs.append
        m.projects = lambda: {"garden-app": self.tmp, "other": self.tmp}
        m.ASSISTANT_CLI = ROOT / "bin" / "hubbus"
        m.RECORDS = False
        state = self.tmp / "state"
        state.mkdir()
        (state / "project-renames.json").write_text(json.dumps({"idea-1": {"new": "room-x", "stage": "done"},
                                                                "room-x": {"new": "garden-app", "stage": "done"}}))
        st = mock.patch.object(m.hubconf, "STATE", state)   # hubconf is a shared module: always swap it back
        st.start()
        self.addCleanup(st.stop)
        self.items = [{"id": 35, "status": "pending", "effect": "relevant", "project": "idea-1", "fact": "f"},
                      {"id": 36, "status": "pending", "effect": "relevant", "project": "gone", "fact": "g"}]
        m.assistant_cli = lambda *a: subprocess.CompletedProcess(a, 0, json.dumps(self.items) if a == ("--json",) else "", "")
        self.c = m.db()

    def test_follows_renames_to_the_current_name(self):
        self.m.import_handoffs(self.c)
        r = self.c.execute("SELECT to_project FROM events WHERE id=?", (f"{self.m.HANDOFF_ID_PREFIX}35",)).fetchone()
        self.assertEqual(r["to_project"], "garden-app")

    def test_unknown_project_logged_only_when_it_changes(self):
        for _ in range(3):
            self.m.import_handoffs(self.c)
        self.assertEqual(sum("#36" in l for l in self.logs), 1)


if __name__ == "__main__":
    unittest.main()
