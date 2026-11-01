"""Where the hub is: what the platform writes that calls back into the hub (the agent's hook commands, the user-level
worktree hooks, the lease resume line) names the hub's real location.

Characterized first: with the hub at ~/agent-hub, the text is exactly what is installed in projects today, character
for character, so a rewrite at the next session start changes nothing on such a machine.

    bin/py -m unittest tests.test_hub_location
"""
import importlib.machinery
import importlib.util
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import runtime_claude  # noqa: E402


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def py(expr: str) -> str:
    """How a platform Python script is written in a hook on this system (hubconf.sh_py)."""
    return expr if sys.platform == "darwin" else f"{expr.rsplit('/', 1)[0]}/py {expr}"


class AtHomeToday(unittest.TestCase):
    """The hub at ~/agent-hub (the layout every installed hook was written for)."""

    def setUp(self):
        hub = pathlib.Path.home() / "agent-hub"
        for name in ("ROOT", "MAIN"):
            p = mock.patch.object(hubconf, name, hub)
            p.start()
            self.addCleanup(p.stop)

    def test_hook_commands(self):
        for _name, _matcher, ev in runtime_claude.HOOK_TABLE:
            self.assertEqual(runtime_claude.hook_command(ev),
                             f"[ -x ~/agent-hub/bin/hub-event ] && {py('~/agent-hub/bin/hub-event')} {ev} || true")

    def test_installed_hooks(self):
        def entry(ev, matcher=None):
            e = {"hooks": [{"type": "command",
                            "command": f"[ -x ~/agent-hub/bin/hub-event ] && {py('~/agent-hub/bin/hub-event')} {ev} || true"}]}
            return {"matcher": matcher, **e} if matcher else e
        self.assertEqual(runtime_claude.install_hooks({}), {"hooks": {
            "SessionStart": [entry("session_start")],
            "SessionEnd": [entry("session_end")],
            "PreCompact": [entry("pre_compact")],
            "UserPromptSubmit": [entry("prompt")],
            "PostToolUse": [entry("tool_used", "Bash")],
            "PreToolUse": [entry("open_session_attempt", "mcp__ccd_session__start_session")],
        }})

    def test_worktree_hooks(self):
        m = load("merge_hook_settings", BIN / "merge-hook-settings.py")
        wt = "[ -x ~/agent-hub/bin/worktree-memory.sh ] && ~/agent-hub/bin/worktree-memory.sh"
        self.assertEqual(m.WORKTREE_HOOKS, (
            ("SessionStart", None, f"{wt} start || true"),
            ("SessionEnd", None, f"{wt} end || true"),
            ("PreCompact", None, f"{wt} end || true"),
            ("PostToolUse", "Bash", f"{wt} nudge || true"),
            ("UserPromptSubmit", None, f"{wt} prompt || true"),
        ))

    def test_resume_command(self):
        self.assertEqual(runtime_claude.resume_command("s1")[0], "~/agent-hub/bin/lease-resume s1")


if __name__ == "__main__":
    unittest.main()
