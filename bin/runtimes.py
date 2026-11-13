#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""`brunna runtimes`: the coding agents' versions, as the platform knows them (docs/design/runtime-versions.md section 6).

  runtimes.py                     per runtime in use: the binary platform runs start now and its version, each version's
                                  conformance result (bin/conformance.py), each version the user's sessions ran in the
                                  last 7 days as their hooks read it, with its session-side checks (bin/session_versions.py:
                                  passed, pending, unreadable, not installable, failed and which check; Desktop's builds),
                                  and the versions their transcripts name with how many lines the readers skipped (a
                                  version that skips more than 5% changed its format: the weekly statistics may undercount it)
  runtimes.py check <runtime> [--binary <exe>] [--only <check>]... [--report]
                                  run the conformance checks (bin/conformance.py run)
  runtimes.py install <runtime>   a checked copy in the store: the first install, or a newer version checked and
                                  promoted (bin/update-runtimes.py for this runtime)
  runtimes.py rollback <runtime>  the previous copy back, after its safety checks pass again

A runtime without the distribution port is unversioned and says so. Reads only; `check` writes the results store,
`install` and `rollback` the store (only from the main checkout).
"""
from __future__ import annotations

import datetime
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import conformance  # noqa: E402
import i18n  # noqa: E402
import runtime_ports  # noqa: E402
import sessions  # noqa: E402

_ = i18n.domain(__file__)
DAYS = 7


def usage() -> str:
    return _("usage: brunna runtimes [check <runtime> [--binary <exe>] [--only <check>]... [--report] | install <runtime> | "
             "rollback <runtime>]")


def in_use() -> list[str]:
    try:
        return [n for n in runtime_ports.installed() if n != "none"]
    except Exception:
        return [runtime_ports.default_name()]


def show_runtime(name: str) -> list[str]:
    if "distribution" not in runtime_ports.capabilities(name):
        return [_("{runtime}: unversioned (no way to install or check a fixed version)", runtime=name)]
    mod = runtime_ports._module(name)
    try:
        exe = mod.binary() if callable(getattr(mod, "binary", None)) else None
    except runtime_ports.Unsupported as e:   # no checked copy: what platform runs start is nothing, said
        return [_("{runtime}: platform runs start nothing: {reason}", runtime=name, reason=e)]
    found = shutil.which(exe) if exe else None
    now = mod.version_of(found) if found else None
    lines = [_("{runtime}: platform runs start {binary} ({version})", runtime=name, binary=found or exe or "-",
               version=now or _("version not readable"))]
    done = conformance.recorded(name)
    if not done:
        lines.append(_("  no version checked yet (brunna runtimes check {runtime})", runtime=name))
    for doc in done[:5]:
        counts: dict[str, int] = {}
        for c in doc.get("checks", {}).values():
            counts[c.get("state", "?")] = counts.get(c.get("state", "?"), 0) + 1
        lines.append(_("  {version}: {verdict}, checked {at} ({counts})", version=doc.get("version"),
                       verdict=conformance.verdict(doc), at=doc.get("at"),
                       counts=", ".join(f"{k} {v}" for k, v in sorted(counts.items()))))
        if (n := conformance.unrun_safety(doc)):
            lines.append(_("    {n} safety {n:check|checks} not run yet", n=n))
    return lines


def show_session_versions(name: str) -> list[str]:
    """The versions the user's sessions ran, as their hooks read them, and how each one's session-side checks went."""
    import session_versions
    rows = session_versions.summary(name)
    out = [_("  versions your sessions ran in the last {days} days:", days=DAYS) if rows else
           _("  no version of your sessions seen in the last {days} days", days=DAYS)]
    out += [f"    {session_versions.describe(name, r)}" for r in rows]
    out += [f"    {line}" for line in session_versions.unusable_rows(name)]
    return out


def show_sessions() -> list[str]:
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=DAYS)
    try:
        rows = sessions.skipped(since)
    except Exception as e:
        return [_("the sessions' versions couldn't be read: {error}", error=type(e).__name__)]
    if not rows:
        return [_("no session files written in the last {days} days", days=DAYS)]
    out = [_("versions the sessions of the last {days} days ran:", days=DAYS)]
    for r in rows:
        line = _("  {source} {version}: {files} {files:file|files}, {skipped} of {lines} lines skipped", source=r["source"],
                 version=r["version"] or _("(version not written)"), files=r["files"], skipped=r["skipped"],
                 lines=r["lines"])
        out.append(line + (_(" ⚠️ more than 5%: the weekly statistics may undercount this version") if r["over"] else ""))
    return out


def rollback(runtime: str) -> int:
    """previous becomes current again, only after its safety checks pass again and it is at least MIN_VERSION."""
    import runtime_store
    prev = runtime_store.previous(runtime)
    if prev is None:
        print(_("{runtime}: no previous version to go back to", runtime=runtime), file=sys.stderr)
        return 1
    safety = [c.name for c in conformance.checks(runtime) if c.kind == "safety" and c.run is not None]
    try:
        doc = conformance.run(runtime, str(prev.exe), safety or None)
    except RuntimeError as e:
        print(f"runtimes: {e}", file=sys.stderr)
        return 1
    print(conformance.show(doc))
    floor = getattr(runtime_ports._module(runtime), "MIN_VERSION", None)
    if conformance.verdict(doc) != "pass" or (floor and tuple(map(int, floor.split("."))) > tuple(map(int, prev.version.split(".")))):
        print(_("{runtime}: not going back to {version}: its safety checks didn't pass, or it is below {floor}",
                runtime=runtime, version=prev.version, floor=floor or "-"), file=sys.stderr)
        return 1
    now = runtime_store.rollback(runtime)
    print(_("{runtime}: platform runs use {version} again", runtime=runtime, version=now.version))
    return 0


def main(argv: list[str]) -> int:
    if argv in (["-h"], ["--help"]):
        print(usage())
        return 0
    if argv[:1] == ["check"]:
        return conformance.main(["run", *argv[1:]])
    if argv[:1] in (["install"], ["rollback"]) and len(argv) == 2:
        import hubconf
        import subprocess
        if hubconf.worktree_lookup():
            print(_("runtimes: {root} is a linked worktree of the hub; the store is changed only from the main checkout "
                    "({main})", root=hubconf.ROOT, main=hubconf.MAIN), file=sys.stderr)
            return 1
        if argv[1] not in runtime_ports.RUNTIMES or "distribution" not in runtime_ports.capabilities(argv[1]):
            print(_("{runtime}: unversioned (no way to install or check a fixed version)", runtime=argv[1]), file=sys.stderr)
            return 1
        if argv[0] == "rollback":
            return rollback(argv[1])
        return subprocess.run([*hubconf.launch(pathlib.Path(__file__).resolve().parent / "update-runtimes.py"),
                               "--runtime", argv[1]]).returncode
    if argv:
        print(usage(), file=sys.stderr)
        return 2
    for name in in_use():
        print("\n".join(show_runtime(name)))
        if "distribution" in runtime_ports.capabilities(name):
            print("\n".join(show_session_versions(name)))
    print("\n".join(show_sessions()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
