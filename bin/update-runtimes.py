#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Keep each coding agent the platform uses on a checked copy (docs/design/runtime-versions.md section 3; replaces
update-claude.py). Nightly, platform job brunna/update-runtimes.

  update-runtimes.py [--first] [--runtime <name>]
    For each runtime in use with a distribution port (runtime.default, runtime.installed, llm.points pins, providers
    whose impl is a runtime):
    - no checked copy (`current`) in the store: the first install (the newest version, checked, promoted);
    - a newer version published: installed beside `current`, every conformance check run, promoted when the result's
      verdict is pass (a not-checkable check doesn't block it, an error holds it back) and it is at least MIN_VERSION;
      otherwise held back: `current` stays, an alert per check and reason (the next version failing the same way doesn't
      alert again), once more after 7 days held back;
    - no new version: the daily checks on `current` (the server side moves without a new version, section 4);
    - then the user's own copy (nvm's) raised to the promoted version when older, never lowered (section 8; DV2 (a));
    - versions other than current and previous that no run holds, 14 days old: removed.
    --first: only the first install, for a runtime with no checked copy (brunna upgrade runs this after its steps; a
    failure is for doctor to show, never a halted upgrade).
  Exit 0 when every runtime in use has a checked copy, 1 otherwise. From a linked worktree of the hub it refuses: the
  store is the machine's.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import alert  # noqa: E402
import conformance  # noqa: E402
import hubconf  # noqa: E402
import i18n  # noqa: E402
import runtime_ports  # noqa: E402
import runtime_store  # noqa: E402

_ = i18n.domain(__file__)
HELD = hubconf.STATE / "runtime-held.json"   # runtime → {version, since}
ESCALATE_DAYS = 7
DAILY = ("agent-result", "completion-no-tools", "completion-schemas")


def usage() -> str:
    return _("usage: update-runtimes.py [--first] [--runtime <name>]")


def in_use() -> list[str]:
    """The runtimes the platform starts runs with, that have a distribution port."""
    names = {hubconf.get("runtime.default") or runtime_ports.DEFAULT}
    names.update(hubconf.get("runtime.installed") or [])
    for point in (hubconf.get("llm.points") or {}).values():
        if isinstance(point, dict) and point.get("runtime"):
            names.add(point["runtime"])
    for prov in (hubconf.get("llm.providers") or {}).values():
        if isinstance(prov, dict) and prov.get("impl") in runtime_ports.RUNTIMES:
            names.add(prov["impl"])
    return sorted(n for n in names if n in runtime_ports.RUNTIMES and "distribution" in runtime_ports.capabilities(n))


def newer(a: str, b: str) -> bool:
    return tuple(map(int, a.split("."))) > tuple(map(int, b.split(".")))


def _held(runtime: str) -> dict:
    try:
        return json.loads(HELD.read_text()).get(runtime) or {}
    except (OSError, ValueError):
        return {}


def _set_held(runtime: str, value: dict | None) -> None:
    try:
        d = json.loads(HELD.read_text())
    except (OSError, ValueError):
        d = {}
    if value:
        d[runtime] = value
    else:
        d.pop(runtime, None)
    HELD.parent.mkdir(parents=True, exist_ok=True)
    HELD.write_text(json.dumps(d, indent=1) + "\n")


def _problems(doc: dict) -> tuple[list[str], list[str]]:
    """(the alert's lines, their stable ids: check and reason, never the version)."""
    lines, ids = [], []
    for name, c in doc.get("checks", {}).items():
        if c.get("state") in ("fail", "error"):
            lines.append(_("{runtime} {version}: {check} {state}: {reason}", runtime=doc["runtime"],
                           version=doc["version"], check=name, state=c["state"], reason=c.get("reason") or "-"))
            ids.append(f"{name}:{c['state']}:{c.get('reason') or ''}")
    return lines, ids


def promotable(doc: dict, mod) -> str | None:
    """None when this result may be promoted; else why not."""
    if conformance.verdict(doc) != "pass":
        return _("its checks didn't pass")
    floor = getattr(mod, "MIN_VERSION", None)
    if floor and newer(floor, doc["version"]):
        return _("{version} is below the oldest version the platform runs on ({floor})", version=doc["version"], floor=floor)
    return None


def _quiet(runtime: str, *keys: str) -> None:
    """The alerts of a state that has passed are resolved (each is a no-op when it isn't open)."""
    for key in keys:
        alert.once(f"runtime-{key}-{runtime}", [], _("{runtime}: back to normal ({what})", runtime=runtime, what=key))


def first_or_new(runtime: str, mod, first: bool) -> tuple[bool, bool, str]:
    """(whether the runtime ends with a checked copy, whether a version was just promoted, what happened)."""
    cur = runtime_store.current(runtime)
    if first and cur:
        return True, False, _("{runtime}: checked copy {version} in place", runtime=runtime, version=cur.version)
    try:
        latest = mod.latest()
    except RuntimeError as e:
        alert.once(f"runtime-latest-{runtime}", [_("{runtime}: the newest version can't be told, so none is checked: "
                                                   "{error}", runtime=runtime, error=e)],
                   _("{runtime}: back to normal ({what})", runtime=runtime, what="latest"), ids=["latest"])
        return cur is not None, False, _("{runtime}: the newest version can't be told: {error}", runtime=runtime, error=e)
    _quiet(runtime, "latest")
    held = _held(runtime)
    skip = runtime_store.rolled_back_from(runtime)
    if cur and (not newer(latest, cur.version) or (skip and not newer(latest, skip))):
        # nothing newer than what runs (or than the version rolled back from): whatever was held back has passed
        if held:
            _set_held(runtime, None)
        _quiet(runtime, "held", "install", "checks")
        return True, False, ""
    try:
        exe = runtime_store.install(runtime, latest, mod)
    except RuntimeError as e:
        alert.once(f"runtime-install-{runtime}", [_("{runtime} {version} could not be installed: {error}", runtime=runtime,
                                                    version=latest, error=e)],
                   _("{runtime}: installing works again", runtime=runtime), ids=["install"])
        return cur is not None, False, _("{runtime} {version}: not installed: {error}", runtime=runtime, version=latest, error=e)
    _quiet(runtime, "install")
    try:
        doc = conformance.run(runtime, str(exe))
    except RuntimeError as e:
        alert.once(f"runtime-checks-{runtime}", [_("{runtime} {version}: the checks could not run, so it isn't promoted: "
                                                   "{error}", runtime=runtime, version=latest, error=e)],
                   _("{runtime}: back to normal ({what})", runtime=runtime, what="checks"), ids=["checks"])
        return cur is not None, False, _("{runtime} {version}: the checks could not run: {error}", runtime=runtime,
                                         version=latest, error=e)
    _quiet(runtime, "checks")
    result = conformance.record(doc)
    why = promotable(doc, mod)
    if why is None:
        try:   # what is promoted is what was checked: the same digest, or nothing
            runtime_store.promote(runtime, latest, str(result), expect=doc.get("digest"))
        except RuntimeError as e:
            alert.once(f"runtime-checks-{runtime}", [str(e)], _("{runtime}: back to normal ({what})", runtime=runtime,
                                                                 what="checks"), ids=["promote"])
            return cur is not None, False, _("{runtime} {version}: not promoted: {error}", runtime=runtime,
                                             version=latest, error=e)
        _set_held(runtime, None)
        alert.once(f"runtime-held-{runtime}", [], _("{runtime} {version} passed its checks and runs the platform's work",
                                                    runtime=runtime, version=latest))
        return True, True, _("{runtime}: {old} → {new}, checked and promoted", runtime=runtime,
                             old=cur.version if cur else _("none"), new=latest)
    since = held.get("since") if held.get("version") else None
    since = since or datetime.date.today().isoformat()
    _set_held(runtime, {"version": latest, "since": since})
    lines, ids = _problems(doc)
    find = getattr(mod, "user_copy", None)
    mine = find() if callable(find) else None
    if mine and mod.version_of(mine) == latest:   # until platform runs move to the store, they run the user's copy
        lines.append(_("platform runs already use {version}: it is your own copy, which they start until they move to "
                       "the store", version=latest))
        ids.append("runs-it")
    days = (datetime.date.today() - datetime.date.fromisoformat(since)).days
    if days >= ESCALATE_DAYS:
        lines.append(_("{runtime} has been held back at {version} since {since}", runtime=runtime,
                       version=cur.version if cur else _("none"), since=since))
        ids.append(f"held-since:{since}")
    alert.once(f"runtime-held-{runtime}", lines or [why], _("{runtime}: no version is held back now", runtime=runtime),
               ids=ids or [f"held:{why}"])
    return cur is not None, False, _("{runtime} {version}: held back ({why}); {current} stays", runtime=runtime, version=latest,
                                     why=why, current=cur.version if cur else _("no checked copy"))


def daily(runtime: str) -> str:
    cur = runtime_store.current(runtime)
    if cur is None:
        return ""
    only = [c.name for c in conformance.checks(runtime) if c.name in DAILY]
    try:
        doc = conformance.run(runtime, str(cur.exe), only or None)
    except RuntimeError as e:
        return _("{runtime}: the daily checks could not run: {error}", runtime=runtime, error=e)
    lines, ids = _problems(doc)
    alert.once(f"runtime-daily-{runtime}", lines, _("{runtime} {version} passes its daily checks again", runtime=runtime,
                                                     version=cur.version), ids=ids)
    return _("{runtime} {version}: daily checks {verdict}", runtime=runtime, version=cur.version,
             verdict=conformance.verdict(doc))


def raise_user_copy(runtime: str, mod) -> str:
    """Section 8: the user's own copy up to the promoted version, never down."""
    cur = runtime_store.current(runtime)
    find, put = getattr(mod, "user_copy", None), getattr(mod, "install_global", None)
    if cur is None or not callable(find) or not callable(put):
        return ""
    exe = find()
    if exe is None:   # no copy of the user's (no nvm, or none installed): not the platform's to make
        return ""
    have = mod.version_of(exe)
    if have and not newer(cur.version, have):
        return ""
    try:
        put(cur.version)
    except RuntimeError as e:
        alert.once(f"runtime-user-copy-{runtime}", [_("your own {runtime} could not be raised to {version}: {error}",
                                                      runtime=runtime, version=cur.version, error=e)],
                   _("your own {runtime} is up to date again", runtime=runtime), ids=[f"user-copy:{e}"])
        return _("{runtime}: your own copy not raised: {error}", runtime=runtime, error=e)
    alert.once(f"runtime-user-copy-{runtime}", [], _("your own {runtime} is up to date again", runtime=runtime))
    return _("{runtime}: your own copy {old} → {new}", runtime=runtime, old=have or _("none"), new=cur.version)


def main(argv: list[str]) -> int:
    if argv in (["-h"], ["--help"]):
        print(usage())
        return 0
    if not hubconf.known_args(argv, {"--first"}, {"--runtime"}):
        print(usage(), file=sys.stderr)
        return 2
    if hubconf.worktree_lookup():
        print(_("update-runtimes: {root} is a linked worktree of the hub; the store is changed only from the main "
                "checkout ({main})", root=hubconf.ROOT, main=hubconf.MAIN), file=sys.stderr)
        return 1
    first = "--first" in argv
    only = argv[argv.index("--runtime") + 1] if "--runtime" in argv else None
    if not first:   # update-claude's alerts, retired with it (11-14): resolved once if one was left open
        for key in ("claude-version", "claude-restricted", "claude-git-filter"):
            alert.once(key, [], _("{what}: now checked by brunna/update-runtimes", what=key))
    ok, used = True, in_use()
    if only and only not in used:
        print(_("{runtime}: not a runtime the platform uses with a distribution port", runtime=only), file=sys.stderr)
        return 1
    for runtime in used:
        if only and runtime != only:
            continue
        mod = runtime_ports._module(runtime)
        try:
            had, promoted, said = first_or_new(runtime, mod, first)
            ok = ok and had
            for line in (said, "" if first or promoted or not had else daily(runtime),
                         "" if first else raise_user_copy(runtime, mod)):
                if line:
                    print(line)
            if not first:
                gone = runtime_store.prune(runtime)
                if gone:
                    print(_("{runtime}: removed {versions}", runtime=runtime, versions=", ".join(gone)))
        except Exception as e:   # one runtime's breakage never stops the others; said, and alerted
            ok = False
            alert.once(f"runtime-job-{runtime}", [_("{runtime}: update-runtimes broke: {error}", runtime=runtime,
                                                    error=type(e).__name__)],
                       _("{runtime}: back to normal ({what})", runtime=runtime, what="job"), ids=[type(e).__name__])
            print(_("{runtime}: update-runtimes broke: {error}", runtime=runtime, error=repr(e)), file=sys.stderr)
        else:
            alert.once(f"runtime-job-{runtime}", [], _("{runtime}: back to normal ({what})", runtime=runtime, what="job"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
