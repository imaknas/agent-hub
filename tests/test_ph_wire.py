"""bin/ph_wire.py: the versions agent-hub reads and sends, and core §5.3's terms digest against the spec's vectors
(contracts/vendor/agent-ops-specs/principal-handoff-0.4/core/terms-digest.json).

    bin/py -m unittest tests.test_ph_wire
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import ph_wire  # noqa: E402

VECTORS = ROOT / "contracts" / "vendor" / "agent-ops-specs" / "principal-handoff-0.4" / "core" / "terms-digest.json"


class TermsDigest(unittest.TestCase):
    def test_vectors(self):
        cases = json.loads(VECTORS.read_text())["cases"]
        self.assertTrue(cases)
        for c in cases:
            with self.subTest(case=c["id"]):
                r = c["record"]
                self.assertEqual(ph_wire.canonical({k: r[k] for k in ("ext", "project", "key", "terms_rev", "terms")}),
                                 c["canonical"])
                self.assertEqual(ph_wire.terms_digest(r), c["digest"])


class Versions(unittest.TestCase):
    def test_family(self):
        self.assertEqual(ph_wire.family("acts/0.3"), "acts")
        self.assertEqual(ph_wire.family("decisions/0.4"), "decisions")
        self.assertEqual(ph_wire.family("acts/0.2"), "")
        self.assertEqual(ph_wire.family(None), "")

    def test_sends_a_version_it_reads(self):
        self.assertIn(ph_wire.SEND, ph_wire.READ)
        self.assertEqual((ph_wire.ACTS, ph_wire.DECISIONS), (f"acts/{ph_wire.SEND}", f"decisions/{ph_wire.SEND}"))


if __name__ == "__main__":
    unittest.main()
