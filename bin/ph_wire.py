"""Which Principal Handoff version agent-hub's records are in (agent-ops-specs specs/principal-handoff).

agent-hub reads 0.3 and 0.4 records and sends 0.4. Before 1.0 every version is its own contract (core §14); the
owner's assistant took 0.4 on 11-01 and answers each project in the version it last sent (m11011416-bcb1). Sending 0.4
means: every `settled` carries `terms_digest` (core §5.3, terms_digest() here), and records are I-JSON with `ts` of at
most 6 decimal places (core §4; agent-hub writes whole seconds).
"""
from __future__ import annotations

import hashlib
import json

READ = ("0.3", "0.4")
SEND = "0.4"
ACTS, DECISIONS = f"acts/{SEND}", f"decisions/{SEND}"


def family(ext: object) -> str:
    """`acts` of `acts/0.4`; '' for anything not a version this reads"""
    name, _, version = str(ext or "").partition("/")
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


def terms_digest(need: dict) -> str:
    """core §5.3: the digest of a need record (raised / amended) a settlement carries as terms_digest."""
    bound = {k: need[k] for k in ("ext", "project", "key", "terms_rev", "terms")}
    return "sha-256:" + hashlib.sha256(canonical(bound).encode("utf-8")).hexdigest()
