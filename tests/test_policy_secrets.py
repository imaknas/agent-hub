"""平台憑證的讀取封鎖:搬家期間新舊兩個位置都擋(2026-10-26 實例目錄搬家:secrets 只複製到新位置,舊的還在)。

    bin/py -m unittest discover -s tests
"""
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hubconf  # noqa: E402
import policy  # noqa: E402
import runtime_claude  # noqa: E402


class SecretDirs(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.hub, self.new = self.tmp / "hub", self.tmp / "home" / "secrets"
        self.hub.mkdir()
        self.new.mkdir(parents=True)
        for target, value in (("ROOT", self.hub), ("secrets_dir", lambda: self.new)):
            p = mock.patch.object(hubconf, target, value)
            p.start()
            self.addCleanup(p.stop)

    def pol(self):
        with mock.patch.object(policy, "project_paths", lambda index: {}):
            return policy.policy("p", {"name": "p", "paths": {"workspace": str(self.tmp / "ws")}}, {"projects": {}})

    def test_both_while_old_still_exists(self):
        hubconf.legacy_secrets_dir().mkdir()
        deny_read = self.pol()["deny_read"]
        self.assertIn(self.new, deny_read)
        self.assertIn(self.hub / ".secrets", deny_read)
        sandbox, deny, _ = runtime_claude.settings(self.pol())
        rules = " ".join(deny)
        self.assertIn(str(self.new), rules)
        self.assertIn(str(self.hub / ".secrets"), rules)
        self.assertEqual(len([d for d in sandbox["filesystem"]["denyRead"] if "secrets" in d]), 2)

    def test_only_current_after_old_is_gone(self):
        self.assertEqual(policy.secret_dirs(), [self.new])

    def test_not_twice_before_the_move(self):
        (self.hub / ".secrets").mkdir()
        with mock.patch.object(hubconf, "secrets_dir", lambda: self.hub / ".secrets"):
            self.assertEqual(policy.secret_dirs(), [self.hub / ".secrets"])


if __name__ == "__main__":
    unittest.main()
