"""Every port of a versioned runtime is accounted for by its conformance checks (docs/design/runtime-versions.md
section 2): a check, or a named not-checkable entry with the measurement that would make it checkable. A new port can't
ship unguarded or unaccounted for."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "bin"))
import conformance  # noqa: E402
import runtime_ports  # noqa: E402


class Cover(unittest.TestCase):
    def test_every_port_of_a_versioned_runtime_has_an_entry(self):
        # the distribution port, or session-side checks of the versions its sessions run (conformance.versioned)
        versioned = [n for n in runtime_ports.RUNTIMES if conformance.versioned(n)]
        self.assertIn("claude", versioned)
        self.assertIn("codex", versioned)
        for name in versioned:
            with self.subTest(runtime=name):
                entries = conformance.checks(name)
                have = runtime_ports.capabilities(name)
                covered = {c.port for c in entries}
                self.assertEqual(sorted(have - covered), [], "ports with no check and no not-checkable entry")
                self.assertEqual(sorted(covered - set(runtime_ports.PORTS)), [], "entries for ports that don't exist")
                self.assertEqual(len({c.name for c in entries}), len(entries), "check names are unique")
                for c in entries:
                    self.assertIn(c.kind, runtime_ports.CHECK_KINDS, c.name)
                    if c.run is None:
                        self.assertTrue(c.measurement.strip(), f"{c.name}: not-checkable names its measurement")
                    else:
                        self.assertTrue(callable(c.run), c.name)
                    self.assertGreater(c.timeout_s, 0, c.name)

    def test_an_unversioned_runtime_declares_none(self):
        for name in runtime_ports.RUNTIMES:
            if not conformance.versioned(name):
                with self.subTest(runtime=name):
                    self.assertEqual(conformance.checks(name), [], "nothing to check without versions")


if __name__ == "__main__":
    unittest.main()
