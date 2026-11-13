"""Session source: Codex's session files → neutral entries (bin/sessions.py, contracts/session-record.v1).

Codex writes one JSONL file per session under $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl (archived
ones under archived_sessions/), default CODEX_HOME ~/.codex. Each line is {timestamp, type, payload}:
  session_meta     the first line: the session's id, cwd, originator (which program opened it), source (a subagent's
                   session says so here)
  event_msg        item_completed with item.type UserMessage = what the person typed (the response_item "user"
                   messages also carry injected context such as <environment_context>, so they are not counted);
                   older versions write user_message instead
  response_item    message (assistant), function_call / custom_tool_call / local_shell_call / web_search_call (a tool
                   call; call_id names it), *_output (its result, by call_id)
Only this file knows the format; an unrecognised file fails explicitly (SessionFormatError), never miscounts.
A session a program opened (`codex exec`, the platform's own runs) has no human entries: its prompt is not the
person's.
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import i18n  # noqa: E402
from runtime_ports import Action  # noqa: E402
from sessions import SessionFormatError, Turn, tally_line  # noqa: E402

_ = i18n.domain(__file__)
NAME = "codex"

# tool name → (kind, collab)
TOOLS = {"shell": ("run_command", None), "local_shell": ("run_command", None), "exec_command": ("run_command", None),
         "shell_command": ("run_command", None), "container.exec": ("run_command", None), "exec": ("run_command", None),
         "apply_patch": ("write_file", None), "view_image": ("read", None), "web_search": ("search", None),
         "request_user_input": ("ask_user", None), "spawn_agent": ("other", "subagent"),
         "send_input": ("other", "peer_message")}
CALLS = {"function_call", "custom_tool_call", "local_shell_call", "web_search_call"}
OUTPUTS = {"function_call_output", "custom_tool_call_output", "local_shell_call_output"}
_KNOWN = {"session_meta", "event_msg", "response_item", "turn_context", "world_state", "token_usage_record", "compacted"}
NOT_A_PERSON = {"codex_exec"}   # originators that are programs (plus the platform's own name, below)


def label() -> str:
    return _("Codex (~/.codex/sessions)")


def _codex_home(home: pathlib.Path, real: bool) -> pathlib.Path:
    env = os.environ.get("CODEX_HOME") if real else None
    return pathlib.Path(env) if env else home / ".codex"


def files(home: pathlib.Path, real: bool) -> list[pathlib.Path]:
    base = _codex_home(home, real)
    return [f for d in ("sessions", "archived_sessions") if (base / d).is_dir()
            for f in sorted((base / d).rglob("rollout-*.jsonl"))]


def _meta(file: pathlib.Path) -> dict | None:
    try:
        with file.open(errors="replace") as fh:
            o = json.loads(fh.readline() or "null")
    except (OSError, ValueError):
        return None
    return o.get("payload") if isinstance(o, dict) and o.get("type") == "session_meta" else None


def folder(file: pathlib.Path) -> str | None:
    m = _meta(file)
    return str(m.get("cwd") or "") if isinstance(m, dict) else ""


def _platform_names() -> set:
    try:
        import names
        return set(names.all("product"))
    except Exception:
        return set()


def _is_subagent(meta: dict) -> bool:
    src = meta.get("source")
    return (isinstance(src, dict) and "subagent" in src) or meta.get("thread_source") not in (None, "user")


def _command(name: str, payload: dict) -> str:
    """The command text of a call that runs one: its arguments' command/cmd, or (code mode) the code it runs."""
    if payload.get("type") == "local_shell_call":
        cmd = (payload.get("action") or {}).get("command")
    elif payload.get("type") == "custom_tool_call":
        return str(payload.get("input") or "")
    else:
        try:
            args = json.loads(payload.get("arguments") or "{}")
        except ValueError:
            return str(payload.get("arguments") or "")
        cmd = args.get("command") or args.get("cmd") if isinstance(args, dict) else None
    if isinstance(cmd, list):
        return " ".join(str(c) for c in cmd)
    return str(cmd or "")


def _ts(s: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(datetime.timezone.utc)


def _entry(o: dict, sid: str, cwd: str, sub: bool, person: bool, new_style: bool) -> tuple[Turn | None, str]:
    """One line → (its Turn or None, the session's folder after it)."""
    p, ts = o.get("payload"), o.get("timestamp")
    if not isinstance(p, dict) or not ts:
        return None, cwd
    if o["type"] == "turn_context" and p.get("cwd"):
        return None, str(p["cwd"])
    common = {"folder": cwd, "subagent": sub}
    if o["type"] == "event_msg":
        item = p.get("item") if p.get("type") == "item_completed" else None
        typed = (isinstance(item, dict) and item.get("type") == "UserMessage") or (
            p.get("type") == "user_message" and not new_style)
        if person and typed:
            return Turn(_ts(ts), sid, "human", id=str((item or {}).get("id") or ""), **common), cwd
        return None, cwd
    if o["type"] != "response_item":
        return None, cwd
    kind = p.get("type")
    if kind == "message" and p.get("role") == "assistant":
        return Turn(_ts(ts), sid, "assistant", id=str(p.get("id") or ""), **common), cwd
    if kind in CALLS:
        name = str(p.get("name") or ("web_search" if kind == "web_search_call" else "shell"))
        k, collab = TOOLS.get(name, ("other", None))
        call = str(p.get("call_id") or p.get("id") or "")
        act = Action(k, "", collab, call, _command(name, p) if k == "run_command" else "")
        return Turn(_ts(ts), sid, "assistant", "", (act,), str(p.get("id") or ""), **common), cwd
    if kind in OUTPUTS and p.get("call_id"):
        return Turn(_ts(ts), sid, "other", id=str(p.get("id") or ""), results=(str(p["call_id"]),), **common), cwd
    if kind == "message":
        return Turn(_ts(ts), sid, "other", id=str(p.get("id") or ""), **common), cwd
    return None, cwd


def records(file: pathlib.Path, tally: dict | None = None):
    """The rollout's entries as Turns; tally (optional) counts its lines and those skipped under the Codex version
    its session meta names (sessions.tally_line)."""
    meta = _meta(file) or {}
    version = str(meta.get("cli_version") or "")
    sid = str(meta.get("id") or meta.get("session_id") or file.stem)
    cwd = str(meta.get("cwd") or "")
    sub = _is_subagent(meta)
    person = not sub and str(meta.get("originator") or "") not in NOT_A_PERSON | _platform_names()
    try:   # newer versions write a typed message as item_completed/UserMessage; then user_message (older) isn't counted
        raw = file.read_text(errors="replace")
    except OSError:
        return
    new_style = '"UserMessage"' in raw
    parsed = recognised = 0
    with file.open(errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except ValueError:
                tally_line(tally, version, True)
                continue
            parsed += 1
            if not isinstance(o, dict) or not isinstance(o.get("type"), str) or o["type"] not in _KNOWN:
                tally_line(tally, version, True)
                continue
            recognised += 1
            try:
                t, cwd = _entry(o, sid, cwd, sub, person, new_style)
            except (TypeError, ValueError, AttributeError):
                tally_line(tally, version, True)
                continue   # one malformed entry is skipped, not the whole run
            tally_line(tally, version, False)
            if t:
                yield t._replace(version=version)
    if parsed >= 5 and recognised == 0:
        raise SessionFormatError(_("{file}: {n} {n:record|records}, none in a known format (did Codex change its "
                                   "session format?)", file=file, n=parsed))
