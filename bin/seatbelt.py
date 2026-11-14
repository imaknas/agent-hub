#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The macOS Seatbelt profiles the platform applies itself: neutral policy (policy.py) → SBPL text. Two kinds:
  profile(pol, cap)                      cap-runner's run jobs (+ capabilities/<name>.sb), described below
  service_profile(pol, dirs, port)       a sandboxed project's own services and command jobs (bin/sandboxed-run,
                                         through isolation.py; landlock.py is the Linux counterpart):
                                         its folders and caches writable (not the bus: its letters go through its
                                         outbox, docs/design/bus-sender.md), outbound network open, only the
                                         declared port listening

Not through any agent: a second layer can't be applied inside the runtime's sandbox (tested 10-25:
`sandbox_apply: Operation not permitted`), so this profile is applied only by cap-runner, outside the sandbox, with
`sandbox-exec -f`. Design: docs/design/capabilities.md.

The base is "allow everything, then block the dangerous parts" (a deny-list; trial period, so that Metal can run):
  write    only the project folder + the temp area (deliberately not policy.write: a run job shouldn't write the bus
           or the shared package caches directly)
  read     blocks all of workspace_root and services_root (except its own folder), other projects, policy.deny_read,
           credentials, platform state (.state whole, policy.platform_state: .logs, the data and evals dirs) and the
           bus, Keychain files; the folders above its own can be stat'ed (only
           those directories themselves: not listed, nothing in them read), or resolving the path to its own folder
           fails (git 2.56 resolves every component of `git -C <dir>` and dies on the workspace root)
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


SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def can_sandbox() -> str | None:
    """None when this system can apply these profiles; otherwise why not (the caller refuses instead of running
    unsandboxed)."""
    import os
    if sys.platform != "darwin" or not os.access(SANDBOX_EXEC, os.X_OK):
        return f"this system has no Seatbelt ({sys.platform})"
    return None


def guarantees() -> set[str]:
    """isolation.py's backend interface: what service_profile enforces (capabilities.md, "What each OS enforces")."""
    return {"own-writes-only", "isolated-reads", "declared-port-only", "localhost-inbound-only", "keychain-blocked",
            "signals-blocked", "runtime-files-locked"}


def exec_service(pol: dict, dirs: list, port: int | None, argv: list[str]):
    """isolation.py's adapter entry: replace this process with argv under service_profile."""
    import os
    os.execv(SANDBOX_EXEC, [SANDBOX_EXEC, "-p", service_profile(pol, dirs, port), *argv])


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


def spellings(p) -> list[str]:
    """A configured path and, when it differs, the path it resolves to. Seatbelt matches the real path of what is
    opened, so a rule naming only a link refuses the link while its target reads fine (a secrets folder reached through
    the instance folder <hub>/.hub-home points to); landlock.denied() resolves the same way."""
    import os
    s, r = str(p), os.path.realpath(p)
    return [s] if r == s else [s, r]


def glob_spellings(p) -> list[str]:
    """spellings() for a glob from policy.deny_read: the part before the first * resolved, the rest kept."""
    parts = pathlib.PurePath(p).parts
    i = next(n for n, x in enumerate(parts) if "*" in x)
    head, tail = pathlib.PurePath(*parts[:i]), parts[i:]
    return [str(pathlib.PurePath(h, *tail)) for h in spellings(head)]


def read_denies(pol: dict) -> list[str]:
    home = pathlib.Path.home()
    out = []
    # its own fixed list as well as the policy's platform_state: a profile built from any policy still denies .state
    for p in [hubconf.p("workspace_root"), hubconf.p("services_root"), *pol["other_projects"], *pol["credentials"],
              hubconf.STATE, hubconf.BUS_DIR, home / "Library" / "Keychains", *(pol.get("platform_state") or [])]:
        if p:
            out += [f"(subpath {q(s)})" for s in spellings(p)]
    for p in pol["deny_read"]:
        if "*" in str(p):
            out += [f"(regex {glob_regex(s)})" for s in glob_spellings(p)]
        else:
            out += [f"(subpath {q(s)})" for s in spellings(p)]
    out += [f"(literal {q(s)})" for p in pol["credential_files"] for s in spellings(p)]
    return out


def protect_denies(pol: dict) -> list[str]:
    """What its sessions load settings, hooks and tools from (policy protect): never written, after the allows (later
    rules win). A tree entry covers everything below it; the others only the folder entry itself (it can't be moved
    away, replaced or created). Anywhere, not only under the workspace: Seatbelt checks a rename at its two ends only,
    so a tree holding one of these names, built where the process may write (the temp area, its runtime folder) and
    moved into the workspace, would plant it at any depth there (docs/design/runtime-codex.md, Step 4 r2, review A-B2).
    The platform's own folders of these names are written by the platform, never from inside a project's sandbox."""
    import re
    if not pol.get("workspace") or not pol.get("protect"):
        return []
    pats = [f'#"/{re.escape(c["name"])}' + ('(/|$)"' if c["tree"] else '$"') for c in pol["protect"]]
    return ["; the files its sessions load settings and hooks from: never written, wherever they are made",
            "(deny file-write* " + " ".join(f"(regex {p})" for p in pats) + ")"]


def root_denies(pol: dict) -> list[str]:
    """The runtime's own folder in the workspace (policy protect_roots): nothing created, changed, removed or renamed
    under it but in the entries it names writable (its worktrees), nor the folder itself moved (V-M9, measured 11-09:
    docs/design/runtime-versions.md section 7). Beside protect_denies, which still covers the control names at any
    depth, those inside the writable entries included. A writable entry holds checkouts (worktrees), each with a tool
    folder of its own: that one is held the same way, one level down (not a worktree's own worktrees)."""
    import re
    ws = pol.get("workspace")
    if not ws or not pol.get("protect_roots"):
        return []
    base = pathlib.Path(ws).resolve()
    out = ["; the runtime's own folder in the project, and in each of its worktrees: nothing in it written but what it "
           "names writable"]
    for r in pol["protect_roots"]:
        top = base / r["name"]
        writable = r.get("writable") or []
        keep = " ".join(f"(require-not (subpath {q(top / w)}))" for w in writable)
        out.append(f"(deny file-write* (require-all (subpath {q(top)}){' ' + keep if keep else ''}))")
        name = re.escape(r["name"])
        for w in writable:
            inner = f"{re.escape(str(top / w))}/[^/]+/{name}"
            keep = " ".join(f'(require-not (regex #"^{inner}/{re.escape(x)}(/|$)"))' for x in writable)
            out.append(f'(deny file-write* (require-all (regex #"^{inner}(/|$)"){" " + keep if keep else ""}))')
    return out


def ancestors(paths) -> list[str]:
    """The folders above each path, as literals: what a process must be able to stat to resolve the path to a folder
    it may use (realpath, `git -C`), when one of them is in a denied tree (the workspace root, the platform state).
    Both the folders above the path as given and above what it resolves to: resolving walks the first, then the
    second (a denied tree may be named by either spelling, read_denies)."""
    import os
    above = {a for p in paths for s in {os.path.abspath(p), os.path.realpath(p)} for a in pathlib.PurePath(s).parents}
    return [f"(literal {q(a)})" for a in sorted(above, key=str)]


def _isolation(pol: dict, writable: list[pathlib.Path], writable_extra: list[pathlib.Path] = (),
               readable: list[pathlib.Path] = ()) -> list[str]:
    """What both kinds share: writes only to `writable` (+ `writable_extra`) and the temp area, reads blocked for other
    projects, platform state and credentials (own folders allowed again, `readable` too, the folders above them stat'ed
    only), Keychain services, other processes, launching outside. The paths come as configured: the allows name what they
    resolve to (Seatbelt matches the real path, so an allow naming a link never matches), the stat-only folders are
    above both spellings (ancestors)."""
    configured = [*writable, *writable_extra, *readable]
    own = [f"(subpath {q(pathlib.Path(w).resolve())})" for w in [*writable, *writable_extra]]
    readable = [pathlib.Path(r).resolve() for r in readable]
    return [
        "; writes: only its own folders and the temp area (the allow after this wins)",
        '(deny file-write* (subpath "/"))',
        "(allow file-write* " + " ".join([*own, *[f"(subpath {q(t)})" for t in TMP],
                                          *[f"(literal {q(d)})" for d in DEVICES]]) + ")",
        *protect_denies(pol),
        *root_denies(pol),
        "; reads: other projects, services, platform state, credentials, Keychain files; its own folders allowed again",
        "(deny file-read* " + " ".join(read_denies(pol)) + ")",
        "(allow file-read* " + " ".join(own + [f"(subpath {q(r)})" for r in readable]) + ")",
        "; the folders above them: stat only (resolving a path to its own folder walks through them)",
        "(allow file-read-metadata " + " ".join(ancestors(configured)) + ")",
        "; the Keychain system services",
        "(deny mach-lookup " + " ".join(f"(global-name {q(s)})" for s in KEYCHAIN_SERVICES) + ")",
        "; no asking other system services to launch programs outside the sandbox, no touching other processes",
        "(deny lsopen)",
        "(deny appleevent-send)",
        "(deny process-exec " + " ".join(f"(literal {q(s)})" for x in NO_EXEC for s in spellings(x)) + ")",
        # tested 10-25: (deny signal (target others)) doesn't block it; block everything first, then allow the same sandbox
        "(deny process-info*)",
        "(allow process-info* (target same-sandbox))",
        "(deny signal)",
        "(allow signal (target same-sandbox))",
    ]


def profile(pol: dict, cap: str) -> str:
    ws = pathlib.Path(pol["workspace"]).resolve()
    lines = [
        "(version 1)",
        f"; agent-hub platform-run: capability {cap} (generated by bin/seatbelt.py on every run; don't edit)",
        "(allow default)",
        "; network: none at all (including localhost and unix sockets)",
        "(deny network-outbound (remote ip))",
        "(deny network-outbound (remote unix-socket))",
        "(deny network-inbound (local ip))",
        "(deny network-bind)",
        *_isolation(pol, [ws]),
        f"; ---------- capability {cap} (capabilities/{cap}.sb) ----------",
        fragment(cap).rstrip(),
        "",
    ]
    return "\n".join(lines)


def service_profile(pol: dict, dirs: list[pathlib.Path], port: int | None = None) -> str:
    """A sandboxed project's own service or command job (bin/sandboxed-run): like a session of that project, not like
    cap-run. dirs = its runtime and workspace (read and write); policy.write may be written too. The bus directory
    is neither readable nor writable: agenthub send writes into the project's outbox (docs/design/bus-sender.md). Network: outbound is open: Seatbelt can't filter
    by domain, so the project's network list is not enforced at this layer (what this layer guarantees is the files,
    Keychain and processes); listening only on the port the service declares in expose.local_port, and only
    connections through localhost are accepted (not from the LAN or the tailnet directly: tailscale serve)."""
    lines = [
        "(version 1)",
        "; agent-hub: a sandboxed project's own service or job (generated by bin/seatbelt.py on every run; don't edit)",
        "(allow default)",
        "; network: outbound open (Seatbelt can't filter by domain); listening only on the declared port",
        "(deny network-bind)",
        "(deny network-inbound (local ip))",
    ]
    if port:
        # tested 10-31: bind is matched by port (0.0.0.0 gets through too); inbound per port is unreliable (listen()
        # refused on some ports, allowed on others), so inbound is localhost on any port: only the declared port can be
        # bound at all, and connections arriving on the LAN or tailnet address are not accepted (tailscale serve
        # connects through localhost)
        lines += [f'(allow network-bind (local ip "*:{int(port)}"))',
                  '(allow network-inbound (local ip "localhost:*"))']
    # policy.write is written too; the registry index the platform CLI reads (agenthub send / complete inside a
    # service): read-only
    lines += _isolation(pol, [pathlib.Path(d) for d in dirs], [hubconf.path(w) for w in pol["write"]],
                        [pathlib.Path(r) for r in pol.get("read") or []])
    return "\n".join(lines + [""])


if __name__ == "__main__":  # seatbelt.py <project> <capability>: print the profile it would use (for debugging)
    import policy
    print(profile(policy.for_project(sys.argv[1]), sys.argv[2]))
