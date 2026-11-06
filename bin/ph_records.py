"""Principal Handoff records as brunna, a requester and converting party, reads them (agent-ops-specs core §4-§7, §10,
§12 and §14; Acts and Decisions §3): validation, what it holds, and each item's settlement view and relations.

Follows the spec's reference (reference/ph_core.py and schema_subset.py, Apache-2.0). Records are validated against
the schemas vendored in contracts/vendor/agent-ops-specs/principal-handoff-0.6.1/, -0.5/ and -0.4/.

    cfg = ph_records.config()                      # the configured principal (core R6), from {settings}
    validate(record, cfg)          -> (schema, [errors])   a 0.6, 0.5 or 0.4 record by its own version's rules; an
                                                           empty list means valid; a record of an extension or version
                                                           brunna does not implement gets NOT_IMPLEMENTED
    implemented(record)            -> bool                 whether it names an extension and version brunna reads; one
                                                           that does not is kept (on the bus) and never acted on
    received(records, cfg)         -> (held, ignored)      core §6's held records of one item, in their 0.6 form
                                                           (ph_wire.convert), deduplicated (core §10); ignored: the
                                                           rest, invalid or not implemented
    settlement_view(records, cfg)  -> {settlement, outcome, answer, authority, stale, state, withdrawn}
                                                           core §6, what a requester computes; state is open, settled or
                                                           (Decisions) withdrawn
    relations(records, cfg)        -> [{kind, target}]     core §7: the item's active relations, sorted
    replacement(records, cfg)      -> target or None       the target of its single active replaced_by
    prevailing(records[, cfg])     -> [index]              core §10 over a store holding every record sent (the bus):
                                                           one record per identity, the greater RFC 8785 form of the
                                                           valid ones, whatever the order
    conflicts(records[, cfg])      -> [{identity, records, kept, ts_only}]   identities sent with different valid
                                                           content, for the report core §10 asks for

What brunna holds (core 0.6 §6, §14). It implements 0.5 and 0.6 and folds in 0.6, so in core §14's terms it is a
converting party configured for 0.6, whatever version it sends the principal side (ph_wire.send_version). A record it
received is held when it is in a version brunna implements, valid in that version's rules as it was sent, and has a
0.6 form; it is held in that form, and core §10's identity and keep-the-greater rule compare the 0.6 forms. A 0.5
record is validated by 0.5's rules and ignored if invalid there, even where 0.6 only relaxes a rule (core §14 item 2);
its converted form is not validated again. The records brunna made itself (the origins cfg.own lists: its projects'
requester and agent records, the registrar's, and the built-in owner side's) are held in their 0.6 form, validated in
0.6: their 0.5 forms exist only on the wire (core §14 item 2). A record of a version or extension brunna does not
implement is not held and never acted on; the bus keeps it (core §6), so it is held once brunna implements that version.

Older versions are outside core 0.6 (§14 covers N−1 and N only); brunna keeps its own reading of them, as before the
upgrade: a 0.4 record is validated by 0.4's rules and schemas, as it was sent (0.5 relaxed 0.4: a 0.4 record invalid
in 0.4 is ignored even when its 0.5 form would be valid), then converted to 0.5 and on to 0.6. A 0.3 record is
validated as its 0.5 form, except that a 0.3 settlement carries no terms digest and gets one only when its need record
is held: without it, it is valid and is never stale by digest.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import re
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ph_wire  # noqa: E402

VENDOR = pathlib.Path(__file__).resolve().parents[1] / "contracts" / "vendor" / "agent-ops-specs"
SPECS = {"0.6": VENDOR / "principal-handoff-0.6.1",
         "0.5": VENDOR / "principal-handoff-0.5",    # for records in 0.5 (core §14: N−1 by N−1's rules)
         "0.4": VENDOR / "principal-handoff-0.4"}    # for records in 0.4, brunna's own reading (see the header)
SPEC, SPEC_04 = SPECS["0.6"], SPECS["0.4"]
RECORD_SCHEMA = SPEC / "core" / "schemas" / "record.schema.json"
NOT_IMPLEMENTED = "names an extension or version brunna does not implement: kept, not acted on (core §4, §6)"

NEED = ("raised", "amended", "withdrawn")
SETTLEMENT = ("settled", "reopened")
TYPES = NEED + SETTLEMENT + ("claimed", "related", "note")   # core §4
OWNER = {"raised": {"requester"}, "amended": {"requester"}, "withdrawn": {"requester"},
         "settled": {"principal"}, "reopened": {"principal"}}   # R1, R2
RELATION_ORIGIN = {"same_as": "principal", "derived_from": "requester", "replaced_by": "requester"}   # core §7
VIEW = ("settlement", "outcome", "answer", "authority", "stale", "state", "withdrawn")   # the settlement view (core §6)
# what a withdrawn need leaves an item that is not settled: each extension's policy (core §6; acts §2: an act stays open
# for the principal side to settle, decisions §2: a withdrawn decision is no longer presented)
WITHDRAWN_STATE = {"acts": "open", "decisions": "withdrawn"}
# module record types each extension's table lists (core §11), with the origins that may emit them; agent-hub
# implements no module, so it checks these records against the core alone and acts on none of them
MODULES = {"acts": {}, "decisions": {"approved": {"principal"}}}


@dataclass(frozen=True)
class Config:
    principal: str           # the principal identifier (core R6)
    legal: bool = False      # the principal is a legal person (core §5.1: representative)
    own: frozenset = frozenset()   # the origins of the records brunna makes itself (core §14 item 2: held in 0.6 form)


def config() -> Config:
    import hubconf
    own = {"requester", "agent", "registrar"} | ({"principal"} if hubconf.owner_side() == "builtin" else set())
    return Config(hubconf.party("principal"), hubconf.principal_is_legal_person(), frozenset(own))


# --- the JSON Schema subset the spec's schemas use (core §4) ---

_TYPES = {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
          "string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool),
          "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
          "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool), "null": lambda v: v is None}
_WS = ph_wire.WS   # ECMAScript's \s (core §1); Python's re on str counts code points, as the u flag does (core §4)
_SCHEMAS: dict[pathlib.Path, dict] = {}


def _schema(path: pathlib.Path) -> dict:
    if path not in _SCHEMAS:
        _SCHEMAS[path] = json.loads(path.read_text())
    return _SCHEMAS[path]


def _ecma(pattern: str) -> str:
    """ECMAScript's $ matches only at the end of the string, and its \\s is not Python's."""
    pattern = pattern.replace("\\S", f"[^{_WS}]").replace("\\s", f"[{_WS}]")
    return re.sub(r"(?<!\\)\$", r"\\Z", pattern)


def _same(a, b) -> bool:
    return type(a) is type(b) and a == b


def schema_errors(instance, schema: dict, base: pathlib.Path, path: str = "$") -> list[str]:
    out = []
    if "$ref" in schema:
        target = (base / schema["$ref"]).resolve()
        out += schema_errors(instance, _schema(target), target.parent, path)
    if "type" in schema and not _TYPES[schema["type"]](instance):
        return out + [f"{path}: not {schema['type']}"]
    if "const" in schema and not _same(instance, schema["const"]):
        out.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and not any(_same(instance, v) for v in schema["enum"]):
        out.append(f"{path}: not one of {schema['enum']}")
    if "pattern" in schema and isinstance(instance, str) and not re.search(_ecma(schema["pattern"]), instance):
        out.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(instance, dict):
        out += [f"{path}: missing {k}" for k in schema.get("required", []) if k not in instance]
        for k, sub in schema.get("properties", {}).items():
            if k in instance:
                out += schema_errors(instance[k], sub, base, f"{path}.{k}")
    if isinstance(instance, list) and "items" in schema:
        for i, v in enumerate(instance):
            out += schema_errors(v, schema["items"], base, f"{path}[{i}]")
    return out


# --- validity (core §4, §5, §7; the extensions' §3) ---

def implemented(r) -> bool:
    """core §4: whether a record names an extension and a version brunna implements (acts or decisions, 0.3 to 0.6).
    One that does not is never acted on; the bus keeps it, so that it is held once brunna implements it (core §6)."""
    return isinstance(r, dict) and ph_wire.family(r.get("ext")) in MODULES


def validate(r: dict, cfg: Config) -> tuple[str | None, list[str]]:
    """A record -> (the schema it validates against, the rules it breaks), by the rules of the version it names: 0.6's
    for a 0.6 record, 0.5's for a 0.5 one (core §14), 0.4's for a 0.4 one (brunna's own reading); a 0.3 record is
    validated as its 0.5 form (held_form). A record of an extension or version brunna does not implement:
    (None, [NOT_IMPLEMENTED])."""
    if not isinstance(r, dict):
        return None, ["not an object"]
    if not implemented(r):
        return None, [NOT_IMPLEMENTED]
    version = ph_wire.version_of(r)
    if version == "0.4":
        return _validate_04(r, cfg)
    return _validate(r, cfg, "0.6" if version == "0.6" else "0.5")


def _validate(r: dict, cfg: Config, version: str) -> tuple[str | None, list[str]]:
    """0.5's and 0.6's validity: the same rules, against each version's schemas, but for a replaced_by's target (core
    0.6 §7: it names the extension by name alone)."""
    spec = SPECS[version]
    name, t = str(r.get("ext") or "").partition("/")[0], r.get("type")
    if isinstance(t, str) and t in MODULES[name]:   # a module record: the core checks only (core §11)
        record_schema = spec / "core" / "schemas" / "record.schema.json"
        errs = schema_errors(r, _schema(record_schema), record_schema.parent)
        if errs:
            return t, errs
        if r.get("ext") != f"{name}/{version}":
            errs.append(f"ext is not {name}/{version}")
        if r.get("origin") not in MODULES[name][t]:
            errs.append(f"origin {r.get('origin')!r} may not emit {t}")
        # 0.6's records.json names no schema for a module pair, which has none of its own (core §4); 0.5's named its type
        return (None if version == "0.6" else t), errs + _common(r, cfg)
    path = spec / name / "schemas" / f"{t}.schema.json"
    if t not in TYPES or not path.exists():
        return None, ["no schema for this type"]
    errs = schema_errors(r, _schema(path), path.parent)
    if errs:   # the rules below read what the schema has established
        return t, errs
    errs = _common(r, cfg)
    terms = r.get("terms") if isinstance(r.get("terms"), dict) else {}
    due = terms.get("due")
    if isinstance(due, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", due) and _day(*map(int, due.split("-"))) is None:
        errs.append("terms.due is not a calendar date")
    if t in OWNER and r.get("origin") not in OWNER[t]:
        errs.append(f"origin {r.get('origin')!r} may not emit {t}")
    rev = r.get("terms_rev")
    if t == "amended" and isinstance(rev, int) and rev < 2:
        errs.append("amended terms_rev must be at least 2")
    if t not in ("raised", "amended") and isinstance(rev, int) and rev < 1:
        errs.append(f"{t} terms_rev must be at least 1")
    if t == "claimed" and r.get("origin") == "principal":   # R3
        errs.append("the principal side settles; it does not claim")
    if t == "related":
        errs += _relation(r, version)
    if t == "settled":
        errs += _authority(r, cfg)
    if "adopts" in r and (t != "settled" or (r.get("authority") or {}).get("kind") not in ("confirmed", "attested")):
        errs.append("adopts only on confirmed or attested settlements")
    if t == "note" and len((r.get("about") or {}).keys() & {"key", "quote"}) != 1:
        errs.append("about has exactly one of key and quote")
    if t == "note" and "key" in (r.get("about") or {}) and r["about"]["key"] != r.get("key"):
        errs.append("a note about a key carries that key")
    if name == "acts" and isinstance(terms.get("text"), str) and ("\n" in terms["text"] or "\r" in terms["text"]):
        errs.append("terms.text is a single line")   # Acts §3
    if name == "decisions":   # Decisions §3
        if t == "settled" and r.get("outcome") == "decided" and "answer" not in r:
            errs.append("decided needs answer")
        if t in ("raised", "amended"):
            ids = [o.get("id") for o in terms.get("options") or [] if isinstance(o, dict)]
            if len(ids) != len(set(ids)):
                errs.append("option ids are unique within the terms")
    return t, errs


def _common(r: dict, cfg: Config) -> list[str]:
    errs = []
    if not _bounded(r):
        errs.append("numbers are integers within 2^53 - 1")
    if isinstance(r.get("ts"), str) and instant(r["ts"]) is None:
        errs.append("ts is not an RFC 3339 date-time")
    if r.get("by") == cfg.principal:   # R6: the principal never emits records itself
        errs.append("the principal identifier cannot emit records")
    return errs


def _bounded(v) -> bool:
    if isinstance(v, (bool, str)) or v is None:
        return True
    if isinstance(v, int):
        return abs(v) <= 2 ** 53 - 1
    if isinstance(v, dict):
        return all(_bounded(x) for x in v.values())
    if isinstance(v, list):
        return all(_bounded(x) for x in v)
    return False


def _relation(r: dict, version: str) -> list[str]:
    """core §7; in 0.4 a uri target carried nothing else; in 0.6 a replaced_by names its extension by name alone"""
    out, rel = [], r.get("relation") or {}
    want = RELATION_ORIGIN.get(rel.get("kind"))
    if want and r.get("origin") != want:
        out.append(f"{rel.get('kind')} is emitted by {want}")
    tg = rel.get("target") or {}
    item = all(k in tg for k in ("ext", "project", "key"))
    other = tg.keys() - {"uri"} if version == "0.4" else tg.keys() & {"ext", "project", "key"}
    if item == ("uri" in tg) or (not item and other):
        out.append("relation target must be an item {ext, project, key} or a {uri}")
    if r.get("origin") == "principal" and item and tg["project"] != r.get("project"):
        out.append("the principal side names another project's item only by uri")
    same = str(r.get("ext") or "").partition("/")[0] if version == "0.6" else r.get("ext")
    if rel.get("kind") == "replaced_by" and not (
            item and tg["ext"] == same and tg["project"] == r.get("project") and tg["key"] != r.get("key")):
        out.append("replaced_by targets another item of the same extension and project, in the item form")
    return out


def _authority(r: dict, cfg: Config) -> list[str]:
    """core §5.1"""
    a, ev = r.get("authority") or {}, r.get("evidence") or {}
    kind, decider = a.get("kind"), a.get("decider")
    out = []
    if kind in ("principal", "confirmed"):
        if decider != cfg.principal:
            out.append(f"{kind} needs the principal as decider")
        if cfg.legal and "representative" not in a:
            out.append("a legal-person principal needs a representative")
    if kind == "confirmed" and "proposal" not in ev:
        out.append("confirmed needs evidence.proposal")
    if kind in ("delegated", "attested") and decider == cfg.principal:
        out.append(f"{kind} decider cannot be the principal")
    if kind == "delegated":
        chain = a.get("chain")
        if "under" not in a or not chain:
            out.append("delegated needs under and chain")
        elif chain[0] != cfg.principal or chain[-1] != decider:
            out.append("chain runs from the principal to the decider")
    if kind == "attested" and "basis" not in ev:
        out.append("attested needs evidence.basis")
    return out


# --- 0.4's validity (core 0.4 §4, §5, §7; Acts and Decisions 0.4 §3) ---
# Core §14: a party that reads N−1 validates a record in N−1 under N−1 and ignores it if it is invalid there, so that
# every receiver computes the same state from the same records, malformed ones included. 0.5 relaxed 0.4 (its change
# log): 0.4 still holds the Evidence rules (§5.2, V1), approvals (§5.3) and Decisions' criteria. After the spec's 0.4
# reference (reference/ph_core.py, ph_acts.py, ph_decisions.py at tag principal-handoff-0.4, Apache-2.0), against the
# 0.4 schemas vendored in SPEC_04.

APPROVAL = "approval:"   # core 0.4 §5.3: authority.under naming an approval record of the same item
OWNER_04 = {**OWNER, "approved": {"principal"}}


def _validate_04(r: dict, cfg: Config) -> tuple[str | None, list[str]]:
    name, t = str(r.get("ext") or "").partition("/")[0], r.get("type")
    if name not in WITHDRAWN_STATE:
        return None, [f"no extension {r.get('ext')!r}"]
    schemas = SPEC_04 / name / "schemas"
    path = schemas / f"{t}.schema.json"
    if t not in TYPES + ("approved",) or not path.exists():
        return None, ["no schema for this type"]
    errs = schema_errors(r, _schema(path), path.parent)
    if not _bounded(r):
        errs.append("numbers are integers within 2^53 - 1")
    ev = r.get("evidence") if isinstance(r.get("evidence"), dict) else {}
    times = [r.get("ts")] + [v.get("at") for v in ev.get("verified") or [] if isinstance(v, dict)]
    if any(isinstance(x, str) and instant(x) is None for x in times):
        errs.append("a time that is not an RFC 3339 date-time")
    terms = r.get("terms") if isinstance(r.get("terms"), dict) else {}
    due = terms.get("due")
    if isinstance(due, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", due) and _day(*map(int, due.split("-"))) is None:
        errs.append("terms.due is not a calendar date")
    if t in OWNER_04 and r.get("origin") not in OWNER_04[t]:
        errs.append(f"origin {r.get('origin')!r} may not emit {t}")
    if r.get("by") == cfg.principal:
        errs.append("the principal identifier cannot emit records")
    rev = r.get("terms_rev")
    if t == "amended" and isinstance(rev, int) and rev < 2:
        errs.append("amended terms_rev must be at least 2")
    if t not in ("raised", "amended") and isinstance(rev, int) and rev < 1:
        errs.append(f"{t} terms_rev must be at least 1")
    if t == "claimed" and r.get("origin") == "principal":
        errs.append("the principal side settles; it does not claim")
    if t == "related":
        errs += _relation(r, "0.4")
    if t == "settled":
        errs += _authority(r, cfg)
        a = r.get("authority") if isinstance(r.get("authority"), dict) else {}
        under = a.get("under") if isinstance(a.get("under"), str) else ""   # the schema names any other
        if under.startswith(APPROVAL) and a.get("kind") != "delegated":
            errs.append("a settlement under an approval is delegated")
        if under.startswith(APPROVAL) and not (schemas / "approved.schema.json").exists():
            errs.append("this extension has no approvals")
        if under == APPROVAL:
            errs.append("approval: names no record")
    if t == "approved":
        errs += _approval_04(r, cfg)
    if r.get("adopts") and (t != "settled" or (r.get("authority") or {}).get("kind") not in ("confirmed", "attested")):
        errs.append("adopts only on confirmed or attested settlements")
    errs += _evidence_04(r)
    if name == "acts" and isinstance(terms.get("text"), str) and ("\n" in terms["text"] or "\r" in terms["text"]):
        errs.append("terms.text is a single line")   # Acts 0.4 §3
    if name == "decisions":
        errs += _decisions_04(r)
    if t == "note" and len((r.get("about") or {}).keys() & {"key", "quote"}) != 1:
        errs.append("about has exactly one of key and quote")
    if t == "note" and "key" in (r.get("about") or {}) and r["about"]["key"] != r.get("key"):
        errs.append("a note about a key carries that key")
    return t, errs


def _approval_04(r: dict, cfg: Config) -> list[str]:
    """core 0.4 §5.3: an approval in force comes from a person; a retraction only narrows."""
    ap = r.get("approval") or {}
    if not ap.get("active", True):
        out = [] if r.get("supersedes") else ["a retraction lists the approval it retracts in supersedes"]
        return out + (["terms_digest only on a grant"] if "terms_digest" in ap else [])
    out = ["a grant needs terms_rev"] if "terms_rev" not in r else []
    out += ["a grant needs approval.terms_digest"] if "terms_digest" not in ap else []
    a = r.get("authority")
    if not isinstance(a, dict):
        return out + ["an approval needs authority"]
    out += _authority(r, cfg)
    if a.get("kind") not in ("principal", "confirmed"):
        out.append("an approval comes from the principal: kind principal or confirmed")
    if "presented" not in (r.get("evidence") or {}):
        out.append("an approval needs evidence.presented")
    return out


def _evidence_04(r: dict) -> list[str]:
    """core 0.4 §5.2: verifiable entries and verification results."""
    ev, t, out = r.get("evidence") or {}, r.get("type"), []
    if not isinstance(ev, dict):
        return out
    entries = ev.get("verifiable")
    if isinstance(entries, list):
        if t not in ("claimed", "note", "settled"):
            out.append("verifiable only on claims, notes and settlements")
        if "terms_rev" not in r:
            out.append("a record with verifiable entries carries terms_rev")
        ids = [x.get("id") for x in entries if isinstance(x, dict)]
        if len(ids) != len(set(ids)):
            out.append("verifiable entry ids are unique within the record")
        if any(isinstance(x, dict) and ("proof" in x) == ("proof_ref" in x) for x in entries):
            out.append("a verifiable entry carries exactly one of proof and proof_ref")
    if "verified" in ev and not (t == "settled" or (t == "note" and r.get("origin") == "principal")):
        out.append("verified only on settlements and principal-side notes")
    return out


def _decisions_04(r: dict) -> list[str]:
    """Decisions 0.4 §2.1 and §3: the rules its schemas cannot state."""
    t, out = r.get("type"), []
    if t == "settled" and r.get("outcome") == "decided" and "answer" not in r:
        out.append("decided needs answer")
    if t in ("raised", "amended") and isinstance(r.get("terms"), dict):
        terms = r["terms"]
        ids = [o.get("id") for o in terms.get("options") or [] if isinstance(o, dict)]
        if len(ids) != len(set(ids)):
            out.append("option ids are unique within the terms")
        c = terms.get("criteria")
        if isinstance(c, dict):
            checks = [x for x in c.get("checks") or [] if isinstance(x, dict)]
            cids = [x.get("id") for x in checks]
            if not cids:
                out.append("criteria need at least one check")
            if len(cids) != len(set(cids)):
                out.append("check ids are unique within the criteria")
            if any(not x.get("trust") for x in checks):
                out.append("every check names at least one trust root")
            option = (c.get("answer") or {}).get("option")
            if option is not None and option not in {o.get("id") for o in terms.get("options") or []}:
                out.append("criteria.answer.option must be one of the options")
    if t == "settled" and str((r.get("authority") or {}).get("under", "")).startswith(APPROVAL):
        verified = (r.get("evidence") or {}).get("verified") or []
        if r.get("outcome") != "decided":
            out.append("a settlement under an approval records the approved answer")
        if not verified or any(v.get("result") != "valid" for v in verified if isinstance(v, dict)):
            out.append("a settlement under an approval carries only valid verification results")
    return out


TS = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?"
                r"(?:([Zz])|([+-])([0-9]{2}):([0-9]{2}))")


def _day(y: int, mo: int, d: int) -> int | None:
    """The day number of a date of the proleptic Gregorian calendar, or None. RFC 3339's years run from 0000; Python's
    date has no year 0, and the calendar repeats every 400 years (146097 days)."""
    try:
        return datetime.date(y, mo, d).toordinal() if y else datetime.date(400, mo, d).toordinal() - 146097
    except ValueError:
        return None


def instant(ts: str) -> int | None:
    """core §4: the instant in microseconds, offsets applied, every fractional digit kept; a leap second (:60)
    compares as the last microsecond of its minute. None when ts is not an RFC 3339 date-time (year 0000 is one)."""
    m = TS.fullmatch(ts)
    if not m:
        return None
    y, mo, d, h, mi, sec = (int(x) for x in m.groups()[:6])
    frac = int((m.group(7) or "0").ljust(6, "0"))
    oh, om = int(m.group(10) or 0), int(m.group(11) or 0)
    day = _day(y, mo, d)
    if day is None or h > 23 or mi > 59 or sec > 60 or oh > 23 or om > 59:
        return None
    if sec == 60:
        sec, frac = 59, 999999
    off = (oh * 3600 + om * 60) * (-1 if m.group(9) == "-" else 1)
    return (day * 86400 + h * 3600 + mi * 60 + sec - off) * 1000000 + frac


def when(r: dict) -> tuple:
    """core §6's order: ts as an instant, then id"""
    return (instant(r["ts"]), r["id"])


# --- what a receiver holds (core §10, §14) ---

def held_form(r, records: list[dict], cfg: Config) -> tuple[dict, str] | None:
    """A record as brunna holds it (core 0.6 §6, §14): its 0.6 form, converted against `records`, with that form's RFC
    8785 serialisation; None when it is not held: not in an extension and version brunna implements (kept on the bus,
    never acted on), invalid in the rules it is validated by, or without a 0.6 form. A record received in 0.5 or 0.4 is
    validated as it was sent, by its own version's rules, and its converted form is not validated again; a 0.6 record
    by 0.6's; a record brunna made itself (cfg.own) as its 0.6 form, by 0.6's (core §14 item 2: its 0.5 form exists
    only on the wire); a 0.3 record as its 0.5 form (see the header). A record that has no serialisation is not I-JSON
    (core §4), so it is invalid too."""
    if not implemented(r):
        return None
    source = ph_wire.version_of(r)
    if source == "0.3":
        x = ph_wire.convert(r, "0.5", records)
        _t, errs = validate(x, cfg)
        if x.get("type") == "settled" and "terms_digest" not in x:
            errs = [e for e in errs if e != "$: missing terms_digest"]   # 0.3 had none; its need record isn't held
        x = None if errs else ph_wire.convert(x, ph_wire.LATEST)
    elif source == "0.6" or (source == "0.5" and r.get("origin") in cfg.own):
        x = ph_wire.convert(r, ph_wire.LATEST)
        x = x if x is not None and not validate(x, cfg)[1] else None
    else:
        x = None if validate(r, cfg)[1] else ph_wire.convert(r, ph_wire.LATEST, records)
    if x is None:
        return None
    try:
        return x, ph_wire.canonical(x)
    except ValueError:
        return None


def received(records: list[dict], cfg: Config) -> tuple[list[dict], list[dict]]:
    """The records brunna holds about one item (core 0.6 §6), in any version it reads, as (held, ignored): each held in
    its 0.6 form (held_form); then one per (origin, type, id), compared in 0.6 form: an invalid record never blocks a
    valid one with the same id, an id reused with different valid content keeps the greater canonical form, and a
    record never displaces one of another type (core §10). ignored: the records not held, invalid or of a version or
    extension brunna does not implement (implemented() tells them apart)."""
    seen: dict[tuple, tuple[dict, str]] = {}
    ignored = []
    for r in records:
        held = held_form(r, records, cfg)
        if held is None:
            ignored.append(r)
            continue
        x, canon = held
        k = (x["origin"], x["type"], x["id"])
        if k not in seen or canon > seen[k][1]:
            seen[k] = held
    return [x for x, _c in seen.values()], ignored


def identity(r: dict) -> tuple:
    """core §10: what one record id names, (ext name, project, origin, type, id): ids are unique per ext name, project
    and origin, and a record never displaces one of another type."""
    return (str(r.get("ext") or "").partition("/")[0], *(_hashable(r.get(k)) for k in ("project", "origin", "type", "id")))


def _hashable(v):
    """a member as part of a key: a malformed record (a list for its type) must not stop a reader"""
    return v if isinstance(v, (str, int, float, type(None))) else json.dumps(v, sort_keys=True, default=repr)


def prevailing(records: list[dict], cfg: Config | None = None) -> list[int]:
    """core §10 for a store that keeps every record it was sent (the bus): the indexes, in order, of the records a
    reader takes, one per identity when the identity holds a valid record. Of an identity's valid records (each valid
    by its own version's rules, held_form) the one whose RFC 8785 serialisation (of its 0.5 form) is greater, whatever
    the arrival order; records whose 0.5 forms are equal
    are the same record (sent again, in another version): the first is taken. An invalid record never displaces or
    blocks a valid one; an identity with no valid record keeps all of them, for the fold to ignore and name. A lone
    record is taken as it is, unvalidated, so cfg (default: config()) is read only when an identity has two."""
    groups: dict[tuple, list[int]] = {}
    for i, r in enumerate(records):
        groups.setdefault(identity(r) if isinstance(r, dict) else ("", i), []).append(i)
    keep = []
    for idx in groups.values():
        if len(idx) == 1:
            keep += idx
            continue
        cfg = cfg or config()
        valid = [(held[1], i) for i in idx if (held := held_form(records[i], records, cfg)) is not None]
        keep += [max(valid, key=lambda v: v[0])[1]] if valid else idx   # max: the first of equals
    return sorted(keep)


def conflicts(records: list[dict], cfg: Config | None = None) -> list[dict]:
    """core §10's report: identities sent with different valid content, {identity, records: the indexes of its valid
    records, kept: the index prevailing() takes, ts_only: they differ only in ts (a record re-created after a crash,
    which the spec does not ask to report)}."""
    groups: dict[tuple, list[int]] = {}
    for i, r in enumerate(records):
        if isinstance(r, dict):
            groups.setdefault(identity(r), []).append(i)
    out = []
    for key, idx in groups.items():
        if len(idx) < 2:
            continue
        cfg = cfg or config()
        valid = [(held[1], held[0], i) for i in idx if (held := held_form(records[i], records, cfg)) is not None]
        if len({c for c, _x, _i in valid}) < 2:
            continue
        no_ts = {ph_wire.canonical({k: v for k, v in x.items() if k != "ts"}) for _c, x, _i in valid}
        out.append({"identity": key, "records": [i for _c, _x, i in valid], "kept": max(valid, key=lambda v: v[0])[2],
                    "ts_only": len(no_ts) == 1})
    return out


def register(records: list[dict]) -> tuple[dict | None, bool]:
    """core §6: one register's current record (the latest head, ties by the greater id) and whether it has a
    conflict."""
    if not records:
        return None, False
    by_id = {r["id"]: r for r in records}
    replaced = {s for r in records for s in r.get("supersedes", []) if s in by_id and s != r["id"]}
    heads = [r for r in records if r["id"] not in replaced] or records
    return max(heads, key=when), len(heads) > 1 or _cycle(by_id)


def _cycle(by_id: dict) -> bool:
    for start in by_id.values():
        seen, stack = set(), [start]
        while stack:
            r = stack.pop()
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            for s in r.get("supersedes", []):
                if s == start["id"]:
                    return True
                if s in by_id:
                    stack.append(by_id[s])
    return False


def latest_terms(valid: list[dict]) -> dict | None:
    """The need record with the highest terms_rev (core §6: settlements against lower ones are stale)."""
    with_terms = [r for r in valid if r["type"] in ("raised", "amended")]
    return max(with_terms, key=lambda r: (r["terms_rev"], *when(r))) if with_terms else None


def settlement_view(records: list[dict], cfg: Config) -> dict:
    """core §6 and §12: what a requester computes for one item, from the need records and the principal side's
    records (records in any version read). state: settled while the current settlement is a settled record (a stale one
    too: stale says it was made on earlier terms), otherwise withdrawn when the current need record is a withdrawal and
    the extension stops presenting such an item (Decisions), otherwise open (a claim changes nothing, core R3)."""
    valid, _ignored = received([r for r in records if isinstance(r, dict) and r.get("type") in NEED + SETTLEMENT], cfg)
    need, _c = register([r for r in valid if r["type"] in NEED])
    settle, _c = register([r for r in valid if r["type"] in SETTLEMENT])
    terms_rev = max((r["terms_rev"] for r in valid if r["type"] in ("raised", "amended")), default=0)
    settled = settle if settle and settle["type"] == "settled" else None
    stale = bool(settled and (settled["terms_rev"] < terms_rev or (
        need is not None and need["type"] != "withdrawn" and need["terms_rev"] == settled["terms_rev"]
        and "terms_digest" in settled and ph_wire.terms_digest(need) != settled["terms_digest"])))
    withdrawn = bool(need and need["type"] == "withdrawn")
    family = next((ph_wire.family(r["ext"]) for r in valid), "")
    return {"settlement": settle["id"] if settle else None,
            "outcome": settled["outcome"] if settled else None,
            "answer": settled.get("answer") if settled else None,
            "authority": settled["authority"]["kind"] if settled else None,
            "stale": stale,
            "state": "settled" if settled else WITHDRAWN_STATE.get(family, "open") if withdrawn else "open",
            "withdrawn": withdrawn}


def relations(records: list[dict], cfg: Config) -> list[dict]:
    """core §7: the item's active relations, as {kind, target} sorted by kind and target. A relation is identified by
    (kind, target.ext, target.project, target.key) or by (kind, target.uri), the uri compared as an exact string; the
    latest held `related` (ts as an instant, then the greater id) decides whether it is active. Its target is given with
    ext, project, key and uri only; a same_as or derived_from naming the record's own item has no effect (a
    replaced_by naming it is invalid)."""
    latest: dict[tuple, dict] = {}
    for r in received(records, cfg)[0]:
        if r["type"] != "related":
            continue
        rel, tg = r["relation"], r["relation"]["target"]
        k = (rel["kind"], "uri", tg["uri"]) if "uri" in tg else (rel["kind"], tg["ext"], tg["project"], tg["key"])
        if k not in latest or when(r) > when(latest[k]):
            latest[k] = r
    out = []
    for k in sorted(latest):
        r = latest[k]
        tg = {m: v for m, v in r["relation"]["target"].items() if m in ("uri", "ext", "project", "key")}
        if r["relation"]["active"] and tg != {"ext": r["ext"].partition("/")[0], "project": r["project"],
                                              "key": r["key"]}:
            out.append({"kind": k[0], "target": tg})
    return out


def replacement(records: list[dict], cfg: Config) -> dict | None:
    """core §7: the target of the item's single active replaced_by; an item with more than one has none."""
    active = [r["target"] for r in relations(records, cfg) if r["kind"] == "replaced_by"]
    return active[0] if len(active) == 1 else None
