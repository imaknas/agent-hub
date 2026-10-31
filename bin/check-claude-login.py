#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Whether claude's logins still work (platform job agent-hub/claude-login, hourly). Broken or about to expire:
Discord.

claude has three sets of credentials on the hub machine, each of which can break (the owner, 2026-10-24: the
assistant silently falling back to the local model when the login lapsed is a serious silent failure):
  1. subscription login, under launchd (jobs that need claude.ai connectors, the assistant): keychain. Make one real
     minimal request (llm.py's login-check)
  2. headless token (~/.claude/headless-oauth-token, `claude setup-token`, one year): llm.py's default credential
     (scheduler, leases, lease-resume). One request with LLM_AUTH=token; remind once the file is over 335 days old
     (30 days before the year runs out)
  3. ~/.claude/.credentials.json (what claude uses when the keychain cannot be read under SSH): only
     refreshTokenExpiresAt is looked at, reminding when fewer than 7 days remain -- no token is read or printed
Only a failed request that is a login problem (Login expired, /login, 401, OAuth…) counts as a broken login;
timeouts, quota and the like are only recorded in the output, no alert (alerting only after 3 in a row: claude
itself may be broken).

Usage: check-claude-login.py
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
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


# What the platform knows: who uses each kind of login (when one breaks, the alert must say who is affected)
def used_by(mode: str) -> str | None:
    return {"subscription": _("used by the connector jobs and the assistant"),
            "token": _("used by all scheduled jobs and leases")}.get(mode)


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
    soft: dict[str, str] = {}
    if runtime_ports.throwaway_only():   # its runs happen only in the platform's throwaway copy; login-check is not one
        print(_("not checked: the runtime {runtime} runs only in the platform's throwaway worktree; its login shows "
                "in its runs (a lost login fails them as a login error)", runtime=runtime_ports.default_name()))
        return 0   # the alert is left as it is: nothing was checked, so nothing recovered either
    rt = runtime_ports.load(ports=("auth",))
    base = rt.clean_env({k: v for k, v in os.environ.items() if k != "LLM_AUTH"})

    for c in rt.credentials():
        if c.mode:  # a login that can be checked with a request: if present make one, if absent that is a problem
            if not c.present:
                hard.append(_("{path} is missing ({means})", path=c.path, means=missing_means(c.mode)))
                continue
            if (err := ask(base if c.mode == "subscription" else {**base, "LLM_AUTH": c.mode})) is None:
                print(_("{name}: ok", name=c.name))
            elif rt.is_login_error(err):
                hard.append(_("{label} ({used_by}) does not work: {err} — {renew}", label=c.label,
                              used_by=used_by(c.mode) or c.used_by or "", err=err[:200], renew=c.renew))
            else:
                soft[c.mode] = err
        if c.present and c.since and c.max_age:
            age = now - c.since
            if age > c.max_age:
                hard.append(_("{label} is {days} {days:day|days} old (expires after {lifetime}); soon: {renew}", label=c.label,
                              days=age.days, lifetime=c.lifetime, renew=c.renew))
        if c.present and c.expires:
            left = c.expires - now
            print(_("{name}: refresh valid for {days} more {days:day|days}", name=c.name, days=left.days))
            if c.warn_before and left < c.warn_before:
                hard.append(_("{label} ({used_by}): the login expires within {days} {days:day|days} — {renew}", label=c.label,
                              used_by=c.used_by, days=max(left.days, 0), renew=c.renew))
        elif c.present and c.note:
            print(c.note)

    try:
        streak = json.loads(STREAK.read_text())
    except (OSError, ValueError):
        streak = {}
    streak = {k: streak.get(k, 0) + 1 for k in soft}
    STREAK.write_text(json.dumps(streak))
    for k, err in soft.items():
        print(_("{mode}: request failed (does not look like a login problem; {n} {n:time|times} in a row): {err}", mode=k, n=streak[k],
                err=err[:200]))
        if streak[k] >= SOFT_LIMIT:
            hard.append(_("claude requests failed {n} times in a row ({mode}): {err}", n=streak[k], mode=k, err=err[:200]))

    alert.once("claude-login", hard, _("The claude login check is back to normal"))
    for x in hard:
        print(f"⚠️ {x}")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
