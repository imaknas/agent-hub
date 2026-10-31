"""The readers of STATE.md's to-do block and of the review-log reason label, function by function (contracts
state-todo / todo-md): gen-todo.py's parse, registrar.py's owner lines, keys, strike / re-add / re-link and
removal_reason, librarian.py's key marker as edits.py checks it, contracts.check_state_todo. The flows around them are
tests/test_registrar_flow.py, tests/test_librarian_flow.py and the E1 goldens; this pins the line-level rules.
The zh-Hant tokens come from the code's own token set; their other accepted spellings from
tests/fixtures/todo-readers.json.

    bin/py -m unittest tests.test_todo_readers
"""
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import contracts  # noqa: E402
import edits  # noqa: E402
import hubconf  # noqa: E402
import prompts  # noqa: E402


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


gen_todo = load("gen_todo_r", "gen-todo.py")
registrar = load("registrar_r", "registrar.py")
librarian = load("librarian_r", "librarian.py")
FX = json.loads((ROOT / "tests" / "fixtures" / "todo-readers.json").read_text())
ZH = prompts.TOKENS["zh-Hant"]
TAG = hubconf.owner_tag()
LIB_TAG = librarian.OWNER
HEAD, NONE, TBD = ZH["todo_heading"], ZH["todo_none"], ZH["tbd"]


def tag(t: str) -> str:
    return ZH["tag"].format(t)


def mark(key: str, owner: str = TAG, form: str | None = None) -> str:
    return (FX[form] if form else ZH["key_marker"]).format(owner=owner, key=key)


def item(t: str, text: str) -> str:
    return f"- [ ] {tag(t)}{text}"


def parse(text: str) -> tuple[dict, list]:
    items = {o: [] for o in gen_todo.OWNERS}
    problems: list[str] = []
    gen_todo.parse("p", text, items, problems)
    return items, problems


def state(*todo: str, heading: str = HEAD) -> str:
    return "# STATE\n\n## Now\n\nAbout.\n\n" + heading + "\n\n" + "\n".join(todo) + "\n\n## References\n\n- a link\n"


class GenTodoParse(unittest.TestCase):
    def test_tagged_items_by_owner(self):
        items, problems = parse(state(item(TAG, "renew the domain"), item("agent", "write tests"),
                                      item(TBD, "go public?")))
        self.assertEqual(problems, [])
        self.assertEqual(items["owner"], [("p", "renew the domain")])
        self.assertEqual(items["agent"], [("p", "write tests")])
        self.assertEqual(items["tbd"], [("p", "go public?")])

    def test_empty_marker_and_notes_are_not_items(self):
        items, problems = parse(state(NONE, "> a note", "a continuation line"))
        self.assertEqual(problems, [])
        self.assertEqual(sum(len(v) for v in items.values()), 0)

    def test_grouped_form(self):
        items, problems = parse(state(f"### {tag(TAG)}", "- [ ] renew the domain", f"#### {tag('agent')}",
                                      "- [ ] write tests"))
        self.assertEqual(problems, [])
        self.assertEqual(items["owner"], [("p", "renew the domain")])
        self.assertEqual(items["agent"], [("p", "write tests")])

    def test_unknown_group_does_not_inherit(self):
        items, problems = parse(state(f"### {tag('agent')}", "- [ ] write tests", f"### {tag(FX['unknown_tag'])}",
                                      "- [ ] whose is this"))
        self.assertEqual(items["agent"], [("p", "write tests")])
        self.assertEqual(len(problems), 1)
        self.assertIn("whose is this", problems[0])

    def test_problems(self):
        _items, problems = parse(state("- [ ] no tag", item(FX["unknown_tag"], "unknown tag"),
                                       f"- [x] {tag('agent')}done already"))
        self.assertEqual(len(problems), 3)

    def test_no_section(self):
        _items, problems = parse("# STATE\n\n## Now\n\nAbout.\n")
        self.assertEqual(len(problems), 1)

    def test_section_ends_at_next_heading(self):
        items, problems = parse(state(item("agent", "write tests")) + "\n" + item("agent", "another section") + "\n")
        self.assertEqual(items["agent"], [("p", "write tests")])
        self.assertEqual(problems, [])

    def test_owner_key_half_and_full_width(self):
        for form in (None, "full_width_marker", "spaced_marker"):
            with self.subTest(form=form):
                m = gen_todo.P.key.search("renew" + mark("task-p-0123456789", form=form))
                self.assertEqual(m.group("key"), "task-p-0123456789")
        self.assertIsNone(gen_todo.P.key.search("renew" + mark("task-p-0123456789") + " later"), "only at the end")


def content(lang: str):
    """the content language the registrar writes in"""
    return mock.patch.dict("os.environ", {"AGENT_HUB_LANG_CONTENT": lang})


class Registrar(unittest.TestCase):
    def setUp(self):
        p = content("zh-Hant")   # this instance's setting: what the registrar writes is the zh-Hant set
        p.start()
        self.addCleanup(p.stop)

    def test_owner_lines_and_keys(self):
        lines = state(item(TAG, "renew the domain" + mark("task-p-1")),
                      item(TAG, "sign" + mark("task-p-2", form="full_width_marker")),
                      item(TAG, "pay"), item("agent", "write tests"), f"### {tag(TAG)}", "- [ ] grouped").splitlines()
        got = [(t["text"], t["key"]) for t in registrar.owner_lines(lines)]
        self.assertEqual(got, [("renew the domain", "task-p-1"), ("sign", "task-p-2"), ("pay", None), ("grouped", None)])
        self.assertEqual(registrar.line_texts(lines), ["renew the domain", "sign", "pay", "write tests", "grouped"])

    def test_stamp(self):
        self.assertEqual(registrar.stamp("task-p-1"), mark("task-p-1"))

    def test_strike_leaves_empty_marker(self):
        lines = state(item(TAG, "renew the domain" + mark("task-p-1"))).splitlines()
        self.assertTrue(registrar.strike(lines, "task-p-1"))
        self.assertIn(NONE, lines)
        self.assertNotIn("renew the domain", "\n".join(lines))
        self.assertFalse(registrar.strike(lines, "task-p-1"))

    def test_strike_keeps_other_lines(self):
        lines = state(item(TAG, "renew the domain" + mark("task-p-1")), item("agent", "write tests")).splitlines()
        registrar.strike(lines, "task-p-1")
        self.assertNotIn(NONE, lines)
        self.assertIn(item("agent", "write tests"), lines)

    def test_readd_replaces_empty_marker(self):
        lines = state(NONE).splitlines()
        self.assertTrue(registrar.readd(lines, "task-p-1", "sign"))
        self.assertIn(item(TAG, "sign" + mark("task-p-1")), lines)
        self.assertNotIn(NONE, lines)
        self.assertFalse(registrar.readd(lines, "task-p-1", "sign"), "already there")

    def test_readd_appends_after_the_last_item(self):
        lines = state(item("agent", "write tests")).splitlines()
        registrar.readd(lines, "task-p-1", "sign")
        i = lines.index(item("agent", "write tests"))
        self.assertEqual(lines[i + 1], item(TAG, "sign" + mark("task-p-1")))

    def test_readd_creates_the_section(self):
        lines = "# STATE\n\n## Now\n\nAbout.".splitlines()
        registrar.readd(lines, "task-p-1", "sign")
        self.assertEqual(lines[-3:], [HEAD, "", item(TAG, "sign" + mark("task-p-1"))])

    def test_relink(self):
        lines = state(item(TAG, "sign  the contract")).splitlines()
        self.assertEqual(registrar.relink(lines, {"task-p-9": "sign the contract"}), ["task-p-9"])
        self.assertIn(item(TAG, "sign  the contract" + mark("task-p-9")), lines)

    def test_removal_reason(self):
        label = FX["reason_label"]
        with tempfile.TemporaryDirectory() as d:
            log = pathlib.Path(d) / "review-log.md"
            log.write_text(f"## 2026-10-30 · p\n\n**commit**: `abc`\n\n{label}: done\n\n```diff\n"
                           f"-{item(TAG, 'renew the domain' + mark('task-p-1'))}\n```\n\n---\n\n"
                           f"## 2026-10-31 · p\n\n{label}: another\n\n```diff\n"
                           f"-{item(TAG, 'sign' + mark('task-p-2'))}\n```\n")
            with mock.patch.object(registrar, "REVIEW_LOG", log):
                self.assertEqual(registrar.removal_reason("task-p-1"), "2026-10-30 · p:done")
                self.assertEqual(registrar.removal_reason("task-p-2"), "2026-10-31 · p:another")
                self.assertIsNone(registrar.removal_reason("task-p-3"))


class LibrarianMarkers(unittest.TestCase):
    DOC = state(item(LIB_TAG, "renew the domain" + mark("task-p-1", LIB_TAG)), item("agent", "write tests"))

    def test_markers_found(self):
        self.assertEqual(edits.markers(self.DOC, librarian.KEY_MARK), [mark("task-p-1", LIB_TAG)])
        full = mark("task-p-2", LIB_TAG, "full_width_marker")
        self.assertEqual(edits.markers("x" + full, librarian.KEY_MARK), [full])

    def test_rewritten_key_is_caught(self):
        after = self.DOC.replace("task-p-1", "task-p-x")
        self.assertTrue(edits.check_markers(self.DOC, after, librarian.KEY_MARK))

    def test_whole_line_removed_is_fine(self):
        after = self.DOC.replace(item(LIB_TAG, "renew the domain" + mark("task-p-1", LIB_TAG)) + "\n", "")
        self.assertEqual(edits.check_markers(self.DOC, after, librarian.KEY_MARK), [])


class ContractCheck(unittest.TestCase):
    C = ROOT / "contracts" / "state-todo.v1.md"
    OWNER = "Ada"

    def test_valid(self):
        text = state(item(self.OWNER, "renew" + mark("task-p-1", self.OWNER)), item(TBD, "go public?"),
                     f"### {tag('agent')}", "- [ ] write tests")
        self.assertEqual(contracts.check_state_todo(text, self.C, self.OWNER), [])
        self.assertEqual(contracts.check_state_todo(state(NONE), self.C, self.OWNER), [])

    def test_problems(self):
        self.assertTrue(contracts.check_state_todo(state("- [ ] no tag"), self.C, self.OWNER))
        self.assertTrue(contracts.check_state_todo("# STATE\n", self.C, self.OWNER))


# ---- the English set, and files that mix the two (contracts/state-todo.v2.md): every reader, whatever the settings

EN = prompts.TOKENS["en"]


def en_item(t: str, text: str) -> str:
    return f"- [ ] {EN['tag'].format(t)} {text}"


def en_mark(key: str, owner: str = TAG) -> str:
    return " " + EN["key_marker"].format(owner=owner, key=key)


class EnglishGenTodo(unittest.TestCase):
    def test_tagged_items(self):
        items, problems = parse(state(en_item(TAG, "renew the domain"), en_item("agent", "write tests"),
                                      en_item("tbd", "go public?"), EN["todo_none"], heading=EN["todo_heading"]))
        self.assertEqual(problems, [])
        self.assertEqual(items, {"owner": [("p", "renew the domain")], "agent": [("p", "write tests")],
                                 "tbd": [("p", "go public?")]})

    def test_grouped_form_and_links(self):
        items, problems = parse(state(f"### [{TAG}]", "- [ ] renew the domain", "- [ ] [the docs](https://x.org) first",
                                      en_item("agent", "read [the notes](n.md)"), heading=EN["todo_heading"]))
        self.assertEqual(problems, [])
        self.assertEqual(items["owner"], [("p", "renew the domain"), ("p", "[the docs](https://x.org) first")])
        self.assertEqual(items["agent"], [("p", "read [the notes](n.md)")])

    def test_problems(self):
        _items, problems = parse(state("- [ ] no tag", en_item("someone", "unknown tag"), en_item(TBD, "zh word"),
                                       "- [x] [agent] done already", heading=EN["todo_heading"]))
        self.assertEqual(len(problems), 4)

    def test_owner_key(self):
        for line in ("renew" + en_mark("task-p-0123456789"), f"renew (waiting on {TAG}:task-p-0123456789)",
                     "renew" + en_mark("task-p-0123456789").translate(str.maketrans("(:)", "".join(map(chr, (0xFF08, 0xFF1A, 0xFF09)))))):
            with self.subTest(line=line):
                self.assertEqual(gen_todo.P.key.search(line).group("key"), "task-p-0123456789")

    def test_mixed_lines(self):
        items, problems = parse(state(item(TAG, "old line" + mark("task-p-1")), en_item(TAG, "new line" + en_mark("task-p-2")),
                                      item(TAG, "zh tag, en marker" + en_mark("task-p-3")), en_item("agent", "a"),
                                      item("agent", "b"), NONE, heading=EN["todo_heading"]))
        self.assertEqual(problems, [])
        self.assertEqual([gen_todo.P.key.search(t).group("key") for _p, t in items["owner"]],
                         ["task-p-1", "task-p-2", "task-p-3"])
        self.assertEqual(items["agent"], [("p", "a"), ("p", "b")])

    def test_two_sections_are_both_read_and_reported(self):
        text = state(item("agent", "a")) + "\n" + EN["todo_heading"] + "\n\n" + en_item("agent", "b") + "\n"
        items, problems = parse(text)
        self.assertEqual(items["agent"], [("p", "a"), ("p", "b")])
        self.assertEqual(len(problems), 1)


class EnglishRegistrar(unittest.TestCase):
    def setUp(self):
        p = content("en")
        p.start()
        self.addCleanup(p.stop)

    def test_writes_the_english_set(self):
        self.assertEqual(registrar.stamp("task-p-1"), en_mark("task-p-1"))
        lines = state(en_item(TAG, "renew" + en_mark("task-p-1")), heading=EN["todo_heading"]).splitlines()
        registrar.strike(lines, "task-p-1")
        self.assertIn(EN["todo_none"], lines)
        registrar.readd(lines, "task-p-2", "sign")
        self.assertIn(en_item(TAG, "sign" + en_mark("task-p-2")), lines)
        self.assertNotIn(EN["todo_none"], lines)
        lines = "# STATE\n\n## Now\n\nAbout.".splitlines()
        registrar.readd(lines, "task-p-3", "pay")
        self.assertEqual(lines[-3:], [EN["todo_heading"], "", en_item(TAG, "pay" + en_mark("task-p-3"))])

    def test_mixed_file_only_what_it_writes_changes(self):
        """an English instance with an older zh-Hant section: the stamp is English, the zh line's tag and the heading
        stay as they are"""
        lines = state(item(TAG, "old line"), item("agent", "b")).splitlines()
        self.assertEqual(registrar.relink(lines, {"task-p-9": "old line"}), ["task-p-9"])
        self.assertIn(item(TAG, "old line") + en_mark("task-p-9"), lines)
        self.assertIn(HEAD, lines)
        self.assertEqual([t["key"] for t in registrar.owner_lines(lines)], ["task-p-9"], "read back")
        registrar.strike(lines, "task-p-9")
        registrar.strike(lines, "nothing")
        self.assertIn(item("agent", "b"), lines)

    def test_owner_lines(self):
        lines = state(en_item(TAG, "renew the domain" + en_mark("task-p-1")), en_item(TAG, "pay"),
                      item(TAG, "zh line" + mark("task-p-2")), en_item("agent", "write tests"),
                      f"### [{TAG}]", "- [ ] grouped", heading=EN["todo_heading"]).splitlines()
        got = [(t["text"], t["key"]) for t in registrar.owner_lines(lines)]
        self.assertEqual(got, [("renew the domain", "task-p-1"), ("pay", None), ("zh line", "task-p-2"), ("grouped", None)])
        self.assertEqual(registrar.line_texts(lines), ["renew the domain", "pay", "zh line", "write tests", "grouped"])

    def test_strike_by_key_in_either_set(self):
        for marker in (en_mark("task-p-1"), mark("task-p-1")):
            with self.subTest(marker=marker):
                lines = state(en_item(TAG, "renew" + marker), en_item("agent", "a"), heading=EN["todo_heading"]).splitlines()
                self.assertTrue(registrar.strike(lines, "task-p-1"))
                self.assertNotIn("renew", "\n".join(lines))

    def test_readd_knows_the_english_empty_marker_and_heading(self):
        lines = state(EN["todo_none"], heading=EN["todo_heading"]).splitlines()
        registrar.readd(lines, "task-p-1", "sign")
        self.assertNotIn(EN["todo_none"], lines)
        self.assertEqual(lines.count(EN["todo_heading"]), 1)
        self.assertNotIn(HEAD, lines, "no second section")

    def test_removal_reason_either_label(self):
        with tempfile.TemporaryDirectory() as d:
            log = pathlib.Path(d) / "review-log.md"
            log.write_text(f"## 2026-10-30 · p\n\n**Reason**: done\n\n```diff\n-{en_item(TAG, 'x' + en_mark('task-p-1'))}\n```\n"
                           f"\n## 2026-10-31 · p\n\n{FX['reason_label']}{chr(0xFF1A)} full-width colon\n\n```diff\n-x{mark('task-p-2')}\n```\n")
            with mock.patch.object(registrar, "REVIEW_LOG", log):
                self.assertEqual(registrar.removal_reason("task-p-1"), "2026-10-30 · p:done")
                self.assertEqual(registrar.removal_reason("task-p-2"), "2026-10-31 · p:full-width colon")


class EnglishLibrarianMarkers(unittest.TestCase):
    def test_both_markers_protected(self):
        doc = state(en_item(LIB_TAG, "renew" + en_mark("task-p-1", LIB_TAG)), item(LIB_TAG, "x" + mark("task-p-2", LIB_TAG)),
                    heading=EN["todo_heading"])
        self.assertEqual(edits.markers(doc, librarian.KEY_MARK),
                         [en_mark("task-p-1", LIB_TAG).strip(), mark("task-p-2", LIB_TAG)])
        self.assertTrue(edits.check_markers(doc, doc.replace("task-p-1", "task-p-x"), librarian.KEY_MARK))


if __name__ == "__main__":
    unittest.main()
