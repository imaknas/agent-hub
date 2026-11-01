"""Which model a judgment point uses: computed only in llm.model_for across the platform (since 10-29; five places
used to keep their own default table each, and a blind review on 10-27 found them out of sync).

Precedence: --model > hub.json llm.points.<point>.model (no provider given, or the provider is this runtime) > the
runtime's group table (runtime_<name>.MODELS) > none (a model_group: default point uses the tool's own default).
Nobody reads models.env any more.

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
        # login: a fake token in a fake HOME (never this machine's real credentials; the gate uses a fake HOME too)
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
                # agent-job as run-agent-job.sh runs every job but a connectors one: sealed to the platform's MCP
                # config (an unsealed run first lists the servers it would load, which needs the runtime installed)
                extra = ["--strict-mcp-config"] if point == "agent-job" else []
                a = argparse.Namespace(cmd="run", point=point, cwd=None, model=cli, prompt_file=str(self.prompt),
                                       dry_run=True, extra=extra)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    llm.run(a)
                direct = llm.model_for(point, cli=cli)
            finally:
                hubconf._cfg = None
        cmd = json.loads(out.getvalue())
        used = cmd[cmd.index("--model") + 1] if "--model" in cmd else None
        self.assertEqual(used, direct, "the actual command matches what model_for reports (what goes in the log)")
        return used

    def test_group_defaults_from_the_runtime(self):
        self.assertEqual(self.model_for("librarian-state"), "haiku")
        self.assertEqual(self.model_for("librarian-index"), "sonnet")
        self.assertEqual(self.model_for("consolidate"), "opus")

    def test_default_group_passes_no_model(self):
        self.assertIsNone(self.model_for("agent-job"), "model_group: default = the tool's own default, no --model")

    def test_pin_in_hub_json(self):
        hub = {"llm": {"points": {"librarian-state": {"model": "opus"}}}}
        self.assertEqual(self.model_for("librarian-state", hub), "opus")
        self.assertEqual(self.model_for("librarian-index", hub), "sonnet", "a point with nothing set follows its group")

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
