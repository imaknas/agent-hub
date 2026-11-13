#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""`brunna runtimes`: the coding agents' versions, as the platform knows them (docs/design/runtime-versions.md section 6).

  runtimes.py                     per runtime in use: the binary platform runs start now and its version, each version's
                                  conformance result (bin/conformance.py), and the versions the user's sessions ran in
                                  the last 7 days with how many of their transcript lines the readers skipped (a version
                                  that skips more than 5% changed its format: the weekly statistics may undercount it)
  runtimes.py check <runtime> [--binary <exe>] [--only <check>]... [--report]
                                  run the conformance checks (bin/conformance.py run)

A runtime without the distribution port is unversioned and says so. Reads only; `check` writes the results store.
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
    return _("usage: brunna runtimes [check <runtime> [--binary <exe>] [--only <check>]... [--report]]")


def in_use() -> list[str]:
    try:
        return [n for n in runtime_ports.installed() if n != "none"]
    except Exception:
        return [runtime_ports.default_name()]


def show_runtime(name: str) -> list[str]:
    if "distribution" not in runtime_ports.capabilities(name):
        return [_("{runtime}: unversioned (no way to install or check a fixed version)", runtime=name)]
    mod = runtime_ports._module(name)
    exe = mod.binary() if callable(getattr(mod, "binary", None)) else None
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


def main(argv: list[str]) -> int:
    if argv in (["-h"], ["--help"]):
        print(usage())
        return 0
    if argv[:1] == ["check"]:
        return conformance.main(["run", *argv[1:]])
    if argv:
        print(usage(), file=sys.stderr)
        return 2
    for name in in_use():
        print("\n".join(show_runtime(name)))
    print("\n".join(show_sessions()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
