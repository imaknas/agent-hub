"""E1 golden (docs/design/i18n.md) for bin/cap-runner.py, on zh-Hant: every reason a request is rejected or lost
(state.json, which bin/hub-run prints to the session) and the details in .state/cap-runner/log.jsonl.

In-process, in a temp workspace; nothing is run under sandbox-exec (starting a job fails on purpose).

    bin/py -m unittest tests.test_i18n_golden_cap_runner
"""
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_lang  # noqa: E402

GOLDEN = golden_lang.GOLDEN
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
ZH = {"AGENT_HUB_LANG_UI": golden_lang.LANG}


class Golden(unittest.TestCase):
    maxDiff = None

    def scenario(self, tmp: pathlib.Path) -> str:
        spec = importlib.util.spec_from_file_location("cap_runner_golden", ROOT / "bin" / "cap-runner.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        ws = tmp / "ws"
        (ws / "sub").mkdir(parents=True)
        (ws / "f.txt").write_text("x")
        caps = ["gpu"]
        out = []
        good = {"v": 1, "id": "j", "cap": "gpu", "argv": ["true"]}
        checks = [
            ("not an object", []),
            ("wrong v", {"v": 2}),
            ("id mismatch", {**good, "id": "other"}),
            ("undeclared cap", {**good, "cap": "net"}),
            ("argv empty", {**good, "argv": []}),
            ("cwd absolute", {**good, "cwd": "/etc"}),
            ("cwd outside", {**good, "cwd": "../x"}),
            ("cwd not a dir", {**good, "cwd": "f.txt"}),
            ("timeout too long", {**good, "timeout_s": 99999}),
            ("ok", {**good, "cwd": "sub"}),
        ]
        with mock.patch.dict(os.environ, ZH), mock.patch.object(m, "DIR", tmp / "state"), \
                mock.patch.object(m, "LOG", tmp / "state" / "log.jsonl"), \
                mock.patch.object(m, "conf", lambda key, default: default):
            for name, req in checks:
                reason, clean = m.check(req, "j", ws, caps)
                out.append((f"check: {name}", reason or "(passes)"))
            reqs = ws / ".hub-run" / "requests"
            reqs.mkdir(parents=True)
            (ws / ".hub-run" / "jobs" / "dup").mkdir(parents=True)
            files = {"bad name.json": "{}", "dup.json": json.dumps({**good, "id": "dup"}), "broken.json": "not json",
                     "stale.json": json.dumps({**good, "id": "stale"}), "wrongv.json": json.dumps({"v": 2})}
            for n, body in files.items():
                (reqs / n).write_text(body)
            old = time.time() - 3600
            os.utime(reqs / "stale.json", (old, old))
            r = m.Runner()
            hr_fd = os.open(ws / ".hub-run", m.DIRFLAGS)
            req_fd = os.open(reqs, m.DIRFLAGS)
            try:
                for n in files:
                    r.take("demo", ws.resolve(), caps, hr_fd, req_fd, n)
            finally:
                os.close(req_fd)
                os.close(hr_fd)
            jobs = ws / ".hub-run" / "jobs"
            for j in ("broken", "stale", "wrongv"):
                out.append((f"take: {j}", json.loads((jobs / j / "state.json").read_text()).get("reason")))
            (jobs / "boom").mkdir()
            job = m.Job("demo", "boom", os.open(jobs / "boom", m.DIRFLAGS), {"cap": "gpu", "argv": ["true"], "cwd": ws})
            with mock.patch.object(m, "make_profile", side_effect=RuntimeError("boom")):
                r.start(job)
            out.append(("start fails", json.loads((jobs / "boom" / "state.json").read_text()).get("reason")))
            (jobs / "left").mkdir()
            (jobs / "left" / "state.json").write_text(json.dumps({"state": "running"}))
            with mock.patch.object(m, "eligible", lambda: {"demo": {"dirs": [ws], "caps": caps, "models": {}}}):
                r.recover()
            out.append(("recover", json.loads((jobs / "left" / "state.json").read_text()).get("reason")))
            log = [json.loads(line) for line in (tmp / "state" / "log.jsonl").read_text().splitlines()]
            out.append(("log", "\n".join(json.dumps({k: v for k, v in rec.items() if k != "at"}, ensure_ascii=False)
                                         for rec in log)))
        text = "\n".join(f"=== {k}\n{v}" for k, v in out) + "\n"
        return text.replace(str(tmp), "<TMP>")

    def test_texts(self):
        with tempfile.TemporaryDirectory() as d:
            text = self.scenario(pathlib.Path(d).resolve())
        f = GOLDEN / "cap-runner.txt"
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())


if __name__ == "__main__":
    sys.exit(unittest.main())
