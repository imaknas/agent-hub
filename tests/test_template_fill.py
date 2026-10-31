"""Placeholders in the platform's templates (bin/prompts.py fill / render / agent_rules): the agent rules, the MCP
instructions and the prompts under templates/ name no people and no language; {{owner}} / {{owner_alias}} /
{{assistant}}, {{content_language}} and the to-do tokens are filled from hub.json. A placeholder without a value is an
error, never text that reaches an agent (a mistyped one would otherwise show up as is in every project).

    bin/py -m unittest tests.test_template_fill
"""
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hubconf  # noqa: E402
import prompts  # noqa: E402

LEFT = re.compile(r"\{\{[^}]*\}\}")


class Fill(unittest.TestCase):
    def test_known_placeholders_are_replaced(self):
        out = prompts.fill("{{tag_owner}} --to {{owner_alias}} {{assistant}} {{content_language}}", lang="en")
        self.assertNotIn("{{", out)
        self.assertIn(prompts.TOKENS["en"]["tag"].format(hubconf.owner_tag()), out)
        self.assertIn(hubconf.owner_alias(), out)
        self.assertIn(hubconf.assistant_name(), out)
        self.assertIn("English", out)

    def test_unknown_placeholder_is_an_error(self):
        with self.assertRaises(KeyError):
            prompts.fill("{{owner}} {{ownr}}")

    def test_values_are_inserted_once(self):
        self.assertEqual(prompts.fill("[{{letters}}]", letters="{{owner}}"), "[{{owner}}]")

    def test_text_without_placeholders_is_returned_as_is(self):
        self.assertEqual(prompts.fill("a {b} ${{ x }}"), "a {b} ${{ x }}")

    def test_tokens_follow_the_content_language(self):
        """the to-do tokens are the content language's set (contracts/state-todo.v2.md); every reader takes both"""
        text = "{{todo_heading}} {{tag_owner}} {{tag_agent}} {{tag_tbd}} {{todo_none}} {{key_marker}}"
        for lang in prompts.LANGUAGE_NAMES:
            with self.subTest(lang=lang):
                t = prompts.TOKENS[lang]
                out = prompts.fill(text, lang=lang, owner="Alex")
                self.assertEqual(out, " ".join([t["todo_heading"], t["tag"].format("Alex"), t["tag"].format("agent"),
                                                t["tag"].format(t["tbd"]), t["todo_none"],
                                                t["key_marker"].format(owner="Alex", key="task-…")]))

    def test_key_pattern(self):
        out = prompts.fill("{{key_pattern}}", lang="en", owner="Alex")
        self.assertIn("Alex", out)
        self.assertIn("<key>", out)

    def test_agent_rules_leave_no_placeholder(self):
        rules = sorted(p.stem for p in (ROOT / "templates" / "rules").glob("*.md"))
        for lang in prompts.LANGUAGE_NAMES:
            with self.subTest(lang=lang):
                text, missing = prompts.agent_rules(rules, lang=lang)
                self.assertEqual(missing, [])
                self.assertEqual(LEFT.findall(text), [])
                self.assertIn(prompts.LANGUAGE_NAMES[lang], text)

    def test_agent_rules_report_missing_rules(self):
        text, missing = prompts.agent_rules(["no-such-rule"])
        self.assertEqual(missing, ["no-such-rule"])
        self.assertNotIn("<!--", text)

    def test_mcp_instructions_and_mailbox_prompt_fill(self):
        mcp = prompts.fill((ROOT / "templates" / "mcp-instructions.md").read_text(), project="demo", unread="-",
                           take_timeout=60)
        self.assertEqual(LEFT.findall(mcp), [])
        self.assertEqual(LEFT.findall(prompts.render("mailbox")), [])

    def test_agent_md_is_copied_as_is(self):
        """sync-local-setup.sh copies AGENT.md byte for byte onto every project's memory branch: no placeholders"""
        self.assertEqual(LEFT.findall((ROOT / "templates" / "AGENT.md").read_text()), [])


if __name__ == "__main__":
    unittest.main()
