"""Which Claude Code sessions dispatch counts as live (runtime_claude.live_sessions; docs/design/mailbox-runtimes.md B1,
"Left": a forked session's id is not on its command line).

CharacterizeArgv pins what live_sessions reads from the process table: the ids on Claude Code processes' command lines
(--resume / --session-id, spaced or with =). It was committed before the hook records existed and must keep passing
unchanged; that includes reading a fork's parent id off `--resume=<id> --fork-session` (that process runs another id).

A temporary secrets folder; the process table is handed in (CharacterizeArgv) or real processes this test starts. No
Claude session is started.

    bin/py -m unittest tests.test_claude_session_records
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import runtime_claude  # noqa: E402

A = "11111111-2222-4333-8444-555555555555"
B = "22222222-3333-4444-8555-666666666666"
C = "33333333-4444-4555-8666-777777777777"
DESKTOP = "/Users/u/.claude/remote/ccd-cli/2.1.309-40c3beb4ef2f --output-format stream-json --verbose"


def ps(*lines):
    """subprocess.run as live_sessions calls it, answering with this process table."""
    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, "\n".join(lines) + "\n", "")
    return run


class SecretsDir(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        p = mock.patch.object(hubconf, "secrets_dir", lambda: self.tmp / "secrets")
        p.start()
        self.addCleanup(p.stop)


class CharacterizeArgv(SecretsDir):
    """The command-line path, as it was before the hook records."""

    def live(self, *lines):
        with mock.patch.object(runtime_claude.subprocess, "run", ps(*lines)):
            return runtime_claude.live_sessions()

    def test_ids_on_claude_command_lines(self):
        self.assertEqual(self.live(f"{DESKTOP} --resume={A}", f"claude --session-id {B}", "vim notes.md"), {A, B})

    def test_a_fork_reads_as_its_parent(self):
        self.assertEqual(self.live(f"{DESKTOP} --resume={A} --fork-session"), {A})

    def test_a_desktop_session_started_fresh(self):
        self.assertEqual(self.live(f"{DESKTOP} --session-id={C}"), {C})

    def test_no_session_without_an_id_on_the_command_line(self):
        self.assertEqual(self.live(f"{DESKTOP}", "claude", "claude -p hello"), set())

    def test_an_unreadable_process_table_is_no_session(self):
        def fail(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, "", "ps: denied")
        with mock.patch.object(runtime_claude.subprocess, "run", fail):
            self.assertEqual(runtime_claude.live_sessions(), set())

    def test_alive_asks_the_same_set(self):
        with mock.patch.object(runtime_claude.subprocess, "run", ps(f"claude --resume {A}")):
            self.assertTrue(runtime_claude.alive(A))
            self.assertFalse(runtime_claude.alive(B))
            self.assertFalse(runtime_claude.alive(None))


if __name__ == "__main__":
    unittest.main()
