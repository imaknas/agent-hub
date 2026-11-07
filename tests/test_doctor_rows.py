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

    def rows(self, handler, docs):
        rows = []
        with mock.patch.object(hubconf, "get", lambda k, d=None: {"handler": handler} if k == "mailbox" else (d if d is not None else {})), \
                mock.patch.object(hubconf, "projects", lambda: docs):
            load_agenthub().doctor_propose(lambda *a: rows.append(a))
        return rows

    def test_named(self):
        got = self.rows("propose", {"box": {"enforce": {"sandbox": True}}, "open": {}})
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0], "warn")
        self.assertIn("(box)", got[0][1], "the sandboxed one, and only it")

    def test_nothing_without_a_sandboxed_project_or_the_handler(self):
        self.assertEqual(self.rows("propose", {"open": {}}), [])
        self.assertEqual(self.rows("headless", {"box": {"enforce": {"sandbox": True}}}), [])


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
