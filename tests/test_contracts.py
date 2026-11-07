"""Check ②: this repo's example data validated against its own contracts (and the vendored copies). Does not
depend on whether other repos are present.

    bin/py -m unittest discover -s tests
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import contracts  # noqa: E402
import todo_tokens  # noqa: E402

C = ROOT / "contracts"
F = C / "fixtures"
# example files use a neutral owner tag; real files use the configured one (hub.json owner.tag). Read from the v1
# example's first owner heading.
_PRE, _POST = todo_tokens.tokens("zh-Hant")["todo_md_headings"][0].split("{owner}")
OWNER = next(l[len(_PRE):len(l) - len(_POST)] for l in (F / "todo-md.v1.example.md").read_text().splitlines()
             if l.startswith(_PRE) and l.endswith(_POST))


class Markdown(unittest.TestCase):
    def test_todo_md_fixture(self):
        self.assertEqual(contracts.check_todo_md((F / "todo-md.v1.example.md").read_text(), C / "todo-md.v1.md", OWNER), [])

    def test_todo_md_rejects_renamed_heading(self):
        text = (F / "todo-md.v1.example.md").read_text()
        heading = todo_tokens.todo_md_headings("zh-Hant", OWNER)["owner"]
        self.assertIn(heading, text)
        bad = text.replace(heading, "## My things")
        self.assertTrue(contracts.check_todo_md(bad, C / "todo-md.v1.md", OWNER))

    def test_state_todo_fixture(self):
        self.assertEqual(contracts.check_state_todo((F / "state-todo.v1.example.md").read_text(),
                                                    C / "state-todo.v1.md", OWNER), [])

    def test_state_todo_key_regex(self):
        import re
        key = re.compile(contracts.md_rules(C / "state-todo.v1.md", OWNER)["regex:key"][0])
        m = key.search(todo_tokens.item("zh-Hant", "owner", OWNER, "x")
                       + todo_tokens.stamp("zh-Hant", OWNER, "task-p-0123456789"))
        self.assertEqual(m.group(1), "task-p-0123456789")


class MarkdownV2(unittest.TestCase):
    """v2 = v1 + the English token set: v1 files stay valid, English and mixed files are valid too."""
    EN = "Ada"   # the English examples' owner tag

    def test_state_todo_examples(self):
        for f, owner in (("state-todo.v1.example.md", OWNER), ("state-todo.v2.example.md", self.EN),
                         ("state-todo.v2.mixed.example.md", self.EN)):
            with self.subTest(f=f):
                self.assertEqual(contracts.check_state_todo((F / f).read_text(), C / "state-todo.v2.md", owner), [])

    def test_state_todo_v1_rejects_english(self):
        self.assertTrue(contracts.check_state_todo((F / "state-todo.v2.example.md").read_text(), C / "state-todo.v1.md",
                                                   self.EN))

    def test_state_todo_problems(self):
        text = (F / "state-todo.v2.example.md").read_text()
        for bad in ("- [ ] [someone] Unknown tag", "- [ ] No tag at all", "- [x] [agent] Done, not removed",
                    "- [ ] [Ada] The configured tag is not an English label"):
            with self.subTest(bad=bad):
                self.assertTrue(contracts.check_state_todo(text.replace("- [ ] [agent] Add tests", bad),
                                                           C / "state-todo.v2.md", self.EN))

    def test_state_todo_key_regex(self):
        import re
        import todo_tokens
        keys = [re.compile(r) for r in contracts.md_rules(C / "state-todo.v2.md", self.EN)["regex:key"]]
        full = str.maketrans("(:)", "".join(map(chr, (0xFF08, 0xFF1A, 0xFF09))))
        for line in ("x" + todo_tokens.marker("zh-Hant", self.EN, "task-p-0123456789"),
                     "x (waiting on owner: task-p-0123456789)", "x(waiting on owner:task-p-0123456789)".translate(full)):
            with self.subTest(line=line):
                self.assertEqual([m.group(1) for k in keys if (m := k.search(line))], ["task-p-0123456789"])
        for line in ("x (waiting on Ada: task-p-0123456789)", "x (waiting on owner: two words)"):
            with self.subTest(line=line):   # the en profile: the word `owner`, a key without whitespace
                self.assertEqual([m for k in keys if (m := k.search(line))], [])

    def test_todo_md_examples(self):
        for f, owner in (("todo-md.v1.example.md", OWNER), ("todo-md.v2.example.md", self.EN)):
            with self.subTest(f=f):
                self.assertEqual(contracts.check_todo_md((F / f).read_text(), C / "todo-md.v2.md", owner), [])

    def test_todo_md_english_needs_v2(self):
        en = (F / "todo-md.v2.example.md").read_text()
        self.assertTrue(contracts.check_todo_md(en, C / "todo-md.v1.md", self.EN), "a v1 reader refuses it")
        self.assertTrue(contracts.check_todo_md(en.replace("todo-md v2", "todo-md v1"), C / "todo-md.v2.md", self.EN),
                        "English headings under a v1 marker")

    def test_todo_md_one_set_per_file(self):
        import todo_tokens
        zh_agent = todo_tokens.todo_md_headings("zh-Hant", self.EN)["agent"]
        mixed = (F / "todo-md.v2.example.md").read_text().replace("## An agent can do", zh_agent)
        self.assertTrue(contracts.check_todo_md(mixed, C / "todo-md.v2.md", self.EN))

    def test_v1_machine_parts_unchanged(self):
        """the readers' vendored v1 copies must keep comparing equal: v1's rules are frozen"""
        rules = contracts.md_rules(C / "state-todo.v1.md")
        self.assertEqual(sorted(rules), ["regex:bare", "regex:group", "regex:item", "regex:key"])
        self.assertEqual(sorted(contracts.md_rules(C / "todo-md.v1.md")), ["heading:owner", "regex:item", "regex:project"])


class TokenModuleAgrees(unittest.TestCase):
    """bin/todo_tokens.py (what the platform's readers and writers use) and the v2 contracts (what other repos vendor)
    define the same two token sets"""
    EN = "Ada"

    def setUp(self):
        import todo_tokens
        self.t = todo_tokens

    def test_headings_and_empty_markers(self):
        rules = contracts.md_rules(C / "state-todo.v2.md", self.EN)
        sets = [self.t.tokens(lang) for lang in ("zh-Hant", "en")]
        self.assertEqual(rules["heading:section"], [s["todo_heading"] for s in sets])
        self.assertEqual(rules["heading:none"], [s["todo_none"] for s in sets])
        md = contracts.md_rules(C / "todo-md.v2.md", self.EN)
        self.assertEqual(md["heading:owner"], list(self.t.todo_md_headings("zh-Hant", self.EN).values()))
        self.assertEqual(md["heading:owner-en"], list(self.t.todo_md_headings("en", self.EN).values()))
        v1 = contracts.md_rules(C / "todo-md.v1.md", self.EN)
        self.assertEqual(v1["heading:owner"], md["heading:owner"])

    def test_same_lines_accepted(self):
        import re
        rules = {k: [re.compile(r) for r in v] for k, v in contracts.md_rules(C / "state-todo.v2.md", self.EN).items()
                 if k.startswith("regex:")}
        P = self.t.patterns(self.EN)
        lines = []
        for lang in ("zh-Hant", "en"):
            for role in self.t.ROLES:
                lines.append(self.t.item(lang, role, self.EN, "do it"))
                lines.append(f"### {self.t.tag(lang, role, self.EN)}")
            for other in ("someone", self.EN, "owner", "tbd"):   # unknown in this set, or the other set's word
                lines.append(f"- [ ] {self.t.bracket(lang, other)} do it")
                lines.append(f"### {self.t.bracket(lang, other)}")
            lines.append(self.t.item(lang, "owner", self.EN, "x") + self.t.stamp(lang, self.EN, "task-p-0123456789"))
        zh_tbd, full = self.t.tokens("zh-Hant")["tbd"], str.maketrans("(:)", "".join(map(chr, (0xFF08, 0xFF1A, 0xFF09))))
        lines += ["- [ ] [agent](https://example.org) a link", "- [ ] no tag",
                  self.t.item("zh-Hant", "tbd", self.EN, "x").replace(zh_tbd, "undecided"),
                  f"- [ ] {self.t.bracket('en', zh_tbd)} the other set's word", f"### {self.t.bracket('en', zh_tbd)}",
                  "x" + self.t.marker("zh-Hant", self.EN, "task-p-1").translate(full),
                  "x" + self.t.marker("en", self.EN, "task-p-1").translate(full), "x (waiting on Bob: task-p-1)",
                  "x (waiting on owner: two words)"]
        for l in lines:
            with self.subTest(line=l):
                m = P.item.match(l)
                self.assertEqual(any(r.match(l) for r in rules["regex:item"]), bool(m and P.role(m)))
                g = P.group.match(l)
                self.assertEqual(any(r.match(l) for r in rules["regex:group"]), bool(g and P.role(g)))
                self.assertEqual(any(r.match(l) for r in rules["regex:bare"]), bool(P.bare.match(l)))
                self.assertEqual([m.group(1) for r in rules["regex:key"] if (m := r.search(l))],
                                 [k.group("key")] if (k := P.key.search(l)) else [])


class LlmPoints(unittest.TestCase):
    """The shared format of the judgment point registry (contracts/llm-points.v1.schema.json): our own registry must
    be valid, and so must the other side's way of writing it."""

    def setUp(self):
        import minischema
        self.errors = minischema.errors
        self.schema = json.loads((C / "llm-points.v1.schema.json").read_text())

    def test_own_registry_valid(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("needs PyYAML")
        self.assertEqual(self.errors(self.schema, yaml.safe_load((C / "llm-points.yaml").read_text())), [])

    def test_other_side_example_valid(self):
        """The example from the other side's design doc (only the shared fields + its own optional fields)."""
        doc = {"v": 1, "points": {"packet_fit": {
            "caller": "core/prepare.py", "purpose": "whether a job opening is a fit", "model_group": "draft", "material": "personal",
            "output": {"format": "json", "schema": "llm/packet_fit.schema.json"}, "on_failure": "retry_later",
            "eval": {"scorer": "field:verdict", "truth": "the user's later answer"},
            "kind": "decide", "labels": ["fit", "not_fit"]}}}
        self.assertEqual(self.errors(self.schema, doc), [])

    def test_rejects_unknown_field_and_old_values(self):
        bad = {"v": 1, "points": {"x": {"caller": "a", "purpose": "b", "output": {"format": "text"},
                                        "on_failure": "caller", "surprise": 1}}}
        errs = " ".join(self.errors(self.schema, bad))
        self.assertIn("on_failure", errs)
        self.assertIn("surprise", errs)

    def test_shared_fields_required(self):
        errs = " ".join(self.errors(self.schema, {"v": 1, "points": {"x": {"caller": "a"}}}))
        for f in ("purpose", "output", "on_failure"):
            self.assertIn(f, errs)


class Neutral(unittest.TestCase):
    def test_consumer_facing_contracts_dont_name_the_platform(self):
        """Contracts and examples that other repos vendor must not mention this platform (some of those repos are open
        source)."""
        for p in [*C.glob("todo-md.v*.md"), *C.glob("state-todo.v*.md"), *C.glob("reflect-week.v*.schema.json"),
                  *F.glob("*.md")]:
            text = p.read_text()
            for word in ("agent-hub", "librarian", "bin/"):
                self.assertNotIn(word, text, f"{p.name} mentions {word}")
        for p in [C / "lease-runner.v2.schema.json", C / "lease-handover.v1.schema.json",
                  C / "llm-points.v1.schema.json"]:  # Muninn vendors these too
            text = p.read_text()
            for word in ("agent-hub", "librarian", "bin/", "Knas", "muninn"):
                self.assertNotIn(word, text, f"{p.name} mentions {word}")
        for p in F.glob("*"):  # example files hold no user name either (the owner is a neutral tag, OWNER)
            self.assertNotIn("Knas", p.read_text(), f"{p.name} mentions the user's name")

    def test_same_ignores_prose(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            a, b = pathlib.Path(d, "a.md"), pathlib.Path(d, "b.md")
            text = (C / "todo-md.v1.md").read_text()
            prose = "Readers take only items"   # a sentence of the explanation, outside the rule blocks
            self.assertIn(prose, text)
            a.write_text(text)
            b.write_text(text.replace(prose, "Consumers take only items"))
            self.assertTrue(contracts.same(a, b))
            b.write_text((C / "todo-md.v1.md").read_text().replace("^- \\[ \\] (.+)$", "^\\* (.+)$"))
            self.assertFalse(contracts.same(a, b))


class Json(unittest.TestCase):
    def test_schemas_parse(self):
        for p in list(C.glob("*.schema.json")) + list((C / "vendor").rglob("*.schema.json")) + list((C / "llm").glob("*.json")):
            json.loads(p.read_text())

    def test_llm_points_declare_existing_schemas(self):
        """Every output.schema a judgment point declares exists (required fields are validated by LlmPoints with the
        shared format)."""
        try:
            import yaml
        except ImportError:
            self.skipTest("needs PyYAML")
        points = yaml.safe_load((C / "llm-points.yaml").read_text())["points"]
        self.assertGreaterEqual(len(points), 6)
        for name, spec in points.items():
            schema = (spec.get("output") or {}).get("schema")
            if schema:
                self.assertTrue((C / schema).exists(), f"{name}:{schema}")

    def test_hubbus_message(self):
        ev = {"seq": 1, "id": "m1", "ts": "2026-10-22T00:00:00+08:00", "type": "message", "source": "example",
              "to_project": "owner", "ref": None, "payload": {"v": 1, "type": "task", "text": "x", "due": "2026-11-01"}}
        self.assertEqual(contracts.check_json(C / "hubbus.v1.schema.json", ev), [])
        ev["payload"]["type"] = "nope"
        self.assertTrue(contracts.check_json(C / "hubbus.v1.schema.json", ev))

    def test_hubbus_woken(self):
        """B1 (docs/design/mailbox-runtimes.md): a live session was nudged about a letter, once per letter."""
        ev = {"seq": 3, "id": "m1:woken", "ts": "2026-11-01T00:00:00+08:00", "type": "message.woken", "source": "p",
              "to_project": "p", "ref": "m1", "payload": {"v": 1, "session": "S1", "via": "wake", "ok": True}}
        self.assertEqual(contracts.check_json(C / "hubbus.v1.schema.json", ev), [])

    def test_hubbus_routed(self):
        """docs/design/mailbox-session-first.md: one session holds a letter, or the route ended."""
        for p in ({"session": "S1", "by": "hook"}, {"session": None, "by": None, "ended": "window"}):
            ev = {"seq": 4, "id": "m1:routed:ab12cd34", "ts": "2026-11-07T00:00:00+08:00", "type": "message.routed",
                  "source": "p", "to_project": "p", "ref": "m1", "payload": {"v": 1, **p}}
            self.assertEqual(contracts.check_json(C / "hubbus.v1.schema.json", ev), [])

    def test_hubbus_record(self):
        ev = {"seq": 2, "id": "rec:acts/0.3:p:r:k", "ts": "2026-10-28T00:00:00+08:00", "type": "record", "source": "p",
              "to_project": "owner", "ref": None, "payload": {"v": 1, "record": {
                  "ph": "0.3", "ext": "acts/0.3", "id": "r:k", "type": "raised", "project": "p"}}}
        self.assertEqual(contracts.check_json(C / "hubbus.v1.schema.json", ev), [])
        del ev["payload"]["record"]["id"]
        self.assertTrue(contracts.check_json(C / "hubbus.v1.schema.json", ev))

    def test_vendored_messages_drop_needs_task_or_question(self):
        s = C / "vendor" / "muninn" / "messages.v1.schema.json"
        drop = {"id": "abcd", "project": "p", "type": "drop", "text": "t"}
        self.assertTrue(contracts.check_json(s, {"messages": [drop]}))
        self.assertEqual(contracts.check_json(s, {"messages": [{**drop, "task": "k"}]}), [])
        self.assertEqual(contracts.check_json(s, {"messages": [{**drop, "question": "m1"}]}), [])

    def test_vendored_handoffs_fixture(self):
        self.assertEqual(contracts.check_json(C / "vendor" / "muninn" / "handoffs.v1.schema.json",
                                              json.loads((F / "muninn-handoffs.v1.example.json").read_text())), [])


if __name__ == "__main__":
    unittest.main()
