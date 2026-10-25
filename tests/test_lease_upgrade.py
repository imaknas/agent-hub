"""想法沙盒升級時,正在用它的深談怎麼辦(10-25 Muninn m10251626-26e9、m10251631-2bdd)。

- 正在回答/等回覆/在終端機 → 409 busy,不升級;閒著的先停程序。
- 改名:對話紀錄搬到新資料夾(Claude Code 照資料夾的 slug 放,--resume 只找目前那格)、租約的 project/cwd 跟上、
  還沒收回的送 state 事件帶新的 project;work 改回 discuss(work 只開給想法沙盒)。
- 接回時(POST /v1/leases 同一個 session_id)資料夾跟上次不同:先把對話紀錄搬過來。

不會真的升級:upgrade-idea.py 的呼叫換成假的。

    bin/py -m unittest discover -s tests
"""
import importlib.util
import json
import pathlib
import shutil
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import runtime_claude  # noqa: E402

SID = "aaaaaaaa-0000-4000-8000-000000000001"
SID2 = "aaaaaaaa-0000-4000-8000-000000000002"


class Relocate(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        p = mock.patch.object(runtime_claude, "PROJECTS", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        self.old, self.new = "/Users/x/workspace/idea-1", "/Users/x/workspace/room3d"
        d = self.tmp / runtime_claude.slug_for_path(self.old)
        (d / SID).mkdir(parents=True)
        (d / f"{SID}.jsonl").write_text("{}\n")
        (d / SID / "sub.jsonl").write_text("{}\n")

    def test_moves_transcript_and_subagents(self):
        self.assertTrue(runtime_claude.relocate(SID, self.old, self.new))
        nd = self.tmp / runtime_claude.slug_for_path(self.new)
        self.assertTrue((nd / f"{SID}.jsonl").exists())
        self.assertTrue((nd / SID / "sub.jsonl").exists())
        self.assertFalse((self.tmp / runtime_claude.slug_for_path(self.old) / f"{SID}.jsonl").exists())
        self.assertTrue(runtime_claude.relocate(SID, self.old, self.new), "已經在新的地方 = True")

    def test_unknown_or_bad_ids(self):
        self.assertFalse(runtime_claude.relocate(SID2, self.old, self.new))
        self.assertFalse(runtime_claude.relocate("../x", self.old, self.new))


class Endpoint(unittest.TestCase):
    def setUp(self):
        import http.server
        spec = importlib.util.spec_from_file_location("lease_runner_up", ROOT / "bin" / "lease-runner.py")
        self.m = m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        m.DIR = self.tmp / "state"
        m.token = lambda: "t"
        self.ws = {"idea-1": self.tmp / "idea-1", "room3d": self.tmp / "room3d"}
        for d in self.ws.values():
            d.mkdir()
        self.upgraded = []
        self.moves = []
        rt = m.rt()
        for target, attr, value in [
                (m.hubconf, "workspace", lambda name: self.ws.get(name)),
                (m.hubconf, "projects", lambda: {"room3d": {}, "idea-1": {}}),
                (rt, "relocate", lambda sid, a, b: (self.moves.append((sid, a, b)), True)[1]),
                (m, "work_ready", lambda project: (False, "不是想法沙盒")),
                (m.subprocess, "run", self.fake_run)]:
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

    def fake_run(self, cmd, **kw):
        self.upgraded.append(cmd)
        name = cmd[cmd.index("--rename") + 1] if "--rename" in cmd else cmd[2]
        out = json.dumps({"result": "upgraded", "name": name, "origin": f"git@github.com:o/{name}.git"})
        return types.SimpleNamespace(returncode=0, stdout=out + "\n", stderr="")

    def lease(self, sid, state="idle_stopped", mode="work", **kw):
        l = self.m.Lease({"lease_id": sid, "project": "idea-1", "cwd": str(self.ws["idea-1"]), "mode": mode})
        l.state = state
        for k, v in kw.items():
            setattr(l, k, v)
        self.m.LEASES[sid] = l
        return l

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": "Bearer t"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def events(self, l):
        f = l.dir / "events.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def test_busy_lease_blocks_upgrade(self):
        self.lease(SID, state="running", in_turn=True)
        code, body = self.post("/v1/sandboxes/idea-1/upgrade", {"v": 2, "rename": "room3d"})
        self.assertEqual((code, body["error"]), (409, "busy"))
        self.assertEqual(self.upgraded, [])

    def test_rename_follows_leases(self):
        live = self.lease(SID)
        done = self.lease(SID2, state="detached", mode="discuss")
        code, body = self.post("/v1/sandboxes/idea-1/upgrade", {"v": 2, "rename": "room3d"})
        self.assertEqual((code, body["name"]), (200, "room3d"))
        for l in (live, done):
            self.assertEqual((l.project, l.cwd), ("room3d", str(self.ws["room3d"])))
        self.assertEqual(sorted(s for s, _, _ in self.moves), [SID, SID2])
        ev = [e for e in self.events(live) if e["type"] == "state"][-1]
        self.assertEqual((ev["project"], ev["mode"], live.mode), ("room3d", "discuss", "discuss"))
        self.assertEqual(self.events(done), [], "已經收回的不送事件")

    def test_no_rename_keeps_folder(self):
        l = self.lease(SID, mode="discuss")
        code, _ = self.post("/v1/sandboxes/idea-1/upgrade", {"v": 2})
        self.assertEqual(code, 200)
        self.assertEqual((l.project, self.moves), ("idea-1", []))

    def test_reattach_in_moved_folder_relocates(self):
        prev = self.lease(SID, state="detached", mode="discuss")
        prev.cwd = str(self.tmp / "gone")
        with mock.patch.object(self.m, "start_proc", lambda lease, resume: None):
            code, _ = self.post("/v1/leases", {"v": 2, "project": "room3d", "session_id": SID, "thread": "t",
                                               "resume": True})
        self.assertEqual(code, 201)
        self.assertEqual(self.moves, [(SID, str(self.tmp / "gone"), str(self.ws["room3d"]))])


if __name__ == "__main__":
    unittest.main()
