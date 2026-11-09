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
        self.assertFalse(registry.rule_shaped("--dangerously-skip-permissions"))
        self.assertFalse(registry.rule_shaped("Read\nWrite"))
        self.assertTrue(registry.rule_shaped("Bash(npm run build:*)"))


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
                                                   [self.ws, (main / ".agent-memory" / "memory").resolve()])
        self.assertEqual(dirs, [str((main / ".agent-memory").resolve())])
        self.assertEqual(rules, [f"Edit(/{(main / '.agent-memory' / 'memory').resolve()}/**)"])

    def test_inside_the_folder_is_untouched(self):
        (self.ws / "data").mkdir()
        self.assertEqual(runtime_claude._linked_rules(["Edit(./data/**)"], self.ws, [self.ws]), ([], []))


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
        for new in (widened, turned_on, reprompted):
            with self.subTest(new=new):
                self.assertEqual(len(self.changes(new)), 1)


class DenyLists(unittest.TestCase):
    def test_the_headless_token_is_a_credential(self):
        files = runtime_claude.credential_paths()["files"]
        self.assertIn(str(pathlib.Path.home() / ".claude" / "headless-oauth-token"), files)

    def test_every_startup_file_of_the_shells(self):
        for f in ("~/.zshenv", "~/.zlogin", "~/.bash_login", "~/.zshrc", "~/.profile"):
            self.assertIn(f, policy.SHELL_FILES)


if __name__ == "__main__":
    unittest.main()
