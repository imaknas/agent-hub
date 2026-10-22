#!/usr/bin/env python3
"""第二階段的 PreToolUse 守門:專案的 session 不能開別的專案的 session(跨專案只能寄信)。

matcher:mcp__ccd_session__start_session(精確比對)。stdin 是 hook JSON(cwd = 這個 session 在哪,
tool_input = start_session 的參數)。新 session 要去的地方:
  cwd        → 不在這個 session 自己的專案(主 checkout 或它的 worktree)底下就擋
  repository → 別的 GitHub repo:擋
  host       → 別台機器:擋
  preset     → 存好的地點,這裡看不出是不是同一個專案:擋(請在 cwd 寫清楚)
都沒給 = 同一個專案,放行。擋的時候用 permissionDecision: deny 加理由(叫它寄信)。
不是 onboard 過的專案(例:平台自己)不擋;這支自己出錯一律放行(不能因為守門壞掉擋住正常工作)。
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


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}, ensure_ascii=False))


def check(data: dict) -> None:
    ti = data.get("tool_input") or {}
    proj, root = own_project(pathlib.Path(data.get("cwd") or os.getcwd()))
    if not proj:
        return
    how = f"跨專案的事請寄信:~/agent-hub/bin/hubbus send --to <專案> --type request --text \"…\";或請 Knas 自己到那個專案開 session。"
    if ti.get("repository"):
        return deny(f"{proj} 的 session 不能在別的 repository({ti['repository']})開 session。{how}")
    if ti.get("host") or ti.get("target") == "ssh":
        return deny(f"{proj} 的 session 不能到別台機器開 session。{how}")
    if ti.get("preset"):
        return deny(f"{proj} 的 session 用 preset 開 session 時看不出是不是同一個專案;同一個專案就不要給 preset/cwd。{how}")
    if ti.get("cwd"):
        target = pathlib.Path(os.path.expanduser(ti["cwd"])).resolve()
        if target != root and root not in target.parents:
            return deny(f"{proj} 的 session 不能在 {ti['cwd']} 開 session(那不是這個專案)。{how}")


def main() -> int:
    if sys.argv[1:] != ["pre-tool"]:
        print(__doc__)
        return 2
    try:
        check(json.loads(sys.stdin.read() or "{}"))
    except Exception:  # 守門壞掉不能擋正常工作
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
