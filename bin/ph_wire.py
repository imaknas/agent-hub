"""Which Principal Handoff version brunna's records are in, and the conversion between versions
(agent-ops-specs specs/principal-handoff, core §4 and §14).

brunna reads 0.3, 0.4 and 0.5 records. What it sends to the principal side is a setting, {settings}
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
    """RFC 8785 (JCS) serialisation of a parsed JSON value: no whitespace, object members sorted by the UTF-16 code
    units of their names (§3.2.3), strings escaped as ECMAScript's JSON.stringify does (§3.2.2.2: \\b \\f \\n \\r \\t \\"
    \\\\ in short form, other controls as \\u00xx, everything else as is), numbers as ECMAScript's Number.toString
    (§3.2.2.3). ValueError for what has no serialisation: NaN, infinities, unpaired surrogates (§3.2.2.2: not I-JSON),
    member names that are not strings. Records hold integers only (core §4); floats are here because the RFC is."""
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise ValueError("member names are strings")
        items = sorted(value.items(), key=lambda kv: _utf16(kv[0]))
        return "{" + ",".join(f"{canonical(k)}:{canonical(v)}" for k, v in items) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical(v) for v in value) + "]"
    if isinstance(value, str):
        _utf16(value)
        return json.dumps(value, ensure_ascii=False)
    if value is None or isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, int):
        # an I-JSON number is an IEEE 754 double: an integer beyond 2^53 is the double it parses to
        return str(value) if abs(value) <= 2 ** 53 else _number(_double(value))
    if isinstance(value, float):
        return _number(value)
    raise ValueError(f"no JSON serialisation for {type(value).__name__}")


def _utf16(s: str) -> bytes:
    try:
        return s.encode("utf-16-be")
    except UnicodeEncodeError:
        raise ValueError("unpaired surrogate (not I-JSON)") from None


def _double(n: int) -> float:
    try:
        return float(n)
    except OverflowError:
        raise ValueError("number out of the range of an IEEE 754 double") from None


def _number(x: float) -> str:
    """ECMAScript Number::toString(x) (ECMA-262 7.1.12.1) for a finite double: the shortest digits that round-trip
    (Python's repr gives the same), placed by the exponent n where x = 0.d1d2...dk * 10^n."""
    if x != x or x in (float("inf"), float("-inf")):
        raise ValueError("NaN and infinities have no JSON serialisation")
    if x == 0:
        return "0"   # -0 too
    if x < 0:
        return "-" + _number(-x)
    mantissa, _e, exp = repr(x).partition("e")
    whole, _dot, frac = mantissa.partition(".")
    digits = (whole + frac).lstrip("0")
    n = len(digits) + int(exp or 0) - len(frac)
    digits = digits.rstrip("0")
    k = len(digits)
    if k <= n <= 21:
        return digits + "0" * (n - k)
    if 0 < n <= 21:
        return digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return "0." + "0" * -n + digits
    sign = "+" if n - 1 >= 0 else "-"
    return (digits[0] + ("." + digits[1:] if k > 1 else "")) + "e" + sign + str(abs(n - 1))


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
