"""The E2/E3 harnesses (bin/evals/e2.py, e3.py; docs/design/i18n.md): scorers, a frozen case re-renders to the prompt
production sent, E3's throwaway project and checks. No model calls.

    bin/py -m unittest tests.test_evals_e2
"""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "bin" / "evals"))
import e2  # noqa: E402
import e3  # noqa: E402

IN = json.loads((ROOT / "tests" / "fixtures" / "evals-e2.json").read_text())
IN["state"] = IN["state"].replace("{owner}", e2._load("gen_todo_t", "gen-todo.py").TAG)   # whatever owner tag this env has


class Scorers(unittest.TestCase):
    def test_language(self):
        zh = e2.language(IN["zh_text"])
        self.assertGreater(zh["cjk_share"], 0.8)
        self.assertEqual(zh["simplified"], [])
        self.assertEqual(zh["full_punct"], 1.0)
        self.assertEqual(e2.language(IN["simplified_text"])["simplified"], sorted(set(IN["simplified_expected"])))
        en = e2.language("Plain English, with a comma.")
        self.assertEqual((en["cjk_share"], en["full_punct"]), (0.0, None))
        self.assertEqual(e2.language(IN["half_punct_text"])["full_punct"], 0.0)

    def test_script_lists_are_disjoint(self):
        self.assertFalse(e2.FULL_PUNCT & e2.HALF_PUNCT)
        self.assertFalse(e2.SIMPLIFIED & set(IN["traditional_shared"]), "characters both scripts use are not evidence")

    def test_match(self):
        a = {("agent", IN["item_a"]), ("x", IN["item_b"])}
        b = {("agent", IN["item_a_reworded"]), ("tbd", IN["item_b"])}
        m = e2.match(a, b)
        self.assertEqual(m, {"items": 1.0, "owner": 0.5})
        self.assertEqual(e2.match(set(), set()), {"items": 1.0, "owner": 1.0})
        self.assertIsNone(e2.match(None, a))
        self.assertEqual(e2.match({("agent", "alpha beta gamma")}, {("agent", "something else entirely")})["items"], 0.0)

    def test_todo_items(self):
        items = e2.todo_items(IN["state"])
        self.assertEqual(len(items), 3)
        self.assertTrue(all("task-" not in t for _, t in items), "the registrar's key marker is not part of the item")
        self.assertIsNone(e2.todo_items("# no to-do section\n"))

    def test_added_text(self):
        self.assertEqual(e2.added_text("a\nb\nc\n", "a\nB\nc\nd\n"), "B\nd")

    def test_boot(self):
        lo, hi = e2.boot([0.0, 1.0] * 10)
        self.assertLess(lo, 0.5)
        self.assertGreater(hi, 0.5)
        self.assertIsNone(e2.boot([1.0]))


class FrozenCase(unittest.TestCase):
    """freeze from a memory repo's history, then: the new arm re-renders byte-identically, the edits outcome applies"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
               "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        self.patch = mock.patch.dict(os.environ, {**env, "AGENT_HUB_EVALS": str(self.tmp / "evals")})
        self.patch.start()
        bare, copy = self.tmp / "demo-memory.git", self.tmp / "copies" / "demo"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "agent-memory", str(bare)], check=True)
        subprocess.run(["git", "clone", "-q", str(bare), str(copy)], check=True, capture_output=True)
        g = lambda *a: subprocess.run(["git", "-C", str(copy), *a], check=True, capture_output=True)  # noqa: E731
        (copy / "memory").mkdir()
        (copy / "AGENT.md").write_text("agent\n")
        (copy / "STATE.md").write_text(IN["state"])
        (copy / "memory" / "2026-10-01.md").write_text("- one\n")
        g("add", "-A"), g("commit", "-qm", "init")
        (copy / "memory" / "2026-10-02.md").write_text(IN["note"])
        g("add", "-A"), g("commit", "-qm", "agent-hub: sync memory/STATE.md")
        (copy / "STATE.md").write_text(IN["state"].replace(IN["item_a"], IN["item_a_reworded"]))
        g("add", "-A"), g("commit", "-qm", "librarian: update STATE.md")
        g("push", "-q", "origin", "HEAD")
        self.copy = copy

    def tearDown(self):
        self.patch.stop()

    def test_freeze_and_render(self):
        lib = e2.librarian()
        with mock.patch.object(e2.hubconf, "p", side_effect=lambda k, d=None: self.tmp / "copies" if k == "memory_copies"
                               else None), mock.patch.object(e2, "librarian", return_value=lib):
            case = e2.freeze_state("demo", None, night=1)
        args = json.loads((case / "args.json").read_text())
        self.assertIn("memory/2026-10-02.md", args["changed"])
        self.assertIn(IN["item_a_reworded"], (case / "after.STATE.md").read_text(), "that night's outcome is kept")
        self.assertIn(IN["item_a"], (case / "snapshot" / "STATE.md").read_text(), "the input is the commit before it")
        for point in ("librarian-state", "librarian-state-edits"):
            self.assertEqual(e2.prompt(point, case, "new"), e2.prompt(point, case, "old"), point)
        out = self.tmp / "run" / "old-0"
        out.mkdir(parents=True)
        so = {"state_updated": True, "edits": [{"old": IN["item_b"], "new": IN["item_b"] + "!"}], "worth_digesting": False,
              "summary": "", "reason": "r"}
        (out / "result.json").write_text(json.dumps({"is_error": False, "structured_output": so}))
        r = e2.outcome("librarian-state-edits", case, out)
        self.assertTrue(r["valid"] and r["changed"] and r["contract_ok"])
        (out / "result.json").write_text(json.dumps({"is_error": False, "structured_output": {
            **so, "edits": [{"old": "not there", "new": "x"}]}}))
        self.assertFalse(e2.outcome("librarian-state-edits", case, out)["valid"])


class E3(unittest.TestCase):
    def test_project_has_the_rules(self):
        with tempfile.TemporaryDirectory() as d:
            proj = pathlib.Path(d) / "rates"
            e3.build(proj, "new", "note")
            rules = e3.runtime_ports.load().instructions_file(proj).read_text()
            self.assertTrue(rules.startswith("<!-- agent-hub:begin -->"))
            self.assertNotIn("{{owner}}", rules, "names are filled in")
            for f in (".agent-memory/AGENT.md", ".agent-memory/STATE.md", "README.md"):
                self.assertTrue((proj / f).exists(), f)
            log = subprocess.run(["git", "-C", str(proj), "log", "--format=%s"], capture_output=True, text=True).stdout
            self.assertEqual(log.split(), ["chore:", "initial", "commit"])

    @unittest.skipUnless(subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{e3.OLD_REF}^{{commit}}"],
                                        capture_output=True).returncode == 0, "no git history (the commit gate's copy)")
    def test_old_arm_is_the_pinned_templates(self):
        self.assertIn("agent-hub:begin", e3.managed_block("old"))

    def test_agent_is_sealed_from_mcp(self):
        """the command E3 runs must carry the flag that keeps the environment's MCP servers out (same as run-agent-job.sh)"""
        cmd = e3.llm_cmd(pathlib.Path("p"), "--dry-run")
        self.assertIn("--strict-mcp-config", cmd[cmd.index("--"):], "passed through to the agent, after --")
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "prompt.txt"
            p.write_text("x")
            r = subprocess.run(e3.llm_cmd(p, "--dry-run"), capture_output=True, text=True, timeout=60)
        if r.returncode != 0 and not r.stdout.startswith("["):
            self.skipTest("no coding agent installed here (the commit gate's empty HOME)")
        self.assertIn("--strict-mcp-config", json.loads(r.stdout))

    def test_letter_check_reads_the_run_bus(self):
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d)
            (out / "meta.json").write_text(json.dumps({"exit": 0}))
            files = {"before": {}, "after": {}, "git_log": ["chore: initial commit"], "letter": "m10301200-abcd"}
            (out / "files.json").write_text(json.dumps(files))
            (out / "bus").mkdir()
            c = sqlite3.connect(out / "bus" / "bus.db")
            c.execute("CREATE TABLE events(seq INTEGER PRIMARY KEY, type, source, to_project, ref, payload)")
            fact = e3.data()["scenarios"]["letter"]["expect"][0]
            c.execute("INSERT INTO events(type, source, to_project, ref, payload) VALUES('message', 'rates', 'dashboard', "
                      "'m10301200-abcd', ?)", (json.dumps({"type": "info", "text": f"limit {fact}/min"}),))
            c.execute("INSERT INTO events(type, source, to_project, ref, payload) VALUES('message.done', 'rates', 'rates', "
                      "'m10301200-abcd', '{}')")
            c.commit(), c.close()
            r = e3.check("letter", out)
            self.assertEqual((r["replied"], r["reply_has_fact"], r["closed"]), (True, True, True))


if __name__ == "__main__":
    unittest.main()
