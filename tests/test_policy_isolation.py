"""What a sandboxed project may read and write (bin/policy.py; every adapter read- and write-denies other_projects):
not another project's memory (bare repos, copies, the mirrors), and not a tool cache another project installs from
(docs/design/projects-and-repos.md section 9).

    bin/py -m unittest tests.test_policy_isolation
"""
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hubconf  # noqa: E402


class Policy(unittest.TestCase):
    """Holes 3 and 4: what a sandboxed project may read and write (policy.py; every adapter denies other_projects)."""

    def setUp(self):
        import policy
        self.policy = policy
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.origin, self.copies = self.tmp / "git-origin", self.tmp / "copies"
        for d in (self.origin / "a-memory.git", self.origin / "b-memory.git", self.origin / "mirrors",
                  self.copies / "a", self.copies / "b"):
            d.mkdir(parents=True)
        real = hubconf.p
        dirs = {"git_origin": self.origin, "memory_copies": self.copies}
        p = mock.patch.object(hubconf, "p", lambda key, default=None: dirs.get(key) or real(key, default))
        p.start()
        self.addCleanup(p.stop)
        self.docs = {n: {"name": n, "paths": {"workspace": str(self.tmp / "ws" / n)}, "enforce": {"sandbox": True}}
                     for n in ("a", "b")}
        self.index = {"projects": self.docs}

    def pol(self, name):
        return self.policy.policy(name, self.docs[name], self.index)

    def test_other_projects_memory_is_denied_its_own_is_not(self):
        others = self.pol("a")["other_projects"]
        for p in (self.origin / "b-memory.git", self.copies / "b", self.origin / "mirrors"):
            self.assertIn(p, others)
        for p in (self.origin / "a-memory.git", self.copies / "a"):
            self.assertNotIn(p, others)

    def test_tool_caches_are_per_repo(self):
        a, b = self.pol("a"), self.pol("b")
        self.assertEqual(set(a["write"]) & set(b["write"]), {self.policy.tilde(hubconf.BUS_DIR)})
        self.assertFalse([w for w in a["write"] if "cache" in w or ".npm" in w], a["write"])
        for v in a["env"].values():
            self.assertTrue(pathlib.Path(v).is_relative_to(self.tmp / "ws" / "a" / ".git"), v)
        self.assertEqual(set(a["env"]), {"UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "UV_TOOL_DIR", "PIP_CACHE_DIR",
                                         "npm_config_cache", "COREPACK_HOME"})


if __name__ == "__main__":
    unittest.main()
