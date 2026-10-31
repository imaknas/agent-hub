"""Name placeholders in templates (hubconf.fill_names): the agent instructions under templates/ name no people;
{{owner}} / {{owner_alias}} / {{assistant}} are filled by sync-local-setup from hub.json; after filling no
unrecognised {{…}} may remain (a mistyped placeholder would show up as is in every project).

    bin/py -m unittest discover -s tests
"""
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hubconf  # noqa: E402


class FillNames(unittest.TestCase):
    def test_known_tokens_are_replaced(self):
        out = hubconf.fill_names("〔{{owner}}〕 --to {{owner_alias}} {{assistant}}")
        self.assertNotIn("{{", out)
        self.assertIn(hubconf.owner_tag(), out)
        self.assertIn(hubconf.assistant_name(), out)

    def test_templates_leave_no_unknown_tokens(self):
        for f in [ROOT / "templates" / "agent-instructions.md", *sorted((ROOT / "templates" / "rules").glob("*.md"))]:
            with self.subTest(template=f.name):
                self.assertEqual(re.findall(r"\{\{[^}]*\}\}", hubconf.fill_names(f.read_text())), [])
