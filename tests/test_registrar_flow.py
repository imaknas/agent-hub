"""任務登記(Principal Handoff Acts 的 registrar)的行為測試:整支跑一次,自己帶 memory repo、匯流排、hub.json,不碰真的資料。

同一張情境表對每個在 bin/ 底下的實作都跑:
  舊的實作       匯流排上用 v1 的 task / done --inferred 信件跟個人助理的 handoff
  registrar.py   (新)匯流排上用 0.3 的 record 事件(hubbus record-publish / records)
舊的只跑它做得到的情境(V1);新的全跑。這是舊的換成 registrar.py 之前鎖住的行為,換完是回歸測試。
舊的在 pe、pg 有已知的錯,所以不算進 V1:改寫的比對拿整行(含「- [ ] 〔Alex〕」)去比,幾乎不會判定成改寫;而且先檢查
消失的 key 才寄新行,同文字掉了 key 的行會先寄一個錯的「推斷做完」再蓋回同一個 key。規範的順序(relink 在 reconcile 前)修掉這兩個。

  pa  沒有 key 的〔Alex〕行             → 寄出 raised / task、行尾蓋章
  pb  帶 key 的行 + 結案(done)         → 刪掉那一行,段落空了補「- (無)」,結案標成 done
  pc  被刪掉的 key + 重開(reopened)    → 加回那一行
  pd  寄過、沒結案、STATE.md 找不到      → 寄一次推斷做完的 claim(第二次跑不再寄)
  pe  同上,但有很像的行(沒 key)        → 舊的 key 不寄 claim、只警告;那一行照新的一件 raised
  pf  帶 key 的行被改了字               → amended(新的才有)
  pg  掉了 key 的行,文字跟原本一樣       → 補回原本的 key,不寄新的 raised(新的才有)
  ph  需求已 withdrawn、行也刪了          → 不寄 claim(新的才有)

    bin/py -m unittest discover -s tests
"""
import hashlib
import json
import os
import pathlib
import sqlite3
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
OLD, NEW = "knas-tasks.py", "registrar.py"   # 舊的檔名在 registrar.py 換上去之後跟著刪掉
IMPLS = [p for p in (OLD, NEW) if (ROOT / "bin" / p).exists()]
TAG, OWNER, PRINCIPAL, SIDE = "Alex", "owner", "person:alex", "helper"
V1 = {"pa", "pb", "pc", "pd"}   # 舊的做得到的情境(pe、pg 是舊的已知錯,見上)


def key(project: str, text: str) -> str:
    return f"task-{project}-" + hashlib.sha1(" ".join(text.split()).encode()).hexdigest()[:10]


def marker(k: str) -> str:
    return f"(等 {TAG}:{k})"


def state(*todo: str) -> str:
    return "# STATE\n\n## 現況\n\n測試專案。\n\n## 待辦\n\n" + "\n".join(todo) + "\n"


KB, KC, KD, KE = key("pb", "付網域"), key("pc", "簽合約"), key("pd", "買零件"), key("pe", "續約保險")
KF, KG, KH = key("pf", "訂機票"), key("pg", "繳稅"), key("ph", "回信給房東")
SCENES = {
    "pa": {"state": state("- [ ] 〔Alex〕續約網域", "- [ ] 〔agent〕加重試")},
    "pb": {"state": state(f"- [ ] 〔Alex〕付網域{marker(KB)}"), "posted": {KB: "付網域"},
           "settle": [("s-pb", KB, "done", "付網域")]},
    "pc": {"state": state("- [ ] 〔agent〕別的事"), "posted": {KC: "簽合約"},
           "settle": [("s-pc", KC, "reopened", "簽合約")]},
    "pd": {"state": state("- [ ] 〔agent〕別的事"), "posted": {KD: "買零件"}},
    "pe": {"state": state("- [ ] 〔Alex〕續約保險(今年)"), "posted": {KE: "續約保險"}},
    "pf": {"state": state(f"- [ ] 〔Alex〕訂機票,要靠走道{marker(KF)}"), "posted": {KF: "訂機票"}},
    "pg": {"state": state("- [ ] 〔Alex〕繳稅"), "posted": {KG: "繳稅"}},
    "ph": {"state": state("- [ ] 〔agent〕別的事"), "posted": {KH: "回信給房東"}, "withdrawn": [KH]},
}


def sh(*cmd, cwd=None, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


class Env:
    """暫存 hub(bin/ 連到真的)+ 每個專案一組 bare repo 跟 memory 副本 + 匯流排。"""

    def __init__(self, tmp: pathlib.Path, impl: str):
        self.tmp, self.hub, self.impl = tmp, tmp / "hub", impl
        (self.hub / "bin").mkdir(parents=True)
        for f in (ROOT / "bin").iterdir():
            if f.is_file():
                (self.hub / "bin" / f.name).symlink_to(f)
        for d in ("projects", ".state", ".logs", "data"):
            (self.hub / d).mkdir()
        (self.hub / "data" / "openclaw-migration-status.md").write_text("# status\n\n## 待辦\n\n- (無)\n")
        (self.hub / "hub.json").write_text(json.dumps({
            "v": 1, "registry_dir": "projects", "memory_copies": str(tmp / "copies"), "git_origin": str(tmp / "bare"),
            "bus_dir": str(tmp / "bus"),
            "owner": {"address": OWNER, "aliases": ["alex"], "tag": TAG, "principal_id": PRINCIPAL},
            "assistant": {"project": SIDE, "api": "http://127.0.0.1:9/x", "party_id": SIDE}}))
        e = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "AGENT_HUB_HOME", "AGENT_HUB_CONFIG",
                                                                     "AGENT_HUB_DATA", "AGENT_HUB_SECRETS"))}
        self.env = {**e, "AGENT_HUB_ROOT": str(self.hub), "AGENT_HUB_DATA": str(self.hub / "data"),
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        self.hubbus("inbox", "--project", "pa")   # 建匯流排的表
        self.db = tmp / "bus" / "bus.db"
        self.scenes = {p: s for p, s in SCENES.items() if impl == NEW or p in V1}
        for p, s in self.scenes.items():
            self.project(p, s)

    def hubbus(self, *args, stdin=None):
        return subprocess.run([str(self.hub / "bin" / "py"), str(self.hub / "bin" / "hubbus"), *args], env=self.env,
                              input=stdin, capture_output=True, text=True)

    def git(self, repo, *args):
        return sh("git", "-C", str(repo), *args, env=self.env)

    def project(self, p, s):
        bare, copy = self.tmp / "bare" / f"{p}-memory.git", self.tmp / "copies" / p
        bare.parent.mkdir(exist_ok=True)
        sh("git", "init", "-q", "--bare", "-b", "agent-memory", str(bare), env=self.env)
        sh("git", "clone", "-q", str(bare), str(copy), env=self.env)
        (copy / "STATE.md").write_text(s["state"])
        self.git(copy, "add", "-A"); self.git(copy, "commit", "-qm", "init"); self.git(copy, "push", "-q", "origin", "HEAD")
        (self.hub / "projects" / f"{p}.yaml").write_text(f"v: 1\nname: {p}\npaths:\n  workspace: {copy}\nmemory: true\n")
        if self.impl == OLD:
            self.v1_inputs(p, s)
        else:
            self.v2_inputs(p, s)

    # --- 舊:v1 信件 ---
    def v1_inputs(self, p, s):
        c = sqlite3.connect(self.db)
        for k, text in s.get("posted", {}).items():
            c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                      (k, "2026-10-20T10:00:00+08:00", "message", p, OWNER, None,
                       json.dumps({"v": 1, "type": "task", "text": text}, ensure_ascii=False)))
        for hid, k, effect, text in s.get("settle", []):
            payload = {"v": 1, "type": "handoff", "handoff_id": abs(hash(hid)) % 10000, "effect": effect,
                       "handoff": {"reply_to": k, "item": text, "fact": text, "why": "測試", "effect": effect,
                                   "ts": "2026-10-26T10:00:00"}}
            c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                      (hid, "2026-10-26T10:00:00+08:00", "message", OWNER, p, None, json.dumps(payload, ensure_ascii=False)))
        c.commit(); c.close()

    def v1_outputs(self, p):
        """這次跑寄給 owner 的信:[(種類, key)],種類 raised 或 claim。"""
        c = sqlite3.connect(self.db)
        rows = c.execute("SELECT id, payload FROM events WHERE type='message' AND source=? AND to_project=?"
                         " AND ts > '2026-10-27'", (p, OWNER)).fetchall()
        out = []
        for eid, pl in rows:
            d = json.loads(pl)
            if d.get("type") == "task":
                out.append(("raised", eid))
            elif d.get("type") == "done" and d.get("inferred"):
                out.append(("claim", d.get("task")))
        return sorted(out)

    # --- 新:0.3 records ---
    def rec(self, p, rid, type_, origin, by, k, **kw):
        return {"ph": "0.3", "ext": "acts/0.3", "id": rid, "type": type_, "origin": origin, "by": by, "key": k,
                "project": p, "ts": "2026-10-20T10:00:00Z", **kw}

    def v2_inputs(self, p, s):
        recs = []
        for k, text in s.get("posted", {}).items():
            recs.append(self.rec(p, f"r:{k}", "raised", "requester", "agent-hub:registrar", k,
                                 terms={"text": text}, terms_rev=1))
        for k in s.get("withdrawn", []):
            recs.append(self.rec(p, f"w:{k}", "withdrawn", "requester", f"project:{p}", k, reason="not_needed",
                                 supersedes=[f"r:{k}"], ts="2026-10-21T10:00:00Z"))
        if recs:
            r = self.hubbus("record-publish", "--json", "-", stdin=json.dumps(recs, ensure_ascii=False))
            assert r.returncode == 0, r.stdout + r.stderr
        settles = []
        for hid, k, effect, text in s.get("settle", []):
            if effect == "reopened":
                settles.append(self.rec(p, hid, "reopened", "principal", SIDE, k, supersedes=["s-earlier"],
                                        restore_text=text, ts="2026-10-26T10:00:00Z"))
            else:
                settles.append(self.rec(p, hid, "settled", "principal", SIDE, k, outcome=effect, terms_rev=1,
                                        authority={"kind": "principal", "decider": PRINCIPAL}, ts="2026-10-26T10:00:00Z"))
        if settles:   # 從 principal 端來的:直接寫成匯流排的 record 事件(bridge 往內做的事)
            c = sqlite3.connect(self.db)
            for r in settles:
                c.execute("INSERT INTO events(id, ts, type, source, to_project, ref, payload) VALUES(?,?,?,?,?,?,?)",
                          (f"rec:acts:{p}:{r['id']}", "2026-10-26T10:00:00+08:00", "record", OWNER, p, None,
                           json.dumps({"v": 1, "record": r}, ensure_ascii=False)))
            c.commit(); c.close()

    def v2_outputs(self, p):
        r = self.hubbus("records", "--project", p, "--direction", "out")
        out = []
        for e in json.loads(r.stdout or "[]"):
            rec = e["record"]
            if rec["ts"] < "2026-10-27" or rec["type"] == "withdrawn":
                continue
            kind = {"raised": "raised", "amended": "amended", "claimed": "claim"}.get(rec["type"])
            if kind:
                out.append((kind, rec["key"]))
        return sorted(out)

    # --- 共用 ---
    def run(self):
        return subprocess.run([str(self.hub / "bin" / "py"), str(self.hub / "bin" / self.impl)], cwd=self.hub,
                              env=self.env, capture_output=True, text=True, timeout=300)

    def outputs(self, p):
        return self.v1_outputs(p) if self.impl == OLD else self.v2_outputs(p)

    def state(self, p):
        return (self.tmp / "copies" / p / "STATE.md").read_text()

    def remote_state(self, p):
        return self.git(self.tmp / "bare" / f"{p}-memory.git", "show", "agent-memory:STATE.md")


@unittest.skipUnless(IMPLS, "沒有任務登記的實作")
class RegistrarFlow(unittest.TestCase):
    def each(self, check):
        for impl in IMPLS:
            with self.subTest(impl=impl), tempfile.TemporaryDirectory() as d:
                env = Env(pathlib.Path(d), impl)
                r = env.run()
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                check(env, r.stdout + r.stderr)

    def test_scenarios(self):
        def check(env, log):
            ka = key("pa", "續約網域")
            self.assertIn(marker(ka), env.state("pa"))
            self.assertIn(marker(ka), env.remote_state("pa"), "蓋章要 commit 而且 push")
            self.assertEqual(env.outputs("pa"), [("raised", ka)])
            self.assertNotIn("付網域", env.state("pb"))
            self.assertIn("- (無)", env.state("pb"))
            self.assertIn(f"〔Alex〕簽合約{marker(KC)}", env.state("pc"))
            self.assertEqual(env.outputs("pd"), [("claim", KD)])
            if env.impl == NEW:
                self.assertEqual(env.outputs("pe"), [("raised", key("pe", "續約保險(今年)"))])
                self.assertIn("續約保險(今年)", env.state("pe"))
                self.assertEqual(env.outputs("pf"), [("amended", KF)])
                self.assertIn(marker(KG), env.state("pg"))
                self.assertEqual(env.outputs("pg"), [])
                self.assertEqual(env.outputs("ph"), [])
        self.each(check)

    def test_second_run_sends_nothing_new(self):
        def check(env, log):
            first = {p: env.outputs(p) for p in env.scenes}
            r = env.run()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual({p: env.outputs(p) for p in env.scenes}, first)
        self.each(check)


if __name__ == "__main__":
    unittest.main()
