"""Owner to-dos that existed only as v1 task letters, after v1's retirement: `registrar.py migrate-v1` (removed with v1;
see docs/design/principal-handoff-0.3-migration.md) gave each one whose key a line mentions a need record under that
key (context migration-v1). With those records, the registrar does what it did while it still read the v1 letters,
and sends nothing for the migrated items; the letters themselves are no longer read. Runs the whole script, with its
own memory repos, bus and hub.json (test_registrar_flow's Env); never touches real data.

  ma  its line unchanged                               → migrated: raised, the line's text  → nothing sent
  mb  its line edited since the letter                 → migrated: raised, the line's text  → nothing sent
                                                         (or the letter's text raised + the line's amended)
  mc  the letter sent before a rename (mc-old), the    → migrated: raised under mc, the     → nothing sent
      line still carrying that key                       key kept
  md  its key on a line relabelled tbd                 → migrated: raised, the letter's     → a note (displaced),
                                                         text                                 no claim
  me  no line, v1 already sent its inferred closure    → not migrated                       → nothing sent
  mf  no line, no closure                              → not migrated (decided by hand)     → nothing sent (v1_compat
                                                                                              claimed it done)
  mg  already had a need record                        → skipped                            → nothing sent

    bin/py -m unittest tests.test_registrar_v1_migration
"""
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_registrar_flow import OWNER, TAG, Env, agent, key, marker, owner, state  # noqa: E402
import todo_tokens  # noqa: E402

REGISTRAR = "agent-hub:registrar"


def tbd(text: str) -> str:
    return todo_tokens.item("zh-Hant", "tbd", TAG, text)


KA, KB = key("ma", "Renew the passport"), key("mb", "Book the venue")
KC, KD, KE = key("mc-old", "Ship the samples"), key("md", "Pick a printer"), key("me", "Sign the lease")
KF, KG = key("mf", "Return the router"), key("mg", "Order the frames")
SCENES = {
    "ma": {"state": state(owner("Renew the passport") + marker(KA), agent("Other"))},
    "mb": {"state": state(owner("Book the venue for 40 people") + marker(KB), agent("Other"))},
    "mc": {"state": state(owner("Ship the samples") + marker(KC), agent("Other"))},
    "md": {"state": state(tbd("Pick a printer") + marker(KD), agent("Other"))},
    "me": {"state": state(agent("Other"))},
    "mf": {"state": state(agent("Other"))},
    "mg": {"state": state(owner("Order the frames") + marker(KG), agent("Other")), "posted": {KG: "Order the frames"}},
}
# (project the letter came from, key, the v1 letter's text)
LETTERS = [("ma", KA, "Renew the passport"), ("mb", KB, "Book the venue"), ("mc-old", KC, "Ship the samples"),
           ("md", KD, "Pick a printer"), ("me", KE, "Sign the lease"), ("mf", KF, "Return the router"),
           ("mg", KG, "Order the frames")]
CLAIMED_V1 = [("me", KE)]
# what migrate-v1 sent: project -> [(type, key, terms text)]
MIGRATED = {"ma": [("raised", KA, "Renew the passport")], "mb": [("raised", KB, "Book the venue for 40 people")],
            "mc": [("raised", KC, "Ship the samples")], "md": [("raised", KD, "Pick a printer")]}
AMENDED = {**MIGRATED, "mb": [("raised", KB, "Book the venue"), ("amended", KB, "Book the venue for 40 people")]}
# what the registrar sends after the migration, each night (what it sent with v1_compat, but for mf)
AFTER = {"ma": [], "mb": [], "mc": [], "md": [("note", KD)], "me": [], "mf": [], "mg": []}


def v1_letters(env: Env) -> None:
    """the v1 task letters (id = key) and v1's inferred closures (done-<key>), as v1 put them on the bus"""
    c = sqlite3.connect(env.db)
    for p, k, text in LETTERS:
        c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                  (k, "2026-10-22T03:14:20+08:00", "message", p, OWNER, None,
                   json.dumps({"v": 1, "type": "task", "text": text, "context": "migration"}, ensure_ascii=False)))
    for p, k in CLAIMED_V1:
        c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                  (f"done-{k}", "2026-10-24T10:00:00+08:00", "message", p, OWNER, None,
                   json.dumps({"v": 1, "type": "done", "task": k, "inferred": True, "text": "inferred"})))
    c.commit()
    c.close()


def migrated(env: Env, sent: dict) -> None:
    """the records migrate-v1 sent (ids r:<key> / a:<key>:2, context migration-v1, in 0.5)"""
    recs = []
    for p, items in sent.items():
        for type_, k, text in items:
            rec = {"ph": "0.5", "ext": "acts/0.5", "id": f"r:{k}" if type_ == "raised" else f"a:{k}:2", "type": type_,
                   "origin": "requester", "by": REGISTRAR, "key": k, "project": p, "ts": "2026-11-02T03:00:00Z",
                   "terms": {"text": text}, "terms_rev": 1 if type_ == "raised" else 2, "context": "migration-v1"}
            recs.append(rec if type_ == "raised" else {**rec, "supersedes": [f"r:{k}"]})
    r = env.hubbus("record-publish", "--json", "-", stdin=json.dumps(recs, ensure_ascii=False))
    assert r.returncode == 0, r.stdout + r.stderr


def make(d: str, sent: dict | None) -> Env:
    env = Env(pathlib.Path(d), SCENES)
    (env.hub / ".shared").mkdir(exist_ok=True)
    (env.hub / ".shared" / "project-renames.json").write_text(json.dumps({"mc-old": {"new": "mc", "stage": "done"}}))
    v1_letters(env)
    if sent is not None:
        migrated(env, sent)
    return env


class AfterTheMigration(unittest.TestCase):
    def nights(self, sent: dict):
        with tempfile.TemporaryDirectory() as d:
            env = make(d, sent)
            before = {p: env.outputs(p) for p in SCENES}
            for night in (1, 2):
                r = env.run()
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                after = {p: sorted(set(env.outputs(p)) - set(before[p])) for p in SCENES}
                self.assertEqual(after, AFTER, f"night {night}")
                for p, s in SCENES.items():
                    self.assertEqual(env.state(p), s["state"], f"{p}, night {night}: STATE.md untouched")

    def test_raised(self):
        self.nights(MIGRATED)

    def test_edited_as_amended(self):
        self.nights(AMENDED)

    def test_letters_alone_count_for_nothing(self):
        """without the migration the lines are raised again: the reason migrate-v1 ran before the removal"""
        with tempfile.TemporaryDirectory() as d:
            env = make(d, None)
            r = env.run()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(env.outputs("ma"), [("raised", KA)], "the same text: the letter's key, raised anew")
            kb = key("mb", "Book the venue for 40 people")
            self.assertEqual(env.outputs("mb"), [("raised", kb)], "an edited line: a new key")
            self.assertIn(marker(kb), env.state("mb"))
            self.assertEqual(env.outputs("mf"), [])


if __name__ == "__main__":
    unittest.main()
