"""The platform's own entries in the user's tool settings are kept current by brunna upgrade, and nothing else is touched
(AGENTS.md, user-level tool settings; runtime_claude.user_refresh, brunna user-settings, doctor_user_settings).

    bin/py -m unittest tests.test_user_settings_refresh
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import runtime_claude  # noqa: E402
import upgrade  # noqa: E402


def load_agenthub():
    loader = importlib.machinery.SourceFileLoader("agenthub_usr", str(BIN / "agenthub"))
    spec = importlib.util.spec_from_loader("agenthub_usr", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


class Refresh(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.f = self.tmp / "settings.json"
        p = mock.patch.object(runtime_claude, "user_settings_file", lambda: self.f)
        p.start()
        self.addCleanup(p.stop)

    def installed(self, **change) -> dict:
        """What merge-hook-settings.py --worktree writes today, with the owner's own settings beside it."""
        d = runtime_claude.merge_worktree_hooks({"model": "x", "hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": "echo mine"}]}]}})
        if change.get("timeout"):
            [stop] = [h for e in d["hooks"]["Stop"] for h in e["hooks"]]
            stop["timeout"] = change["timeout"]
        return d

    def write(self, d, mode=0o600):
        self.f.write_text(json.dumps(d, indent=4))
        os.chmod(self.f, mode)

    def test_an_earlier_waiter_is_brought_up_and_nothing_else_changes(self):
        old = self.installed(timeout=86400)
        self.write(old, 0o640)
        self.assertEqual(len(runtime_claude.user_refresh(False)), 1, "doctor sees it behind")
        self.assertEqual(json.loads(self.f.read_text()), old, "a check writes nothing")
        self.assertEqual(len(runtime_claude.user_refresh(True)), 1)
        new = json.loads(self.f.read_text())
        [stop] = [h for e in new["hooks"]["Stop"] for h in e["hooks"]]
        self.assertEqual(stop["timeout"], runtime_claude.WAIT_TIMEOUT_S)
        self.assertEqual(new["model"], "x")
        self.assertIn("echo mine", [h["command"] for e in new["hooks"]["SessionStart"] for h in e["hooks"]])
        self.assertEqual(self.f.stat().st_mode & 0o777, 0o640, "the file's mode is kept")
        self.assertEqual(runtime_claude.user_refresh(True), [], "current: nothing to do")
        self.assertEqual(list(self.tmp.iterdir()), [self.f], "no temp file left")

    def test_current_settings_are_not_rewritten_for_their_formatting(self):
        self.write(self.installed())
        before = self.f.read_text()
        self.assertEqual(runtime_claude.user_refresh(True), [])
        self.assertEqual(self.f.read_text(), before)

    def test_an_earlier_hub_location_is_replaced(self):
        old = "[ -x /old/hub/bin/worktree-memory.sh ] && /old/hub/bin/worktree-memory.sh"
        self.write({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": f"{old} start || true"}]}]}})
        self.assertTrue(runtime_claude.user_refresh(True))
        cmds = [h["command"] for es in json.loads(self.f.read_text())["hooks"].values() for e in es for h in e["hooks"]]
        self.assertNotIn(f"{old} start || true", cmds)
        self.assertEqual(sorted(cmds), sorted(c for _e, _m, c in runtime_claude.worktree_hooks()))

    def test_settings_the_owner_never_put_them_in_get_nothing(self):
        for d in ({}, {"model": "x"}, {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}):
            with self.subTest(d=d):
                self.write(d)
                before = self.f.read_text()
                self.assertEqual(runtime_claude.user_refresh(True), [])
                self.assertEqual(self.f.read_text(), before)
        self.f.unlink()
        self.assertEqual(runtime_claude.user_refresh(True), [])
        self.assertFalse(self.f.exists(), "no settings file is made")

    def test_a_link_bad_json_or_odd_shapes_are_left_alone(self):
        real = self.tmp / "real.json"
        real.write_text(json.dumps(self.installed(timeout=86400)))
        self.f.symlink_to(real)
        self.assertEqual(runtime_claude.user_refresh(True), [])
        self.f.unlink()
        wt = runtime_claude.worktree_hooks()[0][2]
        for text in ("{not json", json.dumps([1]), json.dumps({"hooks": [1]}),
                     json.dumps({"hooks": {"Stop": "x", "SessionStart": [{"hooks": [{"command": wt}]}, 3]}})):
            with self.subTest(text=text):
                self.f.write_text(text)
                self.assertEqual(runtime_claude.user_refresh(True), [])
                self.assertEqual(self.f.read_text(), text)

    def test_odd_shapes_anywhere_crash_neither_upgrade_nor_doctor(self):
        wt = runtime_claude.worktree_hooks()[0][2]
        for hooks in ({"Stop": [{"hooks": None}], "SessionStart": [{"hooks": [{"command": wt}]}]},
                      {"Stop": [{"matcher": "x", "hooks": None}]}, {"Stop": [{"hooks": 3}, 5, None]},
                      {"SessionStart": [{"hooks": [None, 2, {"command": wt}]}]}):
            with self.subTest(hooks=hooks):
                self.write({"hooks": hooks})
                runtime_claude.user_refresh(False)
                runtime_claude.user_refresh(True)

    def test_the_owners_odd_entries_and_other_text_survive_a_rewrite(self):
        d = self.installed(timeout=86400)
        d["statusLine"] = {"type": "command", "command": "echo café ✓"}
        d["hooks"]["Notification"] = [{"matcher": "x"}]
        self.write(d)
        self.assertTrue(runtime_claude.user_refresh(True))
        text = self.f.read_text()
        self.assertIn("echo café ✓", text, "written as it was, not escaped")
        new = json.loads(text)
        self.assertEqual(new["hooks"]["Notification"], [{"matcher": "x"}])
        self.assertEqual(new["statusLine"], d["statusLine"])

    def test_a_hook_the_set_gained_is_added(self):
        d = self.installed()
        del d["hooks"]["Stop"]   # installed before the waiter was in the set
        self.write(d)
        self.assertTrue(runtime_claude.user_refresh(True))
        self.assertEqual(len([h for e in json.loads(self.f.read_text())["hooks"]["Stop"] for h in e["hooks"]]), 1)

    def test_an_event_emptied_of_an_old_location_goes(self):
        old = "[ -x /old/hub/bin/worktree-memory.sh ] && /old/hub/bin/worktree-memory.sh"
        d = self.installed()
        d["hooks"]["Gone"] = [{"hooks": [{"type": "command", "command": f"{old} start || true"}]}]
        d["hooks"]["Empty"] = []
        self.write(d)
        self.assertTrue(runtime_claude.user_refresh(True))
        new = json.loads(self.f.read_text())["hooks"]
        self.assertNotIn("Gone", new)
        self.assertEqual(new["Empty"], [], "the owner's own empty event is theirs")

    def test_merge_hook_settings_still_installs_them_and_a_rerun_changes_nothing(self):
        texts = []
        for _ in range(2):
            r = subprocess.run([sys.executable, str(BIN / "merge-hook-settings.py"), "--worktree", str(self.f)],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            texts.append(self.f.read_text())
        self.assertEqual(texts[0], texts[1])
        d = json.loads(texts[0])
        cmds = [h["command"] for es in d["hooks"].values() for e in es for h in e["hooks"]]
        self.assertEqual(len(cmds), len(runtime_claude.worktree_hooks()))
        self.assertTrue(all(runtime_claude._is_worktree_hook(c) for c in cmds))
        [stop] = [h for e in d["hooks"]["Stop"] for h in e["hooks"]]
        self.assertEqual((stop["asyncRewake"], stop["timeout"]), (True, runtime_claude.WAIT_TIMEOUT_S))

    def test_merge_hook_settings_writes_nothing_for_an_option_it_doesnt_know(self):
        for args in (["--help"], ["-h"], ["--worktree"], ["--worktree", "a", "b"], []):
            with self.subTest(args=args):
                r = subprocess.run([sys.executable, str(BIN / "merge-hook-settings.py"), *args], cwd=self.tmp,
                                   capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 2)
                self.assertIn("usage", r.stderr)
                self.assertEqual(list(self.tmp.iterdir()), [], "no file of that name")


class Command(unittest.TestCase):
    """brunna user-settings [--update]: what upgrade runs, and what the owner runs in a checkout upgrade refuses."""

    def run_cmd(self, argv, fn):
        m = load_agenthub()
        mod = mock.Mock(spec=["user_refresh"], user_refresh=fn)
        import runtime_ports
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(runtime_ports, "installed", lambda: ["claude"]), \
                mock.patch.object(runtime_ports, "_module", lambda name: mod), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = m.cmd_user_settings(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_update_applies_and_says_what(self):
        calls = []
        rc, out, _err = self.run_cmd(["--update"], lambda apply: calls.append(apply) or ["the worktree hooks in F"])
        self.assertEqual((rc, calls), (0, [True]))
        self.assertIn("the worktree hooks in F", out)

    def test_without_update_it_only_looks(self):
        calls = []
        rc, out, _err = self.run_cmd([], lambda apply: calls.append(apply) or ["the worktree hooks in F"])
        self.assertEqual((rc, calls), (0, [False]))
        self.assertIn("--update", out)

    def test_a_write_that_fails_fails_the_step(self):
        def fn(apply):
            raise PermissionError(13, "Permission denied")
        rc, _out, err = self.run_cmd(["--update"], fn)
        self.assertEqual(rc, 1)
        self.assertIn("claude", err)

    def test_anything_else_is_usage(self):
        rc, _out, err = self.run_cmd(["--apply"], lambda apply: self.fail("not called"))
        self.assertEqual(rc, 2)

    def test_upgrade_runs_it_after_land_and_not_for_a_target_without_it(self):
        names = [n for n, _argv in upgrade.step_list(True)]
        self.assertEqual(names[names.index("land") + 1], "user settings")
        self.assertEqual(upgrade.step_list(True)[names.index("user settings")][1][-2:], ["user-settings", "--update"])
        self.assertNotIn("user settings", [n for n, _argv in upgrade.step_list(False)])


class Doctor(unittest.TestCase):
    def test_behind_is_a_warning_with_both_ways_to_fix_it(self):
        m = load_agenthub()
        mod = mock.Mock(spec=["user_refresh"], user_refresh=lambda apply: ["the hooks in F"])
        rows = []
        import runtime_ports
        with mock.patch.object(runtime_ports, "_module", lambda name: mod):
            m.doctor_user_settings(lambda *a: rows.append(a), ["claude"])
        [(level, what, fix)] = rows
        self.assertEqual(level, "warn")
        self.assertIn("the hooks in F", what)
        self.assertIn("upgrade", fix)
        self.assertIn("user-settings --update", fix)

    def test_current_or_no_port_says_nothing(self):
        m = load_agenthub()
        rows = []
        import runtime_ports
        for mod in (mock.Mock(spec=["user_refresh"], user_refresh=lambda apply: []), mock.Mock(spec=[])):
            with mock.patch.object(runtime_ports, "_module", lambda name: mod):
                m.doctor_user_settings(lambda *a: rows.append(a), ["claude"])
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
