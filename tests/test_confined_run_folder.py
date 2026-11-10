"""A confined run's own folder and what no confined run writes (docs/design/confined-mail-tools.md, "Depends on" and M7;
docs/design/project-agent-calls.md A2).

- llm.py makes `<state>/agent-jobs/.settings/<project>.<point>.<run id>/` for each run of a `settings: platform` point
  that names its project, and removes it on every way out; the runtime writes the run's settings file there.
- The settings deny Edit and Write on every `.git` under each folder the run works in, the project's git common
  directory, and the platform's request folder; a rule whose link leads into one refuses the run.

No agent is started.

    bin/py -m unittest tests.test_confined_run_folder
"""
import importlib.util
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hubconf  # noqa: E402

try:
    import yaml
except ImportError:
    yaml = None

SPEC = {"settings": "platform", "project_env": "AGENT_JOB_PROJECT"}


def load_llm(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / "llm.py")
    llm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(llm)
    return llm


def tmpdir(test) -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp()).resolve()
    test.addCleanup(shutil.rmtree, d, True)
    return d


@unittest.skipIf(yaml is None, "needs PyYAML")
class RunFolder(unittest.TestCase):
    def setUp(self):
        self.llm = load_llm("llm_run_folder")
        self.state = tmpdir(self)
        p = mock.patch.object(hubconf, "STATE", self.state)
        p.start()
        self.addCleanup(p.stop)

    def test_two_runs_of_one_project_get_two_folders(self):
        with mock.patch.dict(os.environ, {"AGENT_JOB_PROJECT": "p"}):
            a, b = self.llm.make_run_dir("agent-job-confined", SPEC), self.llm.make_run_dir("agent-job-confined", SPEC)
        self.assertNotEqual(a, b)
        for d in (a, b):
            self.assertTrue(d.is_dir())
            self.assertEqual(d.parent, self.state / "agent-jobs" / ".settings")
            self.assertTrue(d.name.startswith("p.agent-job-confined."))
            self.assertEqual(d.stat().st_mode & 0o777, 0o700)

    def test_none_without_a_project_or_for_an_unconfined_point(self):
        for env, spec in (({"AGENT_JOB_PROJECT": ""}, SPEC), ({"AGENT_JOB_PROJECT": "p"}, {"project_env": "AGENT_JOB_PROJECT"}),
                          ({"AGENT_JOB_PROJECT": "../x"}, SPEC), ({"AGENT_JOB_PROJECT": ".x"}, SPEC)):
            with self.subTest(env=env, spec=spec), mock.patch.dict(os.environ, env):
                self.assertIsNone(self.llm.make_run_dir("agent-job-confined", spec))
        self.assertFalse((self.state / "agent-jobs" / ".settings").exists() and
                         any((self.state / "agent-jobs" / ".settings").iterdir()))

    def test_what_a_crash_left_is_removed(self):
        base = self.state / "agent-jobs" / ".settings"
        base.mkdir(parents=True)
        old, fresh, old_file = base / "p.x.0011223344556677", base / "p.x.8899aabbccddeeff", base / "p.json"
        old.mkdir()
        fresh.mkdir()
        old_file.write_text("{}")
        day_ago = time.time() - 2 * 86400
        for f in (old, old_file):
            os.utime(f, (day_ago, day_ago))
        with mock.patch.dict(os.environ, {"AGENT_JOB_PROJECT": "p"}):
            self.llm.make_run_dir("agent-job-confined", SPEC)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(old_file.exists(), "only run folders are swept")

    def run_llm(self, describe):
        """llm.run as a dry run of agent-job-confined on the fake runtime, its describe replaced."""
        import runtime_fake
        seen = []

        def recording(task):
            seen.append(task.run_dir)
            self.assertTrue(task.run_dir and pathlib.Path(task.run_dir).is_dir())
            return describe(task)
        runtime_fake.set_ambient(None)
        a = types.SimpleNamespace(cmd="run", point="agent-job-confined", cwd=None, model=None, prompt_file=None,
                                  dry_run=True, extra=[])
        import registry
        with mock.patch.object(self.llm, "point_runtime", lambda point, spec: "fake"), \
                mock.patch.object(registry, "refresh", lambda: {"projects": {"p": {"name": "p"}}}), \
                mock.patch.object(self.llm, "auth_env", lambda spec, rt: (dict(os.environ), None)), \
                mock.patch.object(runtime_fake, "describe", recording), \
                mock.patch.dict(os.environ, {"AGENT_JOB_PROJECT": "p", "AGENT_JOB_ALLOW": '["Read"]'}), \
                mock.patch("sys.stdin", io.StringIO("x")), mock.patch("sys.stdout", io.StringIO()), \
                mock.patch("sys.stderr", io.StringIO()):
            code = self.llm.run(a)
        return code, seen

    def test_every_way_out_removes_it(self):
        def boom(task):
            raise ValueError("the adapter refused")
        for describe, code in ((lambda task: ["fake"], 0), (boom, 2)):
            with self.subTest(code=code):
                got, seen = self.run_llm(describe)
                self.assertEqual(got, code)
                self.assertEqual(len(seen), 1)
                self.assertFalse(pathlib.Path(seen[0]).exists())

    def test_a_confined_point_without_its_project_makes_none(self):
        a = types.SimpleNamespace(cmd="run", point="agent-job-confined", cwd=None, model=None, prompt_file=None,
                                  dry_run=True, extra=[])
        with mock.patch.dict(os.environ, {"AGENT_JOB_PROJECT": ""}), mock.patch("sys.stdin", io.StringIO("x")), \
                mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            self.llm.run(a)
        base = self.state / "agent-jobs" / ".settings"
        self.assertFalse(base.exists() and any(base.iterdir()))


@unittest.skipIf(yaml is None, "needs PyYAML")
class RuleCheck(unittest.TestCase):
    """The rule_check port (project-agent-calls.md A2): a confined run's own rules, checked when it starts."""

    def problems(self, rules, sandboxed=False, network=(), cwd=None):
        import runtime_claude
        return runtime_claude.rules_problems(rules, sandboxed=sandboxed, network=list(network), untrusted=False, cwd=cwd)

    def test_paths_are_relative_to_the_run(self):
        for bad in ("Edit(~/x/**)", "Read(//etc/**)", "Edit(/x/**)", "Read(./../other/**)", "Edit(../x)"):
            with self.subTest(bad):
                self.assertEqual(len(self.problems([bad])), 1)
        self.assertEqual(self.problems(["Read(data/**)", "Edit(./out/**)", "Glob(*)", "Read", "Bash(git log *)"]), [])

    def test_a_jobs_own_state_folder_may_be_named_by_its_full_path(self):
        state = tmpdir(self)
        job = state / "agent-jobs" / "p-check"
        job.mkdir(parents=True)
        with mock.patch.object(hubconf, "STATE", state):
            self.assertEqual(self.problems([f"Edit(/{job}/**)"], cwd=job), [])
            self.assertEqual(len(self.problems([f"Edit(/{state}/**)"], cwd=job)), 1, "beyond its own folder")
            self.assertEqual(len(self.problems([f"Edit(/{job}/**)"], cwd=state)), 1, "not a job's folder")

    def test_git_and_the_request_folder(self):
        for bad in ("Edit(./.git/config)", "Write(./sub/.GIT/**)", "Edit(.hub-run/**)", "Edit(./.Hub-Run/x)"):
            with self.subTest(bad):
                self.assertEqual(len(self.problems([bad])), 1)
        self.assertEqual(self.problems(["Edit(./**/x)"]), [], "a glob part isn't checked here: the deny covers it")

    def test_a_sandboxed_project_names_only_files_and_its_own_domains(self):
        ok = ["Read", "Glob(src/**)", "Edit(./out/**)", "Write(./out/**)", "MultiEdit", "NotebookEdit",
              "WebFetch(domain:api.example.org)"]
        self.assertEqual(self.problems(ok, sandboxed=True, network=["api.example.org"]), [])
        for bad in ("Bash(ls *)", "WebFetch", "WebFetch(domain:github.com)", "WebSearch", "mcp__x__y", "Task"):
            with self.subTest(bad):
                self.assertEqual(len(self.problems([bad], sandboxed=True, network=["api.example.org"])), 1)
            self.assertEqual(self.problems([bad]), [], "allowed for a project that isn't sandboxed")

    def test_the_mail_handlers_rules_pass_today_in_a_project_that_isnt_sandboxed(self):
        import runtime_claude
        import runtime_ports
        pts = yaml.safe_load((ROOT / "contracts" / "llm-points.yaml").read_text())["points"]
        for point in ("mailbox-escalation", "mailbox-conversation"):
            task = runtime_ports.Task(point, "x", pts[point], env={"MAILBOX_PROJECT": "p"})
            self.assertEqual(runtime_claude.rule_problems(task, sandboxed=False, network=[], untrusted=False), [], point)

    def test_llm_refuses_a_run_whose_rules_are_refused(self):
        import registry
        import runtime_fake
        llm = load_llm("llm_rule_refusal")
        runtime_fake.set_rule_problems(["Bash(x) is not allowed here"])
        self.addCleanup(runtime_fake.set_rule_problems, [])
        task = types.SimpleNamespace(env={"AGENT_JOB_PROJECT": "p"})
        with mock.patch.object(registry, "refresh", lambda: {"projects": {"p": {"name": "p"}}}):
            why = llm.rule_refusal("agent-job-confined", SPEC, runtime_fake, task)
            self.assertIn("Bash(x) is not allowed here", why)
            runtime_fake.set_rule_problems([])
            self.assertIsNone(llm.rule_refusal("agent-job-confined", SPEC, runtime_fake, task))
            self.assertIsNone(llm.rule_refusal("agent-job", {"project_env": "AGENT_JOB_PROJECT"}, runtime_fake, task))
        with mock.patch.object(registry, "refresh", lambda: {"projects": {}}):
            self.assertTrue(llm.rule_refusal("agent-job-confined", SPEC, runtime_fake, task), "not in the registry")

    def test_the_registry_lists_what_a_job_would_refuse(self):
        import registry
        index = {"projects": {
            "p": {"name": "p", "enforce": {"sandbox": True}, "jobs": [
                {"name": "a", "kind": "agent", "allow": ["Read", "Bash(ls *)"]},
                {"name": "b", "kind": "agent", "allow": ["Read"]}]},
            "q": {"name": "q", "jobs": [{"name": "c", "kind": "agent", "allow": ["Bash(ls *)"]},
                                        {"name": "d", "kind": "command", "run": "x"}]}}}
        with mock.patch("runtime_ports.default_name", lambda: "claude"):
            out = registry.rules_refused(index)
        self.assertEqual(sorted(out), ["p/a"])


@unittest.skipIf(yaml is None, "needs PyYAML")
class Untrusted(unittest.TestCase):
    """confined-mail-tools.md M6: a run whose prompt anyone can write (an event job, a mail handler)."""

    def problems(self, rules, untrusted=True, sandboxed=False, network=()):
        import runtime_claude
        return runtime_claude.rules_problems(rules, sandboxed=sandboxed, network=list(network), untrusted=untrusted)

    def test_it_gets_a_sandboxed_projects_list_in_every_project(self):
        ok = ["Read", "Glob(src/**)", "Edit(./out/**)", "WebFetch(domain:api.example.org)"]
        self.assertEqual(self.problems(ok, network=["api.example.org"]), [])
        for bad in ("Bash(git log *)", "WebFetch", "WebFetch(domain:github.com)", "WebSearch", "Task", "mcp__db__query"):
            with self.subTest(bad):
                self.assertEqual(len(self.problems([bad], network=["api.example.org"])), 1)
                self.assertEqual(self.problems([bad], untrusted=False), [], "a clock job keeps it")

    def test_no_confined_run_names_the_cli_or_the_platforms_server(self):
        for bad in ("Bash(brunna send *)", "Bash(agenthub complete *)", "Bash(~/.local/bin/brunna cap-run *)",
                    "Bash(/opt/hub/bin/hub *)", "Bash(sh -c 'brunna send x')", "Bash(/opt/hub/bin/hubbus *)",
                    "Bash(BRUNNA send *)", "mcp__brunna-run__send", "mcp__agenthub-run", "mcp__Brunna-run__note"):
            with self.subTest(bad):
                self.assertTrue(self.problems([bad], untrusted=False))
        self.assertEqual(self.problems(["Bash(git log *)", "Bash(.venv/bin/python tools/x.py *)", "mcp__db__query",
                                        "Bash(hubble *)"], untrusted=False), [])

    def deny(self, ws, spec):
        import runtime_claude
        pol = {"other_projects": [], "deny_write": [], "deny_read": [], "platform_commands": [], "platform_cli": [],
               "write": [str(ws)], "network": [], "credential_files": [], "credentials": [], "workspace": str(ws)}
        doc = {"name": "p", "paths": {"workspace": str(ws)}}
        with mock.patch("policy.policy", lambda name, d, i: pol), mock.patch("registry.refresh", lambda: {"projects": {"p": doc}}), \
                mock.patch.object(runtime_claude, "project_roots", lambda p: [ws]), \
                mock.patch("policy.notes_dir", lambda p: ws / ".agent-memory" / "memory"):
            f = runtime_claude.platform_settings("p", spec, tmpdir(self), None, ws)
        return json.loads(f.read_text())["permissions"]["deny"]

    def test_it_writes_nothing_read_as_instructions(self):
        import runtime_claude
        ws = tmpdir(self)
        (ws / ".agent-memory" / "memory").mkdir(parents=True)
        (ws / ".agent-memory" / "AGENT.md").write_text("x")
        (ws / ".agent-memory" / "extra.md").write_text("x")
        rule = runtime_claude._rule_path
        deny = self.deny(ws, {"prompt_source": "untrusted"})
        for want in (f"Edit({rule(ws)}/**/CLAUDE.md)", f"Write({rule(ws)}/CLAUDE.md)", f"Edit({rule(ws)}/**/AGENTS.md)",
                     f"Edit({rule(ws)}/**/.claude/rules/**)", f"Write({rule(ws)}/.claude/rules/**)",
                     f"Edit({rule(ws / '.agent-memory' / 'STATE.md')})", f"Write({rule(ws / '.agent-memory' / 'STATE.md')})",
                     f"Edit({rule(ws / '.agent-memory' / '.git')})", f"Edit({rule(ws / '.agent-memory' / 'extra.md')})"):
            self.assertIn(want, deny)
        self.assertFalse([r for r in deny if "/.agent-memory/memory" in r], "its notes stay writable")
        clock = self.deny(ws, {})
        self.assertNotIn(f"Edit({rule(ws)}/**/CLAUDE.md)", clock, "a clock job's prompt is the project's own")

    def test_in_a_worktree_its_notes_stay_writable(self):
        """A worktree's .agent-memory is a link to the main checkout's (worktree-memory.sh)."""
        import runtime_claude
        ws, wt = tmpdir(self), tmpdir(self)
        (ws / ".agent-memory" / "memory").mkdir(parents=True)
        (ws / ".agent-memory" / "STATE.md").write_text("x")
        (wt / ".agent-memory").symlink_to(ws / ".agent-memory")
        with mock.patch.object(runtime_claude, "project_roots", lambda p: [ws]), \
                mock.patch("policy.notes_dir", lambda p: ws / ".agent-memory" / "memory"):
            deny = runtime_claude.instructions_denied("p", [wt])
        self.assertFalse([r for r in deny if "/.agent-memory/memory" in r], deny)
        self.assertIn(f"Edit({runtime_claude._rule_path(ws / '.agent-memory' / 'STATE.md')})", deny)

    def test_the_registry_holds_an_event_job_to_it(self):
        import registry
        index = {"projects": {"q": {"name": "q", "jobs": [
            {"name": "e", "kind": "agent", "trigger": {"events": ["merge.done"]}, "allow": ["Read", "Bash(make *)"]},
            {"name": "c", "kind": "agent", "at": "08:00", "allow": ["Read", "Bash(make *)"]}]}}}
        with mock.patch("runtime_ports.default_name", lambda: "claude"):
            self.assertEqual(sorted(registry.rules_refused(index)), ["q/e"])


@unittest.skipIf(yaml is None, "needs PyYAML")
class GitDenied(unittest.TestCase):
    """M7: open before, a confined run's file tools could write a project's .git (a sandboxed project's outbox, or
    .git/config, whose core.fsmonitor the platform's next git command there runs as the user)."""

    def setUp(self):
        self.ws = tmpdir(self)
        (self.ws / ".git").mkdir()
        self.extra = tmpdir(self)
        self.run_dir = tmpdir(self)

    def deny(self, doc=None, folders=()):
        import runtime_claude
        pol = {"other_projects": [], "deny_write": [], "deny_read": [], "platform_commands": [], "platform_cli": [],
               "write": [str(self.ws)], "network": [], "credential_files": [], "credentials": [], "workspace": str(self.ws)}
        doc = doc or {"name": "p", "paths": {"workspace": str(self.ws)}}
        with mock.patch("policy.policy", lambda name, d, i: pol), mock.patch("registry.refresh", lambda: {"projects": {"p": doc}}), \
                mock.patch.object(runtime_claude, "project_roots", lambda p: [self.ws]):
            f = runtime_claude.platform_settings("p", {}, self.run_dir, None, self.ws, folders=folders)
        self.assertEqual(f.parent, self.run_dir)
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        return json.loads(f.read_text())["permissions"]["deny"]

    def test_every_git_under_every_folder_it_works_in(self):
        import runtime_claude
        rule = runtime_claude._rule_path
        deny = self.deny(folders=[self.extra])
        for folder in (self.ws, self.extra):
            for tail in ("/.git", "/.git/**", "/**/.git", "/**/.git/**"):
                for tool in ("Edit", "Write"):
                    self.assertIn(f"{tool}({rule(folder)}{tail})", deny)
        for tool in ("Edit", "Write"):
            self.assertIn(f"{tool}({rule(self.ws / '.hub-run')}/**)", deny)

    def test_the_git_common_directory_of_a_linked_worktree(self):
        import runtime_claude
        main = tmpdir(self)
        wt_git = main / ".git" / "worktrees" / "w"
        wt_git.mkdir(parents=True)
        (wt_git / "commondir").write_text("../..\n")
        shutil.rmtree(self.ws / ".git")
        (self.ws / ".git").write_text(f"gitdir: {wt_git}\n")
        deny = self.deny()
        common = runtime_claude._rule_path(main / ".git")
        for tool in ("Edit", "Write"):
            self.assertIn(f"{tool}({common})", deny)
            self.assertIn(f"{tool}({common}/**)", deny)

    def test_writing_the_settings_needs_a_run_folder(self):
        import runtime_claude
        with self.assertRaises(ValueError):
            runtime_claude.platform_settings("p", {}, None)

    def test_a_link_into_git_refuses_the_run(self):
        import runtime_claude
        (self.ws / ".git" / "brunna").mkdir()
        (self.ws / ".hub-run").mkdir()
        for target in (".git/brunna", ".GIT/brunna", ".Git", ".hub-run", ".HUB-RUN"):
            with self.subTest(target=target):
                link = self.ws / "out"
                if link.is_symlink():
                    link.unlink()
                link.symlink_to(self.ws / target)
                with self.assertRaises(ValueError):
                    runtime_claude._linked_rules(["Edit(./out/**)"], self.ws, [self.ws])
        # a plain folder is fine
        (self.ws / "out").unlink()
        (self.ws / "data").mkdir()
        self.assertEqual(runtime_claude._linked_rules(["Edit(./data/**)"], self.ws, [self.ws]), ([], []))

    def test_notes_inside_a_git_refuse_the_run(self):
        import runtime_claude
        with mock.patch("policy.notes_dir", lambda p: self.ws / ".git" / "memory"):
            with self.assertRaises(ValueError):
                runtime_claude._notes_rules([runtime_claude.MEMORY_RULE], "p", self.ws)


if __name__ == "__main__":
    unittest.main()
