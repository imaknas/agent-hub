"""The platform's headless judgment points do not load the environment's MCP servers (a hole found by the 10-27
sealing experiment).

Experiment: `claude -p --tools ""` (all tools off) still got the global/plugin MCP tools, 265 of them, including
execute_sql on a project's production database. Turning tools off does not turn MCP off; only `--strict-mcp-config`
blocks them (the experiment confirmed it blocks plugin MCP too, without affecting what --mcp-config gives explicitly).
So every judgment point must carry it; exceptions are written here, with a reason.

    bin/py -m unittest discover -s tests
"""
import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
FLAG = "--strict-mcp-config"
POINTS = yaml.safe_load((ROOT / "contracts" / "llm-points.yaml").read_text())["points"]
# points whose flag is added by the calling program, not in the point's declaration → which program (the test
# confirms that program really adds it)
ADDED_BY_CALLER = {
    # exception: a registry job with connectors: true (needs claude.ai connectors) does not get it
    "agent-job": "bin/run-agent-job.sh",
    "mailbox-escalation": "bin/run-agent-job.sh",   # mailbox requests also go through run-agent-job.sh
    "agent-job-event": "bin/run-agent-job.sh",      # event-triggered agent jobs too
    "agent-job-confined": "bin/run-agent-job.sh",   # clock jobs that declare allow: too (connectors: the exception)
    "lease-session": "bin/lease-runner.py",         # a lease uses only the permission relay given by --mcp-config
    "lease-work": "bin/lease-runner.py",
    "lease-handover": "bin/lease-runner.py",
}


class McpSealed(unittest.TestCase):
    def test_every_point_is_sealed_from_ambient_mcp(self):
        missing = []
        # completion-shaped points: with no llm block they go to runtime_claude.complete, which always carries this flag
        # (sealed by construction, not by declaration)
        src = (ROOT / "bin" / "runtime_claude.py").read_text()
        complete_src = src.split("def complete(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("completion_command(req)", complete_src, "complete() runs the one builder")
        self.assertIn(FLAG, src.split("def completion_command(", 1)[1].split("\ndef ", 1)[0])
        # tool_servers: none (a project's declared agent, project-agent): the adapter adds the flag itself
        flags_src = (ROOT / "bin" / "runtime_claude.py").read_text().split("def _flags(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('spec.get("tool_servers") == "none" and SEAL not in extra', flags_src)
        for name, spec in POINTS.items():
            if FLAG in (spec.get("extra_args") or []) or spec.get("shape") == "completion" \
                    or spec.get("tool_servers") == "none":
                continue
            caller = ADDED_BY_CALLER.get(name)
            if caller and FLAG in (ROOT / caller).read_text():
                continue
            missing.append(name)
        self.assertEqual(missing, [], f"these judgment points would load the environment's MCP (production databases "
                                      f"included): add {FLAG}, or write here who adds it and why")

    def test_no_point_gets_the_flag_twice(self):
        # in the declaration and added again by the caller: not needed, and it is unclear whether the CLI accepts a
        # repeated flag
        both = [n for n, c in ADDED_BY_CALLER.items() if FLAG in (POINTS.get(n, {}).get("extra_args") or [])]
        self.assertEqual(both, [])

    def test_agent_jobs_skip_it_only_for_connectors(self):
        src = (ROOT / "bin" / "run-agent-job.sh").read_text()
        line = next(l for l in src.splitlines() if "llm.py" in l and " run " in l)
        self.assertIn("$MCP_ARGS", line.replace("${MCP_ARGS[@]}", "$MCP_ARGS"))
        self.assertRegex(src, r'LLM_AUTH:-\}" *(=|!=) *"?subscription')


if __name__ == "__main__":
    unittest.main()
