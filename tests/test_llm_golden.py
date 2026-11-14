"""Golden file of how llm.py "will run" each judgment point: the command (what --dry-run prints) and the login (which
credentials, whether a token is passed).

When refactoring llm.py / runtime_claude / login (layer 6 AuthProvider), the behaviour seen from outside must not
change: it used to be compared by hand across 20 combinations, now it is pinned in tests/fixtures/llm-golden.json.
Regenerate only when the judgment point declarations (contracts/llm-points.yaml) really changed:

    LLM_GOLDEN_UPDATE=1 bin/py -m unittest tests.test_llm_golden

Prints and stores no token value (a fake token file; only records "passed or not"). Machine-specific paths become
<HUB>, <HOME>, claude. The run's environment is recorded by name only (which of the variables that pin the platform's
checked copy and choose its config dir it gets, and the head of its PATH inside a stand-in store, <STORE>), never a
value (docs/design/runtime-versions.md step 2, item 5).
"""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import store_fixture  # noqa: E402
GOLDEN = ROOT / "tests" / "fixtures" / "llm-golden.json"
TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
# what a run without the sealing flag would load from the environment (fixture names)
AMBIENT = ["claude_ai_Example", "fixture-prod-db", "plugin_example_tools"]
# the variables whose presence the golden records (never their values)
ENV_NAMES = ("CLAUDE_CONFIG_DIR", "DISABLE_AUTOUPDATER", "npm_config_prefix", "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS")

try:
    import yaml  # noqa: F401  llm.py needs it
except ImportError:
    yaml = None


def load_llm():
    spec = importlib.util.spec_from_file_location("llm_t", ROOT / "bin" / "llm.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@unittest.skipIf(yaml is None, "needs PyYAML")
class Golden(unittest.TestCase):
    VARIANTS = {"default": {}, "model": {"model": "golden-model"}, "subscription": {"LLM_AUTH": "subscription"},
                "token": {"LLM_AUTH": "token"}}

    def setUp(self):
        # credentials go in a fake HOME (no patching of module variables: wherever login moves, this test needs
        # no change)
        tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".claude").mkdir()
        self.token_file = tmp / ".claude" / "headless-oauth-token"
        self.token_file.write_text("not-a-real-token\n")
        home = mock.patch.dict(os.environ, {"HOME": str(tmp)})
        home.start()
        self.addCleanup(home.stop)
        # the platform's checked copy: a stand-in store under the test's own state
        self.exe = store_fixture.stand_in(self)
        import hubconf
        self.store = hubconf.runtimes_dir()
        self.m = load_llm()
        self.prompt = tmp / "prompt.txt"
        self.prompt.write_text("golden prompt")

    def norm(self, cmd: list) -> list:
        import hubconf
        cmd = ["claude"] + cmd[1:] if cmd and pathlib.Path(cmd[0]).name == "claude" else cmd
        s = json.dumps(cmd, ensure_ascii=False)
        for real, tag in ((str(hubconf.ROOT), "<HUB>"), (str(pathlib.Path.home()), "<HOME>")):
            s = s.replace(real, tag).replace(json.dumps(real)[1:-1], tag)
        return json.loads(s)

    def run_env(self, task) -> dict:
        """The names the run's environment gets (ENV_NAMES present) and the head of its PATH: the entries inside the store,
        as <STORE>/…, up to the first one outside it."""
        import runtime_claude
        env = runtime_claude._env(task)
        head = []
        for p in env.get("PATH", "").split(":"):
            if not p.startswith(str(self.store) + "/"):
                break
            head.append("<STORE>/" + p[len(str(self.store)) + 1:])
        return {"names": [n for n in ENV_NAMES if n in env], "path_head": head,
                "config_dir": "platform" if env.get("CLAUDE_CONFIG_DIR", "").startswith(str(self.store) + "/") else
                "shared" if "CLAUDE_CONFIG_DIR" not in env else "other"}

    def observe(self, point: str, variant: dict) -> dict:
        spec = self.m.points()["points"][point]
        env = {k: v for k, v in os.environ.items() if k not in ("LLM_AUTH", TOKEN_ENV)}
        if "LLM_AUTH" in variant:
            env["LLM_AUTH"] = variant["LLM_AUTH"]
        # the environment's MCP servers (points declaring tool_servers): a fixed answer, never this machine's
        import runtime_claude
        ambient = mock.patch.object(runtime_claude, "ambient_servers",
                                    lambda task: None if runtime_claude._sealed(task) else list(AMBIENT))
        tasks, describe = [], runtime_claude.describe
        seen = mock.patch.object(runtime_claude, "describe", lambda task: (tasks.append(task), describe(task))[1])
        with mock.patch.dict(os.environ, env, clear=True), ambient, seen:
            # login is observed at the auth interface: which kind (auth), whether an extra credential variable is passed
            # (token_passed); variable names are not checked
            import runtime_ports
            rt = runtime_ports.load(spec.get("runtime"), ports=("auth",))
            auth = self.m.auth_mode(spec)
            agent_env, _ = self.m.auth_env(spec, rt)
            a = argparse.Namespace(cmd="run", point=point, cwd=None, model=variant.get("model"),
                                   prompt_file=str(self.prompt), dry_run=True, extra=[])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = self.m.run(a)
            run_env = self.run_env(tasks[-1]) if code == 0 and tasks else None
        return {"exit": code, "auth": auth, "token_passed": bool(agent_env) and agent_env != rt.clean_env(agent_env),
                "cmd": self.norm(json.loads(out.getvalue())) if code == 0 else None, "env": run_env}

    def current(self) -> dict:
        return {f"{p}/{v}": self.observe(p, var) for p in sorted(self.m.points()["points"])
                for v, var in self.VARIANTS.items()}

    def test_matches_golden(self):
        now = self.current()
        if os.environ.get("LLM_GOLDEN_UPDATE") == "1":
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(json.dumps(now, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        want = json.loads(GOLDEN.read_text())
        self.assertEqual(sorted(now), sorted(want),
                         "judgment points do not match the golden file (regenerate after adding/removing a point)")
        for key in sorted(want):
            with self.subTest(key):
                self.assertEqual(now[key], want[key])

    def test_golden_never_contains_the_token(self):
        self.assertNotIn("not-a-real-token", json.dumps(self.current()))
        self.assertNotIn("not-a-real-token", GOLDEN.read_text())

    def test_missing_token_file_fails_instead_of_falling_back(self):
        self.token_file.unlink()
        spec = self.m.points()["points"]["login-check"]
        with mock.patch.dict(os.environ, {"LLM_AUTH": "token"}):
            import runtime_ports
            env, why = self.m.auth_env(spec, runtime_ports.load("claude", ports=("auth",)))
        self.assertIsNone(env)
        self.assertIn(str(self.token_file), why)


if __name__ == "__main__":
    unittest.main()
