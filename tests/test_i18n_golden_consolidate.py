"""E1 golden (docs/design/i18n.md) for bin/consolidate-memory.py on zh-Hant: --list, --help, the per-project log lines of
each outcome (model failed, no output, HEAD moved, stray files, nothing left, unexpected error, dry run with every
mechanical warning, push and checkpoint, push failed), the review-log entries, run_model's errors and main's summary lines.

Runs in-process on temp bare repos and memory copies; the model is a function that edits the clone the way a model
would. Shell helpers come from a temp bin (the real git-safe-push.sh / with-lock.sh, a fake commit-hub-files.sh).

    bin/py -m unittest tests.test_i18n_golden_consolidate
    I18N_GOLDEN_UPDATE=1 bin/py -m unittest tests.test_i18n_golden_consolidate    # rewrite (only on purpose)
"""
import contextlib
import datetime
import importlib.util
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "i18n-golden"
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
DAY = datetime.date(2026, 10, 15)   # a day that is never today again: TODAY in the code vs commit dates
GIT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
       "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}

RAW = {
    "memory/2026-09-01.md": "- Deploy notes: https://example.com/doc and `make deploy`\n- The cache is probably redis\n",
    "memory/2026-09-02-topic.md": "- Topic notes, nothing hedged here\n",
    "memory/2026-09-03.md": "- Third note\n",
    "memory/2026-09-04.md": "- Fourth note\n",
    "memory/2026-09-05.md": "- Fifth note\n",
    "memory/2026-10-29.md": "- The cache is redis, confirmed on the box\n",
}
BASE = {**RAW, "AGENT.md": "agent\n", "STATE.md": "# STATE\n\n## To-do\n- (none)\n",
        "memory/reference/README.md": "# reference\n- infra.md\n",
        "memory/reference/infra.md": "# infra\n- The database migration is probably done by Friday night\n"}


def sh(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, env={**os.environ, **GIT}, check=True, capture_output=True, text=True).stdout


class Consolidate(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        for d in ("bare", "copies", "hub/.state", "hub/.logs", "bin", "data"):
            (self.tmp / d).mkdir(parents=True)
        for f in ("git-safe-push.sh", "with-lock.sh", "check-state-todo.py"):
            (self.tmp / "bin" / f).symlink_to(ROOT / "bin" / f)
        self.commit_ok(True)
        with mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "zh-Hant"}):
            spec = importlib.util.spec_from_file_location("consolidate_golden", ROOT / "bin" / "consolidate-memory.py")
            self.m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.m)
        m = self.m
        # its own config: hubconf caches hub.json per process, so the owner tag in the prompt must not depend on which
        # config (the real instance's, another test's) was read first
        cfg = mock.patch.object(m.hubconf, "_cfg", {"owner": {"tag": "Alex", "address": "owner"}})
        cfg.start()
        self.addCleanup(cfg.stop)
        m.HUB, m.COPIES, m.BIN, m.TODAY = self.tmp / "hub", self.tmp / "copies", self.tmp / "bin", DAY
        m.STATE_DIR, m.LOG_DIR = self.tmp / "hub" / ".state", self.tmp / "hub" / ".logs"
        m.WORK_STATE = m.STATE_DIR / "consolidate" / "state.json"
        m.REVIEW_LOG = self.tmp / "data" / "review-log.md"
        m.GIT_ENV = {**m.GIT_ENV, **GIT}
        m._log_file = None

    def commit_ok(self, ok: bool):
        f = self.tmp / "bin" / "commit-hub-files.sh"
        f.write_text("#!/bin/sh\nexit 0\n" if ok else "#!/bin/sh\necho 'nothing to push'\nexit 1\n")
        f.chmod(0o755)

    def project(self, name, files=BASE, branch="agent-memory"):
        bare = self.tmp / "bare" / f"{name}-memory.git"
        sh("git", "init", "-q", "--bare", "-b", branch, str(bare))
        work = self.tmp / "seed" / name
        sh("git", "clone", "-q", str(bare), str(work))
        for rel, text in files.items():
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / rel).write_text(text)
        sh("git", "add", "-A", cwd=work)
        sh("git", "commit", "-qm", "init", cwd=work)
        sh("git", "push", "-q", "origin", "HEAD", cwd=work)
        sh("git", "clone", "-q", str(bare), str(self.tmp / "copies" / name))
        return bare

    def survey(self, name):
        return self.m.survey(name, str(self.tmp / "bare" / f"{name}-memory.git"))

    def capture(self, fn, *a, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "zh-Hant", "COLUMNS": "100"}):
            try:
                ret = fn(*a, **kw)
            except SystemExit as e:
                ret = f"SystemExit({e.code})"
        return ret, buf.getvalue()

    # ---- fake models ----

    def rich_model(self, prompt, repo, model):
        """every mechanical check fires once"""
        self.prompts.append(prompt)
        ref = repo / "memory" / "reference"
        (ref / "infra.md").write_text("# infra\n- The database migration is done by Friday night\n")
        (ref / "notes.md").write_text("# notes\n- Deploy is documented (see memory/2026-09-01.md)\n- The cache is redis\n")
        (repo / "STATE.md").write_text("# STATE\n\nNow done.\n\n## To-do\n- (none)\n")
        return {"num_turns": 3, "total_cost_usd": 0.5, "permission_denials": [{}], "structured_output": {
            "summary": "folded the September notes",
            "folded": [{"file": "memory/2026-09-01.md", "destinations": ["memory/reference/notes.md"], "dropped": "hashes"},
                       {"file": "memory/2099-01-01.md", "destinations": []},
                       {"file": "memory/2026-09-02-topic.md", "destinations": ["memory/reference/infra.md"]},
                       {"file": "memory/2026-09-03.md", "destinations": ["memory/reference/missing.md"]},
                       {"file": "memory/2026-09-04.md", "destinations": [], "dropped": ""}],
            "kept": [{"file": "memory/2026-09-05.md", "reason": "not sure yet"}],
            "corrections": [{"file": "memory/reference/notes.md", "before": "probably redis", "after": "redis",
                             "evidence_file": "memory/2026-10-29.md", "evidence_quote": "The cache is redis, confirmed"},
                            {"file": "STATE.md", "before": "nothing", "after": "Now done.",
                             "evidence_file": "memory/2026-10-29.md", "evidence_quote": "a quote that is not there"}]}}, ""

    def push_model(self, prompt, repo, model):
        (repo / "memory" / "reference" / "notes.md").write_text("# notes\n- Deploy: https://example.com/doc `make deploy`\n")
        (repo / "memory" / "reference" / "README.md").write_text("# reference\n- infra.md\n- notes.md\n")
        (repo / "STATE.md").write_text("# STATE\n\nbroken to-do\n")
        return {"num_turns": 1, "total_cost_usd": 0.1, "structured_output": {
            "summary": "", "folded": [{"file": "memory/2026-09-01.md", "destinations": ["memory/reference/notes.md"]}],
            "kept": [], "corrections": []}}, ""

    def scenario(self) -> str:
        out = []
        m = self.m
        self.prompts = []
        # --- candidates and --list
        self.project("alpha")
        self.project("beta", {"memory/2026-10-29.md": "- new\n", "STATE.md": "s\n"})
        self.project("gamma")
        self.project("delta")
        self.project("zeta", {"STATE.md": "s\n"}, branch="main")
        sh("git", "init", "-q", str(self.tmp / "copies" / "epsilon"))
        sh("git", "-C", str(self.tmp / "copies" / "epsilon"), "remote", "add", "origin", "https://example.com/e.git")
        m.save_state({"gamma": {"last_ok": "2026-10-12", "last_try": "2026-10-12"},
                      "delta": {"last_ok": "2026-10-01", "last_try": "2026-10-14"}})
        with mock.patch.object(sys, "argv", ["consolidate-memory.py", "--list"]):
            out.append(("--list", "exit {}\n{}".format(*self.capture(m.main))))
        with mock.patch.object(sys, "argv", ["consolidate-memory.py", "--help"]):
            out.append(("--help", "exit {}\n{}".format(*self.capture(m.main))))

        # --- one project, each outcome (log goes to stdout: no log file outside main)
        def run(label, model, dry_run=False, keep=False, **patches):
            with mock.patch.object(m, "run_model", side_effect=model), \
                    contextlib.ExitStack() as st:
                for k, v in patches.items():
                    st.enter_context(mock.patch.object(m, k, v))
                r, printed = self.capture(m.consolidate, self.survey("alpha"), "test-model", dry_run, keep)
            out.append((label, f"status {r['status']}\n{printed}" + (f"--- entry\n{r['entry']}" if r["entry"] else "")))

        run("model failed", lambda p, repo, mdl: (None, "exit 1:boom"))
        run("no structured output", lambda p, repo, mdl: ({"result": "gave up", "num_turns": 2}, ""))

        def commits(p, repo, mdl):
            (repo / "STATE.md").write_text("x\n")
            sh("git", "commit", "-qam", "model commit", cwd=repo)
            return {"structured_output": {"folded": []}}, ""
        run("HEAD moved", commits)

        def stray(p, repo, mdl):
            (repo / "memory" / "2026-10-29.md").write_text("rewritten\n")
            (repo / "AGENT.md").unlink()
            return {"structured_output": {"folded": []}}, ""
        run("stray files", stray)
        run("nothing to commit", lambda p, repo, mdl: ({"structured_output": {
            "folded": [{"file": "memory/2099-01-01.md", "destinations": []}]}}, ""))

        def boom(p, repo, mdl):
            raise ValueError("model exploded")
        run("unexpected error", boom)
        run("dry run, every warning", self.rich_model, dry_run=True, keep=True)
        out.append(("prompt", self.prompts[0]))
        ckpt = m.STATE_DIR / "alpha.sha"
        ckpt.write_text(sh("git", "-C", str(self.tmp / "copies" / "alpha"), "rev-parse", "HEAD"))
        run("push, checkpoint", self.push_model, state_todo_broken=lambda repo, base: True)
        failing = self.tmp / "bin" / "git-safe-push.sh"
        failing.unlink()
        failing.write_text("#!/bin/sh\necho 'git-safe-push: rejected' >&2\nexit 1\n")
        failing.chmod(0o755)
        run("push failed", self.push_model, state_todo_broken=lambda repo, base: False)
        failing.unlink()
        failing.symlink_to(ROOT / "bin" / "git-safe-push.sh")

        # --- run_model's own errors
        def fake_run(result):
            def r(cmd, **kw):
                if isinstance(result, BaseException):
                    raise result
                return result
            return r
        for label, res in (("timeout", subprocess.TimeoutExpired("llm.py", 1)),
                           ("exit", subprocess.CompletedProcess([], 2, "", "bad things\n")),
                           ("not json", subprocess.CompletedProcess([], 0, "plain text", ""))):
            with mock.patch.object(m.subprocess, "run", side_effect=fake_run(res)):
                out.append((f"run_model {label}", repr(self.capture(m.run_model, "p", self.tmp, "x")[0])))

        # --- main: dry run of one project, a skipped --project, then two due projects with the time budget hit
        llm = mock.Mock(model_for=lambda point: "test-model")
        with mock.patch.dict(sys.modules, {"llm": llm}), mock.patch.object(m, "run_model", side_effect=self.push_model), \
                mock.patch.object(m, "state_todo_broken", lambda repo, base: False):
            for argv in (["--dry-run", "--project", "alpha"], ["--project", "beta"]):
                with mock.patch.object(sys, "argv", ["consolidate-memory.py", *argv]):
                    ret, printed = self.capture(m.main)
                out.append((" ".join(argv), f"exit {ret}\n{printed}"))
            self.project("eta")
            ticks = iter([0, 0, 10 ** 6])
            with mock.patch.object(sys, "argv", ["consolidate-memory.py"]), \
                    mock.patch.object(m.time, "monotonic", side_effect=lambda: next(ticks)):
                ret, printed = self.capture(m.main)
            out.append(("scheduled run", f"exit {ret}\n{printed}"))
            self.commit_ok(False)
            self.project("theta")
            with mock.patch.object(sys, "argv", ["consolidate-memory.py", "--project", "theta"]):
                ret, printed = self.capture(m.main)
            out.append(("commit failed", f"exit {ret}\n{printed}"))
        out.append(("log file", (m.LOG_DIR / f"consolidate-memory-{DAY}.log").read_text()))
        out.append(("review-log.md", m.REVIEW_LOG.read_text()))
        out.append(("state.json", json.dumps(m.load_state(), indent=1, sort_keys=True)))

        text = "\n".join(f"=== {k}\n{v}" for k, v in out) + "\n"
        text = text.replace(str(self.tmp), "<TMP>").replace(datetime.date.today().isoformat(), "<REAL-TODAY>")
        text = re.sub(r"(?<![\w>])/\S*?/consolidate-(\w+)-\w{8}(?:/repo)?", r"<CLONE-\1>", text)
        text = re.sub(r"fatal: [^\n']*", "fatal: <git error>", text)
        text = re.sub(r"\[\d\d:\d\d:\d\d\]", "[<T>]", text)
        return re.sub(r"\b[0-9a-f]{7,40}\b", "<sha>", text)

    def test_texts(self):
        text = self.scenario()
        f = GOLDEN / "consolidate-memory.txt"
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())


if __name__ == "__main__":
    sys.exit(unittest.main())
