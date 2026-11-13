#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Each version of a coding agent the user's sessions run is checked when first seen (docs/design/runtime-versions.md
section 6). Platform job brunna/session-versions, every scheduler tick, in the background.

  session_versions.py [--runtime <name>]

For each runtime the user opens sessions with (runtime_ports.installed) that has the distribution port:
1. What is seen. The versions on the live session records first, then those closed sessions left (<session>.version)
   in the last SEEN_DAYS days, as their hooks read them from the session's process (runtime_ports.record_session), and
   the versions that wrote the leases' sessions (lease-resume resumes one with current). Never a transcript's word: a
   sandboxed session can write its transcripts. Kept in <state>/session-versions/<runtime>.json
   (when each was last seen, live or not, Desktop's builds of it); each session copy in the store is told when a
   session of its version was last seen (runtime_store.seen), which keeps it.
2. Hook payloads the platform couldn't read (hub-event.log's unusable= lines) of an event letters depend on
   (MAIL_EVENTS), UNUSABLE_ALERT or more of one event and reason from one version in SEEN_DAYS days: an alert keyed by
   event and reason, and doctor's row.
3. Each version with no session result: checked against the vendor's published list (the runtime's published()); not
   published, or not installable → not-installable, tried again after RETRY_DAYS. Else installed exactly into the store
   as a session copy (never promoted by this; runtime_store), at most INSTALLS_PER_DAY a day per runtime, and its
   session-side checks (Check.side "session") run against it, paired with the platform's current copy, under section
   3a's login rule (the conformance runner's world). Recorded as <version>.session.json as each finishes
   (conformance.record_session). A result paired with another copy than current has its sessions-fork run again; one
   with an error has those checks run again once a day. A safety check failed: an alert per version and check, which
   ends once the version isn't seen any more.
4. The store's session copies pruned (runtime_store.prune).
Cheap work first: a run that installs and checks takes long (control alone can take hours), and the scheduler starts no
second copy while one runs.

What reads the results: runtime_ports.gated (a sandboxed project's session whose version failed a safety check gets no
letters, wakes, routes, forks or resumes), runtime_ports.fork_refusal and the Claude adapter's listing (session-status),
and summary() for `brunna runtimes` and doctor. From a linked worktree of the hub the job refuses: the store and the
results are the machine's.
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402
import runtime_ports  # noqa: E402

_ = i18n.domain(__file__)
SEEN_DAYS = 7
RETRY_DAYS = 7
INSTALLS_PER_DAY = 3
UNUSABLE_ALERT = 3
LOG_TAIL = 4 << 20   # how much of hub-event.log is read for unusable payloads
MAIL_EVENTS = ("session_start", "prompt", "tool_used")   # the events whose handlers deliver letters
_UNUSABLE = re.compile(r"^(\S+) (\S+) .* unusable=(\S+) runtime=(\S+) version=(\S+)$")


def usage() -> str:
    return _("usage: session_versions.py [--runtime <name>]")


def state_file(runtime: str) -> pathlib.Path:
    return hubconf.STATE / "session-versions" / f"{runtime}.json"


def load(runtime: str) -> dict:
    try:
        got = json.loads(state_file(runtime).read_text())
    except (OSError, ValueError):
        got = None
    return got if isinstance(got, dict) else {}


def save(runtime: str, st: dict) -> None:
    f = state_file(runtime)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(st, indent=1, sort_keys=True) + "\n")
    tmp.replace(f)


def _now() -> datetime.datetime:
    return datetime.datetime.now().astimezone()


def _iso(t: datetime.datetime) -> str:
    return t.isoformat(timespec="seconds")


def _age_days(when: str | None, now: datetime.datetime) -> float:
    try:
        return (now - datetime.datetime.fromisoformat(str(when))).total_seconds() / 86400
    except (TypeError, ValueError):
        return float("inf")


def runtimes() -> list[str]:
    """The runtimes the user opens sessions with that have versions (the distribution port)."""
    return [n for n in runtime_ports.installed() if "distribution" in runtime_ports.capabilities(n)]


# ---------- 1. what is seen ----------

def seen_now(runtime: str, now: datetime.datetime) -> list[tuple[str, str | None, bool]]:
    """(version, build, live) of this runtime's sessions: the live records first, then the closed sessions' versions of
    the last SEEN_DAYS days. A live record with no version yet (written before versions were read) is left for its next
    hook, which reads it. Raises runtime_ports.SessionsUnknown when the live sessions can't be looked up."""
    out, live_ids = [], set()
    for sid, rec in runtime_ports.open_sessions().items():
        if rec.get("runtime") != runtime or not isinstance(rec.get("version"), str):
            continue
        live_ids.add(sid)
        out.append((rec["version"], rec.get("build") if isinstance(rec.get("build"), str) else None, True))
    d = hubconf.session_addresses_dir()
    try:
        files = sorted(d.glob(f"*.{runtime_ports.SESSION_VERSION}"), key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError:
        files = []
    for f in files:
        sid = hubconf.session_of_file(f.name)
        try:
            if not sid or sid in live_ids or (now.timestamp() - f.stat().st_mtime) > SEEN_DAYS * 86400:
                continue
        except OSError:
            continue
        v = runtime_ports.session_version(sid, runtime_ports._json_file(f))
        if v and v.get("runtime") == runtime:
            out.append((v["version"], v.get("build"), False))
    if runtime == runtime_ports.default_name():   # a lease's session, which lease-resume resumes with current
        leases = runtime_ports._json_file(hubconf.STATE / "lease-runner" / "leases.json") or {}
        for lease in leases.values():
            if isinstance(lease, dict) and isinstance(lease.get("version"), str) \
                    and _age_days(lease.get("last_activity"), now) <= SEEN_DAYS:
                out.append((lease["version"], None, False))
    return out


def note_seen(runtime: str, st: dict, seen: list, now: datetime.datetime) -> list[str]:
    """Record what was seen; the versions to check, live ones first, each once (never UNREADABLE)."""
    import runtime_store
    stamp = _iso(now)
    known = st.setdefault("seen", {})
    order = []
    for version, build, live in seen:
        if version != runtime_ports.UNREADABLE and not runtime_ports.checkable_version(version):
            continue
        e = known.setdefault(version, {})
        e["last"] = stamp
        if live:
            e["live"] = stamp
        if build:
            e.setdefault("builds", {})[build] = stamp
        if runtime_ports.checkable_version(version) and version not in order:
            order.append(version)
    for version in list(known):
        if _age_days(known[version].get("last"), now) > SEEN_DAYS:
            del known[version]
    for version in order:
        try:
            runtime_store.seen(runtime, version, stamp)
        except (OSError, ValueError):
            pass
    return order


# ---------- 2. unusable hook payloads ----------

def unusable_counts(runtime: str, now: datetime.datetime, log: pathlib.Path | None = None) -> dict:
    """{(event, reason, version): count} of this runtime's unusable payloads of MAIL_EVENTS in the last SEEN_DAYS days,
    from the tail of hub-event.log."""
    log = log or hubconf.LOGS / "hub-event.log"
    try:
        with open(log, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - LOG_TAIL))
            lines = fh.read().decode(errors="replace").splitlines()
    except OSError:
        return {}
    out: dict = {}
    for line in lines:
        m = _UNUSABLE.match(line)
        if not m or m[4] != runtime or m[2] not in MAIL_EVENTS or _age_days(m[1], now) > SEEN_DAYS:
            continue
        key = (m[2], m[3], m[5])
        out[key] = out.get(key, 0) + 1
    return out


def alert_unusable(runtime: str, st: dict, counts: dict) -> None:
    """An alert per event and reason whose payloads one version sent UNUSABLE_ALERT times or more; recorded for doctor."""
    import alert
    over: dict = {}
    for (event, reason, version), n in counts.items():
        if n >= UNUSABLE_ALERT:
            over.setdefault((event, reason), []).append((version, n))
    before = set(st.get("unusable") or {})
    st["unusable"] = {f"{e} {r}": {"versions": dict(sorted(v))} for (e, r), v in over.items()}
    for key in before | set(st["unusable"]):
        event, _sp, reason = key.partition(" ")
        got = st["unusable"].get(key)
        lines = [_("{runtime}: the platform couldn't read {event} hooks ({reason}) from version {versions}: letters may "
                   "not reach those sessions", runtime=runtime, event=event, reason=reason,
                   versions=", ".join(f"{v} ×{n}" for v, n in got["versions"].items()))] if got else []
        alert.once(f"session-unusable-{runtime}-{event}-{reason}", lines,
                   _("{runtime}: {event} hooks are read again", runtime=runtime, event=event),
                   ids=[f"{event}:{reason}"] if got else [])


# ---------- 3. the checks ----------

def _today() -> str:
    return datetime.date.today().isoformat()


def installs_left(st: dict) -> int:
    got = st.get("installs") or {}
    return INSTALLS_PER_DAY - (int(got.get("n") or 0) if got.get("date") == _today() else 0)


def _count_install(st: dict) -> None:
    got = st.get("installs") or {}
    st["installs"] = {"date": _today(), "n": (int(got.get("n") or 0) if got.get("date") == _today() else 0) + 1}


def what_to_run(runtime: str, version: str, current: str | None) -> list[str] | None:
    """Which session-side checks this version needs now: all of them with no result; sessions-fork alone when the
    result was paired with another copy than current; those that ended in an error, once a day; None: nothing."""
    import conformance
    doc = runtime_ports.session_result(runtime, version)
    every = conformance.session_checks(runtime)
    if not doc:
        return every
    todo = []
    if doc.get("current") != current and runtime_ports.FORK_CHECK in every:
        todo.append(runtime_ports.FORK_CHECK)
    if doc.get("retried") != _today():
        todo += [n for n, c in (doc.get("checks") or {}).items() if c.get("state") == "error" and n in every]
    return list(dict.fromkeys(todo)) or None


def check_version(runtime: str, mod, version: str, st: dict, published: list[str] | None) -> str:
    """Install (when needed) and check one version; what happened, in a line ("" when nothing was done). published: the
    vendor's versions, None when they can't be told now (nothing is installed or marked then). Raises RuntimeError when
    the checks can't run."""
    import conformance
    import runtime_store
    cur = runtime_store.current(runtime)
    todo = what_to_run(runtime, version, cur.version if cur else None)
    if todo is None:
        return ""
    if not runtime_store.installed(runtime, version):
        bad = (st.get("not_installable") or {}).get(version)
        if bad and _age_days(bad.get("at"), _now()) < RETRY_DAYS:
            return ""
        if published is None:   # the vendor can't be asked now: nothing is marked, the next run asks again
            return ""
        if installs_left(st) <= 0:
            return _("{runtime} {version}: waits for tomorrow's installs ({n} a day)", runtime=runtime, version=version,
                     n=INSTALLS_PER_DAY)
        why = None if version in published else _("the vendor doesn't publish it")
        if why is None:
            _count_install(st)
            try:
                runtime_store.install(runtime, version, mod, kind=runtime_store.SESSION)
                runtime_store.seen(runtime, version, _iso(_now()))
            except RuntimeError as e:
                why = str(e)
        if why is not None:
            st.setdefault("not_installable", {})[version] = {"at": _iso(_now()), "why": why}
            return _("{runtime} {version}: not installable ({why}); tried again in {days} days", runtime=runtime,
                     version=version, why=why, days=RETRY_DAYS)
    (st.get("not_installable") or {}).pop(version, None)
    exe = runtime_store.root(runtime) / version / runtime_store.installed(runtime, version)["exe"]
    with runtime_store.hold_version(runtime, version):
        doc = conformance.run(runtime, str(exe), todo, pair=str(cur.exe) if cur else None)
    old = runtime_ports.session_result(runtime, version)
    if old and set(todo) != set(conformance.session_checks(runtime)):   # a part run: merged into what is there
        doc = {**old, "checks": {**(old.get("checks") or {}), **doc["checks"]}, "at": doc["at"],
               "binary": doc["binary"], "digest": doc["digest"]}
    doc["retried"] = _today()
    conformance.record_session(doc, cur.version if cur else None)
    alert_failed(runtime, version, doc)
    return _("{runtime} {version}: session-side checks {verdict}", runtime=runtime, version=version,
             verdict=conformance.verdict(doc))


def alert_failed(runtime: str, version: str, doc: dict | None) -> None:
    """An alert per version and safety check it failed (none: resolved)."""
    import alert
    failed = runtime_ports.failed_checks(doc, "safety")
    checks = (doc or {}).get("checks") or {}
    alert.once(f"session-version-{runtime}-{version}",
               [_("{runtime} {version}, which your sessions run, failed the platform's {check} check: {reason}. The "
                  "platform delivers no letters to its sessions in sandboxed projects and wakes them for none; in other "
                  "projects nothing is held back. Update the tool and open new sessions",
                  runtime=runtime, version=version, check=n, reason=checks[n].get("reason") or "-") for n in failed],
               _("{runtime} {version}: no failed check now", runtime=runtime, version=version), ids=failed)


def run_runtime(runtime: str, now: datetime.datetime | None = None) -> list[str]:
    """One runtime's turn (steps 1–4 above); the lines to print."""
    import runtime_store
    now = now or _now()
    mod = runtime_ports._module(runtime)
    st = load(runtime)
    out = []
    try:
        todo = note_seen(runtime, st, seen_now(runtime, now), now)
    except runtime_ports.SessionsUnknown as e:
        return [_("{runtime}: which sessions run can't be read ({error}); nothing checked", runtime=runtime, error=e)]
    alert_unusable(runtime, st, unusable_counts(runtime, now))
    for version in list(st.get("alerted") or []):   # versions no longer seen: their alert ends
        if version not in st["seen"]:
            alert_failed(runtime, version, None)
    st["alerted"] = sorted(v for v in st["seen"] if runtime_ports.failed_checks(runtime_ports.session_result(runtime, v),
                                                                                 "safety"))
    save(runtime, st)
    published, asked = None, False
    for version in todo:
        cur = runtime_store.current(runtime)
        if what_to_run(runtime, version, cur.version if cur else None) is None:
            continue
        if not asked and not runtime_store.installed(runtime, version):
            asked = True   # once a run, and only when a version has to be installed
            try:
                published = list(mod.published())
            except (RuntimeError, AttributeError, TypeError) as e:
                out.append(_("{runtime}: the published versions can't be told: {error}", runtime=runtime, error=e))
        try:
            line = check_version(runtime, mod, version, st, published)
        except RuntimeError as e:   # the checks couldn't run (the login, the world): said, tried at the next run
            line = _("{runtime} {version}: the checks could not run: {error}", runtime=runtime, version=version, error=e)
        save(runtime, st)
        if line:
            out.append(line)
    st["alerted"] = sorted(v for v in st["seen"] if runtime_ports.failed_checks(runtime_ports.session_result(runtime, v),
                                                                                 "safety"))
    save(runtime, st)
    gone = runtime_store.prune(runtime)
    if gone:
        out.append(_("{runtime}: removed {versions}", runtime=runtime, versions=", ".join(gone)))
    return out


# ---------- what is said (brunna runtimes, doctor) ----------

def summary(runtime: str) -> list[dict]:
    """Each version seen in the last SEEN_DAYS days: {version, state, checks, builds, live}; state one of passed,
    pending, unreadable, not-installable, failed (checks: the failed ones, safety first)."""
    st, now = load(runtime), _now()
    rows = []
    for version, e in sorted((st.get("seen") or {}).items()):
        if _age_days(e.get("last"), now) > SEEN_DAYS:
            continue
        row = {"version": version, "builds": sorted(e.get("builds") or {}), "live": bool(e.get("live")) and
               _age_days(e.get("live"), now) < 1, "checks": [], "errors": []}
        doc = runtime_ports.session_result(runtime, version) if runtime_ports.checkable_version(version) else None
        if version == runtime_ports.UNREADABLE:
            row["state"] = "unreadable"
        elif version in (st.get("not_installable") or {}) and not doc:
            row["state"], row["why"] = "not-installable", st["not_installable"][version].get("why") or ""
        elif not doc:
            row["state"] = "pending"
        else:
            row["checks"] = runtime_ports.failed_checks(doc, "safety") + [
                n for n in runtime_ports.failed_checks(doc) if n not in runtime_ports.failed_checks(doc, "safety")]
            row["errors"] = sorted(n for n, c in (doc.get("checks") or {}).items() if c.get("state") == "error")
            row["safety"] = bool(runtime_ports.failed_checks(doc, "safety"))
            row["state"] = "failed" if row["checks"] else "pending" if row["errors"] else "passed"
        rows.append(row)
    return rows


def unusable_rows(runtime: str) -> list[str]:
    return [_("{runtime}: {event} hooks the platform couldn't read ({reason}) from {versions}", runtime=runtime,
              event=key.partition(" ")[0], reason=key.partition(" ")[2],
              versions=", ".join(f"{v} ×{n}" for v, n in (got.get("versions") or {}).items()))
            for key, got in sorted((load(runtime).get("unusable") or {}).items())]


def describe(runtime: str, row: dict) -> str:
    builds = _(" (Desktop: {builds} {builds:build|builds}, {list})", builds=len(row["builds"]),
                                     list=", ".join(row["builds"])) \
        if row["builds"] else ""
    head = f"{runtime} {row['version']}{builds}"
    if row["state"] == "passed":
        return _("{head}: passed its session-side checks", head=head)
    if row["state"] == "failed":
        return _("{head}: failed {checks}", head=head, checks=", ".join(row["checks"]))
    if row["state"] == "not-installable":
        return _("{head}: not installable ({why}), so not checked; tried again weekly", head=head, why=row.get("why") or "-")
    if row["state"] == "unreadable":
        return _("{runtime}: some sessions run a version that can't be read from their process (pending: nothing is "
                 "held back for them)", runtime=runtime)
    if row["errors"]:
        return _("{head}: pending, {checks} ended in an error (run again daily)", head=head, checks=", ".join(row["errors"]))
    return _("{head}: pending (checked by brunna/session-versions)", head=head)


def doctor_rows(runtime: str) -> list[tuple[str, str]]:
    """(level, text) for doctor: a failed safety check is a warning (its sandboxed projects' sessions get no letters or
    wakes; others hold nothing back), anything else not passed is said."""
    out = []
    for row in summary(runtime):
        if row["state"] == "passed":
            continue
        level = "warn" if row["state"] == "failed" and row.get("safety") else "info"
        text = describe(runtime, row)
        if row["state"] == "failed" and row.get("safety"):
            text += _(": its sessions in sandboxed projects get no letters or wakes from the platform")
        out.append((level, text))
    out += [("warn", line) for line in unusable_rows(runtime)]
    return out


def main(argv: list[str]) -> int:
    if argv in (["-h"], ["--help"]):
        print(usage())
        return 0
    if not hubconf.known_args(argv, set(), {"--runtime"}):
        print(usage(), file=sys.stderr)
        return 2
    if hubconf.worktree_lookup():
        print(_("session_versions: {root} is a linked worktree of the hub; the store and the results are changed only "
                "from the main checkout ({main})", root=hubconf.ROOT, main=hubconf.MAIN), file=sys.stderr)
        return 1
    only = argv[argv.index("--runtime") + 1] if "--runtime" in argv else None
    ok = True
    for runtime in runtimes():
        if only and runtime != only:
            continue
        try:
            for line in run_runtime(runtime):
                print(line)
        except Exception as e:   # one runtime's breakage never stops the others; said
            ok = False
            print(_("{runtime}: session_versions broke: {error}", runtime=runtime, error=repr(e)), file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
