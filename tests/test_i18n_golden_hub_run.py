"""E1 golden (docs/design/i18n.md) for bin/hub-run, on zh-Hant: its --help, the usage errors, and what it tells the
session for each job state (picked up late, queued, timed out, rejected, lost, not found).

In-process with a fake clock; no cap-runner runs (the job's state.json is written by the test).

    bin/py -m unittest tests.test_i18n_golden_hub_run
    I18N_GOLDEN_UPDATE=1 bin/py -m unittest tests.test_i18n_golden_hub_run    # regenerate (only before a conversion)
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
GOLDEN = ROOT / "tests" / "fixtures" / "i18n-golden"
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
ZH = {"AGENT_HUB_LANG_UI": "zh-Hant", "COLUMNS": "100"}


def load():
    loader = importlib.machinery.SourceFileLoader("hub_run_golden", str(ROOT / "bin" / "hub-run"))
    spec = importlib.util.spec_from_loader("hub_run_golden", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


class Golden(unittest.TestCase):
    maxDiff = None

    def run_main(self, hr, argv, ws, on_sleep=None, clock=None):
        """main() with argv; returns (exit code, stdout, stderr)."""
        out, err = io.TextIOWrapper(io.BytesIO(), encoding="utf-8"), io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        ticks = iter(clock or [0.0] * 1000)
        fake_time = types.SimpleNamespace(time=lambda: next(ticks), strftime=lambda fmt: "20261031-120000",
                                          sleep=lambda s: on_sleep and on_sleep())
        with mock.patch.object(sys, "argv", ["hub-run", *argv]), mock.patch.object(sys, "stdout", out), \
                mock.patch.object(sys, "stderr", err), mock.patch.object(hr, "find_workspace", lambda: ws), \
                mock.patch.object(hr, "time", fake_time), \
                mock.patch.object(hr, "secrets", types.SimpleNamespace(token_hex=lambda n: "abcdef")):
            try:
                code = hr.main()
            except SystemExit as e:
                code = e.code
            out.flush()
            err.flush()
        return code, out.buffer.getvalue().decode(), err.buffer.getvalue().decode()

    def scenario(self, tmp: pathlib.Path) -> str:
        hr = load()
        ws = tmp / "ws"
        ws.mkdir()
        jobs = ws / ".hub-run" / "jobs"
        res = []

        def job(job_id, state, out_text="", err_text=""):
            d = jobs / job_id
            d.mkdir(parents=True, exist_ok=True)
            (d / "stdout").write_text(out_text)
            (d / "stderr").write_text(err_text)
            (d / "state.json").write_text(json.dumps(state))

        def flip(job_id, state):
            return lambda: (jobs / job_id / "state.json").write_text(json.dumps(state))

        with mock.patch.dict(os.environ, ZH), mock.patch.object(pathlib.Path, "cwd", lambda: ws):
            res.append(("--help", self.run_main(hr, ["--help"], ws)))
            res.append(("not in a project", self.run_main(hr, ["--cap", "gpu", "--", "true"], None)))
            res.append(("usage: no --cap", self.run_main(hr, ["--", "true"], ws)))
            res.append(("usage: no command", self.run_main(hr, ["--cap", "gpu"], ws)))
            res.append(("wait: unknown id", self.run_main(hr, ["--wait", "nope"], ws)))
            res.append(("nobody picks it up", self.run_main(hr, ["--cap", "gpu", "--", "blender", "-b"], ws,
                                                            clock=[0.0, 0.0, 31.0])))
            job("q1", {"state": "queued"})
            res.append(("queued, then done", self.run_main(
                hr, ["--wait", "q1"], ws, on_sleep=flip("q1", {"state": "done", "exit": 3}))))
            job("t1", {"state": "done", "exit": 124, "timed_out": True}, "partial out\n", "partial err\n")
            res.append(("timed out", self.run_main(hr, ["--wait", "t1"], ws)))
            job("r1", {"state": "rejected", "reason": "cwd ../x"})
            res.append(("rejected", self.run_main(hr, ["--wait", "r1"], ws)))
            job("l1", {"state": "lost", "reason": "restart"})
            res.append(("lost with a reason", self.run_main(hr, ["--wait", "l1"], ws)))
            job("l2", {"state": "lost"})
            res.append(("lost without a reason", self.run_main(hr, ["--wait", "l2"], ws)))
        text = "".join(f"=== {k}\nexit {c}\n--- stdout\n{o}--- stderr\n{e}" for k, (c, o, e) in res)
        return text.replace(str(tmp), "<TMP>")

    def test_texts(self):
        with tempfile.TemporaryDirectory() as d:
            text = self.scenario(pathlib.Path(d).resolve())
        f = GOLDEN / "hub-run.txt"
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())


if __name__ == "__main__":
    sys.exit(unittest.main())
