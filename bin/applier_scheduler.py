"""套用者:排程的 job 清單(appliers.py)。排程器每個 tick 自己讀登錄,不需要「套用」;這個套用者只回報
job 的增減跟改動(plan),apply 把現在的清單記成「已套用」(.state/config-applied/jobs.json),下次 plan 跟它比。"""
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
KEYS = ("time", "every_minutes", "days_of_week", "days_of_month", "months", "end_date", "between", "after", "command",
        "background", "max_attempts_per_day", "enabled")


def jobs(index=None) -> dict[str, dict]:
    """名稱 → 排程看得到的欄位。給 index 就只看登錄(測試);不給就跟排程器一樣(schedule.yaml + 登錄)。"""
    if index is None:
        import scheduler
        js, _info = scheduler.all_jobs()
    else:
        import registry
        js, _skipped, _renames = registry.scheduler_jobs(index, set())
    out = {j["name"]: {k: j[k] for k in KEYS if k in j} for j in js}
    return json.loads(json.dumps(out, default=str))  # 日期之類的值變成字串,跟存起來的快照才比得起來


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
        _("every {n} {n:minute|minutes}", n=j['every_minutes']) if j.get("every_minutes") else ""
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
