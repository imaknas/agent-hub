"""The platform's names, old and new, in one table (docs/design/rename-brunna.md): agent-hub is becoming brunna.

Every name the platform writes that carries the product's name is a pair here, and code uses it in one of three ways:

  current(cls)        writers: the spelling this release writes (old until the switch release, new after)
  all(cls)            readers: every spelling, current first, so a release reads what the one before or after wrote
  resolve(base, cls)  paths: the spelling that exists under base; neither → the new one (a fresh install is all new)

Environment variables: env(X) reads the old spelling when it is set, else the new one: an old spelling in an
environment is one something outside the platform set (env_set removes it), so it is an explicit override. The
platform sets a variable for a child with env_set (the current spelling written, the others removed) and clears one
with env_unset. The few variables project code reads by their documented names (DOCUMENTED) are written in both.

Standard library only: hubconf, the hooks and the scheduler import it. The places that can't import it (install.sh,
the hubpaths.sh fallback, the scheduler watchdog) carry inline copies of the pairs they use, and
tests/test_name_pairs.py compares them with this table.
"""
from __future__ import annotations

import os
import pathlib

# the switch release sets this: from then on writers use the new spelling
SWITCHED = False
# Principal Handoff party ids switch separately, once the owner's assistant has agreed (the design's "gated" class)
PARTY_SWITCHED = False

PAIRS = {
    "product": ("agent-hub", "brunna"),         # the name in text
    "cli": ("agenthub", "brunna"),               # the command
    "project": ("agent-hub", "brunna"),          # the platform's own project name, bus address, job prefix
    "party": ("agent-hub", "brunna"),            # Principal Handoff party ids <name>:registrar, <name>:owner-side
    "unit": ("local.agent-hub", "local.brunna"),  # launchd / systemd label prefix
    "marker": ("agent-hub", "brunna"),           # <!-- X:begin/end -->, # X:gate
    "repo_dir": ("agent-hub", "brunna"),         # .git/<X>/, refs/<X>/
    "share_dir": ("agent-hub", "brunna"),        # ~/.local/share/<X>/python
    "config_file": ("hub.json", "brunna.json"),  # the instance's settings
}
# a mkdir lock exists only while held, so "whichever exists" can't decide it: one name in every release
REPO_LOCK = "agent-hub-op.lock"

ENV_NEW = "BRUNNA_"
# the old prefix of each variable that had AGENTHUB_; every other one had AGENT_HUB_
_AGENTHUB = {"PROJECT", "TIER_CACHE", "CONNECT_ROOTS", "TEST_CALLER"}
# read by project code by their documented names (event-jobs.md, registry run: lines): written in both spellings
DOCUMENTED = {"EVENTS", "JOB", "JOB_CHAIN", "JOB_DEPTH", "TIER_CACHE"}


def _switched(cls: str) -> bool:
    return PARTY_SWITCHED if cls == "party" else SWITCHED


def old(cls: str) -> str:
    return PAIRS[cls][0]


def new(cls: str) -> str:
    return PAIRS[cls][1]


def current(cls: str) -> str:
    return new(cls) if _switched(cls) else old(cls)


def other(cls: str) -> str:
    return old(cls) if _switched(cls) else new(cls)


def all(cls: str) -> tuple[str, str]:  # noqa: A001 (the table's word for "every spelling")
    return current(cls), other(cls)


def resolve(base, cls: str, fmt: str = "{}") -> pathlib.Path:
    """base / fmt.format(spelling) for the spelling that exists; neither → the new one. Both existing is the caller's
    to decide (resolve_both): a file pair that differs is refused there, never guessed here."""
    base = pathlib.Path(base)
    for name in (current(cls), other(cls)):
        p = base / fmt.format(name)
        if os.path.lexists(p):
            return p
    return base / fmt.format(new(cls))


def resolve_both(base, cls: str, fmt: str = "{}") -> list[pathlib.Path]:
    """Every spelling that exists under base (none, one or both)."""
    base = pathlib.Path(base)
    return [base / fmt.format(n) for n in (current(cls), other(cls)) if os.path.lexists(base / fmt.format(n))]


# ---------- environment variables ----------

def env_old(x: str) -> str:
    return ("AGENTHUB_" if x in _AGENTHUB else "AGENT_HUB_") + x


def env_names(x: str) -> tuple[str, str]:
    """(old, new) spelling of variable X (without its prefix)."""
    return env_old(x), ENV_NEW + x


def env(x: str, default: str | None = None, environ=None) -> str | None:
    """The old spelling when set (an explicit override from outside the platform), else the new one."""
    environ = os.environ if environ is None else environ
    o, n = env_names(x)
    if o in environ:
        return environ[o]
    return environ.get(n, default)


def env_set(environ, x: str, value: str):
    """Set X for a child: the current spelling written, the other removed (both written for DOCUMENTED ones). Returns
    environ, so `env=names.env_set(dict(os.environ), "STATE", d)` reads as one step."""
    o, n = env_names(x)
    if x in DOCUMENTED:
        environ[o] = environ[n] = value
        return environ
    keep, drop = (n, o) if SWITCHED else (o, n)
    environ[keep] = value
    environ.pop(drop, None)
    return environ


def env_unset(environ, x: str):
    for name in env_names(x):
        environ.pop(name, None)
    return environ


def env_strip(environ) -> dict:
    """A copy without any of the platform's variables, in any spelling (tests, isolated children)."""
    return {k: v for k, v in environ.items() if not k.startswith(("AGENT_HUB_", "AGENTHUB_", ENV_NEW))}
