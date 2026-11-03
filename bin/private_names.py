"""This deployment's private project names, which must never appear in the platform's own repo: in its files
(tests/test_personal_boundary.py) or its commit messages (bin/commit_title.py's commit-msg hook, bin/merge-branch).

The list is the instance's config/private-names.txt, never in the repo: one name per line, `#` comments; a trailing `~`
marks a name that is also a common word, counted only in a path, a registry file name, or next to a word for "project"
(tests/fixtures/personal-boundary.json project_words). A deployment without the list (someone else's clone) checks
nothing. The commit gate runs under a fake HOME and passes the list's location in the PRIVATE_NAMES variable
(names.env).
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORDS = ROOT / "tests" / "fixtures" / "personal-boundary.json"


def names_file() -> pathlib.Path | None:
    import names
    env = names.env("PRIVATE_NAMES")
    if env:
        return pathlib.Path(env)
    try:
        import hubconf
        f = hubconf.config_file("private-names.txt")
        return f if f.exists() else None
    except Exception:
        return None


def project_words() -> list[str]:
    return json.loads(WORDS.read_text())["project_words"]


def patterns(text: str, words: list[str] | None = None) -> list[tuple[str, re.Pattern]]:
    """(name, its pattern) for each line of the list."""
    w = "|".join(re.escape(x) for x in (project_words() if words is None else words))
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, common = line.rstrip("~").strip(), line.endswith("~")
        n = re.escape(name)
        pat = (rf"(?:~/|(?:workspace|projects|services|memory-copies|git-origin)/){n}(?![\w-])|(?<![\w-]){n}\.yaml|(?<![\w-]){n}\s*(?:{w})"
               rf"|(?:{w})\s+{n}(?![\w-])") if common else rf"(?<![\w]){n}(?![\w])"
        out.append((name, re.compile(pat, re.I)))
    return out


def found(message: str) -> list[str]:
    """The private names a text mentions (empty when the deployment has no list)."""
    f = names_file()
    if not f or not f.exists():
        return []
    return [name for name, pat in patterns(f.read_text()) if pat.search(message)]
