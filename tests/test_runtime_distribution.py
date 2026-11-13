"""Claude Code's distribution port (docs/design/runtime-versions.md section 1): the newest version npm publishes, an exact
version installed into a folder of its own with every resolved package's integrity hash in the lockfile, what an
executable says it is, and what keeps a run of that copy from updating itself. npm is a stand-in script here: no network.
The binary a platform run starts is chosen in process only (check_binary), never by an environment variable."""
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import runtime_claude  # noqa: E402
import runtime_ports  # noqa: E402

NPM = r'''#!/bin/sh
# view <pkg> version | install --prefix <folder> <pkg>@<version> ...
case "$1" in
view) [ -n "$FAKE_LATEST" ] || exit 1; echo "$FAKE_LATEST"; exit 0 ;;
install)
  folder="$3"; version="${4##*@}"
  [ "$FAKE_INSTALL" = fail ] && exit 7
  mkdir -p "$folder/node_modules/.bin"
  printf '#!/bin/sh\necho "%s (Claude Code)"\n' "${FAKE_SAYS:-$version}" > "$folder/node_modules/.bin/claude"
  chmod 755 "$folder/node_modules/.bin/claude"
  if [ "$FAKE_INSTALL" = unhashed ]; then integrity=''; else integrity=',"integrity":"sha512-x"'; fi
  printf '{"packages":{"":{"name":"x"},"node_modules/@anthropic-ai/claude-code":{"version":"%s","resolved":"https://r/a.tgz"%s},"node_modules/@anthropic-ai/claude-code-darwin-arm64":{"resolved":"https://r/b.tgz","integrity":"sha512-y"}}}' "$version" "$integrity" > "$folder/package-lock.json"
  exit 0 ;;
esac
exit 2
'''


class Distribution(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = pathlib.Path(d.name)
        npm = self.tmp / "npm"
        npm.write_text(NPM)
        npm.chmod(0o755)
        p = mock.patch.object(runtime_claude, "_npm", lambda: str(npm))
        p.start()
        self.addCleanup(p.stop)

    def env(self, **kw):
        return mock.patch.dict(os.environ, {f"FAKE_{k.upper()}": v for k, v in kw.items()})

    def test_it_is_a_port_claude_has_and_others_lack(self):
        self.assertIn("distribution", runtime_ports.capabilities("claude"))
        for name in ("acp", "command", "none", "fake"):
            self.assertNotIn("distribution", runtime_ports.capabilities(name), name)
        self.assertIn("distribution", runtime_ports.OPTIONAL, "a runtime without it still loads")
        self.assertTrue(runtime_ports.unversioned("acp"))
        self.assertFalse(runtime_ports.unversioned("claude"))
        self.assertFalse(runtime_ports.unversioned("none"), "no platform runs, nothing to version")
        self.assertRegex(runtime_claude.MIN_VERSION, r"^\d+\.\d+\.\d+$")

    def test_latest(self):
        with self.env(latest="2.1.400"):
            self.assertEqual(runtime_claude.latest(), "2.1.400")
        for bad in ("", "beta", "2.1"):
            with self.subTest(bad=bad), self.env(latest=bad), self.assertRaises(RuntimeError):
                runtime_claude.latest()

    def test_install_and_version(self):
        with self.env(install="ok"):
            exe = runtime_claude.install("2.1.400", self.tmp / "store" / "2.1.400")
        self.assertEqual(exe, self.tmp / "store" / "2.1.400" / "node_modules" / ".bin" / "claude")
        self.assertEqual(runtime_claude.version_of(exe), "2.1.400")
        self.assertIsNone(runtime_claude.version_of(self.tmp / "nothing"))

    def test_install_refusals(self):
        cases = {"fail": "exit 7", "unhashed": "node_modules/@anthropic-ai/claude-code"}
        for kind, said in cases.items():
            with self.subTest(kind=kind), self.env(install=kind), self.assertRaises(RuntimeError) as cm:
                runtime_claude.install("2.1.400", self.tmp / kind)
            self.assertIn(said, str(cm.exception))
        with self.env(install="ok", says="2.1.399"), self.assertRaises(RuntimeError):
            runtime_claude.install("2.1.400", self.tmp / "lies")
        with self.assertRaises(RuntimeError):
            runtime_claude.install("latest", self.tmp / "x")

    def test_pin_env(self):
        self.assertEqual(runtime_claude.pin_env("/store/2.1.400"),
                         {"DISABLE_AUTOUPDATER": "1", "npm_config_prefix": "/store/2.1.400"})


class BinarySeam(unittest.TestCase):
    def tearDown(self):
        runtime_claude.check_binary(None)

    def test_only_in_process(self):
        with mock.patch.object(runtime_claude, "_CHECKED", None), \
                mock.patch.dict(os.environ, {"BRUNNA_CLAUDE": "/evil", "CLAUDE_BINARY": "/evil", "AGENT_HUB_CLAUDE": "/evil"}):
            self.assertNotEqual(runtime_claude.binary(), "/evil", "no environment variable chooses it")
        runtime_claude.check_binary("/store/2.1.400/node_modules/.bin/claude")
        self.assertEqual(runtime_claude.binary(), "/store/2.1.400/node_modules/.bin/claude")
        self.assertEqual(runtime_claude._exe(), runtime_claude.binary())
        runtime_claude.check_binary(None)
        self.assertNotEqual(runtime_claude.binary(), "/store/2.1.400/node_modules/.bin/claude")


if __name__ == "__main__":
    unittest.main()
