"""The waiting report (bin/test-waiting-report.py; docs/design/test-waiting.md B7): commands classified by the declared
tiers before patterns, waits paired call → result and counted once, background runs never waited on, subagents apart,
agent and model time by section 1's gaps, the owner's wait, e2e runs as safety or inner-loop work, the runner's run log
summarized per tier and caller (its lines are a project's data: coerced), the test-runs op that reads it as the project,
and the status page's section.

    bin/py -m unittest discover -s tests
"""
import datetime
import importlib.util
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
import runtime_fake  # noqa: E402
from runtime_ports import Action, Turn  # noqa: E402

PY = os.environ.get("AGENT_HUB_PYTHON") or sys.executable
UTC = datetime.timezone.utc
T0 = datetime.datetime(2026, 11, 1, 10, tzinfo=UTC)


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def at(minutes: float) -> datetime.datetime:
    return T0 + datetime.timedelta(minutes=minutes)


def run(call, cmd, background=False):
    return Action("run_command", call=call, command=cmd, background=background)


DOC = {"v": 2, "commit_gate": True,
       "tests": [{"tier": "full", "run": "pytest -n {jobs} tests", "when": ["merge"]},
                 {"tier": "e2e", "run": "npx playwright test", "when": ["nightly"]}]}


class Classify(unittest.TestCase):
    def setUp(self):
        self.m = load("twr_c", "test-waiting-report.py")
        self.tiers = self.m.tier_patterns(DOC)

    def kind(self, cmd, doc=DOC):
        return self.m.classify(cmd, doc, self.m.tier_patterns(doc))

    def test_order_gates_then_tiers_then_patterns(self):
        self.assertEqual(self.kind("bin/deploy.sh && pytest"), "deploy")
        self.assertEqual(self.kind("pytest -q && agenthub merge-branch --note x"), "merge")
        self.assertEqual(self.kind("git -C /x commit -m 'a'"), "commit")
        self.assertEqual(self.kind("agenthub test --tier full"), "runner")
        self.assertEqual(self.kind("cd w &&  pytest -n 8   tests"), "tier:full", "a declared run, {jobs} any count")
        self.assertEqual(self.kind("npx playwright test"), "tier:e2e", "a declared tier before the e2e pattern")
        self.assertEqual(self.kind("npm run test:e2e"), "e2e")
        self.assertEqual(self.kind("pytest tests/test_a.py -q"), "targeted")
        self.assertIsNone(self.kind("ls -la"))

    def test_a_commit_is_a_gate_only_where_the_repo_has_one(self):
        self.assertIsNone(self.kind("git commit -m x", {"v": 2}))


class Sessions(unittest.TestCase):
    def setUp(self):
        self.m = load("twr_s", "test-waiting-report.py")
        self.tiers = self.m.tier_patterns(DOC)

    def tally(self, turns, subagent=False):
        t = self.m.Tally()
        self.m.session(t, list(turns), DOC, self.tiers, subagent, set())
        return t.out()

    def test_waits_paired_and_capped_and_background_not_waited(self):
        out = self.tally([
            Turn(at(0), "s", "human", "go"),
            Turn(at(1), "s", "assistant", actions=(run("c1", "pytest -n 4 tests"),)),
            Turn(at(3), "s", "other", results=("c1",)),
            Turn(at(4), "s", "assistant", actions=(run("c2", "npm run test:e2e"),)),
            Turn(at(40), "s", "other", results=("c2",)),          # 36 minutes: capped at 20
            Turn(at(41), "s", "assistant", actions=(run("c3", "pytest tests", background=True),)),
            Turn(at(41.1), "s", "other", results=("c3",)),
            Turn(at(42), "s", "assistant", actions=(run("c4", "ls"),)),
            Turn(at(43), "s", "other", results=("c4",)),
        ])
        self.assertEqual(out["by_kind"], {"tier:full": {"min": 2.0, "runs": 1}, "e2e": {"min": 20.0, "runs": 1}})
        self.assertEqual(out["tests_min"], 22.0)
        self.assertEqual(out["background_runs"], 1)
        # gaps: 1 (model) + 2 (tool) + 1 + 20 (capped) + 1 + 0.1 + 0.9 + 1, the person's message starting nothing
        self.assertEqual(out["agent_min"], 27.0)
        self.assertEqual(out["model_min"], 3.9)

    def test_subagents_apart_and_not_agent_time(self):
        out = self.tally([Turn(at(0), "s", "assistant", actions=(run("c1", "pytest tests/x.py"),), subagent=True),
                          Turn(at(5), "s", "other", results=("c1",), subagent=True)], subagent=True)
        self.assertEqual((out["tests_min"], out["subagents"]["tests_min"], out["agent_min"]), (0, 5.0, 0))

    def test_the_owner_waited_only_on_a_turn_they_answered_soon(self):
        watched = [Turn(at(0), "s", "human", "go"),
                   Turn(at(1), "s", "assistant", actions=(run("c1", "pytest x"),)),
                   Turn(at(4), "s", "other", results=("c1",)),
                   Turn(at(5), "s", "assistant", text=""),
                   Turn(at(8), "s", "human", "next")]                    # 3 minutes after the turn ended
        self.assertEqual(self.tally(watched)["owner_waited_min"], 3.0)
        away = watched[:-1] + [Turn(at(60), "s", "human", "back")]
        self.assertEqual(self.tally(away)["owner_waited_min"], 0)

    def test_e2e_runs_as_safety_or_inner_loop(self):
        out = self.tally([
            Turn(at(0), "s", "assistant", actions=(run("a", "npm run test:e2e"),)),
            Turn(at(1), "s", "other", results=("a",)),
            Turn(at(2), "s", "assistant", actions=(Action("write_file", call="w"),)),
            Turn(at(3), "s", "assistant", actions=(run("b", "npm run test:e2e"),)),
            Turn(at(4), "s", "other", results=("b",)),
            Turn(at(5), "s", "assistant", actions=(run("m", "agenthub merge-branch --note x"),)),
            Turn(at(6), "s", "other", results=("m",)),
            Turn(at(7), "s", "assistant", actions=(run("c", "npm run test:e2e"),)),
            Turn(at(8), "s", "other", results=("c",)),
        ])
        self.assertEqual(out["e2e"], {"safety": 1, "inner": 1, "other": 1})


class Scan(unittest.TestCase):
    """Through the transcripts port (a fake runtime): copies counted once, a session attributed to one project."""

    def setUp(self):
        runtime_fake.reset()
        self.addCleanup(runtime_fake.reset)
        self.m = load("twr_scan", "test-waiting-report.py")
        for mod, name, fake in ((self.m.runtime_ports, "load", lambda *a, **k: runtime_fake),
                                (self.m.hublib, "project_paths", lambda: {"proj": ["/w/proj"]}),
                                (self.m.hubconf, "projects", lambda: {"proj": DOC}),
                                (self.m.project_git, "project_at", lambda d: ("proj", DOC) if d else None)):
            self.addCleanup(setattr, mod, name, getattr(mod, name))
            setattr(mod, name, fake)

    def test_copies_counted_once(self):
        turns = [Turn(at(1), "s", "assistant", actions=(run("c1", "pytest x"),), id="u1", folder="/w/proj"),
                 Turn(at(3), "s", "other", results=("c1",), id="u2", folder="/w/proj")]
        for t in turns + turns:                                         # a resumed session's copy
            runtime_fake.add_turn("/w/proj", t)
        out = self.m.scan(at(0), at(60))
        self.assertEqual(out["proj"]["by_kind"], {"targeted": {"min": 2.0, "runs": 1}})
        self.assertEqual(out["proj"]["sessions"], 1)


class RunnerSummary(unittest.TestCase):
    def test_tiers_and_callers(self):
        m = load("twr_r", "test-waiting-report.py")
        recs = [{"run": "a", "project": "p", "tier": "full", "result": "pass", "secs": 30, "total": 33,
                 "caller": "merge-branch"},
                {"run": "b", "project": "p", "tier": "full", "result": "hit", "secs": 0, "total": 1,
                 "caller": "merge-branch"},
                {"run": "c", "project": "p", "tier": "full", "result": "fail", "secs": "x", "total": None,
                 "caller": "session"},
                "not a line"]
        out = m.runner_summary([r for r in recs if isinstance(r, dict)])
        self.assertEqual(out["tiers"]["p/full"], {"runs": 3, "hits": 1, "fails": 1, "run_min": 0.5, "median_s": 15.0,
                                                   "p90_s": 30.0})
        self.assertEqual(out["callers"]["merge-branch"]["invocations"], 2)
        self.assertEqual(out["callers"]["merge-branch"]["all_hits"], 1)
        self.assertEqual(out["callers"]["merge-branch"]["median_s"], 17.0)
        self.assertEqual(out["callers"]["session"]["failed"], 1)


class TestRunsOp(unittest.TestCase):
    """project-git-job.py test-runs: the run log since a time, from the repo's git common dir; never through a link."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        subprocess.run(["git", "init", "-q", str(self.tmp / "r")], check=True)
        self.log = self.tmp / "r" / ".git" / "agent-hub" / "test-runs.jsonl"
        self.log.parent.mkdir(parents=True)

    def op(self, since):
        r = subprocess.run([PY, str(ROOT / "bin" / "project-git-job.py"), "test-runs", str(self.tmp / "r"), since],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_since_and_links(self):
        self.log.write_text('{"at": "2026-11-01T10:00:00+08:00", "tier": "old"}\n'
                            '{"at": "2026-11-02T10:00:00+08:00", "tier": "new"}\nnot json\n')
        self.assertEqual([r["tier"] for r in self.op("2026-11-02T00:00:00+08:00")], ["new"])
        target = self.tmp / "elsewhere.jsonl"
        self.log.rename(target)
        self.log.symlink_to(target)
        self.assertEqual(self.op("2026-11-01T00:00:00+08:00"), [], "a link in the log's place is not followed")


class StatusPage(unittest.TestCase):
    def test_section_from_the_newest_report(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        m = load("gsp_waiting", "gen-status-page.py")
        with mock.patch.object(m.hubconf, "data_file", lambda name: tmp / name):
            self.assertEqual(m.waiting_html(), "", "no report yet: no section")
            (tmp / "test-waiting").mkdir()
            rep = {"v": 1, "days": 7, "to": "2026-11-02T00:00:00+08:00", "projects": {
                "quiet": {"sessions": {"tests_min": 0, "agent_min": 10}, "runner": None},
                "busy": {"sessions": {"tests_min": 30, "agent_min": 120, "owner_waited_min": 12},
                         "runner": {"tiers": {"busy/full": {"runs": 4, "hits": 1}},
                                    "callers": {"merge-branch": {"median_s": 31.5}}}},
                "<odd>": {"sessions": None, "runner": {"unavailable": "sandbox refused"}}}}
            (tmp / "test-waiting" / "2026-11-02.json").write_text(json.dumps(rep))
            html = m.waiting_html()
        self.assertIn("busy", html)
        self.assertIn("(25%)", html)
        self.assertIn("31.5", html)
        self.assertIn("&lt;odd&gt;", html)
        self.assertNotIn("quiet", html)


if __name__ == "__main__":
    unittest.main()
