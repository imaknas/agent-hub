"""A fake runtime: the second implementation of the runtime_ports interface. Design: docs/design/runtime-abstraction.md.

Two uses:
1. Prove the interface doesn't leak Claude Code's shape: Claude and this one must both pass the same contract tests
   (tests/test_runtime_contracts.py).
2. A test double for the platform's other programs: it really remembers state (which sessions are alive,
   how big the context is), not just whether it was called.

Everything lives in memory; tests clear it with reset() and put data in with start / end / set_context.
Standard library.
"""
from __future__ import annotations

import pathlib

import i18n
from runtime_ports import TaskResult

_ = i18n.domain(__file__)

_replies: dict[str, TaskResult] = {}     # judgment point → the result to return
_ran: list = []                          # Tasks run (tests check what it received)
_live: set[str] = set()
_context: dict[str, int] = {}
_env_key = "FAKE_RUNTIME_SESSION"


def reset() -> None:
    _creds.clear()
    _creds.update({"subscription": True, "token": True})
    _resumable.clear()
    _replies.clear()
    _completions.clear()
    _asked.clear()
    _ran.clear()
    _live.clear()
    _status.clear()
    _summary.clear()
    _woken.clear()
    _context.clear()
    _ambient.clear()


# ---------- putting data in, for tests ----------


def start(sid: str) -> None:
    _live.add(sid)


def end(sid: str) -> None:
    _live.discard(sid)


def set_context(sid: str, tokens: int) -> None:
    _context[sid] = tokens


def set_reply(point: str, result: TaskResult) -> None:
    _replies[point] = result


# ---------- agent ----------

def run(task) -> TaskResult:
    _ran.append(task)
    return _replies.get(task.point, TaskResult(True, text=f"fake:{task.point}", session_id="fake-session", turns=1,
                                               cost_usd=0.0))


def describe(task) -> list:
    return ["fake", task.point]


# ---------- sessions ----------

def live_sessions() -> set[str]:
    return set(_live)


def alive(sid: str | None) -> bool:
    return bool(sid) and sid in _live


def context_size(sid: str) -> int | None:
    return _context.get(sid)


def current_session(env) -> str | None:
    return env.get(_env_key) or None


# ---------- wake (the optional port; docs/design/mailbox-runtimes.md B1) ----------

_woken: list[tuple[str, str]] = []      # (session, notice) each wake handed over


def wake(sid: str, text: str) -> bool:
    """A live session takes the notice; one that has ended doesn't."""
    if sid not in _live:
        return False
    _woken.append((sid, text))
    return True


FOLDER_GIT_OFF = True   # it starts nothing in a folder


def can_wake(sid: str) -> bool:
    return sid in _live


# ---------- what a session is doing, how its last reply ended (optional ports) ----------

_status: dict[str, str] = {}
_summary: dict[str, str] = {}


def session_status(sid: str) -> str:
    return _status.get(sid, "unknown")


def session_summary(sid: str) -> str | None:
    return _summary.get(sid)


# ---------- lifecycle (its own format, not Claude's: proves the interface has no Claude shape) ----------
# Native input: {"sid", "dir", "cmd"?, "where"?, "elsewhere"?}; output: {"say": text} / {"block": reason};
# install: write .fake-hooks.json (the event list) in the project folder.

import json  # noqa: E402

from runtime_ports import EVENTS, HubEvent  # noqa: E402


def parse_event(event: str, raw: str):
    try:
        d = json.loads(raw or "{}")
    except ValueError:
        return None
    if not d.get("sid"):
        return None
    target = ({"cwd": d.get("where"), "elsewhere": {"kind": d["elsewhere"]} if d.get("elsewhere") else None}
              if event == "open_session_attempt" else None)
    return HubEvent(event, d["sid"], d.get("dir", ""), command=d.get("cmd") if event == "tool_used" else None,
                    target=target)


def inject(event: str, text: str) -> str:
    return json.dumps({"say": text}, ensure_ascii=False) if text and event in ("session_start", "prompt", "tool_used") else ""


def deny(event: str, reason: str) -> str:
    return json.dumps({"block": reason}, ensure_ascii=False) if event == "open_session_attempt" else ""


def install_lifecycle(workspace, check: bool = False) -> bool:
    path, want = pathlib.Path(workspace) / ".fake-hooks.json", json.dumps(list(EVENTS))
    changed = not path.exists() or path.read_text() != want
    if changed and not check:
        path.write_text(want)
    return changed


def remove_lifecycle(workspace, check: bool = False) -> bool:
    path = pathlib.Path(workspace) / ".fake-hooks.json"
    there = path.exists()
    if there and not check:
        path.unlink()
    return there



# ---------- control (what sessions load settings and hooks from) ----------

def control_paths() -> list:
    return [(".fake-hooks.json", True)]


def session_folders(workspace) -> list:
    return [pathlib.Path(workspace)]


def foreign_controls(workspace) -> list:
    return []


# ---------- instructions ----------

def instructions_file(workspace) -> pathlib.Path:
    return pathlib.Path(workspace) / ".fake-instructions.md"


def local_ignores() -> list:
    return [".fake-instructions.md", ".fake-hooks.json"]


# ---------- stream (output of a long-running session) ----------
# Fake native format: the tool name is the kind (unrecognized ones are other), each output line is
# {"type": ..., "data": {...}}.

from runtime_ports import KINDS, StreamEvent  # noqa: E402

_resumable: set[str] = set()


def set_resumable(sid: str, ok: bool = True) -> None:
    (_resumable.add if ok else _resumable.discard)(sid)


def action(tool: str, inp: dict | None) -> tuple:
    inp = inp or {}
    target = str(inp.get("target", ""))
    return (tool if tool in KINDS else "other"), inp.get("collab"), target, f"{tool} {target}".strip()


def parse_stream(line: str) -> list:
    try:
        d = json.loads(line)
    except ValueError:
        return []
    if not isinstance(d, dict) or not isinstance(d.get("data"), dict):
        return []
    data = dict(d["data"])
    if d.get("type") == "tool_use":  # translated through action() like Claude's, the input isn't trusted directly
        kind, _collab, _target, summary = action(data.pop("tool", ""), data.pop("input", None))
        data = {"kind": kind, "summary": summary, "detail": {}}
    return [StreamEvent(d["type"], data)]


def classify_exit(code, stderr_tail: str) -> StreamEvent:
    if "login" in stderr_tail:
        return StreamEvent("error", {"error": "agent_login", "detail": _("the fake runtime is not logged in")})
    return StreamEvent("error", {"error": "process_crashed", "detail": _("exit {code}: {detail}", code=code,
                                                                         detail=stderr_tail[-200:])})


def resumable(sid: str) -> bool:
    return sid in _resumable


def resume_command(sid: str) -> tuple:
    return (f"fake-resume {sid}", _("fake runtime: resume the same conversation"))


# ---------- auth (its own variable names and error wording, not Claude's) ----------

from runtime_ports import Credential  # noqa: E402

_AUTH_VAR = "FAKE_RUNTIME_KEY"
_creds: dict = {"subscription": True, "token": True}   # mode → present or not


def set_credential(mode: str, present: bool) -> None:
    _creds[mode] = present


def clean_env(env) -> dict:
    return {k: v for k, v in env.items() if k != _AUTH_VAR}


def auth_env(mode: str, env) -> tuple:
    out = clean_env(env)
    if not _creds.get(mode, False):
        return None, _("the fake runtime has no {mode} login", mode=mode)
    if mode == "token":
        out[_AUTH_VAR] = "fake-key"
    return out, ""


def credentials() -> list:
    return [Credential(name=f"fake {m}", label=_("fake {mode} login", mode=m), mode=m, present=p, renew="fake-login")
            for m, p in sorted(_creds.items())]


def is_login_error(text: str) -> bool:
    return "not signed in" in (text or "")


def relocate(sid: str, old_cwd, new_cwd) -> bool:
    return sid in _resumable  # the fake runtime keeps no per-folder records: whatever can be resumed can be resumed


# ---------- completion ----------
from runtime_ports import CompletionResult  # noqa: E402

# judgment point → CompletionResults to return in order (once used up, keeps returning the last)
_completions: dict[str, list] = {}
_asked: list = []                    # the (CompletionRequest, conf) pairs received


def set_completions(point: str, *results) -> None:
    _completions[point] = list(results)


def complete(req, conf) -> CompletionResult:
    _asked.append((req, conf))
    queue = _completions.get(req.point)
    if not queue:
        return CompletionResult(True, text=f"fake:{req.point}", stop="end", input_tokens=len(req.prompt), output_tokens=1)
    return queue.pop(0) if len(queue) > 1 else queue[0]


def platform_settings(project: str, keep=None):
    """confine: the fake honours declared permissions by construction (it runs nothing)."""
    return None


def health(conf) -> tuple:
    return (not conf.get("fake_unhealthy"), "fake_unhealthy" if conf.get("fake_unhealthy") else "")


# ---------- tool servers ----------
_ambient: dict = {}    # "loaded": list | None, "named": list, "error": str | None


def set_ambient(loaded: list | None, named: list | None = None, error: str | None = None) -> None:
    """What ambient_servers / servers_named answer: loaded None = the run loads none of the environment's servers;
    error = it can't tell (ValueError)."""
    _ambient.update({"loaded": loaded, "named": list(named or ()), "error": error})


def ambient_servers(task) -> list | None:
    if _ambient.get("error"):
        raise ValueError(_ambient["error"])
    return _ambient.get("loaded")


def servers_named(task) -> list:
    return list(_ambient.get("named") or ())
