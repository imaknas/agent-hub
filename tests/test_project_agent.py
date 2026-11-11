"""A project's own confined agent calls (docs/design/project-agent-calls.md A): `brunna agent <name> < prompt`.

- The registry takes `agents: {<name>: {tier, allow, timeout?}}` and `agents_per_day`, the shape only; the rules each
  declared agent's runs would refuse are listed, never an error.
- A run that loads no tool servers (tool_servers: none) refuses a rule naming one.
- `llm.py agent <project> <name>` runs judgment point project-agent with the declared rules, tier and timeout (never
  the request's), in one of the project's registry folders, behind the billing gate, under the name <project>/<name>.

No agent is started.

    bin/py -m unittest tests.test_project_agent
"""
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
import hubconf  # noqa: E402
import registry  # noqa: E402
import runtime_claude  # noqa: E402


def load_llm():
    spec = importlib.util.spec_from_file_location("llm_agent", ROOT / "bin" / "llm.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


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


class Call(unittest.TestCase):
    def setUp(self):
        import completion
        import runtime_fake
        self.fake, self.completion = runtime_fake, completion
        runtime_fake.reset()
        self.addCleanup(runtime_fake.reset)
        self.fake.set_billing("subscription")
        self.llm = load_llm()
        self.state, self.ws, self.rt = tmpdir(self), tmpdir(self), tmpdir(self)
        self.failed = []
        self.doc = {"name": "p", "paths": {"workspace": str(self.ws)}, "agents": {
            "research": {"tier": "strong", "allow": ["Edit(./data/log.md)"], "timeout": "45m"}}}
        for target, attr, value in ((completion, "USAGE", self.state / "llm-usage.jsonl"),
                                    (self.llm, "job_failed", lambda *a: self.failed.append(a)),
                                    (hubconf, "STATE", self.state)):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def call(self, request: dict, name="research", cwd=None, env=None, conf=None):
        import runtime_ports
        req = self.state / "req.json"
        req.write_text(json.dumps(request))
        out = io.StringIO()
        get = hubconf.get
        with mock.patch.dict(os.environ, {"AGENT_JOB_MAIL": "1", "LLM_AUTH": "subscription", **(env or {})}), \
                mock.patch.object(runtime_ports, "load", lambda *a, **k: self.fake), \
                mock.patch.object(registry, "refresh", lambda *a, **k: {"projects": {"p": self.doc}}), \
                mock.patch.object(hubconf, "get", lambda k, d=None: (conf or {}).get(k, get(k, d))), \
                contextlib.redirect_stdout(out):
            rc = self.llm.project_agent("p", name, req, str(cwd or self.ws))
        return rc, json.loads(out.getvalue())

    def rows(self):
        f = self.completion.USAGE
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def test_it_runs_what_the_registry_declares(self):
        schema = {"type": "object"}
        rc, out = self.call({"prompt": "look", "schema": schema, "allow": ["Bash"], "timeout": "1h"})
        self.assertEqual(rc, 0, out)
        [task] = self.fake._ran
        self.assertEqual((task.point, task.prompt, task.cwd), ("project-agent", "look", str(self.ws)))
        self.assertEqual(json.loads(task.env["AGENT_JOB_ALLOW"]), ["Edit(./data/log.md)"])   # never the request's
        self.assertEqual((task.env["AGENT_JOB_PROJECT"], task.env["AGENT_JOB_TIER"]), ("p", "strong"))
        self.assertNotIn("AGENT_JOB_MAIL", task.env)
        self.assertEqual(task.timeout_s, 2700)
        self.assertEqual(task.output_schema, schema)
        spec = self.llm.points()["points"]["project-agent"]
        self.assertEqual(task.model, self.llm.model_for("p/research", {**spec, "model_group": "strong"}))
        self.assertEqual(out["llm_point"], "p/research")
        [row] = self.rows()
        self.assertEqual((row["point"], row["billing"], row["project"]), ("p/research", "subscription", "p"))

    def test_the_runtime_folder_too_but_nowhere_else(self):
        self.doc["paths"]["runtime"] = str(self.rt)
        with mock.patch.object(registry, "dirs", lambda doc: {"workspace": self.ws, "runtime": self.rt}):
            self.assertEqual(self.call({"prompt": "x"}, cwd=self.rt)[0], 0)
            for elsewhere in (self.ws / "sub", self.state):
                elsewhere.mkdir(exist_ok=True)
                with self.subTest(cwd=elsewhere):
                    rc, out = self.call({"prompt": "x"}, cwd=elsewhere)
                    self.assertEqual(rc, 1)
                    self.assertTrue(out["llm_error"])
        self.assertEqual(len(self.fake._ran), 1)

    def test_refused_before_anything_runs(self):
        cases = {"undeclared": ({"prompt": "x"}, "other", None),
                 "empty prompt": ({"prompt": "  "}, "research", None),
                 "schema not an object": ({"prompt": "x", "schema": []}, "research", None),
                 "over the limit": ({"prompt": "x"}, "research", {"cap_runner.agent_max_timeout_s": 1800})}
        for what, (req, name, conf) in cases.items():
            with self.subTest(what):
                rc, out = self.call(req, name=name, conf=conf)
                self.assertEqual(rc, 1)
                self.assertTrue(out["llm_error"])
        self.assertEqual(self.fake._ran, [])
        self.assertEqual(len(self.failed), len(cases))

    def test_the_billing_gate(self):
        self.fake.set_billing("unknown")
        rc, out = self.call({"prompt": "x"})
        self.assertEqual(rc, 1)
        self.assertEqual(self.fake._ran, [])
        self.fake.set_billing("metered")
        self.assertEqual(self.call({"prompt": "x"})[0], 1)
        self.doc["metered_models"] = True
        self.assertEqual(self.call({"prompt": "x"})[0], 0)

    def test_the_owners_pin_is_per_call(self):
        rc, _out = self.call({"prompt": "x"}, conf={"llm": {"points": {"p/research": {"model": "pinned"}}}})
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake._ran[-1].model, "pinned")

    def test_rules_the_runtime_refuses(self):
        self.fake.set_rule_problems(["Edit(~/x) names a path that isn't relative to the run's folder"])
        rc, out = self.call({"prompt": "x"})
        self.assertEqual(rc, 1)
        self.assertIn("Edit(~/x)", out["llm_error"])
        self.assertEqual(self.fake._ran, [])

    def test_no_coding_agent(self):
        import runtime_ports

        def unsupported(*a, **k):
            raise runtime_ports.Unsupported("no coding agent here")
        req = self.state / "req.json"
        req.write_text(json.dumps({"prompt": "x"}))
        out = io.StringIO()
        with mock.patch.object(runtime_ports, "load", unsupported), contextlib.redirect_stdout(out), \
                mock.patch.object(registry, "refresh", lambda *a, **k: {"projects": {"p": self.doc}}), \
                mock.patch.dict(os.environ, {}):
            self.assertEqual(self.llm.project_agent("p", "research", req, str(self.ws)), 1)
        self.assertIn("no coding agent here", json.loads(out.getvalue())["llm_error"])


if __name__ == "__main__":
    unittest.main()
