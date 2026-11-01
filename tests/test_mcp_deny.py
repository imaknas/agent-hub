"""Connectors jobs deny every MCP server they don't use (docs/design/evaluators.md step 0).

A job with connectors: true can't be sealed with --strict-mcp-config (the account's connectors would go too), so it
loads every MCP server configured where it runs. The judgment point declares `tool_servers: allowed_only`: the runtime
finds what that run would load (runtime_claude.ambient_servers: `claude mcp list` + the config files), the neutral
policy denies every server the allow list doesn't name (policy.tool_server_denials), and when the servers can't be
determined the run doesn't happen (llm.py). Server names here are fixtures, never a real deployment's.

    bin/py -m unittest tests.test_mcp_deny
"""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

import policy  # noqa: E402
import runtime_claude  # noqa: E402
import runtime_fake  # noqa: E402
import runtime_ports  # noqa: E402

try:
    import yaml  # noqa: F401  llm.py needs it
except ImportError:
    yaml = None

# `claude mcp list` as Claude Code 2.1 prints it: the account's connectors, a plugin's servers (one not configured),
# the user's stdio servers, then a config diagnostics block after a blank line
LIST = """Checking MCP server health…

claude.ai Example Docs: https://docs.example.com/mcp - ✔ Connected
claude.ai Jobs: https://mcp.jobs.example.com/mcp - ✔ Connected
plugin:example:tools: https://mcp.example.com (HTTP) - ✔ Connected
plugin:design:google calendar:  (HTTP) - - Not configured
fixture-prod-db: /usr/local/bin/uvx --access-mode=restricted fixture-db-mcp==1.0 - ✔ Connected
fixture-tracker: npx -y @example/tracker-mcp@latest - ✘ Failed to connect — Couldn't read the stored credentials

MCP config diagnostics
For help configuring MCP servers, see: https://example.com/mcp
"""
EMPTY = "No MCP servers configured. Use `claude mcp add` to add a server.\n"

# a fake `claude` for `claude mcp list`: prints $FAKE_MCP_LIST (a file), exits $FAKE_MCP_EXIT, records its cwd
FAKE_CLAUDE = """#!/bin/sh
[ "$1 $2" = "mcp list" ] || { echo "unexpected: $*" >&2; exit 9; }
pwd > "$FAKE_MCP_CALLED"
cat "$FAKE_MCP_LIST"
exit "${FAKE_MCP_EXIT:-0}"
"""


def load_llm():
    spec = importlib.util.spec_from_file_location("llm_deny_t", ROOT / "bin" / "llm.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Fixture(unittest.TestCase):
    """A fake HOME with the user's config, a project folder with its own .mcp.json, a fake claude."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.ws = self.tmp / "ws" / "proj"
        (self.ws / "sub").mkdir(parents=True)
        self.write_json(self.home / ".claude.json", {
            "mcpServers": {"fixture-prod-db": {"command": "uvx"}, "fixture-tracker": {"command": "npx"}},
            "projects": {str(self.ws): {"mcpServers": {"fixture-local": {"command": "x"}}},
                         "/elsewhere": {"mcpServers": {"other-local": {"command": "y"}}, "allowedTools": []}}})
        self.write_json(self.ws / ".mcp.json", {"mcpServers": {"fixture-project": {"url": "https://p.example.com"}}})
        self.listing = self.tmp / "list.txt"
        self.listing.write_text(LIST)
        self.called = self.tmp / "called"
        exe = self.tmp / "claude"
        exe.write_text(FAKE_CLAUDE)
        exe.chmod(0o755)
        p = mock.patch.object(runtime_claude, "_exe", lambda: str(exe))
        p.start()
        self.addCleanup(p.stop)
        # no machine's managed file
        p = mock.patch.object(runtime_claude, "MANAGED_MCP", (self.tmp / "managed-mcp.json",))
        p.start()
        self.addCleanup(p.stop)
        self.env = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "FAKE_MCP_LIST": str(self.listing), "FAKE_MCP_CALLED": str(self.called)}

    def write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def task(self, allow=None, extra=(), cwd=None, env=None, spec=None):
        env = {**self.env, **(env or {})}
        if allow is not None:
            env["AGENT_JOB_ALLOW"] = json.dumps(allow)
        spec = spec or {"permission_mode": "auto", "allow_from_env": "AGENT_JOB_ALLOW", "tool_servers": "allowed_only"}
        return runtime_ports.Task(point="agent-job", prompt="p", spec=spec, cwd=str(cwd or self.ws), env=env,
                                  extra=tuple(extra))


class ParseList(unittest.TestCase):
    def test_every_kind_of_server_is_read(self):
        self.assertEqual(runtime_claude.parse_mcp_list(LIST), [
            "claude.ai Example Docs", "claude.ai Jobs", "plugin:example:tools", "plugin:design:google calendar",
            "fixture-prod-db", "fixture-tracker"])

    def test_nothing_configured(self):
        self.assertEqual(runtime_claude.parse_mcp_list(EMPTY), [])

    def test_colour_codes_are_ignored(self):
        out = "Checking MCP server health…\n\n\x1b[1mfixture-prod-db\x1b[22m: uvx x - \x1b[32m✔ Connected\x1b[39m\n"
        self.assertEqual(runtime_claude.parse_mcp_list(out), ["fixture-prod-db"])

    def test_output_in_another_shape_fails_closed(self):
        for out in ("", "something else entirely\n", "Checking MCP server health…\n\nfixture-prod-db (stdio)\n"):
            with self.subTest(out=out), self.assertRaises(ValueError):
                runtime_claude.parse_mcp_list(out)

    def test_ids_are_the_names_in_tool_names(self):
        self.assertEqual(runtime_claude.mcp_id("claude.ai Example Docs"), "claude_ai_Example_Docs")
        self.assertEqual(runtime_claude.mcp_id("plugin:design:google calendar"), "plugin_design_google_calendar")
        self.assertEqual(runtime_claude.mcp_id("fixture-prod-db"), "fixture-prod-db")


class ComputedDeny(Fixture):
    def deny(self, task):
        return policy.tool_server_denials(runtime_claude.ambient_servers(task), runtime_claude.servers_named(task))

    def test_global_project_local_plugin_and_connectors_all_denied_but_the_allowed_one(self):
        allow = ["mcp__claude_ai_Jobs__jobs__get_messages", "mcp__claude_ai_Jobs__jobs__list_offers", "ToolSearch",
                 "Read(./state/**)"]
        self.assertEqual(self.deny(self.task(allow)), [
            "claude_ai_Example_Docs",                    # the account's connectors
            "fixture-local",                             # local scope (this folder)
            "fixture-prod-db", "fixture-project",        # the user's; the folder's .mcp.json
            "fixture-tracker",
            "other-local",                               # another folder's local scope: denying it changes nothing
            "plugin_design_google_calendar", "plugin_example_tools"])   # plugins'
        self.assertEqual(pathlib.Path(self.called.read_text().strip()).resolve(), self.ws,
                         "listed from the run's own folder (project-scoped servers depend on it)")

    def test_no_allow_list_denies_every_server(self):
        self.assertIn("claude_ai_Jobs", self.deny(self.task()))
        self.assertIn("fixture-prod-db", self.deny(self.task([])))

    def test_server_level_allow_and_raw_names(self):
        named = runtime_claude.servers_named(self.task(["mcp__fixture-tracker", "mcp__plugin:example:tools__search"]))
        self.assertEqual(named, ["fixture-tracker", "plugin_example_tools"])

    def test_a_server_added_tomorrow_is_denied_tomorrow(self):
        self.listing.write_text(LIST.replace("fixture-tracker:", "fixture-new: https://n.example.com (HTTP) - "
                                                                 "✔ Connected\nfixture-tracker:"))
        self.assertIn("fixture-new", self.deny(self.task(["mcp__claude_ai_Jobs__x"])))

    def test_parent_folders_mcp_json_counts(self):
        self.assertIn("fixture-project", self.deny(self.task([], cwd=self.ws / "sub")))

    def test_config_dir_override(self):
        cfg = self.tmp / "profile"
        self.write_json(cfg / ".claude.json", {"mcpServers": {"fixture-prod-db": {}}})
        self.assertNotIn("other-local", runtime_claude.ambient_servers(self.task(env={"CLAUDE_CONFIG_DIR": str(cfg)})))

    def test_nothing_configured_anywhere(self):
        (self.home / ".claude.json").unlink()
        (self.ws / ".mcp.json").unlink()
        self.listing.write_text(EMPTY)
        self.assertEqual(runtime_claude.ambient_servers(self.task([])), [])

    def test_sealed_run_loads_none_and_lists_nothing(self):
        self.assertIsNone(runtime_claude.ambient_servers(self.task([], extra=["--strict-mcp-config"])))
        spec = {"tool_servers": "allowed_only", "extra_args": ["--strict-mcp-config"]}
        self.assertIsNone(runtime_claude.ambient_servers(self.task([], spec=spec)))
        self.assertFalse(self.called.exists())


class FailClosed(Fixture):
    def assert_closed(self, task=None):
        with self.assertRaises(ValueError):
            runtime_claude.ambient_servers(task or self.task([]))

    def test_unreadable_user_config(self):
        (self.home / ".claude.json").write_text("{not json")
        self.assert_closed()

    def test_user_config_not_an_object(self):
        (self.home / ".claude.json").write_text("[]")
        self.assert_closed()

    def test_unreadable_project_config(self):
        (self.ws / ".mcp.json").write_text("{")
        self.assert_closed()

    def test_list_command_fails(self):
        self.assert_closed(self.task([], env={"FAKE_MCP_EXIT": "1"}))

    def test_list_command_missing(self):
        with mock.patch.object(runtime_claude, "_exe", lambda: str(self.tmp / "no-such-claude")):
            self.assert_closed()

    def test_list_in_a_shape_it_cannot_read(self):
        self.listing.write_text("Checking MCP server health…\n\nfixture-prod-db\n")
        self.assert_closed()

    def test_list_missing_a_server_the_user_configured(self):
        # its output changed under us (or it hides servers): never deny from a partial list
        self.listing.write_text(LIST.replace("fixture-tracker:", "fixture-other:"))
        self.assert_closed()


class Flags(Fixture):
    def test_each_denied_server_is_one_whole_server_rule(self):
        cmd = runtime_claude._flags(self.task(["mcp__claude_ai_Jobs__x"])._replace(
            deny_servers=("fixture-prod-db", "plugin_example_tools")))
        i = cmd.index("--disallowedTools")
        self.assertEqual(cmd[i + 1:i + 3], ["mcp__fixture-prod-db", "mcp__plugin_example_tools"])
        # the built-in resource tools would reach a denied server's resources by name
        self.assertEqual(set(cmd[i + 3:i + 6]), set(runtime_claude.MCP_RESOURCE_TOOLS))
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1], "mcp__claude_ai_Jobs__x")

    def test_nothing_to_deny_adds_nothing(self):
        cmd = runtime_claude._flags(self.task(["mcp__claude_ai_Jobs__x"], extra=["--strict-mcp-config"]))
        self.assertNotIn("--disallowedTools", cmd)
        self.assertEqual(cmd[-1], "--strict-mcp-config")


@unittest.skipIf(yaml is None, "needs PyYAML")
class LlmFailsClosed(unittest.TestCase):
    """llm.py with the fake runtime: the deny reaches the run; when the servers can't be told the run never starts."""

    def setUp(self):
        import hubconf
        self.llm = load_llm()
        runtime_fake.reset()
        self.addCleanup(runtime_fake.reset)
        self.failed, self.alerts = [], []
        tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.prompt = tmp / "prompt.md"
        self.prompt.write_text("p")
        for p in (mock.patch.object(hubconf, "_cfg", {"runtime": {"default": "fake"}}),
                  mock.patch.object(self.llm, "job_failed", lambda point, reason: self.failed.append(reason)),
                  mock.patch.object(self.llm, "tool_servers_alert", lambda point, why: self.alerts.append(why)),
                  mock.patch.dict(os.environ, {"AGENT_JOB_ALLOW": json.dumps(["mcp__kept__x"])})):
            p.start()
            self.addCleanup(p.stop)

    def run_llm(self, dry=False):
        a = argparse.Namespace(cmd="run", point="agent-job", cwd=None, model=None, prompt_file=str(self.prompt),
                               dry_run=dry, extra=[])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.llm.run(a)
        return code, out.getvalue(), err.getvalue()

    def test_the_deny_reaches_the_run(self):
        runtime_fake.set_ambient(["fixture-prod-db", "kept", "claude_ai_Other"], named=["kept"])
        code, _out, _err = self.run_llm()
        self.assertEqual(code, 0)
        self.assertEqual(runtime_fake._ran[-1].deny_servers, ("claude_ai_Other", "fixture-prod-db"))
        self.assertEqual(self.alerts, [None], "a run that could tell clears an earlier alert")

    def test_sealed_run_gets_nothing_and_no_alert(self):
        runtime_fake.set_ambient(None)
        self.assertEqual(self.run_llm()[0], 0)
        self.assertEqual(runtime_fake._ran[-1].deny_servers, ())
        self.assertEqual(self.alerts, [])

    def test_cannot_tell_means_no_run_job_failed_and_alert(self):
        runtime_fake.set_ambient(None, error="cannot read the config")
        code, out, _err = self.run_llm()
        self.assertEqual(code, 1)
        self.assertEqual(runtime_fake._ran, [], "the run never started")
        self.assertTrue(json.loads(out)["is_error"])
        self.assertIn("cannot read the config", json.loads(out)["llm_error"])
        self.assertEqual(len(self.failed), 1)
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("cannot read the config", self.alerts[0])

    def test_dry_run_reports_without_alerting(self):
        runtime_fake.set_ambient(None, error="cannot read the config")
        code, _out, err = self.run_llm(dry=True)
        self.assertEqual(code, 2)
        self.assertIn("cannot read the config", err)
        self.assertEqual((self.failed, self.alerts), ([], []))

    def test_a_runtime_that_cannot_tell_refuses(self):
        runtime_fake.set_ambient(["fixture-prod-db"])
        for missing in (("ambient_servers", "servers_named"), ("ambient_servers",)):   # absent; half there (a bug)
            with self.subTest(missing=missing), contextlib.ExitStack() as stack:
                for fn in missing:
                    stack.enter_context(mock.patch.object(runtime_fake, fn, None))
                code, _out, _err = self.run_llm()
            self.assertEqual(code, 1)
            self.assertEqual(runtime_fake._ran, [])


class Declared(unittest.TestCase):
    @unittest.skipIf(yaml is None, "needs PyYAML")
    def test_the_agent_job_points_declare_it(self):
        import yaml as y
        points = y.safe_load((ROOT / "contracts" / "llm-points.yaml").read_text())["points"]
        for name in ("agent-job", "agent-job-event", "mailbox-escalation"):
            self.assertEqual(points[name].get("tool_servers"), "allowed_only", name)

    def test_policy_is_a_set_difference(self):
        self.assertEqual(policy.tool_server_denials(["b", "a", "c", "a"], ["c"]), ["a", "b"])
        self.assertEqual(policy.tool_server_denials([], ["c"]), [])


if __name__ == "__main__":
    unittest.main()
