"""Send alerts / notifications (hub.json alerts). There can be several channels; whichever are set up get the message
(docs/design/tool-agnostic.md: not tied to Discord):
  discord   discord_token_file + discord_channel_file (files with the bot token and channel id, in the secrets
            directory, not in version control)
  ntfy      {"topic": …, "base_url": default https://ntfy.sh, "token_file": optional} (the phone app receives it,
            no account needed)
  email     {"host", "port": default 587, "user", "from", "to", "password_file", "ssl": false = STARTTLS}
  macos     true (this machine's system notifications; seen only by someone sitting at this machine)

Standard library + the system's curl only (no Homebrew). The scheduler watchdog deliberately has its own copy and
does not import this (it must still be able to send when the scheduler and the platform code are both broken).

  alert.send(text) -> bool
  alert.once(key, problems, ok_text) -> None   new problems are sent at once; unresolved ones are repeated every 24
                                               hours; one recovery message when resolved
  alert.probe() -> bool                         whether the token still works (GET /users/@me, no message sent;
                                               check-secrets calls it daily)

A failure to send does not pass silently (2026-10-24): recorded in .state/alerts-failed.json, the status page turns
red, and it is tried again next time; cleared once a send succeeds. With no channel set up at all there is nothing that
failed: nothing is recorded, and the status page shows one setup reminder instead (docs/design/new-user.md 2: a feature
that is off does not turn the page red).
"""
from __future__ import annotations

import datetime
import json
import subprocess

import hubconf
import i18n

_ = i18n.domain(__file__)

SENT = hubconf.STATE / "alerts-sent.json"      # key -> {"problems": [...], "at": when last sent}
FAILED = hubconf.STATE / "alerts-failed.json"  # key -> {"at": …, "text": first line of the one not sent}
REMIND = datetime.timedelta(hours=24)
API = "https://discord.com/api/v10"


def _creds() -> tuple[str, str] | None:
    try:
        return (hubconf.p("alerts.discord_token_file").read_text().strip(),
                hubconf.p("alerts.discord_channel_file").read_text().strip())
    except (OSError, AttributeError):
        return None


def _curl(*args: str, data: str | None = None) -> str:
    """Returns the HTTP status code (a string); an empty string when curl itself fails."""
    r = subprocess.run(["/usr/bin/curl", "-sS", "-m", "20", "-o", "/dev/null", "-w", "%{http_code}", *args],
                       input=data, capture_output=True, text=True)
    return r.stdout.strip()


def _discord(text: str) -> bool:
    c = _creds()
    if not c:
        return False
    token, channel = c
    return _curl("-H", f"Authorization: Bot {token}", "-H", "Content-Type: application/json", "--data-binary", "@-",
                 f"{API}/channels/{channel}/messages", data=json.dumps({"content": text[:1990]})) == "200"


def _ntfy(text: str) -> bool:
    conf = hubconf.get("alerts.ntfy") or {}
    if not conf.get("topic"):
        return False
    headers = ["-H", "Title: agent-hub"]
    if conf.get("token_file"):
        try:
            headers += ["-H", f"Authorization: Bearer {hubconf.path(conf['token_file']).read_text().strip()}"]
        except OSError:
            return False
    url = f"{(conf.get('base_url') or 'https://ntfy.sh').rstrip('/')}/{conf['topic']}"
    return _curl(*headers, "--data-binary", "@-", url, data=text[:4000]) == "200"


def _email(text: str) -> bool:
    import smtplib
    from email.message import EmailMessage
    conf = hubconf.get("alerts.email") or {}
    if not conf.get("host") or not conf.get("to"):
        return False
    msg = EmailMessage()
    msg["Subject"] = "agent-hub:" + text.splitlines()[0][:80] if text else "agent-hub"
    msg["From"], msg["To"] = conf.get("from") or conf.get("user") or "agent-hub", conf["to"]
    msg.set_content(text)
    try:
        password = hubconf.path(conf["password_file"]).read_text().strip() if conf.get("password_file") else None
        cls = smtplib.SMTP_SSL if conf.get("ssl") else smtplib.SMTP
        with cls(conf["host"], int(conf.get("port") or (465 if conf.get("ssl") else 587)), timeout=20) as s:
            if not conf.get("ssl"):
                s.starttls()
            if conf.get("user") and password:
                s.login(conf["user"], password)
            s.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException):
        return False


def _macos(text: str) -> bool:
    if not hubconf.get("alerts.macos"):
        return False
    script = f"display notification {json.dumps(text[:200])} with title \"agent-hub\""
    return subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True).returncode == 0


CHANNELS = {"discord": (_discord, lambda: _creds() is not None), "ntfy": (_ntfy, lambda: bool(hubconf.get("alerts.ntfy"))),
            "email": (_email, lambda: bool(hubconf.get("alerts.email"))), "macos": (_macos, lambda: bool(hubconf.get("alerts.macos")))}


def configured() -> list[str]:
    return [n for n, (_send, on) in CHANNELS.items() if on()]


def send(text: str) -> bool:
    """Send on every channel set up; success only when all of them went out (one failing is recorded as a failure, and
    the status page turns red). None set up = False."""
    chans = configured()
    return bool(chans) and all(CHANNELS[n][0](text) for n in chans)


def probe() -> bool:
    """Whether the alert channels themselves work: Discord calls /users/@me (no message sent); other channels have no
    check that sends nothing, so being set up counts as working. No channel at all = False."""
    chans = configured()
    if not chans:
        return False
    if "discord" in chans:
        c = _creds()
        return _curl("-H", f"Authorization: Bot {c[0]}", f"{API}/users/@me") == "200"
    return True


def _load(path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _save(path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))


def record_failure(key: str, text: str) -> None:
    if not configured():   # no channel: not a failed send (the setup reminder covers it)
        return
    failed = _load(FAILED)
    failed[key] = {"at": datetime.datetime.now().isoformat(timespec="seconds"), "text": text.splitlines()[0][:200]}
    _save(FAILED, failed)


def clear_failure(key: str) -> None:
    failed = _load(FAILED)
    if failed.pop(key, None) is not None:
        _save(FAILED, failed)


def failures() -> dict:
    """Alerts that could not be sent and have not been resent successfully yet (for the status page). With no channel
    set up, none: what was recorded before applied to channels that are gone."""
    return _load(FAILED) if configured() else {}


def once(key: str, problems: list[str], ok_text: str) -> None:
    now = datetime.datetime.now()
    sent = _load(SENT)
    last = sent.get(key)
    if isinstance(last, list):  # old format: only the list of problems
        last = {"problems": last, "at": None}
    last_problems = (last or {}).get("problems", [])
    if sorted(problems) == sorted(last_problems):
        if not problems:
            clear_failure(key)  # the earlier one was not sent and the problem has fixed itself: nothing to resend
            return
        at = (last or {}).get("at")
        if at and now - datetime.datetime.fromisoformat(at) < REMIND:
            return
        text = _("⏰ Still not resolved (last reminder: {at}):", at=at[:16] if at else _("earlier")) + "\n" \
            + "\n".join(problems)
    elif problems:
        text = "⚠️ " + "\n".join(problems)
    else:
        text = "✅ " + ok_text
    if send(text):
        sent[key] = {"problems": sorted(problems), "at": now.isoformat(timespec="seconds")}
        _save(SENT, sent)
        clear_failure(key)
    else:
        record_failure(key, text)
