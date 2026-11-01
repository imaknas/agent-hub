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
       render-project-settings.py --worktree <folder>   (worktree-memory.sh: a sandboxed project's new worktree)
Also an applier (appliers.py): plan()/apply(), used by `bin/hub config plan/apply`.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402
import nofollow  # noqa: E402
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


def read_settings(doc: dict) -> tuple[dict, str | None]:
    """(content, why it couldn't be read). The file is in the project's folder, where a sandboxed project's job can put
    a link (e.g. to the user-level settings) or a FIFO: it is read without following the one or waiting on the other
    (nofollow.py); what isn't a plain file counts as empty and is replaced by the next write."""
    ws = registry.dirs(doc)["workspace"]
    try:
        raw = nofollow.read(ws, settings_path(doc).relative_to(ws))
        return (json.loads(raw) if raw else {}), None
    except (OSError, ValueError) as e:
        return {}, str(e)


def write_settings(doc: dict, content: dict, folder: pathlib.Path | None = None) -> None:
    """Replaces the file (a link or a FIFO there is replaced, never written through), in the workspace or in another of
    its session folders (a worktree)."""
    ws = registry.dirs(doc)["workspace"]
    nofollow.replace(folder or ws, settings_path(doc).relative_to(ws),
                     (json.dumps(content, indent=2, ensure_ascii=False) + "\n").encode())


def compose(name: str, doc: dict, index: dict) -> tuple[pathlib.Path, dict, dict, dict, dict, str | None] | None:
    """Pure computation, no writes: (settings file, current content, intended content, the record of what the platform
    wrote last time, the record to keep this time, why the file isn't a plain file or None). None when there is no
    workspace."""
    path = settings_path(doc)
    if not path:
        return None
    sandbox, deny, allow = desired(name, doc, index)
    cur, unreadable = read_settings(doc)
    prev = json.loads((RENDERED / f"{name}.json").read_text()) if (RENDERED / f"{name}.json").exists() else {}
    new = dict(cur)
    new["sandbox"] = sandbox
    p2 = dict(cur.get("permissions", {}))
    for key, mine in (("deny", deny), ("allow", allow)):
        # drop what the platform added last time, keep everyone else's
        keep = [r for r in p2.get(key, []) if r not in set(prev.get(key, []))]
        p2[key] = keep + [r for r in mine if r not in keep]
    new["permissions"] = p2
    # the platform's hooks only: a sandboxed project's own processes can't write this file (policy protect), and a
    # hook found here that the platform didn't write would run outside its sandbox: it is removed
    new["hooks"] = runtime_claude.platform_hooks()
    env = desired_env(name, doc, index)
    e2 = {k: v for k, v in (cur.get("env") or {}).items() if k not in set(prev.get("env") or {})}
    e2.update(env)
    if e2:
        new["env"] = e2
    return path, cur, new, prev, {"sandbox": sandbox, "deny": deny, "allow": allow, "env": env,
                                  "hooks": new["hooks"]}, unreadable


def write_project(name: str, doc: dict, index: dict, report: bool) -> list[str]:
    """Returns the drift (where the actual file differs from what it should be). report=True only computes, no
    writes."""
    c = compose(name, doc, index)
    if not c:
        return []
    path, cur, new, prev, record, unreadable = c
    sandbox, deny, allow = record["sandbox"], record["deny"], record["allow"]
    perms = cur.get("permissions", {})
    drift = [_("the settings file is not a plain file ({why}); it is replaced", why=unreadable)] if unreadable else []
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
    if cur.get("hooks") and cur["hooks"] != record["hooks"]:
        drift.append(_("hooks the platform didn't write were removed"))
    ws = registry.dirs(doc)["workspace"]
    drift += [_("its own files would make its sessions run something the platform didn't put there: {what}",
                what=what) for what in runtime_claude.foreign_controls(ws)]
    if report:
        print(_("{project}: would write sandbox (allowWrite {write}, denyRead {read}, {domains} {domains:domain|domains}), "
                "{deny} deny {deny:rule|rules}, {allow} allow {allow:rule|rules} → {path}", project=name,
                write=len(sandbox["filesystem"]["allowWrite"]), read=len(sandbox["filesystem"]["denyRead"]),
                domains=len(sandbox["network"]["allowedDomains"]), deny=len(deny), allow=len(allow), path=path))
        return drift
    for folder in runtime_claude.session_folders(ws):   # the workspace and its worktrees get the same settings
        try:
            if folder != ws or new != cur or drift:
                write_settings(doc, new, folder)
        except OSError as e:   # e.g. the folder it is in is itself a link: nothing written through it
            drift.append(_("the settings file could not be written ({why})", why=e))
    RENDERED.mkdir(parents=True, exist_ok=True)
    (RENDERED / f"{name}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1))
    return drift


def unapply(name: str, doc: dict) -> None:
    """enforce turned off in the registry: remove the sandbox and the rules the platform wrote (roll back)."""
    rec = RENDERED / f"{name}.json"
    path = settings_path(doc)
    if not rec.exists() or not path:
        return
    prev = json.loads(rec.read_text())
    cur, unreadable = read_settings(doc)
    if unreadable is None and not cur:
        return
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
    write_settings(doc, cur)
    rec.unlink()
    print(_("{project}: enforce is off, removed the platform's sandbox/rules", project=name))


GATE_MARK = "# agent-hub:gate (managed; registry gate: strict)"
GATE_HOOK_HEAD = GATE_MARK + """
# No direct commit or merge commit on main/master in the main checkout; merge-branch fast-forwards, which does not run this hook.
b=$(git symbolic-ref --short -q HEAD)
case "$b" in main|master) ;; *) exit 0 ;; esac
[ -n "$MERGE_GATE" ] && exit 0
"""


# the branch, as the hook's shell expands it: braced, since a translation may put a non-ASCII character right after it
# ($b followed by a full-width full stop, U+3002, makes some shells read its first byte as part of the name,
# dropping the branch and leaving broken UTF-8)
BRANCH = "${b}"


def gate_hook() -> str:
    """the hook's text; the line it prints when it refuses a commit is a message ({branch} is filled in by the shell)"""
    return GATE_HOOK_HEAD + 'echo "' + _(
        "This project has the merge gate on (gate: strict): no direct commits to {branch}. Work in your own "
        "worktree/branch and finish with agenthub merge-branch.", branch=BRANCH) + '" >&2\nexit 1\n'


MARK = "# agent-hub:gate"   # every hook the platform writes starts with it; others are the project's own
HOOKS = ("pre-commit", "pre-merge-commit", "commit-msg")


def wanted_hooks(doc: dict) -> dict[str, tuple[str, str]]:
    """hook -> (its text, which registry field wants it). gate: strict refuses commits on main; commit_gate runs the
    commit tiers on a branch and the merge tiers on main through the main checkout's runner (gate code from main,
    docs/design/test-waiting.md A1); commits: conventional checks commit titles. One pre-commit serves gate and
    commit_gate (strict wins on main: no commit there at all)."""
    import shlex
    strict, cg = doc.get("gate") == "strict", doc.get("commit_gate") is True
    py, hub = shlex.quote(str(hubconf.MAIN / "bin" / "py")), hubconf.MAIN / "bin"
    out = {}
    if strict and not cg:
        out["pre-commit"] = ("#!/bin/sh\n" + gate_hook(), "gate strict")
    elif cg:
        refuse = ('  main|master) [ -n "$MERGE_GATE" ] || { echo "' + _(
            "This project has the merge gate on (gate: strict): no direct commits to {branch}. Work in your own "
            "worktree/branch and finish with agenthub merge-branch.", branch=BRANCH) + '" >&2; exit 1; } ;;\n') if strict else "  main|master) moment=merge ;;\n"
        out["pre-commit"] = ("#!/bin/sh\n" + MARK + " (managed; registry commit_gate)\n"
                             "# a commit on a branch runs the commit tiers, one onto main the merge tiers\n"
                             "b=$(git symbolic-ref --short -q HEAD)\nmoment=commit\ncase \"$b\" in\n" + refuse +
                             "esac\nexec " + py + " " + shlex.quote(str(hub / "test-runner.py")) +
                             ' --index --when "$moment"\n', "commit_gate")
    if strict:
        out["pre-merge-commit"] = ("#!/bin/sh\n" + gate_hook(), "gate strict")
    if doc.get("commits") == "conventional":
        out["commit-msg"] = ("#!/bin/sh\n" + MARK + " (managed; registry commits: conventional)\nexec " + py + " " +
                             shlex.quote(str(hub / "commit_title.py")) + ' --file "$1"\n', "commits: conventional")
    return out


def gate(name: str, doc: dict, report: bool, quiet: bool = False, remove_all: bool = False) -> list[tuple[str, str]]:
    """The platform's git hooks for a repo (wanted_hooks): installed when its registry entry wants them, updated when
    their text changed, removed when it no longer does (only those carrying the platform's marker). A project with
    its own core.hooksPath (e.g. husky) gets nothing installed, only detection. Returns what would be done (report) /
    was done: (add|update|remove, hook)."""
    import project_git
    say = (lambda *_args: None) if quiet else print
    ws = registry.dirs(doc)["workspace"]
    if not ws or not (ws / ".git").is_dir():
        return []
    want = {} if remove_all else wanted_hooks(doc)   # remove_all: agenthub uninstall
    # the repo's .git is the project's: what is there is read, and the hooks written, by its own job (a symlink
    # planted in .git/hooks then reaches only what the project can write itself; the job replaces it rather than
    # writing through it)
    try:
        st = project_git.data(name, ws, "hooks", ws, *HOOKS)
    except project_git.Unavailable as e:
        say(_("{project}: the gate hooks could not be checked ({why})", project=name, why=e))
        return []
    st = st if isinstance(st, dict) else {}
    hp = st.get("hooks_path") if isinstance(st.get("hooks_path"), str) else ""
    texts = st.get("hooks") if isinstance(st.get("hooks"), dict) else {}
    acts = []
    if name == hubconf.hub_project() and hp and pathlib.Path(ws, hp).resolve() == (hubconf.MAIN / "githooks").resolve():
        # the hub's tracked githooks/ from before its gate moved into its git dir (step 2.3): the platform's own, so
        # replaced, not detected around
        acts.append(("remove", "core.hooksPath"))
        if not report:
            subprocess.run(["git", "-C", str(ws), "config", "--unset", "core.hooksPath"], capture_output=True)
            say(_("{project}: core.hooksPath {path} unset; the platform's hooks go into the git dir", project=name, path=hp))
        hp = ""
    if want and hp:
        say(_("{project}: the platform's hooks are not installed (the project uses its own core.hooksPath={path}); "
              "detecting only", project=name, path=hp))
        return acts
    for hook in HOOKS:
        text = texts.get(hook) if isinstance(texts.get(hook), str) else None
        mine = text is not None and text.startswith("#!/bin/sh\n" + MARK)
        if hook in want:
            body, why = want[hook]
            if text is not None and not mine:
                say(_("{project}: .git/hooks/{hook} is already the project's own hook; not overwriting it (the gate only "
                      "detects)", project=name, hook=hook))
                continue
            if text == body:
                continue
            acts.append(("update" if mine else "add", hook))
            if not report:
                project_git.job(name, ws, "hook-write", ws, hook, input=body)
                say((_("{project}: updated {hook} ({why})", project=name, hook=hook, why=why) if mine else
                     _("{project}: installed {hook} ({why})", project=name, hook=hook, why=why)))
        elif mine:
            acts.append(("remove", hook))
            if not report:
                project_git.job(name, ws, "hook-remove", ws, hook)
                say(_("{project}: the registry no longer wants {hook}; removed it", project=name, hook=hook))
    return acts


# ---------- applier (appliers.py) ----------
# A Change uses only neutral words (agent-settings:<project>, hook:<project>/<name>); the runtime's settings format
# stays in runtime_claude.

EXTERNAL = False


# ---------- the agent's session hooks (the runtime's lifecycle: `hub-event <event>`) ----------
# A hook names the hub where it was when it was written (hubconf.hub_bin). A project's are written again at each of its
# session starts (sync-local-setup → hub-event install), but only while they still reach the hub: once the hub has
# moved, the old ones quietly do nothing and nothing in a session ever rewrites them. So the applier keeps them too:
# `agenthub config apply` after a move (the scheduler and the CLI need writing again then anyway) points every one at
# the hub's new place. The hub's own sessions get theirs only here: its memory isn't a checkout of its own, so the
# session-start setup that installs a project's skips it. A sandboxed project's are part of its settings above.

def lifecycle_folder(name: str, doc: dict) -> pathlib.Path | None:
    """Where this registry entry's sessions get the platform's hooks from the applier, or None."""
    if name == hubconf.hub_project():
        return hubconf.MAIN
    if (doc.get("enforce") or {}).get("sandbox"):
        return None
    ws = registry.dirs(doc)["workspace"]
    return ws if ws and (ws / ".agent-memory").is_dir() else None   # onboarded on this machine


def lifecycle(index: dict, apply: bool, only: str | None = None) -> list:
    """The session hooks that are not what installing them now would write: installed when apply; the Changes."""
    from appliers import Change
    import runtime_ports
    try:
        rt = runtime_ports.load(ports=("lifecycle",))
    except runtime_ports.Unsupported:   # the runtime has no session events (doctor's feature table says so)
        return other_lifecycles(index, apply, only)
    out = []
    for name, doc in sorted(index["projects"].items()):
        ws = lifecycle_folder(name, doc)
        if not ws or (only and name != only):
            continue
        try:
            if rt.install_lifecycle(ws, check=True):
                if apply:
                    rt.install_lifecycle(ws)
                out.append(Change("update", f"agent-settings:{name}", _("the platform's session hooks")))
        except (OSError, ValueError) as e:   # e.g. its settings aren't valid JSON: left as they are, said
            print(_("{project}: its session hooks could not be checked ({why})", project=name, why=e), file=sys.stderr)
    return out + other_lifecycles(index, apply, only)


CAGED_NEEDS = {"control", "confine"}


def other_lifecycles(index: dict, apply: bool, only: str | None = None) -> list:
    """The session hooks of the installed runtimes other than runtime.default (hub.json runtime.installed;
    docs/design/mailbox-runtimes.md A1), same rules as lifecycle(). In a sandboxed project only a runtime with control
    and confine gets them, written by the platform (its hook files are then in the project's protected paths, and its
    runs can be confined); one without them is refused there, fail closed: never installed, and hub-event does nothing
    for one found anyway (doctor says which). Claude Code's hooks in a sandboxed project are part of its settings
    (compose)."""
    from appliers import Change
    import runtime_ports
    out = []
    for rname in runtime_ports.installed():
        if rname == runtime_ports.default_name():
            continue
        try:
            rt = runtime_ports.load(rname, ports=("lifecycle",))
        except runtime_ports.Unsupported:
            continue
        caged_ok = CAGED_NEEDS <= runtime_ports.capabilities(rname)
        for name, doc in sorted(index["projects"].items()):
            if only and name != only:
                continue
            ws = lifecycle_folder(name, doc)
            if ws is None and (doc.get("enforce") or {}).get("sandbox") and caged_ok and rt is not runtime_claude:
                w = registry.dirs(doc)["workspace"]
                ws = w if w and (w / ".agent-memory").is_dir() else None
            if not ws:
                continue
            try:
                if rt.install_lifecycle(ws, check=True):
                    if apply:
                        rt.install_lifecycle(ws)
                    out.append(Change("update", f"agent-settings:{name}",
                                      _("the platform's session hooks ({runtime})", runtime=rname)))
            except (OSError, ValueError) as e:
                print(_("{project}: its session hooks could not be checked ({why})", project=name, why=e),
                      file=sys.stderr)
    return out


def _changes(index: dict) -> list:
    from appliers import Change
    out = lifecycle(index, apply=False)
    for name, doc in sorted(index["projects"].items()):
        want = wanted_hooks(doc)
        out += [Change(op, f"hook:{name}/{hook}", want.get(hook, ("", _("not wanted")))[1])
                for op, hook in gate(name, doc, report=True, quiet=True)]
        if name == hubconf.hub_project():   # the platform's own repo: its hooks only, no sandbox settings
            continue
        on = bool((doc.get("enforce") or {}).get("sandbox"))
        rec = RENDERED / f"{name}.json"
        if not on:
            if rec.exists() and settings_path(doc) and settings_path(doc).exists():
                out.append(Change("remove", f"agent-settings:{name}", _("enforce is off: remove the platform's sandbox/rules")))
            continue
        c = compose(name, doc, index)
        if not c:
            continue
        path, cur, new, prev, record, unreadable = c
        if new != cur or prev != record or unreadable:
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
    lifecycle(index, apply=True)
    for name, doc in sorted(index["projects"].items()):
        gate(name, doc, report=False, quiet=True)
        if name == hubconf.hub_project():
            continue
        if (doc.get("enforce") or {}).get("sandbox"):
            write_project(name, doc, index, report=False)
        else:
            unapply(name, doc)
    return todo


def worktree(folder: str) -> int:
    """At a worktree session's start (worktree-memory.sh, before anything runs as the project): the platform's settings
    for a sandboxed project written into that worktree, as a plain file (the project can't write it there itself)."""
    import isolation
    import project_git
    found = project_git.project_at(folder)
    if not found or not isolation.sandboxed(found[1]):
        return 0
    try:
        index = registry.refresh()
        c = compose(found[0], index["projects"][found[0]], index)
        write_settings(found[1], c[2], pathlib.Path(folder))
    except Exception as e:   # said into the session (worktree-memory.sh prints it): no sandbox settings here
        print(_("⚠️ This worktree's sandbox settings could not be written ({why}); don't run commands in this session, "
                "ask the owner to look at it", why=e))
        return 1
    return 0


def main() -> int:
    if sys.argv[1:2] == ["--worktree"] and len(sys.argv) == 3:
        return worktree(sys.argv[2])
    report = "--report" in sys.argv
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    index = registry.refresh()
    bad = 0
    for c in lifecycle(index, apply=not report, only=only):
        name = c.target.split(":", 1)[1]
        print(_("{project}: would write the platform's session hooks again", project=name) if report else
              _("{project}: the platform's session hooks written again", project=name))
    for name, doc in sorted(index["projects"].items()):
        if only and name != only:
            continue
        gate(name, doc, report)
        if name == hubconf.hub_project():   # the platform's own repo: its hooks only, no sandbox settings
            continue
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
