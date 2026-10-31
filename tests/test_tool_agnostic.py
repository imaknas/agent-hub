"""Mechanical checks for the tool-agnostic hard rules (AGENTS.md, Tool neutrality; docs/design/tool-agnostic.md).

1. The platform's nightly core judgment points must run with any LLM provider (shape: completion). Those not yet
   converted are listed in PENDING, which **may only shrink**: once one is converted (declared completion), remove it
   from PENDING, or this fails as a reminder.
2. Platform files that import the Claude adapter (runtime_claude) directly are listed in DIRECT, which **may only
   shrink**; everywhere else goes through runtime_ports.

    bin/py -m unittest discover -s tests
"""
import pathlib
import re
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]

# the nightly core: judgment points that must run for someone with only an API key and no coding agent at all
CORE = {"librarian-state", "librarian-todo-format", "librarian-index", "reflect-week"}
# not yet converted to completion (switched only once the shadow-mode comparison is clean; llm-providers.md step 4)
PENDING = {"librarian-state", "librarian-todo-format", "librarian-index"}

# files using the Claude adapter directly (all Claude-only mechanisms, to move into the runtime interface later):
DIRECT = {
    "bin/lease-relay.py",               # Claude Code's permission relay (--permission-prompt-tool) itself
    # long-running session flags, MCP config (moves into the runtime after layer 1's open_session)
    "bin/lease-runner.py",
    "bin/render-project-settings.py",   # neutral policy → Claude's settings.local.json
    "bin/merge-hook-settings.py",       # old entry point, hands off to runtime_claude.install_hooks
    "bin/migrate-to-mini.py",           # one-off migration tool run on the laptop
}
IMPORT = re.compile(r"^\s*(?:import runtime_claude\b|from runtime_claude\b)", re.M)


class ToolAgnostic(unittest.TestCase):
    def test_core_points_run_on_any_provider(self):
        points = yaml.safe_load((ROOT / "contracts" / "llm-points.yaml").read_text())["points"]
        self.assertTrue(CORE <= set(points), CORE - set(points))
        not_yet = {p for p in CORE if points[p].get("shape") != "completion"}
        self.assertLessEqual(not_yet, PENDING,
                             f"core judgment points went back to agent shape: {sorted(not_yet - PENDING)}")
        self.assertEqual(PENDING - not_yet, set(),
                         f"{sorted(PENDING - not_yet)} are completion now: remove them from PENDING")

    def test_direct_use_of_the_claude_adapter_only_shrinks(self):
        found = {f"bin/{f.name}" for f in (ROOT / "bin").iterdir()
                 if f.is_file() and f.name != "runtime_claude.py" and IMPORT.search(f.read_text(errors="replace"))}
        self.assertLessEqual(found, DIRECT,
                             f"new files import runtime_claude directly: {sorted(found - DIRECT)}; "
                             "go through runtime_ports")
        self.assertEqual(DIRECT - found, set(),
                         f"{sorted(DIRECT - found)} no longer use it directly: remove them from DIRECT")


if __name__ == "__main__":
    unittest.main()
