"""執行環境的介面(ports):平台只依賴這裡定義的東西,Claude Code 只是一個實作。設計:docs/design/runtime-abstraction.md。

一個實作 = 一個模組(`runtime_<名稱>.py`),提供下面各介面要求的函式;Strategy 選的是「用哪個模組」。
`load()` 會**機械檢查**模組有沒有提供每個函式,缺了就拒絕載入;契約測試(tests/test_runtime_contracts.py)
從 RUNTIMES 自動找出所有實作來跑,第三個實作加進來不可能沒跑過契約測試。

目前的介面(其他層照設計文件的步驟一層一層加):
  transcripts(第 5 層):讀對話紀錄,用中立的格式
    turns(paths, since, until=None) -> Iterator[Turn]   某個專案(它的工作目錄,可能好幾個)在時間範圍內的每一則
    last_activity(path) -> datetime | None              這個工作目錄最後一次有對話是什麼時候
  sessions(第 1 層的一部分):
    alive(sid) -> bool、live_sessions() -> set[str]、context_size(sid) -> int | None(接續要載入多少 token)
    current_session(env) -> str | None                   目前這個程序屬於哪個 session(給 hubbus send 記寄件人)

標準函式庫,系統的 python3(3.9)也能 import(hubbus 用的就是它)。
"""
from __future__ import annotations

import datetime
import importlib
import os
from typing import NamedTuple

# 登記表:名稱 → 模組。多個 runtime 可以同時登記,每個 session 用它自己的(設計原則 9)。
RUNTIMES = {"claude": "runtime_claude", "fake": "runtime_fake"}
DEFAULT = "claude"   # hub.json 沒寫 runtime.default 時的預設

PORTS = {
    "transcripts": ("turns", "last_activity"),
    "sessions": ("alive", "live_sessions", "context_size", "current_session"),
}

# 中立的「動作種類」:對話紀錄裡 agent 做了什麼,不用任何 runtime 的工具名稱。
ACTIONS = ("subagent", "workflow", "peer_message", "skill", "tool")


class Action(NamedTuple):
    kind: str            # ACTIONS 之一
    detail: str = ""     # skill 名稱、工具名稱之類(給統計用,不是給程式判斷 runtime 的)


class Turn(NamedTuple):
    ts: datetime.datetime    # aware,UTC
    session: str
    role: str                # "human"(使用者實際送出的)| "assistant" | "other"(工具結果、系統訊息、別的 session 傳來的…)
    text: str = ""           # role == human 時是他打的字
    actions: tuple = ()      # role == assistant 時,這一則做了哪些 Action


class MissingFunctions(Exception):
    pass


def default_name() -> str:
    try:
        import hubconf
        return hubconf.get("runtime.default", DEFAULT) or DEFAULT
    except Exception:
        return DEFAULT


def load(name: str | None = None, ports=tuple(PORTS)):
    """拿到一個 runtime 的模組;缺了任何介面要求的函式就拒絕(不是等到呼叫時才壞)。"""
    name = name or default_name()
    if name not in RUNTIMES:
        raise KeyError(f"沒有登記的 runtime:{name}(有:{', '.join(RUNTIMES)})")
    mod = importlib.import_module(RUNTIMES[name])
    missing = [f"{p}.{fn}" for p in ports for fn in PORTS[p] if not callable(getattr(mod, fn, None))]
    if missing:
        raise MissingFunctions(f"runtime {name}({RUNTIMES[name]})缺少:{', '.join(missing)}")
    return mod


def current_session(env=None) -> str | None:
    """目前這個程序屬於哪個 session:問預設的 runtime(現在是讀 Claude Code 給的環境變數)。"""
    return load(ports=("sessions",)).current_session(os.environ if env is None else env)
