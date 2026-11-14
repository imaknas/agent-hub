"""The store of checked copies (bin/runtime_store.py, docs/design/runtime-versions.md section 3) and the job that keeps
it (bin/update-runtimes.py), against a stand-in distribution port: no npm, no network, no model."""
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import runtime_store as store  # noqa: E402


class Dist:
    """A distribution port that writes a small executable saying its version."""
    MIN_VERSION = "2.1.300"

    def __init__(self):
        self.calls = []

    def install(self, version, folder):
        self.calls.append((version, pathlib.Path(folder)))
        exe = pathlib.Path(folder) / "node_modules" / ".bin" / "tool"
        exe.parent.mkdir(parents=True)
        exe.write_text(f"#!/bin/sh\necho {version}\n")
        exe.chmod(0o755)
        return exe


class Temp(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = pathlib.Path(d.name).resolve()
        p = mock.patch.object(hubconf, "runtimes_dir", lambda: self.tmp / "runtimes")
        p.start()
        self.addCleanup(p.stop)
        self.dist = Dist()


class Store(Temp):
    def test_install_stages_then_renames_and_keeps_what_is_there(self):
        exe = store.install("claude", "2.1.400", self.dist)
        self.assertEqual(exe, self.tmp / "runtimes" / "claude" / "2.1.400" / "node_modules" / ".bin" / "tool")
        self.assertTrue(self.dist.calls[0][1].name.startswith(".staging-"))
        self.assertEqual(list((self.tmp / "runtimes" / "claude").glob(".staging-*")), [])
        self.assertEqual(store.install("claude", "2.1.400", self.dist), exe)
        self.assertEqual(len(self.dist.calls), 1, "already there: not installed again")
        half = self.tmp / "runtimes" / "claude" / "2.1.401"
        half.mkdir()
        store.install("claude", "2.1.401", self.dist)
        self.assertTrue((half / "installed.json").exists(), "a folder no install finished is replaced")
        with self.assertRaises(RuntimeError):
            store.install("claude", "latest", self.dist)

    def test_a_failed_install_leaves_nothing(self):
        class Broken:
            def install(self, version, folder):
                pathlib.Path(folder).mkdir(parents=True)
                raise RuntimeError("npm said no")
        with self.assertRaises(RuntimeError):
            store.install("claude", "2.1.400", Broken())
        self.assertEqual(store.versions("claude"), [])
        self.assertEqual(list((self.tmp / "runtimes" / "claude").glob(".staging-*")), [])

    def test_promote_previous_and_rollback(self):
        self.assertIsNone(store.current("claude"))
        store.install("claude", "2.1.400", self.dist)
        e = store.promote("claude", "2.1.400", "/r.json")
        self.assertEqual((e.version, e.promoted["result"]), ("2.1.400", "/r.json"))
        self.assertEqual(os.readlink(self.tmp / "runtimes" / "claude" / "current"), "2.1.400", "a relative link")
        self.assertIsNone(store.previous("claude"))
        store.install("claude", "2.1.401", self.dist)
        store.promote("claude", "2.1.401")
        self.assertEqual((store.current("claude").version, store.previous("claude").version), ("2.1.401", "2.1.400"))
        store.rollback("claude")
        self.assertEqual((store.current("claude").version, store.previous("claude").version), ("2.1.400", "2.1.401"))
        with self.assertRaises(RuntimeError):
            store.promote("claude", "2.1.999")

    def test_verify(self):
        store.install("claude", "2.1.400", self.dist)
        e = store.promote("claude", "2.1.400")
        self.assertIsNone(store.verify(e))
        st = e.exe.stat()
        e.exe.write_text("#!/bin/sh\necho X.X.XXX\n")   # same size, another content
        os.utime(e.exe, (st.st_atime, st.st_mtime))
        self.assertIsNone(store.verify(e), "checked today already: size and time only")
        (e.folder / ".verified").unlink()
        self.assertIsNotNone(store.verify(e), "the content is read once a day")
        e.exe.write_text("#!/bin/sh\necho longer than before\n")
        self.assertIsNotNone(store.verify(e))
        e.exe.unlink()
        self.assertIsNotNone(store.verify(e))

    def test_every_executable_of_a_copy_is_verified(self):
        """runtime-codex.md, Step 1: a copy whose runs execute more than its executable (Codex's code-mode host)."""
        def executables(exe):
            host = exe.parent / "host"
            host.write_text("#!/bin/sh\necho host\n")
            return [exe, host]
        self.dist.executables = executables
        store.install("claude", "2.1.400", self.dist)
        e = store.promote("claude", "2.1.400")
        self.assertEqual(list(e.promoted["also"]), ["node_modules/.bin/host"])
        self.assertIsNone(store.verify(e))
        host = e.exe.parent / "host"
        st = host.stat()
        host.write_text("#!/bin/sh\necho hosX\n")
        os.utime(host, (st.st_atime, st.st_mtime))
        self.assertIsNone(store.verify(e), "checked today already: size and time only")
        (e.folder / ".verified").unlink()
        self.assertIn("host", store.verify(e) or "")
        host.write_text("#!/bin/sh\necho a longer host\n")
        self.assertIn("host", store.verify(e) or "")
        host.unlink()
        self.assertIn("host", store.verify(e) or "")

    def test_an_entry_never_points_outside_its_folder(self):
        store.install("claude", "2.1.400", self.dist)
        e = store.promote("claude", "2.1.400")
        rec = json.loads((e.folder / store.PROMOTED).read_text())
        for bad in ("../../x", "/bin/sh"):
            (e.folder / store.PROMOTED).write_text(json.dumps({**rec, "exe": bad}))
            self.assertIsNone(store.current("claude"), bad)
        os.unlink(self.tmp / "runtimes" / "claude" / "current")
        os.symlink("../../elsewhere", self.tmp / "runtimes" / "claude" / "current")
        self.assertIsNone(store.current("claude"))

    def test_a_held_version_is_kept_and_an_old_free_one_goes(self):
        for v in ("2.1.400", "2.1.401", "2.1.402", "2.1.403"):
            store.install("claude", v, self.dist)
        store.promote("claude", "2.1.402")
        store.promote("claude", "2.1.403")
        old = time.time() - 20 * 86400
        for v in ("2.1.400", "2.1.401"):
            os.utime(self.tmp / "runtimes" / "claude" / v, (old, old))
        import fcntl
        fd = os.open(self.tmp / "runtimes" / "claude" / "2.1.401" / ".lock", os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_SH)
        try:
            self.assertEqual(store.prune("claude"), ["2.1.400"], "the held one stays, current and previous stay")
        finally:
            os.close(fd)
        self.assertEqual(store.prune("claude"), ["2.1.401"])
        self.assertEqual(store.versions("claude"), ["2.1.402", "2.1.403"])

    def test_hold(self):
        with store.hold("claude") as e:
            self.assertIsNone(e)
        store.install("claude", "2.1.400", self.dist)
        store.promote("claude", "2.1.400")
        with store.hold("claude", inheritable=True) as e:
            self.assertEqual(e.version, "2.1.400")


class UpdateRuntimes(Temp):
    def setUp(self):
        super().setUp()
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("update_runtimes", str(BIN / "update-runtimes.py"))
        self.m = importlib.util.module_from_spec(importlib.util.spec_from_loader("update_runtimes", loader))
        loader.exec_module(self.m)
        self.alerts = []
        for p in (mock.patch.object(self.m, "HELD", self.tmp / "held.json"),
                  mock.patch.object(self.m.alert, "once", lambda key, problems, ok, ids=None: self.alerts.append((key, problems))),
                  mock.patch.object(self.m.conformance, "record", lambda doc: self.tmp / "r.json")):
            p.start()
            self.addCleanup(p.stop)
        self.dist.latest = lambda: self.latest
        self.dist.version_of = lambda exe, env=None: "2.1.400"
        self.latest = "2.1.400"

    def doc(self, state="pass", version=None):
        return {"runtime": "claude", "version": version or self.latest, "checks": {
            "a": {"port": "agent", "kind": "safety", "state": state, "reason": "leaked" if state != "pass" else ""}}}

    def test_first_install_then_nothing_new_then_a_new_version(self):
        with mock.patch.object(self.m.conformance, "run", lambda r, exe, only=None: self.doc()):
            had, promoted, _said = self.m.first_or_new("claude", self.dist, first=True)
            self.assertEqual((had, promoted, store.current("claude").version), (True, True, "2.1.400"))
            had, promoted, said = self.m.first_or_new("claude", self.dist, first=True)
            self.assertEqual((had, promoted), (True, False), "--first with a copy in place does nothing")
            self.assertEqual(len(self.dist.calls), 1)
            self.assertEqual(self.m.first_or_new("claude", self.dist, first=False)[1:], (False, ""))
            self.latest = "2.1.401"
            self.assertEqual(self.m.first_or_new("claude", self.dist, first=False)[:2], (True, True))
        self.assertEqual((store.current("claude").version, store.previous("claude").version), ("2.1.401", "2.1.400"))

    def test_a_version_that_fails_is_held_back_and_said_by_check(self):
        with mock.patch.object(self.m.conformance, "run", lambda r, exe, only=None: self.doc()):
            self.m.first_or_new("claude", self.dist, first=False)
        self.latest = "2.1.401"
        with mock.patch.object(self.m.conformance, "run", lambda r, exe, only=None: self.doc("fail")):
            had, promoted, said = self.m.first_or_new("claude", self.dist, first=False)
        self.assertEqual((had, promoted, store.current("claude").version), (True, False, "2.1.400"))
        key, problems = self.alerts[-1]
        self.assertEqual(key, "runtime-held-claude")
        self.assertIn("leaked", problems[0])
        self.assertEqual(json.loads((self.tmp / "held.json").read_text())["claude"]["version"], "2.1.401")

    def test_below_the_floor_is_never_promoted(self):
        self.latest = "2.1.200"
        with mock.patch.object(self.m.conformance, "run", lambda r, exe, only=None: self.doc()):
            had, promoted, _said = self.m.first_or_new("claude", self.dist, first=False)
        self.assertEqual((had, promoted), (False, False))
        self.assertIsNone(store.current("claude"))

    def test_the_users_copy_goes_up_never_down(self):
        with mock.patch.object(self.m.conformance, "run", lambda r, exe, only=None: self.doc()):
            self.m.first_or_new("claude", self.dist, first=False)
        put = []
        self.dist.user_copy = lambda: self.tmp / "nvm-claude"
        self.dist.install_global = lambda v: put.append(v)
        for have, want in (("2.1.300", ["2.1.400"]), ("2.1.400", []), ("2.1.500", [])):
            put.clear()
            self.dist.version_of = lambda exe, env=None, have=have: have
            self.m.raise_user_copy("claude", self.dist)
            self.assertEqual(put, want, have)

    def test_a_rollback_holds_until_a_newer_version(self):
        with mock.patch.object(self.m.conformance, "run", lambda r, exe, only=None: self.doc()):
            self.m.first_or_new("claude", self.dist, first=False)
            self.latest = "2.1.401"
            self.m.first_or_new("claude", self.dist, first=False)
            store.rollback("claude")
            self.assertEqual(store.rolled_back_from("claude"), "2.1.401")
            self.assertEqual(self.m.first_or_new("claude", self.dist, first=False)[:2], (True, False),
                             "the version left isn't promoted again the next night")
            self.assertEqual(store.current("claude").version, "2.1.400")
            self.latest = "2.1.402"
            self.assertEqual(self.m.first_or_new("claude", self.dist, first=False)[:2], (True, True))
        self.assertIsNone(store.rolled_back_from("claude"), "a promotion ends it")

    def test_what_is_promoted_is_what_was_checked(self):
        store.install("claude", "2.1.400", self.dist)
        with self.assertRaises(RuntimeError):
            store.promote("claude", "2.1.400", expect="0" * 64)
        self.assertIsNone(store.current("claude"))
        doc = {**self.doc(), "digest": "0" * 64}
        with mock.patch.object(self.m.conformance, "run", lambda r, exe, only=None: doc):
            had, promoted, _said = self.m.first_or_new("claude", self.dist, first=False)
        self.assertEqual((had, promoted), (False, False))

    def test_held_back_clears_when_nothing_newer_is_left(self):
        with mock.patch.object(self.m.conformance, "run", lambda r, exe, only=None: self.doc()):
            self.m.first_or_new("claude", self.dist, first=False)
        self.latest = "2.1.401"
        with mock.patch.object(self.m.conformance, "run", lambda r, exe, only=None: self.doc("fail")):
            self.m.first_or_new("claude", self.dist, first=False)
        self.assertTrue(json.loads((self.tmp / "held.json").read_text()).get("claude"))
        self.latest = "2.1.400"   # the newer one withdrawn
        self.m.first_or_new("claude", self.dist, first=False)
        self.assertFalse(json.loads((self.tmp / "held.json").read_text()).get("claude"))
        self.assertIn(("runtime-held-claude", []), self.alerts)

    def test_no_user_copy_is_left_alone(self):
        with mock.patch.object(self.m.conformance, "run", lambda r, exe, only=None: self.doc()):
            self.m.first_or_new("claude", self.dist, first=False)
        put = []
        self.dist.user_copy = lambda: None
        self.dist.install_global = lambda v: put.append(v)
        self.assertEqual(self.m.raise_user_copy("claude", self.dist), "")
        self.assertEqual(put, [])

    def test_refusals(self):
        with mock.patch.object(self.m.hubconf, "worktree_lookup", lambda: True):
            self.assertEqual(self.m.main([]), 1)
        self.assertEqual(self.m.main(["--nope"]), 2)

    def test_what_is_in_use(self):
        conf = {"runtime.default": "claude", "runtime.installed": ["claude", "acp"], "llm.points": {"x": {"runtime": "fake"}},
                "llm.providers": {"p": {"impl": "claude"}}}
        with mock.patch.object(self.m.hubconf, "get", lambda k, d=None: conf.get(k, d)):
            self.assertEqual(self.m.in_use(), ["claude"], "only runtimes with a distribution port")

    def test_a_runtime_only_installed_for_sessions_is_not_in_use(self):
        """runtime-codex.md, Step 1 r2, D11: a copy of the platform's only for a runtime that runs platform work."""
        dist = {"claude", "codex"}
        caps = lambda n: {"distribution"} if n in dist else set()   # noqa: E731
        for conf, want in (({"runtime.default": "claude", "runtime.installed": ["claude", "codex"]}, ["claude"]),
                           ({"runtime.default": "claude", "llm.points": {"first-look": {"runtime": "codex"}}},
                            ["claude", "codex"]),
                           ({"runtime.default": "claude", "llm.providers": {"c": {"impl": "codex"}}}, ["claude", "codex"]),
                           ({"runtime.default": "codex", "runtime.installed": ["claude", "codex"]}, ["codex"])):
            with mock.patch.object(self.m.hubconf, "get", lambda k, d=None: conf.get(k, d)), \
                    mock.patch.object(self.m.runtime_ports, "capabilities", caps):
                self.assertEqual(self.m.in_use(), want, conf)

    def test_a_copy_whose_login_checks_havent_run_isnt_promoted(self):
        doc = self.doc()
        doc["checks"]["completion-no-tools"] = {"port": "completion", "kind": "safety", "state": "not-checkable",
                                                "reason": "needs a login", "login": True}
        self.assertIn("completion-no-tools", self.m.promotable(doc, self.dist) or "")
        doc["checks"]["completion-no-tools"]["state"] = "pass"
        self.assertIsNone(self.m.promotable(doc, self.dist))


class Location(unittest.TestCase):
    def test_a_throwaway_state_keeps_its_own(self):
        self.assertEqual(hubconf.runtimes_dir(), hubconf.STATE / "runtimes") if hubconf.STATE != hubconf.MAIN / ".state" \
            else self.assertIn(hubconf.runtimes_dir().name, ("runtimes",))


if __name__ == "__main__":
    unittest.main()
