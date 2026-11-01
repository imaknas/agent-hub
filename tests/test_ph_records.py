"""bin/ph_records.py, agent-hub as a conforming requester (core §12, Acts and Decisions 0.5 §7.1): the spec's
records.json in full, and lifecycle.json compared on the settlement view (settlement, outcome, answer, authority,
stale, state, withdrawn), in the fixtures' order and reversed (contracts/vendor/agent-ops-specs/principal-handoff-0.5/).

    bin/py -m unittest tests.test_ph_records
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import ph_records  # noqa: E402
import ph_wire  # noqa: E402

SPEC = ROOT / "contracts" / "vendor" / "agent-ops-specs" / "principal-handoff-0.5"


def suite(ext: str, name: str) -> dict:
    return json.loads((SPEC / ext / "conformance" / name).read_text())


class Conformance(unittest.TestCase):
    def records(self, ext: str) -> None:
        s = suite(ext, "records.json")
        self.assertTrue(s["cases"])
        for c in s["cases"]:
            with self.subTest(ext=ext, case=c["id"]):
                cfg = ph_records.Config(s["principal"], c.get("principal_type", s.get("principal_type")) == "legal")
                name, errs = ph_records.validate(c["record"], cfg)
                if c["valid"]:
                    self.assertEqual((name, errs), (c["schema"], []))
                else:
                    self.assertTrue(errs, f"expected invalid, validated against {name}")

    def lifecycle(self, ext: str) -> None:
        s = suite(ext, "lifecycle.json")
        self.assertTrue(s["cases"])
        cfg = ph_records.Config(s["principal"])
        for c in s["cases"]:
            want = {k: c["expect"][k] for k in ph_records.VIEW}
            with self.subTest(ext=ext, case=c["id"]):
                self.assertEqual(ph_records.settlement_view(c["records"], cfg), want)
                self.assertEqual(ph_records.settlement_view(list(reversed(c["records"])), cfg), want, "reversed")

    def test_acts_records(self):
        self.records("acts")

    def test_decisions_records(self):
        self.records("decisions")

    def test_acts_lifecycle_settlement_view(self):
        self.lifecycle("acts")

    def test_decisions_lifecycle_settlement_view(self):
        self.lifecycle("decisions")


def rec(**kw) -> dict:
    return {"ph": "0.4", "ext": "acts/0.4", "origin": "requester", "by": "registrar", "key": "k",
            "project": "p", "ts": "2026-10-20T10:00:00Z", **kw}


CFG = ph_records.Config("person:alex")
NEED = rec(id="r:k", type="raised", terms={"text": "Pay"}, terms_rev=1)


def settled(**kw) -> dict:
    return rec(**{"id": "s1", "type": "settled", "origin": "principal", "by": "helper", "outcome": "done",
                  "terms_rev": 1, "authority": {"kind": "principal", "decider": "person:alex"},
                  "ts": "2026-10-21T10:00:00Z", **kw})


class MixedVersions(unittest.TestCase):
    """core §14: records kept as sent, converted to 0.5 at each fold"""

    def test_a_0_4_settlement_on_its_need_record_is_not_stale(self):
        s = settled(terms_digest=ph_wire.terms_digest(NEED, "0.4"))
        self.assertEqual(ph_records.settlement_view([NEED, s], CFG),
                         {"settlement": "s1", "outcome": "done", "answer": None, "authority": "principal",
                          "stale": False, "state": "settled", "withdrawn": False})

    def test_a_0_4_settlement_on_other_terms_is_stale(self):
        other = {**NEED, "terms": {"text": "Pay twice"}}
        s = settled(terms_digest=ph_wire.terms_digest(other, "0.4"))
        self.assertTrue(ph_records.settlement_view([NEED, s], CFG)["stale"])

    def test_a_0_3_settlement_without_a_digest(self):
        s = {k: v for k, v in settled(ph="0.3", ext="acts/0.3").items()}
        self.assertFalse(ph_records.settlement_view([NEED, s], CFG)["stale"])
        self.assertEqual(ph_records.settlement_view([s], CFG)["settlement"], "s1", "valid without its need record")

    def test_the_same_record_in_two_versions_is_held_once(self):
        up = ph_wire.convert(NEED, "0.5", [NEED])
        valid, ignored = ph_records.received([NEED, up], CFG)
        self.assertEqual((len(valid), ignored), (1, []))

    def test_a_record_never_displaces_one_of_another_type(self):
        """core §10 (0.5): the same id on a raised and a note keeps both"""
        note = rec(id="r:k", type="note", about={"key": "k"}, note="x")
        valid, _ = ph_records.received([NEED, note], CFG)
        self.assertEqual(sorted(r["type"] for r in valid), ["note", "raised"])

    def test_invalid_records_are_ignored(self):
        bad = settled(terms_digest=ph_wire.terms_digest(NEED, "0.4"), by="person:alex")   # R6
        valid, ignored = ph_records.received([NEED, bad], CFG)
        self.assertEqual(([r["id"] for r in valid], ignored), (["r:k"], [bad]))

    def test_a_record_with_no_rfc_8785_form_is_invalid(self):
        """core §4: records are I-JSON; one with an unpaired surrogate is ignored, not an exception"""
        bad = rec(id="r:k", type="raised", terms={"text": "Pay \ud800"}, terms_rev=1)
        self.assertEqual(ph_records.received([bad], CFG), ([], [bad]))


def signed(**kw) -> dict:
    """a valid 0.4 settlement of NEED"""
    return settled(terms_digest=ph_wire.terms_digest(NEED, "0.4"), **kw)


class Prevailing(unittest.TestCase):
    """core §10 for the bus, which stores every content it was sent: one record per identity, the greater RFC 8785
    form of the valid ones, whatever the order"""

    def take(self, records: list[dict]) -> list[dict]:
        return [records[i] for i in ph_records.prevailing(records, CFG)]

    def test_the_lifecycle_vectors_through_it(self):
        """what prevails folds to what the spec expects, in the fixtures' order and reversed: it drops nothing a fold
        needs, and the reused-id cases come out the same both ways"""
        for ext in ("acts", "decisions"):
            s = suite(ext, "lifecycle.json")
            cfg = ph_records.Config(s["principal"])
            for c in s["cases"]:
                want = {k: c["expect"][k] for k in ph_records.VIEW}
                for records in (c["records"], list(reversed(c["records"]))):
                    with self.subTest(ext=ext, case=c["id"]):
                        kept = [records[i] for i in ph_records.prevailing(records, cfg)]
                        self.assertEqual(ph_records.settlement_view(kept, cfg), want)

    def test_the_greater_valid_one_whatever_the_order(self):
        a, b = signed(note="a"), signed(note="b")
        self.assertEqual(self.take([NEED, a, b]), [NEED, b])
        self.assertEqual(self.take([NEED, b, a]), [NEED, b])
        [x] = ph_records.conflicts([NEED, a, b], CFG)
        self.assertEqual((x["identity"], x["records"], x["kept"], x["ts_only"]),
                         (("acts", "p", "principal", "settled", "s1"), [1, 2], 2, False))

    def test_an_invalid_one_never_displaces_or_blocks(self):
        good, bad = signed(note="a"), signed(note="z", by="person:alex")   # R6
        self.assertEqual(self.take([NEED, good, bad]), [NEED, good])
        self.assertEqual(self.take([NEED, bad, good]), [NEED, good])
        self.assertEqual(ph_records.conflicts([NEED, bad, good], CFG), [])
        self.assertEqual(self.take([NEED, bad, signed(note="y", by="person:alex")]),
                         [NEED, bad, signed(note="y", by="person:alex")], "no valid one: all left for the fold")

    def test_one_record_in_two_versions_is_one(self):
        up = ph_wire.convert(NEED, "0.5", [NEED])
        self.assertEqual(self.take([NEED, up]), [NEED])
        self.assertEqual(self.take([up, NEED]), [up], "the same record: the first stands")
        self.assertEqual(ph_records.conflicts([NEED, up], CFG), [])

    def test_ts_only(self):
        later = signed(note="a", ts="2026-10-22T10:00:00Z")
        self.assertEqual(self.take([signed(note="a"), later]), [later])
        [x] = ph_records.conflicts([signed(note="a"), later], CFG)
        self.assertTrue(x["ts_only"])

    def test_a_lone_record_is_not_validated(self):
        """the config is read only when an identity holds two records"""
        bad = signed(by="person:alex")
        self.assertEqual(ph_records.prevailing([NEED, bad], None), [0, 1])


if __name__ == "__main__":
    unittest.main()
