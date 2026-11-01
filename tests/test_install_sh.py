"""install.sh, the one-line installer (docs/design/newcomer-path.md B1, B2): its options, the folder it clones into,
carrying on after an install that stopped halfway, and going on into init only when there is a terminal. Nothing is
cloned or installed: an existing agent-hub folder with stand-in scripts is used, and the test runs without a
controlling terminal (a new session), as a pipe in CI does.

    bin/py -m unittest tests.test_install_sh
"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class InstallSh(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        (self.home / ".local" / "bin").mkdir(parents=True)
        uv = self.home / ".local" / "bin" / "uv"
        uv.write_text("#!/bin/sh\nexit 0\n")
        uv.chmod(0o755)
        self.dir = self.tmp / "agent-hub"

    def fake_hub(self):
        """an agent-hub clone whose setup and agenthub are stand-ins that record being run"""
        (self.dir / "bin").mkdir(parents=True)
        for name, body in (("setup", "echo setup-ran"), ("agenthub", "echo agenthub-ran"),
                           ("py", 'shift; exec "$@"')):
            f = self.dir / "bin" / name
            f.write_text(f"#!/bin/sh\n{body}\n")
            f.chmod(0o755)
        subprocess.run(["git", "init", "-q", str(self.dir)], check=True)

    def run_install(self, *args, **env):
        e = {"PATH": os.environ["PATH"], "HOME": str(self.home), "AGENT_HUB_DIR": str(self.dir),
             "AGENT_HUB_REPO": str(self.tmp / "nowhere"), **env}
        return subprocess.run(["sh", str(ROOT / "install.sh"), *args], env=e, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, start_new_session=True, timeout=60)

    def test_unknown_option(self):
        r = self.run_install("--bogus")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unknown option: --bogus", r.stderr)

    def test_a_set_up_hub_is_not_touched(self):
        self.fake_hub()
        (self.dir / ".hub-home").write_text(str(self.tmp / "instance") + "\n")
        r = self.run_install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("agenthub upgrade", r.stderr)
        self.assertNotIn("setup-ran", r.stdout)

    def test_a_half_done_install_carries_on(self):
        self.fake_hub()
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("carrying on", r.stdout)
        self.assertIn("setup-ran", r.stdout)
        self.assertNotIn("agenthub-ran", r.stdout, "no terminal: init is not started")
        self.assertIn("next: agenthub init", r.stdout)

    def test_no_init_flag(self):
        self.fake_hub()
        r = self.run_install("--no-init")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Next: agenthub init", r.stdout)

    def test_another_folder_is_refused(self):
        self.dir.mkdir()
        (self.dir / "notes.txt").write_text("mine")
        r = self.run_install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("is not agent-hub", r.stderr)

    def test_a_cut_off_download_runs_nothing(self):
        text = (ROOT / "install.sh").read_text()
        self.assertTrue(text.rstrip().endswith('main "$@"'), "everything runs from the last line")
        cut = text[: text.index("main() {")]
        r = subprocess.run(["sh", "-c", cut], capture_output=True, text=True, start_new_session=True, timeout=30,
                           env={"PATH": os.environ["PATH"], "HOME": str(self.home)})
        self.assertEqual((r.returncode, r.stdout), (0, ""), "only definitions before main")


if __name__ == "__main__":
    unittest.main()
