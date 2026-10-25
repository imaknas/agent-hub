"""登錄的 enforce.capabilities 跟 manifest.update 不能改的安全欄位(docs/design/capabilities.md)。

    bin/py -m unittest discover -s tests
"""
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

try:
    import registry  # noqa: E402  需要 PyYAML
except ImportError:
    registry = None

CATALOG = {"gpu": {"risk": "low", "default_for": ["idea"]}}


def doc(**extra):
    d = {"v": 1, "name": "idea-7", "type": "idea", "purpose": "x", "paths": {"workspace": "/tmp"}}
    d.update(extra)
    return d


@unittest.skipIf(registry is None, "需要 PyYAML")
class Capabilities(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(registry, "capabilities", return_value=CATALOG)
        p.start()
        self.addCleanup(p.stop)

    def errs(self, d):
        return registry.validate_enforce(d)

    def test_work_needs_sandbox_and_a_bool(self):
        self.assertEqual(self.errs(doc(type="project", enforce={"sandbox": True, "work": True})), [])
        self.assertEqual(self.errs(doc(enforce={"sandbox": False, "work": False})), [])
        self.assertTrue(self.errs(doc(enforce={"sandbox": False, "work": True})))
        self.assertTrue(self.errs(doc(enforce={"work": True})))
        self.assertTrue(self.errs(doc(enforce={"sandbox": True, "work": "yes"})))

    def test_work_is_platform_owned(self):
        self.assertIn("enforce", registry.PLATFORM_OWNED)

    def test_declared_capability(self):
        self.assertEqual(self.errs(doc(enforce={"sandbox": True, "capabilities": ["gpu"]})), [])

    def test_unknown_capability(self):
        self.assertTrue(self.errs(doc(enforce={"sandbox": True, "capabilities": ["camera"]})))

    def test_needs_sandbox(self):
        self.assertTrue(self.errs(doc(enforce={"sandbox": False, "capabilities": ["gpu"]})))
        self.assertTrue(self.errs(doc(enforce={"capabilities": ["gpu"]})))

    def test_projects_keep_capabilities_too(self):
        # 10-25 m10251626:升級成正式專案後,Knas 同意過的能力跟著留;同意的紀錄是平台擁有的 enforce,不看 type
        self.assertEqual(self.errs(doc(type="project", enforce={"sandbox": True, "capabilities": ["gpu"]})), [])
        self.assertTrue(self.errs(doc(type="project", enforce={"sandbox": False, "capabilities": ["gpu"]})))

    def test_shape(self):
        self.assertTrue(self.errs(doc(enforce={"sandbox": True, "capabilities": "gpu"})))
        self.assertTrue(self.errs(doc(enforce={"sandbox": True, "other": 1})))
        self.assertEqual(self.errs(doc(enforce={"sandbox": True})), [])

    def test_catalog_needs_profile_file(self):
        mock.patch.stopall()
        with mock.patch.object(registry.hubconf, "get", return_value={"_comment": "x", "gpu": {}, "nofile": {}}):
            self.assertIn("gpu", registry.capabilities())
            self.assertNotIn("nofile", registry.capabilities())
            self.assertNotIn("_comment", registry.capabilities())


@unittest.skipIf(registry is None, "需要 PyYAML")
class PlatformOwned(unittest.TestCase):
    cur = doc(enforce={"sandbox": True, "capabilities": ["gpu"]}, privacy={"reflection": "stats_only"})

    def test_unchanged_is_fine(self):
        self.assertEqual(registry.platform_owned_changes(dict(self.cur, mailbox={"escalate": True}), self.cur), [])

    def test_each_field_is_locked(self):
        for k, v in [("enforce", {"sandbox": False}), ("enforce", {"sandbox": True, "capabilities": ["gpu", "x"]}),
                     ("type", "project"), ("network", ["evil.example"]), ("allow_write", ["~/"]),
                     ("privacy", {"reflection": "include"})]:
            self.assertTrue(registry.platform_owned_changes(dict(self.cur, **{k: v}), self.cur), k)

    def test_removing_counts_as_change(self):
        new = {k: v for k, v in self.cur.items() if k != "privacy"}
        self.assertTrue(registry.platform_owned_changes(new, self.cur))


if __name__ == "__main__":
    unittest.main()
