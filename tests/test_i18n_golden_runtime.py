"""E1 golden (docs/design/i18n.md) for the runtime layer: runtime_ports, runtime_claude, completion and llm.py, on zh-Hant.

The texts these modules hand to the owner (Unsupported reasons, run/login errors, lease summaries and notes, credential
descriptions, Refused reasons, config errors, llm.py's CLI output) are pinned here; the paths agenthub doctor / llm and
config-check already pin are in their own goldens. Everything runs in-process with the subprocess calls, HOME and the
hub config replaced, so nothing calls a model.

    bin/py -m unittest tests.test_i18n_golden_runtime
    I18N_GOLDEN_UPDATE=1 bin/py -m unittest tests.test_i18n_golden_runtime     # regenerate (only before a conversion)
"""
import contextlib
import datetime
import gc
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
import warnings
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import completion  # noqa: E402
import hubconf  # noqa: E402
import llm  # noqa: E402
import runtime_claude  # noqa: E402
import runtime_fake  # noqa: E402
import runtime_ports  # noqa: E402
from runtime_ports import CompletionResult, Task, TaskResult  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_lang  # noqa: E402

GOLDEN = golden_lang.GOLDEN
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
ZH = {"AGENT_HUB_LANG_UI": golden_lang.LANG, "COLUMNS": "100"}


class RuntimeGolden(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        (self.tmp / "home").mkdir()
        (self.tmp / "secrets").mkdir()
        self.out: list[str] = []
        p = mock.patch.dict(os.environ, {**ZH, "HOME": str(self.tmp / "home")})
        p.start()
        self.addCleanup(p.stop)
        runtime_fake.reset()
        self.addCleanup(runtime_fake.reset)

    def say(self, label: str, value) -> None:
        self.out.append(f"=== {label}")
        self.out.append(value if isinstance(value, str) else repr(value))

    def check(self, name: str) -> None:
        text = "\n".join(self.out).replace(str(self.tmp), "<TMP>").replace(str(ROOT), "<HUB>")
        text = text.replace(runtime_claude.slug_for_path(str(self.tmp)), "<TMP-SLUG>") + "\n"
        f = GOLDEN / f"{name}.txt"
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"{f} did not exist; written, check it and rerun")
        self.assertEqual(text, f.read_text())

    # ---------- runtime_ports ----------

    def test_runtime_ports(self):
        self.say("port labels", sorted(runtime_ports.PORT_LABELS.items()))
        for feat, ok, miss in runtime_ports.feature_table("none"):
            self.say("feature (none)", f"{feat} | {ok} | {miss}")
        try:
            runtime_ports.load("nosuch")
        except KeyError as e:
            self.say("unknown runtime", e.args[0])
        for ports in (tuple(runtime_ports.PORTS), ("completion",)):
            try:
                runtime_ports.load("none", ports=ports)
            except runtime_ports.Unsupported as e:
                self.say(f"unsupported {ports}", str(e))
        half = types.ModuleType("golden_half_runtime")
        half.run = lambda task: None
        half.complete = lambda req, conf: None
        with mock.patch.dict(sys.modules, {"golden_half_runtime": half}), \
                mock.patch.dict(runtime_ports.RUNTIMES, {"half": "golden_half_runtime"}), \
                mock.patch.dict(runtime_ports.PROVIDERS, {"halfp": "golden_half_runtime"}):
            try:
                runtime_ports.load("half", ports=("agent",))
            except runtime_ports.MissingFunctions as e:
                self.say("missing functions (runtime)", str(e))
            try:
                runtime_ports.load_completion("halfp")
            except runtime_ports.MissingFunctions as e:
                self.say("missing functions (provider)", str(e))
        try:
            runtime_ports.load_completion("nosuch")
        except KeyError as e:
            self.say("unknown completion impl", e.args[0])
        self.check("runtime_ports")

    # ---------- runtime_claude ----------

    def test_runtime_claude(self):
        rc = runtime_claude
        ws = self.tmp / "ws"
        ws.mkdir()
        self.say("sandbox: no settings", rc.sandbox_rendered(ws))
        (ws / ".claude").mkdir()
        (ws / ".claude" / "settings.local.json").write_text(json.dumps({"sandbox": {"enabled": False}}))
        self.say("sandbox: not enabled", rc.sandbox_rendered(ws))

        root = self.tmp / "transcripts"
        d = root / rc.slug_for_path(str(ws))
        d.mkdir(parents=True)
        (d / "s1.jsonl").write_text("".join(json.dumps({"type": "weird", "n": i}) + "\n" for i in range(5)))
        with mock.patch.object(rc, "transcript_roots", lambda: (root,)):
            try:
                list(rc.turns([str(ws)], datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc)))
            except rc.TranscriptFormatError as e:
                self.say("transcript format", str(e))

        task = Task(point="p", prompt="hi", spec={}, timeout_s=1200, env={"PATH": "/usr/bin"})
        cases = (("timeout", subprocess.TimeoutExpired("claude", 1)), ("not found", FileNotFoundError()),
                 ("exit", subprocess.CompletedProcess([], 2, "", "bad things")),
                 ("not json", subprocess.CompletedProcess([], 0, "plain text", "")))
        not_found = None
        for label, res in cases:
            with mock.patch.object(rc.subprocess, "run", side_effect=res if isinstance(res, Exception) else None,
                                   return_value=None if isinstance(res, Exception) else res), \
                    contextlib.redirect_stderr(io.StringIO()):
                r = rc.run(task)
            self.say(f"run: {label}", (r.ok, r.error))
            not_found = r.error if label == "not found" else not_found
        self.say("is_login_error(run: not found)", rc.is_login_error(not_found))
        self.say("is_login_error(timeout)", rc.is_login_error("timed out after 120s"))

        for tool, inp in (("Read", {"file_path": "a.py"}), ("Grep", {"pattern": "x"}), ("WebFetch", {"url": "https://e.x"}),
                          ("Edit", {"file_path": "b.py"}), ("Bash", {"command": "ls"}), ("Mystery", {"path": "p"}),
                          ("AskUserQuestion", {"questions": [{"question": "Q1?"}, {"question": "Q2?"}]}),
                          (rc.SHOW_FILE_TOOL, {"path": "r/a.png"})):
            self.say(f"action {tool}", rc.action(tool, inp))
        rate = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning", "unifiedWindows": {
            "five_hour": {"utilization": 0.8, "resetsAt": None}, "seven_day": {"utilization": 0.5}}}}
        for line in (rate, {"type": "result", "subtype": "error_max_turns", "result": ""},
                     {"type": "result", "is_error": True, "result": "usage limit reached"}):
            self.say(f"stream {line['type']}", [(e.type, e.data) for e in rc.parse_stream(json.dumps(line))])
        self.say("classify_exit login", rc.classify_exit(1, "Login expired"))
        self.say("classify_exit crash", rc.classify_exit(1, "segfault"))
        self.say("resume_command", rc.resume_command("0000"))

        self.say("auth_env token missing", rc.auth_env("token", {}))
        creds = self.tmp / "home" / ".claude"
        creds.mkdir(exist_ok=True)
        (creds / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "x"}}))
        for c in rc.credentials():
            self.say(f"credential {c.mode}", (c.name, c.label, c.renew, c.lifetime, c.used_by, c.note))

        req = runtime_ports.CompletionRequest(point="p", prompt="hi", model=None, schema=None, max_tokens=10, timeout_s=30)
        for label, exc in (("timeout", subprocess.TimeoutExpired("claude", 30)), ("not found", FileNotFoundError())):
            with mock.patch.object(rc.subprocess, "run", side_effect=exc):
                r = rc.complete(req, {"auth": "subscription"})
            self.say(f"complete: {label}", (r.ok, r.stop, r.error))
        with mock.patch.object(rc, "_exe", lambda: str(self.tmp / "no-claude")):
            self.say("health: not found", rc.health({"auth": "subscription"}))
        with mock.patch.object(rc, "_exe", lambda: sys.executable):
            self.say("health: token missing", rc.health({"auth": "token"}))
        self.check("runtime_claude")

    # ---------- completion ----------

    def test_completion(self):
        c = completion
        with mock.patch.object(c.hubconf, "secrets_dir", lambda: self.tmp / "secrets"):
            for label, conf in (("missing secret", {"key": {"secret": "k"}}), ("missing env", {"key": {"env": "GOLDEN_NO_KEY"}}),
                                ("bad shape", {"key": {"other": 1}})):
                try:
                    c.read_key(conf)
                except c.Refused as e:
                    self.say(f"read_key: {label}", str(e))
            (self.tmp / "secrets" / "empty").write_text("")
            try:
                c.read_key({"key": {"secret": "empty"}})
            except c.Refused as e:
                self.say("read_key: empty", str(e))

            llm_conf = {"providers": {"f": {"impl": "fake", "models": {"fast": {"name": "m1", "context": 1000}}},
                                      "k": {"impl": "anthropic", "key": {"secret": "nokey"}},
                                      "x": {"impl": "nosuch"}},
                        "use": {"fast": "f", "standard": "zz"}}
            for label, s, spec in (("no llm", None, {}), ("no use", {"providers": {"f": {}}}, {"model_group": "strong"}),
                                   ("no provider", llm_conf, {"model_group": "standard"}),
                                   ("no model", {**llm_conf, "use": {"fast": "k"}}, {"model_group": "fast"})):
                with mock.patch.object(c, "settings", lambda s=s: s):
                    try:
                        c.resolve("p", spec)
                    except c.Refused as e:
                        self.say(f"resolve: {label}", str(e))

            schema = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}
            spec = {"model_group": "fast", "max_tokens": 100}
            with mock.patch.object(c, "USAGE", self.tmp / "usage.jsonl"):
                for label, results, prompt, budget in (
                        ("too long", [], "x" * 4000, None),
                        ("truncated", [CompletionResult(False, text="{", stop="length", error="x")], "hi", None),
                        ("error", [CompletionResult(False, stop="error", error="boom")], "hi", None),
                        ("not json twice", [CompletionResult(True, text="not json", stop="end")], "hi", None),
                        ("schema twice", [CompletionResult(True, text='{"nope": 1}', stop="end")], "hi", None),
                        ("budget", [CompletionResult(True, text='{"answer": "a"}', stop="end", cost_usd=9.0)], "hi", 5)):
                    conf = json.loads(json.dumps(llm_conf))
                    if budget:
                        conf["budget"] = {"daily_usd": budget}
                    runtime_fake.set_completions("p", *results) if results else None
                    with mock.patch.object(c, "settings", lambda conf=conf: conf):
                        for _attempt in range(2 if budget else 1):
                            try:
                                c.run("p", spec, prompt, schema, sleep=lambda s: None)
                            except c.Refused as e:
                                self.say(f"run: {label}", str(e))
                    if label == "schema twice":
                        self.say("retry prompt", runtime_fake._asked[-1][0].prompt)
                with mock.patch.object(c, "settings", lambda: llm_conf):
                    self.say("health_all", c.health_all())

        bad = {"providers": {"a": {"impl": "anthropic", "key": {"secret": "sk-ant-api03-xxxxxxxxxxxx"},
                                   "models": {"fast": "m", "huge": "", "standard": {"context": 1}}},
                             "b": {"impl": "none", "key": {"secret": "a/b"}, "location": "moon"},
                             "c": {"impl": "nope", "key": {"secret": "x", "env": "Y"}, "location": "local",
                                   "base_url": "https://api.example.com"},
                             "d": "string"},
               "use": {"fast": "a", "strong": "a", "standard": "zz", "huge": "a"},
               "points": {"not-a-point": {"provider": "zz"}, "consolidate": {}, "reflect-week": "m"}}
        self.say("config_errors", "\n".join(c.config_errors(bad)))
        self.say("config_errors: providers not a dict", c.config_errors({"providers": []}))
        self.say("config_errors: use without providers", c.config_errors({"use": {"fast": "a"}}))
        self.check("completion")

    # ---------- llm.py ----------

    def cli(self, label: str, argv: list[str], cfg: dict) -> None:
        out, err = io.StringIO(), io.StringIO()
        gc.collect()   # another test's unclosed connection must not print its ResourceWarning into this stderr
        with warnings.catch_warnings(), mock.patch.object(sys, "argv", ["llm.py", *argv]), mock.patch.object(hubconf, "_cfg", cfg), \
                mock.patch.object(llm, "job_failed", lambda point, reason: None), \
                mock.patch.object(sys, "stdin", io.StringIO("prompt")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            warnings.simplefilter("ignore", ResourceWarning)
            try:
                code = llm.main()
            except SystemExit as e:
                code = e.code
        self.out.append(f"$ llm.py {' '.join(argv)}    # {label}\n[exit {code}]\n--- stdout\n{out.getvalue()}--- stderr\n"
                        f"{err.getvalue()}")

    def test_llm(self):
        fake, none, claude = ({"runtime": {"default": n}} for n in ("fake", "none", "claude"))
        for argv in (["--help"], ["run", "--help"], ["exec", "--help"]):
            self.cli("help", argv, fake)
        self.cli("unknown point", ["run", "nosuch"], fake)
        self.cli("exec without stream", ["exec", "reflect-week"], fake)
        self.cli("runtime without stream", ["exec", "lease-session"], fake)
        self.cli("no completion", ["run", "reflect-week"], none)
        self.cli("no agent", ["run", "agent-job"], none)
        self.cli("token missing", ["run", "agent-job"], claude)
        runtime_fake.set_reply("consolidate", TaskResult(True, text="", structured_output={"bad": 1}))
        self.cli("schema", ["run", "consolidate"], fake)
        runtime_fake.set_reply("agent-job", TaskResult(False, error="it broke"))
        self.cli("run failed", ["run", "agent-job"], fake)
        with mock.patch.object(completion, "settings", lambda: {"providers": {
                "k": {"impl": "anthropic", "key": {"secret": "nokey"}}, "f": {"impl": "fake"}}}), \
                mock.patch.object(completion.hubconf, "secrets_dir", lambda: self.tmp / "secrets"):
            self.cli("health", ["health"], fake)
        (self.tmp / "bin").mkdir()
        (self.tmp / "bin" / "x.sh").write_text("echo hi\nclaude -p 'q'\n")
        with mock.patch.object(hubconf, "ROOT", self.tmp), mock.patch.object(llm, "points", lambda: {"points": {
                "pt": {"output": {"format": "json", "schema": "llm/nope.schema.json"}}}}):
            self.cli("lint", ["lint"], fake)
        self.check("llm")


if __name__ == "__main__":
    unittest.main()
