"""`brunna upgrade` (bin/upgrade.py): a user clone moves between releases and main, refuses an unsafe tree, shows the
changelog, and brings the machine in step by running the target's own commands, in order.

The repo under test is built here: a bare "origin" with releases v0.0.1 (older than the upgrade command), v0.1.0 and
v0.2.0, and one unreleased commit on main. Each release carries the real bin/upgrade.py (from v0.1.0 on) and stand-ins
for the commands it runs, which append "<release> <command> <args>" to a log, so the order and the code they came from
can be checked. Nothing touches this machine: HOME, the state and the config are temp folders, and the remote is a local
path.

    bin/py -m unittest tests.test_upgrade
"""
import fcntl
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

# run by the platform's Python (upgrade.py starts them with sys.executable), so they are Python, with no first line
PY_STANDIN = """import os, sys
open(os.environ["UPGRADE_LOG"], "a").write(" ".join(["{mark}", "{name}", *sys.argv[1:]]) + "\\n")
sys.exit(1 if os.environ.get("UPGRADE_FAIL") == " ".join(["{mark}", "{name}", *sys.argv[1:]]) else 0)
"""
SH_STANDIN = """#!/bin/sh
echo "{mark} {name}" >> "$UPGRADE_LOG"
"""
HEADER = "# Changelog\n\nWhat changed in each version.\n\n"
SECTIONS = {
    "v0.0.1": "## v0.0.1 (2026-10-01)\n\n### New\n\n- The first thing\n",
    "v0.1.0": "## v0.1.0 (2026-10-15)\n\n### New\n\n- **agenthub:** One command for the first upgrade\n",
    "v0.2.0": ("## v0.2.0 (2026-11-01)\n\n### New\n\n- **hooks:** The gate runs in the git dir (**breaking:** the old hooks\n"
               "are gone.)\n- **status:** A second thing\n\n### Fixed\n\n- **mailbox:** A fix\n"),
}


class Upgrade(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.home, self.state, self.log_file = self.tmp / "home", self.tmp / "state", self.tmp / "steps.log"
        for d in (self.home / ".local" / "bin", self.state, self.tmp / "config"):
            d.mkdir(parents=True)
        (self.tmp / "config" / "hub.json").write_text(json.dumps({"language": {"ui": "en", "content": "en"}}))
        self.wrapper_dir = self.home / ".local" / "bin"
        w = self.wrapper_dir / "agenthub"
        w.write_text("#!/bin/sh\n# the agent-hub CLI (written by agenthub install-cli; run it again after moving the hub)\n")
        w.chmod(0o755)
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "BRUNNA_", "AGENT_HUB_", "UPGRADE_"))},
                    "HOME": str(self.home), "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t", "PATH": f"{self.wrapper_dir}:/usr/bin:/bin",
                    "AGENT_HUB_STATE": str(self.state), "AGENT_HUB_CONFIG": str(self.tmp / "config"),
                    "AGENT_HUB_DATA": str(self.tmp / "data"), "AGENT_HUB_SECRETS": str(self.tmp / "secrets"),
                    "UPGRADE_LOG": str(self.log_file)}
        self.dev, self.origin, self.user = self.tmp / "dev", self.tmp / "origin.git", self.tmp / "user"
        self.git(self.tmp, "init", "-q", "-b", "main", str(self.dev))
        self.release("v0.0.1", upgrade=False)
        self.release("v0.1.0")
        self.release("v0.2.0")
        self.commit_main("main", "feat(scheduler)!: drop the old flag", "fix(hubbus): an unreleased fix",
                         "chore: not listed")
        self.git(self.tmp, "clone", "-q", "--bare", str(self.dev), str(self.origin))
        self.git(self.tmp, "clone", "-q", str(self.origin), str(self.user))
        self.git(self.user, "checkout", "-q", "--detach", "v0.1.0")
        self.env["AGENT_HUB_ROOT"] = str(self.user)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------- building the repo ----------

    def git(self, cwd, *args) -> str:
        r = subprocess.run(["git", "-C", str(cwd), *args], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"git {' '.join(args)}: {r.stderr}")
        return r.stdout.strip()

    def tree(self, mark: str, upgrade: bool, changelog: str):
        b = self.dev / "bin"
        b.mkdir(exist_ok=True)
        for name in ("registry.py", "agenthub"):
            (b / name).write_text(PY_STANDIN.format(mark=mark, name=name))
        (b / "setup-python").write_text(SH_STANDIN.format(mark=mark, name="setup-python"))
        (b / "setup-python").chmod(0o755)
        for name in ("upgrade.py", "hubconf.py", "i18n.py", "names.py"):
            if upgrade:
                shutil.copy(ROOT / "bin" / name, b / name)
            else:
                (b / name).unlink(missing_ok=True)
        (self.dev / "CHANGELOG.md").write_text(changelog)

    def release(self, tag: str, upgrade: bool = True):
        upto = list(SECTIONS)[:list(SECTIONS).index(tag) + 1]
        self.tree(tag, upgrade, HEADER + "\n".join(SECTIONS[t] for t in reversed(upto)))
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", f"chore(release): {tag}")
        self.git(self.dev, "tag", "-a", tag, "-m", tag)

    def commit_main(self, mark: str, *titles: str):
        self.tree(mark, True, (self.dev / "CHANGELOG.md").read_text())
        for i, title in enumerate(titles):
            (self.dev / f"change-{mark}-{i}").write_text(title)
            self.git(self.dev, "add", "-A")
            self.git(self.dev, "commit", "-q", "-m", title)

    # ---------- running ----------

    def upgrade(self, *args, env=None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(self.user / "bin" / "upgrade.py"), *args],
                              env={**self.env, **(env or {})}, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=120)

    def ran(self) -> list[str]:
        lines = self.log_file.read_text().splitlines() if self.log_file.exists() else []
        self.log_file.unlink(missing_ok=True)
        return lines

    def at(self) -> str:
        return self.git(self.user, "describe", "--tags", "--always")

    def branch(self) -> str:
        return subprocess.run(["git", "-C", str(self.user), "symbolic-ref", "--quiet", "--short", "HEAD"], env=self.env,
                              capture_output=True, text=True).stdout.strip()

    def steps(self, mark: str, scheduler: bool = True) -> list[str]:
        return [f"{mark} setup-python", f"{mark} registry.py check", f"{mark} agenthub config apply",
                *([f"{mark} agenthub init --scheduler-only"] if scheduler else []), f"{mark} agenthub land",
                *([f"{mark} agenthub user-settings --update"] if scheduler else []),
                f"{mark} agenthub install-cli --dir {self.wrapper_dir}", f"{mark} agenthub doctor"]

    # ---------- the cases ----------

    def test_check_reports_the_newest_release_and_moves_nothing(self):
        r = self.upgrade("--check")
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("brunna v0.1.0 → v0.2.0", r.stdout)
        self.assertIn("## v0.2.0 (2026-11-01)", r.stdout)
        self.assertIn("- **mailbox:** A fix", r.stdout)
        self.assertNotIn("## v0.1.0", r.stdout, "only what comes after this checkout's release")
        breaking = r.stdout.split("⚠️ Breaking changes (read these first):")[1].split("## ")[0]
        self.assertIn("v0.2.0: **hooks:** The gate runs in the git dir (**breaking:** the old hooks are gone.)", breaking)
        self.assertNotIn("drop the old flag", r.stdout, "main's unreleased commits are not in a release")
        self.assertEqual(self.at(), "v0.1.0")
        self.assertEqual(self.ran(), [])
        self.assertEqual(json.loads((self.state / "upgrade-check.json").read_text())["newest"], "v0.2.0")

    def test_upgrade_runs_the_targets_steps_in_order(self):
        r = self.upgrade("--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.at(), "v0.2.0")
        self.assertEqual(self.branch(), "", "a release checkout is detached")
        self.assertEqual(self.ran(), self.steps("v0.2.0"))
        self.assertEqual(json.loads((self.state / "upgrade.json").read_text())["commit"],
                         self.git(self.user, "rev-parse", "HEAD"))
        r = self.upgrade("--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("brunna v0.2.0 is up to date (v0.2.0)", r.stdout)
        r = self.upgrade("--yes")
        self.assertEqual((r.returncode, self.ran()), (0, []), "nothing to do: nothing runs")

    def test_without_a_terminal_it_asks_and_stops(self):
        r = self.upgrade()
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("--yes", r.stderr)
        self.assertEqual((self.at(), self.ran()), ("v0.1.0", []))

    def test_a_dirty_tree_is_refused(self):
        (self.user / "CHANGELOG.md").write_text("edited\n")
        for args in (["--check"], ["--yes"]):
            r = self.upgrade(*args)
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
            self.assertIn("uncommitted changes", r.stderr)
        self.assertEqual(self.ran(), [])
        self.assertEqual((self.user / "CHANGELOG.md").read_text(), "edited\n")

    def test_local_commits_are_refused(self):
        (self.user / "mine").write_text("x")
        self.git(self.user, "add", "mine")
        self.git(self.user, "commit", "-q", "-m", "feat: my own work")
        mine = self.git(self.user, "rev-parse", "HEAD")
        r = self.upgrade("--yes")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("1 commit here is on no branch of origin and no release tag", r.stderr)
        self.assertEqual((self.git(self.user, "rev-parse", "HEAD"), self.ran()), (mine, []))

    def test_a_development_branch_is_never_moved(self):
        self.git(self.user, "checkout", "-q", "-b", "work")
        r = self.upgrade("--to", "v0.2.0", "--yes")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("HEAD is on the branch work", r.stderr)
        self.assertEqual((self.branch(), self.at(), self.ran()), ("work", "v0.1.0", []))

    def test_main_ahead_of_its_remote_is_a_development_checkout(self):
        """the author's own setup: develops on main, ahead of the remote"""
        self.git(self.user, "checkout", "-q", "main")
        (self.user / "mine").write_text("x")
        self.git(self.user, "add", "mine")
        self.git(self.user, "commit", "-q", "-m", "feat: work on main")
        r = self.upgrade("--to", "v0.2.0", "--yes")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual((self.branch(), self.ran()), ("main", []))

    def test_going_back_runs_the_older_releases_steps(self):
        self.assertEqual(self.upgrade("--yes").returncode, 0)
        self.ran()
        r = self.upgrade("--to", "v0.1.0", "--check")
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("brunna v0.2.0 → v0.1.0", r.stdout)
        self.assertIn("Going back undoes these changes", r.stdout)
        self.assertIn("## v0.2.0 (2026-11-01)", r.stdout)
        self.assertIn("⚠️ Breaking changes this undoes", r.stdout)
        self.assertNotIn("## v0.1.0", r.stdout)
        r = self.upgrade("--to", "v0.1.0", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.at(), "v0.1.0")
        self.assertEqual(self.ran(), self.steps("v0.1.0"))

    def test_going_back_before_the_upgrade_command_uses_this_versions_steps(self):
        """v0.0.1 has no bin/upgrade.py: the running version's list, without the scheduler step, from v0.0.1's files"""
        r = self.upgrade("--to", "v0.0.1", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.at(), "v0.0.1")
        self.assertEqual(self.ran(), self.steps("v0.0.1", scheduler=False))
        self.assertFalse((self.user / "bin" / "upgrade.py").exists())

    def test_main_follows_main(self):
        r = self.upgrade("--main", "--check")
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("brunna v0.1.0 → origin/main", r.stdout)
        self.assertIn("## v0.2.0", r.stdout)
        unreleased = r.stdout.split("Not released yet (on origin/main):")[1]
        self.assertIn("- feat(scheduler)!: drop the old flag  ⚠️", unreleased)
        self.assertIn("- fix(hubbus): an unreleased fix", unreleased)
        self.assertNotIn("chore: not listed", r.stdout)
        self.assertIn("not released yet: feat(scheduler)!: drop the old flag", r.stdout)
        r = self.upgrade("--main", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.branch(), "main")
        self.assertEqual(self.git(self.user, "rev-parse", "HEAD"), self.git(self.origin, "rev-parse", "main"))
        self.assertEqual(self.ran(), self.steps("main"))
        # a checkout on main follows main by default: the next commit there is fast-forwarded, not a release
        self.commit_main("main2", "feat: next")
        self.git(self.dev, "push", "-q", str(self.origin), "main")
        r = self.upgrade("--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("this checkout is on main, so it follows main", r.stdout)
        self.assertEqual((self.branch(), self.git(self.user, "rev-parse", "HEAD")),
                         ("main", self.git(self.origin, "rev-parse", "main")))
        self.assertEqual(self.ran(), self.steps("main2"))
        r = self.upgrade("--to", "v0.2.0", "--yes")   # and back to releases
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.branch(), self.at()), ("", "v0.2.0"))

    def test_a_failed_step_stops_the_rest_and_says_how_to_go_back(self):
        r = self.upgrade("--yes", env={"UPGRADE_FAIL": "v0.2.0 agenthub config apply"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.ran(), self.steps("v0.2.0")[:3])
        self.assertIn("step config apply failed (exit 1)", r.stderr)
        self.assertIn("brunna upgrade --to v0.1.0", r.stderr)
        self.assertFalse((self.state / "upgrade.json").exists(), "not in step: not recorded")
        r = self.upgrade("--yes", env={"UPGRADE_FAIL": "v0.2.0 agenthub config apply"})   # still failing: no move
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.ran(), self.steps("v0.2.0")[:3])
        self.assertIn("run brunna upgrade again (it reruns the steps)", r.stderr)
        self.assertNotIn("go back", r.stderr, "no record of an earlier version in step on this machine")
        r = self.upgrade("--yes")   # fixed: run again, the checkout is already there, the steps run
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("the in-step commands have not run for this commit", r.stdout)
        self.assertEqual(self.ran(), self.steps("v0.2.0"))

    def test_a_rerun_goes_back_to_where_the_steps_last_passed(self):
        r = self.upgrade("--to", "v0.1.0", "--yes")   # nothing moves; the steps run and v0.1.0 is recorded in step
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.ran(), self.steps("v0.1.0"))
        fail = {"UPGRADE_FAIL": "v0.2.0 registry.py check"}
        self.assertEqual(self.upgrade("--yes", env=fail).returncode, 1)
        self.ran()
        r = self.upgrade("--yes", env=fail)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("go back: brunna upgrade --to v0.1.0", r.stderr)

    def test_a_checkout_moved_by_hand_gets_its_steps(self):
        self.git(self.user, "checkout", "-q", "--detach", "v0.2.0")
        r = self.upgrade("--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("the in-step commands have not run", r.stdout)
        r = self.upgrade("--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.ran(), self.steps("v0.2.0"))

    def test_a_release_cut_after_the_install_is_fetched(self):
        """the usual case: the user installed v0.1.0, then v0.3.0 is released"""
        self.git(self.dev, "checkout", "-q", "--detach", "v0.2.0")
        self.tree("v0.3.0", True, HEADER + "## v0.3.0 (2026-12-01)\n\n### New\n\n- Later\n\n" +
                  (self.dev / "CHANGELOG.md").read_text().removeprefix(HEADER))
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", "chore(release): v0.3.0")
        self.git(self.dev, "tag", "-a", "v0.3.0", "-m", "v0.3.0")
        self.git(self.dev, "push", "-q", str(self.origin), "v0.3.0")
        self.assertNotIn("v0.3.0", self.git(self.user, "tag"))
        r = self.upgrade("--check")
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("brunna v0.1.0 → v0.3.0", r.stdout)
        self.assertIn("## v0.3.0", r.stdout)
        self.assertIn("## v0.2.0", r.stdout)
        r = self.upgrade("--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.at(), "v0.3.0")
        self.assertEqual(self.ran(), self.steps("v0.3.0"))

    def test_a_running_scheduler_tick_refuses(self):
        fd = os.open(self.state / "scheduler.lock", os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            r = self.upgrade("--yes")
        finally:
            os.close(fd)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("a scheduler tick is running", r.stderr)
        self.assertEqual((self.at(), self.ran()), ("v0.1.0", []))

    def test_an_unknown_release_is_a_usage_error(self):
        r = self.upgrade("--to", "v9.9.9")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("releases: v0.0.1, v0.1.0, v0.2.0", r.stderr)

    def test_doctor_asks_the_remote_once_a_day(self):
        code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import upgrade; "
                "print(json.dumps(upgrade.doctor_lines()))")

        def lines():
            r = subprocess.run([sys.executable, "-c", code, str(self.user / "bin")], env=self.env, capture_output=True,
                               text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout)
        first = lines()
        self.assertEqual(first[0][0], "warn")
        self.assertIn("brunna v0.2.0 is out; this checkout is at v0.1.0", first[0][1])
        self.origin.rename(self.tmp / "gone.git")   # within a day the record answers: no network
        self.assertEqual(lines(), first)
        rec = json.loads((self.state / "upgrade-check.json").read_text())
        rec["checked"] = "2026-01-01T00:00:00+00:00"   # expired: asked again, the failure recorded and shown
        (self.state / "upgrade-check.json").write_text(json.dumps(rec))
        again = lines()
        self.assertEqual(again[0][0], "ok")
        self.assertIn("could not check for newer releases", again[0][1])
        self.assertIsNone(json.loads((self.state / "upgrade-check.json").read_text())["newest"])


class Excerpt(unittest.TestCase):
    def test_sections_between_two_releases(self):
        import upgrade
        text = HEADER + "\n".join(SECTIONS[t] for t in ("v0.2.0", "v0.1.0", "v0.0.1"))
        self.assertEqual([t for t, _ in upgrade.between(text, "v0.0.1", "v0.2.0")], ["v0.2.0", "v0.1.0"])
        self.assertEqual([t for t, _ in upgrade.between(text, None, "v0.1.0")], ["v0.1.0", "v0.0.1"])
        self.assertEqual([t for t, _ in upgrade.between(text, "v0.2.0", None)], [])
        v2 = dict(upgrade.sections(text))["v0.2.0"]
        self.assertEqual(upgrade.bullets(v2), ["**hooks:** The gate runs in the git dir (**breaking:** the old hooks are "
                                               "gone.)", "**status:** A second thing", "**mailbox:** A fix"])

    def test_version_order(self):
        import upgrade
        self.assertEqual(upgrade.newest(["v0.9.0", "v0.10.0", "v1.0.0-rc.1", "latest", "v0.2.0"]), "v0.10.0")
        self.assertLess(upgrade.version_key("v1.0.0-rc.1"), upgrade.version_key("v1.0.0"))



class OldestBack(unittest.TestCase):
    """Nothing older than v0.6.1 reads what the rename to brunna writes (docs/design/rename-brunna.md): upgrade refuses
    going back there, and the advice after a jump from there names v0.6.1."""

    def test_too_old_and_the_go_back_advice(self):
        import upgrade
        from unittest import mock
        shas = {"v0.6.1": "c6", "v0.5.0": "c5", "v0.7.0": "c7", "abc1234": "cx", "def5678": "cy"}
        before = {"cx"}   # abc1234 comes before v0.6.0, def5678 after
        def git(*args):
            return subprocess.CompletedProcess(args, 0 if args[0] == "merge-base" and args[2] in before else 1)
        with mock.patch.object(upgrade, "sha", shas.get), mock.patch.object(upgrade, "git", git):
            self.assertTrue(upgrade.too_old("v0.5.0"))
            self.assertTrue(upgrade.too_old("v0.6.0"), "v0.6.0 misses letters to brunna")
            self.assertFalse(upgrade.too_old("v0.6.1"))
            self.assertFalse(upgrade.too_old("v0.7.0"))
            self.assertTrue(upgrade.too_old("abc1234"))
            self.assertFalse(upgrade.too_old("def5678"))
            self.assertTrue(upgrade.go_back("v0.5.0").endswith("upgrade --to v0.6.1"))
            self.assertTrue(upgrade.go_back("v0.7.0").endswith("upgrade --to v0.7.0"))
            self.assertTrue(upgrade.go_back("main").endswith("upgrade --main"))
        with mock.patch.object(upgrade, "sha", lambda r: None if r == upgrade.OLDEST_BACK else "c"), \
                mock.patch.object(upgrade, "git", git):
            self.assertFalse(upgrade.too_old("v0.1.0"), "a repo without the oldest release (a test's) has nothing to refuse")

if __name__ == "__main__":
    unittest.main()
