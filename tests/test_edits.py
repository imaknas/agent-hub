"""編輯清單的套用規則(bin/edits.py):每個 old 剛好出現一次、照順序、不套一半;受保護的標記不能被改寫。

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

KEY = re.compile(r"\(等 Alex:[^)\n]+\)")
DOC = "# STATE\n\n現況:A。\n\n## 待辦\n- [ ] 〔Alex〕續約網域(等 Alex:task-p-1)\n- [ ] 〔agent〕寫測試\n"


class Apply(unittest.TestCase):
    def test_in_order(self):
        out = edits.apply(DOC, [{"old": "現況:A。", "new": "現況:B。"}, {"old": "現況:B。", "new": "現況:C。"}])
        self.assertIn("現況:C。", out)
        self.assertEqual(out.replace("現況:C。", "現況:A。"), DOC, "其他地方逐字不動")

    def test_old_must_occur_exactly_once(self):
        with self.assertRaises(edits.EditError):
            edits.apply(DOC, [{"old": "沒有這段", "new": "x"}])
        with self.assertRaises(edits.EditError):
            edits.apply(DOC, [{"old": "- [ ] ", "new": "x"}])   # 出現兩次
        with self.assertRaises(edits.EditError):
            edits.apply(DOC, [{"old": "", "new": "x"}])

    def test_nothing_applied_when_one_fails(self):
        with self.assertRaises(edits.EditError):
            edits.apply(DOC, [{"old": "現況:A。", "new": "現況:B。"}, {"old": "沒有", "new": "x"}])

    def test_shape(self):
        with self.assertRaises(edits.EditError):
            edits.apply(DOC, [{"old": "現況"}])
        self.assertEqual(edits.apply(DOC, []), DOC)


class Markers(unittest.TestCase):
    def setUp(self):
        zh = mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "zh-Hant"})   # one assertion reads a zh-Hant text
        zh.start()
        self.addCleanup(zh.stop)

    def test_whole_line_removed_is_fine(self):
        after = DOC.replace("- [ ] 〔Alex〕續約網域(等 Alex:task-p-1)\n", "")
        self.assertEqual(edits.check_markers(DOC, after, KEY), [])

    def test_rewritten_key_is_caught(self):
        after = DOC.replace("task-p-1", "task-p-2")
        self.assertTrue(edits.check_markers(DOC, after, KEY))

    def test_marker_stripped_but_line_kept_is_caught(self):
        after = DOC.replace("(等 Alex:task-p-1)", "")
        self.assertTrue(any("改寫了 key" in p for p in edits.check_markers(DOC, after, KEY)))

    def test_not_removable(self):
        after = DOC.replace("- [ ] 〔Alex〕續約網域(等 Alex:task-p-1)\n", "")
        self.assertTrue(edits.check_markers(DOC, after, KEY, removable=False))


if __name__ == "__main__":
    unittest.main()
