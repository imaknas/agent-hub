"""Event-triggered jobs (docs/design/event-jobs.md): which bus events a job with `trigger:` is waiting for, whether it is
due, and what its run receives. The scheduler, the status page and schedulectl all ask here, so they agree.

A job dict (from registry.scheduler_jobs) carries
  trigger: {events: [type…], projects: [name…|*], sources: [glob…], where: {key: value|[values]}, debounce_minutes: n}
(the YAML key is `trigger`, not `on`: YAML 1.1 reads a bare `on` as true)
and the scheduler's state carries its cursor, <job>.event_seq = the highest bus seq this job has consumed.

An event belongs to its to_project, else to its source. A payload's `chain` is the list of scheduled jobs whose runs
caused it, and `depth` how many scheduled runs deep it is (both set by hubbus.publish from AGENT_HUB_JOB_CHAIN /
AGENT_HUB_JOB_DEPTH); an event whose chain holds the job, or whose depth is MAX_DEPTH, never triggers it.

Standard library only; reads the bus read-only.
"""
from __future__ import annotations

import datetime
import fnmatch
import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)

MAX_DEPTH = 4            # a root run plus three event-triggered generations
DEFAULT_CAP = 12         # max_attempts_per_day when an event job sets none
ENV_LIMIT = 64 * 1024    # AGENT_HUB_EVENTS stays under this many bytes (oldest events first; the rest wait)
QUIET_CEILING = 4        # with debounce d, the oldest pending event waits at most QUIET_CEILING × d
CHAIN_ENV = "AGENT_HUB_JOB_CHAIN"
DEPTH_ENV = "AGENT_HUB_JOB_DEPTH"
JOB_ENV = "AGENT_HUB_JOB"
EVENTS_ENV = "AGENT_HUB_EVENTS"


def belongs(ev: dict) -> str | None:
    return ev.get("to_project") or ev.get("source")


def depth_of(ev: dict) -> int:
    """How many scheduled runs deep the event is (an event from before depth existed: its chain's length)."""
    d = (ev.get("payload") or {}).get("depth")
    return d if isinstance(d, int) and not isinstance(d, bool) and d >= 0 else len(chain_of(ev))


def chain_of(ev: dict) -> list[str]:
    c = (ev.get("payload") or {}).get("chain")
    return [x for x in c if isinstance(x, str)] if isinstance(c, list) else []


def _lookup(payload: dict, key: str):
    cur = payload
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def _one(actual, want) -> bool:
    if isinstance(want, str) and isinstance(actual, str) and any(ch in want for ch in "*?["):
        return fnmatch.fnmatchcase(actual, want)
    return type(actual) is type(want) and actual == want


def where_matches(where: dict, payload: dict) -> bool:
    for key, want in (where or {}).items():
        found, actual = _lookup(payload, key)
        if not found or not any(_one(actual, w) for w in (want if isinstance(want, list) else [want])):
            return False
    return True


def classify(job: dict, ev: dict) -> str | None:
    """'match', 'loop' (would match but the chain forbids it) or None."""
    on = job["trigger"]
    if ev["type"] not in on["events"]:
        return None
    projects = on.get("projects") or [job.get("project")]
    # an ordinary project's job matches only events addressed to it (to_project); the platform's also match by
    # source (config.applied, platform job.failed), whose names a project could otherwise take
    owner = belongs(ev) if job.get("platform") else ev.get("to_project")
    if "*" not in projects and owner not in projects:
        return None
    if on.get("sources") and not any(fnmatch.fnmatchcase(ev.get("source") or "", s) for s in on["sources"]):
        return None
    if not where_matches(on.get("where") or {}, ev.get("payload") or {}):
        return None
    chain = chain_of(ev)
    if job["name"] in chain or depth_of(ev) >= MAX_DEPTH:
        return "loop"
    return "match"


def _row(r) -> dict:
    try:
        payload = json.loads(r[7] or "{}")
    except ValueError:
        payload = {}
    return {"seq": r[0], "id": r[1], "ts": r[2], "type": r[3], "source": r[4], "to_project": r[5], "ref": r[6],
            "payload": payload if isinstance(payload, dict) else {}}


def read(cursor: int, types: list[str], db: pathlib.Path | None = None) -> tuple[int, str | None, list[dict]]:
    """(head, the bus file's generation, the events of these types with cursor < seq <= head), in one read
    transaction. No bus yet: (0, None, [])."""
    db = db or hubconf.BUS_DIR / "bus.db"   # the path only: hubconf.bus_db() may migrate (a write)
    if not pathlib.Path(db).exists():
        return 0, None, []
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    try:
        c.execute("BEGIN")
        head = c.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
        try:
            gen = (c.execute("SELECT value FROM meta WHERE key='generation'").fetchone() or [None])[0]
        except sqlite3.OperationalError:   # a bus from before meta existed (hubbus adds it on its next write)
            gen = None
        rows = []
        if types and head > cursor:
            q = ",".join("?" * len(types))
            rows = [_row(r) for r in c.execute(
                f"SELECT seq, id, ts, type, source, to_project, ref, payload FROM events WHERE seq > ? AND seq <= ? "
                f"AND type IN ({q}) ORDER BY seq", (cursor, head, *types))]
        c.execute("COMMIT")
        return head, gen, rows
    finally:
        c.close()


def pending(job: dict, cursor: int, db: pathlib.Path | None = None) -> dict:
    """{head, generation, events: the matching events after the cursor, held: the ones the chain holds back}."""
    head, gen, rows = read(cursor, job["trigger"]["events"], db)
    events, held = [], []
    for ev in rows:
        k = classify(job, ev)
        if k == "match":
            events.append(ev)
        elif k == "loop":
            held.append(ev)
    return {"head": head, "generation": gen, "events": events, "held": held}


def local(ts: str) -> datetime.datetime:
    """An event's ts (timezone-aware) as naive local time, the scheduler's clock. A ts that can't be read (a row written
    by hand) reads as the epoch's start, never an exception: the status page and schedulectl must not break on it."""
    try:
        t = datetime.datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return datetime.datetime(1970, 1, 1)
    return t.astimezone().replace(tzinfo=None) if t.tzinfo else t


def age(ev: dict, now: datetime.datetime) -> datetime.timedelta:
    """How long ago the event was published; a ts in the future (a wrong clock, a hand-written row) counts as now."""
    return max(now - local(ev.get("ts")), datetime.timedelta(0))   # an unreadable ts: old, so it can't stall the job


def quiet(job: dict, events: list[dict], now: datetime.datetime) -> bool:
    """Debounce: the newest pending event is at least debounce old, or the oldest QUIET_CEILING × debounce."""
    if not events:
        return False
    d = datetime.timedelta(minutes=job["trigger"].get("debounce_minutes") or 0)
    return age(events[-1], now) >= d or age(events[0], now) >= QUIET_CEILING * d


def run_chain(name: str, events: list[dict]) -> list[str]:
    """The chain of a run that takes these events: their chains joined (order kept), then the job."""
    out: list[str] = []
    for ev in events:
        for j in chain_of(ev):
            if j not in out:
                out.append(j)
    return [*[j for j in out if j != name], name]


def take(got: dict) -> dict:
    """The batch one run takes from pending(): the oldest events whose JSON fits in ENV_LIMIT bytes (always at least
    one), and upto = how far the cursor may move when the run succeeds: the head when every event fits, else the seq
    of the last one taken, so the rest are the next run's (event-jobs.md section 4)."""
    kept: list[dict] = []
    size = len(json.dumps({"events": []}))
    for ev in got["events"]:
        n = len(json.dumps(ev, ensure_ascii=False).encode()) + 2
        if not kept and size + n > ENV_LIMIT:   # one event alone too big: its envelope, the payload left out
            ev = {**ev, "payload": {"v": 1, "truncated": True, "chain": chain_of(ev), "depth": depth_of(ev)}}
            n = len(json.dumps(ev, ensure_ascii=False).encode()) + 2
        if kept and size + n > ENV_LIMIT:
            break
        kept.append(ev)
        size += n
    upto = got["head"] if len(kept) == len(got["events"]) else kept[-1]["seq"]
    return {"events": kept, "upto": upto, "left": len(got["events"]) - len(kept)}


def batch_env(events: list[dict]) -> str:
    """AGENT_HUB_EVENTS: {events: [...]}, oldest first (take() keeps it under ENV_LIMIT)."""
    return json.dumps({"events": events}, ensure_ascii=False)


def run_depth(events: list[dict]) -> int:
    """A run's depth: one more than its deepest event (a clock run: 1)."""
    return 1 + max((depth_of(ev) for ev in events), default=0)


def run_env(name: str, chain: list[str], depth: int, events: list[dict] | None) -> dict[str, str]:
    """The variables every scheduled run gets; AGENT_HUB_EVENTS only for event jobs (None = not an event job)."""
    env = {JOB_ENV: name, CHAIN_ENV: json.dumps(chain, ensure_ascii=False), DEPTH_ENV: str(depth)}
    if events is not None:
        env[EVENTS_ENV] = batch_env(events)
    return env


def depth_from_env(environ) -> int:
    try:
        return max(int(environ.get(DEPTH_ENV) or 0), 0)
    except ValueError:
        return 0


def chain_from_env(environ) -> list[str]:
    try:
        c = json.loads(environ.get(CHAIN_ENV) or "[]")
    except ValueError:
        return []
    return [x for x in c if isinstance(x, str)] if isinstance(c, list) else []


# ---------- the phrases the status page and schedulectl show ----------

def describe(job: dict) -> str:
    """The "when" of an event job: "on merge.done, merge.failed (quiet 10 min, from any project)"."""
    on = job["trigger"]
    extras = []
    if on.get("debounce_minutes"):
        extras.append(_("quiet {n} min", n=on["debounce_minutes"]))
    own = job.get("project")
    others = [p for p in on.get("projects") or [] if p != own and p not in hubconf.former_names(own or "")]
    if "*" in others:
        extras.append(_("from any project"))
    elif others:
        extras.append(_("from {projects}", projects=", ".join(others)))
    if on.get("where"):
        extras.append(", ".join(f"{k}={v}" for k, v in on["where"].items()))
    text = _("on {events}", events=", ".join(on["events"]))
    return _("{text} ({extras})", text=text, extras="; ".join(extras)) if extras else text


def waiting(job: dict, state: dict, now: datetime.datetime, db: pathlib.Path | None = None) -> str:
    """The "next" of an event job: nothing waiting, or how many events wait since when (and why, when capped)."""
    name = job["name"]
    if f"{name}.event_seq" not in state:
        return _("on the next event (starts counting at the next tick)")
    try:
        got = pending(job, state[f"{name}.event_seq"], db)
    except Exception as e:  # the page must not break on an unreadable bus
        return _("the bus can't be read: {error}", error=e)
    if not got["events"]:
        return _("on the next event")
    since = local(got["events"][0]["ts"])
    text = _("{n} {n:event|events} waiting since {since:%m/%d %H:%M}", n=len(got["events"]), since=since)
    att = state.get(f"{name}.attempts") or {}
    cap = job.get("max_attempts_per_day")
    if cap is not None and att.get("date") == now.strftime("%Y-%m-%d") and att.get("count", 0) >= cap:
        text = _("{text}; today's {cap} {cap:run is|runs are} used up", text=text, cap=cap)
    return text


def stuck(job: dict, state: dict, now: datetime.datetime, db: pathlib.Path | None = None) -> datetime.datetime | None:
    """When the oldest event still waiting is over a day old: its time (for the status page's alert), else None."""
    key = f"{job['name']}.event_seq"
    if key not in state:
        return None
    try:
        got = pending(job, state[key], db)
    except Exception:
        return None
    if got["events"] and age(got["events"][0], now) > datetime.timedelta(days=1):
        return local(got["events"][0]["ts"])
    return None


# ---------- the batch in an agent job's prompt (run-agent-job.sh) ----------

def prompt_with_events(prompt: str, environ) -> str:
    """The job's prompt, followed by the event batch (templates/prompts/event-batch.md) when the run has one.
    Raises ValueError on a batch that can't be read: the run must fail, not go on without its events.
    The events sit between delimiters with a random token, so a payload can't fake their end."""
    raw = environ.get(EVENTS_ENV)
    if not raw:
        return prompt
    events = json.loads(raw).get("events")
    if not isinstance(events, list):
        raise ValueError(f"{EVENTS_ENV} has no event list")
    import prompts
    import secrets
    mark = f"events-{secrets.token_hex(6)}"
    batch = prompts.render("event-batch", count=len(events), mark=mark,
                           events=json.dumps(events, ensure_ascii=False, indent=1))
    return f"{prompt.rstrip()}\n\n{batch}\n"


def main(argv: list[str]) -> int:
    """event_jobs.py prompt <prompt file> <out file>: write the prompt with the batch (if any) for llm.py."""
    import os
    if len(argv) != 3 or argv[0] != "prompt":
        print("usage: event_jobs.py prompt <prompt file> <out file>", file=sys.stderr)
        return 2
    try:
        text = prompt_with_events(pathlib.Path(argv[1]).read_text(), os.environ)
    except (ValueError, AttributeError, KeyError, OSError) as e:
        print(_("the event batch can't go into the prompt: {error!r}", error=e), file=sys.stderr)
        return 1
    pathlib.Path(argv[2]).write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
