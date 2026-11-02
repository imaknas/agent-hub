"""E1 golden (docs/design/i18n.md) on zh-Hant for bin/migrate-to-mini.py: what it prints (stdout and stderr) for the
preflight refusals, the dry-run listing and the --apply steps, success and failure lines.

The real script runs (as a subprocess, on I18N_GOLDEN_PYTHON or this Python: it is meant for the laptop's system
python3) against a temp hub (AGENT_HUB_ROOT, with hub.json, projects/ and legacy-paths.tsv in it) under a temp HOME.
The laptop and the hub machine share that HOME: ssh is a stand-in that runs the remote command locally with a PATH of
stand-ins (gh, vercel, claude, uv, npm, pnpm, go, dbtool) instead of the real one; rsync, lsof and du are stand-ins too
(du gives fixed sizes: real block sizes differ between file systems); with-lock.sh in the temp hub is a stand-in, so
the memory sync does nothing. The hub machine's ~/agent-hub is a temp clone whose recover-project.sh clones the
project from a local bare repo. Nothing touches GitHub, real hosts, the real instance, ~/workspace or ~/.claude.

    bin/py -m unittest tests.test_i18n_golden_migrate
    I18N_GOLDEN_UPDATE=1 bin/py -m unittest tests.test_i18n_golden_migrate     # rewrite (review the diff)
    I18N_GOLDEN_PYTHON=<the system python3> bin/py -m unittest tests.test_i18n_golden_migrate   # as on the laptop
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_lang  # noqa: E402

GOLDEN = golden_lang.GOLDEN
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
SCRIPT = ROOT / "bin" / "migrate-to-mini.py"

FAKEBIN = {  # the laptop
    "ssh": r"""#!/bin/bash
while [ "$1" = -o ]; do shift 2; done
shift
script=${1#export PATH=*; }
PATH="$REMOTE_BIN:/usr/bin:/bin" exec bash -c "$script"
""",
    "rsync": r"""#!/bin/bash
for a in "$@"; do [ "$a" = --files-from=- ] && cat >/dev/null; done
for a in "$@"; do
  case "$a" in *"$RSYNC_FAIL"*) [ -n "$RSYNC_FAIL" ] && { echo "rsync (stand-in): connection closed" >&2; exit 12; };; esac
done
exit 0
""",
    "lsof": r"""#!/bin/bash
[ -n "${LSOF_BUSY:-}" ] || exit 1
echo "COMMAND  PID USER FD TYPE DEVICE SIZE/OFF NODE NAME"
echo "node    4242 me   cwd DIR 1,4 64 2 /x"
echo "zsh      777 me   cwd DIR 1,4 64 2 /x"
""",
    "du": r"""#!/bin/bash
case "$2" in *big.db) echo "3072	$2";; *data*) echo "20480	$2";; *) echo "8	$2";; esac
""",
}

REMOTE_BIN = {  # the hub machine
    "gh": "#!/bin/bash\n[ -n \"$REMOTE_GH_OK\" ] && exit 0\necho 'not logged in' >&2\nexit 1\n",
    "vercel": "#!/bin/bash\nexit 1\n",
    "go": "#!/bin/bash\nexit 0\n",
    "dbtool": "#!/bin/bash\nexit 0\n",
    "uv": "#!/bin/bash\necho \"uv (stand-in): $*\"\n",
    "npm": "#!/bin/bash\necho \"npm (stand-in): ci failed: lockfile out of date\" >&2\nexit 1\n",
    "pnpm": "#!/bin/bash\necho '# touched' >> pnpm-lock.yaml\n",
    "claude": r"""#!/bin/bash
case "$3" in
  web) echo "Invalid configuration: url is not reachable" >&2; exit 1;;
  own) echo "MCP server own already exists in local config" >&2; exit 1;;
esac
exit 0
""",
}

HUB_AGENT_HUB = {  # the hub machine's ~/agent-hub/bin
    "recover-project.sh": r"""#!/bin/bash
[ -n "$RECOVER_FAIL" ] && { echo "recover-project (stand-in): no memory repo for $1" >&2; exit 1; }
git clone -q "$FAKE_REMOTES/o/$1.git" "$2" || exit 1
echo ".agent-memory/" >> "$2/.git/info/exclude"
echo "recover-project (stand-in): $1"
""",
    "sync-local-setup.sh": r"""#!/bin/bash
echo "sync-local-setup (stand-in): $(basename "$PWD")"
echo "⚠️ README.md mentions the platform's hooks: let that project's session handle it"
""",
}

WITH_LOCK = r"""#!/bin/bash
if [ "$(basename "$1")" = "$WITHLOCK_FAIL" ]; then echo "push (stand-in): rejected"; echo "non-fast-forward" >&2; exit 1; fi
exit 0
"""


def tool(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


class Golden(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="w4c_")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        t = self.tmp
        self.hub, self.home, self.remotes = t / "hub", t / "home", t / "remotes"
        self.dev = self.home / "dev"
        for d in (self.hub / "projects", self.dev, self.home / "workspace", self.remotes / "o"):
            d.mkdir(parents=True)
        for name in ("git-sync-memory.sh", "git-safe-push.sh"):
            tool(self.hub / "bin" / name, "#!/bin/bash\nexit 0\n")
        tool(self.hub / "bin" / "with-lock.sh", WITH_LOCK)
        for name, text in FAKEBIN.items():
            tool(t / "fakebin" / name, text)
        for name, text in REMOTE_BIN.items():
            tool(t / "remotebin" / name, text)
        (self.home / ".gitconfig").write_text(
            "[user]\n\tname = t\n\temail = t@t\n[init]\n\tdefaultBranch = main\n[advice]\n\tdetachedHead = false\n")
        (self.hub / "hub.json").write_text(json.dumps({
            "v": 1, "registry_dir": "projects", "workspace_root": "~/workspace", "git_origin": "~/git-origin",
            "memory_copies": "~/.memory-copies", "services_root": "~/services", "hosts": {"server": "hubbox"},
            "github": {"owner": "o"}, "language": {"ui": golden_lang.LANG, "content": golden_lang.LANG}}))
        (self.hub / "legacy-paths.tsv").write_text("name\tpath\tnote\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BRUNNA_", "AGENT_HUB_", "GIT_"))}
        self.env = {**env, "HOME": str(self.home), "AGENT_HUB_ROOT": str(self.hub), "AGENT_HUB_LANG_UI": golden_lang.LANG,
                    "PATH": f"{t / 'fakebin'}:{os.environ['PATH']}", "REMOTE_BIN": str(t / "remotebin"),
                    "FAKE_REMOTES": str(self.remotes), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CEILING_DIRECTORIES": str(t),
                    "PYTHONIOENCODING": "utf-8", "COLUMNS": "200"}
        # the hub machine's agent-hub checkout: `git pull -q` has an upstream to pull from
        self.git(t, "init", "-q", "--bare", str(self.remotes / "agent-hub.git"))
        self.git(t, "clone", "-q", str(self.remotes / "agent-hub.git"), str(self.home / "agent-hub"))
        for name, text in HUB_AGENT_HUB.items():
            tool(self.home / "agent-hub" / "bin" / name, text)
        self.git(self.home / "agent-hub", "add", "bin")
        self.git(self.home / "agent-hub", "commit", "-q", "-m", "stand-ins")
        self.git(self.home / "agent-hub", "push", "-q", "-u", "origin", "main")
        self.out: list[str] = []

    def git(self, cwd, *args) -> str:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True,
                              env=self.env).stdout.strip()

    def call(self, label: str, args: list, env: dict | None = None, fix=None) -> None:
        python = os.environ.get("I18N_GOLDEN_PYTHON") or sys.executable
        r = subprocess.run([python, str(SCRIPT), *map(str, args)], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, env={**self.env, **(env or {})}, timeout=120, cwd=self.home)
        out, err = r.stdout, r.stderr
        if fix:
            out = fix(out)
        self.out.append(f"=== {label}\nexit {r.returncode}\n--- stdout\n{out}--- stderr\n{err}")

    def register(self, name: str, origin: str = "git@github.com:o/{name}.git", legacy: bool = True) -> pathlib.Path:
        (self.hub / "projects" / f"{name}.yaml").write_text(
            f"v: 1\nname: {name}\npaths:\n  workspace: ~/workspace/{name}\n"
            f"  memory_bare: ~/git-origin/{name}-memory.git\n  origin: {origin.format(name=name)}\n")
        if legacy:
            with (self.hub / "legacy-paths.tsv").open("a") as f:
                f.write(f"{name}\t{self.dev / name}\tlaptop\n")
        return self.dev / name

    def project(self, name: str, files: dict, ignored: dict = (), exclude: str = "") -> pathlib.Path:
        """A pushed repo at ~/dev/<name> (its origin a local bare repo), with tracked and ignored files."""
        path = self.register(name)
        path.mkdir()
        for rel, text in {"a.txt": "a\n", **files}.items():
            (path / rel).parent.mkdir(parents=True, exist_ok=True)
            (path / rel).write_text(text)
        self.git(path, "init", "-q")
        self.git(path, "add", "-A")
        self.git(path, "commit", "-q", "-m", "first")
        bare = self.remotes / "o" / f"{name}.git"
        self.git(self.tmp, "init", "-q", "--bare", str(bare))
        self.git(path, "remote", "add", "origin", str(bare))
        self.git(path, "push", "-q", "-u", "origin", "main")
        for rel, text in dict(ignored).items():
            (path / rel).parent.mkdir(parents=True, exist_ok=True)
            (path / rel).write_text(text)
        (path / ".agent-memory").mkdir()
        with (path / ".git" / "info" / "exclude").open("a") as f:
            f.write(".agent-memory/\n" + exclude)
        return path

    def check(self, name: str) -> None:
        slug = str(self.tmp).replace("/", "-").replace(".", "-")
        text = "\n".join(self.out).replace(str(self.tmp), "<TMP>").replace(slug, "<TMP-SLUG>")
        f = GOLDEN / name
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())

    def test_migrate(self):
        ws, home = self.home / "workspace", self.home
        # argparse's heading for the options is "optional arguments:" up to 3.9 (the laptop's python3), "options:" after
        self.call("--help (description and --skip)", ["--help"],
                  fix=lambda out: out.replace("\noptional arguments:\n", "\noptions:\n"))

        # preflight refusals, in the order the script checks them
        self.call("not in the registry", ["nosuch"])
        self.register("nolegacy", legacy=False)
        self.call("no laptop path in legacy-paths.tsv", ["nolegacy"])
        self.register("nat", origin="(native)")
        self.call("native project", ["nat"])
        self.register("gone")
        self.call("folder missing", ["gone"])
        self.register("plain").mkdir()
        self.call("folder is not a git repo (a command fails)", ["plain"])
        (self.project("dirty", {}) / "b.txt").write_text("b\n")
        self.call("uncommitted changes", ["dirty"])
        br = self.project("br", {})
        for b in ("agent-memory", "feature"):
            self.git(br, "branch", b)
        (br / "a.txt").write_text("a2\n")
        self.git(br, "commit", "-q", "-am", "ahead")
        self.call("unpushed branches", ["br"])
        (br / "a.txt").write_text("a3\n")
        self.git(br, "stash", "-q")
        self.call("unpushed branches allowed, a stash: dry-run", ["br", "--allow-local-branches"])

        alpha = self.project("alpha", {
            ".gitignore": ".env\ndata/\nnode_modules/\n.venv/\nvenv/\n*.pyc\nbig.db\n.vercel/\nbuild/\n",
            "Cargo.toml": "[package]\n", "go.mod": "module x\n", "uv.lock": "", "package-lock.json": "{}\n",
            "svc/requirements.txt": "x\n", "lib/pyproject.toml": "[project]\n", "tools/README": "t\n",
            "web/package.json": "{}\n", "app/pnpm-lock.yaml": "lockfileVersion: 9\n"}, ignored={
            ".env": "K=v\n", "data/x.json": "{}\n", "big.db": "db\n", ".vercel/project.json": "{}\n",
            "build/out.bin": "b\n", "x.pyc": "", "node_modules/m/i.js": "", "web/node_modules/m/i.js": "",
            "app/node_modules/m/i.js": "", ".venv/pyvenv.cfg": "home = /usr\nversion_info = 3.12.4\n",
            "lib/.venv/f": "", "svc/venv/pyvenv.cfg": "version = 3.11.9\n", "tools/venv/f": ""},
            exclude="hook.cache.json\n")
        self.call("folder busy", ["alpha"], {"LSOF_BUSY": "1"})
        (ws / "alpha").mkdir()
        self.call("target exists on the hub machine", ["alpha"])
        (ws / "alpha").rmdir()
        self.call("dry-run listing", ["alpha", "--skip", "big.db"])

        # --apply on a small project: each step that can stop the run, then a clean run
        beta = self.project("beta", {".gitignore": ".env\n"}, ignored={".env": "K=v\n"})
        mem = home / ".claude-personal" / "projects" / str(beta).replace("/", "-").replace(".", "-") / "memory"
        mem.mkdir(parents=True)
        (mem / "MEMORY.md").write_text("- note\n")
        ok = {"REMOTE_GH_OK": "1"}
        self.call("apply: memory push fails", ["beta", "--apply"], {**ok, "WITHLOCK_FAIL": "git-safe-push.sh"})
        self.call("apply: recover-project.sh fails on the hub machine", ["beta", "--apply"], {**ok, "RECOVER_FAIL": "1"})
        self.call("apply: rsync fails", ["beta", "--apply"], {**ok, "RSYNC_FAIL": str(beta)})
        shutil.rmtree(ws / "beta")
        self.call("apply: auto memory rsync fails", ["beta", "--apply"], {**ok, "RSYNC_FAIL": "/memory/"})
        shutil.rmtree(ws / "beta")
        self.call("apply: clean", ["beta", "--apply"], ok)

        # --apply with problems: MCP servers, info/exclude, warnings from sync-local-setup, rebuilds that fail
        (home / ".claude-personal" / ".claude.json").write_text(json.dumps({"projects": {str(alpha): {"mcpServers": {
            "db": {"command": f"{home}/bin/dbtool", "args": ["--ro"]},
            "own": {"command": f"{alpha}/target/release/own"},
            "web": {"type": "http", "url": "https://example.invalid/mcp"}}}}}))
        self.call("apply with problems", ["alpha", "--apply", "--skip", "big.db"])
        self.check("migrate-to-mini.txt")


if __name__ == "__main__":
    unittest.main()
