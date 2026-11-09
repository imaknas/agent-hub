"""The memory copy's writer lock (bin/memlock.py, docs/design/state-restore.md "The lock"): one writer at a time in a
memory copy (the librarian, the registrar, consolidate, restore, rename, uninstall --purge). A flock: it dies with its
holder, a detached child of the holder doesn't keep it, a copy moved while someone waited is locked anew.

    bin/py -m unittest tests.test_memlock
"""
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import memlock  # noqa: E402
import names  # noqa: E402


class MemLock(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.copy = self.tmp / "copy"
        (self.copy / ".git").mkdir(parents=True)

    def holder(self, *extra, sleep=30):
        """another process holding the lock (and, with extra, starting a detached child first)"""
        code = textwrap.dedent(f"""
            import sys, time, subprocess
            sys.path.insert(0, {str(ROOT / 'bin')!r})
            import memlock
            with memlock.hold(sys.argv[1], "librarian") as got:
                assert got
                {"subprocess.Popen(['sleep', '60'], start_new_session=True)" if extra else ""}
                print("held", flush=True)
                time.sleep({sleep})
        """)
        p = subprocess.Popen([sys.executable, "-c", code, str(self.copy)], stdout=subprocess.PIPE, text=True)
        self.assertEqual(p.stdout.readline().strip(), "held")
        self.addCleanup(lambda: p.poll() is None and p.kill())
        return p

    def test_one_writer_at_a_time(self):
        with memlock.hold(self.copy, "restore") as got:
            self.assertTrue(got)
            self.assertEqual(memlock.holder(self.copy)[0], "restore")
        with memlock.hold(self.copy, "librarian") as got:
            self.assertTrue(got, "released on leaving")

    def test_busy_names_the_holder(self):
        self.holder()
        t = time.monotonic()
        with memlock.hold(self.copy, "restore", timeout=0.5) as got:
            self.assertFalse(got)
        self.assertGreaterEqual(time.monotonic() - t, 0.5)
        who, since = memlock.holder(self.copy)
        self.assertEqual(who, "librarian")
        self.assertTrue(since)

    def test_a_killed_holder_releases_it(self):
        p = self.holder()
        p.send_signal(signal.SIGKILL)
        p.wait()
        with memlock.hold(self.copy, "restore") as got:
            self.assertTrue(got)

    def test_a_detached_child_does_not_keep_it(self):
        p = self.holder("child", sleep=0)
        p.wait()
        with memlock.hold(self.copy, "restore") as got:
            self.assertTrue(got)

    def test_not_reentrant(self):
        with memlock.hold(self.copy, "restore") as got:
            self.assertTrue(got)
            with memlock.hold(self.copy, "restore") as again:
                self.assertFalse(again)

    def test_no_copy(self):
        with memlock.hold(self.tmp / "gone", "restore") as got:
            self.assertFalse(got)

    def test_the_cli(self):
        cli = [sys.executable, str(ROOT / "bin" / "memlock.py"), "hold", str(self.copy), "--who", "rename", "--"]
        r = subprocess.run([*cli, "sh", "-c", "exit 3"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.holder()
        r = subprocess.run([*cli, "true"], capture_output=True, text=True,
                           env=names.env_set(dict(os.environ), "LANG_UI", "en"))
        self.assertEqual(r.returncode, memlock.BUSY)
        self.assertIn("librarian", r.stderr)


if __name__ == "__main__":
    unittest.main()
