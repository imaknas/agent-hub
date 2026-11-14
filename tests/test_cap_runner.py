"""The platform running things on a project's behalf (bin/cap-runner.py, bin/hub-run): checking and running requests
(docs/design/capabilities.md).

The profile is replaced with one that allows everything (whether the profile itself blocks is tested by
test_cap_profile.py); this tests "which requests must not run, whether results can be redirected to another project,
timeouts / queueing / restarts".
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


@unittest.skipUnless(CAN_SANDBOX, "needs a mac (sandbox-exec)")
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
        self.projects = {"p-a": {"dirs": [self.a], "caps": ["gpu"], "models": {}},
                         "p-b": {"dirs": [self.b], "caps": ["gpu"], "models": {}}}
        for target, value in [("eligible", lambda: self.projects), ("make_profile", lambda *a: allow),
                              ("DIR", self.tmp / "state"), ("LOG", self.tmp / "state" / "log.jsonl"),
                              ("exclude_hub_run", lambda project, ws: None)]:
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
        self.fail(f"{job_id} did not finish within {limit} seconds: {self.state(ws, job_id)}")

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
        # a's .hub-run points to b's: results must not be written into b, and the job must not run as a
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
        self.assertFalse(marker.exists(), "a background child process is still alive after the timeout")

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
        self.assertIsNotNone(self.runner.running[0].proc.returncode, f"pid {pid} still alive")

    def test_start_failure_is_reported(self):
        with mock.patch.object(cr, "make_profile", side_effect=AttributeError("boom")):
            self.request(self.a)
            st = self.run_until(self.a)
        self.assertEqual(st["state"], "rejected")
        self.assertIn("boom", st["reason"])

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
            self.assertEqual(hr.follow(self.a, job, pickup=False), 7)  # --wait picks up the finished job

    def test_client_gives_up_without_service(self):
        with mock.patch.object(hr, "PICKUP_S", 0.5), mock.patch.object(sys, "stderr", io.TextIOWrapper(io.BytesIO())), \
                mock.patch.object(os, "getcwd", return_value=str(self.a)):
            job = hr.submit(self.a, "gpu", ["/usr/bin/true"], None)
            self.assertEqual(hr.follow(self.a, job, pickup=True), 125)
        self.assertEqual(list((self.a / ".hub-run" / "requests").glob("*.json")), [])


class SwappedRequest(unittest.TestCase):
    """The job folders are in the project's folder: what the runner checked a moment ago may be swapped since. Nothing
    there may hold the shared service (a FIFO), be followed (a link), or be read as a request unless it is a regular
    file (docs/design/git-config-fence.md, r2 S5)."""

    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fd = os.open(self.d, os.O_RDONLY)
        self.addCleanup(os.close, self.fd)

    def returns(self, fn, wait=5):
        """fn's result or the exception it raised, as long as it returns at all."""
        done = []

        def go():
            try:
                done.append(fn())
            except Exception as e:   # noqa: BLE001 (what it raised is the result here)
                done.append(e)
        t = threading.Thread(target=go, daemon=True)
        t.start()
        t.join(wait)
        self.assertTrue(done, "it returned")
        return done[0]

    def test_a_fifo_is_refused_without_blocking(self):
        os.mkfifo(self.d / "request.json")
        self.assertIsInstance(self.returns(lambda: cr.read_json(self.fd, "request.json")), ValueError)

    def test_a_fifo_with_a_writer_is_refused_too(self):
        """Without the regular-file check a FIFO someone writes into would be read as the request."""
        os.mkfifo(self.d / "request.json")
        w = os.open(self.d / "request.json", os.O_RDWR)   # a writer holding it open, its request already written
        self.addCleanup(os.close, w)
        os.write(w, json.dumps({"v": 1}).encode())
        self.assertIsInstance(self.returns(lambda: cr.read_json(self.fd, "request.json")), ValueError)

    def test_a_folder_renamed_into_place_is_refused(self):
        (self.d / "request.json").mkdir()
        self.assertIsInstance(self.returns(lambda: cr.read_json(self.fd, "request.json")), ValueError)

    def test_writing_the_state_replaces_a_fifo_left_in_the_way(self):
        os.mkfifo(self.d / ".state.tmp")
        self.assertIsNone(self.returns(lambda: cr.write_state(self.fd, state="running")))
        self.assertEqual(json.loads((self.d / "state.json").read_text())["state"], "running")

    def test_a_jobs_state_is_read_the_same_way(self):
        """recover() at start-up and prune(): a FIFO or a link at state.json is no state, and nothing hangs."""
        os.mkfifo(self.d / "state.json")
        self.assertIsNone(self.returns(lambda: cr.job_state(self.d)))
        (self.d / "state.json").unlink()
        target = self.d / "elsewhere.json"
        target.write_text(json.dumps({"state": "done"}))
        os.utime(target, (0, 0))
        (self.d / "state.json").symlink_to(target)
        self.assertIsNone(cr.job_state(self.d, older_than=time.time()), "the link isn't followed for its age")
        self.assertIsNone(cr.job_state(self.d))
        (self.d / "state.json").unlink()
        (self.d / "state.json").write_text(json.dumps({"state": "done"}))
        self.assertEqual(cr.job_state(self.d), {"state": "done"})
        self.assertIsNone(cr.job_state(self.d, older_than=0), "newer than the cut-off")


class SwappedJobs(unittest.TestCase):
    """prune() and recover() walk the job folders as one chain of fds: a jobs folder or a job folder the project swaps for
    a link after the look is never followed (a review reproduced prune deleting outside the project that way)."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = self.tmp / "ws"
        self.jobs = self.ws / ".hub-run" / "jobs"
        (self.jobs / "victim").mkdir(parents=True)
        st = self.jobs / "victim" / "state.json"
        st.write_text(json.dumps({"state": "done"}))
        os.utime(st, (0, 0))
        self.outside = self.tmp / "outside" / "victim"
        self.outside.mkdir(parents=True)
        (self.outside / "precious.txt").write_text("keep me")
        (self.outside / "state.json").write_text(json.dumps({"state": "running"}))
        (self.tmp / "state").mkdir()
        # every state file in the test's own folder: LOG and ACTIVE are fixed at import, beside the live state
        for p in (mock.patch.object(cr, "eligible", lambda: {"p": {"dirs": [self.ws], "caps": [], "models": {}}}),
                  mock.patch.object(cr, "DIR", self.tmp / "state"),
                  mock.patch.object(cr, "LOG", self.tmp / "state" / "log.jsonl"),
                  mock.patch.object(cr, "ACTIVE", self.tmp / "state" / "active.json")):
            p.start()
            self.addCleanup(p.stop)

    def swap_after_the_read(self):
        real = cr.job_state_at

        def racing(fd, older_than=None):
            got = real(fd, older_than)
            os.rename(self.jobs, self.ws / ".hub-run" / "jobs.real")
            os.symlink(self.tmp / "outside", self.jobs)
            return got
        return mock.patch.object(cr, "job_state_at", racing)

    def test_prune_never_deletes_through_a_link_swapped_in(self):
        with self.swap_after_the_read():
            cr.Runner.prune(object.__new__(cr.Runner))
        self.assertTrue((self.outside / "precious.txt").exists())
        self.assertFalse((self.ws / ".hub-run" / "jobs.real" / "victim").exists(), "the real job folder went")

    def test_a_linked_jobs_folder_isnt_walked(self):
        shutil.rmtree(self.jobs)
        os.symlink(self.tmp / "outside", self.jobs)
        self.assertEqual(list(cr.each_job()), [])

    def test_recover_writes_through_the_fd_it_read(self):
        (self.jobs / "victim" / "state.json").write_text(json.dumps({"state": "running"}))
        runner = object.__new__(cr.Runner)
        runner.minutes = mock.Mock()
        with mock.patch.object(cr, "ACTIVE", self.tmp / "state" / "active.json"), self.swap_after_the_read():
            runner.recover()
        self.assertEqual(json.loads((self.outside / "state.json").read_text())["state"], "running", "outside untouched")
        self.assertEqual(json.loads((self.ws / ".hub-run" / "jobs.real" / "victim" / "state.json").read_text())["state"],
                         "lost")


if __name__ == "__main__":
    unittest.main()
