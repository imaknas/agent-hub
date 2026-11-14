#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Conformance checks of a coding agent's binary (docs/design/runtime-versions.md section 2): whether this version of the
tool still does what each of the platform's ports rests on.

  conformance.py run <runtime> [--binary <exe>] [--pair <exe>] [--only <check>]... [--report]
        run the runtime's checks (conformance_<runtime>.CHECKS) against exe (default: the one platform runs start now) and
        record the result; --report prints it and records nothing (the only form a linked worktree of the hub allows:
        a pass recorded from half-edited code would drive a promotion); --pair: the copy a session-side check pairs exe
        with (sessions-fork: a session exe wrote, forked by that copy; default: exe itself)
  conformance.py child <runtime> <check> <world> [<exe> [<pair>]]   internal: one check, in its throwaway world (pair:
        the platform's current copy, for a check of a session's version that pairs the two, world.pair)

Neutral: the checks are the adapters' (bin/conformance_<runtime>.py: CHECKS, a list of runtime_ports.Check); only runtimes with the
distribution port have versions to check, and (until it has one) a runtime whose checks are all of the session side and
which says its version (versioned(): one whose sessions run a version the platform doesn't install, such as a shared
daemon that updates itself; its result goes where the per-version gate reads it, record_any). How every check runs:
- in its own process, whose environment points the platform's config, state, data, secrets (and so the session records),
  bus and logs, the instance directory, and HOME, at a throwaway world under hubconf.private_tmp() (never the system temp folder, which every
  sandboxed project can write as the same user). Before its check the child proves it: every place the platform would
  write resolves inside the world, or the check is an error. The tool's own config folder is the adapter's to point
  into the world (world.tool);
- a safety check counts at once; a function check that fails runs once more (a model's answer can miss once);
- a check whose run is None is not-checkable, with the measurement that would make it checkable;
- results keep pass / fail / error / not-checkable and a short reason, never the tool's output.

Results: <state>/runtime-conformance/<runtime>/<version>.json: {runtime, version, binary, digest (sha256), at, checks:
{name: {port, kind, state, reason, tries}}}: what promotion reads (bin/update-runtimes.py). A version seen in the user's
sessions has its session-side checks (Check.side "session") recorded beside it as <version>.session.json, with the
version of the platform's copy it was paired with (current), by bin/session-versions.py (record_session; read by
runtime_ports.gated and fork_refusal). Neither overwrites the other.
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

# None: <state>/runtime-conformance, read at each call from hubconf.STATE (not frozen at import: a test that points
# hubconf.STATE elsewhere must never write the live results; a stray result written so on 11-14 showed in doctor).
# Tests may still set RESULTS itself.
RESULTS: pathlib.Path | None = None


def _results() -> pathlib.Path:
    return RESULTS if RESULTS is not None else hubconf.STATE / "runtime-conformance"
REASON_MAX = 300


def usage() -> str:
    return _("usage: conformance.py run <runtime> [--binary <exe>] [--pair <exe>] [--only <check>]... [--report]")


# ---------- the throwaway world ----------

class World:
    """A throwaway project, home, tool folder and platform instance (config, state, data, secrets, bus), all under one
    root. The child's environment points the platform at it (env()); the adapter's checks point the tool at tool/."""

    PARTS = ("project", "home", "tool", "config", "state", "data", "secrets", "bus", "tmp", "instance")
    pair = None   # the platform's current copy, for a check that pairs a session's version with it (the runner's choice)

    def __init__(self, root: pathlib.Path):
        self.root = pathlib.Path(root)
        for part in self.PARTS:
            setattr(self, part, self.root / part)

    def create(self) -> "World":
        for part in self.PARTS:
            getattr(self, part).mkdir(mode=0o700, parents=True, exist_ok=True)
        (self.config / "projects").mkdir(exist_ok=True)
        # where a hub keeps its bare repos and memory copies, inside the world (a check of a run in a memory copy)
        (self.config / names.current("config_file")).write_text(json.dumps(
            {"bus_dir": str(self.bus), "registry_dir": "projects", "git_origin": str(self.home / "git-origin"),
             "memory_copies": str(self.home / ".memory-copies")}, indent=2) + "\n")
        return self

    def env(self, base: dict | None = None) -> dict:
        """The child's environment: the platform's own PATH and language, everything else of the caller's left out (no
        token, key or setting of the user's reaches a check unless its adapter puts it there, section 3a)."""
        base = os.environ if base is None else base
        e = {k: base[k] for k in ("LANG", "LC_ALL", "LC_CTYPE", "USER", "LOGNAME", "SHELL") if k in base}
        e.update(PATH=hubconf.LAUNCHD_PATH, HOME=str(self.home), TMPDIR=str(self.tmp))
        # the instance directory too: else the hub's pointer names the live one, which holds the world (private_tmp is
        # in its secrets) and which a sandboxed project's policy denies whole
        for var, part in (("CONFIG", self.config), ("STATE", self.state), ("DATA", self.data), ("SECRETS", self.secrets),
                          ("HOME", self.instance)):
            e = names.env_set(e, var, str(part))
        # bin/py finds the platform's Python under the home, which is the world's: name it, or the platform's own scripts
        # (the hooks a check fires) can't start there
        return names.env_set(e, "PYTHON", sys.executable)

    def isolated(self) -> str | None:
        """In the child: None when every place the platform writes is inside the world, else which isn't."""
        root = os.path.realpath(self.root)
        places = {"config": hubconf.config_dir(), "state": hubconf.STATE, "data": hubconf.data_dir(),
                  "secrets": hubconf.secrets_dir(), "sessions": hubconf.session_addresses_dir(), "bus": hubconf.BUS_DIR,
                  "logs": hubconf.LOGS, "shared": hubconf.SHARED, "home": pathlib.Path.home(),
                  "instance": hubconf.instance_home() or hubconf.ROOT}
        out = [k for k, p in places.items() if not os.path.realpath(p).startswith(root + os.sep)]
        return _("not isolated: {places} outside the throwaway world", places=", ".join(out)) if out else None


# ---------- the runner ----------

def adapter(runtime: str):
    """The runtime's conformance adapter, bin/conformance_<runtime>.py (it holds the tool's syntax, as runtime_<name>.py
    does), or None: a runtime with none has no checks."""
    import importlib
    if not runtime.isidentifier() or runtime not in runtime_ports.RUNTIMES:
        return None
    try:
        return importlib.import_module(f"conformance_{runtime}")
    except ModuleNotFoundError as e:
        if e.name == f"conformance_{runtime}":
            return None
        raise


def checks(runtime: str) -> list[Check]:
    mod = adapter(runtime)
    return list(getattr(mod, "CHECKS", None) or []) if mod else []


def code_digest(runtime: str) -> str:
    """A digest of the platform's code a safety check's verdict rests on: the checks themselves, the neutral policy, the
    runtime's adapter that renders it into the tool's settings, and the applier that writes them. A session version's
    safety checks run again when it changes (bin/session_versions.py), so a verdict isn't kept past a fix to either side."""
    here = pathlib.Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in (f"conformance_{runtime}.py", "policy.py", runtime_ports.RUNTIMES.get(runtime, runtime) + ".py",
                 "render-project-settings.py"):
        try:
            h.update(name.encode() + b"\0" + (here / name).read_bytes() + b"\0")
        except OSError:
            h.update(name.encode() + b"\0-\0")
    return h.hexdigest()


def session_checks(runtime: str) -> list[str]:
    """The checks run on a version seen in the user's sessions (Check.side "session", docs/design/runtime-versions.md
    section 6)."""
    return [c.name for c in checks(runtime) if c.side == "session"]


def digest(exe) -> str:
    h = hashlib.sha256()
    with open(os.path.realpath(exe), "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _short(reason) -> str:
    return " ".join(str(reason or "").split())[:REASON_MAX]


def run_one(runtime: str, check: Check, exe: str | None, top: pathlib.Path, pair: str | None = None) -> Outcome:
    """One try of one check in a fresh world of its own, in its own process."""
    if check.run is None:
        return Outcome("not-checkable", _short(check.measurement))
    world = World(top / f"{check.name}-{secrets.token_hex(4)}").create()
    prep = getattr(adapter(runtime), "prepare", None)
    try:
        if callable(prep):
            try:   # in this process, which still sees the live home: what the check's runs log in with
                prep(world)
            except OSError as e:
                raise RuntimeError(_("the check's world could not be prepared: {error}", error=type(e).__name__)) from e
        # its own process group, so a timeout ends the tool's processes too, not only the child
        p = subprocess.Popen([sys.executable, str(pathlib.Path(__file__).resolve()), "child", runtime, check.name,
                              str(world.root), exe or "", *([pair] if pair else [])], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                             env=world.env(), stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            out, _err = p.communicate(timeout=check.timeout_s)
        except subprocess.TimeoutExpired:
            _kill_group(p)
            return Outcome("error", _("no answer in {s} s", s=check.timeout_s))
        _kill_group(p)   # what the check left running
        try:
            got = json.loads(out.strip().splitlines()[-1])
            return Outcome(got["state"], _short(got.get("reason"))) if got.get("state") in runtime_ports.CHECK_STATES \
                else Outcome("error", _("the check answered no state"))
        except (ValueError, IndexError, KeyError, AttributeError, TypeError):
            return Outcome("error", _("the check's process ended without an answer (exit {code})", code=p.returncode))
    finally:
        shutil.rmtree(world.root, ignore_errors=True)


def _kill_group(p) -> None:
    import signal
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def run(runtime: str, exe: str | None = None, only: list[str] | None = None, pair: str | None = None) -> dict:
    """Every check (or only these) of runtime against exe; the result document (not written). pair: the platform's
    current copy, which a check of a session's version pairs it with (sessions-fork); with none such a check pairs the
    binary with itself."""
    mod = runtime_ports._module(runtime)
    if not versioned(runtime):
        raise RuntimeError(_("the runtime {runtime} has no distribution port: it is unversioned, nothing to check",
                             runtime=runtime))
    exe = exe or getattr(mod, "binary")()
    resolved = os.path.abspath(shutil.which(exe) or exe)
    todo = [c for c in checks(runtime) if not only or c.name in only]
    unknown = sorted(set(only or ()) - {c.name for c in todo})
    if unknown:
        raise RuntimeError(_("no such check: {names}", names=", ".join(unknown)))
    top = pathlib.Path(tempfile.mkdtemp(prefix="conformance-", dir=hubconf.private_tmp()))
    try:
        # even asking its version runs the candidate's code: in a world of its own, never the live home (section 3a)
        version = mod.version_of(resolved, env=World(top / "version").create().env())
        if not version:
            raise RuntimeError(_("{exe} does not say its version", exe=exe))
        out = {"runtime": runtime, "version": version, "binary": str(resolved), "digest": digest(resolved),
               "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "checks": {}}
        # the checks that need the user's login last, and none of them after a safety fail or when the adapter says a
        # refresh of that login could fall due while they run (section 3a; runtime-codex.md, Step 1 r2, S4)
        todo = [c for c in todo if not c.login] + [c for c in todo if c.login]
        window = getattr(mod, "login_window", None)
        for c in todo:
            held = None
            if c.login and c.run is not None:
                # a fail of a check that holds the user's sessions only says nothing about the copy's own runs
                if any(r["kind"] == "safety" and r["state"] == "fail" and r.get("holds") != "sessions"
                       for r in out["checks"].values()):
                    held = _("withheld: a safety check failed first")
                elif callable(window):
                    held = window(c.timeout_s)
            if held:
                out["checks"][c.name] = {"port": c.port, "kind": c.kind, "state": "error", "reason": _short(held),
                                         "tries": 0, "login": True}
                continue
            tries, got = 1, run_one(runtime, c, str(resolved), top, *([pair] if pair else []))
            if got.state == "fail" and c.kind == "function":
                tries, got = 2, run_one(runtime, c, str(resolved), top, *([pair] if pair else []))
            out["checks"][c.name] = {"port": c.port, "kind": c.kind, "state": got.state, "reason": got.reason,
                                     "tries": tries, **({"login": True} if c.login else {}),
                                     **({"holds": c.holds} if c.holds else {})}
        return out
    finally:
        shutil.rmtree(top, ignore_errors=True)


def versioned(runtime: str) -> bool:
    """Whether the runtime has versions to check: the distribution port, or (before it has one) a version_of and checks
    of the session side only, whose versions are the ones its sessions run (docs/design/runtime-codex.md, Step 4 r2,
    S9)."""
    if "distribution" in runtime_ports.capabilities(runtime):
        return True
    return callable(getattr(runtime_ports._module(runtime), "version_of", None)) and bool(checks(runtime)) \
        and all(c.side == "session" for c in checks(runtime))


def record_any(doc: dict) -> pathlib.Path:
    """A runtime with the distribution port: its full result (promotion reads it). One without: its session result,
    which the per-version gate reads (runtime_ports.gated), paired with no copy of the platform's."""
    if "distribution" in runtime_ports.capabilities(doc["runtime"]):
        return record(doc)
    return record_session(doc, None)


def results_file(runtime: str, version: str) -> pathlib.Path:
    return _results() / runtime / f"{version}.json"


def record(doc: dict) -> pathlib.Path:
    """Write the result (a version checked again replaces its earlier result)."""
    f = results_file(doc["runtime"], doc["version"])
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    tmp.replace(f)
    return f


def record_session(doc: dict, current: str | None) -> pathlib.Path:
    """Write a session version's result (its session-side checks), with the version of the platform's copy it was
    paired with: <version>.session.json, beside and never over the full result promotion reads."""
    f = runtime_ports.session_result_file(doc["runtime"], doc["version"])
    if f is None:
        raise RuntimeError(_("not a version: {version!r}", version=doc.get("version")))
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({**doc, "side": "session", "current": current}, ensure_ascii=False, indent=1) + "\n")
    tmp.replace(f)
    return f


def recorded(runtime: str) -> list[dict]:
    """Every recorded result of this runtime (a full one: not a session version's), newest check first."""
    out = []
    for f in sorted((_results() / runtime).glob("*.json")) if (_results() / runtime).is_dir() else []:
        if f.name.endswith(".session.json"):
            continue
        try:
            out.append(json.loads(f.read_text()))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda d: str(d.get("at", "")), reverse=True)


def verdict(doc: dict, for_promotion: bool = False) -> str:
    """pass when every check that could be run passed; fail when a safety check failed; else the worst state seen.
    for_promotion: a fail of a check that holds the user's sessions only (Check.holds "sessions") doesn't count; its error
    does (runtime-codex.md, Step 1 r2, S7)."""
    states = {(c.get("kind"), c.get("state")) for c in doc.get("checks", {}).values()
              if not (for_promotion and c.get("holds") == "sessions" and c.get("state") == "fail")}
    if ("safety", "fail") in states:
        return "fail"
    seen = {s for _k, s in states}
    for s in ("fail", "error"):
        if s in seen:
            return s
    return "pass"


def login_unrun(doc: dict) -> list[str]:
    """The checks that need the user's login and didn't run to a pass or fail on this result (not-checkable, withheld)."""
    return [n for n, c in doc.get("checks", {}).items() if c.get("login") and c.get("state") not in ("pass", "fail")]


# ---------- the child ----------

def child(runtime: str, name: str, root: str, exe: str, pair: str = "") -> int:
    def say(o: Outcome) -> int:
        print(json.dumps({"state": o.state, "reason": _short(o.reason)}))
        return 0
    world = World(pathlib.Path(root))
    world.pair = pair or None
    if (why := world.isolated()):
        return say(Outcome("error", why))
    mod = runtime_ports._module(runtime)
    if exe and callable(getattr(mod, "check_binary", None)):
        mod.check_binary(exe)
    check = {c.name: c for c in checks(runtime)}.get(name)
    if check is None or check.run is None:
        return say(Outcome("error", _("no such check: {names}", names=name)))
    try:
        enter = getattr(adapter(runtime), "enter", None)
        if callable(enter):
            enter(world)   # the environment every run of this check inherits (the tool's config dir, its login)
            if (why := world.isolated()):   # what it changed must still point into the world
                return say(Outcome("error", why))
        got = check.run(world)
    except Exception as e:   # a check that breaks is an error, said by its kind only (never its text: it may hold output)
        return say(Outcome("error", _("the check broke: {error}", error=type(e).__name__)))
    return say(got if isinstance(got, Outcome) and got.state in runtime_ports.CHECK_STATES
               else Outcome("error", _("the check answered no state")))


def unrun_safety(doc: dict) -> int:
    """How many safety checks couldn't be run (not-checkable): a pass says nothing about what they guard."""
    return sum(1 for c in doc.get("checks", {}).values() if c.get("kind") == "safety" and c.get("state") == "not-checkable")


def show(doc: dict) -> str:
    lines = [_("{runtime} {version}: {verdict} ({binary}, checked {at})", runtime=doc["runtime"], version=doc["version"],
               verdict=verdict(doc), binary=doc["binary"], at=doc["at"])]
    if (n := unrun_safety(doc)):
        lines.append(_("  {n} safety {n:check|checks} not run yet (not-checkable): a pass says nothing about what "
                       "{n:it guards|they guard}", n=n))
    for name, c in doc["checks"].items():
        lines.append(f"  {c['state']:<13} {name} ({c['port']}, {c['kind']})" + (f": {c['reason']}" if c["reason"] else ""))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if argv[:1] == ["child"] and len(argv) in (4, 5, 6):
        return child(argv[1], argv[2], argv[3], argv[4] if len(argv) >= 5 else "", argv[5] if len(argv) == 6 else "")
    if argv in (["-h"], ["--help"]):
        print(usage())
        return 0
    if argv[:1] != ["run"] or len(argv) < 2 or not hubconf.known_args(argv[2:], {"--report"}, {"--binary", "--pair", "--only"}):
        print(usage(), file=sys.stderr)
        return 2
    report = "--report" in argv
    if "--only" in argv and not report:
        # a version's result is all of its checks: some of them would overwrite it with a part (step 2 promotes on it)
        print(_("conformance: --only runs part of the checks, so it only reports: add --report"), file=sys.stderr)
        return 2
    if hubconf.worktree_lookup() and not report:
        print(_("conformance: {root} is a linked worktree of the hub; results are recorded only from the main checkout "
                "({main}); --report works here", root=hubconf.ROOT, main=hubconf.MAIN), file=sys.stderr)
        return 1
    rest = argv[2:]
    exe = rest[rest.index("--binary") + 1] if "--binary" in rest else None
    pair = rest[rest.index("--pair") + 1] if "--pair" in rest else None
    only = [rest[i + 1] for i, a in enumerate(rest) if a == "--only"]
    try:
        doc = run(argv[1], exe, only or None, *([pair] if pair else []))
    except (RuntimeError, KeyError, runtime_ports.Unsupported) as e:   # no checked copy to name: --binary names one
        print(f"conformance: {e}", file=sys.stderr)
        return 1
    print(show(doc))
    if not report:
        try:
            print(_("recorded: {file}", file=record_any(doc)))
        except (RuntimeError, OSError) as e:   # a version the store can't name, a folder it can't write: said
            print(f"conformance: {e}", file=sys.stderr)
            return 1
    return 0 if verdict(doc) == "pass" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
