"""A project's own confined agent calls (docs/design/project-agent-calls.md A): `brunna agent <name> < prompt`.

- The registry takes `agents: {<name>: {tier, allow, timeout?}}` and `agents_per_day`, the shape only; the rules each
  declared agent's runs would refuse are listed, never an error.
- A run that loads no tool servers (tool_servers: none) refuses a rule naming one.
- `llm.py agent <project> <name>` runs judgment point project-agent with the declared rules, tier and timeout (never
  the request's), in one of the project's registry folders, behind the billing gate, under the name <project>/<name>.
- cap-runner takes `kind: agent` requests: only declared names, one call per project at a time, daily minutes reserved
  and settled however a call ends, its own pool (one job per project per pool, the least recently served first), a
  deadline of the declared timeout plus a minute, the prompt through platform state.

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
               "timeout": {"a": {"tier": "fast", "allow": ["Read"], "timeout": "90s"}},
               "no time": {"a": {"tier": "fast", "allow": ["Read"], "timeout": "0m"}}}
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
                 "over the limit": ({"prompt": "x"}, "research", {"cap_runner.agent_max_timeout_s": 1800}),
                 "no tier": ({"prompt": "x"}, "untiered", None)}
        self.doc["agents"]["untiered"] = {"allow": ["Read"]}
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

    def test_the_owners_pin_is_per_call_else_the_points(self):
        rc, _out = self.call({"prompt": "x"}, conf={"llm": {"points": {"p/research": {"model": "pinned"},
                                                                        "project-agent": {"model": "point"}}}})
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake._ran[-1].model, "pinned")
        rc, _out = self.call({"prompt": "x"}, conf={"llm": {"points": {"project-agent": {"model": "point"}}}})
        self.assertEqual(self.fake._ran[-1].model, "point")

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


def load_cap_runner():
    import importlib.machinery
    loader = importlib.machinery.SourceFileLoader("cap_runner_agent", str(ROOT / "bin" / "cap-runner.py"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


class Runner(unittest.TestCase):
    def setUp(self):
        self.cr = cr = load_cap_runner()
        self.tmp = tmpdir(self)
        self.ws, self.state = self.tmp / "ws", self.tmp / "state"
        self.ws.mkdir()
        self.state.mkdir()
        self.agents = {"research": {"tier": "strong", "allow": ["Read"], "timeout": "45m"},
                       "quick": {"tier": "fast", "allow": ["Read"], "timeout": "10m"}}
        self.elig = {"p": {"dirs": [self.ws], "caps": [], "models": {"m": {"tier": "fast"}}, "agents": self.agents,
                           "agents_per_day": None}}
        self.started, self.conf = [], {}
        test = self

        class FakeProc:
            def __init__(self):
                self.pid = 4242 + len(test.started)
                self.rc = None

            def poll(self):
                return self.rc

        def popen(argv, **kw):
            proc = FakeProc()
            self.started.append((argv, kw, proc))
            return proc
        for target, value in (("eligible", lambda: self.elig), ("DIR", self.state), ("LOG", self.state / "log.jsonl"),
                              ("MINUTES", self.state / "agent-minutes.json"), ("ACTIVE", self.state / "active.json"),
                              ("exclude_hub_run", lambda project, ws: None),
                              ("conf", lambda key, default: self.conf.get(key, default))):
            p = mock.patch.object(cr, target, value)
            p.start()
            self.addCleanup(p.stop)
        self.real_popen = cr.subprocess.Popen
        for target, attr, value in ((cr.subprocess, "Popen", popen), (cr, "process_started", lambda pid: None),
                                    (cr.Runner, "kill", staticmethod(lambda pgid, sig: None))):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def put(self, job_id, ws=None, **req):
        d = (ws or self.ws) / ".hub-run" / "requests"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{job_id}.json").write_text(json.dumps({"v": 1, "id": job_id, "kind": "agent", **req}))

    def state_of(self, job_id, ws=None):
        return json.loads(((ws or self.ws) / ".hub-run" / "jobs" / job_id / "state.json").read_text())

    def ledger(self):
        """{<project>/<id>: entry}: the runner's own keys carry a suffix of their own"""
        f = self.state / "agent-minutes.json"
        jobs = json.loads(f.read_text())["jobs"] if f.exists() else {}
        return {k.rsplit("/", 1)[0]: v for k, v in jobs.items()}

    def entries(self):
        f = self.state / "agent-minutes.json"
        return list(json.loads(f.read_text())["jobs"].values()) if f.exists() else []

    def test_a_declared_call_runs_llm_agent_in_the_folder_it_came_from(self):
        self.put("a1", name="research", prompt="look", schema={"type": "object"})
        r = self.cr.Runner()
        r.scan()
        r.start_ready()
        [(argv, kw, _proc)] = self.started
        self.assertNotIn("sandbox-exec", " ".join(argv))
        i = argv.index("agent")
        self.assertEqual(argv[i:i + 3], ["agent", "p", "research"])
        self.assertEqual(argv[argv.index("--cwd") + 1], str(self.ws))
        inp = pathlib.Path(argv[argv.index("--request") + 1])
        self.assertEqual(inp.parent, self.state / "p", "the prompt is read from platform state")
        self.assertEqual(json.loads(inp.read_text()), {"prompt": "look", "schema": {"type": "object"}})
        self.assertNotIn("LLM_AUTH", kw["env"])
        self.assertEqual(self.state_of("a1")["state"], "running")
        job = r.running[0]
        self.assertAlmostEqual(job.deadline - job.started, 45 * 60 + 60)
        self.assertEqual(self.ledger()["p/a1"]["reserved"], 45)

    def test_refused_by_the_runner(self):
        cases = {"b1": {"name": "other", "prompt": "x"}, "b2": {"name": "research", "prompt": " "},
                 "b3": {"name": "research", "prompt": "x", "schema": "no"}}
        for job_id, req in cases.items():
            self.put(job_id, **req)
        self.conf["agent_max_timeout_s"] = 1800
        self.put("b4", name="research", prompt="x")
        r = self.cr.Runner()
        r.scan()
        for job_id in [*cases, "b4"]:
            with self.subTest(job_id):
                self.assertEqual(self.state_of(job_id)["state"], "rejected")
        self.assertEqual(self.ledger(), {})

    def test_one_call_per_project_at_a_time(self):
        self.put("c1", name="quick", prompt="x")
        r = self.cr.Runner()
        r.scan()
        self.put("c2", name="quick", prompt="y")
        r.scan()
        self.assertEqual(self.state_of("c2")["state"], "rejected")
        r.start_ready()
        self.put("c3", name="quick", prompt="z")   # a nested call, from the running one's script
        r.scan()
        self.assertEqual(self.state_of("c3")["state"], "rejected")
        self.assertEqual(list(self.ledger()), ["p/c1"])

    def test_an_agent_doesnt_hold_up_its_projects_other_requests(self):
        self.put("d1", name="quick", prompt="x")
        r = self.cr.Runner()
        r.scan()
        r.start_ready()
        d = self.ws / ".hub-run" / "requests"
        (d / "d2.json").write_text(json.dumps({"v": 1, "id": "d2", "kind": "complete", "point": "m", "prompt": "q"}))
        r.scan()
        r.start_ready()
        self.assertEqual([j.id for j in r.running], ["d1", "d2"])

    def test_the_agent_pool_and_who_goes_first(self):
        ws2, ws3 = self.tmp / "ws2", self.tmp / "ws3"
        for name, ws in (("q", ws2), ("s", ws3)):
            ws.mkdir()
            self.elig[name] = {**self.elig["p"], "dirs": [ws]}
        r = self.cr.Runner()
        r.served[("agent", "p")] = 1.0   # p was served before, q and s never
        for job_id, ws in (("e1", self.ws), ("e2", ws2), ("e3", ws3)):
            self.put(job_id, ws=ws, name="quick", prompt="x")
        r.scan()
        r.start_ready()
        self.assertEqual(len(r.running), 1, "one agent call at a time across projects")
        self.assertNotEqual(r.running[0].project, "p", "p was served most recently")

    def test_minutes_reserved_then_settled_however_it_ends(self):
        r = self.cr.Runner()
        self.put("f1", name="quick", prompt="x")
        r.scan()
        r.start_ready()
        job = r.running[0]
        job.started -= 125
        self.started[0][2].rc = 0
        r.poll()
        self.assertEqual(self.ledger()["p/f1"]["used"], 3)
        self.assertEqual(self.state_of("f1")["kind"], "agent")
        # a runner that restarts settles what the gone one reserved, up to now
        self.put("f2", name="quick", prompt="x")
        r.scan()
        self.cr.Runner().recover()
        self.assertEqual(self.ledger()["p/f2"]["used"], 0)

    def test_a_deadline_kill_is_124_and_settled(self):
        r = self.cr.Runner()
        self.put("g1", name="quick", prompt="x")
        r.scan()
        r.start_ready()
        job = r.running[0]
        job.deadline = 0
        r.poll()
        self.assertTrue(job.timed_out)
        self.started[0][2].rc = -15
        job.started -= 11 * 60
        r.poll()
        st = self.state_of("g1")
        self.assertEqual((st["exit"], st["timed_out"]), (124, True))
        self.assertEqual(self.ledger()["p/g1"]["used"], 10, "never more than it reserved")

    def test_queued_past_its_timeout_is_refused_and_settled(self):
        self.conf["agent_concurrency"] = 0
        r = self.cr.Runner()
        self.put("h1", name="quick", prompt="x")
        r.scan()
        r.queue[0].queued -= 11 * 60
        r.start_ready()
        self.assertEqual(self.state_of("h1")["state"], "rejected")
        self.assertEqual(self.ledger()["p/h1"]["used"], 0)

    def test_the_daily_minutes(self):
        m = self.cr.Minutes()

        def why(*a):
            return m.reserve(*a)[0]
        self.assertEqual(why("p", "1", 45 * 60, None), "")
        two = m.reserve("p", "2", 45 * 60, None)
        self.assertEqual(two[0], "")
        self.assertTrue(why("p", "3", 60, None), "90 minutes a project by default")
        m.settle(two[1], 10 * 60)
        self.assertEqual(why("p", "3", 30 * 60, None), "")
        self.assertTrue(why("q", "1", 60, 0) == "" and why("s", "1", 60, 1) == "")
        self.assertTrue(why("s", "2", 60, 1), "agents_per_day lowers the share")
        self.assertTrue(why("t", "1", 60, 500) == "" and why("t", "2", 90 * 60, 500), "never raises it")
        self.conf["agent_minutes_per_day"] = 90
        self.assertTrue(why("u", "1", 10 * 60, None), "the platform's day is full")

    def test_an_id_used_again_doesnt_replace_what_was_spent(self):
        r = self.cr.Runner()
        for ws in (self.ws, self.tmp / "rt"):
            ws.mkdir(exist_ok=True)
        self.elig["p"]["dirs"] = [self.ws, self.tmp / "rt"]
        for _round in range(3):
            self.put("k1", name="research", prompt="x")   # 45 minutes each
            r.scan()
            r.start_ready()
            if r.running:
                r.running[0].started -= 45 * 60
                self.started[-1][2].rc = 0
                r.poll()
            import shutil as sh
            sh.rmtree(self.ws / ".hub-run" / "jobs", ignore_errors=True)   # the project removes its job folders
        self.assertEqual(sum(e["used"] for e in self.entries()), 90, "two calls fit the day's 90 minutes, not three")
        self.put("k2", ws=self.tmp / "rt", name="quick", prompt="x")
        r.scan()
        self.assertEqual(self.state_of("k2", ws=self.tmp / "rt")["state"], "rejected")

    def test_a_job_folder_that_cant_be_written_wedges_nothing(self):
        import shutil as sh
        r = self.cr.Runner()
        self.put("w1", name="quick", prompt="x")
        r.scan()
        r.start_ready()
        sh.rmtree(self.ws / ".hub-run")   # the project removes it while the agent runs
        self.started[0][2].rc = 0
        r.poll()
        self.assertEqual(r.running, [])
        self.assertIsNotNone(self.entries()[0]["used"])
        self.put("w2", name="quick", prompt="y")
        r.scan()
        r.start_ready()
        self.assertEqual([j.id for j in r.running], ["w2"])

    def test_a_start_whose_state_cant_be_written_leaves_no_run_behind(self):
        killed = []
        r = self.cr.Runner()
        self.put("x1", name="quick", prompt="x")
        r.scan()
        with mock.patch.object(self.cr, "write_state", mock.Mock(side_effect=OSError("planted link"))), \
                mock.patch.object(self.cr.Runner, "kill", staticmethod(lambda pgid, sig: killed.append(pgid))):
            r.start_ready()
        self.assertEqual(r.running, [])
        self.assertEqual(killed, [self.started[0][2].pid])
        self.assertEqual(self.entries()[0]["used"], 0)

    def test_a_restart_kills_only_what_the_runner_recorded(self):
        killed = []
        self.put("y1", name="quick", prompt="x")
        r = self.cr.Runner()
        r.scan()
        r.start_ready()
        mine = r.running[0]
        # the project writes another pid into its own state.json: never killed on its word
        d = self.ws / ".hub-run" / "jobs" / "y1"
        (d / "state.json").write_text(json.dumps({"state": "running", "pid": 1}))
        with mock.patch.object(self.cr, "process_started", lambda pid: mine.started if pid == mine.proc.pid else None), \
                mock.patch.object(self.cr.Runner, "kill", staticmethod(lambda pgid, sig: killed.append(pgid))):
            self.cr.Runner().recover()
        self.assertEqual(killed, [mine.proc.pid])
        self.assertEqual(self.state_of("y1")["state"], "lost")
        # a pid that started at another time (reused after a reboot) is left alone
        killed.clear()
        with mock.patch.object(self.cr, "process_started", lambda pid: mine.started + 3600), \
                mock.patch.object(self.cr.Runner, "kill", staticmethod(lambda pgid, sig: killed.append(pgid))):
            self.cr.Runner().recover()
        self.assertEqual(killed, [])

    def test_process_started(self):
        import time
        cr = load_cap_runner()   # its own process_started, and the real Popen for ps
        with mock.patch.object(cr.subprocess, "Popen", self.real_popen):
            here = cr.process_started(os.getpid())
            gone = cr.process_started(2 ** 22 + 12345)
        self.assertIsNotNone(here)
        self.assertLess(here, time.time() + 2)
        self.assertIsNone(gone)

    def test_eligible_takes_a_project_that_only_declares_agents(self):
        import types
        cr = load_cap_runner()
        rt = self.tmp / "rt"
        rt.mkdir()
        doc = {"name": "a", "agents": self.agents}
        fake = types.SimpleNamespace(dirs=lambda d: {"workspace": self.ws, "runtime": rt})
        with mock.patch.object(cr.hubconf, "projects", lambda: {"a": doc}), mock.patch.dict(sys.modules, {"registry": fake}):
            out = cr.eligible()
        self.assertEqual(out["a"]["dirs"], [self.ws, rt])
        self.assertEqual(out["a"]["agents"], self.agents)


if __name__ == "__main__":
    unittest.main()
