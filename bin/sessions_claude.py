"""Session source: Claude Code's transcripts → neutral entries (bin/sessions.py, contracts/session-record.v1).

Claude Code keeps an undocumented internal format: ~/.claude/projects/<slug of the start folder>/<session>.jsonl, a
session's subagents in <session>/subagents/*.jsonl beside it. This is the only place the platform reads it (plus the
tool table, which the runtime's stream translation shares): parsed into Turn/Action, failing explicitly on an
unrecognised format (SessionFormatError) rather than letting statistics go silently wrong. Read whether or not Claude
Code is the platform's runtime: the files being there is enough.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import i18n  # noqa: E402
from runtime_ports import Action  # noqa: E402
from sessions import SessionFormatError, Turn, tally_line  # noqa: E402

_ = i18n.domain(__file__)
NAME = "claude-code"

# Claude's tools → the neutral kinds (runtime_ports.KINDS) and collaboration kinds (COLLABS); runtime_claude.action()
# uses the same table for a long-running session's events
TOOL_KIND = {"Read": "read", "Glob": "search", "Grep": "search", "WebSearch": "search", "WebFetch": "open_web",
             "Edit": "write_file", "Write": "write_file", "MultiEdit": "write_file", "NotebookEdit": "write_file",
             "Bash": "run_command", "AskUserQuestion": "ask_user"}
_COLLAB = {"Agent": "subagent", "Task": "subagent", "Workflow": "workflow", "Skill": "skill"}


SHOW_FILE_TOOL = "mcp__lease_relay__show_file"   # the platform's "show the user a file" tool (runtime_claude's)


def kind_of(tool: str) -> tuple[str, str | None]:
    """Claude's tool name → (kind, collab)."""
    if tool == SHOW_FILE_TOOL:
        return "read", None
    collab = _COLLAB.get(tool) or ("peer_message" if tool == "SendMessage" or tool.endswith("send_message") else None)
    return TOOL_KIND.get(tool, "other"), collab


def label() -> str:
    return _("Claude Code (~/.claude/projects)")


def _mini_home() -> str:
    try:
        import hubconf
        return hubconf.get("hosts.mini_home", str(pathlib.Path.home()))
    except Exception:
        return str(pathlib.Path.home())


def roots(home: pathlib.Path, real: bool) -> list[pathlib.Path]:
    """Where to read: the hub machine's local sessions (<hosts.mini_home>/.claude), what the laptop backs up hourly
    (the personal profile), and the personal profile when run by hand on the remote machine.
    **Never** the home's own ~/.claude when it isn't the hub machine's (on the remote machine that is the work profile),
    nor the work profile's backup (~/transcript-backups/claude-work-projects): work conversations may only be
    processed locally (see known-issues). A test's home (real=False) stands in for the hub machine's."""
    mini = pathlib.Path(_mini_home()) if real else home
    out = [mini / ".claude" / "projects", home / "transcript-backups" / "claude-personal-projects",
           home / ".claude-personal" / "projects"]
    seen, uniq = set(), []
    for r in out:
        if r.is_dir() and (k := r.resolve()) not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


def files(home: pathlib.Path, real: bool) -> list[pathlib.Path]:
    return [f for r in roots(home, real) for g in ("*/*.jsonl", "*/*/subagents/*.jsonl") for f in sorted(r.glob(g))]


def folder(file: pathlib.Path) -> str | None:
    """The folder the session started in (its first entry naming one); "" when none does."""
    try:
        with file.open(errors="replace") as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if isinstance(o, dict) and o.get("cwd"):
                    return str(o["cwd"])
    except OSError:
        pass
    return ""


def _is_human_prompt(entry: dict) -> bool:
    """Count only messages the user actually sent. Excluded: those from other sessions, background task notifications,
    slash command output, compaction summaries, hook injections, etc. (they are type=user in the transcript too)."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isCompactSummary"):
        return False
    content = (entry.get("message") or {}).get("content") if isinstance(entry.get("message"), dict) else None
    if isinstance(content, list):   # a typed message with an image or a block the tool attached to it
        kinds = {b.get("type") for b in content if isinstance(b, dict)}
        if "tool_result" in kinds or not kinds & {"text", "image"}:
            return False  # tool results
    elif not isinstance(content, str):
        return False
    origin = entry.get("origin")
    return isinstance(origin, dict) and origin.get("kind") == "human"


def _action(block: dict) -> Action:
    """One tool call → a neutral Action. detail holds only skill names: Claude's tool names don't leak out."""
    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
    kind, collab = kind_of(str(block.get("name") or ""))
    return Action(kind, str(inp.get("skill", "?")) if collab == "skill" else "", collab, str(block.get("id") or ""),
                  str(inp.get("command") or "") if kind == "run_command" else "", bool(inp.get("run_in_background")))


def _parse_ts(ts: str) -> datetime.datetime:
    t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return (t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)).astimezone(datetime.timezone.utc)


_KNOWN = {"user", "assistant", "system", "summary", "attachment", "progress", "queue-operation", "file-history-snapshot"}
# Entries Claude Code writes beside the turns, which carry none (request records, titles, modes, links, ledgers): read and
# passed over on purpose, not skipped. A type in neither set counts as skipped, so a version that adds or renames one is
# seen in `brunna runtimes` (seen in a week of transcripts, 2.1.295 to 2.1.322, 11-13)
_IGNORED = {"api-request-blob", "api-request", "api-request-shape", "last-prompt", "atis-latch", "custom-title",
            "agent-name", "bridge-session", "mode", "frame-link", "pr-link", "worktree-state", "relocated",
            "session-owner", "file-history-delta", "cost-state", "artifact-autoreact-ledger", "tools-used",
            "artifact-comment-monitor"}


def _turn(o: dict, fallback_sid: str) -> Turn | None:
    ts = o.get("timestamp")
    if not ts:
        return None
    sid = o.get("sessionId") or fallback_sid
    common = {"id": str(o.get("uuid") or ""), "folder": str(o.get("cwd") or ""), "subagent": bool(o.get("isSidechain"))}
    if _is_human_prompt(o):
        return Turn(_parse_ts(ts), sid, "human", **common)
    content = (o.get("message") or {}).get("content")
    content = content if isinstance(content, list) else []
    if o.get("type") == "assistant":
        acts = tuple(_action(c) for c in content if isinstance(c, dict) and c.get("type") == "tool_use")
        return Turn(_parse_ts(ts), sid, "assistant", "", acts, **common)
    done = tuple(str(c.get("tool_use_id")) for c in content if isinstance(c, dict) and c.get("type") == "tool_result"
                 and c.get("tool_use_id"))
    return Turn(_parse_ts(ts), sid, "other", results=done, **common)


def records(file: pathlib.Path, tally: dict | None = None):
    """The file's entries as Turns; tally (optional) counts the lines per Claude Code version and those skipped
    (sessions.tally_line). Each turn names the version that wrote it; a line that doesn't is counted under the last
    version seen in the file, or under the first one when none is seen yet (the metadata a file opens with)."""
    parsed = recognised = 0
    version = ""
    early: list[bool] = []   # whether each line before the first version was skipped

    def count(skipped: bool) -> None:
        if version:
            tally_line(tally, version, skipped)
        else:
            early.append(skipped)
    with file.open(errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except ValueError:
                count(True)
                continue
            parsed += 1
            if isinstance(o, dict) and isinstance(o.get("version"), str) and o["version"]:
                if not version:
                    version = o["version"]
                    for skipped in early:
                        tally_line(tally, version, skipped)
                    early.clear()
                version = o["version"]
            kind = o.get("type") if isinstance(o, dict) else None
            if isinstance(kind, str) and kind in _IGNORED:
                count(False)
                continue
            if not isinstance(kind, str) or kind not in _KNOWN:
                count(True)
                continue
            recognised += 1
            try:
                t = _turn(o, file.stem)
            except (TypeError, ValueError, AttributeError):
                count(True)
                continue   # one malformed entry (a field of the wrong shape) is skipped, not the whole run
            count(False)
            if t:
                yield t._replace(version=version)
    for skipped in early:   # a file with no version anywhere
        tally_line(tally, "", skipped)
    if parsed >= 5 and recognised == 0:
        raise SessionFormatError(_("{file}: {n} {n:record|records}, none in a known format (did Claude Code change "
                                   "its transcript format?)", file=file, n=parsed))
