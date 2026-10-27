"""librarian 的行為測試(整支跑一次):自己帶的小 memory repo、匯流排、替身 llm.py,每個專案走一條分支,斷言結果。
同一組測試對 bin/ 底下有的每個實作都跑(librarian.sh、librarian.py),最後再比兩個實作送給模型的 prompt 一模一樣——
所以 librarian.sh 拿掉之前,這就是新舊行為一樣的證明;拿掉之後是 librarian.py 的回歸測試。不碰任何真的資料。

  p0     有新 memory、模型改了 STATE.md、值得進 digest,另外帶一筆轉交 → commit、review-log、digest、checkpoint 推進、轉交結案
  p1     有新 memory、模型沒改 → 留理由、checkpoint 推進
  p2     模型多寫了一個不准寫的檔 → 搬到隔離區、checkpoint 不推進
  p3     模型沒回結構化輸出,但改了 STATE.md → 照常 commit、checkpoint 不推進
  pdang  STATE.md 引用不存在的檔 + 待辦格式錯 → prompt 帶修正提示、叫一次修格式的判斷點
  phand  沒有新 memory、只有轉交 → prompt 說只有轉交要處理
  pidle  什麼都沒變 → 不叫模型
  另有一筆帶 key 的轉交(effect done)→ 不進 prompt、不結案(留給助理的任務登錄)

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
IMPLS = [p for p in ("librarian.sh", "librarian.py") if (ROOT / "bin" / p).exists()]
TODAY = datetime.date.today().isoformat()
MODES = {"p0": 0, "p1": 1, "p2": 2, "p3": 3, "pdang": 1, "phand": 0}

STUB = r'''
import json, os, pathlib, sys
point = sys.argv[2]; prompt = sys.stdin.read(); cwd = pathlib.Path.cwd(); env = pathlib.Path(os.environ["LIB_TEST_ENV"])
with open(env / "llm-calls.jsonl", "a") as f:
    f.write(json.dumps({"point": point, "cwd": cwd.name, "prompt": prompt.replace(str(env), "<ENV>")}, ensure_ascii=False) + "\n")
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
    st = cwd / "STATE.md"; st.write_text(st.read_text().replace("- [ ] 沒標負責人", "- [ ] 〔agent〕沒標負責人")); res(structured_output=None)
elif point == "librarian-index":
    p = cwd / "index.md"; p.write_text(p.read_text() + "\n<!-- index edited -->\n"); res(structured_output={"relations_found": "none"})
'''

# 要比佔位檔大(check-state-todo 不檢查佔位用的小檔案,gen-todo.PLACEHOLDER_MAX_BYTES)
STATE_OK = "# STATE\n\n現況。\n\n" + "這個專案的背景說明。" * 40 + "\n\n## 待辦\n- [ ] 〔agent〕做一件事\n"


def sh(*cmd, cwd=None, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


class Env:
    """一個暫存 hub(bin/ 都連到真的,只有 llm.py 是替身)+ 每個專案一組 bare repo / memory 副本 + 匯流排。"""

    def __init__(self, tmp: pathlib.Path):
        self.tmp, self.hub = tmp, tmp / "hub"
        (self.hub / "bin").mkdir(parents=True)
        for f in (ROOT / "bin").iterdir():
            if f.is_file() and f.name != "llm.py":
                (self.hub / "bin" / f.name).symlink_to(f)
        (self.hub / "bin" / "llm.py").write_text(STUB)
        for d in ("projects", ".state", ".logs", "digest"):
            (self.hub / d).mkdir()
        (self.hub / "index.md").write_text("# index\n")
        (self.hub / "hub.json").write_text(json.dumps({
            "v": 1, "registry_dir": "projects", "memory_copies": str(tmp / "copies"), "git_origin": str(tmp / "bare"),
            "bus_dir": str(tmp / "bus"), "owner": {"address": "owner", "aliases": ["alex"], "tag": "Alex"},
            "assistant": {"project": "helper", "api": "http://127.0.0.1:9/x", "name": "Hugin"}}))
        e = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "AGENT_HUB_HOME", "AGENT_HUB_CONFIG",
                                                                     "AGENT_HUB_DATA", "AGENT_HUB_SECRETS"))}
        self.env = {**e, "AGENT_HUB_ROOT": str(self.hub), "LIB_TEST_ENV": str(tmp), "LIB_TEST_MODES": json.dumps(MODES),
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
        (copy / "memory" / "2026-10-20.md").write_text("- 舊的筆記\n")
        (copy / "STATE.md").write_text(STATE_OK)
        self.git(copy, "add", "-A"); self.git(copy, "commit", "-qm", "init"); self.git(copy, "push", "-q", "origin", "HEAD")
        (self.hub / ".state" / f"{p}.sha").write_text(self.git(copy, "rev-parse", "HEAD") + "\n")
        if p in ("phand", "pidle"):
            return
        # 新的 memory 推到 bare,副本退回去:librarian 的 git pull 才拿得到「新的 commit」
        (copy / "memory" / "2026-10-27.md").write_text(f"- {p} 的新筆記\n")
        if p == "pdang":
            (copy / "STATE.md").write_text(STATE_OK.replace("現況。", "現況,見 memory/reference/gone.md。")
                                           + "- [ ] 沒標負責人的一件事\n")
        self.git(copy, "add", "-A"); self.git(copy, "commit", "-qm", "new note")
        self.git(copy, "push", "-q", "origin", "HEAD"); self.git(copy, "reset", "-q", "--hard", "HEAD~1")

    def handoffs(self):
        subprocess.run([str(self.hub / "bin" / "py"), str(self.hub / "bin" / "hubbus"), "open-handoffs"], env=self.env,
                       capture_output=True, check=True)   # 建匯流排的表
        c = sqlite3.connect(self.tmp / "bus" / "bus.db")
        for eid, proj, eff, fact in (("h1", "p0", "relevant", "轉交事實一"), ("h2", "phand", "changes", "只有轉交的事實"),
                                     ("h3", "p0", "done", "帶 key 的轉交")):
            payload = {"v": 1, "type": "handoff", "handoff_id": int(eid[1]) + 100, "effect": eff,
                       "handoff": {"fact": fact, "quote": "原話" if eid == "h1" else None, "item": None, "why": "理由",
                                   "ts": "2026-10-26T10:00:00"}}
            c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                      (eid, "2026-10-26T10:00:00+08:00", "message", "owner", proj, None, json.dumps(payload, ensure_ascii=False)))
        c.commit(); c.close()

    def run(self, impl):
        return subprocess.run([str(self.hub / "bin" / impl)], cwd=self.hub, env=self.env, capture_output=True, text=True,
                              timeout=300)

    def calls(self):
        f = self.tmp / "llm-calls.jsonl"
        return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []

    def ckpt_at_head(self, p):
        return (self.hub / ".state" / f"{p}.sha").read_text().strip() == self.git(self.tmp / "copies" / p, "rev-parse", "HEAD")

    def subjects(self, p):
        return self.git(self.tmp / "copies" / p, "log", "--format=%s").splitlines()


@unittest.skipUnless(IMPLS, "沒有 librarian 實作")
class LibrarianFlow(unittest.TestCase):
    def each(self, check):
        for impl in IMPLS:
            with self.subTest(impl=impl), tempfile.TemporaryDirectory() as d:
                env = Env(pathlib.Path(d))
                r = env.run(impl)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                check(env, (env.hub / ".logs" / f"librarian-{TODAY}.log").read_text())

    def test_each_branch(self):
        def check(env, log):
            by = {}
            for c in env.calls():
                by.setdefault((c["point"], c["cwd"]), c["prompt"])
            # 誰叫了模型、叫了什麼
            self.assertEqual({c for p, c in by if p == "librarian-state"}, set(MODES), "pidle 沒變就不該叫模型")
            self.assertIn(("librarian-todo-format", "pdang"), by)
            self.assertIn(("librarian-index", "hub"), by)
            p0 = by[("librarian-state", "p0")]
            self.assertIn("〔Alex〕", p0)
            self.assertIn("Hugin", p0)
            self.assertIn("轉交事實一", p0)
            self.assertIn("Alex 原話:原話", p0)
            self.assertNotIn("帶 key 的轉交", p0, "帶 key 的轉交不給模型")
            self.assertIn("p0 的新筆記", p0, "diff 要放進 prompt")
            self.assertIn("STATE.md 目前引用了這些已經不存在的檔案", by[("librarian-state", "pdang")])
            self.assertIn("memory/reference/gone.md", by[("librarian-state", "pdang")])
            self.assertIn("這次 memory/ 沒有新變動,只有下面 Alex 的轉交要處理", by[("librarian-state", "phand")])
            # STATE.md 有改的就 commit;checkpoint 只在結果可信時推進
            for p in ("p0", "p2", "p3", "phand"):
                self.assertEqual(env.subjects(p)[0], "librarian: update STATE.md", p)
            self.assertNotEqual(env.subjects("p1")[0], "librarian: update STATE.md")
            for p, at_head in (("p0", True), ("p1", True), ("pdang", True), ("phand", True), ("p2", False), ("p3", False)):
                self.assertEqual(env.ckpt_at_head(p), at_head, p)
            # 隔離區
            self.assertTrue((env.hub / ".state" / "stray" / f"p2-{TODAY}" / "memory_STRAY.md").exists())
            self.assertEqual(env.git(env.tmp / "copies" / "p2", "status", "--porcelain"), "")
            # review-log、digest、index
            rl = (env.hub / "review-log.md").read_text()
            self.assertIn(f"## {TODAY} · p0", rl)
            self.assertIn("**Hugin 轉交**: #101", rl)
            self.assertNotIn("· p1", rl)
            dg = (env.hub / "digest" / f"{TODAY}.md").read_text()
            self.assertIn("## p0", dg)
            self.assertIn("summary p0", dg)
            self.assertNotIn("summary p1", dg)
            self.assertIn("<!-- index edited -->", (env.hub / "index.md").read_text())
            # 推到 bare;轉交只有可信的那幾個結案
            self.assertEqual(env.git(env.tmp / "bare" / "p0-memory.git", "rev-parse", "agent-memory"),
                             env.git(env.tmp / "copies" / "p0", "rev-parse", "HEAD"))
            c = sqlite3.connect(env.tmp / "bus" / "bus.db")
            done = {r[0] for r in c.execute("SELECT ref FROM events WHERE type='message.done'")}
            self.assertEqual(done, {"h1", "h2"})
            # log 裡看得到的判斷
            self.assertIn("pidle: no new commits, skip", log)
            self.assertIn("p1: STATE.md 未更新,理由: reason p1", log)
            self.assertIn("pdang: 待辦段落格式已修正", log)
            self.assertEqual(log.count("這次結果不可信"), 2)
        self.each(check)

    @unittest.skipUnless("librarian.py" in IMPLS, "v2 只有 librarian.py 處理")
    def test_v2_records_are_input_like_handoffs(self):
        """Principal Handoff 0.3:principal 端來的 note 跟 Decisions 的 settled 進 prompt(帶決定者)、可信才 done;
        Acts 的 settled 是 registrar 的,librarian 不碰。"""
        with tempfile.TemporaryDirectory() as d:
            env = Env(pathlib.Path(d))
            c = sqlite3.connect(env.tmp / "bus" / "bus.db")
            def put(eid, source, to, rec):
                c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                          (eid, "2026-10-26T10:00:00+08:00", "record", source, to, None,
                           json.dumps({"v": 1, "record": rec}, ensure_ascii=False)))
            base = {"ph": "0.3", "project": "p1", "ts": "2026-10-26T02:00:00Z"}
            put("rec:decisions/0.3:p1:r:q1", "p1", "owner", {**base, "ext": "decisions/0.3", "id": "r:q1", "type": "raised",
                "origin": "requester", "by": "project:p1", "key": "q1", "terms": {"text": "快取用哪個?"}, "terms_rev": 1})
            put("rec:acts/0.3:p1:n1", "owner", "p1", {**base, "ext": "acts/0.3", "id": "n1", "type": "note",
                "origin": "principal", "by": "helper", "key": "k1", "about": {"quote": "續約網域"}, "note": "註冊商說週五前要續約"})
            put("rec:decisions/0.3:p1:s1", "owner", "p1", {**base, "ext": "decisions/0.3", "id": "s1", "type": "settled",
                "origin": "principal", "by": "helper", "key": "q1", "outcome": "decided", "terms_rev": 1,
                "answer": {"text": "用 SQLite"}, "authority": {"kind": "principal", "decider": "person:alex"}})
            put("rec:acts/0.3:p1:s2", "owner", "p1", {**base, "ext": "acts/0.3", "id": "s2", "type": "settled",
                "origin": "principal", "by": "helper", "key": "k2", "outcome": "done", "terms_rev": 1,
                "authority": {"kind": "principal", "decider": "person:alex"}})
            c.commit()
            self.assertEqual(env.run("librarian.py").returncode, 0)
            p1 = next(x["prompt"] for x in env.calls() if x["point"] == "librarian-state" and x["cwd"] == "p1")
            for want in ("[note]", "註冊商說週五前要續約", "續約網域", "[answers]", "用 SQLite", "快取用哪個?",
                         "Alex 本人決定的", "[dismissed]"):
                self.assertIn(want, p1)
            self.assertNotIn("k2", p1, "Acts 的 settled 歸 registrar")
            self.assertNotIn(":null", p1, "沒有理由的 note 不印 null")
            done = {r[0] for r in c.execute("SELECT ref FROM events WHERE type='message.done'")}
            self.assertLessEqual({"rec:acts/0.3:p1:n1", "rec:decisions/0.3:p1:s1"}, done)
            self.assertNotIn("rec:acts/0.3:p1:s2", done)

    @unittest.skipUnless(len(IMPLS) == 2, "只剩一個實作")
    def test_implementations_send_the_same_prompts(self):
        got = {}
        for impl in IMPLS:
            with tempfile.TemporaryDirectory() as d:
                env = Env(pathlib.Path(d))
                self.assertEqual(env.run(impl).returncode, 0)
                # commit 的 hash 每次不同(時間);其他一個字都不能差
                import re
                got[impl] = [{**c, "prompt": re.sub(r"\b[0-9a-f]{7,40}\b", "<sha>", c["prompt"])} for c in env.calls()]
        self.assertEqual(got["librarian.sh"], got["librarian.py"])


if __name__ == "__main__":
    unittest.main()
