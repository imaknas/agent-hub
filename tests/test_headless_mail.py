"""The platform's headless model calls (llm.py run/exec) are not letter recipients (a bug found by the 10-27 blind
review).

The hourly login-check and the weekly reflect-week run `claude -p` in the hub root and inherited agent-hub's
SessionStart hook, so `hubbus hook session-start` marked agent-hub's new letters "delivered into this session":
dispatch thought someone had taken them and stopped dispatching, and the real session never got them either (cases:
m10250040-50a0 and m10242244-e739 were both taken by the login-check session).
Fix: llm.py gives the processes it starts the env var HUB_LLM_POINT=<judgment point>; when hubbus sees it, it delivers
no letters and records no presence (leases are the exception, as before).

    bin/py -m unittest discover -s tests
"""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


class MailHook(unittest.TestCase):
    def setUp(self):
        m = self.m = load("hubbus_hl", ROOT / "bin" / "hubbus")
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        m.DB = self.tmp / "bus.db"
        m.log = lambda s: None
        m.project_of = lambda cwd: "proj"
        self.c = m.db()
        m.db = lambda *a, **k: self.c
        m.publish(self.c, "m1", "message", "other", "proj", None, {"type": "request", "text": "please handle this"})

    def hook(self, env):
        clean = {k: v for k, v in os.environ.items() if k not in ("HUB_LLM_POINT", "HUBBUS_LEASE")}
        with mock.patch.dict(os.environ, {**clean, **env}, clear=True):
            return self.m.mail_text("session-start", "S1", str(self.tmp))

    def delivered(self):
        return self.c.execute("SELECT COUNT(*) FROM events WHERE type='message.delivered' AND ref='m1'").fetchone()[0]

    def present(self):
        return self.c.execute("SELECT COUNT(*) FROM presence WHERE session='S1'").fetchone()[0]

    def test_headless_platform_call_takes_no_mail(self):
        self.assertIsNone(self.hook({"HUB_LLM_POINT": "login-check"}))
        self.assertEqual((self.delivered(), self.present()), (0, 0))

    def test_a_real_session_still_gets_it(self):
        self.assertIn("m1", self.hook({}) or "")
        self.assertEqual((self.delivered(), self.present()), (1, 1))

    def test_lease_session_still_sees_mail_without_taking_it(self):
        text = self.hook({"HUB_LLM_POINT": "lease-session", "HUBBUS_LEASE": "1"})
        self.assertIn("m1", text or "")
        self.assertEqual((self.delivered(), self.present()), (0, 0))


class LlmMarksItsProcesses(unittest.TestCase):
    def test_task_env_carries_the_point(self):
        import runtime_fake
        runtime_fake.reset()
        self.addCleanup(runtime_fake.reset)
        llm = load("llm_hl", ROOT / "bin" / "llm.py")
        prompt = pathlib.Path(tempfile.mkdtemp()) / "p.txt"
        prompt.write_text("x")
        a = argparse.Namespace(cmd="run", point="login-check", cwd=None, model=None, prompt_file=str(prompt),
                               dry_run=False, extra=[])
        with mock.patch.object(llm.runtime_ports if hasattr(llm, "runtime_ports") else __import__("runtime_ports"),
                               "load", lambda *x, **k: runtime_fake), \
                contextlib.redirect_stdout(io.StringIO()):
            llm.run(a)
        self.assertEqual(runtime_fake._ran[-1].env.get("HUB_LLM_POINT"), "login-check")


if __name__ == "__main__":
    unittest.main()
