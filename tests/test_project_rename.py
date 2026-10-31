"""專案整套改名(bin/project-rename.py、lease-runner 的 POST /v1/projects/<name>/rename;10-25 Muninn m10251650)。

整個 hub 搭在暫存目錄(AGENT_HUB_ROOT);gh 跟 rename-project.sh 換成記錄參數的假腳本,不碰 GitHub、不搬真的資料夾。

    bin/py -m unittest discover -s tests
"""
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]

FAKE_GH = r"""#!/bin/bash
echo "gh $*" >> "$LOG"
if [ "$1 $2" = "repo view" ]; then
  for r in $GH_EXISTS; do [ "$r" = "$3" ] && exit 0; done
  exit 1
fi
[ "$1 $2" = "repo rename" ] && { [ -n "$GH_FAIL" ] && { echo "gh: boom" >&2; exit 1; }; exit 0; }
exit 0
"""
# 假的 rename-project.sh:像真的一樣把登錄檔改名(其他步驟只記參數);RENAME_BUSY 時照真的訊息失敗
FAKE_RENAME = r"""#!/bin/bash
echo "rename-project.sh $*" >> "$LOG"
[ -n "$RENAME_BUSY" ] && { echo "rename-project: 還有程序的工作目錄在 /x:claude(1)——先關掉" >&2; exit 75; }
sed "s/^name: $1$/name: $2/" "$HUB/projects/$1.yaml" > "$HUB/projects/$2.yaml" && rm "$HUB/projects/$1.yaml"
"""


class Script(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.hub = self.tmp / "hub"
        (self.hub / "bin").mkdir(parents=True)
        (self.hub / "projects").mkdir()
        for f in ("project-rename.py", "hubconf.py", "i18n.py"):
            shutil.copy(ROOT / "bin" / f, self.hub / "bin" / f)
        for name, text in (("rename-project.sh", FAKE_RENAME),):
            (self.hub / "bin" / name).write_text(text)
            (self.hub / "bin" / name).chmod(0o755)
        fakebin = self.tmp / "fakebin"
        fakebin.mkdir()
        (fakebin / "gh").write_text(FAKE_GH)
        (fakebin / "gh").chmod(0o755)
        self.ws = self.tmp / "workspace"
        (self.hub / "hub.json").write_text(json.dumps({
            "v": 1, "registry_dir": "projects", "workspace_root": str(self.ws), "git_origin": str(self.tmp / "go"),
            "memory_copies": str(self.tmp / "mc"), "services_root": str(self.tmp / "services")}))
        self.log = self.tmp / "log"
        self.env = {**os.environ, "AGENT_HUB_ROOT": str(self.hub), "HUB": str(self.hub), "LOG": str(self.log),
                    "PATH": f"{fakebin}:{os.environ['PATH']}"}
        self.reg("proj", "git@github.com:me/proj.git")
        self.reg("sandbox", "(native)", type_="idea")

    def reg(self, name, origin, type_="project"):
        (self.ws / name).mkdir(parents=True, exist_ok=True)
        (self.hub / "projects" / f"{name}.yaml").write_text(
            f"v: 1\nname: {name}\ntype: {type_}\npaths:\n  workspace: {self.ws / name}\n  origin: '{origin}'\n")

    def run_it(self, old, new, *flags, **env):
        r = subprocess.run([sys.executable, str(self.hub / "bin" / "project-rename.py"), old, new, "--apply", "--json", *flags],
                           capture_output=True, text=True, env={**self.env, **env})
        return json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {"stderr": r.stderr}

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_github_project(self):
        res = self.run_it("proj", "garden-app")
        self.assertEqual((res["result"], res["name"], res["origin"]),
                         ("renamed", "garden-app", "git@github.com:me/garden-app.git"))
        c = self.calls()
        self.assertIn("gh repo rename garden-app -R me/proj --yes", c)
        self.assertTrue(any(x.startswith("rename-project.sh proj garden-app --origin git@github.com:me/garden-app.git")
                            for x in c))
        self.assertEqual(self.run_it("proj", "garden-app")["result"], "already", "重送安全")

    def test_sandbox_without_remote(self):
        res = self.run_it("sandbox", "room-3d")
        self.assertEqual((res["result"], res["origin"]), ("renamed", "(native)"))
        self.assertFalse(any(x.startswith("gh repo rename") for x in self.calls()))
        self.assertNotIn("--origin", next(x for x in self.calls() if x.startswith("rename-project.sh")))

    def test_retry_after_github_step(self):
        """GitHub 改好了、後面失敗:再叫一次不再改 GitHub,接著做完。"""
        res = self.run_it("proj", "garden-app", RENAME_BUSY="1")
        self.assertEqual(res["result"], "busy")
        res = self.run_it("proj", "garden-app", GH_EXISTS="me/garden-app")
        self.assertEqual(res["result"], "renamed")
        self.assertEqual(sum(x.startswith("gh repo rename") for x in self.calls()), 1)

    def test_taken_and_bad(self):
        self.assertEqual(self.run_it("proj", "room", GH_EXISTS="me/room")["result"], "name_taken")
        (self.ws / "used").mkdir()
        self.assertEqual(self.run_it("proj", "used")["result"], "name_taken")
        self.assertEqual(self.run_it("proj", "sandbox")["result"], "name_taken")
        for bad in ("Bad", "a--b", "-a", "proj", "agent-hub"):
            self.assertEqual(self.run_it("proj", bad)["result"], "bad_name", bad)
        self.assertEqual(self.run_it("nope", "x")["result"], "not_found")
        self.assertEqual(self.calls(), [l for l in self.calls() if l.startswith("gh repo view")], "都沒有動手")

    def test_busy_before_github(self):
        """10-25 review:資料夾有人在用(含 worktree 這種子資料夾)要在動 GitHub 之前就擋下來。"""
        sub = self.ws / "proj" / ".claude" / "worktrees" / "wt"
        sub.mkdir(parents=True)
        sleeper = subprocess.Popen(["/bin/sleep", "30"], cwd=sub)
        self.addCleanup(sleeper.kill)
        res = self.run_it("proj", "garden-app")
        self.assertEqual(res["result"], "busy")
        self.assertIn("sleep", res["detail"])
        self.assertFalse(any(x.startswith("gh repo rename") for x in self.calls()))

    def test_unfinished_upgrade_blocks(self):
        """10-25 review:升級跟改名各記各的進度——對方做到一半就先別動,不然重試會找不到名稱。"""
        (self.hub / ".state").mkdir(exist_ok=True)
        (self.hub / ".state" / "idea-upgrades.json").write_text(json.dumps({"sandbox": {"name": "proj", "stage": "renamed"}}))
        res = self.run_it("proj", "garden-app")
        self.assertEqual(res["result"], "failed")
        self.assertIn("升級", res["detail"])
        self.assertEqual(self.calls(), [l for l in self.calls() if l.startswith("gh repo view")])

    def test_github_failure_changes_nothing(self):
        self.assertEqual(self.run_it("proj", "garden-app", GH_FAIL="1")["result"], "failed")
        self.assertTrue((self.hub / "projects" / "proj.yaml").exists())
        self.assertFalse(any(x.startswith("rename-project.sh") for x in self.calls()))


class Endpoint(unittest.TestCase):
    def setUp(self):
        import http.server
        spec = importlib.util.spec_from_file_location("lease_runner_rn", ROOT / "bin" / "lease-runner.py")
        self.m = m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        m.DIR = self.tmp / "state"
        m.token = lambda: "t"
        self.ws = {"proj": self.tmp / "proj", "garden-app": self.tmp / "garden-app"}
        self.ran, self.moves, self.result = [], [], {"result": "renamed", "name": "garden-app", "origin": "o"}
        rt = m.rt()
        for target, attr, value in [
                (m.hubconf, "workspace", lambda name: self.ws.get(name)),
                (rt, "relocate", lambda sid, a, b: (self.moves.append(sid), True)[1]),
                (m, "work_ready", lambda project: (True, "")),
                (m.subprocess, "run", lambda cmd, **kw: (self.ran.append(cmd), types.SimpleNamespace(
                    returncode=0, stdout=json.dumps(self.result) + "\n", stderr=""))[1])]:
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        reg = types.ModuleType("registry")
        reg.refresh = lambda: {"projects": {}}
        p = mock.patch.dict(sys.modules, {"registry": reg})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(m.LEASES.clear)
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), m.H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def lease(self, sid, **kw):
        l = self.m.Lease({"lease_id": sid, "project": "proj", "cwd": str(self.ws["proj"]), "mode": "work"})
        l.state = "idle_stopped"
        for k, v in kw.items():
            setattr(l, k, v)
        self.m.LEASES[sid] = l
        return l

    def post(self, body):
        req = urllib.request.Request(self.base + "/v1/projects/proj/rename", data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": "Bearer t"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_rename_follows_leases(self):
        l = self.lease("aaaaaaaa-0000-4000-8000-00000000000a")
        code, body = self.post({"v": 2, "to": "garden-app"})
        self.assertEqual((code, body["name"], body["renamed"]), (200, "garden-app", True))
        self.assertEqual((l.project, l.cwd, l.mode), ("garden-app", str(self.ws["garden-app"]), "work"))
        self.assertEqual(self.moves, [l.id])

    def test_busy_does_nothing(self):
        self.lease("aaaaaaaa-0000-4000-8000-00000000000b", waiting=1, state="running")
        code, body = self.post({"v": 2, "to": "garden-app"})
        self.assertEqual((code, body["error"]), (409, "busy"))
        self.assertEqual(self.ran, [])

    def test_errors_map(self):
        for result, code in (("bad_name", 400), ("not_found", 404), ("name_taken", 409), ("busy", 409), ("failed", 500)):
            self.result = {"result": result, "detail": "x"}
            got, body = self.post({"v": 2, "to": "garden-app"})
            self.assertEqual((got, body["error"]), (code, "rename_failed" if code == 500 else result))
        self.assertEqual(self.post({"v": 2})[0], 400)


if __name__ == "__main__":
    unittest.main()
