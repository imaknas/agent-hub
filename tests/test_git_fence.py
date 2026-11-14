"""The git config fence's scan, report only (bin/git_fence.py; docs/design/git-config-fence.md, r2's order of work,
step 1): hostile repos are reported, a clean one holds, and nothing of a project's runs while it is scanned.

The scan runs as a separate process in a throwaway instance (its own config, state and registry index), as the
platform's job would.
"""
import json
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "git_fence.py"
sys.path.insert(0, str(ROOT / "bin"))
import git_fence  # noqa: E402
PY = os.environ.get("AGENT_HUB_PYTHON") or os.environ.get("BRUNNA_PYTHON") or sys.executable


def index_bytes(paths: list[bytes], version: int = 2) -> bytes:
    """A minimal index of regular-file entries (hash all zeros) in version 2 or 4."""
    out, prev = [b"DIRC", struct.pack(">II", version, len(paths))], b""
    for p in paths:
        head = b"\0" * 40 + b"\0" * 20 + struct.pack(">H", min(len(p), 0xFFF))
        if version == 4:
            common = len(os.path.commonprefix([prev, p]))
            strip = len(prev) - common
            assert strip < 0x80
            out.append(head + bytes([strip]) + p[common:] + b"\0")
        else:
            n = (len(head) + len(p) + 8) & ~7
            out.append(head + p + b"\0" * (n - len(head) - len(p)))
        prev = p
    return b"".join(out)


class Pure(unittest.TestCase):
    def test_index_paths_read_what_git_writes(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp), "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_CONFIG_NOSYSTEM": "1"}
        subprocess.run(["git", "init", "-q", str(tmp / "r")], check=True, env=env)
        for f in ("a.txt", "dir/b.txt", "dir/sub/c.txt", "dir/sub/cd.txt"):
            (tmp / "r" / f).parent.mkdir(parents=True, exist_ok=True)
            (tmp / "r" / f).write_text("x")
        subprocess.run(["git", "-C", str(tmp / "r"), "add", "-A"], check=True, env=env)
        want = [b"a.txt", b"dir/b.txt", b"dir/sub/c.txt", b"dir/sub/cd.txt"]
        for version in ("2", "3", "4"):
            subprocess.run(["git", "-C", str(tmp / "r"), "update-index", "--index-version", version], check=True, env=env)
            self.assertEqual(git_fence.index_paths((tmp / "r" / ".git" / "index").read_bytes()), want, version)

    def test_index_entries_into_the_git_dir_are_told_apart(self):
        for p in (b".git/config", b".GIT/hooks/x", b"sub/.git/config", b".gi\xe2\x80\x8ct/config", b"git~1/config",
                  b".git./config", b".git /config"):
            self.assertTrue(git_fence.into_git_dir(p), p)
        for p in (b"a.txt", b".github/workflows/x.yml", b"src/.gitignore", b"gits/x", b".gitattributes"):
            self.assertFalse(git_fence.into_git_dir(p), p)
        self.assertEqual(git_fence.index_paths(index_bytes([b"a", b".git/config"], 4)), [b"a", b".git/config"])
        with self.assertRaises(ValueError):
            git_fence.index_paths(b"DIRC" + struct.pack(">II", 2, 3))

    def test_the_allowlist(self):
        remotes = {"origin"}
        ok = [("core.repositoryformatversion", "0"), ("core.bare", "false"), ("remote.origin.url", "git@github.com:a/b.git"),
              ("remote.origin.url", "https://github.com/a/b.git"), ("remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"),
              ("branch.main.remote", "origin"), ("branch.main.merge", "refs/heads/main"), ("user.name", "A B"),
              ("extensions.refstorage", "files"), ("maintenance.gc.enabled", "true"), ("extensions.worktreeConfig", "true")]
        for k, v in ok:
            self.assertIsNone(git_fence.check_key(k, v, remotes), (k, v))
        bad = [("filter.x.clean", "evil", "key"), ("core.fsmonitor", "/x", "key"), ("core.bare", "true", "value"),
               ("remote.origin.url", "ext::sh -c x", "value"), ("remote.origin.url", "/elsewhere/repo", "value"),
               ("remote.origin.url", "git@-oProxyCommand=x:y", "value"), ("branch.main.remote", "evil", "value"),
               ("remote.origin.uploadpack", "x", "key"), ("alias.st", "!x", "key"), ("core.worktree", "/x", "key")]
        for k, v, why in bad:
            self.assertEqual(git_fence.check_key(k, v, remotes), why, (k, v))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for d in ("home", "config/projects", "state/shared/registry", "bus", "secrets", "temp"):
            (self.tmp / d).mkdir(parents=True)
        (self.tmp / "config" / "hub.json").write_text(json.dumps({"bus_dir": str(self.tmp / "bus"),
                                                                  "registry_dir": "projects"}))
        self.canary = self.tmp / "ran"
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("BRUNNA_", "AGENT_HUB_", "GIT_"))},
                    "HOME": str(self.tmp / "home"), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                    "AGENT_HUB_CONFIG": str(self.tmp / "config"), "AGENT_HUB_STATE": str(self.tmp / "state"),
                    "AGENT_HUB_DATA": str(self.tmp / "data"), "AGENT_HUB_SECRETS": str(self.tmp / "secrets"),
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t", "AGENT_HUB_LANG_UI": "en"}
        self.ws = self.tmp / "ws"
        self.git(self.tmp, "init", "-q", "-b", "main", str(self.ws))
        (self.ws / "a.txt").write_text("a\n")
        (self.ws / ".gitattributes").write_text("*.txt filter=x\n")
        self.git(self.ws, "add", "-A")
        self.git(self.ws, "commit", "-q", "-m", "a")
        self.git(self.ws, "remote", "add", "origin", "git@github.com:someone/demo.git")
        self.doc = {"v": 2, "name": "demo", "paths": {"workspace": str(self.ws)}, "enforce": {"sandbox": True}}
        self.declare()

    def git(self, cwd, *args):
        r = subprocess.run(["git", "-C", str(cwd), *args], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def declare(self, **more):
        self.doc.update(more)
        (self.tmp / "state" / "shared" / "registry" / "index.json").write_text(
            json.dumps({"v": 1, "projects": {"demo": self.doc}}))

    def scan(self, *args) -> dict:
        r = subprocess.run([PY, str(SCRIPT), "--report", "--json", "--temp-root", str(self.tmp / "temp"), *args],
                           env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def kinds(self, rep: dict) -> set[str]:
        return {f["kind"] for f in rep["projects"]["demo"]["findings"]}

    def plant_canary(self, target):
        """Something of the project's that would run if git ran in its repo: a filter, an fsmonitor, a hook."""
        prog = self.tmp / "canary.sh"
        prog.write_text(f"#!/bin/sh\necho ran >> '{self.canary}'\ncat\n")
        prog.chmod(0o755)
        cfg = target / ".git" / "config"
        cfg.write_text(cfg.read_text() + f"[filter \"x\"]\n\tclean = {prog}\n\tsmudge = {prog}\n[core]\n\tfsmonitor = {prog}\n")
        hook = target / ".git" / "hooks" / "post-index-change"
        hook.write_text(f"#!/bin/sh\n{prog}\n")
        hook.chmod(0o755)


class Scan(Fixture):
    def test_a_clean_repo_holds(self):
        self.git(self.ws, "worktree", "add", "-q", "-b", "w", str(self.ws / ".claude" / "worktrees" / "w"))
        rep = self.scan()
        p = rep["projects"]["demo"]
        self.assertTrue(p["holds"], p["findings"])
        self.assertEqual(p["repos"], 1)
        self.assertEqual(rep["temp"], [])

    def test_hostile_repos_are_reported_and_nothing_of_theirs_runs(self):
        self.plant_canary(self.ws)
        fifo = self.ws / ".git" / "fifo"
        os.mkfifo(fifo)
        with open(self.ws / ".git" / "config", "a") as f:
            f.write("[include]\n\tpath = fifo\n")
        logs = self.ws / ".git" / "logs" / "HEAD"
        logs.unlink()
        logs.symlink_to(self.tmp / "home" / "some-file")
        (self.ws / ".git" / "info").mkdir(exist_ok=True)
        (self.ws / ".git" / "info" / "attributes").write_text("*.dat filter=x\n")
        (self.ws / ".git" / "objects" / "info" / "alternates").write_text("/elsewhere/objects\n")
        os.link(self.ws / ".git" / "HEAD", self.ws / "head-twin")
        (self.ws / ".git" / "commondir").write_text("/elsewhere\n")
        self.git(self.tmp, "init", "-q", str(self.ws / "vendor" / "nested"))
        (self.ws / "pointer").mkdir()
        (self.ws / "pointer" / ".git").write_text("gitdir: /elsewhere/gitdir\n")
        self.git(self.tmp, "init", "-q", "--bare", str(self.ws / "stash.git"))
        self.git(self.tmp, "init", "-q", "--bare", str(self.tmp / "temp" / "deep" / "bare.git"))
        rep = self.scan()
        p = rep["projects"]["demo"]
        self.assertFalse(p["holds"])
        self.assertTrue({"include", "key", "hook", "link", "attributes", "alternates", "hardlink", "commondir",
                         "nested", "gitfile", "shaped"} <= self.kinds(rep), self.kinds(rep))
        keys = {f.get("key") for f in p["findings"] if f["kind"] == "key"}
        self.assertTrue({"filter.x.clean", "core.fsmonitor"} <= keys, keys)
        self.assertEqual([f["where"] for f in rep["temp"]], [str(self.tmp / "temp" / "deep" / "bare.git")])
        self.assertFalse(self.canary.exists(), "nothing of the project's ran")
        for f in p["findings"]:
            self.assertNotIn(str(self.tmp), f["where"], "findings name the project's folders, not the machine's paths")

    def test_the_planted_canary_is_live(self):
        """The positive control: the same planted filter, fsmonitor and hook run as soon as git runs in the repo, and
        the scan ran none of them."""
        self.plant_canary(self.ws)
        self.scan()
        self.assertFalse(self.canary.exists())
        (self.ws / "a.txt").write_text("a\n")
        subprocess.run(["git", "-C", str(self.ws), "status", "--porcelain"], env=self.env, capture_output=True, timeout=60)
        self.assertTrue(self.canary.exists(), "git in the repo runs what was planted")

    def test_a_crafted_index_is_reported(self):
        """Index entries the checkout would write into the git dir: counted, the paths not shown."""
        (self.ws / ".git" / "index").write_bytes(index_bytes([b"a.txt", b".git/config", b"x/.GIT/hooks/y"]))
        rep = self.scan()
        f = [x for x in rep["projects"]["demo"]["findings"] if x["kind"] == "index"]
        self.assertEqual([(x["path"], x["n"]) for x in f], [("index", 2)])
        (self.ws / ".git" / "index").write_bytes(b"DIRC" + struct.pack(">II", 2, 5))
        self.assertIn("unreadable", self.kinds(self.scan()))

    def test_a_fifo_where_a_file_belongs_is_reported_not_waited_on(self):
        hook = self.ws / ".git" / "hooks" / "pre-commit"
        os.mkfifo(hook)
        cfg = self.ws / ".git" / "config.worktree"
        os.mkfifo(cfg)
        self.assertTrue({"hook", "unreadable"} <= self.kinds(self.scan()))

    def test_the_commit_gate_and_the_platforms_own_hook(self):
        self.declare(commit_gate=True)
        # the text the platform writes, as the scan's own process computes it (the same instance)
        r = subprocess.run([PY, "-c", "import json, sys, git_fence; print(json.dumps(git_fence.platform_hooks("
                            "json.loads(sys.argv[1]))['pre-commit']))", json.dumps(self.doc)],
                           env={**self.env, "PYTHONPATH": str(ROOT / "bin")}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        wanted = json.loads(r.stdout)
        hook = self.ws / ".git" / "hooks" / "pre-commit"
        hook.write_text(wanted)
        hook.chmod(0o755)
        p = self.scan()["projects"]["demo"]
        self.assertTrue(p["holds"], p["findings"])
        self.assertTrue(p["commit_gate"]["on"])
        self.assertEqual(p["commit_gate"]["hook"], "current")
        hook.write_text(wanted + "evil\n")
        p = self.scan()["projects"]["demo"]
        self.assertEqual(p["commit_gate"]["hook"], "other")
        self.assertIn("hook", {f["kind"] for f in p["findings"]})

    def test_a_rebase_with_exec_lines_is_said_not_counted(self):
        (self.ws / ".git" / "rebase-merge").mkdir()
        (self.ws / ".git" / "rebase-merge" / "git-rebase-todo").write_text("pick abc x\nexec make\n")
        p = self.scan()["projects"]["demo"]
        self.assertTrue(p["holds"])
        self.assertEqual([n["kind"] for n in p["notes"]], ["rebase-exec"])

    def test_the_job_keeps_the_result_and_the_rows_say_it(self):
        (self.ws / ".git" / "hooks" / "post-checkout").write_text("#!/bin/sh\n")
        r = subprocess.run([PY, str(SCRIPT), "--apply", "--temp-root", str(self.tmp / "temp")], env=self.env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("✗ demo: not holding: workspace/.git: a hook the platform didn't write (post-checkout)", r.stdout)
        kept = json.loads((self.tmp / "state" / "git-fence" / "scan.json").read_text())
        self.assertFalse(kept["projects"]["demo"]["holds"])
        sent = json.loads((self.tmp / "state" / "alerts-sent.json").read_text()) \
            if (self.tmp / "state" / "alerts-sent.json").exists() else {}
        self.assertTrue(not sent or "git-fence" in sent, sent)
        self.assertFalse((self.ws / ".git" / "hooks" / "post-checkout").read_text() != "#!/bin/sh\n", "untouched")

    def test_rows(self):
        f = git_fence.finding
        rep = {"projects": {"a": {"findings": [], "notes": [], "commit_gate": {"on": False}},
                            "b": {"findings": [f("hook", "workspace/.git", name="x"),
                                               f("link", "workspace/.git", path="logs/HEAD")],
                                  "notes": [], "commit_gate": {"on": True, "hook": "current", "sandbox": ""}}},
               "temp": [f("shaped", "/t/x.git")], "temp_shaped": 3}
        got = git_fence.rows(rep)
        self.assertEqual([(n, lvl) for n, lvl, _l, _d in got], [("a", "ok"), ("b", "warn"), ("", "warn")])
        self.assertIn(git_fence._(" (+{n} more)", n=1), got[1][2])   # in whatever language the run has
        self.assertEqual(got[2][2], git_fence._("temp area: {n} {n:folder|folders} shaped like a git dir, e.g. {first}",
                                                n=3, first="/t/x.git"))
        self.assertEqual(len(got[1][3]), 2, "every finding listed")
        lines, ids = git_fence.problems(rep)
        self.assertEqual(ids, ["b:hook:workspace/.git:x", "b:link:workspace/.git:logs/HEAD", "temp:shaped"])


if __name__ == "__main__":
    unittest.main()
