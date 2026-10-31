#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""把一個專案(正式專案或想法沙盒)整套改名,包括 GitHub repo——lease-runner 的 POST /v1/projects/<名稱>/rename 用
(10-25 Muninn m10251650,Knas 確認)。

rename-project.sh 管登記、memory bare repo/副本、mirror、本機資料夾,但要求 GitHub repo 先改好;這支把那一步補上,
再交給它,並記下做到哪裡,中途失敗再叫一次可以接著做。

用法:project-rename.py <舊名稱> <新名稱> [--apply] [--json]
  不加 --apply 只印出會做什麼。--json:最後一行印結果:
    {"result": "renamed"|"already", "name", "origin"}                                         exit 0
    {"result": "bad_name"|"not_found"|"name_taken"|"busy"|"failed", "detail"}                exit 1
做的事(照順序;每做完一個改外面的步驟就記進 .state/project-renames.json):
  1. 檢查(動手前全部做完):新名稱的規則(小寫英數跟 -,最多 40 字;跟沙盒同一套)、不是平台自己、新名稱沒被登錄/資料夾/
     memory repo 用掉;origin 是 GitHub 的話,GitHub 上新名稱的 repo 不存在(或是上次自己改過去的);
     **沒有程序的工作目錄在舊資料夾裡**(含子資料夾,例:worktree 裡的 session)→ 有就 busy,GitHub 也還沒動(10-25 review);
     這個專案沒有做到一半的升級(upgrade-idea.py 的紀錄)→ 有就先把升級重試做完
  2. GitHub repo 改名(gh repo rename;GitHub 會把舊網址轉到新的)、本機 origin 改成新網址——沒有 remote 的(想法沙盒)跳過
  3. rename-project.sh <舊> <新> --origin <新網址> --apply(登記、memory、mirror、資料夾、legacy-paths 一起改)
     有程序的工作目錄還在舊資料夾(例:開著的 session)它會拒絕 → busy
重試:舊名稱已經不在登錄、新名稱在、紀錄說是這次改名 → already。GitHub 已經改好(紀錄的 stage)就跳過第 2 步。
不做:租約跟對話紀錄(lease-runner 呼叫完這支自己處理)、改專案程式碼裡的名稱(專案自己的事)。
"""
from __future__ import annotations

import datetime
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)

RECORD = hubconf.STATE / "project-renames.json"
UPGRADES = hubconf.STATE / "idea-upgrades.json"   # upgrade-idea.py 的紀錄:做到一半的升級要先做完
BUSY = 75  # rename-project.sh「有程序的工作目錄在舊資料夾」的 exit code
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
GITHUB = re.compile(r"^(?:git@github\.com:|https://github\.com/)([^/]+)/([^/]+?)(?:\.git)?$")
JSON = False


def done(result: str, code: int, **extra):
    if JSON:
        print(json.dumps({"result": result, **extra}, ensure_ascii=False))
    elif code:
        print(f"project-rename: {extra.get('detail', result)}", file=sys.stderr)
    sys.exit(code)


def fail(msg: str, result: str = "failed") -> None:
    done(result, 1, detail=msg)


def records() -> dict:
    try:
        return json.loads(RECORD.read_text())
    except (OSError, ValueError):
        return {}


def record(old: str, **fields) -> None:
    rec = records()
    rec[old] = {**rec.get(old, {}), **fields, "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text(json.dumps(rec, ensure_ascii=False, indent=1))


def load(name: str) -> dict | None:
    import yaml
    f = hubconf.registry_dir() / f"{name}.yaml"
    return (yaml.safe_load(f.read_text()) or {}) if f.exists() else None


def users_in(path) -> str:
    """工作目錄在這個資料夾(含子資料夾)裡的程序。"""
    return " ".join(hubconf.cwd_users(path))


def unfinished_upgrade(*names: str) -> str:
    try:
        ups = json.loads(UPGRADES.read_text())
    except (OSError, ValueError):
        return ""
    for idea, rec in ups.items():
        if rec.get("stage", "done") != "done" and ({idea, rec.get("name")} & set(names)):
            return _("{idea} → {name}, stage {stage}", idea=idea, name=rec.get('name'), stage=rec.get('stage'))
    return ""


def gh_exists(repo: str) -> bool:
    return subprocess.run(["gh", "repo", "view", repo, "--json", "name"], capture_output=True).returncode == 0


def main(argv: list[str]) -> int:
    global JSON
    args = [a for a in argv if not a.startswith("--")]
    apply, JSON = "--apply" in argv, "--json" in argv
    if len(args) != 2:
        fail(_("usage: project-rename.py <old name> <new name> [--apply] [--json]"), "bad_name")
    old, new = args
    rec = records().get(old) or {}

    # 重試:上次已經改完了
    if load(old) is None:
        doc = load(new)
        if rec.get("new") == new and rec.get("stage") == "done" and doc is not None:
            done("already", 0, name=new, origin=(doc.get("paths") or {}).get("origin", ""))
        fail(_("{name} is not in the registry", name=old), "not_found")
    if not NAME.match(new) or "--" in new:
        fail(_("the new name may only use lowercase letters, digits and - (not at the start or end, at most 40 "
               "characters): {name!r}", name=new), "bad_name")
    if new == old:
        fail(_("the new name is the same as the old one"), "bad_name")
    if old == hubconf.hub_project() or new == hubconf.hub_project():
        fail(_("the platform itself cannot be renamed"), "bad_name")
    taken = [str(p) for p in (hubconf.registry_dir() / f"{new}.yaml", hubconf.p("workspace_root", "~/workspace") / new,
                              hubconf.p("services_root", "~/services") / new,
                              hubconf.p("git_origin", "~/git-origin") / f"{new}-memory.git",
                              hubconf.p("memory_copies", "~/.memory-copies") / new) if p.exists()]
    if taken:
        fail(_("{name} is already taken: {paths}", name=new, paths=', '.join(taken)), "name_taken")

    doc = load(old)
    origin = (doc.get("paths") or {}).get("origin") or "(native)"
    ws = hubconf.path((doc.get("paths") or {}).get("workspace"))
    up = unfinished_upgrade(old, new)
    if up:
        fail(_("{name} has an upgrade that stopped halfway ({upgrade}): retry the upgrade until it completes, then "
               "rename",
               name=old, upgrade=up))
    who = users_in(ws) if ws and ws.is_dir() else ""
    if who:
        fail(_("processes still have their working directory in {path}: {who}; close that project's sessions / "
               "terminal tabs first", path=ws, who=who), "busy")
    m = GITHUB.match(origin)
    new_origin, gh_step = origin, None
    if m:
        owner, repo = m.group(1), m.group(2)
        new_origin = origin[:m.start(2)] + new + (".git" if origin.endswith(".git") else "")
        if repo != new:
            already = rec.get("new") == new and rec.get("stage") in ("github", "done")
            if gh_exists(f"{owner}/{new}") and not already:
                fail(_("GitHub already has {owner}/{name}", owner=owner, name=new), "name_taken")
            gh_step = None if already else (f"{owner}/{repo}", new)

    plan = [_("GitHub: {old} → {owner}/{name}", old=gh_step[0], owner=owner, name=new) if gh_step
            else _("GitHub: nothing to change (no remote, or already renamed)"),
            _("local origin: {old} → {new}", old=origin, new=new_origin) if new_origin != origin
            else _("local origin: unchanged"),
            _("rename-project.sh {old} {new} --origin {origin} (registry entry, memory, mirror, folders)",
              old=old, new=new, origin=new_origin)]
    if not JSON:
        print("\n".join(f"  {i}. {p}" for i, p in enumerate(plan, 1)))
    if not apply:
        if not JSON:
            print(_("(dry-run; add --apply to run it)"))
        return 0

    if gh_step:
        r = subprocess.run(["gh", "repo", "rename", gh_step[1], "-R", gh_step[0], "--yes"], capture_output=True, text=True)
        if r.returncode != 0:
            fail(_("renaming the GitHub repo failed: {err}", err=(r.stderr or r.stdout).strip()[-300:]))
        record(old, new=new, stage="github")
    if new_origin != origin and ws:
        subprocess.run(["git", "-C", str(ws), "remote", "set-url", "origin", new_origin], capture_output=True)
    cmd = [str(hubconf.ROOT / "bin" / "rename-project.sh"), old, new, "--apply"]
    if new_origin != "(native)":
        cmd[3:3] = ["--origin", new_origin]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        out = (r.stderr or r.stdout).strip()
        if r.returncode == BUSY:  # 檢查完到這裡之間又有人進去了
            fail(_("processes are still in the {name} folder (e.g. an open session); close them first: {out}",
                   name=old, out=out[-300:]), "busy")
        fail(_("rename-project.sh failed: {out}", out=out[-400:]))
    record(old, new=new, stage="done", origin=new_origin)
    done("renamed", 0, name=new, origin=new_origin)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
