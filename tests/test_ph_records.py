"""bin/ph_records.py, brunna as a conforming requester (core §12) and converting party (core §14), against the spec's
0.6.1 fixtures (contracts/vendor/agent-ops-specs/principal-handoff-0.6.1/): Acts' and Decisions' records.json in full
(`valid: null`: kept, not acted on), lifecycle.json compared on the settlement view (settlement, outcome, answer,
authority, stale) in the fixtures' order and reversed, relations.json in full, core raw.json (the strict reading of a
record's text) and convert.json's held cases (what a party configured for 0.6 that also implements 0.5 holds). Records
of 0.5 keep being validated by 0.5's rules (core §14), and of 0.4 by 0.4's (brunna's own reading of an older version):
their records.json and lifecycle.json run too.

No case of 0.6.1 is expected to fail: every one passes (docs/design/principal-handoff-0.6-upgrade.md, report 1).

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

VENDOR = ROOT / "contracts" / "vendor" / "agent-ops-specs"
SPEC = VENDOR / "principal-handoff-0.6.1"
V05 = VENDOR / "principal-handoff-0.5"
V04 = VENDOR / "principal-handoff-0.4"
REQUESTER = ("settlement", "outcome", "answer", "authority", "stale")   # what a requester compares (core §12)


def suite(spec: pathlib.Path, ext: str, name: str) -> dict:
    return json.loads((spec / ext / "conformance" / name).read_text())


def cfg_of(s: dict, c: dict) -> ph_records.Config:
    return ph_records.Config(s["principal"], c.get("principal_type", s.get("principal_type")) == "legal")


class Conformance(unittest.TestCase):
    """0.6.1"""

    def records(self, s: dict) -> int:
        for c in s["cases"]:
            with self.subTest(case=c["id"]):
                name, errs = ph_records.validate(c["record"], cfg_of(s, c))
                if c["valid"] is None:   # an extension or version brunna does not implement: kept, not acted on
                    self.assertFalse(ph_records.implemented(c["record"]))
                    self.assertEqual((name, errs), (None, [ph_records.NOT_IMPLEMENTED]))
                    self.assertEqual(ph_records.received([c["record"]], cfg_of(s, c)), ([], [c["record"]]))
                elif c["valid"]:
                    self.assertEqual((name, errs), (c["schema"], []))
                else:
                    self.assertTrue(errs, f"expected invalid, validated against {name}")
        return len(s["cases"])

    def test_acts_records(self):
        self.assertEqual(self.records(suite(SPEC, "acts", "records.json")), 262)

    def test_decisions_records(self):
        self.assertEqual(self.records(suite(SPEC, "decisions", "records.json")), 279)

    def lifecycle(self, s: dict) -> int:
        cfg = ph_records.Config(s["principal"])
        for c in s["cases"]:
            want = {k: c["expect"][k] for k in REQUESTER}
            for order, records in (("given", c["records"]), ("reversed", list(reversed(c["records"])))):
                with self.subTest(case=c["id"], order=order):
                    view = ph_records.settlement_view(records, cfg)
                    self.assertEqual({k: view[k] for k in REQUESTER}, want)
        return len(s["cases"])

    def test_acts_lifecycle_settlement_view(self):
        self.assertEqual(self.lifecycle(suite(SPEC, "acts", "lifecycle.json")), 73)

    def test_decisions_lifecycle_settlement_view(self):
        self.assertEqual(self.lifecycle(suite(SPEC, "decisions", "lifecycle.json")), 78)

    def relations(self, s: dict) -> int:
        cfg = ph_records.Config(s["principal"])
        for c in s["cases"]:
            for order, records in (("given", c["records"]), ("reversed", list(reversed(c["records"])))):
                with self.subTest(case=c["id"], order=order):
                    self.assertEqual({"relations": ph_records.relations(records, cfg),
                                      "replacement": ph_records.replacement(records, cfg)}, c["expect"])
        return len(s["cases"])

    def test_acts_relations(self):
        self.assertEqual(self.relations(suite(SPEC, "acts", "relations.json")), 15)

    def test_decisions_relations(self):
        self.assertEqual(self.relations(suite(SPEC, "decisions", "relations.json")), 15)

    def test_raw(self):
        """core §4 at the text: a strict reader (ph_wire.parse), then validation under the extension `ext` names;
        brunna implements both extensions, so it runs every case"""
        s = suite(SPEC, "core", "raw.json")
        for c in s["cases"]:
            with self.subTest(case=c["id"]):
                rec, errs = ph_wire.parse(c["raw"])
                name, errs = ph_records.validate(rec, cfg_of(s, c)) if rec is not None else (None, errs)
                if c["valid"]:
                    self.assertEqual((name, errs), (c["schema"], []))
                else:
                    self.assertTrue(errs, f"expected invalid, validated against {name}")
        self.assertEqual(len(s["cases"]), 50)

    def test_convert_held(self):
        """core §6, §14 (convert.json `held`, Acts): what a party configured for 0.6 that also implements 0.5 holds, and
        the act's active relations, whatever the order"""
        s = suite(SPEC, "core", "convert.json")
        cfg = ph_records.Config(s["principal"])
        for c in s["held"]:
            for order, records in (("given", c["records"]), ("reversed", list(reversed(c["records"])))):
                with self.subTest(case=c["id"], order=order):
                    self.assertEqual({"held": sorted(r["id"] for r in ph_records.received(records, cfg)[0]),
                                      "relations": ph_records.relations(records, cfg)}, c["expect"])
        self.assertEqual(len(s["held"]), 12)


class Older(unittest.TestCase):
    """0.5 by 0.5's rules (core §14 item 2), 0.4 by 0.4's: their records.json in full, lifecycle.json on the settlement
    view, both orders"""

    def test_0_5_records_by_0_5_rules(self):
        for ext in ("acts", "decisions"):
            s = suite(V05, ext, "records.json")
            self.assertTrue(s["cases"])
            for c in s["cases"]:
                with self.subTest(ext=ext, case=c["id"]):
                    name, errs = ph_records.validate(c["record"], cfg_of(s, c))
                    if c["valid"]:
                        self.assertEqual((name, errs), (c["schema"], []))
                    else:
                        self.assertTrue(errs, f"expected invalid, validated against {name}")

    def test_0_5_lifecycle_on_the_settlement_view(self):
        for ext in ("acts", "decisions"):
            s = suite(V05, ext, "lifecycle.json")
            cfg = ph_records.Config(s["principal"])
            for c in s["cases"]:
                want = {k: c["expect"][k] for k in ph_records.VIEW}
                with self.subTest(ext=ext, case=c["id"]):
                    self.assertEqual(ph_records.settlement_view(c["records"], cfg), want)
                    self.assertEqual(ph_records.settlement_view(list(reversed(c["records"])), cfg), want, "reversed")

    def test_0_4_records_by_0_4_rules(self):
        for ext in ("acts", "decisions"):
            s = json.loads((V04 / ext / "records.json").read_text())
            self.assertTrue(s["cases"])
            for c in s["cases"]:
                with self.subTest(ext=ext, case=c["id"]):
                    name, errs = ph_records.validate(c["record"], cfg_of(s, c))
                    if c["valid"]:
                        self.assertEqual((name, errs), (c["schema"], []))
                    else:
                        self.assertTrue(errs, f"expected invalid, validated against {name}")

    def test_malformed_records_of_any_version_are_invalid_not_an_error(self):
        """0.6.1's malformed shapes (a type that is not a string, an authority that is not an object…), labelled 0.5
        and 0.4: a reader never stops on them (they crashed the validator before the upgrade)"""
        n = 0
        for ext in ("acts", "decisions"):
            s = suite(SPEC, ext, "records.json")
            for c in s["cases"]:
                if c["valid"] is not False or not c["id"].endswith(("-not-an-object", "type-not-a-word")):
                    continue
                for v in ("0.5", "0.4"):
                    r = {**c["record"], "ph": v, "ext": f"{ext}/{v}"}
                    with self.subTest(case=c["id"], version=v):
                        self.assertTrue(ph_records.validate(r, cfg_of(s, c))[1])
                        self.assertEqual(ph_records.received([r], cfg_of(s, c)), ([], [r]))
                        n += 1
        self.assertEqual(n, 34)

    def test_the_0_4_acts_lifecycle_on_the_settlement_view(self):
        """valid 0.4 records are kept and converted, invalid ones ignored"""
        s = json.loads((V04 / "acts" / "lifecycle.json").read_text())
        self.assertTrue(s["cases"])
        cfg = ph_records.Config(s["principal"])
        for c in s["cases"]:
            want = {k: c["expect"][k] for k in ph_records.VIEW}
            with self.subTest(case=c["id"]):
                self.assertEqual(ph_records.settlement_view(c["records"], cfg), want)
                self.assertEqual(ph_records.settlement_view(list(reversed(c["records"])), cfg), want, "reversed")


def rec(**kw) -> dict:
    return {"ph": "0.4", "ext": "acts/0.4", "origin": "requester", "by": "registrar", "key": "k",
            "project": "p", "ts": "2026-10-20T10:00:00Z", **kw}


CFG = ph_records.Config("person:alex")
NEED = rec(id="r:k", type="raised", terms={"text": "Pay"}, terms_rev=1)


def settled(**kw) -> dict:
    return rec(**{"id": "s1", "type": "settled", "origin": "principal", "by": "helper", "outcome": "done",
                  "terms_rev": 1, "authority": {"kind": "principal", "decider": "person:alex"},
                  "ts": "2026-10-21T10:00:00Z", **kw})


def v05(r: dict) -> dict:
    return ph_wire.convert(r, "0.5", [NEED])


def related(target: dict, **kw) -> dict:
    return {**v05(NEED), "id": "rel1", "type": "related", "ts": "2026-10-22T10:00:00Z",
            "relation": {"kind": "derived_from", "target": target, "active": True}, **kw}


class MixedVersions(unittest.TestCase):
    """core §14: records kept as sent, held in their 0.6 form at each fold"""

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

    def test_a_valid_0_4_record_is_held_in_its_0_6_form(self):
        s = settled(terms_digest=ph_wire.terms_digest(NEED, "0.4"))
        valid, ignored = ph_records.received([NEED, s], CFG)
        self.assertEqual((valid, ignored), ([ph_wire.convert(NEED, "0.6", [NEED, s]),
                                             ph_wire.convert(s, "0.6", [NEED, s])], []))
        self.assertEqual([r["ext"] for r in valid], ["acts/0.6", "acts/0.6"])
        self.assertEqual(valid[1]["terms_digest"], ph_wire.terms_digest(NEED, "0.6"))

    def test_a_0_4_record_invalid_in_0_4_is_ignored_though_valid_in_0_5(self):
        """validated under 0.4, not as its later form, so every receiver ignores what 0.4 makes invalid: here
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

    def test_the_same_record_in_three_versions_is_held_once(self):
        records = [NEED, v05(NEED), ph_wire.convert(NEED, "0.6", [NEED])]
        valid, ignored = ph_records.received(records, CFG)
        self.assertEqual((len(valid), ignored), (1, []))

    def test_a_record_never_displaces_one_of_another_type(self):
        """core §10 (since 0.5): the same id on a raised and a note keeps both"""
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

    def test_a_record_of_a_version_not_implemented_is_not_acted_on(self):
        """core 0.6 §4, §6: neither held nor an error; the bus keeps it (tests/test_hubbus_bridge_v2.py)"""
        for later in ({**settled(), "ph": "0.7", "ext": "acts/0.7"}, {**settled(), "ph": "0.6", "ext": "acts/0.6.1"},
                      {**settled(), "ph": "0.6", "ext": "other/0.6"}):
            with self.subTest(ext=later["ext"]):
                self.assertFalse(ph_records.implemented(later))
                self.assertEqual(ph_records.received([NEED, later], CFG)[1], [later])
                self.assertEqual(ph_records.settlement_view([NEED, later], CFG)["settlement"], None)


class Converting(unittest.TestCase):
    """core 0.6 §14 item 2: what brunna, configured for 0.6 and implementing 0.5, holds"""

    def test_a_0_5_relation_is_held_with_the_extension_named(self):
        r = related({"ext": "decisions/0.5", "project": "p", "key": "q1"})
        [held], [] = ph_records.received([r], CFG)
        self.assertEqual((held["ph"], held["relation"]["target"]["ext"]), ("0.6", "decisions"))
        self.assertEqual(ph_records.relations([r], CFG),
                         [{"kind": "derived_from", "target": {"ext": "decisions", "project": "p", "key": "q1"}}])

    def test_a_0_5_relation_with_no_0_6_form_is_not_held(self):
        r = related({"ext": "decisions/0.5", "project": "two words", "key": "q1"})
        self.assertEqual(ph_records.validate(r, CFG), ("related", []), "valid in 0.5")
        self.assertEqual(ph_records.received([r], CFG), ([], [r]))

    def test_a_0_5_record_is_validated_by_0_5_rules_even_where_0_6_relaxes(self):
        """a replaced_by naming the extension without its version is invalid in 0.5, valid in 0.6"""
        r = related({"ext": "acts", "project": "p", "key": "k2"})
        r["relation"]["kind"] = "replaced_by"
        self.assertTrue(ph_records.validate(r, CFG)[1])
        self.assertEqual(ph_records.validate(ph_wire.convert(r, "0.6"), CFG), ("related", []))
        self.assertEqual(ph_records.received([r], CFG), ([], [r]))

    def test_brunnas_own_0_5_records_are_held_as_their_0_6_form(self):
        """core §14 item 2: the 0.5 form of a record brunna made exists only on the wire; it holds the 0.6 form, by
        0.6's rules"""
        r = related({"ext": "acts", "project": "p", "key": "k2"})
        r["relation"]["kind"] = "replaced_by"
        own = ph_records.Config("person:alex", own=frozenset({"requester"}))
        [held], [] = ph_records.received([r], own)
        self.assertEqual(held, ph_wire.convert(r, "0.6"))
        self.assertEqual(ph_records.replacement([r], own), {"ext": "acts", "project": "p", "key": "k2"})

    def test_one_relation_in_two_versions_is_one(self):
        """core §7: identified by the extension's name, so 0.5 and 0.6 records of one relation fold together"""
        on = related({"ext": "acts/0.5", "project": "p", "key": "k2"})
        off = {**ph_wire.convert(on, "0.6"), "id": "rel2", "ts": "2026-10-23T10:00:00Z",
               "relation": {"kind": "derived_from", "target": {"ext": "acts", "project": "p", "key": "k2"},
                            "active": False}}
        self.assertEqual(ph_records.relations([on, off], CFG), [])
        self.assertEqual(ph_records.relations([off, on], CFG), [])


def signed(**kw) -> dict:
    """a valid 0.4 settlement of NEED"""
    return settled(terms_digest=ph_wire.terms_digest(NEED, "0.4"), **kw)


class Prevailing(unittest.TestCase):
    """core §10 for the bus, which stores every content it was sent: one record per identity, the greater RFC 8785
    form (of the 0.6 forms) of the valid ones, whatever the order"""

    def take(self, records: list[dict]) -> list[dict]:
        return [records[i] for i in ph_records.prevailing(records, CFG)]

    def test_the_lifecycle_vectors_through_it(self):
        """what prevails folds to what the spec expects, in the fixtures' order and reversed: it drops nothing a fold
        needs, and the reused-id cases come out the same both ways"""
        for ext in ("acts", "decisions"):
            s = suite(SPEC, ext, "lifecycle.json")
            cfg = ph_records.Config(s["principal"])
            for c in s["cases"]:
                want = {k: c["expect"][k] for k in REQUESTER}
                for records in (c["records"], list(reversed(c["records"]))):
                    with self.subTest(ext=ext, case=c["id"]):
                        kept = [records[i] for i in ph_records.prevailing(records, cfg)]
                        view = ph_records.settlement_view(kept, cfg)
                        self.assertEqual({k: view[k] for k in REQUESTER}, want)

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
        up = v05(NEED)
        self.assertEqual(self.take([NEED, up]), [NEED])
        self.assertEqual(self.take([up, NEED]), [up], "the same record: the first stands")
        self.assertEqual(ph_records.conflicts([NEED, up], CFG), [])

    def test_ts_only(self):
        later = signed(note="a", ts="2026-10-22T10:00:00Z")
        self.assertEqual(self.take([signed(note="a"), later]), [later])
        [x] = ph_records.conflicts([signed(note="a"), later], CFG)
        self.assertTrue(x["ts_only"])

    def test_records_of_one_identity_in_different_versions(self):
        """core §10 across versions: the greater RFC 8785 form of the 0.6 forms, whichever version and order"""
        up = lambda r, v: ph_wire.convert(r, v, [NEED])   # noqa: E731
        for v in ("0.5", "0.6"):
            for old, new, winner in ((signed(note="a"), up(signed(note="b"), v), "new"),
                                     (signed(note="z"), up(signed(note="b"), v), "old")):
                want = old if winner == "old" else new
                for records in ([NEED, old, new], [NEED, new, old]):
                    with self.subTest(version=v, winner=winner, order=[r["ext"] for r in records]):
                        self.assertEqual(self.take(records), [NEED, want])
                        [x] = ph_records.conflicts(records, CFG)
                        self.assertEqual((x["identity"], records[x["kept"]], x["ts_only"]),
                                         (("acts", "p", "principal", "settled", "s1"), want, False))
                        valid, ignored = ph_records.received(records, CFG)
                        self.assertEqual(([r.get("note") for r in valid if r["type"] == "settled"], ignored),
                                         ([want["note"]], []))

    def test_a_0_4_record_invalid_in_0_4_never_displaces_a_valid_one(self):
        """core §10 over 0.4 validity: the invalid one's later form would be greater, yet the valid one is taken"""
        good = signed(note="a")
        bad = signed(note="z", by="zz", evidence={"verifiable": [{"id": "e1", "format": "x", "statement": "paid"}]})
        self.assertGreater(ph_wire.canonical(ph_wire.convert(bad, "0.6", [NEED])),
                           ph_wire.canonical(ph_wire.convert(good, "0.6", [NEED])))
        for records in ([NEED, good, bad], [NEED, bad, good]):
            with self.subTest(first=records[1]["note"]):
                self.assertEqual(self.take(records), [NEED, good])
                self.assertEqual(ph_records.conflicts(records, CFG), [])
                self.assertEqual(ph_records.settlement_view(records, CFG)["settlement"], "s1")

    def test_a_lone_record_is_not_validated(self):
        """the config is read only when an identity holds two records"""
        bad = signed(by="person:alex")
        self.assertEqual(ph_records.prevailing([NEED, bad], None), [0, 1])


class Config(unittest.TestCase):
    def test_brunnas_own_origins(self):
        """the requester side's records are brunna's; the principal side's too when it is the built-in owner side"""
        from unittest import mock
        import hubconf
        for side, own in (("assistant", {"requester", "agent", "registrar"}),
                          ("builtin", {"requester", "agent", "registrar", "principal"})):
            with self.subTest(side=side), mock.patch.object(hubconf, "owner_side", return_value=side):
                self.assertEqual(ph_records.config().own, own)


if __name__ == "__main__":
    unittest.main()
