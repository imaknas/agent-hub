"""check-claude-login.py: checks of the three logins (a real request + the expiry). Alerts are deduplicated by wording
(alert.once compares the problem list), so a change of wording re-sends them: this compares down to the words.

No real requests (ask is faked), no Discord (alert.once is replaced with a recorder), HOME is a temp directory (real
credentials are never read).

    bin/py -m unittest discover -s tests
"""
import datetime
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))


class Check(unittest.TestCase):
    def setUp(self):
        self.home = pathlib.Path(tempfile.mkdtemp()).resolve()
        (self.home / ".claude").mkdir()
        self.token = self.home / ".claude" / "headless-oauth-token"
        self.creds = self.home / ".claude" / ".credentials.json"
        env = mock.patch.dict(os.environ, {"HOME": str(self.home), "AGENT_HUB_LANG_UI": "zh-Hant"})
        env.start()
        self.addCleanup(env.stop)
        spec = importlib.util.spec_from_file_location("check_login_t", ROOT / "bin" / "check-claude-login.py")
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.m.STREAK = self.home / "streak.json"
        self.alerts = []
        self.m.alert.once = lambda key, problems, ok: self.alerts.append((key, list(problems), ok))
        self.answers = {None: None, "token": None}   # LLM_AUTH → ask's result (None = success)
        self.asked = []

        def ask(env):
            self.asked.append(env.get("LLM_AUTH"))
            assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
            return self.answers[env.get("LLM_AUTH")]
        self.m.ask = ask

    def run_check(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = self.m.main()
        return code, out.getvalue().splitlines(), self.alerts[-1][1]

    def fresh_token(self, days_old=10):
        self.token.write_text("x")
        t = time.time() - days_old * 86400
        os.utime(self.token, (t, t))

    def creds_expiring(self, days):
        exp = (datetime.datetime.now() + datetime.timedelta(days=days, hours=1)).timestamp() * 1000
        self.creds.write_text(json.dumps({"claudeAiOauth": {"refreshTokenExpiresAt": exp}}))

    def test_all_good(self):
        self.fresh_token()
        self.creds_expiring(30)
        code, out, hard = self.run_check()
        self.assertEqual((code, hard), (0, []))
        self.assertEqual(out, ["訂閱登入(launchd):ok", "headless token:ok", ".credentials.json(SSH 用):refresh 還有 30 天"])
        self.assertEqual(self.asked, [None, "token"])
        self.assertEqual(self.alerts, [("claude-login", [], "claude 登入檢查恢復正常")])

    def test_subscription_login_broken(self):
        self.fresh_token()
        self.answers[None] = "Login expired · Please run /login"
        code, _, hard = self.run_check()
        self.assertEqual(code, 1)
        self.assertEqual(hard, ["claude 的訂閱登入(Upwork job、Muninn 用的)不能用了:Login expired · Please run /login"
                                "——在 mini 上登入(`claude` → /login)"])

    def test_token_broken(self):
        self.fresh_token()
        self.answers["token"] = "401 unauthorized"
        _, _, hard = self.run_check()
        self.assertEqual(hard, [f"headless token(所有排程跟租借用的)不能用了:401 unauthorized"
                                f"——重跑 `claude setup-token` 寫進 {self.token}"])

    def test_token_missing_is_not_probed(self):
        _, out, hard = self.run_check()
        self.assertEqual(self.asked, [None])
        self.assertEqual(hard, [f"沒有 {self.token}(所有排程跟租借都會失敗)"])

    def test_token_getting_old(self):
        self.fresh_token(days_old=340)
        _, _, hard = self.run_check()
        self.assertEqual(hard, [f"headless token 已經 340 天(一年到期),快要重跑 `claude setup-token` 寫進 {self.token}"])

    def test_ssh_credentials_expiring(self):
        self.fresh_token()
        self.creds_expiring(3)
        _, out, hard = self.run_check()
        self.assertIn(".credentials.json(SSH 用):refresh 還有 3 天", out)
        self.assertEqual(hard, ["~/.claude/.credentials.json(SSH 底下的 claude 用的)登入 3 天內到期——"
                                "在 mini 的終端機跑一次 claude 讓它更新,或重新 /login"])

    def test_credentials_without_expiry(self):
        self.fresh_token()
        self.creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "x"}}))
        _, out, hard = self.run_check()
        self.assertIn(".credentials.json:沒有 refreshTokenExpiresAt,略過到期檢查", out)
        self.assertEqual(hard, [])

    def test_runtime_missing_is_hard(self):
        self.fresh_token()
        self.answers[None] = "找不到 claude"
        _, _, hard = self.run_check()
        self.assertEqual(len(hard), 1)
        self.assertIn("訂閱登入", hard[0])

    def test_runtime_missing_is_hard_for_the_token_probe_too(self):
        # changed on purpose in layer 6: before, only the subscription login treated "claude not found" as needing a person;
        # the token one treated it as an ordinary failure
        self.fresh_token()
        self.answers[None] = self.answers["token"] = "找不到 claude"
        _, _, hard = self.run_check()
        self.assertEqual(len(hard), 2)
        self.assertTrue(hard[1].startswith("headless token(所有排程跟租借用的)不能用了:找不到 claude"))

    def test_non_login_failure_alerts_only_on_third_in_a_row(self):
        self.fresh_token()
        self.answers["token"] = "timeout talking to api"
        for n in (1, 2):
            code, out, hard = self.run_check()
            self.assertEqual((code, hard), (0, []))
            self.assertIn(f"token:請求失敗(不像登入問題,連續第 {n} 次):timeout talking to api", out)
        _, _, hard = self.run_check()
        self.assertEqual(hard, ["claude 請求連續 3 次失敗(token):timeout talking to api"])
        self.answers["token"] = None
        _, _, hard = self.run_check()
        self.assertEqual(hard, [])
        self.assertEqual(json.loads(self.m.STREAK.read_text()), {})


if __name__ == "__main__":
    unittest.main()
