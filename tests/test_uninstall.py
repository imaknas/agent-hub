"""agenthub uninstall (bin/uninstall.py): it lists first and changes nothing; --apply takes the platform's parts out of
a connected project and keeps the notes; --purge removes the notes and data too; the project's own files and the
user's own hooks are never touched; another hub's scheduler is left alone. No real launchd or systemd unit is touched:
the test hub runs with --no-launchd and the core-unit check is unit-tested on stand-in files.

    bin/py -m unittest tests.test_uninstall
"""
import json
import pathlib
import plistlib
import subprocess
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))
import runtime_claude  # noqa: E402
import test_new_user  # noqa: E402


class Uninstall(unittest.TestCase):
    NewUser = test_new_user.NewUser
    setUp, tearDown, hub_cmd = NewUser.setUp, NewUser.tearDown, NewUser.hub_cmd

    def connected(self) -> pathlib.Path:
        r = self.hub_cmd("init", "--yes", "--no-launchd", "--owner", "Alex", "--workspace", str(self.home / "ws"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cfg = self.home / ".brunna" / "config" / "brunna.json"
        d = json.loads(cfg.read_text())
        d["runtime"] = {"default": "claude"}   # a runtime with a rules file and hooks; its binary isn't needed
        cfg.write_text(json.dumps(d))
        app = self.home / "ws" / "app"
        app.mkdir(parents=True)
        (app / "a.txt").write_text("mine\n")
        r = subprocess.run(["bash", str(self.hub / "bin" / "onboard-project.sh"), "--no-remote", str(app)],
                           env=self.env, capture_output=True, text=True, timeout=120, cwd=self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # the user's own words and hook next to the platform's
        rules = app / "CLAUDE.local.md"
        rules.write_text("my own note\n\n" + rules.read_text())
        s = json.loads((app / ".claude" / "settings.local.json").read_text())
        s["hooks"].setdefault("Stop", []).append({"hooks": [{"type": "command", "command": "say done"}]})
        (app / ".claude" / "settings.local.json").write_text(json.dumps(s))
        return app

    def test_plan_apply_purge(self):
        app = self.connected()
        before = {p: p.read_bytes() for p in app.rglob("*") if p.is_file() and ".git" not in p.parts}
        r = self.hub_cmd("uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CLAUDE.local.md", r.stdout)
        self.assertEqual({p: p.read_bytes() for p in before}, before, "the plan changes nothing")

        r = self.hub_cmd("uninstall", "--apply", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rules = (app / "CLAUDE.local.md").read_text()
        self.assertEqual(rules, "my own note\n", "only the platform's block went")
        s = json.loads((app / ".claude" / "settings.local.json").read_text())
        self.assertEqual(s, {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}})
        self.assertEqual((app / "a.txt").read_text(), "mine\n")
        self.assertTrue((app / ".agent-memory" / "STATE.md").exists(), "notes stay")
        self.assertTrue((self.home / ".brunna" / "config" / "brunna.json").exists(), "settings stay")
        self.assertIn("rm -rf", r.stdout)

        r = self.hub_cmd("uninstall", "--apply", "--yes", "--purge")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((app / ".agent-memory").exists())
        self.assertFalse((self.home / ".brunna").exists())
        self.assertFalse((self.home / "git-origin" / "app-memory.git").exists())
        self.assertEqual((app / "a.txt").read_text(), "mine\n", "the project's own files are never touched")
        branches = subprocess.run(["git", "-C", str(app), "branch", "--list", "agent-memory"], capture_output=True,
                                  text=True).stdout
        self.assertEqual(branches.strip(), "")

    def test_refused_without_a_yes(self):
        self.connected()
        r = self.hub_cmd("uninstall", "--apply")   # no terminal and no --yes: nothing
        self.assertEqual(r.returncode, 1)
        self.assertIn("brunna:begin", (self.home / "ws" / "app" / "CLAUDE.local.md").read_text())


class Parts(unittest.TestCase):
    def test_settings_with_only_the_platforms_hooks_go(self):
        import tempfile
        ws = pathlib.Path(tempfile.mkdtemp())
        (ws / ".claude").mkdir()
        f = ws / ".claude" / "settings.local.json"
        f.write_text(json.dumps(runtime_claude.install_hooks({})))
        self.assertTrue(runtime_claude.remove_lifecycle(ws, check=True))
        self.assertTrue(f.exists(), "check writes nothing")
        self.assertTrue(runtime_claude.remove_lifecycle(ws))
        self.assertFalse(f.exists())
        self.assertFalse(runtime_claude.remove_lifecycle(ws))

    def test_another_hubs_scheduler_is_left_alone(self):
        import importlib.util
        import tempfile
        home = pathlib.Path(tempfile.mkdtemp())
        spec = importlib.util.spec_from_file_location("uninstall_t", ROOT / "bin" / "uninstall.py")
        un = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(un)
        agents = home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        (agents / "local.agent-hub-scheduler.plist").write_bytes(plistlib.dumps(
            {"Label": "local.agent-hub-scheduler", "ProgramArguments": ["/elsewhere/agent-hub/bin/py", "x"]}))
        (agents / "local.agent-hub-scheduler-watchdog.plist").write_bytes(plistlib.dumps(
            {"Label": "w", "ProgramArguments": [str(un.hubconf.MAIN / "bin" / "py"), "x"]}))
        with mock.patch.object(un.pathlib.Path, "home", classmethod(lambda cls: home)), \
                mock.patch.object(un.hubconf, "service_manager", lambda: "launchd"):
            self.assertEqual([label for label, _undo in un.core_units()], ["local.agent-hub-scheduler-watchdog"])


if __name__ == "__main__":
    unittest.main()
