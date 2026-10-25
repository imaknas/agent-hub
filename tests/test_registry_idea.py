"""登錄的 type / purpose(想法沙盒,docs/design/idea-sandboxes.md)。

    bin/py -m unittest discover -s tests
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

try:
    import registry  # noqa: E402  需要 PyYAML
except ImportError:
    registry = None


def doc(**extra):
    d = {"v": 1, "name": "idea-7", "paths": {"workspace": "/tmp"}}
    d.update(extra)
    return d


@unittest.skipIf(registry is None, "需要 PyYAML")
class IdeaFields(unittest.TestCase):
    def errs(self, d):
        return [e for e in registry.validate(d, "idea-7") if "type" in e or "purpose" in e]

    def test_default_type_is_project(self):
        self.assertEqual(self.errs(doc()), [])

    def test_idea_needs_purpose(self):
        self.assertTrue(self.errs(doc(type="idea")))
        self.assertEqual(self.errs(doc(type="idea", purpose="試試看把講座配成中文")), [])

    def test_unknown_type(self):
        self.assertTrue(self.errs(doc(type="sandbox", purpose="x")))

    def test_empty_purpose(self):
        self.assertTrue(self.errs(doc(purpose="  ")))


if __name__ == "__main__":
    unittest.main()
