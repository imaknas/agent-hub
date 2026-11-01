"""The status page lists letters held for an MCP session as held, not as unclaimed (docs/design/mailbox-runtimes.md
step 1, A3; `hubbus status --json` key `held`). Built with the status page golden's fixture (tests/
test_i18n_golden_status_page.py), with a held letter added to its quiet bus; the golden itself has none.

    bin/py -m unittest tests.test_status_page_held
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "bin"))
import i18n  # noqa: E402
import test_i18n_golden_status_page as golden  # noqa: E402


class Held(unittest.TestCase):
    build, main_part = golden.StatusPage.build, staticmethod(golden.StatusPage.main_part)

    def test_a_held_letter_is_listed_as_held_and_not_as_unclaimed(self):
        bus = {**golden.INPUTS["bus_quiet"], "stuck": [],
               "held": [{"id": "m11011200-0123456789ab", "to": "alpha", "ts": "2026-10-30T09:00:00",
                         "by": ["mcp:codex"]}]}
        bus["projects"] = {"alpha": {"open": 1, "undelivered": 1, "oldest": "2026-10-30T09:00:00"}}
        with tempfile.TemporaryDirectory() as d:
            page = self.main_part(self.build(pathlib.Path(d).resolve(), golden.quiet_options(bus=bus))["index.html"])
        held = i18n.translate("gen-status-page", "Held: {id} ({project} in use by {by})",
                              {"id": "m11011200-0123456789ab", "project": "alpha", "by": "mcp:codex"}, golden_lang())
        self.assertIn(held, page)
        self.assertNotIn("m11011200-0123456789ab", page.replace(held, ""), "listed once, not as unclaimed too")


def golden_lang() -> str:
    import golden_lang as gl
    return gl.LANG


if __name__ == "__main__":
    unittest.main()
