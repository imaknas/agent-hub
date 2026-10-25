#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
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
  env                  環境變數;值是 cwd 底下存在的相對路徑就換成絕對路徑。run 的第一個字是指令名稱(node)
                       而且沒寫 PATH:照 hubconf.LAUNCHD_PATH 找、也把它寫進 plist(nvm 預設版本的 bin 在
                       最前面——node 只由 nvm 管,見 hubconf.nvm_bin)。用路徑跑的(bin/x.sh)不加 PATH;
                       env 的值裡寫 $PLATFORM_PATH 會換成同一個 PATH
  log                  一個檔(stdout、stderr 都寫這裡,相對 cwd);沒寫就是 <hub>/.logs/<label 去掉 local.>-{stdout,stderr}.log
  expose               只宣告(tailnet port),不會自動開——狀態頁提醒要手動 tailscale serve

安全網(2026-10-24:Homebrew 的 node 被刪,這支把 plist 換成找不到的 `node`,LINE bot 停了一天半沒人知道):
  - 指令的第一個字解析不到 → 不動現在的 plist(能跑的照跑),只報問題
  - bootstrap 失敗、keepalive 的服務沒在跑、該載入的沒載入 → 報問題
  問題發 Discord(同一組只發一次,恢復時再發一次),exit 1(排程器記 job.failed,狀態頁紅)。

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
import alert  # noqa: E402
import hubconf  # noqa: E402
import registry  # noqa: E402

AGENTS = pathlib.Path.home() / "Library" / "LaunchAgents"
# launchd 底下的 PATH:指令照這個(或 service 自己 env 的 PATH)解析,不看跑這支的 shell 的 PATH
# (shell 可能有 nvm 之類的,launchd 沒有)
DEFAULT_PATH = hubconf.LAUNCHD_PATH
RENDERED = hubconf.STATE / "rendered-services.json"


def label_of(project: str, svc: dict) -> str:
    return svc.get("label") or f"local.{project}.{svc['name']}"


def render(project: str, doc: dict, svc: dict) -> tuple[dict, str | None]:
    """(plist, 問題)。問題 = 指令的第一個字解析不到(這時 plist 不能用)。"""
    d = registry.dirs(doc)
    cwd = d[svc.get("cwd") or registry.default_cwd(doc)]
    words = shlex.split(svc["run"])
    first = words[0]
    search = hubconf.expand_env((svc.get("env") or {}).get("PATH") or DEFAULT_PATH)
    if not first.startswith("/"):
        first = str(cwd / first) if "/" in first else (shutil.which(first, path=search) or first)
    problem = None if pathlib.Path(first).is_absolute() and os.access(first, os.X_OK) else \
        f"找不到 `{words[0]}`(PATH={search})"
    args = [first] + [str(cwd / w) if not w.startswith(("/", "-")) and (cwd / w).exists() else w for w in words[1:]]
    label = label_of(project, svc)
    p: dict = {"Label": label, "ProgramArguments": args, "WorkingDirectory": str(cwd)}
    env = {k: (str(cwd / v) if isinstance(v, str) and not v.startswith("/") and (cwd / v).exists()
               else hubconf.expand_env(str(v))) for k, v in (svc.get("env") or {}).items()}
    if "/" not in words[0] and "PATH" not in env:
        env["PATH"] = DEFAULT_PATH  # 指令照平台的 PATH 找到的(node 等),程序自己(npm 的子程序)也要看得到
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
    return p, problem


def bootstrap(uid: int, label: str, path: pathlib.Path) -> subprocess.CompletedProcess:
    """bootout 是非同步的:舊程序還沒結束就 bootstrap 會 `5: Input/output error`(服務就停在沒載入)。
    等它從 launchd 消失(最多 20 秒),失敗再試幾次。"""
    import time
    for _ in range(40):
        if launchctl("print", f"gui/{uid}/{label}").returncode != 0:
            break
        time.sleep(0.5)
    for attempt in range(5):
        r = launchctl("bootstrap", f"gui/{uid}", str(path))
        if r.returncode == 0:
            return r
        time.sleep(2 * (attempt + 1))
    return r


def running(uid: int, label: str) -> str | None:
    """launchctl print 的 state(running / not running / …);沒載入回傳 None。"""
    r = launchctl("print", f"gui/{uid}/{label}")
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        if line.strip().startswith("state = "):
            return line.split("=", 1)[1].strip()
    return "unknown"


def launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True)


def main() -> int:
    dry = "--dry-run" in sys.argv
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    index = registry.refresh()
    uid = os.getuid()
    want: dict[str, tuple[str, dict]] = {}
    problems: list[str] = []
    keep: set[str] = set()       # 指令解析不到:現在的 plist 不動
    keepalive: set[str] = set()  # 應該一直在跑的
    for name, doc in sorted(index["projects"].items()):
        for svc in doc.get("services") or []:
            label = label_of(name, svc)
            plist, problem = render(name, doc, svc)
            want[label] = (name, plist)
            if problem:
                problems.append(f"{label}({name}):{problem}——plist 不動,沒有就不建")
                keep.add(label)
            if svc.get("keepalive"):
                keepalive.add(label)
    try:
        mine = set(json.loads(RENDERED.read_text()))
    except (OSError, ValueError):
        mine = set()
    changed = 0
    for label, (proj, plist) in sorted(want.items()):
        if only and label != only:
            continue
        if label in keep:
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
        r = bootstrap(uid, label, path)
        if r.returncode != 0:
            print(f"    ⚠️ bootstrap 失敗:{r.stderr.strip()}")
            problems.append(f"{label}({proj}):bootstrap 失敗:{r.stderr.strip()[:200]}")
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
    if not dry and not only:
        for label in sorted(want):
            if any(label in x for x in problems):
                continue
            state = running(uid, label)
            if state is None:
                problems.append(f"{label}({want[label][0]}):launchd 沒有載入")
            elif label in keepalive and state != "running":
                problems.append(f"{label}({want[label][0]}):keepalive 的服務沒在跑(state = {state})")
        alert.once("render-services", problems, "背景服務都恢復正常了(render-services)")
    for x in problems:
        print(f"⚠️ {x}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
