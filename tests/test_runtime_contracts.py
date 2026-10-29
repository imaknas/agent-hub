"""執行環境的契約測試(docs/design/runtime-abstraction.md):runtime_ports 登記的每一個實作都跑同一組檢查。

同一個情境分別放進每個實作(Claude:寫成真的 jsonl 對話紀錄;Fake:直接放資料),再用同樣的斷言檢查——
介面的語意是測出來的,不是靠約定。登記表裡有實作卻沒有對應的情境設定,測試直接失敗:新的實作不可能跳過契約測試。

    bin/py -m unittest discover -s tests
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

# 情境:一則真人訊息、一則系統注入(不算真人)、一則助理叫了子 agent、一個技能、跑了一個指令(不是協作)、一則工具結果;
# 另一個專案一則真人訊息
SCENARIO = [
    (WS, runtime_ports.Turn(T0, SID, "human", "幫我看一下測試")),
    (WS, runtime_ports.Turn(T0 + datetime.timedelta(minutes=1), SID, "other")),
    (WS, runtime_ports.Turn(T0 + datetime.timedelta(minutes=2), SID, "assistant", "",
                            (runtime_ports.Action("other", "", "subagent"), runtime_ports.Action("other", "dataviz", "skill"),
                             runtime_ports.Action("run_command")))),
    (WS, runtime_ports.Turn(T0 + datetime.timedelta(minutes=3), SID, "other")),
    ("/Users/someone/workspace/other", runtime_ports.Turn(T0, SID2, "human", "別的專案")),
]
# 同一個專案、runtime 在它底下開的 worktree 裡的 session 一則真人訊息:只有 turns(worktrees=True) 才算
SID3 = "cccccccc-3333-4333-8333-333333333333"
WORKTREE_SCENARIO = [(WS, runtime_ports.Turn(T0 + datetime.timedelta(seconds=30), SID3, "human", "worktree 裡的訊息"))]


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
        for path, t in SCENARIO + [(p + "/.claude/worktrees/wt1", t) for p, t in WORKTREE_SCENARIO]:
            d = root / m.slug_for_path(path)
            d.mkdir(parents=True, exist_ok=True)
            if t.role == "human":
                o = {"type": "user", "timestamp": _iso(t.ts), "sessionId": t.session, "origin": {"kind": "human"},
                     "message": {"role": "user", "content": t.text}}
            elif t.role == "assistant":
                names = {"subagent": "Agent", "workflow": "Workflow", "peer_message": "SendMessage", "skill": "Skill",
                         "run_command": "Bash"}
                inputs = {"skill": {"skill": None}, "run_command": {"command": "ls"}}
                blocks = []
                for a in t.actions:
                    key = a.collab or a.kind
                    inp = dict(inputs.get(key, {}))
                    if "skill" in inp:
                        inp["skill"] = a.detail
                    blocks.append({"type": "tool_use", "name": names[key], "input": inp})
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

    LOGIN_ERROR = "Invalid API key · Please run /login"

    def configure_auth(self, tmp: pathlib.Path, token: bool) -> None:
        """登入情境:HOME 換成暫存目錄,token 檔在或不在。"""
        self._home = os.environ.get("HOME")
        os.environ["HOME"] = str(tmp)
        (tmp / ".claude").mkdir(exist_ok=True)
        if token:
            (tmp / ".claude" / "headless-oauth-token").write_text("contract-token\n")

    def teardown(self):
        self.m.transcript_roots, self.m.PROJECTS = self._roots, self._projects
        if getattr(self, "_path", None):
            os.environ["PATH"] = self._path
        if getattr(self, "_home", None):
            os.environ["HOME"] = self._home


class FakeFixture:
    def setup(self, tmp: pathlib.Path):
        import runtime_fake as m
        m.reset()
        for path, t in SCENARIO:
            m.add_turn(path, t)
        for path, t in WORKTREE_SCENARIO:
            m.add_turn(path, t, worktree=True)
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

    LOGIN_ERROR = "fake: not signed in"

    def configure_auth(self, tmp: pathlib.Path, token: bool) -> None:
        import runtime_fake
        runtime_fake.set_credential("token", token)

    def teardown(self):
        import runtime_fake
        runtime_fake.reset()


class CommandFixture:
    """設定裡的指令樣板(runtime_command):用一支假的 CLI agent。它只回文字/JSON,沒有成本、session、回合數、「沒正常完成」。"""
    REPORTS = frozenset()   # 這個 runtime 回報得出來的(cost、session、turns、incomplete)

    def setup(self, tmp: pathlib.Path):
        import runtime_command as m
        import hubconf
        script = tmp / "agent.py"
        script.write_text("import os, sys\nmode = os.environ['CMD_MODE']\nprompt = open(sys.argv[1]).read()\n"
                          "if mode == 'fail':\n    print('boom: not signed in', file=sys.stderr); sys.exit(3)\n"
                          "print('{\"x\": 1}')\n")
        self._get = hubconf.get
        conf = {"run": [sys.executable, str(script), "{prompt_file}"]}
        hubconf.get = lambda k, default=None: conf if k == "runtime.command" else self._get(k, default)
        return m, {}

    def configure_run(self, tmp: pathlib.Path, mode: str) -> dict:
        return {**os.environ, "CMD_MODE": mode}

    def teardown(self):
        import hubconf
        hubconf.get = self._get


FIXTURES = {"claude": ClaudeFixture, "fake": FakeFixture, "command": CommandFixture}
ALL_REPORTS = frozenset({"cost", "session", "turns", "incomplete"})


def having(port: str) -> list[str]:
    """有這個介面的實作:契約測試只對它們跑(沒有的由 load() 丟 Unsupported,NoneRuntime 檢查)。"""
    return [n for n in runtime_ports.RUNTIMES if port in runtime_ports.capabilities(n)]


class Registry(unittest.TestCase):
    def test_every_registered_runtime_has_a_fixture(self):
        withports = {n for n in runtime_ports.RUNTIMES if runtime_ports.capabilities(n)}
        self.assertEqual(withports, set(FIXTURES), "新的 runtime 要在這裡加情境設定,才算跑過契約測試")

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


class NoneRuntime(unittest.TestCase):
    """沒有 coding agent(runtime.default: none):每個介面都是「不支援」,不是錯誤、也不是空資料。"""

    def test_has_no_ports(self):
        self.assertEqual(runtime_ports.capabilities("none"), set())

    def test_every_port_is_unsupported_with_a_reason(self):
        for port in runtime_ports.PORTS:
            with self.subTest(port=port):
                with self.assertRaises(runtime_ports.Unsupported) as cm:
                    runtime_ports.load("none", ports=(port,))
                self.assertIn(runtime_ports.PORT_LABELS[port], str(cm.exception))

    def test_unsupported_is_not_missing_functions(self):
        self.assertFalse(issubclass(runtime_ports.Unsupported, runtime_ports.MissingFunctions))

    def test_partial_port_is_still_a_bug(self):
        bad = types.ModuleType("runtime_half")
        bad.turns = lambda *a: iter(())   # transcripts 只有一半
        sys.modules["runtime_half"] = bad
        runtime_ports.RUNTIMES["half"] = "runtime_half"
        try:
            with self.assertRaises(runtime_ports.MissingFunctions):
                runtime_ports.load("half", ports=("transcripts",))
            with self.assertRaises(runtime_ports.Unsupported):
                runtime_ports.load("half", ports=("agent",))
        finally:
            del runtime_ports.RUNTIMES["half"]
            del sys.modules["runtime_half"]

    def test_current_session_is_unknown_not_an_error(self):
        from unittest import mock
        with mock.patch.object(runtime_ports, "default_name", lambda: "none"):
            self.assertIsNone(runtime_ports.current_session({}))

    def test_every_label(self):
        self.assertEqual(set(runtime_ports.PORT_LABELS), set(runtime_ports.PORTS))


class Contract(unittest.TestCase):
    """每一個實作都跑這些(subTest 標出是哪一個)。"""

    def each(self, check):
        """對每一個有對話紀錄的實作:放進情境 → 機械檢查介面 → 跑 check(m, env) → 清掉。失敗會標出是哪一個實作。"""
        for name in having("transcripts"):
            with self.subTest(runtime=name):
                fx = FIXTURES[name]()
                with tempfile.TemporaryDirectory() as d:
                    m, env = fx.setup(pathlib.Path(d))
                    try:
                        runtime_ports.load(name, ports=("transcripts", "sessions"))
                        check(m, env)
                    finally:
                        fx.teardown()

    def test_turns_roles_text_actions(self):
        def check(m, env):
            ts = sorted(m.turns([WS], T0 - datetime.timedelta(hours=1)), key=lambda t: t.ts)
            self.assertEqual([t.role for t in ts], ["human", "other", "assistant", "other"])
            self.assertEqual(ts[0].text, "幫我看一下測試")
            self.assertEqual(ts[0].session, SID)
            self.assertEqual([(a.kind, a.collab, a.detail) for a in ts[2].actions],
                             [("other", "subagent", ""), ("other", "skill", "dataviz"), ("run_command", None, "")])
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

    def test_worktree_sessions_only_when_asked(self):
        def check(m, env):
            def humans(**kw):
                return {t.text for t in m.turns([WS], T0 - datetime.timedelta(hours=1), **kw) if t.role == "human"}
            self.assertEqual(humans(), {"幫我看一下測試"})
            self.assertEqual(humans(worktrees=True), {"幫我看一下測試", "worktree 裡的訊息"})
            self.assertEqual({t.text for t in m.turns(["/Users/someone/workspace/other"], T0 - datetime.timedelta(hours=1),
                                                      worktrees=True) if t.role == "human"}, {"別的專案"})
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
        for name in having("agent"):
            with self.subTest(runtime=name, mode=mode):
                fx = FIXTURES[name]()
                with tempfile.TemporaryDirectory() as d:
                    m, _ = fx.setup(pathlib.Path(d))
                    try:
                        reports = getattr(fx, "REPORTS", ALL_REPORTS)
                        if mode == "incomplete" and "incomplete" not in reports:
                            continue   # 這個 runtime 分不出「有結果但沒正常完成」
                        env = fx.configure_run(pathlib.Path(d), mode)
                        rt = runtime_ports.load(name, ports=("agent",))
                        task = runtime_ports.Task(point="p", prompt="hi", spec={}, env=env or None, timeout_s=30,
                                                  output_schema={"type": "object"} if name == "command" else None)
                        self.reports = reports
                        check(rt.run(task), rt.describe(task))
                    finally:
                        fx.teardown()

    def test_ok(self):
        def check(res, desc):
            self.assertTrue(res.ok)
            self.assertFalse(res.incomplete)
            self.assertEqual(res.structured_output, RUN_OK["structured_output"])
            if self.reports == ALL_REPORTS:
                self.assertEqual((res.text, res.cost_usd, res.session_id, res.turns),
                                 (RUN_OK["text"], RUN_OK["cost"], RUN_OK["session"], RUN_OK["turns"]))
            else:   # 回報不出來的就是 None(不能編一個)
                for field, attr in (("cost", "cost_usd"), ("session", "session_id"), ("turns", "turns")):
                    if field not in self.reports:
                        self.assertIsNone(getattr(res, attr), field)
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
        for name in having("lifecycle"):
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


class AuthContract(unittest.TestCase):
    """第 6 層:平台只說用哪種登入(token / subscription);每個實作自己決定憑證放哪、變數叫什麼。"""

    BASE = {"PATH": "/usr/bin", "LLM_AUTH": "x-not-the-runtimes"}

    def each(self, token, check):
        for name in having("auth"):
            with self.subTest(runtime=name, token=token):
                fx = FIXTURES[name]()
                with tempfile.TemporaryDirectory() as d:
                    m, _ = fx.setup(pathlib.Path(d))
                    try:
                        fx.configure_auth(pathlib.Path(d), token)
                        runtime_ports.load(name, ports=("auth",))
                        check(m, fx)
                    finally:
                        fx.teardown()

    def test_token_adds_a_credential_that_clean_env_removes(self):
        def check(m, fx):
            env, why = m.auth_env("token", dict(self.BASE))
            self.assertEqual(why, "")
            self.assertNotEqual(env, m.clean_env(env), "token 登入應該多帶憑證")
            self.assertEqual(m.clean_env(env), m.clean_env(dict(self.BASE)))
            # 已經帶著舊憑證的環境:換成這次的,不會疊兩份
            again, _ = m.auth_env("token", env)
            self.assertEqual(again, env)
        self.each(True, check)

    def test_missing_credential_says_why_instead_of_falling_back(self):
        def check(m, fx):
            env, why = m.auth_env("token", dict(self.BASE))
            self.assertIsNone(env)
            self.assertTrue(why)
        self.each(False, check)

    def test_subscription_never_carries_the_token(self):
        def check(m, fx):
            tok, _ = m.auth_env("token", dict(self.BASE))
            env, why = m.auth_env("subscription", tok)
            self.assertEqual((env, why), (m.clean_env(tok), ""))
        self.each(True, check)

    def test_platform_variables_pass_through(self):
        def check(m, fx):
            env, _ = m.auth_env("token", dict(self.BASE))
            self.assertEqual((env["PATH"], env["LLM_AUTH"]), ("/usr/bin", "x-not-the-runtimes"))
        self.each(True, check)

    def test_credentials_describe_without_values(self):
        def check(m, fx):
            creds = m.credentials()
            self.assertTrue(creds)
            self.assertEqual(len({c.name for c in creds}), len(creds))
            for c in creds:
                self.assertIsInstance(c, runtime_ports.Credential)
                self.assertIn(c.mode, (None, "token", "subscription"))
                self.assertTrue(c.label and c.renew)
            self.assertIn("token", {c.mode for c in creds})
            token = next(c for c in creds if c.mode == "token")
            self.assertTrue(token.present)
            self.assertNotIn("contract-token", repr(creds))
        self.each(True, check)

    def test_missing_token_shows_as_not_present(self):
        def check(m, fx):
            self.assertFalse(next(c for c in m.credentials() if c.mode == "token").present)
        self.each(False, check)

    def test_login_errors_told_apart(self):
        def check(m, fx):
            self.assertTrue(m.is_login_error(fx.LOGIN_ERROR))
            self.assertFalse(m.is_login_error("timed out after 120s"))
            self.assertFalse(m.is_login_error(""))
        self.each(True, check)


class InstructionsContract(unittest.TestCase):
    """第 4 層:規則檔在專案資料夾裡,而且它自己就在本機專用檔的清單上(不然會被 commit 進 repo)。"""

    def test_instructions_file_is_local_and_ignored(self):
        for name in having("instructions"):
            with self.subTest(runtime=name):
                m = runtime_ports.load(name, ports=("instructions",))
                ws = pathlib.Path("/Users/someone/workspace/proj")
                f = m.instructions_file(ws)
                self.assertEqual(f.parent, ws, "規則檔要直接放在專案資料夾裡")
                ignores = m.local_ignores()
                self.assertIn(f.name, ignores, "規則檔要在本機專用檔清單上")
                self.assertNotIn(".agent-memory", ignores, ".agent-memory 是平台的,不是 runtime 的")
                self.assertTrue(all(not pathlib.PurePath(p).is_absolute() and ".." not in pathlib.PurePath(p).parts
                                    for p in ignores), "清單裡都是專案資料夾裡的相對路徑")


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
