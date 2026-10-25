"""平台代跑(bin/cap-runner.py、bin/hub-run):請求的檢查跟執行(docs/design/capabilities.md)。

profile 換成全放行的(profile 本身擋不擋得住由 test_cap_profile.py 測);這裡測的是「哪些請求不能跑、
結果會不會被導到別的專案、逾時/排隊/重啟」。
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))


def load(name, file):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / "bin" / file))
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


cr = load("cap_runner", "cap-runner.py")
hr = load("hub_run", "hub-run")
CAN_SANDBOX = sys.platform == "darwin" and shutil.which("sandbox-exec")


@unittest.skipUnless(CAN_SANDBOX, "要在 mac 上(sandbox-exec)")
class CapRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.a, self.b = self.tmp / "ws-a", self.tmp / "ws-b"
        for d in (self.a, self.b):
            d.mkdir()
        (self.tmp / "state").mkdir()
        allow = self.tmp / "allow.sb"
        allow.write_text("(version 1)(allow default)")
        self.projects = {"p-a": (self.a, ["gpu"]), "p-b": (self.b, ["gpu"])}
        for target, value in [("eligible", lambda: self.projects), ("make_profile", lambda *a: allow),
                              ("DIR", self.tmp / "state"), ("LOG", self.tmp / "state" / "log.jsonl"),
                              ("exclude_hub_run", lambda ws: None)]:
            p = mock.patch.object(cr, target, value)
            p.start()
            self.addCleanup(p.stop)
        self.runner = cr.Runner()

    def request(self, ws, job_id="j1", **over):
        req = {"v": 1, "id": job_id, "cap": "gpu", "argv": ["/bin/sh", "-c", "echo hi; echo err >&2; exit 3"], "cwd": "."}
        req.update(over)
        d = ws / ".hub-run" / "requests"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{job_id}.json").write_text(json.dumps(req))
        return d / f"{job_id}.json"

    def state(self, ws, job_id="j1"):
        try:
            return json.loads((ws / ".hub-run" / "jobs" / job_id / "state.json").read_text())
        except OSError:
            return None

    def run_until(self, ws, job_id="j1", limit=15):
        t0 = time.time()
        while time.time() - t0 < limit:
            self.runner.tick()
            st = self.state(ws, job_id)
            if st and st["state"] in cr.TERMINAL:
                return st
            time.sleep(0.1)
        self.fail(f"{job_id} 沒有在 {limit} 秒內結束:{self.state(ws, job_id)}")

    def test_runs_and_reports(self):
        self.request(self.a)
        st = self.run_until(self.a)
        self.assertEqual((st["state"], st["exit"]), ("done", 3))
        job = self.a / ".hub-run" / "jobs" / "j1"
        self.assertEqual((job / "stdout").read_text(), "hi\n")
        self.assertEqual((job / "stderr").read_text(), "err\n")
        self.assertFalse((self.a / ".hub-run" / "requests" / "j1.json").exists())

    def test_env_is_scrubbed(self):
        with mock.patch.dict(os.environ, {"SOME_TOKEN": "s3cret"}):
            self.request(self.a, argv=["/usr/bin/env"])
            self.run_until(self.a)
        out = (self.a / ".hub-run" / "jobs" / "j1" / "stdout").read_text()
        self.assertNotIn("SOME_TOKEN", out)
        self.assertEqual({l.split("=", 1)[0] for l in out.splitlines()} - {"PATH", "HOME", "TMPDIR", "LANG"}, set())

    def test_rejections(self):
        (self.a / "sub").mkdir()
        (self.a / "out").symlink_to(self.b)
        cases = {"cap": dict(cap="camera"), "escape": dict(cwd="../ws-b"), "abs": dict(cwd=str(self.b)),
                 "link": dict(cwd="out"), "argv": dict(argv="rm -rf /"), "empty": dict(argv=[]),
                 "version": dict(v=2), "slow": dict(timeout_s=10 ** 6)}
        for i, (name, over) in enumerate(cases.items()):
            with self.subTest(name):
                self.request(self.a, f"r{i}", **over)
                st = self.run_until(self.a, f"r{i}")
                self.assertEqual(st["state"], "rejected", name)
                self.assertFalse((self.a / ".hub-run" / "jobs" / f"r{i}" / "stdout").exists())
        self.request(self.a, "ok", cwd="sub", argv=["/bin/pwd"])
        self.assertEqual(self.run_until(self.a, "ok")["state"], "done")

    def test_id_must_match_filename(self):
        p = self.request(self.a, "j1")
        p.write_text(json.dumps({**json.loads(p.read_text()), "id": "other"}))
        self.assertEqual(self.run_until(self.a)["state"], "rejected")

    def test_stale_request(self):
        p = self.request(self.a)
        old = time.time() - cr.STALE_S - 60
        os.utime(p, (old, old))
        self.assertEqual(self.run_until(self.a)["state"], "rejected")

    def test_hub_run_dir_symlink_is_ignored(self):
        # a 的 .hub-run 指到 b 的:結果不能被寫進 b,工作也不能用 a 的身分跑
        self.request(self.b, "x")
        (self.a / ".hub-run").symlink_to(self.b / ".hub-run")
        del self.projects["p-b"]
        for _ in range(5):
            self.runner.tick()
        self.assertFalse((self.b / ".hub-run" / "jobs").exists())
        self.assertTrue((self.b / ".hub-run" / "requests" / "x.json").exists())

    def test_jobs_dir_symlink_is_refused(self):
        self.request(self.a)
        (self.b / "jobs").mkdir()
        (self.a / ".hub-run" / "jobs").symlink_to(self.b / "jobs")
        for _ in range(5):
            self.runner.tick()
        self.assertEqual(list((self.b / "jobs").iterdir()), [])

    def test_request_symlink_is_dropped(self):
        (self.b / "evil.json").write_text(json.dumps({"v": 1, "id": "j1", "cap": "gpu", "argv": ["/usr/bin/true"]}))
        d = self.a / ".hub-run" / "requests"
        d.mkdir(parents=True)
        (d / "j1.json").symlink_to(self.b / "evil.json")
        self.runner.tick()
        self.assertFalse((d / "j1.json").exists())
        self.assertIsNone(self.state(self.a))
        self.assertTrue((self.b / "evil.json").exists())

    def test_timeout_kills_process_group(self):
        marker = self.a / "child-alive"
        self.request(self.a, argv=["/bin/sh", "-c", f"(sleep 3; touch {marker}) & sleep 30"], timeout_s=1)
        st = self.run_until(self.a, limit=20)
        self.assertEqual((st["state"], st["exit"], st["timed_out"]), ("done", 124, True))
        time.sleep(3.5)
        self.assertFalse(marker.exists(), "逾時後背景的子程序還活著")

    def test_one_job_per_project(self):
        self.request(self.a, "j1", argv=["/bin/sleep", "1"])
        self.request(self.a, "j2", argv=["/usr/bin/true"])
        self.runner.tick()
        self.assertEqual(self.state(self.a, "j1")["state"], "running")
        self.assertEqual(self.state(self.a, "j2")["state"], "queued")
        self.assertEqual(self.run_until(self.a, "j2")["state"], "done")

    def test_restart_marks_lost(self):
        self.request(self.a, argv=["/bin/sleep", "30"])
        self.runner.tick()
        pid = self.state(self.a)["pid"]
        cr.Runner().recover()
        self.assertEqual(self.state(self.a)["state"], "lost")
        time.sleep(0.2)
        self.runner.running[0].proc.poll()
        self.assertIsNotNone(self.runner.running[0].proc.returncode, f"pid {pid} 還活著")

    def test_client_end_to_end(self):
        stop = threading.Event()

        def serve():
            while not stop.is_set():
                self.runner.tick()
                time.sleep(0.1)

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        self.addCleanup(stop.set)
        out = io.TextIOWrapper(io.BytesIO())
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", io.TextIOWrapper(io.BytesIO())), \
                mock.patch.object(os, "getcwd", return_value=str(self.a)):
            job = hr.submit(self.a, "gpu", ["/bin/sh", "-c", "echo hello; exit 7"], None)
            code = hr.follow(self.a, job, pickup=True)
            out.flush()
            self.assertEqual(code, 7)
            self.assertEqual(out.buffer.getvalue(), b"hello\n")
            self.assertEqual(hr.follow(self.a, job, pickup=False), 7)  # --wait 接回跑完的工作

    def test_client_gives_up_without_service(self):
        with mock.patch.object(hr, "PICKUP_S", 0.5), mock.patch.object(sys, "stderr", io.TextIOWrapper(io.BytesIO())), \
                mock.patch.object(os, "getcwd", return_value=str(self.a)):
            job = hr.submit(self.a, "gpu", ["/usr/bin/true"], None)
            self.assertEqual(hr.follow(self.a, job, pickup=True), 125)
        self.assertEqual(list((self.a / ".hub-run" / "requests").glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
