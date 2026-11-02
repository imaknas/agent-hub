#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Registry services -> launchd plists (~/Library/LaunchAgents/<label>.plist).

Each project declares its long-running programs in projects/<name>.yaml; the platform generates the plists by the
rules here and does bootout/bootstrap only when the content changed (nothing changed: nothing happens, no service is
restarted for no reason). A service removed from the registry is unloaded only if this script generated it (recorded
in .state/rendered-services.json); other plists are not touched. The platform's own core (scheduler, watchdog, status
page) is not here: those are version-controlled plists under launchd/ -- the scheduler cannot reload itself.

service fields (validated by registry.py):
  name                 required; label defaults to local.<project>.<name>
  label                keep an old label (e.g. other scripts restart it by label)
  run                  command; the first word is resolved to an absolute path, other arguments that are files
                       existing under cwd become absolute paths too
  cwd                  runtime (default when there is a runtime) | workspace | hub
  keepalive: true      KeepAlive (restart when it dies); without it: runs once / per interval_s
  interval_s           StartInterval (run every so many seconds)
  throttle_s           ThrottleInterval
  env                  environment variables; a value that is a relative path existing under cwd becomes an absolute
                       path. When run's first word is a command name (node) and no PATH is given: it is looked up
                       by hubconf.LAUNCHD_PATH, which is also written into the plist (the nvm default version's bin
                       first -- node is managed only by nvm, see hubconf.nvm_bin). Run by path (bin/x.sh): no PATH
                       added; $PLATFORM_PATH in an env value is replaced by that same PATH
  log                  one file (stdout and stderr both go there, relative to cwd); without it:
                       <hub>/.logs/<label without local.>-{stdout,stderr}.log
  expose               {tailnet_https, local_port}: declaration only, tailscale serve is not run automatically --
                       the status page reminds you to run it by hand. For a sandboxed project local_port is the only
                       port its service may listen on
A sandboxed project's (enforce.sandbox) services run through bin/sandboxed-run (isolation.py: Seatbelt on macOS,
Landlock on Linux); where no adapter works they are not rendered (reported like a command that can't be found). On
Linux the unit gets no sandboxing directives of its own: a systemd user unit silently drops them when user namespaces
are restricted (measured 10-31, docs/design/capabilities.md).

Safety net (2026-10-24: Homebrew's node was deleted, this script switched the plist to a `node` that could not be
found, and a LINE bot was down for a day and a half without anyone knowing):
  - the command's first word cannot be resolved -> leave the current plist alone (what runs keeps running), only
    report the problem
  - bootstrap failed, a keepalive service is not running, something that should be loaded is not -> report it
  Problems go to Discord (once per set, once more on recovery), exit 1 (the scheduler records job.failed, the status
  page turns red).

Usage: render-services.py [--dry-run] [--only <label>]
Also an applier (appliers.py): plan()/apply(), used by `bin/hub config plan/apply`.
"""
from __future__ import annotations

import json
import os
import pathlib
import plistlib
import shlex
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import alert  # noqa: E402
import hubconf  # noqa: E402
import i18n  # noqa: E402
import isolation  # noqa: E402
import registry  # noqa: E402

_ = i18n.domain(__file__)

AGENTS = pathlib.Path.home() / "Library" / "LaunchAgents"
# The PATH under launchd: commands are resolved by this (or by the service's own env PATH), not by the PATH of the
# shell running this script (the shell may have nvm and the like; launchd does not)
DEFAULT_PATH = hubconf.LAUNCHD_PATH
RENDERED = hubconf.STATE / "rendered-services.json"


def label_of(project: str, svc: dict) -> str:
    return svc.get("label") or f"local.{project}.{svc['name']}"


def render(project: str, doc: dict, svc: dict) -> tuple[dict, str | None]:
    """(plist, problem). problem = the command's first word cannot be resolved (the plist is then unusable)."""
    d = registry.dirs(doc)
    cwd = d[svc.get("cwd") or registry.default_cwd(doc)]
    words = shlex.split(svc["run"])
    first = words[0]
    search = hubconf.expand_env((svc.get("env") or {}).get("PATH") or DEFAULT_PATH)
    if not first.startswith("/"):
        first = str(cwd / first) if "/" in first else (shutil.which(first, path=search) or first)
    problem = None if pathlib.Path(first).is_absolute() and os.access(first, os.X_OK) else \
        _("`{command}` not found (PATH={path})", command=words[0], path=search)
    args = [first] + [str(cwd / w) if not w.startswith(("/", "-")) and (cwd / w).exists() else w for w in words[1:]]
    if isolation.sandboxed(doc):
        # a sandboxed project's service gets no more than its sessions (bin/sandboxed-run); where that can't be done it
        # is not rendered at all
        if (why := isolation.can_sandbox()):
            problem = _("{project} is sandboxed and {why}; its services are not run unsandboxed", project=project, why=why)
        args = isolation.wrap(project, cwd, args, (svc.get("expose") or {}).get("local_port"))
    label = label_of(project, svc)
    p: dict = {"Label": label, "ProgramArguments": args, "WorkingDirectory": str(cwd)}
    env = {k: (str(cwd / v) if isinstance(v, str) and not v.startswith("/") and (cwd / v).exists()
               else hubconf.expand_env(str(v))) for k, v in (svc.get("env") or {}).items()}
    if "/" not in words[0] and "PATH" not in env:
        # the command was found by the platform's PATH (node etc.); the process itself (npm's child processes) must
        # see it too
        env["PATH"] = DEFAULT_PATH
    if env:
        p["EnvironmentVariables"] = env
    p["RunAtLoad"] = True
    if svc.get("keepalive"):
        p["KeepAlive"] = True
    if svc.get("interval_s"):
        p["StartInterval"] = int(svc["interval_s"])
    if svc.get("throttle_s"):
        p["ThrottleInterval"] = int(svc["throttle_s"])
    if svc.get("log"):
        p["StandardOutPath"] = p["StandardErrorPath"] = str(cwd / svc["log"])
    else:
        base = hubconf.LOGS / label.removeprefix("local.")
        p["StandardOutPath"], p["StandardErrorPath"] = f"{base}-stdout.log", f"{base}-stderr.log"
    return p, problem


def bootstrap(uid: int, label: str, path: pathlib.Path) -> subprocess.CompletedProcess:
    """bootout is asynchronous: bootstrapping before the old process has ended gives `5: Input/output error` (and the
    service stays unloaded). Wait for it to disappear from launchd (at most 20 seconds), and retry a few times on
    failure."""
    import time
    for _wait in range(40):
        if launchctl("print", f"gui/{uid}/{label}").returncode != 0:
            break
        time.sleep(0.5)
    for attempt in range(5):
        r = launchctl("bootstrap", f"gui/{uid}", str(path))
        if r.returncode == 0:
            return r
        time.sleep(2 * (attempt + 1))
    return r


def running(uid: int, label: str) -> str | None:
    """The state from launchctl print (running / not running / …); None when not loaded."""
    if hubconf.service_manager() == "systemd":
        import systemd_units
        return systemd_units.state(label)
    r = launchctl("print", f"gui/{uid}/{label}")
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        if line.strip().startswith("state = "):
            return line.split("=", 1)[1].strip()
    return "unknown"


def launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True)


# ---------- applier (appliers.py): plan only computes, does not touch launchd; apply writes plists + reloads;
# health checks and alerts stay in main ----------

EXTERNAL = False


def _desired(index: dict) -> tuple[dict[str, tuple[str, dict]], list[str], set[str], set[str]]:
    """(label -> (project, plist), problems, labels left alone because the command cannot be resolved, keepalive
    labels)."""
    want: dict[str, tuple[str, dict]] = {}
    problems: list[str] = []
    keep: set[str] = set()       # command cannot be resolved: the current plist is left alone
    keepalive: set[str] = set()  # should be running all the time
    # services of enabled modules too
    docs = sorted(index["projects"].items()) + sorted((index.get("modules") or {}).items())
    for name, doc in docs:
        for svc in doc.get("services") or []:
            label = label_of(name, svc)
            plist, problem = render(name, doc, svc)
            want[label] = (name, plist)
            if problem:
                problems.append(_("{label} ({project}): {problem}; the plist is left as it is (and not created if it is missing)",
                                  label=label, project=name, problem=problem))
                keep.add(label)
            if svc.get("keepalive"):
                keepalive.add(label)
    return want, problems, keep, keepalive


def _mine() -> set[str]:
    try:
        return set(json.loads(RENDERED.read_text()))
    except (OSError, ValueError):
        return set()


def _changes(want: dict, keep: set[str], only: str | None = None) -> list[tuple]:
    """(Change, the plist's current content, the plist to write); for remove the last two are None."""
    from appliers import Change
    out = []
    systemd = hubconf.service_manager() == "systemd"
    if systemd:
        import systemd_units
    for label, (proj, plist) in sorted(want.items()):
        if (only and label != only) or label in keep:
            continue
        if systemd:   # compares the generated unit file's content (the plist has the same source)
            cur_units, want_units = systemd_units.installed(label), systemd_units.files(plist)
            if cur_units != want_units:
                keys = sorted(n for n in set(cur_units) | set(want_units) if cur_units.get(n) != want_units.get(n))
                out.append((Change("update" if cur_units else "add", f"service:{label}",
                                   proj + (":" + _(" changed {keys}", keys=_(", ").join(keys)) if cur_units else "")),
                        None, plist))
            continue
        path = AGENTS / f"{label}.plist"
        cur = plistlib.loads(path.read_bytes()) if path.exists() else None
        if cur == plist:
            continue
        keys = [] if cur is None else sorted(k for k in set(cur) | set(plist) if cur.get(k) != plist.get(k))
        out.append((Change("add" if cur is None else "update", f"service:{label}",
                           proj + (":" + _(" changed {keys}", keys=_(", ").join(keys)) if keys else "")), cur, plist))
    for label in sorted(_mine() - set(want)):
        if not only or label == only:
            out.append((Change("remove", f"service:{label}", _("no longer in the registry")), None, None))
    # removals first: a service whose label changed (the rename, docs/design/rename-brunna.md) frees its port before
    # the new label binds it
    return sorted(out, key=lambda c: c[0].op != "remove")


def _apply_one(uid: int, ch, plist: dict | None, problems: list[str]) -> None:
    label = ch.target.removeprefix("service:")
    if hubconf.service_manager() == "systemd":
        import systemd_units
        if ch.op == "remove":
            systemd_units.remove(label)
            return
        r = systemd_units.install(plist)
        if r.returncode != 0:
            print("    " + _("⚠️ systemctl enable failed: {error}", error=r.stderr.strip()))
            problems.append(_("{label} ({project}): systemctl enable failed: {error}", label=label,
                              project=ch.detail.split(":")[0], error=r.stderr.strip()[:200]))
        return
    if ch.op == "remove":
        launchctl("bootout", f"gui/{uid}/{label}")
        (AGENTS / f"{label}.plist").unlink(missing_ok=True)
        return
    launchctl("bootout", f"gui/{uid}/{label}")
    path = AGENTS / f"{label}.plist"
    path.write_bytes(plistlib.dumps(plist))
    r = bootstrap(uid, label, path)
    if r.returncode != 0:
        print("    " + _("⚠️ bootstrap failed: {error}", error=r.stderr.strip()))
        problems.append(_("{label} ({project}): bootstrap failed: {error}", label=label, project=ch.detail.split(":")[0],
                          error=r.stderr.strip()[:200]))


def plan(index: dict | None = None) -> list:
    if hubconf.service_manager() is None:
        return []   # no service manager: nothing can be applied (doctor and main say so)
    want, _problems, keep, _keepalive = _desired(index or registry.refresh())
    return [c for c, _cur, _plist in _changes(want, keep)]


def apply(index: dict | None = None) -> list:
    if hubconf.service_manager() is None:
        return []
    want, problems, keep, _keepalive = _desired(index or registry.refresh())
    uid = os.getuid()
    todo = _changes(want, keep)
    for ch, _cur, plist in todo:
        _apply_one(uid, ch, plist, problems)
    RENDERED.parent.mkdir(parents=True, exist_ok=True)
    RENDERED.write_text(json.dumps(sorted(want)))
    return [c for c, _cur, _plist in todo]


def uninstall(apply: bool) -> list[str]:
    """agenthub uninstall: every service this script generated (and only those), unloaded and its file removed; the
    labels."""
    from appliers import Change
    labels = sorted(_mine())
    if apply and labels and hubconf.service_manager():
        uid, problems = os.getuid(), []
        for label in labels:
            _apply_one(uid, Change("remove", f"service:{label}", ""), None, problems)
        RENDERED.unlink(missing_ok=True)
    return labels


def main() -> int:
    if hubconf.service_manager() is None:
        print(_("services: {reason}", reason=hubconf.no_service_manager()))
        return 0
    dry = "--dry-run" in sys.argv
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    index = registry.refresh()
    uid = os.getuid()
    want, problems, keep, keepalive = _desired(index)
    todo = _changes(want, keep, only)
    changed = 0
    for ch, cur, plist in todo:
        label = ch.target.removeprefix("service:")
        if ch.op == "remove":
            print(("(dry-run) " if dry else "") + _("{label}: no longer in the registry, unloading", label=label))
        else:
            changed += 1
            print(("(dry-run) " if dry else "") + _("{label} ({project}): {op}", label=label, project=want[label][0],
                                                    op=_("add") if ch.op == "add" else _("update")))
            if cur is not None:
                for k in sorted(set(cur) | set(plist)):
                    if cur.get(k) != plist.get(k):
                        print(f"    {k}: {cur.get(k)!r} → {plist.get(k)!r}")
        if not dry:
            _apply_one(uid, ch, plist, problems)
    if not dry and not only:
        RENDERED.parent.mkdir(parents=True, exist_ok=True)
        RENDERED.write_text(json.dumps(sorted(want)))
    if not changed:
        print(_("services: everything matches the registry"))
    if not dry and not only:
        for label in sorted(want):
            if any(label in x for x in problems):
                continue
            state = running(uid, label)
            if state is None:
                problems.append(_("{label} ({project}): not loaded in launchd", label=label, project=want[label][0]))
            elif label in keepalive and state != "running":
                problems.append(_("{label} ({project}): the keepalive service is not running (state = {state})", label=label,
                                  project=want[label][0], state=state))
        alert.once("render-services", problems, _("The background services are all back to normal (render-services)"))
    for x in problems:
        print(f"⚠️ {x}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
