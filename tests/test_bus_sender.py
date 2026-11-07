"""Who sent a letter (docs/design/bus-sender.md): a sandboxed project's letters are authenticated by the folder they come
from, and the bus file is in no sandbox.

A sandboxed project writes each letter into its own outbox (`<workspace>/.git/agent-hub/outbox/`, which only it can
write); the platform's mailbox service (`hubbus dispatch`) moves it into the bus with source = that project, whatever
the file says, and writes the project's own mailbox view (`.git/agent-hub/inbox.json`) back. The attacks first: a
manifest.update claiming another project (through `hubbus send --from`, in a hand-written outbox file, and straight
into the SQLite file), and reading another project's letters (through the CLI, the hooks and the file). Scratch
config, registry, bus and repos only; the Seatbelt tests run the real profile.

    bin/py -m unittest tests.test_bus_sender
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import names  # noqa: E402
import seatbelt  # noqa: E402

GIT_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
MANIFEST = "name: open\nservices:\n  - name: x\n    run: touch /tmp/pwned\n"


def load_hubbus():
    loader = importlib.machinery.SourceFileLoader("hubbus_sender", str(BIN / "hubbus"))
    spec = importlib.util.spec_from_loader("hubbus_sender", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


class Fixture(unittest.TestCase):
    """Two projects in a scratch instance: `box` (enforce.sandbox) and `open` (not sandboxed). The CLI runs as a
    subprocess with this instance's config, state and bus."""

    def setUp(self):
        t = self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, t, ignore_errors=True)
        for d in ("home", "config/projects", "data", "secrets", "bus", "state/registry"):
            (t / d).mkdir(parents=True)
        self.ws = {}
        docs = {}
        for name, sandbox in (("box", True), ("open", False)):
            ws = self.ws[name] = t / "ws" / name
            ws.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", "-b", "main", str(ws)], check=True, env={**os.environ, **GIT_ENV})
            docs[name] = {"v": 1, "name": name, "paths": {"workspace": str(ws)},
                          **({"enforce": {"sandbox": True}} if sandbox else {})}
        self.docs = docs
        (t / "state" / "registry" / "index.json").write_text(json.dumps({"projects": docs}))
        cfg = {"owner": {"name": "alex", "tag": "Alex"}, "owner_side": "builtin", "bus_dir": str(t / "bus"),
               "registry_dir": "projects", "workspace_root": str(t / "ws"), "runtime": {"default": "none"},
               "language": {"ui": "en", "content": "en"}}
        (t / "config" / "hub.json").write_text(json.dumps(cfg))
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("BRUNNA_", "AGENT_HUB_", "AGENTHUB_", "HUB_"))
                       or k in ("AGENT_HUB_PYTHON", "BRUNNA_PYTHON")}, **GIT_ENV,
                    "HOME": str(t / "home"), "AGENT_HUB_CONFIG": str(t / "config"), "AGENT_HUB_DATA": str(t / "data"),
                    "AGENT_HUB_SECRETS": str(t / "secrets"), "AGENT_HUB_STATE": str(t / "state"),
                    "AGENT_HUB_LANG_UI": "en"}
        self.env.pop("CLAUDE_CODE_SESSION_ID", None)

    def hubbus(self, *args, cwd=None, check=True, env=None):
        r = subprocess.run([sys.executable, str(BIN / "hubbus"), *args], env={**self.env, **(env or {})},
                           cwd=cwd or self.tmp, capture_output=True, text=True, timeout=60)
        if check:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def dispatch(self):
        self.hubbus("dispatch")

    def in_sandbox(self, *args, project="box"):
        """hubbus run as the project under its real Seatbelt profile (what its services and jobs get; its sessions'
        sandbox closes the same paths)."""
        return subprocess.run([seatbelt.SANDBOX_EXEC, "-p", self.profile(project), sys.executable, str(BIN / "hubbus"),
                               *args], env=self.env, cwd=self.ws[project], capture_output=True, text=True, timeout=60)

    def profile(self, name):
        """The real Seatbelt profile of a sandboxed project's service, for this scratch instance."""
        import policy
        with mock.patch.object(hubconf, "BUS_DIR", self.tmp / "bus"), \
                mock.patch.object(hubconf, "projects", lambda: self.docs), \
                mock.patch.object(seatbelt, "TMP", []):   # the fixture is in the temp area
            pol = policy.policy(name, self.docs[name], {"projects": self.docs})
            return seatbelt.service_profile(pol, [self.ws[name]])

    def rows(self, where="1", args=()):
        db = self.tmp / "bus" / "bus.db"
        if not db.exists():
            return []
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in c.execute(f"SELECT * FROM events WHERE {where} ORDER BY seq", args)]
        finally:
            c.close()

    def outbox(self, name="box") -> pathlib.Path:
        return names.repo_dir(self.ws[name] / ".git") / "outbox"

    def drop(self, body, name="m11011200-0123456789ab.json", project="box"):
        """What a sandboxed project can always do: write any file into its own outbox."""
        self.outbox(project).mkdir(parents=True, exist_ok=True)
        f = self.outbox(project) / name
        f.write_text(body if isinstance(body, str) else json.dumps(body))
        return f

    def letter_to(self, project, text, frm="agent-hub", typ="info"):
        self.hubbus("send", "--from", frm, "--to", project, "--type", typ, "--text", text)

    def letter_id(self, out: str) -> str:
        return out.strip().splitlines()[-1]

    def refusals(self, project="box"):
        return [json.loads(r["payload"])["text"] for r in self.rows("type='message' AND to_project=? AND source=?",
                                                                    (project, "brunna"))
                if json.loads(r["payload"]).get("outbox_refused")]


class Attacks(Fixture):
    """Each of these succeeded before the outbox (11-01)."""

    @unittest.skipUnless(seatbelt.can_sandbox() is None, "needs Seatbelt")
    def test_send_from_another_project_never_lands_as_it(self):
        self.letter_to("box", "a bus exists")
        r = self.in_sandbox("send", "--from", "open", "--to", "agent-hub", "--type", "manifest.update", "--text",
                            MANIFEST)
        self.dispatch()
        self.assertEqual(self.rows("source='open'"), [], r.stdout + r.stderr)

    def test_a_forged_sender_in_the_outbox_is_ignored(self):
        self.drop({"v": 1, "op": "send", "id": "m11011200-0123456789ab", "from": "open", "source": "open",
                   "sender": "open", "to": "agent-hub", "type": "manifest.update", "text": MANIFEST})
        self.dispatch()
        self.assertEqual(self.rows("source='open'"), [])
        got = self.rows("type='message' AND to_project='brunna'")
        self.assertEqual([r["source"] for r in got], ["box"])
        self.assertFalse(any(self.outbox().iterdir()), "a moved letter is deleted from the outbox")

    @unittest.skipUnless(seatbelt.can_sandbox() is None, "needs Seatbelt")
    def test_cli_does_not_read_another_projects_mailbox(self):
        self.letter_to("open", "the open project's secret plan")
        self.dispatch()
        for args in (("inbox", "--project", "open"), ("open-questions", "--project", "open"), ("inbox",)):
            r = self.in_sandbox(*args)
            self.assertNotIn("secret plan", r.stdout + r.stderr, args)

    def test_hook_delivery_does_not_follow_a_planted_gitdir(self):
        """A session hook runs outside the sandbox; the session's folder says `gitdir: <other project's .git>`."""
        self.letter_to("open", "the open project's secret plan")
        wt = self.ws["box"] / ".claude" / "worktrees" / "w"
        wt.mkdir(parents=True)
        (wt / ".git").write_text(f"gitdir: {self.ws['open'] / '.git'}\n")
        m = load_hubbus()
        m.DB = self.tmp / "bus" / "bus.db"
        with mock.patch.object(m.hubconf, "projects", lambda: self.docs):
            text = m.mail_text("session-start", "s-box", str(wt)) or ""
        self.assertNotIn("secret plan", text)

    @unittest.skipUnless(seatbelt.can_sandbox() is None, "needs Seatbelt")
    def test_the_sandbox_cannot_write_or_read_the_bus_file(self):
        self.letter_to("open", "the open project's secret plan")
        attack = ("import sqlite3, sys\n"
                  "c = sqlite3.connect(sys.argv[1], isolation_level=None)\n"
                  "try:\n"
                  "    print('read', [r[0] for r in c.execute('SELECT payload FROM events')])\n"
                  "except Exception as e:\n"
                  "    print('read-blocked', type(e).__name__)\n"
                  "try:\n"
                  "    c.execute(\"INSERT INTO events(id, ts, type, source, to_project, payload) VALUES"
                  "('forged', '2026-11-01', 'message', 'open', 'agent-hub', '{\\\"type\\\": \\\"manifest.update\\\"}')\")\n"
                  "    print('write')\n"
                  "except Exception as e:\n"
                  "    print('write-blocked', type(e).__name__)\n")
        r = subprocess.run([seatbelt.SANDBOX_EXEC, "-p", self.profile("box"), sys.executable, "-I", "-c", attack,
                            str(self.tmp / "bus" / "bus.db")], capture_output=True, text=True, timeout=60)
        self.assertNotIn("secret plan", r.stdout)
        self.assertEqual(self.rows("id='forged'"), [], r.stdout + r.stderr)

class Outbox(Fixture):
    def test_a_sandboxed_send_waits_in_the_outbox_then_goes_out_as_the_project(self):
        r = self.hubbus("send", "--to", "open", "--type", "request", "--text", "please look", cwd=self.ws["box"])
        mid = self.letter_id(r.stdout)
        self.assertRegex(mid, r"^m\d{8}-[0-9a-f]{12}$")
        self.assertIn("outbox", r.stderr)
        self.assertEqual(self.rows(), [], "nothing reaches the bus until the platform takes it in")
        self.assertEqual(len(list(self.outbox().iterdir())), 1)
        self.dispatch()
        got = self.rows("type='message'")
        self.assertEqual([(g["id"], g["source"], g["to_project"]) for g in got], [(mid, "box", "open")])
        self.assertEqual(list(self.outbox().iterdir()), [])

    def test_unsandboxed_projects_still_send_straight_to_the_bus(self):
        self.hubbus("send", "--to", "box", "--type", "info", "--text", "hi", cwd=self.ws["open"])
        self.assertEqual([r["source"] for r in self.rows("type='message'")], ["open"])
        self.assertFalse(self.outbox("open").exists())

    def test_the_view_holds_its_own_letters_only(self):
        self.hubbus("send", "--to", "box", "--type", "request", "--text", "for box", cwd=self.ws["open"])
        self.letter_to("open", "the open project's secret plan")
        self.dispatch()
        view = json.loads((names.repo_dir(self.ws["box"] / ".git") / "inbox.json").read_text())
        self.assertEqual({e["to_project"] for e in view["events"]}, {"box"})
        shutil.move(self.tmp / "bus", self.tmp / "bus-away")   # the sandbox has no bus: reading never needs it
        r = self.hubbus("inbox", cwd=self.ws["box"])
        self.assertIn("for box", r.stdout)
        self.assertNotIn("secret plan", r.stdout)
        self.assertFalse((self.tmp / "bus").exists(), "reading the view created the bus")

    def test_reply_ack_and_done_from_the_sandbox(self):
        r = self.hubbus("send", "--to", "box", "--type", "request", "--text", "ping", cwd=self.ws["open"])
        req = self.letter_id(r.stdout)
        self.dispatch()
        self.hubbus("ack", req, cwd=self.ws["box"])
        rep = self.letter_id(self.hubbus("send", "--to", "open", "--type", "done", "--reply-to", req, "--text", "pong",
                                         cwd=self.ws["box"]).stdout)
        self.hubbus("done", req, "--note", "answered", cwd=self.ws["box"])
        self.dispatch()
        self.assertEqual([(r["source"], r["ref"]) for r in self.rows("id=?", (rep,))], [("box", req)])
        self.assertEqual([r["source"] for r in self.rows("type='message.done' AND ref=?", (req,))], ["box"])
        self.assertEqual(len(self.rows("type='message.delivered' AND ref=?", (req,))), 1)
        self.assertEqual(self.refusals(), [])
        r = self.hubbus("show", req, cwd=self.ws["box"])
        self.assertIn("message.done", r.stdout)

    def test_closing_or_replying_outside_its_own_letters_is_refused(self):
        r = self.hubbus("send", "--to", "open", "--type", "request", "--text", "between others", cwd=self.ws["open"])
        theirs = self.letter_id(r.stdout)
        self.drop({"v": 1, "op": "done", "ids": [theirs]}, name="1.json")
        self.drop({"v": 1, "op": "ack", "ids": [theirs]}, name="2.json")
        self.drop({"v": 1, "op": "send", "id": "m11011200-0123456789ab", "to": "open", "type": "info",
                   "text": "hijack", "reply_to": theirs}, name="3.json")
        self.dispatch()
        self.assertEqual(self.rows("ref=? AND type!='message'", (theirs,)), [])
        self.assertEqual(self.rows("id='m11011200-0123456789ab'"), [])
        self.assertEqual(len(self.refusals()), 3)
        self.assertEqual(list(self.outbox().iterdir()), [])

    def test_an_id_of_another_shape_is_refused(self):
        """A short id is what other writers use (new_id): taking one first would swallow their letter."""
        self.drop({"v": 1, "op": "send", "id": "m11011200-ab12", "to": "open", "type": "info", "text": "squat"})
        self.dispatch()
        self.assertEqual(self.rows("id='m11011200-ab12'"), [])
        self.assertEqual(len(self.refusals()), 1)

    def test_a_session_of_another_project_is_dropped(self):
        """from_session decides which session a reply resumes in: only one the hooks saw in this project counts."""
        self.letter_to("open", "a bus to be present on")
        m = load_hubbus()
        m.DB = self.tmp / "bus" / "bus.db"
        with mock.patch.object(m.hubconf, "projects", lambda: self.docs):
            m.mail_text("session-start", "s-open", str(self.ws["open"]))
            m.mail_text("session-start", "s-box", str(self.ws["box"]))
        self.drop({"v": 1, "op": "send", "id": "m11011200-000000000001", "to": "open", "type": "question",
                   "text": "q1", "session": "s-open"}, name="1.json")
        self.drop({"v": 1, "op": "send", "id": "m11011200-000000000002", "to": "open", "type": "question",
                   "text": "q2", "session": "s-box"}, name="2.json")
        self.dispatch()
        sessions = [json.loads(r["payload"]).get("from_session") for r in self.rows("type='message' AND source='box'")]
        self.assertEqual(sessions, [None, "s-box"])

    def test_malformed_files_are_refused_deleted_and_never_followed(self):
        secret = self.ws["open"] / "secret.json"
        secret.write_text(json.dumps({"v": 1, "op": "send", "id": "m11011200-00000000000a", "to": "open",
                                      "type": "info", "text": "through a link"}))
        self.outbox().mkdir(parents=True)
        (self.outbox() / "1-link.json").symlink_to(secret)
        os.mkfifo(self.outbox() / "2-fifo.json")
        self.drop("{not json", name="3-bad.json")
        self.drop("x" * (600 << 10), name="4-big.json")
        self.drop({"v": 1, "op": "send", "id": "m11011200-00000000000b", "to": "nobody", "type": "info",
                   "text": "x"}, name="5-to.json")
        self.drop({"v": 1, "op": "send", "id": "m11011200-00000000000c", "to": "open", "type": "info",
                   "text": ["not", "text"]}, name="6-type.json")
        self.drop({"v": 1, "op": "rm -rf"}, name="7-op.json")
        self.drop({"v": 1, "op": "send", "id": "m11011200-00000000000d", "to": "open", "type": "info",
                   "text": "fine"}, name="8-ok.json")
        self.drop("tmp", name=".9-being-written.json")
        self.dispatch()
        self.assertEqual([r["id"] for r in self.rows("type='message' AND source='box'")], ["m11011200-00000000000d"])
        self.assertEqual(len(self.refusals()), 7)
        self.assertEqual(sorted(p.name for p in self.outbox().iterdir()), [".9-being-written.json"])
        self.assertTrue(secret.exists(), "the link was removed, not what it points to")

    def test_merge_events_go_out_as_the_project_only(self):
        self.hubbus("publish", "merge.done", "--source", "box", "--to", "box", "--id", "merge-box-w-0123456789",
                    "--payload", json.dumps({"project": "box", "branch": "w"}), cwd=self.ws["box"])
        r = self.hubbus("publish", "job.failed", "--source", "box", "--to", "box", "--payload", "{}",
                        cwd=self.ws["box"], check=False)
        self.assertNotEqual(r.returncode, 0)
        self.drop({"v": 1, "op": "publish", "type": "config.applied", "id": "x", "payload": {}}, name="z.json")
        self.dispatch()
        got = [(r["id"], r["type"], r["source"], r["to_project"]) for r in self.rows("type!='message'")]
        self.assertEqual(got, [("outbox:box:merge-box-w-0123456789", "merge.done", "box", "box")])
        self.assertEqual(len(self.refusals()), 1)

    def test_hooks_deliver_a_sandboxed_projects_mail_from_the_bus(self):
        """The hooks run outside the sandbox: they read the bus, record presence and delivery, by the folder's path."""
        self.letter_to("box", "for box")
        wt = self.ws["box"] / ".claude" / "worktrees" / "w"
        wt.mkdir(parents=True)
        (wt / ".git").write_text(f"gitdir: {self.ws['box'] / '.git' / 'worktrees' / 'w'}\n")
        m = load_hubbus()
        m.DB = self.tmp / "bus" / "bus.db"
        with mock.patch.object(m.hubconf, "projects", lambda: self.docs):
            text = m.mail_text("session-start", "s1", str(wt)) or ""
        self.assertIn("for box", text)
        self.assertEqual(len(self.rows("type='message.delivered'")), 1)

    @unittest.skipUnless(seatbelt.can_sandbox() is None, "needs Seatbelt")
    def test_send_and_inbox_work_inside_the_real_sandbox(self):
        """The CLI inside the project's own Seatbelt profile: the bus directory is unreadable there, and the CLI never
        needs it."""
        self.hubbus("send", "--to", "box", "--type", "info", "--text", "for box", cwd=self.ws["open"])
        self.dispatch()
        r = self.in_sandbox("send", "--to", "open", "--type", "info", "--text", "from inside")
        self.assertEqual(r.returncode, 0, r.stderr)
        r2 = self.in_sandbox("inbox")
        self.assertIn("for box", r2.stdout, r2.stderr)
        self.dispatch()
        self.assertEqual([r["source"] for r in self.rows("type='message' AND to_project='open'")], ["box"])

    def in_box(self, *args, session="s-box", live=()):
        """One mail command as a session of the sandboxed project, in-process (the fixture's runtime has no sessions),
        reading its view and writing its outbox."""
        import contextlib
        import io
        import runtime_ports
        m = load_hubbus()
        m.DB = self.tmp / "bus-not-for-the-sandbox.db"
        out = io.StringIO()
        with mock.patch.object(m.hubconf, "projects", lambda: self.docs), \
                mock.patch.object(m.hubconf, "workspace", lambda p: self.ws.get(p)), \
                mock.patch.object(m, "project_of", lambda cwd: "box"), \
                mock.patch.object(runtime_ports, "current_session", lambda env=None: session), \
                mock.patch.object(sys, "argv", ["hubbus", *args]), contextlib.redirect_stdout(out):
            m.main()
        self.assertFalse(m.DB.exists(), "the sandbox never reaches for the bus")
        return out.getvalue()

    def on_bus(self, live=("s-box", "s-other")):
        """The platform's side, in-process, with a fake table of running sessions."""
        import types
        m = load_hubbus()
        m.DB = self.tmp / "bus" / "bus.db"
        m.sessions_port = lambda: types.SimpleNamespace(live_sessions=lambda: set(live))
        return m

    def view(self):
        return json.loads((names.repo_dir(self.ws["box"] / ".git") / "inbox.json").read_text())

    def test_a_view_carries_who_holds_a_letter_and_whether_it_is_taken(self):
        mid = self.letter_id(self.hubbus("send", "--to", "box", "--type", "request", "--text", "ping", cwd=self.ws["open"]).stdout)
        m = self.on_bus()
        with mock.patch.object(m.hubconf, "projects", lambda: self.docs), \
                mock.patch.object(m.hubconf, "workspace", lambda p: self.ws.get(p)):
            self.assertIn("ping", m.mail_text("prompt", "s-box", str(self.ws["box"])) or "")
        self.assertEqual(self.view()["state"], {mid: {"taken": False, "held": True, "holder": "s-box"}},
                         "written by the hook, so the session's next command finds it")
        self.assertTrue(any(e["type"] == "message.routed" for e in self.view()["events"]))
        out = self.in_box("inbox")
        self.assertIn(m._("  (held by this session)"), out)
        self.assertIn(m._("  (held by session {session})", session="s-box"), self.in_box("inbox", session="s-other"))
        c = m.db()
        self.addCleanup(c.close)
        m.publish(c, f"{mid}:ack:s-box", "message.delivered", "box", "box", mid, {"session": "s-box", "via": "ack"})
        with mock.patch.object(m.hubconf, "projects", lambda: self.docs):
            m.write_view(c, "box", self.ws["box"])
        self.assertEqual(self.view()["state"][mid]["taken"], True)
        self.assertIn(m._("  (taken)"), self.in_box("inbox"))
        m = self.on_bus(live=())
        c.execute("UPDATE presence SET seen='2026-01-01T00:00:00+08:00' WHERE session='s-box'")
        with mock.patch.object(m.hubconf, "projects", lambda: self.docs):
            m.write_view(c, "box", self.ws["box"])
        self.assertEqual(self.view()["state"], {}, "the holder closed: its ack and its route ended, derived on the bus")
        self.assertNotIn(m._("  (taken)"), self.in_box("inbox"))

    def test_a_view_from_before_has_no_state_and_reads_as_free(self):
        self.letter_to("box", "ping", typ="request")
        self.dispatch()
        f = names.repo_dir(self.ws["box"] / ".git") / "inbox.json"
        v = json.loads(f.read_text())
        v.pop("state", None)
        f.write_text(json.dumps(v))
        self.assertIn("ping", self.in_box("inbox"))

    def test_a_sandboxed_sessions_inbox_tells_it_through_the_outbox(self):
        mid = self.letter_id(self.hubbus("send", "--to", "box", "--type", "request", "--text", "ping", cwd=self.ws["open"]).stdout)
        self.dispatch()
        m = self.on_bus()
        with mock.patch.object(m.hubconf, "projects", lambda: self.docs), \
                mock.patch.object(m.hubconf, "workspace", lambda p: self.ws.get(p)):
            m.mail_text("post-tool", "s-seen", str(self.ws["open"]))   # presence elsewhere isn't this project's
        c = m.db()
        self.addCleanup(c.close)
        m.record_presence(c, "s-box", "box", self.ws["box"])
        self.in_box("inbox")
        self.assertEqual(self.rows("type='message.delivered' AND ref=?", (mid,)), [], "only into the outbox yet")
        files = list(self.outbox().iterdir())
        self.assertEqual([json.loads(f.read_text())["op"] for f in files], ["told"])
        self.in_box("inbox", session=None)                             # no session named: nobody to tell
        self.assertEqual(len(list(self.outbox().iterdir())), 1)
        with mock.patch.object(m.hubconf, "projects", lambda: self.docs):
            m.take_outboxes(c)
        self.assertEqual(self.refusals(), [])
        self.assertEqual([(json.loads(r["payload"])["session"], json.loads(r["payload"])["via"])
                          for r in self.rows("type='message.delivered' AND ref=?", (mid,))], [("s-box", "inbox")])
        self.assertEqual([json.loads(r["payload"]) for r in self.rows("type='message.routed' AND ref=?", (mid,))],
                         [{"v": 1, "session": "s-box", "by": "hook"}])

    def a_record_to(self, m, c, project="box"):
        m.publish(c, "rec:acts:box:r1", "record", "open", project, None, {"record": {
            "ph": "0.6", "ext": "acts/0.6", "id": "r1", "type": "settled", "project": project, "key": "k1"}})
        return "rec:acts:box:r1"

    def test_a_told_file_naming_a_notice_or_a_record_does_not_jam_the_outbox(self):
        """get_event's rows have no delivered column: a told file with a notice raised past the per-file except, the file
        was never deleted, and every letter after it, and every project after this one, stayed where it was."""
        notice = self.letter_id(self.hubbus("send", "--to", "box", "--type", "info", "--text", "n", cwd=self.ws["open"]).stdout)
        req = self.letter_id(self.hubbus("send", "--to", "box", "--type", "request", "--text", "r", cwd=self.ws["open"]).stdout)
        m = self.on_bus()
        c = m.db()
        self.addCleanup(c.close)
        rec = self.a_record_to(m, c)
        m.record_presence(c, "s-box", "box", self.ws["box"])
        self.drop({"v": 1, "op": "told", "ids": [notice, rec, req], "session": "s-box"}, name="1.json")
        self.drop({"v": 1, "op": "send", "id": "m11011200-000000000009", "to": "open", "type": "info", "text": "after"},
                  name="2.json")
        with mock.patch.object(m.hubconf, "projects", lambda: self.docs):
            m.take_outboxes(c)
        self.assertEqual(list(self.outbox().iterdir()), [], "both files taken in and deleted")
        self.assertEqual(self.refusals(), [])
        self.assertEqual(len(self.rows("id='m11011200-000000000009'")), 1, "the letter after it went out")
        self.assertEqual(self.rows("type='message.delivered' AND ref IN (?,?)", (notice, rec)), [],
                         "a notice's flag is its hooks', as before")
        self.assertEqual([json.loads(r["payload"])["session"] for r in self.rows("type='message.delivered' AND ref=?", (req,))],
                         ["s-box"])

    def test_a_sandboxed_inbox_sends_only_requests_and_questions_in_told(self):
        notice = self.letter_id(self.hubbus("send", "--to", "box", "--type", "info", "--text", "n", cwd=self.ws["open"]).stdout)
        req = self.letter_id(self.hubbus("send", "--to", "box", "--type", "question", "--text", "q", cwd=self.ws["open"]).stdout)
        m = self.on_bus()
        c = m.db()
        self.addCleanup(c.close)
        self.a_record_to(m, c)
        with mock.patch.object(m.hubconf, "projects", lambda: self.docs):
            m.write_view(c, "box", self.ws["box"])
        out = self.in_box("inbox")
        self.assertIn(notice, out)
        entries = [json.loads(f.read_text()) for f in self.outbox().iterdir()]
        self.assertEqual([(e["op"], e["ids"]) for e in entries], [("told", [req])])

    def test_any_failure_on_one_file_stops_neither_its_outbox_nor_the_next_project(self):
        docs = {**self.docs, "zbox": {"v": 1, "name": "zbox", "paths": {"workspace": str(self.tmp / "ws" / "zbox")},
                                      "enforce": {"sandbox": True}}}
        (self.tmp / "ws" / "zbox").mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.tmp / "ws" / "zbox")], check=True, env={**os.environ, **GIT_ENV})
        self.ws["zbox"] = self.tmp / "ws" / "zbox"
        self.drop({"v": 1, "op": "send", "id": "m11011200-00000000000a", "to": "open", "type": "info", "text": "bad"}, name="1.json")
        self.drop({"v": 1, "op": "send", "id": "m11011200-00000000000b", "to": "open", "type": "info", "text": "good"}, name="2.json")
        self.drop({"v": 1, "op": "send", "id": "m11011200-00000000000c", "to": "open", "type": "info", "text": "next"},
                  name="1.json", project="zbox")
        m = self.on_bus()
        c = m.db()
        self.addCleanup(c.close)
        real = m.take

        def take(c_, proj, entry, *a):
            if entry.get("text") == "bad":
                raise IndexError("No item with that key")
            return real(c_, proj, entry, *a)

        m.take = take
        with mock.patch.object(m.hubconf, "projects", lambda: docs), \
                mock.patch.object(m.hubconf, "workspace", lambda p: self.ws.get(p)):
            m.take_outboxes(c)
        self.assertEqual(self.rows("id='m11011200-00000000000a'"), [])
        self.assertEqual(len(self.rows("id='m11011200-00000000000b'")), 1)
        self.assertEqual(len(self.rows("id='m11011200-00000000000c'")), 1)
        self.assertEqual(list(self.outbox().iterdir()), [], "the bad file is deleted too")
        self.assertEqual(len(self.refusals()), 1, "and reported to the project")
        self.assertFalse(c.in_transaction)

    def test_a_busy_bus_leaves_the_file_for_the_next_run(self):
        import sqlite3
        self.drop({"v": 1, "op": "send", "id": "m11011200-00000000000d", "to": "open", "type": "info", "text": "wait"})
        m = self.on_bus()
        c = m.db()
        self.addCleanup(c.close)
        with mock.patch.object(m.hubconf, "projects", lambda: self.docs), \
                mock.patch.object(m, "take", mock.Mock(side_effect=sqlite3.OperationalError("database is locked"))):
            with self.assertRaises(sqlite3.OperationalError):
                m.take_outboxes(c)
        self.assertEqual(len(list(self.outbox().iterdir())), 1)
        self.assertFalse(c.in_transaction)

    def test_a_forks_reply_and_close_through_the_outbox_say_it_was_the_fork(self):
        mid = self.letter_id(self.hubbus("send", "--to", "box", "--type", "request", "--text", "ping", cwd=self.ws["open"]).stdout)
        m = self.on_bus()
        c = m.db()
        self.addCleanup(c.close)
        with mock.patch.object(m.hubconf, "projects", lambda: self.docs):
            m.write_view(c, "box", self.ws["box"])
        with mock.patch.dict(os.environ, {"HUB_LLM_POINT": m.FORK_POINT}):
            self.in_box("send", "--to", "open", "--reply-to", mid, "--type", "done", "--text", "pong", session="F1")
            self.in_box("done", mid, session="F1")
        with mock.patch.object(m.hubconf, "projects", lambda: self.docs):
            m.take_outboxes(c)
        self.assertEqual(self.refusals(), [])
        self.assertEqual([json.loads(r["payload"]).get("point") for r in self.rows("ref=? AND type IN ('message','message.done')", (mid,))],
                         [m.FORK_POINT, m.FORK_POINT])

    def test_a_platform_run_in_a_sandboxed_project_writes_no_told(self):
        self.letter_to("box", "ping", typ="request")
        self.dispatch()
        with mock.patch.dict(os.environ, {"HUB_LLM_POINT": "mailbox-escalation"}):
            self.in_box("inbox")
        self.assertFalse(self.outbox().exists() and list(self.outbox().iterdir()))

    def test_a_told_entry_is_checked_like_the_command(self):
        mine = self.letter_id(self.hubbus("send", "--to", "box", "--type", "request", "--text", "a", cwd=self.ws["open"]).stdout)
        theirs = self.letter_id(self.hubbus("send", "--to", "open", "--type", "request", "--text", "b", cwd=self.ws["open"]).stdout)
        m = self.on_bus()
        c = m.db()
        self.addCleanup(c.close)
        m.record_presence(c, "s-box", "box", self.ws["box"])
        m.record_presence(c, "s-open", "open", self.ws["open"])
        self.drop({"v": 1, "op": "told", "ids": [theirs], "session": "s-box"}, name="1.json")
        self.drop({"v": 1, "op": "told", "ids": "x", "session": "s-box"}, name="2.json")
        self.drop({"v": 1, "op": "told", "ids": [mine], "session": "s-open"}, name="3.json")  # not this project's session
        self.drop({"v": 1, "op": "told", "ids": [mine], "session": "made-up"}, name="4.json")
        with mock.patch.object(m.hubconf, "projects", lambda: self.docs):
            m.take_outboxes(c)
        self.assertEqual(len(self.refusals()), 2)
        self.assertEqual(self.rows("type IN ('message.delivered','message.routed')"), [])

    def test_mcp_server_in_a_sandboxed_project(self):
        self.hubbus("send", "--to", "box", "--type", "info", "--text", "for box", cwd=self.ws["open"])
        self.dispatch()
        msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "inbox", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                    "name": "send", "arguments": {"to": "open", "type": "info", "text": "via mcp"}}}]
        shutil.move(self.tmp / "bus", self.tmp / "bus-away")
        r = subprocess.run([sys.executable, str(BIN / "mcp_server.py")], input="".join(json.dumps(m) + "\n" for m in msgs),
                           env=self.env, cwd=self.ws["box"], capture_output=True, text=True, timeout=60)
        out = {m["id"]: m for m in map(json.loads, r.stdout.splitlines())}
        self.assertIn("📬 1", out[0]["result"]["instructions"])
        self.assertIn("for box", out[1]["result"]["content"][0]["text"])
        self.assertFalse(out[2]["result"]["isError"], out[2])
        self.assertFalse((self.tmp / "bus").exists())
        shutil.move(self.tmp / "bus-away", self.tmp / "bus")
        self.dispatch()
        self.assertEqual([r["source"] for r in self.rows("type='message' AND to_project='open'")], ["box"])


class McpPresence(Fixture):
    """docs/design/mailbox-runtimes.md step 1 (A3): a sandboxed project's MCP server records presence through its
    outbox (op presence), taken in only while that server process runs; its notice counts from the view."""

    def presence(self):
        db = self.tmp / "bus" / "bus.db"
        c = sqlite3.connect(db)
        try:
            return c.execute("SELECT session, project, runtime, via FROM presence").fetchall()
        finally:
            c.close()

    def test_presence_goes_through_the_outbox_and_counts_only_while_the_server_runs(self):
        self.letter_to("box", "for box")
        self.dispatch()   # the view now has the letter
        p = subprocess.Popen([sys.executable, str(BIN / "mcp_server.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env=self.env, cwd=self.ws["box"], text=True)
        self.addCleanup(p.stdout.close)
        self.addCleanup(p.kill)

        def rpc(i, method, params):
            p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n")
            p.stdin.flush()
            return json.loads(p.stdout.readline())

        rpc(0, "initialize", {"clientInfo": {"name": "codex"}})
        text = rpc(1, "tools/call", {"name": "show", "arguments": {"id": "nothing"}})["result"]["content"][0]["text"]
        self.assertIn("📬 1", text, "the count comes from the view")
        ops = sorted(json.loads(f.read_text())["op"] for f in self.outbox().iterdir())
        self.assertEqual(ops, ["presence", "presence"])
        self.assertFalse(any("presence" in str(r) for r in self.rows()), "nothing written to the bus by the server")
        self.dispatch()
        rows = self.presence()
        self.assertEqual([r[1:] for r in rows], [("box", "mcp:codex", "tool")])
        self.assertRegex(rows[0][0], rf"^mcp:{p.pid}:\d+$")
        self.assertEqual(self.refusals(), [])
        p.stdin.close()
        p.wait(timeout=30)
        self.drop({"v": 1, "op": "presence", "session": rows[0][0], "cwd": str(self.ws["box"]), "via": "tool",
                   "runtime": "mcp:codex"}, name="late.json")
        self.hubbus("dispatch")
        self.assertEqual(self.refusals(), [], "a server that has exited since is dropped quietly")


class Policy(Fixture):
    def test_the_bus_is_in_no_sandbox_of_a_sandboxed_project(self):
        import policy
        import runtime_claude
        with mock.patch.object(hubconf, "BUS_DIR", self.tmp / "bus"):
            box = policy.policy("box", self.docs["box"], {"projects": self.docs})
            opn = policy.policy("open", self.docs["open"], {"projects": self.docs})
            sandbox, deny, _allow = runtime_claude.settings(box)
        bus = policy.tilde(self.tmp / "bus")
        self.assertNotIn(bus, box["write"])
        self.assertIn(self.tmp / "bus", box["deny_read"])
        self.assertIn(bus, sandbox["filesystem"]["denyRead"])
        self.assertNotIn(bus, sandbox["filesystem"]["allowWrite"])
        self.assertIn(bus, opn["write"], "nothing changes for a project that isn't sandboxed")


class Routing(Fixture):
    def test_platform_processes_in_a_project_folder_still_reach_the_bus(self):
        """A job's llm.py runs in the project's folder and publishes job.failed as itself: not the project's outbox."""
        self.hubbus("publish", "job.failed", "--source", "llm", "--payload", json.dumps({"job": "llm:x"}),
                    cwd=self.ws["box"])
        self.hubbus("send", "--from", "agent-hub", "--to", "box", "--type", "info", "--text", "from the platform",
                    cwd=self.ws["box"])
        self.assertEqual([(r["type"], r["source"]) for r in self.rows()],
                         [("job.failed", "llm"), ("message", "agent-hub")])
        self.assertFalse(self.outbox().exists())

    def test_a_letter_the_hook_delivered_can_be_answered_at_once(self):
        """The hook delivers from the bus; the view is written right there, not only at the next dispatch."""
        req = self.letter_id(self.hubbus("send", "--to", "box", "--type", "request", "--text", "ping",
                                         cwd=self.ws["open"]).stdout)
        m = load_hubbus()
        m.DB = self.tmp / "bus" / "bus.db"
        with mock.patch.object(m.hubconf, "projects", lambda: self.docs):
            self.assertIn("ping", m.mail_text("prompt", "s1", str(self.ws["box"])) or "")
        self.hubbus("send", "--to", "open", "--type", "done", "--reply-to", req, "--text", "pong", cwd=self.ws["box"])
        self.hubbus("done", req, cwd=self.ws["box"])

    def test_the_outbox_is_never_written_through_a_link(self):
        """Whoever runs it (a user's terminal in the folder has the user's authority)."""
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        names.repo_dir(self.ws["box"] / ".git").mkdir(parents=True)
        self.outbox().symlink_to(elsewhere)
        r = self.hubbus("send", "--to", "open", "--type", "info", "--text", "x", cwd=self.ws["box"], check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_a_task_from_an_outbox_keeps_its_texts_key(self):
        """With records on, a task's key is its text's (task_key): the letter id the outbox picks is not a key."""
        cfg = json.loads((self.tmp / "config" / "hub.json").read_text())
        cfg["mailbox"] = {"records": True}
        (self.tmp / "config" / "hub.json").write_text(json.dumps(cfg))
        self.hubbus("send", "--to", "owner", "--type", "task", "--text", "renew the token", cwd=self.ws["box"])
        self.hubbus("send", "--to", "owner", "--type", "task", "--text", "renew  the token", cwd=self.ws["box"])
        self.dispatch()
        m = load_hubbus()
        recs = [json.loads(r["payload"])["record"] for r in self.rows("type='record'")]
        self.assertEqual([(r["type"], r["key"], r["project"]) for r in recs],
                         [("raised", m.task_key("box", "renew the token"), "box")])
        self.assertEqual(self.refusals(), [])
