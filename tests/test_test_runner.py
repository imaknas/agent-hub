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
import unittest.mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER = ROOT / "bin" / "test-runner.py"
sys.path.insert(0, str(ROOT / "bin"))
import names  # noqa: E402
PY = os.environ.get("AGENT_HUB_PYTHON") or os.environ.get("BRUNNA_PYTHON") or sys.executable
import isolation  # noqa: E402
_ADAPTER = isolation.adapter()
# this test process inside some sandbox already (a session's): the wrap tests can't show anything here
ALREADY_CONFINED = bool(_ADAPTER and getattr(_ADAPTER[1], "confined_now", lambda m=False: False)(True))
SANDBOX_HERE = isolation.can_sandbox() is None and not ALREADY_CONFINED


class Runner(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        for d in ("home", "config/projects", "state/shared/registry", "bus"):
            (self.tmp / d).mkdir(parents=True)
        (self.tmp / "config" / "hub.json").write_text(json.dumps({"bus_dir": str(self.tmp / "bus"),
                                                                  "registry_dir": "projects"}))
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("BRUNNA_", "AGENT_HUB_", "GIT_"))},
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
        (self.tmp / "state" / "shared" / "registry" / "index.json").write_text(json.dumps({"v": 1, "projects": {"demo": d}}))

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
        self.assertEqual(list((names.resolve(self.ws, "product", ".{}") / "test").iterdir()), [], "run folders are removed")
        self.assertIn("/.brunna/", (self.ws / ".git" / "info" / "exclude").read_text().splitlines())
        self.assertEqual(self.git(self.ws, "status", "--porcelain", "--untracked-files=all"), "?? new.txt",
                         "the run folder is not an untracked file")

    def test_run_log_records_runs_and_hits_with_their_caller(self):
        """B7's measurements: every tier per invocation, hits included, beside the store; a hit leaves the store as
        it was (a pass's age is the store's alone)."""
        self.declare(v=2, tests=[{"tier": "full", "run": "sh check.sh", "when": ["merge", "commit"]}])
        self.assertEqual(self.run_("--tier", "full", AGENTHUB_TEST_CALLER="merge-branch").returncode, 0)
        store = names.repo_dir(self.ws / ".git") / "test-results.jsonl"
        before = store.read_text()
        self.assertEqual(self.run_("--tier", "full").returncode, 0)
        self.assertEqual(store.read_text(), before, "a hit writes nothing to the store")
        self.git(self.ws, "add", "-A")
        (self.ws / "ok").unlink()
        self.git(self.ws, "add", "-A")
        self.assertEqual(self.run_("--index", "--tier", "full", AGENTHUB_TEST_CALLER="anything else").returncode, 1)
        log = [json.loads(x) for x in (names.repo_dir(self.ws / ".git") / "test-runs.jsonl").read_text().splitlines()]
        self.assertEqual([(r["result"], r["caller"], r["source"]) for r in log],
                         [("pass", "merge-branch", "worktree"), ("hit", "session", "worktree"),
                          ("fail", "commit-gate", "index")])
        self.assertEqual({r["project"] + "/" + r["tier"] for r in log}, {"demo/full"})
        self.assertTrue(all(r["total"] >= r["secs"] >= 0 and r["run"] and r["tree"] for r in log))
        self.assertEqual(len({r["run"] for r in log}), 3)

    def tier_tmpdir(self, env=None, **doc):
        """The run, and the TMPDIR its tier saw (None: no tier ran)."""
        out = self.ws / ".git" / "tmpdir-was"   # where a sandboxed tier may write (not the test's temp folder: under
        # `brunna test` that is the private one, which no sandbox reaches), and outside the tested tree
        # the path is in the command, not an env: input (a sandboxed project's aren't passed)
        self.declare(v=2, tests=[{"tier": "t", "when": ["merge"], "run": f'echo "$TMPDIR" > "{out}" && echo x > "$TMPDIR/f"'}], **doc)
        out.unlink(missing_ok=True)
        r = self.run_("--tier", "t", "--force", **(env or {}))
        return r, pathlib.Path(out.read_text().strip()) if out.exists() else None

    def test_a_tiers_temp_folder_is_private_and_goes_with_the_run(self):
        """Tests execute what they write there: in the system's temp folder a sandboxed project's process ran its code as the user."""
        r, tmp = self.tier_tmpdir()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(tmp.is_relative_to(self.tmp / "secrets" / "tmp"), tmp)
        self.assertEqual(list((self.tmp / "secrets" / "tmp").iterdir()), [], "removed with the run")

    def test_no_private_folder_is_a_failed_run_not_the_systems_folder(self):
        (self.tmp / "secrets").write_text("in the way")
        r, tmp = self.tier_tmpdir()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIsNone(tmp, "no tier ran")
        self.assertIn("private temp folder", r.stderr)

    def test_a_private_folder_inside_a_repo_is_refused(self):
        """The old layout's <hub>/.secrets: tests that strip the git ceiling would find that repo."""
        r, tmp = self.tier_tmpdir(env={"AGENT_HUB_SECRETS": str(self.ws / ".secrets")})
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIsNone(tmp, "no tier ran")
        self.assertIn("move-instance", r.stderr)

    def test_a_sandboxed_projects_run_keeps_it_in_its_own_folder(self):
        """Its sandbox can't reach the private folder, and the system's would let another project's process into this one's sandbox."""
        r, tmp = self.tier_tmpdir(enforce={"sandbox": True})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(tmp.is_relative_to(names.resolve(self.ws, "product", ".{}") / "test"), tmp)
        self.assertFalse(tmp.exists(), "removed with the run")
        self.assertFalse((self.tmp / "secrets").exists(), "the private folder isn't asked for")

    PROBE = 'if cat "{canary}" >/dev/null 2>&1; then echo as-starter; else echo sandboxed; fi > "{out}"'

    def sandbox_probe(self, tier_when=("merge",), **doc):
        """A sandboxed project whose tier reports whether it could read the platform's state (every sandbox denies it;
        the owner can read it). Paths in the command: a sandboxed project's env: inputs aren't passed."""
        canary = self.tmp / "state" / "canary.txt"
        canary.write_text("platform state\n")
        out = self.ws / ".git" / "probe-out"   # as tier_tmpdir's
        self.declare(v=2, enforce={"sandbox": True},
                     tests=[{"tier": "t", "when": list(tier_when), "run": self.PROBE.format(canary=canary, out=out)}], **doc)
        return out

    @unittest.skipUnless(SANDBOX_HERE, "needs this machine's sandbox adapter, outside any sandbox")
    def test_a_sandboxed_projects_tiers_run_in_its_sandbox_whoever_starts_them(self):
        """r2 B3 (docs/design/git-config-fence.md): the project's own commands never run with the starter's authority."""
        out = self.sandbox_probe()
        r = self.run_("--tier", "t", "--force")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(out.read_text().strip(), "sandboxed")
        # the positive control: the same tier of a project that isn't sandboxed runs as the starter and reads it
        idx = self.tmp / "state" / "shared" / "registry" / "index.json"
        doc = json.loads(idx.read_text())
        doc["projects"]["demo"].pop("enforce")
        idx.write_text(json.dumps(doc))
        r = self.run_("--tier", "t", "--force")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(out.read_text().strip(), "as-starter")

    @unittest.skipUnless(SANDBOX_HERE, "needs this machine's sandbox adapter, outside any sandbox")
    def test_the_owners_commit_runs_a_sandboxed_projects_tiers_in_its_sandbox(self):
        """The commit gate's hook, as the owner's own `git commit` starts it from a terminal or an editor."""
        out = self.sandbox_probe(("commit",))
        hook = self.ws / ".git" / "hooks" / "pre-commit"
        hook.write_text(f"#!/bin/sh\nexec '{PY}' '{RUNNER}' --index --when commit\n")
        hook.chmod(0o755)
        (self.ws / "more.txt").write_text("x")
        self.git(self.ws, "add", "more.txt")
        r = subprocess.run(["git", "-C", str(self.ws), "commit", "-q", "-m", "b"], env=self.env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(out.read_text().strip(), "sandboxed")

    @unittest.skipUnless(SANDBOX_HERE, "needs this machine's sandbox adapter, outside any sandbox")
    def test_the_starters_environment_stays_out(self):
        """Review F2: neither an env: input nor the environment sandboxed-run is started with hands the tier a token
        from the owner's shell."""
        out = self.ws / ".git" / "leak"
        self.declare(v=2, enforce={"sandbox": True}, inputs=["env:OWNER_TOKEN"],
                     tests=[{"tier": "t", "when": ["merge"], "run": f'echo "tok=$OWNER_TOKEN" > "{out}"'}])
        r = self.run_("--tier", "t", "--force", OWNER_TOKEN="s3cret-from-owner-shell")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(out.read_text().strip(), "tok=")
        self.assertIn("OWNER_TOKEN: env inputs aren't passed", r.stdout)

    def test_the_repos_own_pointers_dont_decide_which_project_it_is(self):
        """Review F1: a sandboxed project points its .git/commondir at another project's git dir; the run is still
        its own (by folder), and it refuses rather than run the other project's tiers or as the starter."""
        ws2 = self.tmp / "ws2"
        self.git(self.tmp, "init", "-q", "-b", "main", str(ws2))
        (ws2 / "x").write_text("x")
        self.git(ws2, "add", "-A")
        self.git(ws2, "commit", "-q", "-m", "b")
        canary = self.tmp / "state" / "canary.txt"
        canary.write_text("platform state\n")
        out = self.ws / "probe-out"
        (self.tmp / "state" / "shared" / "registry" / "index.json").write_text(json.dumps({"v": 1, "projects": {
            "demo": {"v": 2, "name": "demo", "paths": {"workspace": str(self.ws)}, "enforce": {"sandbox": True},
                     "tests": [{"tier": "t", "when": ["merge"], "run": "true"}]},
            "other": {"v": 2, "name": "other", "paths": {"workspace": str(ws2)},
                      "tests": [{"tier": "t", "when": ["merge"], "run": self.PROBE.format(canary=canary, out=out)}]}}}))
        (self.ws / ".git" / "commondir").write_text(str(ws2 / ".git") + "\n")
        r = self.run_("--tier", "t", "--force")
        # refused either way: inside the sandbox git can't reach the other repo, or it can and the repo isn't its own
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertFalse(out.exists(), "the other project's tier didn't run")

    def test_a_checkout_outside_the_projects_folders_is_refused(self):
        """A worktree a tool keeps in its own folder (Codex's app does) is out of the project's sandbox's reach: said
        plainly, nothing run, rather than a failure inside the sandbox."""
        out = self.sandbox_probe()
        wt = self.tmp / "toolhome" / "worktrees" / "w1"
        self.git(self.ws, "worktree", "add", "-q", "--detach", str(wt))
        r = self.run_("--tier", "t", "--force", cwd=wt)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("outside its folders", r.stderr)
        self.assertFalse(out.exists())

    @unittest.skipUnless(sys.platform == "darwin" and SANDBOX_HERE, "a permissive Seatbelt profile: macOS")
    def test_some_other_sandbox_is_not_the_projects(self):
        """Review F4: under a permissive profile the platform's state is readable, so the run is wrapped; inside a
        profile sandboxed-run can't apply its own here or can, but the tier never runs as the starter."""
        out = self.sandbox_probe()
        r = subprocess.run(["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)", PY, str(RUNNER), "--tier", "t",
                            "--force"], cwd=self.ws, env=self.env, capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            self.assertEqual(out.read_text().strip(), "sandboxed")
        else:   # measured: sandbox-exec can't apply a profile inside this one
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("nothing was run", r.stderr)
            self.assertNotIn("service or job", r.stderr)
            self.assertFalse(out.exists())

    @unittest.skipIf(ALREADY_CONFINED, "this process already runs in a sandbox")
    def test_a_forged_wrapped_mark_is_refused_not_trusted(self):
        """The inner run's mark only stops a loop: outside a sandbox it refuses, it never skips the wrap."""
        out = self.sandbox_probe()
        r = self.run_("--tier", "t", "--force", **{names.env_names("TEST_WRAPPED")[1]: "1"})
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("nothing was run", r.stderr)
        self.assertFalse(out.exists())

    def test_a_project_whose_jobs_are_exempt_runs_as_the_user_in_the_private_folder(self):
        """service_sandbox_exempt: its nightly tiers run as the user, so not in a folder its own sandboxed sessions write; a session of it,
        sandboxed, can't have the private folder and takes the run folder."""
        cfg = self.tmp / "config" / "hub.json"
        cfg.write_text(json.dumps({**json.loads(cfg.read_text()), "service_sandbox_exempt": ["demo"]}))
        r, tmp = self.tier_tmpdir(enforce={"sandbox": True})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(tmp.is_relative_to(self.tmp / "secrets" / "tmp"), tmp)
        (self.tmp / "secrets").rename(self.tmp / "secrets-gone")
        (self.tmp / "secrets").write_text("as a sandbox finds it: no way in")
        r, tmp = self.tier_tmpdir(enforce={"sandbox": True})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(tmp.is_relative_to(names.resolve(self.ws, "product", ".{}") / "test"), tmp)

    def test_the_temp_folder_removed_is_the_one_made_not_one_a_file_names(self):
        """The run folder is in the project's workspace: a path written into it is the project's word, not the runner's."""
        victim = self.tmp / "users-own"
        victim.mkdir()
        (victim / "keep").write_text("x")
        self.declare(v=2, tests=[{"tier": "t", "when": ["merge"], "run": f"echo '{victim}' > ../tmpdir"}])
        r = self.run_("--tier", "t", "--force")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((victim / "keep").exists(), "a folder named in the run folder was removed")
        self.assertEqual(list((self.tmp / "secrets" / "tmp").iterdir()), [], "the one made is gone")

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
        self.assertEqual((names.repo_dir(self.ws / ".git") / "tier-cache" / "demo-c" / "n").read_text().strip(), "2")

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


class SandboxedExport(unittest.TestCase):
    """A sandboxed project's run folder is in its own workspace, where its sandbox may create no control file at any
    depth: those are left out of the tested copy, and the pass is still recorded for the whole tree."""

    def setUp(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("test_runner_export", str(RUNNER))
        self.mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(self.mod)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        for rel in ("src/a.py", ".claude/skills/x/SKILL.md", "sub/.claude/settings.json", ".mcp.json", "notclaude/b.py"):
            (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / rel).write_text(rel)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@x"}
        for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "x"]):
            subprocess.run(["git", "-C", str(self.repo), *cmd], check=True, env=env, capture_output=True)

    def files(self, run):
        return sorted(str(p.relative_to(run / "tree")) for p in (run / "tree").rglob("*") if p.is_file())

    def test_control_files_are_left_out_and_the_tree_id_is_the_whole_trees(self):
        whole = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD^{tree}"], capture_output=True,
                               text=True, check=True).stdout.strip()
        for skip, want in (((), [".claude/skills/x/SKILL.md", ".mcp.json", "notclaude/b.py", "src/a.py",
                                 "sub/.claude/settings.json"]),
                           ([".claude", ".mcp.json"], ["notclaude/b.py", "src/a.py"])):
            with self.subTest(skip=skip):
                run = pathlib.Path(tempfile.mkdtemp(dir=self.tmp))
                self.assertEqual(self.mod.export(self.repo, "rev", "HEAD", run, skip), whole)
                self.assertEqual(self.files(run), want)

    def test_only_a_sandboxed_project_leaves_anything_out(self):
        self.assertEqual(self.mod.sandbox_controls({}), [])
        self.assertEqual(self.mod.sandbox_controls({"enforce": {"sandbox": False}}), [])
        import policy
        with unittest.mock.patch.object(policy, "protect", lambda: [{"name": ".claude", "tree": False}]):
            self.assertEqual(self.mod.sandbox_controls({"enforce": {"sandbox": True}}), [".claude"])
