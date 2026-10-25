#!/usr/bin/env python3
"""平台代跑:專案在自己資料夾放請求,這裡(不在任何 sandbox 裡)檢查過才用平台的 Seatbelt profile 執行。

設計:docs/design/capabilities.md。服務 `local.agent-hub.cap-runner`(登錄 agent-hub 的 services,KeepAlive;
render-services 會在它沒在跑時發警報)。客戶端是 bin/hub-run。

為什麼要代跑:執行環境的 sandbox 裡套不了第二層 sandbox,而它自己的 sandbox 擋住 GPU(Metal)。
為什麼請求放在專案資料夾:sandbox 只准每個專案寫自己的資料夾,**請求在哪個資料夾被找到,就是哪個專案**——
請求裡沒有「我是哪個專案」可以偽造;執行用的資料夾從登錄來。

<workspace>/.hub-run/
  requests/<id>.json   客戶端放的(先寫 .<id>.tmp 再 rename):{"v": 1, "id", "cap", "argv": [...], "cwd", "timeout_s"}
  jobs/<id>/           這裡建的:request.json(從 requests/ 搬過來)、state.json、stdout、stderr
state.json:{state: queued|running|done|rejected|lost, reason?, exit?, timed_out?, at}
  exit 照 shell 的習慣:被信號殺掉 = 128 + 信號;逾時 = 124(timed_out: true)

只服務登錄 type: idea、enforce.sandbox: true、enforce.capabilities 有宣告的專案。檢查(不過就 rejected + 理由):
  .hub-run/、requests/、jobs/ 都不能是 symlink(全部用 O_NOFOLLOW 的目錄 fd 操作,結果不會被導到別的專案)、
  請求是一般檔案、≤ 64 KB、10 分鐘內;id 是單純的檔名;cap 是這個專案宣告的;argv 是字串 list;
  cwd 解析成真實路徑後在專案資料夾裡;timeout_s ≤ hub.json cap_runner.max_timeout_s。
profile 每次現算(seatbelt.py),放 .state/cap-runner/<專案>/<id>.sb——session 寫不到的地方。
argv 直接 exec(`sandbox-exec -f <profile> <argv>`),中間沒有 shell 字串;環境只給 PATH、HOME、TMPDIR、LANG。
逾時整個 process group 一起殺;同時最多 cap_runner.concurrency 個、每個專案 1 個(其他排隊)。
每個工作記一行在 .state/cap-runner/log.jsonl;做完超過 7 天的 jobs/ 跟 profile 會清掉。
服務重啟時還標 running 的工作:殺掉那個 process group,標 lost(結果不明)。
bin/ 的程式改過(hubconf.code_stamp())、而且沒有工作在跑或排隊:自己結束,讓 launchd 用新的程式重啟。

用法:cap-runner.py            常駐(launchd)
      cap-runner.py --once     掃一次、跑完排到的工作就結束(測試/除錯)
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

DIR = hubconf.STATE / "cap-runner"
LOG = DIR / "log.jsonl"
ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_REQ = 64 * 1024
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


def eligible() -> dict[str, tuple[pathlib.Path, list[str]]]:
    out = {}
    for name, doc in hubconf.projects().items():
        enf = doc.get("enforce") or {}
        caps = list(enf.get("capabilities") or [])
        ws = hubconf.workspace(name)
        if doc.get("type") == "idea" and enf.get("sandbox") is True and caps and ws and ws.is_dir():
            out[name] = (ws.resolve(), caps)
    return out


def subdir(parent_fd: int, name: str, create: bool = False) -> int:
    """parent 底下的目錄 fd;是 symlink 或不是目錄就 OSError。"""
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


def check(req, stem: str, ws: pathlib.Path, caps: list[str]) -> tuple[str, dict]:
    """回傳 (拒絕的理由, 整理好的請求);理由是空字串 = 通過。"""
    if not isinstance(req, dict) or req.get("v") != 1:
        return "請求格式不對(要是 v: 1 的 JSON 物件)", {}
    if req.get("id") != stem:
        return "id 跟檔名不一樣", {}
    cap = req.get("cap")
    if cap not in caps:
        return f"這個專案沒有宣告能力 {cap!r}(登錄 enforce.capabilities:{', '.join(caps)})", {}
    argv = req.get("argv")
    if not (isinstance(argv, list) and argv and all(isinstance(a, str) and "\0" not in a and len(a) <= 4096 for a in argv)):
        return "argv 要是非空的字串 list", {}
    cwd = req.get("cwd", ".")
    if not isinstance(cwd, str) or os.path.isabs(cwd):
        return "cwd 要是相對專案資料夾的路徑", {}
    real = (ws / cwd).resolve()
    if real != ws and ws not in real.parents:
        return f"cwd {cwd} 不在專案資料夾裡", {}
    if not real.is_dir():
        return f"cwd {cwd} 不是資料夾", {}
    t = req.get("timeout_s", conf("timeout_s", 600))
    if not isinstance(t, int) or isinstance(t, bool) or not 0 < t <= conf("max_timeout_s", 1800):
        return f"timeout_s 要是 1–{conf('max_timeout_s', 1800)} 的整數", {}
    return "", {"cap": cap, "argv": argv, "cwd": real, "timeout_s": t}


def make_profile(project: str, cap: str, job_id: str) -> pathlib.Path:
    import policy
    import seatbelt
    p = DIR / project / f"{job_id}.sb"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(seatbelt.profile(policy.for_project(project), cap))
    return p


def exclude_hub_run(ws: pathlib.Path) -> None:
    """.hub-run/ 不進專案的 git(跟 attachments/ 一樣寫 .git/info/exclude)。"""
    r = subprocess.run(["git", "-C", str(ws), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return
    ex = pathlib.Path(r.stdout.strip()) / "info" / "exclude"
    lines = ex.read_text().splitlines() if ex.exists() else []
    if ".hub-run/" not in lines:
        ex.parent.mkdir(parents=True, exist_ok=True)
        ex.write_text("\n".join(lines + [".hub-run/"]) + "\n")


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

    # ---------- 收請求 ----------

    def scan(self) -> None:
        for project, (ws, caps) in eligible().items():
            try:
                self.scan_project(project, ws, caps)
            except OSError as e:  # .hub-run 是 symlink、權限不對之類:跳過這個專案,記下來
                log(project=project, event="scan_error", detail=str(e)[:300])

    def scan_project(self, project: str, ws: pathlib.Path, caps: list[str]) -> None:
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
                    if names and project not in self.excluded:
                        exclude_hub_run(ws)
                        self.excluded.add(project)
                    for n in names:
                        self.take(project, ws, caps, hr_fd, req_fd, n)
                finally:
                    os.close(req_fd)
            finally:
                os.close(hr_fd)
        finally:
            os.close(ws_fd)

    def take(self, project: str, ws: pathlib.Path, caps: list[str], hr_fd: int, req_fd: int, name: str) -> None:
        stem = name[:-5]
        st = os.stat(name, dir_fd=req_fd, follow_symlinks=False)
        if not ID.match(stem) or not stat.S_ISREG(st.st_mode) or st.st_size > MAX_REQ:
            os.unlink(name, dir_fd=req_fd)  # 不是合格的請求檔:沒有地方回覆,直接丟掉並記下來
            log(project=project, id=stem[:80], event="dropped", detail="檔名、檔案類型或大小不對")
            return
        jobs_fd = subdir(hr_fd, "jobs", create=True)
        try:
            try:
                os.mkdir(stem, 0o755, dir_fd=jobs_fd)
            except FileExistsError:
                os.unlink(name, dir_fd=req_fd)
                log(project=project, id=stem, event="dropped", detail="同一個 id 已經跑過")
                return
            job_fd = subdir(jobs_fd, stem)
        finally:
            os.close(jobs_fd)
        os.rename(name, "request.json", src_dir_fd=req_fd, dst_dir_fd=job_fd)
        try:
            req = read_json(job_fd, "request.json")
            reason, clean = check(req, stem, ws, caps)
        except (OSError, ValueError) as e:
            reason, clean = f"請求讀不了:{e}", {}
        if not reason and time.time() - st.st_mtime > STALE_S:
            reason = "請求太舊(超過 10 分鐘沒人接手)"
        if reason:
            write_state(job_fd, state="rejected", reason=reason)
            os.close(job_fd)
            log(project=project, id=stem, event="rejected", detail=reason)
            return
        write_state(job_fd, state="queued")
        self.queue.append(Job(project, stem, job_fd, clean))

    # ---------- 執行 ----------

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
            profile = make_profile(job.project, r["cap"], job.id)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            out = os.open("stdout", flags, 0o644, dir_fd=job.fd)
            err = os.open("stderr", flags, 0o644, dir_fd=job.fd)
            env = {"PATH": hubconf.LAUNCHD_PATH, "HOME": str(pathlib.Path.home()),
                   "TMPDIR": os.environ.get("TMPDIR", "/private/tmp"), "LANG": os.environ.get("LANG", "en_US.UTF-8")}
            try:
                job.proc = subprocess.Popen(["/usr/bin/sandbox-exec", "-f", str(profile), *r["argv"]], cwd=r["cwd"],
                                            env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                            start_new_session=True)
            finally:
                os.close(out)
                os.close(err)
        except Exception as e:  # 任何失敗都要回覆,不然客戶端會一直等「排隊中」(10-25 實測:模組不一致時卡住)
            write_state(job.fd, state="rejected", reason=f"啟動失敗:{e!r}"[:500])
            os.close(job.fd)
            log(project=job.project, id=job.id, event="start_failed", detail=str(e)[:300])
            return
        job.started = time.time()
        job.deadline = job.started + r["timeout_s"]
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
            self.kill(job.proc.pid, signal.SIGKILL)  # 主程序結束了,留在背景的子程序也收掉
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

    # ---------- 重啟、清理 ----------

    def each_job(self):
        """(專案, 工作 id, jobs 目錄的路徑)——只走不是 symlink 的目錄。"""
        for project, (ws, _) in eligible().items():
            jobs = ws / ".hub-run" / "jobs"
            if any(p.is_symlink() for p in (ws / ".hub-run", jobs)) or not jobs.is_dir():
                continue
            for d in jobs.iterdir():
                if d.is_dir() and not d.is_symlink():
                    yield project, d.name, d

    def recover(self) -> None:
        """服務重啟:上一輪還標 queued/running 的工作沒人在管了。"""
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
                write_state(fd, state="lost", reason="平台代跑服務重啟了,這個工作沒跑完(結果不明),請重跑")
            finally:
                os.close(fd)
            log(project=project, id=job_id, event="lost")

    def prune(self) -> None:
        cutoff = time.time() - KEEP_S
        for _, _, d in self.each_job():
            try:
                st = json.loads((d / "state.json").read_text())
                old = (d / "state.json").stat().st_mtime < cutoff
            except (OSError, ValueError):
                continue
            if old and st.get("state") in TERMINAL:
                shutil.rmtree(d, ignore_errors=True)
        for p in DIR.glob("*/*.sb"):
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
        except Exception as e:  # 一個專案的怪資料不能讓整個服務掛掉(launchd 會重啟,但正在跑的工作就變 lost)
            log(event="tick_error", detail=repr(e)[:300])
        stamp = hubconf.code_stamp()
        if not r.running and not r.queue and stamp > code_at_start and time.time() - stamp > 60:  # 改完穩定 60 秒才重啟
            log(event="code_changed_restart")  # 舊模組跟新模組混用會壞(10-25 卡住過一個工作):空檔時讓 launchd 重啟
            return 0
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
