"""判斷點用哪個模型:全平台只在 llm.model_for 算(10-29 起;以前五個地方各留一份預設表,10-27 盲審查到互相不同步)。

優先順序:--model > hub.json llm.points.<點>.model(沒指定 provider、或 provider 就是這個執行環境)> 執行環境的組別表
(runtime_<名稱>.MODELS)> 不指定(model_group: default 的點,用工具自己的預設)。models.env 不再有人讀。

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


class ModelFor(unittest.TestCase):
    def setUp(self):
        self.cfg = pathlib.Path(tempfile.mkdtemp())
        self.prompt = self.cfg / "p.txt"
        self.prompt.write_text("x")
        # 登入:假的 HOME 放一個假的 token(不靠這台機器的真憑證;閘門也是假 HOME)
        self.home = pathlib.Path(tempfile.mkdtemp())
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "headless-oauth-token").write_text("not-a-real-token\n")

    def model_for(self, point, hub=None, cli=None):
        (self.cfg / "hub.json").write_text(json.dumps({"runtime": {"default": "claude"}, **(hub or {})}))
        with mock.patch.dict(os.environ, {"AGENT_HUB_CONFIG": str(self.cfg), "HOME": str(self.home)}):
            import hubconf
            hubconf._cfg = None
            try:
                llm = load_llm()
                a = argparse.Namespace(cmd="run", point=point, cwd=None, model=cli, prompt_file=str(self.prompt),
                                       dry_run=True, extra=[])
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    llm.run(a)
                direct = llm.model_for(point, cli=cli)
            finally:
                hubconf._cfg = None
        cmd = json.loads(out.getvalue())
        used = cmd[cmd.index("--model") + 1] if "--model" in cmd else None
        self.assertEqual(used, direct, "實際的指令跟 model_for 回報的(寫進 log 的)要一樣")
        return used

    def test_group_defaults_from_the_runtime(self):
        self.assertEqual(self.model_for("librarian-state"), "haiku")
        self.assertEqual(self.model_for("librarian-index"), "sonnet")
        self.assertEqual(self.model_for("consolidate"), "opus")

    def test_default_group_passes_no_model(self):
        self.assertIsNone(self.model_for("agent-job"), "model_group: default = 用工具自己的預設,不帶 --model")

    def test_pin_in_hub_json(self):
        hub = {"llm": {"points": {"librarian-state": {"model": "opus"}}}}
        self.assertEqual(self.model_for("librarian-state", hub), "opus")
        self.assertEqual(self.model_for("librarian-index", hub), "sonnet", "沒指定的點照組別")

    def test_pin_for_another_provider_is_not_the_runtime_model(self):
        hub = {"llm": {"points": {"librarian-state": {"provider": "elsewhere", "model": "x"}}}}
        self.assertEqual(self.model_for("librarian-state", hub), "haiku")

    def test_cli_wins(self):
        hub = {"llm": {"points": {"librarian-state": {"model": "opus"}}}}
        self.assertEqual(self.model_for("librarian-state", hub, cli="haiku"), "haiku")

    def test_models_env_is_ignored(self):
        (self.cfg / "models.env").write_text("STATE_MODEL=opus\n")
        self.assertEqual(self.model_for("librarian-state"), "haiku")


if __name__ == "__main__":
    unittest.main()
