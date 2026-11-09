"""A question to the owner with options and a default (hubbus send --option/--default, the MCP tool ask_owner): Decisions
terms options [{id, label}] and default (decisions §2, §3), in every version brunna sends; the default is what the
asking project proceeds on while it waits, never an answer (core R4). A question that goes as a letter carries them in
its text. Asked by agent-ops-specs 11-09: none of the questions since 11-01 recorded what the agent assumed. Scratch
config, registry and bus only.

    bin/py -m unittest tests.test_question_terms
"""
import contextlib
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "bin"))
from test_bus_sender import Fixture  # noqa: E402
import ph_records  # noqa: E402
import ph_wire  # noqa: E402

VERSIONS = {"0.4": ["0.4", "0.5"], "0.5": ["0.4", "0.5"], "0.6": ["0.5", "0.6"]}


class Terms(Fixture):
    def config(self, **kw):
        p = self.tmp / "config" / "hub.json"
        p.write_text(json.dumps({**json.loads(p.read_text()), "owner": {"name": "alex", "tag": "Alex",
                                                                       "principal_id": "person:alex"}, **kw}))

    def records_on(self, version="0.6"):
        self.config(mailbox={"records": True},
                    principal_handoff={"send_version": version, "principal_side_reads": VERSIONS[version]})

    def ask(self, *extra, frm="open", check=True):
        r = self.hubbus("send", "--to", "owner", "--type", "question", "--text", "Which cache for the export job?",
                        *extra, cwd=self.ws[frm], check=check)
        return r if not check else r.stdout.strip().split()[-1]

    def events(self, typ):
        with contextlib.closing(sqlite3.connect(self.tmp / "bus" / "bus.db")) as c:
            return [json.loads(p) for (p,) in c.execute("SELECT payload FROM events WHERE type=? ORDER BY seq", (typ,))]

    def raised(self):
        [rec] = [e["record"] for e in self.events("record") if e["record"]["type"] == "raised"]
        return rec

    def test_options_and_default_go_into_the_raised_terms_in_each_version(self):
        for version in VERSIONS:
            with self.subTest(version):
                self.setUp()
                self.records_on(version)
                self.ask("--option", "sqlite", "SQLite file next to the job", "--option", "redis", "Redis on the hub",
                         "--default", "sqlite")
                rec = self.raised()
                self.assertEqual(rec["terms"], {"text": "Which cache for the export job?",
                                                "options": [{"id": "sqlite", "label": "SQLite file next to the job"},
                                                            {"id": "redis", "label": "Redis on the hub"}],
                                                "default": "sqlite"})
                self.assertEqual(rec["ph"], version)
                cfg = ph_records.Config("person:alex", own=frozenset({"requester", "agent", "registrar"}))
                self.assertEqual(ph_records.validate(rec, cfg)[1], [])

    def test_a_default_may_be_text_with_or_without_options(self):
        self.records_on()
        self.ask("--default", "keep the column for now")
        self.assertEqual(self.raised()["terms"]["default"], "keep the column for now")

    def test_a_question_without_them_is_as_before(self):
        self.records_on()
        self.ask()
        self.assertEqual(self.raised()["terms"], {"text": "Which cache for the export job?"})

    def test_bad_options_are_refused_and_nothing_is_sent(self):
        self.records_on()
        for extra, said in ((["--option", "a", "One", "--option", "a", "Two"], "given twice"),
                            (["--option", "a b", "One"], "without spaces"),
                            (["--option", "x" * 65, "One"], "without spaces"),
                            (["--option", "a\n", "One"], "without spaces"),
                            (["--option", "a", " "], "needs a label"),
                            (["--default", " "], "an option id or a text")):
            with self.subTest(said):
                r = self.ask(*extra, check=False)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(said, r.stderr + r.stdout)
        self.assertEqual(self.events("record"), [])

    def test_only_for_a_question_to_the_owner(self):
        for args in (["--to", "box", "--type", "question"], ["--to", "owner", "--type", "task"],
                     ["--to", "owner", "--type", "request"]):
            with self.subTest(args):
                r = self.hubbus("send", *args, "--text", "x", "--default", "a", cwd=self.ws["open"], check=False)
                self.assertIn("only for a question to", r.stderr + r.stdout)

    def test_the_default_is_shown_and_never_closes_the_question(self):
        self.records_on()
        qid = self.ask("--option", "sqlite", "SQLite", "--option", "redis", "Redis", "--default", "sqlite")
        out = self.hubbus("open-questions").stdout
        self.assertIn(qid, out)
        self.assertIn("(until answered: sqlite)", out)
        [q] = json.loads(self.hubbus("open-questions", "--json").stdout)
        self.assertEqual((q["default"], [o["id"] for o in q["options"]]), ("sqlite", ["sqlite", "redis"]))
        r = subprocess.run([sys.executable, str(ROOT / "bin" / "owner.py"), "list"], env=self.env,
                                         cwd=self.tmp, capture_output=True, text=True, timeout=60)
        self.assertIn("(until answered: sqlite)", r.stdout, r.stderr)
        # the owner answers otherwise: the answer is what settles it, against the terms that carried the default
        r = subprocess.run([sys.executable, str(ROOT / "bin" / "owner.py"), "answer", qid, "Use Redis"],
                                         env=self.env, cwd=self.tmp, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        [st] = [e["record"] for e in self.events("record") if e["record"]["type"] == "settled"]
        self.assertEqual(st["answer"], {"text": "Use Redis"})
        self.assertEqual(st["terms_digest"], ph_wire.terms_digest(self.raised(), "0.6"))
        self.assertEqual(json.loads(self.hubbus("open-questions", "--json").stdout), [])

    def test_as_a_letter_they_go_in_its_text(self):
        """mailbox.records off: the question is a letter, and the assistant's letter has no terms."""
        qid = self.ask("--option", "sqlite", "SQLite", "--default", "sqlite")
        [m] = [p for p in self.events("message") if p.get("type") == "question"]
        self.assertEqual(m["text"], "Which cache for the export job?\n\nOptions: sqlite = SQLite\n"
                                    "Until answered, proceeding with: sqlite")
        [q] = json.loads(self.hubbus("open-questions", "--json").stdout)
        self.assertEqual((q["id"], q["text"], q["default"]), (qid, "Which cache for the export job?", "sqlite"))

    def test_a_sandboxed_project_sends_them_through_its_outbox(self):
        self.records_on()
        r = self.hubbus("send", "--to", "owner", "--type", "question", "--text", "Which cache?", "--option", "a",
                        "SQLite", "--default", "a", cwd=self.ws["box"])
        self.assertEqual(r.returncode, 0, r.stderr)
        [f] = (self.ws["box"] / ".git" / "brunna" / "outbox").iterdir()
        self.assertEqual(json.loads(f.read_text())["options"], [{"id": "a", "label": "SQLite"}])
        self.dispatch()
        terms = self.raised()["terms"]
        self.assertEqual((terms["options"], terms["default"]), ([{"id": "a", "label": "SQLite"}], "a"))

    def test_an_outbox_file_with_bad_options_is_refused(self):
        self.records_on()
        box = self.ws["box"] / ".git" / "brunna" / "outbox"
        box.mkdir(parents=True, exist_ok=True)
        (box / "1-abcd.json").write_text(json.dumps(
            {"v": 1, "op": "send", "id": "m11090900-000000000001", "to": "owner", "type": "question", "text": "Which?",
             "options": [{"id": "a", "label": "One", "extra": 1}]}))
        self.dispatch()
        self.assertEqual([e for e in self.events("record") if e["record"]["type"] == "raised"], [])
        told = [p["text"] for p in self.events("message") if "options must be" in p.get("text", "")]
        self.assertEqual(len(told), 1, self.events("message"))   # the project is told why

    def test_the_mcp_tool_passes_them(self):
        import mcp_server
        self.records_on()
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.object(mcp_server.hubconf, "workspace", lambda p: self.ws.get(p)), \
                mock.patch.object(mcp_server, "_hubbus", lambda: mock.Mock(outboxed=lambda p: False)):
            ok, out = mcp_server.call("open", "", "ask_owner", {
                "text": "Which cache?", "options": [{"id": "sqlite", "label": "SQLite"}, {"id": "redis", "label": "Redis"}],
                "default": "sqlite"})
            self.assertTrue(ok, out)
            ok, out = mcp_server.call("open", "", "ask_owner", {"text": "Which?", "options": "sqlite"})
            self.assertFalse(ok)
        terms = self.raised()["terms"]
        self.assertEqual((len(terms["options"]), terms["default"]), (2, "sqlite"))


if __name__ == "__main__":
    unittest.main()
