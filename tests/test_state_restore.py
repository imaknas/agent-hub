"""brunna state history / restore (bin/state.py): a project's STATE.md as it was on an earlier day, brought back as a new
commit in the platform's memory copy and pushed to the memory bare repo; the owner's to-do keys stay exactly as they are
now (a key the owner closed since is not revived, one added since is not lost); the librarian's checkpoint goes back to
that day so it folds the notes written since into the restored file. A temp instance: a memory bare repo, the copy, two
days of STATE.md.

    bin/py -m unittest tests.test_state_restore
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"

DAY1 = """# STATE

Status: the demo runs on the shared box.

## Tasks

- [ ] [owner] Renew the domain  (waiting on owner: task-a)
- [ ] [agent] Write the deploy notes
"""
DAY2 = """# STATE

Status: everything was moved to a new box (wrong).

## Tasks

- [ ] [owner] Pick a host  (waiting on owner: task-b)
- [ ] [agent] Rewrite the deploy notes
"""


class StateRestore(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        cfg, data, state = self.tmp / "config", self.tmp / "data", self.tmp / "state"
        for d in (cfg, data, state):
            d.mkdir()
        copies = self.tmp / "copies"
        (cfg / "hub.json").write_text(json.dumps({
            "v": 1, "owner": {"address": "owner", "tag": "Alex"}, "memory_copies": str(copies),
            "language": {"ui": "en", "content": "en"}}))
        self.env = {**{k: v for k, v in os.environ.items()
                       if not k.startswith(("AGENT_HUB_", "BRUNNA_", "GIT_")) or k in ("AGENT_HUB_PYTHON", "BRUNNA_PYTHON")},
                    "HOME": str(self.tmp / "home"), "AGENT_HUB_CONFIG": str(cfg), "AGENT_HUB_DATA": str(data),
                    "AGENT_HUB_STATE": str(state), "AGENT_HUB_LANG_UI": "en",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t"}
        self.state, self.data = state, data
        self.bare = self.tmp / "garden-app-memory.git"
        self.git("init", "-q", "--bare", "-b", "agent-memory", str(self.bare))
        self.copy = copies / "garden-app"
        self.git("clone", "-q", str(self.bare), str(self.copy))
        self.git("-C", str(self.copy), "checkout", "-q", "-b", "agent-memory")
        self.day1 = self.commit("STATE.md", DAY1, "2026-10-01T03:00:00", "librarian: update STATE.md")
        self.commit("memory/2026-10-01.md", "- moved things\n", "2026-10-01T20:00:00", "brunna: sync memory/STATE.md")
        self.day2 = self.commit("STATE.md", DAY2, "2026-10-02T03:00:00", "librarian: update STATE.md")
        self.git("-C", str(self.copy), "push", "-q", "-u", "origin", "agent-memory")
        (state / "garden-app.sha").write_text(self.day2 + "\n")

    def git(self, *args, env=None):
        r = subprocess.run(["git", *args], env=env or self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def commit(self, path, text, when, msg):
        f = self.copy / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        env = {**self.env, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
        self.git("-C", str(self.copy), "add", path, env=env)
        self.git("-C", str(self.copy), "commit", "-q", "-m", msg, env=env)
        return self.git("-C", str(self.copy), "rev-parse", "HEAD")

    def state_py(self, *args):
        return subprocess.run([sys.executable, str(BIN / "state.py"), *args], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def test_history_lists_the_changes_newest_first(self):
        r = self.state_py("history", "garden-app")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [l for l in r.stdout.splitlines() if l.startswith("  ") and "librarian" in l]
        self.assertEqual(len(lines), 2, r.stdout)
        self.assertIn(self.day2[:7], lines[0])
        self.assertIn("2026-10-02", lines[0])
        self.assertIn(self.day1[:7], lines[1])

    def test_without_apply_it_only_shows(self):
        r = self.state_py("restore", "garden-app", "2026-10-01")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("+Status: the demo runs on the shared box.", r.stdout)
        self.assertEqual((self.copy / "STATE.md").read_text(), DAY2)
        self.assertEqual(self.git("-C", str(self.copy), "rev-parse", "HEAD"), self.day2)

    def test_restore_brings_the_day_back_and_keeps_todays_keys(self):
        r = self.state_py("restore", "garden-app", "2026-10-01", "--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = (self.copy / "STATE.md").read_text()
        self.assertIn("Status: the demo runs on the shared box.", text)
        self.assertNotIn("(wrong)", text)
        self.assertIn("- [ ] [agent] Write the deploy notes", text, "unkeyed lines come back as they were")
        self.assertNotIn("task-a", text, "a key closed since is not revived")
        self.assertIn("- [ ] [owner] Pick a host  (waiting on owner: task-b)", text, "a key added since stays")
        head = self.git("-C", str(self.copy), "rev-parse", "HEAD")
        self.assertNotEqual(head, self.day2, "a new commit, history not rewritten")
        self.assertEqual(self.git("-C", str(self.copy), "rev-parse", f"{head}~1"), self.day2)
        self.assertEqual(self.git("--git-dir", str(self.bare), "rev-parse", "agent-memory"), head, "pushed")
        self.assertEqual((self.state / "garden-app.sha").read_text().strip(), self.day1,
                         "the librarian folds the notes since that day into the restored file")
        log = (self.data / "review-log.md").read_text()
        self.assertIn("garden-app", log)
        self.assertIn(self.day1[:7], log)

    def test_keep_checkpoint(self):
        r = self.state_py("restore", "garden-app", self.day1[:7], "--apply", "--keep-checkpoint")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("the shared box", (self.copy / "STATE.md").read_text())
        self.assertEqual((self.state / "garden-app.sha").read_text().strip(), self.day2)

    def test_nothing_to_restore_before_the_first_version(self):
        r = self.state_py("restore", "garden-app", "2026-09-01", "--apply")
        self.assertEqual(r.returncode, 1)
        self.assertEqual((self.copy / "STATE.md").read_text(), DAY2)

    def test_an_unknown_project(self):
        r = self.state_py("history", "nope")
        self.assertEqual(r.returncode, 1)
        self.assertIn("nope", r.stderr)


if __name__ == "__main__":
    unittest.main()
