"""The librarian's memory copies are the platform's repos holding project content: checked out without symlinks, so
a STATE.md a project commits as a link never hands the librarian (or the model) the file it points at
(docs/design/projects-and-repos.md section 4).

    bin/py -m unittest tests.test_memory_copies
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hublib  # noqa: E402

ENV = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, "GIT_CONFIG_GLOBAL": "/dev/null",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(cwd, *args) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], env=ENV, capture_output=True, text=True, check=True).stdout


def commit(repo, path, text, msg):
    f = pathlib.Path(repo) / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    git(repo, "add", "--", path)
    git(repo, "commit", "-q", "-m", msg)


class CopiesWithoutSymlinks(unittest.TestCase):
    """Hole 6: the librarian reads the memory copies' files; a project commits STATE.md as a symlink."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_committed_symlink_is_a_plain_file_in_the_copy(self):
        secret = self.tmp / "secret"
        secret.write_text("the owner's secret\n")
        bare = self.tmp / "proj-memory.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "agent-memory", str(bare))
        work = self.tmp / "work"
        git(self.tmp, "init", "-q", "-b", "agent-memory", str(work))
        commit(work, "memory/.gitkeep", "", "init")
        git(work, "push", "-q", str(bare), "agent-memory")
        copy = self.tmp / "copies" / "proj"
        git(self.tmp, "clone", "-q", str(bare), str(copy))   # an existing copy, cloned before this fix
        (work / "STATE.md").symlink_to(secret)
        git(work, "add", "STATE.md")
        git(work, "commit", "-q", "-m", "STATE.md is a link now")
        git(work, "push", "-q", str(bare), "agent-memory")
        self.assertEqual(hublib.pull_copy(copy).returncode, 0)
        self.assertFalse((copy / "STATE.md").is_symlink())
        self.assertEqual((copy / "STATE.md").read_text(), str(secret), "the link's text, not what it points at")


if __name__ == "__main__":
    unittest.main()
