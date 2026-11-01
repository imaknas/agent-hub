"""bin/alert.py: what is recorded as "an alert could not be sent" (the status page turns that red), with and without
a channel set up in hub.json alerts.

    bin/py -m unittest tests.test_alert_channels
"""
import importlib.util
import json
import pathlib
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
        with mock.patch.dict(self.m.CHANNELS, {"ntfy": (lambda text: False, lambda: False)}, clear=True):
            self.m.once("k", ["broken"], "fine again")
            self.assertNotIn("k", self.failed())
            self.assertEqual(self.m.failures(), {}, "records from channels that are gone do not count")


CURL = ["/usr/bin/curl", "-sS", "-m", "20", "-o", "/dev/null", "-w", "%{http_code}"]


class Adapters(unittest.TestCase):
    """Each channel's adapter with the network mocked: curl (subprocess.run) for Discord/ntfy/macOS, smtplib for email.
    hub.json is a dict handed to hubconf.config(); the credential files are real files in a temp folder."""

    def setUp(self):
        self.m = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
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
        self.assertEqual(argv, CURL + ["-H", "Title: agent-hub", "--data-binary", "@-", "https://ntfy.sh/hub-alerts"])
        self.assertEqual(kw["input"], "y" * 4000)

    def test_ntfy_own_server_and_token(self):
        self.cfg["alerts"]["ntfy"] = {"topic": "t", "base_url": "https://push.example.org/",
                                      "token_file": self.secret("ntfy", "tk_1")}
        self.assertTrue(self.m.send("hello"))
        (argv, kw), = self.calls
        self.assertEqual(argv, CURL + ["-H", "Title: agent-hub", "-H", "Authorization: Bearer tk_1", "--data-binary", "@-",
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
                               ("send", "agent-hub:⚠️ disk full", "bot@example.org", "me@example.org",
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
        self.assertEqual(send[1:4], ("agent-hub:" + "a" * 80, "hub@example.org", "me@x"))

    def test_email_explicit_port(self):
        log = self.smtp()
        self.cfg["alerts"]["email"] = {"host": "h", "port": "2525", "to": "me@x"}
        self.assertTrue(self.m.send("x"))
        self.assertEqual(log[0], ("connect", "plain", "h", 2525, 20))
        self.assertEqual(next(e for e in log if e[0] == "send")[2], "agent-hub", "From falls back to agent-hub")

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
        self.assertEqual(argv, ["/usr/bin/osascript", "-e", f'display notification {json.dumps(text)} with title "agent-hub"'])
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


if __name__ == "__main__":
    unittest.main()
