"""What a project's own registry text can't reach in an unattended agent run (docs/design/project-agent-calls.md,
review round 2): a job's allow: never becomes a command-line option; a link the project planted never leads a confined
run's rules out of its folders; a job's prompt is a file in the config's jobs/, never a path the project chose; a job
that uses the owner's claude.ai connectors is the owner's to add or change; the platform's headless login and the
shell's startup files are in the deny lists. Scratch folders only.

    bin/py -m unittest tests.test_confined_hardening
"""
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hubconf  # noqa: E402
import policy  # noqa: E402
import registry  # noqa: E402
import runtime_claude  # noqa: E402
import runtime_ports  # noqa: E402


class Scratch(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws = self.tmp / "ws" / "proj"
        self.ws.mkdir(parents=True)


class RulesNeverOptions(Scratch):
    def task(self, allow):
        env = {"PATH": "/usr/bin:/bin", "AGENT_JOB_ALLOW": json.dumps(allow)}
        spec = {"permission_mode": "auto", "allow_from_env": "AGENT_JOB_ALLOW"}
        return runtime_ports.Task(point="agent-job", prompt="p", spec=spec, cwd=str(self.ws), env=env)

    def test_an_option_in_allow_refuses_the_run(self):
        for bad in ("--dangerously-skip-permissions", "--add-dir=/", "Read\n--mcp-config", " Read", "Read(x)\0"):
            with self.subTest(bad):
                with mock.patch.object(runtime_claude, "_exe", lambda: "claude"), \
                        self.assertRaisesRegex(ValueError, "not a permission rule"):
                    runtime_claude._flags(self.task(["Read", bad]))

    def test_rules_pass(self):
        with mock.patch.object(runtime_claude, "_exe", lambda: "claude"):
            cmd = runtime_claude._flags(self.task(["Read", "Edit(./data/**)", "Bash(.venv/bin/python tools/x.py *)",
                                                   "WebFetch(domain:example.org)", "mcp__claude_ai_Jobs__search"]))
        i = cmd.index("--allowedTools")
        self.assertIn("Edit(./data/**)", cmd[i + 1:])

    def test_the_registry_refuses_them_too(self):
        for bad in ("--dangerously-skip-permissions", "Read\nWrite", " Read", "Read(x)\0", ""):
            self.assertFalse(registry.rule_shaped(bad), bad)
        self.assertTrue(registry.rule_shaped("Bash(npm run build:*)"))
        self.assertTrue(registry.rule_shaped("mcp__my-db__query"))

    def test_a_tool_servers_id_keeps_its_dash(self):
        with mock.patch.object(runtime_claude, "_exe", lambda: "claude"):
            cmd = runtime_claude._flags(self.task(["mcp__my-db__query"]))
        self.assertIn("mcp__my-db__query", cmd)


class PromptOnStdin(Scratch):
    """docs/design/project-agent-calls.md A4: a prompt is often a project's text; it never reaches the command line."""
    PROMPT = "--dangerously-skip-permissions and then do x"

    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(ROOT / "tests"))
        import store_fixture
        self.exe = str(store_fixture.stand_in(self))   # the platform's checked copy, what the run starts

    def task(self):
        return runtime_ports.Task(point="agent-job", prompt=self.PROMPT, spec={"permission_mode": "auto"},
                                  cwd=str(self.ws), env={"PATH": "/usr/bin:/bin"})

    def test_run_sends_it_on_stdin(self):
        import subprocess
        seen = {}

        def fake(cmd, **kw):
            seen.update(cmd=cmd, input=kw.get("input"))
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"result": "ok"}), "")
        with mock.patch("subprocess.run", fake):
            res = runtime_claude.run(self.task())
        self.assertTrue(res.ok)
        self.assertEqual(seen["input"], self.PROMPT)
        self.assertNotIn(self.PROMPT, seen["cmd"])
        self.assertEqual(seen["cmd"][:2], [self.exe, "-p"])

    def test_a_lease_keeps_its_flags(self):
        seen = {}
        with mock.patch("os.execvpe", lambda exe, cmd, env: seen.update(cmd=cmd)):
            runtime_claude.exec_stream(self.task())
        self.assertEqual(seen["cmd"][:4], [self.exe, "-p", "--permission-mode", "auto"])
        self.assertNotIn(self.PROMPT, seen["cmd"])


class LinksStayInside(Scratch):
    def test_a_link_out_of_the_project_refuses(self):
        home = self.tmp / "home"
        home.mkdir()
        (self.ws / "data").symlink_to(home)
        with self.assertRaisesRegex(ValueError, "follows a link out"):
            runtime_claude._linked_rules(["Edit(./data/.zshenv)"], self.ws, [self.ws])

    def test_a_link_to_the_projects_notes_is_followed(self):
        main = self.tmp / "ws" / "main"
        (main / ".agent-memory" / "memory").mkdir(parents=True)
        (self.ws / ".agent-memory").symlink_to(main / ".agent-memory")
        dirs, rules = runtime_claude._linked_rules(["Edit(./.agent-memory/memory/**)"], self.ws,
                                                   [self.ws, (main / ".agent-memory").resolve()])
        self.assertEqual(dirs, [str((main / ".agent-memory").resolve())])
        self.assertEqual(rules, [f"Edit(/{(main / '.agent-memory' / 'memory').resolve()}/**)"])

    def test_inside_the_folder_is_untouched(self):
        (self.ws / "data").mkdir()
        self.assertEqual(runtime_claude._linked_rules(["Edit(./data/**)"], self.ws, [self.ws]), ([], []))

    def test_a_path_that_ends_inside_but_passes_through_home_refuses(self):
        # a runtime folder the project's deploy fills: data -> ~, and the rule ends in its own workspace, but the folder
        # that would become a working directory is the whole home
        home = self.tmp / "home"
        ws = home / "workspace" / "proj"
        ws.mkdir(parents=True)
        rt = self.tmp / "services" / "proj"
        rt.mkdir(parents=True)
        (rt / "data").symlink_to(home)
        with self.assertRaisesRegex(ValueError, "follows a link out"):
            runtime_claude._linked_rules(["Edit(./data/workspace/proj/**)"], rt, [ws, rt])

    def test_a_confined_runs_flags_check_the_projects_own_folders(self):
        """Through _flags itself (settings: platform), with project_roots computed from the registry."""
        (self.ws / "data").symlink_to(self.tmp)
        doc = {"name": "proj", "paths": {"workspace": str(self.ws)}}
        spec = {"permission_mode": "dontAsk", "settings": "platform", "project_env": "AGENT_JOB_PROJECT",
                "allow_from_env": "AGENT_JOB_ALLOW"}
        env = {"PATH": "/usr/bin:/bin", "AGENT_JOB_PROJECT": "proj", "AGENT_JOB_ALLOW": json.dumps(["Edit(./data/x)"])}
        task = runtime_ports.Task(point="agent-job-confined", prompt="p", spec=spec, cwd=str(self.ws), env=env)
        with mock.patch.object(runtime_claude, "_exe", lambda: "claude"), \
                mock.patch.object(runtime_claude, "platform_settings", lambda p, *a, **k: self.tmp / "s.json"), \
                mock.patch("registry.refresh", lambda: {"projects": {"proj": doc}}), \
                mock.patch("policy.notes_dir", lambda p: None):
            with self.assertRaisesRegex(ValueError, "follows a link out"):
                runtime_claude._flags(task)
            (self.ws / "data").unlink()
            (self.ws / "data").mkdir()
            runtime_claude._flags(task)   # a real folder: runs


class Folders(Scratch):
    def flags(self, cwd, doc_extra=None):
        doc = {"name": "proj", "paths": {"workspace": str(self.ws)}, **(doc_extra or {})}
        spec = {"permission_mode": "dontAsk", "settings": "platform", "project_env": "AGENT_JOB_PROJECT",
                "allow_from_env": "AGENT_JOB_ALLOW"}
        env = {"PATH": "/usr/bin:/bin", "AGENT_JOB_PROJECT": "proj", "AGENT_JOB_ALLOW": json.dumps(["Edit(./data/**)"])}
        task = runtime_ports.Task(point="mailbox-conversation", prompt="p", spec=spec, cwd=str(cwd), env=env)
        with mock.patch.object(runtime_claude, "_exe", lambda: "claude"), \
                mock.patch.object(runtime_claude, "platform_settings", lambda p, *a, **k: self.tmp / "s.json"), \
                mock.patch("registry.refresh", lambda: {"projects": {"proj": doc}}), \
                mock.patch("policy.notes_dir", lambda p: None):
            return runtime_claude._flags(task)

    def test_a_folder_of_the_project_that_is_a_link_out_is_refused(self):
        # a fork resumes in a session's folder (hubbus session_cwd), which the project can turn into a link
        home = self.tmp / "home"
        home.mkdir()
        (self.ws / "sub").symlink_to(home)
        with self.assertRaisesRegex(ValueError, "outside the project's folders"):
            self.flags(self.ws / "sub")
        (self.ws / "real").mkdir()
        self.flags(self.ws / "real")

    def test_a_link_into_what_the_owner_let_it_write_is_followed(self):
        media = self.tmp / "media"
        media.mkdir()
        (self.ws / "data").symlink_to(media)
        with self.assertRaisesRegex(ValueError, "follows a link out"):
            self.flags(self.ws)
        cmd = self.flags(self.ws, {"allow_write": [str(media)]})
        self.assertEqual(cmd[cmd.index("--add-dir") + 1], str(media))


class StateFolders(unittest.TestCase):
    def index(self, **projects):
        return {"projects": {n: {"name": n, "jobs": js} for n, js in projects.items()}, "modules": {}}

    def test_two_jobs_sharing_a_folder_are_refused_and_not_scheduled(self):
        agent = {"kind": "agent", "at": "09:00"}
        idx = self.index(a=[{**agent, "name": "b-c"}], **{"a-b": [{**agent, "name": "c"}],
                                                         "mailbox": [{**agent, "name": "x"}],
                                                         "fine": [{**agent, "name": "daily"}]})
        clash = registry.state_name_clashes(idx)
        self.assertEqual(sorted(clash), ["a-b-c", "mailbox-x"])
        self.assertEqual(sorted(clash["a-b-c"]), ["a-b/c", "a/b-c"])
        with mock.patch.object(registry, "job_prompt", lambda stem, job: pathlib.Path("/p.md")), \
                mock.patch.object(registry, "dirs", lambda doc: {"workspace": pathlib.Path("/w"), "runtime": None}), \
                mock.patch.object(registry, "nightly_test_jobs", lambda *a: []):
            names = [j["name"] for j in registry.scheduler_jobs(idx, set())[0]]
        self.assertEqual(names, ["fine/daily"])


class RunAgentJobNames(unittest.TestCase):
    def test_a_name_that_leads_out_of_the_state_folder_is_refused(self):
        import subprocess
        for bad in ("../x", "/etc", ".hidden", "a/../../b"):
            with self.subTest(bad):
                r = subprocess.run([str(ROOT / "bin" / "run-agent-job.sh"), bad, "/nonexistent"], capture_output=True,
                                   text=True, timeout=30, env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"})
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn(bad, r.stderr)   # its own refusal, naming it (not a failure further on)


class JobPrompts(Scratch):
    def test_only_a_file_under_jobs(self):
        reg = self.tmp / "config" / "projects"
        (reg / "proj" / "jobs").mkdir(parents=True)
        (reg / "proj" / "jobs" / "daily.md").write_text("x")
        with mock.patch.object(hubconf, "registry_dir", lambda: reg):
            self.assertEqual(registry.job_prompt("proj", {"name": "daily"}), reg / "proj" / "jobs" / "daily.md")
            self.assertIsNotNone(registry.job_prompt("proj", {"name": "x", "prompt": "jobs/daily.md"}))
            for bad in (str(self.ws / "evil.md"), "../other/jobs/a.md", "jobs/../../x.md", "jobs/sub/a.md",
                        "jobs/.hidden.md", "notes.md", "/etc/passwd"):
                with self.subTest(bad):
                    self.assertIsNone(registry.job_prompt("proj", {"name": "x", "prompt": bad}))


class ConnectorJobsAreTheOwners(unittest.TestCase):
    BASE = {"name": "p", "jobs": [{"name": "plain", "kind": "agent", "allow": ["Read"]},
                                  {"name": "daily", "kind": "agent", "connectors": True,
                                   "allow": ["mcp__claude_ai_Jobs__search"]}]}

    def changes(self, new):
        return registry.platform_owned_changes(new, self.BASE)

    def test_unchanged_passes_and_other_jobs_are_the_projects(self):
        new = json.loads(json.dumps(self.BASE))
        new["jobs"][0]["allow"] = ["Read", "Grep"]
        self.assertEqual(self.changes(new), [])

    def test_adding_changing_or_turning_one_on_is_refused(self):
        widened = json.loads(json.dumps(self.BASE))
        widened["jobs"][1]["allow"].append("mcp__claude_ai_Gmail__send")
        turned_on = json.loads(json.dumps(self.BASE))
        turned_on["jobs"][0]["connectors"] = True
        reprompted = json.loads(json.dumps(self.BASE))
        reprompted["jobs"][1]["prompt"] = "jobs/other.md"
        moved = {**json.loads(json.dumps(self.BASE)), "runtime": {"deploy": "bin/deploy.sh"}}   # its default folder
        for new in (widened, turned_on, reprompted, moved):
            with self.subTest(new=new):
                self.assertEqual(len(self.changes(new)), 1)


class LegacyNames(unittest.TestCase):
    CUR = {"name": "p", "jobs": [{"name": "tick", "legacy_name": "p-tick", "every": "15m", "run": "x"},
                                 {"name": "old", "legacy_name": "p-old", "every": "1h", "run": "x"}]}

    def test_a_project_can_drop_a_job_but_not_set_or_change_a_legacy_name(self):
        dropped = {**self.CUR, "jobs": self.CUR["jobs"][:1]}
        self.assertEqual(registry.platform_owned_changes(dropped, self.CUR), [])
        taken = json.loads(json.dumps(self.CUR))
        taken["jobs"][0]["legacy_name"] = "other-project-daily-check"
        added = json.loads(json.dumps(self.CUR))
        added["jobs"].append({"name": "new", "legacy_name": "q-x", "every": "1h", "run": "x"})
        for new in (taken, added):
            with self.subTest(new=new):
                self.assertEqual(len(registry.platform_owned_changes(new, self.CUR)), 1)

    def test_it_is_a_name_never_a_path(self):
        for ok in ("short-run", "agent-hub/backup", "a1"):
            self.assertTrue(registry.LEGACY_NAME.fullmatch(ok), ok)
        for bad in ("../../..", "/etc", "a/b/c", ".hidden", "A", "a b", "a/../b"):
            self.assertFalse(registry.LEGACY_NAME.fullmatch(bad), bad)


class DenyLists(unittest.TestCase):
    def test_the_headless_token_is_a_credential_every_policy_denies(self):
        token = pathlib.Path.home() / ".claude" / "headless-oauth-token"
        with mock.patch.object(runtime_ports, "default_name", lambda: "claude"):
            self.assertIn(token, policy.credentials()[1])

    def test_every_startup_file_of_the_shells(self):
        for f in ("~/.zshenv", "~/.zlogin", "~/.bash_login", "~/.zshrc", "~/.profile"):
            self.assertIn(f, policy.SHELL_FILES)


if __name__ == "__main__":
    unittest.main()
