"""套用者的契約測試(bin/appliers.py、docs/design/new-user.md 3.7):APPLIERS 登記的每一個都跑同一組檢查——
① plan 沒有副作用(它管的檔案前後一樣)② apply 回傳的 == 剛才 plan 的 ③ apply 完再 plan 是空的(冪等)。
登記表裡有套用者卻沒有對應的情境設定,測試直接失敗:新的套用者不可能跳過契約測試。

每個情境都是假的登錄 + 暫存目錄;launchd 換成只記錄的假 launchctl,不碰真的服務。

    bin/py -m unittest discover -s tests
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import appliers  # noqa: E402


def snapshot(d: pathlib.Path) -> dict:
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


class SchedulerFixture:
    def setup(self, m, tmp: pathlib.Path):
        self._snap = m.SNAPSHOT
        m.SNAPSHOT = tmp / "state" / "jobs.json"
        return {"v": 1, "errors": {}, "projects": {"demo": {
            "name": "demo", "paths": {"workspace": str(tmp / "ws")},
            "jobs": [{"name": "nightly", "at": "03:00", "run": "/bin/echo hi"},
                     {"name": "often", "every": "15m", "run": "/bin/echo hi", "after": ["nightly"]}]}}}

    def teardown(self, m):
        m.SNAPSHOT = self._snap


class ServicesFixture:
    def setup(self, m, tmp: pathlib.Path):
        self._saved = (m.AGENTS, m.RENDERED, m.launchctl, m.hubconf.service_manager)
        m.hubconf.service_manager = lambda: "launchd"   # 測的是 launchd 這一支(launchctl 是假的),跟跑在哪個系統無關
        m.AGENTS, m.RENDERED = tmp / "LaunchAgents", tmp / "state" / "rendered-services.json"
        m.AGENTS.mkdir(parents=True)
        self.calls = []

        def fake(*args):
            self.calls.append(args)
            # print = 沒載入(bootstrap 前的等待馬上結束);其他(bootout、bootstrap)成功
            return subprocess.CompletedProcess(args, 1 if args[0] == "print" else 0, "", "")
        m.launchctl = fake
        (tmp / "ws").mkdir()
        return {"v": 1, "errors": {}, "projects": {"demo": {
            "name": "demo", "paths": {"workspace": str(tmp / "ws")},
            "services": [{"name": "web", "run": "/bin/echo hi", "keepalive": True, "log": "web.log"}]}}}

    def teardown(self, m):
        m.AGENTS, m.RENDERED, m.launchctl, m.hubconf.service_manager = self._saved


class AgentSettingsFixture:
    def setup(self, m, tmp: pathlib.Path):
        self._rendered = m.RENDERED
        m.RENDERED = tmp / "state" / "rendered-settings"
        (tmp / "ws").mkdir()
        return {"v": 1, "errors": {}, "projects": {"demo": {
            "name": "demo", "paths": {"workspace": str(tmp / "ws")}, "enforce": {"sandbox": True}}}}

    def teardown(self, m):
        m.RENDERED = self._rendered


FIXTURES = {"scheduler": SchedulerFixture, "services": ServicesFixture, "agent-settings": AgentSettingsFixture}


class Registry(unittest.TestCase):
    def test_every_applier_has_a_fixture(self):
        self.assertEqual(set(appliers.APPLIERS), set(FIXTURES), "新的套用者要在這裡加情境設定,才算跑過契約測試")

    def test_load_rejects_incomplete_module(self):
        with tempfile.TemporaryDirectory() as d:
            bad = pathlib.Path(d) / "bad.py"
            bad.write_text("def plan(index=None):\n    return []\n")
            appliers.APPLIERS["broken"] = str(bad)
            try:
                with self.assertRaises(appliers.MissingFunctions):
                    appliers.load("broken")
            finally:
                del appliers.APPLIERS["broken"]
                appliers._loaded.pop("broken", None)


class Contract(unittest.TestCase):
    def each(self, check):
        for name in appliers.APPLIERS:
            with self.subTest(applier=name), tempfile.TemporaryDirectory() as d:
                tmp = pathlib.Path(d)
                m = appliers.load(name)
                fx = FIXTURES[name]()
                index = fx.setup(m, tmp)
                try:
                    check(m, index, tmp, fx)
                finally:
                    fx.teardown(m)

    def test_plan_has_no_side_effects(self):
        def check(m, index, tmp, fx):
            before = snapshot(tmp)
            first = m.plan(index)
            self.assertEqual(snapshot(tmp), before, "plan 改了檔案")
            self.assertTrue(first, "情境應該有東西要套用(不然契約測不到)")
            self.assertEqual(m.plan(index), first, "plan 兩次結果不一樣")
            if hasattr(fx, "calls"):
                self.assertFalse([c for c in fx.calls if c[0] in ("bootstrap", "bootout")], "plan 動了 launchd")
        self.each(check)

    def test_apply_matches_plan_and_is_idempotent(self):
        def check(m, index, tmp, fx):
            planned = m.plan(index)
            self.assertEqual(m.apply(index), planned, "apply 做的跟 plan 說的不一樣")
            self.assertEqual(m.plan(index), [], "apply 完再 plan 應該是空的")
            self.assertEqual(m.apply(index), [], "第二次 apply 不該再改東西")
        self.each(check)

    def test_changes_are_neutral(self):
        def check(m, index, tmp, fx):
            for c in m.plan(index):
                self.assertIsInstance(c, appliers.Change)
                self.assertIn(c.op, appliers.OPS)
                self.assertRegex(c.target, r"^(service|agent-settings|hook|job):")
                self.assertNotIn(".claude", c.target + c.detail, "Change 不寫執行環境的設定位置")
            self.assertIsInstance(m.EXTERNAL, bool)
        self.each(check)


if __name__ == "__main__":
    unittest.main()
