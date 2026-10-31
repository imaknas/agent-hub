"""Judgments shared by several agent-hub scripts: which transcript entries are messages a real person typed, which
project a transcript folder maps to, which commits count as project work. Kept in one place so that one change keeps
every script in step.

Reading conversation records isn't here: it goes through runtime_ports (layer 5, transcripts); the Claude Code format
is only in runtime_claude.py.

Users: check-memory-gaps.py, memory-nudge.py, weekly-reflect.py.
"""
# the system python3 (3.9, the one hooks use) must be able to import this too: memory-nudge.py relies on it
from __future__ import annotations

import datetime
import pathlib
import re
import subprocess

import hubconf  # also in bin/; scripts using hublib have already put bin/ on sys.path
import i18n

_ = i18n.domain(__file__)

HOME = pathlib.Path.home()
HUB = hubconf.ROOT
# Since 2026-10-15 every project is in the hub machine's ~/workspace/<name> (that is the registry's paths.workspace).
# Earlier locations on the laptop (and paths before renames) are in legacy-paths.tsv: transcript folder names were
# computed from the paths of the time, so counting old conversations needs it.
LEGACY_PATHS = hubconf.data_file("legacy-paths.tsv")
# home directories read from hub.json, not hardcoded (CLAUDE.md's rules for new code)
MBP_HOME = hubconf.get("hosts.mbp_home", str(pathlib.Path.home()))
MINI_HOME = hubconf.get("hosts.mini_home", str(pathlib.Path.home()))
MINI_WORKSPACE = f"{MINI_HOME}/workspace"
# The memory-only copies the librarian uses on the hub machine (only AGENT.md/STATE.md/memory/, not project code).
# Called ~/projects before 2026-10-15, changed to a hidden directory: too close in name to ~/workspace (where sessions
# are opened) and mistaken for project folders
MEMORY_COPIES = hubconf.p("memory_copies", "~/.memory-copies")
# working directories that aren't onboarded but show up in transcripts and are worth counting separately (old laptop
# locations are in legacy-paths.tsv)
EXTRA_PROJECTS = {
    "agent-hub": f"{MINI_HOME}/agent-hub",
}


# A memory copy is the platform's repo, its content the project's: checked out without symlinks, a committed link is a
# plain file holding the path it pointed at, so the librarian, the registrar and the model never read through one
# (docs/design/projects-and-repos.md section 4). Every clone of a memory repo the platform reads files from passes it.
NO_SYMLINKS = ["-c", "core.symlinks=false"]


def harden_copy(repo: pathlib.Path) -> None:
    """core.symlinks=false in a copy cloned before it was the rule, and its links checked out again as plain files."""
    def git(*args, **kw):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, **kw)
    git("config", "core.symlinks", "false")
    links = [e.split(b"\t", 1)[1].decode(errors="surrogateescape") for e in git("ls-files", "-s", "-z").stdout.split(b"\0")
             if e.startswith(b"120000 ")]
    for p in links:
        if (repo / p).is_symlink():
            (repo / p).unlink()
    if links:
        git("checkout", "--", *links)


def pull_copy(repo: pathlib.Path, **kw) -> subprocess.CompletedProcess:
    """git pull in a memory copy, hardened first (kw: subprocess.run's, e.g. where the output goes)."""
    harden_copy(pathlib.Path(repo))
    return subprocess.run(["git", *NO_SYMLINKS, "pull", "-q"], cwd=repo, **kw)


def legacy_paths(name: str | None = None) -> dict[str, list[str]] | list[str]:
    """Project name → its earlier paths (locations on the laptop, paths before renames). Given name, only that
    project's."""
    out: dict[str, list[str]] = {}
    if LEGACY_PATHS.exists():
        for line in LEGACY_PATHS.read_text().splitlines()[1:]:
            cols = line.split("\t")
            if len(cols) >= 2 and cols[0]:
                out.setdefault(cols[0], []).append(cols[1])
    return out.get(name, []) if name is not None else out


# Commits that don't mean "someone did work in this project": those produced automatically by schedules (papers, data
# snapshots, the librarian), and commits touching only files agent-hub manages (.gitignore/.claude/CLAUDE.md) (onboard,
# batch cleanups).
AUTOMATED_SUBJECT = re.compile(r"^(papers: |data: nightly snapshot|librarian: )")
NON_WORK_PATHS = (":!.gitignore", ":!.claude", ":!CLAUDE.md")


def project_paths() -> dict[str, list[str]]:
    """Project name → its current path (the hub machine's ~/workspace) + its earlier paths (legacy-paths.tsv)."""
    out: dict[str, list[str]] = {}
    for name, paths in hubconf.roster().items():
        ws = paths.get("workspace")
        # ~ is the hub machine's home (transcript folder names follow the hub machine's paths), even when run on the laptop
        if ws:
            out.setdefault(name, []).append(MINI_HOME + ws[1:] if ws.startswith("~/") else ws)
    for name, path in EXTRA_PROJECTS.items():
        out.setdefault(name, []).append(path)
    for name, paths in legacy_paths().items():
        out.setdefault(name, []).extend(p for p in paths if p not in out[name])
    return out


def is_work_subject(subject: str) -> bool:
    return not AUTOMATED_SUBJECT.match(subject)


# ---------- the daily digest (digest/YYYY-MM-DD.md) ----------
# Several jobs each write their own section (the librarian's project summaries, memory-gaps…), and rerunning any of them
# neither overwrites nor duplicates the others: each section is wrapped in <!-- digest:<name>:begin/end -->, a section
# with the same name is replaced whole, otherwise it's appended at the end.

def digest_path(day: str | None = None) -> pathlib.Path:
    return hubconf.data_file("digest") / f"{day or datetime.date.today().isoformat()}.md"


def digest_append(section: str, text: str, day: str | None = None) -> pathlib.Path | None:
    """Write text as the <section> section of today's digest (empty text = remove that section). If the file doesn't
    exist yet and text is empty, no file is created."""
    f = digest_path(day)
    day = f.stem
    begin, end = f"<!-- digest:{section}:begin -->", f"<!-- digest:{section}:end -->"
    old = f.read_text() if f.exists() else ""
    if not old and not text.strip():
        return None
    block = f"{begin}\n{text.strip()}\n{end}" if text.strip() else ""
    pat = re.compile(rf"\n*{re.escape(begin)}.*?{re.escape(end)}", flags=re.S)
    if old and pat.search(old):  # replace in place, section order unchanged
        body = pat.sub(lambda _m: f"\n\n{block}" if block else "", old, count=1)
    else:
        body = (old.rstrip() or f"# Digest {day}") + (f"\n\n{block}" if block else "")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body.rstrip() + "\n")
    return f


def main(argv: list) -> int:
    """For shell scripts: hublib.py digest-append <section name> (content from stdin), hublib.py digest-section
    <section name> (prints that section)."""
    import sys
    if len(argv) == 2 and argv[0] == "digest-append":
        f = digest_append(argv[1], sys.stdin.read())
        if f:
            print(f)
        return 0
    if len(argv) == 2 and argv[0] == "digest-section":
        f = digest_path()
        m = re.search(rf"<!-- digest:{re.escape(argv[1])}:begin -->\n(.*?)\n<!-- digest:{re.escape(argv[1])}:end -->",
                      f.read_text() if f.exists() else "", flags=re.S)
        print(m.group(1) if m else "", end="")
        return 0
    print(_("usage: hublib.py digest-append <section> < content | digest-section <section>"), file=sys.stderr)
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
