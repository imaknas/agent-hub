#!/usr/bin/env python3
"""Scheduler watchdog: when scheduler.py stops (launchd job gone, tick stuck, python broken), post to Discord.

scheduler.py is the only trigger for the librarian, weekly-reflect, mirror-backup and every muninn-* job; when it
stops nothing gets called: the assistant's web server is a separate launchd job, so the PWA still opens, it just
stops updating. So this script deliberately depends neither on the scheduler nor on the assistant:

- its own launchd job (local.agent-hub-scheduler-watchdog, every 5 minutes)
- the system's /usr/bin/python3, standard library only: if Homebrew's python breaks and stops the scheduler, this
  one keeps going
- notifications go through Discord REST (bot token, curl), not through the assistant

The check: scheduler.py writes .state/scheduler-heartbeat at the start of every tick and after every job; it alerts
only when that hasn't been updated for more than STALE_MIN minutes, two checks in a row (no false alarm on the
round right after boot/recovery). After that it reminds every REMIND_H hours, and posts a "recovered" message when it
recovers.

Settings (not version-controlled, .secrets/ is in .gitignore):
  .secrets/discord-token          bot token (can share the same bot with other projects)
  .secrets/discord-alert-channel  the channel id to post to
Without them it fails loudly in the log (exit 1), rather than pretend to be watching.

Usage: scheduler-watchdog.py          check once (for launchd)
       scheduler-watchdog.py --test   post a test message, to confirm the token/channel settings are right
"""
import datetime
import json
import os
import pathlib
import subprocess
import sys

HUB = pathlib.Path(os.environ.get("AGENT_HUB_ROOT") or pathlib.Path(__file__).resolve().parents[1])  # same as hubconf.ROOT


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


def send(text):
    """Return whether it was sent. curl is built into the system, no Homebrew needed."""
    try:
        token, channel = TOKEN.read_text().strip(), CHANNEL.read_text().strip()
    except OSError:
        log(_("failed: {token} or {channel} is not set up; alerts cannot be sent", token=TOKEN, channel=CHANNEL))
        return False
    r = subprocess.run(
        ["/usr/bin/curl", "-sS", "-m", "20", "-o", "/dev/null", "-w", "%{http_code}",
         "-H", f"Authorization: Bot {token}", "-H", "Content-Type: application/json",
         "--data-binary", "@-", f"https://discord.com/api/v10/channels/{channel}/messages"],
        input=json.dumps({"content": text[:1990]}), capture_output=True, text=True)
    if r.stdout.strip() != "200":
        log(_("Discord send failed: HTTP {code} {err}", code=r.stdout.strip() or "-", err=r.stderr.strip()[:200]))
        return False
    return True


def alerts_configured() -> bool:
    import json
    try:
        return bool(json.loads((CONFIG_DIR / "hub.json").read_text()).get("alerts"))
    except (OSError, ValueError):
        return True  # hub.json unreadable: as before, only Discord counts as watching


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
    if not (TOKEN.exists() and CHANNEL.exists()):
        # printed to launchd's stderr log every 5 minutes, exit 1; the status page also flags "watchdog not set up"
        print(_("scheduler-watchdog: {token} / {channel} not set up; no alert can be sent if the scheduler stops",
                token=TOKEN, channel=CHANNEL), file=sys.stderr)
        return 1

    now = datetime.datetime.now()
    st = json.loads(WATCH_STATE.read_text()) if WATCH_STATE.exists() else {}
    tick = last_tick()
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
