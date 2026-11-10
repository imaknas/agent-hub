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
        with mock.patch.object(self.llm, "point_runtime", lambda point, spec: "fake"), \
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
