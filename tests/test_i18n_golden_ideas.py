"""E1 golden (docs/design/i18n.md) for the idea-sandbox and rename endpoints on zh-Hant: every text that
bin/create-idea-sandbox.py, bin/upgrade-idea.py and bin/project-rename.py print or return as `detail`.

Each scenario runs the real script in a temp hub (AGENT_HUB_ROOT) under a temp HOME; gh, onboard-project.sh and
rename-project.sh are stand-ins, nothing touches GitHub or the real workspace.

    bin/py -m unittest tests.test_i18n_golden_ideas
    I18N_GOLDEN_UPDATE=1 bin/py -m unittest tests.test_i18n_golden_ideas     # rewrite (review the diff)
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
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_lang  # noqa: E402

GOLDEN = golden_lang.GOLDEN
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
PY = sys.executable

try:
    import yaml
except ImportError:
    yaml = None

FAKE_ONBOARD = r"""#!/bin/bash
ws="${@: -1}"; name=$(basename "$ws")
mkdir -p "$ws/.agent-memory" "$GO/$name-memory.git" "$MC/$name"
printf 'v: 1\nname: %s\ntype: idea\n' "$name" > "$HUB/projects/$name.yaml"
[ -z "$ONBOARD_FAIL" ] && exit 0
if [ -n "$FAKE_COMMIT" ]; then git -C "$HUB" add "projects/$name.yaml" && git -C "$HUB" commit -qm reg; fi
echo "step 1 ok" >&2; echo "step 2 ok" >&2; echo "boom: could not create the bare repo" >&2
exit 1
"""

UP_GH = r"""#!/bin/bash
case "$1 $2" in
  "repo view") [ -d "$FAKE_REMOTES/$3.git" ];;
  "repo create")
    [ -n "${GH_CREATE_FAIL:-}" ] && { echo "HTTP 502: bad gateway" >&2; exit 1; }
    dest="$FAKE_REMOTES/$3.git"; src=""
    for a in "$@"; do case "$a" in --source=*) src="${a#--source=}";; esac; done
    mkdir -p "$(dirname "$dest")" && git init -q --bare -b main "$dest" || exit 1
    git -C "$src" remote add origin "$dest" && git -C "$src" push -q -u origin HEAD || exit 1
    exit 0;;
  *) echo "fake gh: $*" >&2; exit 2;;
esac
"""

UP_RENAME = r"""#!/bin/bash
[ -n "${RENAME_FAIL:-}" ] && { echo "rename-project: folder is busy" >&2; exit 1; }
mv "$HOME/workspace/$1" "$HOME/workspace/$2"
sed -e "s/^name: $1\$/name: $2/" -e "s#workspace/$1\$#workspace/$2#" -e "s#/$1-memory.git#/$2-memory.git#" \
  "$AGENT_HUB_ROOT/projects/$1.yaml" > "$AGENT_HUB_ROOT/projects/$2.yaml"
rm "$AGENT_HUB_ROOT/projects/$1.yaml"
[ -n "${RENAME_BREAK:-}" ] && echo "bogus: 1" >> "$AGENT_HUB_ROOT/projects/$2.yaml"
exit 0
"""

IDEA = """v: 1
name: idea-7
paths:
  workspace: ~/workspace/idea-7
  memory_bare: ~/git-origin/idea-7-memory.git
  origin: (native)
memory: true
type: idea
purpose: an idea to try
enforce:
  sandbox: true
"""

PR_GH = r"""#!/bin/bash
if [ "$1 $2" = "repo view" ]; then
  for r in $GH_EXISTS; do [ "$r" = "$3" ] && exit 0; done
  exit 1
fi
[ "$1 $2" = "repo rename" ] && { [ -n "$GH_FAIL" ] && { echo "gh: HTTP 403" >&2; exit 1; }; exit 0; }
exit 0
"""

PR_RENAME = r"""#!/bin/bash
[ -n "$RENAME_BUSY" ] && { echo "rename-project: busy /x: claude(1)" >&2; exit 75; }
[ -n "$RENAME_FAIL" ] && { echo "rename-project: memory bare repo is missing" >&2; exit 1; }
sed "s/^name: $1$/name: $2/" "$HUB/projects/$1.yaml" > "$HUB/projects/$2.yaml" && rm "$HUB/projects/$1.yaml"
"""


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def base_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "AGENT_HUB_"))}
    env["AGENT_HUB_LANG_UI"] = golden_lang.LANG
    return env


def tool(path: pathlib.Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


@unittest.skipIf(yaml is None, "needs PyYAML (the platform's Python has it)")
class Golden(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out: list[str] = []

    def call(self, label: str, script: str, args: list[str], env: dict, cwd=None) -> None:
        r = subprocess.run([PY, str(ROOT / "bin" / script), *args], capture_output=True, text=True, env=env,
                           timeout=60, cwd=cwd)
        self.out.append(f"=== {label}\nexit {r.returncode}\n--- stdout\n{r.stdout}--- stderr\n{r.stderr}")

    def text(self) -> str:
        text = "\n".join(self.out)
        text = text.replace(str(self.tmp), "<TMP>")
        return re.sub(r"\((\d+)\)", "(<PID>)", text)

    def check(self, name: str) -> None:
        text = self.text()
        f = GOLDEN / name
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())

    # create-idea-sandbox.py -------------------------------------------------------------------------------------

    def sandbox_hub(self, tag: str, limit: int = 20):
        t = self.tmp / tag
        hub = t / "hub"
        (hub / "bin").mkdir(parents=True)
        (hub / "projects").mkdir()
        tool(hub / "bin" / "onboard-project.sh", FAKE_ONBOARD)
        (hub / "hub.json").write_text(json.dumps({
            "v": 1, "registry_dir": "projects", "workspace_root": str(t / "workspace"), "git_origin": str(t / "go"),
            "memory_copies": str(t / "mc"), "services_root": str(t / "services"), "ideas": {"limit": limit}}))
        git(t, "init", "-q", str(hub))
        git(hub, "commit", "-q", "--allow-empty", "-m", "init")
        env = {**base_env(), "AGENT_HUB_ROOT": str(hub), "HUB": str(hub), "GO": str(t / "go"), "MC": str(t / "mc"),
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
               "ONBOARD_FAIL": "", "FAKE_COMMIT": ""}
        return t, hub, env

    def test_create_idea_sandbox(self):
        t, hub, env = self.sandbox_hub("a")
        s = "create-idea-sandbox.py"
        self.call("usage", s, ["only-one"], env)
        self.call("bad name", s, ["Bad_Name", "p"], env)
        self.call("platform name", s, ["agent-hub", "p"], env)
        self.call("empty purpose", s, ["idea-1", "   "], env)
        self.call("purpose too long", s, ["idea-1", "x" * 501], env)
        (hub / "projects" / "idea-1.yaml").write_text("v: 1\nname: idea-1\ntype: idea\npurpose: a garden planner\n")
        (hub / "projects" / "proj.yaml").write_text("v: 1\nname: proj\n")
        self.call("same idea again", s, ["idea-1", "a  garden planner"], env)
        self.call("another idea", s, ["idea-1", "something else"], env)
        self.call("a project", s, ["proj", "p"], env)
        (t / "workspace" / "notes").mkdir(parents=True)
        (t / "go" / "notes-memory.git").mkdir(parents=True)
        self.call("folders exist", s, ["notes", "p"], env)
        self.call("onboard fails", s, ["idea-5", "p"], {**env, "ONBOARD_FAIL": "1"})
        self.call("onboard fails after commit", s, ["idea-5", "p"], {**env, "ONBOARD_FAIL": "1", "FAKE_COMMIT": "1"})
        self.call("created", s, ["room-3d", "a 3D model of the room"], env)
        self.out.append("=== README\n" + (t / "workspace" / "room-3d" / "README.md").read_text())
        _t2, _hub2, env2 = self.sandbox_hub("b", limit=1)
        (_hub2 / "projects" / "idea-1.yaml").write_text("v: 1\nname: idea-1\ntype: idea\npurpose: p\n")
        self.call("too many", s, ["idea-2", "p"], env2)
        self.check("create-idea-sandbox.txt")

    # upgrade-idea.py --------------------------------------------------------------------------------------------

    def upgrade_hub(self, tag: str, owner: bool = True, reg: str = IDEA):
        t = self.tmp / tag
        hub, home, remotes = t / "hub", t / "home", t / "remotes"
        (hub / "bin").mkdir(parents=True)
        (hub / "projects").mkdir()
        conf = json.loads((ROOT / "hub.example.json").read_text())
        conf["github"] = {**conf.get("github", {}), "remote": str(remotes) + "/{owner}/{name}.git"}
        if owner:
            conf["github"]["owner"] = "o"
        else:
            conf["github"].pop("owner", None)
        conf["registry_dir"] = "projects"
        (hub / "hub.json").write_text(json.dumps(conf, ensure_ascii=False))
        tool(hub / "bin" / "rename-project.sh", UP_RENAME)
        tool(hub / "bin" / "gen-index-roster.sh", "#!/bin/bash\n")
        tool(hub / "bin" / "commit-hub-files.sh", "#!/bin/bash\n")
        fakebin = t / "fakebin"
        fakebin.mkdir()
        tool(fakebin / "gh", UP_GH)
        ws = home / "workspace" / "idea-7"
        ws.mkdir(parents=True)
        (ws / "a.txt").write_text("a")
        git(ws, "init", "-q", "-b", "main")
        git(ws, "add", "a.txt")
        git(ws, "commit", "-q", "-m", "first")
        (hub / "projects" / "idea-7.yaml").write_text(reg)
        env = {**base_env(), "AGENT_HUB_ROOT": str(hub), "HOME": str(home), "FAKE_REMOTES": str(remotes),
               "PATH": f"{fakebin}:{os.environ['PATH']}"}
        return t, hub, ws, env

    def test_upgrade_idea(self):
        s = "upgrade-idea.py"
        t, hub, ws, env = self.upgrade_hub("a")
        self.call("usage", s, [], env)
        self.call("--rename without a name", s, ["idea-7", "--rename"], env)
        self.call("not in the registry", s, ["idea-9"], env)
        self.call("dry-run", s, ["idea-7"], env)
        self.call("dry-run --json", s, ["idea-7", "--json"], env)
        self.call("dry-run rename public", s, ["idea-7", "--rename", "garden", "--public"], env)
        self.call("bad new name", s, ["idea-7", "--rename", "Garden!"], env)
        (hub / "projects" / "taken.yaml").write_text("v: 1\nname: taken\n")
        (t / "home" / "workspace" / "taken").mkdir()
        self.call("new name taken", s, ["idea-7", "--rename", "taken"], env)
        (t / "remotes" / "o").mkdir(parents=True)
        git(t, "init", "-q", "--bare", str(t / "remotes" / "o" / "idea-7.git"))
        self.call("dry-run, repo from last time", s, ["idea-7"], env)
        other = t / "other"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        (other / "b.txt").write_text("b")
        git(other, "add", "b.txt")
        git(other, "commit", "-q", "-m", "other")
        git(other, "push", "-q", str(t / "remotes" / "o" / "idea-7.git"), "HEAD:main")
        self.call("repo is someone else's", s, ["idea-7"], env)
        shutil.rmtree(t / "remotes" / "o" / "idea-7.git")
        (ws / "a.txt").write_text("changed")
        self.call("dirty", s, ["idea-7"], env)
        git(ws, "checkout", "--", "a.txt")
        self.call("gh create fails", s, ["idea-7", "--apply"], {**env, "GH_CREATE_FAIL": "1"})
        self.call("rename fails", s, ["idea-7", "--apply", "--rename", "garden"], {**env, "RENAME_FAIL": "1"})
        self.call("upgraded", s, ["idea-7", "--apply"], env)
        self.call("already", s, ["idea-7", "--apply", "--json"], env)

        t, hub, ws, env = self.upgrade_hub("b")
        (hub / "projects" / "idea-7.yaml").write_text(IDEA.replace("type: idea", "type: project"))
        self.call("not an idea", s, ["idea-7"], env)
        (hub / "projects" / "idea-7.yaml").write_text(IDEA + "bogus: 1\n")
        self.call("invalid registry", s, ["idea-7"], env)
        (hub / "projects" / "idea-7.yaml").write_text(IDEA)
        state = hub / ".state"
        state.mkdir()
        (state / "project-renames.json").write_text(json.dumps({"idea-7": {"new": "garden", "stage": "github"}}))
        self.call("unfinished rename", s, ["idea-7"], env)
        (state / "project-renames.json").unlink()
        self.call("rename then stop", s, ["idea-7", "--apply", "--rename", "garden"], {**env, "GH_CREATE_FAIL": "1"})
        self.call("other name after a rename", s, ["idea-7", "--rename", "orchard"], env)
        self.call("dry-run after a rename", s, ["idea-7"], env)

        _t, _hub, _ws, env = self.upgrade_hub("c", owner=False)
        self.call("no github.owner", s, ["idea-7"], env)

        _t, _hub, _ws, env = self.upgrade_hub("d")
        self.call("registry broken by the rename", s, ["idea-7", "--apply", "--rename", "garden"], {**env, "RENAME_BREAK": "1"})
        self.check("upgrade-idea.txt")

    # project-rename.py ------------------------------------------------------------------------------------------

    def test_project_rename(self):
        s = "project-rename.py"
        hub = self.tmp / "hub"
        (hub / "bin").mkdir(parents=True)
        (hub / "projects").mkdir()
        tool(hub / "bin" / "rename-project.sh", PR_RENAME)
        fakebin = self.tmp / "fakebin"
        fakebin.mkdir()
        tool(fakebin / "gh", PR_GH)
        wsr = self.tmp / "workspace"
        (hub / "hub.json").write_text(json.dumps({
            "v": 1, "registry_dir": "projects", "workspace_root": str(wsr), "git_origin": str(self.tmp / "go"),
            "memory_copies": str(self.tmp / "mc"), "services_root": str(self.tmp / "services")}))
        for name, origin, type_ in (("proj", "git@github.com:me/proj.git", "project"), ("sandbox", "(native)", "idea"),
                                    ("other", "(native)", "project")):
            (wsr / name).mkdir(parents=True)
            (hub / "projects" / f"{name}.yaml").write_text(
                f"v: 1\nname: {name}\ntype: {type_}\npaths:\n  workspace: {wsr / name}\n  origin: '{origin}'\n")
        env = {**base_env(), "AGENT_HUB_ROOT": str(hub), "HUB": str(hub), "PATH": f"{fakebin}:{os.environ['PATH']}",
               "GH_EXISTS": "", "GH_FAIL": "", "RENAME_BUSY": "", "RENAME_FAIL": ""}
        self.call("usage", s, ["proj"], env)
        self.call("not in the registry", s, ["nope", "x"], env)
        self.call("bad name", s, ["proj", "Garden_App"], env)
        self.call("same name", s, ["proj", "proj"], env)
        self.call("the platform", s, ["proj", "agent-hub"], env)
        self.call("taken", s, ["proj", "other"], env)
        self.call("on GitHub already", s, ["proj", "garden-app"], {**env, "GH_EXISTS": "me/garden-app"})
        self.call("dry-run github", s, ["proj", "garden-app"], env)
        self.call("dry-run native", s, ["sandbox", "room-3d"], env)
        state = hub / ".state"
        state.mkdir()
        (state / "idea-upgrades.json").write_text(json.dumps({"sandbox": {"name": "orchard", "stage": "renamed"}}))
        self.call("unfinished upgrade", s, ["sandbox", "room-3d"], env)
        (state / "idea-upgrades.json").unlink()
        p = subprocess.Popen(["sleep", "30"], cwd=wsr / "proj")
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        self.call("busy", s, ["proj", "garden-app"], env)
        p.kill()
        p.wait()
        self.call("gh rename fails", s, ["proj", "garden-app", "--apply"], {**env, "GH_FAIL": "1"})
        self.call("rename-project.sh busy", s, ["proj", "garden-app", "--apply"], {**env, "RENAME_BUSY": "1"})
        self.call("rename-project.sh fails", s, ["proj", "garden-app", "--apply"], {**env, "RENAME_FAIL": "1"})
        self.call("renamed", s, ["proj", "garden-app", "--apply"], env)
        self.call("renamed --json", s, ["sandbox", "room-3d", "--apply", "--json"], env)
        self.check("project-rename.txt")


if __name__ == "__main__":
    sys.exit(unittest.main())
