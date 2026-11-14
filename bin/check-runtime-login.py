#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Whether a coding agent's login still works for the platform's own runs on it (a module's hourly job, e.g. the codex
module's codex-login). Neutral: what to look at is the runtime adapter's login_check() → [(id, problem)], with no model
call; this script only decides whether to ask and alerts.

  check-runtime-login.py --runtime <name>

A runtime the platform runs no work on (runtime_ports.platform_work: not the default, no point pinned to it, no provider
on it) isn't checked: nothing is said and nothing alerts, so a runtime only installed for sessions costs nothing here
(docs/design/runtime-codex.md, Step 1 r2, D11). One alert per runtime, each problem by the adapter's stable id. Exit 0
when the login works or isn't checked, 1 when it doesn't, 2 on a usage error.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402
import runtime_ports  # noqa: E402

_ = i18n.domain(__file__)


def usage() -> str:
    return _("usage: check-runtime-login.py --runtime <name>")


def main(argv: list[str]) -> int:
    if argv in (["-h"], ["--help"]):
        print(usage())
        return 0
    if not hubconf.known_args(argv, set(), {"--runtime"}) or "--runtime" not in argv:
        print(usage(), file=sys.stderr)
        return 2
    name = argv[argv.index("--runtime") + 1]
    if name not in runtime_ports.RUNTIMES:
        print(usage(), file=sys.stderr)
        return 2
    if name not in runtime_ports.platform_work():
        print(_("{runtime}: the platform runs no work on it; its login isn't checked", runtime=name))
        return 0
    check = getattr(runtime_ports._module(name), "login_check", None)
    if not callable(check):
        print(_("{runtime}: it has no login check", runtime=name))
        return 0
    import alert
    try:
        problems = [(str(i), str(p)) for i, p in check()]
    except Exception as e:   # a check that breaks is said by its kind, never a traceback
        problems = [("broke", _("the login check broke: {error}", error=type(e).__name__))]
    alert.once(f"runtime-login-{name}", [p for _i, p in problems], _("{runtime}: its login works again", runtime=name),
               ids=[i for i, _p in problems])
    for _i, p in problems:
        print(_("{runtime}: {problem}", runtime=name, problem=p))
    if not problems:
        print(_("{runtime}: its login works", runtime=name))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
