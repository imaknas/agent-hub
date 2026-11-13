#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Whether claude's logins still work (platform job brunna/claude-login, hourly). Broken or about to expire:
Discord.

claude has three sets of credentials on the hub machine, each of which can break (the owner, 2026-10-24: a caller
silently falling back to a local model when the login lapsed is a serious silent failure):
  1. subscription login, under launchd: keychain. Only registry jobs with `connectors: true` use it (they need the
     account's claude.ai connectors); everything else the platform runs uses the headless token (the owner,
     2026-11-13). So it is checked only while some project's or enabled module's job declares connectors: true: then one
     real minimal request (llm.py's login-check). With none it is not checked and says why (no alert); with no
     registry index to read (a new machine) it is checked, since "no jobs known" is not "no such job"
  2. headless token (~/.claude/headless-oauth-token, `claude setup-token`, one year): llm.py's default credential
     (scheduler, leases, lease-resume). One request with LLM_AUTH=token; remind once the file is over 335 days old
     (30 days before the year runs out)
  3. ~/.claude/.credentials.json (what claude uses when the keychain cannot be read under SSH): only
     refreshTokenExpiresAt is looked at, reminding when fewer than 7 days remain -- no token is read or printed
Only a failed request that is a login problem (Login expired, /login, 401, OAuth…) counts as a broken login;
timeouts, quota and the like are only recorded in the output, no alert (alerting only after 3 in a row: claude
itself may be broken).

One problem is one alert: each is sent with a stable id (which credential, what is wrong), so its text may carry the
error and a count without a new wording counting as a new problem (2026-11-13: the tail of claude's output shifting by a
character re-sent the same broken login five times in a day). The error is also cleaned (`clean_error`) before it is
shown.

Usage: check-claude-login.py
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import alert  # noqa: E402
import hubconf  # noqa: E402
import i18n  # noqa: E402
# which logins exist, where they live and what their errors look like is the runtime's business (auth interface,
# layer 6)
import runtime_ports  # noqa: E402

STREAK = hubconf.STATE / "claude-login-streak.json"
SOFT_LIMIT = 3
_ = i18n.domain(__file__)


def connector_jobs() -> list[str] | None:
    """The jobs that use the subscription login (`connectors: true`), as <project>/<job>, from every project's registry
    entry and the enabled modules. None = can't tell (the registry index has not been produced yet: projects() then
    knows no jobs at all)."""
    try:   # not hubconf.projects(): without a readable index it falls back to entries with no jobs at all
        docs = dict(json.loads(hubconf.REGISTRY_INDEX.read_text())["projects"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    import registry
    docs.update(registry.load_modules()[0])
    return sorted(f"{name}/{job}" for name, doc in docs.items() if isinstance(doc, dict)
                  for job in registry.connector_jobs(doc))


# What the platform knows: who uses each kind of login (when one breaks, the alert must say who is affected)
def used_by(mode: str, jobs: list[str] | None = None) -> str | None:
    if mode == "subscription":
        return (_("used by the jobs with connectors: true: {jobs}", jobs=", ".join(jobs)) if jobs
                else _("used by the jobs with connectors: true"))
    return {"token": _("used by all scheduled jobs and leases")}.get(mode)


def clean_error(err: str) -> str:
    """The error as a person reads it: the runtime's "exit N: <tail of its output>" can start on a stray colon or a cut
    JSON key and end in a cut piece of JSON, depending on where a fixed-length tail happened to fall. Only those cut
    ends are trimmed (a whole JSON object in the middle stays as it is); telling one problem from another is the ids'
    job, not this."""
    err = re.sub(r"^(exit -?\d+):[\s:]*", r"\1: ", err.strip())
    err = re.sub(r'^(exit -?\d+: )"?[^"\s]*"\s*:\s*"', r"\1", err)        # lt":"OAuth error… → OAuth error…
    err = re.sub(r'^(exit -?\d+: )"', r"\1", err)
    err = re.sub(r'"\s*,\s*"[^"]*(?:"\s*:\s*"?[^"]*)?$', "", err)         # …jobs","type":"resu → …jobs
    return " ".join(err.split())[:200]


def missing_means(mode: str) -> str:
    return _("all scheduled jobs and leases will fail") if mode == "token" else _("every call with this login will fail")


def ask(env: dict) -> str | None:
    """None = success; otherwise the reason for the failure."""
    try:
        r = subprocess.run([sys.executable, str(hubconf.ROOT / "bin" / "llm.py"), "run", "login-check"],
                           input="Reply with just the two letters: ok", capture_output=True, text=True, timeout=180, env=env)
    except subprocess.TimeoutExpired:
        return _("No answer for over 3 minutes")
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return _("llm.py printed no JSON (exit {code}): {err}", code=r.returncode, err=r.stderr.strip()[-200:])
    return out.get("llm_error") or (out.get("result") if out.get("is_error") else None)


def main() -> int:
    now = datetime.datetime.now()
    hard: list[str] = []
    ids: list[str] = []          # one stable name per problem in hard (alert.once tells problems apart by these)
    soft: dict[str, str] = {}

    def problem(pid: str, text: str) -> None:
        ids.append(pid)
        hard.append(text)
    if runtime_ports.throwaway_only():   # its runs happen only in the platform's throwaway copy; login-check is not one
        print(_("not checked: the runtime {runtime} runs only in the platform's throwaway worktree; its login shows "
                "in its runs (a lost login fails them as a login error)", runtime=runtime_ports.default_name()))
        return 0   # the alert is left as it is: nothing was checked, so nothing recovered either
    # no checked copy of the tool to make the request with (doctor and the status page say so): the files and dates are
    # still checked, the requests aren't, and a request that wasn't made is no failure
    no_copy = runtime_ports.no_checked_copy()
    rt = runtime_ports.load(ports=("auth",))
    base = rt.clean_env({k: v for k, v in os.environ.items() if k != "LLM_AUTH"})

    jobs = connector_jobs()
    for c in rt.credentials():
        if c.mode == "subscription":
            if jobs == []:   # nothing the platform runs uses it: a lapsed login there breaks nothing
                print(_("{name}: not checked: no registry job declares connectors: true, and everything else the platform "
                        "runs uses the headless token", name=c.name))
                continue
            if jobs is None:
                print(_("{name}: the registry index has not been produced yet, so which jobs declare connectors: true "
                        "is not known; checked in case one does", name=c.name))
        if c.mode:  # a login that can be checked with a request: if present make one, if absent that is a problem
            if not c.present:
                problem(f"{c.mode}:missing", _("{path} is missing ({means})", path=c.path, means=missing_means(c.mode)))
                continue
            if no_copy:
                print(_("{name}: no request made: the platform has no copy of the coding agent to make it with: {reason}",
                        name=c.name, reason=no_copy))
            elif (err := ask(base if c.mode == "subscription" else {**base, "LLM_AUTH": c.mode})) is None:
                print(_("{name}: ok", name=c.name))
            elif rt.is_login_error(err):
                problem(f"{c.mode}:login", _("{label} ({used_by}) does not work: {err} — {renew}", label=c.label,
                                             used_by=used_by(c.mode, jobs) or c.used_by or "", err=clean_error(err),
                                             renew=c.renew))
            else:
                soft[c.mode] = err
        if c.present and c.since and c.max_age:
            age = now - c.since
            if age > c.max_age:
                problem(f"{c.path}:old", _("{label} is {days} {days:day|days} old (expires after {lifetime}); soon: {renew}",
                                           label=c.label, days=age.days, lifetime=c.lifetime, renew=c.renew))
        if c.present and c.expires:
            left = c.expires - now
            print(_("{name}: refresh valid for {days} more {days:day|days}", name=c.name, days=left.days))
            if c.warn_before and left < c.warn_before:
                problem(f"{c.path}:expiring", _("{label} ({used_by}): the login expires within {days} {days:day|days} — {renew}",
                                                label=c.label, used_by=c.used_by, days=max(left.days, 0), renew=c.renew))
        elif c.present and c.note:
            print(c.note)

    # what the last requests found ({id: text}), beside the streak: with no request made it stands as it was, so an open
    # alert keeps its ids and the streak its counts
    requested = STREAK.with_name("claude-login-requests.json")
    if no_copy:
        try:
            carried = json.loads(requested.read_text())
        except (OSError, ValueError):
            carried = {}
        for pid, text in (carried.items() if isinstance(carried, dict) else ()):
            if isinstance(pid, str) and isinstance(text, str) and pid not in ids:
                problem(pid, text)
    else:
        try:
            streak = json.loads(STREAK.read_text())
        except (OSError, ValueError):
            streak = {}
        streak = {k: streak.get(k, 0) + 1 for k in soft}
        STREAK.write_text(json.dumps(streak))
        for k, err in soft.items():
            print(_("{mode}: request failed (does not look like a login problem; {n} {n:time|times} in a row): {err}", mode=k,
                    n=streak[k], err=clean_error(err)))
            if streak[k] >= SOFT_LIMIT:
                problem(f"{k}:failing", _("claude requests failed {n} times in a row ({mode}): {err}", n=streak[k], mode=k,
                                          err=clean_error(err)))
        requested.write_text(json.dumps({pid: text for pid, text in zip(ids, hard)
                                         if pid.endswith((":login", ":failing"))}, ensure_ascii=False))

    alert.once("claude-login", hard, _("The claude login check is back to normal"), ids=ids)
    for x in hard:
        print(f"⚠️ {x}")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
