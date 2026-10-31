"""A project's own model calls through the platform (agenthub complete → cap-runner → llm.py project): only declared
points (registry models:), a metered (pay-per-use) provider only with the owner's metered_models, every check before
anything is sent, usage rows carrying the project (10-31).

    bin/py -m unittest tests.test_project_complete
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import completion  # noqa: E402
import registry  # noqa: E402
import runtime_fake  # noqa: E402


def load(name, file):
    loader = importlib.machinery.SourceFileLoader(name, str(BIN / file))
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


llm = load("llm_pc", "llm.py")
cr = load("cap_runner_pc", "cap-runner.py")


class ProjectCall(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.usage = self.tmp / "usage.jsonl"
        self.provider = {"impl": "fake", "models": {"fast": "m-fast", "standard": "m-std"}}
        self.llm = {"providers": {"f": self.provider}, "use": {"fast": "f", "standard": "f"}}
        self.projects = {"proj": {"name": "proj", "models": {"classify": {"tier": "fast"}}}}
        self.failed = []
        for obj, target, value in ((completion, "settings", lambda: self.llm), (completion, "USAGE", self.usage),
                                   (llm.hubconf, "projects", lambda: self.projects),
                                   (llm, "job_failed", lambda p, r: self.failed.append((p, r)))):
            p = mock.patch.object(obj, target, value)
            p.start()
            self.addCleanup(p.stop)
        runtime_fake._completions.clear()
        self.req = self.tmp / "req.json"
        self.req.write_text(json.dumps({"prompt": "is this paper relevant?", "schema": None}))

    def call(self, point="classify"):
        with redirect_stdout(io.StringIO()) as out:
            rc = llm.project_call("proj", point, self.req)
        return rc, json.loads(out.getvalue())

    def rows(self):
        return [json.loads(x) for x in self.usage.read_text().splitlines()] if self.usage.exists() else []

    def test_declared_point_goes_through_with_the_project_on_the_usage_row(self):
        rc, out = self.call()
        self.assertEqual((rc, out["is_error"]), (0, False))
        [row] = self.rows()
        self.assertEqual((row["project"], row["point"], row["model"], row["billing"]),
                         ("proj", "proj/classify", "m-fast", "subscription"))

    def test_undeclared_point_is_refused_before_sending(self):
        rc, out = self.call("summarize")
        self.assertEqual(rc, 1)
        self.assertIn("summarize", out["llm_error"])
        self.assertEqual(self.rows(), [])

    def test_metered_provider_needs_the_owners_opt_in(self):
        self.provider["key"] = {"env": "PC_TEST_KEY"}
        with mock.patch.dict(os.environ, {"PC_TEST_KEY": "k"}):
            rc, out = self.call()
            self.assertEqual(rc, 1)
            self.assertIn("metered_models", out["llm_error"])
            self.assertEqual(self.rows(), [], "refused before anything was sent")
            self.projects["proj"]["metered_models"] = True
            rc, _ = self.call()
        self.assertEqual(rc, 0)
        self.assertEqual([r["billing"] for r in self.rows()], ["metered"])

    def test_local_and_subscription_targets_are_not_metered(self):
        self.assertFalse(completion.metered({"location": "local", "conf": {"key": {"env": "X"}}}))
        self.assertFalse(completion.metered(completion.runtime_target("claude", None, "token")))
        self.assertTrue(completion.metered({"location": "cloud", "conf": {"key": {"secret": "k"}}}))

    def test_a_failing_provider_is_not_replaced(self):
        runtime_fake.set_completions("proj/classify", runtime_fake.CompletionResult(False, stop="error", error="down"))
        rc, out = self.call()
        self.assertEqual(rc, 1)
        self.assertEqual([r["provider"] for r in self.rows()], ["f"])


class StatusPage(unittest.TestCase):
    def test_subscription_cost_is_marked_not_billed_but_still_shown(self):
        page = load("status_page_pc", "gen-status-page.py")
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        usage = tmp / "usage.jsonl"
        now = page.datetime.datetime.now().astimezone()
        ts = now.isoformat(timespec="seconds")
        usage.write_text("\n".join(json.dumps(r) for r in [
            {"ts": ts, "project": "a", "cost_usd": 0.5, "billing": "subscription", "input_tokens": 10},
            {"ts": ts, "project": "a", "cost_usd": 0.25, "billing": "subscription"},
            {"ts": ts, "project": "b", "cost_usd": 1.0, "billing": "metered"},
            {"ts": ts, "project": "c", "cost_usd": None, "billing": "local"}]) + "\n")
        with mock.patch.object(completion, "USAGE", usage), mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "en"}):
            html = page.model_usage_html(now)
        rows = {r.split("</td>")[0].split("<td>")[-1]: r for r in html.split("<tr>")[2:]}
        self.assertIn("≈$0.75 (subscription, not billed)", rows["a"])
        self.assertIn("$1.00", rows["b"])
        self.assertNotIn("not billed", rows["b"])
        self.assertIn("—", rows["c"])


class Registry(unittest.TestCase):
    def test_models_validation(self):
        self.assertEqual(registry.validate_models({"models": {"classify": {"tier": "fast"}}}), [])
        self.assertTrue(registry.validate_models({"models": {"classify": {"tier": "huge"}}}))
        self.assertTrue(registry.validate_models({"models": {"Bad Name": {"tier": "fast"}}}))
        self.assertTrue(registry.validate_models({"metered_models": "yes"}))

    def test_a_project_cannot_grant_itself_metered_models(self):
        cur = {"name": "proj", "models": {"classify": {"tier": "fast"}}}
        self.assertEqual(registry.platform_owned_changes({**cur, "models": {"x": {"tier": "standard"}}}, cur), [])
        self.assertTrue(registry.platform_owned_changes({**cur, "metered_models": True}, cur))


class Runner(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws, self.state = self.tmp / "rt", self.tmp / "state"
        self.ws.mkdir()
        self.state.mkdir()
        elig = {"proj": {"dirs": [self.ws], "caps": [], "models": {"classify": {"tier": "fast"}}}}
        self.started = []

        class FakeProc:
            pid = 12345

        def popen(argv, **kw):
            self.started.append((argv, kw))
            return FakeProc()
        for target, value in (("eligible", lambda: elig), ("DIR", self.state), ("LOG", self.state / "log.jsonl"),
                              ("exclude_hub_run", lambda ws: None)):
            p = mock.patch.object(cr, target, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(cr.subprocess, "Popen", popen)
        p.start()
        self.addCleanup(p.stop)

    def put(self, job_id, **req):
        d = self.ws / ".hub-run" / "requests"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{job_id}.json").write_text(json.dumps({"v": 1, "id": job_id, "kind": "complete", **req}))

    def state_of(self, job_id):
        return json.loads((self.ws / ".hub-run" / "jobs" / job_id / "state.json").read_text())

    def test_declared_call_runs_llm_project_outside_the_sandbox_from_platform_state(self):
        self.put("c1", point="classify", prompt="hello")
        r = cr.Runner()
        r.scan()
        r.start_ready()
        [(argv, kw)] = self.started
        self.assertNotIn("sandbox-exec", " ".join(argv))
        i = argv.index("project")
        self.assertEqual(argv[i:i + 3], ["project", "proj", "classify"])
        inp = pathlib.Path(argv[argv.index("--request") + 1])
        self.assertEqual(inp.parent, self.state / "proj", "the prompt is read from platform state")
        self.assertEqual(json.loads(inp.read_text())["prompt"], "hello")
        self.assertEqual(self.state_of("c1")["state"], "running")

    def test_undeclared_call_is_rejected_by_the_runner(self):
        self.put("c2", point="other", prompt="hello")
        cr.Runner().scan()
        self.assertEqual(self.state_of("c2")["state"], "rejected")
        self.assertEqual(self.started, [])


if __name__ == "__main__":
    unittest.main()
