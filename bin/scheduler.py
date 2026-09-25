#!/usr/bin/env python3
"""單一排程器：讀 schedule.yaml，到點就跑對應 command，一天只跑一次。
由 launchd 每 15 分鐘呼叫一次；新增排程只要編輯 schedule.yaml，不用開新 plist。
見 project-29/ROADMAP.md「排程（cron 替代方案）」一節的 DIY 備案。

每個 job 除了 time 之外可以加(全部選填,沒寫就是每天):
  days_of_week:  [mon, wed]       只在這幾天跑
  days_of_month: [1, 15]          只在每月這幾號跑
  months:        [1, 4, 7, 10]    只在這幾個月跑(搭配 days_of_month 做季度排程)
  max_attempts_per_day: 2         失敗後同一天最多重試幾次;沒寫就維持原本行為
                                  (每個 tick 都重試到成功為止)——貴的 LLM job
                                  一定要設,不然失敗一次就一整天每 15 分鐘燒一次
"""
import datetime
import json
import pathlib
import subprocess

import yaml

HUB = pathlib.Path.home() / "agent-hub"
SCHEDULE = HUB / "schedule.yaml"
STATE_FILE = HUB / ".state" / "scheduler-last-run.json"
LOG_DIR = HUB / ".logs"


def log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_DIR / "scheduler.log", "a") as f:
        f.write(line + "\n")


WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def runs_today(job: dict, now: datetime.datetime) -> bool:
    if "days_of_week" in job and WEEKDAYS[now.weekday()] not in job["days_of_week"]:
        return False
    if "days_of_month" in job and now.day not in job["days_of_month"]:
        return False
    if "months" in job and now.month not in job["months"]:
        return False
    return True


def main() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    if not SCHEDULE.exists():
        log(f"no schedule.yaml at {SCHEDULE}, nothing to do")
        return

    jobs = (yaml.safe_load(SCHEDULE.read_text()) or {}).get("jobs", [])
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")

    for job in jobs:
        name = job["name"]
        if not job.get("enabled", True) or not runs_today(job, now):
            continue

        hh, mm = map(int, job["time"].split(":"))
        due_time = now.replace(hour=hh, minute=mm, second=0, microsecond=0)

        if now < due_time or state.get(name) == today:
            continue

        attempts_key = f"{name}.attempts"
        attempts = state.get(attempts_key, {})
        tried_today = attempts.get("count", 0) if attempts.get("date") == today else 0
        max_attempts = job.get("max_attempts_per_day")
        if max_attempts is not None and tried_today >= max_attempts:
            continue
        state[attempts_key] = {"date": today, "count": tried_today + 1}

        log(f"running job '{name}': {job['command']}")
        job_log_path = LOG_DIR / f"{name}-{today}.log"
        try:
            with open(job_log_path, "a") as job_log:
                subprocess.run(
                    ["/bin/zsh", "-lc", job["command"]],
                    check=True,
                    stdout=job_log,
                    stderr=subprocess.STDOUT,
                )
            state[name] = today
            log(f"job '{name}' finished OK")
        except subprocess.CalledProcessError as e:
            log(f"job '{name}' FAILED (exit {e.returncode}), will retry next tick")

    STATE_FILE.write_text(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
