"""執行環境的契約測試(docs/design/runtime-abstraction.md):runtime_ports 登記的每一個實作都跑同一組檢查。

同一個情境分別放進每個實作(Claude:寫成真的 jsonl 對話紀錄;Fake:直接放資料),再用同樣的斷言檢查——
介面的語意是測出來的,不是靠約定。登記表裡有實作卻沒有對應的情境設定,測試直接失敗:新的實作不可能跳過契約測試。

    python3 -m unittest discover -s tests
"""
import datetime
import json
import pathlib
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import runtime_ports  # noqa: E402

UTC = datetime.timezone.utc
T0 = datetime.datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
WS = "/Users/someone/workspace/proj"
SID = "aaaaaaaa-1111-4111-8111-111111111111"
SID2 = "bbbbbbbb-2222-4222-8222-222222222222"   # 另一個專案的 session(現實中一個 session 只在一個專案裡)

# 情境:一則真人訊息、一則系統注入(不算真人)、一則助理叫了子 agent 跟一個技能、一則工具結果;另一個專案一則真人訊息
SCENARIO = [
    (WS, runtime_ports.Turn(T0, SID, "human", "幫我看一下測試")),
    (WS, runtime_ports.Turn(T0 + datetime.timedelta(minutes=1), SID, "other")),
    (WS, runtime_ports.Turn(T0 + datetime.timedelta(minutes=2), SID, "assistant", "",
                            (runtime_ports.Action("subagent"), runtime_ports.Action("skill", "dataviz")))),
    (WS, runtime_ports.Turn(T0 + datetime.timedelta(minutes=3), SID, "other")),
    ("/Users/someone/workspace/other", runtime_ports.Turn(T0, SID2, "human", "別的專案")),
]


def _iso(t):
    return t.isoformat().replace("+00:00", "Z")


class ClaudeFixture:
    """把情境寫成 Claude Code 的對話紀錄格式(~/.claude/projects/<slug>/<session>.jsonl)。"""

    def setup(self, tmp: pathlib.Path):
        import runtime_claude as m
        self.m, self._roots, self._projects = m, m.transcript_roots, m.PROJECTS
        root = tmp / "projects"
        m.transcript_roots = lambda: (root,)
        m.PROJECTS = root
        for path, t in SCENARIO:
            d = root / m.slug_for_path(path)
            d.mkdir(parents=True, exist_ok=True)
            if t.role == "human":
                o = {"type": "user", "timestamp": _iso(t.ts), "sessionId": t.session, "origin": {"kind": "human"},
                     "message": {"role": "user", "content": t.text}}
            elif t.role == "assistant":
                names = {"subagent": "Agent", "workflow": "Workflow", "peer_message": "SendMessage", "skill": "Skill"}
                blocks = [{"type": "tool_use", "name": names[a.kind], "input": {"skill": a.detail} if a.kind == "skill" else {}}
                          for a in t.actions]
                o = {"type": "assistant", "timestamp": _iso(t.ts), "sessionId": t.session,
                     "message": {"role": "assistant", "content": blocks,
                                 "usage": {"input_tokens": 10, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 0}}}
            elif t.ts == T0 + datetime.timedelta(minutes=1):
                o = {"type": "user", "timestamp": _iso(t.ts), "sessionId": t.session, "isMeta": True,
                     "message": {"role": "user", "content": "<system-reminder>…</system-reminder>"}}
            else:  # 工具結果:也是 type=user,但不是真人
                o = {"type": "user", "timestamp": _iso(t.ts), "sessionId": t.session,
                     "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}}
            with open(d / f"{t.session}.jsonl", "a") as fh:
                fh.write(json.dumps(o, ensure_ascii=False) + "\n")
        return m, {"CLAUDE_CODE_SESSION_ID": SID}

    def teardown(self):
        self.m.transcript_roots, self.m.PROJECTS = self._roots, self._projects


class FakeFixture:
    def setup(self, tmp: pathlib.Path):
        import runtime_fake as m
        m.reset()
        for path, t in SCENARIO:
            m.add_turn(path, t)
        m.set_context(SID, 1010)
        return m, {"FAKE_RUNTIME_SESSION": SID}

    def teardown(self):
        import runtime_fake
        runtime_fake.reset()


FIXTURES = {"claude": ClaudeFixture, "fake": FakeFixture}


class Registry(unittest.TestCase):
    def test_every_registered_runtime_has_a_fixture(self):
        self.assertEqual(set(runtime_ports.RUNTIMES), set(FIXTURES), "新的 runtime 要在這裡加情境設定,才算跑過契約測試")

    def test_load_rejects_incomplete_module(self):
        bad = types.ModuleType("runtime_broken")
        bad.turns = lambda *a: iter(())
        sys.modules["runtime_broken"] = bad
        runtime_ports.RUNTIMES["broken"] = "runtime_broken"
        try:
            with self.assertRaises(runtime_ports.MissingFunctions):
                runtime_ports.load("broken")
        finally:
            del runtime_ports.RUNTIMES["broken"]
            del sys.modules["runtime_broken"]

    def test_unknown_runtime(self):
        with self.assertRaises(KeyError):
            runtime_ports.load("nope")


class Contract(unittest.TestCase):
    """每一個實作都跑這些(subTest 標出是哪一個)。"""

    def each(self, check):
        """對每一個登記的實作:放進情境 → 機械檢查介面 → 跑 check(m, env) → 清掉。失敗會標出是哪一個實作。"""
        for name in runtime_ports.RUNTIMES:
            with self.subTest(runtime=name):
                fx = FIXTURES[name]()
                with tempfile.TemporaryDirectory() as d:
                    m, env = fx.setup(pathlib.Path(d))
                    try:
                        runtime_ports.load(name)
                        check(m, env)
                    finally:
                        fx.teardown()

    def test_turns_roles_text_actions(self):
        def check(m, env):
            ts = sorted(m.turns([WS], T0 - datetime.timedelta(hours=1)), key=lambda t: t.ts)
            self.assertEqual([t.role for t in ts], ["human", "other", "assistant", "other"])
            self.assertEqual(ts[0].text, "幫我看一下測試")
            self.assertEqual(ts[0].session, SID)
            self.assertEqual([(a.kind, a.detail) for a in ts[2].actions], [("subagent", ""), ("skill", "dataviz")])
            self.assertTrue(all(t.ts.tzinfo is not None for t in ts), "時間要帶時區")
        self.each(check)

    def test_time_window(self):
        def check(m, env):
            mid = T0 + datetime.timedelta(minutes=2)
            self.assertEqual(len(list(m.turns([WS], mid))), 2)
            self.assertEqual(len(list(m.turns([WS], T0 - datetime.timedelta(hours=1), mid))), 2)
        self.each(check)

    def test_paths_are_separate(self):
        def check(m, env):
            humans = [t.text for t in m.turns(["/Users/someone/workspace/other"], T0 - datetime.timedelta(hours=1))]
            self.assertEqual(humans, ["別的專案"])
            self.assertEqual(list(m.turns(["/Users/someone/workspace/none"], T0 - datetime.timedelta(hours=1))), [])
        self.each(check)

    def test_last_activity(self):
        def check(m, env):
            self.assertIsNotNone(m.last_activity(WS))
            self.assertIsNone(m.last_activity("/Users/someone/workspace/none"))
        self.each(check)

    def test_sessions(self):
        def check(m, env):
            self.assertFalse(m.alive("00000000-0000-4000-8000-000000000000"))
            self.assertIsNone(m.context_size("00000000-0000-4000-8000-000000000000"))
            self.assertEqual(m.context_size(SID), 1010)
            self.assertIsInstance(m.live_sessions(), set)
            self.assertEqual(m.current_session(env), SID)
            self.assertIsNone(m.current_session({}))
        self.each(check)


class ClaudeOnly(unittest.TestCase):
    """Claude 實作自己的事:認不得的對話紀錄格式要明確失敗,不能默默算錯。"""

    def test_unrecognised_format_fails_loudly(self):
        import runtime_claude as m
        saved = m.transcript_roots
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            sub = root / m.slug_for_path(WS)
            sub.mkdir(parents=True)
            (sub / "x.jsonl").write_text("".join(json.dumps({"kind": "message", "at": i}) + "\n" for i in range(6)))
            m.transcript_roots = lambda: (root,)
            try:
                with self.assertRaises(m.TranscriptFormatError):
                    list(m.turns([WS], T0 - datetime.timedelta(days=3650)))
            finally:
                m.transcript_roots = saved


if __name__ == "__main__":
    unittest.main()
