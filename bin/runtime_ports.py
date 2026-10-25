"""執行環境的介面(ports):平台只依賴這裡定義的東西,Claude Code 只是一個實作。設計:docs/design/runtime-abstraction.md。

一個實作 = 一個模組(`runtime_<名稱>.py`),提供下面各介面要求的函式;Strategy 選的是「用哪個模組」。
`load()` 會**機械檢查**模組有沒有提供每個函式,缺了就拒絕載入;契約測試(tests/test_runtime_contracts.py)
從 RUNTIMES 自動找出所有實作來跑,第三個實作加進來不可能沒跑過契約測試。

目前的介面(其他層照設計文件的步驟一層一層加):
  transcripts(第 5 層):讀對話紀錄,用中立的格式
    turns(paths, since, until=None, worktrees=False) -> Iterator[Turn]   某個專案(它的工作目錄,可能好幾個)在時間範圍內的每一則;
                                                        worktrees=True 連 runtime 在這些工作目錄底下開的 worktree 裡的 session 也算
    last_activity(path) -> datetime | None              這個工作目錄最後一次有對話是什麼時候
  agent(第 1 層,跑一次):
    run(task: Task) -> TaskResult                       跑一次判斷點(prompt、工作目錄、模型、輸出 schema、逾時)
    describe(task: Task) -> list[str]                   會怎麼跑(給 llm.py --dry-run 看;內容是 runtime 自己的)
    失敗政策(on_failure、job.failed、schema 驗證、成本累計)留在 llm.py,不在 runtime 裡(設計原則 10)。
    Task.spec 是判斷點的宣告(contracts/llm-points.yaml);裡面還有 Claude 專用的欄位(allowed_tools、permission_mode…),
    要等第 2 層的中立政策(另一個 session)定案才換掉——在那之前只有 runtime 自己讀這些欄位,llm.py 不讀。
  lifecycle(第 3 層,事件):平台自己的事件名稱(EVENTS),各 runtime 把原生的 hook 接到 `bin/hub-event <事件>`
    parse_event(event, raw) -> HubEvent | None          原生的 hook 輸入 → 中立的事件(不是真的 session 在跑就 None)
    inject(event, text) -> str                           要放進 agent context 的文字 → 原生的輸出格式(這個事件不能放就 "")
    deny(event, reason) -> str                           擋下這個動作 → 原生的輸出格式
    install_lifecycle(workspace) -> None                 在這個專案裝好 hook(平台管的換成新的,使用者自己加的留著)
  instructions(第 4 層,規則檔):規則跟 memory 說明寫到 runtime 會讀的哪個檔(內容是平台的,templates/)
    instructions_file(workspace) -> Path                 平台受管區塊要寫進的檔(不進版控)
    local_ignores() -> list[str]                         這個 runtime 的本機專用檔(相對專案資料夾),要加進 .git/info/exclude
                                                         (.agent-memory 是平台自己的,不在這裡)
  sessions(第 1 層的一部分):
    alive(sid) -> bool、live_sessions() -> set[str]、context_size(sid) -> int | None(接續要載入多少 token)
    current_session(env) -> str | None                   目前這個程序屬於哪個 session(給 hubbus send 記寄件人)
  stream(長駐 session 的輸出翻譯;docs/design/lease-contract-neutral.md):
    action(tool, input) -> (kind, collab, target, summary)   工具 → KINDS/COLLABS;target 給判斷用,summary 給人看
    parse_stream(line) -> list[StreamEvent]             runtime 原生的一行輸出 → 中立的對話事件
    classify_exit(code, stderr_tail) -> StreamEvent      程序結束了:沒登入(agent_login)還是掛了(process_crashed)
    resumable(sid) -> bool                               這段對話還接得回來嗎(紀錄還在)
    resume_command(sid) -> (command, hint)               在終端機接續的指令跟給人看的一句

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
    "lifecycle": ("parse_event", "inject", "deny", "install_lifecycle"),
    "instructions": ("instructions_file", "local_ignores"),
    "transcripts": ("turns", "last_activity"),
    "sessions": ("alive", "live_sessions", "context_size", "current_session"),
    "stream": ("action", "parse_stream", "classify_exit", "resumable", "resume_command"),
}

# 中立的「動作種類」:對話紀錄裡 agent 做了什麼,不用任何 runtime 的工具名稱。


# 平台的事件(第 3 層)。runtime 沒有某個時機就不支援,平台照不支援處理,不假裝有。
EVENTS = ("session_start", "session_end", "pre_compact", "prompt", "tool_used", "open_session_attempt")


class HubEvent(NamedTuple):
    event: str               # EVENTS 之一
    session: str             # 哪個 session
    cwd: str                 # 這個 session 在哪個資料夾
    command: str | None = None   # tool_used:跑了什麼指令(目前只接「跑指令」這種工具)
    target: dict | None = None   # open_session_attempt:想在哪裡開 {"cwd": 路徑或 None, "elsewhere": 種類或 None}
                                 # elsewhere = {"kind": "repository", "name"} | {"kind": "machine"} | {"kind": "unknown_place"}


# 動作的效果(docs/design/lease-contract-neutral.md):全平台只有這一套——租約的事件、權限(approvals)、
# 判斷點宣告、對話紀錄統計都用它,不各自取名。哪個工具算哪種由各 runtime 的 action() 翻譯。
KINDS = ("read", "search", "ask_user", "open_web", "write_file", "run_command", "other")
# 協作方式(跟效果是不同的維度):交給子 agent、跑 workflow、跨 session 傳訊息、用技能;其他動作是 None。
COLLABS = ("subagent", "workflow", "peer_message", "skill")


class Action(NamedTuple):
    kind: str            # KINDS 之一(效果:讀、搜尋、問人、開網頁、寫檔、跑指令、其他)
    detail: str = ""     # 技能名稱(collab 是 skill 時);不放任何 runtime 的工具名稱
    collab: str | None = None   # COLLABS 之一或 None


# 對話事件(長駐 session 的輸出;租約契約 v2 的事件就是這個形狀)。跟 HubEvent(hook 事件)是兩回事。
STREAM_TYPES = ("text_delta", "assistant", "tool_use", "tool_result", "turn_end", "quota", "error")
QUOTA_STATUS = ("ok", "warning", "exhausted")


class StreamEvent(NamedTuple):
    """type 是 STREAM_TYPES 之一;data 是中立的欄位(見 contracts/lease-runner.v2.schema.json),`_` 開頭的鍵是給 runner 的
    內部訊號(例:`_detach`),不會送出去。legacy:這個事件在租約契約 v1 長什麼樣(跟 v2 一樣就 None)——
    只在 v1/v2 並存期間用,v1 刪掉時一起拿掉。"""
    type: str
    data: dict
    legacy: dict | None = None


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
