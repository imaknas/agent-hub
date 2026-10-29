"""hubbus send 翻成 Principal Handoff 0.3 的 Acts record(階段 3,docs/design/principal-handoff-0.3-migration.md「方向一」)。

開關 hub.json mailbox.records(預設關):關的時候跟以前一模一樣(v1 信);開了以後寄給 owner 的 task/request → raised、
done --task → claimed、drop --task → withdrawn(不需要了),不再寄 v1 信。--inferred 由 registrar 自己寄(要算 reopened 次數),
send 不翻、開了就擋。question / info / progress 這一步還是 v1(Decisions 跟讀問題的那幾支一起換)。

    bin/py -m unittest discover -s tests
"""
import importlib.machinery
import importlib.util
import io
import pathlib
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
# acts/0.3 conformance/keys.json 的向量(規範的範例 key 做法;registrar 用同一個)
KEY_VECTORS = [("demo", "Renew the domain", "task-demo-c3d8af57d4"),
               ("demo", "  Renew   the\tdomain  ", "task-demo-c3d8af57d4"),
               ("demo", "Renew the domain.", "task-demo-67ed67cd37"),
               ("demo", "續約網域", "task-demo-544fefce49")]
# 每種 record 一定要有的(core + acts schema 的 required)
REQUIRED = {"raised": ("terms", "terms_rev"), "claimed": ("outcome", "evidence"), "withdrawn": ("reason",)}


def load():
    loader = importlib.machinery.SourceFileLoader("hubbus_sendrec", str(ROOT / "bin" / "hubbus"))
    spec = importlib.util.spec_from_loader("hubbus_sendrec", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


class SendRecords(unittest.TestCase):
    def setUp(self):
        m = self.m = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        m.DB = self.tmp / "bus.db"
        m.log = lambda s: None
        m.projects = lambda: {"proj": self.tmp, "other": self.tmp}
        self.c = m.db()
        m.db = lambda *a, **k: self.c
        self.owner = m.hubconf.owner_address()

    def send(self, *argv, records=True):
        self.m.RECORDS = records
        with mock.patch.object(sys, "argv", ["hubbus", "send", "--from", "proj", "--to", self.owner, *argv]), \
                redirect_stdout(io.StringIO()) as out:
            self.m.main()
        return out.getvalue().strip()

    def events(self, etype):
        return [(r["id"], self.m.payload(r)) for r in self.c.execute("SELECT * FROM events WHERE type=? ORDER BY seq",
                                                                      (etype,))]

    def rec(self):
        recs = self.events("record")
        self.assertEqual(len(recs), 1, recs)
        r = recs[0][1]["record"]
        self.assertEqual(self.m.record_errors(r), [])
        self.assertRegex(r["ts"], TS)
        for k in REQUIRED[r["type"]]:
            self.assertIn(k, r)
        self.assertEqual((r["ph"], r["ext"], r["project"]), ("0.3", "acts/0.3", "proj"))
        return recs[0][0], r

    # --- 開關關著:跟以前一樣 ---
    def test_off_is_v1_unchanged(self):
        mid = self.send("--type", "task", "--id", "task-proj-0000000001", "--text", "繳房屋稅", "--due", "2026-11-30",
                        records=False)
        self.assertEqual(mid, "task-proj-0000000001")
        msgs = self.events("message")
        self.assertEqual([(i, p["type"], p["text"], p["due"]) for i, p in msgs],
                         [("task-proj-0000000001", "task", "繳房屋稅", "2026-11-30")])
        self.assertEqual(self.events("record"), [])

    # --- 開了 ---
    def test_task_with_key_becomes_raised(self):
        out = self.send("--type", "task", "--id", "task-proj-0000000001", "--text", "繳房屋稅", "--due", "2026-11-30",
                        "--context", "migration", "--new-reason", "去年那筆是另一棟")
        eid, r = self.rec()
        self.assertEqual(out, eid)
        self.assertEqual(eid, "rec:acts/0.3:proj:r:task-proj-0000000001")
        self.assertEqual((r["type"], r["id"], r["key"], r["origin"], r["by"], r["terms_rev"]),
                         ("raised", "r:task-proj-0000000001", "task-proj-0000000001", "requester", "project:proj", 1))
        self.assertEqual(r["terms"], {"text": "繳房屋稅", "due": "2026-11-30"})
        self.assertEqual((r["context"], r["note"]), ("migration", "去年那筆是另一棟"))
        self.assertEqual(self.events("message"), [])   # 不再寄 v1 信

    def test_resend_same_key_is_noop(self):
        self.send("--type", "task", "--id", "task-proj-0000000001", "--text", "繳房屋稅")
        self.send("--type", "task", "--id", "task-proj-0000000001", "--text", "繳房屋稅")
        self.rec()

    def test_task_without_key_uses_the_registrar_recipe(self):
        for project, text, key in KEY_VECTORS:
            self.assertEqual(self.m.task_key(project, text), key)
        self.send("--type", "task", "--text", "  續約網域 ")
        _, r = self.rec()
        self.assertEqual(r["key"], self.m.task_key("proj", "續約網域"))
        self.assertEqual(r["terms"], {"text": "續約網域"})

    def test_request_to_owner_raises_too(self):
        self.send("--type", "request", "--text", "幫我簽合約")
        _, r = self.rec()
        self.assertEqual((r["type"], r["key"]), ("raised", self.m.task_key("proj", "幫我簽合約")))

    def test_done_task_becomes_claimed(self):
        out = self.send("--type", "done", "--task", "task-proj-0000000001", "--text", "已經繳了,收據在 docs/")
        eid, r = self.rec()
        self.assertEqual(out, eid)
        self.assertEqual((r["type"], r["key"], r["origin"], r["by"], r["outcome"]),
                         ("claimed", "task-proj-0000000001", "agent", "project:proj", "done"))
        self.assertEqual(r["evidence"], {"basis": "已經繳了,收據在 docs/"})
        self.assertTrue(r["id"].startswith("c:task-proj-0000000001:"))
        self.assertNotRegex(r["id"], r":r\d+$")   # 跟 registrar 的 c:<key>:r<n> 分開
        self.assertEqual(self.events("message"), [])

    def test_two_claims_get_distinct_ids(self):
        self.send("--type", "done", "--task", "task-proj-0000000001", "--text", "做了")
        self.send("--type", "done", "--task", "task-proj-0000000001", "--text", "真的做了")
        self.assertEqual(len({i for i, _ in self.events("record")}), 2)

    def test_drop_task_becomes_withdrawn_not_needed(self):
        self.send("--type", "drop", "--task", "task-proj-0000000001", "--text", "改用別的方案,不用買了")
        _, r = self.rec()
        self.assertEqual((r["type"], r["key"], r["origin"], r["by"], r["reason"], r["note"]),
                         ("withdrawn", "task-proj-0000000001", "requester", "project:proj", "not_needed",
                          "改用別的方案,不用買了"))

    def test_inferred_is_the_registrars_and_is_rejected(self):
        with self.assertRaises(SystemExit) as e:
            self.send("--type", "done", "--task", "task-proj-0000000001", "--inferred", "--text", "STATE.md 刪了")
        self.assertIn("record-publish", str(e.exception.code))
        self.assertEqual(self.events("record") + self.events("message"), [])

    def test_questions_and_progress_stay_v1_for_now(self):
        self.send("--type", "question", "--text", "要哪一家?")
        self.send("--type", "info", "--text", "部署好了")
        self.assertEqual([p["type"] for _, p in self.events("message")], ["question", "info"])
        self.assertEqual(self.events("record"), [])

    def test_done_without_task_to_owner_stays_v1(self):
        self.send("--type", "done", "--text", "那件事好了")
        self.assertEqual([p["type"] for _, p in self.events("message")], ["done"])

    def test_send_works_where_the_sandbox_forbids_ps(self):
        # 專案的沙盒不准跑 ps:看不到上層程序 = 不是平台代寄的工作,照常寄(10-29 resume 的 send 因此整個掛掉)
        real = self.m.subprocess.run
        def no_ps(cmd, *a, **k):
            if cmd and cmd[0] == "ps":
                raise PermissionError(1, "Operation not permitted", "ps")
            return real(cmd, *a, **k)
        with mock.patch.dict(self.m.os.environ, {}, clear=False), mock.patch.object(self.m.subprocess, "run", no_ps):
            self.m.os.environ.pop(self.m.PLATFORM_MAILS_ENV, None)
            self.assertIsNone(self.m.platform_mails())
            self.send("--type", "question", "--text", "要哪一家?")
        self.assertEqual([p["type"] for _, p in self.events("message")], ["question"])

    def test_v1_forward_never_posts_records(self):
        self.send("--type", "task", "--text", "繳房屋稅")
        ev = self.c.execute("SELECT * FROM events WHERE type='record'").fetchone()
        self.assertIsNone(self.m.to_assistant(self.c, ev))


if __name__ == "__main__":
    unittest.main()
