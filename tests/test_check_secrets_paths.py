"""Where check-secrets looks for credentials: the platform's own `.secrets/<name>` is the platform's secrets directory
(no longer in the hub root since the 10-26 move); a project's relative paths are still relative to runtime/workspace.

    bin/py -m unittest discover -s tests
"""
import importlib.util
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hubconf  # noqa: E402
import i18n  # noqa: E402


def zh(msgid: str, **kw) -> str:
    """check-secrets' zh-Hant text of a message (run_main runs in zh-Hant)"""
    return i18n.translate("check-secrets", msgid, kw, "zh-Hant")

spec = importlib.util.spec_from_file_location("check_secrets", ROOT / "bin" / "check-secrets.py")
check_secrets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_secrets)


class Locate(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
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
    """A feature that is off needs no credentials: the alerts' Discord token depends on hub.json alerts; a module's on
        whether the module is on (the registry index holds only enabled ones)."""

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
             mock.patch.object(check_secrets, "undeclared", lambda *a: []), contextlib.redirect_stdout(out), \
             mock.patch.dict("os.environ", {"AGENT_HUB_LANG_UI": "zh-Hant"}):
            check_secrets.main([hub, *modules])   # names given = the alert channels are not touched
        return out.getvalue()

    def test_discord_token_only_when_alerts_use_discord(self):
        self.assertIn(zh("Checked {count} {count:credential|credentials}, {problems} {problems:problem|problems}",
                         count=0, problems=0), self.run_main(None, {}))
        out = self.run_main({"discord_token_file": "/nonexistent/discord-token"}, {})
        self.assertIn(zh("{name} is missing ({path})", name="discord-bot-token", path="/nonexistent/discord-token"), out)

    def test_module_secrets_only_when_enabled(self):
        mod = {"name": "backup", "type": "module",
               "secrets": [{"name": "b2-key", "path": "/nonexistent/b2-env"}]}
        self.assertIn(zh("{name} is missing ({path})", name="b2-key", path="/nonexistent/b2-env"),
                      self.run_main(None, {"backup": mod}))
        self.assertNotIn("b2-key", self.run_main(None, {}))


if __name__ == "__main__":
    unittest.main()
