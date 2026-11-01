"""init's agent step (docs/design/newcomer-path.md B4): the agents it offers come from the runtime adapters'
install_info() and the modules' runtime_preset, never from init itself; applying one sets hub.json's runtime and turns
the other presets' modules off.

    bin/py -m unittest tests.test_agent_presets
"""
import importlib.machinery
import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import registry  # noqa: E402
import yaml  # noqa: E402

loader = importlib.machinery.SourceFileLoader("agenthub_presets", str(ROOT / "bin" / "agenthub"))
spec = importlib.util.spec_from_loader("agenthub_presets", loader)
ah = importlib.util.module_from_spec(spec)
loader.exec_module(ah)


class Presets(unittest.TestCase):
    def test_mcp_registration_needs_the_owners_yes(self):
        """B5: a user-level change, so --yes or no terminal registers nothing; a yes runs each tool's command with the
        agenthub command put in."""
        from unittest import mock
        presets = {"x": {"label": "X", "find": ["x"], "mcp_register": ["x", "mcp", "add", "{agenthub}", "mcp"]}}
        with mock.patch.object(ah, "agent_found", lambda info: True), \
             mock.patch.object(ah.subprocess, "run") as run, mock.patch.object(ah, "agenthub_command",
                                                                               lambda: ["/hub/bin/agenthub"]):
            for yes, tty in ((True, True), (False, False)):
                with mock.patch.object(ah.sys.stdin, "isatty", lambda: tty), mock.patch("builtins.input", lambda q: "y"):
                    ah.offer_mcp_registration(presets, yes)
            run.assert_not_called()
            run.return_value.returncode = 0
            with mock.patch.object(ah.sys.stdin, "isatty", lambda: True), mock.patch("builtins.input", lambda q: ""):
                ah.offer_mcp_registration(presets, False)
            self.assertEqual(run.call_args.args[0], ["x", "mcp", "add", "/hub/bin/agenthub", "mcp"])

    def test_offered_agents(self):
        presets = ah.agent_presets()
        self.assertEqual(list(presets)[:2], ["claude", "codex"], "adapters first, then modules")
        for name, info in presets.items():
            with self.subTest(name=name):
                self.assertTrue(info["label"] and info["gives"] and info["find"])
                self.assertIn("runtime", info["settings"])
                self.assertIn(info["credential"]["kind"], ("pasted-token", "tool-login"))

    def test_apply(self):
        presets = ah.agent_presets()
        d = {"runtime": {"default": "claude"}, "modules": {"claude": True, "codex": False, "owner": True}}
        ah.apply_agent(d, "codex", presets)
        self.assertEqual(d["runtime"]["default"], "command")
        self.assertEqual((d["modules"]["claude"], d["modules"]["codex"], d["modules"]["owner"]), (False, True, True))
        ah.apply_agent(d, "none", presets)
        self.assertEqual(d["runtime"]["default"], "none")
        self.assertFalse(d["modules"]["codex"])

    def test_codex_module_validates(self):
        doc = yaml.safe_load((ROOT / "modules" / "codex.yaml").read_text())
        self.assertEqual(registry.validate_module(doc, "codex"), [])
        bad = {**doc, "runtime_preset": {"label": "X"}}
        self.assertTrue(registry.validate_module(bad, "codex"))

    def test_init_never_installs_or_logs_in_without_a_yes(self):
        self.assertFalse(ah.confirm("anything?", True), "--yes does not mean installing for the user")


if __name__ == "__main__":
    unittest.main()
