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
        with mock.patch("runtime_ports.load", lambda name=None, ports=(): fake):
            self.assertEqual(policy.protect(), [{"name": ".x/settings", "tree": True}, {"name": ".x", "tree": False}])

    def test_no_control_port_means_none_and_doctor_says_so(self):
        import policy
        import runtime_ports

        def unsupported(name=None, ports=()):
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

    def test_other_agents_control_folders_are_denied_at_any_depth_and_by_the_sandbox(self):
        """Codex's .codex and .agents (runtime-codex.md, Step 4 r2, S1): Edit denies at any depth (the sandbox takes them
        in, so Bash too), and a denyWrite on each one at the top of every session folder as a second layer, whether
        it exists or not."""
        import runtime_codex
        protect = PROTECT + [{"name": n, "tree": t} for n, t in runtime_codex.control_paths()]
        pol = {"other_projects": [], "deny_write": [], "deny_read": [], "platform_commands": [], "write": [],
               "network": [], "workspace": self.ws, "protect": protect}
        sandbox, deny, _allow = runtime_claude.settings(pol)
        for name in (".codex", ".agents"):
            self.assertIn(f"Edit(/{self.ws}/**/{name})", deny)
            self.assertIn(f"Edit(/{self.ws}/**/{name}/**)", deny)
        dw = sandbox["filesystem"]["denyWrite"]
        w = self.ws / ".claude" / "worktrees" / "w"
        for folder in (self.ws, w):
            for name in (".codex", ".agents"):
                self.assertIn(str(folder / name), dw)
        self.assertFalse(any(e.endswith("/.claude") or e.endswith("/.mcp.json") for e in dw),
                         "Claude's own are its sandbox's and its Edit rules', not this layer's")

    def test_the_rules_hold_where_else_it_writes_and_in_every_spelling(self):
        """review A-B2 (a tree staged in the temp area or its runtime folder and moved in) and A-C1 (the long s)"""
        import runtime_codex
        protect = PROTECT + [{"name": n, "tree": t} for n, t in runtime_codex.control_paths()]
        pol = {"other_projects": [], "deny_write": [], "deny_read": [], "platform_commands": [],
               "write": [str(self.tmp / "runtime")], "network": [], "workspace": self.ws, "protect": protect}
        _sandbox, deny, _allow = runtime_claude.settings(pol)
        for root in (self.tmp / "runtime", "/private/tmp", "/private/var/folders"):
            self.assertIn(f"Edit(/{root}/**/.codex/**)", deny)
        self.assertIn(f"Edit(/{self.ws}/**/.agentſ/**)", deny)
        self.assertIn(f"Edit(/{self.ws}/**/.claude/ſettings.json)", deny)

    def test_a_change_under_a_control_name_anywhere_is_held_before_a_merge(self):
        """review S2: merge-branch says it before anything moves"""
        import policy
        got = policy.protected_changes([".agents/skills/x/SKILL.md", "sub/.codex/config.toml", "src/a.py",
                                        ".claude/worktrees/w/a.py", "docs/agents.md"])
        self.assertEqual(got, [".agents/skills/x/SKILL.md", "sub/.codex/config.toml"])

    def test_the_tracked_op_lists_files_under_control_names_at_any_depth(self):
        repo = self.tmp / "repo"
        for p in (".agents/skills/x/SKILL.md", "sub/.codex/config.toml", "src/a.py", "docs/agents.md"):
            (repo / p).parent.mkdir(parents=True, exist_ok=True)
            (repo / p).write_text("x")
        env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "PATH": "/usr/bin:/bin"}
        subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
        r = subprocess.run([sys.executable, str(BIN / "project-git-job.py"), "tracked", str(repo), ".codex", ".agents"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(json.loads(r.stdout), [".agents/skills/x/SKILL.md", "sub/.codex/config.toml"], r.stderr)

    def test_no_other_agent_means_no_second_layer(self):
        pol = {"other_projects": [], "deny_write": [], "deny_read": [], "platform_commands": [], "write": [],
               "network": [], "workspace": self.ws, "protect": PROTECT}
        sandbox, _deny, _allow = runtime_claude.settings(pol)
        self.assertNotIn("denyWrite", sandbox["filesystem"])


@unittest.skipUnless(seatbelt.can_sandbox() is None, "needs Seatbelt")
class SeatbeltLock(Base):
    """A sandboxed project's service or job (bin/sandboxed-run on macOS) can't write them."""

    def run_in(self, script, roots=(), protect=None):
        pol = {"workspace": self.ws, "write": [], "other_projects": [], "deny_read": [], "credentials": [],
               "credential_files": [], "protect": PROTECT if protect is None else protect, "protect_roots": list(roots)}
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

    def test_codex_control_folders_are_locked_at_any_depth(self):
        """runtime-codex.md, Step 4 r2, S1: a sandboxed project's service can't plant a .codex or .agents folder, at the
        top, nested or in a worktree, and Codex's tool folder is one rule (control_root, nothing writable)."""
        import runtime_codex
        protect = PROTECT + [{"name": n, "tree": t} for n, t in runtime_codex.control_paths()]
        folder, writable = runtime_codex.control_root()
        w = self.ws / ".claude" / "worktrees" / "w"
        r = self.run_in(f"""
mkdir -p .codex 2>/dev/null && echo x > .codex/config.toml 2>/dev/null || echo root-blocked
mkdir -p sub/.codex 2>/dev/null && echo x > sub/.codex/config.toml 2>/dev/null || echo nested-blocked
mkdir -p {w}/.codex 2>/dev/null && echo x > {w}/.codex/hooks.json 2>/dev/null || echo worktree-blocked
mkdir -p .agents/skills/x 2>/dev/null && echo x > .agents/skills/x/SKILL.md 2>/dev/null || echo agents-blocked
mkdir -p other && mv other .codex 2>/dev/null || echo rename-blocked
echo ok > code.py && echo code-written
""", [{"name": folder, "writable": list(writable)}], protect)
        self.assertEqual(r.stdout.split(), ["root-blocked", "nested-blocked", "worktree-blocked", "agents-blocked",
                                            "rename-blocked", "code-written"], r.stderr)
        self.assertFalse((self.ws / ".codex" / "config.toml").exists())

    def test_a_tree_staged_elsewhere_and_moved_in_carries_no_control_name(self):
        """review A-B2: Seatbelt checks a rename at its two ends only, so a control name made in the temp area and moved
        in nested would land; it can't be made there in the first place (the denies hold anywhere)."""
        import runtime_codex
        protect = PROTECT + [{"name": n, "tree": t} for n, t in runtime_codex.control_paths()]
        stage = self.tmp / "stage"
        stage.mkdir()
        r = self.run_in(f"""
mkdir -p {stage}/a/.codex 2>/dev/null && echo x > {stage}/a/.codex/config.toml 2>/dev/null || echo codex-stage-blocked
mkdir -p {stage}/b/.agents/skills/x 2>/dev/null || echo agents-stage-blocked
mkdir -p {stage}/c/.claude 2>/dev/null || echo claude-stage-blocked
mkdir -p {stage}/d && echo ok > {stage}/d/file && echo plain-staged
mv {stage}/a {self.ws}/sub2 2>/dev/null; mv {stage}/b {self.ws}/sub3 2>/dev/null; mv {stage}/c {self.ws}/sub4 2>/dev/null
mv {stage}/d {self.ws}/.claude/worktrees/w2 2>/dev/null && echo plain-moved
""", protect=protect)
        self.assertEqual(r.stdout.split(), ["codex-stage-blocked", "agents-stage-blocked", "claude-stage-blocked",
                                            "plain-staged", "plain-moved"], r.stderr)
        for p in ("sub2/.codex", "sub3/.agents", "sub4/.claude"):
            self.assertFalse((self.ws / p).exists(), p)

    def test_the_tool_folder_is_one_rule_but_its_worktrees(self):
        """V-M9: nothing new or changed under the tool's folder (a name no list knows included), its worktrees
        written, a control name inside a worktree still not."""
        folder, writable = runtime_claude.control_root()
        roots = [{"name": folder, "writable": list(writable)}]
        w = self.ws / folder / "worktrees" / "w"
        (self.ws / folder / "kept").write_text("x")
        probe = f"""
echo x > {folder}/new-name 2>/dev/null || echo new-blocked
echo y >> {folder}/kept 2>/dev/null || echo change-blocked
rm {folder}/kept 2>/dev/null || echo remove-blocked
mkdir {folder}/newdir 2>/dev/null || echo mkdir-blocked
echo x > {w}/{folder}/settings.json 2>/dev/null || echo worktree-control-blocked
echo x > {w}/{folder}/agent-memory 2>/dev/null || echo worktree-folder-blocked
mkdir -p {w}/{folder}/worktrees/v && echo ok > {w}/{folder}/worktrees/v/a && echo nested-worktree-written
echo ok > {w}/work.py && echo worktree-written
mkdir -p {w}/sub && echo ok > {w}/sub/a && echo worktree-dir-written
"""
        r = self.run_in(probe, roots)
        self.assertEqual(r.stdout.split(), ["new-blocked", "change-blocked", "remove-blocked", "mkdir-blocked",
                                            "worktree-control-blocked", "worktree-folder-blocked",
                                            "nested-worktree-written", "worktree-written", "worktree-dir-written"],
                         r.stderr)
        self.assertFalse((self.ws / folder / "new-name").exists())
        # the positive control: without the rule a name no list knows is written
        self.assertIn("new-written", self.run_in(f"echo x > {folder}/other && echo new-written").stdout)


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

    def test_a_new_platform_hook_set_is_not_drift(self):
        """A platform update that changes its own hooks (e.g. a new Stop entry) rewrites them without calling it drift:
        the file holds what the platform wrote last time. A hook someone else added still is."""
        f = self.ws / ".claude" / "settings.local.json"
        self.rps.write_project("proj", self.doc, self.index, report=False)
        old = runtime_claude.platform_hooks()
        newer = {**old, "Stop": [{"hooks": [{"type": "command", "command": "~/agent-hub/bin/hub-event wait"}]}]}
        with mock.patch.object(runtime_claude, "platform_hooks", lambda: newer):
            drift = self.rps.write_project("proj", self.doc, self.index, report=False)
            self.assertEqual(json.loads(f.read_text())["hooks"], newer)
            self.assertFalse(any("hook" in d for d in drift), drift)
            written = json.loads(f.read_text())
            written["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "evil"}]})
            f.write_text(json.dumps(written))
            drift = self.rps.write_project("proj", self.doc, self.index, report=False)
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
