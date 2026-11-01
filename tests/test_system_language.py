"""init's default language (docs/design/newcomer-path.md B0): a locale or language tag → one of hubconf.LANGUAGES, and
which tag the machine prefers (macOS's AppleLanguages before LANG, which follows the Region setting there).

    bin/py -m unittest tests.test_system_language
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "bin"))
import hubconf  # noqa: E402


class LanguageOf(unittest.TestCase):
    def test_table(self):
        for tag, lang, simplified in [
                ("zh_TW.UTF-8", "zh-Hant", False), ("zh-Hant-TW", "zh-Hant", False), ("zh-Hant", "zh-Hant", False),
                ("zh_HK", "zh-Hant", False), ("zh_MO.UTF-8", "zh-Hant", False), ("zh-Hant-HK", "zh-Hant", False),
                ("zh_CN.UTF-8", "en", True), ("zh-Hans-CN", "en", True), ("zh_SG", "en", True), ("zh", "en", True),
                ("en_US.UTF-8", "en", False), ("de_DE@euro", "en", False), ("C.UTF-8", "en", False), ("", "en", False)]:
            with self.subTest(tag=tag):
                self.assertEqual(hubconf.language_of(tag), lang)
                self.assertEqual(hubconf.is_simplified_chinese(tag), simplified)


class SystemTag(unittest.TestCase):
    def test_apple_languages_come_first(self):
        env = {"LANG": "en_US.UTF-8"}
        self.assertEqual(hubconf.system_tag(env, ["zh-Hant-TW", "en-US"]), "zh-Hant-TW")
        self.assertEqual(hubconf.system_language(env, ["zh-Hant-TW"]), "zh-Hant")

    def test_locale_variables_in_order(self):
        self.assertEqual(hubconf.system_tag({"LC_ALL": "zh_TW.UTF-8", "LANG": "en_US.UTF-8"}, []), "zh_TW.UTF-8")
        self.assertEqual(hubconf.system_tag({"LC_MESSAGES": "zh_HK", "LANG": "en_US.UTF-8"}, []), "zh_HK")
        self.assertEqual(hubconf.system_tag({"LANG": "en_US.UTF-8"}, []), "en_US.UTF-8")
        self.assertEqual(hubconf.system_language({}, []), "en")


class InitQuestion(unittest.TestCase):
    """init's first question, asked before any language is chosen: every line in both languages, from the catalog."""

    def setUp(self):
        import importlib.machinery
        import importlib.util
        path = str(pathlib.Path(__file__).resolve().parents[1] / "bin" / "agenthub")
        loader = importlib.machinery.SourceFileLoader("agenthub_lang", path)
        spec = importlib.util.spec_from_loader("agenthub_lang", loader)
        self.m = importlib.util.module_from_spec(spec)
        loader.exec_module(self.m)

    def ask(self, answers, env_ui=None):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        it = iter(answers)
        env = {"AGENT_HUB_LANG_UI": env_ui} if env_ui else {}
        with mock.patch.object(self.m.sys.stdin, "isatty", lambda: True), \
                mock.patch("builtins.input", lambda prompt="": next(it)), \
                mock.patch.dict(self.m.os.environ, env, clear=False), \
                mock.patch.object(self.m.hubconf, "system_tag", lambda *a: "en_US.UTF-8"), \
                redirect_stdout(io.StringIO()) as out:
            if not env_ui:
                self.m.os.environ.pop("AGENT_HUB_LANG_UI", None)
            self.m.init_dialogue.use(self.m.init_dialogue.Terminal(False))
            return self.m.choose_language({}), out.getvalue()

    def test_shown_in_both_languages(self):
        import i18n
        _, out = self.ask(["2"])
        for msgid in ("Language", "Traditional Chinese", "A different language for notes than for the interface"):
            self.assertIn(f"{msgid} / {i18n.translate('agenthub', msgid, {}, 'zh-Hant')}", out)

    def test_choices(self):
        self.assertEqual(self.ask(["1"])[0], ("en", "en"))
        self.assertEqual(self.ask(["2"])[0], ("zh-Hant", "zh-Hant"))
        self.assertEqual(self.ask(["3", "2", "1"])[0], ("zh-Hant", "en"))
        self.assertEqual(self.ask(["x", ""])[0], ("en", "en"), "a wrong answer asks again; empty takes the default")
        self.assertEqual(self.ask([""], env_ui="zh-Hant")[0], ("zh-Hant", "zh-Hant"))

    def test_lang_flag(self):
        self.assertEqual(self.m.choose_language({"lang": "en,zh-Hant"}), ("en", "zh-Hant"))
        self.assertIsNone(self.m.parse_lang("fr"))
        self.assertIsNone(self.m.parse_lang("en,zh-Hant,en"))


if __name__ == "__main__":
    unittest.main()
