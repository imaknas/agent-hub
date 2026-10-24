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
  enforce: {sandbox: false}       第二階段:true = render-project-settings.py 寫 sandbox + 權限規則
  network: [網域]                 sandbox 平台預設之外還要連的
  allow_write: [路徑]             sandbox 平台預設之外還要寫的
  secrets:   [{name, path, env_key?, expires? | max_age_days?, warn_days?, backup?, why?, rotate?}]
                                  這個專案要用的憑證:只寫名稱、位置、到期,**不寫值**(check-secrets.py 每天檢查
                                  在不在、有沒有被備份、快不快到期)。path 相對 runtime(有 runtime 時)否則 workspace,
                                  或 ~/、絕對路徑;env_key = 那個檔是 .env、憑證是其中一行(只檢查有沒有這個 key);
                                  expires 是 "YYYY-MM-DD"(要加引號);max_age_days 照檔案修改時間算(換新就重算);
                                  backup: false 要寫 why(例:重發就好)
  gate: strict                    合併閘門強制:主 checkout 的 main 不能直接 commit(只能 merge-branch);預設只偵測
  jobs:      [{name, at | every, run | (kind: agent + prompt), cwd?, days?, days_of_month?, months?,
               until?, between?, background?, after?, max_attempts_per_day?, legacy_name?}]
    every: 15m / 1h;cwd: runtime(預設,有 runtime 時)| workspace | hub | state(只有 agent job:在自己的
      狀態目錄跑,不載入專案的設定/MCP)
    after: [job](同專案)或 [專案/job]
    connectors: true(只有 agent job):要 claude.ai connector(Upwork 等)→ 用訂閱登入,不用預設的 headless token
      (allow 有 mcp__claude_ai_ 的工具卻沒寫就不合格)
    legacy_name: schedule.yaml 裡的舊名稱——舊名稱還在 schedule.yaml 時這個 job 不採用(切換期間
                 舊的優先,刪掉 schedule.yaml 那一項就切過來;狀態也從舊名稱搬過來)
驗證:未知的欄位、名稱重複、after 指到不存在的 job、run/deploy/test 的第一個字找不到(相對 cwd 的
檔案、PATH 上的指令、或存在的絕對路徑)都算不合格。

用法:registry.py [check [name...]]   驗證並更新快取,印出結果
      registry.py add <name> <workspace> <origin> <memory_bare>        onboard 時新增(已存在就不動)
      registry.py rename <old> <new> <workspace> <origin> <memory_bare> 改名(檔案、prompt 目錄一起搬)
      registry.py apply-updates           套用專案寄來的 manifest.update 信(平台 job,每 15 分鐘)
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402

import yaml  # noqa: E402

CACHE = hubconf.STATE / "registry"
TOP = {"v", "name", "paths", "memory", "privacy", "runtime", "push_on_merge", "mailbox", "publishes",
       "provides", "consumes", "services", "jobs", "enforce", "network", "allow_write", "gate", "secrets"}
SECRET = {"name", "path", "env_key", "expires", "max_age_days", "warn_days", "backup", "why", "rotate"}
JOB = {"name", "at", "every", "run", "kind", "prompt", "cwd", "days", "days_of_month", "months", "until",
       "between", "background", "after", "max_attempts_per_day", "legacy_name", "allow", "connectors"}
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


def validate_secrets(secrets) -> list[str]:
    if secrets is None:
        return []
    if not isinstance(secrets, list):
        return ["secrets 要是 list"]
    errs, seen = [], set()
    for i, sec in enumerate(secrets):
        where = f"secrets[{i}]"
        if not isinstance(sec, dict):
            errs.append(f"{where} 要是 mapping")
            continue
        errs += [f"{where}:未知的欄位 {k}" for k in sec if k not in SECRET]
        for k in ("name", "path"):
            if not isinstance(sec.get(k), str) or not sec.get(k):
                errs.append(f"{where}:{k} 必填")
        if sec.get("name") in seen:
            errs.append(f"{where}:name {sec.get('name')} 重複")
        seen.add(sec.get("name"))
        if "expires" in sec and not (isinstance(sec["expires"], str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", sec["expires"])):
            errs.append(f"{where}:expires 要是加引號的 \"YYYY-MM-DD\"")
        if "expires" in sec and "max_age_days" in sec:
            errs.append(f"{where}:expires 跟 max_age_days 只能寫一個")
        for k in ("max_age_days", "warn_days"):
            if k in sec and (not isinstance(sec[k], int) or isinstance(sec[k], bool) or sec[k] <= 0):
                errs.append(f"{where}:{k} 要是正整數")
        if "backup" in sec and not isinstance(sec["backup"], bool):
            errs.append(f"{where}:backup 要是 true/false")
        if sec.get("backup") is False and not sec.get("why"):
            errs.append(f"{where}:backup: false 要寫 why(為什麼不用備份)")
    return errs


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
    enf = doc.get("enforce")
    if enf is not None and (not isinstance(enf, dict) or set(enf) - {"sandbox"} or not isinstance(enf.get("sandbox", False), bool)):
        errs.append("enforce 只能是 {sandbox: true|false}")
    if doc.get("gate") not in (None, "strict", "detect"):
        errs.append("gate 只能是 strict 或 detect(預設)")
    errs += validate_secrets(doc.get("secrets"))
    for key in ("network", "allow_write"):
        v = doc.get(key)
        if v is not None and (not isinstance(v, list) or not all(isinstance(x, str) and x for x in v)):
            errs.append(f"{key} 要是字串的 list")
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
        if (e := resolves(svc["run"], cwd, hubconf.expand_env((svc.get("env") or {}).get("PATH") or hubconf.LAUNCHD_PATH))):
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
        if "at" in job and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(job["at"])):
            errs.append(f"job {n}:at 要是 HH:MM(加引號)")
        if "every" in job and not EVERY.match(str(job["every"])):
            errs.append(f"job {n}:every 要像 15m / 1h")
        if set(job.get("days") or []) - DAYS:
            errs.append(f"job {n}:days 只能是 {sorted(DAYS)}")
        cwd_key = job.get("cwd") or default_cwd(doc)
        if cwd_key == "state" and job.get("kind") == "agent":
            pass  # agent job 在自己的狀態目錄跑(不載入專案的設定/MCP;例:專案自己的同名 MCP 會蓋掉 claude.ai connector)
        elif cwd_key not in ("runtime", "workspace", "hub"):
            errs.append(f"job {n}:cwd 只能是 runtime / workspace / hub(agent job 另外可以是 state)")
            continue
        if job.get("allow") is not None and (not isinstance(job["allow"], list)
                                             or not all(isinstance(x, str) and x for x in job["allow"])):
            errs.append(f"job {n}:allow 要是權限規則字串的 list")
        if "connectors" in job and not isinstance(job["connectors"], bool):
            errs.append(f"job {n}:connectors 要是 true/false")
        if any(str(x).startswith("mcp__claude_ai_") for x in job.get("allow") or []) and not job.get("connectors"):
            errs.append(f"job {n}:allow 有 claude.ai connector 的工具,要加 connectors: true(預設的 headless token 不載入 connector)")
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
            if job.get("kind") == "agent" and job.get("cwd") == "state":
                cwd = hubconf.STATE / "agent-jobs" / (job.get("legacy_name") or f"{name}-{job['name']}")
            if job.get("kind") == "agent":
                prompt = hubconf.registry_dir() / name / (job.get("prompt") or f"jobs/{job['name']}.md")
                agent_name = job.get("legacy_name") or f"{name}-{job['name']}"
                cmd = (f"{shlex.quote(str(hubconf.ROOT / 'bin' / 'run-agent-job.sh'))} {shlex.quote(agent_name)} "
                       f"{shlex.quote(str(cwd))} {shlex.quote(str(prompt))}")
                if job.get("allow"):  # 無人值守的權限:只准這些(llm.py 的 agent-job 判斷點會改用 dontAsk)
                    cmd = f"AGENT_JOB_ALLOW={shlex.quote(json.dumps(job['allow'], ensure_ascii=False))} {cmd}"
                if job.get("connectors"):  # 要 claude.ai connector:用訂閱登入,不用預設的 headless token
                    cmd = f"LLM_AUTH=subscription {cmd}"
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


def tilde(p: str) -> str:
    home = str(pathlib.Path.home())
    return "~" + p[len(home):] if p.startswith(home) else p


def new_doc(name: str, workspace: str, origin: str, memory_bare: str) -> dict:
    """onboard 時的預設登錄。"""
    return {"v": 1, "name": name,
            "paths": {"workspace": tilde(workspace), "memory_bare": tilde(memory_bare), "origin": origin},
            "memory": True, "privacy": {"reflection": "include"}, "push_on_merge": False,
            "mailbox": {"escalate": True},
            "publishes": [{"kind": "state", "path": "STATE.md", "contract": "state-todo", "v": 1}]}


HEADER = "# 專案登錄(schema v1,見 bin/registry.py)\n"


def cmd_add(name: str, workspace: str, origin: str, memory_bare: str) -> int:
    f = hubconf.registry_dir() / f"{name}.yaml"
    if f.exists():
        print(f"{name} 已經在登錄裡({f}),不覆蓋")
        return 0
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(HEADER + yaml.safe_dump(new_doc(name, workspace, origin, memory_bare), allow_unicode=True,
                                         sort_keys=False, width=200))
    print(f"登錄:{f}")
    return 0


def cmd_rename(old: str, new: str, workspace: str, origin: str, memory_bare: str) -> int:
    reg = hubconf.registry_dir()
    src, dst = reg / f"{old}.yaml", reg / f"{new}.yaml"
    if not src.exists() or dst.exists():
        sys.exit(f"registry rename:{src} 要存在、{dst} 不能存在")
    text = src.read_text()
    doc = yaml.safe_load(text)
    doc["name"] = new
    doc.setdefault("paths", {}).update(workspace=tilde(workspace), origin=origin, memory_bare=tilde(memory_bare))
    # 保留檔頭的註解
    head = "".join(l for l in text.splitlines(keepends=True) if l.startswith("#")) if text.startswith("#") else HEADER
    dst.write_text(head + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=200))
    src.unlink()
    if (reg / old).is_dir():
        (reg / old).rename(reg / new)
    print(f"登錄:{old} → {new}")
    return 0


def cmd_apply_updates() -> int:
    """專案寄來的 manifest.update 信(text = 完整的登錄 YAML):驗證過就寫進登錄 + commit,不過就把錯誤
    寄回那個專案的信箱;兩種都把信結案。只能改寄件專案自己的登錄。零 LLM。"""
    import sqlite3
    bus = hubconf.bus_db()
    if not bus.exists():
        return 0
    hub = hubconf.hub_project()
    c = sqlite3.connect(bus, timeout=30)
    rows = c.execute("SELECT id, source, payload FROM events m WHERE m.type='message' AND m.to_project=?"
                     " AND NOT EXISTS(SELECT 1 FROM events x WHERE x.type='message.done' AND x.ref=m.id) ORDER BY seq",
                     (hub,)).fetchall()
    hubbus = ["/usr/bin/python3", str(hubconf.ROOT / "bin" / "hubbus")]

    def reply(to, mid, text, note):
        subprocess.run([*hubbus, "send", "--from", hub, "--to", to, "--reply-to", mid, "--type", "info", "--text", text],
                       capture_output=True)
        subprocess.run([*hubbus, "done", mid, "--note", note], capture_output=True, cwd=hubconf.ROOT)

    for mid, sender, pl in rows:
        p = json.loads(pl or "{}")
        if p.get("type") != "manifest.update":
            continue
        try:
            doc = yaml.safe_load(p.get("text") or "")
            errs = validate(doc, sender)
        except yaml.YAMLError as e:
            doc, errs = None, [f"YAML 讀不了:{e}"]
        if isinstance(doc, dict) and doc.get("name") != sender:
            errs.append(f"只能改自己的登錄(寄件人是 {sender},YAML 的 name 是 {doc.get('name')})")
        if errs:
            reply(sender, mid, "manifest.update 沒有套用,登錄沒變:\n- " + "\n- ".join(errs), "退回:" + "; ".join(errs)[:200])
            print(f"{sender}:manifest.update {mid} 退回({len(errs)} 個問題)")
            continue
        f = hubconf.registry_dir() / f"{sender}.yaml"
        f.write_text(f"# 專案登錄(schema v1,見 bin/registry.py)。最後一次由 manifest.update {mid} 更新\n"
                     + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=200))
        r = subprocess.run([str(hubconf.ROOT / "bin" / "commit-hub-files.sh"), f"registry: {sender} manifest.update {mid}",
                            f"projects/{sender}.yaml"], capture_output=True, text=True)
        reply(sender, mid, "manifest.update 已套用" + ("" if r.returncode == 0 else "(commit 失敗,檔案已寫入)") +
              ";下一個排程 tick 生效。", "已套用")
        print(f"{sender}:manifest.update {mid} 已套用")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["add"] and len(args) == 5:
        return cmd_add(*args[1:])
    if args[:1] == ["rename"] and len(args) == 6:
        return cmd_rename(*args[1:])
    if args[:1] == ["apply-updates"]:
        return cmd_apply_updates()
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
