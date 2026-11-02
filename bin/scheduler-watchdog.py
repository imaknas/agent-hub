#!/usr/bin/env python3
"""Scheduler watchdog: when scheduler.py stops (launchd job gone, tick stuck, python broken), post to Discord.

scheduler.py is the only trigger for the librarian, weekly-reflect, mirror-backup and every muninn-* job; when it
stops nothing gets called: the assistant's web server is a separate launchd job, so the PWA still opens, it just
stops updating. So this script deliberately depends neither on the scheduler nor on the assistant:

- its own launchd job (local.agent-hub-scheduler-watchdog, every 5 minutes)
- the system's /usr/bin/python3, standard library only: if the platform's Python (bin/py) breaks and stops the
  scheduler, this one keeps going
- notifications go through Discord REST (bot token, curl), not through the assistant

The check: scheduler.py writes .state/scheduler-heartbeat at the start of every tick and after every job; it alerts
only when that hasn't been updated for more than STALE_MIN minutes, two checks in a row (no false alarm on the
round right after boot/recovery). After that it reminds every REMIND_H hours, and posts a "recovered" message when it
recovers.

Settings, in the platform's secrets directory (outside the repo; `bin/hubconf.py dir secrets`):
  discord-token          bot token (can share the same bot with other projects)
  discord-alert-channel  the channel id to post to
Without them it fails loudly in the log (exit 1), rather than pretend to be watching.

Usage: scheduler-watchdog.py          check once (for launchd)
       scheduler-watchdog.py --test   post a test message, to confirm the token/channel settings are right
"""
from __future__ import annotations   # the system python3 (3.9 on macOS) runs this: no 3.10 syntax at run time

import datetime
import json
import os
import pathlib
import subprocess
import sys
import time

# same as hubconf.ROOT
HUB = pathlib.Path(os.environ.get("AGENT_HUB_ROOT") or pathlib.Path(__file__).resolve().parents[1])


def _instance(sub: str, legacy: pathlib.Path) -> pathlib.Path:
    """The instance directory's layout (same rules as hubconf, docs/design/new-user.md 3.1). Deliberately imports no
    platform module: this must still run when the platform is broken (checked by tests/test_python_floor.py)."""
    env = {"config": "AGENT_HUB_CONFIG", "secrets": "AGENT_HUB_SECRETS"}[sub]
    if os.environ.get(env):
        return pathlib.Path(os.path.expanduser(os.environ[env]))
    home = os.environ.get("AGENT_HUB_HOME") or ((HUB / ".hub-home").read_text().strip()
                                                if (HUB / ".hub-home").is_file() else "")
    return pathlib.Path(os.path.expanduser(home)) / sub if home else legacy


CONFIG_DIR, SECRETS_DIR = _instance("config", HUB), _instance("secrets", HUB / ".secrets")


class i18n:
    """bin/i18n.py's lookup, small enough to keep here (this script imports nothing of the platform): English msgids,
    locales/<hub.json language.ui>/scheduler-watchdog.json for the translation"""
    @staticmethod
    def domain(file):
        lang = os.environ.get("AGENT_HUB_LANG_UI") or ""
        if not lang:
            try:
                lang = (json.loads((CONFIG_DIR / "hub.json").read_text()).get("language") or {}).get("ui") or "en"
            except (OSError, ValueError):
                lang = "en"
        try:
            code = pathlib.Path(file).resolve().parents[1]   # the catalogs ship with the code, wherever the hub's state is
            cat = json.loads((code / "locales" / lang / "scheduler-watchdog.json").read_text()) if lang != "en" else {}
        except (OSError, ValueError):
            cat = {}

        def _(msgid, **kw):
            text = cat.get(msgid, msgid)
            return text.format(**kw) if kw else text
        return _


_ = i18n.domain(__file__)
HEARTBEAT = HUB / ".state" / "scheduler-heartbeat"
STATE_FILE = HUB / ".state" / "scheduler-last-run.json"
WATCH_STATE = HUB / ".state" / "scheduler-watchdog.json"
SCHED_LOG = HUB / ".logs" / "scheduler.log"
LOG = HUB / ".logs" / "scheduler-watchdog.log"
TOKEN = SECRETS_DIR / "discord-token"
CHANNEL = SECRETS_DIR / "discord-alert-channel"
LABEL = "local.agent-hub-scheduler"
RESUME_GRACE_MIN = 20  # after a resume (Linux): the scheduler's 15-minute timer gets one full interval and a margin
STALE_MIN = 35  # a tick every 15 minutes; two missed ticks plus a few minutes of drift
REMIND_H = 6


def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")


def last_tick():
    """The time of the last tick: the heartbeat, falling back to the state file's modification time (older
    scheduler). Not the newer of the two: schedulectl run writes the state file too, which would hide the fact that
    the scheduler has actually stopped."""
    for p in (HEARTBEAT, STATE_FILE):
        if p.exists():
            return datetime.datetime.fromtimestamp(p.stat().st_mtime)
    return None


def diagnose():
    uid = os.getuid()
    # Linux: the scheduler is a systemd user timer (bin/systemd_units.py; this script deliberately imports no platform
    # module)
    if sys.platform != "darwin":
        r = subprocess.run(["systemctl", "--user", "status", "--no-pager", f"{LABEL}.timer"], capture_output=True, text=True)
        lines = [_("`systemctl --user status {label}.timer`:\n```\n{out}\n```", label=LABEL,
                   out=(r.stdout or r.stderr).strip()[:600]),
                 _("restart: `systemctl --user restart {label}.timer`", label=LABEL)]
        if SCHED_LOG.exists():
            tail = SCHED_LOG.read_text(errors="replace").splitlines()[-3:]
            lines.append(_("last lines of scheduler.log:\n```\n{tail}\n```", tail="\n".join(x[:180] for x in tail)))
        return "\n".join(lines)
    r = subprocess.run(["/bin/launchctl", "print", f"gui/{uid}/{LABEL}"], capture_output=True, text=True)
    if r.returncode != 0:
        lines = [_("{label} is not in launchd (booted out?) → `schedulectl start`", label=LABEL)]
    else:
        info = {}
        for l in r.stdout.splitlines():  # the first one is the job's own (endpoints etc. have states further down)
            k, eq, v = l.partition("=")
            if eq:
                info.setdefault(k.strip(), v.strip())
        lines = [f"launchd:state={info.get('state', '?')} pid={info.get('pid', '-')} "
                 f"last exit={info.get('last exit code', '?')}"]
        ps = subprocess.run(["/bin/ps", "-axo", "pid,etime,command"], capture_output=True, text=True).stdout
        stuck = [l.strip() for l in ps.splitlines() if "bin/scheduler.py" in l and "watchdog" not in l]
        if stuck:
            lines.append(_("scheduler.py is still running (stuck in a job?): {proc}", proc=stuck[0][:120]))
        lines.append(_("restart: `launchctl kickstart -k gui/{uid}/{label}`", uid=uid, label=LABEL))
    if SCHED_LOG.exists():
        tail = SCHED_LOG.read_text(errors="replace").splitlines()[-3:]
        lines.append(_("last lines of scheduler.log:\n```\n{tail}\n```", tail="\n".join(t[:180] for t in tail)))
    return "\n".join(lines)


def hub_alerts() -> dict | None:
    """hub.json's alerts section; None when hub.json can't be read."""
    try:
        return json.loads((CONFIG_DIR / "hub.json").read_text()).get("alerts") or {}
    except (OSError, ValueError):
        return None


def conf_path(value: str) -> pathlib.Path:
    """hubconf.path's rules, kept here: .secrets/<name> is the secrets directory, ~ expands, relative is the config
    directory."""
    if value == ".secrets" or value.startswith(".secrets/"):
        return SECRETS_DIR / value[len(".secrets") + 1:]
    p = pathlib.Path(os.path.expanduser(value))
    return p if p.is_absolute() else CONFIG_DIR / p


def curl(*args, data=None) -> str:
    """curl is built into the system, no Homebrew needed. Returns the HTTP status ("" when curl itself fails)."""
    r = subprocess.run(["/usr/bin/curl", "-sS", "-m", "20", "-o", "/dev/null", "-w", "%{http_code}", *args],
                       input=data, capture_output=True, text=True)
    return r.stdout.strip()


def send_discord(conf, text) -> bool:
    try:
        token = conf_path(conf.get("discord_token_file") or ".secrets/discord-token").read_text().strip()
        channel = conf_path(conf.get("discord_channel_file") or ".secrets/discord-alert-channel").read_text().strip()
    except OSError:
        return False
    return curl("-H", f"Authorization: Bot {token}", "-H", "Content-Type: application/json", "--data-binary", "@-",
                f"https://discord.com/api/v10/channels/{channel}/messages",
                data=json.dumps({"content": text[:1990]})) == "200"


def send_ntfy(conf, text) -> bool:
    n = conf.get("ntfy") or {}
    headers = ["-H", "Title: agent-hub"]
    if n.get("token_file"):
        try:
            headers += ["-H", f"Authorization: Bearer {conf_path(n['token_file']).read_text().strip()}"]
        except OSError:
            return False
    return curl(*headers, "--data-binary", "@-", f"{(n.get('base_url') or 'https://ntfy.sh').rstrip('/')}/{n['topic']}",
                data=text[:4000]) == "200"


def send_email(conf, text) -> bool:
    import smtplib
    from email.message import EmailMessage
    e = conf.get("email") or {}
    msg = EmailMessage()
    msg["Subject"] = "agent-hub: " + (text.splitlines()[0][:80] if text else "")
    msg["From"], msg["To"] = e.get("from") or e.get("user") or "agent-hub", e["to"]
    msg.set_content(text)
    try:
        password = conf_path(e["password_file"]).read_text().strip() if e.get("password_file") else None
        cls = smtplib.SMTP_SSL if e.get("ssl") else smtplib.SMTP
        with cls(e["host"], int(e.get("port") or (465 if e.get("ssl") else 587)), timeout=20) as s:
            if not e.get("ssl"):
                s.starttls()
            if e.get("user") and password:
                s.login(e["user"], password)
            s.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException):
        return False


def send_macos(conf, text) -> bool:
    script = f"display notification {json.dumps(text[:200])} with title \"agent-hub\""
    return subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True).returncode == 0


def channels(conf) -> list[tuple[str, object]]:
    """The channels hub.json alerts sets up (the same ones as bin/alert.py, which this deliberately doesn't import:
    it must still send when the platform's Python is broken). Discord with no files configured falls back to the
    secrets directory's default names, as before."""
    out = []
    if (conf_path(conf.get("discord_token_file") or ".secrets/discord-token").exists()
            and conf_path(conf.get("discord_channel_file") or ".secrets/discord-alert-channel").exists()):
        out.append(("discord", send_discord))
    if (conf.get("ntfy") or {}).get("topic"):
        out.append(("ntfy", send_ntfy))
    if (conf.get("email") or {}).get("host") and (conf.get("email") or {}).get("to"):
        out.append(("email", send_email))
    if conf.get("macos") and sys.platform == "darwin":
        out.append(("macos", send_macos))
    return out


def send(text):
    """Send on every channel set up. True when at least one went out: the point is that someone hears; each failure is
    logged."""
    conf = hub_alerts() or {}
    sent = False
    for name, fn in channels(conf):
        if fn(conf, text):
            sent = True
        else:
            log(_("{channel} send failed", channel=name))
    if not sent:
        log(_("failed: no alert channel could send"))
    return sent


def alerts_configured() -> bool:
    a = hub_alerts()
    return True if a is None else bool(a)   # hub.json unreadable: keep watching with whatever channel there is


def resumed(st: dict, now: datetime.datetime) -> None:
    """Linux: systemd timers resume their remaining time after a suspend, so this check can run before the scheduler's
    first tick after waking. The gap between CLOCK_BOOTTIME (counts suspended time) and CLOCK_MONOTONIC (doesn't) grows
    by the time spent asleep: when it grew since the last check, record the resume. macOS has no CLOCK_BOOTTIME; there
    launchd fires missed intervals on wake, so nothing is needed."""
    boottime = getattr(time, "CLOCK_BOOTTIME", None)
    if boottime is None:
        return
    gap = time.clock_gettime(boottime) - time.clock_gettime(time.CLOCK_MONOTONIC)
    if st.get("sleep_gap") is not None and gap - st["sleep_gap"] > 60:
        st["resumed_at"] = now.isoformat(timespec="seconds")
    st["sleep_gap"] = gap


def in_resume_grace(st: dict, tick, now: datetime.datetime) -> bool:
    """Right after a resume, until the scheduler has ticked or RESUME_GRACE_MIN have passed: no alarm."""
    r = st.get("resumed_at")
    if not r:
        return False
    at = datetime.datetime.fromisoformat(r)
    if (tick is not None and tick >= at) or now - at >= datetime.timedelta(minutes=RESUME_GRACE_MIN):
        st.pop("resumed_at", None)
        return False
    return True


def main():
    if sys.argv[1:] == ["--test"]:
        ok = send(_("🧪 agent-hub scheduler watchdog test message (settings are correct)") + "\n" + diagnose())
        print(_("sent") if ok else _("could not send, see {log}", log=LOG))
        return 0 if ok else 1

    if not alerts_configured():
        # no alerts section in hub.json = the owner chose no alerts (the default for new users): still check and log,
        # not treated as a failure
        tick = last_tick()
        stale = tick is None or (datetime.datetime.now() - tick).total_seconds() / 60 > STALE_MIN
        if stale:
            log(_("no alerts set up (hub.json has no alerts), only logging: the scheduler's last tick was {tick}; it has "
                  "stopped", tick=tick) if tick else
                _("no alerts set up (hub.json has no alerts), only logging: the scheduler has never ticked"))
        return 0
    if not channels(hub_alerts() or {}):
        # printed to the service's stderr log every 5 minutes, exit 1; the status page also flags "watchdog not set up"
        print(_("scheduler-watchdog: hub.json alerts has no channel this watchdog can send on (Discord, ntfy, email, "
                "macOS); no alert can be sent if the scheduler stops"), file=sys.stderr)
        return 1

    now = datetime.datetime.now()
    st = json.loads(WATCH_STATE.read_text()) if WATCH_STATE.exists() else {}
    tick = last_tick()
    resumed(st, now)
    age_min = (now - tick).total_seconds() / 60 if tick else None
    stale = age_min is None or age_min > STALE_MIN
    when = f"{tick:%m-%d %H:%M}" if tick else _("never")

    if not stale:
        if st.get("alerting"):
            if send(_("✅ The agent-hub scheduler is back (last tick {when}; it had been stopped since about {since})",
                      when=when, since=st.get("since", "?"))):
                log(_("recovered, notified"))
                st = {}
        else:
            st = {}
    elif in_resume_grace(st, tick, now):
        log(_("the machine just woke up; waiting for the scheduler's first tick before judging it"))
    else:
        st["stale_checks"] = st.get("stale_checks", 0) + 1
        st.setdefault("since", when)
        last = st.get("last_alert")
        due = (st["stale_checks"] >= 2 and not st.get("alerting")) or (
            st.get("alerting") and now - datetime.datetime.fromisoformat(last) >= datetime.timedelta(hours=REMIND_H))
        if due:
            age = _("{minutes:.0f} minutes", minutes=age_min) if age_min is not None else _("a long time")
            prefix = "🚨" if not st.get("alerting") else _("⏰ Still down:")
            text = (_("{prefix} The agent-hub scheduler has not ticked for {age} (last tick {when}). The librarian, "
                      "the assistant's jobs, reflect and the upload jobs have all stopped.", prefix=prefix, age=age, when=when)
                    if tick else
                    _("{prefix} The agent-hub scheduler has not ticked for {age} (no tick recorded). The librarian, "
                      "the assistant's jobs, reflect and the upload jobs have all stopped.", prefix=prefix, age=age)) \
                + "\n" + diagnose()
            if send(text):
                st.update(alerting=True, last_alert=now.isoformat(timespec="seconds"))
                log(_("the scheduler stopped {age} ago, notified", age=age))
            else:
                return 1
    WATCH_STATE.parent.mkdir(parents=True, exist_ok=True)
    WATCH_STATE.write_text(json.dumps(st, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
