"""agent-hub runs the Human Task Handoff (HTH 0.1, agent-ops-specs) conformance test data: proves that the
implementations already running (gen-todo's parsing, the task registry's keys and applying) meet the spec. The test
data is vendored in contracts/vendor/agent-ops-specs/hth-0.1/ (source commit in SOURCE).

Runs the zh-hant profile (this instance's token set); parse also runs the en profile as it is (agent-hub's English set
is that profile). The zh-hant data's owner tag is `Owner`; per the conformance/README rules it is replaced with this
machine's configured owner.tag before comparing.
Applying routes: agent-hub applies only events sent back from the owner side (hubbus open-handoffs takes only
handoffs sent from the owner address); a done that an agent reports in a session, or that the registrar infers itself,
never reaches apply_keyed, so here those two kinds of case are "unchanged".

    bin/py -m unittest discover -s tests
"""
import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIX = ROOT / "contracts" / "vendor" / "agent-ops-specs" / "hth-0.1"


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


gen_todo = load("gen_todo", "gen-todo.py")
tasks = load("hth_tasks", "knas-tasks.py")  # the task registry; renaming it is a to-do in the new-user design
TAG = gen_todo.TAG


def cases(file: str):
    return [c for c in json.loads((FIX / file).read_text())["cases"] if c.get("profile", "zh-hant") == "zh-hant"]


def own(x):
    """Replace the test data's owner tag `Owner` with this machine's owner.tag (in tags and key markers, including the
    "等Owner" form without a space)."""
    if isinstance(x, str):
        return x.replace("〔Owner〕", f"〔{TAG}〕").replace("等 Owner", f"等 {TAG}").replace("等Owner", f"等{TAG}")
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
                self.assertEqual(tasks.task_key(c["project"], c["text"]), c["key"])


class Apply(unittest.TestCase):
    def test_apply_vectors(self):
        cs = cases("apply.json")
        self.assertTrue(cs)
        for c in cs:
            with self.subTest(case=c["id"]):
                lines = own(c["document"])
                ev = c["event"]
                routed = ev.get("origin") == "owner" and not ev.get("inferred")
                changed = tasks.apply_keyed(lines, {"effect": ev["type"], "reply_to": ev["key"],
                                                         "item": ev.get("text")}) if routed else False
                self.assertEqual(changed, c["expect"]["changed"])
                self.assertEqual(lines_cmp(lines), lines_cmp(own(c["expect"]["document"])))


if __name__ == "__main__":
    unittest.main()
