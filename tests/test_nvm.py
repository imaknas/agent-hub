"""hubconf.nvm_bin(): nvm's default version (an alias, which can chain) maps to the newest matching installed version.

    bin/py -m unittest discover -s tests
"""
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hubconf  # noqa: E402


class NvmBin(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.nvm = pathlib.Path(self.d.name)
        for v in ("v22.22.2", "v24.15.0", "v24.19.0", "v25.1.0"):
            b = self.nvm / "versions" / "node" / v / "bin"
            b.mkdir(parents=True)
            (b / "node").write_text("")
        (self.nvm / "alias" / "lts").mkdir(parents=True)
        self.old = os.environ.get("NVM_DIR")
        os.environ["NVM_DIR"] = str(self.nvm)

    def tearDown(self):
        if self.old is None:
            os.environ.pop("NVM_DIR", None)
        else:
            os.environ["NVM_DIR"] = self.old
        self.d.cleanup()

    def default(self, spec):
        (self.nvm / "alias" / "default").write_text(spec + "\n")
        b = hubconf.nvm_bin()
        return b.parent.name if b else None

    def test_major(self):
        self.assertEqual(self.default("24"), "v24.19.0")

    def test_exact(self):
        self.assertEqual(self.default("v24.15.0"), "v24.15.0")

    def test_node_is_latest(self):
        self.assertEqual(self.default("node"), "v25.1.0")

    def test_alias_chain(self):
        (self.nvm / "alias" / "lts" / "jod").write_text("v22\n")
        (self.nvm / "alias" / "lts" / "*").write_text("lts/jod\n")
        self.assertEqual(self.default("lts/*"), "v22.22.2")

    def test_not_installed(self):
        self.assertIsNone(self.default("20"))


if __name__ == "__main__":
    unittest.main()
