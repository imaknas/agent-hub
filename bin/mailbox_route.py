#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""mailbox-route: a small model's pick of which live session gets a letter, when there is a real choice
(docs/design/mailbox-session-first.md, "Picking one", "The last reply"). hubbus dispatch asks; this module keeps what the
ask leaves and runs it.

Never inside the dispatch loop: dispatch starts this file as a process of its own (start), in its own process group, and
reads the answer on a later tick (read_answer). Waiting on it is bounded (WAIT_S, two ticks), then the most recently
active session is picked. One call per letter, one per project per tick (the caller's), DAILY_CALLS per project per
day; past those, recency.

What the model reads (prompt): the letter's sender, type and first LETTER_CHARS characters, and for each candidate (at
most MAX_CANDIDATES) its id, its folder under the project, how long since its last hook and what it is doing; its last
reply (session_summary) only where text_allowed says so. All of it goes in as one JSON block marked as data: a letter
anyone can send, and a reply a session wrote, are never instructions.

What comes back: only an id in the list it was given, written by the call's process to a mode-600 file in the secrets
sessions folder (hubconf.session_file, suffix ANSWER), which dispatch checks against the candidates again. Nothing the
model wrote is kept elsewhere: the process's output goes nowhere, llm.answer prints and publishes nothing (a reason can
quote the model). For a call that gave no answer dispatch says so itself, in fixed words: in its log, and as a job.failed
once a day per project (hubbus route_failed). Nothing is recorded for evals. The calls file (CALLS, in the state folder) holds a hash of each letter's id, its
project and when the call started; no text.

    mailbox_route.py      the call's process: reads {"key", "prompt", "cands"} on stdin (start writes it)
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402

POINT = "mailbox-route"
WAIT_S = 90             # no answer this long after the start (two ticks of dispatch): recency picks
CALL_TIMEOUT_S = 75     # the call's process ends itself by then, answer or not; the point's own timeout_s is under it
DAILY_CALLS = 24        # per project per day; past it, recency
LETTER_CHARS = 1000     # of the letter's text
LAST_CHARS = 500        # of a candidate's last reply (what wait() keeps is at most this)
MAX_CANDIDATES = 8      # the most recently active, when there are more
KEEP_DAYS = 8           # a letter is routed until it is 7 days old; what its call left goes a day after
ANSWER = "route"        # the answer's suffix in hubconf.session_addresses_dir


def key(letter_id: str) -> str:
    """A letter's name in the files here: its id is free text for an unsandboxed sender, a hash isn't."""
    return hashlib.sha256(letter_id.encode("utf-8", "surrogatepass")).hexdigest()[:32]


def calls_file() -> pathlib.Path:
    return hubconf.STATE / "mailbox" / "route-calls.json"


def _calls() -> dict:
    try:
        d = json.loads(calls_file().read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _save(calls: dict) -> None:
    f = calls_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(calls, sort_keys=True))
    os.replace(tmp, f)


def call_of(letter_id: str) -> dict | None:
    """What this letter's call left: {project, ts (epoch seconds), done}; None: never asked."""
    c = _calls().get(key(letter_id))
    return c if isinstance(c, dict) and isinstance(c.get("ts"), (int, float)) else None


def calls_today(names) -> int:
    """Calls started today for a project, under any of its names."""
    day = datetime.date.today().isoformat()
    names = set(names)
    return sum(1 for c in _calls().values() if isinstance(c, dict) and c.get("project") in names
               and isinstance(c.get("ts"), (int, float))
               and datetime.date.fromtimestamp(c["ts"]).isoformat() == day)


def mark(letter_id: str, project: str, done: bool = False) -> None:
    """The call for this letter has started (or couldn't: done). Old entries go here, KEEP_DAYS after their start: the file is
    written only by dispatch, under its lock, so nothing else prunes it. An answer file goes with the sessions folder's own
    prune, ten minutes after it was written."""
    cut = time.time() - KEEP_DAYS * 86400
    calls = {k: v for k, v in _calls().items() if isinstance(v, dict) and isinstance(v.get("ts"), (int, float)) and v["ts"] > cut}
    calls[key(letter_id)] = {"project": project, "ts": time.time(), "done": done}
    _save(calls)


def consume(letter_id: str) -> None:
    """Its answer has been read, or recency picked instead: the letter is never asked about again, and its answer goes."""
    calls = _calls()
    k = key(letter_id)
    if isinstance(calls.get(k), dict):
        calls[k]["done"] = True
        _save(calls)
    f = answer_file(letter_id)
    if f:
        f.unlink(missing_ok=True)


def answer_file(letter_id: str) -> pathlib.Path | None:
    return hubconf.session_file(key(letter_id), ANSWER)


def read_answer(letter_id: str) -> tuple[str, str | None]:
    """("pending", None): nothing yet; ("none", None): the call ended with no usable answer; ("answer", id)."""
    f = answer_file(letter_id)
    try:
        text = f.read_text(encoding="utf-8").strip() if f else None
    except FileNotFoundError:
        return "pending", None
    except (OSError, ValueError):
        return "none", None
    if text is None:
        return "none", None
    return ("answer", text) if text else ("none", None)


def write_answer(k: str, sid: str) -> None:
    """The call's one output: an id from its list, or nothing (empty: no usable answer). Mode 600, never through a link."""
    f = hubconf.session_file(k, ANSWER)
    if not f:
        return
    f.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(sid)
    os.replace(tmp, f)


# ---------- where it runs, and what it may read ----------

def target():
    """(where the point would run, None) or (None, why it can't run anywhere): no provider in the settings and a default
    runtime with no completion interface. Off then: recency picks, and dispatch says why once."""
    import completion
    import llm
    try:
        spec = llm.points()["points"][POINT]
        return llm.completion_target(POINT, spec), None
    except completion.Refused as e:
        return None, str(e)
    except Exception as e:   # a broken settings file or registry: off, with the type only
        return None, type(e).__name__


def text_allowed(tgt: dict, runtime: str | None, project: str) -> bool:
    """A candidate's last reply goes to the model only when privacy.reflection is include for its project (the project on
    its process record; wait() kept it only under the label at its folder then: step 3) and the point runs on the runtime
    that session uses, with no llm.points entry for it: the text goes nowhere it hasn't been. A registry that can't be read
    is no."""
    pins = (hubconf.get("llm") or {}).get("points") or {}
    if POINT in pins or not tgt or not runtime or tgt.get("impl") != runtime:
        return False
    private = hubconf.reflection_private()
    return private is not None and project not in private


def prompt(project: str, letter: dict, cands: list[dict]) -> str:
    """The point's prompt: the platform's template with the data as one JSON block. A letter's text can't close it: JSON
    escapes its quotes, and every < is written \\u003c, so no tag is closed either. letter: {from, type, text}; each candidate:
    {session, folder?, idle_minutes?, doing?, last_reply?}."""
    import prompts
    data = {"letter": {"from": letter.get("from") or "?", "type": letter.get("type") or "?",
                       "text": str(letter.get("text") or "")[:LETTER_CHARS]},
            "sessions": [{k: (str(v)[-LAST_CHARS:] if k == "last_reply" else v) for k, v in c.items() if v is not None}
                         for c in cands[:MAX_CANDIDATES]]}
    block = json.dumps(data, ensure_ascii=False, indent=1).replace("<", "\\u003c")
    return prompts.render(POINT, project=project, data=block)


def start(letter_id: str, project: str, text: str, cands: list[str]) -> bool:
    """Start the call for this letter in the background; whether it started. Either way the letter has had its one call: it
    is marked before the start, so a dispatch killed in between leaves a letter asked with no answer (recency after WAIT_S),
    never a second call; a start that failed is done at once, and recency picks."""
    k = key(letter_id)
    if not hubconf.session_file(k, ANSWER):
        return False
    answer_file(letter_id).unlink(missing_ok=True)
    mark(letter_id, project)
    try:
        p = subprocess.Popen([sys.executable, str(pathlib.Path(__file__).resolve())], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, text=True)
        p.stdin.write(json.dumps({"key": k, "prompt": text, "cands": list(cands)}, ensure_ascii=False))
        p.stdin.close()
    except (OSError, ValueError):
        mark(letter_id, project, done=True)
        return False
    return True


def call(data: dict) -> None:
    """The call's process: ask, check, write an id from the list or nothing. Bounded by CALL_TIMEOUT_S whatever the
    runtime does: then it writes nothing usable and ends its process group (the runtime's process with it)."""
    k, cands = data.get("key"), data.get("cands")
    if not isinstance(k, str) or not isinstance(cands, list):
        return

    def out_of_time(_sig, _frame):
        try:
            write_answer(k, "")
        finally:
            os.killpg(os.getpgid(0), signal.SIGKILL)
    signal.signal(signal.SIGALRM, out_of_time)
    signal.alarm(CALL_TIMEOUT_S)
    import llm
    got = llm.answer(POINT, str(data.get("prompt") or ""))
    sid = got.get("session") if isinstance(got, dict) else None
    signal.alarm(0)
    write_answer(k, sid if isinstance(sid, str) and sid in [c for c in cands if isinstance(c, str)] else "")


def main() -> int:
    try:
        data = json.loads(sys.stdin.read())
    except ValueError:
        return 1
    call(data if isinstance(data, dict) else {})
    return 0


if __name__ == "__main__":
    sys.exit(main())
