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


class Doctor(unittest.TestCase):
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
