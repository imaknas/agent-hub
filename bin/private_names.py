"""This deployment's private project names, which must never appear in the platform's own repo: in its files
(tests/test_personal_boundary.py) or its commit messages (bin/commit_title.py's commit-msg hook, bin/merge-branch).

The list is the instance's config/private-names.txt, never in the repo: one name per line, `#` comments; a trailing `~`
marks a name that is also a common word, counted only in a path, a registry file name, or next to a word for "project"
(tests/fixtures/personal-boundary.json project_words). A deployment without the list (someone else's clone) checks
nothing; a list that is named but can't be read is an error (ListError), never an empty list. The commit gate runs under
a fake HOME and passes the list's location in the PRIVATE_NAMES variable (names.env).

A name counts only as a whole word in ASCII terms: next to a letter or digit it is part of another word, next to
anything else (a space, `-`, `_`, Chinese text) it stands alone.

    private_names.py --pre-push <remote> <url>   the platform repo's pre-push hook: reads git's ref lines on stdin and
                                                 refuses when a pushed commit's or annotated tag's message names a
                                                 private project, however it was made (cherry-pick, revert, an amend,
                                                 --no-verify)
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORDS = ROOT / "tests" / "fixtures" / "personal-boundary.json"


class ListError(Exception):
    """The list is named but can't be read."""


def names_file() -> pathlib.Path | None:
    import names
    env = names.env("PRIVATE_NAMES")
    if env:
        if not pathlib.Path(env).is_file():
            raise ListError(f"{env}: not a readable file")
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
        if not name:
            continue
        n = re.escape(name)
        a, ah = "A-Za-z0-9", "A-Za-z0-9-"   # ASCII word characters: Chinese text and `_` around a name don't hide it
        pat = (rf"(?:~/|(?:workspace|projects|services|memory-copies|git-origin)/){n}(?![{ah}])|(?<![{ah}]){n}\.yaml|(?<![{ah}]){n}\s*(?:{w})"
               rf"|(?:{w})\s+{n}(?![{ah}])") if common else rf"(?<![{a}]){n}(?![{a}])"
        out.append((name, re.compile(pat, re.I)))
    return out


def found(message: str) -> list[str]:
    """The private names a text mentions (empty when the deployment has no list). ListError when the list is named but
    can't be read."""
    f = names_file()
    if not f or not f.exists():
        return []
    try:
        text = f.read_text()
    except OSError as e:
        raise ListError(f"{f}: {e.strerror or e}") from e
    return [name for name, pat in patterns(text) if pat.search(message)]


ZERO = "0" * 40


def pushed(lines: list[str]) -> list[tuple[str, str]]:
    """(object, its message) for every commit and annotated tag a push sends: git's pre-push lines `<local ref> <local
    sha> <remote ref> <remote sha>`; a deletion sends nothing, a new ref everything the remote doesn't have yet."""
    def git(*a):
        return subprocess.run(["git", *a], capture_output=True, text=True).stdout
    out = []
    for line in lines:
        parts = line.split()
        if len(parts) != 4 or set(parts[1]) == {"0"}:
            continue
        local, remote = parts[1], parts[3]
        if git("cat-file", "-t", local).strip() == "tag":
            out.append((local, git("cat-file", "tag", local).split("\n\n", 1)[-1]))
        span = [local, "--not", "--remotes"] if set(remote) == {"0"} else [f"{remote}..{local}"]
        for sha in git("rev-list", *span).split():
            out.append((sha, git("log", "-1", "--format=%B", sha)))
    return out


def pre_push(lines: list[str]) -> int:
    import i18n
    _ = i18n.domain(__file__)
    try:
        bad = [(sha, names) for sha, msg in pushed(lines) if (names := found(msg))]
    except ListError as e:
        print(_("pre-push: the private-names list can't be read ({error}); nothing pushed", error=e), file=sys.stderr)
        return 1
    if bad:
        print(_("pre-push: these messages name a private project, and the platform's history is public:"), file=sys.stderr)
        for sha, names in bad:
            print(f"  {sha[:9]}: {', '.join(names)}", file=sys.stderr)
        print(_("Reword them (say \"a project\") before pushing; nothing pushed."), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--pre-push"]:
        sys.path.insert(0, str(ROOT / "bin"))
        sys.exit(pre_push(sys.stdin.read().splitlines()))
    print(__doc__, file=sys.stderr)
    sys.exit(2)
