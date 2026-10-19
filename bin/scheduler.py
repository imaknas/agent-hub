#!/usr/bin/env python3
"""單一排程器：讀 schedule.yaml，到點就跑對應 command，一天只跑一次。
由 launchd 每 15 分鐘呼叫一次；新增排程只要編輯 schedule.yaml，不用開新 plist。
見 project-29/ROADMAP.md「排程（cron 替代方案）」一節的 DIY 備案。

每個 job 除了 time 之外可以加(全部選填,沒寫就是每天):
  days_of_week:  [mon, wed]       只在這幾天跑
  days_of_month: [1, 15]          只在每月這幾號跑
  months:        [1, 4, 7, 10]    只在這幾個月跑(搭配 days_of_month 做季度排程)
  end_date:      2026-11-01       這天之後不再跑(含這天);一次性的 job 搭配 months+days_of_month
  max_attempts_per_day: 2         失敗後同一天最多重試幾次;沒寫就維持原本行為
                                  (每個 tick 都重試到成功為止)——貴的 LLM job
                                  一定要設,不然失敗一次就一整天每 15 分鐘燒一次

間隔型 job:不寫 time,改寫 every_minutes(上面的日期條件照樣可以加):
  every_minutes: 60               距離上次成功超過這麼久就再跑一次(tick 是 15 分鐘,
                                  實際間隔會落在 every_minutes 之後的第一個 tick)
  between: "08:00-23:00"          只在這個時段內跑(選填,不跨午夜)
  max_attempts_per_day 對間隔型 job 是「一天總共最多跑幾次」(成功也算)
"""
import datetime
import json
import os
import pathlib
import subprocess
import sys

import yaml

HUB = pathlib.Path.home() / "agent-hub"

# 給各專案 job 用的環境變數,名稱刻意不帶工具名稱:專案 repo 的程式碼只讀
# $SAFE_PUSH(沒有就退回 git push),不寫死這裡的路徑——repo 之後可能公開,
# 被 commit 的內容不能出現這套機制的名稱或路徑。
JOB_ENV = {**os.environ, "SAFE_PUSH": str(HUB / "bin" / "git-safe-push.sh")}
SCHEDULE = HUB / "schedule.yaml"
STATE_FILE = HUB / ".state" / "scheduler-last-run.json"
LOG_DIR = HUB / ".logs"


def log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_DIR / "scheduler.log", "a") as f:
        f.write(line + "\n")


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)


WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def runs_today(job: dict, now: datetime.datetime) -> bool:
    if "days_of_week" in job and WEEKDAYS[now.weekday()] not in job["days_of_week"]:
        return False
    if "days_of_month" in job and now.day not in job["days_of_month"]:
        return False
    if "months" in job and now.month not in job["months"]:
        return False
    if "end_date" in job and now.date() > datetime.date.fromisoformat(str(job["end_date"])):
        return False
    return True


def hhmm(now: datetime.datetime, s: str) -> datetime.datetime:
    hh, mm = map(int, s.split(":"))
    return now.replace(hour=hh, minute=mm, second=0, microsecond=0)


def is_due(job: dict, state: dict, now: datetime.datetime, today: str) -> bool:
    name = job["name"]
    if "every_minutes" in job:
        if "between" in job:
            start, end = job["between"].split("-")
            if not hhmm(now, start) <= now <= hhmm(now, end):
                return False
        last = state.get(f"{name}.last_ok")
        # 少 1 分鐘的寬限:launchd 的 tick 會有幾秒漂移,不然 every_minutes: 15 會變成 30
        return last is None or now - datetime.datetime.fromisoformat(last) >= \
            datetime.timedelta(minutes=job["every_minutes"] - 1)
    return now >= hhmm(now, job["time"]) and state.get(name) != today


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

        if not is_due(job, state, now, today):
            continue

        attempts_key = f"{name}.attempts"
        attempts = state.get(attempts_key, {})
        tried_today = attempts.get("count", 0) if attempts.get("date") == today else 0
        max_attempts = job.get("max_attempts_per_day")
        if max_attempts is not None and tried_today >= max_attempts:
            continue
        state[attempts_key] = {"date": today, "count": tried_today + 1}
        # 跑之前就先存:job 跑到一半 scheduler 被殺(重開機、OOM),嘗試次數也
        # 已經記下,max_attempts_per_day 才擋得住。
        save_state(state)

        log(f"running job '{name}': {job['command']}")
        job_log_path = LOG_DIR / f"{name}-{today}.log"
        try:
            with open(job_log_path, "a") as job_log:
                subprocess.run(
                    ["/bin/zsh", "-lc", job["command"]],
                    check=True,
                    stdout=job_log,
                    stderr=subprocess.STDOUT,
                    env=JOB_ENV,
                )
            state[name] = today
            if "every_minutes" in job:
                state[f"{name}.last_ok"] = now.isoformat(timespec="seconds")
            save_state(state)
            log(f"job '{name}' finished OK")
        except subprocess.CalledProcessError as e:
            log(f"job '{name}' FAILED (exit {e.returncode}), will retry next tick")

    save_state(state)
    # 排程狀態頁(tailnet 內看得到),每個 tick 重生;失敗不影響排程本身
    subprocess.run([sys.executable, str(HUB / "bin" / "gen-status-page.py")], capture_output=True)


if __name__ == "__main__":
    main()
