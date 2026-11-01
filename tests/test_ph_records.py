"""bin/ph_records.py, agent-hub as a conforming requester (core §12, Acts and Decisions 0.5 §7.1): the spec's
records.json in full, and lifecycle.json compared on the settlement view (settlement, outcome, answer, authority,
stale, state, withdrawn), in the fixtures' order and reversed (contracts/vendor/agent-ops-specs/principal-handoff-0.5/);
0.4's records.json by 0.4's rules (core §14: a record in N−1 is validated under N−1).

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
V04 = ROOT / "contracts" / "vendor" / "agent-ops-specs" / "principal-handoff-0.4"


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

    def test_0_4_records_by_0_4_rules(self):
        """core §14: a record in 0.4 is validated by 0.4's rules, so 0.4's records.json holds in full"""
        for ext in ("acts", "decisions"):
            s = json.loads((V04 / ext / "records.json").read_text())
            self.assertTrue(s["cases"])
            for c in s["cases"]:
                with self.subTest(ext=ext, case=c["id"]):
                    cfg = ph_records.Config(s["principal"], c.get("principal_type", s.get("principal_type")) == "legal")
                    name, errs = ph_records.validate(c["record"], cfg)
                    if c["valid"]:
                        self.assertEqual((name, errs), (c["schema"], []))
                    else:
                        self.assertTrue(errs, f"expected invalid, validated against {name}")


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

    def test_a_valid_0_4_record_is_held_in_its_0_5_form(self):
        s = settled(terms_digest=ph_wire.terms_digest(NEED, "0.4"))
        valid, ignored = ph_records.received([NEED, s], CFG)
        self.assertEqual((valid, ignored), ([ph_wire.convert(NEED, "0.5", [NEED, s]),
                                             ph_wire.convert(s, "0.5", [NEED, s])], []))
        self.assertEqual(valid[1]["terms_digest"], ph_wire.terms_digest(NEED, "0.5"))

    def test_the_0_4_acts_lifecycle_on_the_settlement_view(self):
        """0.4's lifecycle.json (Acts) folds as 0.4 says, on the settlement view, in the fixtures' order and
        reversed: valid 0.4 records are kept and converted, invalid ones ignored"""
        s = json.loads((V04 / "acts" / "lifecycle.json").read_text())
        self.assertTrue(s["cases"])
        cfg = ph_records.Config(s["principal"])
        for c in s["cases"]:
            want = {k: c["expect"][k] for k in ph_records.VIEW}
            with self.subTest(case=c["id"]):
                self.assertEqual(ph_records.settlement_view(c["records"], cfg), want)
                self.assertEqual(ph_records.settlement_view(list(reversed(c["records"])), cfg), want, "reversed")

    def test_a_0_4_record_invalid_in_0_4_is_ignored_though_valid_in_0_5(self):
        """core §14: validated under 0.4, not as its 0.5 form, so every receiver ignores what 0.4 makes invalid: here
        Evidence and Criteria content, which 0.5 moved to modules and no longer checks"""
        neither = {"verifiable": [{"id": "e1", "format": "x", "statement": "paid"}]}   # 0.4 V1: proof or proof_ref
        bad_settled = signed(evidence=neither)
        dq = rec(ext="decisions/0.4", id="r:q", key="q", type="raised", terms_rev=1,
                 terms={"text": "Which?", "criteria": {"subject": {"uri": "u", "digest": "sha-256:" + "0" * 64},
                                                           "checks": [], "answer": {"text": "a"}}})   # 0.4: one check
        for bad, held in ((bad_settled, [NEED, bad_settled]), (dq, [dq])):
            with self.subTest(type=bad["type"]):
                self.assertTrue(ph_records.validate(bad, CFG)[1], "invalid in 0.4")
                self.assertEqual(ph_records.validate(ph_wire.convert(bad, "0.5", held), CFG), (bad["type"], []),
                                 "its 0.5 form is valid")
                valid, ignored = ph_records.received(held, CFG)
                self.assertEqual(ignored, [bad])
                self.assertNotIn(bad["id"], [r["id"] for r in valid])
        view = ph_records.settlement_view([NEED, bad_settled], CFG)
        self.assertEqual((view["settlement"], view["state"]), (None, "open"))

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

    def test_a_0_4_and_a_0_5_record_of_one_identity(self):
        """core §10 across versions: the greater RFC 8785 form of the 0.5 forms, whichever version and order"""
        up = lambda r: ph_wire.convert(r, "0.5", [NEED])   # noqa: E731
        for old, new, winner in ((signed(note="a"), up(signed(note="b")), "new"),
                                 (signed(note="z"), up(signed(note="b")), "old")):
            want = old if winner == "old" else new
            for records in ([NEED, old, new], [NEED, new, old]):
                with self.subTest(winner=winner, order=[r["ext"] for r in records]):
                    self.assertEqual(self.take(records), [NEED, want])
                    [x] = ph_records.conflicts(records, CFG)
                    self.assertEqual((x["identity"], records[x["kept"]], x["ts_only"]),
                                     (("acts", "p", "principal", "settled", "s1"), want, False))
                    valid, ignored = ph_records.received(records, CFG)
                    self.assertEqual(([r.get("note") for r in valid if r["type"] == "settled"], ignored),
                                     ([want["note"]], []))

    def test_a_0_4_record_invalid_in_0_4_never_displaces_a_valid_one(self):
        """core §10 over 0.4 validity: the invalid one's 0.5 form would be greater, yet the valid one is taken"""
        good = signed(note="a")
        bad = signed(note="z", by="zz", evidence={"verifiable": [{"id": "e1", "format": "x", "statement": "paid"}]})
        self.assertGreater(ph_wire.canonical(ph_wire.convert(bad, "0.5", [NEED])),
                           ph_wire.canonical(ph_wire.convert(good, "0.5", [NEED])))
        for records in ([NEED, good, bad], [NEED, bad, good]):
            with self.subTest(first=records[1]["note"]):
                self.assertEqual(self.take(records), [NEED, good])
                self.assertEqual(ph_records.conflicts(records, CFG), [])
                self.assertEqual(ph_records.settlement_view(records, CFG)["settlement"], "s1")

    def test_a_lone_record_is_not_validated(self):
        """the config is read only when an identity holds two records"""
        bad = signed(by="person:alex")
        self.assertEqual(ph_records.prevailing([NEED, bad], None), [0, 1])


if __name__ == "__main__":
    unittest.main()
