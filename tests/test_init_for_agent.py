"""init driven by an agent (docs/design/newcomer-path.md B3): --questions lists what to ask without writing anything,
--answers applies the chat answers, and a consent or a secret never comes from the answers file: only from the owner's
own dialog or page, else it is handed back as needs_user. No real dialog or browser is ever opened here.

    bin/py -m unittest tests.test_init_for_agent
"""
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))
import init_dialogue  # noqa: E402
import test_new_user  # noqa: E402

loader = importlib.machinery.SourceFileLoader("agenthub_agent_init", str(ROOT / "bin" / "agenthub"))
spec = importlib.util.spec_from_loader("agenthub_agent_init", loader)
ah = importlib.util.module_from_spec(spec)
loader.exec_module(ah)


class ForAgent(unittest.TestCase):
    NewUser = test_new_user.NewUser
    setUp, tearDown, hub_cmd = NewUser.setUp, NewUser.tearDown, NewUser.hub_cmd

    def answers(self, **a) -> str:
        f = self.tmp / "answers.json"
        f.write_text(json.dumps(a))
        return str(f)

    def test_questions_write_nothing(self):
        r = self.hub_cmd("init", "--questions", "--answers", self.answers(agent="claude", llm="anthropic",
                                                                          connect=str(self.home / "code")))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        qs = {q["id"]: q for q in json.loads(r.stdout)["questions"]}
        for qid in ("language", "owner", "workspace", "modules", "llm_model"):
            self.assertEqual(qs[qid]["via"], "chat", qid)
        self.assertNotIn("agent", qs, "answered: not asked again")
        r = self.hub_cmd("init", "--questions", "--answers", self.answers(connect=""))
        self.assertNotIn("connect", {q["id"] for q in json.loads(r.stdout)["questions"]}, "\"\" is an answer (skip)")
        for qid, via in (("install_agent", "dialog"), ("agent_token", "user"), ("register_mcp", "dialog"),
                         ("llm_key", "page"), ("connect_root", "dialog")):
            self.assertEqual(qs[qid]["via"], via, qid)
            self.assertNotIn("default", qs[qid], "the owner answers it directly, the agent has nothing to fill in")
        self.assertFalse((self.hub / ".hub-home").exists())
        self.assertFalse((self.home / ".brunna").exists())

    def test_answers_apply_and_end_with_one_json_line(self):
        r = self.hub_cmd("init", "--answers", self.answers(owner="Alex", workspace=str(self.home / "ws"), agent="none",
                                                           llm="none", language="en"), "--no-launchd")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout.splitlines()[-1]), {"done": True, "exit": 0, "needs_user": []})
        cfg = json.loads((self.home / ".brunna" / "config" / "brunna.json").read_text())
        self.assertEqual((cfg["owner"]["tag"], cfg["language"]["ui"]), ("Alex", "en"))
        r = self.hub_cmd("init", "--questions")
        self.assertEqual(r.returncode, 1)
        self.assertIn("error", json.loads(r.stdout))

    def test_the_file_cannot_consent_or_carry_secrets(self):
        for bad in ({"register_mcp": "yes"}, {"connect_root": "yes"}, {"agent_token": "x"}, {"llm_key": "x"},
                    {"agent": "someone"}, {"owner": 5}, {"owner": "--dry-run"}, {"owner": "--scheduler-only"}):
            with self.subTest(bad=bad):
                r = self.hub_cmd("init", "--answers", self.answers(**bad), "--no-launchd")
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertIn("error", json.loads(r.stdout))
        r = self.hub_cmd("init", "--answers", self.answers(owner="Alex"), "--yes")
        self.assertEqual(r.returncode, 2, "--yes can't stand in for the owner")
        self.assertFalse((self.hub / ".hub-home").exists(), "refused before anything was written")


class Transport(unittest.TestCase):
    def test_no_screen_hands_consent_and_secrets_back(self):
        agent = init_dialogue.Agent()
        with mock.patch.object(init_dialogue, "has_screen", lambda: False), \
                mock.patch.object(init_dialogue, "native_yes") as dialog, \
                mock.patch.object(init_dialogue, "secret_page") as page, redirect_stdout(io.StringIO()):
            self.assertFalse(agent.consent("register_mcp", "Register?"))
            self.assertIsNone(agent.secret("llm_key", "key"))
        dialog.assert_not_called()
        page.assert_not_called()
        self.assertEqual([n["id"] for n in agent.needs], ["register_mcp", "llm_key"])

    def test_the_dialog_decides(self):
        for answer, want, needs in ((True, True, 0), (False, False, 0), (None, False, 1)):
            agent = init_dialogue.Agent()
            with mock.patch.object(init_dialogue, "has_screen", lambda: True), \
                    mock.patch.object(init_dialogue, "native_yes", lambda q: answer), redirect_stdout(io.StringIO()):
                self.assertIs(agent.consent("x", "Do it?"), want)
            self.assertEqual(len(agent.needs), needs, "unanswered: handed back, never taken as yes")

    def test_connect_root_needs_the_dialog_through_an_agent(self):
        ah.init_dialogue.use(init_dialogue.Agent())
        try:
            with mock.patch.object(init_dialogue, "has_screen", lambda: True), \
                    mock.patch.object(init_dialogue, "native_yes", lambda q: False), \
                    mock.patch.object(ah.subprocess, "run") as run, redirect_stdout(io.StringIO()):
                ah.connect_existing({"connect": "/somewhere"}, False)
            run.assert_not_called()
        finally:
            ah.init_dialogue.use(init_dialogue.Terminal())

    def test_osascript_gets_the_question_as_an_argument(self):
        q = 'Connect "x" \\ end tell'
        done = subprocess.CompletedProcess([], 0, "button returned:Yes, gave up:false\n", "")
        with mock.patch.object(init_dialogue.sys, "platform", "darwin"), \
                mock.patch.object(init_dialogue.subprocess, "run", return_value=done) as run:
            self.assertTrue(init_dialogue.native_yes(q))
        argv = run.call_args.args[0]
        self.assertEqual(argv[-2:], ["--", q])
        self.assertNotIn(q, " ".join(argv[:-1]), "never part of the script")
        self.assertIn('default button "No"', " ".join(argv))
        for out, rc, err, want in (("button returned:, gave up:true\n", 0, "", None), ("", 1, "User canceled. (-128)", False)):
            with mock.patch.object(init_dialogue.sys, "platform", "darwin"), mock.patch.object(
                    init_dialogue.subprocess, "run", return_value=subprocess.CompletedProcess([], rc, out, err)):
                self.assertIs(init_dialogue.native_yes(q), want)

    def test_secret_page(self):
        """Only the exact path on 127.0.0.1 answers; one submission; nothing about the request is logged."""
        seen = {}

        def browser(argv, **kw):
            url = argv[-1]
            base, path = url.rsplit("/", 1)
            seen["url"] = url

            def go():
                def post(target, data, headers=None):
                    req = urllib.request.Request(target, data=data, headers=headers or {})
                    try:
                        return urllib.request.urlopen(req, timeout=5).status
                    except urllib.error.HTTPError as e:
                        return e.code
                    except urllib.error.URLError:   # closed: the page already took its one answer
                        return None
                seen["wrong path"] = post(base + "/nope", b"value=bad")
                seen["wrong host"] = post(url, b"value=bad", {"Host": "rebound.example"})
                seen["other origin"] = post(url, b"value=bad", {"Origin": "http://evil.example"})
                seen["get"] = urllib.request.urlopen(url, timeout=5).read().decode()
                seen["post"] = post(url, b"value=s3cret%21")
            seen["thread"] = threading.Thread(target=go, daemon=True)
            seen["thread"].start()
            return subprocess.CompletedProcess(argv, 0, "", "")
        err = io.StringIO()
        with mock.patch.object(init_dialogue.subprocess, "run", browser), \
                mock.patch.object(init_dialogue.shutil, "which", lambda name: "/usr/bin/" + name), \
                mock.patch.object(sys, "stderr", err):
            value = init_dialogue.secret_page("the key", wait=20)
            seen["thread"].join(10)
        self.assertEqual(value, "s3cret!")
        self.assertEqual((seen["wrong path"], seen["wrong host"], seen["other origin"]), (404, 404, 404))
        self.assertIn('type="password"', seen["get"])
        self.assertEqual(seen["post"], 200)
        self.assertEqual(err.getvalue(), "", "the request line (with the path) is never logged")
        self.assertGreater(len(seen["url"].rsplit("/", 1)[1]), 30, "the path is long and random")

    def test_recorder_never_acts(self):
        rec = init_dialogue.Recorder({})
        self.assertFalse(rec.consent("register_mcp", "Register?"))
        self.assertIsNone(rec.secret("llm_key", "key"))
        self.assertEqual([q["via"] for q in rec.questions], ["dialog", "page"])


if __name__ == "__main__":
    unittest.main()
