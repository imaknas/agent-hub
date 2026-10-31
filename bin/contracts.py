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
    rules = md_rules(contract, owner)
    errs = []
    v = md_version(text)
    if v and v != ("todo-md", 1):
        errs.append(_("the version marker is {v}, not todo-md v1", v=v))
    headings = rules["heading:owner"]
    found = [l for l in text.splitlines() if l.startswith("## ")]
    owners = [h for h in found if h in headings]
    if owners != headings:
        errs.append(_("the owner headings must be, in order, {headings}; found {owners}", headings=headings, owners=owners))
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
    rules = md_rules(contract, owner)
    item, group, bare = (re.compile(rules[f"regex:{k}"][0]) for k in ("item", "group", "bare"))
    lines = text.splitlines()
    try:
        start = lines.index("## 待辦") + 1
    except ValueError:
        return [_("no `{heading}` section", heading="## 待辦")]
    errs, in_group = [], False
    for l in lines[start:]:
        if l.startswith("## "):
            break
        if not l.strip() or l.strip() == "- (無)":
            continue
        if group.match(l):
            in_group = True
            continue
        if item.match(l) or (in_group and bare.match(l)):
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
