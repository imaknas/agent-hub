"""agent-hub 幾支腳本共用的判斷:哪些 transcript 是真人打的訊息、transcript 資料夾
對應到哪個專案、哪些 commit 算專案工作。放在一處,改一次所有腳本一起跟上。

讀對話紀錄不在這裡:經過 runtime_ports(第 5 層 transcripts),Claude Code 的格式只在 runtime_claude.py。

使用者:check-memory-gaps.py、memory-nudge.py、weekly-reflect.py。
"""
from __future__ import annotations  # 系統的 python3(3.9,hook 裡用的就是它)也要能 import:memory-nudge.py 靠它

import datetime
import json
import pathlib
import re

import hubconf  # 同在 bin/;用 hublib 的腳本都已經把 bin/ 放進 sys.path

HOME = pathlib.Path.home()
HUB = HOME / "agent-hub"
# 2026-10-15 起所有專案都在 mini 的 ~/workspace/<name>(登錄的 paths.workspace 就是這個)。
# 之前在 MBP 上的位置(以及改名前的路徑)記在 legacy-paths.tsv:transcript 資料夾名稱是用
# 當時的路徑算的,統計舊對話要靠它。
LEGACY_PATHS = HUB / "legacy-paths.tsv"
MBP_HOME = hubconf.get("hosts.mbp_home", "/Users/you")   # 家目錄從 hub.json 讀,不寫死(CLAUDE.md 寫新程式的規則)
MINI_HOME = hubconf.get("hosts.mini_home", "/Users/you")
MINI_WORKSPACE = f"{MINI_HOME}/workspace"
# mini 上 librarian 用的 memory-only 副本(只有 AGENT.md/STATE.md/memory/,不是專案程式碼)。
# 2026-10-15 前叫 ~/projects,改成隱藏目錄:跟 ~/workspace(開 session 的地方)名字太像,會被當成專案資料夾
MEMORY_COPIES = HOME / ".memory-copies"
# 沒有 onboard、但 transcript 裡會出現、值得單獨算的工作目錄(舊的 MBP 位置在 legacy-paths.tsv)
EXTRA_PROJECTS = {
    "agent-hub": f"{MINI_HOME}/agent-hub",
}


def legacy_paths(name: str | None = None) -> dict[str, list[str]] | list[str]:
    """專案名稱 → 它以前的路徑(MBP 上的位置、改名前的路徑)。給 name 就只回那個專案的。"""
    out: dict[str, list[str]] = {}
    if LEGACY_PATHS.exists():
        for line in LEGACY_PATHS.read_text().splitlines()[1:]:
            cols = line.split("\t")
            if len(cols) >= 2 and cols[0]:
                out.setdefault(cols[0], []).append(cols[1])
    return out.get(name, []) if name is not None else out


# 不代表「有人在這個專案做了工作」的 commit:排程自動產生的(文獻、資料快照、librarian),
# 以及只動到 agent-hub 管的檔案(.gitignore/.claude/CLAUDE.md)的 commit(onboard、批次清理)。
AUTOMATED_SUBJECT = re.compile(r"^(papers: |data: nightly snapshot|librarian: )")
NON_WORK_PATHS = (":!.gitignore", ":!.claude", ":!CLAUDE.md")


def project_paths() -> dict[str, list[str]]:
    """專案名稱 → 現在的路徑(mini 的 ~/workspace)+ 以前的路徑(legacy-paths.tsv)。"""
    out: dict[str, list[str]] = {}
    for name, paths in hubconf.roster().items():
        ws = paths.get("workspace")
        if ws:  # ~ 是 mini 的家目錄(transcript 資料夾名稱照 mini 的路徑算),在 MBP 上跑也一樣
            out.setdefault(name, []).append(MINI_HOME + ws[1:] if ws.startswith("~/") else ws)
    for name, path in EXTRA_PROJECTS.items():
        out.setdefault(name, []).append(path)
    for name, paths in legacy_paths().items():
        out.setdefault(name, []).extend(p for p in paths if p not in out[name])
    return out


def is_work_subject(subject: str) -> bool:
    return not AUTOMATED_SUBJECT.match(subject)
