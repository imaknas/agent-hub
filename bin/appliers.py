"""Config appliers (docs/design/new-user.md 3.7): the programs that turn config (project registry, hub.json…) into
actual state on the machine.

An applier = a module providing:
  plan(index=None) -> list[Change]    what would change; **no side effects allowed** (doesn't write the files it
                                      manages, doesn't touch launchd)
  apply(index=None) -> list[Change]   makes the change and returns what changed: must equal the plan just made; a plan
                                      after it is empty (idempotent)
  EXTERNAL: bool                      whether apply reaches outside (push, sending mail). plan may never reach outside,
                                      whatever EXTERNAL says
index is the result of registry.refresh(); if not given, the applier reads it itself (tests pass a fake one).

Written the same way as runtime_ports: a registration table APPLIERS, load() checking mechanically that the functions
exist, and tests/test_appliers.py running the same contract on every registered applier (plan has no side effects,
apply == plan, a plan after apply is empty). An applier that fails the contract tests doesn't get in.
Change uses neutral words: target is "what of whom" (service:<label>, agent-settings:<project>, job:<name>), never a
runtime's format.

The 15-minute jobs (render-services, render-settings) keep running as a safety net; `bin/hub config apply` is "apply
now".
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import NamedTuple

BIN = pathlib.Path(__file__).resolve().parent
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))
import i18n  # noqa: E402

_ = i18n.domain(__file__)


class Change(NamedTuple):
    op: str        # add | update | remove
    target: str    # service:<label> | agent-settings:<project> | hook:<project>/<hook> | job:<name>
    detail: str = ""

    def line(self) -> str:
        mark = {"add": "+", "update": "~", "remove": "-"}.get(self.op, "?")
        return f"{mark} {self.target}" + (f"  {self.detail}" if self.detail else "")


OPS = ("add", "update", "remove")
FUNCTIONS = ("plan", "apply")
# name → module file (under bin/). The order is the apply order: schedule first (just records), then services, then the
# projects' agent settings
APPLIERS = {
    "scheduler": "applier_scheduler.py",
    "services": "render-services.py",
    "agent-settings": "render-project-settings.py",
}


class MissingFunctions(Exception):
    pass


_loaded: dict[str, object] = {}


def load(name: str):
    """A registered applier module; refuses to load one missing plan/apply/EXTERNAL."""
    if name in _loaded:
        return _loaded[name]
    path = BIN / APPLIERS[name]
    spec = importlib.util.spec_from_file_location(f"applier_{name.replace('-', '_')}", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    missing = [f for f in FUNCTIONS if not callable(getattr(m, f, None))]
    if not isinstance(getattr(m, "EXTERNAL", None), bool):
        missing.append("EXTERNAL")
    if missing:
        raise MissingFunctions(_("{file} is not an applier: missing {names}", file=path.name, names=", ".join(missing)))
    _loaded[name] = m
    return m


# config files (new-user.md 3.0): these are what git tracks; after step 6 moves them to the config repo, hubconf points
# there
CONFIG_PATHS = ["hub.json", "mirror-list.txt", "services.tsv", "schedule.yaml", "projects"]


def applied_path() -> pathlib.Path:
    import hubconf
    return hubconf.STATE / "config-applied.json"


def config_commit() -> str:
    """The last commit of the config files; adds +dirty if the working tree has uncommitted config changes."""
    import subprocess
    import hubconf

    def git(*a):
        return subprocess.run(["git", "-C", str(hubconf.config_dir()), *a], capture_output=True, text=True).stdout.strip()
    sha = git("log", "-1", "--format=%H", "--", *CONFIG_PATHS) or "unknown"
    return sha + ("+dirty" if git("status", "--porcelain", "--", *CONFIG_PATHS) else "")


def plan_all(index=None, only: list[str] | None = None) -> dict[str, list[Change]]:
    return {n: load(n).plan(index) for n in APPLIERS if not only or n in only}


def apply_all(index=None, only: list[str] | None = None) -> dict[str, list[Change]]:
    return {n: load(n).apply(index) for n in APPLIERS if not only or n in only}
