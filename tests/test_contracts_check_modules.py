"""contracts-check's copy comparison also covers the consumes of enabled modules (e.g. the assistant module reads a
format the assistant defines); modules that are off are not compared, so a deployment without the assistant is not red
from the start.

    bin/py -m unittest discover -s tests
"""
import importlib.util
import os
import pathlib
import string
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import i18n  # noqa: E402

spec = importlib.util.spec_from_file_location("contracts_check", ROOT / "bin" / "contracts-check.py")
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)


def zh(msgid: str, **kw) -> str:
    """contracts-check's zh-Hant text of a message, its placeholders filled from kw; a placeholder not given ends it
    there (the fixed part before it, to look for)"""
    out = ""
    for lit, field, spec, conv in string.Formatter().parse(i18n.catalog("contracts-check", "zh-Hant")[msgid]):
        out += lit
        if field is None:   # the end, or an escaped brace
            continue
        if field not in kw:
            return out
        out += format(kw[field], spec)
    return out


class ModuleConsumes(unittest.TestCase):
    def setUp(self):
        zh = mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "zh-Hant"})   # the assertions read the zh-Hant texts
        zh.start()
        self.addCleanup(zh.stop)
        self.ws = pathlib.Path(tempfile.mkdtemp()).resolve()
        (self.ws / "c.json").write_text('{"type": "object"}\n')
        self.producer = {"name": "helper", "paths": {"workspace": str(self.ws)},
                         "provides": [{"contract": "helper-x", "v": 1, "schema": "c.json"}]}
        self.module = {"name": "assistant", "type": "module",
                       "consumes": [{"contract": "helper-x", "v": 1, "copy": "contracts/hubbus.v1.schema.json",
                                     "from": "helper"}]}

    def test_enabled_module_copies_are_compared(self):
        errs = cc.cross_copies({"projects": {"helper": self.producer}, "modules": {"assistant": self.module}})
        want = zh("{name}'s copy of {contract} differs from {producer}'s original: {copy} vs {orig}", name="assistant",
                  contract="helper-x", producer="helper")
        self.assertTrue(any(want in e for e in errs), errs)

    def test_missing_producer_is_reported_for_an_enabled_module(self):
        errs = cc.cross_copies({"projects": {}, "modules": {"assistant": self.module}})
        want = zh("{name} reads {contract}, but {producer}'s registry entry does not provide it", name="assistant",
                  contract="helper-x", producer="helper")
        self.assertTrue(any(want in e for e in errs), errs)

    def test_disabled_module_is_not_compared(self):
        self.assertEqual(cc.cross_copies({"projects": {}, "modules": {}}), [])


if __name__ == "__main__":
    unittest.main()
