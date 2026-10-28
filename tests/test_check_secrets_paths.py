"""check-secrets 找憑證的位置:平台自己的 `.secrets/<名稱>` 是平台的憑證目錄(10-26 搬家後不在 hub 根目錄),
專案的相對路徑照舊以 runtime/workspace 為準。

    bin/py -m unittest discover -s tests
"""
import importlib.util
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hubconf  # noqa: E402

spec = importlib.util.spec_from_file_location("check_secrets", ROOT / "bin" / "check-secrets.py")
check_secrets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_secrets)


class Locate(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.secrets = self.tmp / "home" / "secrets"
        p = mock.patch.object(hubconf, "secrets_dir", lambda: self.secrets)
        p.start()
        self.addCleanup(p.stop)

    def test_platform_secret_is_in_secrets_dir(self):
        doc = {"name": hubconf.hub_project(), "paths": {"workspace": str(self.tmp / "hub")}}
        self.assertEqual(check_secrets.locate(hubconf.hub_project(), doc, ".secrets/token"), self.secrets / "token")

    def test_project_secret_is_relative_to_workspace(self):
        ws = self.tmp / "ws"
        doc = {"name": "p", "paths": {"workspace": str(ws)}}
        self.assertEqual(check_secrets.locate("p", doc, ".secrets/token"), ws / ".secrets" / "token")

    def test_absolute_and_home(self):
        doc = {"name": "p", "paths": {"workspace": str(self.tmp / "ws")}}
        self.assertEqual(check_secrets.locate("p", doc, str(self.tmp / "x")), self.tmp / "x")


if __name__ == "__main__":
    unittest.main()
