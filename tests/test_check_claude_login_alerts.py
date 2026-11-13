"""check-claude-login.py, what it alerts about and how often (the 11-13 letter from the assistant's project):

  - the subscription login is checked only while some registry job (or enabled module's job) declares
    connectors: true; with none it is not checked and says why, with no registry index it is checked anyway
  - one problem is one alert: alert.once tells the problems apart by a stable id, so the same broken login with its error
    tail shifted by a character (`exit 1:` / `exit 1::`, as on 11-13) or a failure count that went up is not re-sent
  - the subscription login's "used by" names the connector jobs, never the assistant

No real requests (ask is faked), alert.send replaced with a recorder, HOME and the alert state in a temp directory.

    bin/py -m unittest tests.test_check_claude_login_alerts
"""
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

# the two error texts the 11-13 alerts alternated between (the tail of claude's output, cut at a fixed length)
TAIL = ("this login reached its time limit and cannot be renewed. Run `claude auth login` to sign in again, or use "
        "`claude setup-token` and set CLAUDE_CODE_OAUTH_TOKEN for unattended jobs")
SHIFTED_A = f'exit 1: {TAIL}","type":"resu'
SHIFTED_B = f'exit 1:: {TAIL}","type":"res'


def ui(msgid, **kw):
    return i18n.translate("check-claude-login", msgid, kw, "zh-Hant")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class AlertIds(unittest.TestCase):
    """alert.once(..., ids=…): a problem is its id, whatever its text says this time."""

    def setUp(self):
        self.a = load("alert_ids_t", ROOT / "bin" / "alert.py")
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        for name in ("SENT", "FAILED"):
            p = mock.patch.object(self.a, name, self.tmp / f"{name}.json")
            p.start()
            self.addCleanup(p.stop)
        self.sent = []
        self.a.send = lambda text, notice=None: self.sent.append(text) or True

    def test_same_id_new_wording_is_not_sent_again(self):
        self.a.once("k", ["login broken: exit 1: x"], "fine", ids=["subscription:login"])
        self.a.once("k", ["login broken: exit 1:: x"], "fine", ids=["subscription:login"])
        self.a.once("k", ["login broken: exit 1: x"], "fine", ids=["subscription:login"])
        self.assertEqual(len(self.sent), 1)

    def test_a_new_id_is_sent_at_once(self):
        self.a.once("k", ["a"], "fine", ids=["one"])
        self.a.once("k", ["a", "b"], "fine", ids=["one", "two"])
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(self.sent[-1].startswith("⚠️ "))

    def test_a_record_from_before_ids_compares_once_then_carries_them(self):
        (self.tmp / "SENT.json").write_text(json.dumps({"k": {"problems": ["old wording"], "at": "2099-01-01T00:00:00"}}))
        self.a.once("k", ["new wording"], "fine", ids=["one"])
        self.a.once("k", ["newer wording"], "fine", ids=["one"])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(json.loads((self.tmp / "SENT.json").read_text())["k"]["ids"], ["one"])

    def test_without_ids_wording_still_decides(self):
        self.a.once("k", ["a"], "fine")
        self.a.once("k", ["a "], "fine")
        self.assertEqual(len(self.sent), 2)

    def test_ids_must_match_the_problems(self):
        with self.assertRaises(ValueError):
            self.a.once("k", ["a", "b"], "fine", ids=["one"])


class Check(unittest.TestCase):
    def setUp(self):
        self.home = pathlib.Path(tempfile.mkdtemp()).resolve()
        (self.home / ".claude").mkdir()
        token = self.home / ".claude" / "headless-oauth-token"
        token.write_text("x")
        t = time.time() - 10 * 86400
        os.utime(token, (t, t))
        env = mock.patch.dict(os.environ, {"HOME": str(self.home), "AGENT_HUB_LANG_UI": "zh-Hant"})
        env.start()
        self.addCleanup(env.stop)
        self.m = load("check_login_alerts_t", ROOT / "bin" / "check-claude-login.py")
        self.m.STREAK = self.home / "streak.json"
        for name in ("SENT", "FAILED"):
            p = mock.patch.object(self.m.alert, name, self.home / f"{name}.json")
            p.start()
            self.addCleanup(p.stop)
        self.sent = []
        p = mock.patch.object(self.m.alert, "send", lambda text, notice=None: self.sent.append(text) or True)
        p.start()
        self.addCleanup(p.stop)
        self.jobs = ["proj/job"]
        self.m.connector_jobs = lambda: self.jobs
        self.answers = {None: None, "token": None}
        self.asked = []

        def ask(env):
            self.asked.append(env.get("LLM_AUTH"))
            return self.answers[env.get("LLM_AUTH")]
        self.m.ask = ask

    def run_check(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = self.m.main()
        return code, out.getvalue().splitlines()

    def test_no_connector_job_means_the_subscription_login_is_not_checked(self):
        self.jobs = []
        self.answers[None] = "Login expired · Please run /login"   # broken, but nothing uses it
        code, out = self.run_check()
        self.assertEqual((code, self.asked, self.sent), (0, ["token"], []))
        sub = self.m.runtime_ports.load(ports=("auth",)).credentials()[0].name
        self.assertIn(ui("{name}: not checked: no registry job declares connectors: true, and everything else the "
                         "platform runs uses the headless token", name=sub), out)

    def test_no_registry_index_checks_it_anyway(self):
        self.jobs = None
        code, out = self.run_check()
        self.assertEqual((code, self.asked), (0, [None, "token"]))
        sub = self.m.runtime_ports.load(ports=("auth",)).credentials()[0].name
        self.assertIn(ui("{name}: the registry index has not been produced yet, so which jobs declare connectors: true "
                         "is not known; checked in case one does", name=sub), out)

    def test_the_jobs_that_use_it_are_named_and_the_assistant_is_not(self):
        self.answers[None] = "Login expired · Please run /login"
        self.run_check()
        self.assertEqual(len(self.sent), 1)
        self.assertIn(ui("used by the jobs with connectors: true: {jobs}", jobs="proj/job"), self.sent[0])
        catalog = (ROOT / "locales" / "zh-Hant" / "check-claude-login.json").read_text()
        self.assertNotIn("{assistant}", catalog)

    def test_the_11_13_alternating_error_alerts_once(self):
        for err in (SHIFTED_A, SHIFTED_B, SHIFTED_B, SHIFTED_A, SHIFTED_B):
            self.answers[None] = err
            code, _ = self.run_check()
            self.assertEqual(code, 1)
        self.assertEqual(len(self.sent), 1, self.sent)

    def test_a_rising_failure_count_alerts_once(self):
        self.answers["token"] = "timeout talking to api"
        for _ in range(5):
            self.run_check()
        self.assertEqual(len(self.sent), 1, self.sent)
        self.assertIn(ui("claude requests failed {n} times in a row ({mode}): {err}", n=3, mode="token",
                         err="timeout talking to api"), self.sent[0])

    def test_when_the_last_connector_job_goes_the_open_alert_is_resolved(self):
        self.answers[None] = "Login expired · Please run /login"
        self.run_check()
        self.jobs = []
        code, _ = self.run_check()
        self.assertEqual(code, 0)
        self.assertEqual(self.sent[-1], "✅ " + ui("The claude login check is back to normal"))


class CleanError(unittest.TestCase):
    def setUp(self):
        self.m = load("check_login_clean_t", ROOT / "bin" / "check-claude-login.py")

    def test_the_11_13_pair_reads_the_same(self):
        self.assertEqual(self.m.clean_error(SHIFTED_A), f"exit 1: {TAIL}")
        self.assertEqual(self.m.clean_error(SHIFTED_B), f"exit 1: {TAIL}")

    def test_json_before_the_message_is_dropped(self):
        self.assertEqual(self.m.clean_error('exit 1: lt":"OAuth error: expired","type":"resu'),
                         "exit 1: OAuth error: expired")
        self.assertEqual(self.m.clean_error('exit 1: "OAuth error: expired","ty'), "exit 1: OAuth error: expired")

    def test_a_whole_json_error_keeps_its_message(self):
        err = ('exit 1: API Error: 401 {"type":"error","error":{"type":"authentication_error",'
               '"message":"OAuth token has expired"}}')
        self.assertEqual(self.m.clean_error(err), err)

    def test_plain_errors_are_left_alone(self):
        for err in ("Login expired · Please run /login", "401 unauthorized", "timeout talking to api"):
            self.assertEqual(self.m.clean_error(err), err)

    def test_at_most_200_characters(self):
        self.assertEqual(len(self.m.clean_error("exit 1: " + "x" * 500)), 200)


class ConnectorJobs(unittest.TestCase):
    """Which jobs use the subscription login: every project's entry and the enabled modules, as <project>/<job>."""

    def setUp(self):
        self.m = load("check_login_jobs_t", ROOT / "bin" / "check-claude-login.py")
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def jobs(self, projects, modules=None, index=True, text=None):
        idx = self.tmp / "index.json"
        if index:
            idx.write_text(text if text is not None else json.dumps({"projects": projects}))
        import registry
        with mock.patch.object(self.m.hubconf, "REGISTRY_INDEX", idx), \
                mock.patch.object(registry, "load_modules", return_value=(modules or {}, {})):
            return self.m.connector_jobs()

    def test_found_in_projects_and_modules(self):
        agent = {"name": "check", "kind": "agent", "connectors": True, "allow": ["x"]}
        self.assertEqual(self.jobs({"proj": {"jobs": [agent, {"name": "plain", "run": "true"}]},
                                    "other": {"jobs": [{"name": "off", "connectors": False}]}},
                                   {"mod": {"type": "module", "jobs": [{**agent, "name": "m"}]}}),
                         ["mod/m", "proj/check"])

    def test_none_declared(self):
        self.assertEqual(self.jobs({"proj": {"jobs": [{"name": "plain", "run": "true"}]}, "bare": {}}), [])

    def test_no_index_is_not_known(self):
        self.assertIsNone(self.jobs({}, index=False))

    def test_an_unreadable_index_is_not_known_either(self):
        for text in ("{not json", "{}", '{"projects": 3}'):
            self.assertIsNone(self.jobs({}, text=text), text)


if __name__ == "__main__":
    unittest.main()
