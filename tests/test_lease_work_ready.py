"""lease-runner 的 work_ready:深談能不能用 work 模式。看登錄的 enforce.work(Knas 明確授權,10-25),不看 type;
而且 OS sandbox 要真的寫進那個專案的設定(render-project-settings 寫出來的才是事實)。

    bin/py -m unittest discover -s tests
"""
import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import runtime_claude  # noqa: E402


def load_runner():
    spec = importlib.util.spec_from_file_location("lease_runner_wr", ROOT / "bin" / "lease-runner.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class WorkReady(unittest.TestCase):
    def setUp(self):
        self.m = load_runner()
        self.docs = {}
        self.rendered = {"room": True, "idea-old": True, "plain": True, "not-rendered": False}
        for mod, name, fake in ((self.m.hubconf, "projects", lambda: self.docs),
                                (self.m.hubconf, "workspace", lambda name: pathlib.Path("/w") / name),
                                (runtime_claude, "sandbox_rendered",
                                 lambda ws: (self.rendered[ws.name], "" if self.rendered[ws.name] else "還沒渲染"))):
            self.addCleanup(setattr, mod, name, getattr(mod, name))
            setattr(mod, name, fake)

    def test_upgraded_project_with_work(self):
        self.docs["room"] = {"type": "project", "enforce": {"sandbox": True, "work": True}}
        self.assertTrue(self.m.work_ready("room")[0])

    def test_idea_without_explicit_work_is_not_enough(self):
        self.docs["idea-old"] = {"type": "idea", "enforce": {"sandbox": True}}
        ok, why = self.m.work_ready("idea-old")
        self.assertFalse(ok)
        self.assertIn("enforce.work", why)

    def test_sandbox_alone_is_not_enough(self):
        self.docs["plain"] = {"type": "project", "enforce": {"sandbox": True}}
        self.assertFalse(self.m.work_ready("plain")[0])

    def test_work_needs_the_sandbox_really_rendered(self):
        self.docs["not-rendered"] = {"type": "project", "enforce": {"sandbox": True, "work": True}}
        self.assertFalse(self.m.work_ready("not-rendered")[0])

    def test_work_projects_lists_only_ready_ones(self):
        self.docs.update(room={"enforce": {"sandbox": True, "work": True}}, plain={"enforce": {"sandbox": True}})
        self.assertEqual(self.m.work_projects(), ["room"])


if __name__ == "__main__":
    unittest.main()
