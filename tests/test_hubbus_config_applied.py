"""匯流排的 config.applied 事件(new-user.md 3.7 的 record):hubbus publish 收得下,契約驗得過、驗得出錯的。

    bin/py -m unittest discover -s tests
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import minischema  # noqa: E402

SCHEMA = json.loads((ROOT / "contracts" / "hubbus.v1.schema.json").read_text())


def event(payload, type_="config.applied"):
    return {"seq": 1, "id": "cfg-1", "ts": "2026-10-26T21:00:00+08:00", "type": type_, "source": "agent-hub",
            "to_project": None, "ref": None, "payload": payload}


class ConfigApplied(unittest.TestCase):
    def test_valid_event(self):
        ok = {"v": 1, "commit": "abc1234", "source": "hub config apply",
              "changes": [{"op": "update", "target": "local.agent-hub.lease-runner", "detail": "ProgramArguments"}]}
        self.assertEqual(minischema.errors(SCHEMA, event(ok)), [])
        self.assertEqual(minischema.errors(SCHEMA, event({"v": 1, "commit": "abc", "changes": []})), [])

    def test_bad_payloads_are_caught(self):
        for bad in ({"v": 1, "changes": []},                                          # 沒有 commit
                    {"v": 1, "commit": "abc", "changes": [{"op": "rename", "target": "x"}]},   # op 不在列表
                    {"v": 1, "commit": "abc", "changes": [{"op": "add"}]}):            # 沒有 target
            with self.subTest(bad=bad):
                self.assertTrue(minischema.errors(SCHEMA, event(bad)))

    def test_other_types_unaffected(self):
        self.assertEqual(minischema.errors(SCHEMA, event({"v": 1, "job": "x"}, "job.failed")), [])

    def test_publish_accepts_the_type(self):
        src = (ROOT / "bin" / "hubbus").read_text()
        self.assertIn('"config.applied"', src[src.index('pb.add_argument("type"'):][:300])


if __name__ == "__main__":
    unittest.main()
