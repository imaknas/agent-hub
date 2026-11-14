"""The platform's own Codex runs (bin/runtime_codex.py, docs/design/runtime-codex.md Step 1 and its r2): the store's copy,
a run home of each run's own, the tools checked before and after a run, the judgment points and folders Codex may run,
the login shared by a link; and that with Codex only installed for sessions nothing new happens. A stand-in codex
(tests/codex_fake.py) and temp folders only: never ~/.codex, the live store or a login."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))
import codex_fake  # noqa: E402
import hubconf  # noqa: E402
import runtime_codex as rx  # noqa: E402
import runtime_ports  # noqa: E402
from runtime_ports import CompletionRequest, Task  # noqa: E402

POINTS = __import__("yaml").safe_load((ROOT / "contracts" / "llm-points.yaml").read_text())["points"]


class Temp(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = pathlib.Path(d.name).resolve()
        self.user = self.tmp / "user-codex"
        self.user.mkdir()
        self.origin = self.tmp / "origin"
        (self.origin / "box-memory.git").mkdir(parents=True)
        p = hubconf.p
        for patch in (mock.patch.object(hubconf, "runtimes_dir", lambda: self.tmp / "runtimes"),
                      mock.patch.object(hubconf, "private_tmp", lambda: self.private()),
                      mock.patch.object(hubconf, "p", lambda k, d=None: self.origin if k == "git_origin" else p(k, d)),
                      mock.patch.object(rx, "codex_home", lambda: self.user),
                      mock.patch("i18n.lang", lambda: "en"),
                      mock.patch("alert.once", lambda *a, **k: self.alerts.append(a))):
            patch.start()
            self.addCleanup(patch.stop)
        self.alerts = []
        self.addCleanup(rx.check_binary, None)

    def private(self):
        d = self.tmp / "private"
        d.mkdir(exist_ok=True)
        return d

    def login(self, mode="chatgpt", last="2099-01-01T00:00:00Z"):
        (self.user / "auth.json").write_text(json.dumps({"auth_mode": mode, "last_refresh": last, "tokens": "secret-x"}))

    def stand_in(self, **conf):
        exe = codex_fake.write(self.tmp / "prefix", **conf)
        rx.check_binary(str(exe))
        return exe

    def copy(self, config=None) -> pathlib.Path:
        c = self.tmp / "copies" / "box"
        (c / ".git").mkdir(parents=True)
        (c / ".git" / "config").write_text(config if config is not None else (
            f'[core]\n\trepositoryformatversion = 0\n\tbare = false\n[remote "origin"]\n\turl = '
            f'{self.origin / "box-memory.git"}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
            '[branch "agent-memory"]\n\tremote = origin\n\tmerge = refs/heads/agent-memory\n'))
        (c / "STATE.md").write_text("s\n")
        return c


class NothingNewWhileOnlyInstalled(Temp):
    """runtime-codex.md, Step 1 r2, D11 and S6: Codex in runtime.installed, no point, provider or default on it (this
    machine's case): no store install, no session copies, no login use, no alerts."""

    CONF = {"runtime.default": "claude", "runtime.installed": ["claude", "codex"]}

    def setUp(self):
        super().setUp()
        p = mock.patch.object(hubconf, "get", lambda k, d=None: self.CONF.get(k, d))
        p.start()
        self.addCleanup(p.stop)

    def test_no_platform_copy_and_no_session_copies(self):
        import importlib
        update = importlib.import_module("update-runtimes")
        import session_versions
        self.assertIn("distribution", runtime_ports.capabilities("codex"))
        self.assertEqual(runtime_ports.platform_work(), ["claude"])
        self.assertEqual(update.in_use(), ["claude"])
        self.assertEqual(session_versions.runtimes(), ["claude"], "a Codex session can't be confined (B1): no copies")

    def test_the_login_job_checks_nothing_and_alerts_nothing(self):
        import importlib
        job = importlib.import_module("check-runtime-login")
        with mock.patch.object(rx, "login_check", side_effect=AssertionError("no login use")):
            self.assertEqual(job.main(["--runtime", "codex"]), 0)
        self.assertEqual(self.alerts, [])
        self.assertFalse((self.tmp / "runtimes").exists(), "nothing made in the store")

    def test_a_pin_or_a_provider_brings_it_in(self):
        import importlib
        update = importlib.import_module("update-runtimes")
        self.CONF = {**self.CONF, "llm.points": {"first-look": {"runtime": "codex"}}}
        self.assertEqual(update.in_use(), ["claude", "codex"])
        self.CONF = {"runtime.default": "claude", "llm.providers": {"c": {"impl": "codex"}}}
        self.assertEqual(update.in_use(), ["claude", "codex"])


class Distribution(Temp):
    def test_published_keeps_only_releases(self):
        out = json.dumps(["0.169.0", "0.169.0-darwin-arm64", "0.170.0-alpha.1", "0.168.0"])
        with mock.patch.object(rx.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out, "")):
            self.assertEqual(rx.published(), ["0.169.0", "0.168.0"])

    def lay_out(self, version="0.169.0", integrity=True, target=None):
        prefix = self.tmp / "copy"
        vendor = rx.vendor_folder(prefix)
        (vendor / "bin").mkdir(parents=True)
        (vendor / "bin" / "codex").write_text("x")
        (vendor / "bin" / "codex-code-mode-host").write_text("x")
        (vendor / "codex-package.json").write_text(json.dumps({"version": version, "target": target or rx._target()[1],
                                                               "entrypoint": "bin/codex"}))
        pkgs = {"": {}, "node_modules/@openai/codex": {"resolved": "https://r/c.tgz", "integrity": "sha512-a"},
                "node_modules/@openai/codex-x": {"resolved": "https://r/x.tgz", **({"integrity": "sha512-b"} if integrity else {})}}
        (prefix / "package-lock.json").write_text(json.dumps({"packages": pkgs}))
        return prefix, vendor

    @unittest.skipUnless(rx._target(), "no Codex build for this machine")
    def test_an_installed_copy_is_checked_and_its_executables_named(self):
        prefix, vendor = self.lay_out()
        self.assertEqual(rx.copy_problems(prefix, "0.169.0"), [])
        self.assertTrue(rx.copy_problems(prefix, "0.170.0"), "another version")
        self.assertEqual(rx.executables(vendor / "bin" / "codex"),
                         [vendor / "bin" / "codex", vendor / "bin" / "codex-code-mode-host"])
        self.assertEqual(rx.pin_env(self.tmp / "runtimes" / "codex" / "0.169.0"),
                         {"npm_config_prefix": str(self.tmp / "runtimes" / "codex" / rx.NPM_SINK)})

    @unittest.skipUnless(rx._target(), "no Codex build for this machine")
    def test_a_resolved_package_without_integrity_or_another_target_is_refused(self):
        prefix, _vendor = self.lay_out(integrity=False)
        self.assertIn("integrity", rx.copy_problems(prefix, "0.169.0")[0])

    def test_no_copy_no_run_and_never_the_one_on_path(self):
        with self.assertRaises(runtime_ports.NoCheckedCopy):
            rx.binary()
        self.login()
        got = rx.complete(CompletionRequest(point="first-look", prompt="hi"), {})
        self.assertFalse(got.ok)
        self.assertIn("no checked copy", got.error)
        self.assertIsNotNone(runtime_ports.no_checked_copy("codex"))

    def test_a_store_copy_says_its_version_by_its_folder(self):
        exe = self.tmp / "runtimes" / "codex" / "0.169.0" / "node_modules" / "x" / "bin" / "codex"
        exe.parent.mkdir(parents=True)
        exe.write_text("x")
        self.assertEqual(rx.version_at(exe), ("0.169.0", None))


class RunHome(Temp):
    def test_a_fresh_home_holds_the_config_and_a_link_to_the_login_and_goes(self):
        self.login()
        h = rx.run_home()
        self.assertEqual((h / "config.toml").read_text(), rx.HOME_CONFIG)
        self.assertEqual(os.readlink(h / "auth.json"), str(self.user / "auth.json"))
        self.assertEqual(sorted(p.name for p in h.iterdir()), ["auth.json", "config.toml", "home"])
        rx.end_run_home(h)
        self.assertFalse(h.exists())

    def test_no_login_no_link_and_old_homes_are_pruned(self):
        old = rx.run_home()
        t = 0
        os.utime(old, (t, t))
        h = rx.run_home()
        self.assertFalse(old.exists())
        self.assertFalse((h / "auth.json").exists())

    def test_the_config_holds_what_was_measured(self):
        import tomllib
        c = tomllib.loads(rx.HOME_CONFIG)
        self.assertFalse(any(c["features"].values()), "every feature listed is off")
        for f in ("shell_snapshot", "hooks", "plugins", "apps", "multi_agent_v2", "view_image", "sleep_tool"):
            self.assertIn(f, c["features"])
        self.assertEqual((c["agents"]["enabled"], c["skills"]["include_instructions"], c["approval_policy"],
                          c["default_permissions"], c["cli_auth_credentials_store"], c["check_for_update_on_startup"]),
                         (False, False, "never", ":read-only", "file", False))
        for never in ("hooks", "projects", "mcp_servers", "permissions", "profiles"):
            self.assertNotIn(never, c)

    def test_the_run_env_is_minimal_and_its_home_its_own(self):
        h = rx.run_home()
        env = rx.run_env(h, {"OPENAI_API_KEY": "k", "HUB_LLM_POINT": "p", "PATH": "/x", "SECRET": "s",
                             "BRUNNA_CONFINED": "1", "CLAUDE_CODE_SESSION_ID": "c"})
        self.assertEqual(env["HOME"], str(h / "home"))
        self.assertEqual(env["CODEX_HOME"], str(h))
        self.assertEqual((env["HUB_LLM_POINT"], env["BRUNNA_CONFINED"]), ("p", "1"))
        for k in ("OPENAI_API_KEY", "SECRET", "CLAUDE_CODE_SESSION_ID"):
            self.assertNotIn(k, env)

    def test_the_profile_denies_the_home_the_store_and_the_temp_roots_and_reads_back(self):
        folder = self.tmp / "f"
        (folder / "ref").mkdir(parents=True)
        e = rx.profile_entries(read=[folder], write=[folder / "ref"], exe="/v/triple/bin/codex")
        self.assertEqual(e[str(pathlib.Path.home().resolve())], "deny")
        self.assertEqual(e[os.path.realpath(self.tmp / "runtimes" / "codex")], "deny")
        self.assertEqual(e[str(folder)], "read")
        self.assertEqual(e[str(folder / "ref")], "write")
        self.assertEqual(e[f"{folder / 'ref'}/**/.git*"], "deny")
        self.assertEqual(e["/v/triple"], "read", "the running version's vendor folder")
        args = rx.run_args("m", e)
        self.assertIn('default_permissions="brunna-run"', args)
        self.assertIn('model="m"', args)
        self.assertNotIn("-m", args)


class Shape(Temp):
    def items(self, **conf):
        exe = self.stand_in(**conf)
        r = subprocess.run([str(exe), "debug", "prompt-input"], capture_output=True, text=True)
        return json.loads(r.stdout)

    def test_the_pinned_shape_and_what_breaks_it(self):
        self.assertEqual(rx.shape_problems(self.items()), [])
        self.assertTrue(rx.shape_problems(self.items(pre_extra=True)), "v2's collaboration")
        self.assertTrue(rx.shape_problems(self.items(no_list=True)), "a model whose tools can't be seen (gpt-5.5)")
        deferred = self.items() + [{"type": "message", "role": "developer",
                                    "content": [{"text": "<tools>\nDeferred tool namespaces:\n- multi_agent_v1"}]}]
        self.assertTrue(rx.shape_problems(deferred), "v1's deferred sub-agents")


class Completion(Temp):
    def req(self, **kw):
        return CompletionRequest(point="first-look", prompt="the prompt", model="gpt-6-luna", **kw)

    def test_a_clean_run_answers_with_a_minimal_env_and_only_the_platforms_flags(self):
        self.login()
        exe = self.stand_in(answer='{"a": 1}')
        got = rx.complete(self.req(schema={"type": "object"}, system="be brief"), {})
        self.assertTrue(got.ok, got.error)
        self.assertEqual((got.text, got.input_tokens, got.output_tokens), ('{"a": 1}', 11, 3))
        calls = codex_fake.calls(exe)
        self.assertEqual([c["argv"][:2] for c in calls], [["debug", "prompt-input"], ["exec", "--strict-config"]])
        pre, run = calls
        self.assertEqual([a for a in pre["argv"] if a.startswith(("model=", "permissions.", "default_"))],
                         [a for a in run["argv"] if a.startswith(("model=", "permissions.", "default_"))],
                         "the check and the run get the same -c list")
        for flag in ("--ignore-user-config", "--trust", "--ephemeral", "--dangerously-bypass-approvals-and-sandbox",
                     "--dangerously-bypass-hook-trust", "-m"):
            self.assertNotIn(flag, run["argv"])
        self.assertIn('developer_instructions="be brief"', run["argv"])
        self.assertIn(rx.SCHEMA_ASK, run["stdin"], "the schema goes into the prompt until the login list says otherwise")
        self.assertTrue(run["env"]["HOME"].endswith("/home") and run["env"]["CODEX_HOME"] in run["env"]["HOME"])
        self.assertNotIn("OPENAI_API_KEY", run["env"])
        self.assertEqual(list((self.tmp / "runtimes" / "codex" / "runs").iterdir()), [], "the run home went")

    def test_a_wider_list_before_the_run_refuses_it_and_alerts(self):
        self.login()
        exe = self.stand_in(pre_extra=True)
        got = rx.complete(self.req(), {})
        self.assertFalse(got.ok)
        self.assertIn("not run", got.error)
        self.assertEqual([c["argv"][0] for c in codex_fake.calls(exe)], ["debug"], "never run")
        self.assertEqual(self.alerts[-1][0], "completion-tools:codex:first-look")

    def test_a_wider_rollout_refuses_the_result(self):
        self.login()
        for conf in ({"post_extra": True}, {"entries_extra": True}, {"no_rollout": True}):
            with self.subTest(**conf):
                self.stand_in(**conf)
                got = rx.complete(self.req(), {})
                self.assertFalse(got.ok)
                self.assertIn("refused", got.error)

    def test_no_login_is_said(self):
        self.stand_in()
        got = rx.complete(self.req(), {})
        self.assertFalse(got.ok)
        self.assertTrue(rx.is_login_error(got.error))


class Points(unittest.TestCase):
    def test_every_declared_point_maps_fully_or_is_refused(self):
        runs = {p for p, spec in POINTS.items() if spec.get("shape") != "completion" and rx.point_refusal(p, spec) is None}
        self.assertEqual(runs, {"librarian-state", "librarian-todo-format", "consolidate"})
        for p, spec in POINTS.items():
            if spec.get("shape") == "completion":
                self.assertIsNone(rx.point_refusal(p, spec))

    def test_an_unknown_field_or_rule_is_refused_never_dropped(self):
        spec = dict(POINTS["librarian-state"])
        self.assertIn("new_limit", rx.point_refusal("x", {**spec, "new_limit": 1}))
        self.assertTrue(rx.point_refusal("x", {**spec, "allowed_tools": ["Bash(git:*)"]}))
        self.assertTrue(rx.point_refusal("x", {**spec, "allowed_tools": ["Edit(./../x)"]}))
        self.assertTrue(rx.point_refusal("x", {**spec, "extra_args": ["--resume"]}))
        self.assertTrue(rx.point_refusal("x", {**spec, "permission_mode": "bypassPermissions"}))
        self.assertTrue(rx.point_refusal("x", {**spec, "tools": "Read,Bash"}))

    def test_llm_ready_says_a_refused_point_is_off(self):
        import llm_ready
        rx.check_binary("/x/codex")
        self.addCleanup(rx.check_binary, None)
        with mock.patch.object(runtime_ports, "default_name", lambda: "codex"):
            self.assertIsNone(llm_ready.unsupported("librarian-state"))
            self.assertIn("B1", llm_ready.unsupported("librarian-index"))


class Folders(Temp):
    """r2 S1 and S7: every kind of folder a caller might hand a Codex run, read as files only."""

    def test_only_a_plain_clone_of_a_memory_repo(self):
        self.assertIsNone(rx.folder_refusal(self.copy()))

    def test_every_other_kind_is_refused(self):
        c = self.copy()
        cases = {}
        wt = self.tmp / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /somewhere/.git/worktrees/wt\n")
        cases["a linked worktree (a throwaway copy, a session's worktree)"] = wt
        nested = c / "memory"
        nested.mkdir()
        cases["a folder inside a repo"] = nested
        plain = self.tmp / "plain"
        plain.mkdir()
        cases["a folder that isn't a clone"] = plain
        for name, cfg in (("a filter", '[filter "x"]\n\tclean = touch /tmp/x\n'),
                          ("an include", "[include]\n\tpath = /x\n"),
                          ("fsmonitor", "[core]\n\tfsmonitor = /x\n"),
                          ("another remote", '[remote "origin"]\n\turl = https://example.com/x.git\n')):
            d = self.tmp / name.replace(" ", "-")
            (d / ".git").mkdir(parents=True)
            (d / ".git" / "config").write_text(cfg)
            cases[name] = d
        attrs = self.tmp / "attrs"
        (attrs / ".git" / "info").mkdir(parents=True)
        (attrs / ".git" / "config").write_text("[core]\n\tbare = false\n")
        (attrs / ".git" / "info" / "attributes").write_text("* filter=x\n")
        cases["info/attributes"] = attrs
        for name, folder in cases.items():
            with self.subTest(name):
                self.assertTrue(rx.folder_refusal(folder))
        self.assertTrue(rx.folder_refusal(None))


class Agent(Temp):
    def task(self, cwd, point="consolidate", **kw):
        return Task(point=point, prompt="do it", spec=POINTS[point], cwd=str(cwd), model="gpt-6-astra",
                    output_schema={"type": "object"}, env={"HUB_LLM_POINT": point}, **kw)

    def test_a_memory_copy_point_runs_with_the_shell_under_its_profile(self):
        self.login()
        c = self.copy()
        exe = self.stand_in(answer='{"folded": []}')
        res = rx.run(self.task(c))
        self.assertTrue(res.ok, res.error)
        self.assertEqual((res.structured_output, res.session_id), ({"folded": []}, "t1"))
        self.assertTrue((c / "memory" / "reference").is_dir(), "a folder rule's folder made first (S1)")
        run = codex_fake.calls(exe)[-1]["argv"]
        self.assertIn("features.shell_tool=true", run)
        table = next(a for a in run if a.startswith("permissions.brunna-run.filesystem="))
        self.assertIn(f'"{c / "STATE.md"}" = "write"', table)
        self.assertIn(f'"{c / "memory" / "reference"}/**/.git*" = "deny"', table)

    def test_refused_where_the_folder_isnt_the_platforms_or_the_point_isnt_one(self):
        self.login()
        self.stand_in()
        wt = self.tmp / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: x\n")
        self.assertFalse(rx.run(self.task(wt)).ok)
        c = self.copy()
        self.assertFalse(rx.run(Task(point="librarian-index", prompt="x", spec=POINTS["librarian-index"], cwd=str(c))).ok)
        self.assertFalse(rx.run(self.task(c, extra=("--resume", "x"))).ok, "another runtime's arguments")

    def test_describe_starts_nothing_and_makes_nothing(self):
        c = self.copy()
        self.stand_in()
        argv = rx.describe(self.task(c))
        self.assertEqual(argv[1:3], ["exec", "--strict-config"])
        self.assertFalse((c / "memory" / "reference").exists())


class Auth(Temp):
    def test_billing_and_the_login_window_read_two_fields_only(self):
        self.login("chatgpt")
        self.assertEqual(rx.billing("token", {}), "subscription")
        self.assertEqual(rx.billing("token", {"OPENAI_API_KEY": "k"}), "metered")
        self.assertIsNone(rx.login_window(3600))
        self.login("chatgpt", last="2000-01-01T00:00:00Z")
        self.assertTrue(rx.login_window(3600))
        self.login("apikey", last="2000-01-01T00:00:00Z")
        self.assertIsNone(rx.login_window(3600), "an API key doesn't refresh")
        self.assertEqual(rx.billing("token", {}), "metered")
        self.assertNotIn("secret-x", repr(rx.credentials()))

    def test_a_keyring_store_is_said(self):
        self.login()
        (self.user / "config.toml").write_text('cli_auth_credentials_store = "keyring"\n')
        self.assertTrue(rx.credentials()[0].note)
        self.assertIn("store", [i for i, _p in rx.login_check()])
        self.assertTrue(any("keyring" in what for _l, what, _f in rx.doctor_notes()))

    def test_the_login_check_runs_status_through_a_run_home(self):
        self.login()
        exe = self.stand_in()
        self.assertEqual(rx.login_check(), [])
        self.assertEqual(codex_fake.calls(exe)[-1]["argv"], ["login", "status"])
        self.assertNotEqual(codex_fake.calls(exe)[-1]["env"]["CODEX_HOME"], str(self.user))


if __name__ == "__main__":
    unittest.main()
