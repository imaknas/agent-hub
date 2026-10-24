"""hubconf.roster() 用簡單的逐行解析讀登錄的 paths(不需要 PyYAML);確認跟 PyYAML 讀到的一樣。

    python3 -m unittest discover -s tests
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hubconf  # noqa: E402

try:
    import yaml
except ImportError:  # 系統的 python3 沒有 PyYAML
    yaml = None


@unittest.skipIf(yaml is None, "需要 PyYAML")
class Roster(unittest.TestCase):
    def test_roster_matches_yaml(self):
        roster = hubconf.roster()
        for f in sorted(hubconf.registry_dir().glob("*.yaml")):
            doc = yaml.safe_load(f.read_text())
            if f.stem == hubconf.hub_project():
                self.assertNotIn(f.stem, roster)
                continue
            self.assertEqual(roster[f.stem], doc.get("paths") or {}, f.name)

    def test_roster_parses_safe_dump(self):
        import tempfile
        doc = {"v": 1, "name": "x", "paths": {"workspace": "~/workspace/x", "memory_bare": "~/git-origin/x-memory.git",
                                              "origin": "(native)"}, "memory": True, "jobs": [{"name": "j", "at": "01:00"}]}
        with tempfile.TemporaryDirectory() as d:
            old = hubconf.registry_dir
            hubconf.registry_dir = lambda: pathlib.Path(d)
            try:
                pathlib.Path(d, "x.yaml").write_text("# 註解\n" + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False))
                self.assertEqual(hubconf.roster(), {"x": doc["paths"]})
            finally:
                hubconf.registry_dir = old


if __name__ == "__main__":
    unittest.main()
