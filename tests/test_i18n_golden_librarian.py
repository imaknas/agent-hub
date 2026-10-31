"""E1 golden (docs/design/i18n.md) for bin/librarian.py on zh-Hant: one whole run in the flow test's temp hub
(tests/test_librarian_flow.py: stub llm.py, a branch per project, handoffs and v2 records), pinning the log, the
review-log blocks, the digest, the notes on the letters it closes, and every prompt it sends (byte for byte).

    bin/py -m unittest tests.test_i18n_golden_librarian
    I18N_GOLDEN_UPDATE=1 bin/py -m unittest tests.test_i18n_golden_librarian    # rewrite (only on purpose)
"""
import datetime
import json
import os
import pathlib
import re
import sqlite3
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_lang  # noqa: E402

GOLDEN = golden_lang.GOLDEN
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
sys.path.insert(0, str(ROOT / "tests"))
import test_librarian_flow as flow  # noqa: E402

TODAY = datetime.date.today().isoformat()


def records(env):
    """v2 records for p1: a note, a decided question, a dismissed one, a same_as (prompt input), an Acts settle (not ours)"""
    c = sqlite3.connect(env.tmp / "bus" / "bus.db")

    def put(eid, source, to, rec):
        c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                  (eid, "2026-10-26T10:00:00+08:00", "record", source, to, None, json.dumps({"v": 1, "record": rec})))
    base = {"ph": "0.3", "project": "p1", "ts": "2026-10-26T02:00:00Z", "origin": "principal", "by": "helper"}
    put("rec:q1", "p1", "owner", {**base, "ext": "decisions/0.3", "id": "r:q1", "type": "raised", "origin": "requester",
                                  "by": "project:p1", "key": "q1", "terms": {"text": "which cache?"}, "terms_rev": 1})
    put("rec:n1", "owner", "p1", {**base, "ext": "acts/0.3", "id": "n1", "type": "note", "key": "k1",
                                  "about": {"quote": "renew the domain"}, "note": "registrar says renew by Friday"})
    put("rec:s1", "owner", "p1", {**base, "ext": "decisions/0.3", "id": "s1", "type": "settled", "key": "q1",
                                  "outcome": "decided", "terms_rev": 1, "answer": {"text": "SQLite"},
                                  "authority": {"kind": "attested"}, "evidence": {"basis": "a receipt"}})
    put("rec:s3", "owner", "p1", {**base, "ext": "decisions/0.3", "id": "s3", "type": "settled", "key": "q2",
                                  "outcome": "dismissed", "terms_rev": 1, "note": "not needed",
                                  "authority": {"kind": "delegated"}})
    put("rec:r1", "owner", "p1", {**base, "ext": "acts/0.3", "id": "r1", "type": "related", "key": "k3",
                                  "relation": {"kind": "same_as", "target": {"project": "p0", "key": "k9"}}})
    put("rec:s2", "owner", "p1", {**base, "ext": "acts/0.3", "id": "s2", "type": "settled", "key": "k2",
                                  "outcome": "done", "terms_rev": 1, "authority": {"kind": "principal"}})
    c.commit()
    c.close()


class Librarian(unittest.TestCase):
    maxDiff = None

    def scenario(self, tmp: pathlib.Path) -> str:
        env = flow.Env(tmp)
        env.env["AGENT_HUB_LANG_UI"] = env.env["AGENT_HUB_LANG_CONTENT"] = golden_lang.LANG
        records(env)
        r = env.run("librarian.py")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = [("stdout", r.stdout), ("stderr", r.stderr),
               ("log", (env.hub / ".logs" / f"librarian-{TODAY}.log").read_text()),
               ("review-log.md", (env.hub / "review-log.md").read_text()),
               ("digest", (env.hub / "digest" / f"{TODAY}.md").read_text())]
        c = sqlite3.connect(env.tmp / "bus" / "bus.db")
        notes = [f"{ref}: {json.loads(p).get('note')}" for ref, p in
                 c.execute("SELECT ref, payload FROM events WHERE type='message.done' ORDER BY ref")]
        out.append(("done notes", "\n".join(notes)))
        for call in env.calls():
            out.append((f"prompt {call['point']} @ {call['cwd']}", call["prompt"]))
        text = "\n".join(f"=== {k}\n{v}" for k, v in out) + "\n"
        text = text.replace(str(tmp), "<TMP>").replace(TODAY, "<TODAY>")
        text = re.sub(r"\[\d\d:\d\d:\d\d\]", "[<T>]", text)
        return re.sub(r"\b[0-9a-f]{7,40}\b", "<sha>", text)

    def failing(self, tmp: pathlib.Path) -> str:
        """every model call fails, and a handoff names a project with no memory copy"""
        env = flow.Env(tmp)
        env.env["AGENT_HUB_LANG_UI"] = env.env["AGENT_HUB_LANG_CONTENT"] = golden_lang.LANG
        (env.hub / "bin" / "llm.py").write_text("import sys\nsys.stdin.read()\nsys.exit(1)\n")
        c = sqlite3.connect(env.tmp / "bus" / "bus.db")
        payload = {"v": 1, "type": "handoff", "handoff_id": 109, "effect": "relevant",
                   "handoff": {"fact": "f", "quote": None, "item": None, "why": "w", "ts": "2026-10-26T10:00:00"}}
        c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                  ("h9", "2026-10-26T10:00:00+08:00", "message", "owner", "pnone", None, json.dumps(payload)))
        c.commit()
        c.close()
        r = env.run("librarian.py")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = "=== failing log\n" + (env.hub / ".logs" / f"librarian-{TODAY}.log").read_text()
        text = text.replace(str(tmp), "<TMP>").replace(TODAY, "<TODAY>")
        text = re.sub(r"\[\d\d:\d\d:\d\d\]", "[<T>]", text)
        return re.sub(r"\b[0-9a-f]{7,40}\b", "<sha>", text)

    def test_run(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as d2:
            text = self.scenario(pathlib.Path(d).resolve()) + self.failing(pathlib.Path(d2).resolve())
        f = GOLDEN / "librarian.txt"
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())


if __name__ == "__main__":
    sys.exit(unittest.main())
