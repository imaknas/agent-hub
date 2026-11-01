"""Records of a renamed project: the ones filed under a name it had before (.state/project-renames.json) stay on the bus
as they were sent (their project field, the event's source or recipient), and are read as this project's.

  hubbus records --project <new>   lists them, each in its direction; a former name another project now uses is that
                                   project's
  record-publish                   takes a record about an item filed under a former name (its project field kept),
                                   on the event under the current name
  open_questions                   a question asked under a former name is this project's (shown under the current
                                   name, answered under the name it was asked under)
  the registrar                    an item raised under a former name is bound or known as before: its line is not
                                   raised again, a settlement strikes it, an edit is amended, a vanished line claimed;
                                   what it sends about such an item carries the item's project field

Isolated bus, registry and memory repos; never touches real data.

    bin/py -m unittest tests.test_records_renamed
"""
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_registrar_flow import OWNER, PRINCIPAL, SIDE, Env, agent, key, marker, owner, state  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
TS = "2026-10-20T10:00:00Z"
RENAMES = {"proj-old": {"new": "proj", "stage": "done"},
           "reused": {"new": "proj", "stage": "done"},        # a former name another project took later
           "half": {"new": "proj", "stage": "github"}}         # a rename not finished: not a former name yet


def load():
    loader = importlib.machinery.SourceFileLoader("hubbus_renamed", str(ROOT / "bin" / "hubbus"))
    spec = importlib.util.spec_from_loader("hubbus_renamed", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def rec(project, rid, type_, k, ext="acts/0.3", **kw):
    base = {"ph": ext.split("/")[1], "ext": ext, "id": rid, "type": type_, "key": k, "project": project, "ts": TS}
    if type_ in ("settled", "reopened"):
        base |= {"origin": "principal", "by": SIDE}
    else:
        base |= {"origin": "requester", "by": f"project:{project}"}
    return {**base, **kw}


def insert(db, source, to, r, ts="2026-10-20T18:00:00+08:00"):
    """a record event as the bus held it (written before the rename, under the name of then)"""
    c = sqlite3.connect(db)
    c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
              (f"rec:{r['ext']}:{r['project']}:{r['id']}", ts, "record", source, to, None,
               json.dumps({"record": r}, ensure_ascii=False)))
    c.commit()
    c.close()


class Bus(unittest.TestCase):
    def setUp(self):
        m = self.m = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        m.DB = self.tmp / "bus.db"
        m.log = lambda s: None
        m.projects = lambda: {"proj": self.tmp, "reused": self.tmp, "other": self.tmp}
        st = self.tmp / "state"
        st.mkdir()
        (st / "project-renames.json").write_text(json.dumps(RENAMES))
        patch = mock.patch.object(m.hubconf, "STATE", st)   # hubconf is a shared module: always swap it back
        patch.start()
        self.addCleanup(patch.stop)
        self.c = m.db()
        m.db = lambda *a, **k: self.c
        self.owner = m.hubconf.owner_address()

    def cli(self, *argv, stdin=""):
        with mock.patch.object(sys, "argv", ["hubbus", *argv]), mock.patch.object(sys, "stdin", io.StringIO(stdin)), \
                redirect_stdout(io.StringIO()) as out:
            try:
                self.m.main()
            except SystemExit:
                pass
        return out.getvalue().strip()

    def records(self, project="proj"):
        return [(e["direction"], e["record"]["project"], e["record"]["id"])
                for e in json.loads(self.cli("records", "--project", project))]

    def test_records_filed_under_a_former_name(self):
        insert(self.m.DB, "proj-old", self.owner, rec("proj-old", "r:k1", "raised", "k1", terms={"text": "a"},
                                                      terms_rev=1))
        insert(self.m.DB, self.owner, "proj-old", rec("proj-old", "s:k1", "settled", "k1", outcome="done", terms_rev=1,
                                                      authority={"kind": "principal", "decider": PRINCIPAL}))
        # pulled after the rename: the assistant still sends under the former name, the bridge writes it to the new one
        insert(self.m.DB, self.owner, "proj", rec("proj-old", "o:k1", "reopened", "k1", restore_text="a"))
        insert(self.m.DB, "reused", self.owner, rec("reused", "r:k2", "raised", "k2", terms={"text": "b"}, terms_rev=1))
        insert(self.m.DB, "half", self.owner, rec("half", "r:k3", "raised", "k3", terms={"text": "c"}, terms_rev=1))
        self.assertEqual(self.records(), [("out", "proj-old", "r:k1"), ("in", "proj-old", "s:k1"),
                                          ("in", "proj-old", "o:k1")])
        self.assertEqual(self.records("reused"), [("out", "reused", "r:k2")])

    def test_record_publish_about_an_item_filed_under_a_former_name(self):
        claim = rec("proj-old", "c:k1:1", "claimed", "k1", origin="agent", outcome="done", evidence={"basis": "paid"})
        out = json.loads(self.cli("record-publish", "--json", "-", stdin=json.dumps([claim])))
        self.assertEqual(out, [{"id": "c:k1:1", "event": "rec:acts/0.3:proj-old:c:k1:1", "status": "accepted"}])
        ev = self.c.execute("SELECT * FROM events WHERE id=?", (out[0]["event"],)).fetchone()
        self.assertEqual((ev["source"], self.m.payload(ev)["record"]["project"]), ("proj", "proj-old"))
        half = rec("half", "c:k3:1", "claimed", "k3", origin="agent", outcome="done", evidence={"basis": "x"})
        self.assertEqual(json.loads(self.cli("record-publish", "--json", "-", stdin=json.dumps([half])))[0]["status"],
                         "invalid", "not a former name until the rename is done")

    def test_a_question_asked_under_a_former_name(self):
        q = rec("proj-old", "r:q-1", "raised", "q-1", ext="decisions/0.3", terms={"text": "Which?"}, terms_rev=1)
        insert(self.m.DB, "proj-old", self.owner, q)
        self.assertEqual([(x["id"], x["project"]) for x in self.m.open_questions(self.c, "proj")], [("q-1", "proj")])
        self.assertEqual([x["id"] for x in self.m.open_questions(self.c, "reused")], [])
        asked = self.m.question_asked(self.c, "q-1")
        self.assertEqual((asked["project"], asked["filed"]), ("proj", "proj-old"))
        self.assertEqual(self.m.reconcile_questions(self.c, now=self.m.now().replace(year=2026, month=10, day=24)),
                         ["qcheck-q-1-0"])
        letter = self.c.execute("SELECT * FROM events WHERE id='qcheck-q-1-0'").fetchone()
        self.assertEqual(letter["to_project"], "proj", "asked whether still needed under the current name")


# The registrar: project rn, called rn-old before; its items were raised under rn-old.
#   K1  line unchanged                    → nothing sent, the line kept as it is
#   K2  settled (to rn-old, before)       → the line struck
#   K3  line edited                       → amended, project rn-old
#   K4  no line                           → claimed done, project rn-old
#   K5  raised by the project itself      → known: its line is not raised, warned about
K1, K2 = key("rn-old", "Renew the permit"), key("rn-old", "Pay the deposit")
K3, K4 = key("rn-old", "Call the plumber"), key("rn-old", "Return the keys")
K5 = "ask-rn-old-0000000005"
SCENES = {"rn": {"state": state(owner("Renew the permit") + marker(K1), owner("Pay the deposit") + marker(K2),
                                owner("Call the plumber twice") + marker(K3), owner("Water the lawn") + marker(K5),
                                agent("Other"))}}
REGISTRAR = "agent-hub:registrar"


class Registrar(unittest.TestCase):
    def env(self, d):
        env = Env(pathlib.Path(d), SCENES)
        env.env["AGENT_HUB_LANG_UI"] = "en"
        (env.hub / ".state" / "project-renames.json").write_text(json.dumps({"rn-old": {"new": "rn", "stage": "done"}}))
        for k, text in ((K1, "Renew the permit"), (K2, "Pay the deposit"), (K3, "Call the plumber"),
                        (K4, "Return the keys")):
            insert(env.db, "rn-old", OWNER, rec("rn-old", f"r:{k}", "raised", k, by=REGISTRAR, terms={"text": text},
                                                terms_rev=1))
        insert(env.db, "rn-old", OWNER, rec("rn-old", f"r:{K5}", "raised", K5, terms={"text": "Water the lawn"},
                                            terms_rev=1))
        insert(env.db, OWNER, "rn-old", rec("rn-old", "s-k2", "settled", K2, outcome="done", terms_rev=1,
                                            authority={"kind": "principal", "decider": PRINCIPAL}))
        return env

    def sent(self, env):
        """what the registrar sent: (type, key, project field)"""
        r = env.hubbus("records", "--project", "rn", "--direction", "out")
        return sorted((e["record"]["type"], e["record"]["key"], e["record"]["project"]) for e in json.loads(r.stdout)
                      if e["record"]["ts"] >= "2026-10-27")

    def test_items_raised_under_the_former_name(self):
        with tempfile.TemporaryDirectory() as d:
            env = self.env(d)
            r = env.run()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(self.sent(env), [("amended", K3, "rn-old"), ("claimed", K4, "rn-old")])
            st = env.state("rn")
            self.assertIn(owner("Renew the permit") + marker(K1), st)
            self.assertNotIn("Pay the deposit", st)
            self.assertIn(owner("Water the lawn") + marker(K5), st)
            self.assertIn(f"rn: warning — line 11 carries {K5}", r.stdout + r.stderr)
            r = env.run()   # the second night sends nothing new
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(self.sent(env), [("amended", K3, "rn-old"), ("claimed", K4, "rn-old")])


if __name__ == "__main__":
    unittest.main()
