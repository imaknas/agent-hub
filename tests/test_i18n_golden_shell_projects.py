"""E1 golden (docs/design/i18n.md) on zh-Hant for the project/service shell scripts: what bin/onboard-project.sh,
bin/recover-project.sh, bin/recover-service.sh and bin/rename-project.sh print (stdout and stderr) on their main
success paths and the common refusals.

The real scripts run against a temp hub (AGENT_HUB_ROOT: its bin/ links to the real scripts) under a temp HOME whose
~/git-origin exists, so the hub machine's steps run locally under that HOME (hosts.server is set; test_origin_check covers an empty one). sync-local-setup.sh, gen-index-roster.sh,
commit-hub-files.sh and render-services.py are stand-ins in the temp hub; gh, ssh, lsof and launchctl are stand-ins on
PATH; pushes to git@github.com go to a local directory. Nothing touches GitHub, the real instance or ~/workspace.
Lines git itself prints (push progress, tracking notes, worktree repair) are dropped: they differ between git versions. LC_ALL=C: bash
3.2 in a UTF-8 locale takes the first byte of a CJK character right after "$name" into the name and garbles the line.

    bin/py -m unittest tests.test_i18n_golden_shell_projects
    I18N_GOLDEN_UPDATE=1 bin/py -m unittest tests.test_i18n_golden_shell_projects     # rewrite (review the diff)
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "i18n-golden"
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"

STUBS = {
    "sync-local-setup.sh": r"""#!/bin/bash
[ "${1:-}" = --quiet ] && shift
cd "${1:-.}" || exit 1
[ -n "${SYNC_NO_EXCLUDE:-}" ] || echo ".agent-memory/" >> "$(git rev-parse --git-common-dir)/info/exclude"
echo "sync-local-setup (stand-in): $(basename "$PWD")"
""",
    "gen-index-roster.sh": "#!/bin/bash\nexit 0\n",
    "commit-hub-files.sh": r"""#!/bin/bash
[ -n "${COMMIT_FAIL:-}" ] && { echo "commit-hub-files (stand-in): push failed" >&2; exit 1; }
echo "commit-hub-files (stand-in): $1"
""",
    "render-services.py": "import sys\nprint('render-services (stand-in):', *sys.argv[1:])\n",
}

FAKEBIN = {
    "gh": r"""#!/bin/bash
case "$1 $2" in
  "repo create")
    echo "gh (stand-in): repo create $3 $4"
    dest="$FAKE_REMOTES/$3.git"
    mkdir -p "$(dirname "$dest")" && git init -q --bare "$dest" && git remote add origin "$dest";;
  *) echo "gh (stand-in): $*" >&2; exit 2;;
esac
""",
    "ssh": "#!/bin/bash\necho \"ssh (stand-in): no route to host\" >&2\nexit 255\n",
    "lsof": r"""#!/bin/bash
[ -n "${LSOF_BUSY:-}" ] || exit 1
echo "COMMAND  PID USER FD TYPE DEVICE SIZE/OFF NODE NAME"
echo "claude  4242 me   cwd DIR 1,4 64 2 /x"
""",
    "launchctl": "#!/bin/bash\necho \"launchctl (stand-in): $1\"\n",
}

GIT_NOISE = re.compile(r"^(To |\s+\* \[new branch\]|branch '.*' set up to track|hint:|Cloning into|repair: ).*\n?", re.M)


def git(cwd, *args) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True,
                          env=Golden.git_env).stdout.strip()


def tool(path: pathlib.Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


class Golden(unittest.TestCase):
    maxDiff = None
    git_env: dict = {}

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="w3sh2_")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        t = self.tmp
        self.hub, self.home, self.remotes = t / "hub", t / "home", t / "remotes"
        self.ws = self.home / "workspace"
        (self.hub / "bin").mkdir(parents=True)
        (self.hub / "projects").mkdir()
        for f in sorted((ROOT / "bin").iterdir()):
            if f.name not in STUBS:
                (self.hub / "bin" / f.name).symlink_to(f)
        for name, text in STUBS.items():
            tool(self.hub / "bin" / name, text)
        (self.hub / "templates").symlink_to(ROOT / "templates")
        fakebin = t / "fakebin"
        fakebin.mkdir()
        for name, text in FAKEBIN.items():
            tool(fakebin / name, text)
        self.ws.mkdir(parents=True)
        (self.home / "git-origin").mkdir()
        self.remotes.mkdir()
        (self.home / ".gitconfig").write_text(
            "[user]\n\tname = t\n\temail = t@t\n[init]\n\tdefaultBranch = main\n[advice]\n\tdetachedHead = false\n"
            f'[url "{self.remotes}/"]\n\tpushInsteadOf = git@github.com:\n')
        self.hub_json()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_HUB_", "GIT_"))}
        self.env = {**env, "HOME": str(self.home), "AGENT_HUB_ROOT": str(self.hub), "AGENT_HUB_PYTHON": sys.executable,
                    "AGENT_HUB_LANG_UI": "zh-Hant", "PATH": f"{fakebin}:{os.environ['PATH']}", "LC_ALL": "C",
                    "GIT_CONFIG_NOSYSTEM": "1", "FAKE_REMOTES": str(self.remotes)}
        Golden.git_env = self.env
        self.out: list[str] = []

    def hub_json(self, **over) -> None:
        conf = {"v": 1, "registry_dir": "projects", "workspace_root": "~/workspace", "git_origin": "~/git-origin",
                "memory_copies": "~/.memory-copies", "services_root": "~/services", "hosts": {"server": "hubbox"},
                "github": {"owner": "o"}, "language": {"ui": "zh-Hant", "content": "zh-Hant"}}
        conf.update(over)
        (self.hub / "hub.json").write_text(json.dumps({k: v for k, v in conf.items() if v is not None}))

    def call(self, label: str, script: str, args: list, env: dict | None = None, cwd=None) -> None:
        r = subprocess.run(["bash", str(ROOT / "bin" / script), *map(str, args)], capture_output=True, text=True,
                           env={**self.env, **(env or {})}, timeout=120, cwd=cwd or self.home)
        self.out.append(f"=== {label}\nexit {r.returncode}\n--- stdout\n{GIT_NOISE.sub('', r.stdout)}"
                        f"--- stderr\n{GIT_NOISE.sub('', r.stderr)}")

    def check(self, name: str) -> None:
        text = "\n".join(self.out).replace(str(self.tmp), "<TMP>")
        f = GOLDEN / name
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())

    def repo(self, path: pathlib.Path, origin: str | None = None) -> pathlib.Path:
        path.mkdir(parents=True)
        (path / "a.txt").write_text("a\n")
        git(path, "init", "-q")
        git(path, "add", "a.txt")
        git(path, "commit", "-q", "-m", "first")
        if origin:
            git(path, "remote", "add", "origin", origin)
        return path

    def register(self, name: str, workspace, origin: str, memory_bare: str) -> None:
        (self.hub / "projects" / f"{name}.yaml").write_text(
            f"v: 1\nname: {name}\npaths:\n  workspace: {workspace}\n  memory_bare: {memory_bare}\n  origin: {origin}\n"
            "memory: true\n")

    def onboarded(self, name: str, *flags: str) -> pathlib.Path:
        """A project onboarded by the real script (its output is not part of the golden)."""
        r = subprocess.run(["bash", str(ROOT / "bin" / "onboard-project.sh"), *flags, str(self.ws / name)],
                           capture_output=True, text=True, env=self.env, timeout=120, cwd=self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return self.ws / name

    # onboard-project.sh -------------------------------------------------------------------------------------------

    def test_onboard_project(self):
        s, ws, go = "onboard-project.sh", self.ws, self.home / "git-origin"
        self.call("usage", s, [])
        self.call("unknown flag", s, ["--bogus", ws / "x"])
        self.hub_json(hosts={"server": "hubbox"}, git_origin="~/no-such-origin")
        self.call("hub machine unreachable", s, ["--no-remote", ws / "x"])
        self.hub_json()
        self.call("new folder, --no-remote", s, ["--no-remote", ws / "alpha"])
        self.call("already in the registry", s, ["--no-remote", ws / "alpha"])
        git(self.home, "clone", "-q", "--bare", str(go / "alpha-memory.git"), str(go / "gamma-memory.git"))
        self.call("memory bare repo exists on the hub machine", s, ["--no-remote", ws / "gamma"])
        git(self.home, "init", "-q", "--bare", str(self.remotes / "o" / "proj.git"))
        self.register("proj", "~/workspace/proj", str(self.remotes / "o" / "proj.git"), "~/git-origin/proj-memory.git")
        self.repo(ws / "proj-copy", str(self.remotes / "o" / "proj.git"))
        self.call("origin onboarded under another name", s, [ws / "proj-copy"])
        self.repo(ws / "old", str(self.home / "git-origin" / "old.git"))
        self.call("origin points at the hub machine", s, [ws / "old"])
        (self.repo(ws / "dirty") / "b.txt").write_text("b\n")
        self.call("dirty working tree", s, ["--no-remote", ws / "dirty"])
        git(self.repo(ws / "half"), "branch", "agent-memory")
        self.call("agent-memory branch without a worktree", s, ["--no-remote", ws / "half"])
        (self.repo(ws / "delta") / ".agent-memory").mkdir()
        git(self.home, "clone", "-q", "--bare", str(go / "alpha-memory.git"), str(go / "delta-memory.git"))
        self.call(".agent-memory and the bare repo both exist", s, ["--no-remote", ws / "delta"])
        (self.hub / "projects" / "alpha.yaml").unlink()
        shutil.rmtree(go / "alpha-memory.git")
        shutil.rmtree(self.home / ".memory-copies" / "alpha")
        self.call("resume after an interrupted run", s, ["--no-remote", ws / "alpha"])
        self.hub_json(github=None)
        self.call("no github.owner", s, [ws / "epsilon"])
        self.hub_json()
        self.call("GitHub repo created, commit fails", s, ["--public", ws / "zeta"], {"COMMIT_FAIL": "1"})
        self.out.append("=== mirror-list.txt\n" + (self.hub / "mirror-list.txt").read_text())
        self.call("not ignored", s, ["--no-remote", ws / "iota"], {"SYNC_NO_EXCLUDE": "1"})
        self.call("registry add fails", s, ["--no-remote", ws / "theta"], {"ONBOARD_REGISTRY_EXTRA": '{"type": 5}'})
        self.check("shell-onboard-project.txt")

    def test_origin_check(self):
        """10-31: an empty hosts.server matched every origin, and a GitHub repo whose name contains the server's name was
        taken for the hub machine; only the ssh host (or a memory bare repo path) counts"""
        for label, server, origin, want in (
                ("one machine, GitHub origin", "", "git@github.com:o/one.git", False),
                ("server name inside a repo name", "hubbox", "git@github.com:o/hubboxed.git", False),
                ("the hub machine as ssh host", "hubbox", "hubbox:git-origin/three.git", True),
                ("ssh:// to the hub machine", "hubbox", "ssh://t@hubbox/~/x.git", True)):
            with self.subTest(label):
                self.hub_json(hosts={"server": server})
                name = origin.rsplit("/", 1)[1].removesuffix(".git")
                git(self.home, "init", "-q", "--bare", str(self.remotes / "o" / f"{name}.git"))
                self.repo(self.ws / name, origin)
                r = subprocess.run(["bash", str(ROOT / "bin" / "onboard-project.sh"), str(self.ws / name)],
                                   capture_output=True, text=True, env={**self.env, "AGENT_HUB_LANG_UI": "en"},
                                   timeout=120, cwd=self.home)
                hit = "origin points at the hub machine" in r.stdout + r.stderr
                self.assertEqual(hit, want, r.stdout + r.stderr)
                if not want:
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    # recover-project.sh -------------------------------------------------------------------------------------------

    def test_recover_project(self):
        s = "recover-project.sh"
        self.call("usage", s, [])
        self.hub_json(registry_dir="nowhere")
        self.call("no registry", s, ["kappa"])
        self.hub_json()
        alpha = self.onboarded("alpha", "--no-remote")
        self.call("not in the registry", s, ["nosuch"])
        self.call("native project", s, ["alpha"])
        remote = self.remotes / "o" / "kappa.git"
        git(self.home, "clone", "-q", "--bare", str(alpha), str(remote))
        git(self.home, "clone", "-q", "--bare", str(self.home / "git-origin" / "alpha-memory.git"),
            str(self.home / "git-origin" / "kappa-memory.git"))
        self.register("kappa", "~/workspace/kappa", str(remote), "hubbox:~/git-origin/kappa-memory.git")
        (self.ws / "kappa").mkdir()
        self.call("target exists", s, ["kappa"])
        self.call("recovered", s, ["kappa", self.home / "restore" / "kappa"])
        self.check("shell-recover-project.txt")

    # recover-service.sh -------------------------------------------------------------------------------------------

    def test_recover_service(self):
        s = "recover-service.sh"
        self.call("usage", s, [])
        self.call("no services.tsv", s, ["local.x"])
        (self.hub / "launchd").mkdir()
        (self.hub / "launchd" / "local.watch.plist").write_text("<plist>{{hub}}</plist>\n")
        (self.home / "Library" / "LaunchAgents").mkdir(parents=True)
        rows = [("local.web", "web", "agent-hub", "projects/web.yaml", "serves the web app", "restart after deploy"),
                ("local.quiet", "web", "agent-hub", "projects/web.yaml", "a quiet one", ""),
                ("local.watch", "agent-hub", "agent-hub", "agent-hub/launchd/local.watch.plist", "watches", "check logs"),
                ("local.gone", "agent-hub", "agent-hub", "agent-hub/launchd/local.gone.plist", "gone", ""),
                ("homebrew.mxcl.x", "-", "Homebrew", "~/Library/LaunchAgents/x.plist", "a db", "brew services start x")]
        (self.hub / "services.tsv").write_text("".join("\t".join(r) + "\n" for r in rows))
        self.call("unknown label", s, ["local.nope"])
        self.call("registry service", s, ["local.web"])
        self.call("registry service without notes", s, ["local.quiet"])
        self.call("platform plist", s, ["local.watch"])
        self.out.append("=== plist\n" + (self.home / "Library" / "LaunchAgents" / "local.watch.plist").read_text())
        self.call("platform plist missing", s, ["local.gone"])
        self.call("someone else's service", s, ["homebrew.mxcl.x"])
        self.check("shell-recover-service.txt")

    # rename-project.sh --------------------------------------------------------------------------------------------

    def test_rename_project(self):
        s = "rename-project.sh"
        self.call("usage", s, ["alpha"])
        alpha = self.onboarded("alpha", "--no-remote")
        zeta = self.onboarded("zeta")
        (self.hub / "legacy-paths.tsv").write_text("name\tpath\tnote\n")
        self.call("unknown argument", s, ["alpha", "beta", "--bogus"])
        self.call("bad new name", s, ["alpha", "Beta"])
        self.call("not in the registry", s, ["nosuch", "beta"])
        self.call("new name taken", s, ["alpha", "zeta"])
        (self.ws / "mu").mkdir()
        self.register("mu", self.ws / "mu", "(native)", "~/git-origin/mu-memory.git")
        self.call("no .agent-memory", s, ["mu", "beta"])
        (alpha / "README.md").write_text("changed\n")
        self.call("tracked changes", s, ["alpha", "beta"])
        git(alpha, "checkout", "--", "README.md")
        (alpha / ".agent-memory" / "memory" / "note.md").write_text("n\n")
        self.call(".agent-memory not committed", s, ["alpha", "beta"])
        git(alpha / ".agent-memory", "add", "memory/note.md")
        git(alpha / ".agent-memory", "commit", "-q", "-m", "note")
        self.call(".agent-memory not pushed", s, ["alpha", "beta"])
        git(alpha / ".agent-memory", "push", "-q")
        (self.ws / "beta").mkdir()
        self.call("new path exists", s, ["alpha", "beta"])
        (self.ws / "beta").rmdir()
        self.call("new origin unreachable", s, ["alpha", "beta", "--origin", self.remotes / "nope.git"])
        (self.home / ".memory-copies" / "beta").mkdir()
        self.call("taken on the hub machine", s, ["alpha", "beta"])
        (self.home / ".memory-copies" / "beta").rmdir()
        self.call("dry-run", s, ["alpha", "beta"])
        git(self.home, "init", "-q", "--bare", str(self.remotes / "o" / "zeta2.git"))
        self.call("dry-run, new origin, same folder", s,
                  ["zeta", "zeta2", "--origin", self.remotes / "o" / "zeta2.git", "--path", zeta])
        self.call("folder busy", s, ["alpha", "beta", "--apply"], {"LSOF_BUSY": "1"})
        slug = str(alpha).replace("/", "-").replace(".", "-")
        (self.home / ".claude" / "projects" / slug / "memory").mkdir(parents=True)
        (self.home / ".claude" / "projects" / slug / "memory" / "MEMORY.md").write_text("m\n")
        self.call("renamed", s, ["alpha", "beta", "--apply"])
        self.out.append("=== legacy-paths.tsv\n" + (self.hub / "legacy-paths.tsv").read_text())
        self.check("shell-rename-project.txt")


if __name__ == "__main__":
    unittest.main()
