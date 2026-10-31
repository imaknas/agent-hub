"""Linux 的服務管理器:平台產生的 launchd plist(render-services 的 render()、launchd/ 的排程器跟看門狗樣板)→ systemd 的
user unit(~/.config/systemd/user/),跟載入、查狀態、卸載。macOS 照舊用 launchd,這支只在 hubconf.service_manager() 是
systemd 時用。標準函式庫。

對應(平台用到的 plist 欄位就這些):
  ProgramArguments → ExecStart(每個參數用雙引號;% 跟 $ 照 systemd 的規則跳脫)
  WorkingDirectory、EnvironmentVariables → WorkingDirectory=、Environment=
  StandardOutPath / StandardErrorPath → StandardOutput=append: / StandardError=append:
  KeepAlive → Restart=always(RestartSec = ThrottleInterval,沒寫 10 秒)、WantedBy=default.target
  StartInterval(沒有 KeepAlive)→ Type=oneshot 的 service + 一個 timer(OnActiveSec 1 秒 = 載入就跑一次,像 RunAtLoad;
                                    OnUnitActiveSec = 間隔)
  RunAtLoad、沒有間隔、沒有 KeepAlive → 一般的 service,WantedBy=default.target
登出之後也要跑:要開 `loginctl enable-linger <使用者>`(doctor 會說)。
"""
from __future__ import annotations

import os
import pathlib
import subprocess

import i18n

_ = i18n.domain(__file__)

UNIT_DIR = pathlib.Path.home() / ".config" / "systemd" / "user"


def _q(s: str) -> str:
    """一個 ExecStart 參數 / Environment 值:雙引號包起來;\\ 跟 " 跳脫;% 變 %%(specifier);ExecStart 裡的 $ 變 $$。"""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def target(plist: dict) -> str:
    """要 enable 的那一個 unit:有間隔、不是一直跑的 → timer;其他 → service。"""
    return f"{plist['Label']}.timer" if plist.get("StartInterval") and not plist.get("KeepAlive") else f"{plist['Label']}.service"


def files(plist: dict) -> dict[str, str]:
    """plist → {檔名: 內容}。內容只由 plist 決定(同一份 plist 每次一樣),render-services 比對內容決定要不要重載。"""
    label = plist["Label"]
    timer = target(plist).endswith(".timer")
    lines = ["[Unit]", f"Description=agent-hub {label}", "", "[Service]",
             f"Type={'oneshot' if timer else 'simple'}",
             "ExecStart=" + " ".join(_q(a).replace("$", "$$") for a in plist["ProgramArguments"])]
    if plist.get("WorkingDirectory"):
        lines.append(f"WorkingDirectory={plist['WorkingDirectory']}")
    for k, v in sorted((plist.get("EnvironmentVariables") or {}).items()):
        lines.append(f"Environment={_q(f'{k}={v}')}")
    if plist.get("StandardOutPath"):
        lines.append(f"StandardOutput=append:{plist['StandardOutPath']}")
    if plist.get("StandardErrorPath"):
        lines.append(f"StandardError=append:{plist['StandardErrorPath']}")
    if plist.get("KeepAlive"):
        lines += ["Restart=always", f"RestartSec={int(plist.get('ThrottleInterval') or 10)}"]
    if not timer:
        lines += ["", "[Install]", "WantedBy=default.target"]
    out = {f"{label}.service": "\n".join(lines) + "\n"}
    if timer:
        out[f"{label}.timer"] = "\n".join([
            "[Unit]", "Description=" + _("agent-hub {label} (every {secs} s)", label=label, secs=int(plist['StartInterval'])),
            "",
            "[Timer]", "OnActiveSec=1" if plist.get("RunAtLoad") else f"OnActiveSec={int(plist['StartInterval'])}",
            f"OnUnitActiveSec={int(plist['StartInterval'])}", "AccuracySec=1", "",
            "[Install]", "WantedBy=timers.target"]) + "\n"
    return out


def installed(label: str) -> dict[str, str]:
    """現在裝著的這個 label 的 unit 檔(沒有 = 空的)。"""
    out = {}
    for suffix in (".service", ".timer"):
        f = UNIT_DIR / f"{label}{suffix}"
        if f.exists():
            out[f.name] = f.read_text()
    return out


def systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def install(plist: dict) -> subprocess.CompletedProcess:
    """寫 unit 檔、daemon-reload、enable --now 要跑的那個;service 已經在跑就 restart(換上新的內容)。"""
    label, want = plist["Label"], files(plist)
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    for name in set(installed(label)) - set(want):
        (UNIT_DIR / name).unlink()   # 例:原本有間隔、現在改成一直跑
    for name, text in want.items():
        (UNIT_DIR / name).write_text(text)
    systemctl("daemon-reload")
    r = systemctl("enable", "--now", target(plist))
    if r.returncode == 0 and target(plist).endswith(".service"):
        systemctl("restart", f"{label}.service")
    return r


def remove(label: str) -> None:
    for name in installed(label):
        systemctl("disable", "--now", name)
        (UNIT_DIR / name).unlink(missing_ok=True)
    systemctl("daemon-reload")


def state(label: str) -> str | None:
    """跟 render-services 的 running() 同一種回答:沒載入 = None;一直跑的 service → running / not running;
    timer → 它有沒有在排(active)。"""
    have = installed(label)
    if not have:
        return None
    unit = f"{label}.timer" if f"{label}.timer" in have else f"{label}.service"
    r = systemctl("is-active", unit)
    return "running" if r.stdout.strip() == "active" else "not running"


def linger() -> bool | None:
    """登出之後 user unit 還跑不跑(loginctl enable-linger);查不到 = None。"""
    r = subprocess.run(["loginctl", "show-user", os.environ.get("USER") or str(os.getuid()), "-p", "Linger"],
                       capture_output=True, text=True)
    return None if r.returncode != 0 else r.stdout.strip() == "Linger=yes"
