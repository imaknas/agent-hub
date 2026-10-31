#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Mechanically generate the cross-project todo.md from the `## 待辦` section of each project's STATE.md. No LLM.

Source format (the librarian writes STATE.md this way; agent-hub's own to-dos are in openclaw-migration-status.md):

    ## 待辦
    - [ ] 〔{owner}〕something only the owner can do (accounts, passwords, payments, decisions)
    - [ ] 〔agent〕something an agent can do
    - [ ] 〔待定〕something that needs a decision first
    - (無)                       ← when there are no to-dos

Also accepted: grouping with `- [ ] …` under a `### 〔{owner}〕` subheading. Explanatory text and reference links in
the section are skipped; only lines that "look like a to-do but have no owner tag" or "are checked off and still
there" count as format problems.

Same input, same output: todo.md only changes when some project's to-dos really change. Projects with a bad format
(real content but no `## 待辦`, or lines that don't fit the format) are listed under "format problems" at the end of
the file, not silently dropped.

Runs on the hub machine (platform job agent-hub/todo, reads */STATE.md of the memory copies).
Usage: gen-todo.py [output todo.md path] (default: todo.md in the data directory, hubconf.data_file)
Exit code: 0 normally; format problems don't count as failure (they're written to the file and stdout).
"""
from __future__ import annotations  # must also import under the system python3 (3.9)

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)

HOME = pathlib.Path.home()
HUB = hubconf.ROOT
MIGRATION = hubconf.data_file("openclaw-migration-status.md")
# the memory copies the librarian uses (hub.json memory_copies)
PROJECTS = hubconf.p("memory_copies", "~/.memory-copies")

TAG, ASSISTANT = hubconf.owner_tag(), hubconf.assistant_name()   # the owner's name in to-do tags (hub.json owner.tag)
OWNERS = (TAG, "待定", "agent")
ITEM = re.compile(rf"^- \[ \] 〔({re.escape(TAG)}|agent|待定)〕\s*(.+)$")
# an unknown tag is still a subheading: lines under it don't go to the previous group (HTH §5.3)
GROUP = re.compile(r"^#{3,4}\s*〔([^〕]+)〕\s*$")
BARE = re.compile(r"^- \[ \] (?!〔)(.+)$")
# After the librarian's task registration sends the owner's to-dos into the assistant, the end of the line gets
# stamped "(等 {owner}:<key>)". For the owner's to-dos with a key the assistant is the authority, and todo.md no
# longer lists them (only counts them); ones without a key are listed as usual, and have a key once that night's
# registration is done. Half-width and full-width parentheses and colons are both accepted.
OWNER_KEY = re.compile(rf"[(（]等 ?{re.escape(TAG)}[:：]\s*([^()（）]+?)\s*[)）]\s*$")
PLACEHOLDER_MAX_BYTES = 200  # the placeholder STATE.md from onboarding, which has no to-dos


def todo_section(text: str) -> list[str] | None:
    """Return the non-empty lines of the `## 待辦` section; None if there's no such section."""
    m = re.search(r"^## 待辦[ \t]*\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    if not m:
        return None
    return [l.rstrip() for l in m.group(1).splitlines()
            if l.strip() and not l.lstrip().startswith(">")]


def parse(source: str, text: str, items: dict, problems: list) -> None:
    lines = todo_section(text)
    if lines is None:
        problems.append(_("{source}: no `{heading}` section", source=source, heading="## 待辦"))
        return
    group = None  # the other accepted form: `- [ ] …` without an owner tag under a `### 〔{owner}〕` subheading
    for line in lines:
        if line.strip() == "- (無)":
            continue
        h = GROUP.match(line)
        if h:
            group = h.group(1) if h.group(1) in OWNERS else None
            continue
        m = ITEM.match(line)
        if m:
            items[m.group(1)].append((source, m.group(2).strip()))
            continue
        bare = BARE.match(line)
        if bare and group:
            items[group].append((source, bare.group(1).strip()))
        elif bare or line.startswith(("- [ ] 〔", "- [x]", "- [X]")):
            # looks like a to-do but has no (known) owner tag, or is checked off and still there: must be shown,
            # never silently dropped
            problems.append(_("{source}: a to-do without an owner tag, or a finished one not removed: `{line}`", source=source, line=line[:60]))
        # other lines (explanatory text, "see reference" links, continuation lines) aren't to-dos; skipped


def main() -> None:
    # by default, written to the data directory
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else hubconf.data_file("todo.md")
    items: dict[str, list] = {o: [] for o in OWNERS}
    problems: list[str] = []

    names = list(hubconf.roster())
    for name in names:
        state = PROJECTS / name / "STATE.md"
        if not state.exists():
            problems.append(_("{name}: no STATE.md on the hub machine", name=name))
            continue
        if state.stat().st_size <= PLACEHOLDER_MAX_BYTES:
            continue
        parse(name, state.read_text(), items, problems)
    if MIGRATION.exists():
        parse("agent-hub", MIGRATION.read_text(), items, problems)

    in_assistant = [r for r in items[TAG] if OWNER_KEY.search(r[1])]
    items[TAG] = [r for r in items[TAG] if not OWNER_KEY.search(r[1])]
    total = sum(len(v) for v in items.values())
    o = [
        "<!-- contract: todo-md v1 -->",  # the layout's contract is contracts/todo-md.v1.md
        "# " + _("Cross-project to-do"), "",
        # no date in the heading: when the content doesn't change the file shouldn't either (for when it was
        # generated, see git log)
        "> " + _("Generated mechanically by `bin/gen-todo.py` from the `{heading}` section of each project's STATE.md "
                 "({total} {total:item|items});", heading="## 待辦", total=total),
        "> " + _("do not edit by hand: to change the content, change the source (each project's memory, from which "
                 "librarian updates STATE.md;"),
        "> " + _("agent-hub's own to-do items are in `openclaw-migration-status.md`)."), "",
    ]
    # the Muninn project uses these three headings to tell whose to-do it is (the mapping is in its data/config.yaml
    # external_todo_owners); changing a heading means changing it there too
    titles = {TAG: f"只有 {TAG} 能做", "待定": "待定(需要先做決定)", "agent": "agent 可以做"}
    for owner in OWNERS:
        o += [f"## {titles[owner]}", ""]
        if owner == TAG and in_assistant:
            o += ["> " + _("{n} more {n:is|are} registered in {assistant} (marked \"{marker}\" at the end of the line in STATE.md); "
                           "{assistant} is the authority for them, so they are not listed here.",
                           n=len(in_assistant), assistant=ASSISTANT, marker=f"等 {TAG}:<key>"), ""]
        rows = items[owner]
        if not rows:
            o += ["(無)", ""]
            continue
        by_proj: dict[str, list[str]] = {}
        for proj, text in rows:
            by_proj.setdefault(proj, []).append(text)
        for proj in sorted(by_proj):
            o.append(f"### {proj}")
            o += [f"- [ ] {t}" for t in by_proj[proj]]
            o.append("")
    if problems:
        o += ["## " + _("Format problems (these projects' to-do items were not collected)"), ""]
        o += [f"- {p}" for p in problems]
        o.append("")
    out.write_text("\n".join(o).rstrip() + "\n")
    print(_("todo.md: {total} {total:item|items}", total=total) + (_(", {n} format {n:problem|problems}", n=len(problems)) if problems else ""))
    for p in problems:
        print(_("  format problem: {problem}", problem=p))


if __name__ == "__main__":
    main()
