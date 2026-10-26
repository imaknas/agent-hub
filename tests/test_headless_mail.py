"""平台的 headless 模型呼叫(llm.py run/exec)不是信的收件人(10-27 盲審查到的 bug)。

每小時的 login-check、每週的 reflect-week 在 hub 根目錄跑 `claude -p`,繼承了 agent-hub 的 SessionStart hook,
`hubbus hook session-start` 就把 agent-hub 的新信標成「送進這個 session 了」——dispatch 以為有人收了,不再派;
真的 session 也不會再收到(實例:m10250040-50a0、m10242244-e739 都被 login-check 的 session 收走)。
修法:llm.py 給它叫起的程序環境變數 HUB_LLM_POINT=<判斷點>;hubbus 看到就不送信、不記在場(租約例外,照舊)。

    bin/py -m unittest discover -s tests
"""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
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
        m.publish(self.c, "m1", "message", "other", "proj", None, {"type": "request", "text": "請處理"})

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
