#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Platform-run commands: a project puts a request in its own folder; this service (not inside any sandbox) checks it
and only then runs it under the platform's Seatbelt profile.

Design: docs/design/capabilities.md. Service `local.brunna.cap-runner` (in brunna's registry services,
KeepAlive; render-services alerts when it isn't running). The client is bin/hub-run.

Why run it for them: a second sandbox can't be nested inside the runtime's sandbox, and the runtime's own sandbox
blocks the GPU (Metal).
Why requests go in the project folder: the sandbox only lets each project write its own folder, so **the folder a
request is found in is the project it belongs to**: there's no "which project I am" in the request to forge; the
folder used to run it comes from the registry.

<workspace>/.hub-run/
  requests/<id>.json   put there by the client (written as .<id>.tmp, then renamed):
                       {"v": 1, "id", "cap", "argv": [...], "cwd", "timeout_s"}
  jobs/<id>/           created here: request.json (moved over from requests/), state.json, stdout, stderr
state.json: {state: queued|running|done|rejected|lost, reason?, exit?, timed_out?, at}
  exit follows the shell convention: killed by a signal = 128 + signal; timed out = 124 (timed_out: true)

Serves projects whose registry entry has enforce.sandbox: true and declares enforce.capabilities (idea sandboxes
get gpu by default, and keep it after being upgraded into a real project), and, for model calls, projects that declare
models: (sandboxed or not; their workspace and runtime folders are both scanned, so a service or job can call too).
A model call ({"v": 1, "id", "kind": "complete", "point", "prompt", "schema"?}, from `brunna complete`): the point
must be in models:; it runs `llm.py project <project> <point>` outside any sandbox (the platform's credentials), which
checks the provider (metered only with the owner's metered_models) before sending. stdout = llm.py's JSON.
An agent call ({"v": 1, "id", "kind": "agent", "name", "prompt", "schema"?}, from `brunna agent`; projects that declare
agents:, docs/design/project-agent-calls.md A): the name must be in agents:; it runs `llm.py agent <project> <name>
--cwd <the folder the request was found in>`, which takes the rules, tier and timeout from the registry. Its own pool
(cap_runner.agent_concurrency, default 1; the rest share cap_runner.concurrency, default 2), one job per project per pool,
the project served least recently first; one agent call per project at a time (another is refused); daily minutes in
.state/cap-runner/agent-minutes.json (cap_runner.agent_minutes_per_project, default 90, lowered by the registry's
agents_per_day; cap_runner.agent_minutes_per_day, default 480, across projects): a call reserves its declared timeout
when taken, replaced by the minutes it ran when it ends, however it ends; one that wouldn't fit is refused with the
numbers. The deadline is the declared timeout plus a minute; a call queued longer than its timeout is refused.
Checks (failing one → rejected + reason):
  .hub-run/, requests/ and jobs/ must not be symlinks (everything goes through O_NOFOLLOW directory fds, so results
  can't be redirected into another project); the request is a regular file, ≤ 64 KB, within 10 minutes; id is a
  plain file name; cap is one this project declares; argv is a list of strings;
  cwd, resolved to its real path, is inside the project folder; timeout_s ≤ {settings} cap_runner.max_timeout_s.
The profile is computed fresh every time (seatbelt.py) and put in .state/cap-runner/<project>/<id>.sb: a place the
session can't write.
argv is exec'd directly (`sandbox-exec -f <profile> <argv>`), no shell string in between; the environment gets only
PATH, HOME, TMPDIR, LANG.
On a timeout the whole process group is killed; at most cap_runner.concurrency at a time, 1 per project (the rest
queue).
Each job logs one line in .state/cap-runner/log.jsonl; jobs/ and profiles finished more than 7 days ago are cleaned up.
Jobs still marked running when the service restarts: their process group is killed, and they're marked lost (outcome
unknown).
When the code in bin/ has changed (hubconf.code_stamp()) and no job is running or queued: it exits by itself, so
launchd restarts it with the new code.

Usage: cap-runner.py            long-running (launchd)
       cap-runner.py --once     scan once, run the queued jobs, then exit (tests/debugging)
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import shutil
import signal
import stat
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402
import names  # noqa: E402

_ = i18n.domain(__file__)

DIR = hubconf.STATE / "cap-runner"
LOG = DIR / "log.jsonl"
ACTIVE = DIR / "active.json"   # {pid, jobs}: running + queued, while this runs (render-services waits on it)
MINUTES = DIR / "agent-minutes.json"   # agent calls' minutes today: platform state, never the project's folder
AGENT_GRACE_S = 60   # past the declared timeout, the runner's deadline (llm.py stops the run at the timeout itself)
ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_REQ = 512 * 1024      # a model call's prompt can be long; a cap request is still argv only
STALE_S = 600
KEEP_S = 7 * 86400
DIRFLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
TERMINAL = ("done", "rejected", "lost")


def conf(key: str, default: int) -> int:
    return int(hubconf.get(f"cap_runner.{key}", default))


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def log(**rec) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps({"at": now(), **rec}, ensure_ascii=False) + "\n")


def eligible() -> dict[str, dict]:
    """project → {dirs: folders whose .hub-run/ is served (workspace, runtime), caps, models}. caps only for sandboxed
    projects that declare enforce.capabilities; models (registry models:) for any project, so a call made from its
    service's or job's runtime folder is served too."""
    import registry
    out = {}
    for name, doc in hubconf.projects().items():
        enf = doc.get("enforce") or {}
        caps = list(enf.get("capabilities") or []) if enf.get("sandbox") is True else []
        models = doc.get("models") or {}
        agents = {k: v for k, v in (doc.get("agents") or {}).items() if isinstance(v, dict)}
        if not caps and not models and not agents:
            continue
        d = registry.dirs(doc)
        dirs = []
        for p in (d.get("workspace"), d.get("runtime") if models or agents else None):
            if p and p.is_dir() and p.resolve() not in dirs:
                dirs.append(p.resolve())
        if dirs:
            out[name] = {"dirs": dirs, "caps": caps, "models": models, "agents": agents,
                         "agents_per_day": doc.get("agents_per_day")}
    return out


def each_job():
    """(project, job id, path of the jobs directory): only walks directories that aren't symlinks."""
    for project, e in eligible().items():
        for ws in e["dirs"]:
            jobs = ws / ".hub-run" / "jobs"
            if any(p.is_symlink() for p in (ws / ".hub-run", jobs)) or not jobs.is_dir():
                continue
            for d in jobs.iterdir():
                if d.is_dir() and not d.is_symlink():
                    yield project, d.name, d


def subdir(parent_fd: int, name: str, create: bool = False) -> int:
    """A directory fd under parent; OSError if it's a symlink or not a directory."""
    if create:
        try:
            os.mkdir(name, 0o755, dir_fd=parent_fd)
        except FileExistsError:
            pass
    return os.open(name, DIRFLAGS, dir_fd=parent_fd)


def write_state(job_fd: int, **data) -> None:
    """state.json, through a new file: the job folder is in the project's folder, so whatever sits at .state.tmp (a
    FIFO with no reader would hold the shared service on open) is removed and the file made fresh, exclusively."""
    body = json.dumps({**data, "at": now()}, ensure_ascii=False).encode()
    try:
        os.unlink(".state.tmp", dir_fd=job_fd)
    except FileNotFoundError:
        pass
    fd = os.open(".state.tmp", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=job_fd)
    try:
        os.write(fd, body)
    finally:
        os.close(fd)
    os.rename(".state.tmp", "state.json", src_dir_fd=job_fd, dst_dir_fd=job_fd)


def job_state(d: pathlib.Path, older_than: float | None = None):
    """A job's state.json, read as read_json does (the folder is the project's: no link followed at the folder or the
    file, no blocking open, a regular file only); None when it can't be read, or (older_than) when it is newer."""
    try:
        fd = os.open(d, DIRFLAGS)
    except OSError:
        return None
    try:
        if older_than is not None and os.stat("state.json", dir_fd=fd, follow_symlinks=False).st_mtime >= older_than:
            return None
        return read_json(fd, "state.json")
    except (OSError, ValueError):
        return None
    finally:
        os.close(fd)


def read_json(dir_fd: int, name: str):
    """A file in a folder the project writes: what was checked a moment ago may have been swapped since, so it is opened
    without blocking (a FIFO put there would otherwise hold the shared service), and only a regular file is read."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    try:
        regular = stat.S_ISREG(os.fstat(fd).st_mode)
    except OSError:
        os.close(fd)
        raise
    if not regular:
        os.close(fd)
        raise ValueError(f"{name} is not a regular file")
    with os.fdopen(fd, "rb") as f:
        return json.loads(f.read(MAX_REQ + 1).decode())


def check(req, stem: str, ws: pathlib.Path, caps: list[str], models: dict | None = None,
          agents: dict | None = None) -> tuple[str, dict]:
    """Return (reason for rejection, the tidied request); an empty reason = passed."""
    if not isinstance(req, dict) or req.get("v") != 1:
        return _("Malformed request (must be a JSON object with v: 1)"), {}
    if req.get("id") != stem:
        return _("id does not match the file name"), {}
    if req.get("kind") == "complete":
        return check_complete(req, models or {})
    if req.get("kind") == "agent":
        return check_agent(req, agents or {}, ws)
    cap = req.get("cap")
    if cap not in caps:
        return _("This project has not declared the capability {cap!r} (its registry entry's enforce.capabilities: "
                 "{caps})", cap=cap,
                 caps=", ".join(caps)), {}
    argv = req.get("argv")
    if not (isinstance(argv, list) and argv and all(isinstance(a, str) and "\0" not in a and len(a) <= 4096 for a in argv)):
        return _("argv must be a non-empty list of strings"), {}
    cwd = req.get("cwd", ".")
    if not isinstance(cwd, str) or os.path.isabs(cwd):
        return _("cwd must be a path relative to the project folder"), {}
    real = (ws / cwd).resolve()
    if real != ws and ws not in real.parents:
        return _("cwd {cwd} is not inside the project folder", cwd=cwd), {}
    if not real.is_dir():
        return _("cwd {cwd} is not a folder", cwd=cwd), {}
    t = req.get("timeout_s", conf("timeout_s", 600))
    if not isinstance(t, int) or isinstance(t, bool) or not 0 < t <= conf("max_timeout_s", 1800):
        return _("timeout_s must be an integer from 1 to {max}", max=conf("max_timeout_s", 1800)), {}
    return "", {"cap": cap, "argv": argv, "cwd": real, "timeout_s": t}


def check_complete(req: dict, models: dict) -> tuple[str, dict]:
    """A model call (agenthub complete): the point must be in the registry's models:; the rest (which provider, metered
    or not) is llm.py project's to check, outside the project's reach."""
    point = req.get("point")
    if not isinstance(point, str) or point not in models:
        return _("This project has not declared the model call {point!r} (its registry entry's models: {have})",
                 point=point, have=", ".join(sorted(models)) or _("none")), {}
    if not isinstance(req.get("prompt"), str) or not req["prompt"].strip():
        return _("prompt must be a non-empty string"), {}
    if req.get("schema") is not None and not isinstance(req["schema"], dict):
        return _("schema must be a JSON object"), {}
    return "", {"kind": "complete", "point": point, "prompt": req["prompt"], "schema": req.get("schema"),
                "timeout_s": conf("timeout_s", 600)}


def check_agent(req: dict, agents: dict, ws: pathlib.Path) -> tuple[str, dict]:
    """An agent call (brunna agent): the name must be in the registry's agents:; its rules, tier and timeout are the
    registry's (llm.py agent reads them again), the folder is the one the request was found in."""
    import registry
    name = req.get("name")
    if not isinstance(name, str) or name not in agents:
        return _("This project has not declared the agent call {name!r} (its registry entry's agents: {have})",
                 name=name, have=", ".join(sorted(agents)) or _("none")), {}
    if not isinstance(req.get("prompt"), str) or not req["prompt"].strip():
        return _("prompt must be a non-empty string"), {}
    if req.get("schema") is not None and not isinstance(req["schema"], dict):
        return _("schema must be a JSON object"), {}
    timeout = registry.agent_timeout_s(agents[name])
    if timeout > conf("agent_max_timeout_s", 3600):
        return _("agents.{name} declares a timeout of {minutes} minutes; the most is {max}", name=name,
                 minutes=timeout // 60, max=conf("agent_max_timeout_s", 3600) // 60), {}
    return "", {"kind": "agent", "name": name, "prompt": req["prompt"], "schema": req.get("schema"), "cwd": ws,
                "timeout_s": timeout}


def _minutes(seconds: float) -> int:
    return max(0, -(-int(seconds) // 60))


class Minutes:
    """Agent calls' minutes today (A5): {"day", "jobs": {"<project>/<id>": {"project", "reserved", "used", "at"}}}, in
    platform state. A call reserves its declared timeout when taken; settle() puts the minutes it ran in its place."""

    def __init__(self, path: pathlib.Path | None = None):
        self.path = path or MINUTES   # read when made (tests move it)

    def load(self) -> dict:
        today = datetime.date.today().isoformat()
        try:
            d = json.loads(self.path.read_text())
            if isinstance(d, dict) and d.get("day") == today and isinstance(d.get("jobs"), dict):
                return d
        except (OSError, ValueError):
            pass
        return {"day": today, "jobs": {}}

    def save(self, d: dict) -> None:
        hubconf.write_atomic(self.path, json.dumps(d, ensure_ascii=False))

    @staticmethod
    def spent(d: dict, project: str | None = None) -> int:
        return sum((j["used"] if j.get("used") is not None else j.get("reserved", 0))
                   for j in d["jobs"].values() if project is None or j.get("project") == project)

    def reserve(self, project: str, job_id: str, seconds: int, per_day: int | None) -> tuple[str, str]:
        """('', the reservation's key) when it fits (and is reserved), else (why not, with the numbers, ''). The key is
        the runner's own: the request's id is the project's to choose, and could otherwise replace an earlier entry."""
        d, want = self.load(), _minutes(seconds)
        mine, cap = self.spent(d, project), conf("agent_minutes_per_project", 90)
        if isinstance(per_day, int) and not isinstance(per_day, bool) and per_day > 0:
            cap = min(cap, per_day)   # the registry's agents_per_day lowers the share, never raises it
        if mine + want > cap:
            return _("{project} has {spent} of its {cap} agent minutes today, and this call reserves {want}",
                     project=project, spent=mine, cap=cap, want=want), ""
        everyone, total = self.spent(d), conf("agent_minutes_per_day", 480)
        if everyone + want > total:
            return _("agent calls have {spent} of the platform's {cap} minutes today, and this call reserves {want}",
                     spent=everyone, cap=total, want=want), ""
        key = f"{project}/{job_id}/{time.time_ns()}"
        while key in d["jobs"]:
            key += "+"
        d["jobs"][key] = {"project": project, "reserved": want, "used": None, "at": time.time()}
        self.save(d)
        return "", key

    def settle(self, key: str, seconds: float) -> None:
        if not key:
            return
        d = self.load()
        j = d["jobs"].get(key)
        if j is not None and j.get("used") is None:   # a call taken on an earlier day counts there, not today
            j["used"] = min(j.get("reserved", 0), _minutes(seconds))
            self.save(d)

    def settle_orphans(self) -> None:
        """At start: calls still reserved belong to a runner that is gone; each counts the minutes up to now, at most
        its reservation."""
        d, changed = self.load(), False
        for j in d["jobs"].values():
            if j.get("used") is None:
                j["used"] = min(j.get("reserved", 0), _minutes(time.time() - float(j.get("at") or time.time())))
                changed = True
        if changed:
            self.save(d)


def process_started(pid: int) -> float | None:
    """When process pid started (seconds since the epoch, to the second), or None when there is none."""
    try:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True, text=True, timeout=10,
                           env={"LC_ALL": "C", "PATH": "/bin:/usr/bin"})
        text = " ".join(r.stdout.split())
        return time.mktime(time.strptime(text, "%a %b %d %H:%M:%S %Y")) if r.returncode == 0 and text else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def make_profile(project: str, cap: str, job_id: str) -> pathlib.Path:
    import policy
    import seatbelt
    p = DIR / project / f"{job_id}.sb"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(seatbelt.profile(policy.for_project(project), cap))
    return p


def exclude_hub_run(project: str, ws: pathlib.Path) -> None:
    """Keep .hub-run/ out of the project's git (written into .git/info/exclude, like attachments/), as the project:
    its .git is its own to write, a link planted there must not point this at another file (project_git.py)."""
    import project_git
    project_git.job(project, ws, "exclude", ws, ".hub-run/")


class Job:
    def __init__(self, project: str, job_id: str, job_fd: int, req: dict):
        self.project, self.id, self.fd, self.req = project, job_id, job_fd, req
        self.proc: subprocess.Popen | None = None
        self.deadline = 0.0
        self.timed_out = False
        self.killed_at = 0.0
        self.started = 0.0
        self.queued = time.time()

    @property
    def pool(self) -> str:
        return "agent" if self.req.get("kind") == "agent" else "other"


class Runner:
    def __init__(self):
        self.queue: list[Job] = []
        self.running: list[Job] = []
        self.excluded: set[str] = set()
        self.noted: int | None = None
        self.last_prune = 0.0
        self.minutes = Minutes()
        self.served: dict[tuple[str, str], float] = {}   # (pool, project) → when its last job started

    # ---------- taking requests ----------

    def scan(self) -> None:
        for project, e in eligible().items():
            for ws in e["dirs"]:
                try:
                    self.scan_project(project, ws, e["caps"], e["models"], e.get("agents"), e.get("agents_per_day"))
                except OSError as err:  # .hub-run is a symlink, wrong permissions and such: skip this folder, log it
                    log(project=project, event="scan_error", detail=str(err)[:300])

    def scan_project(self, project: str, ws: pathlib.Path, caps: list[str], models: dict | None = None,
                     agents: dict | None = None, per_day: int | None = None) -> None:
        ws_fd = os.open(ws, DIRFLAGS)
        try:
            try:
                hr_fd = subdir(ws_fd, ".hub-run")
            except FileNotFoundError:
                return
            try:
                try:
                    req_fd = subdir(hr_fd, "requests")
                except FileNotFoundError:
                    return
                try:
                    names = sorted(n for n in os.listdir(req_fd) if n.endswith(".json") and not n.startswith("."))
                    if names and str(ws) not in self.excluded:
                        exclude_hub_run(project, ws)
                        self.excluded.add(str(ws))
                    for n in names:
                        self.take(project, ws, caps, hr_fd, req_fd, n, models, agents, per_day)
                finally:
                    os.close(req_fd)
            finally:
                os.close(hr_fd)
        finally:
            os.close(ws_fd)

    def take(self, project: str, ws: pathlib.Path, caps: list[str], hr_fd: int, req_fd: int, name: str,
             models: dict | None = None, agents: dict | None = None, per_day: int | None = None) -> None:
        stem = name[:-5]
        st = os.stat(name, dir_fd=req_fd, follow_symlinks=False)
        if not ID.match(stem) or not stat.S_ISREG(st.st_mode) or st.st_size > MAX_REQ:
            os.unlink(name, dir_fd=req_fd)  # not a valid request file: nowhere to reply, so drop it and log it
            log(project=project, id=stem[:80], event="dropped", detail=_("Wrong file name, file type or size"))
            return
        jobs_fd = subdir(hr_fd, "jobs", create=True)
        try:
            try:
                os.mkdir(stem, 0o755, dir_fd=jobs_fd)
            except FileExistsError:
                os.unlink(name, dir_fd=req_fd)
                log(project=project, id=stem, event="dropped", detail=_("This id has already run"))
                return
            job_fd = subdir(jobs_fd, stem)
        finally:
            os.close(jobs_fd)
        os.rename(name, "request.json", src_dir_fd=req_fd, dst_dir_fd=job_fd)
        try:
            req = read_json(job_fd, "request.json")
            reason, clean = check(req, stem, ws, caps, models, agents)
        except (OSError, ValueError) as e:
            reason, clean = _("Cannot read the request: {err}", err=e), {}
        if not reason and time.time() - st.st_mtime > STALE_S:
            reason = _("Request too old (nobody took it within 10 minutes)")
        if not reason and clean.get("kind") == "agent":
            reason = self.admit_agent(project, stem, clean, per_day)
        if reason:
            write_state(job_fd, state="rejected", reason=reason)
            os.close(job_fd)
            log(project=project, id=stem, event="rejected", detail=reason)
            return
        try:
            write_state(job_fd, state="queued")
        except OSError:
            os.close(job_fd)
            self.minutes.settle(clean.get("ledger", ""), 0)   # never queued: nothing was spent
            raise
        self.queue.append(Job(project, stem, job_fd, clean))

    def admit_agent(self, project: str, job_id: str, clean: dict, per_day: int | None) -> str:
        """One agent call per project at a time, and its minutes reserved (A5); '' when admitted."""
        if any(j.project == project and j.pool == "agent" for j in (*self.queue, *self.running)):
            return _("{project} already has an agent call queued or running; one at a time", project=project)
        why, clean["ledger"] = self.minutes.reserve(project, job_id, clean["timeout_s"], per_day)
        return why

    # ---------- running ----------

    def start_ready(self) -> None:
        t = time.time()
        for job in [j for j in self.queue if j.pool == "agent" and t - j.queued > j.req["timeout_s"]]:
            self.queue.remove(job)   # waited longer than it may run: whoever asked has given up on it
            self.finish(job, 0, state="rejected", reason=_("Waited in the queue longer than its timeout"))
            log(project=job.project, id=job.id, event="rejected", detail="queued past its timeout")
        limits = {"agent": conf("agent_concurrency", 1), "other": conf("concurrency", 2)}
        busy = {(j.pool, j.project) for j in self.running}
        # within a pool, the project served least recently first; then the order they came in
        order = sorted(enumerate(self.queue), key=lambda e: (self.served.get((e[1].pool, e[1].project), 0.0), e[0]))
        for _i, job in order:
            if sum(1 for j in self.running if j.pool == job.pool) >= limits[job.pool]:
                continue
            if (job.pool, job.project) in busy:
                continue
            self.queue.remove(job)
            busy.add((job.pool, job.project))
            self.served[(job.pool, job.project)] = t
            self.start(job)

    def start(self, job: Job) -> None:
        r = job.req
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            out = os.open("stdout", flags, 0o644, dir_fd=job.fd)
            err = os.open("stderr", flags, 0o644, dir_fd=job.fd)
            env = {"PATH": hubconf.LAUNCHD_PATH, "HOME": str(pathlib.Path.home()),
                   "TMPDIR": os.environ.get("TMPDIR", "/private/tmp"), "LANG": os.environ.get("LANG", "en_US.UTF-8")}
            if r.get("kind") == "complete":
                # the platform's own model call, outside any sandbox (it needs the credentials): the prompt goes
                # through a file in platform state, never read back from the project's folder
                inp = DIR / job.project / f"{job.id}.in.json"
                inp.parent.mkdir(parents=True, exist_ok=True)
                inp.write_text(json.dumps({"prompt": r["prompt"], "schema": r["schema"]}, ensure_ascii=False))
                argv = [*hubconf.launch(hubconf.ROOT / "bin" / "llm.py"), "project", job.project, r["point"],
                        "--request", str(inp)]
                env.update({k: v for k, v in os.environ.items() if k.startswith(("AGENT_HUB_", names.ENV_NEW))})
                cwd = DIR
            elif r.get("kind") == "agent":
                # the project's declared agent, outside any sandbox (llm.py agent confines it): the prompt through a
                # file in platform state; the folder is the one the request was found in, never one it names
                inp = DIR / job.project / f"{job.id}.in.json"
                inp.parent.mkdir(parents=True, exist_ok=True)
                inp.write_text(json.dumps({"prompt": r["prompt"], "schema": r["schema"]}, ensure_ascii=False))
                argv = [*hubconf.launch(hubconf.ROOT / "bin" / "llm.py"), "agent", job.project, r["name"],
                        "--request", str(inp), "--cwd", str(r["cwd"])]
                env.update({k: v for k, v in os.environ.items() if k.startswith(("AGENT_HUB_", names.ENV_NEW))})
                cwd = DIR
            else:
                profile = make_profile(job.project, r["cap"], job.id)
                argv, cwd = ["/usr/bin/sandbox-exec", "-f", str(profile), *r["argv"]], r["cwd"]
            try:
                job.proc = subprocess.Popen(argv, cwd=cwd,
                                            env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                            start_new_session=True)
            finally:
                os.close(out)
                os.close(err)
        # every failure must be answered, or the client keeps waiting on "queued" (seen on 10-25: stuck when
        # modules were inconsistent)
        except Exception as e:
            log(project=job.project, id=job.id, event="start_failed", detail=str(e)[:300])
            self.finish(job, 0, state="rejected", reason=_("Failed to start: {err!r}", err=e)[:500])
            return
        job.started = time.time()
        job.deadline = job.started + r["timeout_s"] + (AGENT_GRACE_S if job.pool == "agent" else 0)
        try:
            if r.get("kind") == "agent":
                write_state(job.fd, state="running", pid=job.proc.pid, kind="agent")
            elif r.get("kind") == "complete":
                write_state(job.fd, state="running", pid=job.proc.pid, kind="complete")
            else:
                write_state(job.fd, state="running", pid=job.proc.pid, cap=r["cap"])
        except OSError as e:
            # the project's job folder can't be written (removed, a link planted): never a run nobody minds
            self.kill(job.proc.pid, signal.SIGKILL)
            log(project=job.project, id=job.id, event="state_error", detail=str(e)[:300])
            self.finish(job, 0)
            return
        if r.get("kind") == "agent":
            log(project=job.project, id=job.id, event="started", kind="agent", name=r["name"], pid=job.proc.pid)
        elif r.get("kind") == "complete":
            log(project=job.project, id=job.id, event="started", kind="complete", point=r["point"], pid=job.proc.pid)
        else:
            log(project=job.project, id=job.id, event="started", cap=r["cap"], argv=r["argv"][:20],
                cwd=str(r["cwd"]), pid=job.proc.pid)
        self.running.append(job)
        self.note_active()

    def finish(self, job: Job, seconds: float, **state) -> None:
        """A job's end, whatever happens to its folder: its state written when it can be (else logged), its folder
        closed, its agent minutes settled. The caller has taken it off the queue or running list."""
        if state:
            try:
                write_state(job.fd, **state)
            except OSError as e:
                log(project=job.project, id=job.id, event="state_error", detail=str(e)[:300])
        try:
            os.close(job.fd)
        except OSError:
            pass
        if job.pool == "agent":
            self.minutes.settle(job.req.get("ledger", ""), seconds)

    def poll(self) -> None:
        t = time.time()
        for job in list(self.running):
            rc = job.proc.poll()
            if rc is None:
                if t > job.deadline and not job.timed_out:
                    job.timed_out, job.killed_at = True, t
                    self.kill(job.proc.pid, signal.SIGTERM)
                elif job.timed_out and t - job.killed_at > 5:
                    self.kill(job.proc.pid, signal.SIGKILL)
                continue
            # the main process ended; reap child processes left in the background too
            self.kill(job.proc.pid, signal.SIGKILL)
            code = 124 if job.timed_out else (128 - rc if rc < 0 else rc)
            self.running.remove(job)
            self.finish(job, t - job.started, state="done", exit=code, timed_out=job.timed_out,
                        **({"kind": "agent"} if job.pool == "agent" else {}))
            log(project=job.project, id=job.id, event="done", exit=code, timed_out=job.timed_out,
                seconds=round(t - job.started, 1))

    @staticmethod
    def kill(pgid: int, sig: int) -> None:
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    # ---------- restart, cleanup ----------

    @staticmethod
    def each_job():
        return each_job()

    def recover(self) -> None:
        """The service restarted: jobs still marked queued/running from the last round have nobody minding them, and the
        agent minutes they reserved count only up to now."""
        try:
            self.minutes.settle_orphans()
        except OSError as e:
            log(event="minutes_error", detail=str(e)[:300])
        # the process groups the last runner started, from its own record in platform state (a pid in the project's
        # state.json is the project's to write: never killed on its word), each only if it is still the process
        # that was started then (a pid is reused after a reboot)
        try:
            procs = json.loads(ACTIVE.read_text()).get("procs") or []
        except (OSError, ValueError, AttributeError):
            procs = []
        for entry in procs:
            try:
                pgid, at = int(entry[0]), float(entry[1])
            except (TypeError, ValueError, IndexError):
                continue
            if (began := process_started(pgid)) is not None and abs(began - at) < 3:
                self.kill(pgid, signal.SIGKILL)
        for project, job_id, d in self.each_job():
            st = job_state(d)
            if not isinstance(st, dict) or st.get("state") not in ("queued", "running"):
                continue
            try:
                fd = os.open(d, DIRFLAGS)
                try:
                    write_state(fd, state="lost", reason=_("The platform runner restarted and this job did not finish "
                                                           "(result unknown); run it again"))
                finally:
                    os.close(fd)
            except OSError as e:   # the project's folder: whatever is there, go on
                log(project=project, id=job_id, event="recover_error", detail=str(e)[:300])
                continue
            log(project=project, id=job_id, event="lost")

    def prune(self) -> None:
        cutoff = time.time() - KEEP_S
        for _project, _job_id, d in self.each_job():
            st = job_state(d, older_than=cutoff)
            if isinstance(st, dict) and st.get("state") in TERMINAL:
                shutil.rmtree(d, ignore_errors=True)
        for p in [*DIR.glob("*/*.sb"), *DIR.glob("*/*.in.json")]:
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)

    def note_active(self) -> None:
        """ACTIVE when the number of jobs in flight changed: a restart now would mark them lost (render-services
        changes this service's unit only while it is 0)."""
        n = len(self.running) + len(self.queue)
        procs = sorted([j.proc.pid, j.started] for j in self.running if j.proc is not None)
        if (n, procs) != self.noted:
            hubconf.write_atomic(ACTIVE, json.dumps({"pid": os.getpid(), "jobs": n, "procs": procs}))
            self.noted = (n, procs)

    def tick(self) -> None:
        self.scan()
        self.start_ready()
        self.poll()
        self.note_active()
        if time.time() - self.last_prune > 600:
            self.last_prune = time.time()
            self.prune()


def main() -> int:
    r = Runner()
    r.recover()
    if "--once" in sys.argv:
        r.tick()
        while r.running or r.queue:
            time.sleep(0.2)
            r.start_ready()
            r.poll()
        return 0
    log(event="service_start", pid=os.getpid())
    code_at_start = hubconf.code_stamp()
    while True:
        try:
            r.tick()
        # one project's odd data must not bring down the whole service (launchd would restart it, but running jobs
        # would become lost)
        except Exception as e:
            log(event="tick_error", detail=repr(e)[:300])
        stamp = hubconf.code_stamp()
        # restart only once the change has been stable for 60 seconds
        if not r.running and not r.queue and stamp > code_at_start and time.time() - stamp > 60:
            # mixing old and new modules breaks things (a job got stuck on 10-25): let launchd restart when idle
            log(event="code_changed_restart")
            return 0
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
