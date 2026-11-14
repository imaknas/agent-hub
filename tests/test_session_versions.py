"""The user's sessions' versions (docs/design/runtime-versions.md sections 5 and 6, step 3): read from the session's
process when its hooks run, kept past its end, checked when first seen by brunna/session-versions, and the narrow gate:
a sandboxed project's session whose version failed a safety check gets no letters, wakes, routes, forks or resumes.

A temporary state, secrets folder, store and bus; the session processes are `sleep`s this test starts; the tool is a
stand-in script and the conformance runner is replaced where a check would run. Nothing reaches a live file, no
Claude session or model call is started.

    bin/py -m unittest tests.test_session_versions
"""
import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import alert  # noqa: E402
import conformance  # noqa: E402
import hubconf  # noqa: E402
import runtime_claude  # noqa: E402
import runtime_ports  # noqa: E402
import runtime_store  # noqa: E402
import session_versions  # noqa: E402
import store_fixture  # noqa: E402
import test_bus_sender as tbs  # noqa: E402
import test_claude_session_records as tcsr  # noqa: E402
import test_mailbox_dispatch as tmd  # noqa: E402
import test_mailbox_wake as tmw  # noqa: E402

A, B, C = tcsr.A, tcsr.B, tcsr.C
OLD, NEW = "2.1.300", "2.1.400"   # a version the user's sessions run; the platform's current copy (store_fixture's)
DOCS = {"box": {"v": 2, "name": "box", "enforce": {"sandbox": True}}, "open": {"v": 2, "name": "open"}}


class Instance(unittest.TestCase):
    """A temporary state, secrets folder and registry (box sandboxed, open not)."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        for target, value in ((hubconf, {"STATE": self.tmp / "state", "LOGS": self.tmp / "logs"}),):
            for k, v in value.items():
                p = mock.patch.object(target, k, v)
                p.start()
                self.addCleanup(p.stop)
        for name, fn in (("secrets_dir", lambda: self.tmp / "secrets"), ("projects", lambda: DOCS)):
            p = mock.patch.object(hubconf, name, fn)
            p.start()
            self.addCleanup(p.stop)
        d = mock.patch.object(runtime_ports, "default_name", lambda: "claude")
        d.start()
        self.addCleanup(d.stop)

    def version_file(self, sid, version=OLD, project="box", build=None):
        f = hubconf.session_file(sid, runtime_ports.SESSION_VERSION)
        f.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        f.write_text(json.dumps({"runtime": "claude", "version": version, "project": project,
                                 **({"build": build} if build else {})}))
        return f

    def result(self, version=OLD, current=NEW, **checks):
        """A session version's result: name=(kind, state)."""
        doc = {"runtime": "claude", "version": version, "current": current, "side": "session", "at": "x",
               "checks": {n.replace("_", "-"): {"port": "p", "kind": k, "state": s, "reason": "why"}
                          for n, (k, s) in checks.items()}}
        f = runtime_ports.session_result_file("claude", version)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(doc))
        return doc


# ---------- the version at session start ----------

class Layouts(unittest.TestCase):
    """runtime_claude.version_at: each layout's version from its real path, read as files, never run."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        home = self.tmp / "home"
        for patch in (mock.patch.object(pathlib.Path, "home", lambda: home),
                      mock.patch.object(runtime_claude, "_home", lambda: home / ".claude"),
                      mock.patch.object(runtime_claude, "_store", lambda: self.tmp / "runtimes" / "claude"),
                      mock.patch.object(hubconf, "nvm_dir", lambda: self.tmp / "nvm")):
            patch.start()
            self.addCleanup(patch.stop)
        self.home = home

    def file(self, path, text="x"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def package(self, top, version):
        pkg = top / "lib" / "node_modules" / "@anthropic-ai" / "claude-code"
        self.file(pkg / "package.json", json.dumps({"name": "@anthropic-ai/claude-code", "version": version}))
        return self.file(pkg / "bin" / "claude.exe")

    def test_desktops_file_names_the_version_and_its_build(self):
        exe = self.file(self.home / ".claude" / "remote" / "ccd-cli" / "2.1.317-42a2f87f038a")
        self.assertEqual(runtime_claude.version_at(exe), ("2.1.317", "42a2f87f038a"))

    def test_a_store_copy_by_its_folder(self):
        exe = self.file(self.tmp / "runtimes" / "claude" / NEW / "node_modules" / "@anthropic-ai" / "claude-code" / "bin"
                        / "claude.exe")
        self.assertEqual(runtime_claude.version_at(exe), (NEW, None))

    def test_the_native_installers_copy_by_its_name(self):
        exe = self.file(self.home / ".local" / "share" / "claude" / "versions" / "2.1.330")
        self.assertEqual(runtime_claude.version_at(exe), ("2.1.330", None))
        odd = self.file(self.home / ".local" / "share" / "claude" / "versions" / "latest")
        self.assertEqual(runtime_claude.version_at(odd), (runtime_ports.UNREADABLE, None))

    def test_nvms_copy_by_its_package(self):
        exe = self.package(self.tmp / "nvm" / "versions" / "node" / "v24.0.0", "2.1.350")
        self.assertEqual(runtime_claude.version_at(exe), ("2.1.350", None))

    def test_anything_else_is_unreadable(self):
        no = (runtime_ports.UNREADABLE, None)
        pkg = self.tmp / "ws" / "proj" / "node_modules" / "@anthropic-ai" / "claude-code"
        self.file(pkg / "package.json", json.dumps({"name": "@anthropic-ai/claude-code", "version": "2.1.350"}))
        project_copy = self.file(pkg / "bin" / "claude.exe")
        for exe in (project_copy,                                                         # a project's own node_modules
                    self.file(self.tmp / "elsewhere" / "remote" / "ccd-cli" / "2.1.317-42a2f87f038a"),   # not the home's
                    self.file(self.home / ".claude" / "remote" / "ccd-cli" / "latest"),  # not a version-build name
                    self.file(self.tmp / "runtimes" / "claude" / "stub" / "security"),   # in the store, not a version
                    self.file(self.tmp / "usr" / "bin" / "claude")):
            with self.subTest(exe=exe.relative_to(self.tmp)):
                self.assertEqual(runtime_claude.version_at(exe), no)

    def test_a_package_json_that_names_another_package_is_not_believed(self):
        top = self.tmp / "nvm" / "versions" / "node" / "v24.0.0"
        exe = self.package(top, "2.1.350")
        self.file(top / "lib" / "node_modules" / "@anthropic-ai" / "claude-code" / "package.json",
                  json.dumps({"name": "other", "version": "2.1.350"}))
        self.assertEqual(runtime_claude.version_at(exe), (runtime_ports.UNREADABLE, None))

    def test_from_a_process_never_running_it(self):
        p = tcsr.session_process(self)   # sleep: a layout the adapter doesn't know
        self.assertEqual(runtime_ports.process_path(p.pid), os.path.realpath(runtime_ports.process_path(p.pid)))
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("ran something")):
            self.assertEqual(runtime_claude.session_version(p.pid), (runtime_ports.UNREADABLE, None))
        self.assertEqual(runtime_claude.session_version(None), (runtime_ports.UNREADABLE, None))
        self.assertEqual(runtime_ports.process_path(os.getpid()), os.path.realpath(sys.executable))


class RecordKeepsTheVersion(tcsr.SecretsDir):
    """record_session carries the version on every write and keeps it in <session>.version past the session's end."""

    def setUp(self):
        super().setUp()
        self.asked = []

    def version(self, pid):
        self.asked.append(pid)
        return ("2.1.321", "0add941e9cdd")

    def read(self, sid, kind="live"):
        return json.loads(hubconf.session_file(sid, kind).read_text())

    def test_at_the_start(self):
        p = tcsr.session_process(self)
        runtime_ports.record_session(B, "claude", p.pid, "/ws/box", project_of=lambda f: "box", version=self.version)
        self.assertEqual({k: self.read(B)[k] for k in ("version", "build", "project")},
                         {"version": "2.1.321", "build": "0add941e9cdd", "project": "box"})
        self.assertEqual(self.read(B, "version"),
                         {"runtime": "claude", "version": "2.1.321", "build": "0add941e9cdd", "project": "box"})

    def test_a_clear_takes_over_with_the_version(self):
        p = tcsr.session_process(self)
        runtime_ports.record_session(B, "claude", p.pid, "/ws/box", project_of=lambda f: "box", version=self.version)
        runtime_ports.record_session(C, "claude", p.pid, "/ws/box", project_of=lambda f: "box", version=self.version)
        self.assertFalse(hubconf.session_file(B, "live").exists())
        self.assertEqual(self.read(C)["version"], "2.1.321")
        self.assertEqual(self.read(C, "version")["project"], "box")
        self.assertTrue(hubconf.session_file(B, "version").exists(), "the session it replaced keeps its version")

    def test_a_record_written_before_versions_gets_one_at_its_next_hook_then_costs_nothing(self):
        p = tcsr.session_process(self)
        f = hubconf.session_file(B, "live")
        f.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        f.write_text(json.dumps({"runtime": "claude", "pid": p.pid, "start": runtime_ports.process_start(p.pid),
                                 "cwd": "/ws/box", "project": "box"}))
        self.assertTrue(runtime_ports.record_session(B, "claude", p.pid, "/ws/box", refresh=False, version=self.version))
        self.assertEqual(self.read(B)["version"], "2.1.321")
        self.assertEqual(self.read(B, "version")["version"], "2.1.321")
        self.asked.clear()
        self.assertFalse(runtime_ports.record_session(B, "claude", p.pid, "/ws/box", refresh=False, version=self.version))
        self.assertEqual(self.asked, [])

    def test_what_cant_be_read_is_said_unreadable(self):
        p = tcsr.session_process(self)
        runtime_ports.record_session(B, "claude", p.pid, "/ws/box", version=lambda pid: (None, None))
        self.assertEqual(self.read(B)["version"], runtime_ports.UNREADABLE)
        runtime_ports.record_session(C, "claude", p.pid, "/ws/box", version=lambda pid: 1 / 0)
        self.assertEqual(self.read(C)["version"], runtime_ports.UNREADABLE)

    def test_kept_past_the_end_and_pruned_after_30_days(self):
        p = tcsr.session_process(self)
        runtime_ports.record_session(B, "claude", p.pid, "/ws/box", version=self.version)
        self.assertNotIn(hubconf.session_file(B, "version"), hubconf.session_files(B), "the session's end leaves it")
        tcsr.end(p)
        hb = tmd.load_hubbus()
        hb.prune_wake_addresses(live=set())
        self.assertTrue(hubconf.session_file(B, "version").exists())
        old = time.time() - (hb.VERSION_KEEP_DAYS + 1) * 86400
        os.utime(hubconf.session_file(B, "version"), (old, old))
        hb.prune_wake_addresses(live=set())
        self.assertFalse(hubconf.session_file(B, "version").exists())

    def test_a_closed_sessions_version_comes_from_its_file(self):
        p = tcsr.session_process(self)
        runtime_ports.record_session(B, "claude", p.pid, "/ws/box", project_of=lambda f: "box", version=self.version)
        hubconf.session_file(B, "live").unlink()
        self.assertEqual(runtime_ports.session_version(B)["version"], "2.1.321")
        self.assertIsNone(runtime_ports.session_version(A), "nothing on file: pending")


# ---------- the gate ----------

class Gate(Instance):
    """runtime_ports.gated: a sandboxed project's session whose version failed a safety check; nothing else."""

    def test_a_failed_safety_check_in_a_sandboxed_project(self):
        self.version_file("S1", project="box")
        self.result(control=("safety", "fail"), lifecycle_events=("function", "pass"))
        self.assertEqual(runtime_ports.gated("S1"), ["control"])
        s = runtime_ports.Sessions(["claude"])
        self.assertEqual(s.gated("S1"), ["control"])
        self.assertFalse(s.can_resume("S1"))

    def test_never_for_an_unsandboxed_project(self):
        self.version_file("S1", project="open")
        self.result(control=("safety", "fail"))
        self.assertEqual(runtime_ports.gated("S1"), [])

    def test_not_while_pending_or_for_anything_but_a_safety_fail(self):
        self.version_file("S1", project="box")
        self.assertEqual(runtime_ports.gated("S1"), [], "no result yet")
        for checks in ({"control": ("safety", "error")}, {"control": ("safety", "not-checkable")},
                       {"lifecycle_events": ("function", "fail")}, {"control": ("safety", "pass")}):
            with self.subTest(checks=checks):
                self.result(**checks)
                self.assertEqual(runtime_ports.gated("S1"), [])
        self.version_file("S2", version=runtime_ports.UNREADABLE, project="box")
        self.assertEqual(runtime_ports.gated("S2"), [])
        self.assertEqual(runtime_ports.gated("S3"), [], "no version on file")

    def test_a_project_renamed_since_is_found_by_its_former_name(self):
        self.version_file("S1", project="oldbox")
        self.result(control=("safety", "fail"))
        with mock.patch.object(hubconf, "former_names", lambda n: ["oldbox"] if n == "box" else []):
            self.assertEqual(runtime_ports.gated("S1"), ["control"])

    def test_a_registry_that_cant_be_read_gates_a_recorded_failure(self):
        self.version_file("S1", project="box")
        self.result(control=("safety", "fail"))
        with mock.patch.object(hubconf, "projects", side_effect=OSError("unreadable")):
            self.assertEqual(runtime_ports.gated("S1"), ["control"])


class Forks(Instance):
    """Section 5: a fork or resume of a session with the platform's current copy needs the pair to have passed
    sessions-fork; a pair not checked yet is as before; a lease names the version that wrote its session."""

    def setUp(self):
        super().setUp()
        store_fixture.stand_in(self)   # current: NEW
        self.version_file("S1", project="open")

    def test_a_failed_pair_is_refused_like_a_missing_port(self):
        self.result(sessions_fork=("function", "fail"))
        self.assertIsNotNone(runtime_ports.fork_refusal("S1"))
        self.assertFalse(runtime_ports.Sessions(["claude"]).can_resume("S1"))

    def test_kept_while_pending_passed_or_the_copys_own_version(self):
        s = runtime_ports.Sessions(["claude"])
        self.assertTrue(s.can_resume("S1"), "no result yet")
        self.result(sessions_fork=("function", "pass"))
        self.assertTrue(s.can_resume("S1"))
        self.result(sessions_fork=("function", "fail"), current="2.1.399")
        self.assertIsNone(runtime_ports.fork_refusal("S1"), "checked against another copy: pending again")
        self.result(sessions_fork=("function", "error"))
        self.assertIsNone(runtime_ports.fork_refusal("S1"))
        self.version_file("S2", version=NEW, project="open")
        self.assertEqual(runtime_ports.pair_state("claude", NEW)[0], "same")

    def test_lease_resume_refuses_a_pair_that_didnt_pass(self):
        sid = A
        for checks, word in (({"sessions_fork": ("function", "fail")}, "fail"), ({}, "pending")):
            with self.subTest(word):
                self.result(**checks) if checks else runtime_ports.session_result_file("claude", OLD).unlink()
                with self.assertRaises(runtime_ports.Unsupported):
                    runtime_claude.resume_exec(sid, env={}, written_by=OLD)
        self.result(sessions_fork=("function", "pass"))
        argv, _env = runtime_claude.resume_exec(sid, env={}, written_by=OLD)
        self.assertEqual(argv[1:], ["--resume", sid])
        argv, _env = runtime_claude.resume_exec(sid, env={}, written_by=NEW)
        self.assertEqual(argv[1:], ["--resume", sid])


class LeaseRunner(unittest.TestCase):
    """The lease runner's own resume of a lease (section 5): refused on a failed pair, as before while it is pending; the
    version a lease records moves to the copy that runs it only when that copy wrote it or was checked resuming it."""
    import test_lease_runner_resume as _tlr
    setUp, tearDown, lease, flag = _tlr.Resume.setUp, _tlr.Resume.tearDown, _tlr.Resume.lease, _tlr.Resume.flag

    def start(self, pair, resume=True):
        self.has_transcript = True
        lease = self.lease(started_once=True, version=OLD)
        with mock.patch.object(runtime_ports, "pair_state", lambda runtime, version: (pair, NEW, "why")), \
                mock.patch.object(self.m, "run_version", lambda: NEW):
            self.m.start_proc(lease, resume=resume)
        return lease

    def test_a_failed_pair_is_refused(self):
        with self.assertRaises(RuntimeError):
            self.start("fail")
        self.assertEqual(self.cmds, [])

    def test_pending_resumes_and_keeps_the_version_that_wrote_it(self):
        self.assertEqual(self.start("pending").version, OLD)
        self.assertEqual(self.flag(), "--resume")

    def test_a_passed_pair_takes_the_copys_version(self):
        self.assertEqual(self.start("pass").version, NEW)

    def test_the_record_keeps_it(self):
        lease = self.lease(version=OLD)
        self.assertEqual(self.m.Lease(lease.dump()).version, OLD)


class Listing(Instance):
    """One `agents --json` a tick, within the deadline it is given; a session whose version failed session-status is
    unlisted, one not checked yet keeps the listing's word."""

    def setUp(self):
        super().setUp()
        self.ran = self.tmp / "ran"
        rows = json.dumps([{"sessionId": "S1", "status": "idle"}, {"sessionId": "S2", "status": "idle"}])
        store_fixture.stand_in(self, body=f"#!/bin/sh\ntouch '{self.ran}'\nprintf '%s' '{rows}'\n")

    def test_a_failed_session_status_check_is_unlisted(self):
        self.version_file("S1")
        self.result(session_status=("function", "fail"))
        self.assertEqual(runtime_claude.session_statuses(["S1", "S2"]), {"S1": "unlisted", "S2": "idle"})

    def test_pending_keeps_the_listing(self):
        self.version_file("S1")
        self.assertEqual(runtime_claude.session_statuses(["S1", "S2"]), {"S1": "idle", "S2": "idle"})

    def test_no_time_left_is_unlisted_and_nothing_runs(self):
        self.assertEqual(runtime_claude.session_statuses(["S1"], deadline=time.monotonic() - 1), {"S1": "unlisted"})
        self.assertFalse(self.ran.exists())

    def test_the_tick_passes_what_is_left_of_its_budget(self):
        got = []
        rt = types.SimpleNamespace(session_statuses=lambda sids, deadline: got.append(deadline) or {s: "idle" for s in sids},
                                   can_wake=lambda s: True)
        hb = tmd.load_hubbus()
        t = hb.Tick(None, rt, {"S1"}, {"S1": {}}, set())
        t.spent = hb.STATUS_TICK_S - 2
        before = time.monotonic()
        self.assertEqual(t.status("S1"), "idle")
        self.assertTrue(before + 1 <= got[0] <= time.monotonic() + 2)


class HookGate(Instance):
    """hub-event: for a gated session the record, its end and the deny run; mail (and with it presence), the wake
    address and the reminder don't, and it is told once at its start."""

    def setUp(self):
        super().setUp()
        m = self.m = tmw.load_file("hub_event_gate", BIN / "hub-event")
        m.LOG = self.tmp / "hub-event.log"
        m._workspace = lambda cwd: None
        m.project_git = types.SimpleNamespace(sandbox_of=lambda cwd: None, scope_of=lambda ws: None)
        m._hubbus = lambda: types.SimpleNamespace(project_of=lambda cwd: self.project)
        self.project, self.ran = "box", []
        record = lambda name: (lambda ev, ws: self.ran.append(name))   # noqa: E731
        m.HANDLERS = {k: [(n, record(n)) for n, _f in v] for k, v in m.HANDLERS.items()}
        m.NOT_IN_FOLDER = m.NOT_IN_FOLDER | {"nudge", "memory_sync"}   # run here as where git may run
        m.session_record = record("session_record")
        m.mail = record("mail")
        self.p = tcsr.session_process(self)
        for patch in (mock.patch.dict(os.environ, {"CLAUDE_PID": str(self.p.pid)}),
                      mock.patch.object(runtime_claude, "session_version", lambda pid: (OLD, None))):
            patch.start()
            self.addCleanup(patch.stop)
        for var in ("HUB_LLM_POINT", "HUBBUS_LEASE", "HUB_AS_PROJECT"):
            os.environ.pop(var, None)

    def hook(self, event):
        return self.m.handle(event, json.dumps({"session_id": B, "cwd": str(self.tmp)}), runtime_claude, "claude")

    def test_a_gated_session_gets_only_what_always_runs_and_is_told_at_its_start(self):
        self.result(control=("safety", "fail"))
        out = self.hook("session_start")
        self.assertNotIn("mail", self.ran)
        self.assertNotIn("wake_address", self.ran)
        self.assertNotIn("nudge", self.ran)
        self.assertIn("session_record", self.ran)
        self.assertIn("control", out)
        self.assertIn("mail:gated", self.m.LOG.read_text())
        self.ran.clear()
        self.assertEqual(self.hook("prompt"), "")
        self.assertEqual(self.ran, ["session_record"])
        self.ran.clear()
        self.hook("session_end")
        self.assertEqual(sorted(self.ran), ["end_mark", "mail_end", "memory_sync", "wake_address_drop"])

    def test_pending_and_unsandboxed_run_as_before(self):
        for case in ("pending", "open"):
            with self.subTest(case):
                self.ran.clear()
                self.m._GATE.clear()
                if case == "open":
                    self.result(control=("safety", "fail"))
                    self.project = "open"
                self.hook("session_start")
                self.assertIn("mail", self.ran)
                self.assertIn("wake_address", self.ran)

    def live_record(self, version, start=None):
        f = hubconf.session_file(B, runtime_ports.SESSION_RECORD)
        f.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        f.write_text(json.dumps({"runtime": "claude", "pid": self.p.pid, "project": "box", "cwd": str(self.tmp),
                                 "start": start or runtime_ports.process_start(self.p.pid), "version": version}))

    def test_the_record_of_this_process_counts_over_files_changed_under_it(self):
        """nvm's package.json says another version after an update, while the running session is still the one recorded."""
        self.result(control=("safety", "fail"))
        self.live_record(OLD)
        with mock.patch.object(runtime_claude, "session_version", lambda pid: (NEW, None)):
            self.hook("prompt")
        self.assertNotIn("mail", self.ran)
        self.ran.clear()
        self.m._GATE.clear()
        self.live_record(NEW)
        self.hook("prompt")   # the files say OLD (failed); the process was recorded as NEW
        self.assertIn("mail", self.ran)

    def test_a_record_of_another_process_with_the_same_pid_doesnt(self):
        self.result(control=("safety", "fail"))
        self.live_record(NEW, start="1")
        self.hook("prompt")
        self.assertNotIn("mail", self.ran)

    def test_the_worktree_entry_too(self):
        self.result(control=("safety", "fail"))
        raw = json.dumps({"session_id": B, "cwd": str(self.tmp)})
        with mock.patch("sys.stdout", new=__import__("io").StringIO()) as out:
            self.m.worktree("start", raw, "claude")
        self.assertEqual(self.ran, ["session_record"])
        self.assertIn("control", out.getvalue())
        self.ran.clear()
        self.m.worktree("nudge", raw, "claude")
        self.assertEqual(self.ran, ["session_record"])
        self.assertIn("nudge:gated", self.m.LOG.read_text())


class McpNotice(Instance):
    """The MCP server points a gated session at no letter: no "N letters open" after its tools' results."""

    def setUp(self):
        super().setUp()
        import mcp_server
        self.ms = mcp_server
        mcp_server._GATED.clear()
        self.addCleanup(mcp_server._GATED.clear)
        for patch in (mock.patch.object(mcp_server, "unread", lambda project: 2),
                      mock.patch.object(runtime_ports, "current_session", lambda env=None: "S1"),
                      mock.patch.dict(os.environ)):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("HUB_LLM_POINT", None)
        self.version_file("S1", project="box")

    def test_said_to_a_session_that_isnt_gated(self):
        self.assertTrue(self.ms.notice("box", "send"))

    def test_not_to_a_gated_one(self):
        self.result(control=("safety", "fail"))
        self.assertEqual(self.ms.notice("box", "send"), "")


class GatingRuntime(tmw.WakingRuntime):
    """tests.test_mailbox_wake's runtime with the gate: the sessions in `gates` failed a safety check."""

    def __init__(self):
        super().__init__()
        self.gates = set()

    def gated(self, sid):
        return ["control"] if sid in self.gates else []

    def can_resume(self, sid):
        return True


class DispatchGate(tmw.Harness):
    """hubbus: a gated session is no candidate, isn't woken (notices and holders included), isn't waited for, and is
    never forked or resumed; a letter for it goes on as if it weren't there."""

    def setUp(self):
        super().setUp()
        self.rt = GatingRuntime()

    def test_never_a_candidate_never_woken(self):
        self.rt.live |= {"S1", "S2"}
        self.rt.gates.add("S1")
        self.seen("S1", minutes_ago=1)
        self.seen("S2", minutes_ago=3)
        self.send("m1", "proj", "request")
        self.dispatch()
        self.assertEqual([s for s, _t in self.rt.woken], ["S2"])

    def test_alone_it_isnt_waited_for_or_forked_and_the_letter_goes_to_triage(self):
        self.rt.live.add("S1")
        self.rt.gates.add("S1")
        self.seen("S1")
        self.send("m1", "proj", "question", to_session="S1")
        self.dispatch()
        self.assertEqual(self.rt.woken, [])
        self.assertEqual(self.resumed, [])
        self.assertEqual(self.handled, [("proj", ["m1"])])

    def test_a_notice_for_it_wakes_nothing(self):
        self.rt.live.add("S1")
        self.rt.gates.add("S1")
        self.send("m1", "proj", "info", to_session="S1")
        self.dispatch()
        self.assertEqual(self.rt.woken, [])

    def test_each_choke_point(self):
        m = self.m
        self.rt.live |= {"S1", "S2"}
        self.rt.gates.add("S1")
        self.seen("S1")
        self.seen("S2")
        t = m.Tick(self.c, self.rt, self.rt.live, self.rt.open_sessions(), set())
        self.assertEqual((t.can_wake("S1"), t.can_wake("S2")), (False, True))
        self.assertEqual(m.active_sessions(self.c, "proj", self.rt.live, [], self.rt), ["S2"])
        self.assertEqual(sorted(m.active_sessions(self.c, "proj", self.rt.live, [])), ["S1", "S2"], "no runtime: no gate")
        self.assertEqual((m.can_resume(self.rt, "S1"), m.can_resume(self.rt, "S2")), (False, True))
        self.send("m1", "proj", "request")
        self.assertEqual(m.candidates(self.c, t, "proj", m.get_message(self.c, "m1")), ["S2"])

    def test_pending_is_as_before(self):
        self.rt.live.add("S1")
        self.seen("S1")
        self.send("m1", "proj", "request")
        self.dispatch()
        self.assertEqual([s for s, _t in self.rt.woken], ["S1"])


class OutboxGate(tbs.Fixture):
    """A gated session's `brunna inbox` (told) and `brunna ack` through its project's outbox are read past: it can't hold
    a letter away from the project's other sessions."""

    on_bus = tbs.Outbox.on_bus

    def take(self, gated):
        req = self.letter_id(self.hubbus("send", "--to", "box", "--type", "request", "--text", "r", cwd=self.ws["open"]).stdout)
        m = self.on_bus()
        m.sessions_port = lambda: types.SimpleNamespace(live_sessions=lambda: {"s-box"},
                                                       gated=lambda s: ["control"] if gated else [])
        c = m.db()
        self.addCleanup(c.close)
        if not gated:   # a gated session has no presence: its hooks' mail, which records it, is off
            m.record_presence(c, "s-box", "box", self.ws["box"])
        self.drop({"v": 1, "op": "told", "ids": [req], "session": "s-box"}, name="1.json")
        self.drop({"v": 1, "op": "ack", "ids": [req], "session": "s-box"}, name="2.json")
        with mock.patch.object(m.hubconf, "projects", lambda: self.docs):
            m.take_outboxes(c)
        self.assertEqual(list(self.outbox().iterdir()), [], "both files taken in and deleted")
        self.held = m.being_handled(c, m.get_event(c, req), lambda s: True)
        return [json.loads(r["payload"]).get("via") for r in self.rows("type='message.delivered' AND ref=?", (req,))]

    def test_ignored_from_a_gated_session(self):
        self.assertEqual(self.take(gated=True), [])
        self.assertFalse(self.held, "held neither by the gated session's ack nor by an ack that lost its session")

    def test_taken_from_any_other(self):
        self.assertEqual(sorted(self.take(gated=False)), ["ack", "inbox"])


# ---------- the check job ----------

class Dist(store_fixture.Dist):
    """The distribution port the job sees: installs a stand-in, publishes `published`."""

    def __init__(self, published=(OLD,)):
        super().__init__("#!/bin/sh\n")
        self.published_list, self.asked = list(published), 0

    def published(self):
        self.asked += 1
        if isinstance(self.published_list, Exception):
            raise self.published_list
        return list(self.published_list)


class Job(Instance):
    def setUp(self):
        super().setUp()
        store_fixture.stand_in(self)   # current: NEW
        self.dist = Dist()
        self.live: dict = {}
        self.runs, self.outcome = [], {}
        self.alerts = []
        for patch in (mock.patch.object(runtime_ports, "_module", lambda n: self.dist),
                      mock.patch.object(runtime_ports, "open_sessions", lambda: dict(self.live)),
                      mock.patch.object(conformance, "run", self.fake_run),
                      mock.patch.object(conformance, "RESULTS", self.tmp / "state" / "runtime-conformance"),
                      mock.patch.object(alert, "once", lambda key, lines, ok, ids=None: self.alerts.append((key, ids)))):
            patch.start()
            self.addCleanup(patch.stop)

    def fake_run(self, runtime, exe, only=None, pair=None):
        version = pathlib.Path(exe).relative_to(runtime_store.root(runtime)).parts[0]
        self.runs.append((version, tuple(only or ()), pair))
        checks = {n: {"port": "p", "kind": "safety" if n in ("control", "confine-sandbox") else "function",
                      "state": self.outcome.get(n, "pass"), "reason": "r", "tries": 1} for n in only}
        return {"runtime": runtime, "version": version, "binary": exe, "digest": "d", "at": "now", "checks": checks}

    def live_session(self, sid, version, build=None):
        self.live[sid] = {"runtime": "claude", "pid": 1, "start": "1", "cwd": "/ws/box", "project": "box",
                          "version": version, **({"build": build} if build else {})}

    def run_job(self):
        return session_versions.run_runtime("claude")

    def test_live_first_then_closed_within_seven_days_never_a_transcript(self):
        self.live_session("S1", "2.1.302", build="ab12")
        self.version_file("S2", version="2.1.301")
        old = self.version_file("S3", version="2.1.299")
        t = time.time() - 8 * 86400
        os.utime(old, (t, t))
        st = {}
        order = session_versions.note_seen("claude", st, session_versions.seen_now("claude", session_versions._now()),
                                           session_versions._now())
        self.assertEqual(order, ["2.1.302", "2.1.301"])
        self.assertEqual(list(st["seen"]["2.1.302"]["builds"]), ["ab12"])

    def test_a_lease_by_another_version_counts_however_long_it_has_been_idle(self):
        f = hubconf.STATE / "lease-runner" / "leases.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"a": {"version": OLD, "last_activity": "2000-01-01T00:00:00+00:00"},
                                 "b": {"version": NEW, "last_activity": "2000-01-01T00:00:00+00:00"},
                                 "c": {"last_activity": "2000-01-01T00:00:00+00:00"}}))
        self.assertEqual(session_versions.seen_now("claude", session_versions._now()), [(OLD, None, False)])

    def test_a_version_is_installed_checked_paired_with_current_and_recorded(self):
        self.live_session("S1", OLD)
        self.run_job()
        cur = runtime_store.current("claude")
        self.assertEqual(self.runs, [(OLD, tuple(conformance.session_checks("claude")), str(cur.exe))])
        self.assertIn(runtime_ports.FORK_CHECK, conformance.session_checks("claude"))
        doc = runtime_ports.session_result("claude", OLD)
        self.assertEqual((doc["side"], doc["current"]), ("session", NEW))
        self.assertEqual(runtime_store.installed("claude", OLD)["kind"], runtime_store.SESSION)
        self.assertIsNone(runtime_store.entry("claude", OLD), "never promoted")
        self.assertEqual(conformance.recorded("claude"), [], "beside the full results, not one of them")
        self.runs.clear()
        self.run_job()
        self.assertEqual(self.runs, [], "checked once")

    def test_at_most_three_installs_a_day(self):
        versions = ["2.1.301", "2.1.302", "2.1.303", "2.1.304"]
        self.dist.published_list = versions
        for i, v in enumerate(versions):
            self.live_session(f"S{i}", v)
        self.run_job()
        self.assertEqual(len(self.runs), 3)
        self.run_job()
        self.assertEqual(len(self.runs), 3, "the fourth waits for tomorrow")
        st = session_versions.load("claude")
        st["installs"]["date"] = "2000-01-01"
        session_versions.save("claude", st)
        self.run_job()
        self.assertEqual(len(self.runs), 4)

    def test_not_published_is_not_installable_and_tried_again_weekly(self):
        self.dist.published_list = [NEW]
        self.live_session("S1", OLD)
        self.run_job()
        self.assertEqual(self.runs, [])
        self.assertEqual([r["state"] for r in session_versions.summary("claude") if r["version"] == OLD], ["not-installable"])
        self.dist.published_list = [OLD]
        self.run_job()
        self.assertEqual(self.runs, [], "not before a week")
        st = session_versions.load("claude")
        st["not_installable"][OLD]["at"] = "2000-01-01T00:00:00+00:00"
        session_versions.save("claude", st)
        self.run_job()
        self.assertEqual([v for v, _o, _p in self.runs], [OLD])

    def test_a_vendor_that_cant_be_asked_marks_nothing(self):
        self.dist.published_list = RuntimeError("offline")
        self.live_session("S1", OLD)
        self.run_job()
        self.assertEqual(self.runs, [])
        self.assertNotIn(OLD, session_versions.load("claude").get("not_installable") or {})

    def test_a_failed_safety_check_alerts_per_check_and_reason_until_no_version_seen_fails_it(self):
        self.outcome["control"] = "fail"
        self.live_session("S1", OLD)
        self.run_job()
        self.assertIn(("session-versions-claude", ["control:r"]), self.alerts)
        self.assertEqual([r["state"] for r in session_versions.summary("claude")], ["failed"])
        self.assertEqual([row[0] for row in session_versions.doctor_rows("claude")], ["warn"])
        self.live.clear()
        st = session_versions.load("claude")
        st["seen"][OLD]["last"] = "2000-01-01T00:00:00+00:00"
        session_versions.save("claude", st)
        self.alerts.clear()
        self.run_job()
        self.assertIn(("session-versions-claude", []), self.alerts)

    def test_a_runtime_whose_sessions_cant_be_confined_gets_no_session_copies_unless_it_runs_platform_work(self):
        """runtime-codex.md, Step 1 r2, S6: nothing installed or checked for it."""
        why = {"codex": "Codex runs git in the folder (B1)"}
        for work, want in ((["claude"], ["claude"]), (["claude", "codex"], ["claude", "codex"])):
            with mock.patch.object(runtime_ports, "installed", lambda: ["claude", "codex"]), \
                    mock.patch.object(runtime_ports, "capabilities", lambda n: {"distribution"}), \
                    mock.patch.object(runtime_ports, "unconfined_reason", lambda n: why.get(n)), \
                    mock.patch.object(runtime_ports, "platform_work", lambda: work):
                self.assertEqual(session_versions.runtimes(), want)

    def test_another_version_failing_the_same_way_is_the_same_alert(self):
        self.outcome["control"] = "fail"
        self.live_session("S1", OLD)
        self.live_session("S2", "2.1.398")
        self.run_job()
        keys = {k for k, ids in self.alerts if ids}
        self.assertEqual(keys, {"session-versions-claude"}, "keyed by check and reason, never by version")
        self.assertEqual([ids for k, ids in self.alerts if k == "session-versions-claude"][-1], ["control:r"])

    def test_the_per_version_alerts_of_before_end_once(self):
        session_versions.save("claude", {"seen": {}, "alerted": [OLD]})
        self.run_job()
        self.run_job()
        self.assertEqual([k for k, _ids in self.alerts if k == f"session-version-claude-{OLD}"],
                         [f"session-version-claude-{OLD}"])

    def test_a_pair_checked_against_another_copy_reruns_the_fork_alone_and_an_error_daily(self):
        self.live_session("S1", OLD)
        self.full_result(current="2.1.399")
        self.assertEqual(session_versions.what_to_run("claude", OLD, NEW), [runtime_ports.FORK_CHECK])
        self.run_job()
        self.assertEqual([o for _v, o, _p in self.runs], [(runtime_ports.FORK_CHECK,)])
        doc = runtime_ports.session_result("claude", OLD)
        self.assertEqual((doc["current"], sorted(doc["checks"])), (NEW, sorted(conformance.session_checks("claude"))))
        doc["checks"]["control"]["state"] = "error"
        self.full_result(checks=doc["checks"], retried="2000-01-01")
        self.assertEqual(session_versions.what_to_run("claude", OLD, NEW), ["control"])
        self.full_result(checks=doc["checks"])
        self.assertIsNone(session_versions.what_to_run("claude", OLD, NEW), "once a day")

    def test_unusable_payloads_three_times_alert_by_event_and_reason(self):
        """The lines as hub-event itself writes them (its own clock and format), not a copy of the format."""
        hook = tmw.load_file("hub_event_unusable_job", BIN / "hub-event")
        hook.LOG = hubconf.LOGS / "hub-event.log"
        p = tcsr.session_process(self)
        with mock.patch.dict(os.environ, {"CLAUDE_PID": str(p.pid)}), \
                mock.patch.object(runtime_claude, "session_version", lambda pid: (OLD, None)):
            def send(event, n):
                for _i in range(n):
                    hook.handle(event, "not json", runtime_claude, "claude")
            send("prompt", 2)
            send("session_end", 5)   # not an event letters depend on
            self.run_job()
            self.assertFalse([k for k, _i in self.alerts if k.startswith("session-unusable")])
            send("prompt", 1)
        self.run_job()
        self.assertIn(("session-unusable-claude-prompt-not_json", ["prompt:not_json"]), self.alerts)
        self.assertTrue(session_versions.unusable_rows("claude"))
        old = (datetime.datetime.now() - datetime.timedelta(days=8)).isoformat(timespec="seconds")
        hook.LOG.write_text("".join(f"{old} prompt - ran=- failed=- unusable=not_json runtime=claude version={OLD}\n"
                                    for _i in range(5)))
        self.alerts.clear()
        self.run_job()
        self.assertIn(("session-unusable-claude-prompt-not_json", []), self.alerts, "older than 7 days: resolved")

    def full_result(self, **override):
        """Every session-side check recorded as passed against today's code and current, retried today."""
        every = conformance.session_checks("claude")
        kinds = {c.name: c.kind for c in conformance.checks("claude")}
        doc = {"runtime": "claude", "version": OLD, "current": NEW, "code": conformance.code_digest("claude"),
               "retried": session_versions._today(), "at": "x",
               "checks": {n: {"port": "p", "kind": kinds[n], "state": "pass", "reason": ""} for n in every}}
        doc.update(override)
        f = runtime_ports.session_result_file("claude", OLD)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(doc))
        return doc

    def test_a_check_added_since_runs_and_one_checkable_now_runs_once_a_day(self):
        doc = self.full_result()
        self.assertIsNone(session_versions.what_to_run("claude", OLD, NEW))
        del doc["checks"]["sessions-fork"]
        self.full_result(checks=doc["checks"])
        self.assertEqual(session_versions.what_to_run("claude", OLD, NEW), ["sessions-fork"])
        doc = self.full_result()
        doc["checks"]["wake"]["state"] = "not-checkable"
        self.full_result(checks=doc["checks"], retried="2000-01-01")
        self.assertIsNone(session_versions.what_to_run("claude", OLD, NEW), "still no way to run it")
        built = [c._replace(run=lambda w: None) if c.name == "wake" else c for c in conformance.checks("claude")]
        with mock.patch.object(conformance, "checks", lambda runtime: built):
            self.assertEqual(session_versions.what_to_run("claude", OLD, NEW), ["wake"])

    def test_a_change_to_what_the_safety_checks_rest_on_runs_them_again(self):
        self.full_result(code="older")
        safety = sorted(c.name for c in conformance.checks("claude")
                        if c.side == "session" and c.kind == "safety")
        self.assertEqual(sorted(session_versions.what_to_run("claude", OLD, NEW)), safety)
        self.live_session("S1", OLD)
        runtime_store.install("claude", OLD, self.dist, kind=runtime_store.SESSION)
        self.run_job()
        self.assertEqual(runtime_ports.session_result("claude", OLD)["code"], conformance.code_digest("claude"))

    def test_a_recorded_fail_runs_again_only_when_asked(self):
        doc = self.full_result()
        doc["checks"]["control"]["state"] = "fail"
        self.full_result(checks=doc["checks"])
        self.assertIsNone(session_versions.what_to_run("claude", OLD, NEW))
        import runtimes
        with mock.patch.object(hubconf, "worktree_lookup", lambda: None), mock.patch("sys.stdout", new=__import__("io").StringIO()), mock.patch("sys.stderr", new=__import__("io").StringIO()):
            self.assertEqual(runtimes.main(["recheck", "claude", OLD]), 0)
            self.assertEqual(runtimes.main(["recheck", "claude", "2.1.299"]), 1)
        self.assertEqual(session_versions.what_to_run("claude", OLD, NEW), conformance.session_checks("claude"))
        self.assertEqual(runtime_ports.failed_checks(
            runtime_ports.session_result("claude", OLD), "safety"), ["control"], "it gates until it has run again")
        self.live_session("S1", OLD)
        runtime_store.install("claude", OLD, self.dist, kind=runtime_store.SESSION)
        self.run_job()
        doc = runtime_ports.session_result("claude", OLD)
        self.assertNotIn("recheck", doc)
        self.assertEqual(runtime_ports.failed_checks(doc), [])
        with mock.patch.object(hubconf, "worktree_lookup", lambda: True):
            self.assertEqual(runtimes.main(["recheck", "claude", OLD]), 1)

    def test_a_copy_that_says_another_version_is_not_recorded(self):
        self.live_session("S1", OLD)
        real = self.fake_run
        with mock.patch.object(conformance, "run", lambda *a, **k: {**real(*a, **k), "version": "2.1.299"}):
            lines = self.run_job()
        self.assertIsNone(runtime_ports.session_result("claude", OLD))
        self.assertTrue(any("2.1.299" in line for line in lines))

    def test_from_a_linked_worktree_it_refuses(self):
        with mock.patch.object(hubconf, "worktree_lookup", lambda: True):
            self.assertEqual(session_versions.main([]), 1)
        self.assertEqual(self.runs, [])

    def test_said_by_version(self):
        self.live_session("S1", OLD, build="ab12")
        self.live_session("S2", runtime_ports.UNREADABLE)
        self.live_session("S3", "2.1.301")
        self.result(version="2.1.301", sessions_fork=("function", "fail"))
        self.dist.published_list = RuntimeError("offline")
        self.run_job()
        rows = {r["version"]: r for r in session_versions.summary("claude")}
        self.assertEqual({v: r["state"] for v, r in rows.items()},
                         {OLD: "pending", runtime_ports.UNREADABLE: "unreadable", "2.1.301": "failed"})
        self.assertEqual(rows[OLD]["builds"], ["ab12"])
        self.assertEqual(rows["2.1.301"]["checks"], ["sessions-fork"])
        self.assertEqual({row[0] for row in session_versions.doctor_rows("claude")}, {"info"}, "no safety failure")


class StorePrune(unittest.TestCase):
    """runtime_store.prune and the session copies: kept while seen in 7 days, at most 5, never a held one; a promoted
    copy keeps the promoted copies' rule."""

    def setUp(self):
        store_fixture.empty(self)

    def copy(self, version, days_ago):
        runtime_store.install("claude", version, store_fixture.Dist("#!/bin/sh\n"), kind=runtime_store.SESSION)
        when = (datetime.datetime.now().astimezone() - datetime.timedelta(days=days_ago)).isoformat(timespec="seconds")
        self.assertTrue(runtime_store.seen("claude", version, when))

    def test_an_install_is_seen_in_the_same_turn(self):
        now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        runtime_store.install("claude", OLD, store_fixture.Dist("#!/bin/sh\n"), kind=runtime_store.SESSION, seen=now)
        self.assertEqual(runtime_store.installed("claude", OLD)["seen"], now)
        self.assertEqual(runtime_store.prune("claude"), [])

    def test_seen_lately_kept_five_at_most_held_never_removed(self):
        for i in range(7):
            self.copy(f"2.1.{300 + i}", days_ago=i)          # 2.1.300 seen today … 2.1.306 six days ago
        self.copy("2.1.299", days_ago=9)
        with runtime_store.hold_version("claude", "2.1.306"):
            gone = runtime_store.prune("claude")
        self.assertEqual(sorted(gone), ["2.1.299", "2.1.305"])
        self.assertEqual(runtime_store.versions("claude"), [f"2.1.{300 + i}" for i in range(5)] + ["2.1.306"])

    def test_seen_never_moves_back_and_a_promoted_copy_follows_its_own_rule(self):
        self.copy(OLD, days_ago=0)
        self.copy(OLD, days_ago=20)
        self.assertEqual(runtime_store.prune("claude"), [])
        self.copy("2.1.301", days_ago=30)
        runtime_store.promote("claude", "2.1.301")
        self.assertEqual(runtime_store.prune("claude"), [], "current")
        self.assertFalse(runtime_store.seen("claude", NEW, "x"), "not in the store")


class Results(Instance):
    def test_a_session_result_is_written_beside_the_full_one(self):
        with mock.patch.object(conformance, "RESULTS", self.tmp / "state" / "runtime-conformance"):
            doc = {"runtime": "claude", "version": OLD, "binary": "b", "digest": "d", "at": "t", "checks": {}}
            conformance.record(doc)
            f = conformance.record_session(doc, NEW)
            self.assertEqual(f.name, f"{OLD}.session.json")
            self.assertEqual([d["version"] for d in conformance.recorded("claude")], [OLD])
            self.assertNotIn("current", conformance.recorded("claude")[0])
            self.assertEqual(runtime_ports.session_result("claude", OLD)["current"], NEW)

    def test_the_session_side_set(self):
        sides = {c.name: c.side for c in conformance.checks("claude")}
        self.assertEqual(sorted(n for n, s in sides.items() if s == "session"),
                         sorted(["lifecycle-events", "lifecycle-deny", "lifecycle-context", "instructions-reach",
                                 "sessions-read", "sessions-fork", "confine-sandbox", "control", "session-status",
                                 "session-summary", "wake", "worktree-maker"]))
        self.assertTrue(set(sides.values()) <= set(runtime_ports.CHECK_SIDES))


if __name__ == "__main__":
    unittest.main()
