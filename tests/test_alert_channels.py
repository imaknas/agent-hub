"""bin/alert.py: what is recorded as "an alert could not be sent" (the status page turns that red), with and without
a channel set up in hub.json alerts.

    bin/py -m unittest tests.test_alert_channels
"""
import importlib.util
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))


def load():
    spec = importlib.util.spec_from_file_location("alert_channels", ROOT / "bin" / "alert.py")   # a private copy
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Channels(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for name in ("SENT", "FAILED"):
            p = mock.patch.object(self.m, name, self.tmp / f"{name}.json")
            p.start()
            self.addCleanup(p.stop)

    def failed(self) -> dict:
        f = self.tmp / "FAILED.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def test_a_channel_that_fails_is_recorded(self):
        with mock.patch.dict(self.m.CHANNELS, {"ntfy": (lambda text: False, lambda: True)}, clear=True):
            self.m.once("k", ["broken"], "fine again")
        self.assertIn("k", self.failed())

    def test_no_channel_set_up(self):
        """nothing was there to send on: not a failed send (the status page shows a setup reminder instead)"""
        (self.tmp / "FAILED.json").write_text(json.dumps({"old": {"at": "2026-10-01T00:00:00", "text": "x"}}))
        with mock.patch.dict(self.m.CHANNELS, {"ntfy": (lambda text: False, lambda: False)}, clear=True), \
                mock.patch.dict(self.m.KEYED, {}, clear=True):   # this machine's own assistant channel is not this test's
            self.m.once("k", ["broken"], "fine again")
            self.assertNotIn("k", self.failed())
            self.assertEqual(self.m.failures(), {}, "records from channels that are gone do not count")


CURL = ["/usr/bin/curl", "-sS", "-m", "20", "-o", "/dev/null", "-w", "%{http_code}"]


class NoCurl(unittest.TestCase):
    def test_a_machine_without_curl_has_the_alert_not_sent_not_a_crash(self):
        """A slim Linux has no /usr/bin/curl (11-15: the git fence job died there): the channel says not sent, which is
        recorded and shown, and the job that alerted goes on."""
        m = load()
        with mock.patch.object(m.subprocess, "run", side_effect=FileNotFoundError(2, "No such file", "/usr/bin/curl")):
            self.assertEqual(m._curl("http://127.0.0.1:9/x", data="t"), "")


class AssistantChannel(unittest.TestCase):
    """alerts.assistant: keyed notices to the personal assistant, beside the other channels
    (its platform-alerts contract, modules/assistant.yaml)."""

    def setUp(self):
        self.m = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for name in ("SENT", "FAILED"):
            p = mock.patch.object(self.m, name, self.tmp / f"{name}.json")
            p.start()
            self.addCleanup(p.stop)
        self.token = self.tmp / "token"
        self.token.write_text("tok\n")
        conf = {"alerts.assistant": True, "assistant.api": "http://127.0.0.1:18795/api/messages"}
        for p in (mock.patch.object(self.m.hubconf, "get", lambda k, d=None: conf.get(k, d)),
                  mock.patch.object(self.m.hubconf, "p", lambda k: self.token),
                  mock.patch.dict(self.m.CHANNELS, {"ntfy": (self.other, lambda: True)}, clear=True),
                  mock.patch.object(self.m, "_curl", self.curl)):
            p.start()
            self.addCleanup(p.stop)
        self.status, self.posts, self.texts = "200", [], []

    def other(self, text):
        self.texts.append(text)
        return True

    def curl(self, *args, data=None):
        self.posts.append((args, json.loads(data)))
        return self.status

    def test_a_problem_its_retry_its_reminder_and_its_end(self):
        self.status = "500"
        self.m.once("k", ["one", "two"], "fine again")
        self.assertIn("k", json.loads((self.tmp / "FAILED.json").read_text()))
        first = self.posts[-1][1]
        self.assertEqual({x: first[x] for x in ("v", "source", "key", "state", "detail")},
                         {"v": 1, "source": self.m.hubconf.hub_project(), "key": "k", "state": "open", "detail": "one\ntwo"})
        self.assertTrue(first["text"].startswith("one"))
        self.assertIn("/api/alerts", self.posts[-1][0][-1])
        self.assertIn("Authorization: Bearer tok", self.posts[-1][0])

        self.status = "200"
        self.m.once("k", ["two", "one"], "fine again")     # a retry: the same notice, whatever the order
        self.assertEqual(self.posts[-1][1]["at"], first["at"])
        self.assertEqual(json.loads((self.tmp / "FAILED.json").read_text()), {})
        before = len(self.posts)
        self.m.once("k", ["one", "two"], "fine again")     # went out, nothing changed: nothing to say
        self.assertEqual(len(self.posts), before)

        sent = json.loads((self.tmp / "SENT.json").read_text())
        sent["k"]["at"] = "2020-01-01T00:00:00"
        (self.tmp / "SENT.json").write_text(json.dumps(sent))
        with mock.patch.object(self.m, "datetime", wraps=self.m.datetime) as dt:
            dt.datetime.now.return_value = self.m.datetime.datetime(2030, 1, 1, 12, 0)
            dt.datetime.fromisoformat = self.m.datetime.datetime.fromisoformat
            self.m.once("k", ["one", "two"], "fine again")   # a day on, unresolved: a new notice
        self.assertNotEqual(self.posts[-1][1]["at"], first["at"])
        self.assertEqual(self.posts[-1][1]["state"], "open")

        self.m.once("k", [], "fine again")
        last = self.posts[-1][1]
        self.assertEqual((last["state"], last["text"]), ("resolved", "fine again"))
        self.assertNotIn("detail", last)
        self.assertEqual(len(self.texts), len(self.posts), "the other channels went out each time too")

    def test_one_problem_is_said_as_it_is(self):
        self.m.once("k", ["one"], "fine again")
        self.assertEqual(self.posts[-1][1]["text"], "one")
        self.assertNotIn("detail", self.posts[-1][1])

    def test_a_notice_is_for_keyed_problems_only(self):
        self.assertTrue(self.m.send("free text"))
        self.assertEqual(self.posts, [])
        self.assertEqual(self.texts, ["free text"])

    def test_alone_free_text_has_nowhere_to_go(self):
        with mock.patch.dict(self.m.CHANNELS, {}, clear=True):
            self.assertEqual(self.m.configured(), ["assistant"])
            self.assertFalse(self.m.send("free text"))

    def test_off_or_no_address_is_not_set_up(self):
        for conf in ({"assistant.api": "http://x/"}, {"alerts.assistant": True}):
            with mock.patch.object(self.m.hubconf, "get", lambda k, d=None, c=conf: c.get(k, d)):
                self.assertEqual(self.m.configured(), ["ntfy"])

    def test_no_token_is_a_failure(self):
        self.token.unlink()
        self.m.once("k", ["one"], "fine again")
        self.assertEqual(self.posts, [])
        self.assertIn("k", json.loads((self.tmp / "FAILED.json").read_text()))


class Adapters(unittest.TestCase):
    """Each channel's adapter with the network mocked: curl (subprocess.run) for Discord/ntfy/macOS, smtplib for email.
    hub.json is a dict handed to hubconf.config(); the credential files are real files in a temp folder."""

    def setUp(self):
        self.m = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for name in ("SENT", "FAILED"):
            p = mock.patch.object(self.m, name, self.tmp / f"{name}.json")
            p.start()
            self.addCleanup(p.stop)
        self.cfg = {"alerts": {}}
        p = mock.patch.object(self.m.hubconf, "config", lambda: self.cfg)
        p.start()
        self.addCleanup(p.stop)
        self.calls = []
        self.code = "200"
        p = mock.patch.object(self.m.subprocess, "run", self.fake_run)
        p.start()
        self.addCleanup(p.stop)

    def fake_run(self, argv, **kw):
        self.calls.append((list(argv), kw))
        return mock.Mock(stdout=f"{self.code}\n", returncode=0 if self.code == "200" else 1)

    def secret(self, name, value) -> str:
        f = self.tmp / name
        f.write_text(f"{value}\n")
        return str(f)

    def discord(self):
        self.cfg["alerts"].update(discord_token_file=self.secret("tok", "T0K"), discord_channel_file=self.secret("ch", "42"))

    def failed(self) -> dict:
        f = self.tmp / "FAILED.json"
        return json.loads(f.read_text()) if f.exists() else {}

    # discord
    def test_discord_payload(self):
        self.discord()
        self.assertEqual(self.m.configured(), ["discord"])
        self.assertTrue(self.m.send("x" * 3000))
        (argv, kw), = self.calls
        self.assertEqual(argv, CURL + ["-H", "Authorization: Bot T0K", "-H", "Content-Type: application/json",
                                       "--data-binary", "@-", "https://discord.com/api/v10/channels/42/messages"])
        self.assertEqual(json.loads(kw["input"]), {"content": "x" * 1990}, "cut to Discord's 2000-character limit")

    def test_discord_status_other_than_200_is_a_failure(self):
        self.discord()
        for code in ("204", "401", "500", ""):   # "" = curl itself failed
            with self.subTest(code=code):
                self.code = code
                self.assertFalse(self.m.send("x"))

    def test_discord_without_its_files_is_not_set_up(self):
        self.cfg["alerts"]["discord_token_file"] = str(self.tmp / "missing")
        self.cfg["alerts"]["discord_channel_file"] = self.secret("ch", "42")
        self.assertEqual(self.m.configured(), [])
        self.assertFalse(self.m.send("x"))
        self.assertEqual(self.calls, [])

    # ntfy
    def test_ntfy_default_server_no_token(self):
        self.cfg["alerts"]["ntfy"] = {"topic": "hub-alerts"}
        self.assertTrue(self.m.send("y" * 5000))
        (argv, kw), = self.calls
        self.assertEqual(argv, CURL + ["-H", "Title: brunna", "--data-binary", "@-", "https://ntfy.sh/hub-alerts"])
        self.assertEqual(kw["input"], "y" * 4000)

    def test_ntfy_own_server_and_token(self):
        self.cfg["alerts"]["ntfy"] = {"topic": "t", "base_url": "https://push.example.org/",
                                      "token_file": self.secret("ntfy", "tk_1")}
        self.assertTrue(self.m.send("hello"))
        (argv, kw), = self.calls
        self.assertEqual(argv, CURL + ["-H", "Title: brunna", "-H", "Authorization: Bearer tk_1", "--data-binary", "@-",
                                       "https://push.example.org/t"])

    def test_ntfy_token_file_missing_fails_without_sending(self):
        self.cfg["alerts"]["ntfy"] = {"topic": "t", "token_file": str(self.tmp / "missing")}
        self.assertEqual(self.m.configured(), ["ntfy"])
        self.assertFalse(self.m.send("hello"))
        self.assertEqual(self.calls, [], "no unauthenticated send instead")

    def test_ntfy_without_a_topic(self):
        self.cfg["alerts"]["ntfy"] = {"base_url": "https://push.example.org"}
        self.assertEqual(self.m.configured(), ["ntfy"], "set up (a non-empty block) ...")
        self.assertFalse(self.m.send("x"), "... but it cannot send: a failed send")
        self.assertEqual(self.calls, [])

    def test_ntfy_failure_status(self):
        self.cfg["alerts"]["ntfy"] = {"topic": "t"}
        self.code = "429"
        self.assertFalse(self.m.send("x"))

    # email
    def smtp(self, fail=None):
        """a fake smtplib.SMTP / SMTP_SSL class recording what was done on it"""
        log = []

        class Conn:
            def __init__(self, host, port, timeout=None):
                log.append(("connect", self.kind, host, port, timeout))
                if fail == "connect":
                    raise OSError("refused")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                log.append(("close",))

            def starttls(self):
                log.append(("starttls",))

            def login(self, user, password):
                log.append(("login", user, password))
                if fail == "login":
                    raise smtplib.SMTPAuthenticationError(535, b"bad")

            def send_message(self, msg):
                log.append(("send", msg["Subject"], msg["From"], msg["To"], msg.get_content()))

        plain = type("SMTP", (Conn,), {"kind": "plain"})
        ssl = type("SMTP_SSL", (Conn,), {"kind": "ssl"})
        import smtplib
        for name, cls in (("SMTP", plain), ("SMTP_SSL", ssl)):
            p = mock.patch.object(smtplib, name, cls)
            p.start()
            self.addCleanup(p.stop)
        return log

    def test_email_starttls_with_login(self):
        log = self.smtp()
        self.cfg["alerts"]["email"] = {"host": "smtp.example.org", "user": "bot@example.org", "to": "me@example.org",
                                       "password_file": self.secret("pw", "s3cret")}
        self.assertTrue(self.m.send("⚠️ disk full\nsecond line"))
        self.assertEqual(log, [("connect", "plain", "smtp.example.org", 587, 20), ("starttls",),
                               ("login", "bot@example.org", "s3cret"),
                               ("send", "brunna:⚠️ disk full", "bot@example.org", "me@example.org",
                                "⚠️ disk full\nsecond line\n"), ("close",)])
        self.assertEqual(self.calls, [], "email does not go through curl")

    def test_email_ssl_from_and_no_login_without_a_password(self):
        log = self.smtp()
        self.cfg["alerts"]["email"] = {"host": "h", "ssl": True, "user": "u", "from": "hub@example.org", "to": "me@x"}
        self.assertTrue(self.m.send("a" * 100))
        self.assertEqual(log[0], ("connect", "ssl", "h", 465, 20))
        self.assertNotIn(("starttls",), log)
        self.assertFalse([e for e in log if e[0] == "login"], "a user without a password file: no login")
        send = next(e for e in log if e[0] == "send")
        self.assertEqual(send[1:4], ("brunna:" + "a" * 80, "hub@example.org", "me@x"))

    def test_email_explicit_port(self):
        log = self.smtp()
        self.cfg["alerts"]["email"] = {"host": "h", "port": "2525", "to": "me@x"}
        self.assertTrue(self.m.send("x"))
        self.assertEqual(log[0], ("connect", "plain", "h", 2525, 20))
        self.assertEqual(next(e for e in log if e[0] == "send")[2], "brunna", "From falls back to the product name")

    def test_email_failures(self):
        for fail in ("connect", "login"):
            with self.subTest(fail=fail):
                self.smtp(fail=fail)
                self.cfg["alerts"]["email"] = {"host": "h", "user": "u", "to": "me@x",
                                               "password_file": self.secret("pw", "p")}
                self.assertFalse(self.m.send("x"))
        with self.subTest("password file missing"):
            log = self.smtp()
            self.cfg["alerts"]["email"] = {"host": "h", "user": "u", "to": "me@x", "password_file": str(self.tmp / "no")}
            self.assertFalse(self.m.send("x"))
            self.assertEqual(log, [], "nothing sent without the password")

    def test_email_without_host_or_to(self):
        log = self.smtp()
        for conf in ({"to": "me@x"}, {"host": "h"}):
            with self.subTest(conf=conf):
                self.cfg["alerts"]["email"] = conf
                self.assertEqual(self.m.configured(), ["email"])
                self.assertFalse(self.m.send("x"))
        self.assertEqual(log, [])

    # macos
    def test_macos_notification(self):
        self.cfg["alerts"]["macos"] = True
        self.assertTrue(self.m.send('say "hi"' + "z" * 300))
        (argv, kw), = self.calls
        text = 'say "hi"' + "z" * 192
        self.assertEqual(argv, ["/usr/bin/osascript", "-e", f'display notification {json.dumps(text)} with title "brunna"'])
        self.code = "1"
        self.assertFalse(self.m.send("x"))

    # several channels, probe, failure records
    def test_channel_order(self):
        self.discord()
        self.cfg["alerts"].update(ntfy={"topic": "t"}, email={"host": "h", "to": "me@x"}, macos=True)
        self.assertEqual(self.m.configured(), ["discord", "ntfy", "email", "macos"])

    def test_one_channel_failing_fails_the_send_and_is_recorded(self):
        self.discord()
        self.cfg["alerts"]["ntfy"] = {"topic": "t"}
        self.calls.clear()
        codes = iter(["200", "500"])
        self.fake_run_codes(codes)
        self.m.once("k", ["broken one", "broken two"], "fine")
        self.assertEqual([c[0][-1] for c in self.calls], ["https://discord.com/api/v10/channels/42/messages",
                                                         "https://ntfy.sh/t"])
        rec = self.failed()["k"]
        self.assertEqual(rec["text"], "⚠️ broken one", "the first line of the alert")
        self.assertRegex(rec["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
        self.assertFalse((self.tmp / "SENT.json").exists(), "not marked as sent: tried again next time")
        # next time it goes out: the record is cleared and the send remembered
        self.fake_run_codes(iter(["200", "200"]))
        self.m.once("k", ["broken one", "broken two"], "fine")
        self.assertEqual(self.failed(), {})
        self.assertEqual(json.loads((self.tmp / "SENT.json").read_text())["k"]["problems"], ["broken one", "broken two"])

    def test_every_channel_is_tried_when_one_fails(self):
        """send() sends on every channel set up: a broken Discord must not keep ntfy from being tried (all() over a
        generator used to stop at the first failure)"""
        self.discord()
        self.cfg["alerts"]["ntfy"] = {"topic": "t"}
        self.fake_run_codes(iter(["500", "200"]))
        self.assertFalse(self.m.send("x"))
        self.assertEqual(len(self.calls), 2, "ntfy was not tried after Discord failed")

    def test_probe(self):
        self.assertFalse(self.m.probe(), "no channel")
        self.cfg["alerts"]["ntfy"] = {"topic": "t"}
        self.assertTrue(self.m.probe(), "a channel with no silent check counts as working")
        self.assertEqual(self.calls, [])
        self.discord()
        self.assertTrue(self.m.probe())
        (argv, kw), = self.calls
        self.assertEqual(argv, CURL + ["-H", "Authorization: Bot T0K", "https://discord.com/api/v10/users/@me"])
        self.assertIsNone(kw.get("input"), "no message sent")
        self.code = "401"
        self.assertFalse(self.m.probe())

    def fake_run_codes(self, codes):
        def run(argv, **kw):
            self.calls.append((list(argv), kw))
            return mock.Mock(stdout=next(codes), returncode=0)
        self.calls.clear()
        p = mock.patch.object(self.m.subprocess, "run", run)
        p.start()
        self.addCleanup(p.stop)


class HeldUnderTest(unittest.TestCase):
    """A test that reaches an alert on the live instance's state (no SENT/FAILED of its own) sends and records nothing:
    two memory-sync tests sent a project called proj to the owner's Discord from a worktree (11-14)."""

    def test_nothing_goes_out(self):
        import contextlib
        import io
        m = load()
        sent = []
        with mock.patch.dict(m.CHANNELS, {"ntfy": (lambda text: sent.append(text) or True, lambda: True)}, clear=True), \
                mock.patch.object(m, "_save", lambda path, data: sent.append(path)), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            m.once("k", ["a problem"], "fine")
            self.assertFalse(m.send("free text"))
            m.record_failure("k", "x")
        self.assertEqual(sent, [])
        self.assertIn("held", err.getvalue())


class RealChannelsUnderTest(unittest.TestCase):
    """A test with its own state files that leaves this machine's channels in place reaches nothing: the assistant
    channel pushed "broken" to the owner's phone from test_a_channel_that_fails_is_recorded (11-14)."""

    def test_no_post_with_own_state_and_the_machines_channels(self):
        import contextlib
        import io
        m = load()
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        token = tmp / "token"
        token.write_text("t")
        calls = []

        def run(*a, **k):   # stands for the real subprocess.run: a call here would have been a POST
            calls.append(a)
            raise AssertionError("reached a real channel")
        conf = {"alerts.assistant": True, "assistant.api": "http://127.0.0.1:9/", "alerts.ntfy": {"topic": "t"}}
        with mock.patch.object(m, "SENT", tmp / "s.json"), mock.patch.object(m, "FAILED", tmp / "f.json"), \
                mock.patch.object(m.hubconf, "get", lambda k, d=None: conf.get(k, d)), \
                mock.patch.object(m.hubconf, "p", lambda k: token), \
                mock.patch.object(m.subprocess, "run", run), mock.patch.object(m, "_REAL", (run,)), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(sorted(m.configured()), ["assistant", "discord", "ntfy"])
            m.once("k", ["broken"], "fine")
            self.assertFalse(m.send("free text"))
        self.assertEqual(calls, [])
        self.assertIn("not sent", err.getvalue())


if __name__ == "__main__":
    unittest.main()
