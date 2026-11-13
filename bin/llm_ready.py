"""Whether a judgment point can be called on this machine at all, and if not, why -- asked before a caller does any
work (docs/design/tool-agnostic.md principle 3: a feature whose capability is missing is switched off explicitly, with
the reason, instead of failing on every item it would have handled).

  llm_ready.unsupported(point) -> str | None    None: it can be called (a call may still fail: no login, no key, a
                                                network error; those are real failures). Otherwise the reason it is off

What counts as "can be called" follows bin/llm.py's dispatch: a `shape: completion` point needs {settings}'s llm section
(providers) or a runtime with the completion interface; any other point needs a runtime with agent runs ({settings}
`llm.points.<point>.runtime` > the declaration's `runtime` > runtime.default). Either way, a runtime that runs a store's
checked copy of its tool needs one (runtime_ports.no_checked_copy). It reads the declaration itself rather
than importing llm.py (llm.py is a script; tests stand it in).
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)


def declaration(point: str) -> dict:
    """The point's entry in contracts/llm-points.yaml; {} when it cannot be read (it then counts as agent-shaped)."""
    try:
        import yaml
        return (yaml.safe_load((hubconf.ROOT / "contracts" / "llm-points.yaml").read_text())["points"] or {}).get(point) or {}
    except (OSError, KeyError, TypeError, ValueError, ImportError):
        return {}


def unsupported(point: str) -> str | None:
    import completion
    import runtime_ports
    spec = declaration(point)
    pin = ((hubconf.get("llm") or {}).get("points") or {}).get(point) or {}
    name = pin.get("runtime") or spec.get("runtime") or runtime_ports.default_name()
    if spec.get("shape") == "completion":
        if completion.settings():
            try:
                impl = completion.resolve(point, spec).get("impl")
            except Exception:   # nowhere to go is the call's own failure, said when it runs
                impl = None
            # a provider whose impl is a runtime runs that runtime's checked copy
            return runtime_ports.no_checked_copy(impl) if impl in runtime_ports.RUNTIMES else None
        if "completion" in runtime_ports.capabilities(name):
            return runtime_ports.no_checked_copy(name)
        return _("runtime {name} has no completion interface, and {settings} has no llm section (set one up with "
                 "brunna llm setup)", name=name)
    try:
        runtime_ports.load(name, ports=("agent",))
    except runtime_ports.Unsupported as e:
        return str(e)
    if runtime_ports.throwaway_only(name) and point not in runtime_ports.THROWAWAY_POINTS:
        # it refuses every other point (runtime_command, runtime_acp): off, so a caller with a fallback takes it (the librarian's
        # completion parts) instead of failing on every item
        return _("runtime {name} runs only {points} in the platform's throwaway worktree", name=name,
                 points=", ".join(runtime_ports.THROWAWAY_POINTS))
    return None
