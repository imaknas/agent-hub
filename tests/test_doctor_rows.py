"""Two rows of `agenthub doctor` (bin/agenthub cmd_doctor) the goldens don't reach:

- the isolation row (docs/design/capabilities.md, 10-31 Linux): sandboxed projects' own services and jobs; red only
  when a registered project is sandboxed (and not exempt) and nothing here can isolate it, a reminder otherwise;
- the agent logins: the default runtime's, then each runtime a judgment point is pinned to (hub.json
  llm.points.<point>.runtime), each once.

cmd_doctor runs in-process with the machine replaced: no subprocess (setup-python and config-check are answered, any
other command fails the test), no service manager (so nothing asks launchd or systemd), the release check, PATH lookup,
appliers and sandbox adapter stubbed, hub.json and the registry given. Texts are compared through the catalog in this
process's language.

    bin/py -m unittest tests.test_doctor_rows
"""
import importlib.machinery
import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import i18n  # noqa: E402
import runtime_fake  # noqa: E402

# the assertions read the English texts: pin the UI language (an instance's hub.json, or none at all, may say another)
_LANG = mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "en", "AGENT_HUB_LANG_CONTENT": "en"})


def setUpModule():
    _LANG.start()


def tearDownModule():
    _LANG.stop()


def load_agenthub():
    loader = importlib.machinery.SourceFileLoader("agenthub_doctor_rows", str(BIN / "agenthub"))
    m = importlib.util.module_from_spec(importlib.util.spec_from_loader("agenthub_doctor_rows", loader))
    loader.exec_module(m)
    return m


def T(msgid: str, **kw) -> str:
    return i18n.translate("agenthub", msgid, kw)


class ProposeOff(unittest.TestCase):
    """mailbox.handler: propose, with sandboxed projects: the doctor says which are off, and why."""

    def rows(self, handler, docs, caps=frozenset({"confine"})):
        rows = []
        with mock.patch.object(hubconf, "get", lambda k, d=None: {"handler": handler} if k == "mailbox" else (d if d is not None else {})), \
                mock.patch.object(hubconf, "projects", lambda: docs), mock.patch("runtime_ports.capabilities", lambda r=None: caps):
            load_agenthub().doctor_propose(lambda *a: rows.append(a))
        return rows

    def test_a_runtime_that_cant_confine_is_said(self):
        got = self.rows("propose", {"open": {}}, caps=set())
        self.assertEqual(len(got), 1)
        self.assertIn("settings", got[0][1])
        self.assertEqual(self.rows("headless", {"open": {}}, caps=set()), [])

    def test_named(self):
        got = self.rows("propose", {"box": {"enforce": {"sandbox": True}}, "open": {}})
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0], "warn")
        self.assertIn("(box)", got[0][1], "the sandboxed one, and only it")

    def test_nothing_without_a_sandboxed_project_or_the_handler(self):
        self.assertEqual(self.rows("propose", {"open": {}}), [])
        self.assertEqual(self.rows("headless", {"box": {"enforce": {"sandbox": True}}}), [])


class ThrowawayRuntimes(unittest.TestCase):
    """A runtime that runs only in the throwaway copy (runtime_command since docs/design/runtime-codex.md step 0): the
    owner is told judgment points are off on it, and why; red-ish (warn) only as the default."""

    def rows(self, conf, default="none"):
        rows = []
        with mock.patch.object(hubconf, "get", lambda k, d=None: conf.get(k, d)):
            load_agenthub().doctor_throwaway_runtimes(lambda *a: rows.append(a), default)
        return rows

    def test_the_default_is_a_warning_with_what_to_do(self):
        [row] = self.rows({"runtime": {"default": "command", "command": {"run": ["x"]}}}, default="command")
        self.assertEqual(row[0], "warn")
        self.assertIn("mailbox-propose", row[1])
        self.assertIn("a command template can't carry a judgment point's limits", row[1], "why, in the runtime's words")
        self.assertIn("mailbox.handler: propose", row[2])

    def test_set_up_but_not_the_default_is_said(self):
        [row] = self.rows({"runtime": {"default": "none", "command": {"run": ["x"]}}})
        self.assertEqual(row[0], "info")
        self.assertIn("brunna llm setup", row[2], "not pinned yet: what to do")
        pinned = {"runtime": {"default": "claude", "command": {"run": ["x"]}}, "mailbox": {"handler": "propose"},
                  "llm": {"points": {"mailbox-propose": {"runtime": "command"}}}}
        row, residual = self.rows(pinned, default="claude")
        self.assertEqual((row[0], row[2]), ("info", ""), "pinned where it runs: nothing to do")
        # what the copy doesn't hold, said as the accepted residual
        self.assertEqual(residual[0], "info")
        for said in ("mailbox-propose", "shell", "anyone's", "layer 2"):
            self.assertIn(said, residual[1])

    def test_nothing_when_no_such_runtime_is_named(self):
        self.assertEqual(self.rows({"runtime": {"default": "claude"}}, default="claude"), [])


class MemoryBranches(unittest.TestCase):
    """A memory branch already holding more than notes; and what the check leaves on a runtime that can't be told."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                    "GIT_CONFIG_GLOBAL": "/dev/null"}

    def bare(self, name, *files):
        bare = self.tmp / f"{name}.git"
        seed = self.tmp / f"seed-{name}"
        for args in (["init", "-q", "--bare", "-b", "agent-memory", str(bare)], ["init", "-q", "-b", "agent-memory", str(seed)]):
            subprocess.run(["git", *args], check=True, capture_output=True, env=self.env)
        for f in files:
            (seed / f).parent.mkdir(parents=True, exist_ok=True)
            (seed / f).write_text("x\n")
        for args in (["add", "-A"], ["commit", "-q", "-m", "m"], ["push", "-q", str(bare), "agent-memory"]):
            subprocess.run(["git", "-C", str(seed), *args], check=True, capture_output=True, env=self.env)
        return {"paths": {"memory_bare": str(bare)}}

    def rows(self, docs, default="claude", caps=frozenset({"confine"})):
        rows = []
        with mock.patch.object(hubconf, "get", lambda k, d=None: d if d is not None else {}), mock.patch.object(hubconf, "projects", lambda: docs), \
                mock.patch("runtime_ports.default_name", lambda: default), mock.patch("runtime_ports.capabilities", lambda r=None: caps), \
                mock.patch("runtime_ports.throwaway_only", lambda r=None: False):
            load_agenthub().doctor_memory_branches(lambda *a: rows.append(a))
        return rows

    def test_a_planted_branch_is_said_and_a_clean_or_absent_one_is_not(self):
        docs = {"clean": self.bare("clean", "memory/.gitkeep", "STATE.md", "AGENT.md", "RULES"), "no-bare": {"paths": {"memory_bare": str(self.tmp / "none.git")}},
                "planted": self.bare("planted", "memory/.gitkeep", ".claude/settings.json")}
        got = self.rows(docs)
        self.assertEqual(len(got), 1, got)
        self.assertEqual(got[0][0], "warn")
        self.assertIn("planted", got[0][1])
        self.assertIn(".claude/settings.json", got[0][1])

    def test_a_runtime_that_cant_be_told_says_what_is_left(self):
        got = self.rows({}, default="command", caps=frozenset())
        self.assertEqual(len(got), 1, got)
        self.assertEqual(got[0][0], "info")
        self.assertIn("librarian-state", got[0][1])
        self.assertEqual(self.rows({}), [], "one that can")


class Doctor(unittest.TestCase):
    PORTS = "the runtime {runtime} has no {missing}: routing a letter takes its sessions as idle, and reads nothing they said"

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for d in ("config", "secrets", "state", "home"):
            (self.tmp / d).mkdir()
        (self.tmp / "config" / "hub.json").write_text("{}")   # only its existence is read; the content is self.cfg
        self.ah = load_agenthub()
        runtime_fake.reset()
        self.addCleanup(runtime_fake.reset)
        self.cfg = {"runtime": {"default": "fake"}}
        self.projects = {}
        self.unexpected = []

    def run_(self, cmd, *a, **k):
        c = [str(x) for x in cmd]
        if c[0].endswith("setup-python") or any(x.endswith("config-check") for x in c):
            return subprocess.CompletedProcess(c, 0, "ok\n", "")
        if any(x.endswith("hubbus") for x in c) and "wake-misses" in c:   # the bus: none of these tests' business
            return subprocess.CompletedProcess(c, 0, "[]", "")
        self.unexpected.append(c)
        return subprocess.CompletedProcess(c, 1, "", "not in this test\n")

    def doctor(self, no_sandbox=None) -> list[tuple[str, str, str]]:
        rows = []
        import appliers
        import isolation
        import upgrade
        patches = [
            mock.patch.dict(os.environ, {"AGENT_HUB_CONFIG": str(self.tmp / "config"),
                                         "AGENT_HUB_SECRETS": str(self.tmp / "secrets"), "HOME": str(self.tmp / "home")}),
            mock.patch.object(hubconf, "_cfg", self.cfg),
            mock.patch.object(hubconf, "STATE", self.tmp / "state"),
            mock.patch.object(hubconf, "service_manager", lambda: None),
            mock.patch.object(hubconf, "projects", lambda: self.projects),
            mock.patch.object(subprocess, "run", self.run_),
            mock.patch.object(shutil, "which", lambda *a, **k: None),
            mock.patch.object(upgrade, "doctor_lines", lambda: []),
            mock.patch.object(appliers, "plan_all", lambda only=None: {}),
            mock.patch.object(isolation, "can_sandbox", lambda: no_sandbox),
            mock.patch.object(isolation, "adapter", lambda: ("seatbelt", None)),
            mock.patch.object(isolation, "guarantees", lambda: list(isolation.REQUIRED)),
            mock.patch.object(self.ah, "report", lambda results: rows.extend(results) or 0),
            mock.patch.object(self.ah, "machine_sleeps", lambda: None),   # pmset / batteries: the machine's, not here
        ]
        for p in patches:
            p.start()
        try:
            self.ah.cmd_doctor([])
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertEqual(self.unexpected, [], "doctor started a command this test does not answer")
        return rows

    # ---------- the session ports a runtime lacks ----------

    def port_rows(self, rows):
        tail = T(self.PORTS, runtime="\0", missing="\0").split("\0")[-1]
        return [r for r in rows if r[1].endswith(tail)]

    def test_a_runtime_with_them_has_no_row(self):
        self.assertEqual(self.port_rows(self.doctor()), [])

    def test_a_runtime_without_them_has_an_info_row_saying_which(self):
        self.cfg["runtime"] = {"default": "acp"}
        [(level, what, fix)] = self.port_rows(self.doctor())
        self.assertEqual((level, fix), ("info", ""))
        import runtime_ports
        for port in ("session_status", "session_summary"):
            self.assertIn(runtime_ports.PORT_LABELS[port], what)

    def test_each_installed_runtime_says_for_itself(self):
        self.cfg["runtime"] = {"default": "fake", "installed": ["fake", "acp"]}
        [row] = self.port_rows(self.doctor())
        self.assertIn("acp", row[1])

    def test_no_coding_agent_stays_one_plain_row(self):
        self.cfg["runtime"] = {"default": "none"}
        self.assertEqual(self.port_rows(self.doctor()), [])

    def test_a_runtime_that_doesnt_say_what_it_runs_in_a_folder_says_so(self):
        tail = T("the runtime {runtime} runs in a project's folder, and whether it runs git of its own there, outside any sandbox, "
                 "isn't known: a sandboxed project's repo config could then run as you", runtime="\0").split("\0")[-1]
        self.assertEqual([r for r in self.doctor() if r[1].endswith(tail)], [], "the fake says it runs nothing there")
        self.cfg["runtime"] = {"default": "acp"}
        [row] = [r for r in self.doctor() if r[1].endswith(tail)]
        self.assertEqual(row[0], "info")
        self.assertIn("acp", row[1])

    # ---------- the control rows (docs/design/runtime-versions.md section 7) ----------

    def control_rows(self, adapter, no_sandbox=None):
        import isolation
        import policy
        import runtime_claude
        ws = self.tmp / "ws"
        folder, _writable = runtime_claude.control_root()
        (ws / folder / "agent-memory").mkdir(parents=True)
        (ws / folder / "odd[1]").write_text("x")
        rows = []
        with mock.patch.object(hubconf, "projects", lambda: {"boxed": {"name": "boxed", "enforce": {"sandbox": True},
                                                                       "paths": {"workspace": str(ws)}},
                                                             "open": {"name": "open", "paths": {"workspace": str(ws)}}}), \
                mock.patch.object(isolation, "adapter", lambda: (adapter, None)), \
                mock.patch.object(policy, "protect_roots", lambda: [{"name": folder, "writable": ["worktrees"]}]):
            self.ah.doctor_control(lambda level, what, fix="": rows.append((level, what)), no_sandbox, ["claude", "fake"])
        return rows

    def test_macos_says_what_the_services_rule_stops_and_each_projects_entries(self):
        # the services rule, then the sandboxed project's present entries and its name no rule holds (once, not for
        # the unsandboxed project, nor for a runtime without the notes)
        self.assertEqual([lv for lv, _w in self.control_rows("seatbelt")], ["info", "info", "warn"])

    def test_linux_says_landlock_enforces_none_of_it(self):
        rows = self.control_rows("landlock")
        self.assertEqual([lv for lv, _w in rows], ["info", "info", "warn"])
        shutil.rmtree(self.tmp / "ws")
        self.assertNotEqual(rows[0][1], self.control_rows("seatbelt")[0][1], "each system says its own")

    def test_no_sandbox_here_leaves_only_the_sessions_rows(self):
        self.assertEqual([lv for lv, _w in self.control_rows("seatbelt", no_sandbox="none here")], ["info", "warn"])

    # ---------- the isolation row ----------

    def isolation_row(self, rows):
        head = T("sandboxed projects' own services and jobs: run isolated ({adapter})", adapter="\0").split("\0")[0]
        off = T("sandboxed projects' own services and jobs: off, they are not run ({reason})", reason="\0").split("\0")[0]
        (row,) = [r for r in rows if r[1].startswith((head, off))]
        return row

    def test_isolation_ok_names_the_adapter_and_its_guarantees(self):
        level, what, fix = self.isolation_row(self.doctor())
        self.assertEqual((level, fix), ("ok", ""))
        self.assertEqual(what, T("sandboxed projects' own services and jobs: run isolated ({adapter})",
                                 adapter="seatbelt: own-writes-only, isolated-reads, declared-port-only"))

    def test_isolation_off_with_nothing_sandboxed_is_a_reminder(self):
        self.projects = {"open": {"name": "open"}}
        level, what, fix = self.isolation_row(self.doctor(no_sandbox="no adapter here"))
        self.assertEqual(level, "warn")
        self.assertEqual(what, T("sandboxed projects' own services and jobs: off, they are not run ({reason})",
                                 reason="no adapter here"))
        self.assertTrue(fix)

    def test_isolation_off_with_a_sandboxed_project_is_red_and_names_it(self):
        self.projects = {"boxed": {"name": "boxed", "enforce": {"sandbox": True}}, "open": {"name": "open"},
                         "held": {"name": "held", "enforce": {"sandbox": True}}}
        self.cfg["service_sandbox_exempt"] = ["held"]   # held back by the owner: not run isolated, not counted
        level, what, _fix = self.isolation_row(self.doctor(no_sandbox="no adapter here"))
        self.assertEqual(level, "bad")
        self.assertEqual(what, T("sandboxed projects' own services and jobs: off, they are not run ({reason})",
                                 reason="no adapter here") + T("; affects: {projects}", projects="boxed"))

    KEYCHAIN = ("sandboxed projects' sessions: denied what their services are, except the Keychain, which isn't a file: "
                "the coding agent's own sandbox can't block it, and denying the security command is only reinforcement")

    def test_the_sessions_keychain_residual_is_said_when_a_project_is_sandboxed(self):
        self.projects = {"open": {"name": "open"}}
        self.assertFalse([r for r in self.doctor() if r[1] == T(self.KEYCHAIN)])
        self.projects["boxed"] = {"name": "boxed", "enforce": {"sandbox": True}}
        self.assertEqual([r for r in self.doctor() if r[1] == T(self.KEYCHAIN)], [("info", T(self.KEYCHAIN), "")])

    LEFT = "platform state still in .state after its move to {shared}: {paths}"

    def test_a_copy_left_in_state_after_the_move_is_red(self):
        head = T(self.LEFT, shared="\0", paths="\0").split("\0")[0]
        self.assertFalse([r for r in self.doctor() if r[1].startswith(head)])
        left = self.tmp / "state" / "project-renames.json"
        with mock.patch.object(hubconf, "shared_leftovers", lambda: [left]):
            [row] = [r for r in self.doctor() if r[1].startswith(head)]
        self.assertEqual(row[0], "bad")
        self.assertIn(str(left), row[1])
        self.assertTrue(row[2])

    # ---------- agent logins: the default runtime, then each pinned one ----------

    def login_rows(self, rows):
        marks = [T("agent login ({label}): present", label="\0").split("\0")[0],
                 T("cannot read the agent's login state: {error}", error="\0").split("\0")[0]]
        return [(level, what) for level, what, _fix in rows if what.startswith(tuple(marks))]

    def test_only_the_default_runtime_without_pins(self):
        runtime_fake._creds["subscription"] = False
        got = self.login_rows(self.doctor())
        labels = {c.mode: c.label for c in runtime_fake.credentials()}
        self.assertEqual(got, [("warn", T("agent login ({label}): missing", label=labels["subscription"])),
                               ("ok", T("agent login ({label}): present", label=labels["token"]))])

    def test_pinned_runtimes_follow_the_default_once_each_in_name_order(self):
        """fake is the default and pinned too (said once); acp's key file is missing (a token: red); none has no
        login to check (skipped); a name no runtime has is reported, not raised"""
        self.cfg["runtime"]["acp"] = {"agent": "x", "agents": {"x": {"command": ["x"],
                                                                     "credential": {"env": "K", "file": "acp-key"}}}}
        self.cfg["llm"] = {"points": {"mailbox-propose": {"runtime": "acp"}, "librarian-state": {"runtime": "fake"},
                                      "consolidate": {"runtime": "none"}, "reflect-week": {"runtime": "nosuch"},
                                      "lease-session": "not a pin"}}
        with mock.patch.object(hubconf, "_cfg", self.cfg):
            import runtime_acp
            acp_label = runtime_acp.credentials()[0].label
        fake_token = [c.label for c in runtime_fake.credentials() if c.mode == "token"][0]
        got = self.login_rows(self.doctor())
        self.assertEqual(got[0], ("ok", T("agent login ({label}): present", label=fake_token)), "the default first")
        self.assertEqual(got[1], ("bad", T("agent login ({label}): missing", label=acp_label)))
        self.assertEqual(got[2][0], "bad")
        self.assertTrue(got[2][1].startswith(T("cannot read the agent's login state: {error}", error="")), got[2])
        self.assertIn("nosuch", got[2][1])
        self.assertEqual(len(got), 3, got)

    def test_a_key_file_that_is_there_is_present(self):
        self.cfg["runtime"]["acp"] = {"agent": "x", "agents": {"x": {"command": ["x"],
                                                                     "credential": {"env": "K", "file": "acp-key"}}}}
        self.cfg["llm"] = {"points": {"mailbox-propose": {"runtime": "acp"}}}
        (self.tmp / "secrets" / "acp-key").write_text("k\n")
        got = self.login_rows(self.doctor())
        self.assertEqual([level for level, _w in got], ["ok", "ok"], got)


if __name__ == "__main__":
    unittest.main()
