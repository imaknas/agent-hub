"""bin/ph_wire.py: the versions agent-hub reads and sends, the terms digest of 0.4 and of 0.5 (core §4), and the
conversion between 0.4 and 0.5 (core §14), against the spec's vectors (contracts/vendor/agent-ops-specs/).

    bin/py -m unittest tests.test_ph_wire
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import ph_wire  # noqa: E402

VENDOR = ROOT / "contracts" / "vendor" / "agent-ops-specs"
V04 = VENDOR / "principal-handoff-0.4" / "core" / "terms-digest.json"
V05 = VENDOR / "principal-handoff-0.5" / "core" / "conformance"


class TermsDigest(unittest.TestCase):
    def check(self, path: pathlib.Path, version: str) -> None:
        cases = json.loads(path.read_text())["cases"]
        self.assertTrue(cases)
        for c in cases:
            with self.subTest(case=c["id"], version=version):
                self.assertEqual(ph_wire.canonical(ph_wire.digest_input(c["record"], version)), c["canonical"])
                self.assertEqual(ph_wire.terms_digest(c["record"], version), c["digest"])

    def test_vectors_0_5(self):
        """core §4: over the extension's name without its version"""
        self.check(V05 / "terms-digest.json", "0.5")

    def test_vectors_0_4(self):
        """0.4's digest, which 0.4 settlements carry and conversion compares with"""
        self.check(V04, "0.4")

    def test_default_is_0_5(self):
        need = {"ext": "acts/0.5", "project": "p", "key": "k", "terms_rev": 1, "terms": {"text": "x"}}
        self.assertEqual(ph_wire.terms_digest(need), ph_wire.terms_digest(need, "0.5"))
        self.assertNotEqual(ph_wire.terms_digest(need, "0.4"), ph_wire.terms_digest(need, "0.5"))


class Convert(unittest.TestCase):
    def test_vectors(self):
        """core §14 (convert.json); the Criteria cases are for parties that implement Criteria, which agent-hub does not"""
        cases = [c for c in json.loads((V05 / "convert.json").read_text())["cases"] if c.get("module") != "criteria"]
        self.assertTrue(cases)
        for c in cases:
            with self.subTest(case=c["id"]):
                self.assertEqual(ph_wire.convert(c["record"], c["to"], c["held"]), c["expect"])

    def test_a_0_3_settlement_gets_the_digest_of_its_need_record(self):
        """Acts 0.5 §6: 0.3 to 0.4 adds terms_digest over the need record at the settlement's terms_rev"""
        need = {"ph": "0.3", "ext": "acts/0.3", "id": "r:k", "type": "raised", "origin": "requester", "by": "x",
                "key": "k", "project": "p", "ts": "2026-10-20T10:00:00Z", "terms": {"text": "Pay"}, "terms_rev": 1}
        s = {"ph": "0.3", "ext": "acts/0.3", "id": "s1", "type": "settled", "origin": "principal", "by": "y",
             "key": "k", "project": "p", "ts": "2026-10-21T10:00:00Z", "terms_rev": 1, "outcome": "done",
             "authority": {"kind": "attested", "decider": "y"}, "evidence": {"basis": "b"}}
        self.assertEqual(ph_wire.convert(s, "0.5", [need, s])["terms_digest"], ph_wire.terms_digest(need, "0.5"))
        self.assertEqual(ph_wire.convert(s, "0.4", [need, s])["terms_digest"], ph_wire.terms_digest(need, "0.4"))
        self.assertNotIn("terms_digest", ph_wire.convert(s, "0.5", [s]), "no need record held: none to compute")
        self.assertEqual(ph_wire.convert(s, "0.5", [s])["ext"], "acts/0.5")

    def test_same_version_is_unchanged(self):
        r = {"ph": "0.5", "ext": "acts/0.5", "type": "settled", "evidence": {"proof": "x"}}
        self.assertIs(ph_wire.convert(r, "0.5", []), r)


class Versions(unittest.TestCase):
    def test_family(self):
        self.assertEqual(ph_wire.family("acts/0.3"), "acts")
        self.assertEqual(ph_wire.family("decisions/0.4"), "decisions")
        self.assertEqual(ph_wire.family("acts/0.5"), "acts")
        self.assertEqual(ph_wire.family("acts/0.2"), "")
        self.assertEqual(ph_wire.family(None), "")

    def test_send_version(self):
        """hub.json principal_handoff.send_version, 0.4 until set"""
        with mock.patch.object(ph_wire.hubconf, "get", return_value=None):
            self.assertEqual(ph_wire.send_version(), "0.4")
            self.assertEqual((ph_wire.ext("acts"), ph_wire.ext("decisions")), ("acts/0.4", "decisions/0.4"))
        with mock.patch.object(ph_wire.hubconf, "get", return_value="0.5"):
            self.assertEqual((ph_wire.ext("acts"), ph_wire.ext("decisions")), ("acts/0.5", "decisions/0.5"))
        with mock.patch.object(ph_wire.hubconf, "get", return_value="0.3"):
            with self.assertRaises(SystemExit):
                ph_wire.send_version()
        self.assertLessEqual(set(ph_wire.SENDS), set(ph_wire.READ))

    def test_the_schema_and_config_check_know_the_setting(self):
        import minischema
        schema = json.loads((ROOT / "contracts" / "hub.v1.schema.json").read_text())
        base = minischema.errors(schema, {})
        for v in ph_wire.SENDS:
            self.assertEqual(minischema.errors(schema, {"principal_handoff": {"send_version": v}}), base)
        for bad in ({"send_version": "0.3"}, {"send_version": 0.5}, {"send_versoin": "0.5"}):
            self.assertNotEqual(minischema.errors(schema, {"principal_handoff": bad}), base, bad)
        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp, "hub.json").write_text(json.dumps({"principal_handoff": {"send_version": "0.6"}}))
            r = subprocess.run([sys.executable, str(ROOT / "bin" / "config-check")], cwd=tmp, capture_output=True,
                               text=True, env={**os.environ, "AGENT_HUB_ROOT": str(ROOT), "AGENT_HUB_CONFIG": tmp,
                                               "AGENT_HUB_LANG_UI": "en"})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("send_version", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
