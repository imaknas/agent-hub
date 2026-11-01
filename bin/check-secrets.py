#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Whether the secrets declared in the registry (secrets: in projects/<p>.yaml) exist, are backed up, and are close to
expiring (platform job agent-hub/secrets, daily).

Who owns which secrets (decided by the owner on 2026-10-24): machine-level ones (claude, Discord, B2, the backup
password…) are the platform's, declared in agent-hub's registry entry; keys for a project's own services are the
project's, declared in its own registry entry (manifest.update); issuing new ones at the external service, revoking
and replacing them is done by the owner.
The registry holds only names, locations and expiry, **never values**; this script neither reads nor prints values:
  - exists: the file exists and is not empty; with env_key (one line in a .env) only whether that key is there and
    the part after the equals sign is not empty
  - backup: unless backup is false, per backup.covers() (the same rules as the nightly backup)
  - expiry: the expires date, or the file's mtime + max_age_days; reported when fewer than warn_days (default 14)
    days remain
  - permissions: readable by group/others is only printed, not a problem
  - undeclared: files in each project's runtime / workspace (and the platform's .secrets/) whose names look like
    credentials but are not declared are only listed as a reminder (not a problem, content not read) -- either
    declare them or confirm they are not credentials
On problems: the alert channels (once per set, once more on recovery), exit 1 (the scheduler records a failure, the
status page turns red). No alert channel at all is printed as a reminder, not a problem.

Usage: check-secrets.py [project...]
"""
from __future__ import annotations

import datetime
import os
import pathlib
import re
import stat
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import alert  # noqa: E402
import backup  # noqa: E402
import hubconf  # noqa: E402
import i18n  # noqa: E402
import registry  # noqa: E402

_ = i18n.domain(__file__)

WARN_DAYS = 14
LOOKS_SECRET = re.compile(r"(^\.env($|\.)|token|secret|credential|oauth|api[-_]?key|password|\.pem$|\.key$|\.p12$)", re.I)
NOT_SECRET = re.compile(r"(\.(example|sample|template|py|pyc|ts|tsx|js|mjs|cjs|md|sh|html|css|map|lock)$)", re.I)
SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "dist", "build", "__pycache__", ".next", ".agent-memory", ".claude",
             "worktrees", "logs", ".logs"}


def locate(project: str, doc: dict, raw: str) -> pathlib.Path | None:
    """The platform's own paths (modules included) are resolved by hubconf.path (secrets are in secrets_dir(), not in
    the hub root after the move); a project's are relative to its runtime/workspace."""
    if raw.startswith(("~", "/")) or project == hubconf.hub_project() or doc.get("type") == "module":
        return hubconf.path(raw)
    d = registry.dirs(doc)
    base = d["runtime"] if doc.get("runtime") is not None else d["workspace"]
    return base / raw if base else None


def has_key(path: pathlib.Path, key: str) -> bool:
    """Whether the .env has `KEY=<non-empty>`. Only the key is compared; the value is neither kept nor printed."""
    for line in path.read_text(errors="replace").splitlines():
        k, sep, v = line.strip().removeprefix("export ").partition("=")
        if sep and k.strip() == key:
            return bool(v.strip().strip("'\""))
    return False


def check(project: str, doc: dict, sec: dict, today: datetime.date) -> tuple[list[str], list[str]]:
    """(problems, notes)"""
    name = f"{project}:{sec['name']}"
    path = locate(project, doc, sec["path"])
    # a module's rotate text is in the "modules" catalog; a project's own registry text is shown as written
    how = _(" — {rotate}", rotate=i18n.translate("modules", sec["rotate"], {})) if sec.get("rotate") else ""
    if not path or not path.exists():
        return [_("{name} is missing ({path})", name=name, path=path or sec["path"]) + how], []
    probs, notes = [], []
    if path.is_file() and path.stat().st_size == 0:
        probs.append(_("{name} is an empty file ({path})", name=name, path=path))
    if sec.get("env_key") and path.is_file() and not has_key(path, sec["env_key"]):
        probs.append(_("{name}: {path} has no {key} (or it is empty)", name=name, path=path, key=sec["env_key"]))
    if sec.get("backup", True) and not backup.covers(path):
        probs.append(_("{name} is not backed up ({path}) — add it to hub.json backup.include, or write backup: false + why "
                       "in the registry", name=name, path=path))
    exp = None
    if sec.get("expires"):
        exp = datetime.date.fromisoformat(sec["expires"])
    elif sec.get("max_age_days"):
        exp = datetime.date.fromtimestamp(path.stat().st_mtime) + datetime.timedelta(days=sec["max_age_days"])
    if exp:
        left = (exp - today).days
        if left < 0:
            probs.append(_("{name} has expired ({date})", name=name, date=exp) + how)
        elif left == 0:
            probs.append(_("{name} expires today ({date})", name=name, date=exp) + how)
        elif left <= sec.get("warn_days", WARN_DAYS):
            probs.append(_("{name} expires in {days} {days:day|days} ({date})", name=name, days=left, date=exp) + how)
        if left < 0:
            notes.append(_("{name}: expired {days} {days:day|days} ago ({date})", name=name, date=exp, days=-left))
        elif left == 0:
            notes.append(_("{name}: expires today ({date})", name=name, date=exp))
        else:
            notes.append(_("{name}: expires {date} ({days} {days:day|days} left)", name=name, date=exp, days=left))
    mode = path.stat().st_mode
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        notes.append(_("{name}: readable by group/others ({mode}, chmod 600 recommended)", name=name, mode=oct(mode & 0o777)))
    return probs, notes


def undeclared(project: str, doc: dict, declared: set[pathlib.Path]) -> list[pathlib.Path]:
    """Files under runtime / workspace (within three levels) whose names look like credentials and are not declared. For
    the platform itself only .secrets/ is looked at, without its sessions' wake addresses (hubconf.session_addresses_dir:
    the platform's own, written and removed by the session hooks)."""
    d = registry.dirs(doc)
    roots = [hubconf.secrets_dir()] if project == hubconf.hub_project() else \
        [r for r in (d["runtime"], d["workspace"]) if r]
    found, skip = [], hubconf.session_addresses_dir()
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(pathlib.Path(dirpath).relative_to(root).parts)
            dirnames[:] = [] if depth >= 3 else [x for x in dirnames if x not in SKIP_DIRS
                                                 and pathlib.Path(dirpath, x) != skip]
            for fn in filenames:
                path = pathlib.Path(dirpath) / fn
                if LOOKS_SECRET.search(fn) and not NOT_SECRET.search(fn) and path.resolve() not in declared:
                    found.append(path)
    return sorted(set(found))


def main(argv: list[str]) -> int:
    today = datetime.date.today()
    index = registry.refresh()  # a registry change just made counts too, without waiting for the scheduler's tick
    projects = index["projects"]
    # Enabled modules' secrets are the platform's (e.g. the claude module's headless token); disabled modules are not
    # checked -- someone who does not use Claude is not asked for a Claude token
    modules = {n: d for n, d in (index.get("modules") or {}).items() if d.get("secrets")}
    module_paths = {p.resolve() for d in modules.values() for s in d["secrets"] if (p := locate(d["name"], d, s["path"]))}
    problems, count = [], 0
    hub = hubconf.hub_project()
    if hub in projects:
        # the API key of hub.json llm (the kind kept in the secrets directory) is a platform secret: no need to add it
        # to the registry
        import completion
        llm_keys = [{"name": f"llm-{n}-key", "path": str(hubconf.secrets_dir() / p["key"]["secret"]),
                     "why": _("API key of hub.json llm.providers.{name}", name=n)}
                    for n, p in (((completion.settings() or {}).get("providers")) or {}).items()
                    if isinstance(p, dict) and isinstance(p.get("key"), dict) and p["key"].get("secret")]
        # the alert channel's credentials (only when hub.json alerts sets up Discord): the platform's too, no need to
        # add them to the registry
        alert_keys = [{"name": "discord-bot-token", "path": tok,
                       "why": _("the Discord bot for alerts (watchdog, render-services, claude-login, secrets)")}
                      for tok in [hubconf.get("alerts.discord_token_file")] if tok]
        if llm_keys or alert_keys:
            projects = {**projects, hub: {**projects[hub],
                                          "secrets": [*(projects[hub].get("secrets") or []), *alert_keys, *llm_keys]}}
    for project in sorted(argv or [*projects, *modules]):
        doc = projects.get(project) or modules.get(project) or {}
        declared = set(module_paths) if project == hubconf.hub_project() else set()
        for sec in doc.get("secrets") or []:
            count += 1
            probs, notes = check(project, doc, sec, today)
            problems += probs
            for n in notes:
                print(n)
            if (path := locate(project, doc, sec["path"])):
                declared.add(path.resolve())
        for path in ([] if doc.get("type") == "module" else undeclared(project, doc, declared)):
            print(_("{project}: not declared, file name looks like a credential: {path} (declare it under secrets: in "
                    "the registry, or confirm it is not one)", project=project, path=path))
    if not argv:
        # the alert channel itself: if the token stops working, no alert can be sent when something goes wrong -- check
        # once a day first (without sending a message)
        if alert.probe():
            alert.clear_failure("discord-probe")
        elif not alert.configured():
            # not set up is not broken: a reminder, not a failing job (the status page shows the same reminder)
            print(_("Reminder: no alert channel is set, so nothing tells you when something breaks — set one in hub.json "
                    "alerts (ntfy is the simplest: a phone app, no account)"))
        else:
            alert.record_failure("discord-probe", _("The Discord bot token does not work (GET /users/@me failed); no "
                                                    "platform alert can be sent"))
            problems.append(_("The Discord alert channel does not work (bot token invalid or unreachable) — the status page "
                              "shows it in red too"))
        alert.once("secrets", problems, _("The credentials declared in the registry are all fine again"))
    for p in problems:
        print(f"⚠️ {p}")
    print(_("Checked {count} {count:credential|credentials}, {problems} {problems:problem|problems}", count=count,
            problems=len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
