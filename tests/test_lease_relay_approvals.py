"""lease-relay.py 照中立政策(policy.approvals)決定(docs/design/lease-contract-neutral.md)。

work 模式的寫檔/指令在 test_lease_relay_work.py;這裡測開網頁、提問、送助理的請求格式(v1/v2)、政策本身。
送手機(ask_owner 的 HTTP)跟回報 runner 都換成假的,不發任何請求。

    /opt/homebrew/bin/python3 -m unittest discover -s tests
"""
import importlib.util
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import policy  # noqa: E402
import runtime_ports  # noqa: E402


def load_relay():
    spec = importlib.util.spec_from_file_location("lease_relay_ap", ROOT / "bin" / "lease-relay.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Policy(unittest.TestCase):
    def test_every_kind_has_a_rule_in_both_modes(self):
        for mode in ("discuss", "work"):
            rules = policy.approvals("x", mode, doc={})["rules"]
            self.assertEqual(set(rules), set(runtime_ports.KINDS), mode)

    def test_registry_network_is_web_allowed(self):
        self.assertEqual(policy.approvals("x", "discuss", doc={"network": ["docs.example"]})["web_allowed"],
                         ["docs.example"])


class Relay(unittest.TestCase):
    def setUp(self):
        self.m = load_relay()
        self.m.MODE = "discuss"
        self.asked, self.reported = [], []
        self.answer = {"decision": "allow"}
        self.m.ask_owner = lambda rid, tool, summary, risk, q: (self.asked.append((tool, summary, risk)), self.answer)[1]
        self.m.tell_runner = lambda phase, **kw: self.reported.append((phase, kw))
        self.ap = {"network": ["docs.example"]}
        real = policy.approvals  # 跟 relay 用的是同一個模組:先拿到原本的,再換掉
        p = mock.patch.object(self.m.policy, "approvals", lambda name, mode: real(name, mode, doc=self.ap))
        p.start()
        self.addCleanup(p.stop)

    def call(self, tool, **inp):
        return self.m.decide({"tool_name": tool, "input": inp, "tool_use_id": "t1"})

    def test_listed_site_allowed_without_asking(self):
        self.assertEqual(self.call("WebFetch", url="https://docs.example/a")["behavior"], "allow")
        self.assertEqual(self.asked, [])

    def test_unlisted_site_asks_and_allow_session_remembers(self):
        self.answer = {"decision": "allow_session"}
        self.assertEqual(self.call("WebFetch", url="https://news.example/a")["behavior"], "allow")
        self.assertEqual(self.call("WebFetch", url="https://news.example/b")["behavior"], "allow")
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(self.reported[0][1]["kind"], "open_web")

    def test_no_session_domain_only_this_once(self):
        self.answer = {"decision": "allow_session"}
        self.call("WebFetch", url="https://webhook.site/x")
        self.call("WebFetch", url="https://webhook.site/y")
        self.assertEqual(len(self.asked), 2)

    def test_question_with_multi_answer(self):
        self.answer = {"decision": "allow", "answers": {"哪些?": ["臥室", "客廳"]}}
        r = self.call("AskUserQuestion", questions=[{"question": "哪些?", "multiSelect": True,
                                                     "options": [{"label": "臥室"}, {"label": "客廳"}]}])
        self.assertEqual(r["updatedInput"]["answers"], {"哪些?": "臥室, 客廳"})
        self.assertEqual(self.reported[0][1]["kind"], "ask_user")

    def test_discuss_denies_everything_else(self):
        for tool, inp in (("Write", {"file_path": "a"}), ("Bash", {"command": "ls"}), ("TodoWrite", {})):
            self.assertEqual(self.call(tool, **inp)["behavior"], "deny", tool)
        self.assertEqual(self.asked, [])


class RequestBody(unittest.TestCase):
    def setUp(self):
        self.m = load_relay()
        self.q = {"questions": [{"question": "測哪個?", "header": "h", "multiSelect": False,
                                 "options": [{"label": "臥室", "description": "小"}, {"label": "客廳"}]}]}

    def body(self, v, *args):
        with mock.patch.object(self.m.hubconf, "get", lambda k, d=None: v if k == "lease.permission_requests_v" else d):
            return self.m.request_body("L:t1", *args)

    def test_v1_unchanged(self):
        b = self.body(1, "WebFetch", "https://a", "external_fetch", None)
        self.assertEqual(set(b), {"v", "id", "lease", "project", "tool", "input_summary", "risk"})
        self.assertEqual(self.body(1, "AskUserQuestion", "q", "question", self.q)["question"], self.q)

    def test_v2_is_runtime_neutral(self):
        b = self.body(2, "WebFetch", "https://a", "external_fetch", None)
        self.assertEqual((b["v"], b["kind"], b["target"], b["detail"]), (2, "open_web", "https://a", {"tool": "WebFetch"}))
        self.assertNotIn("tool", b)
        q = self.body(2, "AskUserQuestion", "測哪個?", "question", self.q)
        self.assertEqual(q["question"], {"questions": [{"question": "測哪個?", "multi": False, "options": [
            {"label": "臥室", "description": "小"}, {"label": "客廳"}]}]})
        self.assertEqual(self.body(2, "Bash", "ls", "run_command", None)["kind"], "run_command")


if __name__ == "__main__":
    unittest.main()
