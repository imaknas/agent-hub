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
import names  # noqa: E402
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
    return ("<div class='sub gap'>"
            + _("Worktree auto-cleanup ({at}): {parts}", at=esc(str(r.get('at', ''))[:16]), parts=_(", ").join(parts))
            + "</div>")


def watchdog_channels() -> list[str]:
    """The channels the scheduler watchdog can send on, by the same rules as its channels() (it imports nothing of the
    platform, so the rules are kept in both places; tests/test_watchdog_channels.py checks the watchdog's side)."""
    a = hubconf.get("alerts") or {}
    out = []
    def file(key: str, default: str) -> pathlib.Path:
        return hubconf.path(a[key]) if a.get(key) else hubconf.secrets_dir() / default
    if file("discord_token_file", "discord-token").exists() and file("discord_channel_file", "discord-alert-channel").exists():
        out.append("discord")
    if (a.get("ntfy") or {}).get("topic"):
        out.append("ntfy")
    if (a.get("email") or {}).get("host") and (a.get("email") or {}).get("to"):
        out.append("email")
    if a.get("macos") and sys.platform == "darwin":
        out.append("macos")
    return out


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
    if "trigger" in job:   # event jobs (docs/design/event-jobs.md)
        import event_jobs
        return event_jobs.describe(job) + (_(" (until {date})", date=job['end_date']) if "end_date" in job else "")
    return describe_clock(job)


def describe_clock(job: dict) -> str:
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
    if "trigger" in job:
        import event_jobs
        return event_jobs.waiting(job, state, now)
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


def event_detail(job: dict, state: dict, now: datetime.datetime, alerts: list) -> list[str]:
    """An event job's last batch and the events the loop chain held back today; an alert when an event has waited
    over a day (docs/design/event-jobs.md section 9)."""
    import event_jobs
    name, out = job["name"], []
    last = state.get(f"{name}.event_last")
    if last:
        out.append(_("last run: {n} {n:event|events}, {at}", n=last.get("count", 0),
                     at=str(last.get("at", ""))[5:16].replace("T", " ")))
    held = state.get(f"{name}.event_held") or {}
    if held.get("date") == now.strftime("%Y-%m-%d") and held.get("count"):
        out.append(_("{n} {n:event|events} held back today by the loop chain (schedulectl events {name})",
                     n=held["count"], name=name))
    if (since := event_jobs.stuck(job, state, now)):
        alerts.append(("notify", _("Event job {name} has had events waiting since {since:%m/%d %H:%M} (schedulectl events "
                                   "{name})", name=name, since=since)))
    return out


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
        return systemd_units.state(names.unit_label("-scheduler")) == "running"
    if hubconf.service_manager() != "launchd":
        return False   # no service manager (the alert below says so)
    r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{names.unit_label('-scheduler')}"],
                       capture_output=True, text=True)
    return r.returncode == 0


def agent_status(name: str) -> dict | None:
    f = AGENT_JOBS / name / "status.json"
    return json.loads(f.read_text()) if f.exists() else None


def agent_reports(name: str, n: int = 5) -> list[pathlib.Path]:
    return sorted((AGENT_JOBS / name).glob("20*.md"), reverse=True)[:n]


def esc(s: str) -> str:
    return html.escape(s or "")


def hue(project: str) -> int:
    """Each project keeps one of six colors, the same one the herdr panel gives it (integrations/herdr/mods/brunna/main.js hue())."""
    h = 0
    for c in project or "":
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    return h % 6 + 1


def proj_html(project: str) -> str:
    return f"<span class=proj><i class='dot c{hue(project)}'></i>{esc(project)}</span>"


def ago(at: str, now: datetime.datetime) -> str:
    """How long ago, the way the herdr panel says it (now, 5m, 3h, 2d); '' when the time can't be read."""
    try:
        t = datetime.datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return ""
    if t.tzinfo:
        t = t.astimezone().replace(tzinfo=None)
    m = max(0, int((now - t).total_seconds() // 60))
    return (_("now") if m < 1 else _("{n}m", n=m) if m < 60 else _("{n}h", n=m // 60) if m < 24 * 60
            else _("{n}d", n=m // (24 * 60)))


# the platform's mark (integrations/herdr/mods/brunna/mark.svg), drawn in the text color
MARK = ('<svg class=mark viewBox="0 0 256 256" aria-hidden="true"><g stroke="currentColor" stroke-width="14" '
        'stroke-linecap="round"><line x1="128" y1="69.5" x2="128" y2="103.5"/><line x1="195.5" y1="186.5" x2="166.1" '
        'y2="169.5"/><line x1="60.5" y1="186.5" x2="89.9" y2="169.5"/></g><g fill="currentColor"><circle cx="128" '
        'cy="69.5" r="22"/><circle cx="195.5" cy="186.5" r="22"/><circle cx="60.5" cy="186.5" r="22"/><circle cx="128" '
        'cy="147.5" r="26"/></g><circle cx="128" cy="147.5" r="44" fill="none" stroke="currentColor" stroke-width="14"/>'
        '</svg>')


def mail_failure(f: dict) -> bool:
    """A job.failed from a run the mailbox started, or one it couldn't start: its letters wait for a session, and this is
    the only word of why."""
    return str(f.get("job") or "").startswith(("mailbox-", "llm:mailbox-"))


def path_labels() -> dict[str, str]:
    """hubbus delivery_paths' steps, for people."""
    return {"routed": _("routed"), "woken": _("woken"), "woken-missed": _("woken, not picked up"), "wake-failed": _("wake failed"),
            "hook": _("hook"), "inbox": _("inbox"), "passed": _("passed on"),
            "ack": _("acked"), "resume": _("background copy"), "handler": _("agent dispatched"),
            "triage": _("agent triaged it"), "held": _("held (loop guard)")}


def wake_gap_label(gap: str) -> str:
    """Why an open session can't be woken (runtime_ports.WAKE_GAPS), for people."""
    return {"no_port": _("its coding agent has no way to wake a session"),
            "not_armed": _("nothing is armed to wake it: no turn has ended since it opened, or it has been idle past the limit"),
            "stopped": _("what waited to wake it was stopped: a hook installed with a shorter timeout, or the coding agent "
                         "itself")}.get(gap, gap)


def no_wake_line(nw: dict | None) -> str:
    """How many open sessions a letter couldn't wake now, and why; nothing when every one can, or it can't be told."""
    if not nw or not nw.get("n"):
        return ""
    why = _("; ").join(_("{n}: {why}", n=k, why=wake_gap_label(g)) for g, k in nw["why"].items())
    return "<div class=sub>" + esc(_("{n} of {open} open {open:session|sessions} can't be woken by a letter ({why})",
                                     n=nw["n"], open=nw["open"], why=why)) + "</div>"


def waiting_label(w: dict) -> str:
    """Why a request/question in hubbus status's waiting list waits, for people."""
    if w.get("why") == "routed":
        return _("session {session} has it", session=w["session"][:8]) if w.get("session") else _("seen, held for now")
    return {"triaged": _("triaged, waiting for a session"), "held": _("the loop guard asked the owner"),
            "old": _("too old for a platform run"), "budget": _("today's runs or wakes are used up"),
            "refused": _("no propose run for this project; its sessions handle it")}.get(w.get("why"), w.get("why", ""))


def letter_paths(letters: list) -> str:
    """How each request/question of the last day went (docs/design/mailbox-runtimes.md B1): woken (or woken, not picked
    up: the woken session didn't take it in time) / hook / background copy / dispatched, in order."""
    if not letters:
        return ""
    labels = path_labels()
    rows = "".join(f"<tr><td>{esc(x['id'])}</td><td>{proj_html(x['to'])}</td><td>{esc(x['ts'][5:16].replace('T', ' '))}</td>"
                   f"<td>{esc(' → '.join(labels.get(s, s) for s in x.get('path') or []) or _('waiting'))}"
                   f"{esc(_(' (done)')) if x.get('done') else ''}</td></tr>" for x in letters)
    return (f"<div class=sub>{esc(_('How the requests and questions of the last day went'))}</div>"
            f"<div class=tbl><table><tr><th>{_('Letter')}</th><th>{_('To')}</th><th>{_('Sent')}</th><th>{_('Path')}</th></tr>"
            f"{rows}</table></div>")


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
    rows = "".join(f"<tr><td>{proj_html(p)}</td><td>{v['calls']}</td><td>{v['today']}</td><td>{v['tokens']:,}</td>"
                   f"<td>{cost(v)}</td></tr>" for p, v in sorted(per.items()))
    return (f"<h2>{_('Model calls by project (7 days)')}</h2>"
            f"<div class=tbl><table><tr><th>{_('Project')}</th><th>{_('Calls')}</th><th>{_('Today')}</th><th>{_('Tokens')}</th>"
            f"<th>{_('Cost')}</th></tr>{rows}</table></div>")


def waiting_html() -> str:
    """The newest waiting report (bin/test-waiting-report.py, test-waiting.md B7): per project, how long agents waited on
    tests, how much of it the owner sat through, and the runner's time to a merge's result and its hits. Nothing before
    the first report."""
    files = sorted(hubconf.data_file("test-waiting").glob("*.json"))
    try:
        rep = json.loads(files[-1].read_text()) if files else None
    except (OSError, ValueError):
        rep = None
    if not isinstance(rep, dict) or not isinstance(rep.get("projects"), dict):
        return ""
    rows = []
    for name, p in sorted(rep["projects"].items()):
        s, r = p.get("sessions") or {}, p.get("runner") or {}
        tests = s.get("tests_min") or 0
        if not tests and not r.get("tiers") and "unavailable" not in r:
            continue
        agent = s.get("agent_min") or 0
        share = f" ({round(100 * tests / agent)}%)" if agent else ""
        merge = (r.get("callers") or {}).get("merge-branch") or {}
        runs = sum(t.get("runs", 0) for t in (r.get("tiers") or {}).values())
        hits = sum(t.get("hits", 0) for t in (r.get("tiers") or {}).values())
        runner_txt = (_("could not be read") if "unavailable" in r else
                      _("{hits} of {runs} tier runs were hits", hits=hits, runs=runs) if runs else "—")
        median = merge.get("median_s")
        rows.append(f"<tr><td>{proj_html(name)}</td><td>{esc(_('{min} min', min=agent))}</td>"
                    f"<td>{esc(_('{min} min', min=tests) + share)}</td>"
                    f"<td>{esc(_('{min} min', min=s.get('owner_waited_min') or 0))}</td>"
                    f"<td>{esc(_('{secs} s', secs=median) if median is not None else '—')}</td>"
                    f"<td>{esc(runner_txt)}</td></tr>")
    if not rows:
        return ""
    return (f"<h2>"
            f"{esc(_('Waiting on tests ({days} days to {end})', days=rep.get('days'), end=str(rep.get('to', ''))[:10]))}</h2>"
            f"<div class=tbl><table><tr><th>{_('Project')}</th><th>{_('Agent time')}</th><th>{_('Waiting on tests')}</th>"
            f"<th>{_('The owner waited')}</th><th>{_('Merge to result (median)')}</th><th>{_('Recorded passes')}</th></tr>"
            f"{''.join(rows)}</table></div><div class='sub gap'>"
            f"{_('Details: bin/test-waiting-report.py (docs/design/test-waiting.md B7)')}</div>")


def main() -> None:
    import hubconf  # imported further down too: put first so the whole function sees it
    now = datetime.datetime.now()
    import scheduler  # schedule.yaml + the project registry, the same list the scheduler uses
    jobs, reg_info = scheduler.all_jobs()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    runs, last_tick = scheduler_runs()
    alive = scheduler_alive()

    alerts, rows, report_blocks = [], [], []
    sm = hubconf.service_manager()
    # scheduler.py writes its log only when a job runs, but saves its state file on every tick -- use that file's
    # mtime to tell whether the scheduler is still running (going by the log raises false alarms in night hours with
    # no jobs)
    tick = datetime.datetime.fromtimestamp(STATE.stat().st_mtime) if STATE.exists() else None
    stale = tick is None or now - tick > datetime.timedelta(minutes=40)
    if sm is None:
        # nothing to load it into: one line, red only while nothing runs it (a cron line that ticks is fine)
        if stale:
            t = _("{reason}: nothing scheduled runs until you {how}", reason=hubconf.no_service_manager(),
                  how=hubconf.manual_scheduler())
        else:
            t = _("{reason}; the scheduler is ticking, so something else runs it (the watchdog does not: nobody is told "
                  "if it stops)", reason=hubconf.no_service_manager())
        alerts.append(("bad" if stale else "notify", t[:1].upper() + t[1:]))   # starts upper-case (a no-op on CJK)
    elif not alive:
        alerts.append(("bad", _("The scheduler (local.brunna-scheduler) is not loaded: nothing scheduled runs. "
                                "On the hub machine, run schedulectl start")))
    elif stale:
        alerts.append(("bad", _("The scheduler's last tick was {tick:%m/%d %H:%M}, over 40 minutes ago", tick=tick) if tick
                       else _("The scheduler's state file is missing")))

    # The scheduler's watchdog (scheduler-watchdog.py, a separate launchd job): this page is produced by the scheduler
    # itself, so it stops updating when the scheduler stops -- the one that actually raises the alarm is the watchdog,
    # so when it is not on watch, this page has to say so (with no service manager, the line above already does)
    if sm == "systemd" and __import__("systemd_units").state(names.unit_label("-scheduler-watchdog")) != "running":
        alerts.append(("bad", _("The scheduler watchdog (local.brunna-scheduler-watchdog.timer) is not scheduled: "
                                "nobody is told when the scheduler stops. Run brunna init")))
    elif sm is None:
        pass
    elif sm == "launchd" and subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{names.unit_label('-scheduler-watchdog')}"],
                                            capture_output=True).returncode != 0:
        alerts.append(("bad", _("The scheduler watchdog (local.brunna-scheduler-watchdog) is not loaded: nobody is told "
                                "when the scheduler stops. On the hub machine, run "
                                "bin/recover-service.sh local.brunna-scheduler-watchdog")))
    elif hubconf.get("alerts") and not watchdog_channels():
        alerts.append(("bad", _("The scheduler watchdog has no alert channel it can send on (Discord, ntfy, email or macOS "
                                "notifications in {settings} alerts): it cannot alert when the scheduler stops")))

    # no alert channel at all: one setup reminder, not red (a feature that is off does not turn the page red)
    if not hubconf.get("alerts"):
        alerts.append(("notify", _("No alerts set up ({settings} has no alerts): a stopped scheduler or a dead service only "
                                   "shows on this page and in the log; nobody is notified")))
    # alerts that could not be sent (recorded by bin/alert.py; Discord token invalid, network down…): must not pass
    # silently
    try:
        import alert
        channels = alert.configured()
        for key, f in sorted(alert.failures().items()):
            if "discord" in channels:
                alerts.append(("bad", _("A Discord alert could not be sent ({key}, {at}): {text}. Check that .secrets/"
                                        "discord-token still works; it is tried again next time",
                                        key=key, at=f.get('at', '')[:16], text=f.get('text', ''))))
            else:
                alerts.append(("bad", _("An alert could not be sent ({key}, {at}): {text}. Check the channels in {settings} "
                                        "alerts ({channels}); it is tried again next time", key=key,
                                        at=f.get('at', '')[:16], text=f.get('text', ''), channels=_(", ").join(channels))))
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
            alerts.append(("bad", _("The last brunna config apply left differences from the config ({at}): brunna config "
                                    "plan shows which", at=rec.get('at', '')[:16])))
        elif rec and rec["commit"] != cur:
            alerts.append(("notify", _("The config has changed but has not been applied (brunna config apply; applied: {applied}; "
                                       "now: {current}{dirty}). The 15-minute jobs catch up services and project settings, "
                                       "but only brunna config apply records it", applied=rec['commit'][:7],
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
        if "trigger" in job:
            detail = "; ".join(x for x in [detail, *event_detail(job, state, now, alerts)] if x)
        cls = {"ok": "ok", "running": "run"}.get(result, "bad" if result.startswith("failed") else "")
        label = {"ok": _("succeeded"), "running": _("running"), "failed": _("failed")}.get(result.split(" ")[0], result or "—")
        dot = f"<i class='dot c{hue(name.split('/')[0])}'></i>" if "/" in name else "<i class='dot none'></i>"
        rows.append(
            f"<tr class='job{' off' if not job.get('enabled', True) else ''}'>"
            f"<td class=name>{dot}<b>{esc(name)}</b>{' <span class=tag>Claude</span>' if is_agent else ''}"
            f"{'<div class=sub>' + esc(detail) + '</div>' if detail else ''}</td>"
            f"<td class=when>{esc(describe_when(job))}</td>"
            f"<td class=last><span class='st {cls}'>{esc(label)}</span> <span class=sub>{esc(last)}</span></td>"
            f"<td class=next data-l='{esc(_('Next'))}'>{esc(next_run(job, state, now))}</td></tr>")
        if is_agent:
            reps = agent_reports(aname)
            if reps:
                items = "".join(
                    f"<details{' open' if i == 0 else ''}><summary>{esc(r.stem)}</summary>"
                    f"<pre>{esc(r.read_text(errors='replace'))}</pre></details>"
                    for i, r in enumerate(reps))
                report_blocks.append(f"<section class=report><h3>{dot}{esc(name)}</h3>{items}</section>")

    # encrypted cloud backup (bin/backup.py): red when the last success or the last restore test is too long ago; only
    # when the backup module is on (a feature that is off does not turn the page red)
    bdir = HUB / ".state" / "backup"
    backup_on = (hubconf.get("modules") or {}).get("backup") is True
    for fname, limit_h, what in (("last.json", 36, _("backup")), ("last-verify.json", 40 * 24, _("restore test"))) \
            if backup_on else ():
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
            elif mail_failure(f):
                alerts.append(("bad", _("A mailbox run failed ({job}): {detail}", job=f.get("job"), detail=f.get("detail", ""))))
        if bus["corrupt"]:
            alerts.append(("bad", _("bus.db was corrupt; the old file was moved to {files} (letters in it that "
                                    "had not been received are lost)", files=', '.join(bus['corrupt']))))
        for x in bus.get("record_conflicts") or []:   # core §10: a sender reused a record id for other content
            alerts.append(("notify", _("Record {id} of {project} ({ext} {type}, origin {origin}) was sent with different "
                                       "content ({n} versions, last {last}); the bus keeps them all and readers take "
                                       "{kept}, the greater RFC 8785 form (core §10). The sender should not reuse an id",
                                       id=x["id"], project=x["project"], ext=x["ext"], type=x["type"],
                                       origin=x["origin"], n=len(x["events"]), last=x["last"][5:16].replace("T", " "),
                                       kept=x["kept"])))
        mail_rows = "".join(f"<tr><td>{proj_html(p)}</td><td>{v['open']}</td><td>{v['undelivered']}</td>"
                            f"<td>{esc(v['oldest'][5:16].replace('T', ' '))}</td></tr>" for p, v in bus["projects"].items())
        mail_html = (f"<h2>{_('Project mailboxes')}</h2>"
                     + (f"<div class=tbl><table><tr><th>{_('To')}</th><th>{_('Open')}</th><th>{_('Not yet in a session')}</th>"
                        f"<th>{_('Oldest')}</th></tr>{mail_rows}</table></div>"
                        if mail_rows else f"<div class=sub>{_('No open letters')}</div>")
                     # held for an MCP session using the project (hubbus mcp_sessions), not unclaimed
                     + "".join("<div class=sub>" + esc(_("Held: {id} ({project} in use by {by})", id=h["id"],
                                                         project=h["to"], by=", ".join(h.get("by") or ["mcp"])))
                               + "</div>" for h in bus.get("held", []))
                     # routed to a session, or the platform has no run left to start: waiting, not stuck
                     + "".join("<div class=sub>" + esc(_("Waiting: {id} ({project}: {why})", id=w["id"], project=w["to"],
                                                         why=waiting_label(w))) + "</div>"
                               for w in bus.get("waiting", []))
                     + no_wake_line(bus.get("no_wake"))
                     + letter_paths(bus.get("letters") or []))

    # worktrees of parallel sessions / the merge gate (bin/worktrees.py, bin/merge-branch)
    wt_html = ""
    try:
        import worktrees
        ov = worktrees.overview()
        wt_rows = []
        for proj, info in ov.items():
            if info.get("unavailable"):   # its git runs only as the project, and that didn't work: said, not hidden
                alerts.append(("bad", _("{project}: its worktrees could not be read ({why})", project=proj,
                                        why=info["unavailable"][:200])))
            for w in info["worktrees"]:
                state_txt = _("merged") + (_(", can be cleaned up (bin/prune-worktrees)") if w["clean"]
                                           else _(", has uncommitted changes")) if w["merged"] \
                    else _("in progress, {n} {n:file|files} changed", n=len(w['files']))
                hot = any(f in info["overlaps"] for f in w["files"]) and not w["merged"]
                wt_rows.append(f"<tr><td>{proj_html(proj)}</td><td>{esc(w['branch'])}</td><td>{age_text(w['age_days'])}</td>"
                               f"<td{' class=\'st bad\'' if hot else ''}>{esc(state_txt)}"
                               f"{_(' (another branch changes the same file)') if hot else ''}</td></tr>")
            for f, bs in info["overlaps"].items():
                alerts.append(("bad", _("{project}: {n} unmerged branches both change {file} ({branches}); whichever "
                                        "merges second will conflict on rebase", project=proj, n=len(bs), file=f,
                                        branches=', '.join(bs)) if len(bs) == 2 else
                               _("{project}: {n} unmerged branches all change {file} ({branches}); the ones merged later "
                                 "will conflict on rebase", project=proj, n=len(bs), file=f, branches=', '.join(bs))))
        ungated = {p: i["ungated_main_7d"] for p, i in ov.items() if i["ungated_main_7d"]}
        wt_html = (f"<h2>{_('Parallel sessions (worktrees)')}</h2>"
                   + (f"<div class=tbl><table><tr><th>{_('Project')}</th><th>{_('Branch')}</th><th>{_('Last commit')}</th><th>{_('State')}</th></tr>"
                      f"{''.join(wt_rows)}</table></div>"
                      if wt_rows else f"<div class=sub>{_('No worktrees in progress')}</div>")
                   + ("<div class='sub gap'>"
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
    try:
        waiting = waiting_html()
    except Exception as e:  # a broken report does not affect the rest of the page
        waiting = f"<div class=sub>{_('Waiting report failed: {e}', e=esc(repr(e)))}</div>"

    needs, needs_error = needs_you()
    problems = [t for c, t in alerts if c == "bad"]
    notices = [t for c, t in alerts if c == "notify"]
    sched_ok = not stale and (sm is None or alive)
    mail_projects = (bus or {}).get("projects") or {}
    letters = sum(v.get("open", 0) for v in mail_projects.values())

    def tile(n, label: str, cls: str = "") -> str:
        return f"<div class='tile {cls}'><b>{esc(str(n))}</b><span>{esc(label)}</span></div>"
    pill = (f"<span class='pill ok'><i class=dot></i>{_('Healthy')}</span>" if not problems else
            f"<span class='pill bad'><i class=dot></i>{esc(_('{n} {n:problem|problems}', n=len(problems)))}</span>")
    tiles = (tile(len(needs), _("need you"), "hot" if needs else "")
             + tile(letters, _("letter open") if letters == 1 else _("letters open"))
             + tile(len(problems), _("problem") if len(problems) == 1 else _("problems"), "hot" if problems else "")
             + tile(f"{tick:%H:%M}" if tick else "—", _("scheduler ticking") if sched_ok else _("scheduler stopped"),
                    "" if sched_ok else "hot"))

    need_rows = "".join(
        f"<li class=item title='{esc(x['text'])}'><span class='kind {x['kind']}'>{'?' if x['kind'] == 'question' else ''}"
        f"</span><div class=body><div class=l1>{proj_html(x['project'])}<span class=ago>{esc(ago(x['since'], now))}"
        f"</span></div><div class=l2>{esc(x['text'])}</div></div></li>" for x in needs[:SHOW])
    more = (f"<a class=more href='todo.html'>{esc(_('{n} more on the to-do page', n=len(needs) - SHOW))}</a>"
            if len(needs) > SHOW else "")
    needs_card = (f"<section class=card><h2>{_('Needs you')}<span class=sub>{_('newest first')}</span></h2>"
                  + (f"<ul class=list>{need_rows}</ul>{more}" if needs else
                     f"<div class=empty><i class='dot good'></i>{_('Nothing needs you right now.')}</div>")
                  + (f"<div class=sub>{esc(_('What needs you could not be read: {e}', e=needs_error))}</div>"
                     if needs_error else "") + "</section>")
    mail_list = "".join(f"<li class=mail>{proj_html(p)}<span class=ago>{esc(ago(v.get('oldest', ''), now))}</span>"
                        f"<span class=badge>{v.get('open', 0)}</span></li>" for p, v in mail_projects.items())
    mail_card = (f"<section class=card><h2>{_('Mail')}<span class=sub>{_('open letters by project')}</span></h2>"
                 + (f"<ul class=list>{mail_list}</ul>" if mail_list else
                    f"<div class=empty><i class='dot good'></i>{_('No open letters')}</div>") + "</section>")

    def alert_list(items: list[str], cls: str) -> str:
        """the first few; a long list folds the rest away so the jobs stay in reach"""
        def ul(xs):
            return f"<ul class='alerts {cls}'>" + "".join(f"<li><i class=dot></i><span>{esc(t)}</span></li>"
                                                          for t in xs) + "</ul>"
        rest = items[FOLD:] if len(items) > FOLD + 1 else []
        return ul(items[:len(items) - len(rest)]) + (
            f"<details class=rest><summary>{esc(_('{n} more', n=len(rest)))}</summary>{ul(rest)}</details>" if rest else "")
    alert_html = ((f"<section class='card wide'><h2>{_('Problems')}<span class=sub>{len(problems)}</span></h2>"
                   f"{alert_list(problems, 'bad')}</section>" if problems else "")
                  + (f"<section class='card wide'><h2>{_('Worth a look')}<span class=sub>{len(notices)}</span></h2>"
                     f"{alert_list(notices, 'notify')}</section>" if notices else "")
                  or f"<section class='card wide'><div class=empty><i class='dot good'></i>"
                     f"{_('Nothing needs attention')}</div></section>")
    generated = _("the hub machine · generated {now:%Y-%m-%d %H:%M} · refreshes every 5 minutes · schedules are defined "
                  "in the project registry (projects/) and schedule.yaml", now=now)
    page = f"""<!doctype html><html lang="{hubconf.lang_ui()}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="300">
<title>{_('Schedule status')}</title><style>{STATUS_CSS}</style></head><body><main>
<header class=top><div class=brand>{MARK}<div><div class=name>brunna</div><h1>{_('Schedule status')}</h1></div></div>{pill}</header>
<nav class=links><a href="digest.html">{_('Digest')}</a> · <a href="todo.html">{_('To-do')}</a> · <a href="reflect/">{_('Weekly review')}</a></nav>
<div class=tiles>{tiles}</div>
<div class=grid>{needs_card}{mail_card}{alert_html}</div>
<h2>{_('Scheduled jobs')}<span class=sub>{len(jobs)}</span></h2>
<div class=tbl><table class=jobs><thead><tr><th>{_('Job')}</th><th>{_('When')}</th><th>{_('Last')}</th><th>{_('Next')}</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
{mail_html}
{wt_html}{waiting}{usage_html}
<h2>{_('Claude job reports')}</h2>
{''.join(report_blocks) or f"<div class=sub>{_('No reports yet')}</div>"}
<footer class=sub>{generated}{config_note}</footer>
</main></body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(page)
    tmp.replace(OUT)
    write_glance(now, alerts, (needs, needs_error))
    write_reading_pages(now)


GLANCE = hubconf.STATE / "status" / "glance.json"   # bin/glance.py reads it there
FOLD = 6   # problems / notices shown before the rest folds away
SHOW = 8   # items listed under "Needs you", as the herdr panel; the rest are on the to-do page

# the look of the herdr panel (integrations/herdr/mods/brunna/main.js): a header with the health pill, number tiles, cards of two-line
# rows, one color per project (c1..c6, the panel's ansi1..6)
STATUS_CSS = """
:root{--bg:#f6f6f3;--surface:#fff;--raised:#f1f0ec;--fg:#1f1f1c;--mut:#62625c;--dim:#8e8d86;--line:#e4e3de;
--acc:#3a66d0;--ok:#2e9a58;--bad:#d1435b;--warn:#b97f10;--hover:#f3f2ee;
--c1:#d1435b;--c2:#2e9a58;--c3:#c08a12;--c4:#3a66d0;--c5:#9255c9;--c6:#1497a8}
@media (prefers-color-scheme:dark){:root{--bg:#16171a;--surface:#1e1f23;--raised:#26272c;--fg:#e8e8ea;--mut:#a5a5ab;
--dim:#74747b;--line:#303137;--acc:#7aa2f7;--ok:#6cc58a;--bad:#f0707f;--warn:#e3b45a;--hover:#24252a;
--c1:#f0707f;--c2:#6cc58a;--c3:#e3b45a;--c4:#7aa2f7;--c5:#bb9af7;--c6:#5fc6d4}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,BlinkMacSystemFont,"PingFang TC",
"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:1040px;margin:0 auto;padding:24px 16px 56px}
a{color:var(--acc);text-decoration:none} a:hover{text-decoration:underline}
.sub{color:var(--dim);font-size:12.5px;font-weight:400}
.top{display:flex;align-items:center;gap:12px}
.brand{display:flex;align-items:center;gap:12px;flex:1;min-width:0}
.mark{width:34px;height:34px;color:var(--acc);flex:none}
.name{font-size:12px;color:var(--dim);letter-spacing:.02em}
h1{font-size:21px;line-height:1.2;margin:0;font-weight:650}
.pill{display:inline-flex;align-items:center;gap:7px;height:28px;padding:0 12px;border-radius:14px;
background:var(--surface);border:1px solid var(--line);font-size:13px;color:var(--mut);white-space:nowrap}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;flex:none;background:var(--dim)}
.pill.ok .dot,.dot.good{background:var(--ok)} .pill.bad .dot{background:var(--bad)} .pill.bad{color:var(--bad)}
.dot.c1{background:var(--c1)}.dot.c2{background:var(--c2)}.dot.c3{background:var(--c3)}
.dot.c4{background:var(--c4)}.dot.c5{background:var(--c5)}.dot.c6{background:var(--c6)}.dot.none{background:transparent}
.links{margin:10px 0 18px;color:var(--dim);font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px 14px;min-width:0}
.tile b{display:block;font-size:26px;line-height:32px;font-weight:600;font-variant-numeric:tabular-nums}
.tile span{display:block;font-size:12px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tile.hot b{color:var(--bad)}
.grid{display:grid;grid-template-columns:3fr 2fr;gap:12px;margin-top:12px;align-items:start}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:6px 8px 8px;min-width:0}
.card.wide{grid-column:1/-1}
h2{display:flex;align-items:baseline;gap:8px;font-size:12.5px;font-weight:600;color:var(--mut);margin:30px 2px 8px;
text-transform:uppercase;letter-spacing:.04em}
.card h2{margin:8px 8px 4px}
.list,.alerts{list-style:none;margin:0;padding:0}
.item{display:flex;gap:10px;padding:8px;border-radius:8px} .item:hover,.mail:hover{background:var(--hover)}
.kind{width:16px;height:16px;margin-top:1px;flex:none;border-radius:4px;border:1.5px solid var(--dim)}
.kind.question{border-radius:50%;border-color:var(--bad);color:var(--bad);font-size:10px;font-weight:700;
display:flex;align-items:center;justify-content:center}
.body{flex:1;min-width:0}
.l1{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--mut)}
.l2{font-size:13.5px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.proj{display:inline-flex;align-items:center;gap:6px;min-width:0;font-weight:500}
.ago{margin-left:auto;font-size:12px;color:var(--dim);white-space:nowrap}
.mail{display:flex;align-items:center;gap:8px;padding:7px 8px;border-radius:8px}
.badge{min-width:22px;height:20px;padding:0 7px;border-radius:10px;background:var(--raised);border:1px solid var(--line);
font-size:12px;font-weight:600;display:inline-flex;align-items:center;justify-content:center}
.more{display:block;padding:6px 8px;font-size:12.5px}
.empty{display:flex;align-items:center;gap:8px;margin:6px 8px;padding:10px;border-radius:8px;background:var(--raised);
color:var(--mut)}
.alerts li{display:flex;gap:10px;padding:7px 8px;font-size:13px;line-height:1.5;color:var(--fg)}
.alerts .dot{margin-top:7px} .alerts.bad .dot{background:var(--bad)} .alerts.notify .dot{background:var(--warn)}
.alerts li span{min-width:0;overflow-wrap:anywhere}
.tbl{background:var(--surface);border:1px solid var(--line);border-radius:12px;overflow-x:auto;margin-top:8px}
table{width:100%;border-collapse:collapse}
td,th{padding:9px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
tr:last-child td{border-bottom:0}
th{font-size:12px;color:var(--dim);font-weight:500;background:var(--raised)}
td .proj .dot,td.name .dot{margin-right:2px}
td.name b{font-weight:600;color:var(--fg)} td.last,td.next{white-space:nowrap} td.name .dot{margin-right:8px;position:relative;top:-1px} td.when{color:var(--mut)}
tr.off{opacity:.45}
.st{font-weight:500} .st.ok{color:var(--ok)} .st.bad{color:var(--bad);font-weight:600} .st.run{color:var(--acc)}
.tag{font-size:11px;border:1px solid var(--line);border-radius:5px;padding:0 5px;margin-left:6px;color:var(--mut);
background:var(--raised)}
.gap{margin-top:8px}
.report{margin-top:12px} h3{display:flex;align-items:center;gap:8px;font-size:14px;margin:0 2px 6px}
details{background:var(--surface);border:1px solid var(--line);border-radius:10px;margin:6px 0;padding:8px 14px}
summary{cursor:pointer;color:var(--mut);font-size:13px}
pre{white-space:pre-wrap;word-break:break-word;font:13.5px/1.6 -apple-system,"PingFang TC",sans-serif;margin:8px 0 2px}
footer{margin-top:32px;text-align:center}
.rest{background:none;border:0;margin:0;padding:2px 8px 4px}
.rest summary{font-size:12.5px;color:var(--acc)}
@media (max-width:720px){
.tiles{grid-template-columns:repeat(2,1fr)} .grid{grid-template-columns:1fr}
table.jobs thead{display:none}
table.jobs tr.job{display:grid;grid-template-columns:1fr auto;gap:2px 12px;padding:10px 12px;
border-bottom:1px solid var(--line)}
table.jobs tr.job:last-child{border-bottom:0}
table.jobs td{border:0;padding:0}
td.name{grid-column:1;grid-row:1} td.last{grid-column:2;grid-row:1;text-align:right;white-space:nowrap}
td.when{grid-column:1;grid-row:2;font-size:12.5px} td.next{grid-column:2;grid-row:2;text-align:right;font-size:12.5px;
color:var(--mut)}
td.next::before{content:attr(data-l) " ";color:var(--dim)}
}
"""


def needs_you() -> tuple[list[dict], str]:
    """The owner's open items (owner.open_items, which folds them from the bus records: too slow for every glance,
    cheap once a tick), in the glance.v1 form, and why they couldn't be read ('' when they could)."""
    try:
        import owner
        return [{"kind": "todo" if x["kind"] == "task" else "question", "project": x["project"],
                 "id": x.get("key") or x.get("id"), "text": x["text"], "since": x.get("since") or ""}
                for x in owner.open_items()], ""
    except Exception as e:   # the page is written either way; glance says the items couldn't be read
        return [], repr(e)[:300]


def write_glance(now: datetime.datetime, alerts: list, needs: tuple[list[dict], str] | None = None) -> None:
    """The part of this tick `brunna glance` reads (bin/glance.py, docs/design/herdr.md): the alerts the page just
    showed, from this same list so the two never disagree, and the owner's open items (needs_you(), passed in when the
    page already read them). Replaced whole, never half-written."""
    data = {"v": 1, "as_of": now.astimezone().isoformat(timespec="seconds"),
            "alerts": [{"level": level, "text": text} for level, text in alerts]}
    items, error = needs if needs is not None else needs_you()
    data["needs_you"] = items
    if error:
        data["needs_you_error"] = error
    GLANCE.parent.mkdir(parents=True, exist_ok=True)
    tmp = GLANCE.with_name(f".{GLANCE.name}.{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    tmp.replace(GLANCE)


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
