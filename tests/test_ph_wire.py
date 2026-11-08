"""bin/ph_wire.py: the versions brunna reads and sends and the guard on sending 0.6, the terms digest of 0.4 and since
0.5 (core §4), the strict reading of a record's text, and the conversion between versions (core §14), against the
spec's vectors (contracts/vendor/agent-ops-specs/: 0.6.2's terms-digest and convert cases, 0.5's and 0.4's).

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
V06 = VENDOR / "principal-handoff-0.6.2" / "core" / "conformance"


class JCS(unittest.TestCase):
    """ph_wire.canonical against RFC 8785's own examples (the terms digest vectors below exercise it too)"""

    def test_section_3_2_2_sample(self):
        """the RFC's input, its printable \\u escapes (U+20AC, B, the quote, the backslash) written as characters"""
        given = json.loads(r'''{
          "numbers": [333333333.33333329, 1E30, 4.50, 2e-3, 0.000000000000000000000000001],
          "string": "€$\u000F\u000aA'B\"\\\\\"\/",
          "literals": [null, true, false]
        }''')
        self.assertEqual(ph_wire.canonical(given), '{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,'
                                                   '4.5,0.002,1e-27],"string":"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}')

    def test_section_3_2_3_member_order_is_by_utf16_code_units(self):
        """U+1F600 is the surrogate pair D83D DE00, so it sorts before U+FB33 (by code points it would come after)"""
        given = json.loads(r'''{"€": "Euro Sign", "\r": "Carriage Return", "דּ": "Hebrew Letter Dalet With Dagesh",
          "1": "One", "😀": "Emoji: Grinning Face", "\u0080": "Control",
          "ö": "Latin Small Letter O With Diaeresis"}''')
        self.assertEqual(list(json.loads(ph_wire.canonical(given)).values()),
                         ["Carriage Return", "One", "Control", "Latin Small Letter O With Diaeresis", "Euro Sign",
                          "Emoji: Grinning Face", "Hebrew Letter Dalet With Dagesh"])
        self.assertTrue(ph_wire.canonical(given).startswith('{"\\r":"Carriage Return","1":"One","\u0080":"Control"'))

    def test_appendix_b_numbers(self):
        import struct
        cases = {"0000000000000000": "0", "8000000000000000": "0", "0000000000000001": "5e-324",
                 "8000000000000001": "-5e-324", "7fefffffffffffff": "1.7976931348623157e+308",
                 "ffefffffffffffff": "-1.7976931348623157e+308", "4340000000000000": "9007199254740992",
                 "c340000000000000": "-9007199254740992", "4430000000000000": "295147905179352830000",
                 "44b52d02c7e14af5": "9.999999999999997e+22", "44b52d02c7e14af6": "1e+23",
                 "44b52d02c7e14af7": "1.0000000000000001e+23", "444b1ae4d6e2ef4e": "999999999999999700000",
                 "444b1ae4d6e2ef4f": "999999999999999900000", "444b1ae4d6e2ef50": "1e+21",
                 "3eb0c6f7a0b5ed8c": "9.999999999999997e-7", "3eb0c6f7a0b5ed8d": "0.000001",
                 "41b3de4355555553": "333333333.3333332", "41b3de4355555554": "333333333.33333325",
                 "41b3de4355555555": "333333333.3333333", "41b3de4355555556": "333333333.3333334",
                 "41b3de4355555557": "333333333.33333343", "becbf647612f3696": "-0.0000033333333333333333",
                 "43143ff3c1cb0959": "1424953923781206.2"}
        for bits, want in cases.items():
            with self.subTest(bits=bits):
                self.assertEqual(ph_wire.canonical(struct.unpack(">d", bytes.fromhex(bits))[0]), want)
        for bits in ("7fffffffffffffff", "7ff0000000000000", "fff0000000000000"):   # NaN, ±Infinity: an error
            with self.subTest(bits=bits), self.assertRaises(ValueError):
                ph_wire.canonical(struct.unpack(">d", bytes.fromhex(bits))[0])

    def test_integers_are_the_doubles_they_parse_to(self):
        self.assertEqual(ph_wire.canonical([0, -1, 2 ** 53 - 1, -(2 ** 53)]), "[0,-1,9007199254740991,-9007199254740992]")
        self.assertEqual(ph_wire.canonical(295147905179352825856), "295147905179352830000")
        self.assertEqual(ph_wire.canonical(10 ** 21), "1e+21")
        with self.assertRaises(ValueError):
            ph_wire.canonical(10 ** 400)

    def test_what_is_not_i_json_is_an_error(self):
        for bad in ("\ud83d", {"\udead": 1}, {1: "x"}, {"a": float("nan")}, object()):
            with self.subTest(bad=repr(bad)), self.assertRaises(ValueError):
                ph_wire.canonical(bad)

    def test_escapes(self):
        """§3.2.2.2: the short escapes, other controls as lowercase \\u00xx, U+007F and beyond as they are"""
        self.assertEqual(ph_wire.canonical("\b\f\n\r\t\"\\/\x00\x1f\x7f é😀"),
                         '"\\b\\f\\n\\r\\t\\"\\\\/\\u0000\\u001f\x7f é😀"')


class TermsDigest(unittest.TestCase):
    def check(self, path: pathlib.Path, version: str) -> None:
        cases = json.loads(path.read_text())["cases"]
        self.assertTrue(cases)
        for c in cases:
            with self.subTest(case=c["id"], version=version):
                self.assertEqual(ph_wire.canonical(ph_wire.digest_input(c["record"], version)), c["canonical"])
                self.assertEqual(ph_wire.terms_digest(c["record"], version), c["digest"])

    def test_vectors_0_6(self):
        """core §4: over the extension's name without its version"""
        self.check(V06 / "terms-digest.json", "0.6")

    def test_vectors_0_5(self):
        self.check(V05 / "terms-digest.json", "0.5")

    def test_vectors_0_4(self):
        """0.4's digest, which 0.4 settlements carry and conversion compares with"""
        self.check(V04, "0.4")

    def test_default_is_0_6_the_same_as_0_5(self):
        need = {"ext": "acts/0.5", "project": "p", "key": "k", "terms_rev": 1, "terms": {"text": "x"}}
        self.assertEqual(ph_wire.terms_digest(need), ph_wire.terms_digest(need, "0.6"))
        self.assertEqual(ph_wire.terms_digest(need, "0.6"), ph_wire.terms_digest(need, "0.5"))
        self.assertNotEqual(ph_wire.terms_digest(need, "0.4"), ph_wire.terms_digest(need, "0.5"))


class Convert(unittest.TestCase):
    def test_vectors_0_6(self):
        """core 0.6 §14 (0.6.2's convert.json cases): from the record alone; the Criteria cases are for parties that
        implement Criteria, which brunna does not. Its held cases: tests/test_ph_records.py"""
        cases = [c for c in json.loads((V06 / "convert.json").read_text())["cases"] if c.get("module") != "criteria"]
        self.assertEqual(len(cases), 28)
        for c in cases:
            with self.subTest(case=c["id"]):
                self.assertEqual(ph_wire.convert(c["record"], c["to"]), c["expect"])

    def test_vectors_0_5(self):
        """core 0.5 §14 (0.5's convert.json): 0.4 <-> 0.5, given the records held"""
        cases = [c for c in json.loads((V05 / "convert.json").read_text())["cases"] if c.get("module") != "criteria"]
        self.assertTrue(cases)
        for c in cases:
            with self.subTest(case=c["id"]):
                self.assertEqual(ph_wire.convert(c["record"], c["to"], c["held"]), c["expect"])

    def test_0_4_and_0_6_go_through_0_5(self):
        """one version at a time: a 0.4 settlement's digest becomes that of its need record, both ways"""
        need = {"ph": "0.4", "ext": "acts/0.4", "id": "r:k", "type": "raised", "origin": "requester", "by": "x",
                "key": "k", "project": "p", "ts": "2026-10-20T10:00:00Z", "terms": {"text": "Pay"}, "terms_rev": 1}
        s = {**need, "id": "s1", "type": "settled", "origin": "principal", "outcome": "done",
             "terms_digest": ph_wire.terms_digest(need, "0.4"), "evidence": {"proof": "x"}}
        up = ph_wire.convert(s, "0.6", [need])
        self.assertEqual((up["ph"], up["ext"], up["terms_digest"], "evidence" in up),
                         ("0.6", "acts/0.6", ph_wire.terms_digest(need, "0.6"), False))
        self.assertEqual(ph_wire.convert(up, "0.4", [need])["terms_digest"], s["terms_digest"])

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

    def settings(self, cfg: dict):
        return mock.patch.object(ph_wire.hubconf, "get", side_effect=lambda k, d=None: ph_wire._get(cfg, k) or d)

    def test_send_version(self):
        """hub.json principal_handoff.send_version, 0.4 until set"""
        assistant = {"assistant": {"api": "http://127.0.0.1:1"}}
        with self.settings(assistant):
            self.assertEqual(ph_wire.send_version(), "0.4")
            self.assertEqual((ph_wire.ext("acts"), ph_wire.ext("decisions")), ("acts/0.4", "decisions/0.4"))
        with self.settings({**assistant, "principal_handoff": {"send_version": "0.5"}}):
            self.assertEqual((ph_wire.ext("acts"), ph_wire.ext("decisions")), ("acts/0.5", "decisions/0.5"))
        with self.settings({**assistant, "principal_handoff": {"send_version": "0.3"}}):
            with self.assertRaises(SystemExit):
                ph_wire.send_version()
        self.assertLessEqual(set(ph_wire.SENDS), set(ph_wire.READ))

    def test_0_6_waits_for_the_principal_side(self):
        """core 0.6 §14: never a party that speaks 0.6 alone with one that speaks 0.5 alone; 0.6 is sent only once the
        principal side is confirmed to read it (principal_handoff.principal_side_reads), or when the principal side is
        the built-in owner side, which reads what brunna reads"""
        six = {"send_version": "0.6"}
        for cfg, ok in (({"assistant": {"api": "x"}, "principal_handoff": six}, False),
                        ({"assistant": {"api": "x"}, "principal_handoff": {**six, "principal_side_reads": ["0.5"]}}, False),
                        ({"assistant": {"api": "x"}, "principal_handoff": {**six, "principal_side_reads": ["0.5", "0.6"]}},
                         True),
                        ({"owner_side": "builtin", "principal_handoff": six}, True),
                        ({"principal_handoff": six}, True),   # no assistant: the built-in owner side
                        ({"owner_side": "assistant", "assistant": {"api": "x"},
                          "principal_handoff": {"send_version": "0.5", "principal_side_reads": ["0.6"]}}, False)):
            with self.subTest(cfg=cfg), self.settings(cfg):
                if ok:
                    self.assertEqual(ph_wire.send_version(), cfg["principal_handoff"]["send_version"])
                else:
                    with self.assertRaises(SystemExit) as e:
                        ph_wire.send_version()
                    self.assertIn("principal_side_reads", str(e.exception))
                self.assertEqual(ph_wire.config_errors(cfg) == [], ok)

    def check(self, hub: dict) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp, "hub.json").write_text(json.dumps(hub))
            return subprocess.run([sys.executable, str(ROOT / "bin" / "config-check")], cwd=tmp, capture_output=True,
                                  text=True, env={**os.environ, "AGENT_HUB_ROOT": str(ROOT), "AGENT_HUB_CONFIG": tmp,
                                                  "AGENT_HUB_LANG_UI": "en"})

    def test_the_schema_and_config_check_know_the_setting(self):
        import minischema
        schema = json.loads((ROOT / "contracts" / "hub.v1.schema.json").read_text())
        base = minischema.errors(schema, {})
        for v in ph_wire.SENDS:
            self.assertEqual(minischema.errors(schema, {"principal_handoff": {"send_version": v}}), base)
        self.assertEqual(minischema.errors(schema, {"principal_handoff": {"principal_side_reads": ["0.5", "0.6"]}}), base)
        for bad in ({"send_version": "0.3"}, {"send_version": 0.5}, {"send_versoin": "0.5"},
                    {"principal_side_reads": "0.6"}, {"principal_side_reads": []}, {"principal_side_reads": ["0.7"]}):
            self.assertNotEqual(minischema.errors(schema, {"principal_handoff": bad}), base, bad)
        assistant = {"assistant": {"api": "http://127.0.0.1:1", "project": "helper"}}
        r = self.check({**assistant, "principal_handoff": {"send_version": "0.6"}})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("principal_side_reads", r.stdout + r.stderr)
        r = self.check({**assistant, "principal_handoff": {"send_version": "0.6", "principal_side_reads": ["0.5", "0.6"]}})
        self.assertNotIn("principal_handoff", r.stdout + r.stderr)
        self.assertNotIn("send_version", r.stdout + r.stderr)


class Strict(unittest.TestCase):
    """core §4: a record is I-JSON (the reading raw.json tests through tests/test_ph_records.py)"""

    def test_a_member_named_twice_marks_that_object_only(self):
        page = ph_wire.loads('{"records": [{"id": "a", "id": "b"}, {"id": "c"}]}')
        self.assertIsInstance(page["records"][0], ph_wire.Duplicated)
        self.assertEqual(ph_wire.ijson_errors(page["records"][0]), ["$: a member name appears twice"])
        self.assertEqual(ph_wire.ijson_errors(page["records"][1]), [])
        self.assertEqual(page["records"][0], {"id": "b"})

    def test_what_is_not_i_json(self):
        self.assertTrue(ph_wire.ijson_errors(ph_wire.loads('{"a": "\\ud83d"}')))
        self.assertTrue(ph_wire.ijson_errors(ph_wire.loads('{"a": 1.0}')))
        self.assertEqual(ph_wire.ijson_errors(ph_wire.loads('{"a": "\\ud83d\\ude00", "b": 9007199254740991}')), [])
        for text in ('{"a": NaN}', '{"a": Infinity}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                ph_wire.loads(text)

    def test_parse(self):
        self.assertEqual(ph_wire.parse('{"a": 1}'), ({"a": 1}, []))
        self.assertEqual(ph_wire.parse('{"a": 1.5}'), ({"a": 1.5}, []), "numbers are validation's to judge")
        for text in ('[]', 'null', '{"a": 1, "a": 2}', '{"a": "\\udead"}', '{} {}'):
            with self.subTest(text=text):
                self.assertEqual(ph_wire.parse(text)[0], None)


if __name__ == "__main__":
    unittest.main()
