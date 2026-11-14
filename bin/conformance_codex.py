"""Codex's conformance checks (docs/design/runtime-versions.md section 2): an adapter, like runtime_codex.py, so it holds
Codex's syntax; bin/conformance.py runs CHECKS against a binary, each in its own throwaway world.

Only the session side the per-version gate needs so far (docs/design/runtime-codex.md, Step 4 r2, S9): whether Codex's
own git in a session's folder runs what the folder's git config names (B1), and whether a permission profile's deny
globs keep a session's tools off every control path (what Step 4's renderer rests on). Step 1 adds the checks of the
platform's own runs (agent, completion, the tool list) here.

How a check meets the tool: no model call and no login. enter(world) points CODEX_HOME at the world's tool folder (a
home of its own, nothing of the user's), keeps git off the user's and the system's config, and puts a git that logs its
calls first on PATH, so a check can tell "Codex ran no git" from "its git ran nothing".
"""
from __future__ import annotations

import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import i18n  # noqa: E402
import runtime_codex as rx  # noqa: E402
from runtime_ports import Check, Outcome  # noqa: E402

_ = i18n.domain(__file__)
RUN_S = 120
# the folder-git canary's drivers, one per config source git reads in a repo's folder: the repo's config, a file it
# includes, the worktree's own config (extensions.worktreeConfig) and a long-running process driver; each file a driver
# is chosen for is staged, then its stat moved, so `git status` hashes it through the driver
DRIVERS = {"brunna-c1": "config", "brunna-c2": "include", "brunna-c3": "worktree", "brunna-c4": "process"}

WRAPPER = """#!{python}
# a git that logs each call (folder, arguments, exit code, done) and runs the real one, not replacing itself: the log
# says whether the call completed
import json, os, subprocess, sys
r = subprocess.run([{real!r}, *sys.argv[1:]])
with open({log!r}, "a") as f:
    f.write(json.dumps({{"cwd": os.getcwd(), "argv": sys.argv[1:], "exit": r.returncode, "done": True}}) + "\\n")
sys.exit(r.returncode)
"""


# ---------- the world ----------

def enter(world) -> None:
    """In the check's process: the environment every run there inherits."""
    real = shutil.which("git", path=os.environ.get("PATH", ""))
    if not real:
        raise RuntimeError(_("no git on PATH"))
    bindir = pathlib.Path(world.root) / "bin"
    bindir.mkdir(mode=0o700, exist_ok=True)
    log = pathlib.Path(world.root) / "git-calls"
    (bindir / "git").write_text(WRAPPER.format(python=sys.executable, real=real, log=str(log)))
    (bindir / "git").chmod(0o700)
    os.environ.update({"CODEX_HOME": str(world.tool), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                       "PATH": f"{bindir}:{os.environ.get('PATH', '')}"})


def _codex(args, *, cwd, timeout=RUN_S) -> subprocess.CompletedProcess:
    return subprocess.run([rx.binary(), *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL, env=dict(os.environ))


def _git(*args, cwd) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=60,
                   env={**os.environ, "GIT_AUTHOR_NAME": "c", "GIT_AUTHOR_EMAIL": "c@c", "GIT_COMMITTER_NAME": "c",
                        "GIT_COMMITTER_EMAIL": "c@c"})


def subcommand(argv: list[str]) -> str | None:
    """git's subcommand in a call's arguments: the first word that isn't a global option or its value."""
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-c", "-C", "--git-dir", "--work-tree", "--namespace", "--exec-path"):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return a
    return None


def _calls(log: pathlib.Path) -> list[dict]:
    out = []
    for line in log.read_text(errors="replace").splitlines() if log.exists() else []:
        try:
            got = json.loads(line)
        except ValueError:
            continue
        if isinstance(got, dict) and isinstance(got.get("argv"), list):
            out.append(got)
    return out


def _in_folder(call: dict, project: pathlib.Path) -> bool:
    """The call ran in the project's folder (its working directory, or the -C it names)."""
    argv, where = call["argv"], call.get("cwd") or ""
    for i, a in enumerate(argv[:-1]):
        if a == "-C":
            where = os.path.join(where, argv[i + 1])
    return os.path.realpath(where) == str(project)


# ---------- the checks ----------

def _plant(project: pathlib.Path, root: pathlib.Path) -> dict[str, pathlib.Path]:
    """The repo with one canary driver per config source (DRIVERS), each touching its own marker: {driver: marker}."""
    _git("init", "-q", cwd=project)
    markers = {d: root / f"ran-{d}" for d in DRIVERS}
    # hashed in this order: the process driver last (one that does not speak its protocol makes git stop)
    files = {d: f"{i}-{kind}.txt" for i, (d, kind) in enumerate(DRIVERS.items(), 1)}
    for f in files.values():
        (project / f).write_text("canary\n")
    _git("add", *files.values(), cwd=project)
    git = project / ".git"
    run = {d: f"touch {shlex.quote(str(m))}; cat" for d, m in markers.items()}
    (git / "included.cfg").write_text(f'[filter "brunna-c2"]\n\tclean = {json.dumps(run["brunna-c2"])}\n')
    (git / "config.worktree").write_text(f'[filter "brunna-c3"]\n\tclean = {json.dumps(run["brunna-c3"])}\n')
    with open(git / "config", "a") as f:
        f.write("[core]\n\trepositoryformatversion = 1\n[extensions]\n\tworktreeConfig = true\n"
                f'[include]\n\tpath = {json.dumps(str(git / "included.cfg"))}\n'
                f'[filter "brunna-c1"]\n\tclean = {json.dumps(run["brunna-c1"])}\n'
                f'[filter "brunna-c4"]\n\tprocess = {json.dumps(f"touch {shlex.quote(str(markers["brunna-c4"]))}")}\n')
    (project / ".gitattributes").write_text("".join(f"{f} filter={d}\n" for d, f in files.items()))
    for f in files.values():
        st = (project / f).stat()
        os.utime(project / f, (st.st_atime + 5, st.st_mtime + 5))
    return markers


def session_folder_git(world) -> Outcome:
    """B1: Codex runs git itself in a session's folder, outside its tool sandbox, as the user. No driver a sandboxed
    project can name (it writes its own .git) may run from it: one per config source git reads there (DRIVERS). Each
    file a driver is chosen for has its stat moved with its content as staged, so `git status` hashes it through the
    driver. `codex debug prompt-input` builds a session's first context without a model call, and that runs Codex's git
    (measured 11-14 on 0.167; it also opens an unauthenticated connection to the model provider, which carries nothing).
    It passes only when a `status` ran to the end in the folder and no driver ran."""
    project, root = pathlib.Path(world.project).resolve(), pathlib.Path(world.root).resolve()
    markers = _plant(project, root)
    log = root / "git-calls"
    log.unlink(missing_ok=True)
    try:
        _codex(["debug", "prompt-input", "hi"], cwd=project)
    except subprocess.TimeoutExpired:
        return Outcome("error", _("no answer in {s} s", s=RUN_S))
    ran = [DRIVERS[d] for d, m in markers.items() if m.exists()]
    if ran:
        return Outcome("fail", _("Codex's own git ran the folder's git drivers outside its sandbox: {kinds} "
                                 "(runtime-codex.md, B1)", kinds=", ".join(ran)))
    statuses = [c for c in _calls(log) if subcommand(c["argv"]) == "status" and _in_folder(c, project)]
    if not any(c.get("done") for c in statuses):
        return Outcome("error", _("tests nothing: no git status of Codex's ran to the end in the folder"))
    return Outcome("pass", _("Codex's own git status ran in the folder, and none of its drivers ran ({kinds})",
                             kinds=", ".join(DRIVERS.values())))


# (what it tries, the shell command, the path that is there when it landed): every way a session's tools could make a
# control folder (CONTROL: .codex, .agents), each run on its own, its folders made by the command itself
PROBES = (
    ("a top .codex", "mkdir -p .codex && printf x > .codex/config.toml", ".codex/config.toml"),
    ("a nested .codex", "mkdir -p sub/.codex && printf x > sub/.codex/config.toml", "sub/.codex/config.toml"),
    ("a top .agents skill", "mkdir -p .agents/skills/c && printf x > .agents/skills/c/SKILL.md",
     ".agents/skills/c/SKILL.md"),
    ("a folder moved to the top .codex", "mkdir -p m && printf x > m/config.toml && mv m .codex", ".codex/config.toml"),
    ("a folder moved to a nested .agents", "mkdir -p sub m && printf x > m/f && mv m sub/.agents", "sub/.agents/f"),
    ("a link named .codex", "mkdir -p real sub && printf x > real/config.toml && ln -s ../real sub/.codex",
     "sub/.codex"),
)
RAN = "ran"   # an allowed write each probe makes last: the command ran under the profile


def _empty(project: pathlib.Path) -> None:
    for p in list(project.iterdir()):
        shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()


def _try_writes(project: pathlib.Path, profile: str) -> tuple[list[str], list[str]]:
    """Each probe run alone under this profile through `codex sandbox` (no model): (the ones that landed, the ones
    whose command didn't run under it)."""
    landed, unrun = [], []
    for what, command, there in PROBES:
        _empty(project)
        _codex(["sandbox", "-P", profile, "--", "/bin/sh", "-c", f"{command} 2>/dev/null; printf x > {RAN}"],
               cwd=project)
        if os.path.lexists(project / there):
            landed.append(what)
        if not (project / RAN).exists():
            unrun.append(what)
    _empty(project)
    return landed, unrun


def _toml_key(k: str) -> str:
    return json.dumps(k, ensure_ascii=False)   # a TOML basic string: JSON's escapes are TOML's


def control_globs(world) -> Outcome:
    """A permission profile with runtime_codex.profile_control_entries keeps a session's tools off every control path:
    each of PROBES (made at the top or nested, moved into place, a link of the name) is refused; with those entries left
    out each lands (the positive control)."""
    project = pathlib.Path(world.project).resolve()
    entries = rx.profile_control_entries(project)
    lines = ["[permissions.brunna-guarded]", 'extends = ":workspace"', "", "[permissions.brunna-guarded.filesystem]"]
    lines += [f"{_toml_key(k)} = {_toml_key(v)}" for k, v in entries.items()]
    lines += ["", "[permissions.brunna-open]", 'extends = ":workspace"', ""]
    (pathlib.Path(world.tool) / "config.toml").write_text("\n".join(lines) + "\n")
    try:
        landed, unrun = _try_writes(project, "brunna-guarded")
        if unrun:
            return Outcome("error", _("codex sandbox didn't run the probe under the profile: {paths}",
                                      paths=", ".join(unrun)))
        if landed:
            return Outcome("fail", _("written under the profile: {paths}", paths=", ".join(landed)))
        opened, unrun = _try_writes(project, "brunna-open")
    except subprocess.TimeoutExpired:
        return Outcome("error", _("no answer in {s} s", s=RUN_S))
    missing = [what for what, _c, _t in PROBES if what not in opened]
    if missing or unrun:
        return Outcome("error", _("tests nothing: {paths} not written without the entries either",
                                  paths=", ".join(dict.fromkeys(missing + unrun))))
    return Outcome("pass", "")


CHECKS = [
    # the session side: the per-version gate reads these (a sandboxed project's Codex session whose version failed one)
    Check("session-folder-git", "sessions", "safety", session_folder_git, timeout_s=RUN_S + 60, side="session"),
    Check("control-globs", "control", "safety", control_globs, timeout_s=2 * 6 * RUN_S + 60, side="session"),
    Check("lifecycle-hooks", "lifecycle", "function", None,
          "the hooks fire and their additionalContext reaches the model (measured by hand 11-14, runtime-codex.md): a "
          "check needs a model call, so a login in the world (Step 1's login rule)", side="session"),
]
