#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Take agent-hub off this machine: `agenthub uninstall [--apply] [--purge] [--yes]`.

Without --apply it only lists what it would do. Each part is undone by the code that put it there (render-services,
render-project-settings, the runtime adapters); this only runs them in order:
  1. the scheduler and its watchdog, when their unit is this hub's (the same labels on another hub are left alone)
  2. the background services render-services generated (and only those: services.tsv entries it doesn't own stay)
  3. in each connected project: the platform's block in the rules file, its hooks in the agent's settings, the sandbox
     and rules it wrote, its git hooks (through project_git, as the project). The project's code is never touched
  4. user level, only on the owner's own yes at the terminal (--yes doesn't count): agent-hub's MCP server registered
     with each AI app, and the runtime's user-level hooks for this hub
  5. the `agenthub` command it put on PATH
Notes stay: the instance directory (settings, data, keys), the memory repos, each project's .agent-memory, and the bus.
--purge removes those too (notes can't be brought back). Never removed: the projects' code, nvm, node, the AI agents,
uv, git. The hub folder itself is removed last, by you: the command is printed at the end.

Refused from a linked worktree, and while a scheduler tick runs (it holds the scheduler's lock while it works).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import pathlib
import plistlib
import shutil
import subprocess
import sys

BIN = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import names  # noqa: E402
import i18n  # noqa: E402
import init_dialogue  # noqa: E402

_ = i18n.domain(__file__)
CORE = ("local.agent-hub-scheduler", "local.agent-hub-scheduler-watchdog")
BLOCKS = [names.block(s) for s in names.all("marker")]   # either spelling (docs/design/rename-brunna.md)


def _load(script: str):
    spec = importlib.util.spec_from_file_location(script.replace("-", "_"), BIN / f"{script}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ours(text: str) -> bool:
    """Whether a unit's text runs this hub (its bin/ named in it)."""
    return str(hubconf.MAIN / "bin") in text


def core_units() -> list[tuple[str, object]]:
    """(what, undo) for the scheduler and watchdog of this hub."""
    out = []
    sm = hubconf.service_manager()
    for label in CORE:
        if sm == "systemd":
            import systemd_units
            units = systemd_units.installed(label)
            if units and any(ours(t) for t in units.values()):
                out.append((label, lambda label=label: systemd_units.remove(label)))
        elif sm == "launchd":
            path = pathlib.Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
            try:
                plist = plistlib.loads(path.read_bytes())
            except (OSError, ValueError, plistlib.InvalidFileException):
                continue
            if ours(" ".join(map(str, plist.get("ProgramArguments") or []))):
                def undo(label=label, path=path):
                    subprocess.run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True)
                    path.unlink(missing_ok=True)
                out.append((label, undo))
    return out


def strip_block(path: pathlib.Path, apply: bool) -> bool:
    """The platform's managed block out of a rules file; a file left empty goes. A link is left alone."""
    if not path.is_file() or path.is_symlink():
        return False
    text = rest = path.read_text()
    for begin, end in BLOCKS:
        a, b = rest.find(begin), rest.find(end)
        if 0 <= a < b:
            rest = rest[:a].rstrip("\n") + "\n" + rest[b + len(end):].lstrip("\n")
    if rest == text:
        return False
    if apply:
        rest = rest.strip("\n")
        if rest.strip():
            path.write_text(rest + "\n")
        else:
            path.unlink()
    return True


def project_parts(name: str, doc: dict, apply: bool, purge: bool) -> list[str]:
    """What is (or would be) taken out of one project."""
    import registry
    import runtime_ports
    ws = registry.dirs(doc)["workspace"]
    if not ws or not ws.is_dir():
        return []
    done = []
    for rname in runtime_ports.installed():
        try:
            rt = runtime_ports.load(rname, ports=("instructions",))
            f = rt.instructions_file(ws)
            if strip_block(f, apply):
                done.append(_("the platform's block in {file}", file=f.name))
        except runtime_ports.Unsupported:
            pass
        try:
            rt = runtime_ports.load(rname, ports=("lifecycle",))
            remove = getattr(rt, "remove_lifecycle", None)
            if remove is None:
                done.append(_("{runtime}'s hooks for agent-hub stay: it can't remove them; remove them by hand",
                              runtime=rname))
            elif remove(ws, check=not apply):
                done.append(_("{runtime}'s hooks for agent-hub", runtime=rname))
        except runtime_ports.Unsupported:
            pass
    rps = _load("render-project-settings")
    if (rps.RENDERED / f"{name}.json").exists():
        done.append(_("the sandbox and rules agent-hub wrote"))
        if apply:
            rps.unapply(name, doc)
    for _op, hook in rps.gate(name, doc, report=not apply, quiet=True, remove_all=True):
        done.append(_("git hook {hook}", hook=hook))
    if purge and (ws / ".agent-memory").exists():
        done.append(_("its notes (.agent-memory)"))
        if apply:
            import project_git
            project_git.job(name, ws, "worktree-remove", str(ws), str(ws / ".agent-memory"), "--force")
            project_git.job(name, ws, "branch-delete", str(ws), "agent-memory")
    return done


def purge_paths() -> list[pathlib.Path]:
    """Data that only --purge removes: the instance directory, the memory repos and copies of the connected projects,
    the bus."""
    out = []
    home = hubconf.instance_home()
    if home and home.is_dir():
        out.append(home)
    for name in sorted(hubconf.projects() or {}):
        for p in (hubconf.p("git_origin", "~/git-origin") / f"{name}-memory.git",
                  hubconf.p("memory_copies", "~/.memory-copies") / name):
            if p.is_dir() and p not in out:
                out.append(p)
    if hubconf.BUS_DIR.is_dir():
        out.append(hubconf.BUS_DIR)
    return out


def cli_wrapper() -> pathlib.Path | None:
    f = pathlib.Path.home() / ".local" / "bin" / "agenthub"
    try:
        return f if f.is_file() and not f.is_symlink() and str(hubconf.MAIN / "bin" / "agenthub") in f.read_text() else None
    except OSError:
        return None


def mcp_tools() -> tuple[list[tuple[str, list[str]]], object]:
    """(label, unregister command) for each AI app that has one and is installed, and the environment to run it in."""
    spec = importlib.util.spec_from_loader("agenthub_cli", importlib.machinery.SourceFileLoader("agenthub_cli",
                                                                                                str(BIN / "agenthub")))
    ah = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ah)
    return [(info["label"], info["mcp_unregister"]) for info in ah.agent_presets().values()
            if info.get("mcp_unregister") and ah.agent_found(info)], ah.agent_env


def main(argv: list[str]) -> int:
    flags = set(argv)
    unknown = flags - {"--apply", "--purge", "--yes", "-h", "--help"}
    if unknown or flags & {"-h", "--help"}:
        print(_(__doc__), file=sys.stderr if unknown else sys.stdout)
        return 2 if unknown else 0
    apply, purge, yes = "--apply" in flags, "--purge" in flags, "--yes" in flags
    if apply and hubconf.ROOT != hubconf.MAIN:
        print(_("agenthub uninstall: run it from the hub's main checkout ({main}), not a linked worktree; nothing was "
                "done", main=hubconf.MAIN), file=sys.stderr)
        return 2
    import runtime_ports
    projects = {n: d for n, d in sorted((hubconf.projects() or {}).items()) if n != hubconf.hub_project()}
    core = core_units()
    rs = _load("render-services")
    services = rs.uninstall(apply=False)
    per_project = {n: project_parts(n, d, apply=False, purge=purge) for n, d in projects.items()}
    tools, agent_env = mcp_tools()
    user = [label for label, _cmd in tools]
    runtime_user = [line for name in runtime_ports.installed()
                    for line in (getattr(runtime_ports._module(name), "user_cleanup", lambda apply: [])(False))]
    wrapper = cli_wrapper()
    data = purge_paths() if purge else []

    print(_("agenthub uninstall {what}:", what=_("removes") if apply else _("would remove (nothing is done without "
                                                                              "--apply)")))
    for label, _undo in core:
        print("  " + _("service {label}", label=label))
    for label in services:
        print("  " + _("service {label}", label=label))
    for name, parts in per_project.items():
        for part in parts:
            print("  " + _("{project}: {part}", project=name, part=part))
    for label in user:
        print("  " + _("agent-hub in {tool}'s tools (asked first)", tool=label))
    for line in runtime_user:
        print("  " + _("{what} (asked first)", what=line))
    if wrapper:
        print("  " + str(wrapper))
    for p in data:
        print("  " + _("{path} (notes and data: can't be brought back)", path=p))
    kept = [] if purge else [hubconf.instance_home(), hubconf.p("git_origin", "~/git-origin")]
    if kept:
        print(_("Kept: your settings, notes and keys ({paths}) and each project's .agent-memory. --purge removes them "
                "too.", paths=", ".join(str(k) for k in kept if k)))
    print(_("Never touched: your projects' code, nvm, node, the AI agents, uv, git."))
    if not apply:
        return 0

    if not yes and not init_dialogue.Terminal().consent("uninstall", _("Remove all of the above?")):
        print(_("Nothing was done."))
        return 1
    upgrade = _load("upgrade")
    fd = upgrade.scheduler_lock()
    if fd is None:
        print(_("agenthub uninstall: a scheduler tick is running; try again in a minute (nothing was done)"),
              file=sys.stderr)
        return 1
    try:
        for label, undo in core:
            undo()
            print(_("✓ {label} stopped and removed", label=label))
        for label in rs.uninstall(apply=True):
            print(_("✓ {label} stopped and removed", label=label))
        for name, doc in projects.items():
            for part in project_parts(name, doc, apply=True, purge=purge):
                print(_("✓ {project}: {part}", project=name, part=part))
        # user level: the owner's own yes at the terminal, each time; --yes doesn't stand in for it (AGENTS.md)
        term = init_dialogue.Terminal()
        for label, cmd in tools:
            if term.consent("mcp_unregister", _("Remove agent-hub from {tool}'s tools (a setting for your user)?",
                                                 tool=label)):
                r = subprocess.run(cmd, env=agent_env(), capture_output=True, text=True)
                print(_("✓ removed from {tool}", tool=label) if r.returncode == 0 else
                      _("{tool}: {error}", tool=label, error=(r.stderr or r.stdout).strip()[:200]))
            else:
                print(_("Left in {tool}: remove it later with {command}", tool=label, command=" ".join(cmd)))
        for name in runtime_ports.installed():
            fn = getattr(runtime_ports._module(name), "user_cleanup", None)
            if fn and fn(False) and term.consent("user_cleanup", _("Remove {what} (a setting for your user)?",
                                                                    what="; ".join(fn(False)))):
                for line in fn(True):
                    print(_("✓ {what} removed", what=line))
        if wrapper:
            wrapper.unlink(missing_ok=True)
            print(_("✓ {path} removed", path=wrapper))
        for p in data:
            shutil.rmtree(p, ignore_errors=True)
            print(_("✓ {path} removed", path=p))
        if purge:
            hubconf.POINTER.unlink(missing_ok=True)
    finally:
        os.close(fd)
    print(_("Last step, yours: remove the hub folder with: rm -rf {hub}", hub=hubconf.MAIN))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
