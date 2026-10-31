"""Staleness anchors in STATE.md (docs/design/state-anchors.md): find them, resolve them against the project's base
branch, say which no longer resolve. Zero LLM; standard library and git only.

An anchor is `⚓` directly followed by a code span: ⚓`path`, ⚓`dir/`, ⚓`path#Symbol`, ⚓`path#Class.method`. It
marks a statement about code that exists now. Anchors in the to-do section and in fenced code blocks are not read.

    import state_anchors
    anchors = state_anchors.find(text, owner_tag)
    repo = state_anchors.Repo.open(workspace)       # None + reason when it cannot be checked
    results = [repo.resolve(a) for a in anchors]    # Result.outcome: ok | pending | untracked | renamed | deleted |
                                                    #                 missing | symbol | malformed

Users: check-state-anchors.py (the nightly job), librarian.py (the stale note in its prompt), bin/evals/e2.py (scores).
"""
from __future__ import annotations

import dataclasses
import pathlib
import re
import subprocess

import todo_tokens

ANCHOR = re.compile(r"⚓️? ?`([^`\n]+)`")
SIGN = "⚓"
SYMBOL = re.compile(r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*$")
LINE_NO = re.compile(r":\d+(?:[-:]\d+)?$")
FENCE = re.compile(r"^\s*(```|~~~)")
# the codes callers turn into text: why an anchor is malformed, why a workspace cannot be checked
PROBLEMS = ("empty", "url", "absolute", "parent", "line-number", "symbol-name", "directory-symbol", "memory")
UNCHECKED = ("no-workspace", "no-directory", "not-git", "no-base-branch")
# the memory worktree's branch (onboard-project.sh): it holds notes, not code, and is never merged into main
MEMORY_BRANCH = "agent-memory"
STALE = ("renamed", "deleted", "missing", "symbol", "malformed")   # what is reported as stale (pending is not)


@dataclasses.dataclass(frozen=True)
class Anchor:
    raw: str              # the code span's content
    path: str
    symbol: str | None
    problem: str | None   # why it is malformed (a code: PROBLEMS), None when it is well-formed
    line_no: int          # 1-based
    statement: str        # the line it is on, list marker stripped


@dataclasses.dataclass(frozen=True)
class Result:
    anchor: Anchor
    outcome: str
    hint: str = ""        # renamed: the new path; deleted: the commit; pending: the branch; symbol: other files;
                          # malformed: a code of PROBLEMS
    commit: str = ""      # renamed: the commit that renamed it


def parse(raw: str) -> tuple[str, str | None, str | None]:
    """an anchor's content → (path, symbol, problem); problem is None for a well-formed anchor"""
    path, sep, symbol = raw.strip().rpartition("#")
    if not sep:
        path, symbol = raw.strip(), None
    if not path:
        return path, symbol, "empty"
    if "://" in path:
        return path, symbol, "url"
    if path.startswith(("/", "~")):
        return path, symbol, "absolute"
    if ".." in path.split("/"):
        return path, symbol, "parent"
    if path.split("/")[0] in ("memory", ".agent-memory") or path == "STATE.md":
        return path, symbol, "memory"
    if LINE_NO.search(path):
        return path, symbol, "line-number"
    if symbol is not None and not SYMBOL.match(symbol):
        return path, symbol, "symbol-name"
    if symbol is not None and path.endswith("/"):
        return path, symbol, "directory-symbol"
    return path, symbol, None


def statement(line: str) -> str:
    return re.sub(r"^\s*(?:[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+|#+\s+)", "", line).strip()


def find(text: str, owner: str) -> list[Anchor]:
    """every anchor in the text, outside the to-do section(s) and fenced code blocks"""
    lines = text.splitlines()
    skip = set()
    for first, end in todo_tokens.patterns(owner).section(lines):
        skip.update(range(first - 1, end))   # the heading line too
    out, fenced = [], False
    for i, line in enumerate(lines):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced or i in skip:
            continue
        for m in ANCHOR.finditer(line):
            path, symbol, problem = parse(m.group(1))
            out.append(Anchor(m.group(1), path, symbol, problem, i + 1, statement(line)))
    return out


def count(text: str, owner: str) -> int:
    return len(find(text, owner))


def in_todo(text: str, owner: str) -> int:
    """anchors written inside the to-do section (the librarian must not write any)"""
    lines = text.splitlines()
    return sum(len(ANCHOR.findall(lines[j])) for first, end in todo_tokens.patterns(owner).section(lines)
               for j in range(first, end))


class Repo:
    """a project's workspace and its base branch (the first of main, master that exists)"""

    def __init__(self, ws: pathlib.Path, base: str, prefix: str):
        self.ws, self.base, self.prefix = ws, base, prefix
        self._others: list[str] | None = None

    @classmethod
    def open(cls, workspace: str | pathlib.Path | None) -> tuple[Repo | None, str]:
        """(the repo, "") or (None, why it cannot be checked: a code of UNCHECKED)"""
        if not workspace:
            return None, "no-workspace"
        ws = pathlib.Path(workspace).expanduser()
        if not ws.is_dir():
            return None, "no-directory"
        if _git(ws, "rev-parse", "--is-inside-work-tree").returncode != 0:
            return None, "not-git"
        base = next((b for b in ("main", "master")
                     if _git(ws, "rev-parse", "--verify", "-q", f"refs/heads/{b}").returncode == 0), None)
        if not base:
            return None, "no-base-branch"
        return cls(ws, base, _git(ws, "rev-parse", "--show-prefix").stdout.strip()), ""

    def _type(self, rev: str, path: str) -> str:
        r = _git(self.ws, "cat-file", "-t", f"{rev}:./{path.rstrip('/')}")
        return r.stdout.strip() if r.returncode == 0 else ""

    def _present(self, rev: str, path: str) -> bool:
        """a file, a directory or a submodule, whatever the trailing slash says: the check never reports what is
        there"""
        return self._type(rev, path) in ("blob", "tree", "commit")   # commit: a submodule

    def others(self) -> list[str]:
        """local branches not merged into the base branch (merged ones outlive their worktrees and hold old files),
        without the memory branch"""
        if self._others is None:
            refs = _git(self.ws, "for-each-ref", "--no-merged", self.base, "--format=%(refname:short)",
                        "refs/heads").stdout.split()
            self._others = [b for b in refs if b not in (self.base, MEMORY_BRANCH)]
        return self._others

    def untracked(self, path: str) -> bool:
        """on disk in the working tree but not in git (ignored or never added): data, output, a cache"""
        return (self.ws / path).exists() and _git(self.ws, "ls-files", "--error-unmatch", "--", path).returncode != 0

    def ignored_on_disk(self, path: str) -> bool:
        """on disk and gitignored: a file that left git for .gitignore (build output) is not gone"""
        return (self.ws / path).exists() and _git(self.ws, "check-ignore", "-q", "--", path).returncode == 0

    def has_word(self, rev: str, word: str, path: str = ".") -> bool:
        return _git(self.ws, "grep", "-q", "-w", "-F", "-e", word, rev, "--", f"./{path}").returncode == 0

    def files_with(self, parts: list[str], limit: int = 3) -> list[str]:
        """files on the base branch where every part of a dotted name occurs as a word"""
        out = _git(self.ws, "grep", "-l", "-w", "-F", "--all-match", *(x for p in parts for x in ("-e", p)),
                   self.base, "--", ".").stdout.splitlines()
        return [l.split(":", 1)[1] for l in out if ":" in l][:limit]

    def resolve(self, a: Anchor) -> Result:
        """the base branch's history decides first (a file deleted there stays deleted, whatever old branch still has
        it), unless it is now gitignored and still on disk (it left git as build output: untracked); only a path never
        on the base branch can be pending or untracked otherwise"""
        if a.problem:
            return Result(a, "malformed", a.problem)
        if self._present(self.base, a.path):
            if a.symbol is None:
                return Result(a, "ok")
            parts = a.symbol.split(".")
            if all(self.has_word(self.base, p, a.path) for p in parts):
                return Result(a, "ok")
            return Result(a, "symbol", ", ".join([f for f in self.files_with(parts, 4) if f != a.path][:3]))
        last = _git(self.ws, "log", "-1", "--format=%H", self.base, "--", f"./{a.path.rstrip('/')}").stdout.strip()
        if last and self.ignored_on_disk(a.path.rstrip("/")):
            return Result(a, "untracked")
        if last:
            full = self.prefix + a.path.rstrip("/")
            for line in _git(self.ws, "show", "-M", "--name-status", "--format=", last).stdout.splitlines():
                f = line.split("\t")
                if len(f) == 3 and f[0].startswith("R") and f[1] == full:
                    new = f[2][len(self.prefix):] if f[2].startswith(self.prefix) else f[2]
                    return Result(a, "renamed", new, last[:8])
            return Result(a, "deleted", last[:8])
        branch = next((b for b in self.others() if self._present(b, a.path)), None)
        if branch:
            return Result(a, "pending", branch)
        if self.untracked(a.path.rstrip("/")):
            return Result(a, "untracked")
        return Result(a, "missing")


# what each stale outcome means, for the librarian's prompt (prompts are English; the model writes in the content
# language): the owner-facing text is check-state-anchors.py's, through locales/
PROMPT_TEXT = {
    "renamed": "the file was renamed to `{hint}` (commit {commit})",
    "deleted": "the file was deleted on main (commit {hint})",
    "missing": "the path is not in the project's repository, on main or any branch",
    "symbol": "the file is there, but the name no longer occurs in it",
    "malformed": "not a valid anchor ({hint})",
}


def record(r: Result) -> dict:
    """a stale result as plain data (the librarian's eval cases record it in args.json)"""
    return {"raw": r.anchor.raw, "outcome": r.outcome, "hint": r.hint, "commit": r.commit, "line_no": r.anchor.line_no,
            "statement": r.anchor.statement}


# what the librarian may do about each (it never sees the code): a gone file may mean a stale statement, a bad anchor
# usually does not
PROMPT_ACTION = {
    "renamed": "the statement may be out of date: correct it only from what the memory notes say; otherwise just "
               "point the anchor at the new path",
    "deleted": "the statement may be out of date: correct or remove it only if the memory notes say so; otherwise "
               "remove the anchor and keep the statement",
    "missing": "the anchor is probably wrong, not the statement: remove the anchor and keep the statement",
    "symbol": "the anchor is probably wrong, not the statement: remove the `#name` (or the anchor) and keep the "
              "statement",
    "malformed": "remove the anchor and keep the statement",
}


def note_line(rec: dict) -> str:
    what = PROMPT_TEXT[rec["outcome"]].format(hint=rec["hint"], commit=rec.get("commit", ""))
    return (f"- line {rec['line_no']}, ⚓`{rec['raw']}`: {what}; {PROMPT_ACTION[rec['outcome']]}. "
            f"The statement: {rec['statement']}")


def _git(ws: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True)


def check(text: str, owner: str, workspace) -> tuple[list[Result], str]:
    """all anchors of a STATE.md resolved: (results, "") or ([], why not checked: UNCHECKED) when it has anchors but the
    workspace cannot be checked; ([], "") when it has none"""
    anchors = find(text, owner)
    if not anchors:
        return [], ""
    repo, why = Repo.open(workspace)
    if repo is None:
        return [], why
    return [repo.resolve(a) for a in anchors], ""


def stale(results: list[Result]) -> list[Result]:
    return [r for r in results if r.outcome in STALE]
