"""A model strength an agent job chooses (docs/design/project-agent-calls.md B, the billing gate of A5).

- The registry takes `tier: fast|standard|strong` on agent jobs with allow: only; the scheduler puts it in
  AGENT_JOB_TIER.
- The confined agent-job points take it as the run's model_group (an owner's pin on the point still wins).
- Such a run goes only where the runtime can tell how it is paid for: unknown is refused, metered needs the project's
  metered_models, room in llm.budget.daily_usd (with or without llm.providers), and no metered run earlier that day
  whose cost is unknown; every such run is a usage row.
- The day's spending counts metered rows only.

No agent is started.

    bin/py -m unittest tests.test_job_tier
"""
import argparse
import contextlib
import datetime
import importlib.util
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hubconf  # noqa: E402

try:
    import yaml
except ImportError:
    yaml = None

POINT = "agent-job-confined"


def load_llm():
    spec = importlib.util.spec_from_file_location("llm_tier", ROOT / "bin" / "llm.py")
    llm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(llm)
    return llm


def tmpdir(test) -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp()).resolve()
    test.addCleanup(shutil.rmtree, d, True)
    return d


def points() -> dict:
    return yaml.safe_load((ROOT / "contracts" / "llm-points.yaml").read_text())["points"]


@unittest.skipIf(yaml is None, "needs PyYAML")
class Registry(unittest.TestCase):
    def errors(self, job: dict) -> list:
        import registry
        tmp = tmpdir(self)
        (tmp / "p" / "jobs").mkdir(parents=True)
        (tmp / "p" / "jobs" / "j.md").write_text("x\n")
        doc = {"v": 1, "name": "p", "paths": {"workspace": str(tmp)}, "jobs": [{"name": "j", **job}]}
        with mock.patch.object(registry.hubconf, "registry_dir", lambda: tmp):
            return [e for e in registry.validate_runs(doc, "p", {"workspace": tmp}) if "tier" in e]

    def test_only_a_confined_agent_job_and_only_a_strength(self):
        agent = {"at": "08:00", "kind": "agent", "prompt": "jobs/j.md", "allow": ["Read"]}
        for tier in ("fast", "standard", "strong"):
            self.assertEqual(self.errors({**agent, "tier": tier}), [])
        no_allow = {k: v for k, v in agent.items() if k != "allow"}
        for job in ({**agent, "tier": "opus"}, {**agent, "tier": None}, {"at": "08:00", "run": "true", "tier": "fast"},
                    {**no_allow, "tier": "fast"}):
            with self.subTest(job):
                self.assertEqual(len(self.errors(job)), 1)

    def test_the_command_carries_it(self):
        import registry
        index = {"projects": {"p": {"name": "p", "paths": {"workspace": "/tmp/p-ws"}, "jobs": [
            {"name": "a", "at": "08:00", "kind": "agent", "allow": ["Read"], "tier": "strong"},
            {"name": "b", "trigger": {"events": ["merge.done"]}, "kind": "agent", "allow": ["Read"], "tier": "fast"},
            {"name": "c", "at": "10:00", "kind": "agent", "allow": ["Read"]}]}}}
        with mock.patch.object(registry.isolation, "sandboxed", lambda doc: False):
            jobs = {j["name"]: j["command"] for j in registry.scheduler_jobs(index, set())[0]}
        self.assertIn("AGENT_JOB_TIER=strong ", jobs["p/a"])
        self.assertIn("AGENT_JOB_TIER=fast ", jobs["p/b"])
        self.assertNotIn("AGENT_JOB_TIER", jobs["p/c"])

    def test_only_the_confined_points_take_it(self):
        taking = ["agent-job-confined", "project-agent", "agent-job-event"]   # project-agent: a declared agent's tier
        self.assertEqual([p for p, s in points().items() if "tier_from_env" in s], taking)
        for p in taking:
            self.assertEqual(points()[p]["tier_from_env"], "AGENT_JOB_TIER")
            self.assertEqual(points()[p]["settings"], "platform")   # restricted: the billing can be told

    def test_a_secrets_path_never_breaks_a_rule(self):
        import registry
        ok = [{"name": "a", "path": "secrets/token"}, {"name": "b", "path": "~/.config/x/key.json"}]
        self.assertEqual(registry.validate_secrets(ok), [])
        for bad in ("x)", "data/*", "a\nb", "a[1]", "{x}", "t?k", "a\x00"):
            with self.subTest(bad):
                self.assertEqual(len(registry.validate_secrets([{"name": "a", "path": bad}])), 1)


@unittest.skipIf(yaml is None, "needs PyYAML")
class Gate(unittest.TestCase):
    def setUp(self):
        import runtime_fake
        self.fake = runtime_fake
        runtime_fake.reset()
        self.addCleanup(runtime_fake.reset)
        self.llm = load_llm()
        self.state = tmpdir(self)
        import completion
        self.completion = completion
        for target, attr, value in ((completion, "USAGE", self.state / "llm-usage.jsonl"),
                                    (self.llm, "job_failed", lambda *a: None), (hubconf, "STATE", self.state)):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        self.spec = points()[POINT]
        self.doc = {"name": "p", "paths": {"workspace": str(self.state)}}

    def gate(self, how, doc=None, llm_conf=None, spent=0.0, unknown=False):
        import registry
        self.fake.set_billing(how)
        index = {"projects": {"p": doc}} if doc is not None else {"projects": {}}
        get = hubconf.get
        with mock.patch.object(registry, "refresh", lambda *a, **k: index), \
                mock.patch.object(hubconf, "get", lambda k, d=None: llm_conf if k == "llm" else get(k, d)), \
                mock.patch.object(self.completion, "today_cost", lambda: spent), \
                mock.patch.object(self.completion, "unknown_cost_today", lambda: unknown):
            return self.llm.billing_gate(POINT, self.spec, self.fake, {"AGENT_JOB_PROJECT": "p"}, "strong")

    def test_subscription_runs(self):
        self.assertEqual(self.gate("subscription"), ("subscription", None))

    def test_unknown_is_refused(self):
        how, why = self.gate("unknown")
        self.assertEqual(how, "unknown")
        self.assertIn("strong", why)

    def test_metered_needs_the_owners_yes(self):
        self.assertTrue(self.gate("metered", {"name": "p"})[1])
        self.assertTrue(self.gate("metered", None)[1])
        self.assertEqual(self.gate("metered", {"name": "p", "metered_models": True}), ("metered", None))

    def test_metered_stops_at_the_budget_with_or_without_providers(self):
        doc = {"name": "p", "metered_models": True}
        for conf in ({"budget": {"daily_usd": 5}}, {"budget": {"daily_usd": 5}, "providers": {"f": {"impl": "fake"}}}):
            with self.subTest(conf=conf):
                self.assertIsNone(self.gate("metered", doc, conf, spent=4.99)[1])
                self.assertIn("budget", self.gate("metered", doc, conf, spent=5.0)[1])

    def test_a_metered_run_of_unknown_cost_stops_the_day(self):
        doc = {"name": "p", "metered_models": True}
        self.assertTrue(self.gate("metered", doc, unknown=True)[1])
        self.assertIsNone(self.gate("subscription", doc, unknown=True)[1])

    def test_a_runtime_without_a_login_interface_is_unknown(self):
        import runtime_ports
        with mock.patch.object(runtime_ports, "capabilities", lambda name=None: {"agent"}):
            how, why = self.llm.billing_gate(POINT, self.spec, self.fake, {}, "fast")
        self.assertEqual(how, "unknown")
        self.assertTrue(why)

    def test_job_tier(self):
        self.assertIsNone(self.llm.job_tier(self.spec, {}))
        self.assertIsNone(self.llm.job_tier(points()["agent-job"], {"AGENT_JOB_TIER": "strong"}))
        self.assertEqual(self.llm.job_tier(self.spec, {"AGENT_JOB_TIER": "fast"}), "fast")
        with self.assertRaises(ValueError):
            self.llm.job_tier(self.spec, {"AGENT_JOB_TIER": "opus"})

    def run_job(self, env):
        import registry
        import runtime_ports
        prompt = self.state / "p.txt"
        prompt.write_text("x")
        a = argparse.Namespace(cmd="run", point=POINT, cwd=str(self.state), model=None, prompt_file=str(prompt),
                               dry_run=False, extra=[])
        out = io.StringIO()
        env = {"AGENT_JOB_PROJECT": "p", "AGENT_JOB_ALLOW": '["Read"]', **env}
        with mock.patch.dict(os.environ, env), mock.patch.object(runtime_ports, "load", lambda *x, **k: self.fake), \
                mock.patch.object(registry, "refresh", lambda *x, **k: {"projects": {"p": self.doc}}), \
                contextlib.redirect_stdout(out):
            rc = self.llm.run(a)
        return rc, out.getvalue()

    def rows(self) -> list:
        f = self.completion.USAGE
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def test_a_tier_sets_the_model_and_writes_a_row(self):
        self.fake.set_billing("subscription")
        rc, out = self.run_job({"AGENT_JOB_TIER": "strong"})
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.fake._ran[-1].model, self.llm.model_for(POINT, {**self.spec, "model_group": "strong"}))
        [row] = self.rows()
        self.assertEqual((row["point"], row["billing"], row["model"], row["project"]),
                         (POINT, "subscription", self.fake._ran[-1].model, "p"))

    def test_no_tier_no_gate_no_row(self):
        self.fake.set_billing("unknown")
        rc, out = self.run_job({"AGENT_JOB_TIER": ""})
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.fake._ran[-1].model, self.llm.model_for(POINT, self.spec))
        self.assertEqual(self.rows(), [])

    def test_a_refused_tier_starts_nothing(self):
        self.fake.set_billing("unknown")
        rc, out = self.run_job({"AGENT_JOB_TIER": "fast"})
        self.assertEqual(rc, 1)
        self.assertEqual(self.fake._ran, [])
        self.assertTrue(json.loads(out)["llm_error"])

    def test_the_owners_pin_still_wins(self):
        with mock.patch.object(hubconf, "get", lambda k, d=None: {"points": {POINT: {"model": "pinned"}}}
                               if k == "llm" else d):
            self.assertEqual(self.llm.model_for(POINT, {**self.spec, "model_group": "fast"}), "pinned")


class Spending(unittest.TestCase):
    def setUp(self):
        import completion
        self.completion = completion
        self.f = tmpdir(self) / "u.jsonl"
        p = mock.patch.object(completion, "USAGE", self.f)
        p.start()
        self.addCleanup(p.stop)
        self.ts = datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    def test_only_metered_rows_count(self):
        rows = [{"ts": self.ts, "cost_usd": 1.0, "billing": "metered"},
                {"ts": self.ts, "cost_usd": 9.0, "billing": "subscription"},
                {"ts": self.ts, "cost_usd": 2.0}, {"ts": "2000-01-01T00:00:00", "cost_usd": 50.0, "billing": "metered"}]
        self.f.write_text("".join(json.dumps(r) + "\n" for r in rows))
        self.assertEqual(self.completion.today_cost(), 3.0)
        self.completion.record_run(POINT, "claude", "opus", "metered", 0.5, "end", "p")
        self.assertEqual(self.completion.today_cost(), 3.5)
        last = json.loads(self.f.read_text().splitlines()[-1])
        self.assertEqual((last["provider"], last["project"], last["input_tokens"]), ("claude", "p", None))
        self.assertNotIn("cost_unknown", last)

    def test_a_metered_run_without_a_cost_is_marked(self):
        self.assertFalse(self.completion.unknown_cost_today())
        self.completion.record_run(POINT, "claude", "opus", "subscription", None, "", "p")
        self.assertFalse(self.completion.unknown_cost_today())
        self.completion.record_run(POINT, "claude", "opus", "metered", None, "", "p")
        self.assertTrue(self.completion.unknown_cost_today())
        self.f.write_text(json.dumps({"ts": "2000-01-01T00:00:00", "cost_unknown": True}) + "\n")
        self.assertFalse(self.completion.unknown_cost_today())


class ClaudeBilling(unittest.TestCase):
    def setUp(self):
        import runtime_claude
        self.rc = runtime_claude

    def test_only_the_token_in_a_restricted_run_is_the_plan(self):
        self.assertEqual(self.rc.billing("token", {"CLAUDE_CODE_OAUTH_TOKEN": "t"}, restricted=True), "subscription")
        self.assertEqual(self.rc.billing("subscription", {}, restricted=True), "unknown")
        self.assertEqual(self.rc.billing("token", {}, restricted=False), "unknown")

    def test_a_key_or_a_cloud_is_metered(self):
        for var in self.rc.METERED_VARS:
            with self.subTest(var):
                self.assertEqual(self.rc.billing("token", {var: "1"}, restricted=True), "metered")
        self.assertEqual(self.rc.billing("token", {"ANTHROPIC_API_KEY": ""}, restricted=True), "subscription")

    def test_another_endpoint_is_unknown(self):
        self.assertEqual(self.rc.billing("token", {"ANTHROPIC_BASE_URL": "http://x"}, restricted=True), "unknown")

    def test_a_failed_run_still_says_what_it_cost(self):
        import runtime_ports
        sys.path.insert(0, str(ROOT / "tests"))
        import store_fixture
        store_fixture.stand_in(self)   # the platform's checked copy
        task = runtime_ports.Task(point="x", prompt="p", spec={}, cwd=None)
        for stdout, want in (('{"total_cost_usd": 1.25, "is_error": true}', 1.25), ("not json", None), ("", None),
                             ('{"total_cost_usd": "lots"}', None)):
            done = subprocess.CompletedProcess([], 1, stdout=stdout, stderr="")
            with self.subTest(stdout), mock.patch.object(self.rc, "describe", lambda t: ["claude"]), \
                    mock.patch.object(self.rc, "_env", lambda t: {}), \
                    mock.patch.object(self.rc.subprocess, "run", lambda *a, **k: done):
                res = self.rc.run(task)
            self.assertFalse(res.ok)
            self.assertEqual(res.cost_usd, want)


if __name__ == "__main__":
    unittest.main()
