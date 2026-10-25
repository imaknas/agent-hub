"""租約契約跟執行環境無關(docs/design/lease-contract-neutral.md,Muninn m10251446)。

- v1 相容:同一段 Claude 輸出(fixtures/lease/claude-stream.jsonl),runner 送出的 v1 事件跟改版前錄下的
  (fixtures/lease/v1-events.json)完全一樣。
- v2:事件、/v1/status、/terminal、租約物件都合 contracts/lease-runner.v2.schema.json,沒有實作的名字。
- stream 介面的契約:runtime_ports 登記的每個實作,同一個情境翻成同樣的中立事件(Claude 用真的 stream-json,Fake 用它的格式)。
- 權限轉接的回報、接不回來的對話、HTTP 依請求選版本。

    /opt/homebrew/bin/python3 -m unittest discover -s tests
"""
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import minischema  # noqa: E402
import runtime_ports  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "lease"
V2 = json.loads((ROOT / "contracts" / "lease-runner.v2.schema.json").read_text())
PER_TYPE = {"tool_use": "tool_use_event", "tool_result": "tool_result_event", "question": "question_event",
            "permission": "permission_event", "state": "state_event", "turn_end": "turn_end_event", "quota": "quota",
            "error": "error"}
RUNTIME_WORDS = ("Read", "Grep", "Glob", "Bash", "WebFetch", "WebSearch", "AskUserQuestion", "five_hour", "seven_day",
                 "auth\"")


def load_runner():
    spec = importlib.util.spec_from_file_location("lease_runner_v2", ROOT / "bin" / "lease-runner.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.DIR = pathlib.Path(tempfile.mkdtemp())
    return m


class P:
    def __init__(self, text):
        self.stdout, self.returncode = io.StringIO(text), 1

    def wait(self, timeout=None):
        return 1


def run_reader(m, stream, stderr=None):
    """reader 跑一段輸出,回傳存下來的事件(跟真的 emit 一樣的形狀)。"""
    stored = []
    lease = m.Lease({"lease_id": "66666666-6666-4666-8666-666666666666", "project": "p", "cwd": "/tmp"})

    def emit(t, legacy_v1=None, **kw):
        ev = {"v": 2, "seq": len(stored) + 1, "lease_id": lease.id, "ts": "T", "type": t,
              **{k: v for k, v in kw.items() if not k.startswith("_")}}
        if legacy_v1 is not None:
            ev["_v1"] = legacy_v1
        stored.append(ev)
    lease.emit = emit
    lease.set_state = lambda st, legacy_v1=None, **kw: emit("state", legacy_v1=legacy_v1, state=st, **kw)
    proc = P(stream)
    lease.proc = object() if stderr is None else proc
    if stderr is not None:
        lease.dir.mkdir(parents=True, exist_ok=True)
        (lease.dir / "stderr.log").write_text(stderr)
    with mock.patch.object(m, "detach", lambda l, reason: stored.append({"_detach": reason})):
        m.reader(lease, proc)
    return stored


def strip(ev):
    return {k: v for k, v in ev.items() if k not in ("v", "seq", "lease_id", "ts")}


class V1Compat(unittest.TestCase):
    def test_v1_output_unchanged(self):
        m = load_runner()
        stored = run_reader(m, (FIX / "claude-stream.jsonl").read_text())
        out = [e if "_detach" in e else strip(m.render(e, 1)) for e in stored]
        st = m.STATUS["rate_limit"]
        out.append({"_status": st and {k: v for k, v in st.items() if k != "at"}})
        for stderr in ("Error: Invalid API key · Please run /login\n", "Segmentation fault: 11\n"):
            out += [strip(m.render(e, 1)) for e in run_reader(m, "", stderr=stderr)]
        self.assertEqual(out, json.loads((FIX / "v1-events.json").read_text()))

    def test_old_stored_events_pass_through(self):
        m = load_runner()
        old = {"v": 1, "seq": 3, "lease_id": "x", "ts": "T", "type": "tool_use", "name": "Read", "summary": "Read a"}
        self.assertEqual(m.render(old, 1), old)
        self.assertEqual(m.render(old, 2), old)


class V2Events(unittest.TestCase):
    def setUp(self):
        self.m = load_runner()
        self.stored = [e for e in run_reader(self.m, (FIX / "claude-stream.jsonl").read_text()) if "_detach" not in e]
        for stderr in ("Please run /login", "Segmentation fault"):
            self.stored += run_reader(self.m, "", stderr=stderr)

    def test_events_match_schema(self):
        for ev in self.stored:
            out = self.m.render(ev, 2)
            with self.subTest(out["type"]):
                self.assertEqual(minischema.errors(V2["$defs"]["event"], out, root=V2), [])
                if out["type"] in PER_TYPE:
                    self.assertEqual(minischema.errors(V2["$defs"][PER_TYPE[out["type"]]], out, root=V2), [])

    def test_no_runtime_vocabulary(self):
        for ev in self.stored:
            out = {k: v for k, v in self.m.render(ev, 2).items() if k not in ("detail",)}  # detail 是除錯用、明說不要依賴
            # 時間窗的 id 是不透明的識別字(讀的一方照 label 顯示),不算實作的字彙
            out["windows"] = [{k: v for k, v in w.items() if k != "id"} for w in out.get("windows") or []]
            text = json.dumps(out, ensure_ascii=False)
            for w in RUNTIME_WORDS:
                self.assertNotIn(w, text, text)
            self.assertNotIn("_v1", text)

    def test_kinds(self):
        kinds = [e["kind"] for e in self.stored if e["type"] == "tool_use"]
        self.assertEqual(kinds, ["read", "search", "search", "run_command", "open_web", "search", "ask_user", "write_file",
                                 "write_file", "write_file", "other", "other", "other"])

    def test_quota_and_errors(self):
        q = [e for e in self.stored if e["type"] == "quota"][0]
        self.assertEqual((q["status"], [w["id"] for w in q["windows"]], q["windows"][0]["used"]),
                         ("warning", ["five_hour", "seven_day"], 0.81))
        self.assertTrue(q["windows"][0]["resets_at"].startswith("2026-"))
        codes = [e["error"] for e in self.stored if e["type"] == "error"]
        self.assertEqual(codes, ["rate_limit", "bad_request", "agent_login", "process_crashed"])


class RelayEvents(unittest.TestCase):
    def test_old_and_new_relay_reports(self):
        m = load_runner()
        old = {"phase": "start", "tool": "WebFetch", "summary": "https://a", "kind": "permission"}
        new = {"phase": "start", "tool": "WebFetch", "summary": "https://a", "kind": "open_web", "target": "https://a"}
        for b in (old, new):
            etype, data, legacy = m.relay_event(b)
            self.assertEqual((etype, data["kind"]), ("permission", "open_web"))
            self.assertEqual(legacy, {"phase": "start", "tool": "WebFetch", "summary": "https://a", "kind": "permission"})
        etype, data, legacy = m.relay_event({"phase": "end", "tool": "AskUserQuestion", "summary": "q",
                                             "kind": "ask_user", "decision": "answered"})
        self.assertEqual((etype, data["kind"], legacy["kind"], data["decision"]), ("question", "ask_user", "question", "answered"))


class ResumeTruthfully(unittest.TestCase):
    def test_cannot_resume_says_so(self):
        m = load_runner()
        stored = []
        lease = m.Lease({"lease_id": "77777777-7777-4777-8777-777777777777", "project": "p", "cwd": "/tmp",
                         "started_once": True})
        lease.set_state = lambda st, legacy_v1=None, **kw: stored.append((st, kw, legacy_v1))
        fake = type("FP", (), {"pid": 1, "stdout": io.StringIO(""), "stdin": io.StringIO()})()
        with mock.patch.object(m, "deny_settings", lambda p: m.DIR / "d.json"), \
                mock.patch.object(m, "mcp_config", lambda l: m.DIR / "m.json"), \
                mock.patch.object(m, "preamble", lambda p, mode="discuss": "p"), \
                mock.patch.object(m, "env_for", lambda l: {}), mock.patch.object(m, "reader", lambda l, p: None), \
                mock.patch.object(m.subprocess, "Popen", lambda *a, **k: fake), \
                mock.patch.object(m.rt(), "resumable", lambda sid: False):
            m.start_proc(lease, resume=True)
        st, kw, legacy = stored[-1]
        self.assertEqual((st, kw["resumed"]), ("running", False))
        self.assertIn("紀錄已經不在", kw["detail"])
        self.assertEqual(legacy, {"state": "running", "pid": 1, "resumed": False})


class StreamContract(unittest.TestCase):
    """每個登記的 runtime:同一個情境(一段文字、讀一個檔、跑一個指令、配額 80%、一輪結束、額度用完)→ 同樣的中立事件。"""

    SCENARIOS = {
        "claude": [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "好"},
                                                          {"type": "tool_use", "name": "Read", "input": {"file_path": "a.md"}},
                                                          {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}},
            {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning",
                                                             "unifiedWindows": {"five_hour": {"utilization": 0.8}}}},
            {"type": "result", "is_error": False, "num_turns": 2, "result": "ok"},
            {"type": "result", "is_error": True, "num_turns": 1, "result": "usage limit reached"},
        ],
        "fake": [
            {"type": "assistant", "data": {"text": "好"}},
            {"type": "tool_use", "data": {"tool": "read", "input": {"target": "a.md"}}},
            {"type": "tool_use", "data": {"tool": "run_command", "input": {"target": "ls"}}},
            {"type": "quota", "data": {"status": "warning", "windows": [{"id": "w", "label": "w", "used": 0.8}]}},
            {"type": "turn_end", "data": {"num_turns": 2, "is_error": False}},
            {"type": "error", "data": {"error": "rate_limit", "detail": "x"}},
            {"type": "turn_end", "data": {"num_turns": 1, "is_error": True}},
        ],
    }

    def test_every_runtime_has_a_scenario(self):
        self.assertEqual(set(runtime_ports.RUNTIMES), set(self.SCENARIOS))

    def shape(self, events):
        out = []
        for e in events:
            if e.type == "tool_use":
                out.append(("tool_use", e.data["kind"]))
            elif e.type == "quota":
                out.append(("quota", e.data["status"], [w["used"] for w in e.data["windows"]]))
            elif e.type == "error":
                out.append(("error", e.data["error"]))
            elif e.type == "turn_end":
                out.append(("turn_end", e.data.get("num_turns"), e.data.get("is_error")))
            else:
                out.append((e.type,))
        return out

    def test_same_neutral_events(self):
        shapes = {}
        for name in runtime_ports.RUNTIMES:
            rt = runtime_ports.load(name)
            events = [e for line in self.SCENARIOS[name] for e in rt.parse_stream(json.dumps(line))]
            for e in events:
                self.assertIn(e.type, runtime_ports.STREAM_TYPES, name)
                if e.type == "tool_use":
                    self.assertIn(e.data["kind"], runtime_ports.KINDS)
                if e.type == "quota":
                    self.assertIn(e.data["status"], runtime_ports.QUOTA_STATUS)
            self.assertEqual(rt.parse_stream("not json"), [])
            shapes[name] = self.shape(events)
        first = next(iter(shapes.values()))
        for name, s in shapes.items():
            self.assertEqual(s, first, name)

    def test_exit_classification(self):
        for name in runtime_ports.RUNTIMES:
            rt = runtime_ports.load(name)
            self.assertEqual(rt.classify_exit(1, "please login again").data["error"], "agent_login", name)
            self.assertEqual(rt.classify_exit(1, "segfault").data["error"], "process_crashed", name)

    def test_resume_command(self):
        for name in runtime_ports.RUNTIMES:
            cmd, hint = runtime_ports.load(name).resume_command("s1")
            self.assertIn("s1", cmd)
            self.assertTrue(hint)


class HttpVersions(unittest.TestCase):
    def setUp(self):
        import http.server
        self.m = m = load_runner()
        m.token = lambda: "t"
        m.STATUS.update(rate_limit={"five_hour": 0.2, "at": "T"}, quota={"status": "ok", "windows": [], "at": "T"})
        lease = m.Lease({"lease_id": "88888888-8888-4888-8888-888888888888", "project": "p", "cwd": "/tmp"})
        lease.state = "idle_stopped"
        m.LEASES[lease.id] = lease
        self.addCleanup(m.LEASES.clear)
        self.lid = lease.id
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), m.H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     method="POST" if body is not None else "GET",
                                     headers={"Authorization": "Bearer t", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def test_status(self):
        v1, v2 = self.call("/v1/status"), self.call("/v1/status?v=2")
        self.assertEqual((v1["v"], "rate_limit" in v1, "quota" in v1), (1, True, False))
        self.assertEqual((v2["v"], "rate_limit" in v2, "quota" in v2), (2, False, True))
        self.assertEqual(minischema.errors(V2["$defs"]["status"], v2, root=V2), [])

    def test_leases_and_terminal(self):
        with mock.patch.object(self.m.rt(), "resumable", lambda sid: True):
            ls = self.call("/v1/leases?v=2")
        self.assertTrue(ls["leases"][0]["resumable"])
        self.assertNotIn("resumable", self.call("/v1/leases")["leases"][0])
        t2 = self.call(f"/v1/leases/{self.lid}/terminal", {"v": 2})
        self.assertEqual(minischema.errors(V2["$defs"]["terminal"], t2, root=V2), [])
        self.m.LEASES[self.lid].state = "idle_stopped"
        t1 = self.call(f"/v1/leases/{self.lid}/terminal", {"v": 1})
        self.assertEqual(set(t1), {"v", "command"})


if __name__ == "__main__":
    unittest.main()
