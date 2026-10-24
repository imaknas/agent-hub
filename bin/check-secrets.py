#!/usr/bin/env python3
"""登錄宣告的憑證(projects/<p>.yaml 的 secrets:)在不在、有沒有被備份、快不快到期(平台 job agent-hub/secrets,每天)。

憑證的分工(2026-10-24 Knas 決定):機器層級的(claude、Discord、B2、備份密碼…)平台管,宣告在 agent-hub 的登錄;
專案自己服務的金鑰專案管,宣告在自己的登錄(manifest.update);到外部服務發新的、撤銷、換掉是 Knas 做。
登錄只寫名稱、位置、到期,**不寫值**;這支也不讀值、不印值:
  - 在不在:檔案存在、不是空的;有 env_key 的(.env 裡的一行)只看那個 key 有沒有、等號後面是不是空的
  - 備份:backup 沒寫 false 的,照 backup.covers()(跟每晚的備份同一套規則)
  - 到期:expires 日期,或檔案修改時間 + max_age_days;剩不到 warn_days(預設 14)天就報
  - 權限:群組/其他人讀得到只印出來,不算問題
有問題就發 Discord(同一組只發一次,解決後發一次恢復)、exit 1(排程器記失敗,狀態頁紅)。

用法:check-secrets.py [專案...]
"""
from __future__ import annotations

import datetime
import pathlib
import stat
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import alert  # noqa: E402
import backup  # noqa: E402
import hubconf  # noqa: E402
import registry  # noqa: E402

WARN_DAYS = 14


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


def main(argv: list[str]) -> int:
    today = datetime.date.today()
    projects = hubconf.projects()
    problems, count = [], 0
    for project in sorted(argv or projects):
        doc = projects.get(project) or {}
        for sec in doc.get("secrets") or []:
            count += 1
            probs, notes = check(project, doc, sec, today)
            problems += probs
            for n in notes:
                print(n)
    if not argv:
        alert.once("secrets", problems, "登錄宣告的憑證都沒問題了")
    for p in problems:
        print(f"⚠️ {p}")
    print(f"檢查了 {count} 個憑證,{len(problems)} 個問題")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
