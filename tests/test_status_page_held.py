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


class Waiting(unittest.TestCase):
    build, main_part = golden.StatusPage.build, staticmethod(golden.StatusPage.main_part)

    def test_a_waiting_letter_is_listed_apart_and_raises_no_alert(self):
        ts = "2026-10-30T09:00:00"
        waits = {"m1": {"why": "routed", "session": "abcdef0123"}, "m2": {"why": "budget"}, "m3": {"why": "old"}}
        bus = {**golden.INPUTS["bus_quiet"], "stuck": [],
               "waiting": [{"id": i, "to": "alpha", "ts": ts, **w} for i, w in waits.items()]}
        bus["projects"] = {"alpha": {"open": 3, "undelivered": 0, "oldest": ts}}
        with tempfile.TemporaryDirectory() as d:
            page = self.main_part(self.build(pathlib.Path(d).resolve(), golden.quiet_options(bus=bus))["index.html"])
        t = lambda msg, **kw: i18n.translate("gen-status-page", msg, kw, golden_lang())
        for mid, why in (("m1", t("session {session} has it", session="abcdef01")), ("m2", t("today's runs are used up")),
                         ("m3", t("too old for a platform run"))):
            self.assertIn(t("Waiting: {id} ({project}: {why})", id=mid, project="alpha", why=why), page)
        self.assertNotIn(t("Letter {id} to {to}: unclaimed for over an hour: no session took it and the platform did "
                           "not handle it (see the hubbus dispatch log and the daily limit)", id="m1", to="alpha"), page)


def golden_lang() -> str:
    import golden_lang as gl
    return gl.LANG


if __name__ == "__main__":
    unittest.main()
