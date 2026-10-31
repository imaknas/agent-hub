"""E1 golden (docs/design/i18n.md) for bin/applier_scheduler.py on zh-Hant: the plan lines `agenthub config plan`
shows for the scheduler's job list (added, removed, changed jobs).

    bin/py -m unittest tests.test_i18n_golden_applier_scheduler
"""
import os
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
GOLDEN = ROOT / "tests" / "fixtures" / "i18n-golden"
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"


class Golden(unittest.TestCase):
    maxDiff = None

    def scenario(self) -> str:
        import applier_scheduler as m
        old = {"p/kept": {"time": "03:00", "command": "a"},
               "p/changed": {"time": "03:00", "command": "a", "after": ["p/kept"], "enabled": True},
               "p/gone": {"time": "04:00"}}
        cur = {"p/kept": {"time": "03:00", "command": "a"},
               "p/changed": {"time": "05:00", "command": "b", "after": ["p/kept"], "enabled": True},
               "p/at": {"time": "06:30"},
               "p/every": {"every_minutes": 15},
               "p/after": {"every_minutes": 60, "after": ["p/at", "p/every"]},
               "p/time-after": {"time": "07:00", "after": ["p/at"]},
               "p/plain": {"command": "x"}}
        with mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "zh-Hant"}):
            return "\n".join(c.line() for c in m._diff(cur, old)) + "\n"

    def test_texts(self):
        text = self.scenario()
        f = GOLDEN / "applier_scheduler.txt"
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())


if __name__ == "__main__":
    sys.exit(unittest.main())
