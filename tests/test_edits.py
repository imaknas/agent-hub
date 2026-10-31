"""The rules for applying an edit list (bin/edits.py): each old occurs exactly once, in order, never half applied;
protected markers cannot be rewritten.

    bin/py -m unittest discover -s tests
"""
import os
import pathlib
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "bin"))
import edits  # noqa: E402
import i18n  # noqa: E402
import todo_tokens  # noqa: E402

ZH = todo_tokens.tokens("zh-Hant")
MARK = todo_tokens.marker("zh-Hant", "Alex", "task-p-1")
# the zh-Hant key marker with any key (no `key` group: compared by the whole marker)
KEY = re.compile(re.escape(todo_tokens.marker("zh-Hant", "Alex", "@")).replace("@", r"[^)\n]+"))
OWNER_LINE = todo_tokens.item("zh-Hant", "owner", "Alex", "Renew the domain") + todo_tokens.stamp("zh-Hant", "Alex",
                                                                                                    "task-p-1")
DOC = (f"# STATE\n\nStatus: A.\n\n{ZH['todo_heading']}\n{OWNER_LINE}\n"
       f"{todo_tokens.item('zh-Hant', 'agent', 'Alex', 'Write tests')}\n")


class Apply(unittest.TestCase):
    def test_in_order(self):
        out = edits.apply(DOC, [{"old": "Status: A.", "new": "Status: B."}, {"old": "Status: B.", "new": "Status: C."}])
        self.assertIn("Status: C.", out)
        self.assertEqual(out.replace("Status: C.", "Status: A."), DOC, "everything else untouched, word for word")

    def test_old_must_occur_exactly_once(self):
        with self.assertRaises(edits.EditError):
            edits.apply(DOC, [{"old": "no such passage", "new": "x"}])
        with self.assertRaises(edits.EditError):
            edits.apply(DOC, [{"old": "- [ ] ", "new": "x"}])   # occurs twice
        with self.assertRaises(edits.EditError):
            edits.apply(DOC, [{"old": "", "new": "x"}])

    def test_nothing_applied_when_one_fails(self):
        with self.assertRaises(edits.EditError):
            edits.apply(DOC, [{"old": "Status: A.", "new": "Status: B."}, {"old": "missing", "new": "x"}])

    def test_shape(self):
        with self.assertRaises(edits.EditError):
            edits.apply(DOC, [{"old": "Status"}])
        self.assertEqual(edits.apply(DOC, []), DOC)


class Markers(unittest.TestCase):
    def setUp(self):
        zh = mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "zh-Hant"})   # one assertion reads a zh-Hant text
        zh.start()
        self.addCleanup(zh.stop)

    def test_whole_line_removed_is_fine(self):
        after = DOC.replace(OWNER_LINE + "\n", "")
        self.assertEqual(edits.check_markers(DOC, after, KEY), [])

    def test_rewritten_key_is_caught(self):
        after = DOC.replace("task-p-1", "task-p-2")
        self.assertTrue(edits.check_markers(DOC, after, KEY))

    def test_marker_stripped_but_line_kept_is_caught(self):
        after = DOC.replace(MARK, "")
        want = i18n.translate("edits", "marker removed but its line is still there (the key was rewritten): {line}",
                              {"line": OWNER_LINE.strip()[:80]}, "zh-Hant")
        self.assertIn(want, edits.check_markers(DOC, after, KEY))

    def test_not_removable(self):
        after = DOC.replace(OWNER_LINE + "\n", "")
        self.assertTrue(edits.check_markers(DOC, after, KEY, removable=False))

    def test_compared_by_key(self):
        """with a pattern that has a `key` group (the librarian's: both to-do token sets), a marker rewritten into the
        other set with the same key is the same marker; a changed key is not"""
        import todo_tokens
        pat = todo_tokens.patterns("Alex").key_mark
        zh, en = todo_tokens.marker("zh-Hant", "Alex", "task-p-1"), todo_tokens.marker("en", "Alex", "task-p-1")
        self.assertIn(zh, DOC)
        converted = DOC.replace(zh, " " + en)
        self.assertEqual(edits.check_markers(DOC, converted, pat), [])
        self.assertEqual(edits.check_markers(DOC, converted, pat, removable=False), [])
        self.assertTrue(edits.check_markers(DOC, converted.replace("task-p-1", "task-p-2"), pat))


if __name__ == "__main__":
    unittest.main()
