#!/usr/bin/env python3
"""Move an onboarded project's development environment from the laptop to ~/workspace/<name> on the hub machine.

Afterwards sessions are opened on the hub machine from Claude Desktop over SSH: the session runs on the hub machine,
the laptop is only the remote control, and the laptop disconnecting/running out of battery/closing doesn't interrupt
running work (verified in a trial).

Runs on the laptop. Dry-run by default: lists the check results, the untracked files to copy (with sizes), and the
dependencies to rebuild on the hub machine; only --apply acts. The folder on the laptop is left untouched as a backup:
whether to clear it is the owner's decision, and this deletes nothing.

Steps:
  1. Preflight checks (stop if any fails): registered in the project registry, not native, no uncommitted changes,
     every local branch pushed, no process with its working directory in the project folder (close that project's
     sessions first), ~/workspace/<name> doesn't exist on the hub machine yet
  2. One more memory sync + push, so the hub machine's bare repo is up to date
  3. recover-project.sh <name> ~/workspace/<name> on the hub machine (clone + attach the existing agent-memory branch)
  4. rsync what's untracked and can't be rebuilt (.env, data/, CLAUDE.local.md, the local .claude/ settings, etc.);
     dependencies, build output and caches aren't copied
  4b. Claude Code's auto memory, the project-scope MCP server settings and .git/info/exclude (none in the repo) move
      over too
  5. Rerun sync-local-setup.sh on the hub machine: align the managed blocks and hooks, keeping the other copied content
  6. Directories with .venv/venv/node_modules on the laptop are rebuilt on the hub machine from their lockfiles (a
     failure is only reported, not fatal)

Usage: migrate-to-mini.py <name> [--apply] [--allow-local-branches] [--skip <relative path>]...
  --skip: an untracked file not to move this time (e.g. a large experiment database that can stay on the laptop as a
  backup); can be given several times
"""
from __future__ import annotations  # the system python3 (3.9) must be able to import this too

import argparse
import datetime
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import hublib  # noqa: E402
import i18n  # noqa: E402
import runtime_claude  # noqa: E402  # this moves Claude Code's auto memory, which is Claude-specific anyway

_ = i18n.domain(__file__)
MINI = hubconf.host("server")  # the machine that runs the hub (hub.json hosts.server)
MINI_PATH_PREFIX = "export PATH=/opt/homebrew/bin:/opt/homebrew/opt/rustup/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH; "
# dependencies, build output, caches: not copied, rebuilt on the hub machine when needed
REBUILDABLE = {
    "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "target", "dist", "build", ".next", ".turbo", ".parcel-cache", ".svelte-kit", ".astro",
    ".godot", "coverage", ".DS_Store", ".husky",
}
REBUILDABLE_SUFFIX = (".pyc", ".egg-info", ".egg-info/", ".tsbuildinfo")
# what recover-project.sh creates/aligns itself
MANAGED = {".agent-memory"}


def fail(msg: str) -> None:
    sys.exit(f"migrate-to-mini: {msg}")


def run(cmd: list[str], cwd: str | None = None, check: bool = True) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        fail(_("{cmd} failed:\n{stderr}", cmd=' '.join(cmd), stderr=r.stderr.strip()))
    return r.stdout


def mini(script: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", MINI, MINI_PATH_PREFIX + script],
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        fail(_("failed on the hub machine: {script}\n{output}", script=script, output=r.stdout + r.stderr))
    return r


def manifest_row(name: str) -> dict:
    """path is the location on the laptop: since 2026-10-15 the registry records the hub machine's location, and the
    laptop's old location is in legacy-paths.tsv (the first entry)."""
    paths = hubconf.roster().get(name)
    if paths is None:
        fail(_("'{name}' is not in the project registry (projects/)", name=name))
    mbp = [p for p in hublib.legacy_paths(name) if p.startswith(hublib.MBP_HOME + "/")]
    if not mbp:
        fail(_("legacy-paths.tsv has no location on the laptop for '{name}'", name=name))
    return {"path": mbp[0], "origin": paths.get("origin") or "(native)"}


def is_rebuildable(rel: str) -> bool:
    # it counts if any level is build output/dependencies (e.g. for build/web/ the last level is web)
    parts = rel.rstrip("/").split("/")
    return any(part in REBUILDABLE for part in parts) or rel.endswith(REBUILDABLE_SUFFIX)


def ignored_entries(path: str) -> list[str]:
    out = run(["git", "status", "--ignored", "--porcelain", "-z"], cwd=path)
    return [e[3:] for e in out.split("\0") if e.startswith("!! ")]


def du_kb(path: str, rel: str) -> int:
    r = subprocess.run(["du", "-sk", os.path.join(path, rel)], capture_output=True, text=True)
    return int(r.stdout.split()[0]) if r.stdout else 0


def human(kb: int) -> str:
    for unit in ("K", "M", "G"):
        if kb < 1024:
            return f"{kb:.0f}{unit}"
        kb /= 1024
    return f"{kb:.1f}T"


def preflight(name: str, path: str, origin: str, allow_local_branches: bool) -> list[str]:
    """Return warnings; fatal problems fail right away."""
    warnings = []
    if origin == "(native)":
        fail(_("'{name}' is a native project (no GitHub remote): recover-project.sh cannot clone it on the hub machine",
               name=name))
    if not os.path.isdir(path):
        fail(_("{path} does not exist", path=path))
    dirty = run(["git", "status", "--porcelain"], cwd=path).strip()
    if dirty:
        fail(_("{path} has uncommitted changes: ask that project's session to commit+push first:\n{dirty}",
               path=path, dirty=dirty))
    branches = run(["git", "for-each-ref", "--format=%(refname:short)\t%(upstream:short)\t%(upstream:track)",
                    "refs/heads"], cwd=path)
    unpushed = []
    for line in branches.splitlines():
        b, up, track = (line.split("\t") + ["", ""])[:3]
        if b == "agent-memory":
            continue
        if not up or "ahead" in track or "gone" in track:
            unpushed.append(_("{branch} ({state})", branch=b, state=_("no upstream") if not up else track))
    if unpushed:
        branches = ", ".join(unpushed)
        if not allow_local_branches:
            fail(_("local branches are not pushed, so the hub machine cannot clone them: {branches}\n"
                   "Push them first (or confirm they are not needed), or add --allow-local-branches to leave them on "
                   "the laptop",
                   branches=branches))
        warnings.append(_("Local branches are not pushed, so the hub machine cannot clone them: {branches} "
                          "(--allow-local-branches: they stay on the laptop)", branches=branches))
    if run(["git", "stash", "list"], cwd=path).strip():
        warnings.append(_("There is a git stash: it does not move (it stays on the laptop)"))
    users = run(["lsof", "+d", path], check=False)
    procs = sorted({f"{f[0]}({f[1]})" for f in (l.split() for l in users.splitlines()[1:]) if f})
    if procs:
        fail(_("processes still have their working directory in {path}: {procs}; close that project's agent session / "
               "terminal tab first", path=path, procs=' '.join(procs)))
    if mini(f"test -e ~/workspace/{shlex.quote(name)}", check=False).returncode == 0:
        fail(_("~/workspace/{name} already exists on the hub machine: already moved? Check, then handle it by hand",
               name=name))
    return warnings


def dep_dirs(path: str, entries: list[str]) -> list[tuple[str, str]]:
    """Directories on the laptop where dependencies were actually built → (relative directory, kind)."""
    out = []
    for e in entries:
        rel = e.rstrip("/")
        base = rel.rsplit("/", 1)[-1]
        parent = rel.rsplit("/", 1)[0] if "/" in rel else "."
        if base in (".venv", "venv"):
            out.append((parent, base))
        elif base == "node_modules":
            out.append((parent, "node"))
    return out


def python_version(path: str, parent: str, venv: str) -> str | None:
    cfg = pathlib.Path(path, parent, venv, "pyvenv.cfg")
    if cfg.exists():
        m = re.search(r"^version(?:_info)?\s*=\s*(\d+\.\d+)", cfg.read_text(), re.M)
        if m:
            return m.group(1)
    return None


def rebuild_cmd(path: str, parent: str, kind: str) -> str | None:
    d = pathlib.Path(path, parent)
    """Return the command to run in the project root."""
    if kind in (".venv", "venv"):
        py = python_version(path, parent, kind)
        pyarg = f" --python {py}" if py else ""
        cd = f"cd {shlex.quote(parent)} && " if parent != "." else ""
        if (d / "uv.lock").exists():
            return f"{cd}uv sync --frozen --all-extras{pyarg}" if kind == ".venv" else None
        if (d / "requirements.txt").exists():
            return f"{cd}uv venv {kind}{pyarg} && VIRTUAL_ENV={kind} uv pip install -r requirements.txt"
        if (d / "pyproject.toml").exists():
            return f"{cd}uv venv {kind}{pyarg} && VIRTUAL_ENV={kind} uv pip install -e ."
        return None
    # npm/pnpm workspaces: the lockfile may be in a parent directory; installing once there covers every workspace
    root = pathlib.Path(path)
    while True:
        rel = d.relative_to(root).as_posix()
        if (d / "pnpm-lock.yaml").exists():
            return f"cd {shlex.quote(rel)} && pnpm install --frozen-lockfile" if rel != "." else "pnpm install --frozen-lockfile"
        if (d / "package-lock.json").exists():
            return f"cd {shlex.quote(rel)} && npm ci" if rel != "." else "npm ci"
        if d == root:
            return None
        d = d.parent


# these files in the project → the hub machine needs the matching toolchain (dependencies aren't copied, they're
# rebuilt on the hub machine)
TOOLCHAINS = {"Cargo.toml": "cargo", "project.godot": "godot", "go.mod": "go"}


def missing_toolchains(path: str) -> list[str]:
    files = {f.rsplit("/", 1)[-1] for f in run(["git", "ls-files"], cwd=path).splitlines()}
    need = sorted({tool for marker, tool in TOOLCHAINS.items() if marker in files})
    return [t for t in need if mini(f"command -v {t}", check=False).returncode != 0]


def copy_mcp_servers(name: str, path: str) -> None:
    """Project-scope (local scope) MCP server settings live in the laptop's ~/.claude-personal/.claude.json, keyed by
    the laptop's paths, not in the repo, so a clone doesn't bring them (e.g. the project's read-only database
    connection, the project's own MCP). After converting the paths, they're added to that folder on the hub machine
    with `claude mcp add-json`. The settings may hold credentials: they're passed only through stdin, never printed.
    When a stdio command can't be found on the hub machine, the same-named tool at the hub machine's location is used;
    if it still can't be found (e.g. the project's own executable that has to be built first), a reminder is given."""
    cfg = hublib.HOME / ".claude-personal" / ".claude.json"
    if not cfg.exists():
        return
    projects = json.loads(cfg.read_text()).get("projects", {})
    old_paths = [path] + [p for p in hublib.legacy_paths(name) if p != path]
    target = f"{hublib.MINI_WORKSPACE}/{name}"
    for old in old_paths:
        for server, conf in (projects.get(old, {}).get("mcpServers") or {}).items():
            text = json.dumps(conf).replace(old, target).replace(f"{hublib.MBP_HOME}/", f"{hublib.MINI_HOME}/")
            conf2 = json.loads(text)
            note = ""
            cmd = conf2.get("command")
            if cmd and mini(f"test -x {shlex.quote(cmd)}", check=False).returncode != 0:
                found = mini(f"command -v {shlex.quote(os.path.basename(cmd))}", check=False).stdout.strip()
                if found:
                    conf2["command"] = found
                else:
                    note = _(" (the hub machine has no {cmd} yet: build/install it first)", cmd=cmd)
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", MINI, MINI_PATH_PREFIX +
                                f"cd {target} && read -r J && claude mcp add-json {shlex.quote(server)} \"$J\""],
                               input=json.dumps(conf2) + "\n", capture_output=True, text=True)
            ok = r.returncode == 0 or "already exists" in (r.stdout + r.stderr)
            error = (r.stderr or r.stdout).strip()[:200]
            result = _("added on the hub machine") if ok else _("failed: {error}", error=error)
            print(_("  MCP {server}: {result}{note}", server=server, result=result, note=note))


def merge_info_exclude(name: str, path: str) -> None:
    """Ignore rules the project/tools added to the laptop repo's .git/info/exclude (e.g. impeccable's hook.cache.json)
    don't come along with a clone, and without them those files show up as untracked on the hub machine. Adds the
    lines the hub machine lacks."""
    src = pathlib.Path(run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           cwd=path).strip(), "info", "exclude")
    if not src.exists():
        return
    lines = [l for l in src.read_text().splitlines() if l.strip() and not l.startswith("#")]
    if not lines:
        return
    target = f"~/workspace/{name}"
    have = set(mini(f"cat {target}/.git/info/exclude 2>/dev/null || true").stdout.splitlines())
    missing = [l for l in lines if l not in have]
    if missing:
        body = "\n".join(missing)
        mini(f"mkdir -p {target}/.git/info && printf '%s\\n' {shlex.quote(body)} >> {target}/.git/info/exclude")
        print(_("  info/exclude: added {count} ignore {count:rule|rules}", count=len(missing)))


def copy_auto_memory(name: str, path: str) -> None:
    """Claude Code's per-project auto memory (laptop: ~/.claude-personal/projects/<slug>/memory/) isn't in the repo;
    move it to the hub machine's ~/.claude/projects/<slug of the hub machine's path>/memory/ (the hub machine has only
    the personal account, hence ~/.claude). For renamed projects, the old paths' memory comes along too. Files that
    already exist on the hub machine aren't overwritten."""
    old_paths = [path] + [p for p in hublib.legacy_paths(name) if p != path]
    dest = f".claude/projects/{runtime_claude.slug_for_path(f'{hublib.MINI_WORKSPACE}/{name}')}/memory/"
    for old in old_paths:
        src = hublib.HOME / ".claude-personal" / "projects" / runtime_claude.slug_for_path(old) / "memory"
        if not src.is_dir() or not any(src.iterdir()):
            continue
        mini(f"mkdir -p ~/{dest}")
        r = subprocess.run(["rsync", "-a", "--ignore-existing", f"{src}/", f"{MINI}:{dest}"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            fail(_("auto memory rsync failed: {stderr}", stderr=r.stderr))
        print(_("  auto memory: {src} → the hub machine ~/{dest}", src=src.parent.name, dest=dest))


def missing_logins(path: str) -> list[str]:
    """CLI logins the project uses on the hub machine that an SSH session can't read. Credentials have to be stored in
    files (the keychain can't be read under SSH), e.g. gh has to log in with --insecure-storage. Only checks, never logs
    in on anyone's behalf."""
    out = []
    if mini("gh auth status", check=False).returncode != 0:
        out.append(_("gh on the hub machine is not logged in or cannot read its token: in a terminal on the hub machine "
                     "run {cmd}", cmd="`gh auth login -h github.com --git-protocol ssh --web --insecure-storage`"))
    if os.path.isdir(os.path.join(path, ".vercel")) and \
            mini("vercel whoami", check=False).returncode != 0:
        out.append(_("This project uses Vercel, but vercel is not logged in on the hub machine: in a terminal on the hub "
                     "machine run `vercel login`"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=_(
        "Move an onboarded project's development setup from the laptop to ~/workspace/<name> on the hub machine."))
    ap.add_argument("name")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--allow-local-branches", action="store_true")
    ap.add_argument("--skip", action="append", default=[], help=_("untracked files not to move (relative path)"))
    a = ap.parse_args()

    row = manifest_row(a.name)
    path, target = row["path"], f"~/workspace/{a.name}"
    warnings = preflight(a.name, path, row["origin"], a.allow_local_branches)

    entries = ignored_entries(path)
    skip = {x.rstrip("/") for x in a.skip}
    copy = [e for e in entries if not is_rebuildable(e) and e.rstrip("/") not in MANAGED
            and e.rstrip("/") not in skip]
    left = [e for e in entries if e.rstrip("/") in skip]
    skipped = [e for e in entries if is_rebuildable(e)]
    deps = dep_dirs(path, entries)
    sizes = {e: du_kb(path, e) for e in copy}

    print(_("== {name}: {path} → the hub machine {target}", name=a.name, path=path, target=target))
    for w in warnings:
        print(f"  ! {w}")
    print(_("  Copy (untracked, cannot be rebuilt), {size} in all:", size=human(sum(sizes.values()))))
    for e in copy:
        print(f"    {human(sizes[e]):>6}  {e}")
    if skipped:
        print(_("  Not copied (dependencies/build/cache): {entries}", entries=" ".join(skipped)))
    if left:
        print(_("  Not moved (--skip, kept on the laptop as a backup), {size} in all: {entries}",
                size=human(sum(du_kb(path, e) for e in left)), entries=" ".join(left)))
    plans, seen = [], set()
    for p, k in deps:
        cmd = rebuild_cmd(path, p, k)
        if cmd and cmd in seen:
            continue  # the same workspace root is installed only once
        seen.add(cmd)
        plans.append((p, k, cmd))
    for w in missing_logins(path):
        warnings.append(w)
        print(f"  ! {w}")
    for t in missing_toolchains(path):
        warnings.append(_("The hub machine has no {tool}; this project needs it to build/run. Install it there first",
                          tool=t))
        print(_("  ! The hub machine has no {tool}", tool=t))
    for p, k, cmd in plans:
        print(_("  Rebuild {dir} on the hub machine: {cmd}", dir=f"{p}/{'node_modules' if k == 'node' else k}",
                cmd=cmd or _("(no lockfile found, handle it by hand)")))
    if not a.apply:
        print(_("\n(dry-run; add --apply once you have checked)"))
        return

    print(_("[2/6] Catching up memory sync and push"))
    mem = os.path.join(path, ".agent-memory")
    env = dict(os.environ, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.hooksPath",
               GIT_CONFIG_VALUE_0="/dev/null")
    for script in ("git-sync-memory.sh", "git-safe-push.sh"):
        r = subprocess.run([str(hublib.HUB / "bin/with-lock.sh"), str(hublib.HUB / "bin" / script)],
                           cwd=mem, env=env, capture_output=True, text=True)
        if r.returncode != 0:
            fail(_("{script} failed: {output}", script=script, output=r.stdout + r.stderr))

    print(_("[3/6] Hub machine: recover-project.sh"))
    mini(f"git -C ~/agent-hub pull -q && mkdir -p ~/workspace && "
         f"~/agent-hub/bin/recover-project.sh {shlex.quote(a.name)} {target}")

    if copy:
        print(_("[4/6] rsync {count} {count:entry|entries} ({size})", count=len(copy), size=human(sum(sizes.values()))))
        excludes = [f"--exclude={x}" for x in sorted(REBUILDABLE)] + ["--exclude=*.pyc"]
        r = subprocess.run(["rsync", "-a", "-r", "--from0", "--files-from=-", *excludes,
                            f"{path}/", f"{MINI}:workspace/{a.name}/"],
                           input="\0".join(e.rstrip("/") for e in copy), text=True,
                           capture_output=True)
        if r.returncode != 0:
            fail(_("rsync failed: {stderr}", stderr=r.stderr))

    copy_auto_memory(a.name, path)
    merge_info_exclude(a.name, path)
    copy_mcp_servers(a.name, path)

    print(_("[5/6] Hub machine: sync-local-setup.sh"))
    out = mini(f"cd {target} && ~/agent-hub/bin/sync-local-setup.sh").stdout
    for l in out.splitlines():
        if l.startswith("⚠️"):  # committed files mention this mechanism etc.: for that project's session to handle
            print(f"  ! {l}")

    print(_("[6/6] Hub machine: rebuild dependencies"))
    problems = []
    for p, k, cmd in plans:
        if not cmd:
            problems.append(_("{dir}: no lockfile for {kind}, so it was not rebuilt", dir=p, kind=k))
            continue
        r = mini(f"cd {target} && {cmd}", check=False)
        status = "ok" if r.returncode == 0 else _("failed")
        print(_("  {dir} ({cmd}): {status}", dir=p, cmd=cmd, status=status))
        if r.returncode != 0:
            problems.append(_("{dir}: {cmd} failed\n{output}", dir=p, cmd=cmd, output=(r.stdout + r.stderr)[-800:]))
    dirty = mini(f"cd {target} && git status --porcelain").stdout.strip()
    if dirty:
        problems.append(_("The working copy on the hub machine has unexpected changes (a rebuild changed a lockfile?):\n"
                          "{dirty}", dirty=dirty))

    # leave a marker in the laptop's old copy (inside .git: never committed, invisible to git status): if a session is
    # opened there later, sync-local-setup.sh reminds at SessionStart to open it on the hub machine instead
    common = run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=path).strip()
    pathlib.Path(common, "agent-hub-migrated-to-mini").write_text(
        f"mini:{target}\t{datetime.date.today().isoformat()}\n")

    print(_("\nDone: {name} is at {target} on the hub machine. Open this folder over SSH ({host}) from your coding "
            "agent.", name=a.name, target=target, host=MINI))
    print(_("{path} on the laptop is untouched, kept as a backup (a marker in .git: a session opened there is told to "
            "go to the hub machine).", path=path))
    for p in problems:
        print(f"  ! {p}")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
