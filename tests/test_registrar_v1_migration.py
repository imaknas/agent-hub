"""Owner to-dos that exist only as v1 task letters (sent before there were records; no need record on the bus): what
the registrar does with them. Runs the whole script, with its own memory repos, bus and hub.json (test_registrar_flow's
Env); never touches real data.

  ma  a v1 letter, its line unchanged                 → nothing sent, the line kept
  mb  a v1 letter, its line edited since              → nothing sent (no need record an amendment could supersede)
  mc  a v1 letter sent before a rename (mc-old), the  → nothing sent, the line kept
      line still carrying that key
  md  a v1 letter, its key on a line relabelled tbd   → a note (displaced), no claim
  me  a v1 letter, no line, v1 already sent its        → nothing sent
      inferred closure (done-<key>)

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


def tbd(text: str) -> str:
    return todo_tokens.item("zh-Hant", "tbd", TAG, text)


KA, KB = key("ma", "Renew the passport"), key("mb", "Book the venue")
KC, KD, KE = key("mc-old", "Ship the samples"), key("md", "Pick a printer"), key("me", "Sign the lease")
SCENES = {
    "ma": {"state": state(owner("Renew the passport") + marker(KA), agent("Other"))},
    "mb": {"state": state(owner("Book the venue for 40 people") + marker(KB), agent("Other"))},
    "mc": {"state": state(owner("Ship the samples") + marker(KC), agent("Other"))},
    "md": {"state": state(tbd("Pick a printer") + marker(KD), agent("Other"))},
    "me": {"state": state(agent("Other"))},
}
# (project the letter came from, key, the v1 letter's text)
LETTERS = [("ma", KA, "Renew the passport"), ("mb", KB, "Book the venue"), ("mc-old", KC, "Ship the samples"),
           ("md", KD, "Pick a printer"), ("me", KE, "Sign the lease")]
CLAIMED_V1 = [("me", KE)]
# what each project sends on the first night, and on the nights after
FIRST = {"ma": [], "mb": [], "mc": [], "md": [("note", KD)], "me": []}


def v1_letters(env: Env, letters=LETTERS, claimed=CLAIMED_V1) -> None:
    """the v1 task letters (id = key) and v1's inferred closures (done-<key>), as v1 put them on the bus"""
    c = sqlite3.connect(env.db)
    for p, k, text in letters:
        c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                  (k, "2026-10-22T03:14:20+08:00", "message", p, OWNER, None,
                   json.dumps({"v": 1, "type": "task", "text": text, "context": "migration"}, ensure_ascii=False)))
    for p, k in claimed:
        c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                  (f"done-{k}", "2026-10-24T10:00:00+08:00", "message", p, OWNER, None,
                   json.dumps({"v": 1, "type": "done", "task": k, "inferred": True, "text": "inferred"})))
    c.commit()
    c.close()


def make(d: str) -> Env:
    env = Env(pathlib.Path(d), SCENES)
    (env.hub / ".state" / "project-renames.json").write_text(json.dumps({"mc-old": {"new": "mc", "stage": "done"}}))
    v1_letters(env)
    return env


class V1OnlyItems(unittest.TestCase):
    def test_what_the_registrar_does_with_them(self):
        with tempfile.TemporaryDirectory() as d:
            env = make(d)
            for night in (1, 2):
                r = env.run()
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual({p: env.outputs(p) for p in SCENES}, FIRST, f"night {night}")
                for p, s in SCENES.items():
                    self.assertEqual(env.state(p), s["state"], f"{p}, night {night}: STATE.md untouched")


if __name__ == "__main__":
    unittest.main()
