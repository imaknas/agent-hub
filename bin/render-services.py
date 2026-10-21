#!/usr/bin/env python3
"""專案登錄的 services → launchd plist(~/Library/LaunchAgents/<label>.plist)。

每個專案在 projects/<name>.yaml 宣告它的長駐程式,平台照這裡的規則產生 plist,內容變了才
bootout/bootstrap(沒變什麼都不做,不會無故重啟服務)。登錄裡拿掉的 service:只有這支產生過的
(記在 .state/rendered-services.json)才會被卸載,別人的 plist 不碰。平台自己的核心(排程器、
看門狗、狀態頁)不在這裡:它們是 launchd/ 底下版控的 plist——排程器不能自己重載自己。

service 欄位(registry.py 驗證):
  name                 必填;label 預設 local.<專案>.<name>
  label                沿用舊 label(例:別的腳本按 label 重啟)
  run                  指令;第一個字解析成絕對路徑,其他參數是 cwd 底下存在的檔案也換成絕對路徑
  cwd                  runtime(有 runtime 時預設)| workspace | hub
  keepalive: true      KeepAlive(掛了就重啟);不寫就是只跑一次 / 照 interval_s 跑
  interval_s           StartInterval(每隔幾秒跑一次)
  throttle_s           ThrottleInterval
  env                  環境變數;值是 cwd 底下存在的相對路徑就換成絕對路徑
  log                  一個檔(stdout、stderr 都寫這裡,相對 cwd);沒寫就是 <hub>/.logs/<label 去掉 local.>-{stdout,stderr}.log
  expose               只宣告(tailnet port),不會自動開——狀態頁提醒要手動 tailscale serve

用法:render-services.py [--dry-run] [--only <label>]
"""
from __future__ import annotations

import json
import os
import pathlib
import plistlib
import shlex
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import registry  # noqa: E402

AGENTS = pathlib.Path.home() / "Library" / "LaunchAgents"
# launchd 底下的 PATH:指令照這個(或 service 自己 env 的 PATH)解析,不看跑這支的 shell 的 PATH
# (shell 可能有 nvm 之類的,launchd 沒有)
DEFAULT_PATH = hubconf.LAUNCHD_PATH
RENDERED = hubconf.STATE / "rendered-services.json"


def label_of(project: str, svc: dict) -> str:
    return svc.get("label") or f"local.{project}.{svc['name']}"


def render(project: str, doc: dict, svc: dict) -> dict:
    d = registry.dirs(doc)
    cwd = d[svc.get("cwd") or registry.default_cwd(doc)]
    words = shlex.split(svc["run"])
    first = words[0]
    if not first.startswith("/"):
        search = (svc.get("env") or {}).get("PATH") or DEFAULT_PATH
        first = str(cwd / first) if "/" in first else (shutil.which(first, path=search) or first)
    args = [first] + [str(cwd / w) if not w.startswith(("/", "-")) and (cwd / w).exists() else w for w in words[1:]]
    label = label_of(project, svc)
    p: dict = {"Label": label, "ProgramArguments": args, "WorkingDirectory": str(cwd)}
    env = {k: (str(cwd / v) if isinstance(v, str) and not v.startswith("/") and (cwd / v).exists() else str(v))
           for k, v in (svc.get("env") or {}).items()}
    if env:
        p["EnvironmentVariables"] = env
    p["RunAtLoad"] = True
    if svc.get("keepalive"):
        p["KeepAlive"] = True
    if svc.get("interval_s"):
        p["StartInterval"] = int(svc["interval_s"])
    if svc.get("throttle_s"):
        p["ThrottleInterval"] = int(svc["throttle_s"])
    if svc.get("log"):
        p["StandardOutPath"] = p["StandardErrorPath"] = str(cwd / svc["log"])
    else:
        base = hubconf.LOGS / label.removeprefix("local.")
        p["StandardOutPath"], p["StandardErrorPath"] = f"{base}-stdout.log", f"{base}-stderr.log"
    return p


def launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True)


def main() -> int:
    dry = "--dry-run" in sys.argv
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    index = registry.refresh()
    uid = os.getuid()
    want: dict[str, tuple[str, dict]] = {}
    for name, doc in sorted(index["projects"].items()):
        for svc in doc.get("services") or []:
            want[label_of(name, svc)] = (name, render(name, doc, svc))
    try:
        mine = set(json.loads(RENDERED.read_text()))
    except (OSError, ValueError):
        mine = set()
    changed = 0
    for label, (proj, plist) in sorted(want.items()):
        if only and label != only:
            continue
        path = AGENTS / f"{label}.plist"
        cur = plistlib.loads(path.read_bytes()) if path.exists() else None
        if cur == plist:
            continue
        changed += 1
        print(f"{'(dry-run) ' if dry else ''}{label}({proj}):{'新增' if cur is None else '更新'}")
        if cur is not None:
            for k in sorted(set(cur) | set(plist)):
                if cur.get(k) != plist.get(k):
                    print(f"    {k}: {cur.get(k)!r} → {plist.get(k)!r}")
        if dry:
            continue
        launchctl("bootout", f"gui/{uid}/{label}")
        path.write_bytes(plistlib.dumps(plist))
        r = launchctl("bootstrap", f"gui/{uid}", str(path))
        if r.returncode != 0:
            print(f"    ⚠️ bootstrap 失敗:{r.stderr.strip()}")
    for label in sorted(mine - set(want)):
        if only and label != only:
            continue
        print(f"{'(dry-run) ' if dry else ''}{label}:登錄裡已經沒有,卸載")
        if not dry:
            launchctl("bootout", f"gui/{uid}/{label}")
            (AGENTS / f"{label}.plist").unlink(missing_ok=True)
    if not dry and not only:
        RENDERED.write_text(json.dumps(sorted(want)))
    if not changed:
        print("services:都跟登錄一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
