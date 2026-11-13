"""The conformance runner (bin/conformance.py, docs/design/runtime-versions.md section 2): each check in its own process
in a throwaway world it proves it is in, a function check run once more on a fail and a safety check counted at once,
results that keep a state and a short reason, and nothing recorded from a linked worktree of the hub."""
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import conformance  # noqa: E402
import names  # noqa: E402
from runtime_ports import Check, Outcome  # noqa: E402


class Temp(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = pathlib.Path(d.name).resolve()


class Isolation(Temp):
    def child(self, env, check="nosuch", runtime="fake"):
        r = subprocess.run([sys.executable, str(BIN / "conformance.py"), "child", runtime, check, str(self.world.root)],
                           capture_output=True, text=True, env=env, timeout=60)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def setUp(self):
        super().setUp()
        self.world = conformance.World(self.tmp / "w").create()

    def test_a_child_in_its_world_gets_to_its_check(self):
        got = self.child(self.world.env())
        self.assertEqual(got["state"], "error")
        self.assertIn("nosuch", got["reason"], "past the isolation proof, to the check lookup")

    def test_a_child_outside_its_world_runs_nothing(self):
        for var in ("STATE", "SECRETS", "CONFIG", "DATA"):
            with self.subTest(left_out=var):
                env = names.env_unset(self.world.env(), var)
                got = self.child(env)
                self.assertEqual(got["state"], "error")
                self.assertNotIn("nosuch", got["reason"], "stopped before the check (in whichever language)")
                self.assertIn(var.lower(), got["reason"])
        env = {**self.world.env(), "HOME": str(self.tmp)}
        self.assertIn("home", self.child(env)["reason"])

    def test_the_world_env_carries_nothing_of_the_callers(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k", "CLAUDE_CODE_OAUTH_TOKEN": "t", "BRUNNA_X": "y"}):
            env = self.world.env()
        self.assertFalse({"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "BRUNNA_X"} & set(env))
        self.assertEqual(env["HOME"], str(self.world.home))

    def test_logs_follow_a_state_of_its_own(self):
        code = "import hubconf; print(hubconf.LOGS)"
        out = subprocess.run([sys.executable, "-c", code], cwd=BIN, env=self.world.env(), capture_output=True, text=True,
                             timeout=60).stdout.strip()
        self.assertEqual(out, str(self.world.state / "logs"))


class Runner(Temp):
    def setUp(self):
        super().setUp()
        self.exe = self.tmp / "claude"
        self.exe.write_text("#!/bin/sh\n")
        for p in (mock.patch.object(conformance, "RESULTS", self.tmp / "results"),
                  mock.patch("runtime_claude.version_of", lambda exe, env=None: "2.1.400"),
                  mock.patch.object(conformance.hubconf, "private_tmp", lambda: self.tmp)):
            p.start()
            self.addCleanup(p.stop)

    def run_with(self, entries, answers):
        """answers: check name → the outcomes its tries give, in order."""
        seen = {k: list(v) for k, v in answers.items()}

        def one(runtime, check, exe, top):
            if check.run is None:
                return Outcome("not-checkable", check.measurement)
            return seen[check.name].pop(0)
        with mock.patch.object(conformance, "checks", lambda runtime: entries), \
                mock.patch.object(conformance, "run_one", one):
            return conformance.run("claude", str(self.exe))

    def test_tries_and_verdicts(self):
        f = lambda w: None   # noqa: E731
        entries = [Check("a", "agent", "function", f), Check("b", "confine", "safety", f),
                   Check("c", "completion", "function", f), Check("d", "wake", "function", None, "V-M6")]
        doc = self.run_with(entries, {"a": [Outcome("fail", "x"), Outcome("pass")], "b": [Outcome("pass")],
                                      "c": [Outcome("fail", "x"), Outcome("fail", "y")]})
        c = doc["checks"]
        self.assertEqual((c["a"]["state"], c["a"]["tries"]), ("pass", 2), "a function check runs once more")
        self.assertEqual((c["c"]["state"], c["c"]["reason"], c["c"]["tries"]), ("fail", "y", 2))
        self.assertEqual((c["d"]["state"], c["d"]["reason"]), ("not-checkable", "V-M6"))
        self.assertEqual((doc["version"], doc["runtime"]), ("2.1.400", "claude"))
        self.assertEqual(len(doc["digest"]), 64)
        self.assertEqual(conformance.verdict(doc), "fail")
        doc = self.run_with(entries, {"a": [Outcome("pass")], "b": [Outcome("fail", "leaked")], "c": [Outcome("pass")]})
        self.assertEqual(doc["checks"]["b"]["tries"], 1, "a safety check counts at once")
        self.assertEqual(conformance.verdict(doc), "fail")
        doc = self.run_with(entries, {"a": [Outcome("pass")], "b": [Outcome("pass")], "c": [Outcome("pass")]})
        self.assertEqual(conformance.verdict(doc), "pass", "not-checkable doesn't hold a pass back")

    def test_record_and_read_back(self):
        doc = self.run_with([Check("a", "agent", "function", lambda w: None)], {"a": [Outcome("pass")]})
        f = conformance.record(doc)
        self.assertEqual(f, self.tmp / "results" / "claude" / "2.1.400.json")
        self.assertEqual(conformance.recorded("claude")[0]["checks"], doc["checks"])
        self.assertEqual(conformance.recorded("nothing"), [])

    def test_refusals(self):
        with self.assertRaises(RuntimeError):
            conformance.run("fake")   # no distribution port
        with mock.patch.object(conformance, "checks", lambda r: []), self.assertRaises(RuntimeError):
            conformance.run("claude", str(self.exe), ["nosuch"])
        with mock.patch("runtime_claude.version_of", lambda exe, env=None: None), self.assertRaises(RuntimeError):
            conformance.run("claude", str(self.exe))

    def test_a_reason_is_short_and_one_line(self):
        self.assertEqual(conformance._short("a\nb   c"), "a b c")
        self.assertEqual(len(conformance._short("x" * 5000)), conformance.REASON_MAX)


class Arguments(Temp):
    def main(self, *argv, worktree=False):
        err, out = io.StringIO(), io.StringIO()
        with mock.patch.object(conformance.hubconf, "worktree_lookup", lambda: worktree), \
                mock.patch.object(conformance, "run", lambda *a: {"runtime": "claude", "version": "1", "binary": "b",
                                                                  "at": "t", "checks": {}}), \
                mock.patch.object(conformance, "record", lambda doc: self.tmp / "r.json"), \
                redirect_stderr(err), redirect_stdout(out):
            return conformance.main(list(argv)), out.getvalue(), err.getvalue()

    def test_help_and_unknown(self):
        self.assertEqual(self.main("--help")[0], 0)
        for bad in ([], ["run"], ["go", "claude"], ["run", "claude", "--nope"], ["run", "claude", "--only"]):
            self.assertEqual(self.main(*bad)[0], 2, bad)

    def test_a_linked_worktree_records_nothing(self):
        code, _out, err = self.main("run", "claude", worktree=True)
        self.assertEqual(code, 1)
        self.assertIn("linked worktree", err)
        code, out, _err = self.main("run", "claude", "--report", worktree=True)
        self.assertEqual(code, 0)
        self.assertNotIn("r.json", out)
        code, out, _err = self.main("run", "claude")
        self.assertIn("r.json", out)


class Runtimes(Temp):
    """`brunna runtimes` (bin/runtimes.py): reads only, and its check is the runner's."""

    def test_arguments_and_an_unversioned_runtime(self):
        import runtimes
        with redirect_stdout(io.StringIO()):
            self.assertEqual(runtimes.main(["--help"]), 0)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(runtimes.main(["nope"]), 2)
        self.assertEqual(len(runtimes.show_runtime("acp")), 1, "unversioned: one line, nothing to check")
        seen = []
        with mock.patch.object(runtimes.conformance, "main", lambda argv: seen.append(argv) or 0):
            self.assertEqual(runtimes.main(["check", "claude", "--report"]), 0)
        self.assertEqual(seen, [["run", "claude", "--report"]])

    def test_a_versioned_runtime_with_and_without_results(self):
        import runtimes
        with mock.patch.object(conformance, "RESULTS", self.tmp / "results"), \
                mock.patch("runtime_claude.binary", lambda: "/bin/sh"), \
                mock.patch("runtime_claude.version_of", lambda exe, env=None: "2.1.400"):
            lines = runtimes.show_runtime("claude")
            self.assertEqual(len(lines), 2, lines)
            conformance.record({"runtime": "claude", "version": "2.1.400", "binary": "/bin/sh", "digest": "x", "at": "t",
                                "checks": {"a": {"port": "agent", "kind": "function", "state": "pass", "reason": "",
                                                 "tries": 1}}})
            lines = runtimes.show_runtime("claude")
        self.assertIn("2.1.400", lines[1])
        self.assertIn("pass 1", lines[1])


if __name__ == "__main__":
    unittest.main()
