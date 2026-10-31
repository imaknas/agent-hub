"""Applier: the scheduler's job list (appliers.py). The scheduler reads the registry itself on every tick, so there
is nothing to "apply"; this applier only reports jobs added, removed and changed (plan), and apply records the current
list as "applied" (.state/config-applied/jobs.json) for the next plan to compare against."""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402
from appliers import Change  # noqa: E402

_ = i18n.domain(__file__)

EXTERNAL = False
SNAPSHOT = hubconf.STATE / "config-applied" / "jobs.json"
KEYS = ("time", "every_minutes", "trigger", "days_of_week", "days_of_month", "months", "end_date", "between", "after", "command",
        "background", "max_attempts_per_day", "enabled")


def jobs(index=None) -> dict[str, dict]:
    """Name -> the fields the scheduler sees. Given an index, only the registry is looked at (tests); otherwise the same
    as the scheduler (schedule.yaml + registry)."""
    if index is None:
        import scheduler
        js, _info = scheduler.all_jobs()
    else:
        import registry
        js, _skipped, _renames = registry.scheduler_jobs(index, set())
    out = {j["name"]: {k: j[k] for k in KEYS if k in j} for j in js}
    # values like dates become strings so they compare with the stored snapshot
    return json.loads(json.dumps(out, default=str))


def _diff(cur: dict[str, dict], old: dict[str, dict]) -> list[Change]:
    out = []
    for n in sorted(set(cur) | set(old)):
        if n not in old:
            out.append(Change("add", f"job:{n}", _when(cur[n])))
        elif n not in cur:
            out.append(Change("remove", f"job:{n}"))
        elif cur[n] != old[n]:
            keys = sorted(k for k in set(cur[n]) | set(old[n]) if cur[n].get(k) != old[n].get(k))
            out.append(Change("update", f"job:{n}", _("changed {keys}", keys=_(", ").join(keys))))
    return out


def _when(j: dict) -> str:
    w = _("at {time}", time=j["time"]) if j.get("time") else \
        _("every {n} {n:minute|minutes}", n=j['every_minutes']) if j.get("every_minutes") else \
        _("on {events}", events=", ".join(j["trigger"]["events"])) if j.get("trigger") else ""
    return w + (_(", after {jobs}", jobs=', '.join(j['after'])) if j.get("after") else "")


def _old() -> dict[str, dict]:
    try:
        return json.loads(SNAPSHOT.read_text())
    except (OSError, ValueError):
        return {}


def plan(index=None) -> list[Change]:
    return _diff(jobs(index), _old())


def apply(index=None) -> list[Change]:
    cur = jobs(index)
    changes = _diff(cur, _old())
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(cur, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    return changes
