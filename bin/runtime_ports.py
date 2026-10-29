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
  auth(第 6 層,登入):平台只說「用哪種登入」(mode:token = 無人值守用的長期憑證、subscription = 互動登入,
  要 connector 的 job 用;名稱沿用 contracts/llm-points.yaml 的 auth 欄位),憑證放哪、環境變數叫什麼是 runtime 的事
    auth_env(mode, env) -> (env | None, reason)          這種登入要給 agent 的環境變數(從 env 出發,先清掉這個
                                                         runtime 自己的憑證變數);憑證不在就 (None, 給人看的原因)
    clean_env(env) -> dict                               拿掉這個 runtime 的憑證變數(交給別人之前、或要用預設登入時)
    credentials() -> list[Credential]                   這台機器上有哪些登入、在不在、什麼時候到期(不讀值、不印值)
    is_login_error(text) -> bool                         這個錯誤訊息是登入的問題嗎(要人去處理),還是別的(逾時、額度…)
  completion(判斷點的「只回答案」;docs/design/llm-providers.md):模型沒有工具、不碰檔案
    complete(req: CompletionRequest, conf: dict) -> CompletionResult   conf = hub.json llm.providers 裡這一家的設定
                                                        (key 由 completion.py 讀好放在 conf["_key"],實作不自己找憑證)
    health(conf) -> (ok, reason)                         最小的檢查(不花 token 的優先,例:列模型)
    除了 runtime(Claude Code 關掉工具跑),只會 completion 的 provider 也是這個介面的實作(PROVIDERS)。
    JSON 的解析、驗證、重試、截斷、退避、記帳在 completion.py,不在實作裡。
  stream(長駐 session 的輸出翻譯;docs/design/lease-contract-neutral.md):
    action(tool, input) -> (kind, collab, target, summary)   工具 → KINDS/COLLABS;target 給判斷用,summary 給人看
    parse_stream(line) -> list[StreamEvent]             runtime 原生的一行輸出 → 中立的對話事件
    classify_exit(code, stderr_tail) -> StreamEvent      程序結束了:沒登入(agent_login)還是掛了(process_crashed)
    resumable(sid) -> bool                               這段對話還接得回來嗎(紀錄還在)
    resume_command(sid) -> (command, hint)               在終端機接續的指令跟給人看的一句
    relocate(sid, old_cwd, new_cwd) -> bool              專案資料夾搬了(改名、升級):讓這段對話在新資料夾也接得回來

標準函式庫,系統的 python3(3.9)也能 import(hubbus 用的就是它)。
"""
from __future__ import annotations

import datetime
import importlib
import os
from typing import NamedTuple

# 登記表:名稱 → 模組。多個 runtime 可以同時登記,每個 session 用它自己的(設計原則 9)。
RUNTIMES = {"claude": "runtime_claude", "fake": "runtime_fake", "none": "runtime_none", "command": "runtime_command"}
DEFAULT = "claude"   # hub.json 沒寫 runtime.default 時的預設
# 只實作 completion 介面的(LLM API):hub.json llm.providers 的 impl 寫這些名稱,或任何有 completion 介面的 runtime 名稱
PROVIDERS = {"anthropic": "provider_anthropic", "openai": "provider_openai", "ollama": "provider_ollama"}

# 介面給人看的名稱(Unsupported 的原因、doctor 的能力表)
PORT_LABELS = {"agent": "跑 agent", "lifecycle": "session 事件(hook)", "instructions": "規則檔",
               "transcripts": "對話紀錄", "sessions": "session 狀態", "stream": "長駐串流 session", "auth": "登入狀態",
               "completion": "completion(只回答案)"}

# 平台的功能需要哪些介面(doctor 的「工具 × 功能」表;docs/design/tool-agnostic.md 原則 3)。缺任何一個 = 那個功能關掉。
FEATURES = (
    ("判斷點(librarian、index、consolidate、週報分析)", ("agent", "auth")),
    ("信箱交辦(沒人收的信由平台派 agent)", ("agent", "auth")),
    ("信箱接續(用閒置 session 的 context 回信)", ("agent", "sessions")),
    ("通道租借(另外要助理模組)", ("stream", "sessions", "agent", "auth")),
    ("session 裡即時看到信、提醒寫筆記", ("lifecycle",)),
    ("平台規則寫進專案的規則檔", ("instructions",)),
    ("memory 缺口、週報的對話統計", ("transcripts",)),
)


def feature_table(name: str | None = None) -> list[tuple[str, bool, str]]:
    """(功能, 能不能用, 缺什麼)——缺的用 PORT_LABELS 的名稱。"""
    have = capabilities(name)
    return [(f, not (miss := [PORT_LABELS[p] for p in need if p not in have]), "、".join(miss)) for f, need in FEATURES]


PORTS = {
    "agent": ("run", "describe"),
    "lifecycle": ("parse_event", "inject", "deny", "install_lifecycle"),
    "instructions": ("instructions_file", "local_ignores"),
    "transcripts": ("turns", "last_activity"),
    "sessions": ("alive", "live_sessions", "context_size", "current_session"),
    "stream": ("action", "parse_stream", "classify_exit", "resumable", "resume_command", "relocate"),
    "auth": ("auth_env", "clean_env", "credentials", "is_login_error"),
    "completion": ("complete", "health"),
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
    內部訊號(例:`_detach`),不會送出去。"""
    type: str
    data: dict


class Turn(NamedTuple):
    ts: datetime.datetime    # aware,UTC
    session: str
    role: str                # "human"(使用者實際送出的)| "assistant" | "other"(工具結果、系統訊息、別的 session 傳來的…)
    text: str = ""           # role == human 時是他打的字
    actions: tuple = ()      # role == assistant 時,這一則做了哪些 Action


class Credential(NamedTuple):
    """一份登入(第 6 層)。字句欄位是給人看的(警報、檢查輸出),平台照它們組句子;門檻是這份憑證自己的性質。"""
    name: str                    # 短名稱(檢查輸出的「…:ok」)
    label: str                   # 壞了時的主詞
    mode: str | None             # 實際打請求檢查時用哪種登入;None = 不能打請求檢查(只看到期)
    present: bool                # 在不在(看不到的登入,例:keychain,當作在,只能靠打請求確認)
    renew: str                   # 怎麼重新登入(給人做的)
    path: str | None = None      # 憑證檔在哪(只給人看)
    since: datetime.datetime | None = None          # 什麼時候產生的(看年紀用)
    max_age: datetime.timedelta | None = None       # 超過就提醒換
    lifetime: str | None = None                     # 「一年」之類,給人看
    expires: datetime.datetime | None = None        # 什麼時候到期
    warn_before: datetime.timedelta | None = None   # 到期前多久提醒
    used_by: str | None = None                      # 誰在用(給人看;平台知道的用途由平台自己說)
    note: str | None = None                         # 檢查時要印的說明(例:看不出到期日)


class Task(NamedTuple):
    point: str               # 判斷點名稱
    prompt: str
    spec: dict               # 判斷點的宣告(見上:暫時帶著 runtime 專用欄位)
    cwd: str | None = None
    model: str | None = None
    output_schema: dict | None = None
    timeout_s: int = 1200
    env: dict | None = None  # 執行環境變數(已經套好登入:llm.py 用 auth 介面的 auth_env 算出來)
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


class CompletionRequest(NamedTuple):
    """completion 介面的一次請求(docs/design/llm-providers.md):模型沒有工具、不碰檔案,只回文字或合 schema 的 JSON。"""
    point: str                       # 哪個判斷點(log、記帳用)
    prompt: str
    model: str | None = None         # 這個實作自己的模型名稱(llm 設定的 provider.models 給的)
    schema: dict | None = None       # 要的輸出 JSON schema;None = 文字
    system: str | None = None
    max_tokens: int = 8192
    timeout_s: int = 600


class CompletionResult(NamedTuple):
    ok: bool
    text: str = ""                   # 模型回的原文(JSON 也在這裡;解析、驗證、重試在 completion.py,不在實作裡)
    stop: str = ""                   # COMPLETION_STOPS 之一
    error: str = ""                  # ok 是 False 時的原因
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None    # 實作自己知道成本才填(例:Claude Code 會回);API 只回 token,成本由 completion.py 照價目算
    retry_after_s: float | None = None   # stop 是 rate_limited 時,服務端建議等多久


# end = 正常結束;length = 被長度截斷(重試會原樣再截一次,不重試);refused = 模型拒絕;rate_limited = 429/529 之類,退避後重試;
# error = 其他失敗
COMPLETION_STOPS = ("end", "length", "refused", "rate_limited", "error")


class MissingFunctions(Exception):
    """實作了一個介面的一部分函式:這是 bug,載入就拒絕。"""


class Unsupported(Exception):
    """這個 runtime 完全沒有這個介面(例:runtime_none、只會 completion 的轉接器):不是錯誤,是能力不足。
    呼叫端照「不支援」處理——明確關掉那個功能、說出原因(str(e)),**不是拿空資料照跑**(docs/design/tool-agnostic.md 原則 3)。"""


def _module(name: str):
    if name not in RUNTIMES:
        raise KeyError(f"沒有登記的 runtime:{name}(有:{', '.join(RUNTIMES)})")
    return importlib.import_module(RUNTIMES[name])


def capabilities(name: str | None = None) -> set[str]:
    """這個 runtime 有哪些介面:從它實作了哪些函式推出來,不另外宣告(宣告會跟實作不同步)。"""
    mod = _module(name or default_name())
    return {p for p, fns in PORTS.items() if all(callable(getattr(mod, fn, None)) for fn in fns)}


def default_name() -> str:
    try:
        import hubconf
        return hubconf.get("runtime.default", DEFAULT) or DEFAULT
    except Exception:
        return DEFAULT


def load(name: str | None = None, ports=tuple(PORTS)):
    """拿到一個 runtime 的模組。要求的介面只實作了一部分 → MissingFunctions(bug,不是等到呼叫時才壞);
    完全沒有 → Unsupported(能力不足,呼叫端說原因、關掉功能)。"""
    name = name or default_name()
    mod = _module(name)
    absent = []
    for p in ports:
        have = [fn for fn in PORTS[p] if callable(getattr(mod, fn, None))]
        if not have:
            absent.append(p)
        elif len(have) < len(PORTS[p]):
            missing = [f"{p}.{fn}" for fn in PORTS[p] if fn not in have]
            raise MissingFunctions(f"runtime {name}({RUNTIMES[name]})缺少:{', '.join(missing)}")
    if absent:
        raise Unsupported(f"執行環境 {name} 沒有{'、'.join(PORT_LABELS.get(p, p) for p in absent)}"
                          f"(hub.json runtime.default;要這個功能就裝一個有的 coding agent)")
    return mod


def load_completion(impl: str):
    """hub.json llm.providers 的 impl → 實作 completion 介面的模組(provider,或有 completion 的 runtime)。"""
    if impl in PROVIDERS:
        mod = importlib.import_module(PROVIDERS[impl])
        missing = [fn for fn in PORTS["completion"] if not callable(getattr(mod, fn, None))]
        if missing:
            raise MissingFunctions(f"provider {impl} 缺少:{', '.join(missing)}")
        return mod
    if impl in RUNTIMES:
        return load(impl, ports=("completion",))
    raise KeyError(f"沒有這個 completion 實作:{impl}(有:{', '.join([*PROVIDERS, *RUNTIMES])})")


def current_session(env=None) -> str | None:
    """目前這個程序屬於哪個 session:問預設的 runtime(現在是讀 Claude Code 給的環境變數)。
    沒有 session 介面(runtime_none)= 不知道,不是錯誤:寄信、記在場照樣做,只是沒有寄件 session。"""
    try:
        return load(ports=("sessions",)).current_session(os.environ if env is None else env)
    except Unsupported:
        return None
