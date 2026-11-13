#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Conformance checks of a coding agent's binary (docs/design/runtime-versions.md section 2): whether this version of the
tool still does what each of the platform's ports rests on.

  conformance.py run <runtime> [--binary <exe>] [--only <check>]... [--report]
        run the runtime's checks (its adapter's CONFORMANCE) against exe (default: the one platform runs start now) and
        record the result; --report prints it and records nothing (the only form a linked worktree of the hub allows:
        a pass recorded from half-edited code would drive a promotion)
  conformance.py child <runtime> <check> <world> [<exe>]      internal: one check, in its throwaway world

Neutral: the checks are the adapters' (runtime_<name>.CONFORMANCE, a list of runtime_ports.Check); only runtimes with the
distribution port have versions to check. How every check runs:
- in its own process, whose environment points the platform's config, state, data, secrets (and so the session records),
  bus and logs, and HOME, at a throwaway world under hubconf.private_tmp() (never the system temp folder, which every
  sandboxed project can write as the same user). Before its check the child proves it: every place the platform would
  write resolves inside the world, or the check is an error. The tool's own config folder is the adapter's to point
  into the world (world.tool);
- a safety check counts at once; a function check that fails runs once more (a model's answer can miss once);
- a check whose run is None is not-checkable, with the measurement that would make it checkable;
- results keep pass / fail / error / not-checkable and a short reason, never the tool's output.

Results: <state>/runtime-conformance/<runtime>/<version>.json: {runtime, version, binary, digest (sha256), at, checks:
{name: {port, kind, state, reason, tries}}}. Nothing reads them to change which binary runs yet (step 2 does).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402
import names  # noqa: E402
import runtime_ports  # noqa: E402
from runtime_ports import Check, Outcome  # noqa: E402

_ = i18n.domain(__file__)

RESULTS = hubconf.STATE / "runtime-conformance"
REASON_MAX = 300


def usage() -> str:
    return _("usage: conformance.py run <runtime> [--binary <exe>] [--only <check>]... [--report]")


# ---------- the throwaway world ----------

class World:
    """A throwaway project, home, tool folder and platform instance (config, state, data, secrets, bus), all under one
    root. The child's environment points the platform at it (env()); the adapter's checks point the tool at tool/."""

    PARTS = ("project", "home", "tool", "config", "state", "data", "secrets", "bus", "tmp")

    def __init__(self, root: pathlib.Path):
        self.root = pathlib.Path(root)
        for part in self.PARTS:
            setattr(self, part, self.root / part)

    def create(self) -> "World":
        for part in self.PARTS:
            getattr(self, part).mkdir(mode=0o700, parents=True, exist_ok=True)
        (self.config / "projects").mkdir(exist_ok=True)
        (self.config / names.current("config_file")).write_text(json.dumps(
            {"bus_dir": str(self.bus), "registry_dir": "projects"}, indent=2) + "\n")
        return self

    def env(self, base: dict | None = None) -> dict:
        """The child's environment: the platform's own PATH and language, everything else of the caller's left out (no
        token, key or setting of the user's reaches a check unless its adapter puts it there, section 3a)."""
        base = os.environ if base is None else base
        e = {k: base[k] for k in ("LANG", "LC_ALL", "LC_CTYPE", "USER", "LOGNAME", "SHELL") if k in base}
        e.update(PATH=hubconf.LAUNCHD_PATH, HOME=str(self.home), TMPDIR=str(self.tmp))
        for var, part in (("CONFIG", self.config), ("STATE", self.state), ("DATA", self.data), ("SECRETS", self.secrets)):
            e = names.env_set(e, var, str(part))
        return e

    def isolated(self) -> str | None:
        """In the child: None when every place the platform writes is inside the world, else which isn't."""
        root = os.path.realpath(self.root)
        places = {"config": hubconf.config_dir(), "state": hubconf.STATE, "data": hubconf.data_dir(),
                  "secrets": hubconf.secrets_dir(), "sessions": hubconf.session_addresses_dir(), "bus": hubconf.BUS_DIR,
                  "logs": hubconf.LOGS, "shared": hubconf.SHARED, "home": pathlib.Path.home()}
        out = [k for k, p in places.items() if not os.path.realpath(p).startswith(root + os.sep)]
        return _("not isolated: {places} outside the throwaway world", places=", ".join(out)) if out else None


# ---------- the runner ----------

def checks(runtime: str) -> list[Check]:
    return list(getattr(runtime_ports._module(runtime), "CONFORMANCE", None) or [])


def digest(exe) -> str:
    h = hashlib.sha256()
    with open(os.path.realpath(exe), "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _short(reason) -> str:
    return " ".join(str(reason or "").split())[:REASON_MAX]


def run_one(runtime: str, check: Check, exe: str | None, top: pathlib.Path) -> Outcome:
    """One try of one check in a fresh world of its own, in its own process."""
    if check.run is None:
        return Outcome("not-checkable", _short(check.measurement))
    world = World(top / f"{check.name}-{secrets.token_hex(4)}").create()
    try:
        r = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve()), "child", runtime, check.name,
                            str(world.root), exe or ""], capture_output=True, text=True, timeout=check.timeout_s,
                           env=world.env(), stdin=subprocess.DEVNULL)
        try:
            got = json.loads(r.stdout.strip().splitlines()[-1])
            return Outcome(got["state"], _short(got.get("reason"))) if got.get("state") in runtime_ports.CHECK_STATES \
                else Outcome("error", _("the check answered no state"))
        except (ValueError, IndexError, KeyError, AttributeError, TypeError):
            return Outcome("error", _("the check's process ended without an answer (exit {code})", code=r.returncode))
    except subprocess.TimeoutExpired:
        return Outcome("error", _("no answer in {s} s", s=check.timeout_s))
    finally:
        shutil.rmtree(world.root, ignore_errors=True)


def run(runtime: str, exe: str | None = None, only: list[str] | None = None) -> dict:
    """Every check (or only these) of runtime against exe; the result document (not written)."""
    mod = runtime_ports._module(runtime)
    if "distribution" not in runtime_ports.capabilities(runtime):
        raise RuntimeError(_("the runtime {runtime} has no distribution port: it is unversioned, nothing to check",
                             runtime=runtime))
    exe = exe or getattr(mod, "binary")()
    resolved = shutil.which(exe) or exe
    version = mod.version_of(resolved)
    if not version:
        raise RuntimeError(_("{exe} does not say its version", exe=exe))
    todo = [c for c in checks(runtime) if not only or c.name in only]
    unknown = sorted(set(only or ()) - {c.name for c in todo})
    if unknown:
        raise RuntimeError(_("no such check: {names}", names=", ".join(unknown)))
    out = {"runtime": runtime, "version": version, "binary": str(resolved), "digest": digest(resolved),
           "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "checks": {}}
    top = pathlib.Path(tempfile.mkdtemp(prefix="conformance-", dir=hubconf.private_tmp()))
    try:
        for c in todo:
            tries, got = 1, run_one(runtime, c, str(resolved), top)
            if got.state == "fail" and c.kind == "function":
                tries, got = 2, run_one(runtime, c, str(resolved), top)
            out["checks"][c.name] = {"port": c.port, "kind": c.kind, "state": got.state, "reason": got.reason,
                                     "tries": tries}
    finally:
        shutil.rmtree(top, ignore_errors=True)
    return out


def results_file(runtime: str, version: str) -> pathlib.Path:
    return RESULTS / runtime / f"{version}.json"


def record(doc: dict) -> pathlib.Path:
    """Write the result (a version checked again replaces its earlier result)."""
    f = results_file(doc["runtime"], doc["version"])
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    tmp.replace(f)
    return f


def recorded(runtime: str) -> list[dict]:
    """Every recorded result of this runtime, newest check first."""
    out = []
    for f in sorted((RESULTS / runtime).glob("*.json")) if (RESULTS / runtime).is_dir() else []:
        try:
            out.append(json.loads(f.read_text()))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda d: str(d.get("at", "")), reverse=True)


def verdict(doc: dict) -> str:
    """pass when every check that could be run passed; fail when a safety check failed; else the worst state seen."""
    states = {(c.get("kind"), c.get("state")) for c in doc.get("checks", {}).values()}
    if ("safety", "fail") in states:
        return "fail"
    seen = {s for _k, s in states}
    for s in ("fail", "error"):
        if s in seen:
            return s
    return "pass"


# ---------- the child ----------

def child(runtime: str, name: str, root: str, exe: str) -> int:
    def say(o: Outcome) -> int:
        print(json.dumps({"state": o.state, "reason": _short(o.reason)}))
        return 0
    world = World(pathlib.Path(root))
    if (why := world.isolated()):
        return say(Outcome("error", why))
    mod = runtime_ports._module(runtime)
    if exe and callable(getattr(mod, "check_binary", None)):
        mod.check_binary(exe)
    check = {c.name: c for c in checks(runtime)}.get(name)
    if check is None or check.run is None:
        return say(Outcome("error", _("no such check: {names}", names=name)))
    try:
        got = check.run(world)
    except Exception as e:   # a check that breaks is an error, said by its kind only (never its text: it may hold output)
        return say(Outcome("error", _("the check broke: {error}", error=type(e).__name__)))
    return say(got if isinstance(got, Outcome) and got.state in runtime_ports.CHECK_STATES
               else Outcome("error", _("the check answered no state")))


def show(doc: dict) -> str:
    lines = [_("{runtime} {version}: {verdict} ({binary}, checked {at})", runtime=doc["runtime"], version=doc["version"],
               verdict=verdict(doc), binary=doc["binary"], at=doc["at"])]
    for name, c in doc["checks"].items():
        lines.append(f"  {c['state']:<13} {name} ({c['port']}, {c['kind']})" + (f": {c['reason']}" if c["reason"] else ""))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if argv[:1] == ["child"] and len(argv) in (4, 5):
        return child(argv[1], argv[2], argv[3], argv[4] if len(argv) == 5 else "")
    if argv in (["-h"], ["--help"]):
        print(usage())
        return 0
    if argv[:1] != ["run"] or len(argv) < 2 or not hubconf.known_args(argv[2:], {"--report"}, {"--binary", "--only"}):
        print(usage(), file=sys.stderr)
        return 2
    report = "--report" in argv
    if hubconf.worktree_lookup() and not report:
        print(_("conformance: {root} is a linked worktree of the hub; results are recorded only from the main checkout "
                "({main}); --report works here", root=hubconf.ROOT, main=hubconf.MAIN), file=sys.stderr)
        return 1
    rest = argv[2:]
    exe = rest[rest.index("--binary") + 1] if "--binary" in rest else None
    only = [rest[i + 1] for i, a in enumerate(rest) if a == "--only"]
    try:
        doc = run(argv[1], exe, only or None)
    except (RuntimeError, KeyError) as e:
        print(f"conformance: {e}", file=sys.stderr)
        return 1
    print(show(doc))
    if not report:
        print(_("recorded: {file}", file=record(doc)))
    return 0 if verdict(doc) == "pass" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
