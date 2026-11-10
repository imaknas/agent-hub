"""The platform's MCP server attached to a confined run (docs/design/confined-mail-tools.md M2): llm.py builds it, the
runtime makes its tools final and starts it, the run's values go in server.json.

No agent is started.

    bin/py -m unittest tests.test_confined_mail_attach
"""
import importlib.util
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hubconf  # noqa: E402
import names  # noqa: E402

try:
    import yaml
except ImportError:
    yaml = None


def load_llm():
    spec = importlib.util.spec_from_file_location("llm_attach", ROOT / "bin" / "llm.py")
    llm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(llm)
    return llm


def points() -> dict:
    return yaml.safe_load((ROOT / "contracts" / "llm-points.yaml").read_text())["points"]


def tmpdir(test) -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp()).resolve()
    test.addCleanup(shutil.rmtree, d, True)
    return d


RID = f"{names.current('cli')}-run"


@unittest.skipIf(yaml is None, "needs PyYAML")
class Attach(unittest.TestCase):
    def setUp(self):
        self.llm = load_llm()
        self.run_dir = tmpdir(self)

    def attach(self, point, env, rt=None):
        import runtime_claude
        import runtime_ports
        spec = points()[point]
        task = runtime_ports.Task(point, "x", spec, env=env)
        return self.llm.attach_server(point, spec, rt or runtime_claude, task, self.run_dir)

    def test_a_handler_gets_its_tools_and_its_values_in_the_file(self):
        env = {"MAILBOX_PROJECT": "demo", "MAILBOX_LETTERS": '["m1"]'}
        with mock.patch.dict(os.environ, {"AGENT_HUB_JOB_CHAIN": '["a/b"]', "AGENT_HUB_JOB_DEPTH": "1"}):
            task, why = self.attach("mailbox-escalation", env)
        self.assertIsNone(why)
        [s] = task.servers
        self.assertEqual(s.id, RID)
        self.assertEqual(set(s.tools), {"inbox", "show", "thread", "send", "done", "ask_owner", "note"})
        self.assertEqual(s.args[-2:], ("--run", str(self.run_dir / "server.json")))
        body = json.loads((self.run_dir / "server.json").read_text())
        self.assertEqual((body["project"], body["point"], body["letters"], body["chain"], body["depth"]),
                         ("demo", "mailbox-escalation", ["m1"], '["a/b"]', "1"))
        self.assertEqual((self.run_dir / "server.json").stat().st_mode & 0o777, 0o600)

    def test_its_env_holds_only_what_the_platform_names(self):
        agent = {"HOME": "/h", "PATH": "/usr/bin", "AGENT_HUB_STATE": "/s", "CLAUDE_CODE_OAUTH_TOKEN": "secret",
                 "MAILBOX_LETTERS": '["m1"]', "AGENT_HUB_JOB": "x/y"}
        with mock.patch.dict(os.environ, agent, clear=True):
            task, why = self.attach("mailbox-escalation", {"MAILBOX_PROJECT": "demo", "MAILBOX_LETTERS": '["m1"]'})
        self.assertIsNone(why)
        self.assertEqual(task.servers[0].env, {"HOME": "/h", "PATH": "/usr/bin", "AGENT_HUB_STATE": "/s"})

    def test_a_dollar_refuses_the_run(self):
        with mock.patch.dict(os.environ, {"AGENT_HUB_STATE": "/s/${CLAUDE_CODE_OAUTH_TOKEN}"}):
            task, why = self.attach("mailbox-escalation", {"MAILBOX_PROJECT": "demo", "MAILBOX_LETTERS": '["m1"]'})
        self.assertTrue(why)
        self.assertFalse((self.run_dir / "server.json").exists())

    def test_a_job_gets_note_only_with_the_notes_rule(self):
        for allow, want in ((["Read"], {"send", "ask_owner"}),
                            (["Read", "Edit(./.agent-memory/memory/**)"], {"send", "ask_owner", "note"})):
            with self.subTest(allow=allow):
                shutil.rmtree(self.run_dir)
                self.run_dir.mkdir()
                env = {"AGENT_JOB_PROJECT": "demo", "AGENT_JOB_ALLOW": json.dumps(allow), "AGENT_JOB_MAIL": "1"}
                task, _why = self.attach("agent-job-confined", env)
                self.assertEqual(set(task.servers[0].tools), want)
                self.assertEqual(set(json.loads((self.run_dir / "server.json").read_text())["tools"]), want)

    def test_only_a_job_that_opts_in(self):
        spec = points()["agent-job-confined"]
        self.assertFalse(self.llm.mail_tools(spec, {}))
        self.assertFalse(self.llm.mail_tools(spec, {"AGENT_JOB_MAIL": "yes"}))
        self.assertTrue(self.llm.mail_tools(spec, {"AGENT_JOB_MAIL": "1"}))
        self.assertTrue(self.llm.mail_tools(points()["mailbox-escalation"], {}))
        self.assertFalse(self.llm.mail_tools(points()["agent-job-event"], {"AGENT_JOB_MAIL": "1"}))


@unittest.skipIf(yaml is None, "needs PyYAML")
class Command(unittest.TestCase):
    """What the Claude Code adapter makes of it."""

    def test_the_command_has_the_server_and_only_its_tools(self):
        import runtime_claude
        import runtime_ports
        ws, run_dir = tmpdir(self), tmpdir(self)
        server = runtime_ports.ToolServer(RID, "/py", ("/hub/bin/mcp_server.py", "--run", str(run_dir / "server.json")),
                                          {"HOME": "/h"}, ("send", "done"))
        task = runtime_ports.Task("mailbox-escalation", "x", points()["mailbox-escalation"], cwd=str(ws),
                                  env={"MAILBOX_PROJECT": "demo", "MAILBOX_LETTERS": '["m1"]'}, servers=(server,),
                                  run_dir=str(run_dir))
        with mock.patch.object(runtime_claude, "platform_settings", lambda p, *a, **k: run_dir / "settings.json"), \
                mock.patch.object(runtime_claude, "project_roots", lambda p: [ws]):
            cmd = runtime_claude._flags(task)
        conf = json.loads(pathlib.Path(cmd[cmd.index("--mcp-config") + 1]).read_text())
        self.assertEqual(conf, {"mcpServers": {RID: {"command": "/py", "args": list(server.args), "env": {"HOME": "/h"}}}})
        allowed = cmd[cmd.index("--allowedTools") + 1:]
        mine = [r for r in allowed if r.startswith("mcp__")]
        self.assertEqual(sorted(mine), [f"mcp__{RID}__done", f"mcp__{RID}__send"])
        self.assertLess(cmd.index("--mcp-config"), cmd.index("--allowedTools"))

    def test_rule_checks_never_see_the_servers_rules(self):
        import runtime_claude
        import runtime_ports
        server = runtime_ports.ToolServer(RID, "/py", (), {}, ("send",))
        task = runtime_ports.Task("agent-job-confined", "x", points()["agent-job-confined"],
                                  env={"AGENT_JOB_PROJECT": "demo", "AGENT_JOB_ALLOW": '["Read"]'}, servers=(server,))
        self.assertEqual(runtime_claude.servers_named(task), [])
        self.assertNotIn(f"mcp__{RID}__send", runtime_claude._rules(task)[1])


class Refusals(unittest.TestCase):
    def test_a_runtime_without_the_port_runs_no_mail(self):
        import runtime_ports
        with self.assertRaises(runtime_ports.Unsupported):
            runtime_ports.load("none", ports=("run_server",))
        self.assertIn("run_server", runtime_ports.PORTS)
        self.assertNotIn("run_server", runtime_ports.OPTIONAL)

    def test_an_environment_server_under_the_platforms_id_refuses_the_run(self):
        import runtime_fake
        import runtime_ports
        llm = load_llm()
        for rid in llm.RESERVED_SERVERS:
            with self.subTest(rid):
                runtime_fake.set_ambient([rid, "other"], [])
                self.addCleanup(runtime_fake.reset)
                task = runtime_ports.Task("agent-job-confined", "x", {"tool_servers": "allowed_only"})
                with mock.patch.object(llm, "point_runtime", lambda p, s: "fake"):
                    _task, why, checked = llm.deny_unnamed_servers("agent-job-confined", task)
                self.assertTrue(why)
                self.assertFalse(checked)


@unittest.skipIf(yaml is None, "needs PyYAML")
class Flow(unittest.TestCase):
    """Through llm.run (a dry run on the fake runtime): the server attached, the run folder gone after."""

    def test_a_dry_run_of_a_handler(self):
        import registry
        import runtime_fake
        llm = load_llm()
        state = tmpdir(self)
        seen = []

        def describe(task):
            seen.append((task.servers, json.loads((pathlib.Path(task.run_dir) / "server.json").read_text())))
            return ["fake"]
        runtime_fake.set_ambient(None)
        a = types.SimpleNamespace(cmd="run", point="mailbox-escalation", cwd=None, model=None, prompt_file=None,
                                  dry_run=True, extra=[])
        with mock.patch.object(hubconf, "STATE", state), \
                mock.patch.object(llm, "point_runtime", lambda point, spec: "fake"), \
                mock.patch.object(llm, "auth_env", lambda spec, rt: (dict(os.environ), None)), \
                mock.patch.object(registry, "refresh", lambda: {"projects": {"demo": {"name": "demo"}}}), \
                mock.patch.object(runtime_fake, "describe", describe), \
                mock.patch.dict(os.environ, {"MAILBOX_PROJECT": "demo", "MAILBOX_LETTERS": '["m1"]'}), \
                mock.patch("sys.stdin", io.StringIO("x")), mock.patch("sys.stdout", io.StringIO()), \
                mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(llm.run(a), 0)
        [(servers, body)] = seen
        self.assertEqual(servers[0].id, RID)
        self.assertEqual((body["project"], body["letters"]), ("demo", ["m1"]))
        self.assertEqual(list((state / "agent-jobs" / ".settings").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
