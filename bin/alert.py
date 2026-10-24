"""發 Discord 警報(hub.json alerts:bot token 跟頻道 id 的檔案,在 .secrets/,不進版控)。

只用標準函式庫 + 系統的 curl(不靠 Homebrew)。排程器看門狗刻意有自己的一份,不 import 這支
(它要在排程器、平台程式碼都壞掉的時候還能發)。

  alert.send(text) -> bool
  alert.once(key, problems, ok_text) -> None   新的問題馬上發;沒解決的每 24 小時再提醒一次;解決時發一次恢復
  alert.probe() -> bool                         token 還能不能用(GET /users/@me,不發訊息;check-secrets 每天呼叫)

發不出去不會默默算了(2026-10-24):記在 .state/alerts-failed.json,狀態頁標紅,下次再試;發得出去就清掉。
"""
from __future__ import annotations

import datetime
import json
import subprocess

import hubconf

SENT = hubconf.STATE / "alerts-sent.json"      # key → {"problems": [...], "at": 上次發的時間}
FAILED = hubconf.STATE / "alerts-failed.json"  # key → {"at": …, "text": 發不出去的那則的第一行}
REMIND = datetime.timedelta(hours=24)
API = "https://discord.com/api/v10"


def _creds() -> tuple[str, str] | None:
    try:
        return (hubconf.p("alerts.discord_token_file").read_text().strip(),
                hubconf.p("alerts.discord_channel_file").read_text().strip())
    except (OSError, AttributeError):
        return None


def _curl(*args: str, data: str | None = None) -> str:
    """回傳 HTTP 狀態碼(字串);curl 本身失敗是空字串。"""
    r = subprocess.run(["/usr/bin/curl", "-sS", "-m", "20", "-o", "/dev/null", "-w", "%{http_code}", *args],
                       input=data, capture_output=True, text=True)
    return r.stdout.strip()


def send(text: str) -> bool:
    c = _creds()
    if not c:
        return False
    token, channel = c
    return _curl("-H", f"Authorization: Bot {token}", "-H", "Content-Type: application/json", "--data-binary", "@-",
                 f"{API}/channels/{channel}/messages", data=json.dumps({"content": text[:1990]})) == "200"


def probe() -> bool:
    c = _creds()
    return bool(c) and _curl("-H", f"Authorization: Bot {c[0]}", f"{API}/users/@me") == "200"


def _load(path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _save(path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))


def record_failure(key: str, text: str) -> None:
    failed = _load(FAILED)
    failed[key] = {"at": datetime.datetime.now().isoformat(timespec="seconds"), "text": text.splitlines()[0][:200]}
    _save(FAILED, failed)


def clear_failure(key: str) -> None:
    failed = _load(FAILED)
    if failed.pop(key, None) is not None:
        _save(FAILED, failed)


def failures() -> dict:
    """發不出去、還沒補發成功的警報(狀態頁用)。"""
    return _load(FAILED)


def once(key: str, problems: list[str], ok_text: str) -> None:
    now = datetime.datetime.now()
    sent = _load(SENT)
    last = sent.get(key)
    if isinstance(last, list):  # 舊格式:只有問題清單
        last = {"problems": last, "at": None}
    last_problems = (last or {}).get("problems", [])
    if sorted(problems) == sorted(last_problems):
        if not problems:
            clear_failure(key)  # 之前那則沒發出去、問題已經自己好了:沒有要補發的
            return
        at = (last or {}).get("at")
        if at and now - datetime.datetime.fromisoformat(at) < REMIND:
            return
        text = f"⏰ 還沒解決(上次提醒:{at[:16] if at else '之前'}):\n" + "\n".join(problems)
    elif problems:
        text = "⚠️ " + "\n".join(problems)
    else:
        text = "✅ " + ok_text
    if send(text):
        sent[key] = {"problems": sorted(problems), "at": now.isoformat(timespec="seconds")}
        _save(SENT, sent)
        clear_failure(key)
    else:
        record_failure(key, text)
