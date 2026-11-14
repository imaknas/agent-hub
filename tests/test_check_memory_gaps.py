"""check-memory-gaps.py's note count: only commits an agent wrote into memory/ count, the platform's own
(consolidate, librarian, the onboard skeleton, merges during sync) do not; reads the workspace's .agent-memory.

    bin/py -m unittest discover -s tests
"""
import datetime
import importlib.util
import os
import pathlib
import shutil
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
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
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
        self.commit("librarian: register the owner's tasks (assistant)", "x.md")
        self.assertEqual(self.m.note_commits_since(str(self.repo), self.since), 0)

    def test_agent_notes_count_but_merges_do_not(self):
        self.commit("init: memory skeleton", ".gitkeep")
        self.git("checkout", "-q", "-b", "side")
        self.commit("agent-hub: sync memory/STATE.md", "a.md")
        self.git("checkout", "-q", "agent-memory")
        self.commit("consolidate: fold 1 raw notes into reference/ (2026-10-25)", "b.md")
        self.git("merge", "-q", "--no-ff", "-m", "Merge branch 'side' into agent-memory", "side")
        self.assertEqual(self.m.note_commits_since(str(self.repo), self.since), 1)

    def test_no_agent_memory(self):
        self.assertEqual(self.m.note_commits_since(str(self.ws / "nope"), self.since), -1)


class TodoBlock(unittest.TestCase):
    """The fixed block --apply writes into todo.md: with gaps it goes at the end of the file, a rerun does not repeat
        it, with no gaps it is removed."""

    def setUp(self):
        self.m = load()
        self.todo = pathlib.Path(tempfile.mkdtemp()) / "todo.md"
        self.addCleanup(shutil.rmtree, self.todo.parent, ignore_errors=True)
        self.todo.write_text("# todo\n\n- [ ] [agent] x\n")

    def test_block_added_replaced_removed(self):
        self.m.write_todo_block(self.todo, ["- [ ] **a**:…"])
        self.m.write_todo_block(self.todo, ["- [ ] **b**:…"])
        s = self.todo.read_text()
        self.assertEqual(s.count("<!-- memory-gaps:begin -->"), 1)
        self.assertIn("**b**", s)
        self.assertNotIn("**a**", s)
        self.assertTrue(s.startswith("# todo\n\n- [ ] [agent] x\n"))
        self.m.write_todo_block(self.todo, [])
        self.assertEqual(self.todo.read_text(), "# todo\n\n- [ ] [agent] x\n")

    def test_missing_todo_is_left_alone(self):
        missing = self.todo.parent / "nope.md"
        self.m.write_todo_block(missing, ["- [ ] **a**:…"])
        self.assertFalse(missing.exists())


class DigestSections(unittest.TestCase):
    """hublib.digest_append: each job writes its own section; a rerun replaces it in place, order unchanged; an empty
        one removes the section; no file and no content means no file is created."""

    def setUp(self):
        import hublib
        self.h = hublib
        self._data = os.environ.get("AGENT_HUB_DATA")
        # the digest is written in the data directory (hubconf.data_dir()); never the real one
        os.environ["AGENT_HUB_DATA"] = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, os.environ["AGENT_HUB_DATA"], ignore_errors=True)

    def tearDown(self):
        if self._data is None:
            os.environ.pop("AGENT_HUB_DATA", None)
        else:
            os.environ["AGENT_HUB_DATA"] = self._data

    def test_sections(self):
        h, day = self.h, "2026-10-26"
        self.assertIsNone(h.digest_append("memory-gaps", "", day))
        self.assertFalse(h.digest_path(day).exists())
        h.digest_append("projects", "## a\n\nx", day)
        h.digest_append("memory-gaps", "- gap", day)
        h.digest_append("projects", "## a\n\ny", day)
        s = h.digest_path(day).read_text()
        self.assertTrue(s.startswith("# Digest 2026-10-26\n"))
        self.assertLess(s.index("digest:projects"), s.index("digest:memory-gaps"),
                        "a rerun replaces in place, not moved to the end")
        self.assertIn("y", s)
        self.assertNotIn("\nx\n", s)
        h.digest_append("projects", "", day)
        s = h.digest_path(day).read_text()
        self.assertNotIn("digest:projects", s)
        self.assertIn("- gap", s)


if __name__ == "__main__":
    unittest.main()
