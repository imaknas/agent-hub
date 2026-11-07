"""What a platform run's agent does in a project's folder outside any sandbox (docs/design/mailbox-session-first.md, step 3's record).

`claude -p` runs `git status` of its own at its start, and a sandboxed project writes its own repo's config, so a clean filter there ran
as the user. Every way the platform starts it carries the variable that stops that, and each new version gets a real probe, since
the variable is a tool's. Real runs of the CLI are in the step record; here a stand-in `claude` that notes what it was given. Where a
fork starts, and whose files the settings pass writes, are the same question for a remembered folder.

    bin/run-tests        (running this module directly in a worktree reads the real config through .hub-home)
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import runtime_claude  # noqa: E402
import runtime_ports  # noqa: E402
import test_bus_sender as tbs  # noqa: E402
import test_mailbox_dispatch as tmd  # noqa: E402
import test_worktree_pointer as twp  # noqa: E402

VAR = "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS"


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    m = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(m)
    return m


class StandIn(unittest.TestCase):
    """A `claude` that notes its environment and folder, and answers as the real one does."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.seen = self.tmp / "seen.json"
        exe = self.tmp / "claude"
        exe.write_text(f"#!{sys.executable}\nimport json, os\n"
                       f"json.dump({{'var': os.environ.get({VAR!r}), 'cwd': os.getcwd()}}, open({str(self.seen)!r}, 'w'))\n"
                       "print(json.dumps({'type': 'result', 'result': 'ok', 'is_error': False, 'session_id': 's', 'num_turns': 1}))\n")
        exe.chmod(0o755)
        p = mock.patch.object(hubconf, "tool", lambda name: str(exe))
        p.start()
        self.addCleanup(p.stop)

    def var(self):
        return json.loads(self.seen.read_text())["var"]

    def test_every_point_of_the_agent_shape_carries_it(self):
        import llm
        import yaml
        points = yaml.safe_load((ROOT / "contracts" / "llm-points.yaml").read_text())
        points = points.get("points", points)
        ran = 0
        for point, spec in points.items():
            if not isinstance(spec, dict) or spec.get("shape") != "agent":
                continue
            with self.subTest(point=point):
                self.seen.unlink(missing_ok=True)
                env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.tmp), "MAILBOX_PROJECT": "x", "AGENT_JOB_PROJECT": "x",
                       "AGENT_JOB_ALLOW": "[]", "LEASE_MODE": "discuss", "LEASE_WS": str(self.tmp), "LEASE_PROJECT": "x"}
                task = runtime_ports.Task(point=point, prompt="p", spec=spec, cwd=str(self.tmp), env=env)
                try:
                    runtime_claude.run(task)
                except (ValueError, KeyError):   # a point whose flags need what only its caller has: its env is the same _env
                    self.assertEqual(runtime_claude._env(task)[VAR], "1")
                    continue
                self.assertEqual(self.var(), "1")
                ran += 1
        self.assertGreater(ran, 3, "the stand-in really ran")
        self.assertTrue(llm)

    def test_the_other_ways_it_starts(self):
        task = runtime_ports.Task(point="x", prompt="p", spec={}, cwd=str(self.tmp), env={"PATH": os.environ.get("PATH", "")})
        with mock.patch.object(runtime_claude.os, "execvpe") as ex:
            runtime_claude.exec_stream(task)
        self.assertEqual(ex.call_args[0][2][VAR], "1", "a lease's long-running session")
        runtime_claude.session_status("s")
        self.assertEqual(self.var(), "1", "the agents listing")
        self.seen.unlink()
        with mock.patch.object(runtime_claude, "auth_env", lambda mode, env: (dict(env), "")):
            runtime_claude.complete(runtime_ports.CompletionRequest(point="x", prompt="p"), {})
        self.assertEqual(self.var(), "1", "the completion path")

    def test_it_overrides_what_a_caller_set(self):
        task = runtime_ports.Task(point="x", prompt="p", spec={}, env={VAR: "0", "PATH": ""})
        self.assertEqual(runtime_claude._env(task)[VAR], "1")

    def test_a_sandboxed_projects_sessions_get_it_too(self):
        self.assertEqual(runtime_claude.session_env({"env": {"A": "b"}}), {"A": "b", VAR: "1"})
        self.assertEqual(runtime_claude.session_env({})[VAR], "1")


class Probe(unittest.TestCase):
    def setUp(self):
        self.m = load("update_claude_probe", BIN / "update-claude.py")

    def test_a_run_that_leaves_the_filter_alone(self):
        self.assertIsNone(self.m.filter_probe(run=lambda repo: None))

    def test_a_run_that_runs_git_status_is_said(self):
        def run(repo):
            subprocess.run(["git", "-C", str(repo), "--no-optional-locks", "status", "--short"], capture_output=True,
                           env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull})
        self.assertIn("NO_FOLDER_GIT", self.m.filter_probe(run=run))

    def test_a_run_that_couldnt_be_made_is_said(self):
        self.assertIn("no login", self.m.filter_probe(run=lambda repo: "no login"))

    def test_a_probe_whose_control_shows_nothing_is_said(self):
        real = pathlib.Path.write_text

        def no_attributes(path, *a, **k):
            return 0 if path.name == "attributes" else real(path, *a, **k)
        with mock.patch.object(pathlib.Path, "write_text", no_attributes):
            why = self.m.filter_probe(run=lambda repo: self.fail("never got this far"))
        self.assertIn("git status", why)

    def state(self, tmp):
        return mock.patch.object(self.m, "PROBED", pathlib.Path(tmp) / "probed.json")

    def test_once_per_version(self):
        with tempfile.TemporaryDirectory() as tmp, self.state(tmp), mock.patch.object(self.m.hubconf, "nvm_bin", lambda: pathlib.Path(tmp)), \
                mock.patch.object(self.m.alert, "once") as once, mock.patch.object(self.m, "version", lambda c: "9.9.9"), \
                mock.patch.object(self.m, "filter_probe", return_value=None) as probe:
            self.m.probe_new_version()
            self.m.probe_new_version()
            self.assertEqual(probe.call_count, 1, "one small request per version")
            self.assertEqual(once.call_args[0][1], [])
        with tempfile.TemporaryDirectory() as tmp, self.state(tmp), mock.patch.object(self.m.hubconf, "nvm_bin", lambda: pathlib.Path(tmp)), \
                mock.patch.object(self.m.alert, "once"), mock.patch.object(self.m, "filter_probe", return_value=None) as probe:
            for v in ("1.0.0", "1.0.1"):
                with mock.patch.object(self.m, "version", lambda c, v=v: v):
                    self.m.probe_new_version()
            self.assertEqual(probe.call_count, 2, "whoever installed it: it goes by the version")

    def test_a_filter_that_ran_alerts_and_is_tried_again(self):
        with tempfile.TemporaryDirectory() as tmp, self.state(tmp), mock.patch.object(self.m.hubconf, "nvm_bin", lambda: pathlib.Path(tmp)), \
                mock.patch.object(self.m.alert, "once") as once, mock.patch.object(self.m, "version", lambda c: "9.9.9"), \
                mock.patch.object(self.m, "filter_probe", return_value="it ran") as probe:
            self.m.probe_new_version()
            self.assertEqual(once.call_args[0][0], "claude-git-filter")
            self.assertIn("it ran", once.call_args[0][1][0])
            self.m.probe_new_version()
            self.assertEqual(probe.call_count, 2, "not marked as probed")

    def test_the_job_runs_it_and_a_dry_run_doesnt(self):
        with mock.patch.object(self.m, "update", lambda dry: (True, "x")), mock.patch.object(self.m.alert, "once"), \
                mock.patch.object(self.m, "probe_new_version") as probe:
            with mock.patch.object(sys, "argv", ["update-claude.py"]):
                self.m.main()
            self.assertEqual(probe.call_count, 1)
            with mock.patch.object(sys, "argv", ["update-claude.py", "--dry-run"]):
                self.m.main()
            self.assertEqual(probe.call_count, 1)


class ProbeThroughTheRealPath(tbs.Fixture):
    """agent_run on the platform's path, the agent a stand-in: the request reaches it in the repo, with the variable."""

    def test_llm_runs_it_in_the_folder_with_the_variable(self):
        (self.tmp / "config" / "hub.json").write_text(json.dumps({**json.loads((self.tmp / "config" / "hub.json").read_text()),
                                                                  "runtime": {"default": "claude"}}))
        (self.tmp / "home" / ".claude").mkdir()
        (self.tmp / "home" / ".claude" / "headless-oauth-token").write_text("t\n")
        fake = self.tmp / "fakebin"
        fake.mkdir()
        seen = self.tmp / "seen.json"
        (fake / "claude").write_text(f"#!{sys.executable}\nimport json, os\n"
                                     f"json.dump({{'var': os.environ.get({VAR!r}), 'cwd': os.getcwd()}}, open({str(seen)!r}, 'w'))\n"
                                     "print(json.dumps({'type': 'result', 'result': 'ok', 'is_error': False, 'session_id': 's', 'num_turns': 1}))\n")
        (fake / "claude").chmod(0o755)
        repo = self.tmp / "repo"
        repo.mkdir()
        env = {**self.env, "PATH": f"{fake}:{os.environ['PATH']}", "AGENT_HUB_ROOT": str(ROOT), "AGENT_HUB_PYTHON": sys.executable, "LLM_AUTH": "token"}
        r = subprocess.run([sys.executable, str(BIN / "llm.py"), "run", "login-check", "--cwd", str(repo)], input="ok", cwd=repo,
                           capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        got = json.loads(seen.read_text())
        self.assertEqual((got["var"], got["cwd"]), ("1", str(repo)))


class ARuntimeThatDoesntSay(unittest.TestCase):
    def test_which_are_unknown(self):
        self.assertFalse(runtime_ports.folder_git_unknown("claude"))
        self.assertFalse(runtime_ports.folder_git_unknown("fake"))
        self.assertFalse(runtime_ports.folder_git_unknown("none"), "it runs nothing")
        self.assertTrue(runtime_ports.folder_git_unknown("acp"))
        self.assertTrue(runtime_ports.folder_git_unknown("command"))


class AForksFolder(tmd.Dispatch):
    """A fork starts where the session's hooks last were only if that really is the project's."""

    def setUp(self):
        super().setUp()
        self.ws = self.ws.resolve()
        self.other = (self.tmp / "other-project").resolve()
        self.other.mkdir()

    def at(self, cwd):
        self.c.execute("INSERT OR REPLACE INTO presence(session, project, cwd, seen, runtime, via) VALUES('S', 'proj', ?, ?, NULL, NULL)",
                       (str(cwd), self.m.iso()))
        return self.m.session_cwd(self.c, "S", self.ws)

    def test_a_folder_in_the_project(self):
        (self.ws / "sub").mkdir()
        self.assertEqual(self.at(self.ws / "sub"), self.ws / "sub")

    def test_swapped_for_a_link_into_another_project(self):
        (self.ws / "sub").symlink_to(self.other)
        self.assertIsNone(self.at(self.ws / "sub"))

    def test_a_folder_elsewhere_or_gone(self):
        self.assertIsNone(self.at(self.other))
        self.assertIsNone(self.at(self.ws / "gone"))

    def test_no_row(self):
        self.assertIsNone(self.m.session_cwd(self.c, "nobody", self.ws))

    def git(self, *a):
        subprocess.run(["git", *map(str, a)], check=True, capture_output=True, env={**os.environ, **tbs.GIT_ENV})

    def test_a_real_worktree_of_it_elsewhere(self):
        self.git("init", "-q", "-b", "main", self.ws)
        (self.ws / "f").write_text("x")
        self.git("-C", self.ws, "add", "f")
        self.git("-C", self.ws, "commit", "-qm", "x")
        wt = (self.tmp / "out").resolve()
        self.git("-C", self.ws, "worktree", "add", "-q", wt, "-b", "w")
        self.assertEqual(self.at(wt), wt)

    def test_a_pointer_naming_it_from_elsewhere_isnt_one(self):
        self.git("init", "-q", "-b", "main", self.ws)
        planted = (self.tmp / "planted").resolve()
        planted.mkdir()
        (planted / ".git").write_text(f"gitdir: {self.ws / '.git'}\n")
        self.assertIsNone(self.at(planted))

    def test_dispatch_starts_the_fork_in_the_projects_folder(self):
        (self.ws / "sub").symlink_to(self.other)
        self.rt.live.add("S")
        self.rt.tokens["S"] = 1000
        self.seen("S", minutes_ago=30)
        self.c.execute("UPDATE presence SET cwd=? WHERE session='S'", (str(self.ws / "sub"),))
        self.send("m1", "proj", "request")
        started = []
        self.m.resume = lambda c, r, sid, cwd, rt, ws=None, prompt=None: started.append(pathlib.Path(cwd).resolve())
        self.dispatch()
        self.assertEqual(started, [self.ws])


class SettingsThroughASwap(twp.Fixture):
    """The settings pass, as the user: it writes down from the workspace, whatever a session folder has become."""

    def setUp(self):
        super().setUp()
        self.target = self.ws["open"] / ".claude" / "settings.local.json"

    def render(self, folder):
        env = {**self.hook_env()}
        return subprocess.run([sys.executable, str(BIN / "render-project-settings.py"), "--worktree", str(folder)], capture_output=True,
                              text=True, timeout=60, env=env)

    def swap(self):
        wts = self.ws["box"] / ".claude" / "worktrees"
        wts.mkdir(parents=True)
        (wts / "w").symlink_to(self.ws["open"])
        return wts / "w"

    def test_a_worktree_swapped_for_a_link_into_another_project_isnt_written_through(self):
        self.render(self.swap())
        self.assertFalse(self.target.exists())

    def test_through_the_script_either(self):
        self.tree("open")
        link = self.swap()
        self.run_hook("start", link, "SessionStart", source="compact")
        self.assertFalse(self.target.exists())

    def test_write_settings_refuses_a_folder_outside_its_workspace(self):
        m = load("render_swap", BIN / "render-project-settings.py")
        doc = {**self.docs["box"], "name": "box"}
        link = self.swap()
        with mock.patch.object(hubconf, "projects", lambda: self.docs), self.assertRaises(ValueError):
            m.write_settings(doc, {"a": 1}, link)
        self.assertFalse(self.target.exists())

    def test_a_real_folder_of_its_own_is_written(self):
        m = load("render_real", BIN / "render-project-settings.py")
        doc = {**self.docs["box"], "name": "box"}
        wt = self.ws["box"] / ".claude" / "worktrees" / "w"
        wt.mkdir(parents=True)
        with mock.patch.object(hubconf, "projects", lambda: self.docs):
            m.write_settings(doc, {"a": 1}, wt)
        self.assertEqual(json.loads((wt / ".claude" / "settings.local.json").read_text()), {"a": 1})


class TheHourlySync(twp.Fixture):
    def test_a_folder_the_registry_doesnt_name_runs_nothing(self):
        left = self.tmp / "home" / "workspace" / "left-behind"
        left.mkdir(parents=True)
        self.plant_memory(left)
        env = {**self.hook_env(), "AGENT_HUB_LOG": str(self.tmp / "sync.log")}
        r = subprocess.run(["bash", str(BIN / "sync-all-memory.sh")], capture_output=True, text=True, timeout=120, env=env, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.ran_as_the_user(), [])
        self.assertFalse(self.marker.exists())


class ThePlatformsOwnSync(twp.Fixture):
    def test_a_sandboxed_projects_goes_as_the_project(self):
        import project_git
        with mock.patch.object(hubconf, "projects", lambda: self.docs), \
                mock.patch.object(project_git, "sync_memory") as job, \
                mock.patch.object(project_git.subprocess, "run", side_effect=AssertionError("as the user")):
            project_git.sync_memory_of("box", self.ws["box"])
        self.assertEqual(job.call_args[1], {"lock": True})

    def test_an_open_projects_goes_the_hooks_way(self):
        import project_git
        with mock.patch.object(hubconf, "projects", lambda: self.docs), mock.patch.object(project_git.subprocess, "run") as run:
            project_git.sync_memory_of("open", self.ws["open"])
        self.assertTrue(str(run.call_args[0][0][0]).endswith("with-lock.sh"))


if __name__ == "__main__":
    unittest.main()
