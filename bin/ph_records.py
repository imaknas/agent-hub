"""Principal Handoff records as agent-hub, a requester, reads them (agent-ops-specs core §4-§7, §10, §12 and §14; Acts
and Decisions 0.5 §3): validation, deduplication and each item's settlement view.

Follows the spec's reference (reference/ph_core.py and schema_subset.py, Apache-2.0). Records are validated against
the schemas vendored in contracts/vendor/agent-ops-specs/principal-handoff-0.5/.

    cfg = ph_records.config()                      # the configured principal (core R6), from hub.json
    validate(record, cfg)          -> (schema, [errors])   a 0.5 record; an empty list means valid
    received(records, cfg)         -> (valid, ignored)     records of one item in any version read, converted to 0.5
                                                           (ph_wire.convert), validated and deduplicated (core §10)
    settlement_view(records, cfg)  -> {settlement, outcome, answer, authority, stale, state, withdrawn}
                                                           core §6, what a requester computes; state is open, settled or
                                                           (Decisions) withdrawn

A record in an older version is validated as its 0.5 form. 0.5 only relaxes 0.4 (core §14's change log: module content,
the terms digest), so this accepts what 0.4 accepts and, beyond it, only Evidence and Criteria content that agent-hub
ignores anyway. A 0.3 settlement carries no terms digest and gets one only when its need record is held: without it,
it is valid and is never stale by digest.
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

SPEC = pathlib.Path(__file__).resolve().parents[1] / "contracts" / "vendor" / "agent-ops-specs" / "principal-handoff-0.5"
RECORD_SCHEMA = SPEC / "core" / "schemas" / "record.schema.json"

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


def config() -> Config:
    import hubconf
    return Config(hubconf.party("principal"), hubconf.principal_is_legal_person())


# --- the JSON Schema subset the spec's schemas use (core §4) ---

_TYPES = {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
          "string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool),
          "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
          "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool), "null": lambda v: v is None}
_WS = "\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"   # ECMAScript's \s
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

def validate(r: dict, cfg: Config) -> tuple[str | None, list[str]]:
    """A 0.5 record -> (the schema it validates against, the rules it breaks)."""
    if not isinstance(r, dict):
        return None, ["not an object"]
    name, t = str(r.get("ext") or "").partition("/")[0], r.get("type")
    if name not in MODULES:
        return None, [f"no extension {r.get('ext')!r}"]
    if t in MODULES[name]:   # a module record: the core checks only (core §11)
        errs = schema_errors(r, _schema(RECORD_SCHEMA), RECORD_SCHEMA.parent)
        if r.get("ext") != f"{name}/0.5":
            errs.append(f"ext is not {name}/0.5")
        if r.get("origin") not in MODULES[name][t]:
            errs.append(f"origin {r.get('origin')!r} may not emit {t}")
        return t, errs + _common(r, cfg)
    path = SPEC / name / "schemas" / f"{t}.schema.json"
    if t not in TYPES or not path.exists():
        return None, ["no schema for this type"]
    errs = schema_errors(r, _schema(path), path.parent) + _common(r, cfg)
    terms = r.get("terms") if isinstance(r.get("terms"), dict) else {}
    due = terms.get("due")
    if isinstance(due, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", due):
        try:
            datetime.date.fromisoformat(due)
        except ValueError:
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
        errs += _relation(r)
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


def _relation(r: dict) -> list[str]:
    out, rel = [], r.get("relation") or {}
    want = RELATION_ORIGIN.get(rel.get("kind"))
    if want and r.get("origin") != want:
        out.append(f"{rel.get('kind')} is emitted by {want}")
    tg = rel.get("target") or {}
    item = all(k in tg for k in ("ext", "project", "key"))
    if item == ("uri" in tg) or (not item and tg.keys() & {"ext", "project", "key"}):
        out.append("relation target must be an item {ext, project, key} or a {uri}")
    if r.get("origin") == "principal" and item and tg["project"] != r.get("project"):
        out.append("the principal side names another project's item only by uri")
    if rel.get("kind") == "replaced_by" and not (
            item and tg["ext"] == r.get("ext") and tg["project"] == r.get("project") and tg["key"] != r.get("key")):
        out.append("replaced_by targets another item of the same extension and project")
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


TS = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?"
                r"(?:([Zz])|([+-])([0-9]{2}):([0-9]{2}))")


def instant(ts: str) -> datetime.datetime | None:
    """core §4: an instant, offsets applied, every fractional digit kept; a leap second compares as the last
    microsecond of its minute. None when ts is not an RFC 3339 date-time."""
    m = TS.fullmatch(ts)
    if not m:
        return None
    y, mo, d, h, mi, sec = (int(x) for x in m.groups()[:6])
    frac = int((m.group(7) or "0").ljust(6, "0"))
    if sec == 60:
        sec, frac = 59, 999999
    try:
        off = datetime.timedelta(0) if m.group(8) else (1 if m.group(9) == "+" else -1) * datetime.timedelta(
            hours=int(m.group(10)), minutes=int(m.group(11)))
        return datetime.datetime(y, mo, d, h, mi, sec, frac, tzinfo=datetime.timezone(off))
    except ValueError:
        return None


def when(r: dict) -> tuple:
    """core §6's order: ts as an instant, then id"""
    return (instant(r["ts"]), r["id"])


# --- what a receiver holds (core §10, §14) ---

def received(records: list[dict], cfg: Config) -> tuple[list[dict], list[dict]]:
    """The records one receiver holds about one item, in any version it reads, as (valid, ignored): each converted to
    0.5 against all of them (core §14), validated; then one per (origin, type, id): an invalid record never blocks a
    valid one with the same id, an id reused with different valid content keeps the greater canonical form, and a
    record never displaces one of another type (core §10)."""
    seen: dict[tuple, dict] = {}
    ignored = []
    for r in records:
        if not isinstance(r, dict) or ph_wire.version_of(r) not in ph_wire.READ:
            ignored.append(r)
            continue
        source = ph_wire.version_of(r)
        x = ph_wire.convert(r, ph_wire.LATEST, records)
        _t, errs = validate(x, cfg)
        if source == "0.3" and x.get("type") == "settled" and "terms_digest" not in x:
            errs = [e for e in errs if e != "$: missing terms_digest"]   # 0.3 had none; its need record isn't held
        if errs:
            ignored.append(r)
            continue
        k = (x["origin"], x["type"], x["id"])
        if k not in seen or ph_wire.canonical(x) > ph_wire.canonical(seen[k]):
            seen[k] = x
    return list(seen.values()), ignored


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
