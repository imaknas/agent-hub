"""假的執行環境:runtime_ports 介面的第二個實作。設計:docs/design/runtime-abstraction.md。

兩個用途:
1. 證明介面沒有漏出 Claude Code 的形狀——同一組契約測試(tests/test_runtime_contracts.py)Claude 跟這個都要跑過。
2. 平台其他程式的測試替身:真的記得狀態(對話紀錄、哪些 session 活著、context 多大),不是只記錄有沒有被呼叫。

全部存在記憶體;測試用 reset() 清掉,用 add_turn / start / end / set_context 放資料。標準函式庫。
"""
from __future__ import annotations

import datetime
import pathlib

from runtime_ports import Turn

_turns: list[tuple[str, Turn]] = []      # (工作目錄, Turn)
_live: set[str] = set()
_context: dict[str, int] = {}
_env_key = "FAKE_RUNTIME_SESSION"


def reset() -> None:
    _turns.clear()
    _live.clear()
    _context.clear()


# ---------- 給測試放資料 ----------

def add_turn(path: str, turn: Turn) -> None:
    _turns.append((str(pathlib.Path(path)), turn))


def start(sid: str) -> None:
    _live.add(sid)


def end(sid: str) -> None:
    _live.discard(sid)


def set_context(sid: str, tokens: int) -> None:
    _context[sid] = tokens


# ---------- transcripts ----------

def turns(paths, since: datetime.datetime, until: datetime.datetime | None = None):
    want = {str(pathlib.Path(p)) for p in paths}
    for path, t in sorted(_turns, key=lambda x: x[1].ts):
        if path in want and t.ts >= since and (until is None or t.ts < until):
            yield t


def last_activity(path) -> datetime.datetime | None:
    ts = [t.ts for p, t in _turns if p == str(pathlib.Path(path))]
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
