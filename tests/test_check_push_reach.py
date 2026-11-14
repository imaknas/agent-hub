"""bin/check-push-reach.py: when the assistant's pushes stop reaching the phone, the platform says so on its own channels.

    bin/py -m unittest tests.test_check_push_reach
"""
import importlib.util
import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))


def load():
    spec = importlib.util.spec_from_file_location("check_push_reach_t", ROOT / "bin" / "check-push-reach.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


OK = {"reaching": True, "unshown_since": None, "subscriptions": 1}
DOWN = {"reaching": False, "unshown_since": "t", "subscriptions": 1}


def http(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://a.example/api/push/health", code, "x", {}, io.BytesIO(b"{}"))


class Check(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "token").write_text("secret-token\n")
        self.alerts = []
        self.requests = []
        conf = {"assistant.api": "https://a.example:8444/anything"}
        for p in (mock.patch.object(self.m, "STREAK", self.tmp / "streak.json"),
                  mock.patch.object(self.m.alert, "_assistant_on", lambda: True),
                  mock.patch.object(self.m.alert, "once", lambda key, problems, ok: self.alerts.append((key, problems, ok))),
                  mock.patch.object(self.m.hubconf, "get", lambda k, d=None: conf.get(k, d)),
                  mock.patch.object(self.m.hubconf, "p", lambda k, d=None: self.tmp / "token"),
                  mock.patch("builtins.print")):
            p.start()
            self.addCleanup(p.stop)

    def run_with(self, answer) -> int:
        def urlopen(req, timeout=None):
            self.requests.append(req)
            if isinstance(answer, Exception):
                raise answer
            return Answer(answer if isinstance(answer, bytes) else json.dumps(answer).encode())
        with mock.patch.object(self.m.urllib.request, "urlopen", urlopen):
            return self.m.main()

    def test_reaching_is_quiet(self):
        self.assertEqual(self.run_with(OK), 0)
        self.assertEqual(self.alerts[-1][:2], ("push-reach", []))

    def test_it_asks_the_assistant_with_the_ingest_token(self):
        self.run_with(OK)
        self.assertEqual(self.requests[0].full_url, "https://a.example:8444/api/push/health")
        self.assertEqual(self.requests[0].get_header("Authorization"), "Bearer secret-token")

    def test_not_reaching_is_a_problem_at_once_and_names_since_when(self):
        self.assertEqual(self.run_with({**DOWN, "unshown_since": "2026-11-07T10:00:00+08:00"}), 1)
        self.assertEqual(len(self.alerts[-1][1]), 1)
        self.assertIn("2026-11-07T10:00:00+08:00", self.alerts[-1][1][0])

    def test_no_subscription_is_a_problem_though_reaching_is_true(self):
        """nothing is being sent, so reaching stays true, and nothing can arrive"""
        self.assertEqual(self.run_with({**OK, "subscriptions": 0}), 1)
        self.assertEqual(len(self.alerts[-1][1]), 1)

    def test_not_reaching_and_no_subscription_are_two_problems(self):
        self.run_with({**DOWN, "subscriptions": 0})
        self.assertEqual(len(self.alerts[-1][1]), 2)

    def test_the_same_problem_keeps_the_same_text(self):
        """a change of text is a new alert: nothing in it may change from hour to hour"""
        self.run_with(DOWN)
        self.run_with({**DOWN, "subscriptions": 2})
        self.assertEqual(self.alerts[0][1], self.alerts[1][1])
        for _ in range(4):
            self.run_with(TimeoutError())
        self.assertEqual(self.alerts[-1][1], self.alerts[-2][1])

    def test_recovery_clears_it(self):
        self.run_with(DOWN)
        self.assertEqual(self.run_with(OK), 0)
        self.assertEqual(self.alerts[-1][1], [])

    def test_no_answer_waits_three_hours(self):
        for code in (0, 0):
            self.assertEqual(self.run_with(TimeoutError()), code)
            self.assertEqual(self.alerts[-1][1], [])
        self.assertEqual(self.run_with(TimeoutError()), 1)
        self.assertEqual(len(self.alerts[-1][1]), 1)

    def test_a_server_error_is_no_answer(self):
        self.assertEqual(self.run_with(http(503)), 0)
        self.assertEqual(self.alerts[-1][1], [])

    def test_an_answer_between_resets_the_count(self):
        self.run_with(TimeoutError())
        self.run_with(TimeoutError())
        self.run_with(OK)
        self.run_with(TimeoutError())
        self.assertEqual(self.alerts[-1][1], [])

    def test_a_broken_contract_is_a_problem_at_once(self):
        for answer in (http(401), http(404), b"<html>", {"subscriptions": 1}, {"reaching": "yes", "subscriptions": 1},
                       {"reaching": True}, {"reaching": True, "subscriptions": True}, [1]):
            self.alerts.clear()
            self.assertEqual(self.run_with(answer), 1, answer)
            self.assertEqual(len(self.alerts[-1][1]), 1, answer)

    def test_a_missing_token_is_a_problem_and_no_request_goes_out(self):
        (self.tmp / "token").unlink()
        self.assertEqual(self.run_with(OK), 1)
        self.assertEqual(self.requests, [])

    def test_the_token_never_appears_in_a_problem(self):
        for answer in (http(401), TimeoutError(), DOWN):
            self.run_with(answer)
        self.assertNotIn("secret-token", json.dumps(self.alerts))

    def test_channel_off_checks_nothing_and_clears_nothing(self):
        with mock.patch.object(self.m.alert, "_assistant_on", lambda: False):
            self.assertEqual(self.run_with(OK), 0)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.alerts, [], "nothing was checked, so nothing recovered either")


if __name__ == "__main__":
    unittest.main()
