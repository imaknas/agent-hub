"""E1 golden (docs/design/i18n.md) for the lease runner, the lease relay, lease_files and the MCP server, on zh-Hant.

Every text these write for a person or hand back to a session: HTTP error details (shown on the owner's phone), events,
log lines, the letters a handover sends, relay deny messages and tool results, show_file refusals, the MCP server's
errors and config text. Prompts (the lease preamble, the handover prompt, tool descriptions) are not here.

In-process, with the subprocesses, the assistant and the runtime replaced by canned answers; the MCP server runs as a
subprocess against an isolated config, as in tests/test_mcp_server.py.

    bin/py -m unittest tests.test_i18n_golden_lease
"""
import http.server
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_lang  # noqa: E402

GOLDEN = golden_lang.GOLDEN
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
ZH = {"AGENT_HUB_LANG_UI": golden_lang.LANG, "AGENT_HUB_LANG_CONTENT": golden_lang.LANG}   # an instance on one language
SID = "77777777-7777-4777-8777-777777777777"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, BIN / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def dump(out: list, tmp: pathlib.Path) -> str:
    text = "\n".join(f"=== {k}\n{v}" for k, v in out) + "\n"
    text = text.replace(str(tmp), "<TMP>").replace(str(ROOT), "<ROOT>")
    text = re.sub(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]", "[<TS>]", text)
    text = re.sub(r"\d{8}-\d{6}-", "<STAMP>-", text)
    text = re.sub(r"\d{4}-\d{2}-\d{2}\.md", "<DATE>.md", text)
    return re.sub(r"(## [^\n]*? )\d{2}:\d{2}( ?\(session)", r"\1<HM>\2", text)   # the lease note's heading


class Golden(unittest.TestCase):
    maxDiff = None

    def check(self, name: str, text: str):
        f = GOLDEN / f"{name}.txt"
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        p = mock.patch.dict(os.environ, ZH)
        p.start()
        self.addCleanup(p.stop)


class FakeRuntime:
    def __init__(self):
        self.can_resume = False

    def resumable(self, sid):
        return self.can_resume

    def relocate(self, sid, old, new):
        return True

    def parse_stream(self, line):
        return []


class FakeProc:
    def __init__(self):
        import io
        self.stdin, self.stdout, self.pid, self.returncode = io.StringIO(), io.StringIO(""), 4242, 0

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0


class Runner(Golden):
    def setUp(self):
        super().setUp()
        self.m = m = load("lease_runner_golden", "lease-runner.py")
        m.DIR = self.tmp / "state"
        self.rt = FakeRuntime()
        self.ws = self.tmp / "ws" / "demo"
        (self.ws / ".agent-memory" / "memory").mkdir(parents=True)
        self.projects = {"demo": {"paths": {"workspace": str(self.ws)}}}
        for target, name, value in [
                (m, "rt", lambda: self.rt), (m, "deny_settings", lambda project: self.tmp / "deny.json"),
                (m, "mcp_config", lambda lease: self.tmp / "mcp.json"), (m, "env_for", lambda lease: {}),
                (m, "token", lambda: "t"), (m.hubconf, "projects", lambda: self.projects),
                (m.hubconf, "workspace", lambda name: self.ws if name in self.projects else None),
                (m.hubconf, "owner_tag", lambda: "Alex")]:   # not the real instance's (hubconf caches hub.json)
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(m.LEASES.clear)

    def lease(self, sid=SID, events=None):
        l = self.m.Lease({"lease_id": sid, "project": "demo", "cwd": str(self.ws)})
        l.state = "idle_stopped"
        if events is not None:
            l.emit = lambda t, **kw: events.append((t, kw))
        return l

    def serve(self):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self.m.H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def call(path, body=None, raw=None):
            data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
            req = urllib.request.Request(base + path, data=data, method="POST" if data is not None else "GET",
                                         headers={"Authorization": "Bearer t"})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, r.read().decode()
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode()
        return call

    def test_texts(self):
        m, out = self.m, []
        out.append(("work_ready", m.work_ready("demo")[1]))
        # start_proc: wanted to resume, but there is no conversation to resume
        ev = []
        l = self.lease(events=ev)
        l.started_once = True
        with mock.patch.object(m.subprocess, "Popen", lambda cmd, **kw: FakeProc()), \
                mock.patch.object(m.threading, "Thread", lambda **kw: types.SimpleNamespace(start=lambda: None)):
            m.start_proc(l, resume=True)
        out.append(("start_proc not resumable", json.dumps(ev, ensure_ascii=False)))
        # attachments
        for atts in ("x", [{}] * 9, [{}], [{"name": "a.png", "media_type": "image/png", "data": "@@"}],
                     [{"name": "big.png", "media_type": "image/png", "data": "QUJD"}]):
            with mock.patch.object(m, "MAX_ATTACHMENT_BYTES", 2 if atts and atts != "x" and atts[0].get("data") == "QUJD"
                                   else m.MAX_ATTACHMENT_BYTES):
                out.append(("check_attachments", m.check_attachments(atts)))
        l = self.lease(events=[])
        l.proc = FakeProc()
        m.send_message(l, "look", [{"name": "a.png", "media_type": "image/png", "data": "QUJD"}])
        out.append(("send_message", l.proc.stdin.getvalue()))
        # handover
        out.append(("empty handover", m.run_handover(self.lease())[0]["summary"]))  # no transcript: nothing to hand over
        self.rt.can_resume = True
        bad = types.SimpleNamespace(returncode=1, stdout=json.dumps({"llm_error": "schema: no summary"}), stderr="")
        for name, run_handover, deliver in [
                ("model failed", None, None),
                ("platform failed", mock.Mock(side_effect=RuntimeError("old module")), None),
                ("deliver failed", mock.Mock(return_value=({"summary": "s"}, "")), mock.Mock(side_effect=OSError("bus")))]:
            ev = []
            l = self.lease(sid=SID[:-1] + str(len(out) % 10), events=ev)
            patches = [mock.patch.object(m.subprocess, "run", lambda *a, **k: bad),
                       mock.patch.object(m, "deliver", deliver or (lambda lease, h, n, earlier=None: None)),
                       mock.patch.object(m, "earlier_tasks", lambda project: [])]
            if run_handover:
                patches.append(mock.patch.object(m, "run_handover", run_handover))
            for p in patches:
                p.start()
            try:
                m.detach(l, "user")
            finally:
                for p in patches:
                    p.stop()
            out.append((f"detach {name}", "\n".join(
                json.dumps(e, ensure_ascii=False) for e in ev if e[0] in ("error", "handover"))))
        ev = []
        l = self.lease(events=ev)
        l.state = "terminal"
        with mock.patch.object(m, "terminal_open", lambda lease: True):
            m.detach(l, "user")
        out.append(("detach terminal open", json.dumps(ev, ensure_ascii=False)))
        # deliver: the letters and the memory note
        sent = []
        h = {"summary": "talked about A", "decisions": [{"what": "use X", "why": "fast"}, {"what": "drop Y"}],
             "open_questions": ["which Z?"], "tasks_for_owner": [{"text": "renew key", "due": "2026-11-01"}],
             "notes_markdown": "- note one"}
        with mock.patch.object(m, "hubbus", lambda *a: sent.append(a)), \
                mock.patch.object(m.subprocess, "Popen", lambda *a, **k: None):
            m.deliver(self.lease(), h, 1)
        texts = [a[a.index("--text") + 1] for a in sent if "--text" in a]
        out.append(("deliver letters", "\n---\n".join(texts)))
        out.append(("deliver memory", "".join(f.read_text() for f in (self.ws / ".agent-memory" / "memory").glob("*.md"))))
        # show_file refusal, prune, restart
        l = self.lease()
        out.append(("show_file refused", m.show_file(l, "../x.png", None)[1]))
        with mock.patch.object(m.hubconf, "code_stamp", lambda: 1.0), mock.patch.object(m.os, "_exit", lambda c: None):
            m.CODE_AT_START = 0.5
            m.restart_if_code_changed()
        # quiesce, follow_move
        busy = self.lease()
        busy.in_turn = True
        m.LEASES[busy.id] = busy
        out.append(("quiesce", m.quiesce("demo")))
        m.LEASES.clear()
        ev = []
        moving = self.lease(events=ev)
        moving.mode = "work"
        m.LEASES[moving.id] = moving
        with mock.patch.dict(sys.modules, {"registry": types.SimpleNamespace(refresh=lambda: None)}):
            m.follow_move("demo", "demo", "/old/demo")
        out.append(("follow_move", json.dumps(ev, ensure_ascii=False)))
        m.LEASES.clear()
        # HTTP
        call = self.serve()
        l = self.lease()
        m.LEASES[l.id] = l
        reqs = [("not json", f"/v1/leases/{SID}/messages", None, b"{"),
                ("bad attachments", f"/v1/leases/{SID}/messages", {"client_msg_id": "c", "attachments": "x"}, None),
                ("no text", f"/v1/leases/{SID}/messages", {"client_msg_id": "c"}, None),
                ("bad mode", f"/v1/leases/{SID}/mode", {"mode": "x"}, None),
                ("work not allowed", f"/v1/leases/{SID}/mode", {"mode": "work"}, None),
                ("file 404", f"/v1/leases/{SID}/files/{'0' * 16}", None, None),
                ("sandbox bad name", "/v1/sandboxes", {"name": 1}, None),
                ("rename bad name", "/v1/projects/demo/rename", {}, None),
                ("upgrade bad rename", "/v1/sandboxes/demo/upgrade", {"rename": 1}, None)]
        for name, path, body, raw in reqs:
            out.append((f"http {name}", "%s %s" % call(path, body, raw)))
        l.in_turn = True
        out.append(("http mode busy", "%s %s" % call(f"/v1/leases/{SID}/mode", {"mode": "discuss"})))
        out.append(("http rename busy", "%s %s" % call("/v1/projects/demo/rename", {"to": "demo2"})))
        out.append(("http upgrade busy", "%s %s" % call("/v1/sandboxes/demo/upgrade", {})))
        l.in_turn = False
        need = {"agent", "auth", "sessions", "stream"}
        with mock.patch.object(m.runtime_ports, "capabilities", lambda *a: set()), \
                mock.patch.object(m.runtime_ports, "default_name", lambda *a: "fake"):
            out.append(("http runtime unsupported", "%s %s" % call("/v1/leases", {"project": "demo", "session_id": SID})))
        with mock.patch.object(m.runtime_ports, "capabilities", lambda *a: need), \
                mock.patch.dict(sys.modules, {"registry": types.SimpleNamespace(refresh=lambda: None)}):
            out.append(("http create bad mode", "%s %s" % call("/v1/leases", {"project": "demo", "mode": "x"})))
            out.append(("http create bad sid", "%s %s" % call("/v1/leases", {"project": "demo", "session_id": "x"})))
            with mock.patch.object(m, "GLOBAL_CAP", 1):
                code, body = call("/v1/leases", {"project": "demo", "session_id": SID[:-1] + "0"})
                out.append(("http cap reached", f"{code} {json.loads(body)['detail']}"))
        # main's start line
        with mock.patch.object(m.http.server, "ThreadingHTTPServer",
                               lambda *a: types.SimpleNamespace(serve_forever=lambda: None)), \
                mock.patch.object(m.threading, "Thread", lambda **kw: types.SimpleNamespace(start=lambda: None)):
            m.main()
        out.append(("runner.log", (m.DIR / "runner.log").read_text().replace(f"{m.HOST}:{m.PORT}", "<HOST:PORT>")))
        self.check("lease-runner", dump(out, self.tmp))


class Relay(Golden):
    def test_texts(self):
        m = load("lease_relay_golden", "lease-relay.py")
        out, answer = [], {}
        ws = self.tmp / "ws"
        ws.mkdir()
        rules = {}
        with mock.patch.object(m, "tell_runner", lambda *a, **k: None), \
                mock.patch.object(m, "ask_owner", lambda *a: answer), \
                mock.patch.object(m.policy, "approvals", lambda project, mode: {"rules": rules, "web_allowed": []}), \
                mock.patch.object(m, "WS", str(ws)):

            def decide(tool, **inp):
                return m.decide({"tool_name": tool, "input": inp, "tool_use_id": "t"}).get("message", "allow")
            for mode in ("discuss", "work"):
                with mock.patch.object(m, "MODE", mode):
                    out.append((f"deny {mode}", decide("NotebookEdit", notebook_path="x")))
            rules.update(ask_user="ask", open_web="listed", write_file="inside", run_command="ask_once",
                         edit_file="ask")
            out.append(("question no answer", decide("AskUserQuestion", questions=[{"question": "q?"}])))
            for d in ("deny", None):
                answer.clear()
                if d:
                    answer["decision"] = d
                out.append((f"web {d}", decide("WebFetch", url="https://example.com/x")))
                out.append((f"command {d}", decide("Bash", command="ls")))
                out.append((f"action {d}", m.decide_ask("r", "Other", "other", "t", {})["message"]))
            out.append(("write outside", decide("Write", file_path="/etc/hosts", content="x")))
            out.append(("write no path", m.decide_write("r", "Write", "write_file", "", {})["message"]))
            sent = []
            with mock.patch.object(m, "decide", side_effect=RuntimeError("boom")), mock.patch.object(m, "send", sent.append):
                m.handle_call(1, {})
            out.append(("handle_call crash", sent[0]["result"]["content"][0]["text"]))
        for name, res in [("shown", (200, {"id": "a" * 16, "path": "r.png", "mime": "image/png", "size": 9})),
                          ("no runner", (0, {})), ("refused", (400, {"error": "file_refused", "detail": "why"})),
                          ("error only", (500, {"error": "internal"})), ("code only", (502, {}))]:
            with mock.patch.object(m, "http", return_value=res):
                out.append((f"show_file {name}", "%s %s" % m.show_file({"path": "r.png"})))
        sent = []
        with mock.patch.object(m, "show_file", side_effect=RuntimeError("boom")), mock.patch.object(m, "send", sent.append):
            m.handle_show(1, {})
        out.append(("handle_show crash", sent[0]["result"]["content"][0]["text"]))
        self.check("lease-relay", dump(out, self.tmp))


class Files(Golden):
    def test_texts(self):
        import lease_files
        ws, store = self.tmp / "ws", self.tmp / "store"
        (ws / "r").mkdir(parents=True)
        (ws / "r" / "a.png").write_bytes(PNG)
        (ws / "fake.png").write_bytes(b"nope")
        (ws / "bad.md").write_bytes(b"\xff\xfe")
        (ws / "key.md").write_text("-----BEGIN RSA PRIVATE KEY-----\n")
        (ws / "token.txt").write_text("x")
        (ws / "dir.png").mkdir()
        (ws / "linked.png").write_bytes(PNG)
        os.link(ws / "linked.png", ws / "hard.png")
        (self.tmp / "outside.png").write_bytes(PNG)
        out = []

        def why(path, **kw):
            return lease_files.take(ws, path, "s", store, **kw)[1]
        for path in ("", "../outside.png", "x.svg", "token.txt", "r/a.png@secret", "gone.png", "dir.png", "hard.png",
                     "bad.md", "key.md", "fake.png"):
            kw = {"secret_paths": [ws / "r" / "a.png"]} if path.endswith("@secret") else {}
            out.append((path or "(empty)", why(path.removesuffix("@secret"), **kw)))
        with mock.patch.object(lease_files, "MAX_FILE", 10):
            out.append(("too big", why("r/a.png")))
        with mock.patch.object(lease_files.os, "fstat", lambda fd: os.stat_result((0o100644, 0, 0, 1, 0, 0, 0, 0, 0, 0))), \
                mock.patch.object(lease_files, "MAX_FILE", 10):
            out.append(("too big after read", why("r/a.png")))
        self.assertEqual(why("r/a.png"), "")
        with mock.patch.object(lease_files, "MAX_COUNT", 1):
            out.append(("count", why("r/a.png")))
        with mock.patch.object(lease_files, "MAX_TOTAL", len(PNG) + 1):
            out.append(("total", why("r/a.png")))
        with mock.patch.object(lease_files, "_fd_path", lambda fd: self.tmp / "elsewhere.png"):
            out.append(("swapped", why("r/a.png")))
        self.check("lease_files", dump(out, self.tmp))


class Mcp(Golden):
    def test_texts(self):
        import yaml
        t = self.tmp
        for d in ("home", "config/projects", "data", "secrets", "bus"):
            (t / d).mkdir(parents=True)
        ws = t / "ws" / "demo"
        (ws / ".agent-memory" / "memory").mkdir(parents=True)
        (t / "ws" / "bare").mkdir(parents=True)
        cfg = {"owner": {"name": "alex", "tag": "Alex"}, "owner_side": "builtin", "bus_dir": str(t / "bus"),
               "registry_dir": "projects", "workspace_root": str(t / "ws")}
        (t / "config" / "hub.json").write_text(json.dumps(cfg))
        for name, path in (("demo", ws), ("bare", t / "ws" / "bare")):
            doc = {"v": 1, "name": name, "paths": {"workspace": str(path)}, "memory": True}
            (t / "config" / "projects" / f"{name}.yaml").write_text(yaml.safe_dump(doc))
        env = {**{k: v for k, v in os.environ.items() if not k.startswith(("AGENT_HUB_", "AGENTHUB_", "HUB_"))
                  or k == "AGENT_HUB_PYTHON"},
               "HOME": str(t / "home"), "AGENT_HUB_CONFIG": str(t / "config"), "AGENT_HUB_DATA": str(t / "data"),
               "AGENT_HUB_SECRETS": str(t / "secrets"), "AGENT_HUB_STATE": str(t / "state"), **ZH}
        out = []

        def session(calls, cwd=ws, extra=None):
            msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}}]
            msgs += [{"jsonrpc": "2.0", "id": 10 + i, "method": "tools/call", "params": {"name": n, "arguments": a}}
                     for i, (n, a) in enumerate(calls)]
            r = subprocess.run([sys.executable, str(BIN / "mcp_server.py")],
                               input="".join(json.dumps(x) + "\n" for x in msgs), env={**env, **(extra or {})}, cwd=cwd,
                               capture_output=True, text=True, timeout=120)
            res = {x["id"]: x["result"] for x in map(json.loads, r.stdout.splitlines())}
            return r.stderr, [res[10 + i]["content"][0]["text"] for i in range(len(calls))]
        err, res = session([("inbox", {}), ("note", {"text": "a  b"}), ("nope", {}), ("show", {})])
        out += [("stderr", err), ("inbox (empty)", res[0]), ("note", res[1]), ("unknown tool", res[2]),
                ("bad arguments", res[3])]
        err, res = session([("note", {"text": "x"})], extra={"AGENTHUB_PROJECT": "nowhere"})
        out += [("note without memory", res[0])]
        err, res = session([("inbox", {})], cwd=t)
        out += [("stderr unknown project", err), ("unknown project", res[0])]
        err, res = session([("send", {"to": "x", "type": "info", "text": "x"})], extra={"HUB_LLM_POINT": "mailbox-propose"})
        out += [("platform-launched", res[0])]
        subprocess.run([sys.executable, str(BIN / "hubbus"), "send", "--from", "bare", "--to", "demo", "--type", "info",
                        "--text", "hello"], env=env, cwd=t, capture_output=True, text=True, timeout=60, check=True)
        err, res = session([("inbox", {})])
        one = re.sub(r"\bm\d{8}-[0-9a-f]{4}\b", "<ID>", res[0])
        out += [("inbox (one letter)", re.sub(r"\b\d{2}-\d{2} \d{2}:\d{2}\b", "<TS>", one))]
        # in-process: the unread line of the instructions, the registry error, config
        m = load("mcp_server_golden", "mcp_server.py")
        tpl = self.tmp / "mcp-instructions.md"
        tpl.write_text("P={{project}} U={{unread}} T={{take_timeout}}")
        with mock.patch.object(m, "INSTRUCTIONS", tpl):
            out.append(("instructions unknown", m.instructions(None, "why")))
            for name, hb in [("2 unread", types.SimpleNamespace(TAKE_TIMEOUT_MIN=60, db=lambda: types.SimpleNamespace(
                                 close=lambda: None), inbox=lambda c, p: [1, 2])),
                             ("1 unread", types.SimpleNamespace(TAKE_TIMEOUT_MIN=60, db=lambda: types.SimpleNamespace(
                                 close=lambda: None), inbox=lambda c, p: [1])),
                             ("0 unread", types.SimpleNamespace(TAKE_TIMEOUT_MIN=60, db=lambda: types.SimpleNamespace(
                                 close=lambda: None), inbox=lambda c, p: [])),
                             ("mailbox error", types.SimpleNamespace(TAKE_TIMEOUT_MIN=60, db=mock.Mock(
                                 side_effect=OSError("locked"))))]:
                with mock.patch.object(m, "_hubbus", lambda: hb):
                    out.append((f"instructions {name}", m.instructions("demo", "")))
        with mock.patch.dict(os.environ, {"AGENTHUB_PROJECT": ""}), \
                mock.patch.object(m, "_hubbus", mock.Mock(side_effect=ValueError("bad yaml"))):
            out.append(("registry error", m.resolve_project()[1]))
        with mock.patch.object(m.pathlib.Path, "home", lambda: t / "home"), mock.patch("builtins.print") as p:
            m.config()
        out.append(("config", "\n".join(" ".join(str(a) for a in c.args) for c in p.call_args_list)))
        self.check("mcp_server", dump(out, t).replace(sys.executable, "<PY>"))


if __name__ == "__main__":
    sys.exit(unittest.main())
