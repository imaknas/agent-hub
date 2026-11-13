"""The owner's sessions with coding agents, from whatever agent produced them (contracts/session-record.v1).

The session-based features (the weekly review's statistics, the memory-gap check, the waiting report, a worktree's
last activity) read the sessions found on this machine, not the platform's own runtime: which agent the platform runs
its jobs with says nothing about where the owner's work is. Each source turns one tool's files into neutral entries
(Turn/Action), found by presence alone, independent of the runtime.* settings:

  SOURCES          sessions_<tool>.py adapters: Claude Code (sessions_claude), Codex (sessions_codex). The only
                   places that know a tool's files; a source whose files aren't on this machine contributes nothing
  the import folder  <data>/sessions-import/*.jsonl: files already in the contract's format, dropped there by any tool
                   or script, read like any source (invalid lines, or a file with none valid, are said on stderr and
                   skipped: a drop never stops the other sources' statistics)

  turns(projects, since, until=None, worktrees=False, subagents=False) -> Iterator[(project, Turn)]
        every entry of every session that belongs to a project, within the time range. A session belongs to the
        folder of its first entry that names one: to a project when that folder is one of the project's paths, and,
        with worktrees=True, also when it lies inside one (a worktree, a subfolder); when several projects' paths hold
        it, the deepest wins. subagents=True also yields subagent entries (Turn.subagent). An entry id is yielded
        once per source: a resumed session's copied history, or the same session read from two places (a backup of the remote
        machine's sessions holds the hub machine's too), counts once.
  last_activity(path) -> datetime | None     when an entry was last written in this folder
  present() -> [(source name, files)]        the sources with any files on this machine
  require() -> list[str]                     present()'s names; raises NoSessions (the reason, where it looked)
                                             when no source has any files: the features say they are off, never
                                             an empty report shown as a quiet week

No message text is read into the neutral entries (Turn.text stays empty): what a person typed never leaves its tool's
files through here.
"""
from __future__ import annotations

import collections
import contextlib
import datetime
import json
import pathlib
import sys
from typing import NamedTuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import i18n  # noqa: E402
from runtime_ports import COLLABS, KINDS, Action  # noqa: E402

_ = i18n.domain(__file__)

CONTRACT = pathlib.Path(__file__).resolve().parents[1] / "contracts" / "session-record.v1.schema.json"
IMPORT = "sessions-import"   # the import folder's name in the data directory


class Turn(NamedTuple):
    ts: datetime.datetime    # aware, UTC
    session: str
    role: str                # "human" (what the person actually sent) | "assistant" | "other" (tool results, system
                             # messages, messages from other sessions…)
    text: str = ""           # never filled from a session's files (the contract carries no text)
    actions: tuple = ()      # for role == assistant, which Actions this entry did
    id: str = ""             # the entry's own id ("" when the tool has none)
    folder: str = ""         # the working folder the session was in at this entry
    subagent: bool = False   # an entry of a subagent the session started
    results: tuple = ()      # the call ids (Action.call) whose results this entry returns
    version: str = ""        # the version of the tool that wrote this entry ("" when its files don't say)


def tally_line(tally: dict | None, version: str, skipped: bool) -> None:
    """Count one line a source read into tally (records(file, tally)): {version: [lines, skipped]}. A line counts as
    skipped when it isn't an entry the source can read: not JSON, a type it doesn't know, a field of the wrong shape
    (docs/design/runtime-versions.md section 6: readers count what they skip)."""
    if tally is not None:
        row = tally.setdefault(version or "", [0, 0])
        row[0] += 1
        row[1] += bool(skipped)


class NoSessions(Exception):
    """No source has any session files on this machine."""


class SessionFormatError(Exception):
    """A source's files are there but in a format it doesn't recognise (fails loudly, never miscounts)."""


# ---------- the sources ----------

def _home() -> pathlib.Path:
    return _HOME or pathlib.Path.home()


def _data() -> pathlib.Path:
    if _DATA:
        return _DATA
    import hubconf
    return hubconf.data_dir()


_HOME: pathlib.Path | None = None   # only() points these elsewhere (tests, a read of another home)
_DATA: pathlib.Path | None = None


def sources() -> list:
    """The source modules (each: NAME, label(), files(home, real) -> [Path], folder(file), records(file))."""
    import sessions_claude
    import sessions_codex
    return [sessions_claude, sessions_codex, ImportFolder]


class ImportFolder:
    """<data>/sessions-import/*.jsonl, already in the contract's format."""
    NAME = "import"

    @staticmethod
    def label() -> str:
        return _("the import folder")

    @staticmethod
    def files(home: pathlib.Path, real: bool) -> list[pathlib.Path]:
        d = _data() / IMPORT
        return sorted(d.glob("*.jsonl")) if d.is_dir() else []

    @staticmethod
    def folder(file: pathlib.Path) -> str | None:
        return None   # a file may hold several sessions in several folders: read it whole

    @staticmethod
    def records(file: pathlib.Path, tally: dict | None = None):
        import minischema
        schema = json.loads(CONTRACT.read_text())
        bad = good = 0
        with file.open(errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    o = None
                try:   # the schema checks a time's shape, not that it exists (2026-02-30): from_record checks that
                    t = from_record(o) if isinstance(o, dict) and not minischema.errors(schema, o) else None
                except ValueError:
                    t = None
                version = str(o.get("version") or "") if isinstance(o, dict) else ""
                tally_line(tally, version, t is None)
                if t is None:
                    bad += 1
                    continue
                good += 1
                yield t
        if bad and not good:   # a drop the reader can't read (e.g. written for a later version): said, not fatal
            print(_("{file}: {n} {n:line|lines}, none a session record (contracts/session-record.v1.schema.json); "
                    "skipped", file=file, n=bad), file=sys.stderr)
        elif bad:
            print(_("{file}: skipped {n} {n:line|lines} that {n:is not a|are not} session {n:record|records}",
                    file=file, n=bad), file=sys.stderr)


def from_record(o: dict) -> Turn:
    """A contract record (already valid) → Turn."""
    ts = datetime.datetime.fromisoformat(o["ts"].replace("Z", "+00:00")).astimezone(datetime.timezone.utc)
    acts = tuple(Action(a["kind"], a.get("detail") or "", a.get("collab"), a.get("call") or "", a.get("command") or "",
                        bool(a.get("background"))) for a in o.get("actions") or ())
    return Turn(ts, o["session"], o["role"], "", acts, o.get("id") or "", o.get("folder") or "",
                bool(o.get("subagent")), tuple(o.get("results") or ()), str(o.get("version") or ""))


def to_record(t: Turn) -> dict:
    """Turn → a contract record (what a tool or script writes into the import folder)."""
    o = {"v": 1, "ts": t.ts.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
         "session": t.session, "role": t.role}
    if t.id:
        o["id"] = t.id
    if t.folder:
        o["folder"] = t.folder
    if t.subagent:
        o["subagent"] = True
    if t.actions:
        o["actions"] = [{"kind": a.kind if a.kind in KINDS else "other",
                         "collab": a.collab if a.collab in COLLABS else None,
                         **({"detail": a.detail} if a.detail else {}), **({"call": a.call} if a.call else {}),
                         **({"command": a.command} if a.command else {}),
                         **({"background": True} if a.background else {})} for a in t.actions]
    if t.results:
        o["results"] = list(t.results)
    if t.version:
        o["version"] = t.version
    return o


@contextlib.contextmanager
def only(home: pathlib.Path, data: pathlib.Path):
    """Read only the sessions under this home and this data directory (tests; reading another machine's copy)."""
    global _HOME, _DATA
    saved = _HOME, _DATA
    _HOME, _DATA = pathlib.Path(home), pathlib.Path(data)
    try:
        yield
    finally:
        _HOME, _DATA = saved


def _files() -> list[tuple[object, list[pathlib.Path]]]:
    real = _HOME is None
    return [(s, s.files(_home(), real)) for s in sources()]


def present() -> list[tuple[str, int]]:
    return [(s.NAME, len(fs)) for s, fs in _files() if fs]


def places() -> list[str]:
    """Where the sources look, for people (the off message)."""
    return [s.label() for s in sources()]


def require() -> list[str]:
    have = present()
    if not have:
        raise NoSessions(_("no session records on this machine; looked for {places}",
                           places=_(", ").join(places())))
    return [n for n, _n in have]


def _mtime(f: pathlib.Path) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(f.stat().st_mtime, datetime.timezone.utc)


def _owner(folder: str, projects: dict[str, list[str]], worktrees: bool) -> str | None:
    """The project a session in this folder belongs to (the deepest path that holds it)."""
    best, depth = None, -1
    for name, paths in projects.items():
        for p in paths:
            p = p.rstrip("/")
            if folder == p or (worktrees and folder.startswith(p + "/")):
                if len(p) > depth:
                    best, depth = name, len(p)
    return best


def turns(projects: dict[str, list[str]], since: datetime.datetime, until: datetime.datetime | None = None,
          worktrees: bool = False, subagents: bool = False):
    """(project, Turn) for every entry of the projects' sessions in [since, until); see the module header."""
    seen: set[tuple[str, str]] = set()
    for src, files in _files():
        for f in files:
            try:
                if _mtime(f) < since:
                    continue   # not written to since before the range started
            except OSError:
                continue
            hint = src.folder(f)
            if hint is not None and _owner(hint, projects, worktrees) is None:
                continue       # the whole file is one session in a folder no project holds
            first: dict[str, str] = {}
            pending: dict[str, list[Turn]] = collections.defaultdict(list)
            for t in src.records(f):
                if t.session not in first:
                    if not t.folder:
                        pending[t.session].append(t)
                        continue
                    first[t.session] = t.folder
                    held = pending.pop(t.session, [])
                else:
                    held = []
                owner = _owner(first[t.session], projects, worktrees)
                if owner is None:
                    continue
                for x in (*held, t):
                    if x.id:
                        if (src.NAME, x.id) in seen:
                            continue
                        seen.add((src.NAME, x.id))
                    if (subagents or not x.subagent) and x.ts >= since and (until is None or x.ts < until):
                        yield owner, x


RECENT_DAYS = 30   # last_activity reads every entry of the files written this recently; older ones by start folder
_index: dict = {}


def _activity() -> dict[str, datetime.datetime]:
    """{folder: when an entry was last written in it}, built once per process (and per only() context): files written
    in the last RECENT_DAYS are read entry by entry, so a session that moved into a folder counts there; older files
    count for the folder they started in, at their file's time."""
    key = (_HOME, _DATA)
    if key in _index:
        return _index[key]
    out: dict[str, datetime.datetime] = {}

    def note(folder: str, ts: datetime.datetime) -> None:
        if folder and (folder not in out or ts > out[folder]):
            out[folder] = ts

    recent = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=RECENT_DAYS)
    for src, files in _files():
        for f in files:
            try:
                m = _mtime(f)
            except OSError:
                continue
            hint = src.folder(f)
            if hint:
                note(hint.rstrip("/"), m)
            if m < recent and hint is not None:
                continue
            at: dict[str, str] = {}   # an entry that names no folder is where its session last was
            try:
                for t in src.records(f):
                    if t.folder:
                        at[t.session] = t.folder.rstrip("/")
                    note(at.get(t.session, ""), min(t.ts, m))
            except SessionFormatError as e:   # worktree cleanup keeps the file's time for its start folder (as before)
                print(e, file=sys.stderr)
    _index[key] = out
    return out


def last_activity(path) -> datetime.datetime | None:
    """When an entry was last written in this folder, by any session: one that started elsewhere and moved here (a
    worktree it entered) counts too."""
    return _activity().get(str(path).rstrip("/"))


SKIP_LIMIT = 0.05   # a version whose files skip more than this share of their lines is said (runtime-versions.md section 6)


def skipped(since: datetime.datetime) -> list[dict]:
    """Per source and tool version, the lines of the files written since `since` and how many the source skipped:
    [{source, version, lines, skipped, files, over}], over = the share skipped is above SKIP_LIMIT, so the statistics
    may undercount that version's sessions. A file a source can't read at all counts as one error row of its own."""
    rows: dict[tuple[str, str], dict] = {}
    for src, files in _files():
        for f in files:
            try:
                if _mtime(f) < since:
                    continue
            except OSError:
                continue
            tally: dict = {}
            try:
                for _t in src.records(f, tally):
                    pass
            except SessionFormatError:   # nothing in it could be read: every line it counted is a skip
                tally = {v: [lines, lines] for v, (lines, _s) in tally.items()} or {"": [1, 1]}
            except OSError:
                continue   # a file gone meanwhile
            for version, (lines, skip) in tally.items():
                row = rows.setdefault((src.NAME, version), {"source": src.NAME, "version": version, "lines": 0,
                                                            "skipped": 0, "files": 0})
                row["lines"] += lines
                row["skipped"] += skip
                row["files"] += 1
    out = sorted(rows.values(), key=lambda r: (r["source"], r["version"]))
    for r in out:
        r["over"] = r["lines"] > 0 and r["skipped"] / r["lines"] > SKIP_LIMIT
    return out
