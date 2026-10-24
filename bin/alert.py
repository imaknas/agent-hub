"""發 Discord 警報(hub.json alerts:bot token 跟頻道 id 的檔案,在 .secrets/,不進版控)。

只用標準函式庫 + 系統的 curl(不靠 Homebrew)。排程器看門狗刻意有自己的一份,不 import 這支
(它要在排程器、平台程式碼都壞掉的時候還能發)。

  alert.send(text) -> bool
  alert.once(key, problems, ok_text) -> None   同一組問題只發一次;問題消失時發一次恢復
"""
from __future__ import annotations

import json
import subprocess

import hubconf

SENT = hubconf.STATE / "alerts-sent.json"


def send(text: str) -> bool:
    try:
        token = hubconf.p("alerts.discord_token_file").read_text().strip()
        channel = hubconf.p("alerts.discord_channel_file").read_text().strip()
    except (OSError, AttributeError):
        return False
    r = subprocess.run(
        ["/usr/bin/curl", "-sS", "-m", "20", "-o", "/dev/null", "-w", "%{http_code}",
         "-H", f"Authorization: Bot {token}", "-H", "Content-Type: application/json",
         "--data-binary", "@-", f"https://discord.com/api/v10/channels/{channel}/messages"],
        input=json.dumps({"content": text[:1990]}), capture_output=True, text=True)
    return r.stdout.strip() == "200"


def once(key: str, problems: list[str], ok_text: str) -> None:
    """problems 跟上次發的一樣就不再發;變成空的時候發 ok_text。發不出去就下次再試。"""
    try:
        sent = json.loads(SENT.read_text())
    except (OSError, ValueError):
        sent = {}
    last = sent.get(key, [])
    if sorted(problems) == sorted(last):
        return
    if problems:
        ok = send("⚠️ " + "\n".join(problems))
    else:
        ok = send("✅ " + ok_text)
    if ok:
        sent[key] = sorted(problems)
        SENT.write_text(json.dumps(sent, ensure_ascii=False, indent=1))
