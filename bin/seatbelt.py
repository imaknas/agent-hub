#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The macOS Seatbelt profile for the platform's run service (cap-runner): neutral policy (policy.py) + capability
(capabilities/<name>.sb) → SBPL text.

Not through any agent: a second layer can't be applied inside the runtime's sandbox (tested 10-25:
`sandbox_apply: Operation not permitted`), so this profile is applied only by cap-runner, outside the sandbox, with
`sandbox-exec -f`. Design: docs/design/capabilities.md.

The base is "allow everything, then block the dangerous parts" (a deny-list; trial period, so that Metal can run):
  write    only the project folder + the temp area (deliberately not policy.write: a run job shouldn't write the bus
           or the shared package caches directly)
  read     blocks all of workspace_root and services_root (except its own folder), other projects, policy.deny_read,
           credentials, platform state and the bus, Keychain files
  network  fully off (127.0.0.1 and unix sockets included); Seatbelt can't filter by domain
  other    blocks the Keychain system services (the hole the runtime's sandbox can't close), opening apps, Apple Events,
           launchctl/crontab/at, inspecting or signalling other processes
Capability fragments follow the base (later rules win); the gpu fragment is empty: here a capability's role is
authorization.

  profile(pol, cap) → str        paths are written straight into the profile (computed fresh on every run), no -D
                                 parameters
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402

TMP = ["/private/tmp", "/private/var/folders"]
DEVICES = ["/dev/null", "/dev/zero", "/dev/random", "/dev/urandom", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/dtracehelper"]
KEYCHAIN_SERVICES = ["com.apple.SecurityServer", "com.apple.securityd.xpc", "com.apple.security.agent",
                     "com.apple.security.keychain-circle-notification", "com.apple.secd", "com.apple.trustd.agent"]
NO_EXEC = ["/usr/bin/open", "/usr/bin/osascript", "/bin/launchctl", "/usr/bin/crontab", "/usr/bin/at", "/usr/bin/batch",
           "/usr/bin/security"]


def q(p) -> str:
    """An SBPL string literal."""
    return '"' + str(p).replace("\\", "\\\\").replace('"', '\\"') + '"'


def glob_regex(p) -> str:
    """A glob from policy.deny_read (only * is used) → an SBPL regex, everything below included."""
    import re
    parts = [re.escape(x) for x in str(p).split("*")]
    return '#"^' + "[^/]+".join(parts).replace('"', '\\"') + '(/|$)"'


def fragment(cap: str) -> str:
    return (hubconf.ROOT / "capabilities" / f"{cap}.sb").read_text()


def read_denies(pol: dict) -> list[str]:
    home = pathlib.Path.home()
    out = []
    for p in [hubconf.p("workspace_root"), hubconf.p("services_root"), *pol["other_projects"], *pol["credentials"],
              hubconf.STATE, hubconf.BUS_DIR, home / "Library" / "Keychains"]:
        if p:
            out.append(f"(subpath {q(p)})")
    for p in pol["deny_read"]:
        out.append(f"(regex {glob_regex(p)})" if "*" in str(p) else f"(subpath {q(p)})")
    out += [f"(literal {q(p)})" for p in pol["credential_files"]]
    return out


def profile(pol: dict, cap: str) -> str:
    ws = pathlib.Path(pol["workspace"]).resolve()
    lines = [
        "(version 1)",
        f"; agent-hub 平台代跑:能力 {cap}(bin/seatbelt.py 產生,每次執行現算;不要手改)",
        "(allow default)",
        "; 網路:完全不通(含本機跟 unix socket)",
        "(deny network-outbound (remote ip))",
        "(deny network-outbound (remote unix-socket))",
        "(deny network-inbound (local ip))",
        "(deny network-bind)",
        "; 寫入:只准專案資料夾跟暫存區(後面的 allow 優先)",
        '(deny file-write* (subpath "/"))',
        "(allow file-write* " + " ".join([f"(subpath {q(ws)})", *[f"(subpath {q(t)})" for t in TMP],
                                          *[f"(literal {q(d)})" for d in DEVICES]]) + ")",
        "; 讀取:別的專案、服務、平台狀態、憑證、Keychain 檔案;自己的資料夾放行",
        "(deny file-read* " + " ".join(read_denies(pol)) + ")",
        f"(allow file-read* (subpath {q(ws)}))",
        "; Keychain 的系統服務",
        "(deny mach-lookup " + " ".join(f"(global-name {q(s)})" for s in KEYCHAIN_SERVICES) + ")",
        "; 不准請別的系統服務在 sandbox 外替它啟動程式、不准碰別的程序",
        "(deny lsopen)",
        "(deny appleevent-send)",
        "(deny process-exec " + " ".join(f"(literal {q(x)})" for x in NO_EXEC) + ")",
        # tested 10-25: (deny signal (target others)) doesn't block it; block everything first, then allow the same sandbox
        "(deny process-info*)",
        "(allow process-info* (target same-sandbox))",
        "(deny signal)",
        "(allow signal (target same-sandbox))",
        f"; ---------- 能力 {cap}(capabilities/{cap}.sb)----------",
        fragment(cap).rstrip(),
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":  # seatbelt.py <project> <capability>: print the profile it would use (for debugging)
    import policy
    print(profile(policy.for_project(sys.argv[1]), sys.argv[2]))
