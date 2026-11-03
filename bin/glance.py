#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""brunna glance [--json]: what needs the owner and whether the platform is healthy, in one look (docs/design/herdr.md,
contract contracts/glance.v1.schema.json). For status bars and other clients: the herdr mod (mods/brunna/), an iTerm2
status bar component, a menu-bar script.

It reads and takes no write lock:
- the status page's last tick (.state/status/glance.json, written by gen-status-page.py): the alerts and the owner's
  open items, up to one tick old (their time is in as_of);
- live: the open letters per project (the bus opened read-only; every project, whatever folder this runs in), the
  scheduler's heartbeat and its failing jobs.
An instance that isn't set up gives {"v": 1, "set_up": false} and exit 0, touching nothing (opening the bus would make
one). Exit 0 whenever it could answer; problems are in the answer, not the exit code.
"""
from __future__ import annotations

import datetime
import importlib.machinery
import importlib.util
import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)
STATE_DIR = hubconf.STATE   # the main checkout's .state, as the scheduler and the status page write it
SNAPSHOT = STATE_DIR / "status" / "glance.json"
HEARTBEAT = STATE_DIR / "scheduler-heartbeat"
FAILING = STATE_DIR / "scheduler-failing.json"
STALE_TICK = datetime.timedelta(minutes=40)   # the status page's own threshold for a stopped scheduler
LOCAL_PAGE = "http://127.0.0.1:18790/"


def _hubbus():
    loader = importlib.machinery.SourceFileLoader("hubbus_glance", str(hubconf.ROOT / "bin" / "hubbus"))
    spec = importlib.util.spec_from_loader("hubbus_glance", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def _iso(t: datetime.datetime | None) -> str | None:
    return t.astimezone().isoformat(timespec="seconds") if t else None


def mail() -> dict:
    """Open letters per project, read-only: no schema run, no move of an old bus file (hubbus.db() would)."""
    db = hubconf.BUS_DIR / "bus.db"
    if not db.exists():
        return {"total": 0, "projects": []}
    hb = _hubbus()
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    try:
        out = []
        for p in sorted({*hubconf.projects(), hubconf.hub_project()}):
            rows = [r for r in hb.addressed_to(c, p) if r["type"] == "message"]
            if rows:
                out.append({"project": p, "open": len(rows), "oldest": min(r["ts"] for r in rows)})
    finally:
        c.close()
    return {"total": sum(x["open"] for x in out), "projects": out}


def scheduler(now: datetime.datetime) -> dict:
    tick = datetime.datetime.fromtimestamp(HEARTBEAT.stat().st_mtime) if HEARTBEAT.exists() else None
    return {"last_tick": _iso(tick), "alive": bool(tick) and now - tick <= STALE_TICK}


def jobs_failed() -> list[dict]:
    try:
        d = json.loads(FAILING.read_text())
    except (OSError, ValueError):
        return []
    return [{"job": name, "since": (v or {}).get("since") or ""} for name, v in sorted(d.items())]


def status_page() -> dict:
    url = hubconf.get("status_page.url")
    # without a setting the page listens on the hub machine's localhost: a client elsewhere must not offer it as a link
    return {"url": url, "local_to": None} if url else {"url": LOCAL_PAGE, "local_to": "hub"}


def glance() -> dict:
    now = datetime.datetime.now()
    if not hubconf.config_path().exists():
        return {"v": 1, "set_up": False}
    try:
        snap = json.loads(SNAPSHOT.read_text())
    except (OSError, ValueError):
        snap = None
    alerts = (snap or {}).get("alerts") or []
    out = {"v": 1, "set_up": True, "at": _iso(now), "instance": str(hubconf.instance_home() or hubconf.ROOT),
           "needs_you": (snap or {}).get("needs_you") or [],
           "mail": mail(), "scheduler": scheduler(now), "jobs_failed": jobs_failed(), "status_page": status_page()}
    sched_ok = out["scheduler"]["alive"]
    if snap is None:   # no tick has written one yet (or an older release): said, never guessed
        out["health"] = {"state": "unknown", "alerts": [], "as_of": None}
    else:
        bad = any(a.get("level") == "bad" for a in alerts) or not sched_ok
        out["health"] = {"state": "problem" if bad else "ok", "alerts": alerts, "as_of": snap.get("as_of")}
    if snap and snap.get("needs_you_error"):
        out["needs_you_error"] = snap["needs_you_error"]
    return out


def text(g: dict) -> str:
    if not g.get("set_up"):
        return _("brunna is not set up here (brunna init)")
    lines = []
    need = g["needs_you"]
    lines.append(_("Needs you: {n}", n=len(need)))
    lines += [f"  · {x['project']}: {x['text'][:100]}" for x in need[:10]]
    lines.append(_("Mail: {n} open", n=g["mail"]["total"]) + "".join(
        f" · {x['project']} {x['open']}" for x in g["mail"]["projects"]))
    h = g["health"]
    lines.append({"ok": _("Health: ok"), "problem": _("Health: problems"),
                  "unknown": _("Health: not known yet (the status page has not run)")}[h["state"]])
    lines += [f"  {'⚠️' if a['level'] == 'bad' else '🔔'} {a['text'][:140]}" for a in h["alerts"][:10]]
    if not g["scheduler"]["alive"]:
        lines.append(_("The scheduler has not ticked since {at}", at=g["scheduler"]["last_tick"] or "-"))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print(_(__doc__).strip())
        return 0
    g = glance()
    print(json.dumps(g, ensure_ascii=False, indent=1) if "--json" in argv else text(g))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
