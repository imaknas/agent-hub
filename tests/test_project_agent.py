"""A project's own confined agent calls (docs/design/project-agent-calls.md A): `brunna agent <name> < prompt`.

- The registry takes `agents: {<name>: {tier, allow, timeout?}}` and `agents_per_day`, the shape only; the rules each
  declared agent's runs would refuse are listed, never an error.
- A run that loads no tool servers (tool_servers: none) refuses a rule naming one.

No agent is started.

    bin/py -m unittest tests.test_project_agent
"""
import contextlib
import io
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import registry  # noqa: E402
import runtime_claude  # noqa: E402


def tmpdir(test) -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp()).resolve()
    test.addCleanup(shutil.rmtree, d, True)
    return d


class Registry(unittest.TestCase):
    def errs(self, **doc):
        return registry.validate_agents(doc)

    def test_a_declared_agent(self):
        self.assertEqual(self.errs(agents={"research": {"tier": "strong", "allow": ["Edit(./data/log.md)",
                                                                                    "WebFetch(domain:example.org)"],
                                                        "timeout": "45m"}}, agents_per_day=60), [])
        self.assertEqual(self.errs(agents={"a-1": {"tier": "fast", "allow": ["Read"]}}), [])

    def test_its_shape(self):
        bad = {"name": {"Bad_Name": {"tier": "fast", "allow": ["Read"]}},
               "keys": {"a": {"tier": "fast", "allow": ["Read"], "model": "x"}},
               "tier": {"a": {"tier": "opus", "allow": ["Read"]}},
               "no allow": {"a": {"tier": "fast"}},
               "empty allow": {"a": {"tier": "fast", "allow": []}},
               "an option": {"a": {"tier": "fast", "allow": ["--dangerously-skip-permissions"]}},
               "two lines": {"a": {"tier": "fast", "allow": ["Edit(x)\nBash"]}},
               "timeout": {"a": {"tier": "fast", "allow": ["Read"], "timeout": "90s"}}}
        for what, agents in bad.items():
            with self.subTest(what):
                self.assertTrue(self.errs(agents=agents))
        self.assertTrue(self.errs(agents=["a"]))
        for per_day in (0, -5, "60", True, 1.5):
            with self.subTest(per_day=per_day):
                self.assertTrue(self.errs(agents_per_day=per_day))

    def test_a_valid_entry_with_agents_and_the_timeout(self):
        self.assertIn("agents", registry.TOP)
        self.assertIn("agents_per_day", registry.TOP)
        self.assertEqual(registry.agent_timeout_s({}), 1800)
        self.assertEqual(registry.agent_timeout_s({"timeout": "45m"}), 2700)
        self.assertEqual(registry.agent_timeout_s({"timeout": "1h"}), 3600)

    def test_both_schemas(self):
        import json
        import minischema
        doc = {"agents": {"research": {"tier": "strong", "allow": ["Read"], "timeout": "45m"}}, "agents_per_day": 60}
        for v in (1, 2):
            schema = json.loads((ROOT / "contracts" / f"project.v{v}.schema.json").read_text())
            props = schema["properties"]
            with self.subTest(v=v):
                self.assertEqual(minischema.errors(props["agents"], doc["agents"]), [])
                self.assertEqual(minischema.errors(props["agents_per_day"], 60), [])
                self.assertTrue(minischema.errors(props["agents"], {"research": {"tier": "strong"}}))

    def test_the_rules_a_run_would_refuse_are_listed(self):
        import runtime_ports
        index = {"projects": {"p": {"name": "p", "agents": {
            "ok": {"tier": "fast", "allow": ["Edit(./data/x.md)"]},
            "server": {"tier": "fast", "allow": ["mcp__github__create_issue"]},
            "outside": {"tier": "fast", "allow": ["Edit(~/x)"]}}}}}
        with mock.patch.object(runtime_ports, "load", lambda *a, **k: runtime_claude):
            out = registry.rules_refused(index)
        self.assertEqual(sorted(out), ["p/agents.outside", "p/agents.server"])
        self.assertIn("mcp__github__create_issue", out["p/agents.server"])

    def test_the_secrets_report_names_an_agents_commands(self):
        import runtime_ports
        ws = tmpdir(self)
        doc = {"name": "p", "paths": {"workspace": str(ws)}, "secrets": [{"name": "t", "path": "secrets/token"}],
               "agents": {"run": {"tier": "fast", "allow": ["Read", "Bash(.venv/bin/python x.py)"]}}}
        out = io.StringIO()
        with mock.patch.object(registry, "sandbox_enforced", lambda doc: False), \
                mock.patch.object(runtime_ports, "load", lambda *a, **k: runtime_claude), contextlib.redirect_stdout(out):
            registry.secrets_report({"projects": {"p": doc}})
        self.assertIn("agents.run", out.getvalue())
        self.assertIn("Bash(.venv/bin/python x.py)", out.getvalue())


class NoServers(unittest.TestCase):
    def test_a_run_that_loads_none_refuses_a_rule_naming_one(self):
        rules = ["Read", "mcp__github", "mcp__github__create_issue"]
        self.assertEqual(runtime_claude.rules_problems(rules, sandboxed=False, network=[], untrusted=False), [])
        problems = runtime_claude.rules_problems(rules, sandboxed=False, network=[], untrusted=False, servers=False)
        self.assertEqual(len(problems), 2)

    def test_the_point_seals_the_run(self):
        import runtime_ports
        task = runtime_ports.Task(point="project-agent", prompt="x", spec={"tool_servers": "none"}, cwd=None,
                                  model=None, output_schema=None, timeout_s=60, env={})
        self.assertTrue(runtime_claude._sealed(task))
        self.assertIsNone(runtime_claude.ambient_servers(task))


if __name__ == "__main__":
    unittest.main()
