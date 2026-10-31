"""agent-hub runs the Principal Handoff Acts 0.3 conformance test data (agent-ops-specs, source in
contracts/vendor/.../SOURCE): proves that bin/registrar.py (the registry's steps) and bin/gen-todo.py (classifying the
to-do section) meet the spec.

Runs the zh-hant profile (this instance's token set). The owner tag in the data is `Owner`; per the
conformance/README rules it is replaced with this machine's configured owner.tag before comparing. parse's full
classification (agent, to be decided, format problems) is in gen-todo; the registrar handles only the owner's lines.
parse also runs the spec's en profile, translated into agent-hub's English set (EN_PROFILE: the spec's default English
profile differs from it in the heading, the tbd label and the owner tag, which agent-hub takes from settings).

    bin/py -m unittest discover -s tests
"""
import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIX = ROOT / "contracts" / "vendor" / "agent-ops-specs" / "principal-handoff-0.3" / "acts"


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


registrar = load("registrar", "registrar.py")
gen_todo = load("gen_todo", "gen-todo.py")
TAG = registrar.TAG


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


def nonblank(lines):
    """acts §9.2: compare non-empty lines, trailing whitespace stripped."""
    return [l.rstrip() for l in lines if l.strip()]


ASSIGNEE = {"owner": "principal", "agent": "agent", "tbd": "undecided"}   # gen-todo's roles -> the spec's assignees
# the spec's en profile -> agent-hub's English set (bin/todo_tokens.py): the only differences between the two
EN_PROFILE = {"## Tasks": "## To-do", "[owner]": f"[{TAG}]", "[undecided]": "[tbd]",
              "(waiting on owner:": f"(waiting on {TAG}:"}


def en(x):
    if isinstance(x, str):
        for a, b in EN_PROFILE.items():
            x = x.replace(a, b)
        return x
    if isinstance(x, list):
        return [en(v) for v in x]
    return x


class Acts(unittest.TestCase):
    def check_parse(self, c, own):
        text = "\n".join(own(c["document"])) + "\n"
        exp = c["expect"]
        self.assertEqual(gen_todo.todo_sections(text) != [], exp["section"])
        items = {o: [] for o in gen_todo.OWNERS}
        problems: list = []
        gen_todo.parse("demo", text, items, problems)
        got = []
        for owner, rows in items.items():
            for _, t in rows:
                m = registrar.KEY.search(t)
                got.append((ASSIGNEE[owner], (t[:m.start()] if m else t).strip(), m.group(1) if m else None))
        want = [(t["assignee"], own(t["text"]), t["key"]) for t in exp["tasks"]]
        self.assertEqual(sorted(got), sorted(want))
        self.assertEqual(len(problems) - (not exp["section"]), len(exp["problems"]), "no section is one problem here")
        # the owner lines the registrar sees match the spec
        mine = [(t["text"], t["key"]) for t in registrar.owner_lines(own(c["document"]))]
        self.assertEqual(sorted(mine), sorted((own(t["text"]), t["key"]) for t in exp["tasks"]
                                              if t["assignee"] == "principal"))

    def test_parse(self):
        cs = cases("parse.json")
        self.assertTrue(cs)
        for c in cs:
            with self.subTest(case=c["id"]):
                self.check_parse(c, own)

    def test_parse_english(self):
        cs = [c for c in json.loads((FIX / "parse.json").read_text())["cases"] if c.get("profile") == "en"]
        self.assertTrue(cs)
        for c in cs:
            with self.subTest(case=c["id"]):
                self.check_parse(c, en)

    def test_keys(self):
        for c in json.loads((FIX / "keys.json").read_text())["cases"]:
            with self.subTest(case=c["id"]):
                self.assertEqual(registrar.task_key(c["project"], c["text"]), c["key"])

    def test_register(self):
        cs = cases("register.json")
        self.assertTrue(cs)
        for c in cs:
            with self.subTest(case=c["id"]):
                lines = own(c["document"])
                raised = []
                for i, k, text in registrar.candidates(lines, c["project"]):
                    raised.append({"key": k, "text": text})
                    lines[i] = lines[i].rstrip() + registrar.stamp(k)
                self.assertEqual(raised, own(c["expect"]["raised"]))
                self.assertEqual(nonblank(lines), nonblank(own(c["expect"]["document"])))
                self.assertEqual(registrar.candidates(lines, c["project"]), [], "not sent a second time")

    def test_relink(self):
        cs = cases("relink.json")
        self.assertTrue(cs)
        for c in cs:
            with self.subTest(case=c["id"]):
                lines = own(c["document"])
                self.assertEqual(registrar.relink(lines, own(c["vanished"])), c["expect"]["relinked"])
                self.assertEqual(nonblank(lines), nonblank(own(c["expect"]["document"])))

    def test_amend(self):
        cs = cases("amend.json")
        self.assertTrue(cs)
        for c in cs:
            with self.subTest(case=c["id"]):
                self.assertEqual(registrar.amendments(own(c["document"]), own(c["latest"])), own(c["expect"]))

    def test_apply(self):
        cs = cases("apply.json")
        self.assertTrue(cs)
        for c in cs:
            with self.subTest(case=c["id"]):
                lines = own(c["document"])
                self.assertEqual(registrar.apply(lines, own(c["record"])), c["expect"]["changed"])
                self.assertEqual(nonblank(lines), nonblank(own(c["expect"]["document"])))

    def test_reconcile(self):
        cs = cases("reconcile.json")
        self.assertTrue(cs)
        for c in cs:
            with self.subTest(case=c["id"]):
                self.assertEqual(registrar.reconcile(own(c["document"]), own(c["open"])), c["expect"])


if __name__ == "__main__":
    unittest.main()
