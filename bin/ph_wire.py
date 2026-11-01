"""Which Principal Handoff version agent-hub's records are in (agent-ops-specs specs/principal-handoff).

agent-hub reads 0.3 and 0.4 records, and sends 0.3 until the principal side (the owner's assistant) takes 0.4 ones:
before 1.0 every version is its own contract (core §14), so a 0.4 record the assistant does not implement would be
consumed and not acted on. Switching SEND to "0.4" also means: every `settled` carries `terms_digest` (core §5.3), and
records are I-JSON with `ts` of at most 6 decimal places (core §4).
"""
from __future__ import annotations

READ = ("0.3", "0.4")
SEND = "0.3"
ACTS, DECISIONS = f"acts/{SEND}", f"decisions/{SEND}"


def family(ext: object) -> str:
    """`acts` of `acts/0.4`; '' for anything not a version this reads"""
    name, _, version = str(ext or "").partition("/")
    return name if version in READ else ""
