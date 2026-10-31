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
import i18n  # noqa: E402


def ui(msgid, **kw):
    """the zh-Hant text of the check script's message (the assertions compare the zh-Hant wording)"""
    return i18n.translate("check-claude-login", msgid, kw, "zh-Hant")


def rt(msgid, **kw):
    """the zh-Hant text of a runtime_claude message (the credentials' names, labels and how to renew them)"""
    return i18n.translate("runtime_claude", msgid, kw, "zh-Hant")


SUB_NAME, SUB_LABEL = rt("subscription login (launchd)"), rt("claude's subscription login")
SSH_NAME = rt(".credentials.json (for SSH)")
NOT_FOUND = rt("claude not found")   # what llm.py reports in zh-Hant; is_login_error must recognise it


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
        self.assertEqual(out, [ui("{name}: ok", name=SUB_NAME), ui("{name}: ok", name="headless token"),
                               ui("{name}: refresh valid for {days} more {days:day|days}", name=SSH_NAME, days=30)])
        self.assertEqual(self.asked, [None, "token"])
        self.assertEqual(self.alerts, [("claude-login", [], ui("The claude login check is back to normal"))])

    def test_subscription_login_broken(self):
        self.fresh_token()
        self.answers[None] = "Login expired · Please run /login"
        code, _, hard = self.run_check()
        self.assertEqual(code, 1)
        self.assertEqual(hard, [ui("{label} ({used_by}) does not work: {err} — {renew}", label=SUB_LABEL,
                                   used_by=ui("used by the connector jobs and the assistant"),
                                   err="Login expired · Please run /login",
                                   renew=rt("log in on the hub machine (`claude` → /login)"))])

    def test_token_broken(self):
        self.fresh_token()
        self.answers["token"] = "401 unauthorized"
        _, _, hard = self.run_check()
        self.assertEqual(hard, [ui("{label} ({used_by}) does not work: {err} — {renew}", label="headless token",
                                   used_by=ui("used by all scheduled jobs and leases"), err="401 unauthorized",
                                   renew=rt("rerun `claude setup-token` and write it to {path}", path=self.token))])

    def test_token_missing_is_not_probed(self):
        _, out, hard = self.run_check()
        self.assertEqual(self.asked, [None])
        self.assertEqual(hard, [ui("{path} is missing ({means})", path=self.token,
                                   means=ui("all scheduled jobs and leases will fail"))])

    def test_token_getting_old(self):
        self.fresh_token(days_old=340)
        _, _, hard = self.run_check()
        self.assertEqual(hard, [ui("{label} is {days} {days:day|days} old (expires after {lifetime}); soon: {renew}",
                                   label="headless token", days=340, lifetime=rt("one year"),
                                   renew=rt("rerun `claude setup-token` and write it to {path}", path=self.token))])

    def test_ssh_credentials_expiring(self):
        self.fresh_token()
        self.creds_expiring(3)
        _, out, hard = self.run_check()
        self.assertIn(ui("{name}: refresh valid for {days} more {days:day|days}", name=SSH_NAME, days=3), out)
        self.assertEqual(hard, [ui("{label} ({used_by}): the login expires within {days} {days:day|days} — {renew}",
                                   label="~/.claude/.credentials.json", used_by=rt("used by claude under SSH"), days=3,
                                   renew=rt("run claude once in a terminal on the hub machine so it refreshes, "
                                            "or /login again"))])

    def test_credentials_without_expiry(self):
        self.fresh_token()
        self.creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "x"}}))
        _, out, hard = self.run_check()
        self.assertIn(rt(".credentials.json: no refreshTokenExpiresAt, expiry check skipped"), out)
        self.assertEqual(hard, [])

    def test_runtime_missing_is_hard(self):
        self.fresh_token()
        self.answers[None] = NOT_FOUND
        _, _, hard = self.run_check()
        self.assertEqual(len(hard), 1)
        self.assertIn(SUB_LABEL, hard[0])

    def test_runtime_missing_is_hard_for_the_token_probe_too(self):
        # changed on purpose in layer 6: before, only the subscription login treated "claude not found" as needing a person;
        # the token one treated it as an ordinary failure
        self.fresh_token()
        self.answers[None] = self.answers["token"] = NOT_FOUND
        _, _, hard = self.run_check()
        self.assertEqual(len(hard), 2)
        self.assertTrue(hard[1].startswith(ui("{label} ({used_by}) does not work: {err} — {renew}", label="headless token",
                                              used_by=ui("used by all scheduled jobs and leases"), err=NOT_FOUND,
                                              renew="")))

    def test_non_login_failure_alerts_only_on_third_in_a_row(self):
        self.fresh_token()
        self.answers["token"] = "timeout talking to api"
        for n in (1, 2):
            code, out, hard = self.run_check()
            self.assertEqual((code, hard), (0, []))
            self.assertIn(ui("{mode}: request failed (does not look like a login problem; {n} {n:time|times} in a row): "
                             "{err}", mode="token", n=n, err="timeout talking to api"), out)
        _, _, hard = self.run_check()
        self.assertEqual(hard, [ui("claude requests failed {n} times in a row ({mode}): {err}", n=3, mode="token",
                                   err="timeout talking to api")])
        self.answers["token"] = None
        _, _, hard = self.run_check()
        self.assertEqual(hard, [])
        self.assertEqual(json.loads(self.m.STREAK.read_text()), {})


if __name__ == "__main__":
    unittest.main()
