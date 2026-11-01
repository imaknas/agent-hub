#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Removes Docker containers labelled org.testcontainers that are more than a day old (the testcontainers module,
docs/design/test-waiting.md B5). A suite whose test server was killed before its own cleanup leaves its database
container running, and the library's reaper does not always start (e.g. under Colima): 65 of them were found on 10-31,
39 running. The test runner stops a tier's whole process group, which removes the usual cause; this catches the rest.

Without docker, or with its daemon not running, there is nothing to clean: it says so and exits 0. It prints how many
it removed of how many it found.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)
MAX_AGE = datetime.timedelta(days=1)
LABEL = "org.testcontainers"


def created(value: str) -> datetime.datetime | None:
    """docker's CreatedAt, e.g. '2026-10-07 12:00:00 +0800 CST'."""
    try:
        return datetime.datetime.strptime(" ".join(value.split()[:3]), "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None


def main() -> int:
    docker = hubconf.tool("docker")
    try:
        r = subprocess.run([docker, "ps", "-a", "--filter", f"label={LABEL}", "--format", "{{json .}}"],
                           capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        print(_("docker is not installed; nothing to clean"))
        return 0
    if r.returncode != 0:
        print(_("docker is not answering ({error}); nothing to clean", error=r.stderr.strip()[-200:]))
        return 0
    now = datetime.datetime.now().astimezone()
    found, old = 0, []
    for line in r.stdout.splitlines():
        try:
            c = json.loads(line)
        except ValueError:
            continue
        found += 1
        at = created(c.get("CreatedAt", ""))
        if at and now - at > MAX_AGE:
            old.append(c["ID"])
    if old:
        rm = subprocess.run([docker, "rm", "-f", *old], capture_output=True, text=True, timeout=300)
        if rm.returncode != 0:
            print(_("docker rm failed: {error}", error=rm.stderr.strip()[-300:]), file=sys.stderr)
            return 1
    print(_("removed {removed} of {found} test {found:container|containers} (more than a day old)", removed=len(old),
            found=found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
