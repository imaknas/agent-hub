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
import prompts  # noqa: E402

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
        # old = the recorded prompt; new = the English template, content zh-Hant; new-en = content en (with the English
        # to-do tokens); --rules new swaps in templates/AGENT.md, ~oldrules keeps the snapshot's
        agent_md = (case / "snapshot" / "AGENT.md").read_text()
        template = (e2.hubconf.ROOT / "templates" / "AGENT.md").read_text()
        for point in ("librarian-state", "librarian-state-edits"):
            old, new, new_en = (e2.prompt(point, case, a) for a in ("old", "new", "new-en"))
            self.assertEqual(old, (case / f"prompt.{point}.txt").read_text())
            self.assertIn("Write in Traditional Chinese (zh-Hant)", new)
            self.assertIn("Write in English", new_en)
            self.assertIn(prompts.TOKENS["zh-Hant"]["todo_heading"], new)
            self.assertIn(f"`{prompts.TOKENS['en']['todo_heading']}` section", new_en)   # (the case's STATE.md is zh)
            for p in (new, new_en):
                self.assertNotIn("{{", p, "every placeholder filled")
        edits_new = e2.prompt("librarian-state-edits", case, "new")
        edits_oldrules = e2.prompt("librarian-state-edits", case, "new~oldrules")
        self.assertIn(template.strip()[:40], edits_new)
        self.assertIn(agent_md.strip()[:40], edits_oldrules)
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


class Plugins(unittest.TestCase):
    """the points with their own E2 module (e2_reflect, e2_propose, e2_handover): the new arm renders a complete prompt,
    the scorers read a run as production returns it"""

    STATS = {"week": "2026-W40", "totals": {"prompts": 9, "prompts_prev": 4, "sessions": 2, "projects": 1, "days": 2},
             "projects": [{"name": "demo", "prompts": 9, "prompts_prev": 4, "sessions": 2, "days": 2, "commits": 3,
                           "notes": 1}],
             "rhythm": {"late": 1, "late_share": 0.11, "late_prev": 0, "longest_hours": 1.5,
                        "last_per_day": [{"day": "10/01", "time": "23:10"}]},
             "delegation": {"agents": 0, "agents_prev": 1, "peer": 2, "peer_prev": 0, "skills": {}},
             "changes": {"new": ["demo"], "quiet": []}}

    def test_reflect_prompt(self):
        rl = e2._load("reflect_llm_t", "reflect_llm.py")
        d = {"sessions": [{"date": "10-01", "project": "demo", "summary": "a summary", "themes": ["cache"], "user_turns": 3}],
             "withheld": 2, "complete": False, "undigested_sessions": 1}
        for lang, name in (("zh-Hant", "Traditional Chinese (zh-Hant)"), ("en", "English")):
            p = rl.build_prompt(self.STATS, d, lang=lang, owner="Alex")
            self.assertIn(f"in {name}", p)
            self.assertIn("- demo: messages 9 (last week 4)", p)
            self.assertIn("[demo] a summary | themes: cache | 3 messages", p, "the summaries are data, kept as they are")
            self.assertIn(" 2 more work-related sessions", p)
            self.assertIn(" 1 sessions were not summarized", p)
            self.assertNotIn("{{", p)
        self.assertIn(rl.NO_DIGEST_INTRO, rl.build_prompt(self.STATS, None, lang="zh-Hant", owner="Alex"))
        import e2_reflect
        props = json.loads((ROOT / "contracts" / "llm" / "reflect-week.schema.json").read_text())["properties"]
        status = props["projects"]["items"]["properties"]["status"]["enum"]   # the enum values are tokens, kept
        level = props["fluency"]["items"]["properties"]["level"]["enum"]
        a = {"valid": True, "status": {"demo": status[0], "x": status[1]}, "level": {"d": level[0]}, "text": "t"}
        b = {"valid": True, "status": {"demo": status[0]}, "level": {"d": level[1]}, "text": "t"}
        self.assertEqual(e2_reflect.agree(a, b), {"projects": 0.5, "status": 1.0, "level": 0.0, "text_sim": 1.0})

    def test_propose(self):
        import e2_propose
        pa = e2._load("propose_apply_t", "propose_apply.py")
        letters = [{"id": "m1", "source": "other", "type": "question", "text": "which one?", "reply_to": "m0",
                    "reply_text": "the letter before"}, {"id": "m2", "source": "owner", "type": "request", "text": "push it"}]
        p = pa.mailbox_prompt(pa.letter_lines(letters), "2026-10-31", owner="Alex", assistant="Ava", lang="zh-Hant")
        self.assertIn("- id m1, from other, type question:\n  which one?\n  (reply to m0: the letter before)", p)
        self.assertIn(".agent-memory/memory/2026-10-31.md", p)
        self.assertNotIn("{{", p)
        with tempfile.TemporaryDirectory() as d:
            case, out = pathlib.Path(d) / "case", pathlib.Path(d) / "run" / "old-0"
            (case / "snapshot").mkdir(parents=True)
            (case / "args.json").write_text(json.dumps({"letters": letters}))
            note = ".agent-memory/memory/2026-10-31.md"
            (out / "files" / note).parent.mkdir(parents=True)
            (out / "files" / note).write_text("- answered m1\n")
            so = {"replies": [{"reply_to": "m1", "type": "done", "text": "SQLite."}], "done": [{"id": "m1"}],
                  "ask_owner": [{"text": "push?"}], "summary": "🔔 m2 asked\nm1 answered"}
            (out / "result.json").write_text(json.dumps({"ok": True, "output": so, "applied": [note], "refused": []}))
            r = e2_propose.outcome(case, out)
            self.assertEqual((r["closed"], r["asked"], r["note"], r["bell_ok"], r["contract_ok"]),
                             ({"m1": True, "m2": False}, True, True, True, True))
            self.assertIn("answered m1", r["text"])
            (out / "result.json").write_text(json.dumps({"ok": True, "output": {**so, "done": [{"id": "m9"}]},
                                                         "applied": [note], "refused": ["README.md"]}))
            q = e2_propose.outcome(case, out)
            self.assertFalse(q["contract_ok"])
            self.assertEqual(e2_propose.agree(r, q)["closed"], 0.5)

    def test_handover(self):
        import e2_handover
        turns = [["user", "do it?"], ["assistant", "yes"]]
        lr = e2_handover._runner((e2.BIN / "lease-runner.py").read_text(), "lease_runner_t")
        p = e2_handover.conversation(turns) + lr.handover_prompt(owner="Alex", lang="zh-Hant")
        self.assertTrue(p.startswith("<conversation>\n[user]\ndo it?\n\n[assistant]\nyes\n</conversation>\n\n"))
        self.assertIn("in Traditional Chinese (zh-Hant)", p)
        self.assertNotIn("{{", p)
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d)
            so = {"summary": "Decided.", "decisions": [{"what": "do the thing", "why": "worth it"}], "open_questions": [],
                  "tasks_for_owner": [{"text": "measure the ceiling by Friday", "due": "2026-11-06"}], "notes_markdown": "- do"}
            (out / "result.json").write_text(json.dumps({"is_error": False, "structured_output": so}))
            x = e2_handover.outcome(out)
            so["tasks_for_owner"][0]["due"] = "2026-11-07"
            (out / "result.json").write_text(json.dumps({"is_error": False, "structured_output": so}))
            y = e2_handover.outcome(out)
        a = e2_handover.agree(x, y)
        self.assertEqual((a["decisions"], a["tasks_for_owner"], a["due"], a["open_questions_empty"]), (1.0, 1.0, 0.0, 1.0))


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

    def test_mailbox_check_reads_the_run_bus(self):
        exp = e3.data()["scenarios"]["mailbox"]["expect"]
        ls = {"question": "m10301200-aaaa", "change": "m10301200-bbbb", "decision": "m10301200-cccc"}
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d)
            (out / "meta.json").write_text(json.dumps({"exit": 0}))
            (out / "result.json").write_text(json.dumps({"result": "🔔 " + " ".join(ls.values())}))
            note = ".agent-memory/memory/2026-10-31.md"
            files = {"before": {"app.py": "x"}, "after": {"app.py": "x", note: f"- limit {exp['change'][0]} later"},
                     "git_log": ["chore: initial commit"], "letter": None, "letters": ls}
            (out / "files.json").write_text(json.dumps(files))
            (out / "bus").mkdir()
            c = sqlite3.connect(out / "bus" / "bus.db")
            c.execute("CREATE TABLE events(seq INTEGER PRIMARY KEY, type, source, to_project, ref, payload)")
            rows = [("message", "rates", "dashboard", ls["question"], {"type": "info", "text": f"{exp['question'][0]}/min"}),
                    ("message.done", "rates", "rates", ls["question"], {}),
                    ("message", "rates", e3.hubconf.owner_address(), None, {"type": "question", "text": "open it?"})]
            for t, s, to, ref, p in rows:
                c.execute("INSERT INTO events(type, source, to_project, ref, payload) VALUES(?,?,?,?,?)",
                          (t, s, to, ref, json.dumps(p)))
            c.commit(), c.close()
            r = e3.check("mailbox", out)
        self.assertTrue(all(r[k] for k in ("answered", "question_closed", "code_untouched", "change_noted",
                                           "change_left_open", "owner_asked", "decision_left_open", "report_bell",
                                           "report_names_letters")), r)

    def test_branch_check_reads_the_run_bus(self):
        """delegate_question: replied to the sender with the fact, closed, code untouched; the point that seals the
        agent from MCP itself (mailbox-conversation) gets no second flag"""
        fact = e3.data()["scenarios"]["delegate_question"]["expect"][0]
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d)
            (out / "meta.json").write_text(json.dumps({"exit": 0}))
            (out / "result.json").write_text(json.dumps({"result": "replied"}))
            files = {"before": {"app.py": "x"}, "after": {"app.py": "x"}, "git_log": ["chore: initial commit"],
                     "letter": "m10301200-abcd"}
            (out / "files.json").write_text(json.dumps(files))
            (out / "bus").mkdir()
            c = sqlite3.connect(out / "bus" / "bus.db")
            c.execute("CREATE TABLE events(seq INTEGER PRIMARY KEY, type, source, to_project, ref, payload)")
            for t, s, to, p in (("message", "rates", e3.SENDER, {"type": "info", "text": f"limit {fact}/min"}),
                                ("message.done", "rates", "rates", {})):
                c.execute("INSERT INTO events(type, source, to_project, ref, payload) VALUES(?,?,?,'m10301200-abcd',?)",
                          (t, s, to, json.dumps(p)))
            c.commit(), c.close()
            r = e3.check("delegate_question", out)
        self.assertEqual((r["replied"], r["reply_has_fact"], r["closed"], r["code_untouched"], r["owner_asked"]),
                         (True, True, True, True, False))
        cmd = e3.llm_cmd(pathlib.Path("p"), point="mailbox-conversation")
        self.assertNotIn("--strict-mcp-config", cmd[cmd.index("--"):], "declared by the point (extra_args)")

    def test_lease_check_sees_a_changed_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d)
            (out / "meta.json").write_text(json.dumps({"exit": 0}))
            (out / "result.json").write_text(json.dumps({"result": "the limit is 100 per minute", "permission_denials": []}))
            files = {"before": {"README.md": "teh"}, "after": {"README.md": "the"}, "git_log": ["chore: initial commit"],
                     "letter": None}
            (out / "files.json").write_text(json.dumps(files))
            r = e3.check("lease_question", out)
        self.assertEqual((r["files_untouched"], r["reply_has"], r["denials"]), (False, True, 0))

    def test_mcp_scenario_has_only_the_platform_server(self):
        with tempfile.TemporaryDirectory() as d:
            proj = pathlib.Path(d) / "rates"
            e3.build(proj, "new", "mcp")
            self.assertFalse(e3.runtime_ports.load().instructions_file(proj).exists(), "no rules file: MCP only")
            cfg = json.loads(e3.mcp_config(pathlib.Path(d) / "mcp.json", "new", pathlib.Path(d),
                                           {"AGENT_HUB_CONFIG": "/c", "AGENT_HUB_STATE": "/s"}).read_text())
        server = cfg["mcpServers"]["agenthub"]
        self.assertEqual(list(cfg["mcpServers"]), ["agenthub"])
        self.assertTrue(server["args"][0].endswith("bin/mcp_server.py"))
        self.assertEqual((server["env"]["AGENT_HUB_CONFIG"], server["env"]["AGENTHUB_PROJECT"]), ("/c", "rates"))


if __name__ == "__main__":
    unittest.main()
