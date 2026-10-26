#!/usr/bin/env python3
"""排程器看門狗:scheduler.py 停了(launchd job 不見、tick 卡死、python 壞掉)就發 Discord。

scheduler.py 是 librarian、weekly-reflect、mirror-backup、所有 muninn-* job 唯一的觸發點,
它停了什麼都不會叫——muninn 的 web server 是另一個 launchd job,PWA 照樣打得開,只是不再更新。
所以這支刻意不靠排程器、也不靠 muninn:

- 自己一個 launchd job(local.agent-hub-scheduler-watchdog,每 5 分鐘)
- 用系統的 /usr/bin/python3、只用標準函式庫:Homebrew 的 python 壞掉讓排程器停了,這支還在
- 通知走 Discord REST(bot token,curl),不經過 muninn

判斷:scheduler.py 每個 tick 開始、每個 job 跑完都寫 .state/scheduler-heartbeat;超過
STALE_MIN 分鐘沒更新、連續兩次檢查都這樣(剛開機/剛恢復的那一輪不誤報)才發。之後每
REMIND_H 小時提醒一次,恢復時發一則「恢復了」。

設定(不進版控,.secrets/ 在 .gitignore):
  .secrets/discord-token          bot token(跟 project-36 / project-16 同一個 bot)
  .secrets/discord-alert-channel  要發到哪個頻道的 channel id
沒設定就在 log 裡大聲失敗(exit 1),不會假裝有在顧。

用法:scheduler-watchdog.py          檢查一次(launchd 用)
      scheduler-watchdog.py --test   發一則測試訊息,確認 token/頻道設定對
"""
import datetime
import json
import os
import pathlib
import subprocess
import sys

HUB = pathlib.Path(os.environ.get("AGENT_HUB_ROOT") or pathlib.Path(__file__).resolve().parents[1])  # 跟 hubconf.ROOT 一樣


def _instance(sub: str, legacy: pathlib.Path) -> pathlib.Path:
    """實例目錄的擺法(跟 hubconf 同一套規則,docs/design/new-user.md 3.1)。刻意不 import 平台的任何模組:
    平台壞了這支也要能跑(tests/test_python_floor.py 檢查)。"""
    env = {"config": "AGENT_HUB_CONFIG", "secrets": "AGENT_HUB_SECRETS"}[sub]
    if os.environ.get(env):
        return pathlib.Path(os.path.expanduser(os.environ[env]))
    home = os.environ.get("AGENT_HUB_HOME") or ((HUB / ".hub-home").read_text().strip()
                                                if (HUB / ".hub-home").is_file() else "")
    return pathlib.Path(os.path.expanduser(home)) / sub if home else legacy


CONFIG_DIR, SECRETS_DIR = _instance("config", HUB), _instance("secrets", HUB / ".secrets")
HEARTBEAT = HUB / ".state" / "scheduler-heartbeat"
STATE_FILE = HUB / ".state" / "scheduler-last-run.json"
WATCH_STATE = HUB / ".state" / "scheduler-watchdog.json"
SCHED_LOG = HUB / ".logs" / "scheduler.log"
LOG = HUB / ".logs" / "scheduler-watchdog.log"
TOKEN = SECRETS_DIR / "discord-token"
CHANNEL = SECRETS_DIR / "discord-alert-channel"
LABEL = "local.agent-hub-scheduler"
STALE_MIN = 35  # tick 每 15 分鐘;兩個 tick 沒來再加幾分鐘漂移
REMIND_H = 6


def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")


def last_tick():
    """最後一次 tick 的時間:heartbeat,沒有的話退回狀態檔的修改時間(舊版 scheduler)。
    不取兩者較新的:schedulectl run 也會寫狀態檔,會蓋掉排程器其實停了的事實。"""
    for p in (HEARTBEAT, STATE_FILE):
        if p.exists():
            return datetime.datetime.fromtimestamp(p.stat().st_mtime)
    return None


def diagnose():
    uid = os.getuid()
    r = subprocess.run(["/bin/launchctl", "print", f"gui/{uid}/{LABEL}"], capture_output=True, text=True)
    if r.returncode != 0:
        lines = [f"launchd 裡沒有 {LABEL}(被 bootout 了?)→ `schedulectl start`"]
    else:
        info = {}
        for l in r.stdout.splitlines():  # 第一個出現的才是 job 本身的(後面還有 endpoint 之類的 state)
            k, eq, v = l.partition("=")
            if eq:
                info.setdefault(k.strip(), v.strip())
        lines = [f"launchd:state={info.get('state', '?')} pid={info.get('pid', '-')} "
                 f"last exit={info.get('last exit code', '?')}"]
        ps = subprocess.run(["/bin/ps", "-axo", "pid,etime,command"], capture_output=True, text=True).stdout
        stuck = [l.strip() for l in ps.splitlines() if "bin/scheduler.py" in l and "watchdog" not in l]
        if stuck:
            lines.append(f"scheduler.py 還在跑(卡在某個 job?):{stuck[0][:120]}")
        lines.append(f"重啟:`launchctl kickstart -k gui/{uid}/{LABEL}`")
    if SCHED_LOG.exists():
        tail = SCHED_LOG.read_text(errors="replace").splitlines()[-3:]
        lines.append("scheduler.log 最後幾行:\n```\n" + "\n".join(t[:180] for t in tail) + "\n```")
    return "\n".join(lines)


def send(text):
    """回傳是否送成功。curl 是系統內建的,不靠 Homebrew。"""
    try:
        token, channel = TOKEN.read_text().strip(), CHANNEL.read_text().strip()
    except OSError:
        log(f"失敗:沒有設定 {TOKEN} 或 {CHANNEL},警報發不出去")
        return False
    r = subprocess.run(
        ["/usr/bin/curl", "-sS", "-m", "20", "-o", "/dev/null", "-w", "%{http_code}",
         "-H", f"Authorization: Bot {token}", "-H", "Content-Type: application/json",
         "--data-binary", "@-", f"https://discord.com/api/v10/channels/{channel}/messages"],
        input=json.dumps({"content": text[:1990]}), capture_output=True, text=True)
    if r.stdout.strip() != "200":
        log(f"Discord 送出失敗:HTTP {r.stdout.strip() or '-'} {r.stderr.strip()[:200]}")
        return False
    return True


def alerts_configured() -> bool:
    import json
    try:
        return bool(json.loads((CONFIG_DIR / "hub.json").read_text()).get("alerts"))
    except (OSError, ValueError):
        return True  # 讀不到 hub.json 就照舊:要有 Discord 才算有在顧


def main():
    if sys.argv[1:] == ["--test"]:
        ok = send("🧪 agent-hub 排程器看門狗測試訊息(設定正確)\n" + diagnose())
        print("已送出" if ok else f"送不出去,見 {LOG}")
        return 0 if ok else 1

    if not alerts_configured():
        # hub.json 沒有 alerts 段 = owner 選了不要警報(新使用者的預設):照樣檢查、記 log,不當成失敗
        tick = last_tick()
        stale = tick is None or (datetime.datetime.now() - tick).total_seconds() / 60 > STALE_MIN
        if stale:
            log(f"沒設定警報(hub.json 沒有 alerts),只記錄:排程器最後 tick {tick or '從來沒有'},停了")
        return 0
    if not (TOKEN.exists() and CHANNEL.exists()):
        # 每 5 分鐘印到 launchd 的 stderr log、exit 1;狀態頁也會標出「看門狗沒設定」
        print(f"scheduler-watchdog: 沒有設定 {TOKEN} / {CHANNEL},排程器停了也發不出警報", file=sys.stderr)
        return 1

    now = datetime.datetime.now()
    st = json.loads(WATCH_STATE.read_text()) if WATCH_STATE.exists() else {}
    tick = last_tick()
    age_min = (now - tick).total_seconds() / 60 if tick else None
    stale = age_min is None or age_min > STALE_MIN
    when = f"{tick:%m-%d %H:%M}" if tick else "從來沒有"

    if not stale:
        if st.get("alerting"):
            if send(f"✅ agent-hub 排程器恢復了(最後 tick {when};停了大約從 {st.get('since', '?')} 開始)"):
                log("恢復,已通知")
                st = {}
        else:
            st = {}
    else:
        st["stale_checks"] = st.get("stale_checks", 0) + 1
        st.setdefault("since", when)
        last = st.get("last_alert")
        due = (st["stale_checks"] >= 2 and not st.get("alerting")) or (
            st.get("alerting") and now - datetime.datetime.fromisoformat(last) >= datetime.timedelta(hours=REMIND_H))
        if due:
            age = f"{age_min:.0f} 分鐘" if age_min is not None else "很久"
            prefix = "🚨" if not st.get("alerting") else "⏰ 還沒好:"
            text = (f"{prefix} agent-hub 排程器已經 {age} 沒有 tick(最後 {when})。librarian、"
                    f"muninn-tick/reflect、上傳 job 都停了。\n{diagnose()}")
            if send(text):
                st.update(alerting=True, last_alert=now.isoformat(timespec="seconds"))
                log(f"排程器停了 {age},已通知")
            else:
                return 1
    WATCH_STATE.parent.mkdir(parents=True, exist_ok=True)
    WATCH_STATE.write_text(json.dumps(st, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
