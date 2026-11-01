"""The testcontainers module (bin/testcontainers-cleanup.py; docs/design/test-waiting.md B5) against a fake docker:
only containers more than a day old are removed; without docker, nothing to clean.

    bin/py -m unittest discover -s tests
"""
import datetime
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "testcontainers-cleanup.py"
PY = os.environ.get("AGENT_HUB_PYTHON") or sys.executable


class Cleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        (self.tmp / "bin").mkdir()
        now = datetime.datetime.now().astimezone()
        fmt = lambda t: t.strftime("%Y-%m-%d %H:%M:%S %z") + " CST"  # noqa: E731
        old, new = fmt(now - datetime.timedelta(days=3)), fmt(now - datetime.timedelta(hours=2))
        log = self.tmp / "rm.log"
        (self.tmp / "bin" / "docker").write_text(
            "#!/bin/sh\n"
            'if [ "$1" = ps ]; then\n'
            f"  echo '{{\"ID\": \"aaa\", \"CreatedAt\": \"{old}\"}}'\n"
            f"  echo '{{\"ID\": \"bbb\", \"CreatedAt\": \"{new}\"}}'\n"
            f'elif [ "$1" = rm ]; then shift; echo "$@" >> {log}; fi\n')
        (self.tmp / "bin" / "docker").chmod(0o755)
        self.log = log

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_(self, path):
        env = {**os.environ, "PATH": path, "AGENT_HUB_LANG_UI": "en"}
        return subprocess.run([PY, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)

    def test_only_old_ones_go(self):
        r = self.run_(f"{self.tmp / 'bin'}:/usr/bin:/bin")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.log.read_text().split(), ["-f", "aaa"])
        self.assertIn("removed 1 of 2", r.stdout)

    def test_no_docker(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        r = subprocess.run([PY, str(SCRIPT)], env={**os.environ, "PATH": str(empty), "AGENT_HUB_LANG_UI": "en",
                                                   "AGENT_HUB_ROOT": str(self.tmp)},
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
