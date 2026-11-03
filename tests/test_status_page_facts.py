"""Every fact the status page shows stays on it, whatever it looks like. The golden (tests/test_i18n_golden_status_page.py)
pins the page byte for byte, so a redesign rewrites it; this test pins only the text a reader sees: each text node of
the page as it was before the redesign (tests/fixtures/status-page-facts.json) must still be found in the page's text.
Markup, order and the icons in front of an alert may change; a fact may not go missing.

    bin/py -m unittest tests.test_status_page_facts
    STATUS_FACTS_UPDATE=1 bin/py -m unittest tests.test_status_page_facts    # rewrite (only when a fact goes on purpose)
"""
import html.parser
import json
import os
import pathlib
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_lang  # noqa: E402
import test_i18n_golden_status_page as golden  # noqa: E402

FACTS = pathlib.Path(__file__).resolve().parent / "fixtures" / "status-page-facts.json"
DECORATION = re.compile(r"[⚠️🔔✅️]")


class Text(html.parser.HTMLParser):
    """The text nodes a reader sees: not style, not script, not the title."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in ("style", "script", "title")

    def handle_endtag(self, tag):
        self.skip -= tag in ("style", "script", "title")

    def handle_data(self, data):
        t = " ".join(DECORATION.sub("", data).split())
        if t and not self.skip:
            self.nodes.append(t)


def nodes(page: str) -> list[str]:
    p = Text()
    p.feed(page)
    return p.nodes


def cases() -> list[tuple[str, dict]]:
    return [("full", golden.full_options()),
            ("scheduler down", golden.quiet_options(sm="systemd", failures=None, commit=None, bus=None, worktrees=None,
                                                    jobs=[], backup_age=None, verify_age=None)),
            ("no alerts", golden.quiet_options(alerts=False, tick_age=None)),
            ("all clear", golden.quiet_options())]


@unittest.skipIf(golden_lang.LANG != "zh-Hant", "the facts are pinned on zh-Hant")
class Facts(unittest.TestCase):
    build = golden.StatusPage.build

    def pages(self) -> dict[str, str]:
        out = {}
        with tempfile.TemporaryDirectory() as d:
            for name, o in cases():
                out[name] = self.build(pathlib.Path(d).resolve() / name.replace(" ", "-"), o)["index.html"]
        return out

    def test_every_fact_is_still_on_the_page(self):
        pages = self.pages()
        if os.environ.get("STATUS_FACTS_UPDATE") == "1" or not FACTS.exists():
            FACTS.write_text(json.dumps({k: list(dict.fromkeys(nodes(v))) for k, v in pages.items()},
                                        ensure_ascii=False, indent=1) + "\n")
            return
        for name, facts in json.loads(FACTS.read_text()).items():
            text = " ".join(nodes(pages[name]))
            missing = [f for f in facts if f not in text]
            self.assertEqual(missing, [], f"{name}: facts no longer on the page")


if __name__ == "__main__":
    unittest.main()
