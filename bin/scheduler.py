#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The single scheduler: reads schedule.yaml and, when a job is due, runs its command, once a day.
Called by launchd every 15 minutes; adding a schedule only takes editing schedule.yaml, no new plist.

Besides time, each job can add (all optional; without them, daily):
  days_of_week:  [mon, wed]       run only on these days
  days_of_month: [1, 15]          run only on these days of the month
  months:        [1, 4, 7, 10]    run only in these months (with days_of_month for quarterly schedules)
  end_date:      2026-11-01       no runs after this day (this day included); one-off jobs pair it with
                                  months+days_of_month
  max_attempts_per_day: 2         how many times to retry on the same day after a failure; without it, the original
                                  behaviour (retry every tick until it succeeds): expensive LLM jobs
                                  must set it, or one failure burns a run every 15 minutes all day

Interval jobs: no time, every_minutes instead (the date conditions above still apply):
  every_minutes: 60               run again once this long has passed since the last success (the tick is 15 minutes,
                                  so the actual interval lands on the first tick after every_minutes)
  between: "08:00-23:00"          run only within this window (optional, doesn't cross midnight)
  for interval jobs max_attempts_per_day is "at most this many runs in a day in total" (successes count)

Long jobs: add background: true
  Runs in the background (detached from the scheduler's process group) and this tick moves straight on to other
  jobs: jobs run one at a time, and a 30–90 minute job would make every punctual job after it (muninn-tick, uploads)
  wait. The result of a finished run is written to .state/runs/<name>.json and merged back into the state on the next
  tick (only a success counts as "ran today"); while it's running, a second copy isn't started. The log is still
  .logs/<name>-<date>.log.

Order: add after: [job name]
  While a listed job is due today but not finished (not yet time, still running, failed but will retry), this job
  doesn't start; the next tick looks again. Once that job succeeded today, used up max_attempts_per_day, isn't
  scheduled today or is disabled, it's no longer waited for.
  Background jobs can't be kept apart by time alone (e.g. consolidate-memory 02:00 → librarian 03:00 both touch
  ~/.memory-copies, and once the tick drifts they may overlap). If the job waited on has no max_attempts_per_day and
  keeps failing, the wait lasts all day: when hanging an after on it, better give the waited-on job a limit.

At the start of every tick and after every job, .state/scheduler-heartbeat is written, for scheduler-watchdog.py
(a separate launchd job) to tell whether the scheduler has stopped.
"""
from __future__ import annotations  # the system python3 (3.9) must be able to import this too

import datetime
import json
import os
import pathlib
import subprocess
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)

HUB = hubconf.ROOT

# Environment variables for the projects' jobs, deliberately named without the tool's name: a project repo's code only
# reads $SAFE_PUSH (falling back to git push without it) and never hardcodes the path here: the repo may become public
# later, and committed content mustn't show this mechanism's name or paths.
JOB_ENV = {**os.environ, "SAFE_PUSH": str(HUB / "bin" / "git-safe-push.sh")}
SCHEDULE = hubconf.config_file("schedule.yaml")
STATE_FILE = HUB / ".state" / "scheduler-last-run.json"
RUNS_DIR = HUB / ".state" / "runs"
HEARTBEAT = HUB / ".state" / "scheduler-heartbeat"
LOG_DIR = HUB / ".logs"


ALIAS: dict[str, str] = {}  # during the switch-over: registry job name ↔ old schedule.yaml name (filled by all_jobs)


def fs(name: str) -> str:
    """job name → file name (registry jobs are called <project>/<job>, and a slash can't go into a file name)."""
    return name.replace("/", "__")


def all_jobs(log_errors: bool = False) -> tuple[list[dict], dict]:
    """schedule.yaml's jobs + the project registry's jobs. During the switch-over schedule.yaml wins: a registry job
    whose legacy_name is still in schedule.yaml isn't used (deleting that schedule.yaml entry switches it over). A
    broken registry doesn't affect schedule.yaml.
    Returns (jobs, info); info: alias (the two sides' names, for after:), renames (new→old, for moving state),
    skipped, errors."""
    legacy = (yaml.safe_load(SCHEDULE.read_text()) or {}).get("jobs", []) if SCHEDULE.exists() else []
    info = {"alias": {}, "renames": {}, "skipped": [], "errors": {}}
    try:
        import registry
        index = registry.refresh()
        reg_jobs, skipped, renames = registry.scheduler_jobs(index, {j["name"] for j in legacy})
        info.update(renames=renames, skipped=skipped, errors=index["errors"])
        # module jobs have old names too
        for d in [*index["projects"].values(), *(index.get("modules") or {}).values()]:
            for j in d.get("jobs") or []:
                if j.get("legacy_name"):
                    full = f"{d['name']}/{j['name']}"
                    info["alias"][full] = j["legacy_name"]
                    info["alias"][j["legacy_name"]] = full
    except Exception as e:  # the whole registry can't be read: run only schedule.yaml's
        reg_jobs = []
        info["errors"]["(registry)"] = [repr(e)]
        if log_errors:
            log(_("The project registry can't be read ({error!r}); this tick runs only schedule.yaml", error=e))
    return legacy + reg_jobs, info


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


FAILING_FILE = HUB / ".state" / "scheduler-failing.json"
# alert only after failing this long (no noise about the ones the next tick's retry fixes)
ALERT_AFTER = datetime.timedelta(minutes=60)


def mark_job(name: str, failed: bool, detail: str = "") -> None:
    """Record which jobs are failing now (last run failed): removed on success. For alert_failing. Failing to write
    doesn't affect scheduling."""
    try:
        d = json.loads(FAILING_FILE.read_text()) if FAILING_FILE.exists() else {}
        if failed:
            prev = d.get(name) or {}
            d[name] = {"since": prev.get("since") or datetime.datetime.now().isoformat(timespec="seconds"),
                       "detail": detail}
        elif name in d:
            del d[name]
        else:
            return
        FAILING_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1))
    except (OSError, ValueError):
        pass


def failing_problems(failing: dict, now: datetime.datetime) -> list[str]:
    """Jobs failing for longer than ALERT_AFTER → the alert's problem list (alert.once dedupes by wording: a job's
    wording must be stable)."""
    out = []
    for name, rec in sorted(failing.items()):
        try:
            since = datetime.datetime.fromisoformat(rec["since"])
        except (KeyError, ValueError):
            continue
        if now - since >= ALERT_AFTER:
            out.append(_("Scheduled job {name} has been failing since {since} ({detail})", name=name,
                         since=rec["since"][5:16].replace("T", " "), detail=rec.get("detail") or _("see the log")))
    return out


def alert_failing() -> None:
    """10-27: contracts-check failed two nights running and nobody knew: a failed scheduled job used to show only as red
    on the status page. Now persistent failures raise an alert (alert.py: new problems at once, unresolved ones
    reminded every 24 hours, recovery sent when fixed)."""
    try:
        import alert
        failing = json.loads(FAILING_FILE.read_text()) if FAILING_FILE.exists() else {}
        alert.once("scheduler-jobs", failing_problems(failing, datetime.datetime.now()),
                   _("The scheduled jobs are all back to normal"))
    except Exception as e:  # broken alerts don't affect scheduling
        log(_("The scheduled-job alert was not sent: {error!r}", error=e))


def job_failed(name: str, code: int, detail: str = "") -> None:
    """job.failed on the bus (read by the status page and, later, the assistant); failing to write doesn't affect
    scheduling."""
    mark_job(name, True, detail or f"exit {code}")
    subprocess.run([sys.executable, str(HUB / "bin" / "hubbus"), "publish", "job.failed", "--source", "scheduler",
                    "--payload", json.dumps({"job": name, "exit": code, "detail": detail}, ensure_ascii=False)],
                   capture_output=True, timeout=30)


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
    """Merge the background jobs' results into state; return the names of jobs still running."""
    running = set()
    for f in RUNS_DIR.glob("*.json"):
        name = f.stem.replace("__", "/")
        try:
            rec = json.loads(f.read_text())
        except ValueError:
            f.unlink()
            continue
        if "exit" not in rec:
            started = datetime.datetime.fromisoformat(rec["started"])
            # the instant before the pid is written (just forked) also counts as running
            if pid_alive(rec.get("pid")) or (rec.get("pid") is None
                                             and datetime.datetime.now() - started < datetime.timedelta(minutes=1)):
                running.add(name)
                continue
            # gone without writing a result (reboot, killed): counts as a failure; the attempt was recorded before it
            # started
            log(f"job '{name}' FAILED (exit -1), background process gone without a result")
            job_failed(name, -1, _("the background run vanished midway (reboot or killed)"))
        elif rec["exit"] == 0:
            state[name] = rec["date"]
            mark_job(name, False)
            if "every_minutes" in jobs.get(name, {}):
                state[f"{name}.last_ok"] = rec["started"]
        f.unlink()
    return running


def spawn_background(name: str, command: str, job_log_path: pathlib.Path,
                     now: datetime.datetime, today: str) -> None:
    """double fork + setsid: launchd kills what's left in the same process group when the tick ends
    (run-agent-job.sh hit this on 2026-10-14). The grandchild writes its log and result file itself when done."""
    run_file = RUNS_DIR / f"{fs(name)}.json"
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
            rc = subprocess.run([hubconf.login_shell(), "-lc", command], stdout=job_log,
                                stderr=subprocess.STDOUT, env=JOB_ENV).returncode
        rec.update(exit=rc, finished=datetime.datetime.now().isoformat(timespec="seconds"))
        write_json(run_file, rec)
        log(f"job '{name}' finished OK" if rc == 0 else
            f"job '{name}' FAILED (exit {rc}), will retry next tick")
        if rc != 0:
            job_failed(name, rc, f"log:.logs/{job_log_path.name}")
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
    """If a job listed in after: hasn't finished today, return whom we're waiting for; None if no need to wait."""
    for dep in job.get("after", []):
        d = by_name.get(dep)
        # during the switch-over: after names the new name while the job still has the old one (or vice versa)
        if d is None and dep in ALIAS:
            dep = ALIAS[dep]
            d = by_name.get(dep)
        if d is None or not d.get("enabled", True) or not runs_today(d, now):
            continue
        if dep in running:
            return dep
        if "every_minutes" in d:  # interval jobs have no "done for today"; wait only for this run to finish
            continue
        if now < hhmm(now, d["time"]):
            return dep
        if state.get(dep) == today:
            continue
        att = state.get(f"{dep}.attempts", {})
        tried = att.get("count", 0) if att.get("date") == today else 0
        if d.get("max_attempts_per_day") is not None and tried >= d["max_attempts_per_day"]:
            continue  # given up for today; stop waiting
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
        # one minute's grace: launchd's tick drifts by a few seconds, or every_minutes: 15 would become 30
        return last is None or now - datetime.datetime.fromisoformat(last) >= \
            datetime.timedelta(minutes=job["every_minutes"] - 1)
    return now >= hhmm(now, job["time"]) and state.get(name) != today


def main() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    heartbeat()

    jobs, info = all_jobs(log_errors=True)
    ALIAS.clear()
    ALIAS.update(info["alias"])
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")
    by_name = {j["name"]: j for j in jobs}
    running = reconcile(state, by_name)
    # jobs just switched from schedule.yaml to the registry: move their state over from the old name, or they'd rerun
    # today (after reconcile: the old name's background run may have just finished and its result only now merged)
    for new, old in info["renames"].items():
        for suffix in ("", ".attempts", ".last_ok"):
            if old + suffix in state and new + suffix not in state:
                state[new + suffix] = state[old + suffix]
        if old in running:
            running.add(new)  # the old name is still running in the background: the new name counts as running too

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
        # save before running: if the scheduler is killed mid-job (reboot, OOM), the attempt is already
        # recorded, so max_attempts_per_day can hold.
        save_state(state)

        job_log_path = LOG_DIR / f"{fs(name)}-{today}.log"
        if job.get("background"):
            log(f"running job '{name}' (background): {job['command']}")
            spawn_background(name, job["command"], job_log_path, now, today)
            running.add(name)  # jobs with after: on it in the same tick must wait too
            continue
        log(f"running job '{name}': {job['command']}")
        try:
            with open(job_log_path, "a") as job_log:
                subprocess.run(
                    [hubconf.login_shell(), "-lc", job["command"]],
                    check=True,
                    stdout=job_log,
                    stderr=subprocess.STDOUT,
                    env=JOB_ENV,
                )
            state[name] = today
            if "every_minutes" in job:
                state[f"{name}.last_ok"] = now.isoformat(timespec="seconds")
            save_state(state)
            mark_job(name, False)
            log(f"job '{name}' finished OK")
        except subprocess.CalledProcessError as e:
            log(f"job '{name}' FAILED (exit {e.returncode}), will retry next tick")
            job_failed(name, e.returncode, f"log:.logs/{job_log_path.name}")
        heartbeat()

    save_state(state)
    alert_failing()
    # the schedule status page (visible inside the tailnet), regenerated every tick; a failure doesn't affect scheduling
    subprocess.run([sys.executable, str(HUB / "bin" / "gen-status-page.py")], capture_output=True)


if __name__ == "__main__":
    main()
