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
FILTER = "brunna-canary"   # the filter driver the folder-git canary names in the repo's config


# ---------- the world ----------

def enter(world) -> None:
    """In the check's process: the environment every run there inherits."""
    real = shutil.which("git", path=os.environ.get("PATH", ""))
    if not real:
        raise RuntimeError(_("no git on PATH"))
    bindir = pathlib.Path(world.root) / "bin"
    bindir.mkdir(mode=0o700, exist_ok=True)
    log = pathlib.Path(world.root) / "git-calls"
    (bindir / "git").write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(log))}\n"
                                f"exec {shlex.quote(real)} \"$@\"\n")
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


# ---------- the checks ----------

def session_folder_git(world) -> Outcome:
    """B1: Codex runs git itself in a session's folder, outside its tool sandbox, as the user. A clean filter named in
    the repo's config (a sandboxed project can write its own .git) must not run from it. A tracked file whose stat
    changed with its content as staged makes `git status` hash it, which runs the filter. `codex debug prompt-input`
    builds a session's first context without a model call, and that runs Codex's git (measured 11-14 on 0.167)."""
    project, root = pathlib.Path(world.project).resolve(), pathlib.Path(world.root).resolve()
    marker = root / "filter-ran"
    _git("init", "-q", cwd=project)
    (project / "a.txt").write_text("canary\n")
    _git("add", "a.txt", cwd=project)
    with open(project / ".git" / "config", "a") as f:
        f.write(f'[filter "{FILTER}"]\n\tclean = "touch {marker}; cat"\n')
    (project / ".gitattributes").write_text(f"*.txt filter={FILTER}\n")
    st = (project / "a.txt").stat()
    os.utime(project / "a.txt", (st.st_atime + 5, st.st_mtime + 5))
    calls = root / "git-calls"
    calls.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
    try:
        _codex(["debug", "prompt-input", "hi"], cwd=project)
    except subprocess.TimeoutExpired:
        return Outcome("error", _("no answer in {s} s", s=RUN_S))
    if marker.exists():
        return Outcome("fail", _("Codex's own git ran the folder's git filter, outside its sandbox (runtime-codex.md, "
                                 "B1)"))
    ran = calls.read_text(errors="replace").splitlines() if calls.exists() else []
    if not any(" status" in f" {line}" for line in ran):
        return Outcome("error", _("tests nothing: Codex ran no git status in the folder"))
    return Outcome("pass", _("Codex's own git ran in the folder and the filter didn't run"))


PROBES = (".codex/config.toml", "sub/.codex/config.toml", ".agents/skills/c/SKILL.md")


def _try_writes(project: pathlib.Path, profile: str) -> tuple[list[str], subprocess.CompletedProcess]:
    """Each probe written under this profile through `codex sandbox` (no model): the ones that landed."""
    script = "; ".join(f"mkdir -p {shlex.quote(str(pathlib.Path(p).parent))} 2>/dev/null; "
                       f"printf x > {shlex.quote(p)} 2>/dev/null" for p in PROBES)
    script += "; mkdir -p moved 2>/dev/null; mv moved sub/.agents 2>/dev/null; printf x > ran 2>/dev/null"
    r = _codex(["sandbox", "-P", profile, "--", "/bin/sh", "-c", script], cwd=project)
    landed = [p for p in PROBES if (project / p).exists()] + (["sub/.agents"] if (project / "sub" / ".agents").exists()
                                                             else [])
    if (project / "ran").exists():   # the command ran under the profile (an allowed write landed)
        landed.append("ran")
    for p in [*PROBES, "sub/.agents", "moved", "ran"]:
        shutil.rmtree(project / p, ignore_errors=True) if (project / p).is_dir() else (project / p).unlink(missing_ok=True)
    for d in (".codex", "sub/.codex", ".agents/skills/c", ".agents/skills", ".agents", "sub"):
        try:
            (project / d).rmdir()
        except OSError:
            pass
    return landed, r


def control_globs(world) -> Outcome:
    """A permission profile with runtime_codex.profile_control_entries keeps a session's tools off every control path:
    making .codex/ and .agents/ at the top and nested, and moving a folder into place, are refused; with those entries
    left out they land (the positive control)."""
    project = pathlib.Path(world.project).resolve()
    entries = rx.profile_control_entries(project)
    lines = ["[permissions.brunna-guarded]", 'extends = ":workspace"', "", "[permissions.brunna-guarded.filesystem]"]
    lines += [f'"{k}" = "{v}"' for k, v in entries.items()]
    lines += ["", "[permissions.brunna-open]", 'extends = ":workspace"', ""]
    (pathlib.Path(world.tool) / "config.toml").write_text("\n".join(lines) + "\n")
    try:
        landed, r = _try_writes(project, "brunna-guarded")
        if "ran" not in landed:
            return Outcome("error", _("codex sandbox didn't run the probe under the profile (exit {code})",
                                      code=r.returncode))
        if (wrote := [p for p in landed if p != "ran"]):
            return Outcome("fail", _("written under the profile: {paths}", paths=", ".join(wrote)))
        opened, _r = _try_writes(project, "brunna-open")
    except subprocess.TimeoutExpired:
        return Outcome("error", _("no answer in {s} s", s=RUN_S))
    missing = [p for p in [*PROBES, "sub/.agents", "ran"] if p not in opened]
    if missing:
        return Outcome("error", _("tests nothing: {paths} not written without the entries either",
                                  paths=", ".join(missing)))
    return Outcome("pass", "")


CHECKS = [
    # the session side: the per-version gate reads these (a sandboxed project's Codex session whose version failed one)
    Check("session-folder-git", "sessions", "safety", session_folder_git, timeout_s=RUN_S + 60, side="session"),
    Check("control-globs", "control", "safety", control_globs, timeout_s=2 * RUN_S + 60, side="session"),
    Check("lifecycle-hooks", "lifecycle", "function", None,
          "the hooks fire and their additionalContext reaches the model (measured by hand 11-14, runtime-codex.md): a "
          "check needs a model call, so a login in the world (Step 1's login rule)", side="session"),
]
