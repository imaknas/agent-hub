"""Acceptance for a brand-new user (docs/design/new-user.md 3.6): an empty instance with only hub.example.json + a
fake HOME; `hub init --yes --no-launchd` can write hub.json and create the folders, the config is valid, `hub config
plan` and `hub doctor` run (doctor reports problems like "the scheduler is not loaded" but must not crash), and the
generated settings contain no personal names. Never touches launchd or calls a model.

The platform's own registry entry (projects/agent-hub.yaml) still lives in the user's registry folder, so a copy is
made as usual; step 6 is to move it back into the platform.

    bin/py -m unittest discover -s tests
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PERSONAL = re.compile(r"knas|muninn|imaknas", re.I)
sys.path.insert(0, str(ROOT / "bin"))
import i18n  # noqa: E402


def scheduler_not_running() -> str:
    """doctor's zh-Hant line for this hub's scheduler when it is not running: not loaded, a different hub's, no service
    manager at all, or (systemd) not installed"""
    def zh(domain, msgid, **kw):
        return i18n.translate(domain, msgid, kw, "zh-Hant")
    what, label = zh("agenthub", "scheduler"), "local.agent-hub-scheduler"
    lines = [zh("agenthub", "{what} ({label}): not loaded", what=what, label=label),
             zh("agenthub", "{what} ({label}): the loaded one belongs to a different hub, not {bin}", what=what,
                label=label, bin="{bin}").split("{bin}")[0],
             zh("agenthub", "scheduler, watchdog: {reason}", reason=zh(
                 "hubconf", "this system has no supported service manager (neither macOS launchd nor the Linux systemd "
                 "user level): the scheduler, the watchdog and projects' long-running services will not run by "
                 "themselves")),
             zh("agenthub", "{what} ({label}.timer): not installed", what=what, label=label)]
    return "✗ (?:" + "|".join(map(re.escape, lines)) + ")"


class NewUser(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.hub, self.home = self.tmp / "hub", self.tmp / "home"
        self.home.mkdir()
        for d in ("bin", "contracts", "templates", "modules", "launchd", "capabilities", "githooks", "locales"):
            shutil.copytree(ROOT / d, self.hub / d, ignore=shutil.ignore_patterns("__pycache__"))
        (self.hub / "projects").mkdir()
        shutil.copy(ROOT / "projects" / "agent-hub.yaml", self.hub / "projects")
        shutil.copy(ROOT / "hub.example.json", self.hub)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "AGENT_HUB"))}
        self.env = {**env, "HOME": str(self.home), "AGENT_HUB_ROOT": str(self.hub),
                    "AGENT_HUB_PYTHON": sys.executable, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "AGENT_HUB_LANG_UI": "zh-Hant",
                    # the fake HOME has no git identity: do not let commit rely on git guessing (macOS can guess, a
                    # Linux container cannot)
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hub_cmd(self, *args):
        return subprocess.run([sys.executable, str(self.hub / "bin" / "agenthub"), *args], env=self.env,
                              capture_output=True, text=True, timeout=120, cwd=self.hub)

    def test_empty_instance_comes_up(self):
        r = self.hub_cmd("init", "--yes", "--no-launchd", "--owner", "Alex", "--timezone", "UTC",
                         "--workspace", str(self.home / "workspace"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # a new user starts with the new layout: instance directory config/ + data/ + secrets/
        inst = self.home / ".agent-hub"
        self.assertEqual((self.hub / ".hub-home").read_text().strip(), str(inst))
        self.assertFalse((self.hub / "hub.json").exists(), "no user hub.json in the platform repo")
        cfg = (inst / "config" / "hub.json").read_text()
        self.assertIn('"tag": "Alex"', cfg)
        self.assertEqual(PERSONAL.findall(cfg), [], "a new user's hub.json must not contain someone else's name")
        for d in (inst / "config" / "projects", inst / "data", inst / "secrets", self.hub / ".state", self.home / "workspace"):
            self.assertTrue(d.is_dir(), d)
        self.assertEqual(oct((inst / "secrets").stat().st_mode & 0o777), "0o700")
        self.assertTrue((inst / "config" / ".git").is_dir(), "config as code: config is its own git repo")
        hp = subprocess.run(["git", "-C", str(inst / "config"), "config", "core.hooksPath"], capture_output=True, text=True)
        self.assertEqual(pathlib.Path(hp.stdout.strip()).resolve(), (self.hub / "githooks" / "config").resolve())

        r = subprocess.run([sys.executable, str(self.hub / "bin" / "config-check")], env=self.env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        r = self.hub_cmd("config", "plan")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # no coding agent, no Ollama, no --llm: the modules that only do judgment-point work stay off
        self.assertNotIn("job:consolidate/", r.stdout, "no model for judgment points: consolidate is not on by default")
        self.assertNotIn("job:reflect/", r.stdout, "no model for judgment points: reflect is not on by default")
        self.assertIn("job:owner/notify", r.stdout, "modules that need no model keep their defaults")
        self.assertNotIn("job:assistant/", r.stdout, "without the assistant enabled there are no assistant jobs")

        r = self.hub_cmd("doctor")
        self.assertNotIn("Traceback", r.stdout + r.stderr)
        self.assertRegex(r.stdout, scheduler_not_running(), "this hub's scheduler is not installed, and doctor must say so")

    def test_llm_only_modules_follow_the_llm(self):
        """consolidate and reflect only do judgment-point work: on by default when a model is set up, off (and said
        so) when none is; asked for by name with no model, they stay on with a warning"""
        def modules():
            import json
            return json.loads((self.home / ".agent-hub" / "config" / "hub.json").read_text())["modules"]
        base = ("init", "--yes", "--no-launchd", "--owner", "Alex", "--workspace", str(self.home / "workspace"))
        r = self.hub_cmd(*base, "--llm", "anthropic", "--llm-model", "m1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(modules()["consolidate"] and modules()["reflect"], r.stdout)
        for f in (self.home / ".agent-hub", self.hub / ".hub-home"):
            shutil.rmtree(f) if f.is_dir() else f.unlink()
        r = self.hub_cmd(*base, "--llm", "none")
        self.assertFalse(modules()["consolidate"] or modules()["reflect"], r.stdout)
        self.assertTrue(modules()["owner"])
        self.assertIn("consolidate, reflect", r.stdout, "init says which modules it left off")
        self.assertIn("agenthub modules enable", r.stdout)
        for f in (self.home / ".agent-hub", self.hub / ".hub-home"):
            shutil.rmtree(f) if f.is_dir() else f.unlink()
        r = self.hub_cmd(*base, "--llm", "none", "--modules", "consolidate,owner")
        self.assertTrue(modules()["consolidate"], "an explicit choice is kept")
        self.assertIn("consolidate", r.stdout)
        self.assertIn("agenthub llm setup", r.stdout)

    def test_config_repo_gate_rejects_bad_config(self):
        self.hub_cmd("init", "--yes", "--no-launchd", "--owner", "Alex")
        cfg = self.home / ".agent-hub" / "config"
        (cfg / "hub.json").write_text((cfg / "hub.json").read_text().replace('"modules"', '"modulez"'))
        git = ["git", "-C", str(cfg), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*git, "add", "-A"], env=self.env, check=True, capture_output=True)
        r = subprocess.run([*git, "commit", "-q", "-m", "x"], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, "the config repo's pre-commit must block an invalid config")
        self.assertIn("modulez", r.stdout + r.stderr)

    def test_move_instance_copies_validates_then_points(self):
        """Moving the old layout (hub.json in the hub root) into the instance directory: copy only (the source stays),
        the config repo gets a first commit, and the pointer file is written last."""
        (self.hub / "hub.json").write_text((self.hub / "hub.example.json").read_text())
        (self.hub / "todo.md").write_text("# todo\n")
        (self.hub / ".secrets").mkdir()
        (self.hub / ".secrets" / "x").write_text("s")
        inst = self.tmp / "inst"
        r = self.hub_cmd("move-instance", "--to", str(inst))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(inst.exists(), "without --apply it only lists, does nothing")
        r = self.hub_cmd("move-instance", "--to", str(inst), "--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.hub / ".hub-home").read_text().strip(), str(inst.resolve()))
        for f in ("config/hub.json", "config/projects", "data/todo.md", "secrets/x"):
            self.assertTrue((inst / f).exists(), f)
        self.assertTrue((self.hub / "hub.json").exists() and (self.hub / "todo.md").exists(),
                        "the source is not deleted")
        log = subprocess.run(["git", "-C", str(inst / "config"), "log", "--oneline"], capture_output=True, text=True)
        self.assertIn("config moved", log.stdout)
        self.assertFalse((inst / "config" / "projects" / "agent-hub.yaml").exists(),
                         "the platform's own registry entry stays in the platform")
        r = self.hub_cmd("move-instance", "--to", str(self.tmp / "again"), "--apply")
        self.assertNotEqual(r.returncode, 0, "once moved, not moved again")

    def test_init_acts_only_on_arguments_it_knows(self):
        """`init --help` once ran a real init (11-01); help, a typo or a missing value must write nothing"""
        for args, rc in ((["--help"], 0), (["-h"], 0), (["--yes", "--bogus"], 2), (["--yes", "--owner"], 2)):
            r = self.hub_cmd("init", *args)
            self.assertEqual(r.returncode, rc, (args, r.stdout + r.stderr))
            self.assertFalse((self.home / ".agent-hub").exists(), args)
            self.assertFalse((self.hub / ".hub-home").exists(), args)
            self.assertFalse((self.hub / "hub.json").exists(), args)
        self.assertIn("agenthub init", self.hub_cmd("init", "--help").stdout)

    def test_core_services_only_from_the_main_checkout(self):
        """units rendered from a linked worktree would run the scheduler from there, and it refuses every tick"""
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("agenthub_cli", str(ROOT / "bin" / "agenthub"))
        spec = importlib.util.spec_from_loader("agenthub_cli", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        saved, run = mod.hubconf.MAIN, mod.subprocess.run
        try:
            mod.hubconf.MAIN = mod.hubconf.ROOT.parent / "elsewhere"
            mod.subprocess.run = lambda *a, **k: self.fail(f"ran {a[0]}")
            out = mod.install_core_plists(dry=False)
        finally:
            mod.hubconf.MAIN, mod.subprocess.run = saved, run
        self.assertEqual(len(out), 1)
        self.assertIn(str(mod.hubconf.ROOT), out[0])

    def git_cfg(self, *args, env=None):
        return subprocess.run(["git", "-C", str(self.home / ".agent-hub" / "config"), *args], env=env or self.env,
                              capture_output=True, text=True)

    def assert_onboard_commits(self, env):
        """the config repo holds init's commit and nothing uncommitted, and onboard's registry commit lands on it (it
        once failed: the repo had no commit and hub.json untracked, so the pre-commit check found no hub.json)"""
        self.assertEqual(self.git_cfg("status", "--porcelain", env=env).stdout, "", "init leaves the config repo clean")
        self.assertIn("hub.json", self.git_cfg("ls-files", env=env).stdout.split())
        # a newcomer's hub is a clone: commit-hub-files.sh takes its lock in the hub checkout's git dir
        subprocess.run(["git", "init", "-q", str(self.hub)], env=env, check=True)
        ws = self.home / "workspace" / "demo"
        r = subprocess.run([sys.executable, str(self.hub / "bin" / "agenthub"), "onboard", "--no-remote", str(ws)],
                           env=env, capture_output=True, text=True, timeout=120, cwd=self.hub)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("projects/demo.yaml", self.git_cfg("log", "--name-only", "--format=", env=env).stdout.split(),
                      r.stdout + r.stderr)
        self.assertEqual(self.git_cfg("status", "--porcelain", "--", "projects", env=env).stdout, "")

    def test_onboard_after_init_commits_the_registry(self):
        r = self.hub_cmd("init", "--yes", "--no-launchd", "--owner", "Alex", "--llm", "none",
                         "--workspace", str(self.home / "workspace"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.git_cfg("config", "--local", "user.email").stdout, "",
                         "with an identity of its own, git commits as the user: none is set in the repo")
        self.assert_onboard_commits(self.env)

    def test_init_without_a_git_identity(self):
        """a fresh container has no user.email: init says so, gives the config repo an identity of its own (only there)
        and commits; the platform's later commits there (commit-hub-files.sh, as onboard's registry step) work too.
        Onboard as a whole is not run here: it also commits in the user's own project, which needs their identity and
        where git says so itself"""
        env = {k: v for k, v in self.env.items() if not k.startswith("GIT_") and k != "EMAIL"}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        r = subprocess.run([sys.executable, str(self.hub / "bin" / "agenthub"), "init", "--yes", "--no-launchd",
                            "--owner", "Alex", "--llm", "none", "--workspace", str(self.home / "workspace")],
                           env=env, capture_output=True, text=True, timeout=120, cwd=self.hub)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("user.email", r.stdout, "init says git has no identity, and how to set yours")
        self.assertEqual(self.git_cfg("config", "--local", "user.name", env=env).stdout.strip(), "Alex")
        self.assertTrue(self.git_cfg("config", "--local", "user.email", env=env).stdout.strip())
        self.assertEqual(self.git_cfg("status", "--porcelain", env=env).stdout, "", "init leaves the config repo clean")
        subprocess.run(["git", "init", "-q", str(self.hub)], env=env, check=True)   # a clone, as a newcomer's hub is
        hub_json = self.home / ".agent-hub" / "config" / "hub.json"
        hub_json.write_text(hub_json.read_text().replace('"tag": "Alex"', '"tag": "Alexa"'))
        r = subprocess.run([str(self.hub / "bin" / "commit-hub-files.sh"), "owner renamed", "hub.json"], env=env,
                           capture_output=True, text=True, timeout=120, cwd=self.hub)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.git_cfg("log", "--format=%s", env=env).stdout.splitlines()), 2)

    def cfg_json(self):
        import json
        return json.loads((self.home / ".agent-hub" / "config" / "hub.json").read_text())

    def test_init_writes_the_language(self):
        r = self.hub_cmd("init", "--yes", "--no-launchd", "--owner", "Alex", "--llm", "none", "--lang", "en,zh-Hant")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.cfg_json()["language"], {"ui": "en", "content": "zh-Hant"})

    def test_init_defaults_to_the_environments_language(self):
        r = self.hub_cmd("init", "--yes", "--no-launchd", "--owner", "Alex", "--llm", "none")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.cfg_json()["language"], {"ui": "zh-Hant", "content": "zh-Hant"})

    def test_init_lang_on_an_existing_instance_changes_only_the_language(self):
        subprocess.run(["git", "init", "-q", str(self.hub)], env=self.env, check=True)   # a clone, as a real hub is
        self.hub_cmd("init", "--yes", "--no-launchd", "--owner", "Alex", "--llm", "none")
        before = self.cfg_json()
        commits = len(self.git_cfg("log", "--format=%s").stdout.splitlines())
        r = self.hub_cmd("init", "--lang", "en")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        after = self.cfg_json()
        self.assertEqual(after.pop("language"), {"ui": "en", "content": "en"})
        before.pop("language")
        self.assertEqual(after, before, "nothing but the language changed")
        self.assertEqual(len(self.git_cfg("log", "--format=%s").stdout.splitlines()), commits + 1)
        self.assertEqual(self.git_cfg("status", "--porcelain", "--", "hub.json").stdout, "")

    def test_init_refuses_an_unknown_language(self):
        r = self.hub_cmd("init", "--yes", "--no-launchd", "--lang", "fr")
        self.assertEqual(r.returncode, 2)
        self.assertFalse((self.home / ".agent-hub").exists(), "nothing was done")

    def test_init_never_overwrites_hub_json(self):
        (self.hub / "hub.json").write_text('{"v": 1, "registry_dir": "projects", "owner": {"address": "owner", "tag": "Keep"}}\n')
        r = self.hub_cmd("init", "--yes", "--no-launchd", "--owner", "Other")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('"tag": "Keep"', (self.hub / "hub.json").read_text())


if __name__ == "__main__":
    unittest.main()
