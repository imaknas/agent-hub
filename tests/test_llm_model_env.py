"""判斷點的模型設定(model_env)要真的生效(10-27 第四次盲審查到)。

contracts/llm-points.yaml 的點宣告 `model_env: STATE_MODEL` 之類,llm.py 照環境變數選模型。但設定寫在 config 的
models.env,librarian.sh source 了卻沒 export(librarian.py 也只拿來寫 log),所以 `schedulectl set-model` 改了沒有用、
一直是 model_default。修法放在 llm.py:環境變數沒有,就讀 config 的 models.env——所有呼叫端一起生效。
優先順序:--model > 環境變數 > models.env > model_default。

    bin/py -m unittest discover -s tests
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


def load_llm():
    spec = importlib.util.spec_from_file_location("llm_me", ROOT / "bin" / "llm.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class ModelEnv(unittest.TestCase):
    def setUp(self):
        self.cfg = pathlib.Path(tempfile.mkdtemp())
        self.prompt = self.cfg / "p.txt"
        self.prompt.write_text("x")
        # 登入:假的 HOME 放一個假的 token(不靠這台機器的真憑證;閘門也是假 HOME)
        self.home = pathlib.Path(tempfile.mkdtemp())
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "headless-oauth-token").write_text("not-a-real-token\n")

    def model_for(self, point, env=None, models_env=None, cli=None):
        if models_env is not None:
            (self.cfg / "models.env").write_text(models_env)
        clean = {k: v for k, v in os.environ.items() if k not in ("STATE_MODEL", "INDEX_MODEL", "CONSOLIDATE_MODEL")}
        with mock.patch.dict(os.environ, {**clean, "AGENT_HUB_CONFIG": str(self.cfg), "HOME": str(self.home), **(env or {})}, clear=True):
            llm = load_llm()
            a = argparse.Namespace(cmd="run", point=point, cwd=None, model=cli, prompt_file=str(self.prompt),
                                   dry_run=True, extra=[])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                llm.run(a)
        cmd = json.loads(out.getvalue())
        return cmd[cmd.index("--model") + 1]

    def test_models_env_is_used_when_the_variable_is_not_exported(self):
        self.assertEqual(self.model_for("librarian-state", models_env="STATE_MODEL=opus\nINDEX_MODEL=haiku\n"), "opus")
        self.assertEqual(self.model_for("librarian-index", models_env="STATE_MODEL=opus\nINDEX_MODEL=haiku\n"), "haiku")

    def test_precedence(self):
        menv = "STATE_MODEL=opus\n"
        self.assertEqual(self.model_for("librarian-state", env={"STATE_MODEL": "sonnet"}, models_env=menv), "sonnet")
        self.assertEqual(self.model_for("librarian-state", env={"STATE_MODEL": "sonnet"}, models_env=menv, cli="haiku"),
                         "haiku")

    def test_default_without_any_setting(self):
        self.assertEqual(self.model_for("librarian-state"), "haiku")

    def test_comments_quotes_and_unrelated_lines(self):
        menv = '# 註解\nexport STATE_MODEL="opus"   # 行尾註解\nFOO=bar\n'
        self.assertEqual(self.model_for("librarian-state", models_env=menv), "opus")


if __name__ == "__main__":
    unittest.main()
