"""The git config fence's scan, report only (bin/git_fence.py; docs/design/git-config-fence.md, r2's order of work,
step 1): hostile repos are reported, a clean one holds, a folder the scan can't list is said, and nothing of a project's
runs or changes while it is scanned.

The scan runs as a separate process in a throwaway instance (its own config, state and registry index), as the
platform's job would.
"""
import hashlib
import http.server
import json
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "git_fence.py"
sys.path.insert(0, str(ROOT / "bin"))
import git_fence  # noqa: E402
PY = os.environ.get("AGENT_HUB_PYTHON") or os.environ.get("BRUNNA_PYTHON") or sys.executable
AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0   # permissions don't stop root: the unlistable cases can't be made

REPORTED = [b".git/config", b".GIT/hooks/x", b"sub/.git/config", b".gi\xe2\x80\x8ct/config", b"git~1/config",
            b".git./config", b".git /config"]
NOT_REPORTED = [b"a.txt", b".github/workflows/x.yml", b"src/.gitignore", b"gits/x", b".gitattributes"]


def entry(name: bytes, length: int | None = None, extended: bool = False) -> bytes:
    """One index entry (version 2 or 3 layout), its name length in the flags as given."""
    flags = (min(len(name), 0xFFF) if length is None else length) | (0x4000 if extended else 0)
    head = struct.pack(">IIIIIIIIII", 0, 0, 0, 0, 0, 0, 0o100644, 0, 0, 0) + b"\0" * 20 + struct.pack(">H", flags)
    head += b"\0\0" if extended else b""
    n = (len(head) + len(name) + 8) & ~7
    return (head + name).ljust(n, b"\0")


def index_bytes(names: list[bytes], version: int = 2, raw: list[bytes] | None = None, checksum: bool = True) -> bytes:
    """An index of regular-file entries in version 2 or 4 (raw: entries already laid out), with its checksum."""
    parts, prev = [], b""
    for p in names:
        if version == 4:
            head = struct.pack(">IIIIIIIIII", 0, 0, 0, 0, 0, 0, 0o100644, 0, 0, 0) + b"\0" * 20 + \
                struct.pack(">H", min(len(p), 0xFFF))
            common = len(os.path.commonprefix([prev, p]))
            parts.append(head + bytes([len(prev) - common]) + p[common:] + b"\0")
        else:
            parts.append(entry(p))
        prev = p
    parts += raw or []
    body = b"DIRC" + struct.pack(">II", version, len(names) + len(raw or [])) + b"".join(parts)
    return body + (hashlib.sha1(body).digest() if checksum else b"\1" * 20)


class Pure(unittest.TestCase):
    def test_read_index_matches_what_git_writes(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp), "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_CONFIG_NOSYSTEM": "1"}
        subprocess.run(["git", "init", "-q", str(tmp / "r")], check=True, env=env)
        for f in ("a.txt", "dir/b.txt", "dir/sub/c.txt", "dir/sub/cd.txt"):
            (tmp / "r" / f).parent.mkdir(parents=True, exist_ok=True)
            (tmp / "r" / f).write_text("x")
        subprocess.run(["git", "-C", str(tmp / "r"), "add", "-A"], check=True, env=env)
        subprocess.run(["git", "-C", str(tmp / "r"), "update-index", "--add", "--cacheinfo",
                        "100644,e69de29bb2d1d6434b8b29ae775ad8c2e48c5391," + "x" * 5000], check=True, env=env)
        want = sorted([b"a.txt", b"dir/b.txt", b"dir/sub/c.txt", b"dir/sub/cd.txt", b"x" * 5000])
        for version in ("2", "3", "4"):
            subprocess.run(["git", "-C", str(tmp / "r"), "update-index", "--index-version", version], check=True, env=env)
            self.assertEqual(git_fence.read_index((tmp / "r" / ".git" / "index").read_bytes()), (want, 0), version)

    def test_index_entries_the_scan_reports(self):
        for p in REPORTED:
            self.assertTrue(git_fence.into_git_dir(p), p)
        for p in NOT_REPORTED:
            self.assertFalse(git_fence.into_git_dir(p), p)
        self.assertEqual(git_fence.read_index(index_bytes([b"a", *REPORTED[:1]], 4)), ([b"a", *REPORTED[:1]], 0))

    def test_name_length_as_git_reads_it(self):
        self.assertEqual(git_fence.read_index(index_bytes([], raw=[entry(b"ab\0cd", length=5)])), ([b"ab\0cd"], 1))
        self.assertEqual(git_fence.read_index(index_bytes([], raw=[entry(b"abcdefgh", length=3)]))[1], 1)
        self.assertEqual(git_fence.read_index(index_bytes([], raw=[entry(b"name", extended=True)])), ([b"name"], 0))
        with self.assertRaises(ValueError):
            git_fence.read_index(index_bytes([b"a"], checksum=False))
        with self.assertRaises(ValueError):
            git_fence.read_index(b"DIRC" + struct.pack(">II", 2, 3) + b"\0" * 20)
        broken = index_bytes([b"a"])[:-20] + b"EXT1" + struct.pack(">I", 99)
        with self.assertRaises(ValueError):
            git_fence.read_index(broken + hashlib.sha1(broken).digest())

    def test_the_allowlist(self):
        remotes, own = {"origin"}, ("/srv/git/demo-memory.git",)
        ok = [("core.repositoryformatversion", "0"), ("core.bare", "false"), ("remote.origin.url", "git@github.com:a/b.git"),
              ("remote.origin.url", "https://github.com/a/b"), ("remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"),
              ("remote.origin.fetch", "+refs/pull/*/head:refs/remotes/origin/pr/*"), ("branch.main.remote", "origin"),
              ("branch.main.merge", "refs/heads/main"), ("user.name", "A B"), ("extensions.refstorage", "files"),
              ("maintenance.gc.enabled", "true"), ("extensions.worktreeConfig", "true"), ("core.autocrlf", "input"),
              ("remote.origin.gh-resolved", "base"), ("branch.main.vscode-merge-base", "origin/main"),
              ("branch.main.description", "a line"), ("lfs.repositoryformatversion", "0")]
        for k, v in ok:
            self.assertIsNone(git_fence.check_key(k, v, remotes, own), (k, v))
        bad = [("filter.x.clean", "evil", "key"), ("core.fsmonitor", "/x", "key"), ("core.bare", "true", "value"),
               ("remote.origin.url", "ext::sh -c x", "value"), ("remote.origin.url", "/elsewhere/repo", "value"),
               ("remote.origin.url", "/srv/git/other-memory.git", "value"),
               ("remote.origin.url", "git@-oProxyCommand=x:y", "value"), ("branch.main.remote", "evil", "value"),
               ("remote.origin.uploadpack", "x", "key"), ("alias.st", "!x", "key"), ("core.worktree", "/x", "key"),
               ("lfs.url", "https://e.example/x", "key")]
        for k, v, why in bad:
            self.assertEqual(git_fence.check_key(k, v, remotes, own), why, (k, v))

    def test_values_are_made_safe_to_show(self):
        self.assertEqual(git_fence.clean("http://user:tok@example.com/x"), "http://***@​example.com/x")
        self.assertEqual(git_fence.clean("a\x1b]0;x\x07b\nc"), "a\\x1b]0;x\\x07b\\x0ac")
        self.assertEqual(git_fence.clean("@everyone"), "@​everyone")

    def test_shapes_git_takes_for_a_git_dir(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for name, made in (("dirs", ("objects/", "refs/")), ("files", ("objects", "refs")),
                           ("links", ("objects>", "refs>")), ("common", ("commondir",))):
            d = tmp / name
            d.mkdir()
            (d / "HEAD").write_text("ref: refs/heads/main\n")
            for m in made:
                if m.endswith("/"):
                    (d / m).mkdir()
                elif m.endswith(">"):
                    (d / m[:-1]).symlink_to(tmp)
                else:
                    (d / m).write_text("x")
            self.assertTrue(git_fence.git_shaped(d), name)
        (tmp / "plain").mkdir()
        (tmp / "plain" / "HEAD").write_text("x")
        self.assertFalse(git_fence.git_shaped(tmp / "plain"))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for d in ("home", "config/projects", "state/shared/registry", "bus", "secrets", "temp"):
            (self.tmp / d).mkdir(parents=True)
        self.hub = {"bus_dir": str(self.tmp / "bus"), "registry_dir": "projects"}
        (self.tmp / "config" / "hub.json").write_text(json.dumps(self.hub))
        self.canary = self.tmp / "ran"
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("BRUNNA_", "AGENT_HUB_", "GIT_"))},
                    "HOME": str(self.tmp / "home"), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                    "AGENT_HUB_CONFIG": str(self.tmp / "config"), "AGENT_HUB_STATE": str(self.tmp / "state"),
                    "AGENT_HUB_DATA": str(self.tmp / "data"), "AGENT_HUB_SECRETS": str(self.tmp / "secrets"),
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t", "AGENT_HUB_LANG_UI": "en", "GIT_CEILING_DIRECTORIES": str(self.tmp)}
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

    def scan(self, *args, env=None) -> dict:
        r = subprocess.run([PY, str(SCRIPT), "--report", "--json", "--temp-root", str(self.tmp / "temp"), *args],
                           env={**self.env, **(env or {})}, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def apply(self, *args):
        return subprocess.run([PY, str(SCRIPT), "--apply", *args], env=self.env, capture_output=True, text=True,
                              timeout=120)

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

    def unlistable(self, folder: pathlib.Path):
        os.chmod(folder, 0o111)
        self.addCleanup(os.chmod, folder, 0o755)

    def snapshot(self) -> dict:
        out = {}
        for d, dirs, files in os.walk(self.ws):
            for n in dirs + files:
                st = os.lstat(os.path.join(d, n))
                out[os.path.join(d, n)] = (st.st_mode, st.st_size, st.st_mtime_ns, st.st_ino)
        return out


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
        os.mkfifo(self.ws / ".git" / "fifo")
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

    def test_nothing_in_the_project_changes(self):
        self.plant_canary(self.ws)
        (self.ws / ".git" / "info").mkdir(exist_ok=True)
        (self.ws / ".git" / "info" / "attributes").write_text("*.dat filter=x\n")
        before = self.snapshot()
        self.scan()
        self.assertEqual(self.snapshot(), before)

    @unittest.skipIf(AS_ROOT, "permissions don't stop root")
    def test_a_folder_the_scan_cant_list_is_a_finding(self):
        (self.ws / ".git" / "hooks" / "post-index-change").write_text("#!/bin/sh\n")
        self.unlistable(self.ws / ".git" / "hooks")
        self.git(self.tmp, "init", "-q", str(self.ws / "vendor" / "x"))
        self.unlistable(self.ws / "vendor")
        p = self.scan()["projects"]["demo"]
        got = {(f["kind"], f["where"]) for f in p["findings"]}
        self.assertIn(("unreadable", "workspace/.git/hooks"), got)
        self.assertIn(("unreadable", "workspace/vendor"), got)
        self.assertFalse(p["holds"])

    def test_remotes_and_branches_files(self):
        for legacy, text in (("remotes/evil", "URL: /elsewhere/repo\n"), ("branches/b", "/elsewhere/repo\n")):
            f = self.ws / ".git" / legacy
            f.parent.mkdir(exist_ok=True)
            f.write_text(text)
        names = {f.get("name") for f in self.scan()["projects"]["demo"]["findings"] if f["kind"] == "legacy"}
        self.assertEqual(names, {"remotes/evil", "branches/b"})

    def test_pointers_only_into_git_dirs_the_scan_inspected(self):
        gd = self.ws / ".git"
        self.git(self.tmp, "init", "-q", "--bare", str(gd / "modules" / "m"))
        (gd / "modules" / "m" / "commondir").write_text("../../../evil\n")
        (self.ws / "sub").mkdir()
        (self.ws / "sub" / ".git").write_text("gitdir: ../.git/modules/m\n")
        y = gd / "worktrees" / "x" / "y"
        y.mkdir(parents=True)
        (y / "HEAD").write_text("ref: refs/heads/main\n")
        (y / "commondir").write_text("../../../../evil\n")
        (self.ws / "deep").mkdir()
        (self.ws / "deep" / ".git").write_text("gitdir: ../.git/worktrees/x/y\n")
        (gd / "modules" / "a" / "b").mkdir(parents=True)
        (gd / "modules" / "a" / "b" / "config").write_text("[core]\n\tfsmonitor = /x\n")
        p = self.scan()["projects"]["demo"]
        got = {(f["kind"], f["where"]) for f in p["findings"]}
        self.assertIn(("commondir", "workspace/.git/modules/m"), got)
        self.assertIn(("commondir", "workspace/.git/worktrees/x"), got, "a worktree's admin dir needs ../..")
        self.assertIn(("gitfile", "workspace/deep"), got)
        self.assertNotIn(("gitfile", "workspace/sub"), got, "it leads to a module's git dir the scan inspected")
        self.assertIn(("key", "workspace/.git/modules/a/b/config"), got, "a folder under modules/ that isn't shaped")

    def test_shapes_and_the_root_itself(self):
        (self.ws / "vendor" / "pkg").mkdir(parents=True)
        (self.ws / "vendor" / "pkg" / "HEAD").write_text("x")
        (self.ws / "vendor" / "pkg" / "commondir").write_text("../other\n")
        other = self.tmp / "other-root"
        self.git(self.tmp, "init", "-q", "--bare", str(other))
        self.declare(allow_write=[str(other)])
        got = {(f["kind"], f["where"]) for f in self.scan()["projects"]["demo"]["findings"]}
        self.assertIn(("shaped", "workspace/vendor/pkg"), got)
        self.assertIn(("shaped", "allow_write[0]"), got)

    def test_a_dotgit_at_a_temp_root_or_just_below(self):
        t = self.tmp / "temp"
        self.git(self.tmp, "init", "-q", str(t))
        self.git(self.tmp, "init", "-q", str(t / "first"))
        self.git(self.tmp, "init", "-q", str(t / "first" / "second"))
        bare = self.tmp / "temp2"
        self.git(self.tmp, "init", "-q", "--bare", str(bare))
        rep = self.scan("--temp-root", str(bare))
        got = {(f["kind"], f["where"]) for f in rep["temp"]}
        self.assertEqual(got, {("temp-dotgit", str(t)), ("temp-dotgit", str(t / "first")), ("shaped", str(bare))})

    def test_the_index_entries_the_scan_reports(self):
        (self.ws / ".git" / "index").write_bytes(index_bytes([b"a.txt", *REPORTED[:2]]))
        f = [x for x in self.scan()["projects"]["demo"]["findings"] if x["kind"] == "index"]
        self.assertEqual([(x["path"], x["n"]) for x in f], [("index", 2)])
        (self.ws / ".git" / "index").write_bytes(b"DIRC" + struct.pack(">II", 2, 5))
        self.assertIn("unreadable", self.kinds(self.scan()))

    def test_a_fifo_where_a_file_belongs_is_reported_not_waited_on(self):
        os.mkfifo(self.ws / ".git" / "hooks" / "pre-commit")
        os.mkfifo(self.ws / ".git" / "config.worktree")
        self.assertTrue({"hook", "unreadable"} <= self.kinds(self.scan()))

    def test_the_deadline_is_said(self):
        p = self.scan(env={"GIT_FENCE_TIME_CAP": "0"})["projects"]["demo"]
        self.assertEqual([f["kind"] for f in p["findings"]], ["incomplete"])
        self.assertFalse(p["holds"])

    def test_the_commit_gate_and_the_platforms_own_hook(self):
        self.declare(commit_gate=True)
        r = subprocess.run([PY, "-c", "import json, sys, git_fence; print(json.dumps(sorted(git_fence.platform_hooks("
                            "json.loads(sys.argv[1]))['pre-commit'])))", json.dumps(self.doc)],
                           env={**self.env, "PYTHONPATH": str(ROOT / "bin")}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        wanted = json.loads(r.stdout)
        hook = self.ws / ".git" / "hooks" / "pre-commit"
        for text in wanted:   # the platform's text in any UI language it may have been written in
            hook.write_text(text)
            hook.chmod(0o755)
            p = self.scan()["projects"]["demo"]
            self.assertTrue(p["holds"], p["findings"])
            self.assertEqual(p["commit_gate"]["hook"], "current")
        hook.write_text(wanted[0] + "evil\n")
        p = self.scan()["projects"]["demo"]
        self.assertEqual(p["commit_gate"]["hook"], "other")
        self.assertIn("hook", {f["kind"] for f in p["findings"]})

    def test_a_rebase_with_exec_lines_is_said_not_counted(self):
        (self.ws / ".git" / "rebase-merge").mkdir()
        (self.ws / ".git" / "rebase-merge" / "git-rebase-todo").write_text("pick abc x\nexec make\n")
        p = self.scan()["projects"]["demo"]
        self.assertTrue(p["holds"])
        self.assertEqual([n["kind"] for n in p["notes"]], ["rebase-exec"])

    def test_the_job_keeps_the_result_and_alerts(self):
        got = []

        class Channel(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                got.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), Channel)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        (self.tmp / "config" / "hub.json").write_text(json.dumps(
            {**self.hub, "alerts": {"ntfy": {"topic": "t", "base_url": f"http://127.0.0.1:{srv.server_port}"}}}))
        # the temp area was walked an hour ago: the job keeps that result instead of walking it again
        (self.tmp / "state" / "git-fence").mkdir()
        (self.tmp / "state" / "git-fence" / "scan.json").write_text(json.dumps(
            {"projects": {}, "temp": [], "temp_shaped": 0, "temp_at": "2999-01-01T00:00:00"}))
        (self.ws / ".git" / "hooks" / "post-checkout").write_text("#!/bin/sh\n")
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("✗ demo: not holding: workspace/.git: a hook the platform didn't write (post-checkout)", r.stdout)
        kept = json.loads((self.tmp / "state" / "git-fence" / "scan.json").read_text())
        self.assertFalse(kept["projects"]["demo"]["holds"])
        self.assertEqual(kept["temp_at"], "2999-01-01T00:00:00", "the temp area isn't walked again within a day")
        self.assertEqual(len(got), 1, got)
        self.assertIn("post-checkout", got[0])
        self.assertEqual((self.ws / ".git" / "hooks" / "post-checkout").read_text(), "#!/bin/sh\n", "untouched")
        for narrowing in (["--project", "demo"], ["--no-temp"], ["--temp-root", str(self.tmp)]):
            self.assertEqual(self.apply(*narrowing).returncode, 2, narrowing)

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
        self.assertEqual(len(got[1][3]), 2, "every finding listed")
        lines, ids = git_fence.problems(rep)
        self.assertEqual(ids, ["b:hook:workspace/.git:x", "b:link:workspace/.git:logs/HEAD", "temp:shaped"])


if __name__ == "__main__":
    unittest.main()
