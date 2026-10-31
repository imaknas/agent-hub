#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Platform-run commands: a project puts a request in its own folder; this service (not inside any sandbox) checks it
and only then runs it under the platform's Seatbelt profile.

Design: docs/design/capabilities.md. Service `local.agent-hub.cap-runner` (in agent-hub's registry services,
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
A model call ({"v": 1, "id", "kind": "complete", "point", "prompt", "schema"?}, from `agenthub complete`): the point
must be in models:; it runs `llm.py project <project> <point>` outside any sandbox (the platform's credentials), which
checks the provider (metered only with the owner's metered_models) before sending. stdout = llm.py's JSON. Checks (failing one → rejected + reason):
  .hub-run/, requests/ and jobs/ must not be symlinks (everything goes through O_NOFOLLOW directory fds, so results
  can't be redirected into another project); the request is a regular file, ≤ 64 KB, within 10 minutes; id is a
  plain file name; cap is one this project declares; argv is a list of strings;
  cwd, resolved to its real path, is inside the project folder; timeout_s ≤ hub.json cap_runner.max_timeout_s.
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

_ = i18n.domain(__file__)

DIR = hubconf.STATE / "cap-runner"
LOG = DIR / "log.jsonl"
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
        if not caps and not models:
            continue
        d = registry.dirs(doc)
        dirs = []
        for p in (d.get("workspace"), d.get("runtime") if models else None):
            if p and p.is_dir() and p.resolve() not in dirs:
                dirs.append(p.resolve())
        if dirs:
            out[name] = {"dirs": dirs, "caps": caps, "models": models}
    return out


def subdir(parent_fd: int, name: str, create: bool = False) -> int:
    """A directory fd under parent; OSError if it's a symlink or not a directory."""
    if create:
        try:
            os.mkdir(name, 0o755, dir_fd=parent_fd)
        except FileExistsError:
            pass
    return os.open(name, DIRFLAGS, dir_fd=parent_fd)


def write_state(job_fd: int, **data) -> None:
    body = json.dumps({**data, "at": now()}, ensure_ascii=False).encode()
    fd = os.open(".state.tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644, dir_fd=job_fd)
    try:
        os.write(fd, body)
    finally:
        os.close(fd)
    os.rename(".state.tmp", "state.json", src_dir_fd=job_fd, dst_dir_fd=job_fd)


def read_json(dir_fd: int, name: str):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    with os.fdopen(fd, "rb") as f:
        return json.loads(f.read(MAX_REQ + 1).decode())


def check(req, stem: str, ws: pathlib.Path, caps: list[str], models: dict | None = None) -> tuple[str, dict]:
    """Return (reason for rejection, the tidied request); an empty reason = passed."""
    if not isinstance(req, dict) or req.get("v") != 1:
        return _("Malformed request (must be a JSON object with v: 1)"), {}
    if req.get("id") != stem:
        return _("id does not match the file name"), {}
    if req.get("kind") == "complete":
        return check_complete(req, models or {})
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


class Runner:
    def __init__(self):
        self.queue: list[Job] = []
        self.running: list[Job] = []
        self.excluded: set[str] = set()
        self.last_prune = 0.0

    # ---------- taking requests ----------

    def scan(self) -> None:
        for project, e in eligible().items():
            for ws in e["dirs"]:
                try:
                    self.scan_project(project, ws, e["caps"], e["models"])
                except OSError as err:  # .hub-run is a symlink, wrong permissions and such: skip this folder, log it
                    log(project=project, event="scan_error", detail=str(err)[:300])

    def scan_project(self, project: str, ws: pathlib.Path, caps: list[str], models: dict | None = None) -> None:
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
                        self.take(project, ws, caps, hr_fd, req_fd, n, models)
                finally:
                    os.close(req_fd)
            finally:
                os.close(hr_fd)
        finally:
            os.close(ws_fd)

    def take(self, project: str, ws: pathlib.Path, caps: list[str], hr_fd: int, req_fd: int, name: str,
             models: dict | None = None) -> None:
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
            reason, clean = check(req, stem, ws, caps, models)
        except (OSError, ValueError) as e:
            reason, clean = _("Cannot read the request: {err}", err=e), {}
        if not reason and time.time() - st.st_mtime > STALE_S:
            reason = _("Request too old (nobody took it within 10 minutes)")
        if reason:
            write_state(job_fd, state="rejected", reason=reason)
            os.close(job_fd)
            log(project=project, id=stem, event="rejected", detail=reason)
            return
        write_state(job_fd, state="queued")
        self.queue.append(Job(project, stem, job_fd, clean))

    # ---------- running ----------

    def start_ready(self) -> None:
        busy = {j.project for j in self.running}
        for job in list(self.queue):
            if len(self.running) >= conf("concurrency", 2):
                return
            if job.project in busy:
                continue
            self.queue.remove(job)
            busy.add(job.project)
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
                env.update({k: v for k, v in os.environ.items() if k.startswith("AGENT_HUB_")})
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
            write_state(job.fd, state="rejected", reason=_("Failed to start: {err!r}", err=e)[:500])
            os.close(job.fd)
            log(project=job.project, id=job.id, event="start_failed", detail=str(e)[:300])
            return
        job.started = time.time()
        job.deadline = job.started + r["timeout_s"]
        if r.get("kind") == "complete":
            write_state(job.fd, state="running", pid=job.proc.pid, kind="complete")
            log(project=job.project, id=job.id, event="started", kind="complete", point=r["point"], pid=job.proc.pid)
        else:
            write_state(job.fd, state="running", pid=job.proc.pid, cap=r["cap"])
            log(project=job.project, id=job.id, event="started", cap=r["cap"], argv=r["argv"][:20],
                cwd=str(r["cwd"]), pid=job.proc.pid)
        self.running.append(job)

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
            write_state(job.fd, state="done", exit=code, timed_out=job.timed_out)
            os.close(job.fd)
            self.running.remove(job)
            log(project=job.project, id=job.id, event="done", exit=code, timed_out=job.timed_out,
                seconds=round(t - job.started, 1))

    @staticmethod
    def kill(pgid: int, sig: int) -> None:
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    # ---------- restart, cleanup ----------

    def each_job(self):
        """(project, job id, path of the jobs directory): only walks directories that aren't symlinks."""
        for project, e in eligible().items():
            for ws in e["dirs"]:
                jobs = ws / ".hub-run" / "jobs"
                if any(p.is_symlink() for p in (ws / ".hub-run", jobs)) or not jobs.is_dir():
                    continue
                for d in jobs.iterdir():
                    if d.is_dir() and not d.is_symlink():
                        yield project, d.name, d

    def recover(self) -> None:
        """The service restarted: jobs still marked queued/running from the last round have nobody minding them."""
        for project, job_id, d in self.each_job():
            try:
                st = json.loads((d / "state.json").read_text())
            except (OSError, ValueError):
                continue
            if st.get("state") not in ("queued", "running"):
                continue
            if st.get("pid"):
                self.kill(int(st["pid"]), signal.SIGKILL)
            fd = os.open(d, DIRFLAGS)
            try:
                write_state(fd, state="lost", reason=_("The platform runner restarted and this job did not finish "
                                                       "(result unknown); run it again"))
            finally:
                os.close(fd)
            log(project=project, id=job_id, event="lost")

    def prune(self) -> None:
        cutoff = time.time() - KEEP_S
        for _project, _job_id, d in self.each_job():
            try:
                st = json.loads((d / "state.json").read_text())
                old = (d / "state.json").stat().st_mtime < cutoff
            except (OSError, ValueError):
                continue
            if old and st.get("state") in TERMINAL:
                shutil.rmtree(d, ignore_errors=True)
        for p in [*DIR.glob("*/*.sb"), *DIR.glob("*/*.in.json")]:
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)

    def tick(self) -> None:
        self.scan()
        self.start_ready()
        self.poll()
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
