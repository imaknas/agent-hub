"""A model's edit list → the new text (docs/design/llm-providers.md "the model returns an edit list, not a full
rewrite").

Completion-shaped judgment points don't touch files: the model returns `[{"old": a piece of the original, "new": what
it becomes}, …]`, the platform applies them in order, and the platform decides which file is written.
Rules (guaranteeing by construction that "every other line stays verbatim"):
  - no old may be empty, and it must occur exactly once in the text **as it stands at that step** (zero or several
    occurrences = the list is rejected)
  - after applying, the protected markers (e.g. the key marker at the end of an owner to-do line) may not
    lose or gain a single one: markers are managed by the registrar, the model may only delete the whole line (the item
    is done), never change a key; when a whole line is to be deleted, that key is taken out of "should be present" (the
    caller decides)
Standard library only.
"""
from __future__ import annotations

import re

import i18n   # its callers have bin/ on sys.path; the catalog is read only when a problem is reported

_ = i18n.domain(__file__)

class EditError(Exception):
    pass


def apply(text: str, edits: list) -> str:
    """Apply in order; any invalid edit raises EditError (the whole list is rejected, never half applied)."""
    out = text
    for i, e in enumerate(edits or []):
        if not isinstance(e, dict) or not isinstance(e.get("old"), str) or not isinstance(e.get("new"), str):
            raise EditError(_("edit {index} is not {{old, new}}", index=i + 1))
        old, new = e["old"], e["new"]
        if not old:
            raise EditError(_("edit {index} has an empty old", index=i + 1))
        n = out.count(old)
        if n != 1:
            raise EditError(_("edit {index}: old occurs {count} times (it must occur exactly once): {old!r}", index=i + 1,
                              count=n, old=old[:60]))
        out = out.replace(old, new, 1)
    return out


def markers(text: str, pattern: re.Pattern) -> list[str]:
    return [m.group(0) for m in pattern.finditer(text)]


def check_markers(before: str, after: str, pattern: re.Pattern, *, removable: bool = True) -> list[str]:
    """Markers may not be rewritten or appear from nowhere; removable = deleting the whole line (the item is done) is
    allowed. Returns the problems (empty = none)."""
    b, a = markers(before, pattern), markers(after, pattern)
    probs = [_("extra marker: {marker}", marker=m) for m in a if m not in b]
    if not removable:
        probs += [_("missing marker: {marker}", marker=m) for m in b if m not in a]
    else:
        # a removed marker's whole line must be gone too (removing only the marker and keeping the text = a rewrite,
        # not done)
        lines_after = after.splitlines()
        for m in b:
            if m not in a:
                line = next((l for l in before.splitlines() if m in l), "")
                body = line.replace(m, "").strip()
                if body and any(body == l.strip() or body in l for l in lines_after):
                    probs.append(_("marker removed but its line is still there (the key was rewritten): {line}",
                                    line=line.strip()[:80]))
    return probs
