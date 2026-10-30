"""發警報 / 通知(hub.json alerts)。管道可以好幾個,設了哪個就發哪個(docs/design/tool-agnostic.md:不綁 Discord):
  discord   discord_token_file + discord_channel_file(bot token、頻道 id 的檔案,在 secrets 目錄,不進版控)
  ntfy      {"topic": …, "base_url": 預設 https://ntfy.sh, "token_file": 選填}(手機 app 就收得到,不用帳號)
  email     {"host", "port": 預設 587, "user", "from", "to", "password_file", "ssl": false = STARTTLS}
  macos     true(這台機器的系統通知;只有人坐在這台前面才看得到)

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
import i18n

_ = i18n.domain(__file__)

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


def _discord(text: str) -> bool:
    c = _creds()
    if not c:
        return False
    token, channel = c
    return _curl("-H", f"Authorization: Bot {token}", "-H", "Content-Type: application/json", "--data-binary", "@-",
                 f"{API}/channels/{channel}/messages", data=json.dumps({"content": text[:1990]})) == "200"


def _ntfy(text: str) -> bool:
    conf = hubconf.get("alerts.ntfy") or {}
    if not conf.get("topic"):
        return False
    headers = ["-H", "Title: agent-hub"]
    if conf.get("token_file"):
        try:
            headers += ["-H", f"Authorization: Bearer {hubconf.path(conf['token_file']).read_text().strip()}"]
        except OSError:
            return False
    url = f"{(conf.get('base_url') or 'https://ntfy.sh').rstrip('/')}/{conf['topic']}"
    return _curl(*headers, "--data-binary", "@-", url, data=text[:4000]) == "200"


def _email(text: str) -> bool:
    import smtplib
    from email.message import EmailMessage
    conf = hubconf.get("alerts.email") or {}
    if not conf.get("host") or not conf.get("to"):
        return False
    msg = EmailMessage()
    msg["Subject"] = "agent-hub:" + text.splitlines()[0][:80] if text else "agent-hub"
    msg["From"], msg["To"] = conf.get("from") or conf.get("user") or "agent-hub", conf["to"]
    msg.set_content(text)
    try:
        password = hubconf.path(conf["password_file"]).read_text().strip() if conf.get("password_file") else None
        cls = smtplib.SMTP_SSL if conf.get("ssl") else smtplib.SMTP
        with cls(conf["host"], int(conf.get("port") or (465 if conf.get("ssl") else 587)), timeout=20) as s:
            if not conf.get("ssl"):
                s.starttls()
            if conf.get("user") and password:
                s.login(conf["user"], password)
            s.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException):
        return False


def _macos(text: str) -> bool:
    if not hubconf.get("alerts.macos"):
        return False
    script = f"display notification {json.dumps(text[:200])} with title \"agent-hub\""
    return subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True).returncode == 0


CHANNELS = {"discord": (_discord, lambda: _creds() is not None), "ntfy": (_ntfy, lambda: bool(hubconf.get("alerts.ntfy"))),
            "email": (_email, lambda: bool(hubconf.get("alerts.email"))), "macos": (_macos, lambda: bool(hubconf.get("alerts.macos")))}


def configured() -> list[str]:
    return [n for n, (_send, on) in CHANNELS.items() if on()]


def send(text: str) -> bool:
    """設了的管道全部發;全部都發出去才算成功(有一個發不出去就記失敗,狀態頁會標紅)。一個都沒設 = False。"""
    chans = configured()
    return bool(chans) and all(CHANNELS[n][0](text) for n in chans)


def probe() -> bool:
    """警報管道本身能不能用:Discord 打 /users/@me(不發訊息);其他管道沒有不發訊息的檢查,設了就當可以。沒有任何管道 = False。"""
    chans = configured()
    if not chans:
        return False
    if "discord" in chans:
        c = _creds()
        return _curl("-H", f"Authorization: Bot {c[0]}", f"{API}/users/@me") == "200"
    return True


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
        text = _("⏰ Still not resolved (last reminder: {at}):", at=at[:16] if at else _("earlier")) + "\n" \
            + "\n".join(problems)
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
