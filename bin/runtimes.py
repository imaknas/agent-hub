#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""`brunna runtimes`: the coding agents' versions, as the platform knows them (docs/design/runtime-versions.md section 6).

  runtimes.py                     per runtime in use: the binary platform runs start now and its version, each version's
                                  conformance result (bin/conformance.py), each version the user's sessions ran in the
                                  last 7 days as their hooks read it, with its session-side checks (bin/session_versions.py:
                                  passed, pending, unreadable, not installable, failed and which check; Desktop's builds),
                                  and the versions their transcripts name with how many lines the readers skipped (a
                                  version that skips more than 5% changed its format: the weekly statistics may undercount it)
  runtimes.py check <runtime> [--binary <exe>] [--pair <exe>] [--only <check>]... [--report]
                                  run the conformance checks (bin/conformance.py run)
  runtimes.py install <runtime>   a checked copy in the store: the first install, or a newer version checked and
                                  promoted (bin/update-runtimes.py for this runtime)
  runtimes.py rollback <runtime>  the previous copy back, after its safety checks pass again
  runtimes.py recheck <runtime> <version>
                                  every session-side check of a version the user's sessions ran, run again by the next
                                  brunna/session-versions (a recorded fail is otherwise never run again); its result,
                                  and the gate it sets, stay until then

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
    return _("usage: brunna runtimes [check <runtime> [--binary <exe>] [--pair <exe>] [--only <check>]... [--report] | "
             "install <runtime> | rollback <runtime> | recheck <runtime> <version>]")


def in_use() -> list[str]:
    try:
        return [n for n in runtime_ports.installed() if n != "none"]
    except Exception:
        return [runtime_ports.default_name()]


def show_runtime(name: str) -> list[str]:
    if "distribution" not in runtime_ports.capabilities(name):
        return [_("{runtime}: unversioned (no way to install or check a fixed version)", runtime=name)]
    if name not in runtime_ports.platform_work():   # only installed for sessions: no copy of the platform's (D11)
        return [_("{runtime}: serves your sessions only; the platform keeps no copy of it and starts none of its runs",
                  runtime=name)]
    mod = runtime_ports._module(name)
    try:
        exe = mod.binary() if callable(getattr(mod, "binary", None)) else None
    except runtime_ports.Unsupported as e:   # no checked copy: what platform runs start is nothing, said
        return [_("{runtime}: platform runs start nothing: {reason}", runtime=name, reason=e)]
    found = shutil.which(exe) if exe else None
    now = _version(name, mod, found) if found else None
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


def _version(name: str, mod, exe: str) -> str | None:
    """The version of the copy platform runs start: what the store recorded for it at promotion, read as a file; else
    asked of the binary in a throwaway home, never the user's (a tool's --version writes into its home)."""
    import os
    import tempfile
    import hubconf
    import runtime_store
    cur = runtime_store.current(name)
    if cur is not None and os.path.realpath(exe) == os.path.realpath(cur.exe):
        return cur.version
    with tempfile.TemporaryDirectory(dir=hubconf.private_tmp()) as home:
        # HOME alone: each tool's own folder defaults under it, so nothing of the user's is reached
        return mod.version_of(exe, env={"HOME": home, "PATH": hubconf.LAUNCHD_PATH, "LANG": os.environ.get("LANG", "C")})


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


def recheck(runtime: str, version: str) -> int:
    import hubconf
    import session_versions
    if hubconf.worktree_lookup():
        print(_("runtimes: {root} is a linked worktree of the hub; the results are changed only from the main checkout "
                "({main})", root=hubconf.ROOT, main=hubconf.MAIN), file=sys.stderr)
        return 1
    if runtime not in runtime_ports.RUNTIMES or not session_versions.request_recheck(runtime, version):
        print(_("{runtime} {version}: no session-side result to check again (a version seen in your sessions is checked "
                "by brunna/session-versions on its own)", runtime=runtime, version=version), file=sys.stderr)
        return 1
    print(_("{runtime} {version}: its session-side checks run again at the next brunna/session-versions (within 15 "
            "minutes); until then its result stays", runtime=runtime, version=version))
    return 0


def main(argv: list[str]) -> int:
    if argv in (["-h"], ["--help"]):
        print(usage())
        return 0
    if argv[:1] == ["check"]:
        return conformance.main(["run", *argv[1:]])
    if argv[:1] == ["recheck"] and len(argv) == 3:
        return recheck(argv[1], argv[2])
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
