"""Claude Code's conformance checks (bin/conformance_claude.py) through the runner's real path: run_one prepares a
throwaway world, starts the check's own process, which proves it is isolated, enters the world and runs the check
against a stand-in `claude` (no model, no network). Each check reaches an Outcome, the ones the stand-in can satisfy pass,
and the stand-in's misbehaviour is told apart (fail vs error). Also prepare() and enter(): no token is a refusal, and
entering keeps the world's pointers."""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import conformance  # noqa: E402
import conformance_claude  # noqa: E402
import runtime_claude  # noqa: E402

STAND_IN = r'''#!PYTHON
import json, os, pathlib, subprocess, sys
a = sys.argv[1:]
mode = os.environ.get("STAND_IN_MODE", "good")
if a[:1] == ["--version"]:
    print("2.1.400 (Claude Code)"); sys.exit(0)
if a[:2] == ["mcp", "list"]:
    f = pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"]) / "servers"
    names = f.read_text().split() if f.exists() else []
    print("Checking MCP server health..." + "".join(f"\n\n{n}: /bin/echo - ✓ Connected" for n in names) if names
          else "No MCP servers configured. Use `claude mcp add` to add a server.")
    sys.exit(0)
if a[:2] == ["mcp", "add"]:
    f = pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"]) / "servers"
    f.write_text((f.read_text() if f.exists() else "") + a[4] + "\n"); sys.exit(0)
prompt = sys.stdin.read()
token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
if not token:   # what DV5 (c) guards: with no token it asks the Keychain, for its own config dir's item
    subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials-1a2b3c4d"], capture_output=True)
bad = token.startswith("sk-ant-oat01-000") or not token
answer = "ok"
rules = pathlib.Path.cwd() / "CLAUDE.local.md"
if "code word" in prompt and rules.exists():
    answer = rules.read_text().split()[-1].rstrip(".")
tools = [] if "--tools" in a and a[a.index("--tools") + 1] == "" else ["Bash", "Read"]
if "--json-schema" in a and not tools:
    tools = ["StructuredOutput"]
if mode == "tools-leak":
    tools = ["Bash"]
sid = "s-1"
result = {"type": "result", "subtype": "success", "is_error": bad, "result": "Invalid API key · Please run /login" if bad
          else answer, "usage": {}, "permission_denials": [], "session_id": sid, "num_turns": 1, "total_cost_usd": 0}
if "--no-session-persistence" not in a and not bad:
    d = pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "-w"
    d.mkdir(parents=True, exist_ok=True)
    line = {"type": "user", "uuid": "u1", "sessionId": sid, "timestamp": "2026-11-14T00:00:00Z", "cwd": str(pathlib.Path.cwd()),
            "version": "2.1.400", "message": {"role": "user", "content": "go"}}
    (d / f"{sid}.jsonl").write_text(json.dumps(line) + "\n")
if "stream-json" in a:
    print(json.dumps({"type": "system", "subtype": "init", "tools": tools, "session_id": sid}))
    print(json.dumps(result))
else:
    print(json.dumps(result))
sys.exit(1 if bad else 0)
'''


class Through(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = pathlib.Path(d.name).resolve()
        self.exe = self.tmp / "claude"
        self.exe.write_text(STAND_IN.replace("PYTHON", sys.executable, 1))
        self.exe.chmod(0o755)
        token = self.tmp / "headless-oauth-token"
        token.write_text("sk-ant-oat01-good\n")
        p = mock.patch.object(runtime_claude, "_token_file", lambda: token)
        p.start()
        self.addCleanup(p.stop)
        self.top = self.tmp / "top"
        self.top.mkdir()

    def one(self, name):
        check = {c.name: c for c in conformance_claude.CHECKS}[name]
        return conformance.run_one("claude", check, str(self.exe), self.top)

    def test_the_checks_a_stand_in_can_satisfy_pass(self):
        for name in ("agent-result", "completion-no-tools", "instructions-reach", "sessions-read", "stream-exchange",
                     "tool-servers-listed", "auth-login-error"):
            with self.subTest(check=name):
                got = self.one(name)
                self.assertEqual(got.state, "pass", got)
        self.assertEqual(self.one("distribution-integrity").state, "not-checkable", "no lockfile beside it")
        if sys.platform == "darwin":
            self.assertEqual(self.one("auth-keychain").state, "pass")

    def test_each_reaches_an_outcome_and_the_world_is_gone(self):
        for c in conformance_claude.CHECKS:
            if c.run is None or c.name in ("confine-folder-git", "lifecycle-events", "completion-schemas", "agent-models"):
                continue
            with self.subTest(check=c.name):
                self.assertIn(self.one(c.name).state, conformance.runtime_ports.CHECK_STATES)
        self.assertEqual(list(self.top.iterdir()), [], "every check's world removed, the token copy with it")

    def test_a_tool_that_leaks_into_a_completion_fails_it(self):
        with mock.patch.dict(os.environ, {"STAND_IN_MODE": "tools-leak"}):
            check = {c.name: c for c in conformance_claude.CHECKS}["completion-no-tools"]
            world = conformance.World(self.tmp / "w").create()
            env = {**world.env(), "STAND_IN_MODE": "tools-leak"}
        import subprocess
        conformance_claude.prepare(world)
        r = subprocess.run([sys.executable, str(BIN / "conformance.py"), "child", "claude", check.name, str(world.root),
                            str(self.exe)], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["state"], "fail")

    def test_no_token_is_a_refusal_not_a_fail(self):
        with mock.patch.object(runtime_claude, "_token_file", lambda: self.tmp / "none"), self.assertRaises(RuntimeError):
            self.one("agent-result")


class Enter(unittest.TestCase):
    def test_enter_keeps_the_world_and_adds_the_tools(self):
        with tempfile.TemporaryDirectory() as d:
            world = conformance.World(pathlib.Path(d)).create()
            (world.home / ".claude").mkdir()
            (world.home / ".claude" / "headless-oauth-token").write_text("t\n")
            with mock.patch.dict(os.environ, world.env(), clear=True):
                conformance_claude.enter(world)
                import names   # what a child's hubconf would read: the world's pointers survive
                for var, part in (("CONFIG", world.config), ("STATE", world.state), ("DATA", world.data),
                                  ("SECRETS", world.secrets)):
                    self.assertEqual(names.env(var), str(part), var)
                self.assertEqual(os.environ["HOME"], str(world.home))
                self.assertEqual(os.environ["CLAUDE_CONFIG_DIR"], str(world.tool))
                self.assertEqual(os.environ["DISABLE_AUTOUPDATER"], "1")
                self.assertTrue(os.environ["npm_config_prefix"].startswith(str(world.root)))
                self.assertTrue(os.environ["PATH"].startswith(str(world.root / "bin")))
                self.assertEqual(os.environ[runtime_claude.TOKEN_VAR], "t")
            (world.home / ".claude" / "headless-oauth-token").unlink()
            with mock.patch.dict(os.environ, world.env(), clear=True), self.assertRaises(RuntimeError):
                conformance_claude.enter(world)


if __name__ == "__main__":
    unittest.main()
