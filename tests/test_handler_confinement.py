"""The agents anyone who can write the bus can reach are confined (docs/design/mailbox-routing.md "11-02",
docs/design/event-jobs.md 3.2): the mailbox handlers (mailbox-escalation, mailbox-conversation), event agent jobs
(agent-job-event) and clock agent jobs that declare allow: (agent-job-confined) load only the platform's settings for
the project, so the project's saved allow rules, the user's and the hooks never apply, and their Bash reaches only the
mail commands.

No agent is started: the runtime's command line, the platform settings file it writes, and what dispatch and
run-agent-job.sh hand down are checked.

    bin/py -m unittest tests.test_handler_confinement
"""
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
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

MAILBOX = ("mailbox-escalation", "mailbox-conversation")
CONFINED = ("agent-job-event", "agent-job-confined", *MAILBOX)
# what a person accumulated in a project's settings: none of it may reach an unattended run
SAVED = ["Bash(*)", "Bash(npm:*)", "Bash(git push:*)", "WebFetch", "Edit(**)"]


def points() -> dict:
    return yaml.safe_load((ROOT / "contracts" / "llm-points.yaml").read_text())["points"]


def load_hubbus():
    loader = importlib.machinery.SourceFileLoader("hubbus_confine", str(ROOT / "bin" / "hubbus"))
    spec = importlib.util.spec_from_loader("hubbus_confine", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def flags(point: str, cwd, env: dict) -> list:
    import runtime_claude
    import runtime_ports
    task = runtime_ports.Task(point, "x", points()[point], cwd=str(cwd), env=env)
    with mock.patch.object(runtime_claude, "platform_settings", lambda p, keep=None: pathlib.Path(f"/platform/{p}.json")):
        return runtime_claude._flags(task)


def allowed(cmd: list) -> list:
    i = cmd.index("--allowedTools") + 1
    out = []
    while i < len(cmd) and not cmd[i].startswith("--"):
        out.append(cmd[i])
        i += 1
    return out


@unittest.skipIf(yaml is None, "needs PyYAML")
class Declared(unittest.TestCase):
    def test_every_point_reachable_through_the_bus_is_confined(self):
        p = points()
        for name in CONFINED:
            with self.subTest(name):
                self.assertEqual(p[name].get("settings"), "platform")
                self.assertTrue(p[name].get("project_env"))
                self.assertEqual(p[name].get("permission_mode"), "dontAsk")
                self.assertEqual(p[name].get("prompts"), "none")

    def test_a_clock_job_with_allow_is_never_run_unconfined(self):
        # agent-job (auto, the project's settings) can't take an allow list: such a job goes to agent-job-confined
        self.assertNotIn("allow_from_env", points()["agent-job"])
        self.assertEqual(points()["agent-job-confined"].get("allow_from_env"), "AGENT_JOB_ALLOW")


@unittest.skipIf(yaml is None, "needs PyYAML")
class Flags(unittest.TestCase):
    def setUp(self):
        self.ws = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.ws, True)
        # the project's saved permissions, where Claude Code would read them without --restricted
        (self.ws / ".claude").mkdir()
        (self.ws / ".claude" / "settings.local.json").write_text(json.dumps({"permissions": {"allow": SAVED}}))
        (self.ws / ".agent-memory" / "memory").mkdir(parents=True)

    def env(self, point):
        spec = points()[point]
        env = {spec["project_env"]: "p"}
        if spec.get("allow_from_env"):
            env[spec["allow_from_env"]] = json.dumps(["Read(./docs/**)"])
        return env

    def test_only_the_platform_settings_load(self):
        for point in CONFINED:
            with self.subTest(point):
                cmd = flags(point, self.ws, self.env(point))
                self.assertIn("--restricted", cmd)   # no user, project or local settings file (nor their hooks)
                self.assertEqual(cmd[cmd.index("--settings") + 1], "/platform/p.json")
                self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")
                self.assertEqual(cmd[cmd.index("--permission-prompts") + 1], "none")
                self.assertFalse(set(SAVED) & set(cmd), "a saved project rule reached the command line")

    def test_a_mailbox_handlers_bash_reaches_only_the_mail_commands(self):
        for point in MAILBOX:
            with self.subTest(point):
                cmd = flags(point, self.ws, self.env(point))
                tools = set(cmd[cmd.index("--tools") + 1].split(","))
                self.assertLessEqual(tools, set(points()[point]["tools"].split(",")))
                bash = [r for r in allowed(cmd) if r.startswith("Bash(")]
                self.assertTrue(bash)
                self.assertTrue(all(r.startswith(("Bash(agenthub ", "Bash(brunna ")) for r in bash), bash)   # no raw hubbus
                self.assertNotIn("Bash", [r for r in allowed(cmd)])
                others = [r for r in allowed(cmd) if not r.startswith("Bash(")]
                self.assertEqual(others, ["Edit(./.agent-memory/memory/**)"])

    def test_declared_tools_without_bash_give_no_bash(self):
        import runtime_claude
        import runtime_ports
        spec = {"settings": "platform", "project_env": "P", "permission_mode": "dontAsk", "tools": "Read,Edit",
                "allowed_tools": ["Edit(./notes/**)"]}
        task = runtime_ports.Task("synthetic", "x", spec, cwd=str(self.ws), env={"P": "p"})
        with mock.patch.object(runtime_claude, "platform_settings", lambda p, keep=None: pathlib.Path("/platform/p.json")):
            cmd = runtime_claude._flags(task)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "Edit,Read")

    def test_without_the_project_it_never_runs(self):
        import runtime_claude
        for point in MAILBOX:
            with self.subTest(point), self.assertRaises(ValueError):
                flags(point, self.ws, {})
        self.assertTrue(callable(runtime_claude.platform_settings))

    def test_a_worktrees_linked_memory_is_reached_where_it_resolves(self):
        # --restricted confines file tools to the working directories and checks the resolved path (live probe
        # 11-02): a worktree's .agent-memory is a link to the main checkout's
        main = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, main, True)
        (main / ".agent-memory" / "memory").mkdir(parents=True)
        wt = self.ws / "wt"
        wt.mkdir()
        (wt / ".agent-memory").symlink_to(main / ".agent-memory")
        cmd = flags("mailbox-conversation", wt, self.env("mailbox-conversation"))
        self.assertEqual(cmd[cmd.index("--add-dir") + 1], str(main / ".agent-memory"))
        self.assertIn(f"Edit(/{main}/.agent-memory/memory/**)", allowed(cmd))
        self.assertIn("Edit(./.agent-memory/memory/**)", allowed(cmd))
        # a real folder needs nothing added
        cmd = flags("mailbox-conversation", self.ws, self.env("mailbox-conversation"))
        self.assertNotIn("--add-dir", cmd)

    def test_an_unconfined_point_keeps_the_raw_mail_rule_out_of_confined_ones_only(self):
        # mailbox-propose has no Bash at all; nothing else declares {platform_mail}
        for name, spec in points().items():
            if "{platform_mail}" in (spec.get("allowed_tools") or []):
                self.assertEqual(spec.get("settings"), "platform", name)


@unittest.skipIf(yaml is None, "needs PyYAML")
class PlatformSettingsFile(unittest.TestCase):
    def test_the_settings_written_hold_no_saved_rule(self):
        import runtime_claude
        ws = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, ws, True)
        (ws / ".claude").mkdir()
        (ws / ".claude" / "settings.local.json").write_text(json.dumps({"permissions": {"allow": SAVED}}))
        pol = {"other_projects": [], "deny_write": [], "deny_read": [], "platform_commands": [],
               "platform_cli": ["agenthub send"], "write": [str(ws)], "network": [], "credential_files": [],
               "credentials": [], "workspace": str(ws)}
        state = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, state, True)
        doc = {"name": "p", "paths": {"workspace": str(ws)}, "enforce": {"sandbox": True}}
        with mock.patch("policy.policy", lambda name, d, i: pol), mock.patch.object(hubconf, "STATE", state), \
                mock.patch("registry.refresh", lambda: {"projects": {"p": doc}}):
            d = json.loads(runtime_claude.platform_settings("p").read_text())
        allow = d["permissions"]["allow"]
        self.assertFalse(set(SAVED) & set(allow))
        self.assertTrue(allow and all(r.startswith(("Bash(agenthub ", "Bash(brunna ")) for r in allow), allow)
        self.assertIs(d["sandbox"]["autoAllowBashIfSandboxed"], False)


@unittest.skipIf(yaml is None, "needs PyYAML")
class JobStateFolder(unittest.TestCase):
    """An unattended job writes its own state folder under the hub, which the platform's deny list covers whole: that
    one folder is carved out (a runtime's deny wins over any allow; 11-02 the Upwork job couldn't save its state).
    Live probe 11-02 (2.1.316): the job's folder took the Write, .settings/ was denied, a new file at the hub's top
    was outside --restricted's working directory."""

    def setUp(self):
        self.hub = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.hub, True)
        self.state = self.hub / ".state"
        self.job = self.state / "agent-jobs" / "p-check"
        for d in (self.hub / "bin", self.state / "agent-jobs" / ".settings", self.state / "agent-jobs" / "other", self.job):
            d.mkdir(parents=True)
        (self.hub / ".hub-home").write_text("x")
        (self.state / "scheduler-state.json").write_text("{}")

    def test_only_its_own_folder_with_a_rule_writing_it(self):
        import runtime_claude
        with mock.patch.object(hubconf, "STATE", self.state):
            rule = "Edit(" + str(self.job).replace(str(pathlib.Path.home()), "~", 1) + "/**)" \
                if str(self.job).startswith(str(pathlib.Path.home())) else f"Edit(/{self.job}/**)"
            self.assertEqual(runtime_claude.own_state_dir([rule, "ToolSearch"], self.job), self.job)
            self.assertEqual(runtime_claude.own_state_dir(["Edit(./**)"], self.job), self.job)
            self.assertIsNone(runtime_claude.own_state_dir(["Read(./**)", "ToolSearch"], self.job), "no rule writes it")
            self.assertIsNone(runtime_claude.own_state_dir(["Edit(./**)"], self.state / "agent-jobs" / ".settings"))
            self.assertIsNone(runtime_claude.own_state_dir(["Edit(./**)"], self.hub / "bin"), "not a job's folder")

    def test_the_deny_list_keeps_everything_but_that_folder(self):
        import policy
        carved = policy.write_except([self.hub, pathlib.Path("/elsewhere")], self.job)
        self.assertNotIn(self.job, carved)
        self.assertFalse(any(self.job == c or c in self.job.parents for c in carved), carved)
        for kept_denied in (self.hub / "bin", self.hub / ".hub-home", self.state / "scheduler-state.json",
                            self.state / "agent-jobs" / ".settings", self.state / "agent-jobs" / "other",
                            pathlib.Path("/elsewhere")):
            self.assertIn(kept_denied, carved)

    def test_the_settings_file_is_the_jobs_own(self):
        import runtime_claude
        pol = {"other_projects": [], "deny_write": [self.hub], "deny_read": [], "platform_commands": [],
               "platform_cli": ["agenthub send"], "write": [], "network": [], "credential_files": [],
               "credentials": [], "workspace": str(self.hub)}
        doc = {"name": "p", "paths": {"workspace": str(self.hub)}}
        with mock.patch("policy.policy", lambda name, d, i: pol), mock.patch.object(hubconf, "STATE", self.state), \
                mock.patch("registry.refresh", lambda: {"projects": {"p": doc}}):
            whole, own = runtime_claude.platform_settings("p"), runtime_claude.platform_settings("p", self.job)
        self.assertNotEqual(whole, own)
        deny_whole = json.loads(whole.read_text())["permissions"]["deny"]
        deny_own = json.loads(own.read_text())["permissions"]["deny"]
        rule = runtime_claude._rule_path   # ~/… or //… (a single / would be the settings file's folder)
        self.assertIn(f"Edit({rule(self.hub)}/**)", deny_whole)
        self.assertNotIn(f"Edit({rule(self.hub)}/**)", deny_own)
        self.assertIn(f"Edit({rule(self.state / 'agent-jobs' / '.settings')}/**)", deny_own)
        self.assertIn(f"Edit({rule(self.hub / '.hub-home')})", deny_own)
        # its own folder isn't in the hub's platform state either (a project other than the hub: policy.platform_state)
        self.assertFalse([r for r in deny_own if str(self.job) in r and not r.startswith("Edit(") or
                          r in (f"Read({rule(self.state / 'agent-jobs')}/**)", f"Edit({rule(self.state / 'agent-jobs')}/**)")],
                         deny_own)
        self.assertIn(f"Read({rule(self.state)}/**)", deny_whole)   # whole, without the job's folder: listed beside it


@unittest.skipIf(yaml is None, "needs PyYAML")
class RuntimeWithoutConfine(unittest.TestCase):
    def test_the_handlers_are_refused_rather_than_run_unconfined(self):
        import runtime_ports
        spec = importlib.util.spec_from_file_location("llm_conf_h", ROOT / "bin" / "llm.py")
        llm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(llm)
        for point in (*MAILBOX, "agent-job-confined"):
            with self.subTest(point):
                failed = []
                a = types.SimpleNamespace(cmd="run", point=point, cwd=None, model=None, prompt_file=None,
                                          dry_run=True, extra=[])
                with mock.patch.object(llm, "fail", lambda point, spec, why: failed.append(why) or 1), \
                        mock.patch.object(runtime_ports, "default_name", lambda: "command"), \
                        mock.patch("sys.stdin", io.StringIO("x")):
                    self.assertEqual(llm.run(a), 1)
                self.assertEqual(len(failed), 1)   # refused with the reason, never run


class Dispatch(unittest.TestCase):
    """hubbus dispatch names the project for both handlers (without it the runtime refuses to run)."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        m = self.m = load_hubbus()
        m.DB = self.tmp / "bus.db"
        m.AGENT_JOBS = self.tmp / "agent-jobs"
        m.log = lambda msg: None
        self.c = m.db()
        self.addCleanup(self.c.close)
        m.publish(self.c, "m1", "message", "other", "proj", None, {"type": "request", "text": "hi"})
        self.row = self.c.execute("SELECT * FROM events WHERE id='m1'").fetchone()

    def test_escalation(self):
        seen = {}

        def run(cmd, **kw):
            seen.update(kw["env"])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with mock.patch.object(self.m.subprocess, "run", run), mock.patch.object(self.m.prompts, "render", lambda *a, **k: "p"), \
                mock.patch.object(self.m.hubconf, "get", lambda k, d=None: d):
            self.m.run_handler(self.c, "proj", self.tmp, [self.row])
        self.assertEqual(seen["AGENT_JOB_POINT"], "mailbox-escalation")
        self.assertEqual(seen["MAILBOX_PROJECT"], "proj")

    def test_conversation(self):
        seen = {}

        class P:
            def __init__(self, cmd, **kw):
                seen.update(kw["env"])
                seen["cmd"] = cmd
                self.stdin = io.StringIO()
        rt = types.SimpleNamespace(resume_args=lambda sid: ["--resume", sid, "--fork-session"])
        with mock.patch.object(self.m.subprocess, "Popen", P), mock.patch.object(self.m, "branch_prompt", lambda r, p: "t"), \
                mock.patch.object(self.m.hubconf, "STATE", self.tmp):
            self.m.resume(self.c, self.row, "S1", self.tmp, rt)
        self.assertIn("mailbox-conversation", seen["cmd"])
        self.assertEqual(seen["MAILBOX_PROJECT"], "proj")


class Registry(unittest.TestCase):
    def test_a_clock_job_with_allow_names_its_project(self):
        import registry
        index = {"projects": {"p": {"name": "p", "paths": {"workspace": "/tmp/p-ws"}, "jobs": [
            {"name": "a", "at": "08:00", "kind": "agent", "allow": ["Read"]},
            {"name": "b", "at": "09:00", "kind": "agent"}]}}}
        with mock.patch.object(registry.isolation, "sandboxed", lambda doc: False):
            jobs = {j["name"]: j["command"] for j in registry.scheduler_jobs(index, set())[0]}
        self.assertIn("AGENT_JOB_ALLOW=", jobs["p/a"])
        self.assertIn(" AGENT_JOB_PROJECT=p ", jobs["p/a"])
        self.assertNotIn("AGENT_JOB_ALLOW", jobs["p/b"])


if __name__ == "__main__":
    unittest.main()
