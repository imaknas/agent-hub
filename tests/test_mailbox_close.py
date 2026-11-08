"""Who closes a letter (bin/hubbus close_refusal): its recipient, with done; its sender only takes it back, with
done --withdraw and a note, recorded as withdrawn: true; the platform's own jobs and the owner's terminal close any.
A sender that closed its own question with plain done left the recipient's handler with nothing to answer and nobody
told (11-09), so plain done on another project's letter is refused. Scratch config, registry and bus only.

    bin/py -m unittest tests.test_mailbox_close
"""
import json
import pathlib
import sqlite3
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_bus_sender import Fixture, load_hubbus  # noqa: E402


class Close(Fixture):
    """`open` is not sandboxed, `box` is; `alpha` is one more unsandboxed project."""

    def setUp(self):
        super().setUp()
        ws = self.ws["alpha"] = self.tmp / "ws" / "alpha"
        ws.mkdir(parents=True)
        self.docs["alpha"] = {"v": 1, "name": "alpha", "paths": {"workspace": str(ws)}}
        (self.tmp / "state" / "shared" / "registry" / "index.json").write_text(json.dumps({"projects": self.docs}))

    def send(self, frm, to, typ="question", text="how many?"):
        out = self.hubbus("send", "--from", frm, "--to", to, "--type", typ, "--text", text, cwd=self.ws[frm]).stdout
        return out.strip().split()[-1]

    def closes(self, mid):
        c = sqlite3.connect(self.tmp / "bus" / "bus.db")
        return [(r[0], json.loads(r[1])) for r in
                c.execute("SELECT source, payload FROM events WHERE type='message.done' AND ref=?", (mid,))]

    def test_the_recipient_closes_it(self):
        mid = self.send("alpha", "open")
        self.hubbus("done", mid, "--note", "answered", cwd=self.ws["open"])
        [(src, p)] = self.closes(mid)
        self.assertEqual((src, p["note"]), ("open", "answered"))
        self.assertNotIn("withdrawn", p)

    def test_the_sender_s_plain_done_is_refused_and_says_how_to_take_it_back(self):
        mid = self.send("alpha", "open")
        r = self.hubbus("done", mid, "--note", "handled", cwd=self.ws["alpha"], check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("only its recipient closes it", r.stderr + r.stdout)
        self.assertIn(f"brunna done --withdraw {mid}", r.stderr + r.stdout)
        self.assertEqual(self.closes(mid), [])

    def test_another_project_can_t_close_it_and_nothing_is_closed(self):
        mid = self.send("open", "box", "info", "fyi")
        r = self.hubbus("done", mid, cwd=self.ws["alpha"], check=False)
        self.assertIn("is a letter to box, not to alpha", r.stderr + r.stdout)
        own = self.send("open", "alpha", "info", "for alpha")
        r = self.hubbus("done", own, mid, cwd=self.ws["alpha"], check=False)   # one of them not alpha's: neither closes
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((self.closes(own), self.closes(mid)), ([], []))

    def test_the_sender_takes_it_back_with_a_note(self):
        mid = self.send("alpha", "open")
        self.hubbus("done", "--withdraw", mid, "--note", "sent to the wrong project", cwd=self.ws["alpha"])
        [(src, p)] = self.closes(mid)
        self.assertEqual((src, p["note"], p["withdrawn"]), ("alpha", "sent to the wrong project", True))
        inbox = self.hubbus("inbox", "--project", "open", cwd=self.ws["open"]).stdout
        self.assertNotIn(mid, inbox)

    def test_withdraw_needs_a_note_the_sender_and_an_open_letter(self):
        mid = self.send("alpha", "open")
        r = self.hubbus("done", "--withdraw", mid, cwd=self.ws["alpha"], check=False)
        self.assertIn("--withdraw needs --note", r.stderr + r.stdout)
        r = self.hubbus("done", "--withdraw", mid, "--note", "mine now", cwd=self.ws["open"], check=False)
        self.assertIn("only the project that sent", r.stderr + r.stdout)
        self.hubbus("done", mid, cwd=self.ws["open"])
        r = self.hubbus("done", "--withdraw", mid, "--note", "too late", cwd=self.ws["alpha"], check=False)
        self.assertIn("already closed", r.stderr + r.stdout)
        self.assertEqual(len(self.closes(mid)), 1)

    def test_a_question_to_the_owner_is_taken_back_with_drop(self):
        mid = self.send("alpha", "owner")
        r = self.hubbus("done", "--withdraw", mid, "--note", "not needed", cwd=self.ws["alpha"], check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--type drop --question", r.stderr + r.stdout)

    def test_a_sandboxed_project_is_told_it_can_t_take_one_back(self):
        r = self.hubbus("done", "--withdraw", "m1", "--note", "x", cwd=self.ws["box"], check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sandboxed project can't take back", r.stderr + r.stdout)

    def test_the_owner_s_terminal_closes_any(self):
        mid = self.send("alpha", "open")
        self.hubbus("done", mid, "--note", "closed by the owner")   # cwd: the scratch dir, no project
        self.assertEqual(len(self.closes(mid)), 1)

    def test_a_platform_job_closes_any_a_project_s_own_job_doesn_t(self):
        mid = self.send("alpha", "open")
        job = {"BRUNNA_JOB": "alpha/nightly", "CLAUDE_CODE_SESSION_ID": "s-job"}
        r = self.hubbus("done", mid, "--note", "x", cwd=self.ws["alpha"], env=job, check=False)
        self.assertNotEqual(r.returncode, 0)   # a project's job runs in its own folder: only its own letters
        self.hubbus("done", mid, "--note", "applied", cwd=ROOT, env={**job, "BRUNNA_JOB": "brunna/librarian"})
        self.assertEqual(len(self.closes(mid)), 1)


    def test_the_mcp_tool_asks_as_its_project_from_any_folder(self):
        """A server whose project comes from BRUNNA_PROJECT runs hubbus in that project's folder: its plain done on a
        letter it sent is refused, and withdraw takes it back, as on the command line."""
        import os
        from unittest import mock
        sys.path.insert(0, str(ROOT / "bin"))
        import mcp_server
        mid = self.send("alpha", "open")
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.object(mcp_server.hubconf, "workspace", lambda p: self.ws.get(p)), \
                mock.patch.object(mcp_server, "_hubbus", lambda: mock.Mock(outboxed=lambda p: False)):
            os.chdir(self.tmp)   # a folder in no project
            try:
                ok, out = mcp_server.hubbus_cli("alpha", "done", mid)
                self.assertFalse(ok, out)
                ok, out = mcp_server.call("alpha", "", "done", {"ids": [mid], "note": "wrong project", "withdraw": True})
                self.assertTrue(ok, out)
            finally:
                os.chdir(ROOT)
        [(src, p)] = self.closes(mid)
        self.assertEqual((src, p["withdrawn"]), ("alpha", True))


class Rule(unittest.TestCase):
    """close_refusal's cases that need a session or the hub's own folder."""

    @classmethod
    def setUpClass(cls):
        cls.hb = load_hubbus()

    def letter(self, src="alpha", to="open"):
        return {"id": "m1", "type": "message", "source": src, "to_project": to, "payload": "{}"}

    def refusal(self, here, session=None, **kw):
        from unittest import mock
        hb = self.hb
        with mock.patch.object(hb, "own_names", lambda p, taken=None: [p]), \
                mock.patch.object(hb, "now_named", lambda p, taken=None: p), \
                mock.patch.object(hb.hubconf, "hub_project", lambda: "brunna"), \
                mock.patch.object(hb, "is_done", lambda c, mid: False), \
                mock.patch.object(hb, "OWNERS", {"owner"}), \
                mock.patch.object(hb, "_", lambda msg, **kw: msg.format(**kw) if kw else msg):   # the msgids
            return hb.close_refusal(None, kw.get("r", self.letter()), here, session, kw.get("withdraw", False),
                                    kw.get("note"))

    def test_the_hub_s_own_folder_without_a_session_is_the_platform(self):
        self.assertIsNone(self.refusal("brunna"))

    def test_a_session_in_the_hub_s_folder_is_a_project_like_any(self):
        self.assertIn("only its recipient closes it", self.refusal("brunna", session="s1"))

    def test_a_session_in_the_hub_s_folder_closes_the_owner_side_s_handoffs(self):
        handoff = self.letter(src="owner", to="open")
        self.assertIsNone(self.refusal("brunna", session="s1", r=handoff))   # a librarian run by hand
        self.assertIn("only its recipient closes it", self.refusal("alpha", session="s1", r=handoff))

    def test_records_keep_their_rule(self):
        rec = {"id": "r1", "type": "record", "source": "alpha", "to_project": "open", "payload": "{}"}
        self.assertIsNone(self.refusal("beta", session="s1", r=rec))
        self.assertIn("not a letter", self.refusal("alpha", session="s1", r=rec, withdraw=True, note="x"))


if __name__ == "__main__":
    unittest.main()
