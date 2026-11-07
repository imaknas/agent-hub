"""Running a sandboxed project's own services and command jobs isolated, whatever the OS: the neutral front that
bin/sandboxed-run, render-services and the scheduler's job list use. It picks the adapter for this machine; each
adapter turns the neutral policy (policy.py) into its OS's mechanism:
  macOS   seatbelt.py   Seatbelt profile (seatbelt.service_profile), sandbox-exec
  Linux   landlock.py   Landlock rules + a seccomp filter on the sandboxed-run process before it execs
Elsewhere, or when the adapter can't work on this machine (no sandbox-exec, a kernel without Landlock ABI 4), there is
no fallback: can_sandbox() says why, render-services doesn't render the service, sandboxed-run exits 78 (fail closed;
never unsandboxed). What each adapter enforces and can't: docs/design/capabilities.md.

The backend interface (what a module needs to be an adapter here):
  can_sandbox() → None | why not              whether it can work on this machine
  guarantees() → set[str]                     which of GUARANTEES it enforces on this machine
  exec_service(pol, dirs, port, argv)         replace this process with argv under the policy (raises OSError when it
                                              can't; nothing was run)
An adapter that doesn't guarantee every one of REQUIRED, or names a guarantee not in GUARANTEES, is refused like one
that can't work here. tests/test_sandboxed_services.py runs the same probe through every adapter on its own OS and
checks each guarantee it declares (Conformance): a new backend is added by passing that, not by review alone.

  sandboxed(doc) → bool                       whether this project's services and command jobs go through it
  wrap(project, cwd, argv, port) → argv       the command through bin/sandboxed-run
  as_project(project, cwd, argv) → argv       argv as the project: wrapped when it is sandboxed, as is otherwise (the
                                              platform's git in project repos goes only through this: project_git.py,
                                              with a timeout and an output cap)
  adapter() → (name, module) | None           this machine's adapter
  can_sandbox() → None | why not              the adapter's own reason, or a missing/unknown guarantee
  guarantees() → list[str]                    what this machine's adapter guarantees (empty when it can't sandbox)
  exec_service(pol, dirs, port, argv)         through the adapter, after can_sandbox()
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402

ADAPTERS = {"darwin": "seatbelt", "linux": "landlock"}

# every guarantee an adapter may declare; the names are the rows of capabilities.md's "What each OS enforces" table
GUARANTEES = {
    "own-writes-only": "writes only to its own folders, tool caches and the temp area (not the bus)",
    "isolated-reads": "other projects, platform state and credentials can't be read",
    "declared-port-only": "only the port the service declares can be bound",
    "localhost-inbound-only": "connections are accepted only through localhost",
    "keychain-blocked": "the Keychain and launchd can't be reached",
    "unix-sockets-blocked": "unix-domain sockets can't be created (no D-Bus, no systemd user manager)",
    "signals-blocked": "the owner's other processes can't be signalled",
    "runtime-files-locked": "what its sessions load settings, hooks and tools from can't be written (policy protect)",
}
# what a sandboxed service or job must never get past, whatever the backend
REQUIRED = ("own-writes-only", "isolated-reads", "declared-port-only")


def adapter():
    name = next((m for p, m in ADAPTERS.items() if sys.platform.startswith(p)), None)
    if not name:
        return None
    import importlib
    return name, importlib.import_module(name)


def can_sandbox() -> str | None:
    a = adapter()
    if not a:
        return f"there is no sandbox adapter for this system ({sys.platform})"
    if (why := a[1].can_sandbox()):
        return why
    has = set(a[1].guarantees())
    if (unknown := sorted(has - GUARANTEES.keys())):
        return f"the {a[0]} adapter declares unknown guarantees: {', '.join(unknown)}"
    if (missing := [g for g in REQUIRED if g not in has]):
        return f"the {a[0]} adapter doesn't guarantee {', '.join(missing)}"
    return None


def guarantees() -> list[str]:
    """What this machine's adapter guarantees, in GUARANTEES order; empty when it can't sandbox here."""
    if can_sandbox():
        return []
    has = set(adapter()[1].guarantees())
    return [g for g in GUARANTEES if g in has]


def sandboxed(doc: dict) -> bool:
    """The project's own services and command jobs run isolated (bin/sandboxed-run). hub.json's
    service_sandbox_exempt holds projects back temporarily (owner's decision, until they're fixed)."""
    return (doc.get("enforce") or {}).get("sandbox") is True \
        and doc.get("name") not in hubconf.get("service_sandbox_exempt", [])


def wrap(project: str, cwd, argv: list[str], port: int | None = None) -> list[str]:
    """argv → the same command through bin/sandboxed-run."""
    return [*hubconf.launch(hubconf.ROOT / "bin" / "sandboxed-run"), "--project", project, "--cwd", str(cwd),
            *(["--port", str(int(port))] if port else []), "--", *argv]


def as_project(project: str | None, cwd, argv: list[str]) -> list[str]:
    """argv with the project's authority: through bin/sandboxed-run when the project is sandboxed (which refuses, exit
    78, where this machine can't sandbox), unchanged otherwise (an unsandboxed project, an unregistered folder or the
    platform itself (None) has the user's authority already). A name the registry has and its index doesn't (a stale index) may be
    sandboxed: it goes through bin/sandboxed-run too, which refuses a project it doesn't know."""
    doc = hubconf.projects().get(project) if project else None
    if project and doc is None and project in hubconf.roster():
        return wrap(project, cwd, argv)
    return wrap(project, cwd, argv) if doc and sandboxed({**doc, "name": project}) else argv


def exec_service(pol: dict, dirs: list, port: int | None, argv: list[str]):
    if (why := can_sandbox()):
        raise OSError(0, why)
    adapter()[1].exec_service(pol, dirs, port, argv)
