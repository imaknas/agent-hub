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

Time limit: every run is stopped past its limit (its whole process group killed: what it started too) and counts as a
failed run, so one hung job can't hold up every tick after it (round 6, 11-01). Default FOREGROUND_TIMEOUT_MIN for a
job the tick waits on, BACKGROUND_TIMEOUT_MIN for a background one; a job sets its own with
  timeout_minutes: 90              (the registry's `timeout: 90m`)

Order: add after: [job name]
  While a listed job is due today but not finished (not yet time, still running, failed but will retry), this job
  doesn't start; the next tick looks again. Once that job succeeded today, used up max_attempts_per_day, isn't
  scheduled today or is disabled, it's no longer waited for.
  Background jobs can't be kept apart by time alone (e.g. consolidate-memory 02:00 → librarian 03:00 both touch
  ~/.memory-copies, and once the tick drifts they may overlap). If the job waited on has no max_attempts_per_day and
  keeps failing, the wait lasts all day: when hanging an after on it, better give the waited-on job a limit.

Event-triggered jobs (registry only, trigger: instead of at/every; docs/design/event-jobs.md, bin/event_jobs.py):
  due when bus events matching trigger.events/projects/sources/where are waiting after the job's cursor
  (<job>.event_seq, tied to the bus file's generation <job>.event_gen) and trigger.debounce has passed. A new job's
  cursor starts at the bus head (never history). The run takes the oldest waiting events that fit in 64 KB
  (AGENT_HUB_EVENTS); success moves the cursor past them, failure leaves them for the next attempt
  (max_attempts_per_day, default 12). Events whose chain holds the job, or whose depth is MAX_DEPTH, are held back (loops);
  they are counted in <job>.event_held. Days/between filters keep events waiting; after:/before: work as for interval
  jobs (waited for only while running). schedulectl events/skip inspect and drop a job's waiting events.

Every run gets AGENT_HUB_JOB (its name), AGENT_HUB_JOB_CHAIN (the causal chain: [job], or the batch's chains + job) and
AGENT_HUB_JOB_DEPTH; hubbus stamps both on every event the run publishes. job.failed carries the run's chain and, for registry jobs,
to_project = the job's project.

One writer: the tick holds .state/scheduler.lock for its whole run (held by someone else for over TICK_LOCK_WAIT =
this tick does nothing, not even the heartbeat); schedulectl's state writes take the same lock. A background run closes its inherited copy at once.

At the start of every tick and after every job, .state/scheduler-heartbeat is written, for scheduler-watchdog.py
(a separate launchd job) to tell whether the scheduler has stopped.
"""
from __future__ import annotations  # the system python3 (3.9) must be able to import this too

import contextlib
import datetime
import json
import os
import pathlib
import subprocess
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import event_jobs  # noqa: E402
import hubconf  # noqa: E402
import names  # noqa: E402
import i18n  # noqa: E402
import memlock  # noqa: E402

_ = i18n.domain(__file__)

HUB = hubconf.ROOT
FOREGROUND_TIMEOUT_MIN = 30   # the longest foreground job ran about 8 minutes (11-01, a week of logs)
BACKGROUND_TIMEOUT_MIN = 240  # the longest background job ran about 31 minutes
TIMED_OUT = 124

# Environment variables for the projects' jobs, deliberately named without the tool's name: a project repo's code only
# reads $SAFE_PUSH (falling back to git push without it) and never hardcodes the path here: the repo may become public
# later, and committed content mustn't show this mechanism's name or paths.
JOB_ENV = {**os.environ, "SAFE_PUSH": str(HUB / "bin" / "git-safe-push.sh")}
SCHEDULE = hubconf.config_file("schedule.yaml")
STATE_FILE = HUB / ".state" / "scheduler-last-run.json"
RUNS_DIR = HUB / ".state" / "runs"
HEARTBEAT = HUB / ".state" / "scheduler-heartbeat"
LOG_DIR = HUB / ".logs"
LOCK_FILE = HUB / ".state" / "scheduler.lock"
LOCK_FD: int | None = None   # held for the whole tick; a forked background run closes its copy at once
TICK_LOCK_WAIT = 60          # a schedulectl write is short: wait this long for it before giving the tick up


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
        try:   # what their runs would refuse (they still run, and fail saying so): listed for the status page
            by_rules = registry.rules_refused(index)
        except Exception as e:
            by_rules = {"*": str(e)}
        info.update(renames=renames, skipped=skipped, errors=index["errors"],
                    refused={**(index.get("refused") or {}), **by_rules})
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


@contextlib.contextmanager
def state_lock(timeout: float = 0, path: pathlib.Path | None = None):
    """The scheduler's state has one writer at a time: the tick holds this for its whole run, and schedulectl's writes
    (run, skip) take it too, so an event job's cursor never moves back to a stale copy (docs/design/event-jobs.md
    section 4). Waits up to timeout seconds, then yields False (never blocks forever: a run that calls schedulectl
    while its own tick holds the lock must fail, not hang)."""
    global LOCK_FD
    path = path or LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
    try:
        if not memlock.flock(fd, timeout):
            yield False
            return
        LOCK_FD = fd
        yield True
    finally:
        LOCK_FD = None
        os.close(fd)


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


def job_failed(name: str, code: int, detail: str = "", project: str | None = None,
               chain: list[str] | None = None, depth: int = 1) -> None:
    """job.failed on the bus (read by the status page, the assistant and event jobs); failing to write doesn't affect
    scheduling. A registry job's failure belongs to its project (to_project), and carries the run's chain so an event
    job can't trigger itself through its own failure (event-jobs.md section 7)."""
    mark_job(name, True, detail or f"exit {code}")
    to = ["--to", project] if project else []
    env = dict(os.environ)
    names.env_set(env, event_jobs.CHAIN_VAR, json.dumps(chain or [name], ensure_ascii=False))
    names.env_set(env, event_jobs.DEPTH_VAR, str(depth))
    subprocess.run([sys.executable, str(HUB / "bin" / "hubbus"), "publish", "job.failed", "--source", "scheduler", *to,
                    "--payload", json.dumps({"job": name, "exit": code, "detail": detail}, ensure_ascii=False)],
                   capture_output=True, timeout=30, env=env)


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


def reconcile(state: dict, jobs: dict[str, dict], merged: list[pathlib.Path]) -> set[str]:
    """Merge the background jobs' results into state; return the names of jobs still running. The merged run files
    go into merged: the caller deletes them only after saving the state (a crash in between must not lose a success,
    or an event job's batch would be run again)."""
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
            job_failed(name, -1, _("the background run vanished midway (reboot or killed)"), rec.get("project"),
                       rec.get("chain"), rec.get("depth", 1))
        elif rec["exit"] == 0:
            state[name] = rec["date"]
            mark_job(name, False)
            if "every_minutes" in jobs.get(name, {}):
                state[f"{name}.last_ok"] = rec["started"]
            if "events_upto" in rec:
                consumed(state, name, rec["events_upto"], rec.get("events_count", 0), rec["started"])
                held_back(state, name, rec.get("events_held") or [], rec["date"])
        merged.append(f)
    return running


def run_bounded(command: str, job_log, minutes: float, env: dict | None = None) -> int:
    """Run the job's command in its own process group; past `minutes`, SIGTERM the group, SIGKILL it 10 s later, and
    return TIMED_OUT."""
    import signal
    p = subprocess.Popen([hubconf.login_shell(), "-lc", command], stdout=job_log, stderr=subprocess.STDOUT,
                         env=env or JOB_ENV, start_new_session=True)
    try:
        return p.wait(timeout=minutes * 60)
    except subprocess.TimeoutExpired:
        for sig, grace in ((signal.SIGTERM, 10), (signal.SIGKILL, None)):
            try:
                os.killpg(p.pid, sig)
            except OSError:
                pass
            try:
                p.wait(timeout=grace)
                break
            except subprocess.TimeoutExpired:
                continue
        job_log.write(f"\n[scheduler] stopped after {minutes:g} minutes (the job's time limit)\n")
        return TIMED_OUT


def timeout_of(job: dict) -> float:
    return float(job.get("timeout_minutes") or (BACKGROUND_TIMEOUT_MIN if job.get("background")
                                                else FOREGROUND_TIMEOUT_MIN))


def spawn_background(name: str, command: str, job_log_path: pathlib.Path,
                     now: datetime.datetime, today: str, env: dict | None = None, extra: dict | None = None,
                     minutes: float = BACKGROUND_TIMEOUT_MIN) -> None:
    """double fork + setsid: launchd kills what's left in the same process group when the tick ends
    (run-agent-job.sh hit this on 2026-10-14). The grandchild writes its log and result file itself when done.
    extra goes into the run file (project, chain, an event job's events_upto) for reconcile."""
    run_file = RUNS_DIR / f"{fs(name)}.json"
    rec = {"date": today, "started": now.isoformat(timespec="seconds"), "pid": None, **(extra or {})}
    write_json(run_file, rec)
    sys.stdout.flush()
    pid = os.fork()
    if pid:
        os.waitpid(pid, 0)
        return
    try:
        if LOCK_FD is not None:
            os.close(LOCK_FD)   # the tick's lock must not live as long as this run
        os.setsid()
        if os.fork():
            os._exit(0)
        rec["pid"] = os.getpid()
        write_json(run_file, rec)
        devnull = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull, 0)
        with open(job_log_path, "a") as job_log:
            rc = run_bounded(command, job_log, minutes, env)
        rec.update(exit=rc, finished=datetime.datetime.now().isoformat(timespec="seconds"))
        write_json(run_file, rec)
        log(f"job '{name}' finished OK" if rc == 0 else
            f"job '{name}' FAILED (exit {rc}), will retry next tick")
        if rc != 0:
            job_failed(name, rc, f"log:.logs/{job_log_path.name}", rec.get("project"), rec.get("chain"),
                       rec.get("depth", 1))
    finally:
        os._exit(0)


WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def scheduled_on(job: dict, d: datetime.date) -> bool:
    """The day filters alone (days_of_week, days_of_month, months)."""
    if "days_of_week" in job and WEEKDAYS[d.weekday()] not in job["days_of_week"]:
        return False
    if "days_of_month" in job and d.day not in job["days_of_month"]:
        return False
    if "months" in job and d.month not in job["months"]:
        return False
    return True


CATCH_UP_DAYS = 62   # how far back catch_up: period looks for the day a job missed (covers a monthly job)


def missed_day(job: dict, now: datetime.datetime, state: dict | None) -> datetime.date | None:
    """catch_up: period (docs/design/newcomer-path.md B7): the last scheduled day before today, when the job hasn't run
    on or since it (the machine was asleep or off that day). A weekly or monthly job then runs at the first tick after
    waking instead of skipping its whole period. None: no catch-up (not opted in, or it ran)."""
    if job.get("catch_up") != "period" or state is None or "time" not in job:
        return None
    last = state.get(job["name"]) or ""
    for back in range(1, CATCH_UP_DAYS + 1):
        d = now.date() - datetime.timedelta(days=back)
        if scheduled_on(job, d):
            return d if last < d.isoformat() else None
    return None


def runs_today(job: dict, now: datetime.datetime, state: dict | None = None) -> bool:
    if "end_date" in job and now.date() > datetime.date.fromisoformat(str(job["end_date"])):
        return False
    return scheduled_on(job, now.date()) or missed_day(job, now, state) is not None


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
        if d is None and "/" in dep:   # a project's former name, the platform's other spelling included
            proj, short = dep.split("/", 1)
            for now_name in {hubconf.hub_project()} if proj in names.all("project") else set():
                if (d := by_name.get(f"{now_name}/{short}")):
                    dep = f"{now_name}/{short}"
        if d is None or not d.get("enabled", True) or not runs_today(d, now, state):
            continue
        if dep in running:
            return dep
        if "every_minutes" in d or "trigger" in d:  # interval and event jobs have no "done for today"; wait only for this
            continue                               # run to finish
        if now < hhmm(now, d["time"]) and not (not scheduled_on(d, now.date()) and missed_day(d, now, state)):
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
    if not scheduled_on(job, now.date()) and missed_day(job, now, state):   # catching up: due now, whatever the time
        return state.get(name) != today
    return now >= hhmm(now, job["time"]) and state.get(name) != today


def consumed(state: dict, name: str, upto: int, count: int, started: str) -> None:
    """A successful run of an event job took its batch: the cursor moves to upto (never back)."""
    state[f"{name}.event_seq"] = max(state.get(f"{name}.event_seq", 0), upto)
    state[f"{name}.event_last"] = {"at": started, "count": count}


def held_back(state: dict, name: str, held: list[dict], today: str) -> None:
    """Events the loop chain kept from triggering this job, counted per day for the status page (never silent)."""
    if not held:
        return
    rec = state.get(f"{name}.event_held") or {}
    n = rec.get("count", 0) if rec.get("date") == today else 0
    state[f"{name}.event_held"] = {"date": today, "count": n + len(held), "last": held[-1]["id"]}
    log(f"job '{name}': {len(held)} event(s) held back by the loop chain ({held[-1]['id']})")


def adopt_cursor(job: dict, state: dict) -> None:
    """A renamed project's job takes the cursor its old name had (hubconf.former_names)."""
    project, short, name = job.get("project"), job["name"].split("/", 1)[-1], job["name"]
    for old in hubconf.former_names(project) if project else []:
        seq, gen = state.get(f"{old}/{short}.event_seq"), state.get(f"{old}/{short}.event_gen")
        if seq is None:
            continue
        if f"{name}.event_seq" not in state:
            state[f"{name}.event_seq"], state[f"{name}.event_gen"] = seq, gen
        elif gen == state.get(f"{name}.event_gen"):
            # a background run under the old name may finish after the copy: its advance must still count
            state[f"{name}.event_seq"] = max(state[f"{name}.event_seq"], seq)


def adopt_state(job: dict, state: dict) -> None:
    """A renamed project's job takes the daily state its old names left (the platform's own rename agent-hub ↔ brunna
    counts both ways, hubconf.former_names), newest wins, and the old keys go: a daily job never runs twice because its
    project changed name, on an upgrade, a rollback or a jump (docs/design/rename-brunna.md). Its failing record moves
    too, so an open alert still gets its recovery message."""
    project, short, name = job.get("project"), job["name"].split("/", 1)[-1], job["name"]
    for old in hubconf.former_names(project) if project else []:
        was = f"{old}/{short}"
        if was == name:
            continue
        if was in state:
            state[name] = max(state.get(name) or "", state.pop(was))
        if f"{was}.last_ok" in state:
            state[f"{name}.last_ok"] = max(state.get(f"{name}.last_ok") or "", state.pop(f"{was}.last_ok"))
        if f"{was}.attempts" in state:
            a, b = state.pop(f"{was}.attempts") or {}, state.get(f"{name}.attempts") or {}
            if (a.get("date") or "", a.get("count", 0)) > (b.get("date") or "", b.get("count", 0)):
                state[f"{name}.attempts"] = a
        try:
            failing = json.loads(FAILING_FILE.read_text()) if FAILING_FILE.exists() else {}
            if was in failing:
                failing.setdefault(name, failing[was])
                del failing[was]
                FAILING_FILE.write_text(json.dumps(failing, ensure_ascii=False, indent=1))
        except (OSError, ValueError):
            pass


def first_sight(job: dict, state: dict) -> None:
    """An event job with no cursor (new, or renamed without an old one) starts at the bus head: from now on, never
    history. Runs before the date filters, so events on a day the job doesn't run wait for it."""
    adopt_cursor(job, state)
    if f"{job['name']}.event_seq" in state or not (hubconf.BUS_DIR / "bus.db").exists():
        return   # no bus file yet: no cursor either (one taken now couldn't tell a bus moved in later from history)
    try:
        head, gen, _rows = event_jobs.read(0, [])
    except Exception as e:
        log(_("job '{name}': the bus can't be read ({error!r}); its events wait", name=job["name"], error=e))
        return
    state[f"{job['name']}.event_seq"], state[f"{job['name']}.event_gen"] = head, gen


def event_batch(job: dict, state: dict, now: datetime.datetime, today: str) -> dict | None:
    """An event job's turn: the batch to run with (event_jobs.take: {events, upto, left}), or None when there is
    nothing to run now. Moves the cursor where nothing waits (first sight, nothing pending, a new bus file).
    docs/design/event-jobs.md section 4."""
    name = job["name"]
    key, gkey = f"{name}.event_seq", f"{name}.event_gen"
    if key not in state:
        return None
    try:
        got = event_jobs.pending(job, state[key])
    except Exception as e:  # an unreadable bus must not stop the other jobs
        log(_("job '{name}': the bus can't be read ({error!r}); its events wait", name=name, error=e))
        return None
    if got["head"] < state[key] or (state.get(gkey) and got["generation"] and got["generation"] != state[gkey]):
        log(_("job '{name}': the bus file was replaced (cursor {cursor}, head now {head}); starting from the head",
              name=name, head=got["head"], cursor=state[key]))
        state[key], state[gkey] = got["head"], got["generation"]
        return None
    state[gkey] = got["generation"] or state.get(gkey)
    if not got["events"]:
        held_back(state, name, got["held"], today)
        state[key] = got["head"]
        return None
    if "between" in job:
        start, end = job["between"].split("-")
        if not hhmm(now, start) <= now <= hhmm(now, end):
            return None
    if not event_jobs.quiet(job, got["events"], now):
        return None
    batch = event_jobs.take(got)
    batch["held"] = [h for h in got["held"] if h["seq"] <= batch["upto"]]
    return batch


def main() -> None:
    if hubconf.worktree_lookup():   # it would run the machine's jobs from a half-edited copy
        sys.exit(_("scheduler: {root} is a linked worktree of the hub; the scheduler runs only from the main checkout "
                   "({main})", root=hubconf.ROOT, main=hubconf.MAIN))
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    with state_lock(timeout=TICK_LOCK_WAIT) as mine:
        if not mine:   # no heartbeat either: a tick that can't run must not look alive
            log(f"the state has been held for over {TICK_LOCK_WAIT}s (another tick, or a schedulectl write); "
                "this tick does nothing")
            return
        heartbeat()
        tick()


def tick() -> None:
    jobs, info = all_jobs(log_errors=True)
    ALIAS.clear()
    ALIAS.update(info["alias"])
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")
    by_name = {j["name"]: j for j in jobs}
    merged: list[pathlib.Path] = []
    running = reconcile(state, by_name, merged)
    # jobs just switched from schedule.yaml to the registry: move their state over from the old name, or they'd rerun
    # today (after reconcile: the old name's background run may have just finished and its result only now merged)
    for new, old in info["renames"].items():
        for suffix in ("", ".attempts", ".last_ok", ".event_seq"):
            if old + suffix in state and new + suffix not in state:
                state[new + suffix] = state[old + suffix]
        if old in running:
            running.add(new)  # the old name is still running in the background: the new name counts as running too
    for job in jobs:   # a renamed project's job whose old name still runs in the background: not a second copy
        short = job["name"].split("/", 1)[-1]
        if job.get("project") and any(f"{old}/{short}" in running for old in hubconf.former_names(job["project"])):
            running.add(job["name"])
        adopt_state(job, state)
    save_state(state)
    for f in merged:
        f.unlink(missing_ok=True)

    for job in jobs:
        name = job["name"]
        if "trigger" in job:
            first_sight(job, state)
        if not job.get("enabled", True) or not runs_today(job, now, state) or name in running:
            continue

        batch = None
        if "trigger" in job:
            if not (batch := event_batch(job, state, now, today)):
                continue
        elif not is_due(job, state, now, today):
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
        events = batch["events"] if batch else None
        chain, depth = event_jobs.run_chain(name, events or []), event_jobs.run_depth(events or [])
        env = {**JOB_ENV, **event_jobs.run_env(name, chain, depth, events)}
        extra = {"project": job.get("project"), "chain": chain, "depth": depth}
        if batch:
            extra.update(events_upto=batch["upto"], events_count=len(events),
                         events_held=[{"id": h["id"]} for h in batch["held"]])
            log(f"job '{name}' triggered by {len(events)} event(s) (seq {events[0]['seq']}..{events[-1]['seq']})")
        if job.get("background"):
            log(f"running job '{name}' (background): {job['command']}")
            spawn_background(name, job["command"], job_log_path, now, today, env, extra, timeout_of(job))
            running.add(name)  # jobs with after: on it in the same tick must wait too
            continue
        log(f"running job '{name}': {job['command']}")
        try:
            with open(job_log_path, "a") as job_log:
                rc = run_bounded(job["command"], job_log, timeout_of(job), env)
            if rc:
                raise subprocess.CalledProcessError(rc, job["command"])
            state[name] = today
            if "every_minutes" in job:
                state[f"{name}.last_ok"] = now.isoformat(timespec="seconds")
            if batch:
                consumed(state, name, batch["upto"], len(events), now.isoformat(timespec="seconds"))
                held_back(state, name, batch["held"], today)
            save_state(state)
            mark_job(name, False)
            log(f"job '{name}' finished OK")
        except subprocess.CalledProcessError as e:
            log(f"job '{name}' FAILED (exit {e.returncode}), will retry next tick")
            job_failed(name, e.returncode, f"log:.logs/{job_log_path.name}", job.get("project"), chain, depth)
        heartbeat()

    save_state(state)
    alert_failing()
    # the schedule status page (served by local.status-page, on 127.0.0.1 by default), regenerated every tick; a failure doesn't affect scheduling
    try:
        subprocess.run([sys.executable, str(HUB / "bin" / "gen-status-page.py")], capture_output=True, timeout=300)
    except subprocess.TimeoutExpired:
        log("gen-status-page took over 5 minutes; stopped")


if __name__ == "__main__":
    main()
