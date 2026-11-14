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


# ---------- the platform side (docs/design/runtime-codex.md, Step 1 and its r2) ----------
# No login in any of these: what needs a model call is a login check (Check.login), not checkable until the owner's login
# session (r2 (9)); a run that has to reach the request goes there unauthenticated (401), which carries only the check's
# fixed prompt, and its rollout still says what it would have sent.

LOGIN = ("needs a model call with the user's login: measured with the owner (runtime-codex.md, Step 1 r2, the login "
         "list); the runner runs it last, under section 3a's rule")
RC_FILES = (".zshenv", ".zprofile", ".zshrc", ".bash_profile", ".bashrc", ".profile")
RC_VAR = "BRUNNA_RC_CANARY"


def _prefix(exe) -> pathlib.Path | None:
    """The npm prefix a native binary was installed into (<prefix>/node_modules/@openai/<pkg>/vendor/<triple>/bin/codex)."""
    p = pathlib.Path(os.path.realpath(exe))
    return p.parents[6] if len(p.parents) > 6 and p.parents[5].name == "node_modules" else None


def distribution_integrity(world) -> Outcome:
    """The binary is a copy npm installed with an integrity hash for every package it resolved, and its platform build
    names this version and this machine's target."""
    exe = rx.binary()
    prefix = _prefix(exe)
    if prefix is None:
        return Outcome("error", _("{exe} isn't the native binary of an npm copy", exe=exe))
    version = rx.version_of(exe, env=dict(os.environ))
    if not version:
        return Outcome("error", _("the binary doesn't say its version"))
    bad = rx.copy_problems(prefix, version)
    return Outcome("fail", bad[0]) if bad else Outcome("pass", "")


def _prompt_input(exe, folder, home, args, base=None) -> list | None:
    r = subprocess.run([exe, "debug", "prompt-input", *args, "x"], cwd=folder, env=base or rx.run_env(home),
                       capture_output=True, text=True, timeout=RUN_S, stdin=subprocess.DEVNULL)
    try:
        got = json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        return None
    return got if isinstance(got, list) else None


def _plant_rc(home: pathlib.Path, marks: pathlib.Path) -> None:
    for f in RC_FILES:
        (home / f).write_text(f"touch {shlex.quote(str(marks / f))}\nexport {RC_VAR}=planted\n")


def _snapshot_has(codex_home: pathlib.Path) -> bool:
    for f in (codex_home / "shell_snapshots").glob("*") if (codex_home / "shell_snapshots").is_dir() else []:
        try:
            if RC_VAR in f.read_text(errors="replace"):
                return True
        except OSError:
            pass
    return False


def home_flags(world) -> Outcome:
    """The run home (HOME_CONFIG) gives every model in MODELS the pinned shape, through the same arguments a run gets;
    `exec --strict-config` takes it (to its unauthenticated request) and the rollout matches; the user's rc files don't
    run and no shell snapshot keeps what they export (r2 B1). Positive controls: the shell on shows exec_command; sub-agents
    on show collaboration on a v2 model; the rc files with the snapshot on and the user's HOME do run."""
    exe = rx.binary()
    folder = pathlib.Path(world.project).resolve()
    marks = pathlib.Path(world.root) / "rc-ran"
    marks.mkdir(exist_ok=True)
    _plant_rc(pathlib.Path(world.home), marks)
    homes = []

    def fresh():
        h = rx.run_home()
        homes.append(h)
        return h
    try:
        entries = rx.profile_entries(read=[folder], exe=exe)
        for model in dict.fromkeys(rx.MODELS.values()):
            items = _prompt_input(exe, folder, fresh(), rx.run_args(model, entries))
            if items is None:
                return Outcome("error", _("prompt-input gave nothing readable for {model}", model=model))
            if (bad := rx.shape_problems(items)):
                return Outcome("fail", _("{model}: {what}", model=model, what="; ".join(bad)))
        model = rx.MODELS["fast"]
        h = fresh()
        got = rx._one_run("conformance", exe, folder, h, rx.run_args(model, entries), "Reply with ok.", RUN_S, False,
                          entries, {"LANG": os.environ.get("LANG", "C")})
        if got.get("refused"):
            return Outcome("fail", got["error"])
        if rx._rollout(h) is None:   # a run with no login still leaves one (measured): it never started
            return Outcome("fail", _("the run with the run home's config didn't start: {error}", error=got.get("error")))
        if any(marks.iterdir()) or _snapshot_has(h):
            return Outcome("fail", _("the user's rc files ran for a platform run, or a snapshot kept what they export"))
        # positive controls
        shell = _prompt_input(exe, folder, fresh(), rx.run_args(model, entries, shell=True))
        agents = _prompt_input(exe, folder, fresh(), rx.run_args(model, entries) + ["-c", "agents.enabled=true"])
        if not shell or "exec_command" not in (rx.tools_shape(shell) or ({}, set()))[1]:
            return Outcome("error", _("tests nothing: the shell turned on shows no exec_command"))
        if not agents or "collaboration" not in (rx.tools_shape(agents) or ({}, set()))[0]:
            return Outcome("error", _("tests nothing: sub-agents turned on show no collaboration namespace"))
        h = fresh()
        env = {**rx.run_env(h), "HOME": str(world.home)}
        _prompt_input(exe, folder, h, rx.run_args(model, entries) + ["-c", "features.shell_snapshot=true"], base=env)
        if not any(marks.iterdir()) and not _snapshot_has(h):
            return Outcome("error", _("tests nothing: the rc files didn't run with the snapshot on and the user's HOME"))
        return Outcome("pass", "")
    except subprocess.TimeoutExpired:
        return Outcome("error", _("no answer in {s} s", s=RUN_S))
    finally:
        for h in homes:
            rx.end_run_home(h)


# (what, the shell command, whether it may succeed under the run's profile); {c} the copy, {o} a sibling of it, {h} the
# home's own file, {a} the run home's login link, {s} a file in the store
PROFILE_PROBES = (
    ("read the copy's STATE.md", "cat {c}/STATE.md", True),
    ("write STATE.md", "printf y >> {c}/STATE.md", True),
    ("write memory/reference", "printf y > {c}/memory/reference/x.md", True),
    ("write AGENT.md", "printf y >> {c}/AGENT.md", False),
    ("write RULES", "printf y >> {c}/RULES", False),
    ("write .git/config", "printf y >> {c}/.git/config", False),
    ("a new memory note", "printf y > {c}/memory/new.md", False),
    ("a .gitattributes under memory/reference", "printf y > {c}/memory/reference/.gitattributes", False),
    ("read a sibling of the copy", "cat {o}/secret.txt", False),
    ("read the home's own file", "cat {h}", False),
    ("read the run's login link", "cat {a}", False),
    ("write the run's login link", "printf y > {a}", False),
    ("read the store", "cat {s}", False),
    ("write /tmp", "printf y > /tmp/brunna-profile-probe-$$", False),
    ("rename AGENT.md", "mv {c}/AGENT.md {c}/memory/reference/moved", False),
    ("link to the home's file", "ln -sf {h} {c}/memory/reference/l && cat {c}/memory/reference/l", False),
)


def agent_profile(world) -> Outcome:
    """The per-run profile of an agent run in a memory copy (rx.profile_entries, as rx.run gets it) holds: the copy
    readable, STATE.md and memory/reference/ writable, everything else refused, the store and the home whole (r2 S2,
    S3: the copy under the world's home, so the check meets production's shape). Through `codex sandbox` with the same
    -c entries; positive control: each refused probe lands under a :workspace profile."""
    exe = rx.binary()
    home = pathlib.Path(world.home).resolve()
    copy, other = home / "memory-copies" / "box", home / "memory-copies" / "other"
    (copy / "memory" / "reference").mkdir(parents=True)
    (copy / ".git").mkdir()
    other.mkdir(parents=True)
    for f, text in (("STATE.md", "s"), ("AGENT.md", "a"), ("RULES", "r"), (".git/config", "[core]\n")):
        (copy / f).write_text(text)
    (other / "secret.txt").write_text("secret")
    (home / "home-secret").write_text("secret")
    pathlib.Path(world.tool).mkdir(parents=True, exist_ok=True)
    (pathlib.Path(world.tool) / "auth.json").write_text("{}")   # a canary login for the run home to link
    store_file = rx._store() / "canary"
    store_file.parent.mkdir(parents=True, exist_ok=True)
    store_file.write_text("store")
    run_home = rx.run_home()
    try:
        entries = rx.profile_entries(read=[copy], write=[copy / "STATE.md", copy / "memory" / "reference"], exe=exe)
        guarded = rx.run_args(None, entries)
        opened = ["-c", "permissions.brunna-open.extends=\":workspace\"", "-c", "default_permissions=\"brunna-open\""]
        names = {"c": copy, "o": other, "h": home / "home-secret", "a": run_home / "auth.json", "s": store_file}
        wrong, untested = [], []
        for what, cmd, may in PROFILE_PROBES:
            line = cmd.format(**{k: shlex.quote(str(v)) for k, v in names.items()})
            ok = _sandboxed(exe, copy, run_home, guarded, rx.PROFILE, line)
            if ok != may:
                wrong.append(what)
            if not may and not _sandboxed(exe, copy, run_home, opened, "brunna-open", line) \
                    and what != "write the run's login link":
                untested.append(what)
            for f in ("AGENT.md",):   # a probe that moved it puts it back
                if not (copy / f).exists() and (copy / "memory" / "reference" / "moved").exists():
                    (copy / "memory" / "reference" / "moved").rename(copy / f)
        if wrong:
            return Outcome("fail", _("the run's profile let through or refused the wrong things: {what}",
                                     what=", ".join(wrong)))
        if untested:
            return Outcome("error", _("tests nothing: {what} refused even without the profile", what=", ".join(untested)))
        return Outcome("pass", "")
    except subprocess.TimeoutExpired:
        return Outcome("error", _("no answer in {s} s", s=RUN_S))
    finally:
        rx.end_run_home(run_home)
        for f in pathlib.Path("/tmp").glob("brunna-profile-probe-*"):
            f.unlink(missing_ok=True)


def _sandboxed(exe, cwd, home, args, profile, line) -> bool:
    r = subprocess.run([exe, "sandbox", *args, "-P", profile, "--", "/bin/sh", "-c", line], cwd=cwd, env=rx.run_env(home),
                       capture_output=True, text=True, timeout=RUN_S, stdin=subprocess.DEVNULL)
    return r.returncode == 0


def _login_key(exe, codex_home, key: str) -> int:
    r = subprocess.run([exe, "login", "--with-api-key"], input=key, env={**rx.run_env(codex_home), "CODEX_HOME":
                       str(codex_home)}, capture_output=True, text=True, timeout=RUN_S)
    return r.returncode


def auth_link_write(world) -> Outcome:
    """M9 with dummy keys only: a login written through a run home's link lands in the user's file and keeps the link
    (so a refresh by a platform run doesn't fork the login). Positive control: a home holding a copy leaves the user's
    file alone."""
    import hashlib
    exe = rx.binary()
    user = pathlib.Path(world.tool)
    user.mkdir(parents=True, exist_ok=True)
    (user / "config.toml").write_text('cli_auth_credentials_store = "file"\n')
    if _login_key(exe, user, "sk-brunna-canary-0001") != 0 or not (user / "auth.json").is_file():
        return Outcome("error", _("a dummy login couldn't be written into the world"))
    digest = lambda: hashlib.sha256((user / "auth.json").read_bytes()).hexdigest()   # noqa: E731
    homes = [rx.run_home(), rx.run_home()]
    try:
        linked, copied = homes
        if not (linked / "auth.json").is_symlink():
            return Outcome("error", _("the run home holds no link to the login"))
        before = digest()
        _login_key(exe, linked, "sk-brunna-canary-0002")
        if not (linked / "auth.json").is_symlink():
            return Outcome("fail", _("a write through the run home replaced its link with a file"))
        if digest() == before:
            return Outcome("fail", _("a write through the run home's link didn't reach the user's file"))
        (copied / "auth.json").unlink()
        (copied / "auth.json").write_bytes((user / "auth.json").read_bytes())
        before = digest()
        _login_key(exe, copied, "sk-brunna-canary-0003")
        if digest() != before:
            return Outcome("error", _("tests nothing: a home holding a copy changed the user's file too"))
        return Outcome("pass", "")
    except subprocess.TimeoutExpired:
        return Outcome("error", _("no answer in {s} s", s=RUN_S))
    finally:
        for h in homes:
            rx.end_run_home(h)


def auth_login_error(world) -> Outcome:
    """A run with no login fails as a login error (is_login_error), not as anything else."""
    exe = rx.binary()
    folder = pathlib.Path(world.project).resolve()
    h = rx.run_home()   # the world's user home has no login: no link
    try:
        entries = rx.profile_entries(read=[folder], exe=exe)
        got = rx._one_run("conformance", exe, folder, h, rx.run_args(rx.MODELS["fast"], entries), "Reply with ok.",
                          RUN_S, False, entries, {"LANG": os.environ.get("LANG", "C")})
    except subprocess.TimeoutExpired:
        return Outcome("error", _("no answer in {s} s", s=RUN_S))
    finally:
        rx.end_run_home(h)
    if got.get("ok"):
        return Outcome("error", _("tests nothing: a run with no login answered"))
    return Outcome("pass", "") if rx.is_login_error(got.get("error") or "") else \
        Outcome("fail", _("a run with no login failed with something else: {error}", error=got.get("error")))


CHECKS = [
    # the platform side (Step 1): what its own runs rest on
    Check("distribution-integrity", "distribution", "function", distribution_integrity, timeout_s=120),
    Check("updater-off", "distribution", "safety", None,
          "`codex exec` has no update path to switch off: 0.167.0 with 0.169.0 published wrote no version.json or "
          "packages/ and left npm's sink empty (runtime-codex.md, Step 1); the store's digest check holds the copy"),
    Check("home-flags", "completion", "safety", home_flags, timeout_s=8 * RUN_S + 60),
    Check("agent-profile", "agent", "safety", agent_profile, timeout_s=2 * len(PROFILE_PROBES) * RUN_S + 60),
    Check("auth-link-write", "auth", "safety", auth_link_write, timeout_s=4 * RUN_S + 60),
    Check("auth-login-error", "auth", "function", auth_login_error, timeout_s=2 * RUN_S + 60),
    Check("completion-no-tools", "completion", "safety", None, LOGIN, login=True),
    Check("completion-schemas", "completion", "function", None, LOGIN, login=True),
    Check("agent-result", "agent", "function", None, LOGIN, login=True),
    Check("agent-models", "agent", "function", None, LOGIN, login=True),
    # the session side: the per-version gate reads these (a sandboxed project's Codex session whose version failed one).
    # session-folder-git holds the user's sessions only: the platform's runs never start where it matters
    # (runtime_codex.folder_refusal)
    Check("session-folder-git", "sessions", "safety", session_folder_git, timeout_s=RUN_S + 60, side="session",
          holds="sessions"),
    Check("control-globs", "control", "safety", control_globs, timeout_s=2 * 6 * RUN_S + 60, side="session"),
    Check("lifecycle-hooks", "lifecycle", "function", None,
          "the hooks fire and their additionalContext reaches the model (measured by hand 11-14, runtime-codex.md): a "
          "check needs a model call, so a login in the world (Step 1's login rule)", side="session", login=True),
]
