"""agent-hub 跑 Human Task Handoff(HTH 0.1,agent-ops-specs)的一致性測試資料:證明已經在跑的實作(gen-todo 的解析、
任務登記的 key 跟套用)符合規範。測試資料 vendor 在 contracts/vendor/agent-ops-specs/hth-0.1/(來源 commit 見 SOURCE)。

只跑 zh-hant profile(agent-hub 用的那個);en profile 是規範的預設,agent-hub 沒有實作它。
資料裡 owner 的標籤是 `Owner`,照 conformance/README 的規則換成這台設定的 owner.tag 再比。
套用的路由:agent-hub 只套用 owner 那邊送回來的事件(hubbus open-handoffs 只收 owner 地址寄的轉交);agent 在
session 裡回報的、registrar 自己推斷的 done 不會走到 apply_keyed——所以那兩種案例在這裡就是「沒有改」。

    bin/py -m unittest discover -s tests
"""
import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIX = ROOT / "contracts" / "vendor" / "agent-ops-specs" / "hth-0.1"


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


gen_todo = load("gen_todo", "gen-todo.py")
tasks = load("hth_tasks", "knas-tasks.py")  # 任務登記;改名在 new-user 設計的待辦
TAG = gen_todo.TAG


def cases(file: str):
    return [c for c in json.loads((FIX / file).read_text())["cases"] if c.get("profile", "zh-hant") == "zh-hant"]


def own(x):
    """測試資料的 owner 標籤 `Owner` 換成這台的 owner.tag(標籤跟 key 標記裡的,含「等Owner」不帶空格的寫法)。"""
    if isinstance(x, str):
        return x.replace("〔Owner〕", f"〔{TAG}〕").replace("等 Owner", f"等 {TAG}").replace("等Owner", f"等{TAG}")
    if isinstance(x, list):
        return [own(v) for v in x]
    if isinstance(x, dict):
        return {k: own(v) for k, v in x.items()}
    return x


def lines_cmp(lines):
    """SPEC §8.2:比非空行、去掉行尾空白。"""
    return [line.rstrip() for line in lines if line.strip()]


ASSIGNEE = {TAG: "owner", "agent": "agent", "待定": "undecided"}


class Parse(unittest.TestCase):
    def test_parse_vectors(self):
        cs = cases("parse.json")
        self.assertTrue(cs)
        for c in cs:
            with self.subTest(case=c["id"]):
                text = "\n".join(own(c["document"])) + "\n"
                exp = c["expect"]
                self.assertEqual(gen_todo.todo_section(text) is not None, exp["section"])
                items = {o: [] for o in gen_todo.OWNERS}
                problems: list = []
                gen_todo.parse("demo", text, items, problems)
                got = []
                for owner, rows in items.items():
                    for _, t in rows:
                        m = gen_todo.OWNER_KEY.search(t)
                        got.append((ASSIGNEE[owner], (t[:m.start()] if m else t).strip(), m.group(1) if m else None))
                want = [(t["assignee"], own(t["text"]), t["key"])
                        for t in exp["tasks"]]
                self.assertEqual(sorted(got), sorted(want))
                self.assertEqual(len([p for p in problems if "沒有 `## 待辦`" not in p]), len(exp["problems"]))


class Keys(unittest.TestCase):
    def test_example_recipe(self):
        cs = json.loads((FIX / "keys.json").read_text())["cases"]
        self.assertTrue(cs)
        for c in cs:
            with self.subTest(case=c["id"]):
                self.assertEqual(tasks.task_key(c["project"], c["text"]), c["key"])


class Apply(unittest.TestCase):
    def test_apply_vectors(self):
        cs = cases("apply.json")
        self.assertTrue(cs)
        for c in cs:
            with self.subTest(case=c["id"]):
                lines = own(c["document"])
                ev = c["event"]
                routed = ev.get("origin") == "owner" and not ev.get("inferred")
                changed = tasks.apply_keyed(lines, {"effect": ev["type"], "reply_to": ev["key"],
                                                         "item": ev.get("text")}) if routed else False
                self.assertEqual(changed, c["expect"]["changed"])
                self.assertEqual(lines_cmp(lines), lines_cmp(own(c["expect"]["document"])))


if __name__ == "__main__":
    unittest.main()
