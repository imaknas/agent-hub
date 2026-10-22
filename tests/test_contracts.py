"""② 檢查:這個 repo 的範例資料照自己的契約(跟 vendor 的副本)驗證。不依賴別的 repo 在不在。

    python3 -m unittest discover -s tests
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import contracts  # noqa: E402

C = ROOT / "contracts"
F = C / "fixtures"
OWNER = "我"  # 範例檔用中性的 owner 標籤;實際檔案用設定裡的(hub.json owner.tag)


class Markdown(unittest.TestCase):
    def test_todo_md_fixture(self):
        self.assertEqual(contracts.check_todo_md((F / "todo-md.v1.example.md").read_text(), C / "todo-md.v1.md", OWNER), [])

    def test_todo_md_rejects_renamed_heading(self):
        bad = (F / "todo-md.v1.example.md").read_text().replace("## 只有 我 能做", "## 我的事")
        self.assertTrue(contracts.check_todo_md(bad, C / "todo-md.v1.md", OWNER))

    def test_state_todo_fixture(self):
        self.assertEqual(contracts.check_state_todo((F / "state-todo.v1.example.md").read_text(),
                                                    C / "state-todo.v1.md", OWNER), [])

    def test_state_todo_key_regex(self):
        import re
        key = re.compile(contracts.md_rules(C / "state-todo.v1.md", OWNER)["regex:key"][0])
        m = key.search("- [ ] 〔我〕x(等 我:task-p-0123456789)")
        self.assertEqual(m.group(1), "task-p-0123456789")


class Neutral(unittest.TestCase):
    def test_consumer_facing_contracts_dont_name_the_platform(self):
        """給別的 repo vendor 的契約跟範例不能提這個平台(有開源的 repo)。"""
        for p in [C / "todo-md.v1.md", C / "state-todo.v1.md", C / "reflect-week.v1.schema.json", *F.glob("*.md")]:
            text = p.read_text()
            for word in ("agent-hub", "librarian", "bin/"):
                self.assertNotIn(word, text, f"{p.name} 提到 {word}")
        for p in F.glob("*"):  # 範例檔也不放使用者的名字(owner 用中性的「我」)
            self.assertNotIn("Knas", p.read_text(), f"{p.name} 提到使用者的名字")

    def test_same_ignores_prose(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            a, b = pathlib.Path(d, "a.md"), pathlib.Path(d, "b.md")
            a.write_text((C / "todo-md.v1.md").read_text())
            b.write_text((C / "todo-md.v1.md").read_text().replace("讀的人", "使用的人"))
            self.assertTrue(contracts.same(a, b))
            b.write_text((C / "todo-md.v1.md").read_text().replace("^- \\[ \\] (.+)$", "^\\* (.+)$"))
            self.assertFalse(contracts.same(a, b))


class Json(unittest.TestCase):
    def test_schemas_parse(self):
        for p in list(C.glob("*.schema.json")) + list((C / "vendor").rglob("*.schema.json")) + list((C / "llm").glob("*.json")):
            json.loads(p.read_text())

    def test_llm_points_declare_existing_schemas(self):
        import re
        text = (C / "llm-points.yaml").read_text()
        for schema in re.findall(r"schema: (\S+)", text):
            self.assertTrue((C / schema).exists(), schema)
        for need in ("on_failure", "purpose", "caller"):
            self.assertGreaterEqual(text.count(f"    {need}:"), 6, need)

    def test_hubbus_message(self):
        ev = {"seq": 1, "id": "m1", "ts": "2026-10-22T00:00:00+08:00", "type": "message", "source": "example",
              "to_project": "owner", "ref": None, "payload": {"v": 1, "type": "task", "text": "x", "due": "2026-11-01"}}
        self.assertEqual(contracts.check_json(C / "hubbus.v1.schema.json", ev), [])
        ev["payload"]["type"] = "nope"
        self.assertTrue(contracts.check_json(C / "hubbus.v1.schema.json", ev))

    def test_vendored_messages_drop_needs_task(self):
        s = C / "vendor" / "muninn" / "messages.v1.schema.json"
        self.assertTrue(contracts.check_json(s, {"messages": [{"id": "abcd", "project": "p", "type": "drop", "text": "t"}]}))
        self.assertEqual(contracts.check_json(s, {"messages": [{"id": "abcd", "project": "p", "type": "drop",
                                                                "text": "t", "task": "k"}]}), [])

    def test_vendored_handoffs_fixture(self):
        self.assertEqual(contracts.check_json(C / "vendor" / "muninn" / "handoffs.v1.schema.json",
                                              json.loads((F / "muninn-handoffs.v1.example.json").read_text())), [])


if __name__ == "__main__":
    unittest.main()
