"""llm.py 對每個判斷點「會怎麼跑」的黃金檔:指令(--dry-run 印的)跟登入方式(哪種憑證、有沒有帶 token)。

重構 llm.py / runtime_claude / 登入(第 6 層 AuthProvider)時,外面看到的行為不能變——以前是手動比 20 種組合,
現在固定在 tests/fixtures/llm-golden.json。判斷點的宣告(contracts/llm-points.yaml)真的改了才重生:

    LLM_GOLDEN_UPDATE=1 python3 -m unittest tests.test_llm_golden

不印、不存任何 token 的值(假的 token 檔,只記「有沒有帶」)。機器相關的路徑換成 <HUB>、<HOME>、claude。
"""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
GOLDEN = ROOT / "tests" / "fixtures" / "llm-golden.json"
TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"

try:
    import yaml  # noqa: F401  llm.py 需要
except ImportError:
    yaml = None


def load_llm():
    spec = importlib.util.spec_from_file_location("llm_t", ROOT / "bin" / "llm.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@unittest.skipIf(yaml is None, "需要 PyYAML")
class Golden(unittest.TestCase):
    VARIANTS = {"default": {}, "model": {"model": "golden-model"}, "subscription": {"LLM_AUTH": "subscription"},
                "token": {"LLM_AUTH": "token"}}

    def setUp(self):
        self.m = load_llm()
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.m.TOKEN_FILE = tmp / "token"
        self.m.TOKEN_FILE.write_text("not-a-real-token\n")
        self.prompt = tmp / "prompt.txt"
        self.prompt.write_text("golden prompt")

    def norm(self, cmd: list) -> list:
        import hubconf
        cmd = ["claude"] + cmd[1:] if cmd and pathlib.Path(cmd[0]).name == "claude" else cmd
        s = json.dumps(cmd, ensure_ascii=False)
        for real, tag in ((str(hubconf.ROOT), "<HUB>"), (str(pathlib.Path.home()), "<HOME>")):
            s = s.replace(real, tag).replace(json.dumps(real)[1:-1], tag)
        return json.loads(s)

    def observe(self, point: str, variant: dict) -> dict:
        spec = self.m.points()["points"][point]
        env = {k: v for k, v in os.environ.items() if k not in ("LLM_AUTH", TOKEN_ENV, spec.get("model_env") or "")}
        if "LLM_AUTH" in variant:
            env["LLM_AUTH"] = variant["LLM_AUTH"]
        with mock.patch.dict(os.environ, env, clear=True):
            auth_env, auth = self.m.auth_env(spec)
            a = argparse.Namespace(cmd="run", point=point, cwd=None, model=variant.get("model"),
                                   prompt_file=str(self.prompt), dry_run=True, extra=[])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = self.m.run(a)
        return {"exit": code, "auth": auth, "token_passed": bool(auth_env and auth_env.get(TOKEN_ENV)),
                "cmd": self.norm(json.loads(out.getvalue())) if code == 0 else None}

    def current(self) -> dict:
        return {f"{p}/{v}": self.observe(p, var) for p in sorted(self.m.points()["points"])
                for v, var in self.VARIANTS.items()}

    def test_matches_golden(self):
        now = self.current()
        if os.environ.get("LLM_GOLDEN_UPDATE") == "1":
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(json.dumps(now, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        want = json.loads(GOLDEN.read_text())
        self.assertEqual(sorted(now), sorted(want), "判斷點跟黃金檔對不上(加了/刪了判斷點就重生)")
        for key in sorted(want):
            with self.subTest(key):
                self.assertEqual(now[key], want[key])

    def test_golden_never_contains_the_token(self):
        self.assertNotIn("not-a-real-token", json.dumps(self.current()))
        self.assertNotIn("not-a-real-token", GOLDEN.read_text())

    def test_missing_token_file_fails_instead_of_falling_back(self):
        self.m.TOKEN_FILE = self.m.TOKEN_FILE.parent / "missing"
        spec = self.m.points()["points"]["login-check"]
        with mock.patch.dict(os.environ, {"LLM_AUTH": "token"}):
            env, auth = self.m.auth_env(spec)
        self.assertEqual((env, auth), (None, "token"))


if __name__ == "__main__":
    unittest.main()
