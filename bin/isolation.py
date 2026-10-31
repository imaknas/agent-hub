"""Running a sandboxed project's own services and command jobs isolated, whatever the OS: the neutral front that
bin/sandboxed-run, render-services and the scheduler's job list use. It picks the adapter for this machine; each
adapter turns the neutral policy (policy.py) into its OS's mechanism:
  macOS   seatbelt.py   Seatbelt profile (seatbelt.service_profile), sandbox-exec
  Linux   landlock.py   Landlock rules + a seccomp filter on the sandboxed-run process before it execs
Elsewhere, or when the adapter can't work on this machine (no sandbox-exec, a kernel without Landlock ABI 4), there is
no fallback: can_sandbox() says why, render-services doesn't render the service, sandboxed-run exits 78 (fail closed;
never unsandboxed). What each adapter enforces and can't: docs/design/capabilities.md.

  sandboxed(doc) → bool                       whether this project's services and command jobs go through it
  wrap(project, cwd, argv, port) → argv       the command through bin/sandboxed-run
  adapter() → (name, module) | None           this machine's adapter
  can_sandbox() → None | why not
  exec_service(pol, dirs, port, argv)         replace this process with argv under the policy (raises OSError when it
                                              can't; nothing was run)
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402

ADAPTERS = {"darwin": "seatbelt", "linux": "landlock"}


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
    return a[1].can_sandbox()


def sandboxed(doc: dict) -> bool:
    """The project's own services and command jobs run isolated (bin/sandboxed-run). hub.json's
    service_sandbox_exempt holds projects back temporarily (owner's decision, until they're fixed)."""
    return (doc.get("enforce") or {}).get("sandbox") is True \
        and doc.get("name") not in hubconf.get("service_sandbox_exempt", [])


def wrap(project: str, cwd, argv: list[str], port: int | None = None) -> list[str]:
    """argv → the same command through bin/sandboxed-run."""
    return [*hubconf.launch(hubconf.ROOT / "bin" / "sandboxed-run"), "--project", project, "--cwd", str(cwd),
            *(["--port", str(int(port))] if port else []), "--", *argv]


def exec_service(pol: dict, dirs: list, port: int | None, argv: list[str]):
    a = adapter()
    if not a or a[1].can_sandbox():
        raise OSError(0, can_sandbox())
    a[1].exec_service(pol, dirs, port, argv)
