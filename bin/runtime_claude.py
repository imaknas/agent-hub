"""執行環境的轉接層(Claude Code):專案信箱的分流(hubbus dispatch)只透過這裡知道「session」的事。

換工具、或 Claude Code 出了正式的「叫醒一個開著的 session」方法,只改這個檔;hubbus 跟各專案的 agent 都不用動。
設計:docs/design/mailbox-routing.md。標準函式庫,系統的 python3(3.9)也能 import。

  live_sessions()        還活著的 session id(Claude Code 程序的參數帶著 --resume=<id> 或 --session-id=<id>;
                         10-24 驗證過跟 hook 的 session_id、CLAUDE_CODE_SESSION_ID 是同一個)
  context_tokens(sid)    接續這個 session 要載入多少 context(對話紀錄最後一則回覆的 usage;壓縮過的就是
                         壓縮後的大小)。找不到就 None
  resume_args(sid)       背景接續用的 claude 參數:--resume <sid> --fork-session(10-24 實測:分支帶著完整 context、
                         拿到新的 id、原本的對話紀錄完全不動——開著的 session 不受影響)
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess

PROJECTS = pathlib.Path.home() / ".claude" / "projects"
_ID = re.compile(r"--(?:resume|session-id)[= ]([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def live_sessions() -> set[str]:
    r = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True)
    return set(_ID.findall(r.stdout)) if r.returncode == 0 else set()


def alive(sid: str | None) -> bool:
    return bool(sid) and sid in live_sessions()


def transcript(sid: str) -> pathlib.Path | None:
    hits = list(PROJECTS.glob(f"*/{sid}.jsonl")) if sid and re.fullmatch(r"[0-9a-f-]{36}", sid) else []
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def context_tokens(sid: str) -> int | None:
    t = transcript(sid)
    if not t:
        return None
    try:
        lines = t.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        u = (e.get("message") or {}).get("usage") if e.get("type") == "assistant" else None
        if u:
            return int(u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                       + u.get("cache_creation_input_tokens", 0))
    return None


def resume_args(sid: str) -> list[str]:
    return ["--resume", sid, "--fork-session"]
