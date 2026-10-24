#!/usr/bin/env python3
"""登錄宣告的憑證(projects/<p>.yaml 的 secrets:)在不在、有沒有被備份、快不快到期(平台 job agent-hub/secrets,每天)。

憑證的分工(2026-10-24 Knas 決定):機器層級的(claude、Discord、B2、備份密碼…)平台管,宣告在 agent-hub 的登錄;
專案自己服務的金鑰專案管,宣告在自己的登錄(manifest.update);到外部服務發新的、撤銷、換掉是 Knas 做。
登錄只寫名稱、位置、到期,**不寫值**;這支也不讀值、不印值:
  - 在不在:檔案存在、不是空的;有 env_key 的(.env 裡的一行)只看那個 key 有沒有、等號後面是不是空的
  - 備份:backup 沒寫 false 的,照 backup.covers()(跟每晚的備份同一套規則)
  - 到期:expires 日期,或檔案修改時間 + max_age_days;剩不到 warn_days(預設 14)天就報
  - 權限:群組/其他人讀得到只印出來,不算問題
  - 沒宣告的:各專案的 runtime / workspace(跟平台的 .secrets/)裡檔名像憑證、卻沒宣告的檔案,只列出來提醒
    (不算問題,也不讀內容)——要嘛宣告,要嘛確認它不是憑證
有問題就發 Discord(同一組只發一次,解決後發一次恢復)、exit 1(排程器記失敗,狀態頁紅)。

用法:check-secrets.py [專案...]
"""
from __future__ import annotations

import datetime
import os
import pathlib
import re
import stat
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import alert  # noqa: E402
import backup  # noqa: E402
import hubconf  # noqa: E402
import registry  # noqa: E402

WARN_DAYS = 14
LOOKS_SECRET = re.compile(r"(^\.env($|\.)|token|secret|credential|oauth|api[-_]?key|password|\.pem$|\.key$|\.p12$)", re.I)
NOT_SECRET = re.compile(r"(\.(example|sample|template|py|pyc|ts|tsx|js|mjs|cjs|md|sh|html|css|map|lock)$)", re.I)
SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "dist", "build", "__pycache__", ".next", ".agent-memory", ".claude",
             "worktrees", "logs", ".logs"}


def locate(doc: dict, raw: str) -> pathlib.Path | None:
    if raw.startswith(("~", "/")):
        return hubconf.path(raw)
    d = registry.dirs(doc)
    base = d["runtime"] if doc.get("runtime") is not None else d["workspace"]
    return base / raw if base else None


def has_key(path: pathlib.Path, key: str) -> bool:
    """.env 裡有沒有 `KEY=<非空>`。只比對 key,值不留、不印。"""
    for line in path.read_text(errors="replace").splitlines():
        k, sep, v = line.strip().removeprefix("export ").partition("=")
        if sep and k.strip() == key:
            return bool(v.strip().strip("'\""))
    return False


def check(project: str, doc: dict, sec: dict, today: datetime.date) -> tuple[list[str], list[str]]:
    """(問題, 備註)"""
    name = f"{project}:{sec['name']}"
    path = locate(doc, sec["path"])
    if not path or not path.exists():
        return [f"{name} 不在({path or sec['path']})" + (f"——{sec['rotate']}" if sec.get("rotate") else "")], []
    probs, notes = [], []
    if path.is_file() and path.stat().st_size == 0:
        probs.append(f"{name} 是空檔({path})")
    if sec.get("env_key") and path.is_file() and not has_key(path, sec["env_key"]):
        probs.append(f"{name}:{path} 裡沒有 {sec['env_key']}(或是空的)")
    if sec.get("backup", True) and not backup.covers(path):
        probs.append(f"{name} 沒有被備份({path})——加進 hub.json backup.include,或在登錄寫 backup: false + why")
    exp = None
    if sec.get("expires"):
        exp = datetime.date.fromisoformat(sec["expires"])
    elif sec.get("max_age_days"):
        exp = datetime.date.fromtimestamp(path.stat().st_mtime) + datetime.timedelta(days=sec["max_age_days"])
    if exp:
        left = (exp - today).days
        how = f"——{sec['rotate']}" if sec.get("rotate") else ""
        if left < 0:
            probs.append(f"{name} 已經過期({exp}){how}")
        elif left <= sec.get("warn_days", WARN_DAYS):
            probs.append(f"{name} {left} 天後到期({exp}){how}")
        notes.append(f"{name}:{exp} 到期(還有 {left} 天)")
    mode = path.stat().st_mode
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        notes.append(f"{name}:群組/其他人讀得到({oct(mode & 0o777)},建議 chmod 600)")
    return probs, notes


def undeclared(project: str, doc: dict, declared: set[pathlib.Path]) -> list[pathlib.Path]:
    """runtime / workspace 底下(三層內)檔名像憑證、沒宣告的檔案。平台自己只看 .secrets/。"""
    d = registry.dirs(doc)
    roots = [hubconf.ROOT / ".secrets"] if project == hubconf.hub_project() else \
        [r for r in (d["runtime"], d["workspace"]) if r]
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(pathlib.Path(dirpath).relative_to(root).parts)
            dirnames[:] = [] if depth >= 3 else [x for x in dirnames if x not in SKIP_DIRS]
            for fn in filenames:
                path = pathlib.Path(dirpath) / fn
                if LOOKS_SECRET.search(fn) and not NOT_SECRET.search(fn) and path.resolve() not in declared:
                    found.append(path)
    return sorted(set(found))


def main(argv: list[str]) -> int:
    today = datetime.date.today()
    projects = registry.refresh()["projects"]  # 剛改的登錄也算,不等排程器的 tick
    problems, count = [], 0
    for project in sorted(argv or projects):
        doc = projects.get(project) or {}
        declared = set()
        for sec in doc.get("secrets") or []:
            count += 1
            probs, notes = check(project, doc, sec, today)
            problems += probs
            for n in notes:
                print(n)
            if (path := locate(doc, sec["path"])):
                declared.add(path.resolve())
        for path in undeclared(project, doc, declared):
            print(f"{project}:沒宣告、檔名像憑證:{path}(宣告在登錄的 secrets:,或確認它不是憑證)")
    if not argv:
        # 警報管道本身:token 失效的話,出事時所有警報都發不出去——每天先確認一次(不發訊息)
        if alert.probe():
            alert.clear_failure("discord-probe")
        else:
            alert.record_failure("discord-probe", "Discord bot token 不能用(GET /users/@me 失敗),所有平台警報都發不出去")
            problems.append("Discord 警報管道不能用(bot token 失效或連不上)——狀態頁也會標紅")
        alert.once("secrets", problems, "登錄宣告的憑證都沒問題了")
    for p in problems:
        print(f"⚠️ {p}")
    print(f"檢查了 {count} 個憑證,{len(problems)} 個問題")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
