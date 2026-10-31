"""librarian.py's helpers: the places most likely to drift silently in the rewrite from librarian.sh: jq's
truthiness and string interpolation, shell's `head | tr`, dead memory/ references in STATE.md, how handoffs are written
into the prompt. The whole script's behaviour was separately compared byte for byte with the shell version in an
isolated environment with a stand-in llm.py.

    bin/py -m unittest discover -s tests
"""
import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("librarian", ROOT / "bin" / "librarian.py")
lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lib)


class JqSemantics(unittest.TestCase):
    def test_truthiness_is_jqs(self):
        for v in ("", 0, [], "x"):
            self.assertTrue(lib.present(v), repr(v))
        for v in (None, False):
            self.assertFalse(lib.present(v), repr(v))

    def test_interpolation(self):
        self.assertEqual(lib.jstr(None), "null")
        self.assertEqual(lib.jstr("文字"), "文字")
        self.assertEqual(lib.jstr(3), "3")
        self.assertEqual(lib.jq_bool(True), "true")
        self.assertEqual(lib.jq_bool(None), "null")

    def test_head_spaced_matches_shell(self):
        self.assertEqual(lib.head_spaced("a\nb\nc\nd", 3), "a b c ")
        self.assertEqual(lib.head_spaced("only", 3), "only ")


class Handoffs(unittest.TestCase):
    def test_quote_and_missing_item(self):
        t = lib.handoff_text({"effect": "changes", "fact": "F", "quote": "Q", "item": None, "why": "W",
                              "ts": "2026-10-26T10:00:00+08:00"})
        self.assertIn("- [changes] Fact: F", t)
        self.assertIn(f"\n  {lib.OWNER}'s own words: Q", t)
        self.assertIn("Matching to-do: (none given)", t)
        self.assertTrue(t.endswith("(2026-10-26T10:00)"))

    def test_empty_quote_is_still_shown_like_jq(self):
        self.assertIn("own words: ", lib.handoff_text({"effect": "x", "fact": "f", "quote": "", "why": "w", "ts": "t"}))
        self.assertNotIn("原話", lib.handoff_text({"effect": "x", "fact": "f", "quote": None, "why": "w", "ts": "t"}))


class Dangling(unittest.TestCase):
    def test_missing_refs_listed_with_leading_space(self):
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "memory" / "reference").mkdir(parents=True)
        (d / "memory" / "reference" / "ok.md").write_text("x")
        (d / "STATE.md").write_text("見 memory/reference/ok.md、memory/reference/gone.md、memory/YYYY-MM-DD.md、memory/reference/*.md\n")
        self.assertEqual(lib.dangling_refs(d), " memory/reference/gone.md")

    def test_no_state_md(self):
        self.assertEqual(lib.dangling_refs(pathlib.Path(tempfile.mkdtemp())), "")


class Prompts(unittest.TestCase):
    def test_names_come_from_settings(self):
        p = lib.state_prompt("demo", "M", "", "")
        self.assertIn("now working in the directory of project demo.\nM\n\n\n\nFor background, read", p)
        self.assertIn(lib.todo_tokens.tag(lib.CONTENT_LANG, "owner", lib.OWNER), p)   # the content language's to-do tokens
        self.assertIn(lib.ASSISTANT, p)
        self.assertNotIn("{", p.replace("{owner}", ""), "the f-string leaves no unfilled braces")


if __name__ == "__main__":
    unittest.main()
