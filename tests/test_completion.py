"""completion (a judgment point that "only returns the answer"; docs/design/llm-providers.md): the same handling every
provider must pass, and the three providers' requests/responses.

The handling is tested with a stand-in (runtime_fake's completion); the providers with a local fake HTTP server (no
outside connection, no key needed).

    bin/py -m unittest discover -s tests
"""
import http.server
import json
import os
import pathlib
import shutil
import string
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))
import completion  # noqa: E402
import i18n  # noqa: E402
import runtime_fake  # noqa: E402
import runtime_ports  # noqa: E402
from runtime_ports import CompletionRequest, CompletionResult  # noqa: E402

SCHEMA = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}
SPEC = {"model_group": "fast", "max_tokens": 100}


def zh(msgid: str, **kw) -> str:
    """completion's zh-Hant text of a message, its placeholders filled from kw; a placeholder not given ends it there (the
    fixed part before it, to look for)"""
    out = ""
    for lit, field, spec, conv in string.Formatter().parse(i18n.catalog("completion", "zh-Hant")[msgid]):
        out += lit
        if field is None:   # the end, or an escaped brace
            continue
        if field not in kw:
            return out
        out += format(repr(kw[field]) if conv == "r" else kw[field], spec)
    return out


def setUpModule():
    p = mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "zh-Hant"})   # the Refused / config texts asserted are zh-Hant
    p.start()
    unittest.addModuleCleanup(p.stop)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
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
        self.assertIn("not valid", runtime_fake._asked[1][0].prompt)

    def test_invalid_twice_refuses(self):
        runtime_fake.set_completions("p", CompletionResult(True, text="not json", stop="end"))
        with self.assertRaises(completion.Refused):
            self.run_()
        self.assertEqual(len(runtime_fake._asked), 2)

    def test_truncation_is_not_retried(self):
        runtime_fake.set_completions("p", CompletionResult(False, text='{"ans', stop="length", error="x"))
        with self.assertRaises(completion.Refused) as cm:
            self.run_()
        self.assertIn(zh("output cut off at the length limit ({provider}/{model}, max_tokens {max_tokens}); not retried",
                         provider="f", model="m1", max_tokens=100), str(cm.exception))
        self.assertEqual(len(runtime_fake._asked), 1)

    def test_rate_limit_backs_off_then_succeeds(self):
        slept = []
        runtime_fake.set_completions("p", CompletionResult(False, stop="rate_limited", retry_after_s=7),
                                     CompletionResult(True, text='{"answer": "a"}', stop="end"))
        out = completion.run("p", SPEC, "hi", SCHEMA, sleep=slept.append)
        self.assertEqual(out["structured_output"], {"answer": "a"})
        self.assertEqual(slept, [7])

    def test_max_attempts_one_asks_once_whatever_comes_back(self):
        """A point anyone's text reaches (mailbox-route): one request, never a second for an invalid answer or a rate limit."""
        one = {**SPEC, "max_attempts": 1}
        for first in (CompletionResult(True, text="not json", stop="end"),
                      CompletionResult(False, stop="rate_limited", retry_after_s=1)):
            with self.subTest(stop=first.stop):
                runtime_fake.reset()
                slept = []
                runtime_fake.set_completions("p", first, CompletionResult(True, text='{"answer": "a"}', stop="end"))
                with self.assertRaises(completion.Refused) as cm:
                    completion.run("p", one, "hi", SCHEMA, sleep=slept.append)
                self.assertEqual((len(runtime_fake._asked), slept), (1, []))
                self.assertNotIn(zh("output does not match the schema (after one retry): {errors}"), str(cm.exception))

    def test_max_attempts_bounds_both_kinds_together(self):
        runtime_fake.set_completions("p", CompletionResult(False, stop="rate_limited", retry_after_s=1),
                                     CompletionResult(True, text="not json", stop="end"),
                                     CompletionResult(True, text='{"answer": "a"}', stop="end"))
        with self.assertRaises(completion.Refused):
            completion.run("p", {**SPEC, "max_attempts": 2}, "hi", SCHEMA, sleep=lambda s: None)
        self.assertEqual(len(runtime_fake._asked), 2)

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
            self.run_(prompt="é" * 3000)   # 6000 UTF-8 bytes, as before; context 1000
        # the fixed part before the token estimate
        self.assertIn(zh("prompt too long: about {tokens} tokens plus {max_tokens} of output exceed {provider}/{model}'s "
                         "{context}-token context"), str(cm.exception))
        self.assertEqual(runtime_fake._asked, [])

    def test_secrets_are_masked_before_sending(self):
        runtime_fake.set_completions("p", CompletionResult(True, text='{"answer": "a"}', stop="end"))
        self.run_(prompt="key sk-ant-abcdefghijklmnop end")
        self.assertNotIn("sk-ant-abcdefghijklmnop", runtime_fake._asked[0][0].prompt)
        self.assertIn("[redacted]", runtime_fake._asked[0][0].prompt)

    def test_budget(self):
        self.llm["providers"]["f"]["models"]["fast"]["price"] = {"in": 1_000_000, "out": 0}   # 1 token = $1
        self.llm["providers"]["f"]["key"] = {"env": "F_TEST_KEY"}   # a key: pay-per-use, what the budget counts
        self.llm["budget"] = {"daily_usd": 5}
        runtime_fake.set_completions("p", CompletionResult(True, text='{"answer": "a"}', stop="end", input_tokens=10))
        with mock.patch.dict(os.environ, {"F_TEST_KEY": "k"}):
            self.run_()
            with self.assertRaises(completion.Refused) as cm:
                self.run_()
        self.assertIn(zh("today's model spending has reached the budget (llm.budget.daily_usd ${budget}); not sending "
                         "this one", budget=5), str(cm.exception))
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
        want = zh("{where} looks like a key value: the value goes in the secrets directory ({{\"secret\": file name}}) or "
                  "an environment variable ({{\"env\": name}})", where="providers.a.key.secret")
        self.assertIn(want, completion.config_errors(bad))

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
        bad["use"]["strong"] = "a"   # a has no strong
        bad["points"] = {"not-a-point": {"provider": "zz"}}
        errs = completion.config_errors(bad)
        for want in (zh("providers.{name}.impl `{impl}` is not known (have: {have})", name="a", impl="nope"),
                     zh("use.{group} → {name}, but providers.{name}.models has no {group}", group="strong", name="a"),
                     zh("points.{point}: not a declared judgment point", point="not-a-point"),
                     zh("points.{point}.provider must name one of the providers", point="not-a-point")):
            self.assertTrue(any(want in e for e in errs), (want, errs))

    def test_points_alone_pin_the_runtime_model(self):
        self.assertEqual(completion.config_errors({"points": {"consolidate": {"model": "m"}}}), [])
        errs = completion.config_errors({"points": {"consolidate": {}}, "use": {"fast": "a"}})
        for want in (zh("points.{point}: without a provider, model is required (the model the runtime uses)",
                        point="consolidate"),
                     zh("use is set but providers is not (to pin only some judgment points' models, write only points)")):
            self.assertTrue(any(want in e for e in errs), (want, errs))
        self.assertIn(zh("with providers, use is required (which provider serves fast / standard / strong)"),
                      completion.config_errors({"providers": self.OK["providers"]}))


class FakeServer:
    """Local fake API: records the requests it gets, responds per handler."""

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


class QuietAnswer(Base):
    """llm.answer: a completion point's answer for a caller none of whose failures may carry text (mailbox-route): the same
    target and checks as `llm.py run`, but nothing printed and no job.failed, however it fails."""

    def setUp(self):
        super().setUp()
        import importlib.util
        spec = importlib.util.spec_from_file_location("llm_quiet", ROOT / "bin" / "llm.py")
        self.llm_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.llm_mod)
        self.published = []
        self.llm_mod.job_failed = lambda point, reason: self.published.append(reason)
        self.points = {"points": {"q": {"shape": "completion", "model_group": "fast", "max_attempts": 1, "max_tokens": 100,
                                        "output": {"format": "json", "schema": "llm/mailbox-route.schema.json"}},
                                  "a": {"shape": "agent"}}}
        self.llm_mod.points = lambda: self.points

    def ask(self, point="q"):
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            got = self.llm_mod.answer(point, "which one")
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""), "nothing printed")
        return got

    def test_an_answer(self):
        runtime_fake.set_completions("q", CompletionResult(True, text='{"session": "S2"}', stop="end"))
        self.assertEqual(self.ask(), {"session": "S2"})
        self.assertEqual(len(runtime_fake._asked), 1)

    def test_no_answer_however_it_fails_and_nothing_published(self):
        cases = {"invalid": CompletionResult(True, text='{"session": 3, "why": "SECRET TEXT"}', stop="end"),
                 "error": CompletionResult(False, stop="error", error="SECRET TEXT"),
                 "limited": CompletionResult(False, stop="rate_limited")}
        for name, res in cases.items():
            with self.subTest(name):
                runtime_fake.reset()
                runtime_fake.set_completions("q", res)
                self.assertIsNone(self.ask())
                self.assertEqual(len(runtime_fake._asked), 1, "max_attempts 1: asked once")
        self.assertEqual(self.published, [])

    def test_nowhere_to_run_and_an_agent_point(self):
        self.llm = None   # no provider; the default runtime: none
        with mock.patch.object(runtime_ports, "default_name", lambda: "none"):
            self.assertIsNone(self.ask())
        self.assertIsNone(self.ask("a"))
        self.assertIsNone(self.ask("not-declared"))
        self.assertEqual((runtime_fake._asked, self.published), ([], []))


class ReadOnlySecrets(Base):
    """A secrets folder the platform can't write (mode 0o500): the runtimes' complete() can't make the run's folder in
    hubconf.private_tmp. That is a refusal with the reason, through completion.run to llm.py's run path (the point's
    failure is published and printed), never an exception escaping to the caller, and nothing is run."""

    def setUp(self):
        super().setUp()
        if os.geteuid() == 0:
            self.skipTest("root writes anywhere")
        import hubconf
        import runtime_claude
        self.secrets = self.tmp / "secrets"
        self.secrets.mkdir(mode=0o500)
        self.addCleanup(self.secrets.chmod, 0o700)
        for obj, name, value in ((hubconf, "secrets_dir", lambda: self.secrets),
                                 (runtime_claude, "auth_env", lambda mode, env: (dict(env), "")),
                                 (hubconf, "get", lambda k, d=None: {"run": ["true"]} if k == "runtime.command" else d)):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        never = mock.patch("subprocess.run", side_effect=AssertionError("nothing may run"))
        never.start()
        self.addCleanup(never.stop)
        import store_fixture
        store_fixture.stand_in(self)   # the checked copy is there: what refuses is the run's folder
        self.llm = None   # no llm section (Base's settings read it at call time): llm.py's run goes to the runtime

    def test_the_premise_private_tmp_raises(self):
        import hubconf
        with self.assertRaises(PermissionError):
            hubconf.private_tmp()

    def test_completion_run_refuses_with_the_reason(self):
        with self.assertRaises(completion.Refused) as cm:
            completion.run("p", SPEC, "hi", None, target=completion.runtime_target("claude", "m", "token"))
        self.assertTrue(str(cm.exception).startswith("claude/m:error:"), cm.exception)
        self.assertIn("Errno 13", str(cm.exception))
        # the command runtime has no completion interface (docs/design/runtime-codex.md, step 0): refused before any folder
        with self.assertRaises(completion.Refused) as cm:
            completion.run("p", SPEC, "hi", None, target=completion.runtime_target("command", "m", "token"))
        self.assertIn(runtime_ports.port_labels()["completion"], str(cm.exception))

    def test_llm_run_reports_the_refusal(self):
        import contextlib
        import importlib.util
        import io
        import types
        spec = importlib.util.spec_from_file_location("llm_ro", ROOT / "bin" / "llm.py")
        llm_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(llm_mod)
        published = []
        llm_mod.job_failed = lambda point, reason: published.append(reason)
        with mock.patch.object(runtime_ports, "default_name", lambda: "claude"), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            rc = llm_mod.run_completion(types.SimpleNamespace(point="p"), {"shape": "completion", **SPEC}, "hi", "m")
        self.assertEqual(rc, 1)
        got = json.loads(out.getvalue())
        self.assertTrue(got["is_error"])
        self.assertIn("Errno 13", got["llm_error"])
        self.assertEqual(published, [got["llm_error"]])


class CompletionContract(unittest.TestCase):
    """The completion interface: every runtime that has it and every provider implement it completely."""

    def test_every_impl_loads(self):
        import store_fixture
        store_fixture.stand_in(self)   # a runtime's completion runs its checked copy
        names = [*runtime_ports.PROVIDERS, *[n for n in runtime_ports.RUNTIMES
                                             if "completion" in runtime_ports.capabilities(n)]]
        import runtime_store
        for n in names:   # every other runtime with a store gets a copy in the same one
            if n != "claude" and n in runtime_ports.RUNTIMES and callable(getattr(runtime_ports._module(n), "checked_copy", None)):
                runtime_store.install(n, "0.169.0", store_fixture.Dist(store_fixture.ANSWER, name=n))
                runtime_store.promote(n, "0.169.0")
        self.assertIn("claude", names)
        for n in names:
            with self.subTest(impl=n):
                m = runtime_ports.load_completion(n)
                self.assertTrue(callable(m.complete) and callable(m.health))

    def test_claude_runs_without_tools_from_stdin_in_an_empty_dir(self):
        import sys
        import tempfile
        import hubconf
        import runtime_claude
        import store_fixture
        tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        seen = tmp / "seen.json"
        result = {"type": "result", "result": "", "structured_output": {"answer": "c"}, "total_cost_usd": 0.01,
                  "usage": {"input_tokens": 3, "output_tokens": 2}}
        # the checked copy notes what it was given and answers in stream-json, as complete() reads it
        store_fixture.stand_in(self, body=f"#!{sys.executable}\nimport json, os, sys\n"
                               f"json.dump({{'cmd': sys.argv[1:], 'input': sys.stdin.read(), 'files': os.listdir('.')}}, open({str(seen)!r}, 'w'))\n"
                               f"print({store_fixture.INIT!r})\nprint({json.dumps(result)!r})\n")
        private = tmp / "private"
        private.mkdir()
        with mock.patch.object(hubconf, "private_tmp", lambda: private), \
             mock.patch.object(runtime_claude, "auth_env", lambda mode, env: ({"PATH": "/usr/bin"}, "")):
            r = runtime_claude.complete(REQ, {})
        self.assertEqual(json.loads(r.text), {"answer": "c"})
        got = json.loads(seen.read_text())
        cmd = got["cmd"]
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertIn("--strict-mcp-config", cmd)
        self.assertNotIn("hi", cmd, "the prompt comes on stdin, not in argv")
        self.assertEqual(got["input"], "hi")
        self.assertEqual(got["files"], [], "runs in an empty temp directory")


if __name__ == "__main__":
    unittest.main()
