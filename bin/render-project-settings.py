#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Stage two: each project's sandbox + permission rules, generated from the project registry and written into
`.claude/settings.local.json` (managed file).

What to write: policy.py computes the neutral policy, runtime_claude.settings() translates it into Claude Code's
format; this script only writes the file, records, and detects drift.

Written only for projects registered with `enforce: {sandbox: true}`; for other projects it only computes what it
would write (--report). Not applied to the platform itself (agent-hub).
What is written:
  sandbox (the whole key is managed by the platform): enabled, failIfUnavailable, allowUnsandboxedCommands: false,
    filesystem.allowWrite (its own runtime, the bus directory, the registry's allow_write),
    filesystem.denyRead (other projects' workspace/runtime, raw memory notes, the platform's secrets),
    network.allowedDomains (platform defaults + the registry's network)
  env: the variables the platform sets (its own tool caches, policy.py env); other variables are untouched
  the entries the platform adds to permissions.deny / permissions.allow (other entries are untouched -- an
  interactive session's "always allow" is written there too):
    deny  Read/Edit other projects' workspace/runtime, Edit the platform/bare repos/memory copies/LaunchAgents,
          Read the raw notes in memory copies, Read the platform's secrets
    allow the mailbox CLI, the merge gate (the only channels across the boundary)
  Which entries were written last time is recorded in .state/rendered-settings/<project>.json; next time those are
  removed first and then added again, so nothing piles up.

Drift detection: the actual file differs from what it should be (a session changed its own settings, or someone
edited it by hand) -> print it, exit 1 (platform job failed -> status page), and write it back right away.

Usage: render-project-settings.py [--report] [--only <project>]
Also an applier (appliers.py): plan()/apply(), used by `bin/hub config plan/apply`.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402
import policy  # noqa: E402
import registry  # noqa: E402
import runtime_claude  # noqa: E402

_ = i18n.domain(__file__)

RENDERED = hubconf.STATE / "rendered-settings"


def desired(name: str, doc: dict, index: dict) -> tuple[dict, list[str], list[str]]:
    """(sandbox, deny rules, allow rules): the neutral policy (policy.py) translated into Claude Code settings
    (runtime_claude.py)."""
    return runtime_claude.settings(policy.policy(name, doc, index))


def desired_env(name: str, doc: dict, index: dict) -> dict[str, str]:
    """The environment its sessions get (its own tool caches)."""
    return runtime_claude.session_env(policy.policy(name, doc, index))


def settings_path(doc: dict) -> pathlib.Path | None:
    ws = registry.dirs(doc)["workspace"]
    return runtime_claude.settings_path(ws) if ws else None


def compose(name: str, doc: dict, index: dict) -> tuple[pathlib.Path, dict, dict, dict, dict] | None:
    """Pure computation, no writes: (settings file, current content, intended content, the record of what the platform
    wrote last time, the record to keep this time). None when there is no workspace."""
    path = settings_path(doc)
    if not path:
        return None
    sandbox, deny, allow = desired(name, doc, index)
    cur = json.loads(path.read_text()) if path.exists() else {}
    prev = json.loads((RENDERED / f"{name}.json").read_text()) if (RENDERED / f"{name}.json").exists() else {}
    new = dict(cur)
    new["sandbox"] = sandbox
    p2 = dict(cur.get("permissions", {}))
    for key, mine in (("deny", deny), ("allow", allow)):
        # drop what the platform added last time, keep everyone else's
        keep = [r for r in p2.get(key, []) if r not in set(prev.get(key, []))]
        p2[key] = keep + [r for r in mine if r not in keep]
    new["permissions"] = p2
    env = desired_env(name, doc, index)
    e2 = {k: v for k, v in (cur.get("env") or {}).items() if k not in set(prev.get("env") or {})}
    e2.update(env)
    if e2:
        new["env"] = e2
    return path, cur, new, prev, {"sandbox": sandbox, "deny": deny, "allow": allow, "env": env}


def write_project(name: str, doc: dict, index: dict, report: bool) -> list[str]:
    """Returns the drift (where the actual file differs from what it should be). report=True only computes, no
    writes."""
    c = compose(name, doc, index)
    if not c:
        return []
    path, cur, new, prev, record = c
    sandbox, deny, allow = record["sandbox"], record["deny"], record["allow"]
    perms = cur.get("permissions", {})
    drift = []
    if prev:  # drift only makes sense once it has been written
        if cur.get("sandbox") != prev.get("sandbox"):
            drift.append(_("sandbox was changed"))
        for key in ("deny", "allow"):
            missing = set(prev.get(key, [])) - set(perms.get(key, []))
            if missing:
                drift.append(_("permissions.{key} is missing {n} {n:rule|rules} (e.g. {example})", key=key, n=len(missing),
                               example=sorted(missing)[0]))
        if any((cur.get("env") or {}).get(k) != v for k, v in (prev.get("env") or {}).items()):
            drift.append(_("env was changed"))
    if report:
        print(_("{project}: would write sandbox (allowWrite {write}, denyRead {read}, {domains} {domains:domain|domains}), "
                "{deny} deny {deny:rule|rules}, {allow} allow {allow:rule|rules} → {path}", project=name,
                write=len(sandbox["filesystem"]["allowWrite"]), read=len(sandbox["filesystem"]["denyRead"]),
                domains=len(sandbox["network"]["allowedDomains"]), deny=len(deny), allow=len(allow), path=path))
        return drift
    if new != cur:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n")
    RENDERED.mkdir(parents=True, exist_ok=True)
    (RENDERED / f"{name}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1))
    return drift


def unapply(name: str, doc: dict) -> None:
    """enforce turned off in the registry: remove the sandbox and the rules the platform wrote (roll back)."""
    rec = RENDERED / f"{name}.json"
    path = settings_path(doc)
    if not rec.exists() or not path or not path.exists():
        return
    prev = json.loads(rec.read_text())
    cur = json.loads(path.read_text())
    cur.pop("sandbox", None)
    perms = cur.get("permissions", {})
    for key in ("deny", "allow"):
        if key in perms:
            perms[key] = [r for r in perms[key] if r not in set(prev.get(key, []))]
    env = {k: v for k, v in (cur.get("env") or {}).items() if k not in set(prev.get("env") or {})}
    if env:
        cur["env"] = env
    else:
        cur.pop("env", None)
    path.write_text(json.dumps(cur, indent=2, ensure_ascii=False) + "\n")
    rec.unlink()
    print(_("{project}: enforce is off, removed the platform's sandbox/rules", project=name))


GATE_MARK = "# agent-hub:gate (managed; registry gate: strict)"
GATE_HOOK_HEAD = GATE_MARK + """
# No direct commit or merge commit on main/master in the main checkout; merge-branch fast-forwards, which does not run this hook.
b=$(git symbolic-ref --short -q HEAD)
case "$b" in main|master) ;; *) exit 0 ;; esac
[ -n "$MERGE_GATE" ] && exit 0
"""


def gate_hook() -> str:
    """the hook's text; the line it prints when it refuses a commit is a message ($b = the branch, filled in by the shell)"""
    return GATE_HOOK_HEAD + 'echo "' + _(
        "This project has the merge gate on (gate: strict): no direct commits to $b. Work in your own worktree/branch "
        "and finish with agenthub merge-branch.") + '" >&2\nexit 1\n'


def gate(name: str, doc: dict, report: bool, quiet: bool = False) -> list[tuple[str, str]]:
    """Registry gate: strict -> install pre-commit / pre-merge-commit; turned off -> remove them (only those carrying
    the platform's marker). A project with its own core.hooksPath (e.g. husky) gets nothing installed, only
    detection. Returns what would be done (report) / was done: (add|update|remove, hook)."""
    import subprocess
    say = (lambda *_args: None) if quiet else print
    ws = registry.dirs(doc)["workspace"]
    if not ws or not (ws / ".git").is_dir():
        return []
    want = doc.get("gate") == "strict"
    hp = subprocess.run(["git", "-C", str(ws), "config", "core.hooksPath"], capture_output=True, text=True).stdout.strip()
    acts = []
    for hook in ("pre-commit", "pre-merge-commit"):
        f = ws / ".git" / "hooks" / hook
        text = f.read_text(errors="replace") if f.exists() else ""
        mine = GATE_MARK in text
        if want and hp:
            say(_("{project}: gate strict not installed (the project uses its own core.hooksPath={path}); detecting only",
                  project=name, path=hp))
            return acts
        if want and not mine:
            if f.exists():
                say(_("{project}: .git/hooks/{hook} is already the project's own hook; not overwriting it (the gate only "
                      "detects)", project=name, hook=hook))
                continue
            acts.append(("add", hook))
            if not report:
                f.write_text("#!/bin/sh\n" + gate_hook())
                f.chmod(0o755)
                say(_("{project}: installed {hook} (gate strict)", project=name, hook=hook))
        elif want and text != "#!/bin/sh\n" + gate_hook():   # the platform's hook from an older version (or language)
            acts.append(("update", hook))
            if not report:
                f.write_text("#!/bin/sh\n" + gate_hook())
                say(_("{project}: updated {hook} (gate strict)", project=name, hook=hook))
        elif not want and mine:
            acts.append(("remove", hook))
            if not report:
                f.unlink()
                say(_("{project}: gate is off, removed {hook}", project=name, hook=hook))
    return acts


# ---------- applier (appliers.py) ----------
# A Change uses only neutral words (agent-settings:<project>, hook:<project>/<name>); the runtime's settings format
# stays in runtime_claude.

EXTERNAL = False


def _changes(index: dict) -> list:
    from appliers import Change
    out = []
    for name, doc in sorted(index["projects"].items()):
        if name == hubconf.hub_project():
            continue
        out += [Change(op, f"hook:{name}/{hook}", "gate strict") for op, hook in gate(name, doc, report=True, quiet=True)]
        on = bool((doc.get("enforce") or {}).get("sandbox"))
        rec = RENDERED / f"{name}.json"
        if not on:
            if rec.exists() and settings_path(doc) and settings_path(doc).exists():
                out.append(Change("remove", f"agent-settings:{name}", _("enforce is off: remove the platform's sandbox/rules")))
            continue
        c = compose(name, doc, index)
        if not c:
            continue
        path, cur, new, prev, record = c
        if new != cur or prev != record:
            what = [w for w, changed in (("sandbox", new.get("sandbox") != cur.get("sandbox")),
                                         (_("permission rules"), new.get("permissions") != cur.get("permissions")),
                                         (_("record"), prev != record)) if changed]
            out.append(Change("add" if not cur else "update", f"agent-settings:{name}", _(", ").join(what)))
    return out


def plan(index: dict | None = None) -> list:
    return _changes(index or registry.refresh())


def apply(index: dict | None = None) -> list:
    index = index or registry.refresh()
    todo = _changes(index)
    for name, doc in sorted(index["projects"].items()):
        if name == hubconf.hub_project():
            continue
        gate(name, doc, report=False, quiet=True)
        if (doc.get("enforce") or {}).get("sandbox"):
            write_project(name, doc, index, report=False)
        else:
            unapply(name, doc)
    return todo


def main() -> int:
    report = "--report" in sys.argv
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    index = registry.refresh()
    bad = 0
    for name, doc in sorted(index["projects"].items()):
        if name == hubconf.hub_project() or (only and name != only):
            continue
        gate(name, doc, report)
        on = bool((doc.get("enforce") or {}).get("sandbox"))
        if not on:
            if report:
                write_project(name, doc, index, report=True)
            else:
                unapply(name, doc)
            continue
        drift = write_project(name, doc, index, report=report)
        if drift:
            bad += 1
            print(_("⚠️ {project}'s settings drifted (report mode: not written back): {drift}", project=name,
                    drift="; ".join(drift)) if report else
                  _("⚠️ {project}'s settings drifted (written back): {drift}", project=name, drift="; ".join(drift)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
