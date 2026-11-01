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
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("AGENT_HUB_", "AGENTHUB_", "HUB_"))
                       or k == "AGENT_HUB_PYTHON"}, **GIT_ENV,
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
        return self.ws[name] / ".git" / "agent-hub" / "outbox"

    def drop(self, body, name="m11011200-0123456789ab.json", project="box"):
        """What a sandboxed project can always do: write any file into its own outbox."""
        self.outbox(project).mkdir(parents=True, exist_ok=True)
        f = self.outbox(project) / name
        f.write_text(body if isinstance(body, str) else json.dumps(body))
        return f

    def letter_to(self, project, text, frm="agent-hub", typ="info"):
        self.hubbus("send", "--from", frm, "--to", project, "--type", typ, "--text", text)

    def refusals(self, project="box"):
        return [json.loads(r["payload"])["text"] for r in self.rows("type='message' AND to_project=? AND source=?",
                                                                    (project, "agent-hub"))
                if json.loads(r["payload"]).get("outbox_refused")]


class Attacks(Fixture):
    """Each of these succeeded before the outbox (11-01)."""

    def test_send_from_another_project_never_lands_as_it(self):
        r = self.hubbus("send", "--from", "open", "--to", "agent-hub", "--type", "manifest.update", "--text", MANIFEST,
                        cwd=self.ws["box"], check=False)
        self.dispatch()
        self.assertEqual(self.rows("source='open'"), [], r.stdout + r.stderr)

    def test_a_forged_sender_in_the_outbox_is_ignored(self):
        self.drop({"v": 1, "op": "send", "id": "m11011200-0123456789ab", "from": "open", "source": "open",
                   "sender": "open", "to": "agent-hub", "type": "manifest.update", "text": MANIFEST})
        self.dispatch()
        self.assertEqual(self.rows("source='open'"), [])
        got = self.rows("type='message' AND to_project='agent-hub'")
        self.assertEqual([r["source"] for r in got], ["box"])
        self.assertFalse(any(self.outbox().iterdir()), "a moved letter is deleted from the outbox")

    def test_cli_does_not_read_another_projects_mailbox(self):
        self.letter_to("open", "the open project's secret plan")
        r = self.hubbus("inbox", "--project", "open", cwd=self.ws["box"], check=False)
        self.assertNotIn("secret plan", r.stdout + r.stderr)

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

    def profile(self, name):
        """The real Seatbelt profile of a sandboxed project's service, for this scratch instance."""
        import policy
        with mock.patch.object(hubconf, "BUS_DIR", self.tmp / "bus"), \
                mock.patch.object(hubconf, "projects", lambda: self.docs), \
                mock.patch.object(seatbelt, "TMP", []):   # the fixture is in the temp area
            pol = policy.policy(name, self.docs[name], {"projects": self.docs})
            return seatbelt.service_profile(pol, [self.ws[name]])


class Outbox(Fixture):
    def letter_id(self, out: str) -> str:
        return out.strip().splitlines()[-1]

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
        view = json.loads((self.ws["box"] / ".git" / "agent-hub" / "inbox.json").read_text())
        self.assertEqual({e["to_project"] for e in view["events"]}, {"box"})
        shutil.move(self.tmp / "bus", self.tmp / "bus-away")   # the sandbox has no bus: reading never needs it
        r = self.hubbus("inbox", cwd=self.ws["box"])
        self.assertIn("for box", r.stdout)
        self.assertNotIn("secret plan", r.stdout)
        self.assertFalse((self.tmp / "bus").exists(), "reading the view created the bus")
        r = self.hubbus("inbox", "--project", "open", cwd=self.ws["box"], check=False)
        self.assertNotEqual(r.returncode, 0)

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
        r = self.hubbus("publish", "merge.done", "--source", "open", "--to", "open", "--payload", "{}",
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
        profile = Attacks.profile(self, "box")
        r = subprocess.run([seatbelt.SANDBOX_EXEC, "-p", profile, sys.executable, str(BIN / "hubbus"), "send", "--to",
                            "open", "--type", "info", "--text", "from inside"], env=self.env, cwd=self.ws["box"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        r2 = subprocess.run([seatbelt.SANDBOX_EXEC, "-p", profile, sys.executable, str(BIN / "hubbus"), "inbox"],
                            env=self.env, cwd=self.ws["box"], capture_output=True, text=True, timeout=60)
        self.assertIn("for box", r2.stdout, r2.stderr)
        self.dispatch()
        self.assertEqual([r["source"] for r in self.rows("type='message' AND to_project='open'")], ["box"])

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
