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
    if mode != "idle":
        keep = [] if mode == "ignore" else ["-c", "filter.brunna-canary.clean=cat"]
        subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *keep, "status",
                        "--porcelain"], capture_output=True)
    print("[]"); sys.exit(0)
if a[:1] == ["sandbox"]:   # the named profile's deny globs, through Seatbelt
    profile, cmd = a[a.index("-P") + 1], a[a.index("--") + 1:]
    with open(os.path.join(os.environ["CODEX_HOME"], "config.toml"), "rb") as f:
        conf = tomllib.load(f)
    fs = (conf["permissions"][profile].get("filesystem") or {})
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

    @unittest.skipUnless(sys.platform == "darwin", "the stand-in's sandbox is Seatbelt")
    def test_the_deny_globs_hold_and_their_control_lands(self):
        got = self.child("control-globs", "good")
        self.assertEqual(got["state"], "pass", got)

    def test_a_profile_that_ignores_them_fails(self):
        self.assertEqual(self.child("control-globs", "ignore")["state"], "fail")

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


class SessionVersion(unittest.TestCase):
    """The version a Codex session runs is its rollout's session_meta.cli_version (the shared daemon's), read only from
    a rollout in the user's Codex home."""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = pathlib.Path(d.name).resolve()
        self.home = self.tmp / "codex-home"
        p = mock.patch.object(runtime_codex, "codex_home", lambda: self.home)
        p.start()
        self.addCleanup(p.stop)

    def rollout(self, where, version="0.169.0"):
        f = where / "rollout-2026-11-14T13-36-36-01a1d88e-c10f-7642-83bf-9b129ddba5a8.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"type": "session_meta", "payload": {"cli_version": version,
                                                                     "originator": "codex-tui"}}) + "\n")
        return json.dumps({"transcript_path": str(f)})

    def test_from_the_rollout_in_the_home(self):
        raw = self.rollout(self.home / "sessions" / "2026" / "11" / "14")
        self.assertEqual(runtime_codex.session_version(123, raw=raw), ("0.169.0", None))

    def test_a_transcript_elsewhere_says_nothing(self):
        raw = self.rollout(self.tmp / "planted")
        self.assertIsNone(runtime_codex.session_version(123, raw=raw))

    def test_a_version_that_isnt_one_says_nothing(self):
        raw = self.rollout(self.home / "sessions", version="../../x")
        self.assertIsNone(runtime_codex.session_version(123, raw=raw))
        self.assertIsNone(runtime_codex.session_version(123))

    def test_hub_event_hands_it_the_hooks_input(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("hub_event_cv", str(BIN / "hub-event"))
        he = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(he)
        raw = self.rollout(self.home / "sessions")
        with mock.patch.object(he, "_RT", runtime_codex), mock.patch.object(he, "_RAW", raw), \
                mock.patch.object(runtime_codex, "session_pid", lambda env: 4242):
            self.assertEqual(he._hook_version(), ("0.169.0", None))
            self.assertEqual(he._version_of()(4242), ("0.169.0", None))


if __name__ == "__main__":
    unittest.main()
