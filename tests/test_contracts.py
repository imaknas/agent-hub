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


class LlmPoints(unittest.TestCase):
    """判斷點登錄的共用格式(contracts/llm-points.v1.schema.json):自己的登錄要合格,另一邊的寫法也要合格。"""

    def setUp(self):
        import minischema
        self.errors = minischema.errors
        self.schema = json.loads((C / "llm-points.v1.schema.json").read_text())

    def test_own_registry_valid(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("需要 PyYAML")
        self.assertEqual(self.errors(self.schema, yaml.safe_load((C / "llm-points.yaml").read_text())), [])

    def test_other_side_example_valid(self):
        """另一邊的設計文件裡的範例(只有共用欄位 + 它自己的選填欄位)。"""
        doc = {"v": 1, "points": {"packet_fit": {
            "caller": "core/prepare.py", "purpose": "判斷職缺對不對口", "model_group": "draft", "material": "personal",
            "output": {"format": "json", "schema": "llm/packet_fit.schema.json"}, "on_failure": "retry_later",
            "eval": {"scorer": "field:verdict", "truth": "使用者後來的回答"},
            "kind": "decide", "labels": ["fit", "not_fit"]}}}
        self.assertEqual(self.errors(self.schema, doc), [])

    def test_rejects_unknown_field_and_old_values(self):
        bad = {"v": 1, "points": {"x": {"caller": "a", "purpose": "b", "output": {"format": "text"},
                                        "on_failure": "caller", "surprise": 1}}}
        errs = " ".join(self.errors(self.schema, bad))
        self.assertIn("on_failure", errs)
        self.assertIn("surprise", errs)

    def test_shared_fields_required(self):
        errs = " ".join(self.errors(self.schema, {"v": 1, "points": {"x": {"caller": "a"}}}))
        for f in ("purpose", "output", "on_failure"):
            self.assertIn(f, errs)


class Neutral(unittest.TestCase):
    def test_consumer_facing_contracts_dont_name_the_platform(self):
        """給別的 repo vendor 的契約跟範例不能提這個平台(有開源的 repo)。"""
        for p in [C / "todo-md.v1.md", C / "state-todo.v1.md", C / "reflect-week.v1.schema.json", *F.glob("*.md")]:
            text = p.read_text()
            for word in ("agent-hub", "librarian", "bin/"):
                self.assertNotIn(word, text, f"{p.name} 提到 {word}")
        for p in [C / "lease-runner.v2.schema.json", C / "lease-handover.v1.schema.json",
                  C / "llm-points.v1.schema.json"]:  # muninn 也會 vendor
            text = p.read_text()
            for word in ("agent-hub", "librarian", "bin/", "Knas", "muninn"):
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
        """每個判斷點宣告的 output.schema 都存在(必填欄位交給 LlmPoints 用共用格式驗證)。"""
        try:
            import yaml
        except ImportError:
            self.skipTest("需要 PyYAML")
        points = yaml.safe_load((C / "llm-points.yaml").read_text())["points"]
        self.assertGreaterEqual(len(points), 6)
        for name, spec in points.items():
            schema = (spec.get("output") or {}).get("schema")
            if schema:
                self.assertTrue((C / schema).exists(), f"{name}:{schema}")

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
