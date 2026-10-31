"""The markdown conversion of the status page's three human-facing pages (digest, to-dos, weekly review): escape
first, recognise only a few forms, so HTML in the content never becomes real HTML.

    bin/py -m unittest discover -s tests
"""
import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import todo_tokens  # noqa: E402

spec = importlib.util.spec_from_file_location("gsp", ROOT / "bin" / "gen-status-page.py")
gsp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gsp)


class Markdown(unittest.TestCase):
    def test_basic_blocks(self):
        todo = todo_tokens.item("zh-Hant", "agent", "owner", "do it")   # a to-do line as the digest has them
        h = gsp.md_html(f"# Digest\n\n## proj\n\nRésumé `x` **key point**\n{todo}\n- second item\n\n<!-- c -->the end")
        self.assertIn("<h2>Digest</h2>", h)
        self.assertIn("<h3>proj</h3>", h)
        self.assertIn("<code>x</code>", h)
        self.assertIn("<strong>key point</strong>", h)
        self.assertEqual(h.count("<li>"), 2)
        self.assertNotIn("<!--", h)

    def test_html_in_content_is_escaped(self):
        h = gsp.md_html("- <script>alert(1)</script> [x](javascript:alert(1))")
        self.assertNotIn("<script>", h)
        self.assertNotIn('href="javascript', h)


if __name__ == "__main__":
    unittest.main()
