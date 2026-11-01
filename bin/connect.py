#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Connect existing folders as projects (docs/design/newcomer-path.md B6): `agenthub connect [<folder>] [--yes]
[--remote] [--dry-run]`.

The user names a folder. What it holds is found by reading files, and the only git call made in it (the
unsaved-changes check) runs with its hooks and fsmonitor off, because the folder isn't trusted yet. Then:
  a plain folder, or one repo       one project
  a monorepo                        one project for the whole repo; whether to split it into its parts later is
                                    asked and recorded (layout_intent: {split: true}) for when projects-and-repos lands
  a folder holding several repos    one project per repo; whether they belong together is asked and recorded
                                    (layout_intent: {span: [...]}) for the same
It never connects a part of a monorepo as a repo of its own. Before each one it says, in plain words, what connecting
does; a repo with unsaved changes is skipped until they're saved. No GitHub repo is created unless --remote is given and
hub.json has github.owner. The connecting itself is bin/onboard-project.sh.

Refused: the home folder and its parents, hidden folders, the hub, the instance directory, anything inside a project
that is already connected. The folder is resolved to its real path first.

  agenthub connect --add-root <folder>   add a folder to hub.json connect.roots: the places from which an agent session
                                         may connect a folder (the MCP server's connect_project, B5). Only the owner
                                         adds roots, here or in init; an agent can't.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)
BIN = pathlib.Path(__file__).resolve().parent

MONOREPO_FILES = ("pnpm-workspace.yaml", "lerna.json", "go.work", "nx.json", "turbo.json", "rush.json")
MANIFESTS = ("package.json", "pyproject.toml", "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "Gemfile")


def sgit(path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    """git in a folder that isn't trusted yet: its hooks and fsmonitor off."""
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=", "-C", str(path), *args],
                          capture_output=True, text=True)


def is_repo(p: pathlib.Path) -> bool:
    return (p / ".git").exists()


def is_monorepo(p: pathlib.Path) -> bool:
    """A repo holding several projects: a workspace file at the top, or two or more parts with their own manifest one
    level down (or under packages/, apps/, services/)."""
    if any((p / f).exists() for f in MONOREPO_FILES):
        return True
    cargo = p / "Cargo.toml"
    if cargo.exists() and "[workspace]" in cargo.read_text(errors="replace"):
        return True
    parts = 0
    for base in (p, p / "packages", p / "apps", p / "services"):
        if base.is_dir():
            for c in base.iterdir():
                if c.is_dir() and not c.name.startswith(".") and any((c / m).exists() for m in MANIFESTS):
                    parts += 1
    return parts >= 2


def layout(p: pathlib.Path) -> tuple[str, list[pathlib.Path]]:
    """(kind, candidates): plain | repo | monorepo → [p]; repos → the repos one level down."""
    if is_repo(p):
        return ("monorepo" if is_monorepo(p) else "repo"), [p]
    repos = sorted(c for c in p.iterdir() if c.is_dir() and not c.name.startswith(".") and is_repo(c))
    if repos:
        return "repos", repos
    return "plain", [p]


def refusal(p: pathlib.Path, root: bool = False) -> str | None:
    """Why this folder can't be connected, or None. root: for a folder of projects (add_root), which may hold the hub or
    the instance (connecting them is still refused) but not be or sit inside them."""
    home = pathlib.Path.home().resolve()
    if p == home or home.is_relative_to(p):
        return _("{path} is your home folder or contains it; name the folder of one project (or of several)", path=p)
    if any(part.startswith(".") for part in p.relative_to(p.anchor).parts):
        return _("{path} is a hidden folder", path=p)
    for what, where in ((_("the hub"), hubconf.MAIN), (_("agent-hub's own settings folder"), hubconf.instance_home())):
        if where and (p == where.resolve() or p.is_relative_to(where.resolve())
                      or (not root and where.resolve().is_relative_to(p))):
            return _("{path} is or holds {what}", path=p, what=what)
    for name, doc in (hubconf.projects() or {}).items():
        ws = ((doc.get("paths") or {}).get("workspace") or "")
        if ws and (p == pathlib.Path(os.path.expanduser(ws)).resolve()
                   or p.is_relative_to(pathlib.Path(os.path.expanduser(ws)).resolve())):
            return _("{path} is already part of the project {name}", path=p, name=name)
    if not p.is_dir():
        return _("{path} is not a folder", path=p)
    return None


def in_roots(given: pathlib.Path, roots: list[pathlib.Path]) -> str | None:
    """For a connect asked from an agent session (B5): why the folder is outside what the owner allowed, or None.
    It must exist, have no symlink anywhere along the path given, and its real path must be strictly inside a root."""
    p = pathlib.Path(os.path.expanduser(str(given)))
    if not p.is_absolute():
        return _("{path} is not an absolute path", path=given)
    for part in [p, *p.parents]:
        if part.is_symlink():
            return _("{path} goes through a symlink ({link}); name the real folder", path=given, link=part)
    if not p.is_dir():
        return _("{path} is not an existing folder", path=given)
    real = p.resolve()
    if not any(real != r and real.is_relative_to(r) for r in roots):
        return _("{path} is not inside a folder you named for projects ({roots}); add one with agenthub connect "
                 "--add-root <folder>", path=real, roots=", ".join(map(str, roots)) or _("none"))
    return None


def add_root(folder: str) -> int:
    p = pathlib.Path(os.path.expanduser(folder)).resolve()
    home = pathlib.Path.home().resolve()
    if p == home or home.is_relative_to(p):
        print(_("agenthub connect: {path} is your home folder or contains it; name the folder that holds your projects",
                path=p), file=sys.stderr)
        return 2
    if (why := refusal(p, root=True)):
        print(_("agenthub connect: {reason}; nothing was done", reason=why), file=sys.stderr)
        return 2
    f = hubconf.config_file("hub.json")
    d = json.loads(f.read_text())
    roots = (d.setdefault("connect", {})).setdefault("roots", [])
    if str(p) not in roots:
        roots.append(str(p))
        f.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
        subprocess.run([str(BIN / "commit-hub-files.sh"), "chore(config): a folder for projects", "hub.json"])
    print(_("✓ projects may be connected from inside {path}", path=p))
    return 0


def ask(question: str, yes: bool, default: bool) -> bool:
    if yes or not sys.stdin.isatty():
        return default
    a = input(f"{question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    return default if not a else a in ("y", "yes")


def explain(p: pathlib.Path) -> None:
    print(_("Connecting {path}:", path=p))
    print("  " + _("agent-hub keeps this project's notes in a separate place, so your files don't change"))
    if not is_repo(p):
        print("  " + _("this folder has no version history yet: it gets one, with a first save of what is in it now"))


def connect_one(p: pathlib.Path, remote: bool, intent: dict | None, dry: bool) -> int:
    if is_repo(p) and sgit(p, "status", "--porcelain").stdout.strip():
        print(_("Skipped {path}: it has unsaved changes. Save (commit) them, then run agenthub connect again.", path=p))
        return 1
    argv = [str(BIN / "onboard-project.sh")]
    if not (remote and hubconf.get("github.owner")):
        argv.append("--no-remote")
    argv.append(str(p))
    if dry:
        print(_("(dry run) would run: {command}", command=" ".join(argv))
              + (f"  layout_intent={json.dumps(intent)}" if intent else ""))
        return 0
    env = dict(os.environ)
    if intent:
        env["ONBOARD_REGISTRY_EXTRA"] = json.dumps({"layout_intent": intent})
    return subprocess.run(argv, env=env).returncode


def main(argv: list[str]) -> int:
    if argv[:1] == ["--add-root"]:
        if len(argv) != 2:
            print(_("agenthub connect --add-root takes one folder"), file=sys.stderr)
            return 2
        return add_root(argv[1])
    flags = {a for a in argv if a.startswith("--")}
    unknown = flags - {"--yes", "--remote", "--dry-run", "--help", "-h"}
    if unknown or {"--help", "-h"} & flags:
        print(_(__doc__), file=sys.stderr if unknown else sys.stdout)
        return 2 if unknown else 0
    args = [a for a in argv if not a.startswith("--")]
    if len(args) > 1:
        print(_("agenthub connect takes one folder"), file=sys.stderr)
        return 2
    yes, remote, dry = "--yes" in flags, "--remote" in flags, "--dry-run" in flags
    folder = pathlib.Path(os.path.expanduser(args[0] if args else ".")).resolve()
    if (why := refusal(folder)):
        print(_("agenthub connect: {reason}; nothing was done", reason=why), file=sys.stderr)
        return 2
    kind, candidates = layout(folder)
    if kind == "repos":
        names = [c.name for c in candidates]
        print(_("{path} holds {n} {n:repo|repos}: {names}. Each becomes a project of its own.", path=folder,
                n=len(names), names=", ".join(names)))
        together = ask(_("Do they belong together as one project? (Recorded now, carried out once agent-hub supports "
                         "a project spanning several repos.)"), yes, False)
        intent = {"span": names} if together else None
    elif kind == "monorepo":
        print(_("{path} holds several parts (a monorepo). It becomes one project now.", path=folder))
        split = ask(_("Split it into its parts as separate projects later? (Recorded now, carried out once agent-hub "
                      "supports that.)"), yes, False)
        intent = {"split": True} if split else None
    else:
        intent = None
    rc = 0
    for c in candidates:
        if (why := refusal(c)):
            print(_("Skipped: {reason}", reason=why))
            rc = 1
            continue
        explain(c)
        if not ask(_("Connect it?"), yes, True):
            continue
        rc = connect_one(c, remote, intent, dry) or rc
    if not dry and rc == 0:
        print(_("Done. Start a new session in each connected folder: hooks and rules load when a session starts."))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
