"""A session in a linked worktree writes its main checkout's memory notes without a prompt, and gains nothing wider.

The worktree reaches <main>/.agent-memory through a link (worktree-memory.sh); Claude Code asks before any write whose
resolved path is outside its working directories, auto mode included. Measured 11-09 on Claude Code 2.1.319 with real
`claude -p` in a throwaway repo: Bash redirection and Edit through the link both asked; an Edit allow rule didn't lift
it; a working directory did; a deny rule held for Bash redirection and Edit, an ask rule only for Edit; a settings file
that is a link gave its hooks but none of its permissions. So the worktree's settings are a plain file naming that
folder as a working directory, with Edit denied on AGENT.md, RULES and .git (runtime_claude.install_worktree /
worktree_memory; render-project-settings for a sandboxed project).

    bin/py -m unittest tests.test_worktree_memory_access
"""
import importlib.machinery
import importlib.util
import json
import os
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
        self.main = self.tmp / "proj"
        self.mem = self.main / ".agent-memory"
        (self.mem / "memory").mkdir(parents=True)
        self.wt = self.main / ".claude" / "worktrees" / "w"
        (self.wt / ".claude").mkdir(parents=True)
        (self.wt / ".agent-memory").symlink_to(self.mem)
        self.main_settings = self.main / ".claude" / "settings.local.json"
        self.wt_settings = self.wt / ".claude" / "settings.local.json"

    def rule(self, name, tree=False):
        p = runtime_claude._rule_path(self.mem / name)
        return [f"Edit({p})"] + ([f"Edit({p}/**)"] if tree else [])


class MemoryAccess(Base):
    def test_the_folder_and_denies_on_what_is_not_notes(self):
        dirs, deny = runtime_claude.memory_access(self.main)
        self.assertEqual(dirs, [str(self.mem)])
        self.assertEqual(deny, self.rule("AGENT.md") + self.rule("RULES") + self.rule(".git", tree=True))
        self.assertFalse(any("STATE.md" in r or "/memory" in r for r in deny), deny)

    def test_a_link_or_nothing_grants_nothing(self):
        elsewhere = self.tmp / "other-project" / ".agent-memory"
        elsewhere.mkdir(parents=True)
        shutil.rmtree(self.mem)
        self.mem.symlink_to(elsewhere)
        self.assertEqual(runtime_claude.memory_access(self.main), ([], []))
        self.mem.unlink()
        self.assertEqual(runtime_claude.memory_access(self.main), ([], []))

    def test_both_spellings_when_the_path_goes_through_a_link(self):
        alias = self.tmp / "alias"
        alias.symlink_to(self.main)
        dirs, deny = runtime_claude.memory_access(alias)
        self.assertEqual(dirs, [str(alias / ".agent-memory"), str(self.mem)])
        self.assertIn(f"Edit({runtime_claude._rule_path(self.mem / 'AGENT.md')})", deny)


class WorktreeMemory(Base):
    def test_merged_kept_and_idempotent(self):
        mine = {"model": "x", "permissions": {"allow": ["Bash(make *)"], "deny": ["Read(./.env)"],
                                              "additionalDirectories": ["/data/shared"]}}
        once = runtime_claude.worktree_memory(mine, self.main)
        self.assertEqual(once["model"], "x")
        self.assertEqual(once["permissions"]["allow"], ["Bash(make *)"])
        self.assertEqual(once["permissions"]["additionalDirectories"], ["/data/shared", str(self.mem)])
        self.assertEqual(once["permissions"]["deny"][0], "Read(./.env)")
        self.assertEqual(runtime_claude.worktree_memory(once, self.main), once)
        self.assertEqual(mine["permissions"]["additionalDirectories"], ["/data/shared"])   # the input isn't changed

    def test_entries_for_an_old_location_are_replaced(self):
        old = runtime_claude.worktree_memory({}, self.main)
        moved = self.tmp / "moved"
        shutil.move(str(self.main), str(moved))
        new = runtime_claude.worktree_memory(old, moved)
        self.assertEqual(new["permissions"]["additionalDirectories"], [str(moved / ".agent-memory")])
        self.assertFalse(any(str(self.main) + "/" in r for r in new["permissions"]["deny"]), new)

    def test_nothing_to_grant_takes_the_old_grant_away(self):
        old = runtime_claude.worktree_memory({"permissions": {"allow": ["Bash(ls)"]}}, self.main)
        shutil.rmtree(self.mem)
        self.assertEqual(runtime_claude.worktree_memory(old, self.main), {"permissions": {"allow": ["Bash(ls)"]}})


class InstallWorktree(Base):
    def test_the_old_link_becomes_a_plain_file_seeded_from_the_main_checkout(self):
        self.main_settings.write_text(json.dumps({"permissions": {"allow": ["Bash(echo mine)"]}}))
        before = self.main_settings.read_text()
        self.wt_settings.symlink_to(self.main_settings)
        self.assertTrue(runtime_claude.install_worktree(self.wt, self.main))
        self.assertFalse(self.wt_settings.is_symlink())
        got = json.loads(self.wt_settings.read_text())
        self.assertEqual(got["permissions"]["allow"], ["Bash(echo mine)"])
        self.assertEqual(got["permissions"]["additionalDirectories"], [str(self.mem)])
        self.assertEqual(got["hooks"], runtime_claude.install_hooks({})["hooks"])
        self.assertEqual(self.main_settings.read_text(), before)   # the main checkout's file is never touched
        self.assertFalse(runtime_claude.install_worktree(self.wt, self.main))

    def test_a_plain_file_keeps_what_the_session_saved_there(self):
        """"Yes, don't ask again" in a worktree session saves its rule in the worktree's file: kept."""
        runtime_claude.install_worktree(self.wt, self.main)
        got = json.loads(self.wt_settings.read_text())
        got["permissions"]["allow"] = ["Bash(npm test)"]
        self.wt_settings.write_text(json.dumps(got))
        runtime_claude.install_worktree(self.wt, self.main)
        again = json.loads(self.wt_settings.read_text())
        self.assertEqual(again["permissions"]["allow"], ["Bash(npm test)"])
        self.assertEqual(again["permissions"]["additionalDirectories"], [str(self.mem)])

    def test_check_writes_nothing(self):
        self.assertTrue(runtime_claude.install_worktree(self.wt, self.main, check=True))
        self.assertFalse(os.path.lexists(self.wt_settings))

    def test_hub_runtime_command(self):
        env = {**os.environ, "AGENT_HUB_STATE": str(self.tmp / "state"), "AGENT_HUB_SECRETS": str(self.tmp / "secrets")}
        r = subprocess.run([str(BIN / "py"), str(BIN / "hub-runtime"), "--runtime", "claude", "worktree-settings",
                            str(self.wt), str(self.main)], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(self.wt_settings.read_text())["permissions"]["additionalDirectories"],
                         [str(self.mem)])


class Sandboxed(Base):
    """render-project-settings: each worktree's plain copy carries its own .agent-memory; the workspace's file doesn't
    (it is inside the session's folder there)."""

    def setUp(self):
        super().setUp()
        self.rps = load("render_project_settings_wma", BIN / "render-project-settings.py")
        self.rps.RENDERED = self.tmp / "rendered"
        subprocess.run(["git", "init", "-q", str(self.main)], check=True)
        self.doc = {"name": "proj", "paths": {"workspace": str(self.main)}, "enforce": {"sandbox": True}}
        self.index = {"projects": {"proj": self.doc}}
        p = mock.patch.object(hubconf, "projects", lambda: self.index["projects"])
        p.start()
        self.addCleanup(p.stop)

    def test_worktrees_get_the_memory_folder_the_workspace_not(self):
        self.rps.write_project("proj", self.doc, self.index, report=False)
        wt = json.loads(self.wt_settings.read_text())
        self.assertEqual(wt["permissions"]["additionalDirectories"], [str(self.mem)])
        self.assertIn(self.rule("AGENT.md")[0], wt["permissions"]["deny"])
        self.assertTrue(wt["sandbox"]["enabled"])
        ws = json.loads(self.main_settings.read_text())
        self.assertNotIn("additionalDirectories", ws["permissions"])
        self.assertNotIn(self.rule("AGENT.md")[0], ws["permissions"]["deny"])

    def test_a_linked_memory_folder_is_never_granted(self):
        elsewhere = self.tmp / "other" / ".agent-memory"
        elsewhere.mkdir(parents=True)
        shutil.rmtree(self.mem)
        self.mem.symlink_to(elsewhere)
        self.rps.write_project("proj", self.doc, self.index, report=False)
        wt = json.loads(self.wt_settings.read_text())
        self.assertNotIn("additionalDirectories", wt["permissions"])
        self.assertNotIn("other", json.dumps(wt["permissions"]))


if __name__ == "__main__":
    unittest.main()
