#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Opens a project for exploring one of the assistant's ideas (an idea sandbox, docs/design/idea-sandboxes.md).

An idea sandbox = a normally onboarded project (with .agent-memory: the handoff notes from discuss leases go there),
with no GitHub remote, marked `type: idea` in the registry, at <workspace_root>/<name>. The name is a human-readable
code the idea has from the start (e.g. room-3d; decided by the owner on 2026-10-25, earlier limited to idea-<n>,
existing ones still work): lowercase letters, digits and -, not clashing with the registry, a folder or a memory repo.
Whether a project is an idea sandbox depends only on the registry's type: idea, not on the name. The registry also gets
the purpose, `enforce: {sandbox: true, work: true}` (the real write block; work = a lease may be a work lease and make
changes, which idea sandboxes allow by default, 10-25 the owner) + the default capabilities (default_for in hub.json
capabilities, e.g. gpu; docs/design/capabilities.md), `privacy: {reflection: stats_only}`, in the same commit as the
registration (onboard's ONBOARD_REGISTRY_EXTRA).

Usage: create-idea-sandbox.py <name> <purpose>
Output: the last line is one JSON object (lease-runner's POST /v1/sandboxes turns it straight into the response):
  {"result": "created"|"exists_same", "name"}                              exit 0
  {"result": "bad_name"|"exists"|"too_many"|"create_failed", "detail", ...}  exit 1
  too_many also carries limit, current.
- Resend: same name and same purpose already registered → exists_same (the assistant resending after a timeout does
  not open a second one).
- Clash: same name but a different purpose, the name of a project, or the folder/bare repo/memory copy already exists
  → exists.
- Limit: hub.json's ideas.limit (default 20) projects of type: idea.
- onboard fails: removes the folder, bare repo, memory copy and the uncommitted registry file made this time (all
  checked beforehand not to exist), then returns create_failed.
Only one runs at a time (lock in <state>/idea-sandbox.lock) because onboarding commits to agent-hub.
"""
from __future__ import annotations

import fcntl
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import names  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)

# same rules as ordinary project names (rename-project.sh), at most 40 characters
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")


def default_capabilities() -> list[str]:
    """Capabilities in hub.json's capabilities whose default_for includes idea and whose risk isn't high (written
    explicitly into the registry, not implied by the type)."""
    return sorted(k for k, v in hubconf.capabilities().items()
                  if "idea" in (v.get("default_for") or []) and v.get("risk") != "high")
MAX_PURPOSE = 500
HUB = hubconf.ROOT


def out(result: str, code: int, **extra) -> int:
    print(json.dumps({"result": result, **extra}, ensure_ascii=False))
    return code


def registered() -> dict[str, dict]:
    """The source files in the registry directory (newer than the cache: counts one onboarded just now)."""
    import yaml
    docs = {}
    for f in hubconf.registry_dir().glob("*.yaml"):
        try:
            docs[f.stem] = yaml.safe_load(f.read_text()) or {}
        except Exception:
            docs[f.stem] = {}
    return docs


def create(name: str, purpose: str) -> int:
    if not NAME.match(name) or "--" in name:
        return out("bad_name", 1, detail=_(
            "The name may only use lowercase letters, digits and - (not at the start or end, at most 40 characters); "
            "got {name!r}", name=name))
    if name in names.all("project"):   # the platform under either of its names
        return out("exists", 1, detail=_("{name} is the platform's own name", name=name))
    purpose = " ".join(purpose.split())
    if not purpose:
        return out("bad_name", 1, detail=_("purpose cannot be empty"))
    if len(purpose) > MAX_PURPOSE:
        return out("bad_name", 1, detail=_("purpose is too long ({n} characters, the limit is {limit})",
                                              n=len(purpose), limit=MAX_PURPOSE))

    docs = registered()
    if name in docs:
        d = docs[name]
        if d.get("type") == "idea" and " ".join(str(d.get("purpose", "")).split()) == purpose:
            return out("exists_same", 0, name=name)
        what = _("another idea") if d.get("type") == "idea" else _("a project")
        return out("exists", 1, detail=_("{name} is already the name of {what}", name=name, what=what))

    ws = hubconf.p("workspace_root", "~/workspace") / name
    bare = hubconf.p("git_origin", "~/git-origin") / f"{name}-memory.git"
    copy = hubconf.p("memory_copies", "~/.memory-copies") / name
    svc = hubconf.p("services_root", "~/services") / name
    taken = [str(x) for x in (ws, bare, copy, svc) if x.exists()]
    if taken:
        return out("exists", 1, detail=_("{name} is not registered, but these already exist: {paths}", name=name,
                                               paths=', '.join(taken)))

    limit = int(hubconf.get("ideas.limit", 20))
    current = sum(1 for d in docs.values() if d.get("type") == "idea")
    if current >= limit:
        return out("too_many", 1, detail=_("There {current:is|are} already {current} idea {current:sandbox|sandboxes} "
                                           "(limit {limit})", current=current, limit=limit), limit=limit, current=current)

    ws.mkdir(parents=True)
    (ws / "README.md").write_text(_(
        "# {name}\n\nA project for exploring an idea (an idea sandbox), opened by the assistant for this idea:\n\n"
        "> {purpose}\n\nConclusions, decisions and to-dos from discuss leases go in memory (`.agent-memory/`); "
        "to turn it into a real project, use agent-hub's `bin/upgrade-idea.py`.\n", name=name, purpose=purpose))
    extra = {"type": "idea", "purpose": purpose, "enforce": {"sandbox": True, "capabilities": default_capabilities(), "work": True},
             "privacy": {"reflection": "stats_only"}}
    env = {**os.environ, "ONBOARD_REGISTRY_EXTRA": json.dumps(extra, ensure_ascii=False)}
    r = subprocess.run([str(HUB / "bin" / "onboard-project.sh"), "--no-remote", str(ws)],
                       capture_output=True, text=True, env=env)
    reg_file = hubconf.registry_dir() / f"{name}.yaml"
    if r.returncode != 0:
        tracked = subprocess.run(["git", "-C", str(HUB), "ls-files", "--error-unmatch", str(reg_file)],
                                 capture_output=True).returncode == 0
        for x in (ws, bare, copy):
            shutil.rmtree(x, ignore_errors=True)
        if reg_file.exists() and not tracked:
            reg_file.unlink()
        tail = " / ".join((r.stderr or r.stdout).strip().splitlines()[-3:])
        note = _(" (the registry file was already committed, so it was not deleted: handle it by hand)") if tracked else ""
        return out("create_failed", 1, detail=_("onboard failed: {tail}{note}", tail=tail, note=note))

    subprocess.run([sys.executable, str(HUB / "bin" / "registry.py"), "check", name],
                   capture_output=True)  # refresh the cache: attachable sees it right away
    # write the sandbox settings now, not waiting for the 15-minute platform job: work mode (lease-runner's work_ready)
    # only lets it through once it sees them
    subprocess.run([sys.executable, str(HUB / "bin" / "render-project-settings.py"), "--only", name],
                   capture_output=True)
    return out("created", 0, name=name)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(_(__doc__).split("\n\n")[2], file=sys.stderr)
        return 2
    lock = hubconf.STATE / "idea-sandbox.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        return create(argv[0], argv[1])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
