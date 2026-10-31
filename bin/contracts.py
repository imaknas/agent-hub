"""Shared contract tools (standard library): read the JSON Schema and markdown contracts under contracts/ and check
actual data.

- JSON contracts: `<name>.v<N>.schema.json`, validated with minischema
- markdown contracts: `<name>.v<N>.md`; the ```regex <name>``` / ```heading <name>``` blocks are machine-read rules
tests/test_contracts.py (each repo's own ② check) and bin/contracts-check.py (the nightly ① check) both use this.
"""
from __future__ import annotations

import json
import pathlib
import re

import i18n
import minischema
import todo_tokens

_ = i18n.domain(__file__)

FENCE = re.compile(r"^```(regex|heading) (\S+)\n(.*?)\n```", re.M | re.S)


def md_rules(path: pathlib.Path, owner: str | None = None) -> dict[str, list[str]]:
    """The regex/heading blocks in a markdown contract: {name: [one rule per line]}. If owner is given, {owner} is
    replaced (with the re.escape'd form in regex blocks)."""
    out = {}
    for kind, name, body in FENCE.findall(path.read_text()):
        if owner is not None:
            body = body.replace("{owner}", re.escape(owner) if kind == "regex" else owner)
        out[f"{kind}:{name}"] = body.splitlines()
    return out


def md_version(text: str) -> tuple[str, int] | None:
    m = re.match(r"<!-- contract: (\S+) v(\d+) -->", text)
    return (m.group(1), int(m.group(2))) if m else None


def check_todo_md(text: str, contract: pathlib.Path, owner: str) -> list[str]:
    """The contract's `heading owner` block is the v1 (zh-Hant) heading set; a v2 contract adds `heading owner-en`.
    A file's version marker names the lowest version it needs: a v1 file has the v1 headings, a v2 file either set (one
    set per file, in order)."""
    rules = md_rules(contract, owner)
    errs = []
    name, top = md_version(contract.read_text()) or ("todo-md", 1)
    v = md_version(text) or (name, 1)   # an old file without a marker reads as v1
    if v[0] != name or not 1 <= v[1] <= top:
        errs.append(_("the version marker is {v}, not {contract} v1 to v{top}", v=v, contract=name, top=top))
    sets = [rules[k] for k in sorted(rules) if k == "heading:owner" or k.startswith("heading:owner-")]
    allowed = sets[:1] if v[1] == 1 else sets
    headings = {h for s in sets for h in s}
    found = [l for l in text.splitlines() if l.startswith("## ")]
    owners = [h for h in found if h in headings]
    if owners not in allowed:
        errs.append(_("the owner headings must be, in order, {headings}; found {owners}",
                      headings=" / ".join(map(str, allowed)) if len(allowed) > 1 else allowed[0], owners=owners))
    item, project = re.compile(rules["regex:item"][0]), re.compile(rules["regex:project"][0])
    section = None
    for i, l in enumerate(text.splitlines(), 1):
        if l.startswith("## "):
            section = l
        elif section in headings and l.startswith("- ") and not item.match(l):
            errs.append(_("line {i} is in an owner section but is not `- [ ] …`: {line}", i=i, line=l[:60]))
        elif section in headings and l.startswith("###") and not project.match(l):
            errs.append(_("line {i}: the project subheading is malformed: {line}", i=i, line=l[:60]))
    return errs


def check_state_todo(text: str, contract: pathlib.Path, owner: str) -> list[str]:
    """Each regex block holds one rule per token set (a line matches if any does). A v2 contract names the section
    headings and the empty markers in `heading section` / `heading none`; v1 states its own (the zh-Hant set) in
    prose. Every to-do section in the file is checked."""
    rules = md_rules(contract, owner)
    item, group, bare = ([re.compile(r) for r in rules[f"regex:{k}"]] for k in ("item", "group", "bare"))
    v1 = todo_tokens.tokens("zh-Hant")
    heads = rules.get("heading:section") or [v1["todo_heading"]]
    nones = rules.get("heading:none") or [v1["todo_none"]]
    lines = text.splitlines()
    starts = [i + 1 for i, l in enumerate(lines) if l.rstrip() in heads]
    if not starts:
        return [_("no `{heading}` section", heading="`, `".join(heads))]
    errs = []
    for start in starts:
        in_group = False
        for l in lines[start:]:
            if l.startswith("## "):
                break
            if not l.strip() or l.strip() in nones:
                continue
            if any(g.match(l) for g in group):
                in_group = True
                continue
            if any(r.match(l) for r in item) or (in_group and any(b.match(l) for b in bare)):
                continue
            errs.append(_("not in the to-do format: {line}", line=l[:60]))
    return errs


def check_json(schema_path: pathlib.Path, data) -> list[str]:
    return minischema.errors(json.loads(schema_path.read_text()), data)


PROSE = {"$id", "$schema", "title", "description"}


def _machine(o):
    """A JSON schema with the explanatory fields removed, keeping only the parts that affect validation."""
    if isinstance(o, dict):
        return {k: _machine(v) for k, v in o.items() if k not in PROSE}
    if isinstance(o, list):
        return [_machine(v) for v in o]
    return o


def same(a: pathlib.Path, b: pathlib.Path) -> bool:
    """The two contracts' "machine-read parts" are the same: for JSON, the schema without $id/title/description; for
    markdown, the regex/heading blocks and the version marker. Changed explanatory text doesn't count as a mismatch."""
    if a.suffix == ".json" and b.suffix == ".json":
        return _machine(json.loads(a.read_text())) == _machine(json.loads(b.read_text()))
    ta, tb = a.read_text(), b.read_text()
    return md_version(ta) == md_version(tb) and FENCE.findall(ta) == FENCE.findall(tb)
