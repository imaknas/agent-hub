"""The Agent Client Protocol runtime (bin/runtime_acp.py; docs/design/runtime-acp.md) against a fake ACP agent
speaking JSON-RPC over stdio (tests/fixtures/fake_acp_agent.py). The agent and auth contracts run in
tests/test_runtime_contracts.py; this file covers what is ACP's own.

Live runs against real agents are opt-in and never part of the gate: ACP_LIVE=1, with ACP_LIVE_COMMAND a JSON argv
of an ACP agent (e.g. '["npx", "-y", "<acp agent package>@<version>"]').
"""
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hubconf  # noqa: E402
import runtime_ports  # noqa: E402

FAKE = ROOT / "tests" / "fixtures" / "fake_acp_agent.py"

# the assertions read the English texts: pin the UI language (an instance's hub.json may set another)
_LANG = mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "en"})


def setUpModule():
    _LANG.start()


def tearDownModule():
    _LANG.stop()


class AcpCase(unittest.TestCase):
    def setUp(self):
        self._secrets = hubconf.secrets_dir
        secrets = pathlib.Path(tempfile.mkdtemp()).resolve()
        hubconf.secrets_dir = lambda: secrets
        self.ws = hubconf.throwaway_copy("test")   # where propose_apply makes its copy
        self.ws.mkdir()
        self.tmp = self.ws.parent
        self.log = self.tmp / "agent.log"
        self.conf = {"agent": "fake", "agents": {"fake": {"command": [sys.executable, str(FAKE)]}}}
        self._get = hubconf.get
        hubconf.get = lambda k, default=None: self.conf if k == "runtime.acp" else self._get(k, default)
        import runtime_acp
        self.m = runtime_acp

    def tearDown(self):
        hubconf.get = self._get
        hubconf.secrets_dir = self._secrets

    def run_mode(self, mode, **kw):
        env = {**os.environ, "FAKE_ACP_MODE": mode, "FAKE_ACP_LOG": str(self.log),
               **({} if kw.pop("unconfined", False) else {"HUB_THROWAWAY_COPY": str(self.ws)})}
        task = runtime_ports.Task(point=kw.pop("point", "mailbox-propose"), prompt="hi", spec={}, cwd=str(self.ws), env=env,
                                  timeout_s=kw.pop("timeout_s", 30), **kw)
        return self.m.run(task)

    def received(self) -> list:
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []


class Run(AcpCase):
    def test_answer_is_the_last_message_not_thoughts(self):
        res = self.run_mode("ok", output_schema={"type": "object"})
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.text, 'done {"x": 1}')
        self.assertEqual(res.structured_output, {"x": 1})
        self.assertEqual((res.session_id, res.cost_usd, res.turns, res.stop), ("sess_fake_1", 0.5, None, "end_turn"))
        self.assertEqual(res.denials, ())

    def test_session_is_opened_in_the_absolute_workdir_with_no_mcp_servers(self):
        self.run_mode("ok")
        new = next(m for m in self.received() if m.get("method") == "session/new")
        self.assertEqual(new["params"], {"cwd": str(self.ws.resolve()), "mcpServers": []})
        init = next(m for m in self.received() if m.get("method") == "initialize")
        self.assertEqual((init["params"]["protocolVersion"], init["params"]["clientCapabilities"]), (1, {}))

    def test_schema_goes_into_the_prompt(self):
        self.run_mode("ok", output_schema={"type": "object", "required": ["x"]})
        prompt = next(m for m in self.received() if m.get("method") == "session/prompt")["params"]["prompt"]
        self.assertEqual(len(prompt), 1)
        self.assertIn('"required": ["x"]', prompt[0]["text"])

    def test_unadvertised_client_methods_are_refused(self):
        res = self.run_mode("fs", output_schema={"type": "object"})
        self.assertEqual(res.structured_output, {"fs_error": -32601})

    def test_unattended_rules_deny_outside_writes_commands_and_unknown_targets(self):
        res = self.run_mode("outside", output_schema={"type": "object"})
        self.assertEqual(res.structured_output["outcomes"], [{"outcome": "selected", "optionId": "r1"}] * 2 + [
            {"outcome": "selected", "optionId": "a1"}],
                         "outside the folder and a command are refused once; a read naming no place works in cwd")
        self.assertEqual([d["kind"] for d in res.denials], ["write_file", "run_command"])

    def test_never_remembers_an_allow(self):
        self.run_mode("ok")
        answers = [m for m in self.received() if "result" in m and "outcome" in (m.get("result") or {})]
        self.assertEqual([a["result"]["outcome"]["optionId"] for a in answers], ["a1"])

    def test_incomplete(self):
        res = self.run_mode("incomplete")
        self.assertTrue(res.ok and res.incomplete)
        self.assertEqual(res.stop, "max_turn_requests")

    def test_login_required_is_a_login_error(self):
        res = self.run_mode("auth")
        self.assertFalse(res.ok)
        self.assertTrue(self.m.is_login_error(res.error), res.error)
        self.assertIn("fake-login", res.error)

    def test_configured_auth_method_is_used(self):
        self.conf["agents"]["fake"]["auth_method"] = "fake-login"
        res = self.run_mode("auth")
        self.assertTrue(res.ok, res.error)

    def test_refusal_is_a_result_but_not_complete(self):
        res = self.run_mode("refusal")
        self.assertTrue(res.ok and res.incomplete)
        self.assertEqual(res.stop, "refusal")

    def test_no_allowed_option_cancels_and_says_it_was_the_platform(self):
        res = self.run_mode("always")
        self.assertFalse(res.ok)
        self.assertIn("allow_once", res.error)
        self.assertEqual([d["kind"] for d in res.denials], ["read"])

    def test_configured_mode_is_set_before_the_prompt(self):
        self.conf["agents"]["fake"]["mode"] = "ask"
        self.assertTrue(self.run_mode("ok").ok)
        methods = [m.get("method") for m in self.received() if m.get("method")]
        self.assertLess(methods.index("session/set_mode"), methods.index("session/prompt"))
        self.conf["agents"]["fake"]["mode"] = "careful"
        res = self.run_mode("ok")
        self.assertFalse(res.ok, "a mode the agent does not offer refuses the run")
        self.assertIn("careful", res.error)

    def test_leaving_the_configured_mode_fails_the_run(self):
        self.conf["agents"]["fake"]["mode"] = "ask"
        res = self.run_mode("modedrift")
        self.assertFalse(res.ok)
        self.assertIn("auto", res.error)
        self.assertTrue(any(m.get("method") == "session/cancel" for m in self.received()))

    def test_a_dependent_change_that_resets_the_mode_is_caught_before_the_prompt(self):
        self.conf["agents"]["fake"]["mode"] = "ask"
        res = self.run_mode("depmode", model="big")
        self.assertTrue(res.ok, "model first, then mode: the reset happens before the mode is set")
        methods = [m.get("params", {}).get("configId") for m in self.received() if m.get("method") == "session/set_config_option"]
        self.assertEqual(methods, ["model", "mode"])

    def test_a_model_given_in_the_command_is_held_too(self):
        self.conf["agents"]["fake"]["command"] = [sys.executable, str(FAKE), "--model", "{model}"]
        res = self.run_mode("fallback", model="small")
        self.assertTrue(res.ok, "the agent reports the model it was started with")
        res = self.run_mode("fallback", model="big")
        self.assertFalse(res.ok, "it reports another model than the command gave: refused before the prompt")

    def test_a_mid_turn_model_fallback_fails_the_run(self):
        self.conf["agents"]["fake"]["mode"] = "ask"
        res = self.run_mode("fallback", model="big")
        self.assertFalse(res.ok)
        self.assertIn("small", res.error)

    def test_the_answer_may_come_before_a_closing_message(self):
        res = self.run_mode("jsonfirst", output_schema={"type": "object"})
        self.assertEqual((res.text, res.structured_output), ("Done.", {"x": 1}))

    def test_a_late_status_update_does_not_split_the_answer(self):
        self.assertEqual(self.run_mode("lateupdate", output_schema={"type": "object"}).structured_output, {"x": 1})

    def test_login_method_must_be_one_the_agent_does_itself(self):
        self.conf["agents"]["fake"]["auth_method"] = "nope"
        res = self.run_mode("ok")
        self.assertFalse(res.ok)
        self.assertIn("fake-login", res.error)

    def test_cancel_is_sent_before_answering_cancelled(self):
        self.run_mode("always")
        msgs = self.received()
        cancel = next(i for i, m in enumerate(msgs) if m.get("method") == "session/cancel")
        answer = next(i for i, m in enumerate(msgs) if (m.get("result") or {}).get("outcome") == {"outcome": "cancelled"})
        self.assertLess(cancel, answer)

    def test_missing_stop_reason_is_not_success(self):
        res = self.run_mode("nostop")
        self.assertFalse(res.ok)

    def test_malformed_message_fails_cleanly(self):
        res = self.run_mode("malformed")
        self.assertFalse(res.ok)
        self.assertIn("does not follow ACP", res.error)

    def test_cost_survives_an_update_without_one(self):
        self.assertEqual(self.run_mode("costkeep").cost_usd, 0.25)

    def test_other_errors_are_not_login_errors(self):
        self.assertFalse(self.m.is_login_error("the agent exited (1): auth_required: forged by the agent"))
        res = self.run_mode("fail")
        self.assertFalse(res.ok)
        self.assertFalse(self.m.is_login_error(res.error), "only the protocol's -32000 counts, not wording")

    def test_protocol_version_mismatch_fails_loudly(self):
        res = self.run_mode("version")
        self.assertFalse(res.ok)
        self.assertIn("2", res.error)

    def test_timeout_cancels_and_kills(self):
        t0 = time.monotonic()
        res = self.run_mode("hang", timeout_s=2)
        self.assertFalse(res.ok)
        self.assertIn("2", res.error)
        self.assertLess(time.monotonic() - t0, 15)
        self.assertTrue(any(m.get("method") == "session/cancel" for m in self.received()))

    def test_closes_the_session_when_the_agent_can(self):
        self.run_mode("ok")
        self.assertTrue(any(m.get("method") == "session/close" for m in self.received()))

    def test_model_through_a_config_option(self):
        res = self.run_mode("model", model="big")
        self.assertTrue(res.ok, res.error)
        res = self.run_mode("model", model="huge")
        self.assertFalse(res.ok, "a model the agent does not offer is never silently replaced")
        self.assertIn("huge", res.error)

    def test_model_through_the_command(self):
        self.conf["agents"]["fake"]["command"] += ["--model", "{model}"]
        self.assertEqual(self.m._argv(self.conf["agents"]["fake"]["command"], None, "/w")[-1], str(FAKE))
        self.assertEqual(self.m._argv(self.conf["agents"]["fake"]["command"], "big", "/w")[-2:], ["--model", "big"])
        self.assertEqual(self.m._argv(["agent", "-c", "model={model}", "--x"], None, "/w"), ["agent", "--x"])
        self.assertEqual(self.m._argv(["agent", "-c", "model={model}"], "m1", "/w"), ["agent", "-c", "model=m1"])

    def test_missing_command(self):
        self.conf["agents"]["fake"]["command"] = [str(self.tmp / "no-such-agent")]
        res = self.run_mode("ok")
        self.assertFalse(res.ok)
        self.assertIn("no-such-agent", res.error)

    def test_not_configured_says_so(self):
        self.conf = {}
        res = self.run_mode("ok")
        self.assertFalse(res.ok)
        self.assertIn("runtime.acp", res.error)

    def test_describe(self):
        d = self.m.describe(runtime_ports.Task(point="p", prompt="hi", spec={}, cwd=str(self.ws)))
        self.assertEqual(d[:2], [sys.executable, str(FAKE)])
        self.assertTrue(all(isinstance(x, str) for x in d))


class Confinement(AcpCase):
    def test_refuses_a_directory_the_platform_did_not_mark(self):
        res = self.run_mode("ok", unconfined=True)
        self.assertFalse(res.ok)
        self.assertIn("throwaway worktree", res.error)
        self.assertEqual(self.received(), [], "refused before the agent is started")

    def test_refuses_another_point_even_when_marked(self):
        res = self.run_mode("ok", point="librarian-state")
        self.assertFalse(res.ok)
        self.assertEqual(self.received(), [])

    def test_refuses_the_same_shape_in_the_system_temp_folder(self):
        """It's every sandboxed project's to write: a copy there would be one a project made."""
        other = pathlib.Path(tempfile.mkdtemp(prefix="hub-propose-test-")) / "ws"
        other.mkdir()
        res = self.m.run(runtime_ports.Task(point="mailbox-propose", prompt="hi", spec={}, cwd=str(other), timeout_s=30,
                                            env={**os.environ, "HUB_THROWAWAY_COPY": str(other)}))
        self.assertFalse(res.ok)
        self.assertIn(str(other.resolve()), res.error)

    def test_refuses_a_marked_directory_that_is_not_the_platforms_copy(self):
        other = pathlib.Path(tempfile.mkdtemp()) / "ws"
        other.mkdir()
        self.ws = other
        res = self.run_mode("ok")
        self.assertFalse(res.ok, "a checkout elsewhere is not the platform's copy, whatever the variable says")
        self.assertEqual(self.received(), [])

    def test_refuses_to_continue_a_conversation(self):
        res = self.run_mode("ok", extra=("--resume", "x"))
        self.assertFalse(res.ok)
        self.assertEqual(self.received(), [])

    def test_no_workdir_means_the_process_directory_checked_the_same_way(self):
        """The librarian and consolidate set the directory through the subprocess, not --cwd."""
        env = {**os.environ, "FAKE_ACP_MODE": "ok", "FAKE_ACP_LOG": str(self.log)}
        saved = os.getcwd()
        os.chdir(self.ws)
        try:
            res = self.m.run(runtime_ports.Task(point="mailbox-propose", prompt="hi", spec={}, env=env, timeout_s=30))
            self.assertFalse(res.ok, "an unmarked process directory is refused, not swapped for an empty one")
            self.assertIn(os.path.realpath(self.ws), res.error)
            res = self.m.run(runtime_ports.Task(point="mailbox-propose", prompt="hi", spec={}, timeout_s=30,
                                                env={**env, "HUB_THROWAWAY_COPY": str(self.ws)}))
            self.assertTrue(res.ok, res.error)
        finally:
            os.chdir(saved)


class Config(AcpCase):
    def test_models_follow_the_selected_agent(self):
        self.conf["agents"]["fake"]["models"] = {"strong": "big"}
        self.assertEqual(runtime_ports.models("acp"), {"strong": "big"})
        self.conf = {}
        self.assertEqual(runtime_ports.models("acp"), {})

    def test_an_empty_key_file_is_missing_for_doctor_and_runs_alike(self):
        import tempfile as tf
        d = pathlib.Path(tf.mkdtemp())
        (d / "k").write_text("\n")
        self.conf["agents"]["fake"]["credential"] = {"env": "K", "file": "k"}
        saved = hubconf.secrets_dir
        hubconf.secrets_dir = lambda: d
        try:
            self.assertFalse(self.m.credentials()[0].present)
            self.assertIsNone(self.m.auth_env("token", {})[0])
        finally:
            hubconf.secrets_dir = saved

    def test_doctor_warns_when_the_pinned_agent_has_no_mode(self):
        cfg = {"runtime": {"acp": self.conf}, "llm": {"points": {"mailbox-propose": {"runtime": "acp"}}},
               "mailbox": {"handler": "propose"}}
        self.assertEqual(len(self.m.warnings(cfg)), 1)
        self.assertEqual(len(self.m.warnings({**cfg, "mailbox": {}})), 2, "a pin that never runs is named too")
        self.conf["agents"]["fake"]["mode"] = "ask"
        self.assertEqual(self.m.warnings(cfg), [])

    def test_own_login_when_no_key_is_configured(self):
        env, why = self.m.auth_env("token", {"PATH": "/usr/bin"})
        self.assertEqual((env, why), ({"PATH": "/usr/bin"}, ""))
        (c,) = self.m.credentials()
        self.assertTrue(c.present and c.mode == "token" and c.note)


class Permission(unittest.TestCase):
    """The unattended rules: the request carries only the id; what the tool does came earlier."""
    OPTS = [{"optionId": "a", "kind": "allow_once"}, {"optionId": "r", "kind": "reject_once"}]

    def decide(self, call: dict) -> str:
        import runtime_acp
        ws = tempfile.mkdtemp()
        t = runtime_acp._Turn(ws)
        t.update({"update": {"sessionUpdate": "tool_call", "toolCallId": "t", **call(ws)}})
        return t.permission({"toolCall": {"toolCallId": "t"}, "options": self.OPTS})["outcome"].get("optionId")

    def test_edit_known_from_its_diff(self):
        self.assertEqual(self.decide(lambda ws: {"kind": "edit", "content": [
            {"type": "diff", "path": os.path.join(ws, "a.md"), "newText": "x"}]}), "a")

    def test_move_needs_both_ends_inside(self):
        self.assertEqual(self.decide(lambda ws: {"kind": "move", "rawInput": {
            "source": os.path.join(ws, "a"), "destination": "/tmp/elsewhere/b"}}), "r")

    def test_escaping_through_dotdot_is_outside(self):
        self.assertEqual(self.decide(lambda ws: {"kind": "read", "locations": [{"path": os.path.join(ws, "..", "x")}]}),
                         "r")

    def test_home_and_variable_paths_are_not_inside(self):
        self.assertEqual(self.decide(lambda ws: {"kind": "edit", "rawInput": {"path": "~/.ssh/config"}}), "r")
        self.assertEqual(self.decide(lambda ws: {"kind": "edit", "rawInput": {"path": "$HOME/x"}}), "r")

    def test_a_write_target_under_any_key_counts(self):
        self.assertEqual(self.decide(lambda ws: {"kind": "move", "rawInput": {
            "path": os.path.join(ws, "a"), "new_path": "/etc/elsewhere"}}), "r")

    def test_content_is_not_a_path(self):
        self.assertEqual(self.decide(lambda ws: {"kind": "edit", "rawInput": {
            "file_path": os.path.join(ws, "a.c"), "old_string": "x", "new_string": "// a comment\n~/not/a/path"}}), "a")

    def test_the_git_directory_is_not_inside(self):
        self.assertEqual(self.decide(lambda ws: {"kind": "edit", "rawInput": {"path": os.path.join(ws, ".git", "config")}}),
                         "r")

    def test_a_read_target_under_a_place_key_counts(self):
        self.assertEqual(self.decide(lambda ws: {"kind": "read", "rawInput": {"absolute_path": os.path.join(ws, "a")}}),
                         "a")

    def test_a_refusal_may_be_for_good_an_allow_never_is(self):
        import runtime_acp
        t = runtime_acp._Turn(tempfile.mkdtemp())
        t.update({"update": {"sessionUpdate": "tool_call", "toolCallId": "t", "kind": "fetch"}})
        out = t.permission({"toolCall": {"toolCallId": "t"}, "options": [{"optionId": "ra", "kind": "reject_always"}]})
        self.assertEqual(out["outcome"].get("optionId"), "ra")

    def test_a_write_naming_no_target_is_refused(self):
        self.assertEqual(self.decide(lambda ws: {"kind": "edit"}), "r")

    def test_places_hidden_in_lists_nested_objects_or_uris_are_found(self):
        for call in ({"kind": "read", "rawInput": {"paths": ["/etc/passwd"]}},
                     {"kind": "read", "rawInput": {"file": "~/.ssh/id_rsa"}},
                     {"kind": "read", "rawInput": {"uri": "file:///etc/passwd"}},
                     {"kind": "search", "rawInput": {"dir": "/"}},
                     {"kind": "search", "rawInput": {"pattern": "x", "where": "../../etc"}},
                     {"kind": "edit", "locations": [{"path": "a.md"}], "rawInput": {"edits": [{"file": "/etc/x"}]}},
                     {"kind": "edit", "rawInput": {"path": ".GIT/config"}}):
            with self.subTest(call=call):
                self.assertEqual(self.decide(lambda ws, c=call: c), "r")
        self.assertEqual(self.decide(lambda ws: {"kind": "search", "rawInput": {"pattern": "TODO"}}), "a",
                         "a search by pattern alone works in cwd")

    def test_fetch_and_unknown_kinds_are_refused(self):
        self.assertEqual(self.decide(lambda ws: {"kind": "fetch", "rawInput": {"url": "https://example.com"}}), "r")
        self.assertEqual(self.decide(lambda ws: {"kind": "switch_mode"}), "r")


class Routing(unittest.TestCase):
    """hub.json llm.points.<point>.runtime puts one agent-shaped point on another runtime (e.g. acp for propose)."""

    def test_point_runtime(self):
        import llm
        saved = hubconf.get
        conf = {"points": {"mailbox-propose": {"runtime": "acp"}}}
        hubconf.get = lambda k, default=None: conf if k == "llm" else saved(k, default)
        try:
            self.assertEqual(llm.point_runtime("mailbox-propose", {}), "acp")
            self.assertEqual(llm.point_runtime("librarian", {"runtime": "fake"}), "fake")
        finally:
            hubconf.get = saved

    def test_config_check(self):
        import completion
        for point, pin, bad in (("mailbox-propose", {"runtime": "acp"}, False),
                                ("mailbox-propose", {"runtime": "nosuch"}, True),
                                ("librarian-state", {"runtime": "acp"}, True),     # not run in a throwaway worktree
                                ("librarian-state", {"runtime": "fake"}, False),
                                ("reflect-week", {"runtime": "fake"}, True),       # completion points use providers
                                ("mailbox-conversation", {"runtime": "fake"}, True),   # resumed with the default's args
                                ("lease-session", {"runtime": "fake"}, True)):     # streamed
            errs = completion.config_errors({"points": {point: pin}})
            self.assertEqual(bool(errs), bad, (point, pin, errs))

    def test_acp_settings_checked(self):
        import runtime_acp
        ok = {"runtime": {"acp": {"agent": "a", "agents": {"a": {"command": ["x"], "env": {"RUST_LOG": "info"}}}}}}
        self.assertEqual(runtime_acp.config_errors(ok), [])
        self.assertEqual(runtime_acp.config_errors({}), [])
        bad = {"runtime": {"acp": {"agent": "b", "agents": {"a": {"command": ["x"], "credential": {"env": "K"},
                                                                  "env": {"PATH": "/x", "HUB_THROWAWAY_COPY": "/",
                                                                          "K": "v", "TOKEN": "sk-abcdef"}}}}}}
        errs = runtime_acp.config_errors(bad)
        self.assertEqual(len(errs), 5, errs)
        unset = {"llm": {"points": {"mailbox-propose": {"runtime": "acp"}}}}
        self.assertEqual(len(runtime_acp.config_errors(unset)), 1, "used but not set up")


class ConfigCheckDefault(unittest.TestCase):
    """bin/config-check refuses runtime.default: acp (it runs only in the platform's throwaway worktree, so as the
    default it would run nothing) and names the points it can be pinned on; acp pinned on such a point passes. Runs
    the real script as a child with a config, state and HOME of its own."""

    def check(self, cfg: dict, lang: str):
        import shutil
        import subprocess
        tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for d in ("config", "state", "data", "secrets", "home"):
            (tmp / d).mkdir()
        base = {"v": 1, "registry_dir": "projects", "owner": {"address": "owner", "tag": "Alex"}}
        (tmp / "config" / "hub.json").write_text(json.dumps({**base, **cfg}))
        env = {**{k: v for k, v in os.environ.items() if not k.startswith(("AGENT_HUB_", "BRUNNA_")) or k in ("AGENT_HUB_PYTHON", "BRUNNA_PYTHON")},
               "AGENT_HUB_ROOT": str(ROOT), "AGENT_HUB_CONFIG": str(tmp / "config"), "AGENT_HUB_STATE": str(tmp / "state"),
               "AGENT_HUB_DATA": str(tmp / "data"), "AGENT_HUB_SECRETS": str(tmp / "secrets"), "HOME": str(tmp / "home"),
               "AGENT_HUB_LANG_UI": lang}
        return subprocess.run([sys.executable, str(ROOT / "bin" / "config-check")], cwd=tmp, env=env,
                              capture_output=True, text=True, timeout=120)

    # each throwaway-only runtime, set up properly, so the refusal is the only problem (runtime_command since
    # docs/design/runtime-codex.md step 0)
    THROWAWAY = {"acp": {"agent": "a", "agents": {"a": {"command": ["x"]}}}, "command": {"run": ["x", "-"]}}

    def test_a_throwaway_only_runtime_as_the_default_is_refused(self):
        import i18n
        for name, conf in self.THROWAWAY.items():
            for lang in ("en", "zh-Hant"):
                with self.subTest(name, lang=lang):
                    r = self.check({"runtime": {"default": name, name: conf}}, lang)
                    self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                    self.assertEqual(len(r.stderr.strip().splitlines()), 1, "the only problem: " + r.stderr)
                    want = i18n.translate("config-check", (
                        "{settings}: runtime.default `{name}` runs only {points} in the platform's throwaway worktree (it can't be "
                        "held to a judgment point's limits anywhere else), so as the default it would run nothing (dispatch needs "
                        "session state, every other point is refused); set runtime.default to a runtime with session state, or "
                        "to none, and pin {name} on {points} (llm.points.<point>.runtime, with mailbox.handler: propose)"),
                        {"settings": "hub.json", "name": name,   # the file this test writes
                         "points": ", ".join(runtime_ports.THROWAWAY_POINTS)}, lang)
                    self.assertIn(want, r.stderr)

    def test_pinned_on_a_throwaway_point_passes(self):
        for name, conf in self.THROWAWAY.items():
            for default in ("fake", "none"):
                with self.subTest(name, default=default):
                    cfg = {"runtime": {"default": default, name: conf}, "mailbox": {"handler": "propose"},
                           "llm": {"points": {p: {"runtime": name} for p in runtime_ports.THROWAWAY_POINTS}}}
                    r = self.check(cfg, "en")
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_pinned_on_another_point_is_refused(self):
        for name, conf in self.THROWAWAY.items():
            with self.subTest(name):
                r = self.check({"runtime": {"default": "fake", name: conf},
                                "llm": {"points": {"librarian-state": {"runtime": name}}}}, "en")
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("librarian-state", r.stderr)


class LoginCheck(unittest.TestCase):
    def test_throwaway_only_default_is_not_checked_and_never_alerts(self):
        """login-check is not a confined point, so with runtime.default: acp every request would be refused and
        turn into a recurring false alert"""
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location("ccl", ROOT / "bin" / "check-claude-login.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        import contextlib
        import io
        sent, out = [], io.StringIO()
        with mock.patch.object(runtime_ports, "default_name", lambda: "acp"), \
                mock.patch.object(m.alert, "once", lambda key, problems, ok: sent.append(problems)), \
                mock.patch.object(m, "ask", lambda env: self.fail("no request is made")), contextlib.redirect_stdout(out):
            self.assertEqual(m.main(), 0)
        self.assertEqual(sent, [], "nothing was checked, so no alert is opened or closed")
        self.assertIn("not checked", out.getvalue())


class Capabilities(unittest.TestCase):
    def test_only_agent_and_auth(self):
        self.assertEqual(runtime_ports.capabilities("acp"), {"agent", "auth"})

    def test_everything_else_is_unsupported_with_a_reason(self):
        for port in ("stream", "sessions", "completion", "lifecycle", "instructions"):
            with self.subTest(port=port), self.assertRaises(runtime_ports.Unsupported) as cm:
                runtime_ports.load("acp", ports=(port,))
            self.assertIn(runtime_ports.PORT_LABELS[port], str(cm.exception))

    def test_feature_table(self):
        table = {f: ok for f, ok, _miss in runtime_ports.feature_table("acp")}
        on = [f for f, ok in table.items() if ok]
        self.assertEqual(len(on), 1, on)   # judgment points only
        self.assertTrue(on[0].startswith("judgment points"))

    def test_no_other_adapter_imported(self):
        import re
        imported = set(re.findall(r"^\s*(?:from|import)\s+(runtime_\w+)", (ROOT / "bin" / "runtime_acp.py").read_text(), re.M))
        self.assertEqual(imported, {"runtime_ports"})


@unittest.skipUnless(os.environ.get("ACP_LIVE"), "live ACP run: set ACP_LIVE=1 and ACP_LIVE_COMMAND")
class Live(unittest.TestCase):
    """A real agent reads one file and answers in JSON; the run must be ok with the structured answer."""

    def test_read_and_answer(self):
        cmd = json.loads(os.environ["ACP_LIVE_COMMAND"])
        conf = {"agent": "live", "agents": {"live": {"command": cmd}}}
        saved, saved_secrets = hubconf.get, hubconf.secrets_dir
        hubconf.get = lambda k, default=None: conf if k == "runtime.acp" else saved(k, default)
        secrets = pathlib.Path(tempfile.mkdtemp()).resolve()
        hubconf.secrets_dir = lambda: secrets
        try:
            import runtime_acp
            ws = hubconf.throwaway_copy("live")   # where propose_apply makes its copy
            ws.mkdir()
            (ws / "secret-word.txt").write_text("periwinkle\n")
            res = runtime_acp.run(runtime_ports.Task(
                point="mailbox-propose", prompt="Read the file secret-word.txt in the current folder and report the word in it.",
                spec={}, cwd=str(ws), model=os.environ.get("ACP_LIVE_MODEL") or None, timeout_s=600,
                env={**os.environ, "HUB_THROWAWAY_COPY": str(ws)},
                output_schema={"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]}))
        finally:
            hubconf.get, hubconf.secrets_dir = saved, saved_secrets
        print(json.dumps(res._asdict(), ensure_ascii=False, default=str), file=sys.stderr)
        self.assertTrue(res.ok, res.error)
        self.assertEqual((res.structured_output or {}).get("word"), "periwinkle")


if __name__ == "__main__":
    unittest.main()
