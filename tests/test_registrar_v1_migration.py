"""Owner to-dos that exist only as v1 task letters (sent before there were records; no need record on the bus): what
the registrar does with them, and `registrar.py migrate-v1`, which gives each a need record under the key it already
has. Runs the whole script, with its own memory repos, bus and hub.json (test_registrar_flow's Env); never touches
real data.

  ma  a v1 letter, its line unchanged                 → nothing sent, the line kept         | raised, the line's text
  mb  a v1 letter, its line edited since              → nothing sent (no need record an     | raised, the line's text
                                                        amendment could supersede)           (--edited amended: raised
                                                                                             with the letter's text +
                                                                                             amended with the line's)
  mc  a v1 letter sent before a rename (mc-old), the  → nothing sent, the line kept         | raised under mc, the key
      line still carrying that key                                                            kept
  md  a v1 letter, its key on a line relabelled tbd   → a note (displaced), no claim        | raised, the letter's text
  me  a v1 letter, no line, v1 already sent its        → nothing sent                        | not migrated
      inferred closure (done-<key>)
  mf  a v1 letter, no line, no closure                 → claimed done                        | not migrated (by hand)
  mg  a v1 letter that already has a need record       → nothing sent                        | skipped
                                                        (left: the registrar now; right: migrate-v1)
After the migration the registrar sends what it sent before it, and nothing for the migrated items.

    bin/py -m unittest tests.test_registrar_v1_migration
"""
import json
import pathlib
import sqlite3
import subprocess
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
# what each project sends on the first night (and nothing new on the nights after)
FIRST = {"ma": [], "mb": [], "mc": [], "md": [("note", KD)], "me": [], "mf": [("claim", KF)], "mg": []}
# what migrate-v1 sends: project -> [(type, key, terms text)]
MIGRATED = {"ma": [("raised", KA, "Renew the passport")], "mb": [("raised", KB, "Book the venue for 40 people")],
            "mc": [("raised", KC, "Ship the samples")], "md": [("raised", KD, "Pick a printer")]}


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
    cfg = json.loads((env.hub / "hub.json").read_text())
    (env.hub / "hub.json").write_text(json.dumps({**cfg, "principal_handoff": {"send_version": "0.5"}}))
    v1_letters(env)
    return env


def sent(env: Env, p: str) -> list[dict]:
    """every record this project sent, oldest first"""
    return [e["record"] for e in json.loads(env.hubbus("records", "--project", p, "--direction", "out").stdout or "[]")]


def migrate(env: Env, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(env.hub / "bin" / "py"), str(env.hub / "bin" / "registrar.py"), "migrate-v1", *args],
                          cwd=env.hub, env={**env.env, "AGENT_HUB_LANG_UI": "en"}, capture_output=True, text=True,
                          timeout=300)


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


class MigrateV1(unittest.TestCase):
    def test_needs_dry_run_or_apply(self):
        with tempfile.TemporaryDirectory() as d:
            env = make(d)
            self.assertNotEqual(migrate(env).returncode, 0)
            self.assertNotEqual(migrate(env, "--dry-run", "--apply").returncode, 0)

    def test_dry_run_sends_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            env = make(d)
            before = {p: sent(env, p) for p in SCENES}
            r = migrate(env, "--dry-run")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual({p: sent(env, p) for p in SCENES}, before)
            out = r.stdout
            self.assertIn(f"mb: raised {KB} (the line was edited since the v1 letter): Book the venue for 40 people", out)
            self.assertIn(f"mc: raised {KC}: Ship the samples", out)
            self.assertIn(f"md: raised {KD} (its key is on no owner line: the v1 letter's text): Pick a printer", out)
            self.assertIn(f"me: {KE} not migrated: no line mentions it; v1 already sent its inferred closure", out)
            self.assertIn(f"mf: {KF} not migrated: no line mentions it and v1 sent no closure: decide by hand", out)
            self.assertNotIn(KG, out, "an item with a need record is not looked at")
            self.assertIn("migrate-v1: 4 records would be sent", out)

    def check_migrated(self, env, want):
        for p in SCENES:
            got = [(r["type"], r["key"], r["terms"]["text"]) for r in sent(env, p) if r.get("context") == "migration-v1"]
            self.assertEqual(got, want.get(p, []), p)
            for r in sent(env, p):
                if r.get("context") == "migration-v1":
                    self.assertEqual((r["ph"], r["ext"], r["by"], r["origin"], r["project"]),
                                     ("0.5", "acts/0.5", REGISTRAR, "requester", p))

    def test_apply_once_then_the_registrar(self):
        with tempfile.TemporaryDirectory() as d:
            env = make(d)
            r = migrate(env, "--apply")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("migrate-v1: 4 records sent, 0 failed", r.stdout)
            self.check_migrated(env, MIGRATED)
            ids = {p: [x["id"] for x in sent(env, p)] for p in SCENES}
            self.assertEqual(ids["ma"], [f"r:{KA}"])
            r = migrate(env, "--apply")   # a rerun: nothing new
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("migrate-v1: 0 records sent, 0 failed", r.stdout)
            self.assertEqual({p: [x["id"] for x in sent(env, p)] for p in SCENES}, ids)
            before = {p: env.outputs(p) for p in SCENES}
            for night in (1, 2):
                r = env.run()
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                after = {p: sorted(set(env.outputs(p)) - set(before[p])) for p in SCENES}
                self.assertEqual(after, FIRST, f"night {night}: what it sent before the migration, nothing more")
                for p, s in SCENES.items():
                    self.assertEqual(env.state(p), s["state"], f"{p}, night {night}: STATE.md untouched")

    def test_edited_amended(self):
        with tempfile.TemporaryDirectory() as d:
            env = make(d)
            r = migrate(env, "--apply", "--edited", "amended")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.check_migrated(env, {**MIGRATED, "mb": [("raised", KB, "Book the venue"),
                                                         ("amended", KB, "Book the venue for 40 people")]})
            amended = [x for x in sent(env, "mb") if x["type"] == "amended"]
            self.assertEqual([(x["id"], x["terms_rev"], x["supersedes"]) for x in amended], [(f"a:{KB}:2", 2, [f"r:{KB}"])])
            before = {p: env.outputs(p) for p in SCENES}
            r = env.run()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual({p: sorted(set(env.outputs(p)) - set(before[p])) for p in SCENES}, FIRST)


if __name__ == "__main__":
    unittest.main()
