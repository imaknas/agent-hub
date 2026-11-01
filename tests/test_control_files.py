"""A sandboxed project can't write what its sessions load settings, hooks and tools from (runtime_ports' control port,
policy.py protect): its sessions run those outside its sandbox, with the user's authority. Its services and jobs are
kept off them by the platform's sandbox, its sessions' file tools by deny rules, and the platform writes the settings
itself, with its own hooks only (11-01, found while closing round 6).

    bin/py -m unittest tests.test_control_files
"""
import importlib.machinery
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import runtime_claude  # noqa: E402
import seatbelt  # noqa: E402

PROTECT = [{"name": n, "tree": t} for n, t in runtime_claude.control_paths()]


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = self.tmp / "ws"
        (self.ws / ".claude" / "worktrees" / "w" / ".claude").mkdir(parents=True)


class Policy(Base):
    def test_policy_carries_the_runtimes_list(self):
        import policy
        fake = type("RT", (), {"control_paths": staticmethod(lambda: [(".x/settings", True), (".x", False)])})
        with mock.patch("runtime_ports.load", lambda ports=(): fake):
            self.assertEqual(policy.protect(), [{"name": ".x/settings", "tree": True}, {"name": ".x", "tree": False}])

    def test_no_control_port_means_none_and_doctor_says_so(self):
        import policy
        import runtime_ports

        def unsupported(ports=()):
            raise runtime_ports.Unsupported("no control port")
        with mock.patch("runtime_ports.load", unsupported):
            self.assertEqual(policy.protect(), [])

    def test_the_sessions_file_tools_are_denied_them_at_any_depth(self):
        pol = {"other_projects": [], "deny_write": [], "deny_read": [], "platform_commands": [], "write": [],
               "network": [], "workspace": self.ws, "protect": PROTECT}
        _sandbox, deny, _allow = runtime_claude.settings(pol)
        self.assertIn(f"Edit(/{self.ws}/**/.claude/settings.local.json)", deny)
        self.assertIn(f"Edit(/{self.ws}/**/.mcp.json)", deny)
        self.assertNotIn(f"Edit(/{self.ws}/**/.claude)", deny, "the folder itself is the sandbox's, not a file rule")


@unittest.skipUnless(seatbelt.can_sandbox() is None, "needs Seatbelt")
class SeatbeltLock(Base):
    """A sandboxed project's service or job (bin/sandboxed-run on macOS) can't write them."""

    def run_in(self, script):
        pol = {"workspace": self.ws, "write": [], "other_projects": [], "deny_read": [], "credentials": [],
               "credential_files": [], "protect": PROTECT}
        prof = seatbelt.service_profile(pol, [self.ws])
        return subprocess.run([seatbelt.SANDBOX_EXEC, "-p", prof, "/bin/sh", "-c", script], capture_output=True,
                              text=True, cwd=self.ws)

    def test_control_files_are_locked_everything_else_is_not(self):
        w = self.ws / ".claude" / "worktrees" / "w"
        r = self.run_in(f"""
echo '{{"hooks": 1}}' > .claude/settings.local.json 2>/dev/null || echo local-blocked
echo '{{}}' > .mcp.json 2>/dev/null || echo mcp-blocked
mkdir -p .claude/hooks 2>/dev/null || echo hooks-blocked
echo x > {w}/.claude/settings.local.json 2>/dev/null || echo worktree-blocked
mv .claude .claude-gone 2>/dev/null || echo rename-blocked
mkdir -p sub/.claude 2>/dev/null && echo x > sub/.claude/settings.json 2>/dev/null || echo nested-blocked
echo ok > code.py && echo code-written
echo ok > {w}/work.py && echo worktree-code-written
""")
        self.assertEqual(r.stdout.split(), ["local-blocked", "mcp-blocked", "hooks-blocked", "worktree-blocked",
                                            "rename-blocked", "nested-blocked", "code-written",
                                            "worktree-code-written"], r.stderr)
        self.assertFalse((self.ws / ".claude" / "settings.local.json").exists())


class PlatformWritesTheSettings(Base):
    """render-project-settings: the platform's hooks only, the same file in every worktree (a plain file, not a link),
    and what it can't remove (committed hooks) reported."""

    def setUp(self):
        super().setUp()
        self.rps = load("render_project_settings_cf", BIN / "render-project-settings.py")
        self.rps.RENDERED = self.tmp / "rendered"
        subprocess.run(["git", "init", "-q", str(self.ws)], check=True)
        self.doc = {"name": "proj", "paths": {"workspace": str(self.ws)}, "enforce": {"sandbox": True}}
        self.index = {"projects": {"proj": self.doc}}
        p = mock.patch.object(hubconf, "projects", lambda: self.index["projects"])
        p.start()
        self.addCleanup(p.stop)

    def test_a_hook_the_platform_didnt_write_is_removed(self):
        f = self.ws / ".claude" / "settings.local.json"
        f.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "evil"}]}]}}))
        drift = self.rps.write_project("proj", self.doc, self.index, report=False)
        written = json.loads(f.read_text())
        self.assertNotIn("evil", json.dumps(written["hooks"]))
        self.assertEqual(written["hooks"], runtime_claude.platform_hooks())
        self.assertTrue(any("hook" in d for d in drift), drift)

    def test_every_worktree_gets_a_plain_copy(self):
        w = self.ws / ".claude" / "worktrees" / "w" / ".claude" / "settings.local.json"
        w.symlink_to(self.tmp / "elsewhere.json")
        self.rps.write_project("proj", self.doc, self.index, report=False)
        self.assertFalse(w.is_symlink())
        self.assertTrue(json.loads(w.read_text())["sandbox"]["enabled"])
        self.assertFalse((self.tmp / "elsewhere.json").exists())

    def test_committed_hooks_are_reported(self):
        (self.ws / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Stop": []}}))
        (self.ws / ".mcp.json").write_text("{}")
        drift = self.rps.write_project("proj", self.doc, self.index, report=False)
        self.assertEqual(sum(1 for d in drift if "settings.json" in d or ".mcp.json" in d), 2, drift)


if __name__ == "__main__":
    unittest.main()
