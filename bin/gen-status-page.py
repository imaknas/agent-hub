#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The schedule status page: replaces Claude Desktop's "Scheduled" view; every scheduled job (schedule.yaml) on one
page.

Runs on the hub machine. Regenerated at the end of every scheduler.py tick and after every run-agent-job.sh run;
zero LLM. Writes .state/status/index.html, opened only to the tailnet with `tailscale serve` (the laptop and the
phone can open it, the internet cannot): https://<the hub machine>.<tailnet>.ts.net/

Contents:
  - needs attention: agent jobs whose report starts with 🔔 (within 7 days), failed jobs, the scheduler itself not
    running
  - each job: when it runs, last result, next run
  - the full text of the latest few reports of agent jobs (run-agent-job.sh)
"""
import sys
import datetime
import html
import json
import os
import pathlib
import re
import subprocess


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)

HUB = hubconf.ROOT
SCHEDULE = hubconf.config_file("schedule.yaml")
STATE = HUB / ".state" / "scheduler-last-run.json"
SCHED_LOG = HUB / ".logs" / "scheduler.log"
AGENT_JOBS = HUB / ".state" / "agent-jobs"
OUT = HUB / ".state" / "status" / "index.html"
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
LOG_LINE = re.compile(r"^\[(\S+ \S+)\] (?:running job '([^']+)'|job '([^']+)' (finished OK|FAILED \(exit (-?\d+)\)))")


def prune_line() -> str:
    """The result of the nightly worktree cleanup (prune-worktrees --auto), one line; nothing shown if it never ran."""
    import json, pathlib, sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import hubconf
    f = hubconf.STATE / "worktree-prune.json"
    try:
        r = json.loads(f.read_text())
    except (OSError, ValueError):
        return ""
    parts = [_("{n} {n:dependency|dependencies} deleted", n=len(r.get('deps', []))), _("{n} {n:worktree|worktrees} removed", n=len(r.get('removed', []))),
             _("{mb} MB freed", mb=r.get('freed_mb', 0))]
    if r.get("skipped"):
        parts.append(_("{n} skipped (files that may not be rebuildable)", n=len(r['skipped'])))
    return ("<div class=sub style='margin-top:8px'>"
            + _("Worktree auto-cleanup ({at}): {parts}", at=esc(str(r.get('at', ''))[:16]), parts=_(", ").join(parts))
            + "</div>")


def runs_on(job: dict, d: datetime.date) -> bool:
    """The same filters as scheduler.py's runs_today()."""
    if "days_of_week" in job and WEEKDAYS[d.weekday()] not in job["days_of_week"]:
        return False
    if "days_of_month" in job and d.day not in job["days_of_month"]:
        return False
    if "months" in job and d.month not in job["months"]:
        return False
    if "end_date" in job and d > datetime.date.fromisoformat(str(job["end_date"])):
        return False
    return True


def weekday_name(i: int) -> str:
    """Monday = 0, as date.weekday()"""
    return (_("Mon"), _("Tue"), _("Wed"), _("Thu"), _("Fri"), _("Sat"), _("Sun"))[i]


def month_name(m: int) -> str:
    return (_("Jan"), _("Feb"), _("Mar"), _("Apr"), _("May"), _("Jun"), _("Jul"), _("Aug"), _("Sep"), _("Oct"), _("Nov"),
            _("Dec"))[m - 1]


def describe_when(job: dict) -> str:
    """One whole phrase per shape (the same English as schedulectl list): "daily at 06:00", "every 15 minutes, 08:00-20:00",
    "weekly on Mon, Thu at 10:00", "monthly on days 1 and 15 at 10:00", "in Jan, Apr on day 1 at 06:00 (until …)"."""
    parts = []
    if "months" in job:
        months = month_name(job["months"][0])
        for m in job["months"][1:]:
            months = _("{months}, {month}", months=months, month=month_name(m))
        parts.append(_("in {months}", months=months))
    if "days_of_month" in job:
        names = [_("{day}", day=d) for d in job["days_of_month"]]
        days = names[0]
        for i, d in enumerate(names[1:], 1):
            days = _("{days} and {day}", days=days, day=d) if i == len(names) - 1 else _("{days}, {day}", days=days, day=d)
        if parts:
            parts.append(_("on day {days}", days=days) if len(names) == 1 else _("on days {days}", days=days))
        else:
            parts.append(_("monthly on day {days}", days=days) if len(names) == 1 else
                         _("monthly on days {days}", days=days))
    if "days_of_week" in job:
        wd = weekday_name(WEEKDAYS.index(job["days_of_week"][0]))
        for w in job["days_of_week"][1:]:
            wd = _("{weekdays}, {weekday}", weekdays=wd, weekday=weekday_name(WEEKDAYS.index(w)))
        parts.append(_("weekly on {weekdays}", weekdays=wd))
    when = " ".join(parts)
    until = _(" (until {date})", date=job['end_date']) if "end_date" in job else ""
    if "every_minutes" in job:
        n, between = job["every_minutes"], _(", {between}", between=job["between"]) if "between" in job else ""
        if not when:
            return _("every {n} {n:minute|minutes}{between}{until}", n=n, between=between, until=until)
        return _("every {n} {n:minute|minutes}{between}, {when}{until}", n=n, between=between, when=when, until=until)
    return _("{when} at {time}{until}", when=when or _("daily"), time=job["time"], until=until)


def next_run(job: dict, state: dict, now: datetime.datetime) -> str:
    if not job.get("enabled", True):
        return _("paused")
    if "every_minutes" in job:
        last = state.get(f"{job['name']}.last_ok")
        if not last:
            return _("next tick")
        nxt = datetime.datetime.fromisoformat(last) + datetime.timedelta(minutes=job["every_minutes"])
        return fmt_dt(nxt) + (_(" (only during {between})", between=job['between']) if "between" in job else "")
    hh, mm = map(int, job["time"].split(":"))
    for i in range(0, 400):
        d = now.date() + datetime.timedelta(days=i)
        if not runs_on(job, d):
            continue
        due = datetime.datetime.combine(d, datetime.time(hh, mm))
        if i == 0 and state.get(job["name"]) == d.isoformat():
            continue  # already ran today
        if i == 0 and now >= due:
            return _("today (waiting for the next tick, or out of retries)")
        return fmt_dt(due)
    return _("never again")


def fmt_dt(t: datetime.datetime) -> str:
    today = datetime.date.today()
    day = {0: _("today"), 1: _("tomorrow")}.get((t.date() - today).days,
                                                _("{weekday} {date:%m/%d}", weekday=weekday_name(t.weekday()), date=t))
    return f"{day} {t:%H:%M}"


def age_text(days: int) -> str:
    """a worktree's last commit: "3 days ago"; 0 days is "earlier today" (its own msgid, so a translation can keep "0 …")"""
    return _("earlier today") if days == 0 else _("{n} {n:day|days} ago", n=days)


def scheduler_runs() -> tuple[dict, datetime.datetime | None]:
    """job name -> last {started, result}; and the time of the last tick."""
    runs: dict[str, dict] = {}
    last_tick = None
    if not SCHED_LOG.exists():
        return runs, None
    lines = SCHED_LOG.read_text(errors="replace").splitlines()[-8000:]
    for line in lines:
        m = LOG_LINE.match(line)
        if line.startswith("["):
            try:
                last_tick = datetime.datetime.strptime(line[1:20], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
        if not m:
            continue
        ts, started, ended, result, code = m.groups()
        if started:
            runs[started] = {"started": ts, "result": "running"}
        else:
            r = runs.setdefault(ended, {"started": ts})
            r["result"] = "ok" if result == "finished OK" else f"failed (exit {code})"
            r["code"] = code
            r["ended"] = ts
    return runs, last_tick


def scheduler_alive() -> bool:
    if hubconf.service_manager() == "systemd":
        import systemd_units
        return systemd_units.state("local.agent-hub-scheduler") == "running"
    if hubconf.service_manager() != "launchd":
        return False   # no service manager (the alert below says so)
    r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/local.agent-hub-scheduler"],
                       capture_output=True, text=True)
    return r.returncode == 0


def agent_status(name: str) -> dict | None:
    f = AGENT_JOBS / name / "status.json"
    return json.loads(f.read_text()) if f.exists() else None


def agent_reports(name: str, n: int = 5) -> list[pathlib.Path]:
    return sorted((AGENT_JOBS / name).glob("20*.md"), reverse=True)[:n]


def esc(s: str) -> str:
    return html.escape(s or "")


def model_usage_html(now: datetime.datetime) -> str:
    """Projects' own model calls (agenthub complete) in the last 7 days, from completion's usage log: calls, tokens,
    cost (only what the provider reports or the model's price gives; a subscription login shows none)."""
    import completion
    since = (now - datetime.timedelta(days=7)).isoformat()
    today = now.date().isoformat()
    per: dict[str, dict] = {}
    try:
        lines = completion.USAGE.read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not r.get("project") or str(r.get("ts", "")) < since:
            continue
        v = per.setdefault(r["project"], {"calls": 0, "today": 0, "tokens": 0, "billed": None, "estimate": None})
        v["calls"] += 1
        v["today"] += str(r.get("ts", "")).startswith(today)
        v["tokens"] += (r.get("input_tokens") or 0) + (r.get("output_tokens") or 0)
        if r.get("cost_usd") is not None:
            # rows from before billing was recorded are all subscription (no metered provider was set up then)
            k = "billed" if r.get("billing") == "metered" else "estimate"
            v[k] = (v[k] or 0.0) + float(r["cost_usd"])
    if not per:
        return ""

    def cost(v: dict) -> str:
        parts = [] if v["billed"] is None else ["$%.2f" % v["billed"]]
        if v["estimate"] is not None:
            parts.append(_("≈${amount} (subscription, not billed)", amount="%.2f" % v["estimate"]))
        return esc(" + ".join(parts)) if parts else "—"
    rows = "".join(f"<tr><td>{esc(p)}</td><td>{v['calls']}</td><td>{v['today']}</td><td>{v['tokens']:,}</td>"
                   f"<td>{cost(v)}</td></tr>" for p, v in sorted(per.items()))
    return (f"<h2 style=\"font-size:18px;margin-top:28px\">{_('Model calls by project (7 days)')}</h2>"
            f"<table><tr><th>{_('Project')}</th><th>{_('Calls')}</th><th>{_('Today')}</th><th>{_('Tokens')}</th>"
            f"<th>{_('Cost')}</th></tr>{rows}</table>")


def main() -> None:
    import hubconf  # imported further down too: put first so the whole function sees it
    now = datetime.datetime.now()
    import scheduler  # schedule.yaml + the project registry, the same list the scheduler uses
    jobs, reg_info = scheduler.all_jobs()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    runs, last_tick = scheduler_runs()
    alive = scheduler_alive()

    alerts, rows, report_blocks = [], [], []
    if not alive:
        alerts.append(("bad", _("The scheduler (local.agent-hub-scheduler) is not loaded: nothing scheduled runs. "
                                "On the hub machine, run schedulectl start")))
    else:
        # scheduler.py writes its log only when a job runs, but saves its state file on every tick -- use that file's
        # mtime to tell whether the scheduler is still running (going by the log raises false alarms in night hours
        # with no jobs)
        tick = datetime.datetime.fromtimestamp(STATE.stat().st_mtime) if STATE.exists() else None
        if tick is None or now - tick > datetime.timedelta(minutes=40):
            alerts.append(("bad", _("The scheduler's last tick was {tick:%m/%d %H:%M}, over 40 minutes ago", tick=tick) if tick
                           else _("The scheduler's state file is missing")))

    # The scheduler's watchdog (scheduler-watchdog.py, a separate launchd job): this page is produced by the scheduler
    # itself, so it stops updating when the scheduler stops -- the one that actually raises the alarm is the watchdog,
    # so when it is not on watch, this page has to say so
    sm = hubconf.service_manager()
    if sm == "systemd" and __import__("systemd_units").state("local.agent-hub-scheduler-watchdog") != "running":
        alerts.append(("bad", _("The scheduler watchdog (local.agent-hub-scheduler-watchdog.timer) is not scheduled: "
                                "nobody is told when the scheduler stops. Run agenthub init")))
    elif sm is None:
        t = _("{text}.", text=hubconf.no_service_manager())
        alerts.append(("bad", t[:1].upper() + t[1:]))   # an alert line starts upper-case (a no-op on CJK)
    elif sm == "launchd" and subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/local.agent-hub-scheduler-watchdog"],
                                            capture_output=True).returncode != 0:
        alerts.append(("bad", _("The scheduler watchdog (local.agent-hub-scheduler-watchdog) is not loaded: nobody is told "
                                "when the scheduler stops. On the hub machine, run "
                                "bin/recover-service.sh local.agent-hub-scheduler-watchdog")))
    elif not hubconf.get("alerts"):
        alerts.append(("notify", _("No alerts set up (hub.json has no alerts): a stopped scheduler or a dead service only "
                                   "shows on this page and in the log; nobody is notified")))
    elif not all((hubconf.secrets_dir() / f).exists() for f in ("discord-token", "discord-alert-channel")):
        alerts.append(("bad", _("The scheduler watchdog has no Discord settings (.secrets/discord-token, .secrets/discord-alert-channel): "
                                "it cannot alert when the scheduler stops")))

    # alerts that could not be sent (recorded by bin/alert.py; Discord token invalid, network down…): must not pass
    # silently
    try:
        import alert
        for key, f in sorted(alert.failures().items()):
            alerts.append(("bad", _("A Discord alert could not be sent ({key}, {at}): {text}. Check that .secrets/discord-token "
                                    "still works; it is tried again next time",
                                    key=key, at=f.get('at', '')[:16], text=f.get('text', ''))))
    except Exception as e:  # the status page must not crash because of this
        alerts.append(("bad", _("Cannot read the alert state: {e}", e=e)))

    # stage two: unattended agent jobs must declare allow: (dontAsk) in the registry; list those that have not yet
    # (auto for now)
    legacy_auto = [j["name"] for j in jobs if "run-agent-job.sh" in j["command"] and "AGENT_JOB_ALLOW=" not in j["command"]
                   and j.get("enabled", True)]
    if legacy_auto:
        alerts.append(("notify", _("These Claude jobs have not declared allow: in the registry (so unattended they run in auto "
                                   "mode, not dontAsk): {jobs}", jobs=', '.join(legacy_auto))))

    config_note = ""
    # config pipeline (new-user.md 3.7): which config commit is in effect; remind when a change is not applied yet
    try:
        import appliers
        cur = appliers.config_commit()
        rec = json.loads(appliers.applied_path().read_text()) if appliers.applied_path().exists() else None
        config_note = f" · config {rec['commit'][:7]}" if rec else ""
        if rec and not rec.get("ok", True):
            alerts.append(("bad", _("The last agenthub config apply left differences from the config ({at}): agenthub config "
                                    "plan shows which", at=rec.get('at', '')[:16])))
        elif rec and rec["commit"] != cur:
            alerts.append(("notify", _("The config has changed but has not been applied (agenthub config apply; applied: {applied}; "
                                       "now: {current}{dirty}). The 15-minute jobs catch up services and project settings, "
                                       "but only agenthub config apply records it", applied=rec['commit'][:7],
                                       current=cur[:7], dirty=_(" + uncommitted changes") if cur.endswith('+dirty') else '')))
    except Exception as e:
        alerts.append(("bad", _("Cannot read the config apply record: {e}", e=e)))

    for proj, errs in sorted(reg_info["errors"].items()):
        alerts.append(("bad", _("Registry entry {project} is invalid (the last valid one is used; with none, its jobs are "
                                "not scheduled): {errors}{more}. `bin/registry.py check {project}`", project=proj,
                                errors=_("; ").join(errs[:4]),
                                more=_("; … ({n} in all)", n=len(errs)) if len(errs) > 4 else "")))

    for job in jobs:
        name = job["name"]
        is_agent = "run-agent-job.sh" in job["command"]
        # a registry agent job's state directory uses the old name or <project>-<job>
        aname = job.get("agent_name", name)
        run = runs.get(name, {})
        result, detail = run.get("result", ""), ""
        last = run.get("started", "")[5:16]
        if is_agent and (st := agent_status(aname)):
            result = st.get("state", result)
            last = (st.get("started_at") or "")[5:16].replace("T", " ")
            detail = st.get("summary") or st.get("error") or ""
            if st.get("state") == "failed":
                alerts.append(("bad", _("{name} failed: {error}", name=name, error=st.get('error') or st.get('stderr') or '')))
            fin = st.get("finished_at") or ""
            if st.get("notify") == "yes" and fin and \
                    now - datetime.datetime.fromisoformat(fin).replace(tzinfo=None) < datetime.timedelta(days=7):
                alerts.append(("notify", _("{name}: {summary}", name=name, summary=st.get('summary', ''))))
            if (AGENT_JOBS / aname / "STOP").exists():
                detail = _("Stopped itself: {reason}", reason=(AGENT_JOBS / aname / "STOP").read_text().strip()[:120])
        elif result.startswith("failed"):
            # a success on the next tick cancels it; remind only when the last run failed
            alerts.append(("bad", _("{name} failed on its last run (exit {code}); log: .logs/{log}-<date>.log", name=name,
                                    code=run.get("code", "?"), log=name.replace('/', '__'))))
        cls = {"ok": "ok", "running": "run"}.get(result, "bad" if result.startswith("failed") else "")
        label = {"ok": _("succeeded"), "running": _("running"), "failed": _("failed")}.get(result.split(" ")[0], result or "—")
        rows.append(
            f"<tr class='{'off' if not job.get('enabled', True) else ''}'>"
            f"<td><b>{esc(name)}</b>{' <span class=tag>Claude</span>' if is_agent else ''}"
            f"{'<div class=sub>' + esc(detail) + '</div>' if detail else ''}</td>"
            f"<td>{esc(describe_when(job))}</td>"
            f"<td><span class='st {cls}'>{esc(label)}</span> <span class=sub>{esc(last)}</span></td>"
            f"<td>{esc(next_run(job, state, now))}</td></tr>")
        if is_agent:
            reps = agent_reports(aname)
            if reps:
                items = "".join(
                    f"<details{' open' if i == 0 else ''}><summary>{esc(r.stem)}</summary>"
                    f"<pre>{esc(r.read_text(errors='replace'))}</pre></details>"
                    for i, r in enumerate(reps))
                report_blocks.append(f"<section><h3>{esc(name)}</h3>{items}</section>")

    # encrypted cloud backup (bin/backup.py): red when the last success or the last restore test is too long ago
    bdir = HUB / ".state" / "backup"
    for fname, limit_h, what in (("last.json", 36, _("backup")), ("last-verify.json", 40 * 24, _("restore test"))):
        f = bdir / fname
        try:
            at = datetime.datetime.fromisoformat(json.loads(f.read_text())["at"]).replace(tzinfo=None)
            if now - at > datetime.timedelta(hours=limit_h):
                alerts.append(("bad", _("The last successful {what} was {at:%m/%d %H:%M}, too long ago "
                                        "(see .logs/backup__backup*)", what=what, at=at)))
        except (OSError, ValueError, KeyError):
            alerts.append(("bad", _("No successful {what} on record yet (.state/backup/{fname})", what=what, fname=fname)))

    # project mailboxes / event bus (bin/hubbus): stuck letters, ones that cannot be delivered to the assistant, a
    # broken bus.db moved aside
    mail_html = ""
    r = subprocess.run([sys.executable, str(HUB / "bin" / "hubbus"), "status", "--json"], capture_output=True, text=True)
    try:
        bus = json.loads(r.stdout)
    except ValueError:
        bus = None
        alerts.append(("bad", _("Cannot read the project mailbox state (hubbus status exit {code}): {stderr}",
                                code=r.returncode, stderr=r.stderr.strip()[-160:])))
    if bus:
        for st in bus["stuck"]:
            alerts.append(("bad", _("Letter {id} to {to}: unclaimed for over an hour: no session took it and the platform did "
                                    "not handle it (see the hubbus dispatch log and the daily limit)", id=st['id'], to=st['to'])))
        tm = bus["to_muninn"]
        if tm["n"] and now - datetime.datetime.fromisoformat(tm["oldest"]).replace(tzinfo=None) > datetime.timedelta(hours=1):
            alerts.append(("bad", _("{n} {n:item|items} for the assistant (letters to the owner, handoff closures) {n:is|are} "
                                    "stuck; the oldest is from {oldest}; see .state/bus/hubbus.log", n=tm['n'], oldest=tm['oldest'][5:16])))
        for f in bus["job_failed_24h"]:
            if f.get("job") == "hubbus-bridge":
                alerts.append(("bad", _("hubbus: {detail}", detail=f.get('detail', ''))))
        if bus["corrupt"]:
            alerts.append(("bad", _("bus.db was corrupt; the old file was moved to {files} (letters in it that "
                                    "had not been received are lost)", files=', '.join(bus['corrupt']))))
        mail_rows = "".join(f"<tr><td>{esc(p)}</td><td>{v['open']}</td><td>{v['undelivered']}</td>"
                            f"<td>{esc(v['oldest'][5:16].replace('T', ' '))}</td></tr>" for p, v in bus["projects"].items())
        mail_html = (f"<h2 style=\"font-size:18px;margin-top:28px\">{_('Project mailboxes')}</h2>"
                     + (f"<table><tr><th>{_('To')}</th><th>{_('Open')}</th><th>{_('Not yet in a session')}</th>"
                        f"<th>{_('Oldest')}</th></tr>{mail_rows}</table>"
                        if mail_rows else f"<div class=sub>{_('No open letters')}</div>"))

    # worktrees of parallel sessions / the merge gate (bin/worktrees.py, bin/merge-branch)
    wt_html = ""
    try:
        import worktrees
        ov = worktrees.overview()
        wt_rows = []
        for proj, info in ov.items():
            for w in info["worktrees"]:
                state_txt = _("merged") + (_(", can be cleaned up (bin/prune-worktrees)") if w["clean"]
                                           else _(", has uncommitted changes")) if w["merged"] \
                    else _("in progress, {n} {n:file|files} changed", n=len(w['files']))
                hot = any(f in info["overlaps"] for f in w["files"]) and not w["merged"]
                wt_rows.append(f"<tr><td>{esc(proj)}</td><td>{esc(w['branch'])}</td><td>{age_text(w['age_days'])}</td>"
                               f"<td{' class=\'st bad\'' if hot else ''}>{esc(state_txt)}"
                               f"{_(' (another branch changes the same file)') if hot else ''}</td></tr>")
            for f, bs in info["overlaps"].items():
                alerts.append(("bad", _("{project}: {n} unmerged branches both change {file} ({branches}); whichever "
                                        "merges second will conflict on rebase", project=proj, n=len(bs), file=f,
                                        branches=', '.join(bs)) if len(bs) == 2 else
                               _("{project}: {n} unmerged branches all change {file} ({branches}); the ones merged later "
                                 "will conflict on rebase", project=proj, n=len(bs), file=f, branches=', '.join(bs))))
        ungated = {p: i["ungated_main_7d"] for p, i in ov.items() if i["ungated_main_7d"]}
        wt_html = (f"<h2 style=\"font-size:18px;margin-top:28px\">{_('Parallel sessions (worktrees)')}</h2>"
                   + (f"<table><tr><th>{_('Project')}</th><th>{_('Branch')}</th><th>{_('Last commit')}</th><th>{_('State')}</th></tr>"
                      f"{''.join(wt_rows)}</table>"
                      if wt_rows else f"<div class=sub>{_('No worktrees in progress')}</div>")
                   + ("<div class=sub style='margin-top:8px'>"
                      + _("Committed straight to main in the last 7 days (not through merge-branch): {list}",
                          list=_(", ").join(f"{esc(p)} {n}" for p, n in sorted(ungated.items()))) + "</div>" if ungated else "")
                   + prune_line())
        import hubconf
        bus_db = hubconf.bus_db()
        if bus_db.exists():
            import sqlite3
            for (pl,) in sqlite3.connect(bus_db, timeout=10).execute(
                    "SELECT payload FROM events WHERE type='merge.failed' AND ts > ? ORDER BY seq DESC LIMIT 5",
                    ((now - datetime.timedelta(days=1)).isoformat(),)):
                f = json.loads(pl)
                alerts.append(("bad", _("merge-branch failed: {project} {branch} ({stage}): {detail}", project=f.get('project'),
                                        branch=f.get('branch'), stage=f.get('stage'), detail=(f.get('detail') or '')[:160])))
    except Exception as e:  # a broken overview does not affect the rest of the page
        wt_html = f"<div class=sub>{_('Worktree overview failed: {e}', e=esc(repr(e)))}</div>"

    usage_html = model_usage_html(now)

    alert_html = "".join(f"<div class='alert {c}'>{'🔔' if c == 'notify' else '⚠️'} {esc(t)}</div>"
                         for c, t in alerts) or f"<div class='alert good'>✅ {_('Nothing needs attention')}</div>"
    generated = _("the hub machine · generated {now:%Y-%m-%d %H:%M} · refreshes every 5 minutes · schedules are defined "
                  "in the project registry (projects/) and schedule.yaml", now=now)
    page = f"""<!doctype html><html lang="{hubconf.lang_ui()}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="300">
<title>{_('Schedule status')}</title><style>
:root{{--bg:#fafaf9;--fg:#1c1917;--mut:#78716c;--line:#e7e5e4;--card:#fff;--ok:#15803d;--bad:#b91c1c;--run:#1d4ed8;--warn:#fef3c7}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1c1917;--fg:#f5f5f4;--mut:#a8a29e;--line:#44403c;--card:#292524;--ok:#4ade80;--bad:#f87171;--run:#93c5fd;--warn:#422006}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,"PingFang TC",sans-serif}}
main{{max-width:1000px;margin:0 auto;padding:20px 16px 60px}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:var(--mut);font-size:13px}}
.alert{{padding:10px 14px;border-radius:8px;margin:8px 0;background:var(--card);border:1px solid var(--line)}}
.alert.bad{{border-color:var(--bad)}} .alert.notify{{background:var(--warn)}}
table{{width:100%;border-collapse:collapse;background:var(--card);border-radius:8px;overflow:hidden;margin-top:16px}}
td,th{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{font-size:13px;color:var(--mut);font-weight:500}} tr.off{{opacity:.45}}
.st.ok{{color:var(--ok)}} .st.bad{{color:var(--bad);font-weight:600}} .st.run{{color:var(--run)}}
.tag{{font-size:11px;border:1px solid var(--line);border-radius:4px;padding:0 4px;color:var(--mut)}}
section{{margin-top:24px}} h3{{font-size:16px;margin:0 0 6px}}
details{{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:6px 0;padding:6px 12px}}
summary{{cursor:pointer;color:var(--mut)}} pre{{white-space:pre-wrap;word-break:break-word;font:14px/1.55 -apple-system,"PingFang TC",sans-serif}}
@media (max-width:640px){{td:nth-child(2),th:nth-child(2){{display:none}}}}
</style></head><body><main>
<h1>{_('Schedule status')}</h1><div class=sub><a href="digest.html">{_('Digest')}</a> · <a href="todo.html">{_('To-do')}</a> · <a href="reflect/">{_('Weekly review')}</a></div><div class=sub>{generated}{config_note}</div>
{alert_html}
<table><tr><th>{_('Job')}</th><th>{_('When')}</th><th>{_('Last')}</th><th>{_('Next')}</th></tr>{''.join(rows)}</table>
{mail_html}
{wt_html}{usage_html}
<h2 style="font-size:18px;margin-top:28px">{_('Claude job reports')}</h2>
{''.join(report_blocks) or f"<div class=sub>{_('No reports yet')}</div>"}
</main></body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(page)
    tmp.replace(OUT)
    write_reading_pages(now)


# ---------- three pages for people: digest, to-do, weekly review (new-user.md section 7, question 7: once data is
# out of git, this is the only place to read them) ----------

PAGE_CSS = """:root{--bg:#fafaf9;--fg:#1c1917;--mut:#78716c;--line:#e7e5e4;--card:#fff;--acc:#1d4ed8}
@media (prefers-color-scheme:dark){:root{--bg:#1c1917;--fg:#f5f5f4;--mut:#a8a29e;--line:#44403c;--card:#292524;--acc:#93c5fd}}
body{background:var(--bg);color:var(--fg);font:16px/1.6 -apple-system,"PingFang TC",sans-serif;margin:0}
main{max-width:860px;margin:0 auto;padding:16px} a{color:var(--acc)} nav{margin:4px 0 16px;color:var(--mut)}
section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:4px 16px;margin:12px 0}
h1{font-size:22px;margin:8px 0} h2{font-size:18px} h3{font-size:16px} code{font-size:14px;background:var(--line);padding:0 3px;border-radius:3px}
li{margin:2px 0} .sub{color:var(--mut);font-size:13px}"""
REFLECT_LINK = "reflect"   # name of the link in the status page folder pointing to data's reflect/


def nav() -> str:
    return (f'<nav><a href="index.html">{_("Schedule status")}</a> · <a href="digest.html">{_("Digest")}</a> · '
            f'<a href="todo.html">{_("To-do")}</a> · <a href="reflect/">{_("Weekly review")}</a></nav>')


def md_html(text: str) -> str:
    """A very small markdown -> HTML (headings, lists, paragraphs, `code`, **bold**, [text](link)); escapes first,
    accepts no raw HTML."""
    def inline(t: str) -> str:
        t = esc(t)
        t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
        t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
        return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', t)
    out, in_list = [], False
    for line in re.sub(r"<!--.*?-->", "", text, flags=re.S).splitlines():
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        item = re.match(r"^\s*[-*]\s+(?:\[ \]\s*)?(.*)", line)
        if in_list and not item:
            out.append("</ul>")
            in_list = False
        if m:
            n = min(len(m.group(1)) + 1, 4)
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
        elif item:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(item.group(1))}</li>")
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def reading_page(title: str, body: str, now) -> str:
    return (f'<!doctype html><html lang="{hubconf.lang_ui()}"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>{PAGE_CSS}</style></head>'
            f'<body><main><h1>{esc(title)}</h1>{nav()}<div class=sub>{_("generated {now:%Y-%m-%d %H:%M}", now=now)}</div>'
            f'{body}</main></body></html>')


def write_reading_pages(now) -> None:
    out = OUT.parent
    days = sorted(hubconf.data_file("digest").glob("20*.md"), reverse=True)[:7]
    body = "".join(f"<section>{md_html(f.read_text())}</section>" for f in days) or f"<p class=sub>{_('No digest yet')}</p>"
    (out / "digest.html").write_text(reading_page(_("Digest (last 7 days)"), body, now))
    todo = hubconf.data_file("todo.md")
    (out / "todo.html").write_text(reading_page(_("Cross-project to-do"), f"<section>{md_html(todo.read_text())}</section>"
                                                if todo.exists() else f"<p class=sub>{_('No todo.md yet')}</p>", now))
    link, target = out / REFLECT_LINK, hubconf.data_file("reflect")   # the weekly review is a site already: link to it
    if target.is_dir() and not (link.is_symlink() and link.resolve() == target.resolve()):
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target)


if __name__ == "__main__":
    main()
