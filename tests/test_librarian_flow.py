"""Behaviour tests for the librarian (the whole script run once): its own small memory repos, bus and a stand-in
llm.py, one branch per project, asserting the results.
The same tests run against every implementation present under bin/ (librarian.sh, librarian.py), then check that the
prompts both send to the model are identical, so before librarian.sh is removed this proves the old and new behave
the same; after it is removed, it is librarian.py's regression test. No real data is touched.

  p0     new memory, the model changed STATE.md, worth a digest entry, plus one handoff → commit, review-log, digest,
         checkpoint advanced, handoff closed
  p1     new memory, the model changed nothing → reason kept, checkpoint advanced
  p2     the model wrote an extra file it may not write → moved to quarantine, checkpoint not advanced
  p3     the model returned no structured output but changed STATE.md → committed as usual, checkpoint not advanced
  pdang  STATE.md cites a file that does not exist + a malformed to-do section → the prompt carries a fix hint, the
         format-fixing judgment point is called once
  phand  no new memory, only a handoff → the prompt says only the handoff needs handling
  pidle  nothing changed → the model is not called
  Plus one handoff with a key (effect done) → not in the prompt, not closed (left to the assistant's task registry)

    bin/py -m unittest discover -s tests
"""
import datetime
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import i18n  # noqa: E402
import todo_tokens  # noqa: E402

# a zh-Hant instance: the memory repos' content (tests/fixtures/librarian-flow.zh-Hant.json) and STATE.md's to-do tokens
# are zh-Hant, the log and review-log lines the zh-Hant catalog's
F = json.loads((ROOT / "tests" / "fixtures" / "librarian-flow.zh-Hant.json").read_text())
ZH = todo_tokens.tokens("zh-Hant")


def zh(msgid: str, **kw) -> str:
    return i18n.translate("librarian", msgid, kw, "zh-Hant")
IMPLS = [p for p in ("librarian.sh", "librarian.py") if (ROOT / "bin" / p).exists()]
TODAY = datetime.date.today().isoformat()
MODES = {"p0": 0, "p1": 1, "p2": 2, "p3": 3, "pdang": 1, "phand": 0}

STUB = r'''
import json, os, pathlib, sys
point = sys.argv[2]; prompt = sys.stdin.read(); cwd = pathlib.Path.cwd(); env = pathlib.Path(os.environ["LIB_TEST_ENV"])
with open(env / "llm-calls.jsonl", "a") as f:
    f.write(json.dumps({"point": point, "cwd": cwd.name, "prompt": prompt.replace(str(env), "<ENV>")}, ensure_ascii=False) + "\n")
if os.environ.get("LIB_TEST_FAIL"):   # what the real llm.py prints when the call cannot be made
    print(json.dumps({"type": "result", "is_error": True, "structured_output": None, "llm_point": point,
                      "llm_error": os.environ["LIB_TEST_FAIL"]}))
    sys.exit(1)
def res(**k):
    print(json.dumps({"type": "result", "is_error": False, "result": "stub", "total_cost_usd": 0.01, "num_turns": 1,
                      "subtype": "success", **k}, ensure_ascii=False))
if point == "librarian-state":
    proj = cwd.name; mode = json.loads(os.environ["LIB_TEST_MODES"]).get(proj, 1); st = cwd / "STATE.md"
    if mode in (0, 2, 3):
        st.write_text(st.read_text() + f"\n<!-- edited {proj} -->\n")
    if mode == 2:
        (cwd / "memory" / "STRAY.md").write_text("should not be here\n")
    if mode == 3:
        res(structured_output=None, result="no structured output")
    else:
        res(structured_output={"state_updated": mode != 1, "summary": f"summary {proj}", "reason": f"reason {proj}",
                               "worth_digesting": mode == 0})
elif point == "librarian-todo-format":
    st = cwd / "STATE.md"; st.write_text(st.read_text().replace(os.environ["LIB_TEST_UNTAGGED"], os.environ["LIB_TEST_TAGGED"])); res(structured_output=None)
elif point == "librarian-index":
    p = cwd / "index.md"; p.write_text(p.read_text() + "\n<!-- index edited -->\n"); res(structured_output={"relations_found": "none"})
elif point == "librarian-state-edits":   # shadow: touches no file, only returns an edit list
    # (p1 returns an old that does not match)
    import re
    proj = re.search(r"the STATE.md of project (\S+)\. You", prompt).group(1)
    heading = os.environ["LIB_TEST_HEADING"]
    old = os.environ["LIB_TEST_MISS"] if proj == "p1" else heading
    live = os.environ.get("LIB_TEST_LIVE")   # in charge: p0 is worth a digest entry, so index.md runs too
    res(structured_output={"state_updated": True, "edits": [{"old": old, "new": f"<!-- shadow {proj} -->\n{heading}"}],
                           "worth_digesting": bool(live) and proj == "p0", "summary": f"summary {proj}" if live else "",
                           "reason": f"shadow {proj}"})
elif point == "librarian-todo-format-edits":
    res(structured_output={"edits": [{"old": os.environ["LIB_TEST_UNTAGGED"], "new": os.environ["LIB_TEST_TAGGED"]}],
                           "reason": "format"})
elif point == "librarian-index-edits":
    e = [{"old": "# index\n", "new": "# index\n<!-- index edits -->\n"}] if os.environ.get("LIB_TEST_LIVE") else []
    res(structured_output={"index_updated": bool(e), "edits": e, "relations_found": "none"})
'''

# must be bigger than a placeholder (check-state-todo skips small placeholder files, gen-todo.PLACEHOLDER_MAX_BYTES)
STATE_OK = (f"# STATE\n\n{F['status']}\n\n" + F["background"] * 40 + f"\n\n{ZH['todo_heading']}\n"
            + todo_tokens.item("zh-Hant", "agent", "Alex", F["one_thing"]) + "\n")


def sh(*cmd, cwd=None, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


class Env:
    """A temporary hub (bin/ all linked to the real one, only llm.py a stand-in) + a bare repo / memory copy per project
    + a bus."""

    def __init__(self, tmp: pathlib.Path):
        self.tmp, self.hub = tmp, tmp / "hub"
        (self.hub / "bin").mkdir(parents=True)
        for f in (ROOT / "bin").iterdir():
            if f.is_file() and f.name != "llm.py":
                (self.hub / "bin" / f.name).symlink_to(f)
        (self.hub / "bin" / "llm.py").write_text(STUB)
        (self.hub / "templates").mkdir()
        (self.hub / "templates" / "prompts").symlink_to(ROOT / "templates" / "prompts")   # the judgment points' prompts
        for d in ("projects", ".state", ".logs", "digest"):
            (self.hub / d).mkdir()
        (self.hub / "index.md").write_text("# index\n")
        (self.hub / "hub.json").write_text(json.dumps({
            "v": 1, "registry_dir": "projects", "memory_copies": str(tmp / "copies"), "git_origin": str(tmp / "bare"),
            "bus_dir": str(tmp / "bus"), "evals_dir": str(tmp / "evals"), "owner": {"address": "owner", "aliases": ["alex"], "tag": "Alex"},
            "assistant": {"project": "helper", "api": "http://127.0.0.1:9/x", "name": "Hugin"}}))
        e = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "AGENT_HUB_HOME", "AGENT_HUB_CONFIG",
                                                                     "AGENT_HUB_DATA", "AGENT_HUB_SECRETS"))}
        # the log and review-log lines asserted below are the zh-Hant catalog's (docs/design/i18n.md), the STATE.md
        # files zh-Hant content
        self.env = {**e, "AGENT_HUB_ROOT": str(self.hub), "AGENT_HUB_LANG_UI": "zh-Hant", "AGENT_HUB_LANG_CONTENT": "zh-Hant",
                    "LIB_TEST_ENV": str(tmp), "LIB_TEST_MODES": json.dumps(MODES), "LIB_TEST_HEADING": ZH["todo_heading"],
                    "LIB_TEST_MISS": F["shadow_miss"], "LIB_TEST_UNTAGGED": "- [ ] " + F["untagged"],
                    "LIB_TEST_TAGGED": todo_tokens.item("zh-Hant", "agent", "Alex", F["untagged"]),
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        for p in (*MODES, "pidle"):
            self.project(p)
        self.handoffs()

    def git(self, repo, *args):
        return sh("git", "-C", str(repo), *args, env=self.env)

    def project(self, p):
        bare, copy = self.tmp / "bare" / f"{p}-memory.git", self.tmp / "copies" / p
        bare.parent.mkdir(exist_ok=True)
        sh("git", "init", "-q", "--bare", "-b", "agent-memory", str(bare), env=self.env)
        sh("git", "clone", "-q", str(bare), str(copy), env=self.env)
        (copy / "memory").mkdir()
        (copy / "AGENT.md").write_text("agent\n")
        (copy / "memory" / "2026-10-20.md").write_text(f"- {F['old_note']}\n")
        (copy / "STATE.md").write_text(STATE_OK)
        self.git(copy, "add", "-A"); self.git(copy, "commit", "-qm", "init"); self.git(copy, "push", "-q", "origin", "HEAD")
        (self.hub / ".state" / f"{p}.sha").write_text(self.git(copy, "rev-parse", "HEAD") + "\n")
        if p in ("phand", "pidle"):
            return
        # new memory pushed to the bare repo and the copy reset back, so the librarian's git pull gets "new commits"
        (copy / "memory" / "2026-10-27.md").write_text("- " + F["new_note"].format(p=p) + "\n")
        if p == "pdang":
            (copy / "STATE.md").write_text(STATE_OK.replace(F["status"], F["status_with_ref"])
                                           + "- [ ] " + F["untagged_line"] + "\n")
        self.git(copy, "add", "-A"); self.git(copy, "commit", "-qm", "new note")
        self.git(copy, "push", "-q", "origin", "HEAD"); self.git(copy, "reset", "-q", "--hard", "HEAD~1")

    def handoffs(self):
        subprocess.run([str(self.hub / "bin" / "py"), str(self.hub / "bin" / "hubbus"), "open-handoffs"], env=self.env,
                       capture_output=True, check=True)   # creates the bus tables
        c = sqlite3.connect(self.tmp / "bus" / "bus.db")
        for eid, proj, eff, fact in F["handoffs"]:
            payload = {"v": 1, "type": "handoff", "handoff_id": int(eid[1]) + 100, "effect": eff,
                       "handoff": {"fact": fact, "quote": F["quote"] if eid == "h1" else None, "item": None, "why": F["why"],
                                   "ts": "2026-10-26T10:00:00"}}
            c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                      (eid, "2026-10-26T10:00:00+08:00", "message", "owner", proj, None, json.dumps(payload, ensure_ascii=False)))
        c.commit(); c.close()

    def run(self, impl):
        cmd = [str(self.hub / "bin" / impl)] if impl.endswith(".sh") else [sys.executable, str(self.hub / "bin" / impl)]
        return subprocess.run(cmd, cwd=self.hub, env=self.env, capture_output=True, text=True, timeout=300)

    def calls(self):
        f = self.tmp / "llm-calls.jsonl"
        return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []

    def ckpt_at_head(self, p):
        return (self.hub / ".state" / f"{p}.sha").read_text().strip() == self.git(self.tmp / "copies" / p, "rev-parse", "HEAD")

    def subjects(self, p):
        return self.git(self.tmp / "copies" / p, "log", "--format=%s").splitlines()


@unittest.skipUnless(IMPLS, "no librarian implementation")
class LibrarianFlow(unittest.TestCase):
    def each(self, check):
        for impl in IMPLS:
            with self.subTest(impl=impl), tempfile.TemporaryDirectory() as d:
                env = Env(pathlib.Path(d))
                r = env.run(impl)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                check(env, (env.hub / ".logs" / f"librarian-{TODAY}.log").read_text())

    def test_eval_cases_recorded(self):
        """docs/design/i18n.md E2: each model call's inputs, rendered prompts, snapshot and outcome are kept"""
        def check(env, log):
            root = env.tmp / "evals" / "cases"
            self.assertEqual(oct((env.tmp / "evals").stat().st_mode & 0o777), "0o700")
            cases = {p.name for p in (root / "librarian-state" / TODAY).iterdir()}
            self.assertEqual(cases, set(MODES))
            p0 = root / "librarian-state" / TODAY / "p0"
            args = json.loads((p0 / "args.json").read_text())
            self.assertEqual((args["project"], args["owner"], args["assistant"]), ("p0", "Alex", "Hugin"))
            self.assertIn("2026-10-27.md", args["changed"])
            self.assertEqual([h.get("handoff_id") for h in args["handoffs"]], [101])
            sent = {c["cwd"]: c["prompt"] for c in env.calls() if c["point"] == "librarian-state"}
            self.assertEqual((p0 / "prompt.librarian-state.txt").read_text(), sent["p0"], "the old arm is tonight's prompt")
            self.assertTrue((p0 / "snapshot" / "memory" / "2026-10-27.md").exists())
            self.assertFalse((p0 / "snapshot" / ".git").exists())
            out = json.loads((p0 / "outcome.json").read_text())
            self.assertTrue(out["changed"])
            self.assertIn("<!-- edited p0 -->", (p0 / "after.STATE.md").read_text())
            idx = root / "librarian-index" / TODAY / "index"
            self.assertTrue((idx / "prompt.librarian-index-edits.txt").exists())
            self.assertIn("<!-- index edited -->", (idx / "after.index.md").read_text())
        self.each(check)

    def test_each_branch(self):
        def check(env, log):
            by = {}
            for c in env.calls():
                by.setdefault((c["point"], c["cwd"]), c["prompt"])
            # who called the model, and with what
            self.assertEqual({c for p, c in by if p == "librarian-state"}, set(MODES),
                             "pidle, unchanged, must not call the model")
            self.assertIn(("librarian-todo-format", "pdang"), by)
            self.assertIn(("librarian-index", "hub"), by)
            p0 = by[("librarian-state", "p0")]
            self.assertIn(todo_tokens.tag("zh-Hant", "owner", "Alex"), p0)
            self.assertIn("Hugin", p0)
            fact, keyed = F["handoffs"][0][3], F["handoffs"][2][3]
            self.assertIn(fact, p0)
            self.assertIn(f"Alex's own words: {F['quote']}", p0)
            self.assertNotIn(keyed, p0, "a handoff with a key is not given to the model")
            self.assertIn(F["new_note"].format(p="p0"), p0, "the diff goes into the prompt")
            self.assertIn("STATE.md now refers to these files, which no longer exist", by[("librarian-state", "pdang")])
            self.assertIn("memory/reference/gone.md", by[("librarian-state", "pdang")])
            self.assertIn("Nothing changed in memory/ this time; there are only Alex's handoffs below", by[("librarian-state", "phand")])
            # a changed STATE.md is committed; the checkpoint advances only when the result is trustworthy
            for p in ("p0", "p2", "p3", "phand"):
                self.assertEqual(env.subjects(p)[0], "librarian: update STATE.md", p)
            self.assertNotEqual(env.subjects("p1")[0], "librarian: update STATE.md")
            for p, at_head in (("p0", True), ("p1", True), ("pdang", True), ("phand", True), ("p2", False), ("p3", False)):
                self.assertEqual(env.ckpt_at_head(p), at_head, p)
            # quarantine
            self.assertTrue((env.hub / ".state" / "stray" / f"p2-{TODAY}" / "memory_STRAY.md").exists())
            self.assertEqual(env.git(env.tmp / "copies" / "p2", "status", "--porcelain"), "")
            # review-log, digest, index
            rl = (env.hub / "review-log.md").read_text()
            self.assertIn(f"## {TODAY} · p0", rl)
            self.assertIn(zh("**Handoffs from {assistant}**: {nums}", assistant="Hugin", nums="#101"), rl)
            self.assertNotIn("· p1", rl)
            dg = (env.hub / "digest" / f"{TODAY}.md").read_text()
            self.assertIn("## p0", dg)
            self.assertIn("summary p0", dg)
            self.assertNotIn("summary p1", dg)
            self.assertIn("<!-- index edited -->", (env.hub / "index.md").read_text())
            # pushed to the bare repo; only the trustworthy handoffs are closed
            self.assertEqual(env.git(env.tmp / "bare" / "p0-memory.git", "rev-parse", "agent-memory"),
                             env.git(env.tmp / "copies" / "p0", "rev-parse", "HEAD"))
            c = sqlite3.connect(env.tmp / "bus" / "bus.db")
            done = {r[0] for r in c.execute("SELECT ref FROM events WHERE type='message.done'")}
            self.assertEqual(done, {"h1", "h2"})
            # the judgments visible in the log
            self.assertIn("pidle: no new commits, skip", log)
            self.assertIn(zh("{project}: STATE.md not updated; reason: {reason}", project="p1", reason="reason p1"), log)
            self.assertIn(zh("{project}: to-do section format fixed", project="pdang"), log)
            untrusted = "{project}: this result is not trustworthy; the checkpoint is not advanced, retrying next time"
            self.assertEqual(log.count(zh(untrusted, project="")), 2)
            for p in ("p2", "p3"):
                self.assertIn(zh(untrusted, project=p), log)
        self.each(check)

    @unittest.skipUnless("librarian.py" in IMPLS, "only librarian.py handles v2")
    def test_v2_records_are_input_like_handoffs(self):
        """Principal Handoff 0.3: notes from the principal side and Decisions' settled go into the prompt (with the
        decider), done only when trustworthy; Acts' settled belongs to the registrar, the librarian leaves it."""
        with tempfile.TemporaryDirectory() as d:
            env = Env(pathlib.Path(d))
            c = sqlite3.connect(env.tmp / "bus" / "bus.db")
            def put(eid, source, to, rec):
                c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                          (eid, "2026-10-26T10:00:00+08:00", "record", source, to, None,
                           json.dumps({"v": 1, "record": rec}, ensure_ascii=False)))
            base = {"ph": "0.3", "project": "p1", "ts": "2026-10-26T02:00:00Z"}
            put("rec:decisions/0.3:p1:r:q1", "p1", "owner", {**base, "ext": "decisions/0.3", "id": "r:q1", "type": "raised",
                "origin": "requester", "by": "project:p1", "key": "q1", "terms": {"text": F["question"]}, "terms_rev": 1})
            put("rec:acts/0.3:p1:n1", "owner", "p1", {**base, "ext": "acts/0.3", "id": "n1", "type": "note",
                "origin": "principal", "by": "helper", "key": "k1", "about": {"quote": F["note_about"]}, "note": F["note"]})
            put("rec:decisions/0.3:p1:s1", "owner", "p1", {**base, "ext": "decisions/0.3", "id": "s1", "type": "settled",
                "origin": "principal", "by": "helper", "key": "q1", "outcome": "decided", "terms_rev": 1,
                "answer": {"text": F["answer"]}, "authority": {"kind": "principal", "decider": "person:alex"}})
            put("rec:acts/0.3:p1:s2", "owner", "p1", {**base, "ext": "acts/0.3", "id": "s2", "type": "settled",
                "origin": "principal", "by": "helper", "key": "k2", "outcome": "done", "terms_rev": 1,
                "authority": {"kind": "principal", "decider": "person:alex"}})
            c.commit()
            self.assertEqual(env.run("librarian.py").returncode, 0)
            p1 = next(x["prompt"] for x in env.calls() if x["point"] == "librarian-state" and x["cwd"] == "p1")
            for want in ("[note]", F["note"], F["note_about"], "[answers]", F["answer"], F["question"],
                         "Alex decided it personally", "[dismissed]"):
                self.assertIn(want, p1)
            self.assertNotIn("k2", p1, "Acts' settled belongs to the registrar")
            self.assertNotIn(":null", p1, "a note without a reason does not print null")
            done = {r[0] for r in c.execute("SELECT ref FROM events WHERE type='message.done'")}
            self.assertLessEqual({"rec:acts/0.3:p1:n1", "rec:decisions/0.3:p1:s1"}, done)
            self.assertNotIn("rec:acts/0.3:p1:s2", done)

    def test_shadow_compares_without_writing(self):
        """Shadow mode (the completion version): every project that called the model has a comparison record; proposals
        are written only in .state/librarian-shadow/, the real files stay untouched."""
        with tempfile.TemporaryDirectory() as d:
            env = Env(pathlib.Path(d))
            self.assertEqual(env.run("librarian.py").returncode, 0)
            recs = [json.loads(x) for x in (env.hub / ".state" / "librarian-shadow" / f"{TODAY}.jsonl").read_text().splitlines()]
            state = {r["project"]: r for r in recs if r["point"] == "librarian-state-edits"}
            self.assertEqual(set(state), set(MODES))
            self.assertIn("edit_error", state["p1"],
                          "an old that does not match is recorded as non-compliant, never half applied")
            self.assertFalse(state["p1"]["ok"])
            self.assertTrue(state["p0"]["ok"])
            self.assertIn("similarity", state["p0"])
            self.assertTrue((env.hub / ".state" / "librarian-shadow" / TODAY / "p0.STATE.md").exists())
            for p in MODES:
                self.assertNotIn("<!-- shadow", (env.tmp / "copies" / p / "STATE.md").read_text(), p)
            self.assertTrue(any(r["point"] == "librarian-index-edits" for r in recs))
            prompt = next(c["prompt"] for c in env.calls() if c["point"] == "librarian-state-edits" and "p0" in c["prompt"])
            self.assertIn("<<<STATE", prompt)
            self.assertNotIn("read ./AGENT.md", prompt,
                             "the completion version has no tools: the content is given directly")

    def test_shadow_of_the_format_fix(self):
        """librarian-todo-format-edits runs on the same malformed STATE.md the agent version was given; its proposal
        is checked with check-state-todo and kept in the shadow dir, the real file is the agent version's"""
        with tempfile.TemporaryDirectory() as d:
            env = Env(pathlib.Path(d))
            self.assertEqual(env.run("librarian.py").returncode, 0)
            recs = [json.loads(x) for x in (env.hub / ".state" / "librarian-shadow" / f"{TODAY}.jsonl").read_text().splitlines()]
            todo = [r for r in recs if r["point"] == "librarian-todo-format-edits"]
            self.assertEqual([r["project"] for r in todo], ["pdang"])
            self.assertTrue(todo[0]["ok"])
            self.assertTrue(todo[0]["passes"])
            self.assertTrue(todo[0]["agent_passes"])
            self.assertEqual(todo[0]["marker_problems"], [])
            prompt = next(c["prompt"] for c in env.calls() if c["point"] == "librarian-todo-format-edits")
            self.assertIn(F["untagged"], prompt, "the malformed STATE.md is in the prompt")

    @unittest.skipUnless(len(IMPLS) == 2, "only one implementation left")
    def test_implementations_send_the_same_prompts(self):
        got = {}
        for impl in IMPLS:
            with tempfile.TemporaryDirectory() as d:
                env = Env(pathlib.Path(d))
                self.assertEqual(env.run(impl).returncode, 0)
                # commit hashes differ every time (timestamps); not a single other character may differ
                import re
                got[impl] = [{**c, "prompt": re.sub(r"\b[0-9a-f]{7,40}\b", "<sha>", c["prompt"])} for c in env.calls()]
        self.assertEqual(got["librarian.sh"], got["librarian.py"])


class InCharge(unittest.TestCase):
    """hub.json librarian.completion (llm-providers.md step 4): a switched part calls only its completion version and
    the platform writes what the checked edit list says; an invalid list is that run's failure, never half written."""

    def env(self, d, settings=None, extra=None):
        env = Env(pathlib.Path(d))
        (env.hub / "contracts").symlink_to(ROOT / "contracts")   # which shape each point has (llm_ready)
        f = env.hub / "hub.json"
        conf = {**json.loads(f.read_text()), **(extra or {})}
        if settings is not None:
            conf["librarian"] = {"completion": settings}
        f.write_text(json.dumps(conf))
        env.env["LIB_TEST_LIVE"] = "1"
        r = env.run("librarian.py")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return env, (env.hub / ".logs" / f"librarian-{TODAY}.log").read_text()

    def check_in_charge(self, env, log):
        points = {c["point"] for c in env.calls()}
        self.assertEqual(points, {"librarian-state-edits", "librarian-todo-format-edits", "librarian-index-edits"})
        p0 = env.tmp / "copies" / "p0"
        self.assertIn("<!-- shadow p0 -->", (p0 / "STATE.md").read_text())
        self.assertEqual(env.subjects("p0")[0], "librarian: update STATE.md")
        self.assertTrue(env.ckpt_at_head("p0"))
        self.assertIn(f"## {TODAY} · p0", (env.hub / "review-log.md").read_text())
        # p1's edit list does not match: nothing written, the checkpoint stays for the next night
        self.assertNotIn("<!-- shadow", (env.tmp / "copies" / "p1" / "STATE.md").read_text())
        self.assertFalse(env.ckpt_at_head("p1"))
        self.assertIn(zh("the edit list is invalid: {error}", error=""), log)
        # the format fix by edit list
        st = (env.tmp / "copies" / "pdang" / "STATE.md").read_text()
        self.assertNotIn("- [ ] " + F["untagged"] + "\n", st)
        self.assertIn(zh("{project}: to-do section format fixed", project="pdang"), log)
        self.assertIn("<!-- index edits -->", (env.hub / "index.md").read_text())
        recs = [json.loads(x) for x in (env.hub / ".state" / "librarian-shadow" / f"{TODAY}.jsonl").read_text().splitlines()]
        self.assertTrue(recs and all(r.get("in_charge") for r in recs), "no comparisons while in charge")

    def test_switched_on(self):
        with tempfile.TemporaryDirectory() as d:
            env, log = self.env(d, {"state": True, "todo_format": True, "index": True})
            self.check_in_charge(env, log)
            self.assertIn(zh("completion version in charge: {points}", points="librarian-state-edits, "
                             "librarian-todo-format-edits, librarian-index-edits"), log)

    def test_only_an_api_key(self):
        """no coding agent, an API provider: the completion versions take over without a setting"""
        with tempfile.TemporaryDirectory() as d:
            env, log = self.env(d, extra={"runtime": {"default": "none"},
                                          "llm": {"providers": {"local": {"impl": "ollama"}}}})
            self.check_in_charge(env, log)

    def test_one_part_at_a_time(self):
        with tempfile.TemporaryDirectory() as d:
            env, _log = self.env(d, {"index": True})
            points = {c["point"] for c in env.calls()}
            self.assertIn("librarian-state", points)
            self.assertIn("librarian-state-edits", points, "state is still compared")
            self.assertNotIn("librarian-index", points)
            self.assertIn("<!-- index edits -->", (env.hub / "index.md").read_text())


class NoModel(unittest.TestCase):
    """The model cannot be called: a newcomer without a coding agent (runtime.default: none), or a call that fails."""

    def env(self, d, runtime=None):
        env = Env(pathlib.Path(d))
        if runtime:
            f = env.hub / "hub.json"
            f.write_text(json.dumps({**json.loads(f.read_text()), "runtime": {"default": runtime}}))
        env.env["LIB_TEST_FAIL"] = "the stand-in's reason"
        return env

    @staticmethod
    def checkpoints(env):
        return {f.name: f.read_text() for f in (env.hub / ".state").glob("*.sha")}

    def run_(self, env):
        r = env.run("librarian.py")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return (env.hub / ".logs" / f"librarian-{TODAY}.log").read_text()

    def test_no_coding_agent(self):
        """switched off once, with the reason; no call, nothing touched, the notes wait"""
        with tempfile.TemporaryDirectory() as d:
            env = self.env(d, runtime="none")
            before = self.checkpoints(env)
            log = self.run_(env)
            self.assertEqual(env.calls(), [], "no model to call: nothing is attempted")
            off = zh("the librarian is off: {reason}. STATE.md and index.md are not updated; the memory notes wait until "
                     "a model is set up (nothing is lost)", reason="<R>").split("<R>")
            self.assertEqual(log.count(off[0]), 1, log)
            reason = i18n.translate("runtime_ports", "runtime {name} has no {missing} (hub.json runtime.default; to use "
                                    "this feature, install a coding agent that supports it)",
                                    {"name": "none", "missing": "<M>"}, "zh-Hant").split("<M>")[0]
            self.assertIn(off[0] + reason, log, "the reason is the runtime's own")
            self.assertNotIn("claude", log)
            for p in MODES:
                self.assertNotIn(f"--- {p} ---", log, "no per-project lines")
            self.assertEqual(self.checkpoints(env), before, "the notes wait for a model")

    def test_a_failed_call_says_why(self):
        with tempfile.TemporaryDirectory() as d:
            env = self.env(d)
            log = self.run_(env)
            failed = "{project}: the model call failed ({error}); the copy is restored and the checkpoint not advanced"
            self.assertIn(zh(failed, project="p0", error="the stand-in's reason"), log)
            self.assertNotIn("claude", log)

if __name__ == "__main__":
    unittest.main()
