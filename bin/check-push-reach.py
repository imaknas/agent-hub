#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Whether the assistant's pushes still reach the owner's phone (assistant module, hourly).

The assistant is one of the ways an alert reaches the owner, and it can't say that it has stopped: a phone that stopped
showing its pushes hears nothing, the assistant's own reminder included. So the platform asks it (GET /api/push/health,
the platform alerts contract in the assistant module's `consumes`) and says so on the other channels too. `alert.once`
sends on every channel set up, so the assistant gets a copy, for when the phone comes back.

What counts:
  - the assistant answers `reaching: false`: a problem at once. It can't tell a phone switched off all day from pushes
    that don't arrive, so the alert leaves room for that;
  - `subscriptions: 0`: a problem at once. `reaching` stays true then, since nothing is being sent, and nothing can arrive;
  - an answer that breaks the contract (401, 404, no `reaching`): a problem at once, asking again will not help;
  - no answer, or a 5xx: only 3 hours in a row. A restart is not worth an alert, and other jobs watch the service.
The problem's text holds nothing that changes from hour to hour, because a change of text is a new alert.

Off when the assistant channel is off (`alerts.assistant`, `assistant.api`): nothing is checked, and an open alert stays
as it is, since nothing recovered either.

Usage: check-push-reach.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import alert  # noqa: E402
import hubconf  # noqa: E402
import i18n  # noqa: E402

STREAK = hubconf.STATE / "push-reach-streak.json"
SOFT_LIMIT = 3
_ = i18n.domain(__file__)


class NoAnswer(Exception):
    """Nobody answered, or the server failed: it may be back within the hour."""


class BadAnswer(Exception):
    """An answer the contract doesn't allow: asking again won't help."""


def ask() -> dict:
    """The assistant's answer; raises NoAnswer or BadAnswer."""
    try:
        token = hubconf.p("assistant.ingest_token").read_text().strip()
    except (OSError, AttributeError) as e:
        raise BadAnswer(_("the ingest token can't be read: {err}", err=type(e).__name__)) from e
    u = urllib.parse.urlsplit(hubconf.get("assistant.api"))
    req = urllib.request.Request(f"{u.scheme}://{u.netloc}/api/push/health", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            raise NoAnswer(f"HTTP {e.code}") from e
        raise BadAnswer(f"HTTP {e.code}") from e
    except (OSError, TimeoutError) as e:
        raise NoAnswer(type(e).__name__) from e
    try:
        health = json.loads(body)
    except ValueError as e:
        raise BadAnswer(_("the answer is not JSON")) from e
    if not isinstance(health, dict) or not isinstance(health.get("reaching"), bool):
        raise BadAnswer(_("the answer has no reaching"))
    if type(health.get("subscriptions")) is not int:
        raise BadAnswer(_("the answer has no subscriptions"))
    return health


def streak(failed: bool) -> int:
    """How many checks in a row have had no answer, this one included."""
    try:
        n = json.loads(STREAK.read_text()).get("no_answer", 0)
    except (OSError, ValueError):
        n = 0
    n = n + 1 if failed else 0
    STREAK.write_text(json.dumps({"no_answer": n}))
    return n


def main() -> int:
    if not alert._assistant_on():
        print(_("not checked: the assistant channel is off"))
        return 0
    problems: list[str] = []
    try:
        health = ask()
    except NoAnswer as e:
        n = streak(True)
        print(_("no answer ({n} {n:time|times} in a row): {err}", n=n, err=e))
        if n >= SOFT_LIMIT:
            problems.append(_("The assistant hasn't answered for {limit} hours, so whether its pushes reach the phone is "
                              "unknown", limit=SOFT_LIMIT))
    except BadAnswer as e:
        streak(False)
        problems.append(_("Whether the assistant's pushes reach the phone can't be checked: {err}", err=e))
    else:
        streak(False)
        print(json.dumps(health, ensure_ascii=False))
        if not health["reaching"]:
            problems.append(_("The assistant's pushes have not been shown on any phone since {since}: unless the phone "
                              "has been off, open the assistant's app and check its notifications",
                              since=health.get("unshown_since") or "?"))
        if health["subscriptions"] == 0:
            problems.append(_("No phone is subscribed to the assistant's pushes: open its app and turn on notifications"))
    alert.once("push-reach", problems, _("The assistant's pushes reach the phone again"))
    for x in problems:
        print(f"⚠️ {x}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
