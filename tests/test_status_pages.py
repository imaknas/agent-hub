"""The markdown conversion of the status page's three human-facing pages (digest, to-dos, weekly review): escape
first, recognise only a few forms, so HTML in the content never becomes real HTML.

    bin/py -m unittest discover -s tests
"""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gsp", ROOT / "bin" / "gen-status-page.py")
gsp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gsp)


class Markdown(unittest.TestCase):
    def test_basic_blocks(self):
        h = gsp.md_html("# Digest\n\n## proj\n\n摘要 `x` **重點**\n- [ ] 〔agent〕做事\n- 第二項\n\n<!-- c -->結尾")
        self.assertIn("<h2>Digest</h2>", h)
        self.assertIn("<h3>proj</h3>", h)
        self.assertIn("<code>x</code>", h)
        self.assertIn("<strong>重點</strong>", h)
        self.assertEqual(h.count("<li>"), 2)
        self.assertNotIn("<!--", h)

    def test_html_in_content_is_escaped(self):
        h = gsp.md_html("- <script>alert(1)</script> [x](javascript:alert(1))")
        self.assertNotIn("<script>", h)
        self.assertNotIn('href="javascript', h)


if __name__ == "__main__":
    unittest.main()
