"""agent-hub 跑 Principal Handoff Acts 0.3 的一致性測試資料(agent-ops-specs,來源見 contracts/vendor/.../SOURCE):
證明 bin/registrar.py(登記的各步)跟 bin/gen-todo.py(待辦段落的分類)符合規範。

只跑 zh-hant profile(agent-hub 用的那個)。資料裡 owner 的標籤是 `Owner`,照 conformance/README 的規則換成這台
設定的 owner.tag 再比。parse 的完整分類(agent、待定、格式問題)看 gen-todo;registrar 只處理 owner 的行。

    bin/py -m unittest discover -s tests
"""
import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIX = ROOT / "contracts" / "vendor" / "agent-ops-specs" / "principal-handoff-0.3" / "acts"


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


registrar = load("registrar", "registrar.py")
gen_todo = load("gen_todo", "gen-todo.py")
TAG = registrar.TAG


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


def nonblank(lines):
    """acts §9.2:比非空行、去掉行尾空白。"""
    return [l.rstrip() for l in lines if l.strip()]


ASSIGNEE = {TAG: "principal", "agent": "agent", "待定": "undecided"}


class Acts(unittest.TestCase):
    def test_parse(self):
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
                        m = registrar.KEY.search(t)
                        got.append((ASSIGNEE[owner], (t[:m.start()] if m else t).strip(), m.group(1) if m else None))
                want = [(t["assignee"], own(t["text"]), t["key"]) for t in exp["tasks"]]
                self.assertEqual(sorted(got), sorted(want))
                self.assertEqual(len([p for p in problems if "沒有 `## 待辦`" not in p]), len(exp["problems"]))
                # registrar 看到的 owner 行跟規範一樣
                mine = [(t["text"], t["key"]) for t in registrar.owner_lines(own(c["document"]))]
                self.assertEqual(sorted(mine), sorted((own(t["text"]), t["key"]) for t in exp["tasks"]
                                                      if t["assignee"] == "principal"))

    def test_keys(self):
        for c in json.loads((FIX / "keys.json").read_text())["cases"]:
            with self.subTest(case=c["id"]):
                self.assertEqual(registrar.task_key(c["project"], c["text"]), c["key"])

    def test_register(self):
        cs = cases("register.json")
        self.assertTrue(cs)
        for c in cs:
            with self.subTest(case=c["id"]):
                lines = own(c["document"])
                raised = []
                for i, k, text in registrar.candidates(lines, c["project"]):
                    raised.append({"key": k, "text": text})
                    lines[i] = lines[i].rstrip() + registrar.stamp(k)
                self.assertEqual(raised, own(c["expect"]["raised"]))
                self.assertEqual(nonblank(lines), nonblank(own(c["expect"]["document"])))
                self.assertEqual(registrar.candidates(lines, c["project"]), [], "第二次不再送")

    def test_relink(self):
        cs = cases("relink.json")
        self.assertTrue(cs)
        for c in cs:
            with self.subTest(case=c["id"]):
                lines = own(c["document"])
                self.assertEqual(registrar.relink(lines, own(c["vanished"])), c["expect"]["relinked"])
                self.assertEqual(nonblank(lines), nonblank(own(c["expect"]["document"])))

    def test_amend(self):
        cs = cases("amend.json")
        self.assertTrue(cs)
        for c in cs:
            with self.subTest(case=c["id"]):
                self.assertEqual(registrar.amendments(own(c["document"]), own(c["latest"])), own(c["expect"]))

    def test_apply(self):
        cs = cases("apply.json")
        self.assertTrue(cs)
        for c in cs:
            with self.subTest(case=c["id"]):
                lines = own(c["document"])
                self.assertEqual(registrar.apply(lines, own(c["record"])), c["expect"]["changed"])
                self.assertEqual(nonblank(lines), nonblank(own(c["expect"]["document"])))

    def test_reconcile(self):
        cs = cases("reconcile.json")
        self.assertTrue(cs)
        for c in cs:
            with self.subTest(case=c["id"]):
                self.assertEqual(registrar.reconcile(own(c["document"]), own(c["open"])), c["expect"])


if __name__ == "__main__":
    unittest.main()
