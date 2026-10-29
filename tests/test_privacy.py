"""隱私路由(bin/privacy.py,擋在 llm.py):privacy.memory: local_only 的專案,內容只准本機模型;有本機的 provider 就自動改用,
沒有就不跑、說原因,不退回雲端。跨專案的文字送雲端前拿掉那些專案(cloud_view)。

隔離的 config;本機模型是假的 Ollama(127.0.0.1),不送任何東西出去。librarian 那一段在 test_librarian_flow。

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

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))


class FakeOllama(http.server.BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.seen.append(body)
        out = {"edits": [], "state_updated": False, "worth_digesting": False, "summary": "", "reason": "local"}
        raw = json.dumps({"message": {"content": json.dumps(out)}, "done_reason": "stop",
                          "prompt_eval_count": 1, "eval_count": 1}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class CloudView(unittest.TestCase):
    def test_drops_sections_and_mentions(self):
        import privacy
        text = ("# Digest\n<!-- digest:projects:begin -->\n## money\n\n帳目整理\n\n## site\n\n改版;跟 money 有關\n"
                "也提到 money-tools 不算\n<!-- digest:projects:end -->\n- 〔Alex〕money:報稅\n")
        out = privacy.cloud_view(text, {"money"})
        self.assertNotIn("帳目", out)
        self.assertNotIn("報稅", out)
        self.assertNotIn("跟 money 有關", out)
        self.assertIn("## site", out)
        self.assertIn("money-tools", out, "名稱是別的專案名稱的一部分不算提到")
        self.assertIn("<!-- digest:projects:end -->", out)
        self.assertEqual(privacy.cloud_view(text, set()), text)


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.local = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        t = self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        for d in ("home", "config/projects", "data", "secrets", "ws/secret", "ws/open"):
            (t / d).mkdir(parents=True)
        for name, mem in (("secret", "local_only"), ("open", None)):
            doc = {"v": 1, "name": name, "paths": {"workspace": str(t / "ws" / name)},
                   **({"privacy": {"memory": mem}} if mem else {})}
            (t / "config" / "projects" / f"{name}.yaml").write_text(yaml.safe_dump(doc))
        self.hub({"runtime": {"default": "claude"}})
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("AGENT_HUB_", "HUB_"))
                       or k == "AGENT_HUB_PYTHON"},
                    "HOME": str(t / "home"), "AGENT_HUB_CONFIG": str(t / "config"), "AGENT_HUB_DATA": str(t / "data"),
                    "AGENT_HUB_SECRETS": str(t / "secrets"), "AGENT_HUB_STATE": str(t / "state")}
        (t / "home" / ".claude").mkdir()
        (t / "home" / ".claude" / "headless-oauth-token").write_text("not-a-real-token\n")
        FakeOllama.seen = []

    def hub(self, extra):
        (self.tmp / "config" / "hub.json").write_text(json.dumps({"owner": {"name": "a", "tag": "A"},
                                                                  "registry_dir": "projects", **extra}))

    def llm(self, *args, stdin="x"):
        r = subprocess.run([sys.executable, str(BIN / "llm.py"), "run", *args], input=stdin, env=self.env, cwd=self.tmp,
                           capture_output=True, text=True, timeout=120)
        return r, (json.loads(r.stdout) if r.stdout.strip().startswith("{") else None)

    def test_local_only_project_is_refused_on_a_cloud_runtime(self):
        r, out = self.llm("librarian-state", "--project", "secret", "--dry-run")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("隱私", out["llm_error"])
        self.assertIn("secret", out["llm_error"])

    def test_project_is_inferred_from_the_workdir(self):
        r, out = self.llm("agent-job", "--cwd", str(self.tmp / "ws" / "secret"), "--dry-run")
        self.assertIn("隱私", (out or {}).get("llm_error", ""), "沒給 --project 也認得出來")
        r, _ = self.llm("agent-job", "--cwd", str(self.tmp / "ws" / "open"), "--dry-run")
        self.assertEqual(r.returncode, 0, "cloud_ok 的專案照舊")

    def test_cloud_safe_points_are_not_blocked(self):
        r, _ = self.llm("login-check", "--project", "secret", "--dry-run")
        self.assertEqual(r.returncode, 0)

    def test_completion_goes_to_the_local_provider(self):
        self.hub({"runtime": {"default": "claude"}, "llm": {
            "providers": {"cloud": {"impl": "openai", "base_url": "http://127.0.0.1:9/v1", "key": {"env": "NOPE_KEY"},
                                    "models": {"fast": "c1", "standard": "c1", "strong": "c1"}},
                          "home": {"impl": "ollama", "base_url": self.local, "location": "local",
                                   "models": {"fast": "l1"}}},
            "use": {"fast": "cloud", "standard": "cloud", "strong": "cloud"}}})
        r, out = self.llm("librarian-state-edits", "--project", "secret")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((out["provider"], out["llm_model"]), ("home", "l1"))
        self.assertEqual(len(FakeOllama.seen), 1)
        r, out = self.llm("librarian-index-edits", "--project", "secret")
        self.assertIn("沒有 location: local 的 provider 有 standard", out["llm_error"], "本機沒有那一組:不跑,不退回雲端")
        self.assertEqual(len(FakeOllama.seen), 1)


class Callers(unittest.TestCase):
    """每個叫模型的地方都說出內容屬於哪個專案(工作目錄推不出來的一定要明說)。"""

    def test_callers_name_the_project(self):
        want = {"librarian.py": '"--project", project', "consolidate-memory.py": '"--project", project',
                "propose_apply.py": '"--project", project', "hubbus": '"--project", canon(r["to_project"])',
                "lease-runner.py": '"--project", lease.project'}
        for f, needle in want.items():
            self.assertIn(needle, (BIN / f).read_text(), f)
        self.assertIn('"HUB_PROJECT": proj', (BIN / "hubbus").read_text(), "交辦經 run-agent-job.sh 也帶專案")


if __name__ == "__main__":
    unittest.main()
