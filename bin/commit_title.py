#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Commit title format: Conventional Commits (https://www.conventionalcommits.org/).

  <type>[(<scope>)][!]: <description>

The types are the same set as the assistant's repo (both generate release notes from the same cliff.toml: feat bumps
minor, fix/perf bump patch, ! bumps major); scope is a part of this repo (free text). A breaking change adds `!` and
says `BREAKING CHANGE: …` in the body. Titles git makes itself (Merge …, Revert "…") pass; `fixup!`/`squash!` only on
your own branch, the merge gate refuses them.

Used by: the platform's commit-msg hook (render-project-settings, repos with `commits: conventional`), and bin/merge-branch before merging a project with `commits: conventional`
in its registry entry.

  commit_title.py --file <COMMIT_EDITMSG>    commit-msg hook: prints the reason and exits 1 when the title is wrong, or,
                                             in the platform's own repo, when the message names a private project
                                             (bin/private_names.py)
  commit_title.py <title>                    check one line
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import i18n  # noqa: E402

_ = i18n.domain(__file__)

TYPES = ("feat", "fix", "perf", "refactor", "test", "docs", "build", "ci", "chore", "revert")
PATTERN = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^()\s][^()]*)\))?(?P<bang>!)?: (?P<desc>\S.*)$")
GIT_MADE = re.compile(r"""^(Merge (branch|remote-tracking branch|tag|commit|pull request) |Revert ")""")
AUTOSQUASH = re.compile(r"^(fixup|squash|amend)! ")


def help_text() -> str:
    return _("Format: <type>[(<scope>)][!]: <description>, where type is {types}; "
             "e.g. `fix(hubbus): dispatch no longer retries on an unsupported runtime`", types=" / ".join(TYPES))


def problem(title: str, final: bool = False) -> str | None:
    """None = fine; otherwise the reason, for people. final = about to land on main (no fixup!/squash!)."""
    title = title.strip()
    if not title:
        return _("the title is empty")
    if GIT_MADE.match(title):
        return None
    if AUTOSQUASH.match(title):
        return _("fixup!/squash! must be folded in first with `git rebase --autosquash`") if final else None
    m = PATTERN.match(title)
    if not m:
        return _("not in the Conventional Commits format. {help}", help=help_text())
    if m["type"] not in TYPES:
        return _("type `{type}` is not in the list. {help}", type=m["type"], help=help_text())
    return None


def title_of(message: str) -> str:
    """the commit message's first line that is neither empty nor a comment (the commit-msg hook's file has # comments)"""
    for line in message.splitlines():
        if line.strip() and not line.startswith("#"):
            return line
    return ""


def platform_repo() -> bool:
    """The commit is in the platform's own repo (its main checkout or a linked worktree of it), whose history is public."""
    import subprocess
    import hubconf
    r = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True)
    try:
        return r.returncode == 0 and pathlib.Path(r.stdout.strip()).resolve() == (hubconf.MAIN / ".git").resolve()
    except Exception:
        return False


def private_in(message: str) -> list[str]:
    """the private project names the message mentions, when it is a commit in the platform's own repo"""
    import private_names
    text = "\n".join(line for line in message.splitlines() if not line.startswith("#"))
    return private_names.found(text) if platform_repo() else []


def main(argv: list[str]) -> int:
    if argv[:1] == ["--file"] and len(argv) == 2:
        with open(argv[1], encoding="utf-8") as f:
            message = f.read()
        title = title_of(message)
        if (named := private_in(message)):
            print(_("commit message: it names a private project ({names}); the platform's history is public: say "
                    "\"a project\" instead", names=", ".join(named)), file=sys.stderr)
            return 1
    elif len(argv) == 1:
        title = argv[0]
    else:
        print(_(__doc__), file=sys.stderr)
        return 2
    why = problem(title)
    if why:
        print(_("commit title: {why}\n  title: {title}", why=why, title=repr(title.strip())), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
