"""completion(判斷點的「只回答案」;docs/design/llm-providers.md):每家都要過的同一套處理,跟三個 provider 的請求/回應。

處理用替身(runtime_fake 的 completion)測;provider 用本機的假 HTTP 伺服器測(不連外、不需要 key)。

    bin/py -m unittest discover -s tests
"""
import http.server
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import completion  # noqa: E402
import runtime_fake  # noqa: E402
import runtime_ports  # noqa: E402
from runtime_ports import CompletionRequest, CompletionResult  # noqa: E402

SCHEMA = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}
SPEC = {"model_group": "fast", "max_tokens": 100}


def setUpModule():
    p = mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "zh-Hant"})   # the Refused / config texts asserted are zh-Hant
    p.start()
    unittest.addModuleCleanup(p.stop)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.llm = {"providers": {"f": {"impl": "fake", "models": {"fast": {"name": "m1", "context": 1000},
                                                                    "standard": "m2"}}},
                    "use": {"fast": "f", "standard": "f"}}
        for target, value in (("settings", lambda: self.llm), ("USAGE", self.tmp / "usage.jsonl")):
            p = mock.patch.object(completion, target, value)
            p.start()
            self.addCleanup(p.stop)
        runtime_fake.reset()
        self.addCleanup(runtime_fake.reset)

    def run_(self, prompt="hi", schema=SCHEMA, spec=SPEC, **kw):
        return completion.run("p", spec, prompt, schema, sleep=lambda s: None, **kw)


class Pipeline(Base):
    def test_valid_json(self):
        runtime_fake.set_completions("p", CompletionResult(True, text='{"answer": "2"}', stop="end"))
        out = self.run_()
        self.assertEqual(out["structured_output"], {"answer": "2"})
        self.assertEqual((out["provider"], out["model"]), ("f", "m1"))

    def test_invalid_then_valid_retries_once_with_the_errors(self):
        runtime_fake.set_completions("p", CompletionResult(True, text='{"nope": 1}', stop="end"),
                                     CompletionResult(True, text='{"answer": "ok"}', stop="end"))
        self.assertEqual(self.run_()["structured_output"], {"answer": "ok"})
        self.assertEqual(len(runtime_fake._asked), 2)
        self.assertIn("不合格", runtime_fake._asked[1][0].prompt)

    def test_invalid_twice_refuses(self):
        runtime_fake.set_completions("p", CompletionResult(True, text="not json", stop="end"))
        with self.assertRaises(completion.Refused):
            self.run_()
        self.assertEqual(len(runtime_fake._asked), 2)

    def test_truncation_is_not_retried(self):
        runtime_fake.set_completions("p", CompletionResult(False, text='{"ans', stop="length", error="x"))
        with self.assertRaises(completion.Refused) as cm:
            self.run_()
        self.assertIn("截斷", str(cm.exception))
        self.assertEqual(len(runtime_fake._asked), 1)

    def test_rate_limit_backs_off_then_succeeds(self):
        slept = []
        runtime_fake.set_completions("p", CompletionResult(False, stop="rate_limited", retry_after_s=7),
                                     CompletionResult(True, text='{"answer": "a"}', stop="end"))
        out = completion.run("p", SPEC, "hi", SCHEMA, sleep=slept.append)
        self.assertEqual(out["structured_output"], {"answer": "a"})
        self.assertEqual(slept, [7])

    def test_think_and_fences_are_stripped(self):
        runtime_fake.set_completions("p", CompletionResult(True, text='<think>hmm {"x":1}</think>\n```json\n{"answer": "b"}\n```',
                                                           stop="end"))
        self.assertEqual(self.run_()["structured_output"], {"answer": "b"})

    def test_text_output(self):
        runtime_fake.set_completions("p", CompletionResult(True, text="plain", stop="end"))
        out = self.run_(schema=None)
        self.assertEqual((out["result"], out["structured_output"]), ("plain", None))

    def test_too_long_is_not_sent(self):
        with self.assertRaises(completion.Refused) as cm:
            self.run_(prompt="字" * 2000)   # context 1000
        self.assertIn("太長", str(cm.exception))
        self.assertEqual(runtime_fake._asked, [])

    def test_secrets_are_masked_before_sending(self):
        runtime_fake.set_completions("p", CompletionResult(True, text='{"answer": "a"}', stop="end"))
        self.run_(prompt="key sk-ant-abcdefghijklmnop end")
        self.assertNotIn("sk-ant-abcdefghijklmnop", runtime_fake._asked[0][0].prompt)
        self.assertIn("[已遮蔽]", runtime_fake._asked[0][0].prompt)

    def test_budget(self):
        self.llm["providers"]["f"]["models"]["fast"]["price"] = {"in": 1_000_000, "out": 0}   # 1 token = $1
        self.llm["budget"] = {"daily_usd": 5}
        runtime_fake.set_completions("p", CompletionResult(True, text='{"answer": "a"}', stop="end", input_tokens=10))
        self.run_()
        with self.assertRaises(completion.Refused) as cm:
            self.run_()
        self.assertIn("預算", str(cm.exception))
        rows = [json.loads(x) for x in (self.tmp / "usage.jsonl").read_text().splitlines()]
        self.assertEqual(rows[0]["cost_usd"], 10.0)

    def test_point_override_and_group(self):
        self.llm["points"] = {"p": {"provider": "f", "model": "special"}}
        runtime_fake.set_completions("p", CompletionResult(True, text='{"answer": "a"}', stop="end"))
        self.assertEqual(self.run_()["model"], "special")
        self.assertEqual(completion.resolve("q", {"model_group": "standard"})["model"], "m2")
        with self.assertRaises(completion.Refused):
            completion.resolve("q", {"model_group": "strong"})


class Keys(Base):
    def test_secret_file(self):
        with mock.patch.object(completion.hubconf, "secrets_dir", lambda: self.tmp):
            (self.tmp / "k").write_text("abc\n")
            self.assertEqual(completion.read_key({"key": {"secret": "k"}}), "abc")
            with self.assertRaises(completion.Refused):
                completion.read_key({"key": {"secret": "missing"}})

    def test_env_key_and_it_never_reaches_agents(self):
        self.llm["providers"]["f"]["key"] = {"env": "HUB_TEST_KEY"}
        with mock.patch.dict(os.environ, {"HUB_TEST_KEY": "v"}):
            self.assertEqual(completion.read_key(self.llm["providers"]["f"]), "v")
            self.assertEqual(completion.key_envs(), {"HUB_TEST_KEY"})
            import importlib.util
            spec = importlib.util.spec_from_file_location("llm_mod", ROOT / "bin" / "llm.py")
            llm = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(llm)
            env, _ = llm.auth_env({"auth": "subscription"}, runtime_ports.load("fake", ports=("agent", "auth")))
            self.assertNotIn("HUB_TEST_KEY", env)
        with self.assertRaises(completion.Refused):
            completion.read_key({"key": {"env": "HUB_TEST_KEY_NOT_SET"}})

    def test_no_key_needed(self):
        self.assertIsNone(completion.read_key({}))


class ConfigErrors(unittest.TestCase):
    OK = {"providers": {"a": {"impl": "anthropic", "key": {"secret": "anthropic-api-key"},
                              "models": {"fast": "claude-haiku-4-5-20251001"}},
                        "l": {"impl": "ollama", "base_url": "http://127.0.0.1:11434", "location": "local",
                              "models": {"standard": {"name": "qwen3:32b", "context": 32768}}}},
          "use": {"fast": "a", "standard": "l"}}

    def test_ok(self):
        self.assertEqual(completion.config_errors(self.OK), [])

    def test_key_value_in_config(self):
        bad = json.loads(json.dumps(self.OK))
        bad["providers"]["a"]["key"] = {"secret": "sk-ant-api03-xxxxxxxxxxxx"}
        self.assertTrue(any("key 的值" in e for e in completion.config_errors(bad)))

    def test_local_must_be_private(self):
        bad = json.loads(json.dumps(self.OK))
        bad["providers"]["l"]["base_url"] = "https://api.example.com"
        self.assertTrue(any("location local" in e for e in completion.config_errors(bad)))
        for ok in ("http://localhost:1", "http://192.168.1.5:1", "http://box.tail0a1b2c.ts.net", "http://100.70.1.2"):
            good = json.loads(json.dumps(self.OK))
            good["providers"]["l"]["base_url"] = ok
            self.assertEqual(completion.config_errors(good), [], ok)

    def test_wiring(self):
        bad = json.loads(json.dumps(self.OK))
        bad["providers"]["a"]["impl"] = "nope"
        bad["use"]["strong"] = "a"   # a 沒有 strong
        bad["points"] = {"not-a-point": {"provider": "zz"}}
        errs = completion.config_errors(bad)
        for want in ("不認得", "沒有 strong", "不是宣告過的判斷點", "providers 裡的一家"):
            self.assertTrue(any(want in e for e in errs), (want, errs))

    def test_points_alone_pin_the_runtime_model(self):
        self.assertEqual(completion.config_errors({"points": {"consolidate": {"model": "m"}}}), [])
        errs = completion.config_errors({"points": {"consolidate": {}}, "use": {"fast": "a"}})
        for want in ("沒有 provider 就要寫 model", "沒有 providers"):
            self.assertTrue(any(want in e for e in errs), (want, errs))
        self.assertTrue(any("要寫 use" in e for e in completion.config_errors({"providers": self.OK["providers"]})))


class FakeServer:
    """本機的假 API:記下收到的請求,照 handler 回應。"""

    def __init__(self, handler):
        outer = self
        self.requests = []

        class H(http.server.BaseHTTPRequestHandler):
            def _do(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                outer.requests.append((self.command, self.path, dict(self.headers), body))
                code, resp = handler(self.command, self.path, body)
                raw = json.dumps(resp).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            do_GET = do_POST = _do

            def log_message(self, *a):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


REQ = CompletionRequest(point="p", prompt="hi", model="m", schema=SCHEMA, max_tokens=50, timeout_s=10)


class Providers(unittest.TestCase):
    def serve(self, handler):
        s = FakeServer(handler)
        self.addCleanup(s.close)
        return s

    def test_openai(self):
        import provider_openai
        s = self.serve(lambda m, p, b: (200, {"choices": [{"message": {"content": '{"answer":"x"}'}, "finish_reason": "stop"}],
                                              "usage": {"prompt_tokens": 3, "completion_tokens": 4}}))
        r = provider_openai.complete(REQ, {"base_url": s.url + "/v1", "_key": "K"})
        self.assertEqual((r.ok, r.text, r.input_tokens, r.output_tokens), (True, '{"answer":"x"}', 3, 4))
        _, path, headers, body = s.requests[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual({k.lower(): v for k, v in headers.items()}["authorization"], "Bearer K")
        self.assertEqual(body["response_format"]["json_schema"]["schema"], SCHEMA)

    def test_openai_falls_back_when_response_format_is_refused(self):
        import provider_openai
        def h(m, p, b):
            if "response_format" in b:
                return 400, {"error": {"message": "response_format is not supported"}}
            return 200, {"choices": [{"message": {"content": '{"answer":"y"}'}, "finish_reason": "stop"}]}
        s = self.serve(h)
        r = provider_openai.complete(REQ, {"base_url": s.url})
        self.assertTrue(r.ok)
        self.assertIn("JSON schema", s.requests[1][3]["messages"][-1]["content"])

    def test_openai_length_and_rate_limit(self):
        import provider_openai
        s = self.serve(lambda m, p, b: (200, {"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]}))
        self.assertEqual(provider_openai.complete(REQ, {"base_url": s.url}).stop, "length")
        s2 = self.serve(lambda m, p, b: (429, {"error": "slow down"}))
        self.assertEqual(provider_openai.complete(REQ, {"base_url": s2.url}).stop, "rate_limited")

    def test_anthropic_uses_a_tool_for_structure(self):
        import provider_anthropic
        s = self.serve(lambda m, p, b: (200, {"content": [{"type": "tool_use", "name": "output", "input": {"answer": "z"}}],
                                              "stop_reason": "tool_use", "usage": {"input_tokens": 5, "output_tokens": 6}}))
        r = provider_anthropic.complete(REQ, {"base_url": s.url, "_key": "K"})
        self.assertEqual(json.loads(r.text), {"answer": "z"})
        _, path, headers, body = s.requests[0]
        self.assertEqual(path, "/v1/messages")
        self.assertEqual({k.lower(): v for k, v in headers.items()}["x-api-key"], "K")
        self.assertEqual(body["max_tokens"], 50)
        self.assertEqual(body["tool_choice"], {"type": "tool", "name": "output"})

    def test_anthropic_max_tokens_is_truncation(self):
        import provider_anthropic
        s = self.serve(lambda m, p, b: (200, {"content": [], "stop_reason": "max_tokens"}))
        self.assertEqual(provider_anthropic.complete(REQ, {"base_url": s.url, "_key": "K"}).stop, "length")
        s2 = self.serve(lambda m, p, b: (529, {"error": "overloaded"}))
        self.assertEqual(provider_anthropic.complete(REQ, {"base_url": s2.url, "_key": "K"}).stop, "rate_limited")

    def test_ollama_native_api_context_and_schema_fallback(self):
        import provider_ollama
        def h(m, p, b):
            if "format" in b:
                return 501, {"error": "structured output is unavailable"}
            return 200, {"message": {"content": '{"answer":"o"}'}, "done_reason": "stop", "prompt_eval_count": 7,
                         "eval_count": 8}
        s = self.serve(h)
        r = provider_ollama.complete(REQ, {"base_url": s.url, "_context": 32768})
        self.assertEqual((r.ok, r.input_tokens), (True, 7))
        first, second = s.requests[0][3], s.requests[1][3]
        self.assertEqual(s.requests[0][1], "/api/chat")
        self.assertEqual(first["options"]["num_ctx"], 32768)
        self.assertFalse(first["think"])
        self.assertNotIn("format", second)
        self.assertIn("JSON schema", second["messages"][-1]["content"])

    def test_ollama_health_lists_missing_models(self):
        import provider_ollama
        s = self.serve(lambda m, p, b: (200, {"models": [{"name": "a:latest"}]}))
        self.assertEqual(provider_ollama.health({"base_url": s.url, "models": {"fast": "a"}}), (True, ""))
        ok, why = provider_ollama.health({"base_url": s.url, "models": {"fast": "b"}})
        self.assertFalse(ok)
        self.assertIn("b", why)


class CompletionContract(unittest.TestCase):
    """completion 介面:每個有它的 runtime 跟每個 provider 都實作得完整。"""

    def test_every_impl_loads(self):
        names = [*runtime_ports.PROVIDERS, *[n for n in runtime_ports.RUNTIMES
                                             if "completion" in runtime_ports.capabilities(n)]]
        self.assertIn("claude", names)
        for n in names:
            with self.subTest(impl=n):
                m = runtime_ports.load_completion(n)
                self.assertTrue(callable(m.complete) and callable(m.health))

    def test_claude_runs_without_tools_from_stdin_in_an_empty_dir(self):
        import runtime_claude
        seen = {}

        def fake_run(cmd, **kw):
            seen.update(cmd=cmd, **kw)
            seen["files"] = os.listdir(kw["cwd"])
            class R:
                returncode, stderr = 0, ""
                stdout = json.dumps({"result": "", "structured_output": {"answer": "c"}, "total_cost_usd": 0.01,
                                     "usage": {"input_tokens": 3, "output_tokens": 2}})
            return R()

        with mock.patch.object(runtime_claude.subprocess, "run", fake_run), \
             mock.patch.object(runtime_claude, "auth_env", lambda mode, env: ({"PATH": "/usr/bin"}, "")):
            r = runtime_claude.complete(REQ, {})
        self.assertEqual(json.loads(r.text), {"answer": "c"})
        cmd = seen["cmd"]
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertIn("--strict-mcp-config", cmd)
        self.assertNotIn("hi", cmd, "prompt 從 stdin 給,不放 argv")
        self.assertEqual(seen["input"], "hi")
        self.assertEqual(seen["files"], [], "在空的暫存目錄跑")


if __name__ == "__main__":
    unittest.main()
