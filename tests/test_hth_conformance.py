"""agent-hub runs the Human Task Handoff (HTH 0.1, agent-ops-specs) conformance test data: proves that the
implementations already running (gen-todo's parsing, the registrar's keys and applying) meet the spec. The test data
is vendored in contracts/vendor/agent-ops-specs/hth-0.1/ (source commit in SOURCE).

Runs the zh-hant profile (this instance's token set); parse and apply also run the en profile as it is (agent-hub's
English set is that profile), apply under the content language that writes each profile. The zh-hant data's owner tag
is `Owner`; per the conformance/README rules it is replaced with this machine's configured owner.tag before comparing.
Applying: HTH 0.1 is the predecessor of Principal Handoff Acts 0.3, which bin/registrar.py implements
(tests/test_acts_conformance.py); an HTH event is applied as the 0.3 record it became (done / dropped / already_done
from the owner side = settled, reopened = reopened with its text to restore). Only the principal side's records change
the file: a done that an agent reports in a session, or that the registrar infers itself, is not from that side, so
those cases are "unchanged".

    bin/py -m unittest discover -s tests
"""
import importlib.util
import json
import os
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIX = ROOT / "contracts" / "vendor" / "agent-ops-specs" / "hth-0.1"
sys.path.insert(0, str(ROOT / "bin"))
import todo_tokens  # noqa: E402


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


gen_todo = load("gen_todo", "gen-todo.py")
registrar = load("hth_registrar", "registrar.py")
TAG = gen_todo.TAG
# the zh-hant key marker's word before the owner tag (the marker without its opening parenthesis, up to the tag)
LEAD = todo_tokens.tokens("zh-Hant")["key_marker"].split("{owner}")[0][1:]


def cases(file: str, profile: str = "zh-hant"):
    return [c for c in json.loads((FIX / file).read_text())["cases"] if c.get("profile", "zh-hant") == profile]


def own(x):
    """Replace the test data's owner tag `Owner` with this machine's owner.tag (in tags and key markers, including the
    marker form without the space)."""
    if isinstance(x, str):
        for a, b in ((todo_tokens.bracket("zh-Hant", "Owner"), todo_tokens.bracket("zh-Hant", TAG)),
                     (LEAD + "Owner", LEAD + TAG), (LEAD.rstrip() + "Owner", LEAD.rstrip() + TAG)):
            x = x.replace(a, b)
        return x
    if isinstance(x, list):
        return [own(v) for v in x]
    if isinstance(x, dict):
        return {k: own(v) for k, v in x.items()}
    return x


def lines_cmp(lines):
    """SPEC §8.2: compare non-empty lines, trailing whitespace stripped."""
    return [line.rstrip() for line in lines if line.strip()]


ASSIGNEE = {"owner": "owner", "agent": "agent", "tbd": "undecided"}   # gen-todo's roles -> the spec's assignees


def as_is(x):
    return x


class Parse(unittest.TestCase):
    def check(self, c, own):
        text = "\n".join(own(c["document"])) + "\n"
        exp = c["expect"]
        self.assertEqual(gen_todo.todo_sections(text) != [], exp["section"])
        items = {o: [] for o in gen_todo.OWNERS}
        problems: list = []
        gen_todo.parse("demo", text, items, problems)
        got = []
        for owner, rows in items.items():
            for _, t in rows:
                m = gen_todo.P.key.search(t)
                got.append((ASSIGNEE[owner], (t[:m.start()] if m else t).strip(), m.group(1) if m else None))
        want = [(t["assignee"], own(t["text"]), t["key"]) for t in exp["tasks"]]
        self.assertEqual(sorted(got), sorted(want))
        self.assertEqual(len(problems) - (not exp["section"]), len(exp["problems"]), "no section is one problem here")

    def test_parse_vectors(self):
        cs = cases("parse.json")
        self.assertTrue(cs)
        for c in cs:
            with self.subTest(case=c["id"]):
                self.check(c, own)

    def test_parse_vectors_english(self):
        """the spec's en profile is agent-hub's English set: its vectors run as they are"""
        cs = [c for c in json.loads((FIX / "parse.json").read_text())["cases"] if c.get("profile") == "en"]
        self.assertTrue(cs)
        for c in cs:
            with self.subTest(case=c["id"]):
                self.check(c, as_is)


class Keys(unittest.TestCase):
    def test_example_recipe(self):
        cs = json.loads((FIX / "keys.json").read_text())["cases"]
        self.assertTrue(cs)
        for c in cs:
            with self.subTest(case=c["id"]):
                self.assertEqual(registrar.task_key(c["project"], c["text"]), c["key"])


def record(ev: dict) -> dict:
    """An HTH 0.1 event as the Acts 0.3 record it became: the owner side's done / dropped / already_done is a
    settlement, reopened a reopening with the text to restore; an agent's report or the registrar's own inference is
    not from the principal side."""
    side = "principal" if ev.get("origin") == "owner" and not ev.get("inferred") else ev.get("origin")
    rec = {"origin": side, "key": ev["key"],
           "type": "settled" if ev["type"] in ("done", "dropped", "already_done") else ev["type"]}
    if ev["type"] == "reopened":
        rec["restore_text"] = ev.get("text")
    return rec


class Apply(unittest.TestCase):
    def test_apply_vectors(self):
        n = 0
        for profile, tr, lang in (("zh-hant", own, "zh-Hant"), ("en", as_is, "en")):
            for c in cases("apply.json", profile):
                n += 1
                with self.subTest(case=c["id"]), mock.patch.dict(os.environ, {"AGENT_HUB_LANG_CONTENT": lang}):
                    lines = tr(c["document"])
                    self.assertEqual(registrar.apply(lines, record(c["event"])), c["expect"]["changed"])
                    self.assertEqual(lines_cmp(lines), lines_cmp(tr(c["expect"]["document"])))
        self.assertTrue(n)


if __name__ == "__main__":
    unittest.main()
