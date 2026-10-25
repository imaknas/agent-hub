"""假的執行環境:runtime_ports 介面的第二個實作。設計:docs/design/runtime-abstraction.md。

兩個用途:
1. 證明介面沒有漏出 Claude Code 的形狀——同一組契約測試(tests/test_runtime_contracts.py)Claude 跟這個都要跑過。
2. 平台其他程式的測試替身:真的記得狀態(對話紀錄、哪些 session 活著、context 多大),不是只記錄有沒有被呼叫。

全部存在記憶體;測試用 reset() 清掉,用 add_turn / start / end / set_context 放資料。標準函式庫。
"""
from __future__ import annotations

import datetime
import pathlib

from runtime_ports import TaskResult, Turn

_turns: list[tuple[str, Turn, bool]] = []   # (工作目錄, Turn, 是不是在它底下的 worktree 裡)
_replies: dict[str, TaskResult] = {}     # 判斷點 → 要回的結果
_ran: list = []                          # 跑過的 Task(測試看它收到什麼)
_live: set[str] = set()
_context: dict[str, int] = {}
_env_key = "FAKE_RUNTIME_SESSION"


def reset() -> None:
    _resumable.clear()
    _replies.clear()
    _ran.clear()
    _turns.clear()
    _live.clear()
    _context.clear()


# ---------- 給測試放資料 ----------

def add_turn(path: str, turn: Turn, worktree: bool = False) -> None:
    """worktree=True:這則是在 path 底下 runtime 開的 worktree 裡的 session 說的。"""
    _turns.append((str(pathlib.Path(path)), turn, worktree))


def start(sid: str) -> None:
    _live.add(sid)


def end(sid: str) -> None:
    _live.discard(sid)


def set_context(sid: str, tokens: int) -> None:
    _context[sid] = tokens


def set_reply(point: str, result: TaskResult) -> None:
    _replies[point] = result


# ---------- agent ----------

def run(task) -> TaskResult:
    _ran.append(task)
    return _replies.get(task.point, TaskResult(True, text=f"fake:{task.point}", session_id="fake-session", turns=1,
                                               cost_usd=0.0))


def describe(task) -> list:
    return ["fake", task.point]


# ---------- transcripts ----------

def turns(paths, since: datetime.datetime, until: datetime.datetime | None = None, worktrees: bool = False):
    want = {str(pathlib.Path(p)) for p in paths}
    for path, t, wt in sorted(_turns, key=lambda x: x[1].ts):
        if path in want and (worktrees or not wt) and t.ts >= since and (until is None or t.ts < until):
            yield t


def last_activity(path) -> datetime.datetime | None:
    ts = [t.ts for p, t, wt in _turns if p == str(pathlib.Path(path)) and not wt]
    return max(ts) if ts else None


# ---------- sessions ----------

def live_sessions() -> set[str]:
    return set(_live)


def alive(sid: str | None) -> bool:
    return bool(sid) and sid in _live


def context_size(sid: str) -> int | None:
    return _context.get(sid)


def current_session(env) -> str | None:
    return env.get(_env_key) or None


# ---------- lifecycle(用自己的格式,不是 Claude 的:證明介面沒有 Claude 的形狀)----------
# 原生輸入:{"sid", "dir", "cmd"?, "where"?, "elsewhere"?};輸出:{"say": 文字} / {"block": 理由};
# 安裝:在專案資料夾寫 .fake-hooks.json(事件清單)。

import json  # noqa: E402

from runtime_ports import EVENTS, HubEvent  # noqa: E402


def parse_event(event: str, raw: str):
    try:
        d = json.loads(raw or "{}")
    except ValueError:
        return None
    if not d.get("sid"):
        return None
    target = ({"cwd": d.get("where"), "elsewhere": {"kind": d["elsewhere"]} if d.get("elsewhere") else None}
              if event == "open_session_attempt" else None)
    return HubEvent(event, d["sid"], d.get("dir", ""), command=d.get("cmd") if event == "tool_used" else None,
                    target=target)


def inject(event: str, text: str) -> str:
    return json.dumps({"say": text}, ensure_ascii=False) if text and event in ("session_start", "prompt", "tool_used") else ""


def deny(event: str, reason: str) -> str:
    return json.dumps({"block": reason}, ensure_ascii=False) if event == "open_session_attempt" else ""


def install_lifecycle(workspace) -> None:
    (pathlib.Path(workspace) / ".fake-hooks.json").write_text(json.dumps(list(EVENTS)))



# ---------- instructions ----------

def instructions_file(workspace) -> pathlib.Path:
    return pathlib.Path(workspace) / ".fake-instructions.md"


def local_ignores() -> list:
    return [".fake-instructions.md", ".fake-hooks.json"]


# ---------- stream(長駐 session 的輸出)----------
# 假的原生格式:工具名稱就是 kind(認不得的是 other),輸出的每一行是 {"type": ..., "data": {...}}。

from runtime_ports import KINDS, StreamEvent  # noqa: E402

_resumable: set[str] = set()


def set_resumable(sid: str, ok: bool = True) -> None:
    (_resumable.add if ok else _resumable.discard)(sid)


def action(tool: str, inp: dict | None) -> tuple:
    inp = inp or {}
    target = str(inp.get("target", ""))
    return (tool if tool in KINDS else "other"), inp.get("collab"), target, f"{tool} {target}".strip()


def parse_stream(line: str) -> list:
    try:
        d = json.loads(line)
    except ValueError:
        return []
    if not isinstance(d, dict) or not isinstance(d.get("data"), dict):
        return []
    data = dict(d["data"])
    if d.get("type") == "tool_use":  # 跟 Claude 一樣經過 action() 翻譯,不直接相信輸入
        kind, _, _, summary = action(data.pop("tool", ""), data.pop("input", None))
        data = {"kind": kind, "summary": summary, "detail": {}}
    return [StreamEvent(d["type"], data)]


def classify_exit(code, stderr_tail: str) -> StreamEvent:
    if "login" in stderr_tail:
        return StreamEvent("error", {"error": "agent_login", "detail": "fake runtime 沒登入"})
    return StreamEvent("error", {"error": "process_crashed", "detail": f"exit {code}:{stderr_tail[-200:]}"})


def resumable(sid: str) -> bool:
    return sid in _resumable


def resume_command(sid: str) -> tuple:
    return (f"fake-resume {sid}", "假的 runtime:接回同一段對話")
