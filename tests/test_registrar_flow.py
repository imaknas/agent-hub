"""Behaviour tests for the task registry (the registrar of Principal Handoff Acts): runs the whole script once, with
its own memory repo, bus and hub.json; never touches real data.

The scenario table was locked in against the old task registration (v1 task / done --inferred letters, removed on
11-01) before bin/registrar.py replaced it (0.3 record events on the bus: hubbus record-publish / records); pa-pd are
what both did, the rest what only the registrar does (the old one's bugs in pe and pg: the spec's order, relink before
reconcile, fixes both). Only records count: v1's letters are not read (tests/test_registrar_v1_migration.py).

  pa  owner line without a key                    → raised / task sent, line end stamped
  pb  line with a key + closed (done)             → that line deleted, an empty section gets the empty marker, closure
                                                    marked done
  pc  deleted key + reopened                      → the line added back
  pd  sent, not closed, not found in STATE.md     → one inferred-done claim sent (not sent again on a second run)
  pe  as above, but with a very similar line      → no claim for the old key, only a warning; that line raised as
      (no key)                                      a new item
  pf  line with a key whose text changed          → amended
  pg  line that lost its key, same text as before → the original key restored, no new raised
  ph  need already withdrawn, line deleted too    → no claim
  pi  an amended item that lost its key           → the original key restored per the amended text, not a new item
                                                    (when the text was never changed the recomputed key is the same
                                                    anyway, so only an amended text shows whether the key gets
                                                    restored)
  pl  an item the project raised itself (send, a  → nothing sent, no claim: it never had a line here, so finding
      lease handover), no line in STATE.md          none is no sign it was done
  pn  closed (done), its key also on a line       → only the to-do line deleted
      outside the to-do section
  pw  warned about before (a very similar line),  → the claim is sent now: a warning holds only while the similar
      that similar line gone too                    line is there
  pt  a ticked owner box carrying the key         → claimed done (acts 0.4 §4 step 5)
  pv  the key on a line relabelled agent          → a note (displaced), no claim
  ps  an owner line in a second to-do section     → not raised (only the first section is)
  pu  an unclosed code fence                      → nothing sent, nothing written
  px  as pb, the settlement in 0.4 (acts/0.4),    → applied the same
      its 0.4 terms digest that of the need record

    bin/py -m unittest discover -s tests
"""
import hashlib
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import names  # noqa: E402
import ph_wire  # noqa: E402
import todo_tokens  # noqa: E402

TAG, OWNER, PRINCIPAL, SIDE = "Alex", "owner", "person:alex", "helper"


def key(project: str, text: str) -> str:
    return f"task-{project}-" + hashlib.sha1(" ".join(text.split()).encode()).hexdigest()[:10]


# the scenarios run on a zh-Hant instance: the registrar reads and writes the zh-Hant to-do tokens (the item texts are
# opaque to it)
ZH = todo_tokens.tokens("zh-Hant")


def marker(k: str) -> str:
    return todo_tokens.stamp("zh-Hant", TAG, k)


def owner(text: str) -> str:
    return todo_tokens.item("zh-Hant", "owner", TAG, text)


def agent(text: str) -> str:
    return todo_tokens.item("zh-Hant", "agent", TAG, text)


def state(*todo: str) -> str:
    return f"# STATE\n\n## Now\n\nA test project.\n\n{ZH['todo_heading']}\n\n" + "\n".join(todo) + "\n"


KB, KC, KD, KE = key("pb", "Pay the domain"), key("pc", "Sign the contract"), key("pd", "Buy parts"), key("pe", "Renew the insurance")
KF, KG, KH, KI = key("pf", "Book flights"), key("pg", "Pay the tax"), key("ph", "Reply to the landlord"), key("pi", "Book the hotel")
KN, KW = key("pn", "pay rent"), key("pw", "renew insurance")
KT, KV, KX = key("pt", "File the report"), key("pv", "Choose a host"), key("px", "Pay the dues")


def v04(p: str, k: str, text: str) -> dict:
    """a settlement the assistant sends in 0.4: its terms digest, as 0.4 computes it, of the need record it was made on"""
    need = {"ext": "acts/0.3", "project": p, "key": k, "terms_rev": 1, "terms": {"text": text}}
    return {"ph": "0.4", "ext": "acts/0.4", "terms_digest": ph_wire.terms_digest(need, "0.4")}


EKN = f" (waiting on owner: {KN})"
SCENES = {
    "pa": {"state": state(owner("Renew the domain"), agent("Add retries"))},
    "pb": {"state": state(owner("Pay the domain") + marker(KB)), "posted": {KB: "Pay the domain"},
           "settle": [("s-pb", KB, "done", "Pay the domain")]},
    "pc": {"state": state(agent("Something else")), "posted": {KC: "Sign the contract"},
           "settle": [("s-pc", KC, "reopened", "Sign the contract")]},
    "pd": {"state": state(agent("Something else")), "posted": {KD: "Buy parts"}},
    "pe": {"state": state(owner("Renew the insurance (this year)")), "posted": {KE: "Renew the insurance"}},
    "pf": {"state": state(owner("Book flights, aisle seat") + marker(KF)), "posted": {KF: "Book flights"}},
    "pg": {"state": state(owner("Pay the tax")), "posted": {KG: "Pay the tax"}},
    "ph": {"state": state(agent("Something else")), "posted": {KH: "Reply to the landlord"}, "withdrawn": [KH]},
    "pi": {"state": state(owner("Book the hotel, breakfast included")), "posted": {KI: "Book the hotel"},
           "amended": {KI: "Book the hotel, breakfast included"}},
    "pl": {"state": state(), "raised_by_project": {"lease-x-1-task-0": "measure the desk"}},
    "pn": {"state": f"# STATE\n\n## Notes\n\n- [ ] [owner] pay rent{EKN}\n\n## Tasks\n\n- [ ] [owner] pay rent{EKN}\n",
           "posted": {KN: "pay rent"}, "settle": [("s-pn", KN, "done", "pay rent")]},
    "pw": {"state": state(), "posted": {KW: "renew insurance"}, "warned": [KW]},
    "pt": {"state": state(owner("File the report").replace("- [ ]", "- [x]", 1) + marker(KT)),
           "posted": {KT: "File the report"}},
    "pv": {"state": state(agent("Choose a host") + marker(KV)), "posted": {KV: "Choose a host"}},
    "ps": {"state": state(agent("Something")) + f"\n{ZH['todo_heading']}\n\n" + owner("Archived ask") + "\n"},
    "pu": {"state": state(owner("Before the fence"), "```", owner("In the fence"))},
    "px": {"state": state(owner("Pay the dues") + marker(KX), agent("Other")), "posted": {KX: "Pay the dues"},
           "settle": [("s-px", KX, "done", "Pay the dues")], "v04": "Pay the dues"},
}


def sh(*cmd, cwd=None, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


class Env:
    """A temporary hub (bin/ linked to the real one) + a bare repo and memory copy per project + a bus."""

    def __init__(self, tmp: pathlib.Path, scenes: dict | None = None, lang: str = "zh-Hant"):
        self.tmp, self.hub = tmp, tmp / "hub"
        (self.hub / "bin").mkdir(parents=True)
        for f in (ROOT / "bin").iterdir():
            if f.is_file():
                (self.hub / "bin" / f.name).symlink_to(f)
        for d in ("projects", ".state", ".logs", "data"):
            (self.hub / d).mkdir()
        (self.hub / "data" / "openclaw-migration-status.md").write_text(f"# status\n\n{ZH['todo_heading']}\n\n{ZH['todo_none']}\n")
        (self.hub / "hub.json").write_text(json.dumps({
            "v": 1, "registry_dir": "projects", "memory_copies": str(tmp / "copies"), "git_origin": str(tmp / "bare"),
            "bus_dir": str(tmp / "bus"),
            "owner": {"address": OWNER, "aliases": ["alex"], "tag": TAG, "principal_id": PRINCIPAL},
            "assistant": {"project": SIDE, "api": "http://127.0.0.1:9/x", "party_id": SIDE}}))
        e = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", *(n for x in ("HOME", "CONFIG", "DATA", "SECRETS")
                                                                          for n in names.env_names(x))))}
        # the content language the scenarios' STATE.md files are written in (SCENES: zh-Hant)
        self.env = {**e, "AGENT_HUB_ROOT": str(self.hub), "AGENT_HUB_DATA": str(self.hub / "data"),
                    "AGENT_HUB_LANG_CONTENT": lang,
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        self.hubbus("inbox", "--project", "pa")   # creates the bus tables
        self.db = tmp / "bus" / "bus.db"
        self.scenes = scenes or SCENES
        for p, s in self.scenes.items():
            self.project(p, s)
        (self.hub / ".state" / "registrar-warned.json").write_text(
            json.dumps(sorted(k for s in self.scenes.values() for k in s.get("warned", []))))

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
        self.inputs(p, s)

    # --- 0.3 records ---
    def rec(self, p, rid, type_, origin, by, k, **kw):
        return {"ph": "0.3", "ext": "acts/0.3", "id": rid, "type": type_, "origin": origin, "by": by, "key": k,
                "project": p, "ts": "2026-10-20T10:00:00Z", **kw}

    def inputs(self, p, s):
        recs = []
        for k, text in s.get("posted", {}).items():
            recs.append(self.rec(p, f"r:{k}", "raised", "requester", "agent-hub:registrar", k,
                                 terms={"text": text}, terms_rev=1))
        for k, text in s.get("raised_by_project", {}).items():   # outside the document (send, a lease handover)
            recs.append(self.rec(p, f"r:{k}", "raised", "requester", f"project:{p}", k, terms={"text": text},
                                 terms_rev=1))
        for k, text in s.get("amended", {}).items():
            recs.append(self.rec(p, f"a:{k}:2", "amended", "requester", "agent-hub:registrar", k, terms={"text": text},
                                 terms_rev=2, supersedes=[f"r:{k}"], ts="2026-10-21T09:00:00Z"))
        for k in s.get("withdrawn", []):
            recs.append(self.rec(p, f"w:{k}", "withdrawn", "requester", f"project:{p}", k, reason="not_needed",
                                 supersedes=[f"r:{k}"], ts="2026-10-21T10:00:00Z"))
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
                                        authority={"kind": "principal", "decider": PRINCIPAL}, ts="2026-10-26T10:00:00Z",
                                        **(v04(p, k, s["v04"]) if s.get("v04") else {})))
        if settles:   # from the principal side: written directly as record events on the bus (what bridge does inbound)
            c = sqlite3.connect(self.db)
            for r in settles:
                c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                          (f"rec:acts:{p}:{r['id']}", "2026-10-26T10:00:00+08:00", "record", OWNER, p, None,
                           json.dumps({"v": 1, "record": r}, ensure_ascii=False)))
            c.commit(); c.close()

    def outputs(self, p):
        r = self.hubbus("records", "--project", p, "--direction", "out")
        out = []
        for e in json.loads(r.stdout or "[]"):
            rec = e["record"]
            if rec["ts"] < "2026-10-27" or rec["type"] == "withdrawn":
                continue
            kind = {"raised": "raised", "amended": "amended", "claimed": "claim", "note": "note"}.get(rec["type"])
            if kind:
                out.append((kind, rec["key"]))
        return sorted(out)

    def run(self):
        return subprocess.run([str(self.hub / "bin" / "py"), str(self.hub / "bin" / "registrar.py")], cwd=self.hub,
                              env=self.env, capture_output=True, text=True, timeout=300)

    def state(self, p):
        return (self.tmp / "copies" / p / "STATE.md").read_text()

    def remote_state(self, p):
        return self.git(self.tmp / "bare" / f"{p}-memory.git", "show", "agent-memory:STATE.md")


class RegistrarFlow(unittest.TestCase):
    def each(self, check):
        with tempfile.TemporaryDirectory() as d:
            env = Env(pathlib.Path(d))
            r = env.run()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            check(env, r.stdout + r.stderr)

    def test_scenarios(self):
        def check(env, log):
            ka = key("pa", "Renew the domain")
            self.assertIn(marker(ka), env.state("pa"))
            self.assertIn(marker(ka), env.remote_state("pa"), "the stamp is committed and pushed")
            self.assertEqual(env.outputs("pa"), [("raised", ka)])
            self.assertNotIn("Pay the domain", env.state("pb"))
            self.assertIn(ZH["todo_none"], env.state("pb"))
            self.assertIn(owner("Sign the contract") + marker(KC), env.state("pc"))
            self.assertEqual(env.outputs("pd"), [("claim", KD)])
            self.assertEqual(env.outputs("pe"), [("raised", key("pe", "Renew the insurance (this year)"))])
            self.assertIn("Renew the insurance (this year)", env.state("pe"))
            self.assertEqual(env.outputs("pf"), [("amended", KF)])
            self.assertIn(marker(KG), env.state("pg"))
            self.assertEqual(env.outputs("pg"), [])
            self.assertEqual(env.outputs("ph"), [])
            self.assertIn(marker(KI), env.state("pi"))
            self.assertEqual(env.outputs("pi"), [])
            self.assertEqual(env.outputs("pl"), [], "an item raised outside the document is never claimed done")
            self.assertEqual(env.state("pn").count(KN), 1, "the line outside the to-do section is kept")
            self.assertIn("## Notes\n\n- [ ] [owner] pay rent", env.state("pn"))
            self.assertEqual(env.outputs("pw"), [("claim", KW)], "a warning holds only while the similar line is")
            self.assertEqual(env.outputs("pt"), [("claim", KT)], "a ticked owner box: claimed done")
            self.assertEqual(env.outputs("pv"), [("note", KV)], "relabelled, the key kept: a note, no claim")
            self.assertEqual(env.outputs("ps"), [], "a later section is not raised")
            self.assertEqual(env.outputs("pu"), [], "an unclosed fence: nothing sent")
            self.assertNotIn("waiting", env.state("pu"))
            self.assertEqual(env.state("pu"), SCENES["pu"]["state"], "nor written")
            self.assertNotIn("Pay the dues", env.state("px"), "a 0.4 settlement is applied too")
        self.each(check)

    def test_second_run_sends_nothing_new(self):
        def check(env, log):
            first = {p: env.outputs(p) for p in env.scenes}
            stored = sqlite3.connect(env.db).execute("SELECT COUNT(*) FROM events WHERE type='record'").fetchone()
            r = env.run()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual({p: env.outputs(p) for p in env.scenes}, first)
            # the claims and notes the run re-creates are not sent again: the bus would keep a re-created one (another
            # ts is another content, core §10)
            self.assertEqual(sqlite3.connect(env.db).execute(
                "SELECT COUNT(*) FROM events WHERE type='record'").fetchone(), stored)
        self.each(check)

    def test_messages_zh_hant(self):
        """what registrar.py writes for people (log, the memory commit title, the note on done letters) stays
        byte-identical on a zh-Hant instance; expected text in tests/fixtures/registrar-messages.zh-Hant.json"""
        want = json.loads((ROOT / "tests" / "fixtures" / "registrar-messages.zh-Hant.json").read_text())
        keys = {"ka": key("pa", want["pa_text"]), "kb": KB, "kc": KC, "ke": KE, "kg": KG, "ki": KI, "kn": KN, "kv": KV, "kx": KX}
        with tempfile.TemporaryDirectory() as d:
            env = Env(pathlib.Path(d))
            env.env["AGENT_HUB_LANG_UI"] = "zh-Hant"
            dry = subprocess.run([str(env.hub / "bin" / "py"), str(env.hub / "bin" / "registrar.py"), "--dry-run"], cwd=env.hub,
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


# Acts Markdown 0.5 (spec §2 step 3): lines carrying the key of an item the project raised outside the document (send,
# a lease handover). Such a key is known: not bound, the registrar holds the item's need record.
#
#   qa  an owner line carrying a known key           → not raised, not restamped; warned about every night
#   qb  a known key settled, the line's text differs → not struck, reported
#   qc  a known key settled, the line's text equal   → the line deleted
#   qd  a known key reopened, no line carries it     → no line added (only a bound key is re-added)
#   qe  a 0.4 settlement whose terms digest is that  → stale (core §6, the digest converted to 0.5): not struck
#       of other terms at the same terms_rev
KQA, KQB, KQC, KQD = "ask-qa-0000000001", "ask-qb-0000000002", "ask-qc-0000000003", "ask-qd-0000000004"
KQE = key("qe", "Pay the fee")
KNOWN_SCENES = {
    "qa": {"state": state(owner("Measure the desk") + marker(KQA)), "raised_by_project": {KQA: "Measure the desk"}},
    "qb": {"state": state(owner("Call the bank about the card") + marker(KQB), agent("Other")),
           "raised_by_project": {KQB: "Call the bank"}, "settle": [("s-qb", KQB, "done", "Call the bank")]},
    "qc": {"state": state(owner("Water the plants") + marker(KQC), agent("Other")),
           "raised_by_project": {KQC: "Water the plants"}, "settle": [("s-qc", KQC, "done", "Water the plants")]},
    "qd": {"state": state(agent("Other")), "raised_by_project": {KQD: "Pick up the keys"},
           "settle": [("s-qd", KQD, "reopened", "Pick up the keys")]},
    "qe": {"state": state(owner("Pay the fee") + marker(KQE), agent("Other")), "posted": {KQE: "Pay the fee"},
           "settle": [("s-qe", KQE, "done", "Pay the fee")], "v04": "Pay the fee, twice"},
}


class RegistrarKnownKeys(unittest.TestCase):
    def test_keys_raised_outside_the_document(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(pathlib.Path(d), KNOWN_SCENES)
            env.env["AGENT_HUB_LANG_UI"] = "en"   # the log lines below are English
            r = env.run()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            log = r.stdout + r.stderr
            self.assertEqual(env.outputs("qa"), [], "a known key's line is not raised")
            self.assertEqual(env.state("qa"), KNOWN_SCENES["qa"]["state"], "nor restamped")
            self.assertIn(f"qa: warning — line 9 carries {KQA}", log)
            self.assertIn(owner("Call the bank about the card") + marker(KQB), env.state("qb"), "a line asking else: kept")
            self.assertIn(f"qb: {KQB} was settled, but the line carrying it asks something else", log)
            self.assertNotIn("Water the plants", env.state("qc"))
            self.assertEqual(env.state("qd"), KNOWN_SCENES["qd"]["state"], "a key not raised from a line: not re-added")
            self.assertEqual(env.outputs("qd"), [])
            self.assertIn("Pay the fee", env.state("qe"), "a stale settlement strikes nothing")
            r = env.run()   # warned again the next night, nothing else
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn(f"qa: warning — line 9 carries {KQA}", r.stdout + r.stderr)
            self.assertEqual(env.outputs("qa"), [])


# An English-content instance (contracts/state-todo.v2.md): the same steps, the English token set written; pm is an
# English instance with an older zh-Hant line, which keeps its tag and gets the English stamp.
EN = todo_tokens.tokens("en")


def en_state(*todo: str) -> str:
    return f"# STATE\n\n## Now\n\nA test project.\n\n{EN['todo_heading']}\n\n" + "\n".join(todo) + "\n"


def en_marker(k: str) -> str:
    return todo_tokens.stamp("en", TAG, k)


EKB, EKC, EKD = key("pb", "Pay the domain"), key("pc", "Sign the contract"), key("pd", "Buy parts")
EN_SCENES = {
    "pa": {"state": en_state(todo_tokens.item("en", "owner", TAG, "Renew the domain"), todo_tokens.item("en", "agent", TAG, "Add retry"))},
    "pb": {"state": en_state(todo_tokens.item("en", "owner", TAG, "Pay the domain") + en_marker(EKB)), "posted": {EKB: "Pay the domain"},
           "settle": [("s-pb", EKB, "done", "Pay the domain")]},
    "pc": {"state": en_state(todo_tokens.item("en", "agent", TAG, "Something else")), "posted": {EKC: "Sign the contract"},
           "settle": [("s-pc", EKC, "reopened", "Sign the contract")]},
    "pd": {"state": en_state(todo_tokens.item("en", "agent", TAG, "Something else")), "posted": {EKD: "Buy parts"}},
    "pm": {"state": en_state(todo_tokens.item("zh-Hant", "owner", TAG, "An older line"))},
}


class RegistrarFlowEnglish(unittest.TestCase):
    def test_english_instance(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(pathlib.Path(d), EN_SCENES, "en")
            r = env.run()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            ka = key("pa", "Renew the domain")
            self.assertIn(todo_tokens.item("en", "owner", TAG, "Renew the domain") + en_marker(ka), env.state("pa"))
            self.assertEqual(env.outputs("pa"), [("raised", ka)])
            self.assertEqual([l for l in env.state("pb").splitlines() if l.strip()],
                             [l for l in en_state(EN["todo_none"]).splitlines() if l.strip()],
                             "the line removed, the English empty marker")
            self.assertIn(todo_tokens.item("en", "owner", TAG, "Sign the contract") + en_marker(EKC), env.state("pc"))
            self.assertEqual(env.outputs("pd"), [("claim", EKD)])
            km = key("pm", "An older line")
            self.assertIn(todo_tokens.item("zh-Hant", "owner", TAG, "An older line") + en_marker(km), env.state("pm"))
            self.assertIn(EN["todo_heading"], env.state("pm"))
            basis = [e["record"]["evidence"]["basis"] for e in json.loads(env.hubbus(
                "records", "--project", "pd", "--direction", "out").stdout) if e["record"]["type"] == "claimed"]
            self.assertEqual(len(basis), 1)
            self.assertTrue(basis[0].startswith("Inferred done by the librarian"), basis[0])
            r = env.run()   # a second night changes nothing
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(env.outputs("pa"), [("raised", ka)])


if __name__ == "__main__":
    unittest.main()
