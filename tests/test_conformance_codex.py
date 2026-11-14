"""Codex's session-side conformance checks (bin/conformance_codex.py; docs/design/runtime-codex.md, Step 4 r2, S9)
through the runner's real path against a stand-in `codex` (no model, no network, no login), the session's version read
from its rollout, a version's result recorded where the per-version gate reads it, and the gate holding a sandboxed
project's Codex session whose version failed.

    bin/py -m unittest tests.test_conformance_codex
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import conformance  # noqa: E402
import conformance_codex  # noqa: E402
import hubconf  # noqa: E402
import runtime_codex  # noqa: E402
import runtime_ports  # noqa: E402

STAND_IN = r'''#!PYTHON
import os, re, subprocess, sys, tomllib
a = sys.argv[1:]
mode = os.environ.get("STAND_IN_MODE", "good")
if a[:1] == ["--version"]:
    print("codex-cli 0.170.0"); sys.exit(0)
if a[:2] == ["debug", "prompt-input"]:   # its own git in the folder, as Codex runs it (hooks and fsmonitor off)
    base = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
    if mode == "good":   # attributes from the empty tree: no driver is chosen for any file
        subprocess.run([*base, "--attr-source=4b825dc642cb6eb9a060e54bf8d69288fbee4904", "status", "--porcelain"],
                       capture_output=True)
    elif mode == "ignore":
        subprocess.run([*base, "status", "--porcelain"], capture_output=True)
    elif mode == "nostatus":
        subprocess.run([*base, "rev-parse", "HEAD"], capture_output=True)
    elif mode == "elsewhere":   # a status, but not in the folder
        subprocess.run([*base, "-C", "/", "status", "--porcelain"], capture_output=True)
    print("[]"); sys.exit(0)
if a[:1] == ["sandbox"]:   # the named profile's deny globs, through Seatbelt
    profile, cmd = a[a.index("-P") + 1], a[a.index("--") + 1:]
    with open(os.path.join(os.environ["CODEX_HOME"], "config.toml"), "rb") as f:
        conf = tomllib.load(f)
    # always: the guarded profile's entries hold under the open one too (its positive control then tests nothing)
    fs = conf["permissions"]["brunna-guarded" if mode == "always" else profile].get("filesystem") or {}
    globs = [k for k, v in fs.items() if v == "deny"] if mode != "ignore" else []
    if globs:
        def rx(g):
            return "^" + re.escape(g).replace(r"/\*\*/", "/(.*/)?").replace(r"/\*\*", "(/.*)?") + "$"
        prof = "(version 1)(allow default)" + "".join(f'(deny file-write* (regex #"{rx(g)}"))' for g in globs)
        os.execvp("sandbox-exec", ["sandbox-exec", "-p", prof, *cmd])
    os.execvp(cmd[0], cmd)
sys.exit(2)
'''


class Through(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = pathlib.Path(d.name).resolve()
        self.exe = self.tmp / "codex"
        self.exe.write_text(STAND_IN.replace("PYTHON", sys.executable, 1))
        self.exe.chmod(0o755)

    def child(self, name, mode="good"):
        world = conformance.World(self.tmp / f"w-{name}-{mode}").create()
        r = subprocess.run([sys.executable, str(BIN / "conformance.py"), "child", "codex", name, str(world.root),
                            str(self.exe)], capture_output=True, text=True, env={**world.env(), "STAND_IN_MODE": mode},
                           timeout=300)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_a_codex_whose_git_runs_the_folders_filter_fails_b1(self):
        self.assertEqual(self.child("session-folder-git", "ignore")["state"], "fail")

    def test_a_codex_whose_git_neutralises_the_filter_passes(self):
        self.assertEqual(self.child("session-folder-git", "good")["state"], "pass")

    def test_no_git_from_codex_tests_nothing(self):
        self.assertEqual(self.child("session-folder-git", "idle")["state"], "error")

    def test_a_status_must_run_in_the_folder_to_count(self):
        for mode in ("nostatus", "elsewhere"):
            with self.subTest(mode=mode):
                self.assertEqual(self.child("session-folder-git", mode)["state"], "error")

    def test_the_subcommand_is_the_word_after_the_global_options(self):
        self.assertEqual(conformance_codex.subcommand(["-c", "status.x=1", "-C", "/p", "status", "--porcelain"]),
                         "status")
        self.assertEqual(conformance_codex.subcommand(["-c", "x=status", "rev-parse", "HEAD"]), "rev-parse")
        self.assertIsNone(conformance_codex.subcommand(["--version"]))

    def test_every_planted_driver_runs_under_a_plain_git_status(self):
        """the canary's own control: each config source it plants is one git reads"""
        root = self.tmp / "plant"
        (root / "p").mkdir(parents=True)
        env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        with mock.patch.dict(os.environ, env):
            markers = conformance_codex._plant(root / "p", root)
            subprocess.run(["git", "status", "--porcelain"], cwd=root / "p", capture_output=True)
        self.assertEqual({conformance_codex.DRIVERS[d] for d, m in markers.items() if m.exists()},
                         set(conformance_codex.DRIVERS.values()))

    @unittest.skipUnless(sys.platform == "darwin", "the stand-in's sandbox is Seatbelt")
    def test_the_deny_globs_hold_and_their_control_lands(self):
        got = self.child("control-globs", "good")
        self.assertEqual(got["state"], "pass", got)

    def test_a_profile_that_ignores_them_fails(self):
        self.assertEqual(self.child("control-globs", "ignore")["state"], "fail")

    @unittest.skipUnless(sys.platform == "darwin", "the stand-in's sandbox is Seatbelt")
    def test_a_positive_control_that_cant_land_tests_nothing(self):
        got = self.child("control-globs", "always")
        self.assertEqual(got["state"], "error", got)
        self.assertIn("a link named .codex", got["reason"])

    def test_the_profile_keys_are_toml_strings(self):
        import tomllib
        key = '/w/a "b"\\c/**/.codex'
        self.assertEqual(tomllib.loads(f"{conformance_codex._toml_key(key)} = 1"), {key: 1})

    def test_the_checks_are_the_session_side_and_codex_is_versioned_by_them(self):
        self.assertTrue(all(c.side == "session" for c in conformance_codex.CHECKS))
        self.assertEqual([c.name for c in conformance_codex.CHECKS if c.run and c.kind == "safety"],
                         ["session-folder-git", "control-globs"])
        self.assertTrue(conformance.versioned("codex"))
        self.assertTrue(conformance.versioned("claude"))
        self.assertFalse(conformance.versioned("none"))
        self.assertEqual(runtime_codex.version_of(self.exe), "0.170.0")

    def test_the_profile_entries_deny_every_control_path_at_any_depth(self):
        ws = os.path.realpath(self.tmp)
        self.assertEqual(runtime_codex.profile_control_entries(self.tmp), {
            f"{ws}/**/.codex": "deny", f"{ws}/**/.codex/**": "deny",
            f"{ws}/**/.agents": "deny", f"{ws}/**/.agents/**": "deny"})


class Recorded(unittest.TestCase):
    """A version's result lands where the per-version gate reads it, and the gate holds a sandboxed project's Codex
    session whose version failed B1."""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = pathlib.Path(d.name).resolve()
        p = mock.patch.object(hubconf, "STATE", self.tmp / "state")
        p.start()
        self.addCleanup(p.stop)

    def test_a_runtime_without_distribution_records_its_session_result(self):
        doc = {"runtime": "codex", "version": "0.170.0", "binary": "/x/codex", "digest": "d", "at": "now",
               "checks": {"session-folder-git": {"port": "sessions", "kind": "safety", "state": "fail", "reason": "B1",
                                                 "tries": 1}}}
        with mock.patch.object(runtime_ports, "session_result_file",
                               lambda r, v: self.tmp / "state" / "runtime-conformance" / r / f"{v}.session.json"):
            f = conformance.record_any(doc)
            self.assertEqual(f.name, "0.170.0.session.json")
            self.assertEqual(runtime_ports.failed_checks(runtime_ports.session_result("codex", "0.170.0"), "safety"),
                             ["session-folder-git"])
            rec = {"runtime": "codex", "version": "0.170.0", "project": "caged"}
            with mock.patch.object(runtime_ports, "_sessions_sandboxed", lambda project: project == "caged"):
                self.assertEqual(runtime_ports.gated("codex:s1", rec), ["session-folder-git"])
                self.assertEqual(runtime_ports.gated("codex:s2", {**rec, "project": "open"}), [])


SID = "01a1d88e-c10f-7642-83bf-9b129ddba5a8"
UNREAD = (runtime_ports.UNREADABLE, None)


class SessionVersion(unittest.TestCase):
    """The version a Codex session runs: the running process's (the daemon's own release folder, or nvm's package), and
    only when that can't be read the rollout's session_meta.cli_version, marked as such, read only from a rollout in
    the user's Codex home whose session id is the hook's (review B1 and its nits)."""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = pathlib.Path(d.name).resolve()
        self.home = self.tmp / "codex-home"
        p = mock.patch.object(runtime_codex, "codex_home", lambda: self.home)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(runtime_ports, "process_path", lambda pid: None)   # the process can't be read
        p.start()
        self.addCleanup(p.stop)

    def rollout(self, where, version="0.169.0", sid=SID, hook_sid=SID):
        f = where / f"rollout-2026-11-14T13-36-36-{sid}.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"type": "session_meta", "payload": {"id": sid, "session_id": sid,
                                                                     "cli_version": version,
                                                                     "originator": "codex-tui"}}) + "\n")
        return json.dumps({"transcript_path": str(f), "session_id": hook_sid})

    def test_the_running_daemons_release_wins(self):
        exe = self.home / "packages" / "app-server-daemon" / "releases" / "0.170.0-aarch64-apple-darwin" / "bin" / "codex"
        exe.parent.mkdir(parents=True)
        exe.write_text("")
        raw = self.rollout(self.home / "sessions")
        with mock.patch.object(runtime_ports, "process_path", lambda pid: str(exe)):
            self.assertEqual(runtime_codex.session_version(123, raw=raw), ("0.170.0", None),
                             "a daemon that updated itself under a resumed thread: its own version, not the thread's")

    def test_nvms_package(self):
        import hubconf
        nvm = self.tmp / "nvm"
        pkg = nvm / "versions" / "node" / "v24" / "lib" / "node_modules" / "@openai" / "codex"
        exe = pkg / "node_modules" / "@openai" / "codex-darwin-arm64" / "vendor" / "x" / "bin" / "codex"
        exe.parent.mkdir(parents=True)
        exe.write_text("")
        (pkg / "package.json").write_text(json.dumps({"name": "@openai/codex", "version": "0.167.0"}))
        with mock.patch.object(hubconf, "nvm_dir", lambda: nvm):
            self.assertEqual(runtime_codex.version_at(exe), ("0.167.0", None))
            self.assertEqual(runtime_codex.version_at(self.tmp / "proj" / "node_modules" / "codex"), UNREAD)

    def test_else_from_the_rollout_in_the_home_marked_so(self):
        raw = self.rollout(self.home / "sessions" / "2026" / "11" / "14")
        self.assertEqual(runtime_codex.session_version(123, raw=raw), ("0.169.0", runtime_codex.ROLLOUT_BUILD))

    def test_a_linked_sessions_folder_works(self):
        real = self.tmp / "elsewhere"
        real.mkdir()
        self.home.mkdir()
        (self.home / "sessions").symlink_to(real)
        raw = self.rollout(self.home / "sessions")
        self.assertEqual(runtime_codex.session_version(123, raw=raw)[0], "0.169.0")

    def test_a_transcript_elsewhere_says_nothing(self):
        for where in (self.tmp / "planted", self.home / "sessions" / ".." / ".." / "x"):
            with self.subTest(where=where):
                raw = self.rollout(where)
                self.assertEqual(runtime_codex.session_version(123, raw=raw), UNREAD)
        planted = self.tmp / "planted2"
        raw = self.rollout(planted)
        (self.home / "sessions").mkdir(parents=True, exist_ok=True)
        link = self.home / "sessions" / pathlib.Path(json.loads(raw)["transcript_path"]).name
        link.symlink_to(json.loads(raw)["transcript_path"])
        self.assertEqual(runtime_codex.session_version(123, raw=json.dumps({"transcript_path": str(link),
                                                                             "session_id": SID})), UNREAD,
                         "a link in the home to a file elsewhere")

    def test_another_sessions_rollout_says_nothing(self):
        raw = self.rollout(self.home / "sessions", hook_sid="01a1d88e-0000-0000-0000-000000000000")
        self.assertEqual(runtime_codex.session_version(123, raw=raw), UNREAD)

    def test_a_long_first_line_is_read(self):
        f = self.home / "sessions" / f"rollout-x-{SID}.jsonl"
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"type": "session_meta", "payload": {"id": SID, "base_instructions": "x" * (2 << 20),
                                                                     "cli_version": "0.169.0"}}) + "\n")
        raw = json.dumps({"transcript_path": str(f), "session_id": SID})
        self.assertEqual(runtime_codex.session_version(123, raw=raw)[0], "0.169.0")

    def test_a_version_that_isnt_one_says_nothing(self):
        for v in ("../../x", "0.170.0-alpha.1"):   # one version grammar (runtime_ports.checkable_version)
            raw = self.rollout(self.home / "sessions", version=v)
            self.assertEqual(runtime_codex.session_version(123, raw=raw), UNREAD)
        self.assertEqual(runtime_codex.session_version(123), UNREAD)

    def test_hub_event_hands_it_the_hooks_input(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("hub_event_cv", str(BIN / "hub-event"))
        he = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(he)
        raw = self.rollout(self.home / "sessions")
        want = ("0.169.0", runtime_codex.ROLLOUT_BUILD)
        with mock.patch.object(he, "_RT", runtime_codex), mock.patch.object(he, "_RAW", raw), \
                mock.patch.object(runtime_codex, "session_pid", lambda env: 4242):
            self.assertEqual(he._hook_version(), want)
            self.assertEqual(he._version_of()(4242), want)


class Reason(unittest.TestCase):
    """doctor's reason comes from the newest recorded probe once there is one (review N4)"""

    def test_from_the_newest_probe(self):
        import hubconf
        with tempfile.TemporaryDirectory() as d, mock.patch.object(hubconf, "STATE", pathlib.Path(d)):
            unprobed = runtime_codex.unconfined()
            self.assertIn("0.167", unprobed, "what was measured by hand")
            folder = pathlib.Path(d) / "runtime-conformance" / "codex"
            folder.mkdir(parents=True)
            (folder / "0.169.0.session.json").write_text(json.dumps(
                {"version": "0.169.0", "at": "2026-11-14T10:00", "checks": {"session-folder-git": {"state": "fail"}}}))
            failed = runtime_codex.unconfined()
            self.assertIn("0.169.0", failed)
            (folder / "0.170.0.session.json").write_text(json.dumps(
                {"version": "0.170.0", "at": "2026-11-15T10:00", "checks": {"session-folder-git": {"state": "pass"}}}))
            passed = runtime_codex.unconfined()
            self.assertIn("0.170.0", passed, "the newest one")
            self.assertEqual(len({unprobed, failed.replace("0.169.0", "V"), passed.replace("0.170.0", "V")}), 3)


if __name__ == "__main__":
    unittest.main()
