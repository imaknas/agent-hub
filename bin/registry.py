#!/usr/bin/env python3
"""專案登錄:<registry_dir>/<name>.yaml(一個專案一個檔,hub.json 指定目錄)→ 驗證 → JSON 快取。

平台的程式碼裡不寫專案名稱:專案的 job、service、隱私標籤、路徑都在登錄裡。scheduler 每個 tick
呼叫 refresh():每個檔各自驗證,合格的寫進 .state/registry/<name>.json(上一份合格的快取),
不合格的沿用上一份合格的、錯誤記在 index.json 的 errors(狀態頁標紅),**不影響其他專案**。
index.json 是給只能用標準函式庫的程式(hubconf.py、hubbus)讀的。

schema v1(contracts/project.v1.md 有完整說明):
  v: 1                      必填
  name                      必填,= 檔名
  paths: {workspace, memory_bare, origin}       workspace 必填(平台自己除外)
  memory: true              參加 memory 追蹤
  privacy: {reflection: include|stats_only|exclude}
  runtime: {deploy, test}   有 runtime = 有 <services_root>/<name>
  push_on_merge: false
  mailbox: {escalate: true}
  publishes: [{kind, path, contract, v}]
  provides:  [{contract, v, schema}]           schema 相對 workspace
  services:  [{name, run, label?, keepalive?, throttle_s?, env?, cwd?, expose?}]
  jobs:      [{name, at | every, run | (kind: agent + prompt), cwd?, days?, days_of_month?, months?,
               until?, between?, background?, after?, max_attempts_per_day?, legacy_name?}]
    every: 15m / 1h;cwd: runtime(預設,有 runtime 時)| workspace | hub
    after: [job](同專案)或 [專案/job]
    legacy_name: schedule.yaml 裡的舊名稱——舊名稱還在 schedule.yaml 時這個 job 不採用(切換期間
                 舊的優先,刪掉 schedule.yaml 那一項就切過來;狀態也從舊名稱搬過來)
驗證:未知的欄位、名稱重複、after 指到不存在的 job、run/deploy/test 的第一個字找不到(相對 cwd 的
檔案、PATH 上的指令、或存在的絕對路徑)都算不合格。

用法:registry.py [check [name...]]   驗證並更新快取,印出結果
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import shlex
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402

import yaml  # noqa: E402

CACHE = hubconf.STATE / "registry"
TOP = {"v", "name", "paths", "memory", "privacy", "runtime", "push_on_merge", "mailbox", "publishes",
       "provides", "consumes", "services", "jobs"}
JOB = {"name", "at", "every", "run", "kind", "prompt", "cwd", "days", "days_of_month", "months", "until",
       "between", "background", "after", "max_attempts_per_day", "legacy_name"}
SERVICE = {"name", "run", "label", "keepalive", "interval_s", "throttle_s", "env", "cwd", "log", "expose"}
DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
EVERY = re.compile(r"^(\d+)(m|h)$")


def dirs(doc: dict) -> dict[str, pathlib.Path | None]:
    """這個專案的 runtime / workspace / hub 目錄。"""
    name = doc.get("name", "")
    if name == hubconf.hub_project():
        return {"runtime": hubconf.ROOT, "workspace": hubconf.ROOT, "hub": hubconf.ROOT}
    ws = hubconf.path((doc.get("paths") or {}).get("workspace"))
    rt = hubconf.p("services_root") / name if doc.get("runtime") is not None else None
    return {"runtime": rt, "workspace": ws, "hub": hubconf.ROOT}


def default_cwd(doc: dict) -> str:
    return "runtime" if doc.get("runtime") is not None else "workspace"


def resolves(cmd: str, cwd: pathlib.Path | None, search: str | None = None) -> str | None:
    """第一個字找得到嗎;找不到回傳錯誤訊息。"""
    try:
        words = shlex.split(cmd)
    except ValueError as e:
        return f"指令解析不了:{e}"
    if not words:
        return "指令是空的"
    first = words[0]
    if "=" in first and not first.startswith(("/", ".")):  # VAR=x cmd
        return None
    if first.startswith("/"):
        if first.startswith(str(pathlib.Path.home())):
            return f"不准用家目錄底下的絕對路徑({first});改成相對 cwd"
        return None if pathlib.Path(first).exists() else f"{first} 不存在"
    if "/" in first:
        if cwd is None:
            return f"{first}:沒有 cwd 可以找"
        return None if (cwd / first).exists() else f"{cwd / first} 不存在"
    found = shutil.which(first, path=search or f"{hubconf.LAUNCHD_PATH}:{os.environ.get('PATH', '')}")
    return None if found or first in ("cd", "test", "[", "true") else f"PATH 上找不到 {first}"


def validate(doc, stem: str) -> list[str]:
    errs: list[str] = []
    if not isinstance(doc, dict):
        return ["不是一個 YAML mapping"]
    if doc.get("v") != 1:
        errs.append(f"v 要是 1(現在是 {doc.get('v')!r})")
    if doc.get("name") != stem:
        errs.append(f"name({doc.get('name')!r})要跟檔名 {stem} 一樣")
    errs += [f"未知的欄位 {k}" for k in doc if k not in TOP]
    is_hub = stem == hubconf.hub_project()
    paths = doc.get("paths") or {}
    if not is_hub and not paths.get("workspace"):
        errs.append("paths.workspace 必填")
    if (doc.get("privacy") or {}).get("reflection", "include") not in ("include", "stats_only", "exclude"):
        errs.append("privacy.reflection 只能是 include / stats_only / exclude")
    d = dirs(doc)
    rt = doc.get("runtime")
    if rt is not None:
        if not isinstance(rt, dict):
            errs.append("runtime 要是 mapping")
        elif d["runtime"] and not d["runtime"].is_dir():
            errs.append(f"runtime 目錄 {d['runtime']} 不存在")
    for key in ("publishes", "provides", "services", "jobs"):
        if not isinstance(doc.get(key, []), list):
            errs.append(f"{key} 要是 list")
    names = set()
    for svc in doc.get("services") or []:
        n = svc.get("name")
        errs += [f"service {n}:未知的欄位 {k}" for k in svc if k not in SERVICE]
        if not n or not svc.get("run"):
            errs.append(f"service {n}:name、run 必填")
            continue
        cwd = d.get(svc.get("cwd") or default_cwd(doc))
        if (e := resolves(svc["run"], cwd, (svc.get("env") or {}).get("PATH") or hubconf.LAUNCHD_PATH)):
            errs.append(f"service {n}:{e}")
    jobs = doc.get("jobs") or []
    for job in jobs:
        n = job.get("name")
        errs += [f"job {n}:未知的欄位 {k}" for k in job if k not in JOB]
        if not n or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", str(n)):
            errs.append(f"job {n!r}:name 要是小寫英數字跟 -")
            continue
        if n in names:
            errs.append(f"job {n}:名稱重複")
        names.add(n)
        if ("at" in job) == ("every" in job):
            errs.append(f"job {n}:at 跟 every 要剛好一個")
        if "at" in job and not re.fullmatch(r"\d{2}:\d{2}", str(job["at"])):
            errs.append(f"job {n}:at 要是 HH:MM(加引號)")
        if "every" in job and not EVERY.match(str(job["every"])):
            errs.append(f"job {n}:every 要像 15m / 1h")
        if set(job.get("days") or []) - DAYS:
            errs.append(f"job {n}:days 只能是 {sorted(DAYS)}")
        cwd_key = job.get("cwd") or default_cwd(doc)
        if cwd_key not in ("runtime", "workspace", "hub"):
            errs.append(f"job {n}:cwd 只能是 runtime / workspace / hub")
            continue
        if job.get("kind", "command") == "agent":
            prompt = hubconf.registry_dir() / stem / (job.get("prompt") or f"jobs/{n}.md")
            if not prompt.exists():
                errs.append(f"job {n}:prompt {prompt} 不存在")
        elif job.get("kind", "command") != "command":
            errs.append(f"job {n}:kind 只能是 command / agent")
        elif not job.get("run"):
            errs.append(f"job {n}:run 必填")
        elif (e := resolves(job["run"], d.get(cwd_key))):
            errs.append(f"job {n}:{e}")
    for job in jobs:
        for dep in job.get("after") or []:
            if "/" not in dep and dep not in names:
                errs.append(f"job {job.get('name')}:after 的 {dep} 不在這個專案")
    for key in ("deploy", "test"):
        if isinstance(rt, dict) and rt.get(key):
            base = d["workspace"] if key == "test" else d["workspace"]
            if (e := resolves(rt[key], base)):
                errs.append(f"runtime.{key}:{e}")
    return errs


def refresh(only: list[str] | None = None) -> dict:
    """驗證全部登錄、更新快取跟 index.json,回傳 index。"""
    CACHE.mkdir(parents=True, exist_ok=True)
    reg = hubconf.registry_dir()
    files = sorted(reg.glob("*.yaml")) if reg.is_dir() else []
    projects, errors = {}, {}
    for f in files:
        stem = f.stem
        try:
            doc = yaml.safe_load(f.read_text())
            errs = validate(doc, stem)
        except yaml.YAMLError as e:
            doc, errs = None, [f"YAML 讀不了:{e}"]
        cache = CACHE / f"{stem}.json"
        if not errs:
            cache.write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str))
            projects[stem] = doc
        else:
            errors[stem] = errs
            if cache.exists():  # 沿用上一份合格的
                projects[stem] = json.loads(cache.read_text())
    # 登錄檔被刪掉的專案:快取也拿掉(刪掉登錄 = 拿掉這個專案)
    for c in CACHE.glob("*.json"):
        if c.name != "index.json" and c.stem not in {f.stem for f in files}:
            c.unlink()
    index = {"v": 1, "generated": datetime.datetime.now().isoformat(timespec="seconds"),
             "projects": projects, "errors": errors}
    tmp = CACHE / "index.json.tmp"
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=1, default=str))
    tmp.replace(CACHE / "index.json")
    return index


def every_minutes(s: str) -> int:
    n, unit = EVERY.match(s).groups()
    return int(n) * (60 if unit == "h" else 1)


def scheduler_jobs(index: dict, legacy_names: set[str]) -> tuple[list[dict], list[str], dict[str, str]]:
    """登錄 → scheduler.py 的 job dict。回傳 (jobs, 因為舊名稱還在 schedule.yaml 而沒採用的, 新名稱→舊名稱)。"""
    out, skipped, renames = [], [], {}
    for name, doc in sorted(index["projects"].items()):
        d = dirs(doc)
        for job in doc.get("jobs") or []:
            full = f"{name}/{job['name']}"
            if job.get("legacy_name") in legacy_names:
                skipped.append(full)
                continue
            if job.get("legacy_name"):
                renames[full] = job["legacy_name"]
            cwd = d.get(job.get("cwd") or default_cwd(doc))
            if job.get("kind") == "agent":
                prompt = hubconf.registry_dir() / name / (job.get("prompt") or f"jobs/{job['name']}.md")
                agent_name = job.get("legacy_name") or f"{name}-{job['name']}"
                cmd = (f"{shlex.quote(str(hubconf.ROOT / 'bin' / 'run-agent-job.sh'))} {shlex.quote(agent_name)} "
                       f"{shlex.quote(str(cwd))} {shlex.quote(str(prompt))}")
            else:
                cmd = f"cd {shlex.quote(str(cwd))} && {job['run']}"
            j = {"name": full, "command": cmd, "project": name}
            if job.get("kind") == "agent":
                j["agent_name"] = agent_name
            if "at" in job:
                j["time"] = str(job["at"])
            else:
                j["every_minutes"] = every_minutes(str(job["every"]))
            for src, dst in (("days", "days_of_week"), ("days_of_month", "days_of_month"), ("months", "months"),
                             ("until", "end_date"), ("between", "between"), ("background", "background"),
                             ("max_attempts_per_day", "max_attempts_per_day")):
                if src in job:
                    j[dst] = job[src]
            if job.get("after"):
                j["after"] = [a if "/" in a else f"{name}/{a}" for a in job["after"]]
            out.append(j)
    return out, skipped, renames


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "check":
        args = args[1:]
    index = refresh()
    names = args or sorted(set(index["projects"]) | set(index["errors"]))
    bad = 0
    for n in names:
        if n in index["errors"]:
            bad += 1
            print(f"✗ {n}" + ("(沿用上一份合格的)" if n in index["projects"] else ""))
            for e in index["errors"][n]:
                print(f"    {e}")
        elif n in index["projects"]:
            doc = index["projects"][n]
            print(f"✓ {n}:{len(doc.get('jobs') or [])} 個 job、{len(doc.get('services') or [])} 個 service")
        else:
            bad += 1
            print(f"? {n}:登錄裡沒有")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
