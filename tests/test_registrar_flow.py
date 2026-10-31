"""Behaviour tests for the task registry (the registrar of Principal Handoff Acts): runs the whole script once, with
its own memory repo, bus and hub.json; never touches real data.

The same scenario table runs against every implementation under bin/:
  the old one    v1 task / done --inferred letters and the personal assistant's handoffs on the bus
  registrar.py   (new) 0.3 record events on the bus (hubbus record-publish / records)
The old one runs only the scenarios it can do (V1); the new one runs all. This is the behaviour locked in before the
old one was replaced with registrar.py; after the switch it is a regression test.
The old one has known bugs in pe and pg, so they are not in V1: the rewrite match compares the whole line (including
"- [ ] 〔Alex〕"), so it almost never judges a line rewritten; and it checks vanished keys before sending new lines, so
a line with the same text that lost its key first gets a wrong "inferred done" sent, then is stamped back with the
same key. The spec's order (relink before reconcile) fixes both.

  pa  〔Alex〕 line without a key                  → raised / task sent, line end stamped
  pb  line with a key + closed (done)             → that line deleted, an empty section gets "- (無)", closure
                                                    marked done
  pc  deleted key + reopened                      → the line added back
  pd  sent, not closed, not found in STATE.md     → one inferred-done claim sent (not sent again on a second run)
  pe  as above, but with a very similar line      → no claim for the old key, only a warning; that line raised as
      (no key)                                      a new item
  pf  line with a key whose text changed          → amended (new one only)
  pg  line that lost its key, same text as before → the original key restored, no new raised (new one only)
  ph  need already withdrawn, line deleted too    → no claim (new one only)
  pj  an item v1 already sent an inferred         → no claim on the night of the switch (new one only)
      closure for (done-<key>)
  pk  an item sent through v1 before a rename     → recognised, not raised again (new one only)
      (pk-old), line still there
  pi  an amended item that lost its key           → the original key restored per the amended text, not a new item
                                                    (new one only; when the text was never changed the recomputed
                                                    key is the same anyway, so only an amended text shows whether
                                                    the key gets restored)

    bin/py -m unittest discover -s tests
"""
import hashlib
import json
import os
import pathlib
import sqlite3
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
OLD, NEW = "knas-tasks.py", "registrar.py"   # the old file name goes once registrar.py has replaced it
IMPLS = [p for p in (OLD, NEW) if (ROOT / "bin" / p).exists()]
TAG, OWNER, PRINCIPAL, SIDE = "Alex", "owner", "person:alex", "helper"
V1 = {"pa", "pb", "pc", "pd"}   # scenarios the old one can do (pe, pg are its known bugs, see above)


def key(project: str, text: str) -> str:
    return f"task-{project}-" + hashlib.sha1(" ".join(text.split()).encode()).hexdigest()[:10]


def marker(k: str) -> str:
    return f"(等 {TAG}:{k})"


def state(*todo: str) -> str:
    return "# STATE\n\n## 現況\n\n測試專案。\n\n## 待辦\n\n" + "\n".join(todo) + "\n"


KB, KC, KD, KE = key("pb", "付網域"), key("pc", "簽合約"), key("pd", "買零件"), key("pe", "續約保險")
KF, KG, KH, KI = key("pf", "訂機票"), key("pg", "繳稅"), key("ph", "回信給房東"), key("pi", "訂飯店")
SCENES = {
    "pa": {"state": state("- [ ] 〔Alex〕續約網域", "- [ ] 〔agent〕加重試")},
    "pb": {"state": state(f"- [ ] 〔Alex〕付網域{marker(KB)}"), "posted": {KB: "付網域"},
           "settle": [("s-pb", KB, "done", "付網域")]},
    "pc": {"state": state("- [ ] 〔agent〕別的事"), "posted": {KC: "簽合約"},
           "settle": [("s-pc", KC, "reopened", "簽合約")]},
    "pd": {"state": state("- [ ] 〔agent〕別的事"), "posted": {KD: "買零件"}},
    "pe": {"state": state("- [ ] 〔Alex〕續約保險(今年)"), "posted": {KE: "續約保險"}},
    "pf": {"state": state(f"- [ ] 〔Alex〕訂機票,要靠走道{marker(KF)}"), "posted": {KF: "訂機票"}},
    "pg": {"state": state("- [ ] 〔Alex〕繳稅"), "posted": {KG: "繳稅"}},
    "ph": {"state": state("- [ ] 〔agent〕別的事"), "posted": {KH: "回信給房東"}, "withdrawn": [KH]},
    "pj": {"state": state("- [ ] 〔agent〕別的事"), "posted": {key("pj", "寄掛號"): "寄掛號"}, "v1_claimed": [key("pj", "寄掛號")]},
    "pk": {"state": state(f"- [ ] 〔Alex〕寄包裹{marker(key('pk-old', '寄包裹'))}"), "v1_posted_as": ("pk-old", "寄包裹")},
    "pi": {"state": state("- [ ] 〔Alex〕訂飯店,含早餐"), "posted": {KI: "訂飯店"}, "amended": {KI: "訂飯店,含早餐"}},
}


def sh(*cmd, cwd=None, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


class Env:
    """A temporary hub (bin/ linked to the real one) + a bare repo and memory copy per project + a bus."""

    def __init__(self, tmp: pathlib.Path, impl: str):
        self.tmp, self.hub, self.impl = tmp, tmp / "hub", impl
        (self.hub / "bin").mkdir(parents=True)
        for f in (ROOT / "bin").iterdir():
            if f.is_file():
                (self.hub / "bin" / f.name).symlink_to(f)
        for d in ("projects", ".state", ".logs", "data"):
            (self.hub / d).mkdir()
        (self.hub / "data" / "openclaw-migration-status.md").write_text("# status\n\n## 待辦\n\n- (無)\n")
        (self.hub / ".state" / "project-renames.json").write_text(json.dumps({"pk-old": {"new": "pk", "stage": "done"}}))
        (self.hub / "hub.json").write_text(json.dumps({
            "v": 1, "registry_dir": "projects", "memory_copies": str(tmp / "copies"), "git_origin": str(tmp / "bare"),
            "bus_dir": str(tmp / "bus"),
            "owner": {"address": OWNER, "aliases": ["alex"], "tag": TAG, "principal_id": PRINCIPAL},
            "assistant": {"project": SIDE, "api": "http://127.0.0.1:9/x", "party_id": SIDE}}))
        e = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "AGENT_HUB_HOME", "AGENT_HUB_CONFIG",
                                                                     "AGENT_HUB_DATA", "AGENT_HUB_SECRETS"))}
        # the scenarios' STATE.md files are zh-Hant content
        self.env = {**e, "AGENT_HUB_ROOT": str(self.hub), "AGENT_HUB_DATA": str(self.hub / "data"),
                    "AGENT_HUB_LANG_CONTENT": "zh-Hant",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        self.hubbus("inbox", "--project", "pa")   # creates the bus tables
        self.db = tmp / "bus" / "bus.db"
        self.scenes = {p: s for p, s in SCENES.items() if impl == NEW or p in V1}
        for p, s in self.scenes.items():
            self.project(p, s)

    def hubbus(self, *args, stdin=None):
        return subprocess.run([str(self.hub / "bin" / "py"), str(self.hub / "bin" / "hubbus"), *args], env=self.env,
                              input=stdin, capture_output=True, text=True)

    def git(self, repo, *args):
        return sh("git", "-C", str(repo), *args, env=self.env)

    def project(self, p, s):
        bare, copy = self.tmp / "bare" / f"{p}-memory.git", self.tmp / "copies" / p
        bare.parent.mkdir(exist_ok=True)
        sh("git", "init", "-q", "--bare", "-b", "agent-memory", str(bare), env=self.env)
        sh("git", "clone", "-q", str(bare), str(copy), env=self.env)
        (copy / "STATE.md").write_text(s["state"])
        self.git(copy, "add", "-A"); self.git(copy, "commit", "-qm", "init"); self.git(copy, "push", "-q", "origin", "HEAD")
        (self.hub / "projects" / f"{p}.yaml").write_text(f"v: 1\nname: {p}\npaths:\n  workspace: {copy}\nmemory: true\n")
        if self.impl == OLD:
            self.v1_inputs(p, s)
        else:
            self.v2_inputs(p, s)

    # --- old: v1 letters ---
    def v1_inputs(self, p, s):
        c = sqlite3.connect(self.db)
        for k, text in s.get("posted", {}).items():
            c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                      (k, "2026-10-20T10:00:00+08:00", "message", p, OWNER, None,
                       json.dumps({"v": 1, "type": "task", "text": text}, ensure_ascii=False)))
        for hid, k, effect, text in s.get("settle", []):
            payload = {"v": 1, "type": "handoff", "handoff_id": abs(hash(hid)) % 10000, "effect": effect,
                       "handoff": {"reply_to": k, "item": text, "fact": text, "why": "測試", "effect": effect,
                                   "ts": "2026-10-26T10:00:00"}}
            c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                      (hid, "2026-10-26T10:00:00+08:00", "message", OWNER, p, None, json.dumps(payload, ensure_ascii=False)))
        c.commit(); c.close()

    def v1_outputs(self, p):
        """Letters this run sent to the owner: [(kind, key)], kind raised or claim."""
        c = sqlite3.connect(self.db)
        rows = c.execute("SELECT id, payload FROM events WHERE type='message' AND source=? AND to_project=?"
                         " AND ts > '2026-10-27'", (p, OWNER)).fetchall()
        out = []
        for eid, pl in rows:
            d = json.loads(pl)
            if d.get("type") == "task":
                out.append(("raised", eid))
            elif d.get("type") == "done" and d.get("inferred"):
                out.append(("claim", d.get("task")))
        return sorted(out)

    # --- new: 0.3 records ---
    def rec(self, p, rid, type_, origin, by, k, **kw):
        return {"ph": "0.3", "ext": "acts/0.3", "id": rid, "type": type_, "origin": origin, "by": by, "key": k,
                "project": p, "ts": "2026-10-20T10:00:00Z", **kw}

    def v2_inputs(self, p, s):
        recs = []
        for k, text in s.get("posted", {}).items():
            recs.append(self.rec(p, f"r:{k}", "raised", "requester", "agent-hub:registrar", k,
                                 terms={"text": text}, terms_rev=1))
        for k, text in s.get("amended", {}).items():
            recs.append(self.rec(p, f"a:{k}:2", "amended", "requester", "agent-hub:registrar", k, terms={"text": text},
                                 terms_rev=2, supersedes=[f"r:{k}"], ts="2026-10-21T09:00:00Z"))
        for k in s.get("withdrawn", []):
            recs.append(self.rec(p, f"w:{k}", "withdrawn", "requester", f"project:{p}", k, reason="not_needed",
                                 supersedes=[f"r:{k}"], ts="2026-10-21T10:00:00Z"))
        if s.get("v1_posted_as"):   # a v1 task sent under the old name before the rename
            old, text = s["v1_posted_as"]
            c = sqlite3.connect(self.db)
            c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                      (key(old, text), "2026-10-20T10:00:00+08:00", "message", old, OWNER, None,
                       json.dumps({"v": 1, "type": "task", "text": text}, ensure_ascii=False)))
            c.commit(); c.close()
        if s.get("v1_claimed"):   # an inferred closure the old registrar sent in v1
            c = sqlite3.connect(self.db)
            for k in s["v1_claimed"]:
                c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                          (f"done-{k}", "2026-10-22T10:00:00+08:00", "message", p, OWNER, None,
                           json.dumps({"v": 1, "type": "done", "task": k, "inferred": True, "text": "推斷"})))
            c.commit(); c.close()
        if recs:
            r = self.hubbus("record-publish", "--json", "-", stdin=json.dumps(recs, ensure_ascii=False))
            assert r.returncode == 0, r.stdout + r.stderr
        settles = []
        for hid, k, effect, text in s.get("settle", []):
            if effect == "reopened":
                settles.append(self.rec(p, hid, "reopened", "principal", SIDE, k, supersedes=["s-earlier"],
                                        restore_text=text, ts="2026-10-26T10:00:00Z"))
            else:
                settles.append(self.rec(p, hid, "settled", "principal", SIDE, k, outcome=effect, terms_rev=1,
                                        authority={"kind": "principal", "decider": PRINCIPAL}, ts="2026-10-26T10:00:00Z"))
        if settles:   # from the principal side: written directly as record events on the bus (what bridge does inbound)
            c = sqlite3.connect(self.db)
            for r in settles:
                c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                          (f"rec:acts:{p}:{r['id']}", "2026-10-26T10:00:00+08:00", "record", OWNER, p, None,
                           json.dumps({"v": 1, "record": r}, ensure_ascii=False)))
            c.commit(); c.close()

    def v2_outputs(self, p):
        r = self.hubbus("records", "--project", p, "--direction", "out")
        out = []
        for e in json.loads(r.stdout or "[]"):
            rec = e["record"]
            if rec["ts"] < "2026-10-27" or rec["type"] == "withdrawn":
                continue
            kind = {"raised": "raised", "amended": "amended", "claimed": "claim"}.get(rec["type"])
            if kind:
                out.append((kind, rec["key"]))
        return sorted(out)

    # --- shared ---
    def run(self):
        return subprocess.run([str(self.hub / "bin" / "py"), str(self.hub / "bin" / self.impl)], cwd=self.hub,
                              env=self.env, capture_output=True, text=True, timeout=300)

    def outputs(self, p):
        return self.v1_outputs(p) if self.impl == OLD else self.v2_outputs(p)

    def state(self, p):
        return (self.tmp / "copies" / p / "STATE.md").read_text()

    def remote_state(self, p):
        return self.git(self.tmp / "bare" / f"{p}-memory.git", "show", "agent-memory:STATE.md")


@unittest.skipUnless(IMPLS, "no task registry implementation")
class RegistrarFlow(unittest.TestCase):
    def each(self, check):
        for impl in IMPLS:
            with self.subTest(impl=impl), tempfile.TemporaryDirectory() as d:
                env = Env(pathlib.Path(d), impl)
                r = env.run()
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                check(env, r.stdout + r.stderr)

    def test_scenarios(self):
        def check(env, log):
            ka = key("pa", "續約網域")
            self.assertIn(marker(ka), env.state("pa"))
            self.assertIn(marker(ka), env.remote_state("pa"), "the stamp is committed and pushed")
            self.assertEqual(env.outputs("pa"), [("raised", ka)])
            self.assertNotIn("付網域", env.state("pb"))
            self.assertIn("- (無)", env.state("pb"))
            self.assertIn(f"〔Alex〕簽合約{marker(KC)}", env.state("pc"))
            self.assertEqual(env.outputs("pd"), [("claim", KD)])
            if env.impl == NEW:
                self.assertEqual(env.outputs("pe"), [("raised", key("pe", "續約保險(今年)"))])
                self.assertIn("續約保險(今年)", env.state("pe"))
                self.assertEqual(env.outputs("pf"), [("amended", KF)])
                self.assertIn(marker(KG), env.state("pg"))
                self.assertEqual(env.outputs("pg"), [])
                self.assertEqual(env.outputs("ph"), [])
                self.assertIn(marker(KI), env.state("pi"))
                self.assertEqual(env.outputs("pi"), [])
                self.assertEqual(env.outputs("pj"), [], "an inferred closure v1 already sent is not sent again")
                self.assertEqual(env.outputs("pk"), [], "one sent before the rename is recognised")
        self.each(check)

    def test_second_run_sends_nothing_new(self):
        def check(env, log):
            first = {p: env.outputs(p) for p in env.scenes}
            r = env.run()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual({p: env.outputs(p) for p in env.scenes}, first)
        self.each(check)

    @unittest.skipUnless(NEW in IMPLS, "no registrar.py")
    def test_messages_zh_hant(self):
        """what registrar.py writes for people (log, the memory commit title, the note on done letters) stays
        byte-identical on a zh-Hant instance; expected text in tests/fixtures/registrar-messages.zh-Hant.json"""
        want = json.loads((ROOT / "tests" / "fixtures" / "registrar-messages.zh-Hant.json").read_text())
        keys = {"ka": key("pa", want["pa_text"]), "kb": KB, "kc": KC, "ke": KE, "kg": KG, "ki": KI}
        with tempfile.TemporaryDirectory() as d:
            env = Env(pathlib.Path(d), NEW)
            env.env["AGENT_HUB_LANG_UI"] = "zh-Hant"
            dry = subprocess.run([str(env.hub / "bin" / "py"), str(env.hub / "bin" / NEW), "--dry-run"], cwd=env.hub,
                                 env=env.env, capture_output=True, text=True, timeout=300)
            self.assertIn(want["dry_run"].format(**keys), dry.stdout)
            r = env.run()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(r.stdout, "".join(f"[registrar] {line.format(**keys)}\n" for line in want["log"]))
            self.assertEqual(env.git(env.tmp / "copies" / "pa", "log", "-1", "--format=%s"), want["commit"])
            c = sqlite3.connect(env.db)
            notes = {json.loads(pl).get("note") for (pl,) in c.execute(
                "SELECT payload FROM events WHERE type='message.done' AND id LIKE 'rec:acts:%'")}
            c.close()
            self.assertEqual(notes, {want["note"]})


if __name__ == "__main__":
    unittest.main()
