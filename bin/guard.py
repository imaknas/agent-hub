#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Second-stage guard: a project's session may not open a session for another project (across projects: letters only).

Platform event open_session_attempt (bin/hub-event calls decide). The input is a neutral target, which the runtime's
binding translates from the native arguments (Claude Code: the Desktop start_session tool; see runtime_claude.parse_event):
  cwd        → not under this session's own project (the main checkout or one of its worktrees): denied
  elsewhere  → another repository, another machine, a preset that does not say where: denied
If neither is given, it is the same project and is allowed. A denial returns the reason (telling it to send a letter),
which the binding turns into the native "deny". Projects never onboarded (e.g. the platform itself) are not guarded;
when this script itself fails it allows (a broken guard must not block normal work).
`guard.py pre-tool` is the old entry point; projects that have not switched to the new settings still call it.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)


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
    how = _("To ask another project for something, send a letter: agenthub send --to <project> --type request "
            "--text \"…\"; or ask the owner to open a session in that project themselves.")
    where = target.get("elsewhere") or {}
    if where.get("kind") == "repository":
        return _("A {project} session cannot open a session in another repository ({name}). {how}", project=proj,
                 name=where.get('name', ''), how=how)
    if where.get("kind") == "machine":
        return _("A {project} session cannot open a session on another machine. {how}", project=proj, how=how)
    if where.get("kind"):  # unknown_place:例如存好的地點,看不出是不是同一個專案
        return _("When a {project} session opens a session with a preset, there is no way to tell whether it is the "
                 "same project; for the same project, give no preset or cwd. {how}", project=proj, how=how)
    if target.get("cwd"):
        t = pathlib.Path(target["cwd"]).resolve()
        if t != root and root not in t.parents:
            return _("A {project} session cannot open a session in {cwd} (that is not this project). {how}",
                     project=proj, cwd=target['cwd'], how=how)
    return None


def main() -> int:
    """舊的入口 `guard.py pre-tool`:還沒換新設定的專案還在叫它。輸入、輸出都透過執行環境的 binding。"""
    if sys.argv[1:] != ["pre-tool"]:
        print(_(__doc__))
        return 2
    try:
        import runtime_ports
        try:
            rt = runtime_ports.load(ports=("lifecycle",))
        except runtime_ports.Unsupported:   # 沒有 hook 的執行環境不會叫到這裡
            return 0
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
