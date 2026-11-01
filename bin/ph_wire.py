"""Which Principal Handoff version agent-hub's records are in, and the conversion between versions
(agent-ops-specs specs/principal-handoff, core §4 and §14).

agent-hub reads 0.3, 0.4 and 0.5 records. What it sends to the principal side is a setting, hub.json
`principal_handoff.send_version` ("0.4" or "0.5", default "0.4"): before 1.0 every version is its own contract, and the
version is configured per role, project and extension (core §14). One principal side serves every project here, so
one value covers every project and both extensions; it moves to 0.5 once the principal side accepts 0.5.

A record received in another version is kept as it was sent and converted to 0.5 each time it is folded
(bin/ph_records.py), as core §14 says: `convert` here, after the spec's reference (reference/ph_core.py, Apache-2.0),
plus the 0.3 to 0.4 step (Acts and Decisions 0.5 §6: a `settled` gets the terms digest of the need record at its
terms_rev). Conversion keeps `id` and removes `evidence.proof`, which 0.5 senders never emit (core §5.2).

The terms digest (core §4) changed in 0.5: it covers the extension's name without its version (`acts`, not
`acts/0.5`). `terms_digest(need, version)` computes it as that version defines it.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402

READ = ("0.3", "0.4", "0.5")
SENDS = ("0.4", "0.5")   # what agent-hub can send
LATEST = "0.5"           # what records are converted to before they are folded
DEFAULT_SEND = "0.4"     # until the principal side confirms it accepts 0.5


def send_version() -> str:
    """hub.json principal_handoff.send_version: the version agent-hub's records to the principal side are in."""
    v = hubconf.get("principal_handoff.send_version") or DEFAULT_SEND
    if v not in SENDS:
        raise SystemExit(f"hub.json principal_handoff.send_version is {v!r}; agent-hub sends one of {', '.join(SENDS)}")
    return v


def ext(family_name: str) -> str:
    """The `ext` agent-hub sends for an extension (`acts`, `decisions`): `acts/0.4` or `acts/0.5`."""
    return f"{family_name}/{send_version()}"


def version_of(record: dict) -> str:
    """The version a record names in its `ext` (`0.4` of `acts/0.4`)."""
    return str(record.get("ext") or "").partition("/")[2]


def family(ext_value: object) -> str:
    """`acts` of `acts/0.4`; '' for anything not a version this reads"""
    name, _, version = str(ext_value or "").partition("/")
    return name if version in READ else ""


def canonical(value) -> str:
    """RFC 8785 (JCS) for what records hold: objects with string members, strings, integers, booleans, null.
    Members sorted by their UTF-16 code units, as the RFC orders them."""
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: kv[0].encode("utf-16-be"))
        return "{" + ",".join(f"{canonical(k)}:{canonical(v)}" for k, v in items) + "}"
    if isinstance(value, list):
        return "[" + ",".join(canonical(v) for v in value) + "]"
    if isinstance(value, float):
        raise ValueError("records hold integers only (core §4)")
    return json.dumps(value, ensure_ascii=False)


def digest_input(need: dict, version: str = LATEST) -> dict:
    """The object a terms digest is computed over, as `version` defines it: in 0.5 `ext` is the extension's name
    alone (core §4); in 0.4 it was the full `ext` of that version (`acts/0.4`)."""
    name = str(need["ext"]).partition("/")[0]
    return {"ext": name if version == "0.5" else f"{name}/{version}", "project": need["project"], "key": need["key"],
            "terms_rev": need["terms_rev"], "terms": need["terms"]}


def terms_digest(need: dict, version: str = LATEST) -> str:
    """The digest of a need record (raised / amended) that a settlement in `version` carries as terms_digest."""
    return "sha-256:" + hashlib.sha256(canonical(digest_input(need, version)).encode("utf-8")).hexdigest()


def _when(r: dict) -> tuple:
    return (str(r.get("ts", "")), str(r.get("id", "")))


def convert(record: dict, version: str, held: list[dict]) -> dict:
    """`record` in `version` ("0.4" or "0.5") of its extension, given the records held about the item (any version).
    A converter keeps what it received and converts again at each fold, so a digest kept because the need record is
    not held yet is replaced once it is (core §14)."""
    source = version_of(record)
    if source == version:
        return record
    name = str(record["ext"]).partition("/")[0]
    out = dict(record)
    out["ph"], out["ext"] = version, f"{name}/{version}"
    if "evidence" in record:
        ev = {k: v for k, v in (record.get("evidence") or {}).items() if k != "proof"}
        if ev:
            out["evidence"] = ev
        else:
            del out["evidence"]
    if record.get("type") == "settled":
        needs = [n for n in held if n.get("type") in ("raised", "amended") and n.get("terms_rev") == record.get("terms_rev")
                 and n.get("key") == record.get("key") and isinstance(n.get("terms"), dict)]
        if "terms_digest" in record:
            # the held need record whose digest in the source version equals it; none: kept (other terms, or its need
            # record not held yet)
            match = [n for n in needs if terms_digest(n, "0.4" if source == "0.3" else source) == record["terms_digest"]]
            if match:
                out["terms_digest"] = terms_digest(match[0], version)
        elif source == "0.3" and needs:
            # 0.3 to 0.4 (Acts and Decisions 0.5 §6): the digest of the need record at the settlement's terms_rev
            out["terms_digest"] = terms_digest(max(needs, key=_when), version)
    return out
