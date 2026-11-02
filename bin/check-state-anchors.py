#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""List STATE.md statements whose code anchor no longer resolves on the project's main branch -- a mechanical check
at zero LLM cost (docs/design/state-anchors.md).

STATE.md says things about code ("the data check lives in ⚓`src/checks/run.ts`"). The librarian rewrites it from
memory notes and never sees the code, so a renamed file or a deleted function stays in STATE.md for weeks. An anchor
(⚓ + a code span: a path, or a path#symbol) marks such a statement; this script resolves every anchor of every
project's STATE.md (the librarian's memory copy) against the workspace's local main/master (bin/state_anchors.py).

Outcomes: renamed, deleted, missing (never on any branch), symbol (the file is there, the name is not) and malformed
are stale; pending (only on an unmerged local branch: a worktree session's work not merged yet) and untracked (on disk
but not in git: data, output, a cache) are listed apart in the digest and are not to-dos. A project with anchors that cannot be checked (no workspace, not git, no main/master) gets a
"not checked" line, never a silent 0.

Usage: check-state-anchors.py [--project P] [--apply]
Output: one markdown line per stale anchor, nothing at all otherwise.
--apply (platform job brunna/state-anchors, nightly after the librarian and todo): the stale anchors go into
  todo.md's fixed block (removed when there are none), stale, pending and not-checked into the state-anchors section
  of today's digest, and only a one-line summary is printed -- whatever the result, so "0 stale" can be told apart
  from "did not run at all". todo.md is regenerated as a whole by gen-todo.py, so this job runs after todo.
"""
import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import hublib  # noqa: E402
import i18n  # noqa: E402
import state_anchors  # noqa: E402

_ = i18n.domain(__file__)
EXCERPT = 100


def problem(code: str) -> str:
    """why an anchor is malformed (state_anchors.PROBLEMS), in the UI language"""
    return {"empty": lambda: _("no path"), "url": lambda: _("a URL, not a path"),
            "absolute": lambda: _("not relative to the project folder"), "parent": lambda: _("leaves the project (..)"),
            "line-number": lambda: _("a line number (lines drift; anchor a symbol instead)"),
            "symbol-name": lambda: _("not a symbol name"),
            "directory-symbol": lambda: _("a symbol in a directory"),
            "memory": lambda: _("a memory file, not code")}[code]()


def unchecked_reason(code: str) -> str:
    """why a workspace cannot be checked (state_anchors.UNCHECKED), in the UI language"""
    return {"no-workspace": lambda: _("no workspace in the registry"),
            "no-directory": lambda: _("the workspace folder does not exist"),
            "not-git": lambda: _("the workspace is not a git repository"),
            "no-base-branch": lambda: _("the repository has neither main nor master")}[code]()


def describe(r: state_anchors.Result) -> str:
    """what happened to the anchor, in the UI language"""
    return {
        "renamed": lambda: _("renamed to `{path}` in {commit}", path=r.hint, commit=r.commit),
        "deleted": lambda: _("deleted in {commit}", commit=r.hint),
        "missing": lambda: _("not on main nor on any branch"),
        "symbol": lambda: (_("the name is gone from the file; now in {files}", files=r.hint) if r.hint
                           else _("the name is gone from the file")),
        "malformed": lambda: _("malformed: {problem}", problem=problem(r.hint)),
        "pending": lambda: _("only on branch `{branch}` (not merged yet)", branch=r.hint),
        "untracked": lambda: _("on disk but not in git; anchors are for tracked code"),
    }[r.outcome]()


def line(project: str, r: state_anchors.Result, box: str = "- [ ] ") -> str:
    s = r.anchor.statement
    s = s if len(s) <= EXCERPT else s[:EXCERPT - 1] + "…"
    return box + _("**{project}**: ⚓`{anchor}` {what} (STATE.md line {line}: \"{statement}\")", project=project,
                        anchor=r.anchor.raw, what=describe(r), line=r.anchor.line_no, statement=s)


def scan(only: str | None) -> tuple[list[str], list[str], list[str], int, list[str]]:
    """(stale lines, lines listed apart (pending, untracked), not-checked lines, anchors checked, projects with stale
    anchors) over every onboarded project"""
    copies = hubconf.p("memory_copies", "~/.memory-copies")
    owner = hubconf.owner_tag()
    stale, pending, unchecked, n, names = [], [], [], 0, []
    for name, paths in sorted(hubconf.roster().items()):
        if only and name != only:
            continue
        st = copies / name / "STATE.md"
        if not st.exists():
            continue
        results, why = state_anchors.check(st.read_text(errors="replace"), owner, hubconf.path(paths.get("workspace")))
        if why:
            unchecked.append("- " + _("**{project}**: anchors not checked: {reason}", project=name, reason=unchecked_reason(why)))
            continue
        n += len(results)
        stale += [line(name, r) for r in state_anchors.stale(results)]
        names += [name] if state_anchors.stale(results) else []
        pending += [line(name, r, "- ") for r in results if r.outcome in ("pending", "untracked")]
    return stale, pending, unchecked, n, names


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    stale, pending, unchecked, n, projects = scan(args.project)
    if not args.apply:
        if stale:
            print("\n".join(stale))
        return
    write_todo_block(hubconf.data_file("todo.md"), stale)
    parts = [("\n".join(stale)) if stale else ""]
    if pending:
        parts.append(_("Not on main, but not stale either (waiting on a merge, or not in git):") + "\n\n"
                     + "\n".join(pending))
    if unchecked:
        parts.append("\n".join(unchecked))
    body = "\n\n".join(p for p in parts if p)
    hublib.digest_append("state-anchors", f"## {heading()}\n\n{body}" if body else "")
    names = " ".join(projects)
    print(_("state anchor check: {n} {n:anchor|anchors} checked, {stale} stale, {pending} listed apart, "
            "{unchecked} {unchecked:project|projects} not checked", n=n, stale=len(stale), pending=len(pending),
            unchecked=len(unchecked))
          + (_("; stale in: {names}", names=names) if names else ""))


def heading() -> str:
    """the heading of the digest section and the todo.md block (looked up per call: the language is read at run time)"""
    return _("STATE.md statements whose code is gone")


def write_todo_block(todo: pathlib.Path, stale: list[str]) -> None:
    """todo.md's <!-- state-anchors:begin/end --> block: put at the end of the file when anchors are stale, removed
    when none are. Nothing is done when todo.md does not exist yet."""
    if not todo.exists():
        return
    s = re.sub(r"\n*<!-- state-anchors:begin -->.*?<!-- state-anchors:end -->\n?", "\n", todo.read_text(),
               flags=re.S).rstrip() + "\n"
    if stale:
        import llm_ready
        # the librarian only hears of it when it can run (no model: it is off, so only a session can fix it)
        why = _("STATE.md marks these statements with a code anchor that no longer resolves on the project's main "
                "branch: the statement is probably out of date. The librarian is told on its next run for the "
                "project; a project session can fix it sooner.") if not llm_ready.unsupported("librarian-state") else \
            _("STATE.md marks these statements with a code anchor that no longer resolves on the project's main "
              "branch: the statement is probably out of date. The librarian is off on this machine (no model), so a "
              "project session has to fix it.")
        s += ("\n<!-- state-anchors:begin -->\n## " + heading() + _(" (mechanical check)") + "\n\n" + why + "\n\n"
              + "\n".join(stale) + "\n<!-- state-anchors:end -->\n")
    todo.write_text(s)


if __name__ == "__main__":
    main()
