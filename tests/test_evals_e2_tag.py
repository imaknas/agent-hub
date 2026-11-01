"""`e2.py run --tag` (bin/evals/e2.py; docs/design/i18n.md): the tag is appended to the arm's label, so the same arm run
by two versions of the code lands in one run and is scored as two arms. Pinned on a real frozen case, through e2's own
main(): the labels (`new~T`, `new~oldrules~T`), that a tagged arm renders and sees the snapshot as its base arm does,
the run's output folders, and the score report keeping the tagged arms apart. The model call is the only stand-in.

    bin/py -m unittest tests.test_evals_e2_tag
"""
import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "bin" / "evals"))
import e2  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
STATE = "# STATE\n\n- The job runs nightly.\n"
REAL_RUN = subprocess.run


class Tag(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        p = mock.patch.dict(os.environ, {**GIT_ENV, "AGENT_HUB_EVALS": str(self.tmp / "evals")})
        p.start()
        self.addCleanup(p.stop)
        bare, copy = self.tmp / "demo-memory.git", self.tmp / "copies" / "demo"
        g = lambda *a: REAL_RUN(["git", *a], check=True, capture_output=True)  # noqa: E731
        g("init", "-q", "--bare", "-b", "agent-memory", str(bare))
        g("clone", "-q", str(bare), str(copy))
        (copy / "memory").mkdir()
        (copy / "AGENT.md").write_text("snapshot rules\n")
        (copy / "STATE.md").write_text(STATE)
        (copy / "memory" / "2026-10-01.md").write_text("- one\n")
        g("-C", str(copy), "add", "-A"), g("-C", str(copy), "commit", "-qm", "init")
        (copy / "memory" / "2026-10-02.md").write_text("- two\n")
        g("-C", str(copy), "add", "-A"), g("-C", str(copy), "commit", "-qm", "agent-hub: sync memory/STATE.md")
        (copy / "STATE.md").write_text(STATE + "- It also runs weekly.\n")
        g("-C", str(copy), "add", "-A"), g("-C", str(copy), "commit", "-qm", "librarian: update STATE.md")
        g("-C", str(copy), "push", "-q", "origin", "HEAD")
        self.lib = e2.librarian()
        with mock.patch.object(e2.hubconf, "p", side_effect=lambda k, d=None: self.tmp / "copies" if k == "memory_copies"
                               else None), mock.patch.object(e2, "librarian", return_value=self.lib):
            self.case = e2.freeze_state("demo", None, night=1)
        self.calls = []

    def fake_run(self, argv, *a, **kw):
        """bin/llm.py is the stand-in (it records the prompt and the AGENT.md it sees, appends a line to STATE.md);
        everything else (git) is real"""
        if any(str(x).endswith("llm.py") for x in argv):
            cwd = pathlib.Path(kw["cwd"])
            prompt = pathlib.Path(argv[argv.index("--prompt-file") + 1]).read_text()
            self.calls.append({"prompt": prompt, "agent_md": (cwd / "AGENT.md").read_text()})
            (cwd / "STATE.md").write_text((cwd / "STATE.md").read_text() + "- Added by the model.\n")
            out = json.dumps({"is_error": False, "llm_model": "fake", "structured_output": {"state_updated": True}})
            return subprocess.CompletedProcess(argv, 0, out, "")
        return REAL_RUN(argv, *a, **kw)

    def main(self, *argv) -> tuple[int, str]:
        buf = io.StringIO()
        with mock.patch.object(e2.subprocess, "run", self.fake_run), mock.patch.object(e2, "run_env", return_value={}), \
                mock.patch.object(e2, "librarian", return_value=self.lib), contextlib.redirect_stdout(buf):
            rc = e2.main(list(argv))
        return rc, buf.getvalue()

    def test_tag_labels_the_arm_and_scores_apart(self):
        run = ("run", "librarian-state", "--k", "1", "--jobs", "1", "--run", "r1")
        rc, out = self.main(*run, "--arm", "new", "--tag", "before")
        self.assertEqual(rc, 0, out)
        self.assertIn("arm=new~before ", out)
        self.assertEqual(self.main(*run, "--arm", "new", "--tag", "after")[0], 0)
        rc, out = self.main(*run, "--arm", "new", "--rules", "old", "--tag", "x")
        self.assertIn("arm=new~oldrules~x ", out)
        self.assertEqual(self.main(*run, "--arm", "new")[0], 0)   # untagged: the plain label
        outs = self.tmp / "evals" / "runs" / "r1" / self.case.parent.name / self.case.name
        self.assertEqual(sorted(p.name for p in outs.iterdir()), ["new-0", "new~after-0", "new~before-0", "new~oldrules~x-0"])
        self.assertEqual(json.loads((outs / "new~before-0" / "meta.json").read_text())["arm"], "new~before")
        # a tagged arm renders and sees the snapshot exactly as its base arm does
        new = e2.prompt("librarian-state", self.case, "new")
        for arm in ("new~before", "new~after"):
            self.assertEqual((outs / f"{arm}-0" / "prompt.txt").read_text(), new)
        template = (e2.hubconf.ROOT / "templates" / "AGENT.md").read_text()
        self.assertEqual([c["agent_md"] for c in self.calls], [template, template, "snapshot rules\n", template],
                         "the tag never changes the rules: --rules old keeps the snapshot's AGENT.md")
        rc, out = self.main("score", "r1", "--json")
        self.assertEqual(rc, 0, out)
        report = json.loads(out)
        self.assertEqual(sorted(report["arms"]), ["new", "new~after", "new~before", "new~oldrules~x", "prod"])
        self.assertEqual(report["arms"]["new~before"]["runs"], 1)
        self.assertIn("new~after~new~before", report["pairs"], "the two versions are compared as a pair")

    def test_a_rerun_with_the_same_tag_reuses_its_results(self):
        run = ("run", "librarian-state", "--k", "1", "--jobs", "1", "--run", "r2", "--arm", "new", "--tag", "t")
        self.main(*run)
        self.main(*run)
        self.assertEqual(len(self.calls), 1, "the second run finds new~t-0/result.json and does not call again")

    @unittest.expectedFailure
    def test_tagged_old_arm_reads_the_recorded_prompt(self):
        """BUG (reported, not fixed here): prompt() checks `arm == "old"`, not base_arm(arm), so `--arm old --tag T`
        re-renders with the current code instead of sending the prompt production recorded"""
        recorded = (self.case / "prompt.librarian-state.txt").read_text()
        recorded = recorded + "<!-- as recorded -->\n"
        (self.case / "prompt.librarian-state.txt").write_text(recorded)
        self.assertEqual(e2.prompt("librarian-state", self.case, "old"), recorded)
        self.assertEqual(e2.prompt("librarian-state", self.case, "old~T"), recorded)


if __name__ == "__main__":
    unittest.main()
