"""agenthub test (bin/test-runner.py; docs/design/test-waiting.md B4) on a real git repo with its own config, state
and registry index: what it exports, what a tier sees, results and hits, failures with their complete output, and
that nothing a tier started outlives it.

    bin/py -m unittest discover -s tests
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER = ROOT / "bin" / "test-runner.py"
PY = os.environ.get("AGENT_HUB_PYTHON") or sys.executable


class Runner(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        for d in ("home", "config/projects", "state/registry", "bus"):
            (self.tmp / d).mkdir(parents=True)
        (self.tmp / "config" / "hub.json").write_text(json.dumps({"bus_dir": str(self.tmp / "bus"),
                                                                  "registry_dir": "projects"}))
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("AGENT_HUB_", "GIT_"))},
                    "AGENT_HUB_PYTHON": PY, "HOME": str(self.tmp / "home"), "GIT_CONFIG_GLOBAL": "/dev/null",
                    "AGENT_HUB_CONFIG": str(self.tmp / "config"), "AGENT_HUB_STATE": str(self.tmp / "state"),
                    "AGENT_HUB_DATA": str(self.tmp / "data"), "AGENT_HUB_SECRETS": str(self.tmp / "secrets"),
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t", "AGENT_HUB_LANG_UI": "en"}
        self.ws = self.tmp / "ws"
        self.git(self.tmp, "init", "-q", "-b", "main", str(self.ws))
        (self.ws / "check.sh").write_text("test -f ok\n")
        (self.ws / "ok").write_text("")
        self.git(self.ws, "add", "-A")
        self.git(self.ws, "commit", "-q", "-m", "a")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, cwd, *args):
        r = subprocess.run(["git", "-C", str(cwd), *args], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def declare(self, **doc):
        """The validated registry index, as registry.refresh would write it (a v1 entry, or a v2 one)."""
        d = {"v": 1, "name": "demo", "paths": {"workspace": str(self.ws)}, **doc}
        (self.tmp / "state" / "registry" / "index.json").write_text(json.dumps({"v": 1, "projects": {"demo": d}}))

    def run_(self, *args, cwd=None, **env):
        return subprocess.run([PY, str(RUNNER), *args], cwd=cwd or self.ws, env={**self.env, **env},
                              capture_output=True, text=True, timeout=120)

    def test_v1_runtime_test_runs_hits_and_reruns_on_a_change(self):
        self.declare(runtime={"test": "sh check.sh"})
        (self.tmp / "services" / "demo").mkdir(parents=True)
        r = self.run_("--when", "nightly")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("demo/full passed", r.stdout)
        r = self.run_("--when", "nightly")
        self.assertIn("already passed", r.stdout)
        (self.ws / "new.txt").write_text("uncommitted")
        r = self.run_("--when", "nightly")
        self.assertIn("demo/full passed", r.stdout, "an uncommitted change is a new tree")
        self.assertEqual(list((self.ws / ".agent-hub" / "test").iterdir()), [], "run folders are removed")
        self.assertIn("/.agent-hub/", (self.ws / ".git" / "info" / "exclude").read_text().splitlines())
        self.assertEqual(self.git(self.ws, "status", "--porcelain", "--untracked-files=all"), "?? new.txt",
                         "the run folder is not an untracked file")

    def test_failure_keeps_the_complete_output(self):
        self.declare(v=2, tests=[{"tier": "full", "run": "echo first-line; i=0; while [ $i -lt 100 ]; do echo $i; "
                                                         "i=$((i+1)); done; exit 3", "when": ["merge"]}])
        r = self.run_("--tier", "full")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        out = pathlib.Path(r.stdout.split("complete output: ")[1].splitlines()[0])
        self.assertIn("first-line", out.read_text(), "the whole output, not a tail")
        self.assertNotIn("first-line", r.stdout, "the terminal gets the tail")

    def test_what_a_tier_sees(self):
        self.declare(v=2, inputs=["env:WANTED"], tests=[{"tier": "env", "when": ["merge"], "run":
                     'test "$HOME" != "$CALLER_HOME" && test -z "$LEAKED" && test "$WANTED" = yes '
                     '&& test -z "$AGENT_HUB_STATE" && test -f ok && ! test -e .git'}])
        r = self.run_("--tier", "env", CALLER_HOME=self.env["HOME"], LEAKED="x", WANTED="yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_tier_cache_outlives_the_run(self):
        self.declare(v=2, tests=[{"tier": "c", "when": ["merge"], "run":
                                  'n=$(cat "$AGENTHUB_TIER_CACHE/n" 2>/dev/null || echo 0); echo $((n+1)) > "$AGENTHUB_TIER_CACHE/n"'}])
        self.run_("--tier", "c")
        self.run_("--tier", "c", "--force")
        self.assertEqual((self.ws / ".git" / "agent-hub" / "tier-cache" / "demo-c" / "n").read_text().strip(), "2")

    def test_rev_and_index(self):
        self.declare(v=2, tests=[{"tier": "full", "run": "test -f staged", "when": ["merge"]}])
        (self.ws / "staged").write_text("")
        self.git(self.ws, "add", "staged")
        self.assertEqual(self.run_("--tier", "full", "--index").returncode, 0)
        self.assertEqual(self.run_("--tier", "full", "--rev", "HEAD").returncode, 1, "HEAD has no staged file")

    def test_nothing_it_started_outlives_it(self):
        pidfile = self.tmp / "pid"
        self.declare(v=2, inputs=["env:PIDFILE"], tests=[{"tier": "bg", "when": ["merge"],
                                                          "run": 'sleep 300 & echo $! > "$PIDFILE"; exit 0'}])
        r = self.run_("--tier", "bg", PIDFILE=str(pidfile))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        pid = int(pidfile.read_text())
        time.sleep(0.5)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_only_affected_tiers_run_at_merge(self):
        self.git(self.ws, "checkout", "-q", "-b", "work")
        self.declare(v=2, code=["src/**"], tests=[{"tier": "full", "run": "true", "when": ["merge"]}])
        (self.ws / "docs.md").write_text("x")
        r = self.run_()
        self.assertIn("nothing to run", r.stdout)
        (self.ws / "src").mkdir()
        (self.ws / "src" / "a.py").write_text("x")
        r = self.run_()
        self.assertIn("demo/full passed", r.stdout)

    def test_config_inputs(self):
        """hub.json tests.config_inputs: a listed file is passed in by its variable (and keyed), a listed one that is
        missing fails the run, an unlisted name counts as absent (the test that needs it skips)."""
        names = self.tmp / "config" / "private-names.txt"
        tier = {"tier": "pn", "when": ["merge"], "run": 'test "$AGENT_HUB_PRIVATE_NAMES" = "$WANT"'}
        self.declare(v=2, inputs=["config:private-names.txt", "env:WANT"], tests=[tier])
        hub = {"bus_dir": str(self.tmp / "bus"), "registry_dir": "projects"}
        (self.tmp / "config" / "hub.json").write_text(json.dumps(hub))
        self.assertEqual(self.run_("--tier", "pn", WANT="").returncode, 0, "unlisted: not passed")
        hub["tests"] = {"config_inputs": [{"file": "private-names.txt", "env": "AGENT_HUB_PRIVATE_NAMES"}]}
        (self.tmp / "config" / "hub.json").write_text(json.dumps(hub))
        r = self.run_("--tier", "pn", WANT=str(names))
        self.assertEqual(r.returncode, 2, "listed but missing")
        self.assertIn("tests.config_inputs", r.stderr)
        names.write_text("secretproj\n")
        self.assertEqual(self.run_("--tier", "pn", WANT=str(names)).returncode, 0, "listed: passed in")

    def test_not_a_registered_repo(self):
        other = self.tmp / "other"
        self.git(self.tmp, "init", "-q", str(other))
        self.declare()
        r = self.run_(cwd=other)
        self.assertEqual(r.returncode, 2)


class SlotsTest(unittest.TestCase):
    """Worker slots (B5): read-only, no links followed, and a wait that always ends."""

    def setUp(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("test_runner_mod", str(RUNNER))
        self.mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(self.mod)
        self.dir = pathlib.Path(tempfile.mkdtemp()) / "slots"

    def tearDown(self):
        shutil.rmtree(self.dir.parent, ignore_errors=True)

    def test_take_and_release(self):
        s = self.mod.Slots(self.dir)
        n = len(list(self.dir.glob("slot-*")))   # one per CPU: 2 on a small CI runner
        self.assertEqual(s.take(1), 1)
        other = self.mod.Slots(self.dir)
        # only the free ones; with none free a run still starts with one worker (test_all_held_starts_with_one_worker)
        self.assertEqual(other.take(n, wait=0.3), max(n - 1, 1), "only the free ones")
        other.release()
        s.release()

    def test_all_held_starts_with_one_worker(self):
        s = self.mod.Slots(self.dir)
        n = len(list(self.dir.glob("slot-*")))
        s.take(n)
        t0 = time.monotonic()
        self.assertEqual(self.mod.Slots(self.dir).take(1, wait=0.5), 1)
        self.assertLess(time.monotonic() - t0, 5)
        s.release()

    def test_planted_links_and_fifos_are_not_slots(self):
        self.dir.mkdir(parents=True)
        target = self.dir.parent / "target"
        target.write_text("keep")
        os.symlink(target, self.dir / "slot-00")
        os.mkfifo(self.dir / "slot-01")
        s = self.mod.Slots(self.dir)
        got = s.take(1000, wait=0.2)
        self.assertEqual(len(s.fds), len(list(self.dir.glob("slot-*"))) - 2)
        self.assertGreaterEqual(got, 1)
        self.assertEqual(target.read_text(), "keep")
        s.release()


if __name__ == "__main__":
    unittest.main()
