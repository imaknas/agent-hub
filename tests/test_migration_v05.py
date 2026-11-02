"""An instance made by v0.5.0 keeps working under this code (docs/design/rename-brunna.md, v0.6 part 7).

The v0.5.0 tree is taken from the repo's own tag (`git archive`), its `agenthub init` builds an instance in a fake HOME
and its hubbus leaves a letter there; then this code reads the same instance: the letter, a letter to the new name, the
settings file, the CLI wrappers, and doctor finds exactly what v0.5.0's doctor finds. Both run as plain copies, not git
checkouts, so neither looks for newer releases (no network; the answer would differ by checkout). Skipped where the
tag is not there (the gate's exported tree, a shallow clone).
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TAG = "v0.5.0"
SERVICE_MANAGER_LINES = ("✗ scheduler", "✗ watchdog", "! background services")


def _has_tag():
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "-q", "--verify", f"refs/tags/{TAG}"],
                              capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@unittest.skipUnless(_has_tag(), f"tag {TAG} not in this checkout")
class FromV05(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = cls.tmp = pathlib.Path(cls._tmp.name).resolve()
        cls.old = tmp / "old"
        cls.old.mkdir()
        archive = subprocess.run(["git", "-C", str(ROOT), "archive", TAG], capture_output=True, check=True, timeout=120)
        subprocess.run(["tar", "-x", "-C", str(cls.old)], input=archive.stdout, check=True, timeout=120)
        cls.new = tmp / "new"
        listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True, timeout=60)
        for rel in filter(None, listed.stdout.decode().split("\0")):
            src, dst = ROOT / rel, cls.new / rel
            if os.path.lexists(src):
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst, follow_symlinks=False)
        (tmp / "home").mkdir()
        cls.inst = tmp / "inst"
        cls.base = {k: v for k, v in os.environ.items()
                    if not k.startswith(("BRUNNA_", "AGENT_HUB", "AGENTHUB", "BRUNNA", "GIT_", "NVM_"))}
        cls.base.update(HOME=str(tmp / "home"), PATH=f"{tmp / 'cli'}:/usr/bin:/bin", AGENT_HUB_PYTHON=sys.executable,
                        AGENT_HUB_LANG_UI="en", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@t")
        r = cls.run_as(cls.old, "agenthub", "init", "--yes", "--no-launchd", "--owner", "Alex",
                       "--workspace", tmp / "ws", "--home", cls.inst)
        assert r.returncode == 0, r.stdout + r.stderr
        r = cls.run_as(cls.old, "hubbus", "send", "--to", "agent-hub", "--from", "agent-hub", "--type", "request",
                       "--text", "written by v0.5")
        assert r.returncode == 0, r.stdout + r.stderr
        cls.old_letter = r.stdout.strip().split()[-1]
        cls.old_findings = cls.findings(cls.old)
        cls.new_findings = cls.findings(cls.new)   # before the other tests add letters and wrappers

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def run_as(cls, root, script, *args):
        env = {**cls.base, "AGENT_HUB_ROOT": str(root), "AGENT_HUB_HOME": str(cls.inst),
               "AGENT_HUB_STATE": str(cls.tmp / "state")}
        return subprocess.run([sys.executable, str(root / "bin" / script), *map(str, args)], env=env, cwd=cls.tmp,
                              capture_output=True, text=True, timeout=300)

    def test_the_instance_is_v05s(self):
        # what the rest assumes: v0.5.0 wrote the old settings file name
        self.assertTrue((self.inst / "config" / "hub.json").is_file())
        self.assertFalse((self.inst / "config" / "brunna.json").exists())

    def test_letters_old_and_to_the_new_name_reach_the_same_inbox(self):
        r = self.run_as(self.new, "hubbus", "send", "--to", "brunna", "--from", "agent-hub", "--type", "request",
                        "--text", "addressed to the new name")
        self.assertEqual(r.returncode, 0, r.stderr)
        new_letter = r.stdout.strip().split()[-1]
        r = self.run_as(self.new, "hubbus", "inbox", "--project", "agent-hub")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(self.old_letter, r.stdout)
        self.assertIn(new_letter, r.stdout)

    def test_config_reads(self):
        r = self.run_as(self.new, "agenthub", "config", "check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_cli_wrappers_for_both_names(self):
        # the wrapper v0.5 wrote is kept (and rewritten), and the new name is added
        r = self.run_as(self.old, "agenthub", "install-cli", "--dir", self.tmp / "cli")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.run_as(self.new, "agenthub", "install-cli", "--dir", self.tmp / "cli")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(p.name for p in (self.tmp / "cli").iterdir()), ["agenthub", "brunna"])

    @classmethod
    def findings(cls, root):
        r = cls.run_as(root, "agenthub", "doctor")
        assert "Traceback" not in r.stdout + r.stderr, r.stdout + r.stderr
        # the service manager's lines are left out: v0.5.0 read the account's real launchd from a fake HOME, which
        # this code no longer does (hubconf.home_is_the_accounts)
        # the product's and the CLI's names are the release's own (v0.7 says brunna for both): what is found is compared
        same = lambda s: re.sub(r"agent-hub|agenthub|brunna", "<name>", s)   # noqa: E731
        return sorted(same(line.strip().replace(str(root), "<hub>")) for line in r.stdout.splitlines()
                      if line.startswith(("✗", "!")) and not line.startswith(SERVICE_MANAGER_LINES))

    def test_doctor_finds_what_v05_found(self):
        # v0.5.0's own view of its instance, taken before this code touched it (v0.5 can't read what this code writes:
        # going back below v0.6 isn't supported)
        self.assertEqual(self.new_findings, self.old_findings)

if __name__ == "__main__":
    unittest.main()
