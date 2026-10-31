#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Mechanically generate the cross-project todo.md from the to-do section of each project's STATE.md. No LLM.

Source format (contracts/state-todo.v2.md; the librarian writes STATE.md this way, agent-hub's own to-dos are in
openclaw-migration-status.md), in either token set (bin/todo_tokens.py), shown here in the English one:

    ## Tasks
    - [ ] [owner] something only the owner can do (accounts, passwords, payments, decisions)
    - [ ] [agent] something an agent can do
    - [ ] [undecided] something that needs a decision first
    - (none)                     ← when there are no to-dos

Also accepted: grouping with `- [ ] …` under a `### [owner]` subheading. Explanatory text and reference links in
the section are skipped; only lines that "look like a to-do but have no (known) owner tag" or "are checked off and
still there" count as format problems. Both token sets are read whatever the settings, also mixed in one file.

Same input, same output: todo.md only changes when some project's to-dos really change. Projects with a bad format
(real content but no to-do section, two of them, or lines that don't fit the format) are listed under "format
problems" at the end of the file, not silently dropped. todo.md (contracts/todo-md.v2.md) is written in the UI
language's heading set.

Runs on the hub machine (platform job agent-hub/todo, reads */STATE.md of the memory copies).
Usage: gen-todo.py [output todo.md path] (default: todo.md in the data directory, hubconf.data_file)
Exit code: 0 normally; format problems don't count as failure (they're written to the file and stdout).
"""
from __future__ import annotations  # must also import under the system python3 (3.9)

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402
import todo_tokens  # noqa: E402

_ = i18n.domain(__file__)

HOME = pathlib.Path.home()
HUB = hubconf.ROOT
MIGRATION = hubconf.data_file("openclaw-migration-status.md")
# the memory copies the librarian uses (hub.json memory_copies)
PROJECTS = hubconf.p("memory_copies", "~/.memory-copies")

TAG, ASSISTANT = hubconf.owner_tag(), hubconf.assistant_name()   # the owner's name in to-do tags (hub.json owner.tag)
P = todo_tokens.patterns(TAG)
OWNERS = todo_tokens.ROLES   # items are collected by role: owner, tbd, agent
# After the registrar sends the owner's to-dos to the assistant, the end of the line gets stamped with the key marker
# (P.key). For the owner's to-dos with a key the assistant is the authority, and todo.md no longer lists them (only
# counts them); ones without a key are listed as usual, and have a key once that night's registration is done.
PLACEHOLDER_MAX_BYTES = 200  # the placeholder STATE.md from onboarding, which has no to-dos


def content_heading() -> str:
    """the to-do heading this instance's writers use (named in messages)"""
    return todo_tokens.tokens(hubconf.lang_content())["todo_heading"]


def todo_sections(text: str) -> list[list[str]]:
    """The non-empty lines of each to-do section (either heading); [] if there is none."""
    lines = text.splitlines()
    return [[l.rstrip() for l in lines[a:b] if l.strip() and not l.lstrip().startswith(">")]
            for a, b in P.section(lines)]


def parse(source: str, text: str, items: dict, problems: list) -> None:
    sections = todo_sections(text)
    if not sections:
        problems.append(_("{source}: no `{heading}` section", source=source, heading=content_heading()))
        return
    if len(sections) > 1:
        # both are read (nothing dropped), but the file must get one section back
        problems.append(_("{source}: {n} to-do sections; there must be one", source=source, n=len(sections)))
    for lines in sections:
        group = None  # the other accepted form: `- [ ] …` without a label under an owner subheading (`### [owner]`)
        for line in lines:
            if P.is_none(line):
                continue
            h = P.group.match(line)
            if h:
                group = P.role(h)
                continue
            m = P.item.match(line)
            if m and P.role(m):
                items[P.role(m)].append((source, m.group("text").strip()))
                continue
            bare = P.bare.match(line)
            if bare and group:
                items[group].append((source, bare.group("text").strip()))
            elif bare or P.tag_start.match(line) or line.startswith(("- [x]", "- [X]")):
                # looks like a to-do but has no (known) owner tag, or is checked off and still there: must be shown,
                # never silently dropped
                problems.append(_("{source}: a to-do without an owner tag, or a finished one not removed: `{line}`", source=source, line=line[:60]))
            elif P.no_box.match(line):
                problems.append(_("{source}: a to-do without its `- [ ]` checkbox: `{line}`", source=source, line=line[:60]))
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

    in_assistant = [r for r in items["owner"] if P.key.search(r[1])]
    items["owner"] = [r for r in items["owner"] if not P.key.search(r[1])]
    total = sum(len(v) for v in items.values())
    ui = todo_tokens.tokens(hubconf.lang_ui())   # todo.md is for the owner: its headings in the UI language
    o = [
        f"<!-- contract: todo-md v{ui['todo_md_version']} -->",  # the layout's contract is contracts/todo-md.v2.md
        "# " + _("Cross-project to-do"), "",
        # no date in the heading: when the content doesn't change the file shouldn't either (for when it was
        # generated, see git log)
        "> " + _("Generated mechanically by `bin/gen-todo.py` from the `{heading}` section of each project's STATE.md "
                 "({total} {total:item|items});", heading=content_heading(), total=total),
        "> " + _("do not edit by hand: to change the content, change the source (each project's memory, from which "
                 "librarian updates STATE.md;"),
        "> " + _("agent-hub's own to-do items are in `openclaw-migration-status.md`)."), "",
    ]
    # the Muninn project uses these three headings to tell whose to-do it is (the mapping is in its data/config.yaml
    # external_todo_owners); changing a heading means changing it there too
    titles = todo_tokens.todo_md_headings(hubconf.lang_ui(), TAG)
    # the marker as the note names it: without its parentheses
    marker = todo_tokens.marker(hubconf.lang_content(), TAG, "<key>")[1:-1]
    for owner in OWNERS:
        o += [titles[owner], ""]
        if owner == "owner" and in_assistant:
            o += ["> " + _("{n} more {n:is|are} registered in {assistant} (marked \"{marker}\" at the end of the line in STATE.md); "
                           "{assistant} is the authority for them, so they are not listed here.",
                           n=len(in_assistant), assistant=ASSISTANT, marker=marker), ""]
        rows = items[owner]
        if not rows:
            o += [ui["todo_md_none"], ""]
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
