"""config 的套用者(docs/design/new-user.md 3.7):把 config(專案登錄、hub.json…)變成機器上的實際狀態的程式。

一個套用者 = 一個模組,提供:
  plan(index=None) -> list[Change]    會改什麼;**不准有副作用**(不寫它管的檔、不動 launchd)
  apply(index=None) -> list[Change]   改下去,回傳改了什麼——要跟剛才的 plan 一樣;改完再 plan 是空的(冪等)
  EXTERNAL: bool                      apply 會不會對外(push、寄信)。plan 不管 EXTERNAL 都不准對外
index 是 registry.refresh() 的結果;不給就自己讀(測試給假的)。

跟 runtime_ports 同一種寫法:登記表 APPLIERS、load() 機械檢查函式在不在、tests/test_appliers.py 對每一個登記的
套用者跑同一組契約(plan 沒副作用、apply == plan、apply 完再 plan 是空的)。沒過契約測試的套用者進不來。
Change 用中立的詞:target 是「誰的什麼」(service:<label>、agent-settings:<專案>、job:<名稱>),不寫執行環境的格式。

每 15 分鐘的那幾個 job(render-services、render-settings)照跑,是安全網;`bin/hub config apply` 是「現在就套用」。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import NamedTuple

BIN = pathlib.Path(__file__).resolve().parent
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))


class Change(NamedTuple):
    op: str        # add | update | remove
    target: str    # service:<label> | agent-settings:<專案> | hook:<專案>/<hook> | job:<名稱>
    detail: str = ""

    def line(self) -> str:
        mark = {"add": "+", "update": "~", "remove": "-"}.get(self.op, "?")
        return f"{mark} {self.target}" + (f"  {self.detail}" if self.detail else "")


OPS = ("add", "update", "remove")
FUNCTIONS = ("plan", "apply")
# 名稱 → 模組檔(bin/ 底下)。順序就是 apply 的順序:排程先(只是記錄),再服務、再專案的 agent 設定
APPLIERS = {
    "scheduler": "applier_scheduler.py",
    "services": "render-services.py",
    "agent-settings": "render-project-settings.py",
}


class MissingFunctions(Exception):
    pass


_loaded: dict[str, object] = {}


def load(name: str):
    """登記的套用者模組;缺 plan/apply/EXTERNAL 就拒絕載入。"""
    if name in _loaded:
        return _loaded[name]
    path = BIN / APPLIERS[name]
    spec = importlib.util.spec_from_file_location(f"applier_{name.replace('-', '_')}", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    missing = [f for f in FUNCTIONS if not callable(getattr(m, f, None))]
    if not isinstance(getattr(m, "EXTERNAL", None), bool):
        missing.append("EXTERNAL")
    if missing:
        raise MissingFunctions(f"{path.name} 不是套用者:缺 {', '.join(missing)}")
    _loaded[name] = m
    return m


# config 檔(new-user.md 3.0):git 裡追蹤的是這些;第 6 步搬到 config repo 之後由 hubconf 指過去
CONFIG_PATHS = ["hub.json", "mirror-list.txt", "services.tsv", "schedule.yaml", "projects"]


def applied_path() -> pathlib.Path:
    import hubconf
    return hubconf.STATE / "config-applied.json"


def config_commit() -> str:
    """config 檔最後一次被 commit 的版本;working tree 還有沒 commit 的 config 修改就加 +dirty。"""
    import subprocess
    import hubconf

    def git(*a):
        return subprocess.run(["git", "-C", str(hubconf.config_dir()), *a], capture_output=True, text=True).stdout.strip()
    sha = git("log", "-1", "--format=%H", "--", *CONFIG_PATHS) or "unknown"
    return sha + ("+dirty" if git("status", "--porcelain", "--", *CONFIG_PATHS) else "")


def plan_all(index=None, only: list[str] | None = None) -> dict[str, list[Change]]:
    return {n: load(n).plan(index) for n in APPLIERS if not only or n in only}


def apply_all(index=None, only: list[str] | None = None) -> dict[str, list[Change]]:
    return {n: load(n).apply(index) for n in APPLIERS if not only or n in only}
