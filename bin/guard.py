#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""第二階段的守門:專案的 session 不能開別的專案的 session(跨專案只能寄信)。

平台事件 open_session_attempt(bin/hub-event 呼叫 decide)。輸入是中立的 target——執行環境的 binding 從原生的
參數翻出來(Claude Code:Desktop 的 start_session 工具;見 runtime_claude.parse_event):
  cwd        → 不在這個 session 自己的專案(主 checkout 或它的 worktree)底下就擋
  elsewhere  → 別的 repository、別台機器、看不出是哪裡的 preset:擋
都沒給 = 同一個專案,放行。擋的時候回理由(叫它寄信),由 binding 翻成原生的「拒絕」。
不是 onboard 過的專案(例:平台自己)不擋;這支自己出錯一律放行(不能因為守門壞掉擋住正常工作)。
`guard.py pre-tool` 是舊的入口,還沒換新設定的專案還在叫它。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402


def main_checkout(cwd: pathlib.Path) -> pathlib.Path | None:
    r = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                       capture_output=True, text=True)
    return pathlib.Path(r.stdout.strip()).parent.resolve() if r.returncode == 0 else None


def own_project(cwd: pathlib.Path) -> tuple[str | None, pathlib.Path | None]:
    main = main_checkout(cwd)
    for name in hubconf.projects():
        ws = hubconf.workspace(name)
        if ws and main and ws.resolve() == main and name != hubconf.hub_project():
            return name, main
    return None, None


def decide(target: dict, cwd) -> str | None:
    """平台的邏輯:這個 session 想開的新 session 在別的專案(或別的地方)就回傳擋下的理由,否則 None。
    target 是中立的 {"cwd": 路徑或 None, "elsewhere": 說明或 None}(執行環境的 binding 從原生的參數翻出來)。"""
    proj, root = own_project(pathlib.Path(cwd or os.getcwd()))
    if not proj:
        return None
    how = f"跨專案的事請寄信:~/agent-hub/bin/hubbus send --to <專案> --type request --text \"…\";或請 Knas 自己到那個專案開 session。"
    where = target.get("elsewhere") or {}
    if where.get("kind") == "repository":
        return f"{proj} 的 session 不能在別的 repository({where.get('name', '')})開 session。{how}"
    if where.get("kind") == "machine":
        return f"{proj} 的 session 不能到別台機器開 session。{how}"
    if where.get("kind"):  # unknown_place:例如存好的地點,看不出是不是同一個專案
        return f"{proj} 的 session 用 preset 開 session 時看不出是不是同一個專案;同一個專案就不要給 preset/cwd。{how}"
    if target.get("cwd"):
        t = pathlib.Path(target["cwd"]).resolve()
        if t != root and root not in t.parents:
            return f"{proj} 的 session 不能在 {target['cwd']} 開 session(那不是這個專案)。{how}"
    return None


def main() -> int:
    """舊的入口 `guard.py pre-tool`:還沒換新設定的專案還在叫它。輸入、輸出都透過執行環境的 binding。"""
    if sys.argv[1:] != ["pre-tool"]:
        print(__doc__)
        return 2
    try:
        import runtime_ports
        rt = runtime_ports.load(ports=("lifecycle",))
        ev = rt.parse_event("open_session_attempt", sys.stdin.read())
        if ev is not None:
            reason = decide(ev.target or {}, ev.cwd)
            if reason:
                print(rt.deny("open_session_attempt", reason))
    except Exception:  # 守門壞掉不能擋正常工作
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
