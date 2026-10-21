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

長 job:加 background: true
  在背景跑(脫離 scheduler 的 process group),這個 tick 馬上往下跑別的 job——job 是一個
  一個依序跑的,一個 30–90 分鐘的 job 會讓後面準時的 job(muninn-tick、上傳)全部等它。
  跑完的結果寫在 .state/runs/<name>.json,下一個 tick 併回狀態(成功才記「今天跑過」);
  還在跑的時候不會再開第二份。log 一樣是 .logs/<name>-<日期>.log。

順序:加 after: [job 名稱]
  列出的 job 今天要跑、但還沒跑完(還沒到時間、還在跑、失敗了但還會重試)時,這個 job 先不開始,
  下個 tick 再看。那個 job 今天成功了、用完 max_attempts_per_day、今天不排或停用了,就不再等。
  背景 job 之間不能只靠時間錯開(例:consolidate-memory 02:00 → librarian 03:00,兩邊都動
  ~/.memory-copies,tick 一漂移就可能重疊)。被等的 job 沒設 max_attempts_per_day 又一直失敗,
  就會等一整天——要掛 after 的話,被等的那個最好設上限。

每個 tick 開始、每個 job 跑完都會寫 .state/scheduler-heartbeat,給 scheduler-watchdog.py
(獨立的 launchd job)判斷排程器是不是停了。
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
RUNS_DIR = HUB / ".state" / "runs"
HEARTBEAT = HUB / ".state" / "scheduler-heartbeat"
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


def heartbeat() -> None:
    HEARTBEAT.write_text(datetime.datetime.now().isoformat(timespec="seconds") + "\n")


def write_json(path: pathlib.Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def reconcile(state: dict, jobs: dict[str, dict]) -> set[str]:
    """把背景 job 的結果併回 state;回傳還在跑的 job 名稱。"""
    running = set()
    for f in RUNS_DIR.glob("*.json"):
        name = f.stem
        try:
            rec = json.loads(f.read_text())
        except ValueError:
            f.unlink()
            continue
        if "exit" not in rec:
            started = datetime.datetime.fromisoformat(rec["started"])
            # pid 還沒寫進去的那一瞬間(剛 fork)也算在跑
            if pid_alive(rec.get("pid")) or (rec.get("pid") is None
                                             and datetime.datetime.now() - started < datetime.timedelta(minutes=1)):
                running.add(name)
                continue
            # 沒寫結果就不見了(重開機、被殺):算失敗,嘗試次數在開始前就記過了
            log(f"job '{name}' FAILED (exit -1), background process gone without a result")
        elif rec["exit"] == 0:
            state[name] = rec["date"]
            if "every_minutes" in jobs.get(name, {}):
                state[f"{name}.last_ok"] = rec["started"]
        f.unlink()
    return running


def spawn_background(name: str, command: str, job_log_path: pathlib.Path,
                     now: datetime.datetime, today: str) -> None:
    """double fork + setsid:launchd 在 tick 結束時會殺掉同一個 process group 剩下的程序
    (run-agent-job.sh 2026-10-14 踩過)。孫程序跑完自己寫 log 跟結果檔。"""
    run_file = RUNS_DIR / f"{name}.json"
    rec = {"date": today, "started": now.isoformat(timespec="seconds"), "pid": None}
    write_json(run_file, rec)
    sys.stdout.flush()
    pid = os.fork()
    if pid:
        os.waitpid(pid, 0)
        return
    try:
        os.setsid()
        if os.fork():
            os._exit(0)
        rec["pid"] = os.getpid()
        write_json(run_file, rec)
        devnull = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull, 0)
        with open(job_log_path, "a") as job_log:
            rc = subprocess.run(["/bin/zsh", "-lc", command], stdout=job_log,
                                stderr=subprocess.STDOUT, env=JOB_ENV).returncode
        rec.update(exit=rc, finished=datetime.datetime.now().isoformat(timespec="seconds"))
        write_json(run_file, rec)
        log(f"job '{name}' finished OK" if rc == 0 else
            f"job '{name}' FAILED (exit {rc}), will retry next tick")
    finally:
        os._exit(0)


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


def waiting_on(job: dict, by_name: dict[str, dict], state: dict, running: set[str],
               now: datetime.datetime, today: str) -> str | None:
    """after: 列的 job 今天還沒結束的話,回傳在等誰;不用等回傳 None。"""
    for dep in job.get("after", []):
        d = by_name.get(dep)
        if d is None or not d.get("enabled", True) or not runs_today(d, now):
            continue
        if dep in running:
            return dep
        if "every_minutes" in d:  # 間隔型的沒有「今天跑完」,只等它這次跑完
            continue
        if now < hhmm(now, d["time"]):
            return dep
        if state.get(dep) == today:
            continue
        att = state.get(f"{dep}.attempts", {})
        tried = att.get("count", 0) if att.get("date") == today else 0
        if d.get("max_attempts_per_day") is not None and tried >= d["max_attempts_per_day"]:
            continue  # 今天放棄了,不再等
        return dep
    return None


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
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    heartbeat()

    if not SCHEDULE.exists():
        log(f"no schedule.yaml at {SCHEDULE}, nothing to do")
        return

    jobs = (yaml.safe_load(SCHEDULE.read_text()) or {}).get("jobs", [])
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")
    by_name = {j["name"]: j for j in jobs}
    running = reconcile(state, by_name)

    for job in jobs:
        name = job["name"]
        if not job.get("enabled", True) or not runs_today(job, now) or name in running:
            continue

        if not is_due(job, state, now, today):
            continue
        if dep := waiting_on(job, by_name, state, running, now, today):
            log(f"job '{name}' waiting for '{dep}' (after:)")
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

        job_log_path = LOG_DIR / f"{name}-{today}.log"
        if job.get("background"):
            log(f"running job '{name}' (background): {job['command']}")
            spawn_background(name, job["command"], job_log_path, now, today)
            running.add(name)  # 同一個 tick 裡 after: 它的 job 也要等
            continue
        log(f"running job '{name}': {job['command']}")
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
        heartbeat()

    save_state(state)
    # 排程狀態頁(tailnet 內看得到),每個 tick 重生;失敗不影響排程本身
    subprocess.run([sys.executable, str(HUB / "bin" / "gen-status-page.py")], capture_output=True)


if __name__ == "__main__":
    main()
