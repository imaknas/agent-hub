#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""A newly connected project's first STATE.md, written right away: `brunna first-look <project>`.

The nightly librarian writes STATE.md from the notes a project's agents keep, so a project connected today would show
nothing until its agents have written some and the night has passed. This writes a first one now, from what the
repository already says: its README, its recent commit titles and its top-level files, read through git as the
project (project-git-job.py first-look: a README that is a link is never followed). The model gets them through judgment
point first-look (contracts/llm-points.yaml) and answers with the whole STATE.md; the platform checks its to-do
section against the state-todo contract and writes it, then syncs the memory branch the usual way (git-sync-memory.sh).

Only for a project whose STATE.md is still onboarding's placeholder and that has no notes yet: an existing state is
never replaced. With no model for brunna's decisions, it says so and does nothing. bin/connect.py runs it after
connecting each project.

Usage: first-look.py <project>        (exit 0 written or nothing to do, 1 failed, 2 bad usage)
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

BIN = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import i18n  # noqa: E402
import nofollow  # noqa: E402
import prompts  # noqa: E402
import todo_tokens  # noqa: E402

_ = i18n.domain(__file__)
POINT = "first-look"


def placeholder(ws: pathlib.Path) -> bool:
    """STATE.md never written by anyone but onboarding (no to-do section in either token set), and no notes yet."""
    raw = nofollow.read(ws, ".agent-memory/STATE.md")
    if raw is None:
        return False
    text = raw.decode(errors="replace")
    heads = {todo_tokens.tokens(lang)["todo_heading"] for lang in todo_tokens.SETS}
    if any(line.rstrip() in heads for line in text.splitlines()):
        return False
    notes = nofollow.entries(ws, ".agent-memory/memory") or []
    return not any(n.endswith(".md") for n in notes)


def prompt(name: str, facts: dict) -> str:
    lang, owner = hubconf.lang_content(), hubconf.owner_tag()
    common = dict(lang=lang, owner=owner, assistant=hubconf.assistant_name())
    return prompts.render(POINT, project=name, rules=prompts.render("librarian-state-rules", **common),
                          tree="\n".join(facts.get("tree") or []) or "-",
                          commits="\n".join(facts.get("commits") or []) or "-",
                          commit_count=str(facts.get("commit_count") or 0),
                          readme=facts.get("readme") or "-", **common)


def todo_problems(text: str) -> list[str]:
    import contracts
    contract = hubconf.MAIN / "contracts" / "state-todo.v2.md"
    return contracts.check_state_todo(text, contract, hubconf.owner_tag())


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0].startswith("-"):
        print(_(__doc__), file=sys.stderr)
        return 2
    name = argv[0]
    doc = (hubconf.projects() or {}).get(name)
    if not doc:
        print(_("first-look: no project {name}", name=name), file=sys.stderr)
        return 2
    import registry
    ws = registry.dirs(doc)["workspace"]
    if not ws or not placeholder(ws):
        return 0   # already has a state, or notes the librarian will settle tonight
    import llm_ready
    if (off := llm_ready.unsupported(POINT)):
        print(_("{project}: no first summary yet ({reason}). It comes with the nightly tidy-up once a model is set up "
                "(brunna llm setup).", project=name, reason=off))
        return 0
    import project_git
    try:
        facts = project_git.data(name, ws, "first-look", str(ws))
    except project_git.Unavailable as e:
        print(_("{project}: can't read the repository for a first summary ({why})", project=name, why=e))
        return 1
    if not isinstance(facts, dict):
        return 1
    print(_("{project}: writing a first summary from its README and history…", project=name), flush=True)
    r = subprocess.run([*hubconf.launch(BIN / "llm.py"), "run", POINT], input=prompt(name, facts),
                       capture_output=True, text=True, timeout=700)
    try:
        got = json.loads(r.stdout).get("structured_output")
    except (ValueError, AttributeError):
        got = None
    state = got.get("state") if isinstance(got, dict) else None
    if not isinstance(state, str) or not state.lstrip().startswith("# "):
        print(_("{project}: the model gave no usable summary (exit {code}); the nightly tidy-up writes it once notes "
                "arrive", project=name, code=r.returncode))
        return 1
    if (problems := todo_problems(state)):
        print(_("{project}: the summary's to-do section isn't in the expected format ({problems}); not written",
                project=name, problems="; ".join(problems)[:300]))
        return 1
    nofollow.replace(ws, ".agent-memory/STATE.md", (state.rstrip() + "\n").encode())
    subprocess.run([str(BIN / "with-lock.sh"), str(BIN / "git-sync-memory.sh")], cwd=ws / ".agent-memory",
                   capture_output=True)
    print(_("✓ {project}: {summary}", project=name, summary=str(got.get("summary") or "").strip()))
    heads = {todo_tokens.tokens(lang)["todo_heading"] for lang in todo_tokens.SETS}
    lines = state.splitlines()
    start = next((i for i, line in enumerate(lines) if line.rstrip() in heads), None)
    if start is not None:
        todo = []
        for line in lines[start + 1:]:
            if line.startswith("## "):
                break
            if line.startswith("- "):
                todo.append(line)
        for line in todo[:8]:
            print("    " + line)
    print(_("  The whole state: {file}. Its agents read it at the start of each session; the digest comes after "
            "tonight's tidy-up.", file=ws / ".agent-memory" / "STATE.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
