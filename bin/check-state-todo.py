#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Check the format of a STATE.md's to-do section (contracts/state-todo.v2.md, either token set), with the same
parsing rules as gen-todo.py.

The librarian calls it every time it has changed a project's STATE.md: problems are printed (exit code 1), so the
librarian asks the model to fix only the format. No problems: prints nothing, exit code 0. Small placeholder files are
not checked.

Usage: check-state-todo.py <STATE.md>
"""
import importlib.util
import pathlib
import sys

here = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("gen_todo", here / "gen-todo.py")
gen_todo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen_todo)


def main() -> None:
    path = pathlib.Path(sys.argv[1])
    if not path.exists() or path.stat().st_size <= gen_todo.PLACEHOLDER_MAX_BYTES:
        return
    items = {o: [] for o in gen_todo.OWNERS}
    problems: list[str] = []
    gen_todo.parse("STATE.md", path.read_text(), items, problems)
    if problems:
        print("\n".join(problems))
        sys.exit(1)


if __name__ == "__main__":
    main()
