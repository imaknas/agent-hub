"""執行環境的轉接層(Claude Code):平台只透過這裡知道 Claude Code 的 session 跟設定格式。

換工具、或 Claude Code 出了正式的「叫醒一個開著的 session」方法,只改這個檔;hubbus 跟各專案的 agent 都不用動。
設計:docs/design/mailbox-routing.md(session)、docs/design/capabilities.md(設定)。標準函式庫,系統的 python3(3.9)
也能 import——所以這裡不 import registry/policy,只吃 policy.policy() 算好的 dict。

  live_sessions()        還活著的 session id(Claude Code 程序的參數帶著 --resume=<id> 或 --session-id=<id>;
                         10-24 驗證過跟 hook 的 session_id、CLAUDE_CODE_SESSION_ID 是同一個)
  context_tokens(sid)    接續這個 session 要載入多少 context(對話紀錄最後一則回覆的 usage;壓縮過的就是
                         壓縮後的大小)。找不到就 None
  resume_args(sid)       背景接續用的 claude 參數:--resume <sid> --fork-session(10-24 實測:分支帶著完整 context、
                         拿到新的 id、原本的對話紀錄完全不動——開著的 session 不受影響)

設定(中立政策 → Claude Code 的 settings;政策的欄位見 policy.py):
  settings(pol)          → (sandbox, deny, allow):專案 .claude/settings.local.json 裡平台管的部分
                         (render-project-settings.py 負責寫檔、記錄、偵測偏移)
  lease_settings(pol)    → 租約 `--settings` 的內容:同一份 deny + 使用者層級的憑證 + 擋 `security`
  settings_path(ws)、sandbox_rendered(ws)   設定檔在哪、OS sandbox 是不是真的已經寫進去了
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


# ---------- 設定 ----------

def _tilde(p) -> str:
    s, h = str(p), str(pathlib.Path.home())
    return "~" + s[len(h):] if s == h or s.startswith(h + "/") else s


def settings(pol: dict) -> tuple[dict, list[str], list[str]]:
    deny = []
    for p in pol["other_projects"]:
        deny += [f"Read(/{p}/**)", f"Edit(/{p}/**)"]  # //abs:絕對路徑
    deny += [f"Edit({_tilde(p)}/**)" for p in pol["deny_write"]]
    deny += [f"Read({_tilde(p)}/**)" for p in pol["deny_read"]]
    allow = [f"Bash({_tilde(c)} *)" for c in pol["platform_commands"]]
    sandbox = {
        "enabled": True,
        "failIfUnavailable": True,
        "allowUnsandboxedCommands": False,
        "filesystem": {
            "allowWrite": list(pol["write"]),
            "denyRead": [_tilde(p) for p in pol["other_projects"]] + [_tilde(p) for p in pol["deny_read"]],
        },
        "network": {"allowedDomains": list(pol["network"])},
    }
    return sandbox, deny, allow


def lease_settings(pol: dict) -> dict:
    deny = settings(pol)[1]
    deny += [f"Read({_tilde(p)})" for p in pol["credential_files"]] + [f"Read({_tilde(p)}/**)" for p in pol["credentials"]]
    # Keychain 不是檔案,OS sandbox 擋不住(10-25 實測:sandbox 裡的 Bash 查得到 Claude 的登入憑證)。
    # 這幾條只是補強——換個寫法(sh -c、python)就繞得過,真正的把關是 work 模式的指令每次都問 Knas
    deny += ["Bash(security:*)", "Bash(security *)", "Bash(/usr/bin/security *)"]
    return {"permissions": {"deny": deny}}


def settings_path(ws: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(ws) / ".claude" / "settings.local.json"


def sandbox_rendered(ws) -> tuple[bool, str]:
    """登錄的 enforce.sandbox 只是意圖,寫出來的設定檔才是事實。"""
    try:
        st = json.loads(settings_path(ws).read_text())
    except (OSError, ValueError, TypeError):
        return False, "sandbox 還沒寫進這個專案的設定(settings.local.json 讀不到)"
    if not (st.get("sandbox") or {}).get("enabled"):
        return False, "sandbox 還沒寫進這個專案的設定(sandbox.enabled 不是 true)"
    return True, ""
