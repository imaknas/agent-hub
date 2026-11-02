"""Linux's service manager: the launchd plists the platform produces (render-services' render(), the scheduler and
watchdog templates in launchd/) → systemd user units (~/.config/systemd/user/), plus loading, status and unloading.
macOS keeps using launchd; this is used only when hubconf.service_manager() is systemd. Standard library only.

Mapping (these are all the plist fields the platform uses):
  ProgramArguments → ExecStart (each argument double-quoted; % and $ escaped by systemd's rules)
  WorkingDirectory, EnvironmentVariables → WorkingDirectory=, Environment=
  StandardOutPath / StandardErrorPath → StandardOutput=append: / StandardError=append:
  KeepAlive → Restart=always (RestartSec = ThrottleInterval, 10 s if unset), WantedBy=default.target
  StartInterval (without KeepAlive) → a Type=oneshot service + a timer (OnActiveSec 1 s = runs once on load, like
                                    RunAtLoad; OnUnitActiveSec = the interval)
  RunAtLoad, no interval, no KeepAlive → a plain service, WantedBy=default.target
To keep running after logout: `loginctl enable-linger <user>` is needed (doctor says so).
"""
from __future__ import annotations

import os
import pathlib
import subprocess

import i18n

_ = i18n.domain(__file__)

UNIT_DIR = pathlib.Path.home() / ".config" / "systemd" / "user"


def _q(s: str) -> str:
    """One ExecStart argument / Environment value: double-quoted; \\ and " escaped; % becomes %% (specifier); $ in
    ExecStart becomes $$."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def target(plist: dict) -> str:
    """The one unit to enable: with an interval and not always running → the timer; otherwise → the service."""
    return f"{plist['Label']}.timer" if plist.get("StartInterval") and not plist.get("KeepAlive") else f"{plist['Label']}.service"


def files(plist: dict) -> dict[str, str]:
    """plist → {file name: content}. The content depends only on the plist (the same plist gives the same result every
    time); render-services compares content to decide whether to reload."""
    label = plist["Label"]
    timer = target(plist).endswith(".timer")
    lines = ["[Unit]", f"Description=agent-hub {label}", "", "[Service]",
             f"Type={'oneshot' if timer else 'simple'}",
             "ExecStart=" + " ".join(_q(a).replace("$", "$$") for a in plist["ProgramArguments"])]
    if plist.get("WorkingDirectory"):
        lines.append(f"WorkingDirectory={plist['WorkingDirectory']}")
    for k, v in sorted((plist.get("EnvironmentVariables") or {}).items()):
        lines.append(f"Environment={_q(f'{k}={v}')}")
    if plist.get("StandardOutPath"):
        lines.append(f"StandardOutput=append:{plist['StandardOutPath']}")
    if plist.get("StandardErrorPath"):
        lines.append(f"StandardError=append:{plist['StandardErrorPath']}")
    if plist.get("KeepAlive"):
        lines += ["Restart=always", f"RestartSec={int(plist.get('ThrottleInterval') or 10)}"]
    if not timer:
        lines += ["", "[Install]", "WantedBy=default.target"]
    out = {f"{label}.service": "\n".join(lines) + "\n"}
    if timer:
        out[f"{label}.timer"] = "\n".join([
            "[Unit]", "Description=" + _("brunna {label} (every {secs} s)", label=label, secs=int(plist['StartInterval'])),
            "",
            "[Timer]", "OnActiveSec=1" if plist.get("RunAtLoad") else f"OnActiveSec={int(plist['StartInterval'])}",
            f"OnUnitActiveSec={int(plist['StartInterval'])}", "AccuracySec=1", "",
            "[Install]", "WantedBy=timers.target"]) + "\n"
    return out


def installed(label: str) -> dict[str, str]:
    """The unit files currently installed for this label (none = empty)."""
    out = {}
    for suffix in (".service", ".timer"):
        f = UNIT_DIR / f"{label}{suffix}"
        if f.exists():
            out[f.name] = f.read_text()
    return out


def systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def install(plist: dict) -> subprocess.CompletedProcess:
    """Write the unit files, daemon-reload, enable --now the one to run; if the service is already running, restart it
    (to pick up the new content)."""
    label, want = plist["Label"], files(plist)
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    for name in set(installed(label)) - set(want):
        (UNIT_DIR / name).unlink()   # e.g. it used to have an interval and now runs all the time
    for name, text in want.items():
        (UNIT_DIR / name).write_text(text)
    systemctl("daemon-reload")
    r = systemctl("enable", "--now", target(plist))
    if r.returncode == 0 and target(plist).endswith(".service"):
        systemctl("restart", f"{label}.service")
    return r


def remove(label: str) -> None:
    for name in installed(label):
        systemctl("disable", "--now", name)
        (UNIT_DIR / name).unlink(missing_ok=True)
    systemctl("daemon-reload")


def state(label: str) -> str | None:
    """The same kind of answer as render-services' running(): not loaded = None; an always-running service → running /
    not running; a timer → whether it is scheduled (active)."""
    have = installed(label)
    if not have:
        return None
    unit = f"{label}.timer" if f"{label}.timer" in have else f"{label}.service"
    r = systemctl("is-active", unit)
    return "running" if r.stdout.strip() == "active" else "not running"


def linger() -> bool | None:
    """Whether user units keep running after logout (loginctl enable-linger); can't tell = None."""
    r = subprocess.run(["loginctl", "show-user", os.environ.get("USER") or str(os.getuid()), "-p", "Linger"],
                       capture_output=True, text=True)
    return None if r.returncode != 0 else r.stdout.strip() == "Linger=yes"
