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


class WhatIsChecked(unittest.TestCase):
    """功能沒開就不要那個功能的憑證:警報的 Discord token 看 hub.json alerts;模組的看模組開了沒(登錄的 index 只有開著的)。"""

    def run_main(self, alerts: dict | None, modules: dict) -> str:
        import contextlib
        import io
        hub = hubconf.hub_project()
        index = {"projects": {hub: {"name": hub, "paths": {"workspace": "/nonexistent"}}}, "modules": modules}
        real_get = hubconf.get
        out = io.StringIO()
        with mock.patch.object(check_secrets.registry, "refresh", lambda: index), \
             mock.patch.object(hubconf, "get", lambda k, d=None: (alerts or {}).get(k.split(".", 1)[1], d)
                               if k.startswith("alerts.") else real_get(k, d)), \
             mock.patch.object(check_secrets, "undeclared", lambda *a: []), contextlib.redirect_stdout(out):
            check_secrets.main([hub, *modules])   # 給名稱 = 不碰警報管道
        return out.getvalue()

    def test_discord_token_only_when_alerts_use_discord(self):
        self.assertIn("檢查了 0 個憑證", self.run_main(None, {}))
        out = self.run_main({"discord_token_file": "/nonexistent/discord-token"}, {})
        self.assertIn("discord-bot-token 不在", out)

    def test_module_secrets_only_when_enabled(self):
        mod = {"name": "backup", "type": "module",
               "secrets": [{"name": "b2-key", "path": "/nonexistent/b2-env"}]}
        self.assertIn("b2-key 不在", self.run_main(None, {"backup": mod}))
        self.assertNotIn("b2-key", self.run_main(None, {}))


if __name__ == "__main__":
    unittest.main()
