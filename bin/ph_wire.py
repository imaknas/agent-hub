"""Which Principal Handoff version brunna's records are in, and the conversion between versions
(agent-ops-specs specs/principal-handoff, core §4 and §14).

brunna reads 0.3, 0.4, 0.5 and 0.6 records, and folds them in 0.6 (bin/ph_records.py). What it sends to the principal
side is a setting, {settings} `principal_handoff.send_version` ("0.4", "0.5" or "0.6", default "0.4"): before 1.0 every
version is its own contract, and the version is configured per role, project and extension (core §14). One principal
side serves every project here, so one value covers every project and both extensions. brunna implements 0.5 and 0.6
both, so in core §14's terms it is a converting party configured for 0.6: send_version is the version its counterpart,
the principal side, is configured for, and records go to it in that version.

Core 0.6 §14 forbids pairing a party that speaks 0.6 alone with one that speaks 0.5 alone, so send_version "0.6" is
refused (config-check, and send_version() here) until the principal side is confirmed to read 0.6:
`principal_handoff.principal_side_reads` lists the versions the owner confirmed it reads (not set: 0.4 and 0.5, what
it read before 0.6 existed). The principal side's version is configuration, as core §14 makes it, not something a
requester can see on the wire: the assistant tells each project in the version configured for that project, so its
first 0.6 record can only follow the switch, and the only probe would be sending it a 0.6 record. The switch is one
edit, `"principal_handoff": {"send_version": "0.6", "principal_side_reads": ["0.5", "0.6"]}`, made once the principal
side's release says it reads 0.6. The built-in owner side (bin/owner.py) is brunna itself and reads what brunna reads.
When the principal side nonetheless answers `unsupported` to a record in a version brunna sends, the bridge keeps the
batch for resending (bin/hubbus forward_records) instead of letting it go.

A record received in another version is kept as it was sent and converted to 0.6 each time it is folded
(bin/ph_records.py), as core §14 says: `convert` here, after the spec's reference (reference/ph_core.py, Apache-2.0), one
version at a time. 0.5 <-> 0.6 is a function of the record alone (core 0.6 §14: `ph`, `ext`, a relation's `target.ext`;
a 0.5 relation whose target does not fit 0.6's patterns has no 0.6 form). 0.4 <-> 0.5 replaces a settlement's terms
digest by that of the held need record (core 0.5 §14), and 0.3 to 0.4 gives a settlement the digest of its need record
(Acts and Decisions 0.5 §6). Every step keeps `id` and removes `evidence.proof`, which senders never emit (core §5.2).

The terms digest (core §4) changed in 0.5: it covers the extension's name without its version (`acts`, not
`acts/0.5`); 0.6 keeps that. `terms_digest(need, version)` computes it as that version defines it.

`loads(text)` reads JSON as core §4 has a record read (I-JSON, RFC 7493): an object that names a member twice comes
back as a `Duplicated` (a dict holding the last value), so that one record is invalid and the rest of a page is not;
NaN and Infinity are refused. `ijson_errors(value)` says what in a parsed value is not I-JSON; `parse(text)` is the
strict reading of one record's text.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402

READ = ("0.3", "0.4", "0.5", "0.6")   # in order
SENDS = ("0.4", "0.5", "0.6")         # what brunna can send
LATEST = "0.6"                         # what records are converted to before they are folded
DEFAULT_SEND = "0.4"                   # not set: what the first principal sides read
CONFIRMED = ("0.4", "0.5")             # principal_handoff.principal_side_reads not set: what it read before 0.6
# core §1 (0.6): whitespace is ECMAScript's WhiteSpace and LineTerminator (its \s); a character is a code point, which is
# what Python's re counts on str, as ECMAScript's u flag does
WS = "\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
NAME = re.compile(r"[a-z][a-z0-9-]*")      # core §4: an extension's name
PROJECT = re.compile(f"[^{WS}]{{1,128}}")   # core §1: a project name


def _get(cfg: dict, key: str):
    cur = cfg
    for k in key.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def principal_side_reads(cfg: dict | None = None) -> tuple[str, ...]:
    """The versions the principal side is confirmed to read: for the built-in owner side, which is brunna, every
    version brunna reads; otherwise {settings} principal_handoff.principal_side_reads (not set: 0.4 and 0.5). cfg: a
    parsed {settings} (config-check); not given, the live one."""
    get = (lambda k: _get(cfg, k)) if cfg is not None else hubconf.get
    side = get("owner_side")
    if side not in ("assistant", "builtin"):
        side = "assistant" if get("assistant.api") else "builtin"   # hubconf.owner_side()
    if side == "builtin":
        return READ
    v = get("principal_handoff.principal_side_reads")
    return tuple(v) if isinstance(v, list) else CONFIRMED


def config_errors(cfg: dict) -> list[str]:
    """{settings} principal_handoff, for config-check: send_version must be one the principal side is confirmed to
    read (core §14: never a party that speaks 0.6 alone with one that speaks 0.5 alone)."""
    v = _get(cfg, "principal_handoff.send_version") or DEFAULT_SEND
    reads = principal_side_reads(cfg)
    return [unconfirmed(v, reads)] if v in SENDS and v not in reads else []


def unconfirmed(v: str, reads: tuple[str, ...]) -> str:
    return (f"principal_handoff.send_version is {v}, but the principal side is confirmed to read only "
            f"{', '.join(reads)} (principal_handoff.principal_side_reads): records in {v} would reach a party that "
            f"cannot read them (core §14). Once the principal side reads {v}, add {v} there in the same edit")


def send_version() -> str:
    """{settings} principal_handoff.send_version: the version brunna's records to the principal side are in. Refused
    when it is not one brunna sends, or not one the principal side is confirmed to read."""
    v = hubconf.get("principal_handoff.send_version") or DEFAULT_SEND
    if v not in SENDS:
        raise SystemExit(f"hub.json principal_handoff.send_version is {v!r}; brunna sends one of {', '.join(SENDS)}")
    if v not in (reads := principal_side_reads()):
        raise SystemExit(unconfirmed(v, reads))
    return v


def ext(family_name: str) -> str:
    """The `ext` brunna sends for an extension (`acts`, `decisions`): `acts/0.5`, `acts/0.6`, ..."""
    return f"{family_name}/{send_version()}"


def version_of(record: dict) -> str:
    """The version a record names in its `ext` (`0.4` of `acts/0.4`)."""
    return str(record.get("ext") or "").partition("/")[2]


def family(ext_value: object) -> str:
    """`acts` of `acts/0.4`; '' for anything not a version this reads (a record brunna keeps without acting on it,
    core §4 and §6)."""
    name, _, version = str(ext_value or "").partition("/")
    return name if version in READ else ""


# --- reading a record's text (core §4: I-JSON) ---

class Duplicated(dict):
    """A JSON object that named a member more than once (it holds the last value): not I-JSON (core §4)."""


def _members(pairs: list) -> dict:
    d = dict(pairs)
    return Duplicated(d) if len(d) != len(pairs) else d


def _constant(name: str):
    raise ValueError(f"{name} is not JSON")


def loads(text: str | bytes):
    """JSON as core §4 reads it: an object with a member named twice comes back as a Duplicated, NaN and Infinity are
    an error (ValueError). A number with a fraction or an exponent comes back as a float, an unpaired surrogate as it
    is: ijson_errors names both."""
    return json.loads(text, object_pairs_hook=_members, parse_constant=_constant)


def ijson_errors(v, path: str = "$") -> list[str]:
    """What in a parsed value is not I-JSON as core §4 has a record (RFC 7493: no member name twice, no unpaired
    surrogate, and every number an integer within 2^53 - 1, written without a fraction or an exponent)."""
    out = []
    if isinstance(v, Duplicated):
        out.append(f"{path}: a member name appears twice")
    if isinstance(v, dict):
        for k, x in v.items():
            if isinstance(k, str) and _unpaired(k):
                out.append(f"{path}: an unpaired surrogate in a member name")
            out += ijson_errors(x, f"{path}.{k}")
    elif isinstance(v, list):
        for i, x in enumerate(v):
            out += ijson_errors(x, f"{path}[{i}]")
    elif isinstance(v, str) and _unpaired(v):
        out.append(f"{path}: an unpaired surrogate")
    elif isinstance(v, float) or (isinstance(v, int) and not isinstance(v, bool) and abs(v) > 2 ** 53 - 1):
        out.append(f"{path}: numbers are integers within 2^53 - 1")
    return out


def _unpaired(s: str) -> bool:
    """json.loads joins paired escapes, so a surrogate left in a string is unpaired."""
    return any("\ud800" <= c <= "\udfff" for c in s)


def parse(text: str) -> tuple[dict | None, list[str]]:
    """The strict reading of one record's text (core §4, the spec's core/conformance/raw.json): (the record, []), or
    (None, [why]) for text that is not JSON, not an object, or not I-JSON. Numbers are left to validation
    (ph_records.validate), which rejects a fraction or an exponent with the other rules."""
    try:
        v = loads(text)
    except (ValueError, RecursionError) as e:
        return None, [str(e)]
    if not isinstance(v, dict):
        return None, ["a record is an object"]
    errs = [e for e in ijson_errors(v) if not e.endswith("within 2^53 - 1")]
    return (None, errs) if errs else (v, [])


# --- RFC 8785 and the terms digest (core §4) ---

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
    """The object a terms digest is computed over, as `version` defines it: since 0.5 `ext` is the extension's name
    alone (core §4); in 0.4 it was the full `ext` of that version (`acts/0.4`)."""
    name = str(need["ext"]).partition("/")[0]
    return {"ext": f"{name}/{version}" if version == "0.4" else name, "project": need["project"], "key": need["key"],
            "terms_rev": need["terms_rev"], "terms": need["terms"]}


def terms_digest(need: dict, version: str = LATEST) -> str:
    """The digest of a need record (raised / amended) that a settlement in `version` carries as terms_digest."""
    return "sha-256:" + hashlib.sha256(canonical(digest_input(need, version)).encode("utf-8")).hexdigest()


# --- conversion (core §14) ---

def _when(r: dict) -> tuple:
    return (str(r.get("ts", "")), str(r.get("id", "")))


def convert(record: dict, version: str, held: list[dict] = ()) -> dict | None:
    """`record` in `version` of its extension, one version at a time; None when it has no form there (core 0.6 §14: a
    0.5 relation whose target does not fit 0.6). `held`: the records held about the item, in any version, which only
    the 0.3 and 0.4 steps read (a settlement's terms digest is that of the held need record). A converter keeps what
    it received and converts again at each fold, so a digest kept because the need record is not held yet is replaced
    once it is (core 0.5 §14)."""
    source = version_of(record)
    if source == version:
        return record
    a, b = READ.index(source), READ.index(version)
    out = record
    for i in (range(a, b) if b > a else range(a, b, -1)):
        out = _step(out, READ[i], READ[i + 1] if b > a else READ[i - 1], held)
        if out is None:
            return None
    return out


def _step(record: dict, source: str, version: str, held) -> dict | None:
    """One version up or down."""
    name = str(record["ext"]).partition("/")[0]
    out = dict(record)
    out["ph"], out["ext"] = version, f"{name}/{version}"
    if "evidence" in record:
        ev = {k: v for k, v in (record.get("evidence") or {}).items() if k != "proof"}
        if ev:
            out["evidence"] = ev
        else:
            del out["evidence"]
    if {source, version} == {"0.5", "0.6"}:
        # core 0.6 §14: the record alone; a relation's target.ext is the extension's name in 0.6, name/0.5 in 0.5
        tg = (record.get("relation") or {}).get("target") if record.get("type") == "related" else None
        if isinstance(tg, dict) and isinstance(tg.get("ext"), str):
            t = dict(tg)
            if version == "0.6":
                t["ext"] = tg["ext"].split("/")[0]
                if not NAME.fullmatch(t["ext"]) or not (isinstance(t.get("project"), str)
                                                        and PROJECT.fullmatch(t["project"])):
                    return None
            else:
                t["ext"] = f"{tg['ext']}/0.5"
            out["relation"] = {**record["relation"], "target": t}
        return out
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
