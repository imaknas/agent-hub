#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""專案登錄:<registry_dir>/<name>.yaml(一個專案一個檔,hub.json 指定目錄)→ 驗證 → JSON 快取。

平台的程式碼裡不寫專案名稱:專案的 job、service、隱私標籤、路徑都在登錄裡。scheduler 每個 tick
呼叫 refresh():每個檔各自驗證,合格的寫進 .state/registry/<name>.json(上一份合格的快取),
不合格的沿用上一份合格的、錯誤記在 index.json 的 errors(狀態頁標紅),**不影響其他專案**。
index.json 是給只能用標準函式庫的程式(hubconf.py、hubbus)讀的。

schema v1(contracts/project.v1.md 有完整說明):
  v: 1                      必填
  name                      必填,= 檔名
  type: project | idea      預設 project。idea = Muninn 替想法開的探討用專案(bin/create-idea-sandbox.py 建,
                            名稱是想法的代號、一般專案名稱的規則(早期的叫 idea-<n>),沒有 GitHub remote;
                            bin/upgrade-idea.py 升級成 project)。是不是想法沙盒只看這個欄位,不看名稱
  purpose                   想法本身(一兩句);type: idea 必填,lease 的系統提示會帶上
  paths: {workspace, memory_bare, origin}       workspace 必填(平台自己除外)
  memory: true              參加 memory 追蹤
  privacy: {reflection: include|stats_only|exclude}
  runtime: {deploy, test}   有 runtime = 有 <services_root>/<name>
  push_on_merge: false
  commits: conventional      commit 標題用 Conventional Commits:merge-branch 合併前檢查(bin/commit_title.py),
                            規則檔自動帶 templates/rules/conventional-commits.md。沒寫 = 不檢查
  mailbox: {escalate: true}
  publishes: [{kind, path, contract, v}]
  provides:  [{contract, v, schema}]           schema 相對 workspace
  services:  [{name, run, label?, keepalive?, throttle_s?, env?, cwd?, expose?}]
  enforce: {sandbox: false, capabilities: [], work: false}
                                  第二階段:sandbox true = render-project-settings.py 寫 sandbox + 權限規則;
                                  capabilities = 平台的能力(hub.json capabilities,例 gpu),要 sandbox: true;
                                  沙盒裡用 hub-run --cap <能力> 透過平台代跑(docs/design/capabilities.md);
                                  work = 深談(租約)可以用 work 模式動手(資料夾內寫檔、指令每次問),要 sandbox: true。
                                  Knas 明確授權的紀錄(10-25):想法沙盒建立時寫上、升級帶過去,其他專案要他同意才加
  network: [網域]                 sandbox 平台預設之外還要連的
  allow_write: [路徑]             sandbox 平台預設之外還要寫的
    type、enforce、network、allow_write、privacy 是安全欄位:專案寄的 manifest.update 改到就整封退回
    (專案不能替自己關 sandbox、開大範圍、加能力);要改請 agent-hub session(Knas 確認)
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
      registry.py add <name> <workspace> <origin> <memory_bare> [--extra <JSON>]
                                          onboard 時新增(已存在就不動;extra 蓋過預設欄位,驗證不過不寫)
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
TOP = {"v", "name", "type", "purpose", "paths", "memory", "privacy", "runtime", "push_on_merge", "commits", "mailbox",
       "publishes", "provides", "consumes", "services", "jobs", "enforce", "network", "allow_write", "gate", "secrets"}
SECRET = {"name", "path", "env_key", "expires", "max_age_days", "warn_days", "backup", "why", "rotate"}
JOB = {"name", "at", "every", "run", "kind", "prompt", "cwd", "days", "days_of_month", "months", "until",
       "between", "background", "after", "before", "max_attempts_per_day", "legacy_name", "allow", "connectors"}
SERVICE = {"name", "run", "label", "keepalive", "interval_s", "throttle_s", "env", "cwd", "log", "expose"}
DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
EVERY = re.compile(r"^(\d+)(m|h)$")


def dirs(doc: dict) -> dict[str, pathlib.Path | None]:
    """這個專案的 runtime / workspace / hub 目錄。"""
    name = doc.get("name", "")
    if name == hubconf.hub_project() or doc.get("type") == "module":
        return {"runtime": hubconf.ROOT, "workspace": hubconf.ROOT, "hub": hubconf.ROOT}
    ws = hubconf.path((doc.get("paths") or {}).get("workspace"))
    rt = hubconf.p("services_root") / name if doc.get("runtime") is not None else None
    return {"runtime": rt, "workspace": ws, "hub": hubconf.ROOT}


def default_cwd(doc: dict) -> str:
    if doc.get("type") == "module":
        return "hub"
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


def capabilities() -> dict[str, dict]:
    return hubconf.capabilities()


def validate_enforce(doc: dict) -> list[str]:
    enf = doc.get("enforce")
    if enf is None:
        return []
    if not isinstance(enf, dict) or set(enf) - {"sandbox", "capabilities", "work"} or not isinstance(enf.get("sandbox", False), bool):
        return ["enforce 只能是 {sandbox: true|false, capabilities: [能力], work: true|false}"]
    work = enf.get("work", False)
    if not isinstance(work, bool):
        return ["enforce.work 要是 true 或 false"]
    if work and not enf.get("sandbox"):  # 深談能寫入,靠的就是 OS sandbox 擋資料夾外的寫入
        return ["enforce.work 要搭配 enforce.sandbox: true"]
    caps = enf.get("capabilities")
    if caps is None:
        return []
    if not isinstance(caps, list) or not all(isinstance(c, str) for c in caps):
        return ["enforce.capabilities 要是字串的 list"]
    errs, have = [], capabilities()
    errs += [f"enforce.capabilities:平台沒有 {c} 這個能力(有的:{', '.join(sorted(have)) or '無'})" for c in caps if c not in have]
    if caps and not enf.get("sandbox"):
        errs.append("enforce.capabilities 要搭配 enforce.sandbox: true")
    # 不看 type:想法沙盒升級成正式專案後,Knas 同意過的能力跟著留(10-25 m10251626)。同意的紀錄就是 enforce 本身——
    # 平台擁有的欄位,專案自己的 manifest.update 改不了(PLATFORM_OWNED);能力能做什麼由平台的 Seatbelt profile 決定,
    # 跟專案是哪一種無關
    return errs


PLATFORM_OWNED = ("type", "enforce", "network", "allow_write", "privacy")


def platform_owned_changes(new: dict, cur: dict) -> list[str]:
    """manifest.update 不能改的安全欄位:跟現在的登錄不一樣就列出來。"""
    return [f"{k} 是安全欄位,只能由 agent-hub 改(要 Knas 確認);這封改到了它" for k in PLATFORM_OWNED
            if new.get(k) != cur.get(k)]


def validate(doc, stem: str) -> list[str]:
    errs: list[str] = []
    if not isinstance(doc, dict):
        return ["不是一個 YAML mapping"]
    if doc.get("v") != 1:
        errs.append(f"v 要是 1(現在是 {doc.get('v')!r})")
    if doc.get("name") != stem:
        errs.append(f"name({doc.get('name')!r})要跟檔名 {stem} 一樣")
    errs += [f"未知的欄位 {k}" for k in doc if k not in TOP]
    if doc.get("type", "project") not in ("project", "idea"):
        errs.append("type 只能是 project 或 idea")
    if doc.get("purpose") is not None and (not isinstance(doc["purpose"], str) or not doc["purpose"].strip()):
        errs.append("purpose 要是非空字串")
    if doc.get("type") == "idea" and not doc.get("purpose"):
        errs.append("type: idea 要寫 purpose(想法本身)")
    is_hub = stem == hubconf.hub_project()
    paths = doc.get("paths") or {}
    if not is_hub and not paths.get("workspace"):
        errs.append("paths.workspace 必填")
    errs += validate_enforce(doc)
    if doc.get("commits") not in (None, "conventional"):
        errs.append("commits 只能是 conventional(沒寫 = 不檢查 commit 標題)")
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
    errs += validate_runs(doc, stem, d)
    for key in ("deploy", "test"):
        if isinstance(rt, dict) and rt.get(key):
            base = d["workspace"] if key == "test" else d["workspace"]
            if (e := resolves(rt[key], base)):
                errs.append(f"runtime.{key}:{e}")
    return errs


def validate_runs(doc: dict, stem: str, d: dict) -> list[str]:
    """services 跟 jobs(專案登錄跟模組共用)。"""
    errs: list[str] = []
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
        for key in ("after", "before"):
            v = job.get(key)
            if v is not None and (not isinstance(v, list) or not all(isinstance(x, str) and x for x in v)):
                errs.append(f"job {job.get('name')}:{key} 要是 job 名稱的 list")
                continue
            for dep in v or []:
                if "/" not in dep and dep not in names:
                    errs.append(f"job {job.get('name')}:{key} 的 {dep} 不在這個專案(別的專案/模組要寫 <名稱>/<job>)")
    return errs


# ---------- 模組(docs/design/new-user.md 3.2)----------
# 可以不開的功能(個人助理、備份、mirror、每週回顧…):平台 repo 的 modules/<名稱>.yaml,格式跟專案登錄的 jobs/services
# 一樣(cwd 一律是 hub);hub.json 的 modules.<名稱>: true 才採用。job 在排程裡叫 <模組>/<job>。
# 搬進模組的 job 用 legacy_name 寫舊的全名(例:agent-hub/backup),排程狀態照搬、別人的 after: 也照樣對得上。

MODULES_DIR = hubconf.ROOT / "modules"
MODULE_TOP = {"v", "name", "type", "description", "requires_config", "jobs", "services", "secrets", "consumes"}


def validate_module(doc, stem: str) -> list[str]:
    if not isinstance(doc, dict):
        return ["不是一個 YAML mapping"]
    errs = [f"未知的欄位 {k}" for k in doc if k not in MODULE_TOP]
    if doc.get("v") != 1:
        errs.append("v 要是 1")
    if doc.get("name") != stem:
        errs.append(f"name({doc.get('name')!r})要跟檔名 {stem} 一樣")
    if doc.get("type") != "module":
        errs.append("type 要是 module")
    if not isinstance(doc.get("description"), str) or not doc["description"].strip():
        errs.append("description 必填")
    req = doc.get("requires_config") or []
    if not isinstance(req, list):
        errs.append("requires_config 要是 hub.json 鍵的 list")
    else:
        errs += [f"要先在 hub.json 設定 {k}" for k in req if hubconf.get(k) in (None, "", {}, [])]
    for key in ("jobs", "services"):
        for x in doc.get(key) or []:
            if isinstance(x, dict) and x.get("cwd", "hub") != "hub":
                errs.append(f"{key[:-1]} {x.get('name')}:模組的 cwd 只能是 hub")
            if key == "jobs" and isinstance(x, dict) and x.get("kind", "command") != "command":
                errs.append(f"job {x.get('name')}:模組只能有 command 的 job")
    errs += validate_secrets(doc.get("secrets"))   # 模組自己的憑證(平台的;沒開的模組不檢查)
    for c in doc.get("consumes") or []:   # 模組讀別人的契約(例:助理模組讀助理的轉交格式);沒開的模組不比對
        if not isinstance(c, dict) or not all(c.get(k) for k in ("contract", "v", "copy")):
            errs.append("consumes 的每一項要有 contract、v、copy")
    return errs + validate_runs(doc, stem, dirs(doc))


def enabled_modules() -> list[str]:
    m = hubconf.get("modules") or {}
    return sorted(k for k, v in m.items() if not k.startswith("_") and v is True)


def load_modules() -> tuple[dict, dict]:
    """(開著的模組 → doc, 錯誤)。開了卻沒有檔案、或不合格:記錯誤、不採用(它的 job/service 就不存在)。"""
    mods, errors = {}, {}
    for name in enabled_modules():
        f = MODULES_DIR / f"{name}.yaml"
        if not f.exists():
            errors[f"module:{name}"] = [f"hub.json 開了模組 {name},但沒有 modules/{name}.yaml"]
            continue
        try:
            doc = yaml.safe_load(f.read_text())
            errs = validate_module(doc, name)
        except yaml.YAMLError as e:
            doc, errs = None, [f"YAML 讀不了:{e}"]
        if errs:
            errors[f"module:{name}"] = errs
        else:
            mods[name] = doc
    return mods, errors


def job_cycles(index: dict) -> list[str]:
    """after:/before: 串起來有沒有繞圈(有的話排程器會永遠等)。"""
    jobs, _, _ = scheduler_jobs(index, set())
    graph = {j["name"]: set(j.get("after") or []) for j in jobs}
    out, state = [], {}

    def visit(n, path):
        state[n] = 1
        for dep in graph.get(n, ()):
            if dep not in graph:
                continue
            if state.get(dep) == 1:
                out.append(" → ".join(path[path.index(dep):] + [dep]) if dep in path else f"{n} → {dep}")
            elif not state.get(dep):
                visit(dep, path + [dep])
        state[n] = 2
    for n in sorted(graph):
        if not state.get(n):
            visit(n, [n])
    return out


def roster_mismatch(doc, text: str) -> list[str]:
    """hubconf.roster()(不用 PyYAML 的逐行解析:recover、sync-all-memory、名冊用它)讀到的 paths 要跟 YAML 一樣,
    不然那些腳本會默默拿到空的或錯的路徑。"""
    if not isinstance(doc, dict) or not isinstance(doc.get("paths") or {}, dict):
        return []
    want = {k: (None if v is None else str(v)) for k, v in (doc.get("paths") or {}).items()}
    got = hubconf.roster_paths(text)
    if got == want:
        return []
    return [f"paths 要寫成一行一個 key 的區塊格式(`paths:` 下一行縮排 `workspace: …`):"
            f"不用 YAML 的讀法讀到 {got},YAML 是 {want}"]


def refresh(only: list[str] | None = None) -> dict:
    """驗證全部登錄、更新快取跟 index.json,回傳 index。"""
    CACHE.mkdir(parents=True, exist_ok=True)
    reg = hubconf.registry_dir()
    files = sorted(reg.glob("*.yaml")) if reg.is_dir() else []
    # 平台自己的登錄不管 registry_dir 在哪都讀,而且一律用平台 repo 那份:使用者的登錄裡同名的檔(例:搬家時多複製的)
    # 不採用,不然平台更新了自己的登錄也不會生效
    plat = hubconf.platform_registry()
    if plat.exists():
        files = sorted([f for f in files if f.stem != plat.stem] + [plat])
    projects, errors = {}, {}
    for f in files:
        stem = f.stem
        try:
            text = f.read_text()
            doc = yaml.safe_load(text)
            errs = validate(doc, stem) + roster_mismatch(doc, text)
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
    modules, mod_errors = load_modules()
    errors.update(mod_errors)
    index = {"v": 1, "generated": datetime.datetime.now().isoformat(timespec="seconds"),
             "projects": projects, "modules": modules, "errors": errors}
    if (cycles := job_cycles(index)):
        errors["(jobs)"] = [f"after:/before: 繞了一圈,這幾個 job 會永遠互等:{c}" for c in cycles]
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
    before: dict[str, list[str]] = {}
    docs = sorted(index["projects"].items()) + sorted((index.get("modules") or {}).items())
    for name, doc in docs:
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
            for t in job.get("before") or []:
                before.setdefault(t if "/" in t else f"{name}/{t}", []).append(full)
            out.append(j)
    # before: [X] = X 的 after 多一個我(模組把自己插進核心流程中間,核心不用知道它)。X 不存在(那個模組沒開)就不管
    by_name = {j["name"]: j for j in out}
    for target, deps in before.items():
        if target in by_name:
            by_name[target]["after"] = list(dict.fromkeys((by_name[target].get("after") or []) + deps))
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


def cmd_add(name: str, workspace: str, origin: str, memory_bare: str, extra: str | None = None) -> int:
    """extra:JSON 物件,蓋過預設登錄的欄位(例:想法沙盒的 type/purpose/enforce/privacy)。驗證不過就不寫。"""
    f = hubconf.registry_dir() / f"{name}.yaml"
    if f.exists():
        print(f"{name} 已經在登錄裡({f}),不覆蓋")
        return 0
    doc = new_doc(name, workspace, origin, memory_bare)
    if extra:
        doc.update(json.loads(extra))
        errs = validate(doc, name)
        if errs:
            print(f"登錄不合格,不寫入:{'; '.join(errs)}", file=sys.stderr)
            return 1
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(HEADER + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=200))
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
    hubbus = [sys.executable, str(hubconf.ROOT / "bin" / "hubbus")]

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
        elif isinstance(doc, dict):
            cur_f = hubconf.registry_dir() / f"{sender}.yaml"
            cur = (yaml.safe_load(cur_f.read_text()) or {}) if cur_f.exists() else {}
            errs += platform_owned_changes(doc, cur)
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
    if args[:1] == ["add"] and len(args) == 7 and args[5] == "--extra":
        return cmd_add(*args[1:5], extra=args[6])
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
