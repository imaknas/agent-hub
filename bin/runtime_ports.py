"""執行環境的介面(ports):平台只依賴這裡定義的東西,Claude Code 只是一個實作。設計:docs/design/runtime-abstraction.md。

一個實作 = 一個模組(`runtime_<名稱>.py`),提供下面各介面要求的函式;Strategy 選的是「用哪個模組」。
`load()` 會**機械檢查**模組有沒有提供每個函式,缺了就拒絕載入;契約測試(tests/test_runtime_contracts.py)
從 RUNTIMES 自動找出所有實作來跑,第三個實作加進來不可能沒跑過契約測試。

目前的介面(其他層照設計文件的步驟一層一層加):
  transcripts(第 5 層):讀對話紀錄,用中立的格式
    turns(paths, since, until=None) -> Iterator[Turn]   某個專案(它的工作目錄,可能好幾個)在時間範圍內的每一則
    last_activity(path) -> datetime | None              這個工作目錄最後一次有對話是什麼時候
  agent(第 1 層,跑一次):
    run(task: Task) -> TaskResult                       跑一次判斷點(prompt、工作目錄、模型、輸出 schema、逾時)
    describe(task: Task) -> list[str]                   會怎麼跑(給 llm.py --dry-run 看;內容是 runtime 自己的)
    失敗政策(on_failure、job.failed、schema 驗證、成本累計)留在 llm.py,不在 runtime 裡(設計原則 10)。
    Task.spec 是判斷點的宣告(contracts/llm-points.yaml);裡面還有 Claude 專用的欄位(allowed_tools、permission_mode…),
    要等第 2 層的中立政策(另一個 session)定案才換掉——在那之前只有 runtime 自己讀這些欄位,llm.py 不讀。
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
    "agent": ("run", "describe"),
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


class Task(NamedTuple):
    point: str               # 判斷點名稱
    prompt: str
    spec: dict               # 判斷點的宣告(見上:暫時帶著 runtime 專用欄位)
    cwd: str | None = None
    model: str | None = None
    output_schema: dict | None = None
    timeout_s: int = 1200
    env: dict | None = None  # 執行環境變數(登入由 llm.py 決定;第 6 層 AuthProvider 之後接手)
    extra: tuple = ()        # 呼叫端多給的參數(例:租約交接要接續哪個 session)——之後由 resume 介面取代


class TaskResult(NamedTuple):
    ok: bool                         # runtime 有沒有跑完並給出結果(結構化輸出合不合 schema 是 llm.py 判斷)
    incomplete: bool = False         # 有結果,但 agent 回報這次沒有正常完成(例:回合上限)——不算失敗,呼叫端自己判斷
    text: str = ""                   # 最後的回覆文字
    structured_output: object = None
    cost_usd: float | None = None
    session_id: str | None = None
    turns: int | None = None
    stop: str = ""                   # 結束的原因(runtime 自己的說法,只給 log 看)
    denials: tuple = ()              # 被拒絕的動作
    error: str = ""                  # ok 是 False 時的原因


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
