"""What brunna computes from the 0.5 and 0.4 fixtures, pinned before the upgrade to Principal Handoff 0.6
(docs/design/principal-handoff-0.6-upgrade.md): records of an older version keep being validated by their own version's
rules and folded to the same result, whatever version the fold runs in.

tests/fixtures/ph-0.5-characterization.json holds, for every lifecycle case of 0.5 (Acts, Decisions) and 0.4 (Acts), in
the fixtures' order and reversed, the records a requester holds ([origin, type, id]), the ids it ignores and its
settlement view; for every records.json case of 0.5 and 0.4, the schema validate names and whether the record is valid.
It was written from the code as it was before the upgrade.

    bin/py -m unittest tests.test_ph_characterization
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import ph_records  # noqa: E402

VENDOR = ROOT / "contracts" / "vendor" / "agent-ops-specs"
GOLDEN = json.loads((ROOT / "tests" / "fixtures" / "ph-0.5-characterization.json").read_text())
LIFECYCLE = (("0.5", "acts", VENDOR / "principal-handoff-0.5" / "acts" / "conformance" / "lifecycle.json"),
             ("0.5", "decisions", VENDOR / "principal-handoff-0.5" / "decisions" / "conformance" / "lifecycle.json"),
             ("0.4", "acts", VENDOR / "principal-handoff-0.4" / "acts" / "lifecycle.json"))
RECORDS = (("0.5", "acts", VENDOR / "principal-handoff-0.5" / "acts" / "conformance" / "records.json"),
           ("0.5", "decisions", VENDOR / "principal-handoff-0.5" / "decisions" / "conformance" / "records.json"),
           ("0.4", "acts", VENDOR / "principal-handoff-0.4" / "acts" / "records.json"),
           ("0.4", "decisions", VENDOR / "principal-handoff-0.4" / "decisions" / "records.json"))


def fold(records: list, cfg: ph_records.Config) -> dict:
    valid, ignored = ph_records.received(records, cfg)
    return {"held": sorted([r["origin"], r["type"], r["id"]] for r in valid),
            "ignored": sorted(str(r.get("id")) if isinstance(r, dict) else repr(r) for r in ignored),
            "view": ph_records.settlement_view(records, cfg)}


class Older(unittest.TestCase):
    def test_lifecycle_cases_hold_ignore_and_fold_as_before(self):
        n = 0
        for version, ext, path in LIFECYCLE:
            s = json.loads(path.read_text())
            cfg = ph_records.Config(s["principal"])
            for c in s["cases"]:
                want = GOLDEN["lifecycle"][f"{version}/{ext}/{c['id']}"]
                with self.subTest(version=version, ext=ext, case=c["id"]):
                    self.assertEqual(fold(c["records"], cfg), want["order"])
                    self.assertEqual(fold(list(reversed(c["records"])), cfg), want["reversed"], "reversed")
                n += 1
        self.assertEqual(n, len(GOLDEN["lifecycle"]))

    def test_records_cases_validate_as_before(self):
        n = 0
        for version, ext, path in RECORDS:
            s = json.loads(path.read_text())
            for c in s["cases"]:
                cfg = ph_records.Config(s["principal"], c.get("principal_type", s.get("principal_type")) == "legal")
                name, errs = ph_records.validate(c["record"], cfg)
                with self.subTest(version=version, ext=ext, case=c["id"]):
                    self.assertEqual([name, not errs], GOLDEN["records"][f"{version}/{ext}/{c['id']}"])
                n += 1
        self.assertEqual(n, len(GOLDEN["records"]))


if __name__ == "__main__":
    unittest.main()
