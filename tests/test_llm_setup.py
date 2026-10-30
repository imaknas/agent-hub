"""agenthub llm setup(init 也叫它):選一家 provider、key 寫進 secrets(600、不印)、模型從那家實際有的清單選,寫 hub.json llm。

隔離的 config/secrets;provider 是本機的假 API(OpenAI 相容、Ollama),不送任何東西出去。

    bin/py -m unittest discover -s tests
"""
import http.server
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
KEY = "test-key-not-real"


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/api/tags":
            return self.reply(200, {"models": [{"name": "local-b"}, {"name": "local-a"}]})
        if self.path.startswith("/v1/models"):
            if self.headers.get("Authorization") != f"Bearer {KEY}":
                return self.reply(401, {"error": "no key"})
            return self.reply(200, {"data": [{"id": "m-big"}, {"id": "m-small"}]})
        self.reply(404, {})


class Setup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        for d in ("home", "config/projects", "data", "secrets"):
            (self.tmp / d).mkdir(parents=True)
        self.cfg = {"owner": {"name": "alex", "tag": "Alex"}, "registry_dir": "projects", "runtime": {"default": "none"},
                    "llm": {"points": {"consolidate": {"model": "pinned"}}}}
        (self.tmp / "config" / "hub.json").write_text(json.dumps(self.cfg))
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith("AGENT_HUB_") or k == "AGENT_HUB_PYTHON"},
                    "HOME": str(self.tmp / "home"), "AGENT_HUB_CONFIG": str(self.tmp / "config"),
                    "AGENT_HUB_DATA": str(self.tmp / "data"), "AGENT_HUB_SECRETS": str(self.tmp / "secrets"),
                    "AGENT_HUB_STATE": str(self.tmp / "state"), "AGENT_HUB_LANG_UI": "zh-Hant"}

    def hub(self, *args, stdin=""):
        return subprocess.run([sys.executable, str(BIN / "agenthub"), *args], input=stdin, env=self.env, cwd=self.tmp,
                              capture_output=True, text=True, timeout=120)

    def hub_json(self):
        return json.loads((self.tmp / "config" / "hub.json").read_text())

    def test_openai_compatible_then_key(self):
        r = self.hub("llm", "setup", "--llm", "openai", "--llm-base-url", f"{self.base}/v1", "--llm-model", "m-small", "--yes")
        self.assertEqual(r.returncode, 1, "key 還沒設:寫進設定,但說還不能用")
        self.assertIn("還不能用", r.stdout)
        llm = self.hub_json()["llm"]
        self.assertEqual(llm["providers"]["openai"], {"impl": "openai", "base_url": f"{self.base}/v1",
                                                      "key": {"secret": "openai-api-key"},
                                                      "models": {"fast": "m-small", "standard": "m-small", "strong": "m-small"}})
        self.assertEqual(llm["use"], {"fast": "openai", "standard": "openai", "strong": "openai"})
        self.assertEqual(llm["points"], {"consolidate": {"model": "pinned"}}, "個別判斷點的指定不被蓋掉")
        r = self.hub("llm", "set-key", "openai", stdin=KEY + "\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        f = self.tmp / "secrets" / "openai-api-key"
        self.assertEqual((f.read_text().strip(), oct(f.stat().st_mode & 0o777)), (KEY, "0o600"))
        self.assertNotIn(KEY, r.stdout + r.stderr, "不印 key")
        self.assertNotIn(KEY, (self.tmp / "config" / "hub.json").read_text(), "hub.json 裡沒有值")
        self.assertEqual(self.hub("llm", "setup", "--llm", "openai", "--llm-base-url", f"{self.base}/v1",
                                  "--llm-model", "m-small", "--yes").returncode, 0, "有 key 就連得上")

    def test_ollama_is_local(self):
        r = self.hub("llm", "setup", "--llm", "ollama", "--llm-base-url", self.base, "--llm-model", "local-a", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        p = self.hub_json()["llm"]["providers"]["ollama"]
        self.assertEqual((p["location"], p["base_url"], "key" in p), ("local", self.base, False))
        import completion
        self.assertEqual(completion.config_errors(self.hub_json()["llm"]), [], "寫出來的設定過得了 config check")

    def test_runtime_choice_drops_providers_keeps_pins(self):
        self.hub("llm", "setup", "--llm", "ollama", "--llm-base-url", self.base, "--llm-model", "local-a", "--yes")
        r = self.hub("llm", "setup", "--llm", "runtime", "--yes")
        self.assertEqual(r.returncode, 0)
        self.assertIn("沒有 completion 介面", r.stdout, "runtime none 選 runtime:明說判斷點會失敗")
        self.assertEqual(self.hub_json()["llm"], {"points": {"consolidate": {"model": "pinned"}}})

    def test_non_interactive_needs_a_model(self):
        before = (self.tmp / "config" / "hub.json").read_text()
        r = self.hub("llm", "setup", "--llm", "ollama", "--llm-base-url", self.base, "--yes")
        self.assertEqual(r.returncode, 1)
        self.assertEqual((self.tmp / "config" / "hub.json").read_text(), before, "沒選模型就不動 hub.json")

    def test_list_models_from_each_provider(self):
        import provider_anthropic
        import provider_ollama
        import provider_openai
        self.assertEqual(provider_ollama.list_models({"base_url": self.base}), ["local-a", "local-b"])
        self.assertEqual(provider_openai.list_models({"base_url": f"{self.base}/v1", "_key": KEY}), ["m-big", "m-small"])
        self.assertEqual(provider_openai.list_models({"base_url": f"{self.base}/v1", "_key": "wrong"}), [], "key 錯 = 空的")
        self.assertEqual(provider_anthropic.list_models({"base_url": "http://127.0.0.1:9", "_key": KEY}), [], "連不上 = 空的")


if __name__ == "__main__":
    unittest.main()
