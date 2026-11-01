"""Whether a judgment point can be called on this machine at all, and if not, why -- asked before a caller does any
work (docs/design/tool-agnostic.md principle 3: a feature whose capability is missing is switched off explicitly, with
the reason, instead of failing on every item it would have handled).

  llm_ready.unsupported(point) -> str | None    None: it can be called (a call may still fail: no login, no key, a
                                                network error; those are real failures). Otherwise the reason it is off

What counts as "can be called" follows bin/llm.py's dispatch: a `shape: completion` point needs hub.json's llm section
(providers) or a runtime with the completion interface; any other point needs a runtime with agent runs (hub.json
`llm.points.<point>.runtime` > the declaration's `runtime` > runtime.default). It reads the declaration itself rather
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
        if completion.settings() or "completion" in runtime_ports.capabilities(name):
            return None
        return _("runtime {name} has no completion interface, and hub.json has no llm section (set one up with "
                 "agenthub llm setup)", name=name)
    try:
        runtime_ports.load(name, ports=("agent",))
    except runtime_ports.Unsupported as e:
        return str(e)
    return None
