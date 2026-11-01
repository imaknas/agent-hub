"""backup.py's retention rules run on Sundays, and after a missed Sunday at the next backup (docs/design/newcomer-path.md
B7: a machine that sleeps through Sunday doesn't wait a whole week).

    bin/py -m unittest tests.test_backup_retention
"""
import datetime
import importlib.machinery
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("backup_retention", str(ROOT / "bin" / "backup.py"))
spec = importlib.util.spec_from_loader("backup_retention", loader)
backup = importlib.util.module_from_spec(spec)
loader.exec_module(backup)

SUN = datetime.date(2026, 11, 1)


class RetentionDue(unittest.TestCase):
    def test_table(self):
        d = datetime.timedelta
        for today, last, due in [
                (SUN, SUN - d(7), True),            # an ordinary Sunday
                (SUN, SUN, False),                  # already ran today
                (SUN + d(1), SUN - d(7), True),     # slept through Sunday: Monday catches up
                (SUN + d(1), SUN, False),           # ran on Sunday: not again on Monday
                (SUN + d(3), SUN - d(2), False),    # less than a week since the last run, not Sunday
                (SUN + d(2), None, True)]:          # never ran (or the record is gone)
            with self.subTest(today=today, last=last):
                self.assertEqual(backup.retention_due(today, last), due)


if __name__ == "__main__":
    unittest.main()
