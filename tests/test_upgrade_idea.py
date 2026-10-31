"""bin/upgrade-idea.py: upgrading an idea sandbox into a full project. Either all of it succeeds, or nothing is
touched, or a run stopped midway can be picked up by calling it again (on 10-25 idea-1 stopped halfway).

Runs the real upgrade-idea.py, but under a temporary hub (AGENT_HUB_ROOT) and a temporary HOME; "GitHub" is a local
bare repo (hub.json's github.remote) plus a fake gh; renaming, regenerating the roster and committing are stand-in
scripts.

    bin/py -m unittest discover -s tests
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
PY = sys.executable   # under the gate this is the platform's Python (bin/py)

try:
    import yaml
except ImportError:
    yaml = None

FAKE_GH = r"""#!/bin/bash
# fake gh: repo view <owner/name> / repo create <owner/name> ... --source=<dir> --push
set -u
case "$1 $2" in
  "repo view") [ -d "$FAKE_REMOTES/$3.git" ];;
  "repo create")
    dest="$FAKE_REMOTES/$3.git"; src=""
    for a in "$@"; do case "$a" in --source=*) src="${a#--source=}";; esac; done
    mkdir -p "$(dirname "$dest")" && git init -q --bare -b main "$dest" || exit 1
    git -C "$src" remote add origin "$dest" && git -C "$src" push -q -u origin HEAD || exit 1
    [ -n "${FAKE_GH_FAIL_AFTER_CREATE:-}" ] && { echo "network went away" >&2; exit 1; }
    exit 0;;
  *) echo "fake gh: $*" >&2; exit 2;;
esac
"""

FAKE_RENAME = r"""#!/bin/bash
# stand-in rename-project.sh <old> <new> --apply: moves the folder, renames the registry entry (the real one has its
# own tests)
set -eu
mv "$HOME/workspace/$1" "$HOME/workspace/$2"
sed -e "s/^name: $1\$/name: $2/" -e "s#workspace/$1\$#workspace/$2#" -e "s#/$1-memory.git#/$2-memory.git#" \
  "$AGENT_HUB_ROOT/projects/$1.yaml" > "$AGENT_HUB_ROOT/projects/$2.yaml"
rm "$AGENT_HUB_ROOT/projects/$1.yaml"
"""

IDEA = """# 專案登錄
v: 1
name: idea-7
paths:
  workspace: ~/workspace/idea-7
  memory_bare: ~/git-origin/idea-7-memory.git
  origin: (native)
memory: true
type: idea
purpose: 測試用的想法
enforce:
  sandbox: true
  capabilities: [gpu]
"""


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@unittest.skipIf(yaml is None, "needs PyYAML (the platform's Python has it, bin/setup-python)")
class Upgrade(unittest.TestCase):
    def setUp(self):
        t = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, t, ignore_errors=True)
        self.hub, self.home, self.remotes = t / "hub", t / "home", t / "remotes"
        (self.hub / "bin").mkdir(parents=True)
        (self.hub / "projects").mkdir()
        (self.hub / "capabilities").mkdir()
        shutil.copy(ROOT / "capabilities" / "gpu.sb", self.hub / "capabilities" / "gpu.sb")
        conf = json.loads((ROOT / "hub.example.json").read_text())
        conf["github"] = {**conf.get("github", {}), "owner": "o", "remote": str(self.remotes) + "/{owner}/{name}.git"}
        conf["registry_dir"] = "projects"
        (self.hub / "hub.json").write_text(json.dumps(conf, ensure_ascii=False))
        for name, body in (("rename-project.sh", FAKE_RENAME), ("gen-index-roster.sh", "#!/bin/bash\n"),
                           ("commit-hub-files.sh", "#!/bin/bash\necho \"$@\" >> \"$AGENT_HUB_ROOT/commits.log\"\n")):
            (self.hub / "bin" / name).write_text(body)
            (self.hub / "bin" / name).chmod(0o755)
        fakebin = t / "fakebin"
        fakebin.mkdir()
        (fakebin / "gh").write_text(FAKE_GH)
        (fakebin / "gh").chmod(0o755)
        self.ws = self.home / "workspace" / "idea-7"
        self.ws.mkdir(parents=True)
        (self.ws / "a.txt").write_text("a")
        git(self.ws, "init", "-q", "-b", "main")
        git(self.ws, "add", "a.txt")
        git(self.ws, "commit", "-q", "-m", "first")
        (self.hub / "projects" / "idea-7.yaml").write_text(IDEA)
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
                    "AGENT_HUB_ROOT": str(self.hub), "AGENT_HUB_LANG_UI": "zh-Hant", "HOME": str(self.home), "FAKE_REMOTES": str(self.remotes),
                    "PATH": f"{fakebin}:{os.environ['PATH']}"}

    def upgrade(self, *args, **env):
        r = subprocess.run([PY, str(ROOT / "bin" / "upgrade-idea.py"), "idea-7", "--apply", "--json", *args],
                           capture_output=True, text=True, env={**self.env, **env}, timeout=60)
        return json.loads(r.stdout.strip().splitlines()[-1]), r.returncode

    def reg(self, name="idea-7"):
        p = self.hub / "projects" / f"{name}.yaml"
        return yaml.safe_load(p.read_text()) if p.exists() else None

    def remote(self, name="idea-7"):
        return self.remotes / "o" / f"{name}.git"

    def record(self):
        p = self.hub / ".state" / "idea-upgrades.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def test_idea_with_capabilities_upgrades_and_keeps_them(self):
        res, code = self.upgrade()
        self.assertEqual((code, res["result"], res["name"]), (0, "upgraded", "idea-7"), res)
        doc = self.reg()
        self.assertEqual(doc["type"], "project")
        # an old sandbox without work spelled out: added on upgrade (an idea sandbox can act in a discussion by default,
        # the owner, 10-25)
        self.assertEqual(doc["enforce"], {"sandbox": True, "capabilities": ["gpu"], "work": True})
        self.assertEqual(doc["paths"]["origin"], str(self.remote()))
        self.assertEqual(git(self.remote(), "rev-parse", "HEAD"), git(self.ws, "rev-parse", "HEAD"))
        self.assertEqual(self.record()["idea-7"]["stage"], "done")
        res, code = self.upgrade()
        self.assertEqual((code, res["result"]), (0, "already"))

    def test_explicit_work_false_is_kept(self):
        (self.hub / "projects" / "idea-7.yaml").write_text(IDEA.replace("  capabilities: [gpu]\n", "  capabilities: [gpu]\n  work: false\n"))
        res, code = self.upgrade()
        self.assertEqual(code, 0, res)
        self.assertIs(self.reg()["enforce"]["work"], False)

    def test_invalid_registry_touches_nothing(self):
        before = IDEA + "bogus: 1\n"
        (self.hub / "projects" / "idea-7.yaml").write_text(before)
        res, code = self.upgrade()
        self.assertEqual((code, res["result"]), (1, "failed"))
        self.assertIn("什麼都沒動", res["detail"])
        self.assertFalse(self.remote().exists(), "no repo is created when validation fails")
        self.assertEqual((self.hub / "projects" / "idea-7.yaml").read_text(), before)
        self.assertEqual(self.record(), {})

    def test_retry_after_failing_midway_completes(self):
        res, code = self.upgrade(FAKE_GH_FAIL_AFTER_CREATE="1")
        self.assertEqual((code, res["result"]), (1, "failed"))
        self.assertTrue(self.remote().exists())
        self.assertEqual(self.reg()["type"], "idea")
        res, code = self.upgrade()
        self.assertEqual((code, res["result"]), (0, "upgraded"), res)
        self.assertEqual(self.reg()["type"], "project")

    def test_new_commits_since_the_failed_attempt_are_pushed(self):
        self.upgrade(FAKE_GH_FAIL_AFTER_CREATE="1")
        (self.ws / "b.txt").write_text("b")
        git(self.ws, "add", "b.txt")
        git(self.ws, "commit", "-q", "-m", "second")
        res, code = self.upgrade()
        self.assertEqual(code, 0, res)
        self.assertEqual(git(self.remote(), "rev-parse", "HEAD"), git(self.ws, "rev-parse", "HEAD"))

    def test_someone_elses_repo_is_name_taken(self):
        other = self.remotes / "scratch"
        other.mkdir(parents=True)
        git(other, "init", "-q")
        (other / "x").write_text("x")
        git(other, "add", "x")
        git(other, "commit", "-q", "-m", "unrelated")
        subprocess.run(["git", "clone", "-q", "--bare", str(other), str(self.remote())], check=True)
        res, code = self.upgrade()
        self.assertEqual((code, res["result"]), (1, "name_taken"))
        self.assertEqual(self.reg()["type"], "idea")

    def test_refuses_while_a_rename_of_the_same_project_is_unfinished(self):
        state = self.hub / ".state"
        state.mkdir(exist_ok=True)
        for rec in ({"idea-7": {"new": "room-3d", "stage": "github"}}, {"old-name": {"new": "idea-7", "stage": "github"}}):
            (state / "project-renames.json").write_text(json.dumps(rec))
            res, code = self.upgrade()
            self.assertEqual((code, res["result"]), (1, "failed"))
            self.assertIn("先把改名的重試做完", res["detail"])
            self.assertFalse(self.remote().exists())
        (state / "project-renames.json").write_text(json.dumps({"idea-7": {"new": "room-3d", "stage": "done"},
                                                                 "other": {"new": "x", "stage": "github"}}))
        res, code = self.upgrade()
        self.assertEqual((code, res["result"]), (0, "upgraded"), res)

    def test_rename_interrupted_then_resumed(self):
        res, code = self.upgrade("--rename", "garden-app", FAKE_GH_FAIL_AFTER_CREATE="1")
        self.assertEqual(code, 1)
        self.assertEqual(self.record()["idea-7"]["stage"], "renamed")
        self.assertIsNone(self.reg("idea-7"))
        res, code = self.upgrade("--rename", "something-else")
        self.assertEqual((code, res["result"]), (1, "name_taken"))
        res, code = self.upgrade("--rename", "garden-app")
        self.assertEqual((code, res["result"], res["name"]), (0, "upgraded", "garden-app"), res)
        doc = self.reg("garden-app")
        self.assertEqual((doc["type"], doc["name"], doc["paths"]["workspace"]),
                         ("project", "garden-app", "~/workspace/garden-app"))
        res, code = self.upgrade()
        self.assertEqual((code, res["result"], res["name"]), (0, "already", "garden-app"))


if __name__ == "__main__":
    unittest.main()
