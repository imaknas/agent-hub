"""The platform runs git only in repos it owns; in a project's repo it runs git only as that project
(docs/design/projects-and-repos.md section 4 "Platform git in project repos", holes listed in its section 9).

A sandboxed project can write its repo's .git: config (fsmonitor, gpg.program with log.showSignature, diff.external,
filters), hooks, objects/info/alternates. Every attack below plants those in a scratch repo and checks that the
platform's job doesn't run them with the user's authority. `wrap` is replaced by a recorder that refuses like
sandboxed-run does where it can't sandbox (exit 78), so these run on any OS; the Seatbelt test runs the real profile.

    bin/py -m unittest tests.test_platform_git_holes
"""
import datetime
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
import isolation  # noqa: E402
import seatbelt  # noqa: E402

ENV = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, "GIT_CONFIG_GLOBAL": "/dev/null",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def git(cwd, *args, **kw) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], env=ENV, capture_output=True, text=True, check=True,
                          **kw).stdout


def commit(repo, path, text, msg):
    f = pathlib.Path(repo) / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    git(repo, "add", "--", path)
    git(repo, "commit", "-q", "-m", msg)


def forge_signed(repo):
    """Turn HEAD into a commit with a (fake) gpgsig header: with log.showSignature, `git log` runs gpg.program."""
    raw = git(repo, "cat-file", "commit", "HEAD")
    head, body = raw.split("\n\n", 1)
    sig = "gpgsig -----BEGIN PGP SIGNATURE-----\n \n iQEz\n -----END PGP SIGNATURE-----"
    new = subprocess.run(["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"], env=ENV,
                         input=f"{head}\n{sig}\n\n{body}", capture_output=True, text=True, check=True).stdout.strip()
    git(repo, "update-ref", "HEAD", new)


def hostile(repo, marker, payload=None):
    """What a sandboxed project can plant in its own .git (tested 11-01 with git 2.50): each runs `evil`, which writes
    the marker (or runs `payload`)."""
    evil = pathlib.Path(repo).parent / f"evil-{pathlib.Path(repo).name}.sh"
    evil.write_text(f"#!/bin/sh\n{payload or f'echo \"$0 $*\" >> {chr(39)}{marker}{chr(39)}'}\nexit 0\n")
    evil.chmod(0o755)
    for k, v in (("core.fsmonitor", evil), ("gpg.program", evil), ("log.showSignature", "true"),
                 ("diff.external", evil), ("filter.evil.clean", evil), ("filter.evil.smudge", evil)):
        git(repo, "config", k, str(v))
    gd = pathlib.Path(git(repo, "rev-parse", "--git-common-dir").strip())
    gd = gd if gd.is_absolute() else pathlib.Path(repo) / gd
    (gd / "info").mkdir(exist_ok=True)
    (gd / "info" / "attributes").write_text("* filter=evil\n")
    for h in ("post-checkout", "reference-transaction", "pre-commit"):
        (gd / "hooks").mkdir(exist_ok=True)
        (gd / "hooks" / h).write_text(f"#!/bin/sh\n'{evil}' {h}\nexit 0\n")
        (gd / "hooks" / h).chmod(0o755)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.marker = self.tmp / "ran"
        self.wrapped: list[tuple[str, str, list]] = []
        self.docs: dict = {}
        for p in (mock.patch.object(isolation, "wrap", self.refuse),
                  mock.patch.object(hubconf, "projects", lambda: self.docs)):
            p.start()
            self.addCleanup(p.stop)

    def refuse(self, project, cwd, argv, port=None):
        """Stands in for bin/sandboxed-run where this machine can't sandbox: records, runs nothing, exits 78."""
        self.wrapped.append((project, str(cwd), list(argv)))
        return ["/bin/sh", "-c", "echo 'sandboxed-run: refused' >&2; exit 78"]

    def repo(self, name, sandbox=True) -> pathlib.Path:
        r = self.tmp / "ws" / name
        r.mkdir(parents=True)
        git(r, "init", "-q", "-b", "main")
        commit(r, "README", "r\n", "init")
        self.docs[name] = {"name": name, "paths": {"workspace": str(r)},
                           **({"enforce": {"sandbox": True}} if sandbox else {})}
        return r

    def assertNotRun(self):
        self.assertFalse(self.marker.exists(), self.marker.read_text() if self.marker.exists() else "")


class StatusPage(Base):
    """worktrees.overview() runs every scheduler tick (gen-status-page.py)."""

    def test_overview_never_runs_git_in_a_sandboxed_repo(self):
        import worktrees
        r = self.repo("proj")
        git(r, "worktree", "add", "-q", str(self.tmp / "wt"), "-b", "feature")
        commit(self.tmp / "wt", "f.txt", "f\n", "feature work")
        forge_signed(self.tmp / "wt")
        hostile(r, self.marker)
        (self.tmp / "wt" / "dirty").write_text("x")
        ov = worktrees.overview()
        self.assertNotRun()
        self.assertIn(("proj", str(r)), [(p, c) for p, c, _a in self.wrapped])
        self.assertEqual(ov["proj"]["worktrees"], [])
        self.assertIn("refused", ov["proj"]["unavailable"])

    def test_unsandboxed_projects_still_get_facts(self):
        import worktrees
        r = self.repo("open", sandbox=False)
        git(r, "worktree", "add", "-q", str(self.tmp / "wt2"), "-b", "feature")
        commit(self.tmp / "wt2", "f.txt", "f\n", "feature work")
        ov = worktrees.overview()
        w = ov["open"]["worktrees"]
        self.assertEqual([(x["branch"], x["merged"], x["clean"], x["files"]) for x in w],
                         [("feature", False, True, ["f.txt"])])
        self.assertIsNotNone(w[0]["age_days"])
        self.assertEqual(self.wrapped, [])


class MemoryGaps(Base):
    def test_note_count_comes_from_the_platforms_bare_repo(self):
        gaps = load("check_memory_gaps_holes", BIN / "check-memory-gaps.py")
        r = self.repo("proj")
        git(r, "worktree", "add", "-q", "--orphan", "-b", "agent-memory", str(r / ".agent-memory"))
        commit(r / ".agent-memory", "memory/n.md", "note\n", "a note")
        forge_signed(r / ".agent-memory")
        bare = self.tmp / "origin" / "proj-memory.git"
        git(self.tmp, "clone", "-q", "--bare", str(r), str(bare))
        self.docs["proj"]["paths"]["memory_bare"] = str(bare)
        hostile(r, self.marker)
        repo = gaps.notes_repo("proj")
        self.assertEqual(repo, bare)
        since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
        self.assertEqual(gaps.note_commits_since(str(repo), since), 1)
        self.assertNotRun()


class Backup(Base):
    def test_stage_runs_the_workspace_git_as_the_project(self):
        import backup
        r = self.repo("proj")
        hostile(r, self.marker)
        (r / "README").write_text("changed\n")   # uncommitted: `git diff HEAD` runs diff.external
        with mock.patch.object(backup, "STAGE", self.tmp / "stage"), \
                mock.patch.object(backup, "CFG", {"bare_repos": str(self.tmp / "no-bare")}), \
                mock.patch.object(hubconf, "bus_db", lambda: self.tmp / "no-bus.db"), \
                mock.patch.object(backup, "say", lambda *a, **k: None):
            m = backup.stage()
        self.assertNotRun()
        self.assertIn(("proj", str(r)), [(p, c) for p, c, _a in self.wrapped])
        self.assertEqual(m["workspace"], [])
        self.assertEqual([s["project"] for s in m["skipped"]], ["proj"])


class Propose(Base):
    def test_worktree_add_runs_as_the_project(self):
        pa = load("propose_apply_holes", BIN / "propose_apply.py")
        r = self.repo("proj")
        hostile(r, self.marker)
        agents = []

        def no_agent(cmd, **kw):   # the real agent never runs in a test
            if "llm.py" in " ".join(map(str, cmd)):
                agents.append(cmd)
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return real(cmd, **kw)
        real = subprocess.run
        with mock.patch.object(pa.subprocess, "run", no_agent):
            with self.assertRaises(RuntimeError):
                pa.run("proj", r, "mailbox", "prompt", ["memory/**"])
        self.assertNotRun()
        self.assertTrue(self.wrapped)
        self.assertEqual(agents, [], "the agent never started")

    def test_copy_back_does_not_follow_a_symlink_the_agent_made(self):
        pa = load("propose_apply_holes2", BIN / "propose_apply.py")
        r = self.repo("open", sandbox=False)
        (self.tmp / "secret").write_text("not the agent's to read")

        def agent(cmd, **kw):
            if "llm.py" in " ".join(map(str, cmd)):
                wt = pathlib.Path(cmd[cmd.index("--cwd") + 1])
                (wt / "memory").mkdir()
                (wt / "memory" / "x.md").symlink_to(self.tmp / "secret")
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"structured_output": {}}), "")
            return real(cmd, **kw)
        real = subprocess.run
        with mock.patch.object(pa.subprocess, "run", agent):
            res = pa.run("open", r, "mailbox", "prompt", ["memory/**"])
        self.assertFalse((r / "memory" / "x.md").exists() and "not the agent's" in (r / "memory" / "x.md").read_text())
        self.assertIn("memory/x.md", res["refused"])


class MemorySync(Base):
    def project_with_memory(self, name, sandbox=True):
        r = self.repo(name, sandbox)
        bare = self.tmp / "origin" / f"{name}-memory.git"
        bare.mkdir(parents=True)
        git(bare, "init", "-q", "--bare", "-b", "agent-memory")
        seed = self.tmp / f"seed-{name}"
        git(self.tmp, "init", "-q", "-b", "agent-memory", str(seed))
        commit(seed, "memory/.gitkeep", "", "init: memory skeleton")
        git(seed, "push", "-q", str(bare), "agent-memory")
        git(r, "remote", "add", "mini", str(bare))
        git(r, "fetch", "-q", "mini")
        git(r, "worktree", "add", "-q", "-b", "agent-memory", str(r / ".agent-memory"), "mini/agent-memory")
        git(r / ".agent-memory", "branch", "-q", "--set-upstream-to=mini/agent-memory")
        self.docs[name]["paths"]["memory_bare"] = str(bare)
        return r, bare

    def test_sync_of_a_sandboxed_project_runs_as_the_project(self):
        import project_git
        r, bare = self.project_with_memory("proj")
        (r / ".agent-memory" / "memory" / "n.md").write_text("note\n")
        hostile(r, self.marker)
        why = project_git.sync_memory("proj", self.docs["proj"], r / ".agent-memory")
        self.assertIn("refused", why)
        self.assertNotRun()
        self.assertEqual([(p, a[a.index(str(project_git.JOB)) + 1]) for p, _c, a in self.wrapped],
                         [("proj", "memory-sync")])

    def test_git_sync_memory_delegates_for_a_sandboxed_project(self):
        """The hourly sync and the session hooks both go through git-sync-memory.sh. What runs there can't read
        another project (the fixture is in the temp area, which the sandbox may write, so reading is what's checked)."""
        r, bare = self.project_with_memory("proj")
        other = self.repo("other", sandbox=False)
        (other / "secret").write_text("the other project's secret\n")
        (r / ".agent-memory" / "memory" / "n.md").write_text("note\n")
        hostile(r, self.marker, payload=f"cat '{other}/secret' >> '{self.marker}'")
        state = self.tmp / "state"
        (state / "registry").mkdir(parents=True)
        (state / "registry" / "index.json").write_text(json.dumps({"projects": self.docs}))
        env = {**ENV, "AGENT_HUB_STATE": str(state), "AGENT_HUB_OFFLINE": "1"}
        p = subprocess.run(["bash", str(BIN / "git-sync-memory.sh")], cwd=r / ".agent-memory", env=env,
                           capture_output=True, text=True, timeout=120)
        self.assertFalse(self.marker.exists() and "secret" in self.marker.read_text(), p.stderr)
        if isolation.can_sandbox() is None:   # where this machine can sandbox, the note did reach the bare repo
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("memory/n.md", git(bare, "ls-tree", "-r", "--name-only", "agent-memory").split())

    def test_platform_half_fast_forwards_and_refuses_the_rest(self):
        import project_git
        _r, bare = self.project_with_memory("open", sandbox=False)
        work = self.tmp / "work"
        git(self.tmp, "clone", "-q", "-b", "agent-memory", str(bare), str(work))
        base = git(work, "rev-parse", "HEAD").strip()
        commit(work, "memory/a.md", "a\n", "a")
        b1 = self.tmp / "b1.bundle"
        git(work, "bundle", "create", "-q", str(b1), "HEAD", f"^{base}")
        self.assertIsNone(project_git.fetch_bundle(bare, b1, "agent-memory"))
        self.assertEqual(git(bare, "rev-parse", "agent-memory"), git(work, "rev-parse", "HEAD"))
        git(work, "reset", "-q", "--hard", base)
        commit(work, "memory/b.md", "b\n", "b (diverged)")
        b2 = self.tmp / "b2.bundle"
        git(work, "bundle", "create", "-q", str(b2), "HEAD", f"^{base}")
        self.assertIn("fast-forward", project_git.fetch_bundle(bare, b2, "agent-memory"))
        self.assertEqual(git(bare, "for-each-ref", "refs/agent-hub"), "")

    @unittest.skipUnless(seatbelt.can_sandbox() is None, "needs Seatbelt")
    def test_alternates_cannot_carry_another_projects_objects(self):
        """Hole 2: the project's repo borrows another project's objects (objects/info/alternates). Built as the
        project, the bundle can't contain what the project can't read."""
        import project_git
        r, bare = self.project_with_memory("proj")
        other = self.repo("other", sandbox=False)
        commit(other, "secret.txt", "the other project's secret\n", "secret")
        blob = git(other, "rev-parse", "HEAD:secret.txt").strip()
        (r / ".git" / "objects" / "info" / "alternates").write_text(str(other / ".git" / "objects") + "\n")
        mem = r / ".agent-memory"
        git(mem, "update-index", "--add", "--cacheinfo", f"100644,{blob},memory/stolen.md")
        git(mem, "commit", "-q", "-m", "a note that is someone else's blob")
        pol = {"workspace": r, "write": [], "other_projects": [other], "deny_read": [], "credentials": [],
               "credential_files": []}

        def sandboxed(project, cwd, argv, port=None):
            return [seatbelt.SANDBOX_EXEC, "-p", seatbelt.service_profile(pol, [r]), *argv]
        with mock.patch.object(isolation, "wrap", sandboxed):
            project_git.sync_memory("proj", self.docs["proj"], mem)
        self.assertNotEqual(subprocess.run(["git", "-C", str(bare), "cat-file", "-e", blob], env=ENV).returncode, 0,
                            "the other project's object reached this project's memory bare repo")


class GateHooks(Base):
    """Hole 7: render-project-settings installs gate: strict hooks in .git/hooks."""

    def setUp(self):
        super().setUp()
        self.rps = load("render_project_settings_holes", BIN / "render-project-settings.py")

    def test_a_planted_symlink_is_never_written_through(self):
        r = self.repo("open", sandbox=False)
        target = self.tmp / "outside" / "x"
        target.parent.mkdir()
        (r / ".git" / "hooks" / "pre-commit").symlink_to(target)
        self.rps.gate("open", {**self.docs["open"], "gate": "strict"}, report=False, quiet=True)
        self.assertFalse(target.exists())
        self.assertTrue((r / ".git" / "hooks" / "pre-commit").is_symlink(), "a link there is the project's own hook")
        self.assertIn(self.rps.GATE_MARK, (r / ".git" / "hooks" / "pre-merge-commit").read_text())

    def test_hook_write_replaces_a_link_instead_of_following_it(self):
        r = self.repo("open", sandbox=False)
        target = self.tmp / "outside2"
        (r / ".git" / "hooks" / "pre-commit").symlink_to(target)
        subprocess.run([sys.executable, str(BIN / "project-git-job.py"), "hook-write", str(r), "pre-commit"],
                       input="#!/bin/sh\n", text=True, check=True)
        self.assertFalse(target.exists())
        self.assertFalse((r / ".git" / "hooks" / "pre-commit").is_symlink())

    def test_a_sandboxed_projects_hooks_are_written_as_the_project(self):
        r = self.repo("proj")
        hooks = self.tmp / "elsewhere"
        hooks.mkdir()
        shutil.rmtree(r / ".git" / "hooks")
        (r / ".git" / "hooks").symlink_to(hooks)
        self.rps.gate("proj", {**self.docs["proj"], "gate": "strict"}, report=False, quiet=True)
        self.assertEqual(list(hooks.iterdir()), [])
        self.assertTrue(self.wrapped)


if __name__ == "__main__":
    unittest.main()
