"""contracts-check 的副本比對也看開著的模組的 consumes(例:助理模組讀助理定義的格式);沒開的模組不比對——沒有助理的部署
不會一開始就紅。

    bin/py -m unittest discover -s tests
"""
import importlib.util
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

spec = importlib.util.spec_from_file_location("contracts_check", ROOT / "bin" / "contracts-check.py")
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)


class ModuleConsumes(unittest.TestCase):
    def setUp(self):
        self.ws = pathlib.Path(tempfile.mkdtemp()).resolve()
        (self.ws / "c.json").write_text('{"type": "object"}\n')
        self.producer = {"name": "helper", "paths": {"workspace": str(self.ws)},
                         "provides": [{"contract": "helper-x", "v": 1, "schema": "c.json"}]}
        self.module = {"name": "assistant", "type": "module",
                       "consumes": [{"contract": "helper-x", "v": 1, "copy": "contracts/hubbus.v1.schema.json",
                                     "from": "helper"}]}

    def test_enabled_module_copies_are_compared(self):
        errs = cc.cross_copies({"projects": {"helper": self.producer}, "modules": {"assistant": self.module}})
        self.assertTrue(any("assistant 的 helper-x 副本" in e for e in errs), errs)

    def test_missing_producer_is_reported_for_an_enabled_module(self):
        errs = cc.cross_copies({"projects": {}, "modules": {"assistant": self.module}})
        self.assertTrue(any("helper 的登錄沒有 provides" in e for e in errs), errs)

    def test_disabled_module_is_not_compared(self):
        self.assertEqual(cc.cross_copies({"projects": {}, "modules": {}}), [])


if __name__ == "__main__":
    unittest.main()
