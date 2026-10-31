"""bin/git-sync-memory.sh commits only memory/ and STATE.md, and only in a checkout of its own.

10-31: in agent-hub (memory: false) .agent-memory was a plain folder inside the hub repo; the session-end sync ran
there, `git add memory/ STATE.md` added nothing (ignored paths), the "anything staged?" check saw another session's
staged files, and a bare `git commit` committed and pushed them as "sync memory/STATE.md".

    bin/py -m unittest tests.test_git_sync_memory
"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "bin" / "git-sync-memory.sh"
ENV = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, "GIT_CONFIG_GLOBAL": "/dev/null",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
       "AGENT_HUB_OFFLINE": "1"}


def git(cwd, *args) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], env=ENV, capture_output=True, text=True, check=True).stdout


class SyncMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def repo(self, path: pathlib.Path) -> pathlib.Path:
        path.mkdir(parents=True)
        git(path, "init", "-q", "-b", "main")
        (path / "README").write_text("r\n")
        git(path, "add", "README")
        git(path, "commit", "-q", "-m", "init")
        return path

    def run_sync(self, cwd):
        return subprocess.run(["bash", str(SCRIPT)], cwd=cwd, env=ENV, capture_output=True, text=True, timeout=60)

    def test_only_memory_paths_are_committed(self):
        r = self.repo(self.tmp / "proj")
        (r / "code.py").write_text("x = 1\n")
        git(r, "add", "code.py")                       # someone's change, staged and not ready
        (r / "memory").mkdir()
        (r / "memory" / "2026-10-31.md").write_text("note\n")
        self.assertEqual(self.run_sync(r).returncode, 0)
        self.assertEqual(git(r, "show", "--name-only", "--format=", "HEAD").split(), ["memory/2026-10-31.md"])
        self.assertEqual(git(r, "diff", "--cached", "--name-only").split(), ["code.py"], "still staged, not committed")

    def test_a_plain_folder_inside_another_repo_does_nothing(self):
        outer = self.repo(self.tmp / "hub")
        (outer / "bin.py").write_text("y = 2\n")
        git(outer, "add", "bin.py")
        mem = outer / ".agent-memory"
        (mem / "memory").mkdir(parents=True)
        (mem / "memory" / "2026-10-31.md").write_text("note\n")
        head = git(outer, "rev-parse", "HEAD")
        self.assertEqual(self.run_sync(mem).returncode, 0)
        self.assertEqual(git(outer, "rev-parse", "HEAD"), head, "no commit in the enclosing repo")
        self.assertEqual(git(outer, "diff", "--cached", "--name-only").split(), ["bin.py"])

    def test_nothing_to_commit(self):
        r = self.repo(self.tmp / "proj")
        head = git(r, "rev-parse", "HEAD")
        self.assertEqual(self.run_sync(r).returncode, 0)
        self.assertEqual(git(r, "rev-parse", "HEAD"), head)


if __name__ == "__main__":
    unittest.main()
