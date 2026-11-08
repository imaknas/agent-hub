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


class VerifyFolder(unittest.TestCase):
    """The whole latest backup is restored for the check: into the platform's private temp folder, never .state, which a
    sandboxed project is denied only entry by entry (docs/design/capabilities.md 11-08, A1 point 8)."""

    def test_restored_into_the_private_temp_folder(self):
        import tempfile
        from unittest import mock
        made = []
        real = tempfile.mkdtemp

        def mkdtemp(**kw):
            made.append(pathlib.Path(kw["dir"]))
            return real(**kw)
        with tempfile.TemporaryDirectory() as top, \
                mock.patch.object(backup.hubconf, "private_tmp", lambda: pathlib.Path(top)), \
                mock.patch.object(backup.tempfile, "mkdtemp", mkdtemp), \
                mock.patch.object(backup, "restic", mock.Mock(side_effect=RuntimeError("no restic here"))), \
                mock.patch.object(backup, "fail", lambda stage, err: 1):
            self.assertEqual(backup.cmd_verify(), 1)
            self.assertEqual(made, [pathlib.Path(top)])
            self.assertEqual(list(pathlib.Path(top).iterdir()), [], "removed afterwards")


if __name__ == "__main__":
    unittest.main()
