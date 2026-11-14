"""Send alerts / notifications ({settings} alerts). There can be several channels; whichever are set up get the message
(docs/design/tool-agnostic.md: not tied to Discord):
  discord   discord_token_file + discord_channel_file (files with the bot token and channel id, in the secrets
            directory, not in version control)
  ntfy      {"topic": …, "base_url": default https://ntfy.sh, "token_file": optional} (the phone app receives it,
            no account needed)
  email     {"host", "port": default 587, "user", "from", "to", "password_file", "ssl": false = STARTTLS}
  macos     true (this machine's system notifications; seen only by someone sitting at this machine)
  assistant true (the personal assistant's own reminders and push, its platform-alerts contract (modules/assistant.yaml), at the
            address and with the token of hub.json assistant. Only for alert.once: it needs the problem's key. It is
            never the only way out: when the assistant is down, nothing sent to it arrives)

Standard library + the system's curl only (no Homebrew). The scheduler watchdog deliberately has its own copy and
does not import this (it must still be able to send when the scheduler and the platform code are both broken).

  alert.send(text) -> bool
  alert.once(key, problems, ok_text, ids=None) -> None
                                               new problems are sent at once; unresolved ones are repeated every 24
                                               hours; one recovery message when resolved. A problem is told apart by
                                               its wording, or by its ids[i] when given (one stable name per problem, so
                                               a text that carries a changing detail, an error's tail or a count, is
                                               still the same problem and is not sent again as new)
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
import sys
import urllib.parse

import hubconf
import names  # noqa: E402
import i18n

_ = i18n.domain(__file__)

SENT = hubconf.STATE / "alerts-sent.json"      # key -> {"problems": [...], "ids": [...] when given, "at": when last sent}
FAILED = hubconf.STATE / "alerts-failed.json"  # key -> {"at": …, "text": first line of the one not sent}
REMIND = datetime.timedelta(hours=24)
API = "https://discord.com/api/v10"
_LIVE = (SENT, FAILED)


def _held() -> bool:
    """A test that reaches an alert without its own state files would tell the owner for real, from the live instance a
    worktree shares (11-14: two memory-sync tests sent a project called proj to Discord): nothing is sent or recorded.
    With no channel set up (the gate's fresh instance) there is no one to reach, and nothing changes."""
    if "unittest" in sys.modules and (SENT, FAILED) == _LIVE and configured():
        print("alert: held, a test on the live instance's alert state (patch alert.SENT/FAILED or alert.once)",
              file=sys.stderr)
        return True
    return False


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
    headers = ["-H", f"Title: {names.current('product')}"]
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
    msg["Subject"] = f"{names.current('product')}:" + text.splitlines()[0][:80] if text else names.current("product")
    msg["From"], msg["To"] = conf.get("from") or conf.get("user") or names.current("product"), conf["to"]
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
    script = f"display notification {json.dumps(text[:200])} with title \"{names.current('product')}\""
    return subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True).returncode == 0


CHANNELS = {"discord": (_discord, lambda: _creds() is not None), "ntfy": (_ntfy, lambda: bool(hubconf.get("alerts.ntfy"))),
            "email": (_email, lambda: bool(hubconf.get("alerts.email"))), "macos": (_macos, lambda: bool(hubconf.get("alerts.macos")))}


def _assistant_on() -> bool:
    return bool(hubconf.get("alerts.assistant") and hubconf.get("assistant.api"))


def _assistant(notice: dict) -> bool:
    """One notice about a keyed problem to the assistant. Only a 200 means it has it; anything else is tried again, with
    the same `at`, which the assistant takes as the same notice."""
    try:
        token = hubconf.p("assistant.ingest_token").read_text().strip()
    except (OSError, AttributeError):
        return False
    u = urllib.parse.urlsplit(hubconf.get("assistant.api"))
    body = json.dumps({"v": 1, "source": hubconf.hub_project(), **notice}, ensure_ascii=False)
    return _curl("-X", "POST", "-H", f"Authorization: Bearer {token}", "-H", "Content-Type: application/json",
                 "--data-binary", "@-", f"{u.scheme}://{u.netloc}/api/alerts", data=body) == "200"


# channels that take a notice with a key (once), not free text (send)
KEYED = {"assistant": (_assistant, _assistant_on)}


def configured() -> list[str]:
    return [n for n, (_send, on) in (*CHANNELS.items(), *KEYED.items()) if on()]


def send(text: str, notice: dict | None = None) -> bool:
    """Send on every channel set up; success only when all of them went out (one failing is recorded as a failure, and
    the status page turns red). None set up = False. notice: for the channels that need a key; without one they are
    skipped."""
    if _held():
        return False
    results = []
    for n in configured():   # every channel is tried: a broken one must not silence the rest
        if n in KEYED:
            if notice:
                results.append(KEYED[n][0](notice))
        else:
            results.append(CHANNELS[n][0](text))
    return bool(results) and all(results)


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


def record_failure(key: str, text: str, notice: dict | None = None) -> None:
    if _held() or not configured():   # no channel: not a failed send (the setup reminder covers it)
        return
    failed = _load(FAILED)
    failed[key] = {"at": datetime.datetime.now().isoformat(timespec="seconds"), "text": text.splitlines()[0][:200],
                   **({"notice": notice} if notice else {})}
    _save(FAILED, failed)


def clear_failure(key: str) -> None:
    failed = _load(FAILED)
    if failed.pop(key, None) is not None:
        _save(FAILED, failed)


def failures() -> dict:
    """Alerts that could not be sent and have not been resent successfully yet (for the status page). With no channel
    set up, none: what was recorded before applied to channels that are gone."""
    return _load(FAILED) if configured() else {}


def once(key: str, problems: list[str], ok_text: str, ids: list[str] | None = None) -> None:
    if ids is not None and len(ids) != len(problems):
        raise ValueError(f"alert.once({key!r}): {len(ids)} ids for {len(problems)} problems")
    if _held():
        return
    now = datetime.datetime.now()
    sent = _load(SENT)
    last = sent.get(key)
    if isinstance(last, list):  # old format: only the list of problems
        last = {"problems": last, "at": None}
    # what the problems are: their ids when the caller names them, else their wording (a record from before ids
    # compares by wording once, then carries them)
    ident = sorted(ids) if ids is not None else sorted(problems)
    last_ident = sorted((last or {}).get("ids") if ids is not None and "ids" in (last or {})
                        else (last or {}).get("problems", []))
    if ident == last_ident:
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
    # a retry is the same notice, to a channel that can tell (`at`): its time stays with the failure until it has gone out
    wanted = sorted(problems)
    unsent = _load(FAILED).get(key, {}).get("notice") or {}
    notice_at = unsent["at"] if unsent.get("ids", unsent.get("problems")) == ident \
        else now.astimezone().isoformat(timespec="seconds")
    notice = {"key": key, "at": notice_at, "state": "open" if problems else "resolved"}
    if problems:
        more = len(problems) - 1
        notice["text"] = _("{first} (and {n} more)", first=problems[0], n=more) if more else problems[0]
        if more:
            notice["detail"] = "\n".join(problems)
    else:
        notice["text"] = ok_text
    if send(text, notice):
        sent[key] = {"problems": wanted, **({"ids": ident} if ids is not None else {}), "at": now.isoformat(timespec="seconds")}
        _save(SENT, sent)
        clear_failure(key)
    else:
        record_failure(key, text, {"problems": wanted, **({"ids": ident} if ids is not None else {}), "at": notice_at})
