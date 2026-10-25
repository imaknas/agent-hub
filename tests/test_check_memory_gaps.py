"""check-memory-gaps.py 的筆記計數:只算 agent 寫進 memory/ 的 commit,平台自己的(consolidate、librarian、
onboard 骨架、同步時的 merge)不算;讀 workspace 的 .agent-memory。

    python3 -m unittest discover -s tests
"""
import datetime
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))


def load():
    spec = importlib.util.spec_from_file_location("check_memory_gaps", ROOT / "bin" / "check-memory-gaps.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


class NoteCommits(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.ws = pathlib.Path(tempfile.mkdtemp())
        self.repo = self.ws / ".agent-memory"
        (self.repo / "memory").mkdir(parents=True)
        self.git("init", "-q", "-b", "agent-memory")
        self.since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)

    def git(self, *a):
        subprocess.run(["git", "-C", str(self.repo), *a], check=True, capture_output=True, env=ENV)

    def commit(self, subject, name="2026-10-25.md"):
        f = self.repo / "memory" / name
        f.write_text(f.read_text() + subject + "\n" if f.exists() else subject + "\n")
        self.git("add", "memory")
        self.git("commit", "-q", "-m", subject)

    def test_platform_commits_are_not_notes(self):
        self.commit("init: memory skeleton", ".gitkeep")
        self.commit("consolidate: fold 3 raw notes into reference/ (2026-10-25)", "reference.md")
        self.commit("librarian: Knas 任務登錄(Muninn)", "x.md")
        self.assertEqual(self.m.note_commits_since(str(self.ws), self.since), 0)

    def test_agent_notes_count_but_merges_do_not(self):
        self.commit("init: memory skeleton", ".gitkeep")
        self.git("checkout", "-q", "-b", "side")
        self.commit("agent-hub: sync memory/STATE.md", "a.md")
        self.git("checkout", "-q", "agent-memory")
        self.commit("consolidate: fold 1 raw notes into reference/ (2026-10-25)", "b.md")
        self.git("merge", "-q", "--no-ff", "-m", "Merge branch 'side' into agent-memory", "side")
        self.assertEqual(self.m.note_commits_since(str(self.ws), self.since), 1)

    def test_no_agent_memory(self):
        self.assertEqual(self.m.note_commits_since(str(self.ws / "nope"), self.since), -1)


if __name__ == "__main__":
    unittest.main()
