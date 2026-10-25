"""執行環境的契約測試(docs/design/runtime-abstraction.md):runtime_ports 登記的每一個實作都跑同一組檢查。

同一個情境分別放進每個實作(Claude:寫成真的 jsonl 對話紀錄;Fake:直接放資料),再用同樣的斷言檢查——
介面的語意是測出來的,不是靠約定。登記表裡有實作卻沒有對應的情境設定,測試直接失敗:新的實作不可能跳過契約測試。

    python3 -m unittest discover -s tests
"""
import datetime
import json
import os
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


# 「跑一次」的三種情境:正常完成、有結果但沒正常完成(例:回合上限)、失敗
RUN_OK = {"text": "done", "structured_output": {"x": 1}, "cost": 0.5, "session": "s1", "turns": 2}


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

    def configure_run(self, tmp: pathlib.Path, mode: str) -> dict:
        """PATH 最前面放一支假的 claude:不呼叫模型,照 mode 吐出 claude -p 的 JSON 或失敗。"""
        b = tmp / "bin"
        b.mkdir(exist_ok=True)
        out = {"ok": {"type": "result", "is_error": False, "result": RUN_OK["text"], "structured_output": RUN_OK["structured_output"],
                      "total_cost_usd": RUN_OK["cost"], "session_id": RUN_OK["session"], "num_turns": RUN_OK["turns"], "subtype": "success"},
               "incomplete": {"type": "result", "is_error": True, "result": "stopped", "num_turns": 50, "subtype": "error_max_turns"}}
        script = b / "claude"
        if mode == "fail":
            script.write_text("#!/bin/sh\necho 'boom: login expired' >&2\nexit 1\n")
        else:
            script.write_text("#!/bin/sh\ncat <<'EOF'\n" + json.dumps(out[mode]) + "\nEOF\n")
        script.chmod(0o755)
        self._path = os.environ["PATH"]
        os.environ["PATH"] = f"{b}:{self._path}"
        return dict(os.environ)

    def raw(self, sid, cwd, command=None, target_cwd=None, elsewhere=None):
        """Claude Code 的 hook JSON。"""
        d = {"session_id": sid, "cwd": cwd} if sid else {"cwd": cwd}
        ti = {}
        if command is not None:
            ti["command"] = command
        if target_cwd:
            ti["cwd"] = target_cwd
        if elsewhere == "machine":
            ti["host"] = "box"
        if ti:
            d["tool_input"] = ti
        return json.dumps(d)

    def teardown(self):
        self.m.transcript_roots, self.m.PROJECTS = self._roots, self._projects
        if getattr(self, "_path", None):
            os.environ["PATH"] = self._path


class FakeFixture:
    def setup(self, tmp: pathlib.Path):
        import runtime_fake as m
        m.reset()
        for path, t in SCENARIO:
            m.add_turn(path, t)
        m.set_context(SID, 1010)
        return m, {"FAKE_RUNTIME_SESSION": SID}

    def raw(self, sid, cwd, command=None, target_cwd=None, elsewhere=None):
        """Fake 自己的格式(故意跟 Claude 不一樣)。"""
        d = {"sid": sid, "dir": cwd} if sid else {"dir": cwd}
        if command is not None:
            d["cmd"] = command
        if target_cwd:
            d["where"] = target_cwd
        if elsewhere:
            d["elsewhere"] = elsewhere
        return json.dumps(d)

    def configure_run(self, tmp: pathlib.Path, mode: str) -> dict:
        import runtime_fake as m
        from runtime_ports import TaskResult
        m.set_reply("p", {"ok": TaskResult(True, False, RUN_OK["text"], RUN_OK["structured_output"], RUN_OK["cost"],
                                           RUN_OK["session"], RUN_OK["turns"], "success"),
                          "incomplete": TaskResult(True, True, "stopped", turns=50, stop="error_max_turns"),
                          "fail": TaskResult(False, error="boom: login expired")}[mode])
        return {}

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


class RunContract(unittest.TestCase):
    """第 1 層「跑一次」:每一個實作都一樣——正常完成、有結果但沒正常完成(不算失敗)、失敗。"""

    def each_run(self, mode, check):
        for name in runtime_ports.RUNTIMES:
            with self.subTest(runtime=name, mode=mode):
                fx = FIXTURES[name]()
                with tempfile.TemporaryDirectory() as d:
                    m, _ = fx.setup(pathlib.Path(d))
                    try:
                        env = fx.configure_run(pathlib.Path(d), mode)
                        rt = runtime_ports.load(name, ports=("agent",))
                        task = runtime_ports.Task(point="p", prompt="hi", spec={}, env=env or None, timeout_s=30)
                        check(rt.run(task), rt.describe(task))
                    finally:
                        fx.teardown()

    def test_ok(self):
        def check(res, desc):
            self.assertTrue(res.ok)
            self.assertFalse(res.incomplete)
            self.assertEqual((res.text, res.structured_output, res.cost_usd, res.session_id, res.turns),
                             (RUN_OK["text"], RUN_OK["structured_output"], RUN_OK["cost"], RUN_OK["session"], RUN_OK["turns"]))
            self.assertTrue(desc and all(isinstance(x, str) for x in desc))
        self.each_run("ok", check)

    def test_incomplete_is_not_failure(self):
        def check(res, desc):
            self.assertTrue(res.ok, "有結果就是 ok;沒正常完成交給呼叫端判斷(librarian 依賴這點)")
            self.assertTrue(res.incomplete)
            self.assertEqual(res.turns, 50)
        self.each_run("incomplete", check)

    def test_failure(self):
        def check(res, desc):
            self.assertFalse(res.ok)
            self.assertIn("boom", res.error)
        self.each_run("fail", check)


class LifecycleContract(unittest.TestCase):
    """第 3 層:每一個實作把原生的 hook 輸入翻成同樣的中立事件,輸出只在該有的時候有。"""

    def each(self, check):
        for name in runtime_ports.RUNTIMES:
            with self.subTest(runtime=name):
                fx = FIXTURES[name]()
                with tempfile.TemporaryDirectory() as d:
                    m, _ = fx.setup(pathlib.Path(d))
                    try:
                        runtime_ports.load(name, ports=("lifecycle",))
                        check(m, fx, pathlib.Path(d))
                    finally:
                        fx.teardown()

    def test_parse(self):
        def check(m, fx, tmp):
            ev = m.parse_event("prompt", fx.raw(SID, WS))
            self.assertEqual((ev.event, ev.session, ev.cwd), ("prompt", SID, WS))
            self.assertEqual(m.parse_event("tool_used", fx.raw(SID, WS, command="git commit -m x")).command, "git commit -m x")
            t = m.parse_event("open_session_attempt", fx.raw(SID, WS, target_cwd="/elsewhere")).target
            self.assertEqual((t["cwd"], t["elsewhere"]), ("/elsewhere", None))
            t = m.parse_event("open_session_attempt", fx.raw(SID, WS, elsewhere="machine")).target
            self.assertEqual(t["elsewhere"], {"kind": "machine"})
            self.assertIsNone(m.parse_event("prompt", fx.raw(None, WS)), "沒有 session = 不是真的 session 在跑")
            self.assertIsNone(m.parse_event("prompt", "not json"))
        self.each(check)

    def test_inject_and_deny_only_where_meaningful(self):
        def check(m, fx, tmp):
            for ev in ("session_start", "prompt", "tool_used"):
                self.assertIn("hello", m.inject(ev, "hello"), ev)
                self.assertEqual(m.inject(ev, ""), "", f"{ev}:沒有文字就沒有輸出")
            for ev in ("session_end", "pre_compact", "open_session_attempt"):
                self.assertEqual(m.inject(ev, "hello"), "", f"{ev} 不能放進 context")
            self.assertIn("no", m.deny("open_session_attempt", "no"))
            self.assertEqual(m.deny("prompt", "no"), "")
        self.each(check)

    def test_install_idempotent(self):
        def check(m, fx, tmp):
            ws = tmp / "ws"
            ws.mkdir()
            m.install_lifecycle(ws)
            snap = {p: p.read_bytes() for p in ws.rglob("*") if p.is_file()}
            self.assertTrue(snap, "裝了要留下東西")
            m.install_lifecycle(ws)
            self.assertEqual(snap, {p: p.read_bytes() for p in ws.rglob("*") if p.is_file()}, "重跑結果一樣")
        self.each(check)


class ClaudeHooks(unittest.TestCase):
    """Claude 專屬:hook 設定的替換規則、輸出格式。"""

    def test_replace_managed_keep_user(self):
        import runtime_claude as m
        settings = {"model": "x", "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "[ -d .agent-memory ] && ~/agent-hub/bin/sync-local-setup.sh --quiet || true"}]},
                             {"hooks": [{"type": "command", "command": "echo mine"}]}],
            "PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "~/agent-hub/bin/memory-nudge.py --post-tool"},
                                                            {"type": "command", "command": "my-linter"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "~/agent-hub/bin/old-thing"}]}]}}
        out = m.install_hooks(json.loads(json.dumps(settings)))
        cmds = [h["command"] for es in out["hooks"].values() for e in es for h in e["hooks"]]
        self.assertIn("echo mine", cmds)
        self.assertIn("my-linter", cmds)
        self.assertFalse([c for c in cmds if "agent-hub/bin/" in c and "hub-event" not in c], "舊的平台 hook 要拿掉")
        self.assertNotIn("Stop", out["hooks"], "只剩舊平台 hook 的事件整個拿掉")
        self.assertEqual(out["model"], "x", "其他設定不動")
        for name, matcher, ev in m.HOOK_TABLE:
            entries = [e for e in out["hooks"][name] if any(f"hub-event {ev}" in h["command"] for h in e["hooks"])]
            self.assertEqual(len(entries), 1, ev)
            self.assertEqual(entries[0].get("matcher"), matcher)
        again = m.install_hooks(json.loads(json.dumps(out)))
        self.assertEqual(again, out, "重跑不變")

    def test_output_shapes(self):
        import runtime_claude as m
        self.assertEqual(m.inject("session_start", "hi"), "hi")
        self.assertEqual(json.loads(m.inject("prompt", "hi"))["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertEqual(json.loads(m.inject("tool_used", "hi"))["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        d = json.loads(m.deny("open_session_attempt", "no"))["hookSpecificOutput"]
        self.assertEqual((d["hookEventName"], d["permissionDecision"], d["permissionDecisionReason"]), ("PreToolUse", "deny", "no"))


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
